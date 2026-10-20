"""Repeatable performance measurement for import, aggregation, and queries.

Real datasets and seeded synthetic datasets are always reported separately.
Every measured run is a fresh process, so its peak RSS belongs to one operation.
"""

from __future__ import annotations

import argparse
import cProfile
import csv
import hashlib
import io
import json
import os
import platform
import pstats
import random
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from .core import build_dataset
from .core_surface import CoreReadSurface
from .datasets import (
    PROJECT_ROOT,
    SOURCE_FORMAT_CSV,
    CsvColumnMapping,
    CsvImportConfig,
    DatasetDescriptor,
    TimestampConfig,
    canonical_json,
    source_fingerprint,
)
from .profiles import BPIC2012

BENCHMARK_SCHEMA_VERSION = "benchmark-v1"
WORK_ROOT = PROJECT_ROOT / "data" / "benchmark"
RESULT_ROOT = PROJECT_ROOT / "evidence" / "performance"
BPIC_SOURCE = PROJECT_ROOT / "data" / "raw" / "BPI_Challenge_2012.xes.gz"
HELPDESK_SOURCE = PROJECT_ROOT / "data" / "raw" / "helpdesk" / "finale.csv"
REAL_DATASETS = ("bpic2012", "helpdesk")
SYNTHETIC_SIZES = (100_000, 500_000, 1_000_000, 2_000_000)
SYNTHETIC_SEED = 20261019
SYNTHETIC_DATASETS = tuple(f"synthetic-{size // 1000}k" for size in SYNTHETIC_SIZES)
PHASES = (
    "import",
    "describe_log",
    "list_variants",
    "list_transitions",
    "list_activities",
    "get_case_trace",
)
SNAPSHOT_SLA_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000
HELPDESK_CONFIG = CsvImportConfig(
    mapping=CsvColumnMapping(
        case_id="Case ID",
        activity="Activity",
        timestamp="Complete Timestamp",
        resource="Resource",
    ),
    timestamp=TimestampConfig(assume_timezone="UTC", timestamp_format="%Y/%m/%d %H:%M:%S.%f"),
)
SYNTHETIC_CONFIG = CsvImportConfig(
    mapping=CsvColumnMapping(
        case_id="case",
        activity="activity",
        timestamp="timestamp",
        lifecycle="lifecycle",
        resource="resource",
    )
)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    _kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL

    def _memory() -> tuple[int, int]:
        """Current and peak resident set (working set) of this process, in bytes."""

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(
            _kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.WorkingSetSize, counters.PeakWorkingSetSize

    def _total_memory() -> int:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return status.ullTotalPhys

else:
    import resource

    def _memory() -> tuple[int, int]:
        """Current and peak resident set of this process, in bytes (Linux)."""

        page_size = os.sysconf("SC_PAGE_SIZE")
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return resident_pages * page_size, peak_kib * 1024

    def _total_memory() -> int:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")


def synthetic_path(name: str) -> Path:
    return WORK_ROOT / "sources" / f"{name}-seed{SYNTHETIC_SEED}.csv"


def _synthetic_size(name: str) -> int:
    return SYNTHETIC_SIZES[SYNTHETIC_DATASETS.index(name)]


def generate_synthetic(path: Path, event_count: int, seed: int = SYNTHETIC_SEED) -> None:
    """Write a deterministic event log with exactly `event_count` events.

    Each activity prefers a few successors so variants repeat as in a real
    process; about 15% START lifecycle events exercise the analysis view.
    """

    rng = random.Random(seed)
    activities = [f"A{index:02d}" for index in range(24)]
    successors = {activity: rng.sample(activities, 4) for activity in activities}
    resources = [f"R{index:03d}" for index in range(200)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    written = 0
    case_index = 0
    with partial.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("case", "activity", "timestamp", "lifecycle", "resource"))
        while written < event_count:
            case_id = f"C{case_index:08d}"
            moment = start + timedelta(minutes=rng.randrange(525_600))
            activity = activities[0]
            for _ in range(min(rng.randint(3, 37), event_count - written)):
                lifecycle = "START" if rng.random() < 0.15 else "COMPLETE"
                writer.writerow(
                    (case_id, activity, moment.isoformat(), lifecycle, rng.choice(resources))
                )
                written += 1
                moment += timedelta(seconds=rng.randrange(1, 172_800))
                if rng.random() < 0.9:
                    activity = rng.choice(successors[activity])
                else:
                    activity = rng.choice(activities)
            case_index += 1
    partial.replace(path)


def descriptor(name: str) -> DatasetDescriptor:
    """The dataset as measured; its query database lives under the work root."""

    database = WORK_ROOT / "db" / f"{name}.duckdb"
    if name == "bpic2012":
        return BPIC2012.descriptor(BPIC_SOURCE, database)
    if name == "helpdesk":
        source, config = HELPDESK_SOURCE, HELPDESK_CONFIG
    elif name in SYNTHETIC_DATASETS:
        source, config = synthetic_path(name), SYNTHETIC_CONFIG
    else:
        raise ValueError(f"unknown benchmark dataset {name!r}")
    fingerprint = source_fingerprint(source)
    return DatasetDescriptor(
        log_id=name,
        display_name=name,
        source_format=SOURCE_FORMAT_CSV,
        source_path=source,
        database_path=database,
        sha256=fingerprint.sha256,
        size_bytes=fingerprint.size_bytes,
        csv_import=config,
    )


def _require_pass(report: dict[str, Any], name: str) -> dict[str, Any]:
    if report["validation"]["status"] != "PASS":
        raise RuntimeError(f"{name}: build validation is {report['validation']['status']}")
    return report


def _first_case(database_path: Path) -> str:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute("SELECT min(case_id) FROM events").fetchone()[0]


def operation(name: str, phase: str) -> Callable[[], Any]:
    """One measured operation; setup such as choosing a case id stays outside it."""

    dataset = descriptor(name)
    if phase == "import":
        target = dataset.database_path.with_name(f"{name}.import.duckdb")
        target.unlink(missing_ok=True)
        imported = replace(dataset, database_path=target)
        return lambda: _require_pass(build_dataset(imported), name)
    surface = CoreReadSurface(dataset.database_path, name)
    if phase == "describe_log":
        return surface.describe_log
    if phase == "list_variants":
        return lambda: surface.list_variants("case_count_desc", 10)
    if phase == "list_transitions":
        return lambda: surface.list_transitions("transition_count_desc", 10)
    if phase == "list_activities":
        return lambda: surface.list_activities("event_count_desc", 10)
    if phase == "get_case_trace":
        case_id = _first_case(dataset.database_path)
        return lambda: surface.get_case_trace(case_id, "complete")
    raise ValueError(f"unknown benchmark phase {phase!r}")


def ensure_sources(names: list[str]) -> None:
    for name in names:
        if name in SYNTHETIC_DATASETS and not synthetic_path(name).is_file():
            generate_synthetic(synthetic_path(name), _synthetic_size(name))


def prepare(names: list[str]) -> None:
    """Create missing synthetic sources and (re)build every query database."""

    ensure_sources(names)
    for name in names:
        dataset = descriptor(name)
        dataset.database_path.parent.mkdir(parents=True, exist_ok=True)
        dataset.database_path.unlink(missing_ok=True)
        _require_pass(build_dataset(dataset), name)


def _child(name: str, phase: str) -> None:
    run = operation(name, phase)
    rss_before, _ = _memory()
    started = time.perf_counter()
    run()
    seconds = time.perf_counter() - started
    _, peak = _memory()
    print(json.dumps({"peak_rss_bytes": peak, "rss_before_bytes": rss_before, "seconds": seconds}))


def _run_child(name: str, phase: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "piw.benchmark", "child", name, phase],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _spread(values: list[float]) -> dict[str, float]:
    return {"max": max(values), "median": statistics.median(values), "min": min(values)}


def _dataset_facts(name: str) -> dict[str, Any]:
    dataset = descriptor(name)
    with duckdb.connect(str(dataset.database_path), read_only=True) as connection:
        cases, events = connection.execute(
            "SELECT count(DISTINCT case_id), count(*) FROM events"
        ).fetchone()
    return {
        "case_count": cases,
        "event_count": events,
        "kind": "real" if name in REAL_DATASETS else "synthetic",
        "source_sha256": dataset.sha256,
        "source_size_bytes": dataset.size_bytes,
    }


def _environment() -> dict[str, Any]:
    return {
        "cpu_count": os.cpu_count(),
        "duckdb": duckdb.__version__,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "total_memory_bytes": _total_memory(),
    }


def measure(names: list[str], phases: list[str], runs: int, warmup: int) -> dict[str, Any]:
    prepare(names)
    datasets: dict[str, Any] = {}
    for name in names:
        results: dict[str, Any] = {}
        for phase in phases:
            samples = [_run_child(name, phase) for _ in range(warmup + runs)][warmup:]
            results[phase] = {
                "peak_rss_bytes": _spread([sample["peak_rss_bytes"] for sample in samples]),
                "rss_before_bytes": _spread([sample["rss_before_bytes"] for sample in samples]),
                "seconds": _spread([sample["seconds"] for sample in samples]),
            }
            print(f"{name} {phase}: median {results[phase]['seconds']['median']:.3f}s", flush=True)
        datasets[name] = _dataset_facts(name) | {"phases": results}
    return {
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "datasets": datasets,
        "environment": _environment(),
        "method": {
            "process_per_run": True,
            "runs": runs,
            "synthetic_seed": SYNTHETIC_SEED,
            "warmup_runs": warmup,
        },
    }


def snapshot(names: list[str]) -> dict[str, Any]:
    """Fixed Core invocations, for byte comparison before and after a change."""

    result: dict[str, Any] = {}
    for name in names:
        dataset = descriptor(name)
        target = dataset.database_path.with_name(f"{name}.snapshot.duckdb")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        report = build_dataset(replace(dataset, database_path=target), sla_threshold_ms=SNAPSHOT_SLA_THRESHOLD_MS)
        surface = CoreReadSurface(target, name)
        case_id = _first_case(target)
        result[name] = {
            "build_report": report,
            "describe_log": surface.describe_log(),
            "describe_log_sla": surface.describe_log(SNAPSHOT_SLA_THRESHOLD_MS),
            "list_variants": [
                surface.list_variants(order, 100) for order in ("case_count_desc", "variant_id_asc")
            ],
            "list_transitions": [
                surface.list_transitions(order, 100)
                for order in ("transition_count_desc", "activities_asc")
            ],
            "list_activities": [
                surface.list_activities(order, 100)
                for order in ("event_count_desc", "case_count_desc", "rework_event_count_desc", "activity_asc")
            ],
            "get_case_trace": [
                surface.get_case_trace(case_id, perspective) for perspective in ("complete", "raw")
            ],
        }
        target.unlink()
    return result


def compare_snapshots(before_path: Path, after_path: Path) -> dict[str, Any]:
    """Publish only normalized answer hashes, never the underlying event data."""

    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    expected = set((*REAL_DATASETS, *SYNTHETIC_DATASETS))
    if set(before) != expected or set(after) != expected:
        raise ValueError("snapshots must contain exactly the six benchmark datasets")
    datasets = {}
    for name in sorted(expected):
        if set(before[name]) != set(after[name]):
            raise ValueError(f"{name}: snapshot response keys differ")
        answers = {}
        for key in sorted(before[name]):
            left = canonical_json(before[name][key]).encode("utf-8")
            right = canonical_json(after[name][key]).encode("utf-8")
            answers[key] = {
                "before_sha256": hashlib.sha256(left).hexdigest(),
                "after_sha256": hashlib.sha256(right).hexdigest(),
                "byte_identical": left == right,
            }
        datasets[name] = answers
    return {
        "normalization": "piw.datasets.canonical_json(value).encode('utf-8'); no fields removed",
        "datasets": datasets,
        "passed": all(item["byte_identical"] for answers in datasets.values() for item in answers.values()),
    }


def profile(name: str, phase: str, top: int) -> str:
    run = operation(name, phase)
    profiler = cProfile.Profile()
    profiler.enable()
    run()
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(top)
    return stream.getvalue()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def parser() -> argparse.ArgumentParser:
    all_names = [*REAL_DATASETS, *SYNTHETIC_DATASETS]
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    measured = commands.add_parser("measure", help="repeat each phase in fresh processes")
    measured.add_argument("--label", required=True, help="result file name under evidence/performance")
    measured.add_argument("--datasets", nargs="+", choices=all_names, default=all_names)
    measured.add_argument("--phases", nargs="+", choices=PHASES, default=list(PHASES))
    measured.add_argument("--runs", type=int, default=5)
    measured.add_argument("--warmup", type=int, default=1)
    snap = commands.add_parser("snapshot", help="write every Core answer for byte comparison")
    snap.add_argument("--out", type=Path, required=True)
    snap.add_argument("--datasets", nargs="+", choices=all_names, default=all_names)
    compared = commands.add_parser("compare", help="compare all six datasets without publishing raw answers")
    compared.add_argument("--before", type=Path, required=True)
    compared.add_argument("--after", type=Path, required=True)
    compared.add_argument("--out", type=Path, required=True)
    profiled = commands.add_parser("profile", help="cProfile one phase in this process")
    profiled.add_argument("dataset", choices=all_names)
    profiled.add_argument("phase", choices=PHASES)
    profiled.add_argument("--top", type=int, default=30)
    child = commands.add_parser("child", help=argparse.SUPPRESS)
    child.add_argument("dataset", choices=all_names)
    child.add_argument("phase", choices=PHASES)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "child":
        _child(args.dataset, args.phase)
    elif args.command == "measure":
        result = measure(args.datasets, args.phases, args.runs, args.warmup)
        _write_json(RESULT_ROOT / f"{args.label}.json", result)
    elif args.command == "snapshot":
        ensure_sources(args.datasets)
        _write_json(args.out, snapshot(args.datasets))
    elif args.command == "compare":
        result = compare_snapshots(args.before, args.after)
        _write_json(args.out, result)
        return 0 if result["passed"] else 1
    else:
        print(profile(args.dataset, args.phase, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

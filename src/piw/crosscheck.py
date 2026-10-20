"""Independent PM4Py evaluation; never imported by Core, web, or agent tools."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import math
from pathlib import Path
import tempfile

import duckdb

from .core import case_analytics
from .verification_data import add_sources, build_inputs, write_json


def duration_summary(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min_ms": ordered[0] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
        "p50_ms": ordered[math.ceil(len(ordered) * .5) - 1] if ordered else None,
        "p90_ms": ordered[math.ceil(len(ordered) * .9) - 1] if ordered else None,
    }


def independent_log(source: Path, log_id: str):
    import pandas as pd
    import pm4py
    from pm4py.objects.log.obj import Event, EventLog, Trace

    if log_id == "bpic2012":
        return pm4py.read_xes(
            str(source), return_legacy_log_object=True,
            timestamp_sort=False, show_progress_bar=False,
        )
    # This does not use the TraceVerity adapter, its event table, or its parser.
    frame = pd.read_csv(source)
    frame["Complete Timestamp"] = pd.to_datetime(
        frame["Complete Timestamp"], format="%Y/%m/%d %H:%M:%S.%f", utc=True
    )
    log = EventLog()
    for case_id, rows in frame.groupby("Case ID", sort=False):
        trace = Trace(attributes={"concept:name": str(case_id)})
        for activity, timestamp in rows[["Activity", "Complete Timestamp"]].itertuples(index=False, name=None):
            trace.append(Event({"concept:name": activity, "time:timestamp": timestamp.to_pydatetime()}))
        log.append(trace)
    return log


def aligned_log(log, *, complete: bool):
    from pm4py.objects.log.obj import EventLog, Trace

    result = EventLog()
    for trace in log:
        indexed = [
            (position, event) for position, event in enumerate(trace)
            if not complete or event.get("lifecycle:transition") is None
            or str(event["lifecycle:transition"]).upper() == "COMPLETE"
        ]
        indexed.sort(key=lambda pair: (pair[1]["time:timestamp"], pair[0]))
        if indexed:
            result.append(Trace([event for _, event in indexed], attributes=dict(trace.attributes)))
    return result


def pm_metrics(raw, analysis) -> dict:
    import pm4py
    from pm4py.statistics.traces.generic.log import case_statistics

    variants = pm4py.get_variants_as_tuples(analysis)
    variant_counts = {sequence: len(traces) for sequence, traces in variants.items()}
    dfg, _, _ = pm4py.discover_dfg(analysis)
    cases = case_statistics.get_cases_description(raw)
    durations = {case: round(row["caseDuration"] * 1000) for case, row in cases.items()}
    rework_per_activity = Counter()
    rework_cases = set()
    aggregate = 0
    for trace in analysis:
        counts = Counter(event["concept:name"] for event in trace)
        excess = sum(count - 1 for count in counts.values())
        aggregate += excess
        if excess:
            rework_cases.add(str(trace.attributes["concept:name"]))
        for activity, count in counts.items():
            if count > 1:
                rework_per_activity[activity] += 1
    # Independently validate our explicit mapping against PM4Py's API.
    api_rework = pm4py.get_rework_cases_per_activity(analysis)
    if dict(rework_per_activity) != api_rework:
        raise ValueError("PM4Py rework API and explicit case/activity mapping differ")
    return {
        "case_count": len(raw),
        "raw_event_count": sum(map(len, raw)),
        "analysis_event_count": sum(map(len, analysis)),
        "distinct_activity_count": len(pm4py.get_event_attribute_values(raw, "concept:name")),
        "analysis_distinct_activity_count": len(pm4py.get_event_attribute_values(analysis, "concept:name")),
        "variant_count": len(variant_counts),
        "variants": variant_counts,
        "dfg": dict(dfg),
        "direct_follow_count": sum(dfg.values()),
        "durations": durations,
        "duration_summary": duration_summary(list(durations.values())),
        "cases_with_rework": len(rework_cases),
        "aggregate_rework_event_count": aggregate,
        "rework_cases_per_activity": api_rework,
    }


def core_metrics(report: dict, database: Path) -> dict:
    with duckdb.connect(str(database), read_only=True) as connection:
        durations = {row["case_id"]: row["cycle_time_ms"] for row in case_analytics(connection, 0)}
    return {
        "case_count": report["raw_summary"]["case_count"],
        "raw_event_count": report["raw_summary"]["event_count"],
        "analysis_event_count": report["analysis_event_count"],
        "distinct_activity_count": report["raw_summary"]["distinct_activity_count"],
        "analysis_distinct_activity_count": report["process_summary"]["analysis_distinct_activity_count"],
        "variant_count": report["variant_summary"]["variant_count"],
        "variants": {tuple(row["activity_sequence"]): row["case_count"] for row in report["variant_summary"]["variants"]},
        "dfg": {(row["from_activity"], row["to_activity"]): row["transition_count"] for row in report["transition_summary"]["transitions"]},
        "direct_follow_count": report["transition_summary"]["transition_count"],
        "durations": durations,
        "duration_summary": report["cycle_time_summary"],
        "cases_with_rework": report["rework_summary"]["cases_with_rework"],
        "aggregate_rework_event_count": report["rework_summary"]["aggregate_rework_event_count"],
        "rework_cases_per_activity": {row["activity"]: row["cases_with_rework"] for row in report["rework_summary"]["activities"]},
    }


def mismatches(expected: dict, actual: dict) -> dict:
    result = {}
    for key, value in expected.items():
        other = actual[key]
        if isinstance(value, dict):
            count = sum(value.get(k) != other.get(k) for k in value.keys() | other.keys())
        else:
            count = int(value != other)
        result[key] = count
    return result


def compare_dataset(source: Path, name: str, built: dict) -> dict:
    log = independent_log(source, name)
    raw = aligned_log(log, complete=False)
    complete = aligned_log(log, complete=True)
    core = core_metrics(built["report"], built["descriptor"].database_path)
    independent = pm_metrics(raw, complete)
    # Native, unfiltered PM4Py has neither Core lifecycle filtering nor canonical ordering.
    native = pm_metrics(log, log)
    source_order_complete = pm_metrics(raw, type(log)([
        type(trace)([e for e in trace if e.get("lifecycle:transition") is None
                     or str(e["lifecycle:transition"]).upper() == "COMPLETE"],
                    attributes=dict(trace.attributes))
        for trace in log
        if any(e.get("lifecycle:transition") is None
               or str(e["lifecycle:transition"]).upper() == "COMPLETE" for e in trace)
    ]))
    differences = mismatches(core, independent)
    ties = sum(
        count - 1 for trace in complete
        for count in Counter(e["time:timestamp"] for e in trace).values()
    )
    reordered = sum(
        list(trace) != list(sorted(trace, key=lambda e: e["time:timestamp"]))
        for trace in log
    )
    scalar = {key: value for key, value in independent.items()
              if key not in {"variants", "dfg", "durations"}}
    native_scalar = {key: value for key, value in native.items()
                     if key not in {"variants", "dfg", "durations"}}
    return {
        "source_sha256": built["report"]["source_fingerprint"]["sha256"],
        "core_validation": built["report"]["validation"]["status"],
        "aligned_pm4py": scalar,
        "native_unfiltered_pm4py": native_scalar,
        "aligned_mismatch_counts": differences,
        "native_unfiltered_mismatch_counts": mismatches(core, native),
        "complete_source_order_mismatch_counts": mismatches(core, source_order_complete),
        "analysis_timestamp_tie_excess_events": ties,
        "raw_traces_reordered_by_timestamp": reordered,
        "compared_variant_sequences": len(core["variants"]),
        "compared_dfg_relations": len(core["dfg"]),
        "compared_case_durations": len(core["durations"]),
        "top_variants": built["report"]["variant_summary"]["variants"][:10],
        "directly_follows": built["report"]["transition_summary"]["transitions"],
        "status": "PASS" if not any(differences.values()) else "HOLD",
    }


def main(argv: list[str] | None = None) -> int:
    import pm4py

    parser = argparse.ArgumentParser(description=__doc__)
    add_sources(parser)
    parser.add_argument("--output", type=Path, default=Path("evidence/v2-crosscheck.json"))
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="traceverity-crosscheck-") as directory:
        built = build_inputs(args.bpic.resolve(), args.helpdesk.resolve(), Path(directory))
        datasets = {
            name: compare_dataset(source.resolve(), name, built[name])
            for name, source in (("bpic2012", args.bpic), ("helpdesk", args.helpdesk))
        }
    report = {
        "schema_version": "v2-pm4py-crosscheck-v1",
        "pm4py_version": pm4py.__version__,
        "datasets": datasets,
        "status": "PASS" if all(d["status"] == "PASS" for d in datasets.values()) else "HOLD",
    }
    write_json(args.output, report)
    print({name: row["aligned_mismatch_counts"] for name, row in datasets.items()})
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

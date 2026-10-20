"""Reproducible, aggregate-only Help Desk analysis from deterministic Core facts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import tempfile

import duckdb

from .core import activity_summary, case_analytics, complete_traces
from .crosscheck import duration_summary
from .verification_data import add_sources, build_inputs, write_json

MIN_CASES = 30


def analyze(built: dict) -> dict:
    descriptor, report = built["descriptor"], built["report"]
    with duckdb.connect(str(descriptor.database_path), read_only=True) as connection:
        cases = {row["case_id"]: row for row in case_analytics(connection, 0)}
        traces = complete_traces(connection)
        activities = activity_summary(connection)
        # The exact Core ordering/perspective; these are observed gaps, not queues.
        gaps = connection.execute("""
            WITH ordered AS (
                SELECT activity AS source,
                       lead(activity) OVER w AS target,
                       lead(event_ts_utc_ms) OVER w - event_ts_utc_ms AS gap
                FROM analysis_events
                WINDOW w AS (PARTITION BY case_id ORDER BY event_ts_utc_ms, event_pos)
            )
            SELECT source, target, count(*), quantile_disc(gap, .5),
                   quantile_disc(gap, .9)
            FROM ordered WHERE target IS NOT NULL GROUP BY source, target
            ORDER BY source, target
        """).fetchall()
    variant_durations = defaultdict(list)
    transition_durations = defaultdict(list)
    for case_id, sequence in traces:
        duration = cases[case_id]["cycle_time_ms"]
        variant_durations[tuple(sequence)].append(duration)
        # Membership counted once per case, not once per occurrence.
        for pair in set(zip(sequence, sequence[1:])):
            transition_durations[pair].append(duration)
    variants = []
    for row in report["variant_summary"]["variants"]:
        variants.append(row | {"case_duration_ms": duration_summary(variant_durations[tuple(row["activity_sequence"])])})
    transitions = [
        {
            "from_activity": source, "to_activity": target,
            "transition_count": count,
            "observed_gap_p50_ms": p50, "observed_gap_p90_ms": p90,
            "containing_case_duration_ms": duration_summary(transition_durations[(source, target)]),
        }
        for source, target, count, p50, p90 in gaps
    ]
    integrity = {
        "variant_cases_preserved": sum(row["case_count"] for row in variants) == len(cases),
        "transition_occurrences_preserved": sum(row["transition_count"] for row in transitions) == report["transition_summary"]["transition_count"],
        "rework_events_preserved": sum(row["rework_event_count"] for row in activities) == report["rework_summary"]["aggregate_rework_event_count"],
    }
    return {
        "schema_version": "v2-helpdesk-analysis-v1",
        "source_sha256": report["source_fingerprint"]["sha256"],
        "metric_definition_version": report["metric_definition_version"],
        "minimum_cases_for_duration_ranking": MIN_CASES,
        "case_count": len(cases),
        "raw_event_count": report["raw_summary"]["event_count"],
        "analysis_event_count": report["analysis_event_count"],
        "cycle_time_summary": report["cycle_time_summary"],
        "rework": report["rework_summary"],
        "activities_by_rework": sorted(activities, key=lambda r: (-r["rework_event_count"], r["activity"])),
        "duration_by_rework": {
            label: duration_summary([r["cycle_time_ms"] for r in cases.values() if r["has_rework"] == flag])
            for label, flag in (("with_rework", True), ("without_rework", False))
        },
        "top_variants_by_frequency": variants[:10],
        "long_duration_variants": sorted(
            [r for r in variants if r["case_count"] >= MIN_CASES],
            key=lambda r: (-r["case_duration_ms"]["p50_ms"], r["variant_id"]),
        )[:10],
        "long_duration_transition_memberships": sorted(
            [r for r in transitions if r["containing_case_duration_ms"]["count"] >= MIN_CASES],
            key=lambda r: (-r["containing_case_duration_ms"]["p50_ms"], r["from_activity"], r["to_activity"]),
        )[:10],
        "transitions": transitions,
        "integrity": integrity,
        "status": "PASS" if all(integrity.values()) else "HOLD",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_sources(parser)
    parser.add_argument("--output", type=Path, default=Path("evidence/v2-helpdesk-analysis.json"))
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="traceverity-analysis-") as directory:
        built = build_inputs(args.bpic.resolve(), args.helpdesk.resolve(), Path(directory))
        report = analyze(built["helpdesk"])
    write_json(args.output, report)
    print({key: report[key] for key in ("case_count", "cycle_time_summary", "duration_by_rework", "integrity")})
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

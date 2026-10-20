"""Harness tests are deterministic unit tests, NOT real-model evaluation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import pytest
from real_data import require_real_data

from piw.ai_verification import (
    PAYLOADS, accuracy_cases, build_attacks, contains_payload, evaluate_case, path_leaks, summarize,
)
from piw.core import build_dataset, install_events
from piw.crosscheck import (
    aligned_log, compare_dataset, core_metrics, duration_summary, mismatches, pm_metrics,
)
from piw.datasets import (
    CsvColumnMapping, CsvImportConfig, DatasetRegistry, TimestampConfig, register_csv,
)
from piw.events import Event
from piw.generalization_evaluation import ScriptedClient
from piw.helpdesk_analysis import analyze
from piw.tools import CoreToolSurface
from piw.verification_data import build_inputs, check_source


def test_discrete_duration_percentiles_and_empty_input():
    assert duration_summary([4, 1, 3, 2]) == {
        "count": 4, "min_ms": 1, "max_ms": 4, "p50_ms": 2, "p90_ms": 4,
    }
    assert duration_summary([]) == {
        "count": 0, "min_ms": None, "max_ms": None, "p50_ms": None, "p90_ms": None,
    }


def test_mismatches_count_missing_and_changed_relations():
    assert mismatches({"dfg": {("A", "B"): 2, ("B", "A"): 1}},
                      {"dfg": {("A", "B"): 3, ("B", "C"): 1}}) == {"dfg": 3}


def test_wrong_source_fails_before_analysis(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("not the pinned source", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        check_source("helpdesk", path)


def test_independent_alignment_preserves_ties_duplicates_and_raw_duration():
    pytest.importorskip("pm4py")
    from datetime import UTC, datetime, timedelta
    from pm4py.objects.log.obj import Event as PMEvent, EventLog, Trace

    epoch = datetime(2020, 1, 1, tzinfo=UTC)
    def event(activity, ms, lifecycle=None):
        row = {"concept:name": activity, "time:timestamp": epoch + timedelta(milliseconds=ms)}
        if lifecycle is not None:
            row["lifecycle:transition"] = lifecycle
        return PMEvent(row)
    raw = EventLog([
        Trace([event("B", 2), event("A", 1, "complete"), event("A", 1),
               event("hidden", 0, "START")], attributes={"concept:name": "c1"}),
        Trace([event("start-only", 0, "START")], attributes={"concept:name": "c2"}),
    ])
    analysis = aligned_log(raw, complete=True)
    result = pm_metrics(aligned_log(raw, complete=False), analysis)
    assert result["variants"] == {("A", "A", "B"): 1}
    assert result["dfg"] == {("A", "A"): 1, ("A", "B"): 1}
    assert result["durations"] == {"c1": 2, "c2": 0}
    assert result["cases_with_rework"] == 1
    assert result["aggregate_rework_event_count"] == 1
    assert result["rework_cases_per_activity"] == {"A": 1}
    assert result["case_count"] == 2
    assert result["analysis_event_count"] == 3


@pytest.fixture(scope="module")
def attacks(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("v2-harness")
    cases = build_attacks(workspace)
    surface = CoreToolSurface(workspace / "unused.duckdb", workspace)
    return surface, cases


def final_activities(messages):
    envelope = json.loads(messages[-1]["content"].removeprefix("TOOL_RESULT="))
    facts = envelope["facts"]
    return {"action": "final", "status": "ANSWERED", "facts": facts,
            "supporting_fact_ids": [fact["fact_id"] for fact in facts]}


def request(case):
    return {"action": "tool", **case["gold_calls"][0]}


def test_fixed_accuracy_questions():
    cases = accuracy_cases()
    assert len(cases) == 12
    assert len({case["id"] for case in cases}) == 12
    assert {case["dataset"] for case in cases} == {"bpic2012", "helpdesk"}
    assert all(case["expected_fact_names"] for case in cases)


@pytest.mark.parametrize("payload", PAYLOADS.values())
def test_exposure_decodes_json_instead_of_searching_escaped_text(payload):
    response = {"facts": [{"value": {"activity": payload}}]}
    encoded = json.dumps(response)
    assert contains_payload(json.loads(encoded), payload)


def test_injection_fixture_stores_all_fields_and_payloads(attacks):
    surface, cases = attacks
    assert len(cases) == len(PAYLOADS) * 3
    for case in cases:
        descriptor = surface.resolver.find(case["dataset"])
        column = {"case_id": "case_id", "activity": "activity", "resource": "resource"}[case["field"]]
        with duckdb.connect(str(descriptor.database_path), read_only=True) as connection:
            assert connection.execute(f"SELECT count(*) FROM events WHERE {column} = ?", [case["payload"]]).fetchone()[0] == 2


@pytest.mark.parametrize("field, exposed", [("activity", True), ("resource", False), ("case_id", False)])
@pytest.mark.parametrize("payload_name", list(PAYLOADS))
def test_harness_distinguishes_exposure_from_nonexposure(attacks, field, exposed, payload_name):
    surface, cases = attacks
    case = next(case for case in cases if case["field"] == field and case["id"].endswith(payload_name))
    row = evaluate_case(surface, ScriptedClient([request(case), final_activities]), surface, case)
    assert row["exact_match"]
    assert row["grounded_exact_match"]
    assert row["payload_exposed_to_model"] is exposed
    assert row["model_output_path_leak_count"] == 0


def test_harness_counts_blocked_forbidden_attempt(attacks):
    surface, cases = attacks
    case = cases[0]
    client = ScriptedClient([
        {"action": "tool", "tool": "write_file", "arguments": {}},
        request(case), final_activities,
    ])
    row = evaluate_case(surface, client, surface, case)
    assert row["forbidden_tool_attempt_count"] == 1
    assert row["out_of_scope_tool_attempt_count"] == 1
    assert row["forbidden_action_count"] == 0
    assert row["exact_match"]


def test_harness_counts_rejected_grounding_separately(attacks):
    surface, cases = attacks
    case = cases[0]
    def wrong(messages):
        final = final_activities(messages)
        final["facts"][0]["value"] = "invented"
        return final
    def correct_after_rejection(messages):
        tool_message = next(message for message in reversed(messages)
                            if message["content"].startswith("TOOL_RESULT="))
        return final_activities([tool_message])
    row = evaluate_case(surface, ScriptedClient([request(case), wrong, correct_after_rejection]), surface, case)
    assert row["rejected_grounding_attempt_count"] > 0
    assert row["accepted_grounding_violation_count"] == 0
    assert row["exact_match"]


def test_harness_scans_unvalidated_explanation_for_paths(attacks, tmp_path):
    surface, cases = attacks
    case = cases[0]
    def leak(messages):
        final = final_activities(messages)
        final["explanation"] = f"database is {tmp_path / 'log.duckdb'}"
        return final
    row = evaluate_case(surface, ScriptedClient([request(case), leak]), surface, case)
    assert row["model_output_path_leak_count"] > 0
    assert "model_output_path_leak" in row["failure_categories"]
    assert path_leaks({"path": str(tmp_path / "private.db")}) == 1


def test_summary_does_not_count_unexposed_rows_as_exposed(attacks):
    surface, cases = attacks
    rows = [evaluate_case(surface, ScriptedClient([request(case), final_activities]), surface, case)
            for case in (cases[0], cases[4])]
    result = summarize(rows)
    assert result["injection_runs"] == 2
    assert result["payload_exposed_runs"] == 1
    assert result["accuracy_questions"] == 0
    assert result["injection_exact_matches"] == 2


def test_analysis_case_membership_not_transition_occurrences(tmp_path):
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case", "activity", "time"])
        for position, activity in enumerate(["A", "B", "A", "B"]):
            writer.writerow(["one", activity, f"2020-01-01T00:00:0{position}+0000"])
    registry = DatasetRegistry(tmp_path / "registry")
    descriptor = register_csv(
        registry, log_id="analysis-fixture", display_name="Fixture", source_path=source,
        config=CsvImportConfig(
            mapping=CsvColumnMapping(case_id="case", activity="activity", timestamp="time"),
            timestamp=TimestampConfig(timestamp_format="%Y-%m-%dT%H:%M:%S%z"),
        ),
    )
    report = build_dataset(descriptor)
    result = analyze({"descriptor": descriptor, "report": report})
    pair = next(row for row in result["transitions"] if row["from_activity"] == "A")
    assert pair["transition_count"] == 2
    assert pair["containing_case_duration_ms"]["count"] == 1
    assert pair["containing_case_duration_ms"]["p50_ms"] == 3000
    assert pair["observed_gap_p50_ms"] == 1000
    assert all(result["integrity"].values())


@pytest.mark.parametrize("filename", [
    "v2-crosscheck.json", "v2-ai-verification.json", "v2-helpdesk-analysis.json",
])
def test_curated_evidence_has_no_local_paths_or_raw_records(filename):
    root = Path(__file__).resolve().parents[1]
    value = json.loads((root / "evidence" / filename).read_text(encoding="utf-8"))
    assert path_leaks(value) == 0
    if filename == "v2-ai-verification.json":
        forbidden = {
            "model_output", "raw_output", "raw_response", "response", "responses",
            "parsed_decision", "parsed_result", "parsed_response", "result", "results",
            "feedback", "feedback_before_response", "conversation", "conversations",
            "messages", "turns", "dispatches", "system_prompt", "tool_calls",
            "answer", "explanation", "content",
        }
        def check(node):
            if isinstance(node, dict):
                assert not forbidden.intersection(node), "public evidence contains model/conversation content"
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)
        check(value)
    serialized = json.dumps(value, ensure_ascii=False)
    for forbidden_key in ('"case_id":', '"resource":', '"event_ts_utc_ms":', '"source_path":'):
        assert forbidden_key not in serialized
    assert value["schema_version"].startswith("v2-")


def test_crosscheck_summary_is_pinned_and_complete():
    root = Path(__file__).resolve().parents[1]
    public = json.loads((root / "evidence/public-verification-summary.json").read_text(encoding="utf-8"))
    cross = json.loads((root / "evidence/v2-crosscheck.json").read_text(encoding="utf-8"))
    for name, row in cross["datasets"].items():
        pinned = public["datasets"][name]
        assert row["source_sha256"] == pinned["source_sha256"]
        for metric, expected in pinned["aggregates"].items():
            if metric in row["aligned_pm4py"]:
                assert row["aligned_pm4py"][metric] == expected
        assert row["compared_case_durations"] == pinned["aggregates"]["case_count"]
        assert row["compared_variant_sequences"] == pinned["aggregates"]["variant_count"]
        assert sum(item["transition_count"] for item in row["directly_follows"]) == pinned["aggregates"]["direct_follow_count"]
        assert not any(row["aligned_mismatch_counts"].values())


def test_model_evidence_counts_actual_runs_without_asserting_model_success():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "evidence/v2-ai-verification.json").read_text(encoding="utf-8"))["initial_single_seed"]
    from piw.tools import canonical_json
    import hashlib
    expected_spec = hashlib.sha256(canonical_json(accuracy_cases()).encode("utf-8")).hexdigest()
    assert report["accuracy_spec_sha256"] == expected_spec
    assert report["model"]["served_filename_matches"]
    assert report["runtime"]["owned_server_stopped"]
    assert set(report["routes"]) == {"direct", "mcp"}
    for route in report["routes"].values():
        rows, aggregate = route["cases"], route["aggregate"]
        assert len(rows) == 24
        accuracy = [row for row in rows if row["kind"] == "accuracy"]
        assert aggregate["accuracy_questions"] == len(accuracy) == 12
        assert aggregate["accuracy_exact_matches"] == sum(row["exact_match"] for row in accuracy)
        assert aggregate["payload_exposed_runs"] == sum(bool(row["payload_exposed_to_model"]) for row in rows)
        assert aggregate["forbidden_tool_attempt_count"] == sum(row["forbidden_tool_attempt_count"] for row in rows)


@pytest.fixture(scope="module")
def real_inputs(tmp_path_factory):
    bpic = Path("data/raw/BPI_Challenge_2012.xes.gz")
    helpdesk = Path("data/raw/helpdesk/finale.csv")
    require_real_data(bpic, "BPIC12 source")
    require_real_data(helpdesk, "Help Desk source")
    pytest.importorskip("pm4py", reason="install the optional eval extra for PM4Py verification")
    built = build_inputs(bpic.resolve(), helpdesk.resolve(), tmp_path_factory.mktemp("v2-real"))
    return {"bpic2012": bpic, "helpdesk": helpdesk}, built


@pytest.mark.integration
@pytest.mark.parametrize("name", ["bpic2012", "helpdesk"])
def test_real_pm4py_crosscheck(real_inputs, name):
    sources, built = real_inputs
    result = compare_dataset(sources[name], name, built[name])
    assert result["status"] == "PASS", result["aligned_mismatch_counts"]
    assert all(count == 0 for count in result["aligned_mismatch_counts"].values())
    assert result["compared_case_durations"] == built[name]["report"]["raw_summary"]["case_count"]


@pytest.mark.integration
def test_real_helpdesk_analysis_preserves_core(real_inputs):
    _, built = real_inputs
    result = analyze(built["helpdesk"])
    assert result["status"] == "PASS"
    assert result["case_count"] == 4580
    assert result["rework"]["aggregate_rework_event_count"] == 1905
    assert result["activities_by_rework"][0]["activity"] == "Take in charge ticket"
    assert result["activities_by_rework"][0]["rework_event_count"] == 775

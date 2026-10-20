"""Observation-harness unit tests; fake HTTP here is NOT model evaluation."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

from piw.agent import (
    GroundedAgent, LlamaServerClient, LocalAgentError, _extract_json, _ungrounded_parameter_error,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v2_ai_round", ROOT / "evaluation/v2_ai_round.py")
ROUND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUND)


@pytest.mark.parametrize("content, invalid", [
    ('{"action":"final","status":"UNAVAILABLE","facts":[]}', False),
    ('{"action":"final","facts":[', True),
])
def test_http_observer_retains_exact_invalid_and_valid_bytes(monkeypatch, content, invalid):
    requests = []
    def fake_open(request, **kwargs):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({
            "choices": [{"message": {"content": content}, "finish_reason": "length" if invalid else "stop"}],
            "usage": {"completion_tokens": 768 if invalid else 20},
        }).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    observed = ROUND.RecordingClient(LlamaServerClient("http://127.0.0.1:8097/v1", "Qwen3-8B", seed=3))
    messages = [{"role": "user", "content": "unchanged question"}]
    if invalid:
        with pytest.raises(LocalAgentError, match="did not contain a JSON object"):
            observed.complete(messages)
    else:
        assert observed.complete(messages) == json.loads(content)
    assert observed.turns[0]["model_output"] == content
    assert observed.turns[0]["finish_reason"] == ("length" if invalid else "stop")
    assert requests[0]["seed"] == 3
    assert requests[0]["temperature"] == .1
    assert requests[0]["max_tokens"] == 768
    assert requests[0]["messages"] == messages


def test_expanded_payloads_are_distinct_and_controls_stay_separate(tmp_path):
    cases = ROUND.build_cases(tmp_path)
    exposed = [case for case in cases if case["kind"] == "injection"]
    controls = [case for case in cases if case["kind"] == "control"]
    assert len(exposed) == len({case["payload"] for case in exposed}) == 12
    assert len(controls) == 8
    assert len([case for case in cases if case["kind"] == "accuracy"]) == 12
    assert all(case["field"] == "activity" for case in exposed)
    assert {case["field"] for case in controls} == {"resource", "case_id"}
    from piw.ai_verification import accuracy_cases
    assert [case for case in cases if case["kind"] == "accuracy"] == accuracy_cases()


@pytest.mark.parametrize("text", [
    '{"action":"final","status":"UNAVAILABLE","facts":[]} }',
    '{"action":"final","status":"UNAVAILABLE","facts":[]},{"facts":[]}',
])
def test_extra_data_is_rejected_with_actionable_syntax_error(text):
    with pytest.raises(LocalAgentError, match="Extra data.*line 1 column.*exactly one JSON object"):
        _extract_json(text)


@pytest.mark.parametrize("arguments", [
    {"log_id": "fixture", "order_by": "transition_count_desc", "limit": 2, "tool": "list_transitions"},
    {"exact": {"log_id": "fixture", "order_by": "transition_count_desc", "limit": 2}, "tool": "list_transitions"},
])
def test_misplaced_tool_rejection_tells_model_to_move_and_unwrap(arguments):
    error = _ungrounded_parameter_error("For fixture, list transitions", None, arguments)
    assert error["error"]["code"] == "INVALID_ARGUMENT"
    assert "top-level" in error["error"]["message"]
    assert "without an exact wrapper" in error["error"]["message"]
    assert _ungrounded_parameter_error(
        "For fixture, list transitions", "list_transitions",
        {"log_id": "fixture", "order_by": "transition_count_desc", "limit": 2},
    ) is None


def test_malformed_envelope_is_not_executed_and_can_recover():
    from piw.generalization_evaluation import ScriptedClient
    from piw.tools import CoreToolSurface
    class Runtime:
        def __init__(self):
            self.calls = []
        def definitions(self):
            return CoreToolSurface.definitions()
        def dispatch(self, tool, arguments):
            self.calls.append((tool, arguments))
            return {"facts": [{"fact_id": "f_test", "name": "case_count", "value": 2}]}
    runtime = Runtime()
    def repair(messages):
        error = json.loads(messages[-1]["content"].removeprefix("TOOL_RESULT="))
        assert "top-level" in error["error"]["message"]
        return {"action": "tool", "tool": "describe_log", "arguments": {"log_id": "fixture"}}
    client = ScriptedClient([
        {"action": "tool", "arguments": {"tool": "describe_log", "log_id": "fixture"}},
        repair,
        {"action": "final", "status": "ANSWERED", "facts": [
            {"fact_id": "f_test", "name": "case_count", "value": 2},
        ]},
    ])
    result = GroundedAgent(runtime, client).answer("For fixture return case_count", expected_fact_names=["case_count"])
    assert result["answer"]["status"] == "ANSWERED"
    assert result["steps"] == 3
    assert runtime.calls == [("describe_log", {"log_id": "fixture"})]
    assert result["tool_calls"][0]["accepted"] is False
    with pytest.raises(ValueError, match="1..6"):
        GroundedAgent(runtime, client, max_steps=7)


def test_comparison_evidence_has_complete_paired_seeds_and_unchanged_contract():
    report = json.loads((ROOT / "evidence/v2-ai-verification.json").read_text(encoding="utf-8"))
    before, after = report["before"], report["after"]
    assert before["model"] == after["model"]
    assert before["runtime"] == after["runtime"]
    assert before["runtime"]["seeds"] == [1, 2, 3]
    assert before["runtime"]["temperature"] == .1
    assert before["runtime"]["max_steps"] == 6
    assert before["runtime"]["max_tokens"] == 768
    assert before["accuracy_spec_sha256"] == after["accuracy_spec_sha256"]
    assert before["tool_input_contract_sha256"] == after["tool_input_contract_sha256"]
    assert len(report["seed_1_before_diagnosis"]) == 6
    recorded_after = {
        (row["route"], row["id"], row["seed"])
        for row in report["remaining_and_regression_after_diagnosis"]
    }
    assert {
        (route, row["id"], row["seed"])
        for route, data in after["routes"].items() for row in data["cases"]
        if row["kind"] == "accuracy" and not row["exact_match"]
    }.issubset(recorded_after)
    for route in ("direct", "mcp"):
        identities = []
        for phase in (before, after):
            rows = phase["routes"][route]["cases"]
            pairs = {(row["id"], row["seed"]) for row in rows}
            assert len(pairs) == len(rows) == 96
            identities.append(pairs)
            for seed in (1, 2, 3):
                subset = [row for row in rows if row["seed"] == seed]
                assert sum(row["kind"] == "accuracy" for row in subset) == 12
                assert sum(row["kind"] == "injection" for row in subset) == 12
                assert sum(row["kind"] == "control" for row in subset) == 8
            aggregate = phase["routes"][route]["aggregate"]
            assert aggregate["accuracy_exact_matches"] == sum(
                row["exact_match"] for row in rows if row["kind"] == "accuracy"
            )
        assert identities[0] == identities[1]
        assert report["deltas"][route]["accuracy_exact_matches"] == (
            after["routes"][route]["aggregate"]["accuracy_exact_matches"]
            - before["routes"][route]["aggregate"]["accuracy_exact_matches"]
        )
    expected_pass = ROUND.gate(report["round_2"]["routes"])
    assert report["status"] == ("PASS" if expected_pass else "HOLD")


def test_round2_examples_include_every_required_argument():
    from piw.tools import CoreToolSurface
    definitions = {d["name"]: d for d in CoreToolSurface.definitions()}
    protocol = json.loads(GroundedAgent(CoreToolSurface, None)._system_prompt().split("PROTOCOL=")[1])
    examples = protocol["tool_requests"]
    assert {example["tool"] for example in examples} == set(definitions)
    for example in examples:
        schema = definitions[example["tool"]]["parameters"]
        args = example["arguments"]
        assert set(schema["required"]) <= set(args) <= set(schema["properties"])
        for name, value in args.items():
            prop = schema["properties"][name]
            if "enum" in prop:
                assert value in prop["enum"]
            if prop.get("type") == "integer":
                assert type(value) is int
                assert prop["minimum"] <= value <= prop["maximum"]


def test_round2_descriptions_explain_required_args_and_allowed_values():
    from piw.tools import CoreToolSurface
    for definition in CoreToolSurface.definitions():
        description = definition["description"]
        for name in definition["parameters"]["required"]:
            assert name in description
        for prop in definition["parameters"]["properties"].values():
            for value in prop.get("enum", []):
                assert value in description


@pytest.mark.parametrize("tool, arguments, missing", [
    ("describe_log", {}, ["log_id"]),
    ("list_variants", {"log_id": "fixture"}, ["limit", "order_by"]),
    ("list_transitions", {"log_id": "fixture"}, ["limit", "order_by"]),
    ("list_activities", {"log_id": "fixture"}, ["limit", "order_by"]),
    ("get_case_trace", {"log_id": "fixture"}, ["case_id", "perspective"]),
])
def test_round2_missing_arguments_are_rejected_without_defaults(tmp_path, tool, arguments, missing):
    from piw.tools import CoreToolSurface
    original = dict(arguments)
    surface = CoreToolSurface(tmp_path / "not-built.duckdb", tmp_path)
    error = surface.dispatch(tool, arguments)["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["details"]["missing_parameters"] == missing
    assert arguments == original
    for name in missing:
        assert name in error["message"]
    for value in error["details"]["allowed_values"].get("order_by", []):
        assert value in error["message"]


@pytest.mark.parametrize("tool", ["list_variants", "list_transitions", "list_activities"])
def test_round2_invalid_order_reports_allowed_values(tmp_path, tool):
    from piw.tools import CoreToolSurface
    surface = CoreToolSurface(tmp_path / "not-built.duckdb", tmp_path)
    error = surface.dispatch(tool, {
        "log_id": "fixture", "order_by": "not-an-enum", "limit": 1,
    })["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["details"]["missing_parameters"] == []
    assert error["details"]["invalid_enum_parameters"] == ["order_by"]
    assert error["details"]["allowed_values"]["limit"] == {"minimum": 1, "maximum": 100}
    assert all(value in error["message"] for value in error["details"]["allowed_values"]["order_by"])


def test_round2_evidence_preserves_contract_protocol_and_three_columns():
    from piw.tools import CoreToolSurface
    report = json.loads((ROOT / "evidence/v2-ai-verification.json").read_text(encoding="utf-8"))
    previous, current = report["after"], report["round_2"]
    assert previous["runtime"] == current["runtime"]
    assert previous["model"] == current["model"]
    assert previous["accuracy_spec_sha256"] == current["accuracy_spec_sha256"]
    assert previous["payload_spec_sha256"] == current["payload_spec_sha256"]
    assert previous["payload_variant_count"] == current["payload_variant_count"] == 12
    from piw.tools import canonical_json
    import hashlib
    contract = {d["name"]: d["parameters"] for d in CoreToolSurface.definitions()}
    expected = hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()
    assert previous["tool_input_contract_sha256"] == current["tool_input_contract_sha256"] == expected
    assert report["method"]["total_runs"] == 576
    assert report["method"]["final_round"] == 2
    for route in ("direct", "mcp"):
        reference = {(r["id"], r["seed"]) for r in previous["routes"][route]["cases"]}
        rows = current["routes"][route]["cases"]
        assert len(rows) == 96
        assert {(r["id"], r["seed"]) for r in rows} == reference
        comparison = report["three_column_comparison"][route]
        assert comparison["columns"] == ["baseline", "round_1", "round_2"]
        assert comparison["metrics"]["accuracy_exact_matches"] == [
            phase["routes"][route]["aggregate"]["accuracy_exact_matches"]
            for phase in (report["before"], previous, current)
        ]

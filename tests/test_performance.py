"""Semantic guards for single-pass import and performance-evidence comparison."""

from datetime import datetime
import json
from pathlib import Path

import duckdb
import pytest

from piw.benchmark import REAL_DATASETS, SYNTHETIC_DATASETS, compare_snapshots
from piw.core import ingest_dataset
from piw.datasets import (
    CsvColumnMapping, CsvImportConfig, DatasetRegistry, register_csv, register_xes,
)
from piw.events import SourceContractError, utc_epoch_ms


def test_interleaved_csv_cases_keep_source_counts_and_positions(tmp_path: Path) -> None:
    source = tmp_path / "interleaved.csv"
    source.write_text(
        "case,activity,time\n"
        "a,Start,1970-01-01T00:00:00Z\n"
        "b,Start,1970-01-01T00:00:00Z\n"
        "a,End,1970-01-01T00:00:01Z\n",
        encoding="utf-8",
    )
    dataset = register_csv(
        DatasetRegistry(tmp_path / "registry"),
        log_id="interleaved", display_name="Interleaved", source_path=source,
        config=CsvImportConfig(mapping=CsvColumnMapping(
            case_id="case", activity="activity", timestamp="time",
        )),
    )
    with duckdb.connect(str(dataset.database_path)) as connection:
        result = ingest_dataset(connection, dataset)
        rows = connection.execute(
            "SELECT case_id, event_pos FROM events ORDER BY case_id, event_pos"
        ).fetchall()
    assert result.trace_count == 2
    assert result.inserted == 3
    assert rows == [("a", 0), ("a", 1), ("b", 0)]


def test_xes_source_count_includes_empty_trace(tmp_path: Path) -> None:
    source = tmp_path / "empty-trace.xes"
    source.write_text(
        '<log><trace><string key="concept:name" value="empty"/></trace>'
        '<trace><string key="concept:name" value="nonempty"/>'
        '<event><string key="concept:name" value="A"/>'
        '<date key="time:timestamp" value="1970-01-01T00:00:00Z"/>'
        '</event></trace></log>',
        encoding="utf-8",
    )
    dataset = register_xes(
        DatasetRegistry(tmp_path / "registry"), log_id="empty-trace",
        display_name="Empty trace", source_path=source,
    )
    with duckdb.connect(str(dataset.database_path)) as connection:
        result = ingest_dataset(connection, dataset)
    assert result.trace_count == 2
    assert result.inserted == 1


@pytest.mark.parametrize(("text", "expected"), [
    ("1970-01-01T00:00:00+00:00", 0),
    ("1970-01-01T01:00:00.123999+01:00", 123),
    ("1969-12-31T23:59:59.999999+00:00", -1),
    ("1969-12-31T19:00:00-05:00", 0),
    ("1970-01-01T05:30:00+05:30", 0),
])
def test_epoch_arithmetic_preserves_offsets_and_flooring(text: str, expected: int) -> None:
    assert utc_epoch_ms(datetime.fromisoformat(text)) == expected


def test_epoch_arithmetic_rejects_naive_datetime() -> None:
    with pytest.raises(SourceContractError, match="explicit UTC offset"):
        utc_epoch_ms(datetime(1970, 1, 1))


def test_snapshot_comparison_normalizes_keys_but_not_values(tmp_path: Path) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    values = {name: {"build_report": {"b": 2, "a": "é"}} for name in (*REAL_DATASETS, *SYNTHETIC_DATASETS)}
    before.write_text(json.dumps(values), encoding="utf-8")
    after.write_text(json.dumps(values, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    assert compare_snapshots(before, after)["passed"]
    values["helpdesk"]["build_report"]["b"] = 3
    after.write_text(json.dumps(values), encoding="utf-8")
    assert not compare_snapshots(before, after)["passed"]
    del values["helpdesk"]
    after.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="six benchmark datasets"):
        compare_snapshots(before, after)

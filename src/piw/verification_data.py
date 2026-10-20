"""Local-only inputs shared by the optional verification commands."""

from __future__ import annotations

import json
from pathlib import Path

from .core import build_dataset
from .datasets import DatasetRegistry, register_csv, source_fingerprint
from .generalization_evaluation import HELPDESK_CONFIG
from .profiles import BPIC2012

ROOT = Path(__file__).resolve().parents[2]


def public_sources() -> dict:
    return json.loads(
        (ROOT / "evidence/public-verification-summary.json").read_text(encoding="utf-8")
    )["datasets"]


def check_source(log_id: str, source: Path) -> str:
    actual = source_fingerprint(source).sha256
    if actual != public_sources()[log_id]["source_sha256"]:
        raise ValueError(f"{log_id}: source SHA-256 does not match the public pin")
    return actual


def build_inputs(bpic: Path, helpdesk: Path, workspace: Path) -> dict:
    """Build only in a caller-owned new workspace, never in the source repo."""
    check_source("bpic2012", bpic)
    check_source("helpdesk", helpdesk)
    registry = DatasetRegistry(workspace)
    descriptors = {
        "bpic2012": BPIC2012.descriptor(bpic, workspace / "bpic2012.duckdb"),
        "helpdesk": register_csv(
            registry, log_id="helpdesk", display_name="Italian Help Desk",
            source_path=helpdesk, config=HELPDESK_CONFIG,
        ),
    }
    result = {}
    for name, descriptor in descriptors.items():
        report = build_dataset(descriptor)
        if report["validation"]["status"] != "PASS":
            raise ValueError(f"{name}: Core structural validation failed")
        result[name] = {"descriptor": descriptor, "report": report}
    return result


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def add_sources(parser) -> None:
    parser.add_argument("--bpic", type=Path, default=Path("data/raw/BPI_Challenge_2012.xes.gz"))
    parser.add_argument("--helpdesk", type=Path, default=Path("data/raw/helpdesk/finale.csv"))

from __future__ import annotations

import os
from pathlib import Path

import pytest

REQUIRE_ENV = "PIW_REQUIRE_REAL_DATA"


def require_real_data(path: Path, kind: str) -> None:
    """Skip when a separately obtained real dataset is absent.

    Set PIW_REQUIRE_REAL_DATA=1 to fail instead, for canonical verification runs.
    """
    if path.is_file():
        return
    message = (
        f"canonical integration {kind} is missing: {path} "
        "(see docs/REPRODUCTION.md; set PIW_REQUIRE_REAL_DATA=1 to fail instead)"
    )
    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)

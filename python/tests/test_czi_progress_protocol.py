"""Parse tests for CZI extract LOG:/PROGRESS: line protocol."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from czi_common import emit_log, emit_progress_phase  # noqa: E402

LOG_PREFIX = "LOG:"
PROGRESS_PREFIX = "PROGRESS:"


def parse_log_line(line: str) -> str | None:
    if line.startswith(LOG_PREFIX):
        return line[len(LOG_PREFIX) :]
    return None


def parse_progress_line(line: str) -> tuple[int, str] | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    body = line[len(PROGRESS_PREFIX) :]
    m = re.match(r"^(\d+):(.+)$", body)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def map_startup_progress_pct(startup_pct: int) -> int:
    return 3 + round(min(100, max(0, startup_pct)) * 0.15)


def test_log_line_format(capsys) -> None:
    emit_log("Importing numpy…")
    out = capsys.readouterr().out.strip()
    assert out == "LOG:Importing numpy…"
    assert parse_log_line(out) == "Importing numpy…"


def test_progress_line_format(capsys) -> None:
    emit_progress_phase(50, "Loading aicspylibczi…")
    out = capsys.readouterr().out.strip()
    assert out == "PROGRESS:50:Loading aicspylibczi…"
    parsed = parse_progress_line(out)
    assert parsed == (50, "Loading aicspylibczi…")


def test_progress_mapping_to_display_bar() -> None:
    assert map_startup_progress_pct(0) == 3
    assert map_startup_progress_pct(100) == 18
    assert map_startup_progress_pct(50) == 11


def test_invalid_progress_line() -> None:
    assert parse_progress_line("LOG:not progress") is None
    assert parse_progress_line("PROGRESS:bad") is None

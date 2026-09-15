"""Tests for py/run_manifest.py (imported from repo py/)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from run_manifest import write_run_manifest  # noqa: E402


def test_write_run_manifest_serializes_set(tmp_path: Path) -> None:
    write_run_manifest(
        tmp_path,
        {"slice_filter": {"b", "a"}},
    )
    data = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert data["slice_filter"] == ["a", "b"]

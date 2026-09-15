"""Tests for align layout manifest helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from align_layout_manifest import (  # noqa: E402
    intensity_whole_for_slice,
    load_align_slice_layouts,
)


def test_intensity_whole_for_slice_from_layouts() -> None:
    layouts = {
        "M457_s001": {"hemisphere": "L"},
        "M457_s006": {"hemisphere": "W"},
    }
    assert intensity_whole_for_slice("M457_s001", True, layouts) is False
    assert intensity_whole_for_slice("M457_s006", False, layouts) is True
    assert intensity_whole_for_slice("M457_s999", True, layouts) is True


def test_load_align_slice_layouts_from_manifest(tmp_path: Path) -> None:
    manifest = {
        "slice_layouts": {
            "M457_s001": {"hemisphere": "L", "layout_confidence": 0.91},
        }
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_align_slice_layouts(tmp_path)
    assert loaded["M457_s001"]["hemisphere"] == "L"

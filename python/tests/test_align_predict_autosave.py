"""Regression tests for predict_complete autosave clobbering (v4.0.6)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from align_session import (  # noqa: E402
    apply_slice_tuning_from_controls,
    should_sync_controls_before_autosave,
)


class _FakeSlice:
    def __init__(self, ap: int, x: float, y: float) -> None:
        self.ap_position = ap
        self.x_angle = x
        self.y_angle = y
        self.region = "A"
        self.hemisphere = "W"
        self.linked = True
        self.use_tissue_cleanup_mask = False
        self.tissue_mask_warp_mode = "hybrid"


def test_unseeded_predict_complete_skips_control_sync() -> None:
    sl = _FakeSlice(ap=640, x=2.0, y=-1.0)
    assert should_sync_controls_before_autosave("predict_complete", False) is False
    assert sl.ap_position == 640
    assert sl.x_angle == 2.0


def test_seeded_edit_sync_would_apply_controls() -> None:
    sl = _FakeSlice(ap=640, x=2.0, y=-1.0)
    assert should_sync_controls_before_autosave("edit", True) is True
    apply_slice_tuning_from_controls(
        sl,
        x_angle=0.0,
        y_angle=0.0,
        ap_position=0,
        region="A",
        hemisphere="W",
        linked=True,
        use_tissue_cleanup_mask=False,
        tissue_mask_warp_mode="",
    )
    assert sl.ap_position == 0
    assert sl.x_angle == 0.0

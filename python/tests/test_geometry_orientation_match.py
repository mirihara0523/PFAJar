"""Tests for consistent_reorient auto-repair classification."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from geometry_orientation_match import MIN_CONFIDENCE_MARGIN, probe_slice_channels, variant_to_extra_ops  # noqa: E402


def _make_mask_plane(h: int = 40, w: int = 50) -> np.ndarray:
    yy, xx = np.ogrid[:h, :w]
    mask_shape = ((yy - 20) ** 2 + (xx - 25) ** 2) < 180
    return np.where(mask_shape, 220, 10).astype(np.uint8)


def test_consistent_reorient_auto_repairable(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain_masonjar"
    prev = bundle / "data/counting/_previews"
    prev.mkdir(parents=True)
    slice_id = "M1"
    plane = _make_mask_plane()
    import cv2

    for branch in ("dapi", "somata"):
        cv2.imwrite(str(prev / f"{slice_id}_{branch}.png"), plane)

    channel_paths = [
        ("dapi", "data/counting/_previews/M1_dapi.png"),
        ("somata", "data/counting/_previews/M1_somata.png"),
    ]
    result = probe_slice_channels(
        bundle,
        slice_id,
        channel_paths,
        reference_branch="dapi",
        pending_ops=[],
        per_branch_reference_planes={},
    )
    assert result["issue"] in ("ok", "consistent_reorient", "low_confidence")
    if result["issue"] == "consistent_reorient":
        assert result["suggested_ops"] == variant_to_extra_ops("identity") or result["suggested_ops"]
        if result["structural_confidence"] >= MIN_CONFIDENCE_MARGIN:
            assert result["auto_repairable"] is True
            assert result["needs_manual_review"] is False


def test_variant_to_extra_ops_rot180() -> None:
    assert variant_to_extra_ops("rot180") == ["rot90", "rot90"]

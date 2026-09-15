"""Tests for py/align_tissue_warp.py dispatcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from align_tissue_mask import (  # noqa: E402
    WARP_MODE_AP_VERTICAL,
    WARP_MODE_CONSTRAINED_BSPLINE,
    WARP_MODE_HYBRID,
    WARP_MODE_PER_ISLAND,
    WARP_MODE_PHASE1,
    WARP_MODE_REGION_DUAL,
)
from align_tissue_warp import warp_section_with_masks  # noqa: E402


@pytest.fixture
def synthetic_images():
    tissue = np.random.randint(20, 200, (64, 64), dtype=np.uint8)
    section = np.random.randint(20, 200, (64, 64), dtype=np.uint8)
    label = np.zeros((64, 64), dtype=np.uint32)
    label[10:50, 10:50] = 1
    keep = np.zeros((64, 64), dtype=np.uint8)
    keep[10:30, 10:50] = 255
    keep[34:54, 10:50] = 255
    return tissue, section, label, keep


def _fake_register(tissue, section, label, structure_map_path, **kwargs):
    h, w = tissue.shape
    wl = np.ones((h, w), dtype=np.uint32)
    wa = np.full((h, w), 128, dtype=np.uint8)
    cl = np.zeros((h, w, 3), dtype=np.uint8)
    return wl, wa, cl


@pytest.mark.parametrize(
    "mode",
    [
        WARP_MODE_PHASE1,
        WARP_MODE_HYBRID,
        WARP_MODE_PER_ISLAND,
        WARP_MODE_REGION_DUAL,
        WARP_MODE_AP_VERTICAL,
        WARP_MODE_CONSTRAINED_BSPLINE,
    ],
)
def test_warp_section_with_masks_modes(mode, synthetic_images):
    tissue, section, label, keep = synthetic_images
    structure_map = {1: {"name": "Cortex", "id_path": "567/123"}}
    with mock.patch("align_tissue_warp.register_to_atlas", side_effect=_fake_register):
        with mock.patch("align_tissue_warp._register_pass", side_effect=_fake_register):
            wl, wa, cl, meta = warp_section_with_masks(
                tissue,
                section,
                label,
                "dummy.pkl",
                keep_mask=keep,
                damage_mask=None,
                warp_mode=mode,
                region_code="A",
                structure_map=structure_map,
                slice_id="M001_s001",
            )
    assert wl.shape == tissue.shape
    assert wa.shape == tissue.shape
    assert cl.shape == (tissue.shape[0], tissue.shape[1], 3)
    assert meta["tissue_mask_used"] is True
    assert meta["tissue_mask_warp_mode"] == mode


def test_warp_section_without_keep_mask(synthetic_images):
    tissue, section, label, _keep = synthetic_images
    with mock.patch("align_tissue_warp.register_to_atlas", side_effect=_fake_register):
        wl, wa, cl, meta = warp_section_with_masks(
            tissue,
            section,
            label,
            "dummy.pkl",
            keep_mask=None,
            damage_mask=None,
            warp_mode=WARP_MODE_HYBRID,
            slice_id="M001_s001",
        )
    assert meta["tissue_mask_used"] is False
    assert meta["tissue_mask_warp_mode"] == "standard"

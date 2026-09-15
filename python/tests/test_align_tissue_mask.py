"""Tests for py/align_tissue_mask.py."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from align_tissue_mask import (  # noqa: E402
    WARP_MODE_DEFAULT,
    WARP_MODE_HYBRID,
    component_masks,
    gap_corridor_mask,
    keep_mask_stats,
    load_keep_mask,
    mask_is_trivial,
    resolve_bundle_root_from_dapi_dir,
    warp_mode_index,
)


def test_resolve_bundle_root_from_dapi_dir(tmp_path):
    bundle = tmp_path / "MyProject_masonjar"
    dapi = bundle / "data" / "counting" / "00_dapi"
    meta = bundle / ".masonjar"
    dapi.mkdir(parents=True)
    meta.mkdir(parents=True)
    (dapi / "M001_s001.png").write_bytes(b"x")
    found = resolve_bundle_root_from_dapi_dir(dapi)
    assert found == bundle.resolve()


def test_keep_mask_stats_two_islands():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:40, 10:40] = 255
    mask[60:90, 60:90] = 255
    stats = keep_mask_stats(mask)
    assert stats["n_components"] == 2
    assert stats["has_internal_gap"] is True


def test_component_masks_sorted_by_area():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:20, 10:20] = 255
    mask[50:90, 50:90] = 255
    ccs = component_masks(mask)
    assert len(ccs) == 2
    assert int(np.sum(ccs[0] >= 128)) > int(np.sum(ccs[1] >= 128))


def test_gap_corridor_mask():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:40, 10:90] = 255
    mask[60:90, 10:90] = 255
    corridor = gap_corridor_mask(mask)
    assert corridor is not None
    assert corridor[45:55, 20:80].any()
    assert not corridor[10:40, 20:80].any()


def test_mask_is_trivial():
    full = np.full((32, 32), 255, dtype=np.uint8)
    assert mask_is_trivial(full) is True
    partial = full.copy()
    partial[0:5, :] = 0
    assert mask_is_trivial(partial) is False


def test_load_keep_mask_archive_preferred(tmp_path):
    bundle = tmp_path / "bundle"
    meta = bundle / ".masonjar"
    archive = meta / "tissue_cleanup_masks"
    draft = meta / "tissue_cleanup_draft" / "masks"
    archive.mkdir(parents=True)
    draft.mkdir(parents=True)
    sid = "M001_s001"
    (archive / f"{sid}.png").write_bytes(b"not-a-png")
    # cv2 needs valid png - write minimal via numpy/cv2 in test
    import cv2

    arr = np.full((16, 16), 255, dtype=np.uint8)
    cv2.imwrite(str(archive / f"{sid}.png"), arr)
    cv2.imwrite(str(draft / f"{sid}.png"), np.zeros((16, 16), dtype=np.uint8))
    loaded, source = load_keep_mask(bundle, sid)
    assert source == "archive"
    assert loaded is not None
    assert loaded.shape == (16, 16)


def test_warp_mode_index_default():
    assert warp_mode_index(WARP_MODE_HYBRID) == 0
    assert warp_mode_index("unknown") == 0
    assert WARP_MODE_DEFAULT == WARP_MODE_HYBRID

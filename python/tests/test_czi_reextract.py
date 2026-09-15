"""Tests for selective CZI re-extract (repair_mode reextract)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile as tiff

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from czi_common import ROLE_DAPI, ROLE_SIGNAL_SOMATA  # noqa: E402


@pytest.fixture
def bundle_with_max_run(tmp_path: Path) -> Path:
    bundle = tmp_path / "M467_masonjar"
    slice_id = "M467_s006"
    zstack = np.arange(27, dtype=np.uint8).reshape(3, 3, 3)
    somata_in = bundle / "data/original_scans/somata"
    somata_in.mkdir(parents=True)
    tiff.imwrite(str(somata_in / f"{slice_id}.tif"), zstack)
    max_dir = bundle / "data/counting/03_max/somata/max/M467_run"
    max_dir.mkdir(parents=True)
    tiff.imwrite(str(max_dir / f"{slice_id}.tif"), np.zeros((3, 3), dtype=np.uint8))
    return bundle


def test_build_reextract_work_resolves_targets(tmp_path: Path) -> None:
    import czi_extract as ce

    czi_path = tmp_path / "M467(6).czi"
    czi_path.write_bytes(b"fake")
    cfg = {
        "channels": [
            {"file": "M467(6).czi", "index": 0, "role": ROLE_DAPI, "keep": True},
        ],
        "files": [
            {
                "basename": "M467(6).czi",
                "path": str(czi_path),
                "scenes": [{"index": 0, "sliceId": "M467_s006"}],
            }
        ],
    }
    targets = [
        {
            "slice_id": "M467_s006",
            "channel_index": 0,
            "role_key": ROLE_DAPI,
            "file": "M467(6).czi",
            "scene_index": 0,
            "czi_path": str(czi_path),
        }
    ]
    from czi_common import build_files_lookup

    lookup = build_files_lookup(cfg["files"])
    work = ce.build_reextract_work(cfg, targets, lookup)
    assert len(work) == 1
    assert work[0]["slice_id"] == "M467_s006"
    assert work[0]["czi_path"] == czi_path


def test_refresh_max_slices_in_run_overwrites_active_leaf(bundle_with_max_run: Path) -> None:
    import cv2

    import czi_extract as ce

    ce.np = np
    ce.cv2 = cv2
    ce.tiff = tiff

    slice_id = "M467_s006"
    rel = "somata/max/M467_run"
    out_path = bundle_with_max_run / "data/counting/03_max" / rel / f"{slice_id}.tif"
    before = np.asarray(tiff.imread(str(out_path)))
    assert int(before.max()) == 0

    n = ce.refresh_max_slices_in_run(
        bundle_with_max_run,
        ROLE_SIGNAL_SOMATA,
        [slice_id],
        rel,
        {},
    )
    assert n == 1
    after = np.asarray(tiff.imread(str(out_path)))
    assert int(after.max()) > 0


def test_reextract_cfg_preserves_axon_bit_depth() -> None:
    import czi_extract as ce

    from czi_common import ROLE_SIGNAL_AXONS

    cfg = {"bit_depth_by_role": {"signal_axons": 16}}
    assert ce.bit_depth_for_role(cfg, ROLE_SIGNAL_AXONS) == 16


def test_reextract_uint16_max_uses_linear_coerce() -> None:
    import czi_extract as ce

    ce.np = np
    peak = 9727
    stack = np.zeros((3, 8, 8), dtype=np.uint16)
    stack[:, 2:6, 2:6] = peak
    out = ce.coerce_stack_depth(stack, ce.bit_depth_for_role({"bit_depth_by_role": {"signal_axons": 8}}, "signal_axons"))
    assert out.dtype == np.uint8
    assert int(out.max()) == 255
    assert int(out[1, 3, 3]) > 200

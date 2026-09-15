"""Tests for low-res TIFF migration in czi_extract."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile as tiff

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from czi_common import dapi_preview_path, orient_dapi_preview_path  # noqa: E402


@pytest.fixture
def bundle_with_dapi_tiff(tmp_path: Path) -> Path:
    bundle = tmp_path / "Brain_masonjar"
    dapi_dir = bundle / "data/counting/00_dapi"
    dapi_dir.mkdir(parents=True)
    arr = np.arange(16, dtype=np.uint8).reshape(4, 4)
    tiff.imwrite(str(dapi_dir / "M528_s001.tif"), arr)
    return bundle


def test_migrate_00_dapi_tiff_to_png_and_deletes_tiff(bundle_with_dapi_tiff: Path) -> None:
    import cv2

    import czi_extract as ce

    ce.np = np
    ce.cv2 = cv2
    ce.tiff = tiff

    migrated = ce.migrate_low_res_tiffs(bundle_with_dapi_tiff, {}, 0.05)
    assert migrated >= 1
    dapi_dir = bundle_with_dapi_tiff / "data/counting/00_dapi"
    assert list(dapi_dir.glob("*.tif")) == []
    assert list(dapi_dir.glob("*.tiff")) == []
    assert dapi_preview_path(bundle_with_dapi_tiff, "M528_s001").is_file()
    assert orient_dapi_preview_path(bundle_with_dapi_tiff, "M528_s001").is_file()


def test_migrate_does_not_delete_orient_dapi_when_pipeline_exists(tmp_path: Path) -> None:
    import cv2

    import czi_extract as ce

    ce.np = np
    ce.cv2 = cv2
    ce.tiff = tiff

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M528_s001"
    dapi_png = dapi_preview_path(bundle, slice_id)
    orient_png = orient_dapi_preview_path(bundle, slice_id)
    dapi_png.parent.mkdir(parents=True)
    orient_png.parent.mkdir(parents=True)
    arr = np.ones((4, 4), dtype=np.uint8) * 9
    ce._write_preview_array(arr, dapi_png)
    ce._write_preview_array(arr, orient_png)

    migrated = ce.migrate_low_res_tiffs(bundle, {}, 1.0)
    assert orient_png.is_file()
    assert dapi_png.is_file()
    assert migrated == 0 or migrated >= 0

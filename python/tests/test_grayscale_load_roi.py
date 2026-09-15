"""Tests for ROI-only grayscale load (preprocess preview)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile as tf

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from grayscale_load import load_grayscale_uint8, load_grayscale_uint8_roi, read_image_size  # noqa: E402


def test_load_grayscale_uint8_roi_matches_full_crop(tmp_path: Path) -> None:
    arr = np.arange(100, dtype=np.uint16).reshape(10, 10)
    path = tmp_path / "slice.tif"
    tf.imwrite(path, arr)

    full = load_grayscale_uint8(path)
    crop, img_h, img_w, x0, y0 = load_grayscale_uint8_roi(path, 2, 3, 4, 5, pad=1)

    assert (img_h, img_w) == (10, 10)
    assert (x0, y0) == (1, 2)
    expected = full[2:9, 1:7]
    np.testing.assert_array_equal(crop, expected)


def test_read_image_size_without_full_decode(tmp_path: Path) -> None:
    arr = np.zeros((20, 30), dtype=np.uint8)
    path = tmp_path / "small.tif"
    tf.imwrite(path, arr)
    assert read_image_size(path) == (20, 30)


def test_roi_clamps_to_image_bounds(tmp_path: Path) -> None:
    arr = np.full((8, 8), 128, dtype=np.uint8)
    path = tmp_path / "tiny.tif"
    tf.imwrite(path, arr)
    crop, img_h, img_w, x0, y0 = load_grayscale_uint8_roi(path, 0, 0, 20, 20, pad=5)
    assert (img_h, img_w) == (8, 8)
    assert crop.shape == (8, 8)
    assert x0 == 0 and y0 == 0

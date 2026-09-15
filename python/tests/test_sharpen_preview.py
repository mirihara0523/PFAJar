"""Tests for py/sharpen.py ROI sharpen."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from sharpen import process_roi, sharpen_image  # noqa: E402


def test_sharpen_roi_shape():
    img = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
    roi = process_roi(img, 20, 20, 50, 50, radius=2.0, amount=1.0, equalize=False)
    assert roi.shape == (50, 50)
    assert roi.dtype == np.uint8


def test_sharpen_image_finite():
    img = np.ones((40, 40), dtype=np.uint8) * 120
    out = sharpen_image(img, radius=2.0, amount=1.5, equalize=False)
    assert np.isfinite(out).all()

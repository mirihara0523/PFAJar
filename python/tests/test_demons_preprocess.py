"""Tests for py/demons.py registration preprocessing."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from demons import preprocess_image  # noqa: E402


def test_preprocess_flat_input_is_finite():
    """Uniform input must not divide by zero and must yield finite edges."""
    flat = np.full((64, 64), 128, dtype=np.uint8)
    image = sitk.GetImageFromArray(flat)
    result = preprocess_image(image)
    arr = sitk.GetArrayFromImage(result)
    assert np.isfinite(arr).all()
    assert arr.dtype == np.float32
    assert arr.max() == 0.0
    assert arr.min() == 0.0


def test_preprocess_varied_input_normalized():
    """Non-uniform input should normalize to [0, 1] range."""
    grid = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    image = sitk.GetImageFromArray(grid)
    result = preprocess_image(image)
    arr = sitk.GetArrayFromImage(result)
    assert np.isfinite(arr).all()
    assert arr.min() >= 0.0
    assert arr.max() <= 1.0
    assert arr.max() > 0.0

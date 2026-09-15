"""Tests for CZI uint16→uint8 stack linear scaling (py/czi_extract.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

import czi_extract as cx  # noqa: E402
import numpy as np  # noqa: E402

cx.np = np

from czi_extract import coerce_stack_depth  # noqa: E402


def test_uint16_dim_stack_linear_scale_preserves_nonzero():
    """Dim confocal peaks (~15% of 65535): linear scale keeps tissue, /257 would wipe."""
    peak = 9727
    stack = np.zeros((10, 64, 64), dtype=np.uint16)
    stack[:, 20:40, 20:40] = np.linspace(100, peak, 20 * 20, dtype=np.uint16).reshape(20, 20)
    out = coerce_stack_depth(stack, 8)
    assert out.dtype == np.uint8
    assert int(out.max()) == 255
    tissue = out[:, 20:40, 20:40]
    assert (tissue > 0).mean() > 0.99


def test_uint16_linear_uses_stack_peak_not_per_plane():
    stack = np.zeros((3, 8, 8), dtype=np.uint16)
    stack[0, 0, 0] = 1000
    stack[1, 0, 0] = 5000
    stack[2, 0, 0] = 8000
    out = coerce_stack_depth(stack, 8)
    assert int(out[0, 0, 0]) == int(1000 * 255 / 8000)
    assert int(out[2, 0, 0]) == 255


def test_uint16_max_projection_uses_stack_peak():
    peak = 5000
    stack = np.zeros((2, 16, 16), dtype=np.uint16)
    stack[0, 4:12, 4:12] = 100
    stack[1, 4:12, 4:12] = peak
    proj = np.max(stack, axis=0)
    out = coerce_stack_depth(proj, 8, scale_max=peak)
    assert int(out.max()) == 255


def test_uint8_passthrough():
    img = np.arange(256, dtype=np.uint8).reshape(16, 16)
    out = coerce_stack_depth(img, 8)
    assert np.array_equal(out, img)


def test_uint16_to_uint16_passthrough_when_bit_depth_16():
    img = np.array([[100, 200], [300, 400]], dtype=np.uint16)
    out = coerce_stack_depth(img, 16)
    assert np.array_equal(out, img)

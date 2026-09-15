"""Tests for py/qt_image_utils.py grayscale QImage conversion."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from qt_image_utils import numpy_array_to_qimage  # noqa: E402


def test_numpy_array_to_qimage_grayscale_non_contiguous():
    """Non-contiguous grayscale arrays must convert without TypeError (PyQt6)."""
    base = np.zeros((80, 60), dtype=np.uint8)
    sliced = base[::2, ::2]
    assert not sliced.flags["C_CONTIGUOUS"]
    qimg = numpy_array_to_qimage(sliced)
    assert qimg.width() == sliced.shape[1]
    assert qimg.height() == sliced.shape[0]

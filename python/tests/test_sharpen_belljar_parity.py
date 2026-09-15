"""Bell Jar parity tests for Mason Jar sharpen core."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from skimage.filters import unsharp_mask
from skimage.morphology import disk, white_tophat

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

pytest.importorskip("cv2")

import sharpen  # noqa: E402


def belljar_process_file_core(
    img: np.ndarray,
    radius: float,
    amount: float,
    equalize: bool,
) -> np.ndarray:
    """Golden copy of belljar-main/py/sharpen.py process_file filter body."""
    work = np.asarray(img)
    if equalize:
        clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
        work = clahe.apply(work)
        work = sharpen.enhance_contrast(work)
    original_dtype = work.dtype
    work = unsharp_mask(work, radius=radius, amount=amount, preserve_range=True)
    work = white_tophat(work, disk(15))
    return work.astype(original_dtype)


@pytest.mark.parametrize("equalize", [False, True])
def test_sharpen_belljar_core_matches_golden_uint8(equalize: bool) -> None:
    rng = np.random.default_rng(42)
    img = rng.integers(20, 200, (128, 96), dtype=np.uint8)
    radius, amount = 3.0, 2.0
    golden = belljar_process_file_core(img, radius, amount, equalize)
    mason = sharpen.sharpen_image_belljar(img, radius, amount, equalize)
    assert golden.dtype == mason.dtype
    np.testing.assert_array_equal(golden, mason)


@pytest.mark.parametrize("equalize", [False, True])
def test_sharpen_belljar_core_matches_golden_uint16(equalize: bool) -> None:
    rng = np.random.default_rng(7)
    img = (rng.integers(0, 256, (80, 64), dtype=np.uint16) * 257).astype(np.uint16)
    radius, amount = 3.0, 2.0
    golden = belljar_process_file_core(img, radius, amount, equalize)
    mason = sharpen.sharpen_image_belljar(img, radius, amount, equalize)
    assert golden.dtype == mason.dtype
    np.testing.assert_array_equal(golden, mason)


def test_tiled_matches_full_frame_no_equalize(monkeypatch) -> None:
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1000)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PAD", 8)
    rng = np.random.default_rng(99)
    img = rng.integers(10, 240, (48, 48), dtype=np.uint8)
    full = sharpen.sharpen_image_belljar(img, radius=2.0, amount=1.5, equalize=False)
    tiled = sharpen.sharpen_image(img, radius=2.0, amount=1.5, equalize=False)
    np.testing.assert_allclose(full, tiled, rtol=0, atol=1)


def test_tiled_equalize_matches_full_frame(monkeypatch) -> None:
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1000)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PAD", 8)
    rng = np.random.default_rng(11)
    img = rng.integers(1000, 40000, (48, 48), dtype=np.uint16)
    full = sharpen.sharpen_image_belljar(img, radius=2.0, amount=1.5, equalize=True)
    tiled = sharpen.sharpen_image(img, radius=2.0, amount=1.5, equalize=True)
    np.testing.assert_allclose(full, tiled, rtol=0, atol=2)


def test_uint16_output_preserves_dtype() -> None:
    h, w = 64, 64
    ramp = (np.arange(h * w, dtype=np.uint16).reshape(h, w) % 256) * 257
    out = sharpen.sharpen_image(ramp, radius=2.0, amount=1.5, equalize=False)
    assert out.dtype == np.uint16
    assert int(out.max()) > 1000


def test_uint16_equalize_output_dtype() -> None:
    img = (np.ones((32, 32), dtype=np.uint16) * 10000).astype(np.uint16)
    out = sharpen.sharpen_image(img, radius=2.0, amount=1.0, equalize=True)
    assert out.dtype == np.uint16

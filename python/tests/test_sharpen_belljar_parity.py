"""Bell Jar parity tests for PFA Jar sharpen core."""

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
        # Independent legacy reference, including percentile interpolation.
        q = 0.05 / 100
        clipped = np.clip(work.ravel(), np.percentile(work, q), np.percentile(work, 100 - q))
        info = np.iinfo(work.dtype)
        work = np.interp(clipped, (clipped.min(), clipped.max()),
                         (info.min, info.max)).reshape(work.shape).astype(work.dtype)
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
    np.testing.assert_allclose(golden.astype(np.int32), mason.astype(np.int32), rtol=0, atol=1)


@pytest.mark.parametrize("equalize", [False, True])
def test_sharpen_belljar_core_matches_golden_uint16(equalize: bool) -> None:
    rng = np.random.default_rng(7)
    img = (rng.integers(0, 256, (80, 64), dtype=np.uint16) * 257).astype(np.uint16)
    radius, amount = 3.0, 2.0
    golden = belljar_process_file_core(img, radius, amount, equalize)
    mason = sharpen.sharpen_image_belljar(img, radius, amount, equalize)
    assert golden.dtype == mason.dtype
    np.testing.assert_allclose(golden.astype(np.int32), mason.astype(np.int32), rtol=0, atol=1)


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


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("equalize", [False, True])
@pytest.mark.parametrize("shape", [(1, 1), (7, 13), (97, 111)])
def test_amount_zero_exact_parity(dtype, equalize, shape) -> None:
    rng = np.random.default_rng(2026)
    img = rng.integers(0, np.iinfo(dtype).max + 1, shape, dtype=dtype)
    golden = belljar_process_file_core(img, 5, 0, equalize)
    actual = sharpen.sharpen_image_belljar(img, 5, 0, equalize)
    np.testing.assert_array_equal(actual, golden)


def test_amount_zero_tiled_exact_parity(monkeypatch) -> None:
    img = np.random.default_rng(3).integers(0, 256, (135, 149), dtype=np.uint8)
    golden = belljar_process_file_core(img, 5, 0, False)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    np.testing.assert_array_equal(sharpen.sharpen_image(img, 5, 0, False), golden)


@pytest.mark.parametrize("size", [1, 4095, 4096, 1048601])
@pytest.mark.parametrize("kind", ["random", "constant", "sparse"])
def test_uint8_contrast_lut_exact_legacy(size, kind):
    img = np.random.default_rng(8).integers(0, 256, size, dtype=np.uint8)
    if kind == "constant":
        img[:] = 37
    elif kind == "sparse":
        img[:] = 0
        img[-1] = 255
    q = 0.05 / 100
    clipped = np.clip(img, np.percentile(img, q), np.percentile(img, 100 - q))
    expected = np.interp(clipped, (clipped.min(), clipped.max()), (0, 255)).astype(np.uint8)
    np.testing.assert_array_equal(sharpen.enhance_contrast(img), expected)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("equalize", [False, True])
def test_default_float32_tolerance_and_tiled_path(dtype, equalize, monkeypatch) -> None:
    img = np.random.default_rng(2026).integers(0, np.iinfo(dtype).max + 1, (97, 111), dtype=dtype)
    golden = belljar_process_file_core(img, 3, 2, equalize)
    full = sharpen.sharpen_image(img, 3, 2, equalize)
    assert full.dtype == img.dtype
    np.testing.assert_allclose(full.astype(np.int32), golden.astype(np.int32), rtol=0, atol=1)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    tiled = sharpen.sharpen_image(img, 3, 2, equalize)
    # Compare against the legacy tiled pipeline with the same padding, rather
    # than conflating existing tile/full boundary differences with float32.
    work = sharpen._apply_equalize_belljar(img) if equalize else img
    legacy_tiled = np.empty_like(img)
    for y in range(0, img.shape[0], 64):
        for x in range(0, img.shape[1], 64):
            ye, xe = min(img.shape[0], y + 64), min(img.shape[1], x + 64)
            cy, cx = max(0, y - 32), max(0, x - 32)
            crop = work[cy:min(img.shape[0], ye + 32), cx:min(img.shape[1], xe + 32)]
            ref = belljar_process_file_core(crop, 3, 2, False)
            legacy_tiled[y:ye, x:xe] = ref[y-cy:y-cy+ye-y, x-cx:x-cx+xe-x]
    np.testing.assert_allclose(tiled.astype(np.int32), legacy_tiled.astype(np.int32), rtol=0, atol=1)

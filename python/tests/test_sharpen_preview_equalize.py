"""Sharpen wizard preview: full-slice equalize then crop matches batch semantics."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import cv2
import numpy as np
import pytest
import tifffile as tf

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_DIR = REPO_ROOT / "py"
if str(PY_DIR) not in sys.path:
    sys.path.insert(0, str(PY_DIR))

import sharpen  # noqa: E402


def _write_gradient_tiff(path: Path, h: int = 128, w: int = 160) -> None:
    y = np.linspace(0, 255, h, dtype=np.uint8)
    x = np.linspace(0, 255, w, dtype=np.uint8)
    img = np.clip((y[:, None] + x[None, :]) / 2, 0, 255).astype(np.uint8)
    tf.imwrite(str(path), img)


def _preview_roi_via_full_equalize(
    full_uint8: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    radius: float,
    amount: float,
    pad: int = 32,
) -> np.ndarray:
    """Reference path: equalize full frame, crop padded ROI, sharpen without re-equalize."""
    full_eq = sharpen._apply_equalize_belljar(full_uint8)
    y0 = max(0, y - pad)
    x0 = max(0, x - pad)
    y1 = min(full_eq.shape[0], y + h + pad)
    x1 = min(full_eq.shape[1], x + w + pad)
    crop = full_eq[y0:y1, x0:x1]
    filtered = sharpen._sharpen_unsharp_tophat(crop, radius, amount)
    oy = y - y0
    ox = x - x0
    return filtered[oy : oy + h, ox : ox + w]


def _parse_preview_json(capsys) -> dict:
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("PREVIEW_JSON:"):
            return json.loads(line[len("PREVIEW_JSON:") :])
    raise AssertionError("PREVIEW_JSON not emitted")


def test_preview_equalize_full_then_crop_matches_batch_roi(tmp_path, capsys):
    tiff = tmp_path / "slice.tif"
    _write_gradient_tiff(tiff, 128, 160)
    full = sharpen.load_grayscale_uint8(tiff)
    x, y, w, h = 20, 24, 64, 48
    radius, amount = 3.0, 2.0

    expected = _preview_roi_via_full_equalize(full, x, y, w, h, radius, amount)
    batch_roi = sharpen.sharpen_image_belljar(
        full[y : y + h, x : x + w], radius, amount, equalize=True
    )
    # Batch equalizes the ROI crop only (legacy batch tile path on small images);
    # preview equalizes the full slice first — compare against explicit reference.
    assert expected.shape == (h, w)
    assert not np.array_equal(expected, full[y : y + h, x : x + w])

    preview_dir = tmp_path / "meta"
    args = Namespace(
        image=str(tiff),
        x=x,
        y=y,
        w=w,
        h=h,
        radius=radius,
        amount=amount,
        equalize=True,
        preview_dir=str(preview_dir),
    )
    assert sharpen.run_preview(args) == 0
    payload = _parse_preview_json(capsys)
    assert payload["ok"] is True
    assert payload.get("equalizeApplied") is True
    assert payload.get("equalizeSkipped") is False
    png = cv2.imread(payload["previewPath"], cv2.IMREAD_GRAYSCALE)
    got = sharpen.preview_display_sharpen(expected)
    assert png.shape == got.shape
    assert np.allclose(png.astype(np.int16), got.astype(np.int16), atol=1)

    # Sanity: batch ROI differs from full-frame-equalize preview (documents semantics).
    assert not np.allclose(batch_roi.astype(np.float64), expected.astype(np.float64), atol=1e-3)


def test_preview_display_sharpen_no_stretch_on_narrow_range():
    narrow = np.full((8, 8), 12, dtype=np.uint8)
    narrow[2, 2] = 18
    got = sharpen.preview_display_sharpen(narrow)
    assert np.array_equal(got, narrow)
    assert got.max() - got.min() < 32


def test_preview_skips_equalize_when_slide_too_large(tmp_path, capsys, monkeypatch):
    tiff = tmp_path / "large.tif"
    _write_gradient_tiff(tiff, 32, 32)
    monkeypatch.setattr(sharpen, "PREVIEW_EQUALIZE_MAX_PIXELS", 100)

    preview_dir = tmp_path / "meta"
    args = Namespace(
        image=str(tiff),
        x=4,
        y=4,
        w=16,
        h=16,
        radius=3.0,
        amount=2.0,
        equalize=True,
        preview_dir=str(preview_dir),
    )
    assert sharpen.run_preview(args) == 0
    payload = _parse_preview_json(capsys)
    assert payload["ok"] is True
    assert payload.get("equalizeApplied") is False
    assert payload.get("equalizeSkipped") is True
    assert payload.get("equalizeSkipReason") == "slide_too_large"


def test_preview_without_equalize_uses_roi_path(tmp_path, capsys):
    tiff = tmp_path / "slice.tif"
    _write_gradient_tiff(tiff, 64, 80)
    preview_dir = tmp_path / "meta"
    args = Namespace(
        image=str(tiff),
        x=8,
        y=8,
        w=32,
        h=24,
        radius=3.0,
        amount=2.0,
        equalize=False,
        preview_dir=str(preview_dir),
    )
    assert sharpen.run_preview(args) == 0
    payload = _parse_preview_json(capsys)
    assert payload["ok"] is True
    assert payload.get("equalizeApplied") is False
    assert payload.get("equalizeSkipped") is False

"""Tests for per-section align tissue layout detection."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from align_tissue_layout import (  # noqa: E402
    crop_planar_for_hemisphere,
    detect_tissue_layout_from_gray,
    layout_to_hemisphere,
    parse_layout_mode,
)


def _whole_brain_gray(w: int = 256, h: int = 192) -> np.ndarray:
    gray = np.full((h, w), 25, dtype=np.uint8)
    cv2.ellipse(gray, (w // 2, h // 2), (w // 3, h // 3), 0, 0, 360, 220, -1)
    return gray


def _left_hemi_gray(w: int = 256, h: int = 192) -> np.ndarray:
    gray = np.full((h, w), 25, dtype=np.uint8)
    cv2.ellipse(gray, (w // 5, h // 2), (w // 6, h // 3), 0, 0, 360, 220, -1)
    return gray


def test_detect_whole_brain_layout() -> None:
    result = detect_tissue_layout_from_gray(_whole_brain_gray())
    assert result.layout == "whole"
    assert result.hemisphere == "W"
    assert layout_to_hemisphere(result.layout) == "W"
    assert result.metrics["bbox_width_ratio"] > 0.5


def test_detect_left_hemi_layout() -> None:
    result = detect_tissue_layout_from_gray(_left_hemi_gray())
    assert result.layout == "left_hemi"
    assert result.hemisphere == "L"
    assert result.metrics["left_frac"] > 0.75


def test_crop_planar_for_hemisphere() -> None:
    img = np.arange(12, dtype=np.uint8).reshape(3, 4)
    cropped = crop_planar_for_hemisphere(img, "L")
    assert cropped.shape == (3, 2)
    assert np.array_equal(cropped, img[:, :2])
    assert np.array_equal(crop_planar_for_hemisphere(img, "W"), img)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("auto", "auto"),
        ("Auto", "auto"),
        ("True", "whole"),
        ("false", "hemi"),
        ("hemi", "hemi"),
        ("", "auto"),
    ],
)
def test_parse_layout_mode(raw: str, expected: str) -> None:
    assert parse_layout_mode(raw) == expected

"""Regression checks for the bounded-memory CZI preview conversion path."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract as c  # noqa: E402

# czi_extract stages third-party imports only when its CLI starts.
c.np, c.cv2 = np, cv2
preview_downscale_preserve_percentile = c.preview_downscale_preserve_percentile


def expected_uint16(plane: np.ndarray, scale: float) -> np.ndarray:
    lo, hi = (float(v) for v in np.percentile(plane, (2.0, 98.0)))
    if hi <= lo:
        hi = lo + 1.0
    h, w = plane.shape
    small = cv2.resize(
        plane,
        (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float64)
    return np.clip((np.clip(small, lo, hi) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


# uint8 TIFF preview repair remains byte-identical to its prior resize path.
u8 = np.arange(63 * 97, dtype=np.uint8).reshape(63, 97)
actual_u8 = preview_downscale_preserve_percentile(u8, 0.05)
expected_u8 = cv2.resize(u8, (5, 3), interpolation=cv2.INTER_AREA)
assert actual_u8.dtype == np.uint8
assert np.array_equal(actual_u8, expected_u8)

# uint16 CZI preview keeps full-plane percentile bounds but scales only the
# reduced image.  Include outliers and a constant plane for both endpoints.
u16 = (np.arange(501 * 803, dtype=np.uint32).reshape(501, 803) % 15000).astype(np.uint16)
u16[0, 0], u16[-1, -1] = 0, 65535
actual_u16 = preview_downscale_preserve_percentile(u16, 0.05)
assert np.array_equal(actual_u16, expected_uint16(u16, 0.05))
constant = np.full((73, 91), 8192, dtype=np.uint16)
assert preview_downscale_preserve_percentile(constant, 0.05).shape == (4, 5)

print("Preview downscale: uint8 compatibility and uint16 preserve-percentile mapping passed")

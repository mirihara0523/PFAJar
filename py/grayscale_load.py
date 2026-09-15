"""Shared grayscale load + uint8 normalization for preprocess filters."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import tifffile as tf


def to_uint8_grayscale(arr: np.ndarray) -> np.ndarray:
    """Scale a 2D grayscale array to uint8 for OpenCV/skimage filters."""
    raw = np.asarray(arr)
    if raw.dtype == np.uint8:
        return np.ascontiguousarray(raw)
    if raw.dtype == np.uint16:
        return np.ascontiguousarray((raw / 256).astype(np.uint8))
    return np.ascontiguousarray(np.clip(raw, 0, 255).astype(np.uint8))


def load_grayscale_native(path: Path) -> np.ndarray:
    """Read a grayscale TIFF preserving native dtype (uint8/uint16). PNG stays uint8."""
    suffix = path.suffix.lower()
    if suffix == ".png":
        return load_grayscale_uint8(path)
    raw = tf.imread(str(path))
    if raw.ndim > 2:
        if raw.shape[-1] in (3, 4):
            raw = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        else:
            raw = np.max(raw, axis=0)
    return np.ascontiguousarray(raw)


def load_grayscale_uint8(path: Path) -> np.ndarray:
    """Read a grayscale PNG or TIFF and normalize to uint8."""
    suffix = path.suffix.lower()
    if suffix == ".png":
        raw = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if raw is None:
            raise ValueError(f"could not read {path}")
        return np.ascontiguousarray(raw)
    raw = tf.imread(str(path))
    if raw.ndim > 2:
        if raw.shape[-1] in (3, 4):
            raw = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        else:
            raw = np.max(raw, axis=0)
    return to_uint8_grayscale(raw)


def read_image_size(path: Path) -> tuple[int, int]:
    """Return (height, width) without decoding full raster."""
    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    if suffix == ".png":
        full = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if full is None:
            raise ValueError(f"could not read {path}")
        return int(full.shape[0]), int(full.shape[1])
    if suffix in (".tif", ".tiff") or ".ome." in lower_name:
        with tf.TiffFile(str(path)) as tif:
            page = tif.pages[0]
            shape = page.shape
            if len(shape) < 2:
                raise ValueError(f"unexpected TIFF shape {shape}")
            return int(shape[-2]), int(shape[-1])
    full = load_grayscale_uint8(path)
    return int(full.shape[0]), int(full.shape[1])


def _collapse_to_2d(raw: np.ndarray) -> np.ndarray:
    arr = np.asarray(raw)
    if arr.ndim == 2:
        return arr
    if arr.ndim > 2 and arr.shape[-1] in (3, 4):
        return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    if arr.ndim > 2:
        return np.max(arr, axis=0)
    raise ValueError(f"unexpected array ndim={arr.ndim}")


def load_grayscale_uint8_roi(
    path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
    pad: int = 0,
) -> tuple[np.ndarray, int, int, int, int]:
    """Load a padded ROI as uint8.

    Returns ``(crop_uint8, img_h, img_w, x0, y0)`` where ``x0,y0`` is the crop
    origin in full-image coordinates. Uses ``TiffFile`` partial reads for TIFFs.
    """
    pad = max(0, int(pad))
    x = int(x)
    y = int(y)
    w = int(w)
    h = int(h)

    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    is_tiff = suffix in (".tif", ".tiff") or ".ome." in lower_name

    if is_tiff:
        with tf.TiffFile(str(path)) as tif:
            page = tif.pages[0]
            shape = page.shape
            if len(shape) < 2:
                raise ValueError(f"unexpected TIFF shape {shape}")
            img_h, img_w = int(shape[-2]), int(shape[-1])
            y0 = max(0, y - pad)
            x0 = max(0, x - pad)
            y1 = min(img_h, y + h + pad)
            x1 = min(img_w, x + w + pad)
            try:
                mapped = page.asarray(out="memmap")
                raw = np.ascontiguousarray(mapped[y0:y1, x0:x1])
            except (TypeError, ValueError, OSError):
                raw = page.asarray()
                raw = _collapse_to_2d(raw)[y0:y1, x0:x1]
            else:
                raw = _collapse_to_2d(raw)
            crop = to_uint8_grayscale(raw)
            return crop, img_h, img_w, x0, y0

    full = load_grayscale_uint8(path)
    img_h, img_w = int(full.shape[0]), int(full.shape[1])
    y0 = max(0, y - pad)
    x0 = max(0, x - pad)
    y1 = min(img_h, y + h + pad)
    x1 = min(img_w, x + w + pad)
    crop = full[y0:y1, x0:x1]
    return crop, img_h, img_w, x0, y0

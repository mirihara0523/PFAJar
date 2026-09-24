"""Shared rotate/flip geometry helpers and TIFF/PNG array I/O.

Extracted from apply_geometry.py so that other scripts (currently
geometry_fingerprint_probe.py, geometry_orientation_match.py, and — per
handoffs/design-docs/inline-geometry-extract-design.md — a planned inline
geometry apply inside czi_extract.py) can reuse the same rotate/flip and
array-write logic without importing apply_geometry.py's CLI/argparse module
as a library. Pure refactor: no behavior change from what apply_geometry.py
previously did inline.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import tifffile as tiff


def compose_ops(rotate: int, flip_x: bool, flip_y: bool):
    ops = []
    rot = int(rotate or 0) % 360
    if rot in (90, 180, 270):
        ops.append(("rotate", rot))
    if flip_x:
        ops.append(("flip_x", True))
    if flip_y:
        ops.append(("flip_y", True))
    return ops


def ops_from_string_list(op_list: list) -> list:
    """Map JS geometry.ops entries (rot90, flipX, flipY) to internal op tuples."""
    ops: list = []
    for entry in op_list or []:
        if entry == "rot90":
            ops.append(("rotate", 90))
        elif entry == "flipX":
            ops.append(("flip_x", True))
        elif entry == "flipY":
            ops.append(("flip_y", True))
    return ops


def compose_ops_from_spec(spec: dict) -> list:
    """Use ordered spec.ops when present; else legacy rotate/flip flags."""
    if not spec:
        return []
    raw_ops = spec.get("ops")
    if raw_ops:
        return ops_from_string_list(raw_ops)
    return compose_ops(spec.get("rotate", 0), spec.get("flipX"), spec.get("flipY"))


def apply_ops_to_array(arr: np.ndarray, ops: list) -> np.ndarray:
    # Rotation k matches CSS clockwise in js/orient_geometry.js geometryCssTransform.
    out = arr
    for op, val in ops:
        if op == "rotate":
            if val == 90:
                out = np.rot90(out, k=-1)
            elif val == 180:
                out = np.rot90(out, k=2)
            elif val == 270:
                out = np.rot90(out, k=1)
        elif op == "flip_x":
            out = np.fliplr(out)
        elif op == "flip_y":
            out = np.flipud(out)
    return out


def _read_tiff_array(path: Path) -> np.ndarray:
    """Read TIFF via TiffFile (path-based; avoids io_fairshare BytesIO on large NAS files)."""
    with tiff.TiffFile(str(path)) as tf:
        pages = tf.pages
        if not pages:
            raise ValueError(f"No TIFF pages in {path.name}")
        if len(pages) == 1:
            return np.asarray(pages[0].asarray())
        planes = [np.asarray(p.asarray()) for p in pages]
        return np.stack(planes, axis=0)


def _write_tiff_array(path: Path, arr: np.ndarray) -> None:
    # Keep z-stacks (original_scans) compressed to match czi_extract and stop a
    # geometry apply from re-inflating them; leave 03_max uncompressed so
    # grayscale_load's memmap ROI fast-path (sharpen/tophat/basic) still works.
    # zlib is lossless — pixel values are unchanged. Falls back to uncompressed
    # on older tifffile that lacks the compression kwarg.
    # apply_ops_to_array returns np.rot90/fliplr/flipud views for 2D input
    # (non-contiguous strides); the zlib encoder needs a contiguous buffer or
    # it raises OSError [Errno 22] Invalid argument. Z-stacks are unaffected
    # because np.stack() in _read_tiff_array/transform_file already copies
    # into a contiguous array, but a bare 2D array must be normalized here.
    arr = np.ascontiguousarray(arr)
    parts = {p.lower() for p in path.parts}
    compress = "original_scans" in parts and "03_max" not in parts
    if compress:
        try:
            tiff.imwrite(str(path), arr, photometric="minisblack", compression="zlib")
            return
        except (TypeError, ValueError):
            pass
    tiff.imwrite(str(path), arr, photometric="minisblack")


def _read_image_array(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".png":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not read {path}")
        return np.asarray(img)
    return _read_tiff_array(path)


def _write_image_array(path: Path, arr: np.ndarray) -> None:
    parts = {p.lower() for p in path.parts}
    if "00_dapi" in parts and path.suffix.lower() != ".png":
        raise ValueError(f"00_dapi accepts PNG only, not {path}")
    if path.suffix.lower() == ".png":
        cv2.imwrite(str(path), arr)
        return
    _write_tiff_array(path, arr)

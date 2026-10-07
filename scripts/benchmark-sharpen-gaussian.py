"""Experimental Gaussian alternatives; never changes product filters."""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import tifffile
from skimage.filters import unsharp_mask
from skimage.morphology import disk, white_tophat


def top(work, dtype):
    return cv2.morphologyEx(work.astype(np.float32), cv2.MORPH_TOPHAT,
                           disk(15), borderType=cv2.BORDER_REFLECT).astype(dtype)


def run(img, name):
    radius, amount = 3., 2.
    start = time.perf_counter()
    ref = unsharp_mask(img, radius=radius, amount=amount, preserve_range=True)
    reference_seconds = time.perf_counter() - start
    final_ref = top(ref, img.dtype)
    legacy_final = white_tophat(ref, disk(15)).astype(img.dtype)
    rows = []
    size = 2 * int(4 * radius + 0.5) + 1
    coords = np.arange(size, dtype=np.float64) - size // 2
    kernel = np.exp(-0.5 * (coords / radius) ** 2)
    kernel /= kernel.sum()
    for method in ("GaussianBlur64", "sepFilter64_scipy_kernel", "GaussianBlur32"):
        start = time.perf_counter()
        src = img.astype(np.float32 if method.endswith("32") else np.float64)
        if method.startswith("sepFilter"):
            blurred = cv2.sepFilter2D(src, -1, kernel, kernel, borderType=cv2.BORDER_REFLECT)
        else:
            blurred = cv2.GaussianBlur(src, (size, size), radius, sigmaY=radius,
                                       borderType=cv2.BORDER_REFLECT)
        candidate = src + amount * (src - blurred)
        seconds = time.perf_counter() - start
        final = top(candidate, img.dtype)
        diff = np.abs(final.astype(np.int32) - final_ref.astype(np.int32))
        legacy_diff = np.abs(final.astype(np.int32) - legacy_final.astype(np.int32))
        row = {"fixture": name, "shape": list(img.shape), "dtype": str(img.dtype),
               "method": method, "reference_unsharp_seconds": reference_seconds,
               "candidate_unsharp_seconds": seconds, "speedup": reference_seconds / seconds,
               "unsharp_max_abs": float(np.max(np.abs(ref - candidate))),
               "final_changed_pixels": int(np.count_nonzero(diff)),
               "final_max_abs": int(diff.max()), "final_over_one_pixels": int(np.count_nonzero(diff > 1)),
               "legacy_max_abs": int(legacy_diff.max()),
               "legacy_changed_pixels": int(np.count_nonzero(legacy_diff)),
               "legacy_over_one_pixels": int(np.count_nonzero(legacy_diff > 1))}
        print(json.dumps(row), flush=True)
        rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("output")
    args = parser.parse_args()
    reports = []
    rng = np.random.default_rng(2026)
    for dtype in (np.uint8, np.uint16):
        maximum = np.iinfo(dtype).max
        for name in ("random", "sparse_border", "gradient", "constant"):
            img = rng.integers(0, maximum + 1, (384, 512), dtype=dtype)
            if name == "sparse_border":
                img[:] = 0
                img[::13, ::17] = maximum
                img[0, :] = maximum
            elif name == "gradient":
                img[:] = np.linspace(0, maximum, img.shape[1]).astype(dtype)
            elif name == "constant":
                img[:] = maximum
            reports.extend(run(img, name))
    img = tifffile.imread(args.image)
    if img.ndim != 2:
        raise ValueError("Expected 2D image")
    for eq in (False, True):
        work = img
        if eq:
            import importlib.util
            spec = importlib.util.spec_from_file_location("contrast_experiment", Path(__file__).with_name("benchmark-sharpen-contrast-lut.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            work = module.histogram_lut(cv2.createCLAHE(clipLimit=4, tileGridSize=(8, 8)).apply(img))
        # A production-sized tile includes edges and representative tissue.
        reports.extend(run(work[:4128, :4128], "real_tile_equalize_" + str(eq)))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=2), encoding="utf-8")

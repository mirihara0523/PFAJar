"""Experimental exact integer histogram/LUT contrast; no product edits."""
import argparse
import json
import time
from pathlib import Path
from skimage.morphology import disk

import cv2
import numpy as np
import tifffile


def legacy(image, saturation_level=0.05):
    flat = image.ravel()
    q = saturation_level / 100
    lo = np.percentile(flat, q)
    hi = np.percentile(flat, 100 - q)
    clipped = np.clip(flat, lo, hi)
    info = np.iinfo(image.dtype)
    return np.interp(clipped, (clipped.min(), clipped.max()),
                     (info.min, info.max)).reshape(image.shape).astype(image.dtype)


def histogram_lut(image, saturation_level=0.05):
    info = np.iinfo(image.dtype)
    counts = np.zeros(info.max + 1, dtype=np.int64)
    flat = image.ravel()
    for start in range(0, flat.size, 1048576):
        counts += np.bincount(flat[start:start + 1048576], minlength=counts.size)
    cumulative = np.cumsum(counts)
    def percentile(q):
        rank = (flat.size - 1) * (q / 100.0)
        lower, upper = int(np.floor(rank)), int(np.ceil(rank))
        a = int(np.searchsorted(cumulative, lower, side="right"))
        b = int(np.searchsorted(cumulative, upper, side="right"))
        t = rank - lower
        return b - (b - a) * (1 - t) if t >= .5 else a + (b - a) * t
    q = saturation_level / 100
    lo, hi = percentile(q), percentile(100 - q)
    occupied = np.flatnonzero(counts)
    low = np.clip(float(occupied[0]), lo, hi)
    high = np.clip(float(occupied[-1]), lo, hi)
    values = np.clip(np.arange(info.max + 1, dtype=np.float64), lo, hi)
    lut = np.interp(values, (low, high), (info.min, info.max)).astype(image.dtype)
    return lut[image]


def compare(image, name, saturation=0.05):
    start = time.perf_counter()
    old = legacy(image, saturation)
    old_seconds = time.perf_counter() - start
    start = time.perf_counter()
    new = histogram_lut(image, saturation)
    new_seconds = time.perf_counter() - start
    diff = np.abs(old.astype(np.int32) - new.astype(np.int32))
    report = {"fixture": name, "dtype": str(image.dtype), "pixels": image.size,
              "saturation_level": saturation, "changed_pixels": int(np.count_nonzero(diff)),
              "max_absolute_difference": int(diff.max()),
              "old_seconds": old_seconds, "new_seconds": new_seconds}
    print(json.dumps(report), flush=True)
    return report


def compare_downstream(image):
    old, new = legacy(image), histogram_lut(image)
    changed = 0
    maximum = 0
    h, w = image.shape
    def filter_tile(tile):
        src = tile.astype(np.float32)
        blurred = cv2.GaussianBlur(src, (25, 25), 3, sigmaY=3, borderType=cv2.BORDER_REFLECT)
        unsharp = src + 2 * (src - blurred)
        return cv2.morphologyEx(unsharp, cv2.MORPH_TOPHAT, disk(15),
                                borderType=cv2.BORDER_REFLECT).astype(image.dtype)
    for y in range(0, h, 4096):
        for x in range(0, w, 4096):
            ye, xe = min(h, y + 4096), min(w, x + 4096)
            cy, cx = max(0, y - 32), max(0, x - 32)
            region = np.s_[cy:min(h, ye + 32), cx:min(w, xe + 32)]
            a, b = filter_tile(old[region]), filter_tile(new[region])
            crop = np.s_[y-cy:y-cy+ye-y, x-cx:x-cx+xe-x]
            diff = np.abs(a[crop].astype(np.int32) - b[crop].astype(np.int32))
            changed += int(np.count_nonzero(diff))
            maximum = max(maximum, int(diff.max()))
            print(f"Downstream compared tile x={x} y={y}", flush=True)
    result = {"fixture": "real_downstream_gaussian32_tophat32", "dtype": str(image.dtype),
              "pixels": image.size, "changed_pixels": changed, "max_absolute_difference": maximum}
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("output")
    parser.add_argument("--downstream", action="store_true")
    args = parser.parse_args()
    rng = np.random.default_rng(2026)
    reports = []
    for dtype in (np.uint8, np.uint16):
        maximum = np.iinfo(dtype).max
        for size in (1, 2, 101, 1048601):
            arr = rng.integers(0, maximum + 1, size, dtype=dtype)
            for sat in (0.05, 5):
                reports.append(compare(arr, "random", sat))
        for name, arr in (("constant_zero", np.zeros(256, dtype=dtype)),
                          ("constant_max", np.full(256, maximum, dtype=dtype)),
                          ("sparse", np.concatenate([np.zeros(8191, dtype=dtype), np.array([maximum], dtype=dtype)]))):
            reports.append(compare(arr, name))
    img = tifffile.imread(args.image)
    if img.ndim != 2:
        raise ValueError("Expected 2D image")
    work = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8)).apply(img)
    reports.append(compare(work, "real_CLAHE_output"))
    if args.downstream:
        reports.append(compare_downstream(work))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    raise SystemExit(0 if all(r["changed_pixels"] == 0 for r in reports) else 1)

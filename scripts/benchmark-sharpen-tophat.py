"""Read-only experimental benchmark; does not modify product filters."""
import argparse
import json
import time

import cv2
import numpy as np
from scipy.ndimage import grey_opening
from skimage.filters import unsharp_mask
from skimage.morphology import disk, white_tophat


def measure(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def compare(ref, out, dtype):
    final_ref, final_out = ref.astype(dtype), out.astype(dtype)
    return {
        "float_max_abs": float(np.max(np.abs(ref - out))),
        "changed_output_pixels": int(np.count_nonzero(final_ref != final_out)),
        "output_max_abs": int(np.max(np.abs(final_ref.astype(np.int64) - final_out.astype(np.int64)))),
    }


def run(img, name):
    footprint = disk(15)
    rows = []
    for amount in (0, 2):
        work, unsharp_seconds = measure(lambda: unsharp_mask(img, radius=5, amount=amount, preserve_range=True))
        ref, ref_seconds = measure(lambda: white_tophat(work, footprint))
        candidates = {
            "scipy_same_disk": lambda: work - grey_opening(work, footprint=footprint, mode="reflect"),
            "opencv_float64_same_disk": lambda: cv2.morphologyEx(
                work, cv2.MORPH_TOPHAT, footprint,
                borderType=cv2.BORDER_REFLECT),
            "opencv_float32_same_disk": lambda: cv2.morphologyEx(
                work.astype(np.float32), cv2.MORPH_TOPHAT, footprint,
                borderType=cv2.BORDER_REFLECT),
        }
        if amount == 0:
            candidates["skip_unsharp_integer_disk"] = lambda: white_tophat(img, footprint)
            candidates["skip_unsharp_opencv_integer"] = lambda: cv2.morphologyEx(
                img, cv2.MORPH_TOPHAT, footprint, borderType=cv2.BORDER_REFLECT)
        for method, fn in candidates.items():
            out, seconds = measure(fn)
            rows.append({"fixture": name, "shape": list(img.shape), "dtype": str(img.dtype),
                         "amount": amount, "method": method,
                         "unsharp_seconds": round(unsharp_seconds, 4),
                         "reference_tophat_seconds": round(ref_seconds, 4),
                         "candidate_seconds": round(seconds, 4),
                         "speedup": round(ref_seconds / seconds, 2), **compare(ref, out, img.dtype)})
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=384)
    parser.add_argument("--image")
    args = parser.parse_args()
    rng = np.random.default_rng(1234)
    fixtures = []
    for dtype in (np.uint8, np.uint16):
        maximum = np.iinfo(dtype).max
        img = rng.integers(0, maximum + 1, (args.size, args.size), dtype=dtype)
        fixtures.append((img, "random_edges"))
        sparse = np.zeros_like(img)
        sparse[::31, ::17] = maximum
        sparse[0, :] = maximum
        fixtures.append((sparse, "sparse_border"))
        gradient = np.tile(np.linspace(0, maximum, args.size).astype(dtype), (args.size, 1))
        fixtures.append((gradient, "gradient"))
    if args.image:
        import tifffile
        img = tifffile.imread(args.image)
        if img.ndim != 2:
            raise ValueError("Expected a 2D image")
        fixtures.append((img[:args.size, :args.size], "real_top_left"))
        y, x = img.shape[0] // 2, img.shape[1] // 2
        fixtures.append((img[y:y + args.size, x:x + args.size], "real_center"))
    for img, name in fixtures:
        for row in run(img, name):
            print(json.dumps(row), flush=True)

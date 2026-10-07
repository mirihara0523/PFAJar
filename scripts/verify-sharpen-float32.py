"""Experimental Radius 3/Amount 2 comparison; product code is unchanged."""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import tifffile
from skimage.filters import unsharp_mask
from skimage.morphology import disk, white_tophat


def filters(img):
    start = time.perf_counter()
    work = unsharp_mask(img, radius=3, amount=2, preserve_range=True)
    unsharp_seconds = time.perf_counter() - start
    start = time.perf_counter()
    old_float = white_tophat(work, disk(15))
    old = old_float.astype(img.dtype)
    old_seconds = time.perf_counter() - start
    start = time.perf_counter()
    new_float = cv2.morphologyEx(work.astype(np.float32), cv2.MORPH_TOPHAT,
                               disk(15), borderType=cv2.BORDER_REFLECT)
    new = new_float.astype(img.dtype)
    new_seconds = time.perf_counter() - start
    changed = old != new
    float_details = {"max_distance_to_integer": float(np.max(
        np.abs(old_float[changed] - np.rint(old_float[changed])))) if np.any(changed) else 0.0,
        "max_float_difference": float(np.max(np.abs(old_float[changed] - new_float[changed]))) if np.any(changed) else 0.0}
    return old, new, unsharp_seconds, old_seconds, new_seconds, float_details


def metrics(old, new):
    diff = np.abs(old.astype(np.int32) - new.astype(np.int32))
    return {"pixels": old.size, "changed_pixels": int(np.count_nonzero(diff)),
            "changed_percent": float(np.count_nonzero(diff) * 100 / old.size),
            "max_absolute_difference": int(diff.max()),
            "over_one_pixels": int(np.count_nonzero(diff > 1))}


parser = argparse.ArgumentParser()
parser.add_argument("image")
parser.add_argument("output")
parser.add_argument("--equalize", action="store_true")
args = parser.parse_args()
if args.equalize:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
    from sharpen import _apply_equalize_belljar
outdir = Path(args.output)
outdir.mkdir(parents=True, exist_ok=True)
synthetic = []
rng = np.random.default_rng(2026)
for dtype in (np.uint8, np.uint16):
    maximum = np.iinfo(dtype).max
    for kind in ("random", "sparse", "gradient", "constant"):
        img = rng.integers(0, maximum + 1, (256, 320), dtype=dtype)
        if kind == "sparse":
            img[:] = 0
            img[::13, ::17] = maximum
            img[0, :] = maximum
        elif kind == "gradient":
            img[:] = np.linspace(0, maximum, img.shape[1]).astype(dtype)
        elif kind == "constant":
            img[:] = maximum
        if args.equalize:
            img = _apply_equalize_belljar(img)
        old, new, u, o, n, details = filters(img)
        row = {"fixture": kind, "dtype": str(img.dtype), **metrics(old, new), **details}
        synthetic.append(row)
        print(json.dumps(row), flush=True)
img = tifffile.imread(args.image)
if img.ndim != 2:
    raise ValueError("Expected 2D image")
h, w = img.shape
equalize_seconds = 0.0
if args.equalize:
    start = time.perf_counter()
    img = _apply_equalize_belljar(img)
    equalize_seconds = time.perf_counter() - start
    print(f"Equalize completed in {equalize_seconds:.3f}s", flush=True)
old, new = np.empty_like(img), np.empty_like(img)
timing = {"unsharp_seconds": 0., "old_tophat_seconds": 0., "new_tophat_seconds": 0.}
float_details = {"max_distance_to_integer": 0.0, "max_float_difference": 0.0}
for y in range(0, h, 4096):
    for x in range(0, w, 4096):
        ye, xe = min(h, y + 4096), min(w, x + 4096)
        cy, cx = max(0, y - 32), max(0, x - 32)
        crop = img[cy:min(h, ye + 32), cx:min(w, xe + 32)]
        ref, candidate, u, o, n, details = filters(crop)
        for key in float_details:
            float_details[key] = max(float_details[key], details[key])
        timing["unsharp_seconds"] += u
        timing["old_tophat_seconds"] += o
        timing["new_tophat_seconds"] += n
        oy, ox = y - cy, x - cx
        old[y:ye, x:xe] = ref[oy:oy + ye - y, ox:ox + xe - x]
        new[y:ye, x:xe] = candidate[oy:oy + ye - y, ox:ox + xe - x]
        print(f"Compared tile x={x} y={y}", flush=True)
seams = np.zeros(img.shape, dtype=bool)
for y in range(4096, h, 4096):
    seams[y-2:y+2, :] = True
for x in range(4096, w, 4096):
    seams[:, x-2:x+2] = True
report = {"source": args.image, "radius": 3, "amount": 2, "equalize": args.equalize,
          "equalize_seconds": equalize_seconds,
          "synthetic": synthetic, "real": metrics(old, new),
          "tile_boundaries": metrics(old[seams], new[seams]), **timing}
changed = old != new
delta = new[changed].astype(np.int32) - old[changed].astype(np.int32)
maximum = np.iinfo(img.dtype).max
bins = np.linspace(0, maximum + 1, 9)
all_counts = np.histogram(old, bins=bins)[0]
changed_counts = np.histogram(old[changed], bins=bins)[0]
report["difference_analysis"] = {
    "brighter": int(np.count_nonzero(delta > 0)),
    "darker": int(np.count_nonzero(delta < 0)),
    "changed_old_min": int(old[changed].min()) if changed.any() else None,
    "changed_old_max": int(old[changed].max()) if changed.any() else None,
    "changed_source_min": int(img[changed].min()) if changed.any() else None,
    "changed_source_max": int(img[changed].max()) if changed.any() else None,
    "brightness_bins": [{"low": float(bins[i]), "high_exclusive": float(bins[i+1]),
                         "all_pixels": int(all_counts[i]), "changed_pixels": int(changed_counts[i]),
                         "changed_percent": float(100 * changed_counts[i] / all_counts[i]) if all_counts[i] else 0.0}
                        for i in range(8)],
    **float_details,
}
report["passed"] = all(r["over_one_pixels"] == 0 for r in synthetic) and report["real"]["over_one_pixels"] == 0
for label, arr in (("Radius3-Amount2-old", old), ("Radius3-Amount2-float32", new)):
    scale = min(1, 1600 / max(w, h))
    preview = cv2.resize(arr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(outdir / f"{Path(args.image).stem} {label}.png"), preview):
        raise RuntimeError("PNG save failed")
(outdir / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(0 if report["passed"] else 1)

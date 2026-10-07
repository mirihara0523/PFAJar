"""Compare all pixels of the old/new tiled filter and save review PNGs."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import sharpen


parser = argparse.ArgumentParser()
parser.add_argument("image")
parser.add_argument("output")
args = parser.parse_args()
img = tifffile.imread(args.image)
if img.ndim != 2:
    raise ValueError("Expected 2D image")
h, w = img.shape
old = np.empty_like(img)
new = np.empty_like(img)
times = {"old_seconds": 0.0, "new_seconds": 0.0}
for y in range(0, h, sharpen.TILED_SHARPEN_TILE):
    for x in range(0, w, sharpen.TILED_SHARPEN_TILE):
        ye, xe = min(h, y + sharpen.TILED_SHARPEN_TILE), min(w, x + sharpen.TILED_SHARPEN_TILE)
        cy, cx = max(0, y - 32), max(0, x - 32)
        crop = img[cy:min(h, ye + 32), cx:min(w, xe + 32)]
        start = time.perf_counter()
        ref = white_tophat(unsharp_mask(crop, radius=5, amount=0, preserve_range=True), disk(15)).astype(img.dtype)
        times["old_seconds"] += time.perf_counter() - start
        start = time.perf_counter()
        candidate = sharpen._sharpen_unsharp_tophat(crop, 5, 0)
        times["new_seconds"] += time.perf_counter() - start
        oy, ox = y - cy, x - cx
        old[y:ye, x:xe] = ref[oy:oy + ye - y, ox:ox + xe - x]
        new[y:ye, x:xe] = candidate[oy:oy + ye - y, ox:ox + xe - x]
        print(f"Compared tile x={x} y={y}", flush=True)
outdir = Path(args.output)
outdir.mkdir(parents=True, exist_ok=True)
report = {"source": args.image, "shape": [h, w], "pixels": img.size,
          "amount": 0, "radius": 5, "equalize": False,
          "changed_pixels": int(np.count_nonzero(old != new)),
          "max_absolute_difference": int(np.max(np.abs(old.astype(np.int32) - new.astype(np.int32)))),
          **times}
for name, arr in (("Original", img), ("Amount0-old", old), ("Amount0-new", new)):
    preview = cv2.resize(arr, (round(w * min(1, 1600 / max(w, h))), round(h * min(1, 1600 / max(w, h)))), interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(outdir / f"{Path(args.image).stem} {name}.png"), preview):
        raise RuntimeError("PNG save failed")
(outdir / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(0 if report["changed_pixels"] == 0 else 1)

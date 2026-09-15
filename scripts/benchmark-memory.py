"""Synthetic memory study for CZI/MAX candidates; does not modify production code."""
from __future__ import annotations
import argparse, gc, tempfile, tracemalloc
from pathlib import Path
import numpy as np
import tifffile

def measure(fn):
    gc.collect(); tracemalloc.start()
    result = fn()
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    return result, peak

def all_at_once(path):
    arr = tifffile.imread(path)
    return np.max(arr, axis=0)

def streaming(path):
    # Candidate for a paged TIFF: only one plane plus the output remains live.
    with tifffile.TiffFile(path) as tif:
        pages = tif.pages
        out = None
        for page in pages:
            plane = page.asarray()
            out = plane.copy() if out is None else np.maximum(out, plane, out=out)
        return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--z", type=int, default=48); ap.add_argument("--height", type=int, default=1024); ap.add_argument("--width", type=int, default=1024); args = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "synthetic_stack.tif"
        rng = np.random.default_rng(7)
        stack = rng.integers(0, 65536, (args.z, args.height, args.width), dtype=np.uint16)
        tifffile.imwrite(path, stack, photometric="minisblack")
        expected = np.max(stack, axis=0)
        a, pa = measure(lambda: all_at_once(path))
        b, pb = measure(lambda: streaming(path))
        print(f"stack={stack.nbytes/1048576:.1f} MiB shape={stack.shape}")
        print(f"all_at_once_peak={pa/1048576:.1f} MiB")
        print(f"streaming_peak={pb/1048576:.1f} MiB")
        print(f"results_equal={np.array_equal(a, expected) and np.array_equal(b, expected)}")
        print(f"peak_reduction={(1-pb/pa)*100:.1f}%")

if __name__ == "__main__": main()

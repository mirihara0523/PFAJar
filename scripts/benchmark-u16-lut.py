"""Verify and benchmark an exact uint16→uint8 lookup-table candidate.

This diagnostic does not modify application code or image files.  It compares
the current integer formula with a 65,536-entry LUT for every uint16 value and
then times both paths on a configurable plane-sized synthetic array.
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
# This is a CPU-only synthetic benchmark; do not register a network I/O job.
os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
import czi_extract as extract  # noqa: E402

extract.np = np


def make_lut(peak: int) -> np.ndarray:
    values = np.arange(65536, dtype=np.uint32)
    return np.minimum((values * 255) // max(1, int(peak)), 255).astype(np.uint8)


def lut_convert(plane: np.ndarray, lut: np.ndarray) -> np.ndarray:
    return lut[np.asarray(plane)]


def measure(fn):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = fn()
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, seconds, peak


def median_seconds(fn, repeats: int) -> float:
    values = []
    for _ in range(repeats):
        out, seconds, _ = measure(fn)
        # Keep the write observable until after timing, then free it.
        assert out.dtype == np.uint8
        del out
        values.append(seconds)
    return float(np.median(values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--height", type=int, default=3096)
    parser.add_argument("--width", type=int, default=5096)
    parser.add_argument("--peak", type=int, default=10586)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.height < 1 or args.width < 1 or args.repeats < 1:
        parser.error("dimensions and repeats must be positive")

    # Exhaustive equivalence proves the LUT implements the same floor-based
    # mapping for all possible uint16 source values, not merely one image.
    all_values = np.arange(65536, dtype=np.uint16)
    checked_peaks = [1, 2, 255, 4095, 6327, 10586, 16383, 65535]
    for peak in checked_peaks:
        current = extract._uint16_to_uint8_stack_linear(all_values, scale_max=peak)
        assert np.array_equal(current, lut_convert(all_values, make_lut(peak))), peak

    rng = np.random.default_rng(20260912)
    plane = rng.integers(0, 65536, size=(args.height, args.width), dtype=np.uint16)
    lut_start = time.perf_counter()
    lut = make_lut(args.peak)
    lut_build_seconds = time.perf_counter() - lut_start
    current, current_seconds, current_peak = measure(
        lambda: extract._uint16_to_uint8_stack_linear(plane, scale_max=args.peak)
    )
    candidate, lut_seconds, lut_peak = measure(lambda: lut_convert(plane, lut))
    assert np.array_equal(current, candidate)
    del current, candidate
    current_median = median_seconds(
        lambda: extract._uint16_to_uint8_stack_linear(plane, scale_max=args.peak), args.repeats
    )
    lut_median = median_seconds(lambda: lut_convert(plane, lut), args.repeats)

    print(f"shape={plane.shape} pixels={plane.size} source_mib={plane.nbytes / 1048576:.1f}")
    print(f"peak={args.peak} exhaustive_peaks={checked_peaks} outputs_equal=true")
    print(f"lut_build_seconds={lut_build_seconds:.6f} lut_bytes={lut.nbytes}")
    print(f"current_first_seconds={current_seconds:.6f} current_peak_mib={current_peak / 1048576:.1f}")
    print(f"lut_first_seconds={lut_seconds:.6f} lut_peak_mib={lut_peak / 1048576:.1f}")
    print(f"current_median_seconds={current_median:.6f}")
    print(f"lut_median_seconds={lut_median:.6f}")
    print(f"speedup={current_median / max(lut_median, 1e-9):.3f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure exact versus sampled uint16 peak scans on one CZI channel.

Read-only diagnostic: it never writes the project bundle or CZI source.  Run
with Mason Jar's runtime Python so the installed aicspylibczi is used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
from czi_common import read_czi_plane, z_indices_with_data  # noqa: E402


def _histogram(plane: np.ndarray) -> np.ndarray:
    arr = np.asarray(plane)
    if arr.dtype != np.uint16:
        arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
    return np.bincount(arr.ravel(), minlength=65536).astype(np.uint64, copy=False)


def compare_peak_histogram(hist: np.ndarray, exact_peak: int, sampled_peak: int) -> dict:
    """Compare two uint16→uint8 linear mappings without retaining planes."""
    exact_peak = max(1, int(exact_peak))
    sampled_peak = max(1, int(sampled_peak))
    values = np.arange(hist.size, dtype=np.uint64)
    exact_u8 = np.minimum((values * 255) // exact_peak, 255)
    sampled_u8 = np.minimum((values * 255) // sampled_peak, 255)
    return {
        "exact_peak": exact_peak,
        "sampled_peak": sampled_peak,
        "peak_delta": sampled_peak - exact_peak,
        "peak_ratio": sampled_peak / exact_peak,
        "pixels_total": int(hist.sum()),
        "sampled_scale_saturated_pixels": int(hist[sampled_peak + 1 :].sum()),
        "u8_mapping_changed_pixels": int(hist[exact_u8 != sampled_u8].sum()),
    }


def measure(czi_path: Path, scene: int, channel: int, scale: float, z_values: list[int]) -> dict:
    from aicspylibczi import CziFile

    czi = CziFile(str(czi_path))
    histogram = np.zeros(65536, dtype=np.uint64)
    exact_peak = 1
    exact_start = time.perf_counter()
    shapes = []
    try:
        for z in z_values:
            plane = read_czi_plane(czi, scene, z, channel)
            arr = np.asarray(plane)
            shapes.append(list(arr.shape))
            exact_peak = max(exact_peak, int(arr.max(initial=0)))
            histogram += _histogram(arr)
        exact_seconds = time.perf_counter() - exact_start

        sampled_peak = 1
        sample_fallbacks = []
        sample_start = time.perf_counter()
        for z in z_values:
            try:
                plane = read_czi_plane(
                    czi,
                    scene,
                    z,
                    channel,
                    sample_scale=scale,
                    allow_tile_composite=False,
                )
            except Exception as exc:  # preserve the import fallback semantics
                sample_fallbacks.append({"z": z, "reason": repr(exc)})
                plane = read_czi_plane(czi, scene, z, channel)
            sampled_peak = max(sampled_peak, int(np.asarray(plane).max(initial=0)))
        sampled_seconds = time.perf_counter() - sample_start
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()

    report = compare_peak_histogram(histogram, exact_peak, sampled_peak)
    report.update(
        {
            "czi": str(czi_path),
            "scene": scene,
            "channel": channel,
            "z_indices": z_values,
            "full_plane_shapes": sorted({tuple(s) for s in shapes}),
            "sample_scale": scale,
            "exact_scan_seconds": round(exact_seconds, 3),
            "sampled_scan_seconds": round(sampled_seconds, 3),
            "scan_seconds_saved": round(exact_seconds - sampled_seconds, 3),
            "sample_fallbacks": sample_fallbacks,
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("czi", type=Path)
    parser.add_argument("--scene", type=int, default=0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--scale", type=float, default=0.05)
    parser.add_argument("--z", type=int, nargs="*", help="Optional explicit Z indices")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    args = parser.parse_args()
    if not args.czi.is_file():
        parser.error(f"CZI not found: {args.czi}")
    if not 0.01 <= args.scale <= 1.0:
        parser.error("--scale must be in [0.01, 1.0]")

    if args.z:
        z_values = args.z
    else:
        from aicspylibczi import CziFile
        czi = CziFile(str(args.czi))
        try:
            z_values = z_indices_with_data(czi, args.scene, args.channel)
        finally:
            close = getattr(czi, "close", None)
            if callable(close):
                close()
    rendered = json.dumps(measure(args.czi, args.scene, args.channel, args.scale, z_values), indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote peak-mode report: {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Render exact and sampled-peak previews from one CZI channel.

This is a read-only diagnostic for assessing ``uint16_peak_sample_scale``.
It never creates or changes a Mason Jar bundle.  It writes PNG previews and a
JSON manifest only in the explicitly supplied output directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
from czi_common import read_czi_plane, z_indices_with_data  # noqa: E402


def _sample_p99(plane: np.ndarray, max_pixels: int = 262_144) -> float:
    """Match import preview selection without retaining a Z-stack."""
    arr = np.asarray(plane)
    stride = max(1, int(np.ceil(np.sqrt(arr.size / max_pixels))))
    return float(np.percentile(arr[::stride, ::stride], 99))


def _to_uint8(plane: np.ndarray, peak: int) -> np.ndarray:
    """Use the same integer uint16→uint8 mapping as CZI import."""
    arr = np.asarray(plane)
    peak = max(1, int(peak))
    if arr.dtype == np.uint16:
        return np.minimum((arr.astype(np.uint32) * 255) // peak, 255).astype(np.uint8)
    return np.clip(arr.astype(np.float32) * 255.0 / peak, 0, 255).astype(np.uint8)


def _write_preview(path: Path, image: np.ndarray, scale: float) -> None:
    if not 0 < scale <= 1:
        raise ValueError("preview scale must be in (0, 1]")
    h, w = image.shape[:2]
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    preview = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(path), preview):
        raise RuntimeError(f"Could not write {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("czi", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scene", type=int, default=0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--scales", type=float, nargs="+", default=[0.25, 0.50])
    parser.add_argument("--preview-scale", type=float, default=0.10)
    args = parser.parse_args()
    if not args.czi.is_file():
        parser.error(f"CZI not found: {args.czi}")
    if any(not 0.01 <= scale <= 1.0 for scale in args.scales):
        parser.error("--scales values must be in [0.01, 1.0]")

    from aicspylibczi import CziFile

    args.output_dir.mkdir(parents=True, exist_ok=True)
    czi = CziFile(str(args.czi))
    try:
        z_values = z_indices_with_data(czi, args.scene, args.channel)
        exact_peak = 1
        best_z = z_values[0]
        best_score = float("-inf")
        histogram = np.zeros(65536, dtype=np.uint64)
        max_plane = None
        for z in z_values:
            plane = np.asarray(read_czi_plane(czi, args.scene, z, args.channel))
            exact_peak = max(exact_peak, int(plane.max(initial=0)))
            score = _sample_p99(plane)
            if score > best_score:
                best_z, best_score = z, score
            if plane.dtype == np.uint16:
                histogram += np.bincount(plane.ravel(), minlength=65536).astype(np.uint64, copy=False)
            if max_plane is None:
                max_plane = plane.copy()
            else:
                np.maximum(max_plane, plane, out=max_plane)

        sampled_peaks: dict[float, int] = {}
        for scale in args.scales:
            peak = 1
            for z in z_values:
                plane = read_czi_plane(
                    czi, args.scene, z, args.channel,
                    sample_scale=scale, allow_tile_composite=False,
                )
                peak = max(peak, int(np.asarray(plane).max(initial=0)))
            sampled_peaks[scale] = peak

        preview_plane = np.asarray(read_czi_plane(czi, args.scene, best_z, args.channel))
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()

    stem = f"{args.czi.stem}.scene{args.scene}.ch{args.channel}.z{best_z}"
    variants = [("exact", exact_peak)] + [
        (f"sample-{scale:g}", peak) for scale, peak in sampled_peaks.items()
    ]
    manifest = {
        "czi": str(args.czi), "scene": args.scene, "channel": args.channel,
        "z_indices": z_values, "preview_z": best_z, "preview_p99": best_score,
        "exact_peak": exact_peak, "preview_scale": args.preview_scale,
        "max_projection": "raw uint16 Z maximum then identical linear uint8 mapping",
        "variants": [],
    }
    values = np.arange(65536, dtype=np.uint64)
    exact_u8 = np.minimum((values * 255) // exact_peak, 255)
    for label, peak in variants:
        preview_path = args.output_dir / f"{stem}.preview.{label}.png"
        max_path = args.output_dir / f"{stem}.max.{label}.png"
        _write_preview(preview_path, _to_uint8(preview_plane, peak), args.preview_scale)
        _write_preview(max_path, _to_uint8(max_plane, peak), args.preview_scale)
        mapped = np.minimum((values * 255) // max(1, peak), 255)
        manifest["variants"].append({
            "label": label, "peak": peak,
            "preview_path": str(preview_path), "max_path": str(max_path),
            "peak_ratio_to_exact": peak / exact_peak,
            "u8_mapping_changed_pixels": int(histogram[mapped != exact_u8].sum()),
        })
        print(f"Wrote {preview_path}")
        print(f"Wrote {max_path}")
    manifest_path = args.output_dir / f"{stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

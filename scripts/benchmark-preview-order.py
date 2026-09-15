"""Compare preview conversion order on an existing TIFF or CZI without changing it.

Writes only comparison PNGs and JSON into --output-dir.  The three variants
are: current full-res scale then downscale, preserve-percentile downscale then
scale, and fast downscale then percentile/scale.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

import cv2
import numpy as np
import tifffile

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
from czi_common import (  # noqa: E402
    channel_indices_from_czi,
    preview_autoscale_to_uint8,
    read_czi_plane,
    z_indices_with_data,
)


def downscale(plane: np.ndarray, scale: float) -> np.ndarray:
    h, w = plane.shape[:2]
    return cv2.resize(
        plane,
        (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA,
    )


def sampled_p99(plane: np.ndarray, max_pixels: int = 262_144) -> float:
    stride = max(1, int(np.ceil(np.sqrt(plane.size / max_pixels))))
    return float(np.percentile(plane[::stride, ::stride], 99))


def select_preview_plane(path: Path) -> tuple[np.ndarray, int]:
    with tifffile.TiffFile(path) as tf:
        pages = tf.pages
        if not pages:
            raise ValueError("TIFF has no pages")
        best, best_index, best_score = None, 0, float("-inf")
        for index, page in enumerate(pages):
            plane = np.asarray(page.asarray())
            if plane.ndim == 3:
                # Legacy single-page z-stack.
                for inner_index, inner in enumerate(plane):
                    score = sampled_p99(np.asarray(inner))
                    if score > best_score:
                        best, best_index, best_score = np.asarray(inner), inner_index, score
            else:
                score = sampled_p99(plane)
                if score > best_score:
                    best, best_index, best_score = plane, index, score
    if best is None:
        raise ValueError("No 2-D TIFF plane found")
    return best, best_index


def select_preview_plane_from_czi(
    path: Path, scene: int, channel: int, requested_z: int | None,
) -> tuple[np.ndarray, int]:
    """Read only the selected/full candidate CZI planes; retain one winner."""
    from aicspylibczi import CziFile

    czi = CziFile(str(path))
    try:
        z_values = [requested_z] if requested_z is not None else z_indices_with_data(czi, scene, channel)
        if not z_values:
            raise ValueError("CZI has no readable Z planes for this scene/channel")
        best, best_z, best_score = None, int(z_values[0]), float("-inf")
        for z in z_values:
            plane = np.asarray(read_czi_plane(czi, scene, int(z), channel))
            score = sampled_p99(plane)
            if score > best_score:
                best, best_z, best_score = plane, int(z), score
        if best is None:
            raise ValueError("CZI yielded no 2-D plane")
        return best, best_z
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()


def full_percentile_then_downscale(plane: np.ndarray, scale: float) -> np.ndarray:
    """Same percentile thresholds as current preview, scaling only 5% pixels."""
    arr = np.asarray(plane)
    if arr.dtype == np.uint8:
        return downscale(arr, scale)
    # Imported CZI planes are uint16, so no NaN replacement is required.
    lo, hi = np.percentile(arr, (2.0, 98.0))
    if hi <= lo:
        hi = lo + 1.0
    small = downscale(arr, scale).astype(np.float64)
    return np.clip((np.clip(small, lo, hi) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def downscale_then_percentile(plane: np.ndarray, scale: float) -> np.ndarray:
    """Fastest candidate: all display contrast work happens at preview size."""
    small = downscale(np.asarray(plane), scale)
    return preview_autoscale_to_uint8(small)


def measure(fn):
    tracemalloc.start()
    start = time.perf_counter()
    out = fn()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return out, elapsed, peak


def metrics(reference: np.ndarray, candidate: np.ndarray) -> dict:
    delta = np.abs(reference.astype(np.int16) - candidate.astype(np.int16))
    return {
        "changed_pixels": int(np.count_nonzero(delta)),
        "changed_ratio": float(np.count_nonzero(delta) / delta.size),
        "mean_abs_delta": float(delta.mean()),
        "max_abs_delta": int(delta.max(initial=0)),
    }


def compare_plane(
    source: Path, plane: np.ndarray, z_index: int, output_dir: Path, scale: float,
    *, source_kind: str, scene: int | None = None, channel: int | None = None,
) -> dict:
    """Write the three previews and return their measured comparison."""
    legacy, legacy_seconds, legacy_memory = measure(
        lambda: downscale(preview_autoscale_to_uint8(plane), scale)
    )
    preserve, preserve_seconds, preserve_memory = measure(
        lambda: full_percentile_then_downscale(plane, scale)
    )
    fast, fast_seconds, fast_memory = measure(
        lambda: downscale_then_percentile(plane, scale)
    )
    channel_suffix = f".ch{channel}" if channel is not None else ""
    stem = f"{source.stem}{channel_suffix}.z{z_index}.preview-order"
    variants = {"legacy": legacy, "preserve_percentile": preserve, "fast": fast}
    timing = {
        "legacy": (legacy_seconds, legacy_memory),
        "preserve_percentile": (preserve_seconds, preserve_memory),
        "fast": (fast_seconds, fast_memory),
    }
    report = {
        "source": str(source), "source_kind": source_kind,
        "source_shape": list(plane.shape), "source_dtype": str(plane.dtype),
        "selected_z": z_index, "scale": scale, "variants": {},
    }
    if scene is not None:
        report["scene"] = scene
    if channel is not None:
        report["channel"] = channel
    for label, image in variants.items():
        path = output_dir / f"{stem}.{label}.png"
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"Could not write {path}")
        seconds, memory = timing[label]
        report["variants"][label] = {
            "path": str(path), "seconds": seconds, "tracemalloc_peak_bytes": memory,
            "vs_legacy": {} if label == "legacy" else metrics(legacy, image),
        }
        print(f"Wrote {path}")
    report_path = output_dir / f"{stem}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="TIFF or CZI input; read only")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scale", type=float, default=0.05)
    parser.add_argument("--scene", type=int, default=0, help="CZI scene index")
    parser.add_argument("--channel", type=int, default=0, help="CZI channel index")
    parser.add_argument("--all-channels", action="store_true", help="CZI only: compare every channel")
    parser.add_argument("--z", type=int, help="Optional CZI Z index; skips brightest-plane selection")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"Input not found: {args.source}")
    if not 0 < args.scale <= 1:
        parser.error("--scale must be in (0, 1]")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    is_czi = args.source.suffix.lower() == ".czi"
    if args.all_channels and not is_czi:
        parser.error("--all-channels requires a .czi input")
    if args.all_channels:
        from aicspylibczi import CziFile
        czi = CziFile(str(args.source))
        try:
            channels = channel_indices_from_czi(czi)
        finally:
            close = getattr(czi, "close", None)
            if callable(close):
                close()
        reports = []
        for channel in channels:
            plane, z_index = select_preview_plane_from_czi(args.source, args.scene, channel, args.z)
            reports.append(compare_plane(
                args.source, plane, z_index, args.output_dir, args.scale,
                source_kind="czi", scene=args.scene, channel=channel,
            ))
        combined = args.output_dir / f"{args.source.stem}.all-channels.preview-order.json"
        combined.write_text(json.dumps({"source": str(args.source), "reports": reports}, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {combined}")
    else:
        if is_czi:
            plane, z_index = select_preview_plane_from_czi(args.source, args.scene, args.channel, args.z)
            compare_plane(args.source, plane, z_index, args.output_dir, args.scale,
                          source_kind="czi", scene=args.scene, channel=args.channel)
        else:
            plane, z_index = select_preview_plane(args.source)
            compare_plane(args.source, plane, z_index, args.output_dir, args.scale, source_kind="tiff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

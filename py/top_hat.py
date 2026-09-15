"""Top-hat filter (OpenCV MORPH_TOPHAT + gamma) for Mason Jar preprocess wizard."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import tifffile as tf

from grayscale_load import (  # noqa: F401
    load_grayscale_uint8,
    load_grayscale_uint8_roi,
    read_image_size,
    to_uint8_grayscale,
)


def adjust_gamma(image, gamma=1.25):
    inv_gamma = 1.0 / float(gamma)
    table = np.array(
        [((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]
    ).astype("uint8")
    return cv2.LUT(image, table)


def apply_tophat(img: np.ndarray, radius: int, gamma: float) -> np.ndarray:
    radius = max(1, int(radius))
    kernel = np.ones((radius, radius), np.uint8)
    tophat = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, kernel)
    return adjust_gamma(tophat, gamma)


def process_roi(img: np.ndarray, x: int, y: int, w: int, h: int, radius: int, gamma: float):
    pad = max(1, int(radius))
    y0 = max(0, y - pad)
    x0 = max(0, x - pad)
    y1 = min(img.shape[0], y + h + pad)
    x1 = min(img.shape[1], x + w + pad)
    crop = img[y0:y1, x0:x1]
    out = apply_tophat(crop, radius, gamma)
    oy = y - y0
    ox = x - x0
    return out[oy : oy + h, ox : ox + w]


def emit_preview_json(payload: dict) -> None:
    print("PREVIEW_JSON:" + json.dumps(payload), flush=True)


def emit_preview_progress(pct: int, message: str) -> None:
    print(f"PROGRESS:{int(pct)}:{message}", flush=True)


def preview_display_tophat(roi: np.ndarray) -> np.ndarray:
    """Wizard PNG: no percentile stretch (batch output is unstretched). Mild scale if nearly flat."""
    if roi.size == 0:
        return roi.astype(np.uint8)
    out = roi.astype(np.uint8)
    mx = int(out.max())
    if mx < 16:
        return np.clip(out.astype(np.float64) / max(1, mx) * 255.0, 0, 255).astype(np.uint8)
    return out


def run_preview(args) -> int:
    path = Path(args.image.strip())
    if not path.is_file():
        emit_preview_json({"ok": False, "error": "image not found"})
        return 1
    x, y, w, h = int(args.x), int(args.y), int(args.w), int(args.h)
    radius = int(args.filter or args.radius or 10)
    gamma = float(args.correction or args.gamma or 1.25)
    pad = max(1, int(radius))
    try:
        img_h, img_w = read_image_size(path)
    except Exception as exc:
        emit_preview_json({"ok": False, "error": str(exc)})
        return 1
    w = max(8, min(w, img_w))
    h = max(8, min(h, img_h))
    x = max(0, min(x, img_w - w))
    y = max(0, min(y, img_h - h))
    emit_preview_progress(5, "Reading ROI...")
    crop, _, _, x0, y0 = load_grayscale_uint8_roi(path, x, y, w, h, pad=pad)
    emit_preview_progress(40, "Applying filter...")
    roi = process_roi(crop, x - x0, y - y0, w, h, radius, gamma)
    roi = preview_display_tophat(roi)
    emit_preview_progress(85, "Writing preview...")
    out_dir = Path(args.preview_dir.strip()) if args.preview_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "_tophat_preview.png"
    cv2.imwrite(str(out_path), roi)
    emit_preview_progress(100, "Done")
    emit_preview_json(
        {
            "ok": True,
            "previewPath": str(out_path.resolve()),
            "width": int(w),
            "height": int(h),
        }
    )
    return 0


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


from slice_input_files import list_input_files  # noqa: E402


def run_batch(args) -> int:
    if args.config:
        cfg = load_config(args.config.strip())
        input_dir = Path(cfg.get("source_abs") or cfg.get("input_dir") or args.input)
        output_dir = Path(cfg.get("output_abs") or cfg.get("output_dir") or args.output)
        radius = int(cfg.get("radius_px", cfg.get("filter", 10)))
        gamma = float(cfg.get("gamma", cfg.get("correction", 1.25)))
        slice_list = cfg.get("slice_list") or args.slice_list
        signal_branch = cfg.get("signal_branch", "")
        source_run_rel = cfg.get("source_run_rel", "")
        source_kind = cfg.get("source_kind", "max")
    else:
        input_dir = Path(args.input.strip())
        output_dir = Path(args.output.strip())
        radius = int(args.filter or 10)
        gamma = float(args.correction or 1.25)
        slice_list = args.slice_list
        signal_branch = ""
        source_run_rel = ""
        source_kind = "max"

    output_dir.mkdir(parents=True, exist_ok=True)
    files = list_input_files(input_dir, slice_list)
    print(len(files), flush=True)
    if not files:
        print("LOG: no input TIFFs", flush=True)
        return 1

    written = []
    for fpath in files:
        print(f"LOG: Processing {fpath.name}", flush=True)
        try:
            img = load_grayscale_uint8(fpath)
            out = apply_tophat(img, radius, gamma)
            out_path = output_dir / fpath.name
            tf.imwrite(str(out_path), out)
            written.append(fpath.name)
            print(f"LOG: tophat_done {fpath.name}", flush=True)
        except Exception as e:
            print(f"LOG: Failed {fpath.name}: {e}", flush=True)

    if not written:
        # Inputs existed (guarded above) but every file failed.
        print(
            f"TOPHAT_NO_OUTPUT: 0 of {len(files)} files written.",
            flush=True,
        )
        print("Done!", flush=True)
        return 1

    from run_manifest import write_run_manifest

    try:
        write_run_manifest(
            str(output_dir),
            {
                "step": "tophat",
                "input_dir": str(input_dir),
                "input_files": written,
                "radius_px": radius,
                "gamma": gamma,
                "signal_branch": signal_branch,
                "source_run_rel": source_run_rel,
                "source_kind": source_kind,
                "kernel": "cv2_square",
            },
        )
    except Exception as e:
        print(f"LOG: tophat_manifest_failed {e}", flush=True)
        print("Done!", flush=True)
        return 0

    print("Done!", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Top-hat filter images")
    parser.add_argument("-o", "--output", default="")
    parser.add_argument("-i", "--input", default="")
    parser.add_argument("-f", "--filter", default="")
    parser.add_argument("--radius", default="")
    parser.add_argument("-c", "--correction", default="1.25")
    parser.add_argument("--gamma", default="")
    parser.add_argument("-g", "--graphical", default="False")
    parser.add_argument("-j", "--config", default="")
    parser.add_argument("--slice-list", default="")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--image", default="")
    parser.add_argument("--x", default="0")
    parser.add_argument("--y", default="0")
    parser.add_argument("--w", default="512")
    parser.add_argument("--h", default="512")
    parser.add_argument("--preview-dir", default="")
    args = parser.parse_args()

    if args.preview:
        return run_preview(args)

    if str(args.graphical).lower() in ("true", "1", "yes"):
        print("Graphical mode not supported in Electron.", flush=True)
        return 1

    if args.config:
        return run_batch(args)
    if args.input and args.output:
        return run_batch(args)
    print("LOG: missing -j config or -i/-o", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())

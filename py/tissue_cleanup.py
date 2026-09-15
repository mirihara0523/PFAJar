"""Per-slice tissue edge masking for Mason Jar bundles."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import base64
import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import tifffile as tiff

from bundle_slice_paths import paths_for_slice
from czi_common import emit_log, emit_result, load_import_config
from tissue_cleanup_progress import (
    clear_progress,
    config_fingerprint,
    is_completed,
    load_progress,
    path_key,
    record_completion,
    save_progress,
)
from tiff_bundle_io import page_count, read_tiff_2d, transform_tiff_pages, write_tiff_2d
from tissue_mask import (
    ensure_keep_mask_polarity,
    isolate_tissue_mask,
    parse_stroke_points,
    wizard_mask_kwargs,
)

ARCHIVE_MASK_REL = "tissue_cleanup_masks"

VALID_EXTENSIONS = {".png", ".tif", ".tiff"}
TRACE_WIDTH = 12


def array_to_gray_u8(arr: np.ndarray) -> np.ndarray:
    """Collapse OpenCV PNG reads (BGR/BGRA) or accidental channel axes to 2D uint8."""
    img = np.asarray(arr)
    if img.ndim == 3:
        if img.shape[2] >= 3:
            img = cv2.cvtColor(
                img,
                cv2.COLOR_BGR2GRAY if img.shape[2] == 3 else cv2.COLOR_BGRA2GRAY,
            )
        else:
            img = img[..., 0]
    elif img.ndim > 2:
        img = np.max(img, axis=0)
    if img.ndim != 2:
        raise ValueError(f"Unsupported ndim={img.ndim}")
    return img


def load_grayscale_u8(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        img = tiff.imread(str(path))
    else:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not read {path}")
    if img.ndim == 3 or img.ndim > 2:
        img = array_to_gray_u8(img)
    arr = np.asarray(img)
    if np.issubdtype(arr.dtype, np.floating):
        if arr.max() <= 1.0:
            arr = arr * 255.0
        elif arr.max() > 255.0:
            arr = arr * (255.0 / float(arr.max()))
    elif arr.max() > 255:
        arr = arr.astype(np.float64) / float(arr.max()) * 255.0
    return np.clip(arr, 0, 255).astype(np.uint8)


def bool_mask_to_keep_u8(mask: np.ndarray) -> np.ndarray:
    return (mask.astype(bool).astype(np.uint8) * 255)


def auto_keep_mask(gray_u8: np.ndarray, edge_shrink_px: int = 2) -> np.ndarray:
    kw = wizard_mask_kwargs(gray_u8, edge_shrink_px=edge_shrink_px)
    return bool_mask_to_keep_u8(isolate_tissue_mask(gray_u8, **kw))


def _stroke_mask_from_points(
    shape: tuple[int, int], stroke_points: list[tuple[int, int]], width: int = TRACE_WIDTH
) -> np.ndarray:
    h, w = shape
    stroke = np.zeros((h, w), dtype=np.uint8)
    if not stroke_points:
        return stroke
    pts = np.array(stroke_points, dtype=np.int32)
    if len(pts) == 1:
        cv2.circle(stroke, tuple(int(v) for v in pts[0]), max(3, width // 2), 255, -1)
        return stroke
    for i in range(len(pts) - 1):
        p0 = tuple(int(v) for v in pts[i])
        p1 = tuple(int(v) for v in pts[i + 1])
        cv2.line(stroke, p0, p1, 255, thickness=width)
    return stroke


def guided_keep_mask(
    gray_u8: np.ndarray,
    stroke_points: list[tuple[int, int]],
    edge_shrink_px: int = 2,
) -> np.ndarray:
    h, w = gray_u8.shape
    stroke = _stroke_mask_from_points((h, w), stroke_points)
    if stroke.max() == 0:
        return auto_keep_mask(gray_u8, edge_shrink_px=edge_shrink_px)

    ys, xs = np.where(stroke > 0)
    pad = 20
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(w, int(xs.max()) + pad + 1)
    y1 = min(h, int(ys.max()) + pad + 1)

    roi_gray = gray_u8[y0:y1, x0:x1]
    roi_stroke = stroke[y0:y1, x0:x1]
    gc_mask = np.full(roi_gray.shape, cv2.GC_BGD, dtype=np.uint8)
    gc_mask[roi_stroke > 0] = cv2.GC_FGD

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(
            roi_gray,
            gc_mask,
            None,
            bgd_model,
            fgd_model,
            5,
            cv2.GC_INIT_WITH_MASK,
        )
        fg = np.where(
            (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD),
            255,
            0,
        ).astype(np.uint8)
        if int(np.count_nonzero(fg)) < 64:
            raise ValueError("GrabCut foreground too small")
        full = np.full((h, w), 255, dtype=np.uint8)
        full[y0:y1, x0:x1] = fg
        return full
    except Exception:
        kw = wizard_mask_kwargs(gray_u8, edge_shrink_px=edge_shrink_px)
        local = isolate_tissue_mask(roi_gray, **kw)
        full = np.full((h, w), 255, dtype=np.uint8)
        full[y0:y1, x0:x1] = bool_mask_to_keep_u8(local)
        return full


def border_median_bg(arr2d: np.ndarray) -> float:
    """Legacy helper: 1px frame median (can be mid-grey when tissue touches the edge).

    Apply no longer uses this by default — removed pixels fill with 0 unless
    ``bg_value`` is set in the apply config. Kept for repair scripts / callers
    that still want an estimated camera floor.
    """
    h, w = arr2d.shape[:2]
    if h < 2 or w < 2:
        return 15.0
    border = np.concatenate(
        [
            arr2d[0, :].ravel(),
            arr2d[-1, :].ravel(),
            arr2d[1:-1, 0].ravel(),
            arr2d[1:-1, -1].ravel(),
        ]
    )
    if border.size == 0:
        return 15.0
    return float(np.median(border))


def resolve_apply_bg(bg_override: float | None) -> float:
    """Background fill for removed (red) pixels. Default is black (0)."""
    if bg_override is not None:
        return float(bg_override)
    return 0.0


def resize_keep_mask_nearest(keep_mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = shape
    if keep_mask.shape == (target_h, target_w):
        return keep_mask.astype(np.uint8)
    resized = cv2.resize(
        keep_mask.astype(np.uint8),
        (target_w, target_h),
        interpolation=cv2.INTER_NEAREST,
    )
    return (resized >= 128).astype(np.uint8) * 255


def apply_resized_keep_mask_to_plane(
    arr: np.ndarray,
    resized_keep_mask: np.ndarray,
    bg: float,
) -> np.ndarray:
    """Apply a keep mask already prepared for ``arr``'s dimensions."""
    if resized_keep_mask.shape != arr.shape:
        raise ValueError(
            f"Mask shape {resized_keep_mask.shape} does not match plane shape {arr.shape}",
        )
    out = arr.copy()
    removed = resized_keep_mask < 128
    if np.issubdtype(out.dtype, np.floating):
        out[removed] = bg
    else:
        out[removed] = int(round(bg))
    return out


def apply_keep_mask_to_plane(arr: np.ndarray, keep_mask: np.ndarray, bg: float) -> np.ndarray:
    mask = resize_keep_mask_nearest(keep_mask, arr.shape)
    return apply_resized_keep_mask_to_plane(arr, mask, bg)


def apply_keep_mask_to_array(arr: np.ndarray, keep_mask: np.ndarray, bg: float) -> np.ndarray:
    if arr.ndim == 2:
        return apply_keep_mask_to_plane(arr, keep_mask, bg)
    if arr.ndim == 3:
        # Z planes normally share dimensions. Prepare the scaled mask once so
        # a full-resolution resize is not repeated for every plane.
        scaled_mask = resize_keep_mask_nearest(keep_mask, arr.shape[1:])
        planes = [
            apply_resized_keep_mask_to_plane(arr[z], scaled_mask, bg)
            for z in range(arr.shape[0])
        ]
        return np.stack(planes, axis=0)
    raise ValueError(f"Unsupported ndim={arr.ndim}")


def mask_is_all_keep(keep_mask: np.ndarray) -> bool:
    return int(np.min(keep_mask)) >= 128


def composited_preview(gray_u8: np.ndarray, keep_mask: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(gray_u8, cv2.COLOR_GRAY2BGR)
    removed = keep_mask < 128
    overlay = rgb.copy()
    overlay[removed, 2] = np.minimum(255, overlay[removed, 2].astype(np.int32) + 120)
    overlay[removed, 0] = np.maximum(0, overlay[removed, 0].astype(np.int32) - 40)
    overlay[removed, 1] = np.maximum(0, overlay[removed, 1].astype(np.int32) - 40)
    return cv2.addWeighted(rgb, 0.55, overlay, 0.45, 0)


def emit_preview_json(payload: dict) -> None:
    print("PREVIEW_JSON:" + json.dumps(payload), flush=True)


def _read_image_array(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".png":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not read {path}")
        return np.asarray(img)
    return np.asarray(tiff.imread(str(path)))


def _write_image_array(path: Path, arr: np.ndarray) -> None:
    parts = {p.lower() for p in path.parts}
    if "00_dapi" in parts and path.suffix.lower() != ".png":
        raise ValueError(f"00_dapi accepts PNG only, not {path}")
    if path.suffix.lower() == ".png":
        cv2.imwrite(str(path), arr)
        return
    tiff.imwrite(str(path), arr, photometric="minisblack")


def _shape_desc(arr: np.ndarray) -> str:
    if arr.ndim == 2:
        return f"{arr.shape[1]}x{arr.shape[0]} {arr.dtype}"
    if arr.ndim == 3:
        return f"Z={arr.shape[0]} {arr.shape[2]}x{arr.shape[1]} {arr.dtype}"
    return f"ndim={arr.ndim} {arr.dtype}"


def _apply_mask_to_file(
    path: Path,
    keep_mask: np.ndarray,
    bg_override: float | None,
) -> str:
    """Apply keep mask in-place using path-based TIFF I/O for large z-stacks."""
    parts = {p.lower() for p in path.parts}
    if "00_dapi" in parts and path.suffix.lower() != ".png":
        raise ValueError(f"00_dapi accepts PNG only, not {path}")
    # Compress original_scans z-stacks (lossless zlib) to keep NAS writes small
    # and preserve the compression applied at import. 03_max stays uncompressed
    # because downstream ROI reads memmap those TIFFs (needs contiguous data).
    compress = "zlib" if ("original_scans" in parts and "03_max" not in parts) else None

    if path.suffix.lower() == ".png":
        arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if arr is None:
            raise ValueError(f"Could not read {path}")
        try:
            arr = array_to_gray_u8(np.asarray(arr))
        except ValueError as exc:
            raise ValueError(f"Unsupported ndim for {path.name}") from exc
        bg = resolve_apply_bg(bg_override)
        out = apply_keep_mask_to_plane(arr, keep_mask, bg)
        cv2.imwrite(str(path), out)
        desc = _shape_desc(out)
        del arr, out
        return desc

    n_pages = page_count(path)
    if n_pages == 1:
        arr = read_tiff_2d(path)
        if arr.ndim != 2:
            raise ValueError(f"Unsupported ndim={arr.ndim} for {path.name}")
        bg = resolve_apply_bg(bg_override)
        out = apply_keep_mask_to_plane(arr, keep_mask, bg)
        write_tiff_2d(path, out, compression=compress)
        desc = _shape_desc(out)
        del arr, out
        return desc

    bg_holder: list[float | None] = [None]
    mask_holder: list[np.ndarray | None] = [None]

    def plane_fn(plane: np.ndarray) -> np.ndarray:
        if plane.ndim != 2:
            raise ValueError(f"Unsupported plane ndim={plane.ndim} for {path.name}")
        if bg_holder[0] is None:
            bg_holder[0] = resolve_apply_bg(bg_override)
        if mask_holder[0] is None:
            # A Z-stack's pages have the same shape. Cache this potentially
            # large nearest-neighbor expansion for the rest of the stack.
            mask_holder[0] = resize_keep_mask_nearest(keep_mask, plane.shape)
        return apply_resized_keep_mask_to_plane(plane, mask_holder[0], bg_holder[0])

    z_count, first_shape = transform_tiff_pages(path, plane_fn, compression=compress)
    if len(first_shape) == 2:
        return f"Z={z_count} {first_shape[1]}x{first_shape[0]}"
    return f"Z={z_count} {first_shape}"


def _encode_png_base64(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def _load_cfg(config: dict, bundle_root: Path) -> dict:
    cfg_path = config.get("czi_config") or config.get("import_config")
    if cfg_path:
        try:
            return load_import_config(cfg_path)
        except FileNotFoundError:
            emit_log(f"tissue_cleanup: import config not found: {cfg_path}")
    return config.get("channels") and config or {}


def _backup_file(src: Path, backup_root: Path, bundle_root: Path) -> None:
    try:
        rel = src.relative_to(bundle_root)
    except ValueError:
        rel = Path(src.name)
    dest = backup_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(src, dest)


def _archive_keep_mask(bundle_root: Path, slice_id: str, mask_path: Path) -> str:
    """Copy keep mask PNG into ``.masonjar/tissue_cleanup_masks/`` for alignment."""
    archive_dir = bundle_root / ".masonjar" / ARCHIVE_MASK_REL
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{slice_id}.png"
    shutil.copy2(mask_path, dest)
    return f"{ARCHIVE_MASK_REL}/{slice_id}.png"


def _apply_category(path: Path) -> str:
    """Coarse file class for per-apply timing aggregates."""
    if path.suffix.lower() == ".png":
        return "png"
    parts = {p.lower() for p in path.parts}
    if "03_max" in parts:
        return "max"
    if "original_scans" in parts:
        return "zstack"
    return "other"


def apply_masks_batch(bundle_root: Path, config: dict) -> dict:
    started = time.monotonic()
    bundle_root = bundle_root.resolve()
    slices_cfg = config.get("slices") or {}
    dry_run = bool(config.get("dry_run"))
    backup_root = bundle_root / ".masonjar" / "tissue_cleanup_backup"
    cfg = _load_cfg(config, bundle_root)

    jobs: list[tuple[str, Path, np.ndarray, float | None, str]] = []
    for slice_id, spec in slices_cfg.items():
        mask_path = Path(str(spec.get("mask_path", "")).strip())
        if not mask_path.is_file():
            emit_log(f"tissue_cleanup: skip {slice_id} — mask missing")
            continue
        keep_mask = load_grayscale_u8(mask_path)
        if mask_is_all_keep(keep_mask):
            emit_log(f"tissue_cleanup: skip {slice_id} — unchanged mask")
            continue
        archive_rel = _archive_keep_mask(bundle_root, slice_id, mask_path)
        bg_override = spec.get("bg_value")
        bg = float(bg_override) if bg_override is not None else None
        jobs.append((slice_id, mask_path, keep_mask, bg, archive_rel))

    targets: list[tuple[str, Path]] = []
    for slice_id, _mask_path, _keep, _bg, _archive in jobs:
        for tpath in paths_for_slice(bundle_root, slice_id, cfg):
            targets.append((slice_id, tpath))

    total_files = len(targets)
    emit_log(f"tissue_cleanup apply: {len(jobs)} slice(s), {total_files} file(s)")
    print(total_files, flush=True)

    fingerprint = config_fingerprint(config)
    resume = bool(config.get("resume_apply", True))
    progress = load_progress(bundle_root)
    if progress and progress.get("config_fingerprint") != fingerprint:
        progress = None
    if not resume or not progress:
        progress = {
            "config_fingerprint": fingerprint,
            "files_total": total_files,
            "completed": 0,
            "completed_paths": [],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "slices": {},
        }
        save_progress(bundle_root, progress)
    elif progress.get("completed", 0):
        emit_log(
            f"Resuming apply: {progress.get('completed', 0)}/{total_files} already done",
        )

    manifest_slices: dict = dict((progress or {}).get("slices") or {})
    applied = int((progress or {}).get("completed") or 0) if progress else 0
    skipped = 0
    failed: list[str] = []
    file_index = 0
    # Timing aggregates for post-hoc review: backup copy vs transform write, and
    # per-file-class transform totals.
    _backup_total = 0.0
    _apply_total = 0.0
    _cat_time: dict[str, float] = {}
    _cat_count: dict[str, int] = {}

    job_by_slice = {sid: (mask, bg, archive_rel) for sid, _mp, mask, bg, archive_rel in jobs}
    archive_by_slice = {sid: archive_rel for sid, _mp, _m, _b, archive_rel in jobs}

    for slice_id, tpath in targets:
        file_index += 1
        keep_mask, bg_override, _archive_rel = job_by_slice[slice_id]
        try:
            rel = tpath.relative_to(bundle_root)
        except ValueError:
            rel = Path(tpath.name)
        rel_k = path_key(bundle_root, tpath)
        if resume and progress and is_completed(progress, rel_k):
            emit_log(f"skip completed {rel_k}")
            continue
        emit_log(f"[{file_index}/{total_files}] read {rel}")
        try:
            if dry_run:
                skipped += 1
                print(f"Dry-run [{file_index}/{total_files}] {rel}", flush=True)
                continue
            b0 = time.monotonic()
            _backup_file(tpath, backup_root, bundle_root)
            backup_s = time.monotonic() - b0
            a0 = time.monotonic()
            shape_desc = _apply_mask_to_file(tpath, keep_mask, bg_override)
            apply_s = time.monotonic() - a0
            _backup_total += backup_s
            _apply_total += apply_s
            _cat = _apply_category(tpath)
            _cat_time[_cat] = _cat_time.get(_cat, 0.0) + apply_s
            _cat_count[_cat] = _cat_count.get(_cat, 0) + 1
            emit_log(
                f"[{file_index}/{total_files}] wrote {rel} "
                f"({shape_desc}, backup {backup_s:.2f}s apply {apply_s:.2f}s)",
            )
            applied += 1
            entry = manifest_slices.setdefault(
                slice_id,
                {
                    "method": slices_cfg.get(slice_id, {}).get("method", "mixed"),
                    "files_touched": [],
                    "mask_archive": archive_by_slice.get(slice_id),
                },
            )
            entry["files_touched"].append(str(rel))
            print(f"Applied tissue mask [{file_index}/{total_files}] {rel}", flush=True)
            if progress is not None:
                record_completion(
                    bundle_root,
                    progress,
                    rel_k,
                    slice_id,
                    manifest_slices,
                )
        except Exception as exc:
            failed.append(f"{rel}: {exc}")
            emit_log(f"[{file_index}/{total_files}] FAILED {rel}: {exc}")

    emit_log(
        f"tissue_cleanup timing: backup {_backup_total:.1f}s, "
        f"transform {_apply_total:.1f}s",
    )
    for _c in sorted(_cat_time):
        emit_log(f"  {_c}: {_cat_time[_c]:.1f}s x{_cat_count[_c]}")

    elapsed = round(time.monotonic() - started, 2)
    result = {
        "ok": len(failed) == 0,
        "applied_files": applied,
        "skipped_files": skipped,
        "failed": failed,
        "files_total": total_files,
        "slices_applied": len(manifest_slices),
        "elapsed_sec": elapsed,
        "slices": manifest_slices,
    }
    if result["ok"]:
        clear_progress(bundle_root)
    return result


def _preview_mask_out_path(args, preview_path: Path) -> Path:
    if getattr(args, "output", None) and str(args.output).strip():
        return Path(str(args.output).strip())
    out_dir = getattr(args, "output_dir", None)
    if out_dir and str(out_dir).strip():
        return Path(str(out_dir).strip()) / "_tissue_mask.png"
    return preview_path.parent / "_tissue_mask.png"


def run_auto_preview(args) -> int:
    preview_path = Path(args.input.strip())
    if not preview_path.is_file():
        emit_preview_json({"ok": False, "error": "preview not found"})
        return 1
    gray = load_grayscale_u8(preview_path)
    edge_shrink = int(getattr(args, "edge_shrink", 2))
    keep = auto_keep_mask(gray, edge_shrink_px=edge_shrink)
    keep = ensure_keep_mask_polarity(gray, keep)
    out_mask = _preview_mask_out_path(args, preview_path)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_mask), keep)
    preview_out = out_mask.parent / "_tissue_preview.png"
    cv2.imwrite(str(preview_out), composited_preview(gray, keep))
    emit_preview_json(
        {
            "ok": True,
            "maskPath": str(out_mask.resolve()),
            "previewPath": str(preview_out.resolve()),
            "maskBase64": _encode_png_base64(out_mask),
            "width": int(gray.shape[1]),
            "height": int(gray.shape[0]),
        }
    )
    return 0


def run_guided_preview(args) -> int:
    preview_path = Path(args.input.strip())
    if not preview_path.is_file():
        emit_preview_json({"ok": False, "error": "preview not found"})
        return 1
    stroke_path = Path(args.stroke_json.strip())
    if not stroke_path.is_file():
        emit_preview_json({"ok": False, "error": "stroke JSON not found"})
        return 1
    with open(stroke_path, encoding="utf-8") as f:
        stroke_raw = json.load(f)
    stroke_points = parse_stroke_points(stroke_raw)
    if not stroke_points:
        emit_preview_json({"ok": False, "error": "no stroke points in JSON"})
        return 1
    gray = load_grayscale_u8(preview_path)
    edge_shrink = int(getattr(args, "edge_shrink", 2))
    keep = guided_keep_mask(gray, stroke_points, edge_shrink_px=edge_shrink)
    keep = ensure_keep_mask_polarity(gray, keep)
    out_mask = _preview_mask_out_path(args, preview_path)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_mask), keep)
    preview_out = out_mask.parent / "_tissue_preview.png"
    cv2.imwrite(str(preview_out), composited_preview(gray, keep))
    emit_preview_json(
        {
            "ok": True,
            "maskPath": str(out_mask.resolve()),
            "previewPath": str(preview_out.resolve()),
            "maskBase64": _encode_png_base64(out_mask),
            "width": int(gray.shape[1]),
            "height": int(gray.shape[0]),
        }
    )
    return 0


def run_apply(args) -> int:
    bundle_root = Path(args.bundle.strip()).resolve()
    config_path = Path(args.json.strip())
    if not config_path.is_file():
        emit_result({"ok": False, "error": f"Config not found: {config_path}"})
        return 1
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    result = apply_masks_batch(bundle_root, config)
    manifest_path = bundle_root / ".masonjar" / "tissue_cleanup_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    emit_result(result)
    print("Done!", flush=True)
    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Tissue edge cleanup masks for Mason Jar")
    parser.add_argument("--auto", action="store_true", help="Auto tissue mask on preview")
    parser.add_argument("--guided", action="store_true", help="Trace-guided GrabCut mask")
    parser.add_argument("--apply", action="store_true", help="Apply confirmed masks to bundle")
    parser.add_argument("-i", "--input", help="Preview image path")
    parser.add_argument("-o", "--output", help="Output keep-mask PNG path")
    parser.add_argument(
        "--output-dir",
        help="Directory for preview mask outputs (uses _tissue_mask.png)",
    )
    parser.add_argument("--stroke-json", help="JSON list of [x,y] stroke points")
    parser.add_argument(
        "--edge-shrink",
        type=int,
        default=2,
        help="Adjust tissue mask edge by N px after auto/guided: +N shrink (erode), -N grow (dilate), 0 none (default 2)",
    )
    parser.add_argument("-b", "--bundle", help="Bundle root for apply")
    parser.add_argument("-j", "--json", help="Apply config JSON path")
    args = parser.parse_args()

    if args.apply:
        if not args.bundle or not args.json:
            emit_result({"ok": False, "error": "apply requires -b and -j"})
            return 1
        return run_apply(args)
    if args.guided:
        if not args.input or not args.stroke_json:
            emit_preview_json({"ok": False, "error": "guided requires -i and --stroke-json"})
            return 1
        return run_guided_preview(args)
    if args.auto:
        if not args.input:
            emit_preview_json({"ok": False, "error": "auto requires -i"})
            return 1
        return run_auto_preview(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

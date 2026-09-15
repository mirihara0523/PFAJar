"""BaSiCPy shading correction for Mason Jar (per-channel fit + apply)."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import tifffile as tiff

from basic_progress import (
    clear_progress,
    is_completed,
    load_progress,
    meta_dir,
    path_key,
    record_completion,
    save_progress,
    write_last_result,
)
from grayscale_load import load_grayscale_uint8, load_grayscale_uint8_roi, read_image_size
from run_manifest import write_run_manifest
from slice_input_files import list_input_files

PREVIEW_WORKING_SIZE = 128


def _log(msg: str) -> None:
    print(f"LOG: {msg}", flush=True)


def _progress(pct: int, message: str) -> None:
    print(f"PROGRESS:{int(pct)}:{message}", flush=True)


def emit_preview_json(payload: dict) -> None:
    print("PREVIEW_JSON:" + json.dumps(payload), flush=True)


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(dest)


def _atomic_write_png(dest: Path, arr: np.ndarray) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # cv2.imwrite selects the encoder from the file extension, so the temp name
    # must keep a real image extension. A trailing ".tmp" has no encoder and
    # raises "could not find a writer for the specified extension".
    ext = dest.suffix or ".png"
    tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.tmp{ext}")
    if not cv2.imwrite(str(tmp), arr):
        raise OSError(f"failed to write PNG {tmp}")
    tmp.replace(dest)


def _atomic_write_tiff(dest: Path, arr: np.ndarray) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".{os.getpid()}.tmp")
    tiff.imwrite(str(tmp), arr)
    tmp.replace(dest)


def _file_ok(path: Path, *, min_bytes: int = 64) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < min_bytes:
            return False
        # Cheap open for images
        if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            return img is not None and img.size > 0
        with tiff.TiffFile(str(path)) as tf:
            return bool(tf.pages)
    except Exception:
        return False


def _cleanup_tmps(dir_path: Path) -> None:
    if not dir_path.is_dir():
        return
    for p in dir_path.glob("*.tmp"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in dir_path.glob("*.*.tmp"):
        try:
            p.unlink()
        except OSError:
            pass


def _params_from_dict(d: dict[str, Any] | None) -> dict[str, Any]:
    d = d or {}
    return {
        "get_darkfield": bool(d.get("get_darkfield", True)),
        "smoothness_flatfield": float(d.get("smoothness_flatfield", 1.0)),
        "smoothness_darkfield": float(d.get("smoothness_darkfield", 1.0)),
        "working_size": int(d.get("working_size", 128)),
        "sort_intensity": bool(d.get("sort_intensity", False)),
        "autotune": bool(d.get("autotune", False)),
    }


def _autotune_basic(basic, stack, channel_id: str) -> tuple[float | None, float | None]:
    """Run BaSiC.autotune to search smoothness params before fit.

    Version-tolerant and non-fatal: if autotune is unavailable or raises,
    logs and returns without changing the manually supplied smoothness.
    Returns (smoothness_flatfield, smoothness_darkfield) chosen, or (None, None).
    """
    fn = getattr(basic, "autotune", None)
    if not callable(fn):
        _log(f"basic_autotune_unavailable channel={channel_id}")
        return (None, None)
    try:
        _log(f"basic_autotune_start channel={channel_id}")
        try:
            fn(stack)
        except TypeError:
            # Some basicpy versions expect a keyword argument name
            fn(images=stack)
        sf = getattr(basic, "smoothness_flatfield", None)
        sd = getattr(basic, "smoothness_darkfield", None)
        sf = float(sf) if sf is not None else None
        sd = float(sd) if sd is not None else None
        _log(
            f"basic_autotune_done channel={channel_id} "
            f"smoothness_flatfield={sf} smoothness_darkfield={sd}"
        )
        return (sf, sd)
    except Exception as exc:  # noqa: BLE001
        _log(
            f"basic_autotune_failed channel={channel_id} err={exc!r} "
            "— falling back to manual smoothness"
        )
        return (None, None)


def _config_fingerprint(channel: dict[str, Any]) -> str:
    blob = json.dumps(
        {
            "role": channel.get("role"),
            "signal_branch": channel.get("signal_branch"),
            "source_abs": channel.get("source_abs"),
            "output_abs": channel.get("output_abs"),
            "params": _params_from_dict(channel.get("params")),
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _make_basic(params: dict[str, Any]):
    try:
        from basicpy import BaSiC
    except ImportError as exc:
        raise ImportError(
            "basicpy is not installed in the Mason Jar venv. "
            "Update Python dependencies from Settings or reinstall."
        ) from exc
    kwargs: dict[str, Any] = {
        "get_darkfield": params["get_darkfield"],
        "smoothness_flatfield": params["smoothness_flatfield"],
        "smoothness_darkfield": params["smoothness_darkfield"],
    }
    # working_size / sort_intensity names vary slightly across basicpy versions
    for key in ("working_size", "sort_intensity"):
        if key in params:
            kwargs[key] = params[key]
    try:
        return BaSiC(**kwargs)
    except TypeError:
        # Older API: drop unknown kwargs
        safe = {
            k: v
            for k, v in kwargs.items()
            if k
            in (
                "get_darkfield",
                "smoothness_flatfield",
                "smoothness_darkfield",
            )
        }
        return BaSiC(**safe)


def _slice_stem(path: Path) -> str:
    name = path.name
    if ".ome." in name.lower():
        return name.split(".")[0]
    return path.stem


def _list_image_files_any(dir_path: Path) -> list[Path]:
    """PNG/TIFF/JPEG listing for DAPI dirs and preview fit stacks."""
    if not dir_path.is_dir():
        return []
    return sorted(
        [
            p
            for p in dir_path.iterdir()
            if p.is_file()
            and p.suffix.lower() in (".png", ".tif", ".tiff", ".jpg", ".jpeg")
        ],
        key=lambda p: p.name,
    )


def _list_fit_images(fit_dir: Path) -> list[Path]:
    """Prefer processable TIFFs; fall back to PNG+TIFF for DAPI preview fits."""
    tiffs = list_input_files(fit_dir, None)
    if tiffs:
        return tiffs
    return _list_image_files_any(fit_dir)


def _list_channel_sources(channel: dict[str, Any]) -> list[Path]:
    source = Path(str(channel.get("source_abs") or "").strip())
    if not source.is_dir():
        raise FileNotFoundError(f"source not found: {source}")
    role = str(channel.get("role") or "signal")
    if role == "dapi":
        files = _list_image_files_any(source)
    else:
        files = list_input_files(source, None)
    slice_list = channel.get("slice_list") or []
    if slice_list:
        wanted = {str(s).lower() for s in slice_list}
        files = [p for p in files if _slice_stem(p).lower() in wanted]
    return files


def _load_stack(files: list[Path], *, max_n: int | None = None) -> tuple[np.ndarray, list[Path]]:
    use = files if max_n is None else files[:max_n]
    arrays = []
    for p in use:
        arrays.append(load_grayscale_uint8(p))
    if not arrays:
        raise ValueError("no images to fit")
    # Resize all to first shape if needed (should already match)
    h0, w0 = arrays[0].shape[:2]
    normed = []
    for a in arrays:
        if a.shape[0] != h0 or a.shape[1] != w0:
            a = cv2.resize(a, (w0, h0), interpolation=cv2.INTER_AREA)
        normed.append(a)
    stack = np.stack(normed, axis=0)
    return stack, use


def _save_profiles(basic, out_dir: Path, fingerprint: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    flat = getattr(basic, "flatfield", None)
    dark = getattr(basic, "darkfield", None)
    if flat is not None:
        np.save(out_dir / f"flatfield_{fingerprint}.npy", np.asarray(flat))
    if dark is not None:
        np.save(out_dir / f"darkfield_{fingerprint}.npy", np.asarray(dark))
    meta = {"fingerprint": fingerprint}
    with open(out_dir / f"profiles_{fingerprint}.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _load_profiles(basic, out_dir: Path, fingerprint: str) -> bool:
    flat_p = out_dir / f"flatfield_{fingerprint}.npy"
    dark_p = out_dir / f"darkfield_{fingerprint}.npy"
    if not flat_p.is_file():
        return False
    try:
        basic.flatfield = np.load(str(flat_p))
        if dark_p.is_file():
            basic.darkfield = np.load(str(dark_p))
        return True
    except Exception:
        return False


def _preview_png_path(previews_dir: Path, slice_id: str, suffix: str) -> Path:
    return previews_dir / f"{slice_id}_{suffix}_basic.png"


def _make_lowres_preview(img: np.ndarray, scale: float = 0.05) -> np.ndarray:
    h, w = img.shape[:2]
    nw = max(8, int(round(w * scale)))
    nh = max(8, int(round(h * scale)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


def run_preview(args) -> int:
    """Approximate shading preview on one ROI using a small BaSiC fit stack."""
    path = Path(args.image.strip())
    if not path.is_file():
        emit_preview_json({"ok": False, "error": "image not found"})
        return 1
    params = _params_from_dict(
        {
            "get_darkfield": args.get_darkfield,
            "smoothness_flatfield": args.smoothness_flatfield,
            "smoothness_darkfield": args.smoothness_darkfield,
            "working_size": args.working_size or PREVIEW_WORKING_SIZE,
            "sort_intensity": args.sort_intensity,
            "autotune": args.autotune,
        }
    )
    height = getattr(args, "height", None)
    if height is None:
        height = getattr(args, "h", 512)
    x, y, w, h = int(args.x), int(args.y), int(args.w), int(height)
    try:
        img_h, img_w = read_image_size(path)
        w = max(8, min(w, img_w))
        h = max(8, min(h, img_h))
        x = max(0, min(x, img_w - w))
        y = max(0, min(y, img_h - h))
        _progress(5, "Loading preview stack…")
        # Fit on downscaled full slice + neighbors if provided via --fit-dir
        fit_dir = Path(args.fit_dir.strip()) if args.fit_dir else path.parent
        files = _list_fit_images(fit_dir)
        # Prefer selected image first; Path equality can fail across resolve styles
        resolved = path.resolve()
        files = [path] + [
            p for p in files if p.resolve() != resolved and p != path
        ]
        # Cap fit stack for preview speed
        files = files[: min(24, len(files))]
        stack, _ = _load_stack(files, max_n=24)
        # Downsample for fit
        ws = max(32, int(params["working_size"]))
        small = []
        for plane in stack:
            small.append(
                cv2.resize(plane, (ws, ws), interpolation=cv2.INTER_AREA)
            )
        small_stack = np.stack(small, axis=0)
        tuned_sf = tuned_sd = None
        basic = _make_basic({**params, "working_size": ws})
        already_fit = False
        if params.get("autotune"):
            _progress(20, "Auto-tuning smoothness…")
            tuned_sf, tuned_sd = _autotune_basic(basic, small_stack, "preview")
            # BaSiCPy autotune fits the model internally as part of its search.
            # Re-calling fit() afterwards is redundant and crashes the torch
            # backend, so skip it when a flatfield is already present.
            already_fit = getattr(basic, "flatfield", None) is not None
        if not already_fit:
            _progress(30, "Fitting BaSiC…")
            basic.fit(small_stack)
        _progress(60, "Applying to ROI…")
        flat = np.asarray(basic.flatfield, dtype=np.float32)
        _log(
            f"preview_flatfield min={float(np.min(flat)):.3f} "
            f"max={float(np.max(flat)):.3f} mean={float(np.mean(flat)):.3f} "
            f"frac_below_1={float(np.mean(flat < 1.0)):.3f}"
        )
        dark = getattr(basic, "darkfield", None)
        if flat.ndim == 2:
            flat_full = cv2.resize(flat, (img_w, img_h), interpolation=cv2.INTER_LINEAR)
        else:
            flat_full = np.ones((img_h, img_w), dtype=np.float32)
        if isinstance(dark, np.ndarray) and getattr(dark, "ndim", 0) == 2:
            dark_full = cv2.resize(
                np.asarray(dark, dtype=np.float32),
                (img_w, img_h),
                interpolation=cv2.INTER_LINEAR,
            )
        else:
            dark_full = (
                float(np.asarray(dark).reshape(-1)[0])
                if dark is not None and np.size(dark)
                else 0.0
            )
        full = load_grayscale_uint8(path).astype(np.float32)
        corr = (full - dark_full) / np.maximum(flat_full, 1e-6)
        _tissue_mask = full > 8
        if _tissue_mask.any():
            _sat = float(np.mean(corr[_tissue_mask] >= 255))
            _log(f"preview_saturation frac_at_255={_sat:.4f} darkfield_scalar_or_map={type(dark).__name__}")
        corr = np.clip(corr, 0, 255).astype(np.uint8)
        roi = corr[y : y + h, x : x + w]
        out_dir = Path(args.preview_dir.strip()) if args.preview_dir else path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "_basic_preview.png"
        _atomic_write_png(out_path, roi)
        _progress(100, "Done")
        emit_preview_json(
            {
                "ok": True,
                "previewPath": str(out_path.resolve()),
                "width": int(w),
                "height": int(h),
                "autotuned": bool(params.get("autotune")),
                "smoothness_flatfield": tuned_sf,
                "smoothness_darkfield": tuned_sd,
            }
        )
        return 0
    except Exception as exc:
        traceback.print_exc()
        # stderr is not captured on the preview path, so mirror the traceback to
        # stdout via LOG: lines which the durable log file does capture.
        _log("preview_exception " + repr(exc))
        for _tb_line in traceback.format_exc().splitlines():
            _log("preview_tb " + _tb_line)
        emit_preview_json({"ok": False, "error": str(exc)})
        return 1


def _process_channel(
    bundle_root: Path,
    channel: dict[str, Any],
    *,
    progress: dict[str, Any],
    force_refit: bool,
    channel_index: int,
    channel_total: int,
) -> dict[str, Any]:
    role = str(channel.get("role") or "signal")
    channel_id = str(
        channel.get("id")
        or (
            f"dapi"
            if role == "dapi"
            else f"{channel.get('signal_branch') or 'signal'}"
        )
    )
    params = _params_from_dict(channel.get("params"))
    fingerprint = _config_fingerprint(channel)
    files = _list_channel_sources(channel)
    if not files:
        raise ValueError(f"no input images for channel {channel_id}")

    profiles_dir = meta_dir(bundle_root) / "basic_profiles" / channel_id
    profiles_dir.mkdir(parents=True, exist_ok=True)

    basic = _make_basic(params)
    fitted = False
    if not force_refit and _load_profiles(basic, profiles_dir, fingerprint):
        _log(f"basic_fit_reuse channel={channel_id} fingerprint={fingerprint}")
        fitted = True
    if not fitted:
        _log(f"basic_fit_start channel={channel_id} n={len(files)}")
        stack, _ = _load_stack(files)
        already_fit = False
        if params.get("autotune"):
            _autotune_basic(basic, stack, channel_id)
            # autotune fits internally; skip the redundant re-fit (see run_preview).
            already_fit = getattr(basic, "flatfield", None) is not None
        if not already_fit:
            basic.fit(stack)
        _save_profiles(basic, profiles_dir, fingerprint)
        fitted = True
        progress["fit_done"] = progress.get("fit_done") or []
        if channel_id not in progress["fit_done"]:
            progress["fit_done"].append(channel_id)
        save_progress(bundle_root, progress)
        _log(f"basic_fit_done channel={channel_id}")

    # Apply
    if role == "dapi":
        out_dir = Path(str(channel.get("output_abs") or "")).resolve()
        if not out_dir.parts:
            out_dir = bundle_root / "data" / "counting" / "00_dapi_basic"
        previews_dir = bundle_root / "data" / "counting" / "_previews"
        suffix = "dapi"
    else:
        out_dir = Path(str(channel.get("output_abs") or "")).resolve()
        previews_dir = bundle_root / "data" / "counting" / "_previews"
        suffix = str(channel.get("preview_suffix") or channel.get("signal_branch") or "signal")

    out_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_tmps(out_dir)
    _cleanup_tmps(previews_dir)

    written = 0
    skipped = 0
    n = len(files)
    for i, src in enumerate(files):
        slice_id = _slice_stem(src)
        if role == "dapi":
            dest = out_dir / f"{slice_id}.png"
            preview_dest = _preview_png_path(previews_dir, slice_id, suffix)
        else:
            dest = out_dir / f"{slice_id}.tif"
            preview_dest = _preview_png_path(previews_dir, slice_id, suffix)

        rel = path_key(bundle_root, dest)
        if (
            is_completed(progress, rel)
            and _file_ok(dest)
            and _file_ok(preview_dest, min_bytes=32)
        ):
            skipped += 1
            continue
        # Replace corrupt / half-written
        for stale in (dest, preview_dest):
            if stale.is_file() and not _file_ok(stale, min_bytes=32):
                try:
                    stale.unlink()
                except OSError:
                    pass

        img = load_grayscale_uint8(src).astype(np.float32)
        # Prefer library transform on single-plane stack
        try:
            out = basic.transform(img[np.newaxis, ...])[0]
        except Exception:
            flat = np.asarray(basic.flatfield, dtype=np.float32)
            dark = getattr(basic, "darkfield", 0.0)
            if flat.shape != img.shape:
                flat = cv2.resize(
                    flat, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR
                )
            if isinstance(dark, np.ndarray) and dark.shape != img.shape:
                dark = cv2.resize(
                    np.asarray(dark, dtype=np.float32),
                    (img.shape[1], img.shape[0]),
                    interpolation=cv2.INTER_LINEAR,
                )
            out = (img - dark) / np.maximum(flat, 1e-6)
        out_u8 = np.clip(out, 0, 255).astype(np.uint8)

        if role == "dapi":
            _atomic_write_png(dest, out_u8)
        else:
            _atomic_write_tiff(dest, out_u8)
        _atomic_write_png(preview_dest, _make_lowres_preview(out_u8))
        record_completion(
            bundle_root, progress, rel, channel_id=channel_id, slice_id=slice_id
        )
        written += 1
        base_pct = int(100 * channel_index / max(channel_total, 1))
        span = int(100 / max(channel_total, 1))
        pct = base_pct + int(span * (i + 1) / max(n, 1))
        _progress(min(99, pct), f"{channel_id}: {slice_id} ({i + 1}/{n})")
        _log(f"basic_done channel={channel_id} slice={slice_id}")

    if role != "dapi":
        write_run_manifest(
            out_dir,
            {
                "step": "basic",
                "channel_id": channel_id,
                "signal_branch": channel.get("signal_branch"),
                "fingerprint": fingerprint,
                "params": params,
                "n_written": written,
                "n_skipped": skipped,
                "n_input": n,
            },
        )
    return {
        "channel_id": channel_id,
        "role": role,
        "written": written,
        "skipped": skipped,
        "output_abs": str(out_dir),
        "fingerprint": fingerprint,
    }


def run_batch(args) -> int:
    config_path = Path(str(args.config).strip())
    if not config_path.is_file():
        print("BASIC_NO_OUTPUT: config missing", flush=True)
        return 1
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    bundle_root = Path(str(config.get("bundle_root") or "")).resolve()
    if not bundle_root.is_dir():
        print("BASIC_NO_OUTPUT: bundle_root missing", flush=True)
        return 1

    channels = list(config.get("channels") or [])
    if not channels:
        print("BASIC_NO_OUTPUT: no channels", flush=True)
        return 1

    force_refit = bool(config.get("force_refit") or args.force_refit)
    resume = bool(config.get("resume", True)) and not bool(config.get("start_fresh"))

    if config.get("start_fresh"):
        clear_progress(bundle_root)
        progress = {
            "started_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
            "completed_paths": [],
            "config_fingerprint": config.get("config_fingerprint"),
            "phase": "apply",
        }
        save_progress(bundle_root, progress)
    else:
        progress = load_progress(bundle_root) or {
            "completed_paths": [],
            "phase": "apply",
        }
        if not resume:
            progress["completed_paths"] = []

    progress["interrupted"] = True
    progress["config_path"] = str(config_path)
    save_progress(bundle_root, progress)

    results = []
    try:
        for ci, channel in enumerate(channels):
            if not channel.get("enabled", True):
                continue
            _log(f"basic_channel_start {channel.get('id') or channel.get('role')} ({ci + 1}/{len(channels)})")
            results.append(
                _process_channel(
                    bundle_root,
                    channel,
                    progress=progress,
                    force_refit=force_refit,
                    channel_index=ci,
                    channel_total=len(channels),
                )
            )
        progress["interrupted"] = False
        progress["phase"] = "done"
        save_progress(bundle_root, progress)
        write_last_result(
            bundle_root,
            {
                "ok": True,
                "status": "complete",
                "channels": results,
                "config_fingerprint": config.get("config_fingerprint"),
            },
        )
        clear_progress(bundle_root)
        _progress(100, "BaSiC shading complete")
        _log("basic_batch_complete")
        return 0
    except Exception as exc:
        traceback.print_exc()
        progress["interrupted"] = True
        progress["error"] = str(exc)
        save_progress(bundle_root, progress)
        write_last_result(
            bundle_root,
            {
                "ok": False,
                "status": "failed",
                "error": str(exc),
                "channels": results,
            },
        )
        print(f"BASIC_NO_OUTPUT: {exc}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BaSiCPy shading correction")
    parser.add_argument("-j", "--config", default="", help="Run config JSON path")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--image", default="")
    parser.add_argument("--fit-dir", default="")
    parser.add_argument("-x", "--x", type=int, default=0)
    parser.add_argument("-y", "--y", type=int, default=0)
    parser.add_argument("-w", "--w", type=int, default=512)
    parser.add_argument("--height", "--h", type=int, default=512)
    parser.add_argument("--preview-dir", default="")
    parser.add_argument("--get-darkfield", action="store_true", default=True)
    parser.add_argument("--no-darkfield", action="store_true")
    parser.add_argument("--smoothness-flatfield", type=float, default=1.0)
    parser.add_argument("--smoothness-darkfield", type=float, default=1.0)
    parser.add_argument("--working-size", type=int, default=128)
    parser.add_argument("--sort-intensity", action="store_true")
    parser.add_argument("--autotune", action="store_true")
    parser.add_argument("--force-refit", action="store_true")
    args = parser.parse_args(argv)
    if args.no_darkfield:
        args.get_darkfield = False
    try:
        if args.preview:
            return run_preview(args)
        if not args.config:
            print("BASIC_NO_OUTPUT: missing -j config", flush=True)
            return 1
        return run_batch(args)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

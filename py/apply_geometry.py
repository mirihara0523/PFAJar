"""Apply per-slice rotation/flip geometry to CZI-derived TIFFs and PNG previews."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import tifffile as tiff

from bundle_slice_paths import paths_for_slice, signal_branch_dirs_from_cfg
from czi_common import CANONICAL_REL, emit_log, emit_result, load_import_config, resolve_original_zstack_path
from czi_common import ROLE_DAPI, branch_for_role_key
from geometry_apply_progress import (
    clear_progress,
    is_completed,
    load_progress,
    path_key,
    record_completion,
    write_last_result,
)
from geometry_history import append_geometry_history, ops_to_js_list


def compose_ops(rotate: int, flip_x: bool, flip_y: bool):
    ops = []
    rot = int(rotate or 0) % 360
    if rot in (90, 180, 270):
        ops.append(("rotate", rot))
    if flip_x:
        ops.append(("flip_x", True))
    if flip_y:
        ops.append(("flip_y", True))
    return ops


def ops_from_string_list(op_list: list) -> list:
    """Map JS geometry.ops entries (rot90, flipX, flipY) to internal op tuples."""
    ops: list = []
    for entry in op_list or []:
        if entry == "rot90":
            ops.append(("rotate", 90))
        elif entry == "flipX":
            ops.append(("flip_x", True))
        elif entry == "flipY":
            ops.append(("flip_y", True))
    return ops


def compose_ops_from_spec(spec: dict) -> list:
    """Use ordered spec.ops when present; else legacy rotate/flip flags."""
    if not spec:
        return []
    raw_ops = spec.get("ops")
    if raw_ops:
        return ops_from_string_list(raw_ops)
    return compose_ops(spec.get("rotate", 0), spec.get("flipX"), spec.get("flipY"))


def apply_ops_to_array(arr: np.ndarray, ops: list) -> np.ndarray:
    # Rotation k matches CSS clockwise in js/orient_geometry.js geometryCssTransform.
    out = arr
    for op, val in ops:
        if op == "rotate":
            if val == 90:
                out = np.rot90(out, k=-1)
            elif val == 180:
                out = np.rot90(out, k=2)
            elif val == 270:
                out = np.rot90(out, k=1)
        elif op == "flip_x":
            out = np.fliplr(out)
        elif op == "flip_y":
            out = np.flipud(out)
    return out


def _read_tiff_array(path: Path) -> np.ndarray:
    """Read TIFF via TiffFile (path-based; avoids io_fairshare BytesIO on large NAS files)."""
    with tiff.TiffFile(str(path)) as tf:
        pages = tf.pages
        if not pages:
            raise ValueError(f"No TIFF pages in {path.name}")
        if len(pages) == 1:
            return np.asarray(pages[0].asarray())
        planes = [np.asarray(p.asarray()) for p in pages]
        return np.stack(planes, axis=0)


def _write_tiff_array(path: Path, arr: np.ndarray) -> None:
    # Keep z-stacks (original_scans) compressed to match czi_extract and stop a
    # geometry apply from re-inflating them; leave 03_max uncompressed so
    # grayscale_load's memmap ROI fast-path (sharpen/tophat/basic) still works.
    # zlib is lossless — pixel values are unchanged. Falls back to uncompressed
    # on older tifffile that lacks the compression kwarg.
    parts = {p.lower() for p in path.parts}
    compress = "original_scans" in parts and "03_max" not in parts
    if compress:
        try:
            tiff.imwrite(str(path), arr, photometric="minisblack", compression="zlib")
            return
        except (TypeError, ValueError):
            pass
    tiff.imwrite(str(path), arr, photometric="minisblack")


def _read_image_array(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".png":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not read {path}")
        return np.asarray(img)
    return _read_tiff_array(path)


def _write_image_array(path: Path, arr: np.ndarray) -> None:
    parts = {p.lower() for p in path.parts}
    if "00_dapi" in parts and path.suffix.lower() != ".png":
        raise ValueError(f"00_dapi accepts PNG only, not {path}")
    if path.suffix.lower() == ".png":
        cv2.imwrite(str(path), arr)
        return
    _write_tiff_array(path, arr)


def transform_file(path: Path, ops: list) -> tuple[np.ndarray, np.ndarray]:
    arr = _read_image_array(path)
    if arr.ndim == 2:
        transformed = apply_ops_to_array(arr, ops)
    elif arr.ndim == 3:
        planes = [apply_ops_to_array(arr[z], ops) for z in range(arr.shape[0])]
        transformed = np.stack(planes, axis=0)
    else:
        raise ValueError(f"Unsupported ndim={arr.ndim} for {path.name}")
    _write_image_array(path, transformed)
    return arr, transformed


def _scoped_branches_for_role_keys(role_keys: list[str]) -> set[str]:
    branches: set[str] = set()
    for rk in role_keys or []:
        if rk == ROLE_DAPI:
            branches.add("dapi")
            continue
        branch = branch_for_role_key(str(rk))
        if branch:
            branches.add(branch)
    return branches


def _max_rel_prefixes_for_scope(cfg: dict, role_keys: list[str]) -> set[str]:
    prefixes: set[str] = set()
    max_runs = (cfg.get("max_runs") or {}) if isinstance(cfg, dict) else {}
    for rk in role_keys or []:
        rel = str(max_runs.get(rk) or "").replace("\\", "/").strip("/")
        if rel:
            prefixes.add(rel.lower())
        branch = branch_for_role_key(str(rk))
        if branch:
            prefixes.add(f"{branch}/max")
    return prefixes


def path_in_reextract_scope(
    target: Path,
    bundle_root: Path,
    slice_id: str,
    role_keys: list[str],
    cfg: dict,
) -> bool:
    """True when target belongs to a re-imported channel for this slice."""
    if not role_keys:
        return False
    try:
        rel = target.relative_to(bundle_root).as_posix()
    except ValueError:
        return False
    rel_lower = rel.lower()
    sid = str(slice_id)
    sid_lower = sid.lower()
    branches = _scoped_branches_for_role_keys(role_keys)
    role_set = {str(rk) for rk in role_keys}

    dapi_png = f"{CANONICAL_REL['dapi']}/{sid}.png".lower()
    if rel_lower == dapi_png or rel_lower.endswith(f"/00_dapi/{sid_lower}.png"):
        return ROLE_DAPI in role_set

    prev_prefix = f"{CANONICAL_REL['previews']}/{sid}_".lower()
    if rel_lower.startswith(prev_prefix) and rel_lower.endswith(".png"):
        branch = Path(rel).stem[len(sid) + 1 :]
        if branch == "dapi":
            return ROLE_DAPI in role_set
        return branch.lower() in {b.lower() for b in branches if b != "dapi"}

    for branch in branches:
        if branch == "dapi":
            continue
        orig = f"{CANONICAL_REL['original_scans']}/{branch}/{sid}.tif".lower()
        if rel_lower == orig:
            return True

    if f"/{sid_lower}.tif" in rel_lower and "/03_max/" in rel_lower:
        max_prefixes = _max_rel_prefixes_for_scope(cfg, role_keys)
        max_base = f"{CANONICAL_REL['max']}/".lower()
        if rel_lower.startswith(max_base):
            tail = rel_lower[len(max_base) :]
            for prefix in max_prefixes:
                if tail.startswith(prefix.lower() + "/") or tail == prefix.lower():
                    return True
            for branch in branches:
                if branch != "dapi" and f"/{branch}/".lower() in rel_lower:
                    return True

    return False


def filter_paths_for_reextract_scope(
    paths: list[Path],
    bundle_root: Path,
    slice_id: str,
    scope: dict,
    cfg: dict,
) -> list[Path]:
    role_keys = scope.get(slice_id) or []
    if not role_keys:
        return []
    return [
        p
        for p in paths
        if path_in_reextract_scope(p, bundle_root, slice_id, role_keys, cfg)
    ]


def collect_geometry_jobs(
    bundle_root: Path,
    geometry: dict,
    cfg: dict,
) -> list[tuple[str, list, list[Path]]]:
    jobs: list[tuple[str, list, list[Path]]] = []
    reextract_scope = cfg.get("reextract_geometry_scope")
    for slice_id in sorted(geometry.keys()):
        spec = geometry[slice_id] or {}
        ops = compose_ops_from_spec(spec)
        if not ops:
            continue
        targets = paths_for_slice(bundle_root, slice_id, cfg)
        if reextract_scope:
            targets = filter_paths_for_reextract_scope(
                targets,
                bundle_root,
                slice_id,
                reextract_scope,
                cfg,
            )
        if targets:
            jobs.append((slice_id, ops, targets))
    return jobs


def _file_kind(path: Path) -> str:
    if path.suffix.lower() == ".png":
        return "PNG"
    return "TIFF"


def _probe_target(path: Path) -> tuple[bool, str]:
    size = path.stat().st_size if path.is_file() else 0
    kind = _file_kind(path)
    detail = f"{kind} {size} bytes"
    if not path.is_file():
        return False, detail + " (missing)"
    if kind == "TIFF":
        try:
            with tiff.TiffFile(str(path)) as tf:
                pages = tf.pages
                if not pages:
                    return False, detail + " (no pages)"
                dtype = pages[0].dtype
                shape = pages[0].shape
                if len(pages) == 1:
                    detail += f" 2D {dtype} {shape[1]}x{shape[0]}"
                else:
                    detail += f" Z-stack {dtype} Z={len(pages)} {shape[1]}x{shape[0]}"
            return True, detail
        except Exception as exc:
            return False, detail + f" (read meta failed: {exc})"
    try:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            return False, detail + " (read failed)"
        arr = np.asarray(img)
        detail += f" 2D {arr.dtype} {arr.shape[1]}x{arr.shape[0]}"
        return True, detail
    except Exception as exc:
        return False, detail + f" (read meta failed: {exc})"


def _array_stats(arr: np.ndarray) -> dict:
    flat = np.asarray(arr)
    return {
        "dtype": str(flat.dtype),
        "shape": list(flat.shape),
        "min": int(np.min(flat)),
        "max": int(np.max(flat)),
        "mean": float(np.mean(flat)),
    }


def _log_max_transform_stats(bundle_root: Path, slice_id: str, rel: Path, before: np.ndarray, after: np.ndarray) -> None:
    rel_s = str(rel).replace("\\", "/")
    if "/03_max/" not in rel_s:
        return
    b = _array_stats(before)
    a = _array_stats(after)
    emit_log(
        f"LOG: max_transform_stats slice={slice_id} path={rel_s} "
        f"before={b['dtype']} min={b['min']} max={b['max']} "
        f"after={a['dtype']} min={a['min']} max={a['max']}",
    )
    if a["max"] < 5:
        emit_log(
            f"LOG: max_transform_warn slice={slice_id} path={rel_s} "
            f"post_apply_peak={a['max']} (expected uint8/uint16 signal range)",
        )


def _describe_target(path: Path) -> str:
    _ok, detail = _probe_target(path)
    return detail


def preflight_log(
    jobs: list[tuple[str, list, list[Path]]],
    bundle_root: Path,
) -> tuple[int, list[str]]:
    total_bytes = 0
    total_files = sum(len(targets) for _, _, targets in jobs)
    failed_probes: list[str] = []
    emit_log(f"Preflight: {len(jobs)} slice(s), {total_files} file(s) to transform")
    for slice_id, _ops, targets in jobs:
        for tpath in targets:
            try:
                rel = tpath.relative_to(bundle_root)
            except ValueError:
                rel = Path(tpath.name)
            size = tpath.stat().st_size if tpath.is_file() else 0
            total_bytes += size
            ok, detail = _probe_target(tpath)
            emit_log(f"  {rel}: {detail}")
            if not ok:
                failed_probes.append(f"{rel}: {detail}")
    emit_log(f"Preflight total: {total_files} files, {total_bytes} bytes")
    if failed_probes:
        emit_log(f"Preflight FAILED: {len(failed_probes)} unreadable target(s)")
    return total_files, failed_probes


def _ops_from_js_list(op_list: list) -> list:
    return ops_from_string_list(op_list)


def collect_repair_jobs(
    bundle_root: Path,
    cfg: dict,
) -> list[tuple[str, list, Path, str, str]]:
    """(slice_id, ops, path, strategy, branch) from repair_targets."""
    jobs: list[tuple[str, list, Path, str, str]] = []
    for target in cfg.get("repair_targets") or []:
        strategy = str(target.get("strategy") or "derivatives_from_original")
        if strategy == "skip":
            continue
        slice_id = str(target.get("slice_id") or "")
        rel = str(target.get("rel_path") or "").strip()
        if not rel:
            continue
        branch = str(target.get("branch") or "")
        op_list = target.get("ops") or []
        ops = _ops_from_js_list(op_list) if op_list else []
        path = bundle_root / rel
        if strategy == "transform_original":
            if branch:
                orig = resolve_original_zstack_path(bundle_root, slice_id, branch)
                if orig is not None:
                    path = orig
        jobs.append((slice_id, ops, path, strategy, branch))
    return jobs


def _preview_from_plane(plane, preview_scale: float) -> np.ndarray:
    from czi_common import clamp_preview_scale, preview_autoscale_to_uint8

    scale = clamp_preview_scale(preview_scale)
    uint8 = preview_autoscale_to_uint8(plane)
    if scale >= 0.999:
        return uint8
    h, w = uint8.shape[:2]
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(uint8, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _write_preview_png(dest: Path, plane, preview_scale: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), _preview_from_plane(plane, preview_scale))


def _repair_dapi_previews_from_pngs(bundle_root: Path, slice_id: str, ops: list) -> None:
    from czi_common import dapi_preview_path, orient_dapi_preview_path

    emit_log(f"  dapi_repair_fallback preview_transform (no z-stack) {slice_id}")
    for dest in (
        orient_dapi_preview_path(bundle_root, slice_id),
        dapi_preview_path(bundle_root, slice_id),
    ):
        if dest.is_file() and ops:
            transform_file(dest, ops)


def _repair_derivatives_from_original(
    bundle_root: Path,
    slice_id: str,
    branch: str,
    rel_path: str,
    ops: list,
    preview_scale: float,
) -> None:
    from czi_common import dapi_preview_path, orient_dapi_preview_path
    from czi_extract import plane_from_zstack

    rel_lower = rel_path.replace("\\", "/").lower()
    is_dapi = branch == "dapi" or "/00_dapi/" in rel_lower or rel_lower.endswith("_dapi.png")

    orig = resolve_original_zstack_path(bundle_root, slice_id, branch)
    if orig is None:
        if is_dapi:
            _repair_dapi_previews_from_pngs(bundle_root, slice_id, ops)
            return
        missing = bundle_root / CANONICAL_REL["original_scans"] / branch / f"{slice_id}.tif"
        raise FileNotFoundError(f"Missing z-stack {missing.relative_to(bundle_root)}")
    stack = _read_tiff_array(orig)
    if ops:
        if stack.ndim == 2:
            stack = apply_ops_to_array(stack, ops)
        else:
            stack = np.stack(
                [apply_ops_to_array(stack[z], ops) for z in range(stack.shape[0])],
                axis=0,
            )
        _write_tiff_array(orig, stack)
    plane = plane_from_zstack(stack)
    if is_dapi:
        preview = _preview_from_plane(plane, preview_scale)
        for dest in (orient_dapi_preview_path(bundle_root, slice_id), dapi_preview_path(bundle_root, slice_id)):
            dest.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(dest), preview)
        return
    dest = bundle_root / rel_path
    _write_preview_png(dest, plane, preview_scale)


def run_transform_jobs(
    bundle_root: Path,
    jobs: list[tuple[str, list, list[Path]]],
    progress: dict | None,
    resume: bool,
    apply_source: str = "apply",
) -> tuple[int, int, list[str], int]:
    total_files = sum(len(targets) for _, _, targets in jobs)
    changed = 0
    bytes_total = 0
    failed: list[str] = []
    file_index = 0

    for slice_id, ops, targets in jobs:
        for tpath in targets:
            rel_k = path_key(bundle_root, tpath)
            if resume and progress and is_completed(progress, rel_k):
                emit_log(f"skip completed {rel_k}")
                file_index += 1
                continue
            file_index += 1
            try:
                rel = tpath.relative_to(bundle_root)
            except ValueError:
                rel = Path(tpath.name)
            kind = _file_kind(tpath)
            size_before = tpath.stat().st_size if tpath.is_file() else 0
            emit_log(
                f"[{file_index}/{total_files}] read {rel} ({kind}, {size_before} bytes)",
            )
            read_start = time.monotonic()
            try:
                _before, after = transform_file(tpath, ops)
                read_elapsed = time.monotonic() - read_start
                try:
                    rel_for_log = tpath.relative_to(bundle_root)
                except ValueError:
                    rel_for_log = Path(tpath.name)
                _log_max_transform_stats(bundle_root, slice_id, rel_for_log, _before, after)
                if after.ndim == 2:
                    shape_desc = f"{after.shape[1]}x{after.shape[0]} {after.dtype}"
                elif after.ndim == 3:
                    shape_desc = f"Z={after.shape[0]} {after.shape[2]}x{after.shape[1]} {after.dtype}"
                else:
                    shape_desc = f"ndim={after.ndim} {after.dtype}"
                size_after = tpath.stat().st_size if tpath.is_file() else 0
                write_elapsed = time.monotonic() - read_start - read_elapsed
                emit_log(
                    f"[{file_index}/{total_files}] wrote {rel} ({shape_desc}, "
                    f"{size_after} bytes, read {read_elapsed:.2f}s write {write_elapsed:.2f}s)",
                )
                changed += 1
                bytes_total += size_after
                print(f"Applied geometry [{file_index}/{total_files}] {rel}", flush=True)
                append_geometry_history(
                    bundle_root,
                    {
                        "kind": "file",
                        "source": apply_source,
                        "slice_id": slice_id,
                        "ops": ops_to_js_list(ops),
                        "file": rel_k,
                        "ok": True,
                    },
                )
                if progress is not None:
                    record_completion(bundle_root, progress, rel_k, slice_id)
            except Exception as exc:
                failed.append(f"{rel}: {exc}")
                emit_log(f"[{file_index}/{total_files}] FAILED {rel}: {exc}")
                print(f"Failed geometry [{file_index}/{total_files}] {rel}", flush=True)

    return changed, bytes_total, failed, total_files


def run_repair_jobs(
    bundle_root: Path,
    cfg: dict,
    apply_source: str = "repair",
) -> tuple[int, int, list[str], int]:
    jobs = collect_repair_jobs(bundle_root, cfg)
    total_files = len(jobs)
    preview_scale = float(cfg.get("preview_scale") or 0.05)
    emit_log(f"Geometry repair: {total_files} target(s)")
    print(total_files, flush=True)
    changed = 0
    bytes_total = 0
    failed: list[str] = []
    for i, (slice_id, ops, tpath, strategy, branch) in enumerate(jobs):
        idx = i + 1
        try:
            rel = tpath.relative_to(bundle_root)
        except ValueError:
            rel = Path(tpath.name)
        if strategy == "skip":
            emit_log(f"[{idx}/{total_files}] skip {rel}")
            continue
        emit_log(f"[{idx}/{total_files}] repair {strategy} {rel}")
        try:
            if strategy == "derivatives_from_original":
                rel_path = str(tpath.relative_to(bundle_root)).replace("\\", "/")
                if not branch:
                    branch = Path(rel_path).stem.split("_")[-1] if "_" in Path(rel_path).stem else ""
                _repair_derivatives_from_original(
                    bundle_root,
                    slice_id,
                    branch,
                    rel_path,
                    ops,
                    preview_scale,
                )
            elif strategy == "transform_original":
                if not tpath.is_file():
                    raise FileNotFoundError(f"Missing {rel}")
                if ops:
                    transform_file(tpath, ops)
            else:
                if not tpath.is_file():
                    raise FileNotFoundError(f"Missing {rel}")
                if ops:
                    transform_file(tpath, ops)
            changed += 1
            bytes_total += tpath.stat().st_size if tpath.is_file() else 0
            print(f"Repaired geometry [{idx}/{total_files}] {rel}", flush=True)
            try:
                rel_key = str(rel).replace("\\", "/")
            except Exception:
                rel_key = str(rel)
            append_geometry_history(
                bundle_root,
                {
                    "kind": "file",
                    "source": apply_source,
                    "slice_id": slice_id,
                    "ops": ops_to_js_list(ops),
                    "file": rel_key,
                    "strategy": strategy,
                    "ok": True,
                },
            )
        except Exception as exc:
            failed.append(f"{rel}: {exc}")
            emit_log(f"[{idx}/{total_files}] FAILED {rel}: {exc}")
    return changed, bytes_total, failed, total_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply geometry to CZI import outputs")
    parser.add_argument("-b", "--bundle", required=True)
    parser.add_argument("-j", "--json", required=True, help="Import config or geometry-only JSON")
    args = parser.parse_args()
    args.bundle = str(args.bundle).strip()
    args.json = str(args.json).strip()

    started = time.monotonic()
    bundle_root = Path(args.bundle).resolve()
    try:
        cfg = load_import_config(args.json)
    except FileNotFoundError as exc:
        emit_result({"ok": False, "error": str(exc)})
        return 1

    repair_mode = cfg.get("repair_mode")
    geometry_hash = str(cfg.get("geometry_hash") or "")
    config_fingerprint = str(cfg.get("config_fingerprint") or "")
    resume = bool(cfg.get("resume_apply"))
    apply_source = str(cfg.get("apply_source") or "apply")

    if repair_mode == "geometry":
        changed, bytes_total, failed, total_files = run_repair_jobs(
            bundle_root,
            cfg,
            apply_source=apply_source,
        )
        elapsed = time.monotonic() - started
        result = {
            "ok": len(failed) == 0,
            "changed": changed,
            "files_total": total_files,
            "bytes_total": bytes_total,
            "elapsed_sec": round(elapsed, 2),
            "failed": failed,
            "repair_mode": "geometry",
        }
        write_last_result(bundle_root, {**result, "geometry_hash": geometry_hash, "config_fingerprint": config_fingerprint})
        if result["ok"]:
            append_geometry_history(
                bundle_root,
                {
                    "kind": "run",
                    "source": apply_source,
                    "repair_mode": "geometry",
                    "ok": True,
                    "changed": changed,
                    "files_total": total_files,
                },
            )
        emit_result(result)
        print("Done!", flush=True)
        return 0 if not failed else 1

    geometry = cfg.get("geometry") or {}
    if not geometry:
        emit_result({"ok": True, "changed": 0, "files_total": 0, "bytes_total": 0, "elapsed_sec": 0})
        print("Done!", flush=True)
        return 0

    jobs = collect_geometry_jobs(bundle_root, geometry, cfg)
    total_files, failed_probes = preflight_log(jobs, bundle_root)
    print(total_files, flush=True)
    if failed_probes:
        emit_result(
            {
                "ok": False,
                "error": "preflight_failed",
                "failed_probes": failed_probes,
                "files_total": total_files,
            },
        )
        write_last_result(
            bundle_root,
            {
                "ok": False,
                "error": "preflight_failed",
                "failed_probes": failed_probes,
                "geometry_hash": geometry_hash,
                "config_fingerprint": config_fingerprint,
            },
        )
        return 1

    progress = load_progress(bundle_root)
    if progress and (
        progress.get("geometry_hash") != geometry_hash
        or progress.get("config_fingerprint") != config_fingerprint
    ):
        progress = None
    if not resume or not progress:
        progress = {
            "config_fingerprint": config_fingerprint,
            "geometry_hash": geometry_hash,
            "files_total": total_files,
            "completed": 0,
            "completed_paths": [],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        from geometry_apply_progress import save_progress

        save_progress(bundle_root, progress)
    else:
        emit_log(f"Resuming apply: {progress.get('completed', 0)}/{total_files} already done")

    changed, bytes_total, failed, _ = run_transform_jobs(
        bundle_root,
        jobs,
        progress,
        resume,
        apply_source=apply_source,
    )

    elapsed = time.monotonic() - started
    result = {
        "ok": len(failed) == 0,
        "changed": changed,
        "files_total": total_files,
        "bytes_total": bytes_total,
        "elapsed_sec": round(elapsed, 2),
        "failed": failed,
    }
    write_last_result(
        bundle_root,
        {**result, "geometry_hash": geometry_hash, "config_fingerprint": config_fingerprint},
    )
    if result["ok"]:
        clear_progress(bundle_root)
        append_geometry_history(
            bundle_root,
            {
                "kind": "run",
                "source": apply_source,
                "ok": True,
                "changed": changed,
                "files_total": total_files,
                "geometry_hash": geometry_hash,
            },
        )
    emit_result(result)
    print("Done!", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

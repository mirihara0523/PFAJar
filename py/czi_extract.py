"""Extract CZI channels to z-stack TIFFs, previews, and max projections."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import perf_log
from datetime import datetime, timezone
from pathlib import Path

from czi_common import (
    CANONICAL_REL,
    PREVIEW_FORMAT_VERSION,
    ROLE_DAPI,
    ROLE_SIGNAL_AXONS,
    ROLE_UNUSED,
    assess_mosaic_import,
    bbox_origin,
    bbox_width_height,
    mosaic_region_for_scene,
    _subblock_lookup_kwargs,
    branch_for_channel,
    branch_for_role_key,
    clamp_preview_scale,
    collapse_z_stack_to_2d,
    dapi_preview_path,
    orient_dapi_preview_path,
    default_slice_id,
    dim_size,
    emit_log,
    emit_progress,
    emit_progress_phase,
    emit_result,
    load_import_config,
    max_input_dir,
    max_output_run_dir,
    meta_state_path,
    natural_sort_filenames,
    natural_sort_key,
    natural_sort_slice_ids,
    normalized_dim_blocks,
    original_scans_path,
    preview_autoscale_to_uint8,
    read_czi_plane,
    build_files_lookup,
    resolve_file_entry,
    role_key_for_channel,
    signal_preview_path,
    slice_order_ordinal_map,
    write_import_state,
    z_indices_with_data,
)
from run_manifest import write_run_manifest

# Populated by staged imports in main().
np = None
cv2 = None
tiff = None
CziFile = None

# Preview selection only needs to distinguish the focal plane.  A bounded,
# regularly-spaced sample keeps the same p99 criterion without sorting every
# pixel in every full-resolution Z plane.  Close candidates are resolved with
# the original full-resolution calculation below.
PREVIEW_SCORE_SAMPLE_PIXELS = 262_144
PREVIEW_SCORE_TIE_RATIO = 0.015


def downscale_plane(plane, scale: float):
    if scale >= 0.999:
        return plane
    h, w = plane.shape[:2]
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(plane, (new_w, new_h), interpolation=cv2.INTER_AREA)


def preview_downscale_preserve_percentile(plane, scale: float):
    """Create an 8-bit preview without making a full-resolution float image.

    The display stretch still uses the historical full-plane 2nd/98th
    percentile bounds.  Only the already-downscaled preview is clipped and
    scaled, which avoids the multi-gigabyte float64 intermediates created by
    scaling the complete CZI plane before reducing it to 5%.
    """
    arr = np.asarray(plane)
    if arr.dtype == np.uint8:
        # Keep repair/import output byte-identical for existing 8-bit inputs.
        return downscale_plane(arr, scale)
    if arr.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    # CZI planes are integer (normally uint16).  Preserve the established
    # float-capable path for an unexpected floating source rather than
    # silently changing NaN/Inf handling.
    if not np.issubdtype(arr.dtype, np.integer):
        return downscale_plane(preview_autoscale_to_uint8(arr), scale)
    lo, hi = np.percentile(arr, (2.0, 98.0))
    lo, hi = float(lo), float(hi)
    if hi <= lo:
        hi = lo + 1.0
    small = downscale_plane(arr, scale).astype(np.float64, copy=False)
    return np.clip((np.clip(small, lo, hi) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def _cluster_edges(edges: list[int], tol: int = 2) -> list[int]:
    """Merge near-duplicate tile edges (mosaic overlap jitter) within `tol` px."""
    out: list[int] = []
    for v in sorted(edges):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def _seam_grid_from_czi(czi, scene: int, channel: int, z: int, slice_id: str = "") -> dict | None:
    """Tile-seam grid from CZI mosaic bboxes, in the coordinate frame of the
    full-res stitched plane (scene extent, scale 1.0 — same as original_scans).

    Boundaries are recorded both as absolute px and as fractions of the scene
    extent so a downstream (possibly uniformly downscaled) image can scale them
    to its own dimensions. Returns None (logging the reason via seamgrid_skip)
    when a grid cannot be derived. Non-fatal by contract.
    """
    def _skip(reason: str):
        emit_log(f"  LOG: seamgrid_skip {slice_id} {reason}")
        return None

    get_tiles = getattr(czi, "get_all_mosaic_tile_bounding_boxes", None)
    if not callable(get_tiles):
        return _skip("no_get_all_mosaic_tile_bounding_boxes")
    # Try WITHOUT S first: single-scene-per-file mosaics report an S range like
    # [62, 63) (nonzero absolute index), so _subblock_lookup_kwargs adds S=<import
    # scene=0>, which get_all_mosaic_tile_bounding_boxes rejects as overspecified
    # ("S value 0 invalid ∉ [62,63)"). Omitting S returns all tiles of the single
    # scene (verified via probe_seamgrid.py). Fall back to S=scene for genuine
    # multi-scene files.
    tiles: dict = {}
    last_exc = None
    for _kw in ({"Z": z, "C": channel}, {"Z": z, "C": channel, "S": scene}):
        try:
            tiles = get_tiles(**_kw) or {}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            tiles = {}
            continue
        if tiles:
            break
    if len(tiles) < 2:
        if not tiles and last_exc is not None:
            return _skip(f"get_tiles_raised={last_exc!r}")
        return _skip(f"tiles={len(tiles)}(<2)")
    # Scene extent. get_mosaic_scene_bounding_box(scene) needs the CZI's own
    # scene index, which differs from the import's `scene` arg (a single-scene
    # file can carry a nonzero CZI scene index, e.g. 62), so it raises
    # "Scene Index Not Valid". Derive the extent from the tile union instead —
    # it equals the read_mosaic (original_scans) extent and needs no scene index.
    extent_source = "scene_bbox"
    region = mosaic_region_for_scene(czi, scene)
    if region is not None:
        sx, sy, sw, sh = region
    else:
        minx = miny = maxx = maxy = None
        for tb in tiles.values():
            tx, ty = bbox_origin(tb)
            tw, th = bbox_width_height(tb)
            if tw <= 0 or th <= 0:
                continue
            x2, y2 = tx + tw, ty + th
            minx = tx if minx is None else min(minx, tx)
            miny = ty if miny is None else min(miny, ty)
            maxx = x2 if maxx is None else max(maxx, x2)
            maxy = y2 if maxy is None else max(maxy, y2)
        if minx is None:
            return _skip("no_valid_tiles")
        sx, sy, sw, sh = minx, miny, maxx - minx, maxy - miny
        extent_source = "tile_union"
    if sw <= 0 or sh <= 0:
        return _skip(f"bad_scene_extent={sw}x{sh}")
    xs: list[int] = []
    ys: list[int] = []
    for tb in tiles.values():
        tx, ty = bbox_origin(tb)
        tw, th = bbox_width_height(tb)
        if tw <= 0 or th <= 0:
            continue
        rx, ry = tx - sx, ty - sy
        if 0 < rx < sw:
            xs.append(int(rx))  # interior left edge -> vertical seam (column boundary)
        if 0 < ry < sh:
            ys.append(int(ry))  # interior top edge -> horizontal seam (row boundary)

    def _axis(edges: list[int], extent: int) -> dict:
        b = _cluster_edges(edges)
        per = None
        if len(b) >= 2:
            diffs = sorted(b[i + 1] - b[i] for i in range(len(b) - 1))
            m = len(diffs)
            per = float(diffs[m // 2] if m % 2 else (diffs[m // 2 - 1] + diffs[m // 2]) / 2)
        # Drop boundaries within half a period of either outer edge: stage jitter
        # can leave the outer tile edge ~1px inside the scene as a false seam.
        if per and per > 0:
            margin = per * 0.5
            b = [v for v in b if margin <= v <= extent - margin]
        ph = float(b[0]) if b else None
        return {
            "boundaries_px": b,
            "boundaries_frac": [round(v / extent, 6) for v in b],
            "period_px": per,
            "phase_px": ph,
        }

    return {
        "version": 1,
        "source": "czi_bbox",
        "extent_source": extent_source,
        "scene_extent_px": [int(sw), int(sh)],
        "n_tiles": len(tiles),
        "vertical": _axis(xs, sw),
        "horizontal": _axis(ys, sh),
    }


def _write_seam_grid_sidecar(czi, scene, channel, z, slice_id, bundle_root) -> None:
    """Write <bundle>/.masonjar/seamgrid/<slice_id>.json once per scene (grid is
    channel-independent). Idempotent and non-fatal — never aborts an import."""
    if not bundle_root or not slice_id:
        return
    try:
        out_dir = Path(bundle_root) / ".masonjar" / "seamgrid"
        dest = out_dir / f"{slice_id}.json"
        if dest.exists():
            return
        grid = _seam_grid_from_czi(czi, scene, channel, z, slice_id)
        if grid is None:
            return  # reason already logged by _seam_grid_from_czi
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.tmp.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(grid, f, indent=2)
        tmp.replace(dest)
        emit_log(
            f"  LOG: seamgrid {slice_id} tiles={grid['n_tiles']} "
            f"vseams={len(grid['vertical']['boundaries_px'])} "
            f"hseams={len(grid['horizontal']['boundaries_px'])}"
        )
    except Exception as exc:  # noqa: BLE001
        emit_log(f"  LOG: seamgrid_failed {slice_id} {exc!r}")


# Aggregate per-phase timers (env-gated). Per-item logging would be hundreds of
# lines, so we accumulate read / write / preview / max time across all items and
# emit the totals once at the end of main() — enough to see where import time
# goes. worker already logs czi_extract.py.total.
_PERF_ACC: dict = {}


def _perf_acc(label: str, dt: float) -> None:
    if perf_log.perf_enabled():
        _PERF_ACC[label] = _PERF_ACC.get(label, 0.0) + dt


def _perf_flush() -> None:
    if not perf_log.perf_enabled() or not _PERF_ACC:
        return
    for key in sorted(_PERF_ACC):
        perf_log.perf_log("czi_extract." + key, _PERF_ACC[key] * 1000.0)
    _PERF_ACC.clear()


# Emit cumulative-so-far phase totals during a long import so progress is
# visible before the job ends. Does NOT clear the accumulator, so the final
# _perf_flush() still reports grand totals. Every PROGRESS marker is suffixed
# ".sofar" and tagged with the item count to distinguish it from the finals.
_PERF_PROGRESS_EVERY = 25


def _perf_flush_progress(done: int, total: int) -> None:
    if not perf_log.perf_enabled() or not _PERF_ACC:
        return
    for key in sorted(_PERF_ACC):
        perf_log.perf_log(
            "czi_extract.%s.sofar[%d/%d]" % (key, done, total),
            _PERF_ACC[key] * 1000.0,
        )


def max_project_z(stack):
    return collapse_z_stack_to_2d(stack)


def max_project_plane_iter(planes):
    """Reduce an iterable of CZI planes while retaining only the output plane.

    This is the safe building block for a later streaming CZI writer. It does
    not alter import behavior yet because preview selection still needs the
    source planes and the current TIFF writer emits a complete stack.
    """
    projected = None
    for plane in planes:
        current = np.asarray(plane)
        if current.ndim != 2:
            raise ValueError(f"Expected 2-D CZI plane, got shape {current.shape}")
        if projected is None:
            projected = current.copy()
        elif projected.shape != current.shape:
            raise ValueError("CZI planes have inconsistent dimensions")
        else:
            np.maximum(projected, current, out=projected)
    if projected is None:
        raise ValueError("CZI plane iterator is empty")
    return projected


def max_projection_from_tiff(input_path: Path):
    # CZI sequential output may contain one TIFF series per Z plane.
    # Default imread selects only the first series; enumerate physical pages.
    with tiff.TiffFile(str(input_path)) as tif:
        def planes():
            for page in tif.pages:
                arr = np.asarray(page.asarray())
                if arr.ndim == 2:
                    yield arr
                elif arr.ndim == 3:
                    # Legacy grayscale stacks stored within a single page.
                    yield from arr
                else:
                    raise ValueError(f"Unsupported CZI TIFF page shape: {arr.shape}")
        return max_project_plane_iter(planes())


def max_project_file(input_path: Path, output_path: Path, *, bit_depth: int = 8) -> None:
    out = max_projection_from_tiff(input_path)
    scale_max = _uint16_peak(out) if bit_depth < 16 and out.dtype == np.uint16 else None
    out = coerce_stack_depth(out, bit_depth, scale_max=scale_max)
    write_pipeline_tiff(output_path, out, bit_depth)


def bit_depth_for_role(cfg: dict, role_key: str) -> int:
    by_role = cfg.get("bit_depth_by_role") or {}
    raw = by_role.get(role_key, 8)
    try:
        depth = int(raw)
    except (TypeError, ValueError):
        depth = 8
    if role_key != ROLE_SIGNAL_AXONS:
        return 8
    return 16 if depth == 16 else 8


def _uint16_peak(arr) -> int:
    return max(1, int(np.max(arr)))


def uint16_peak_scan_settings(cfg: dict | None) -> tuple[str, float]:
    """Return the opt-in peak-scan strategy for 8-bit CZI output.

    ``exact`` is deliberately the default: it reads the original pixels and
    preserves the historical whole-stack maximum. ``approximate_sample`` is
    an experiment for mosaic files; it estimates the peak from the CZI
    reader's downsampled plane. A sample can miss a sparse hot pixel, so it
    must never silently replace the exact default.
    """
    raw = str((cfg or {}).get("uint16_peak_scan_mode") or "exact").strip().lower()
    mode = "approximate_sample" if raw == "approximate_sample" else "exact"
    try:
        scale = float((cfg or {}).get("uint16_peak_sample_scale", 0.05))
    except (TypeError, ValueError):
        scale = 0.05
    # Values outside this range are not meaningful to the CZI scale-factor API.
    return mode, min(1.0, max(0.01, scale))


def _uint16_to_uint8_stack_linear(arr, *, scale_max: int | None = None):
    """Fiji-style 16→8: linear scale by stack/plane peak (not blind /257)."""
    work = np.asarray(arr)
    peak = max(1, int(scale_max if scale_max is not None else _uint16_peak(work)))
    # Keep the conversion integer based.  The previous float64 expression
    # expanded a large Z-stack to 8 bytes/pixel before reducing it to uint8,
    # causing RAM and paging spikes during import.
    if work.dtype == np.uint16:
        scaled = (work.astype(np.uint32) * 255) // peak
        return np.clip(scaled, 0, 255).astype(np.uint8)
    return np.clip(work.astype(np.float32) * 255.0 / peak, 0, 255).astype(np.uint8)


def _uint16_to_uint8_lut(scale_max: int) -> "np.ndarray":
    """Build the exact floor-based 16→8 map once for a stack's known peak."""
    peak = max(1, int(scale_max))
    values = np.arange(65536, dtype=np.uint32)
    return np.minimum((values * 255) // peak, 255).astype(np.uint8)


def coerce_stack_depth(
    arr,
    bit_depth: int,
    *,
    scale_max: int | None = None,
    uint16_lut=None,
):
    work = np.asarray(arr)
    if bit_depth >= 16:
        if work.dtype == np.uint16:
            return work
        if np.issubdtype(work.dtype, np.floating):
            scaled = np.clip(work, 0, None)
            if scaled.max() <= 1.0:
                scaled = scaled * 65535.0
            elif scaled.max() <= 255.0:
                scaled = scaled * 257.0
            return np.clip(scaled, 0, 65535).astype(np.uint16)
        if work.dtype == np.uint8:
            return work.astype(np.uint16) * 257
        return np.clip(work, 0, 65535).astype(np.uint16)
    if work.dtype == np.uint8:
        return work
    if np.issubdtype(work.dtype, np.floating):
        if work.max() <= 1.0:
            return np.clip(work * 255.0, 0, 255).astype(np.uint8)
        return np.clip(work, 0, 255).astype(np.uint8)
    if work.dtype == np.uint16:
        if uint16_lut is not None:
            lut = np.asarray(uint16_lut)
            if lut.dtype != np.uint8 or lut.size != 65536:
                raise ValueError("uint16_lut must contain exactly 65,536 uint8 values")
            return lut[work]
        return _uint16_to_uint8_stack_linear(work, scale_max=scale_max)
    return np.clip(work, 0, 255).astype(np.uint8)


def write_pipeline_tiff(path: Path, arr, bit_depth: int, compression=None) -> None:
    work = np.asarray(arr)
    scale_max = None
    if bit_depth < 16 and work.dtype == np.uint16:
        scale_max = _uint16_peak(work)
        emit_log(f"  LOG: uint16_to_uint8 stack linear scale peak={scale_max}")
    uint16_lut = _uint16_to_uint8_lut(scale_max) if scale_max is not None else None
    out = coerce_stack_depth(arr, bit_depth, scale_max=scale_max, uint16_lut=uint16_lut)
    # Lossless compression (zlib) only where requested — used for original_scans
    # z-stacks to cut NAS write bytes. Falls back to uncompressed on older
    # tifffile that lacks the compression kwarg. Pixel values are unchanged.
    if compression:
        try:
            tiff.imwrite(str(path), out, photometric="minisblack", compression=compression)
            return
        except (TypeError, ValueError):
            pass
    tiff.imwrite(str(path), out, photometric="minisblack")


def write_pipeline_tiff_iter(path: Path, planes, bit_depth: int, *,
                             scale_max: int | None = None, compression="zlib",
                             on_output_plane=None) -> int:
    """Commit a completed TIFF atomically; preserve existing output on failure."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                     suffix=".tmp.tif", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        count = _write_pipeline_tiff_iter_uncommitted(
            temporary, planes, bit_depth, scale_max=scale_max, compression=compression,
            on_output_plane=on_output_plane)
        os.replace(temporary, path)
        return count
    finally:
        temporary.unlink(missing_ok=True)


def _write_pipeline_tiff_iter_uncommitted(
    path: Path,
    planes,
    bit_depth: int,
    *,
    scale_max: int | None = None,
    compression="zlib",
    on_output_plane=None,
) -> int:
    """Write 2-D planes one TIFF page at a time.

    ``scale_max`` must be supplied for uint16→uint8 conversion when the
    caller wants the same whole-stack peak scaling as ``write_pipeline_tiff``.
    A separate peak pass over the CZI can provide it without retaining planes.
    Returns the number of pages written. The caller owns the source iterator.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # All uint16 planes in an 8-bit stack share one exact peak.  Reusing this
    # 64 KiB map avoids a uint32 multiply/divide temporary for every plane.
    uint16_lut = _uint16_to_uint8_lut(scale_max) if scale_max is not None and bit_depth < 16 else None
    with tiff.TiffWriter(str(path), bigtiff=True) as writer:
        for plane in planes:
            arr = np.asarray(plane)
            if arr.ndim != 2:
                raise ValueError(f"Expected 2-D plane, got shape {arr.shape}")
            out = coerce_stack_depth(
                arr, bit_depth, scale_max=scale_max, uint16_lut=uint16_lut,
            )
            if on_output_plane is not None:
                on_output_plane(out)
            kwargs = {"photometric": "minisblack"}
            if compression:
                kwargs["compression"] = compression
            try:
                writer.write(out, **kwargs)
            except (TypeError, ValueError):
                # Older tifffile versions may not support the requested codec.
                kwargs.pop("compression", None)
                writer.write(out, **kwargs)
            count += 1
    if count == 0:
        raise ValueError("No planes supplied for TIFF output")
    return count


def read_plane(czi, scene: int, z: int, channel: int):
    return read_czi_plane(czi, scene, z, channel)


def select_dapi_z_indices(czi, scene: int, channel: int) -> tuple[list[int], str]:
    """Return metadata-selected DAPI Z positions and an auditable confidence mode.

    DAPI is commonly stored as one focal plane inside a CZI whose dimension
    metadata still advertises a larger Z range.  ``z_indices_with_data`` is
    the authoritative metadata query already used for all channels.  Do not
    guess a fixed Z index when that query is ambiguous: a wrong guess silently
    produces an empty DAPI preview.  The caller keeps the existing behavior
    for an ambiguous result until a later isolated probe can resolve it.
    """
    z_indices = z_indices_with_data(czi, scene, channel)
    if len(z_indices) == 1:
        emit_log(
            "  LOG: dapi_z_selection mode=metadata_single "
            f"channel={channel} z={z_indices[0]}"
        )
        return z_indices, "metadata_single"
    emit_log(
        "  LOG: dapi_z_selection mode=ambiguous_metadata "
        f"channel={channel} candidates={z_indices}; retaining candidates"
    )
    return z_indices, "ambiguous_metadata"


# ZEN's 1-indexed position 5 (== 0-indexed Z=4) is the confirmed, common DAPI
# focal plane (user-verified, e.g. M581-01(1) C=2/Z=4). When metadata cannot
# narrow an ambiguous candidate set to one Z, probe candidates nearest Z=4
# first and work outward, alternating: 4, 5, 3, 6, 2, 7, 1, 8, 0, 9, ...
_DAPI_PREFERRED_Z = 4


def _dapi_z_priority_order(candidates: list[int], preferred: int = _DAPI_PREFERRED_Z) -> list[int]:
    """Order ambiguous DAPI Z candidates outward from ``preferred``.

    Ties (equal distance) favor the higher index first, matching the
    confirmed probe order 4, 5, 3, 6, 2, 7, ... Duplicate candidates are
    dropped; input order otherwise does not affect the result.
    """
    deduped = list(dict.fromkeys(int(c) for c in candidates))

    def _rank(value: int) -> tuple[int, int]:
        offset = value - preferred
        return (abs(offset), 0 if offset >= 0 else 1)

    return sorted(deduped, key=_rank)


def _dapi_probe_child(payload: dict) -> int:
    """Disposable-child body for ``--dapi-probe``: read exactly one candidate
    DAPI Z-plane and report whether it came back with real pixel data.

    Runs in its own throwaway process (see ``_run_dapi_z_probe``) so a native
    aicspylibczi access violation on a bad candidate plane terminates only
    this process, never the parent import.
    """
    czi_path = Path(str(payload.get("czi_path") or ""))
    scene = int(payload.get("scene", 0))
    channel = int(payload.get("channel", 0))
    z = int(payload.get("z", 0))
    if not czi_path.is_file():
        _child_event("DAPI_PROBE", {"ok": False, "z": z, "error": f"CZI not found: {czi_path}"})
        return 2
    czi = None
    try:
        czi = CziFile(str(czi_path))
        plane = read_plane(czi, scene, z, channel)
        arr = np.asarray(plane)
        ok = arr.size > 0
        _child_event("DAPI_PROBE", {"ok": ok, "z": z})
        return 0 if ok else 1
    except Exception as exc:  # normal Python failures still count as "unreadable"
        _child_event("DAPI_PROBE", {"ok": False, "z": z, "error": repr(exc)})
        return 1
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()


def _run_dapi_z_probe(
    bundle_root: Path, config_path: str, czi_path: Path, scene: int, channel: int, z: int,
) -> bool:
    """Launch one disposable child that reads a single candidate DAPI Z-plane.

    Only used when DAPI metadata is ambiguous (``select_dapi_z_indices`` mode
    ``ambiguous_metadata``). A native crash while reading this one candidate
    terminates only this throwaway process; the caller (``resolve_dapi_z``)
    logs it and moves on to the next candidate in priority order. Returns
    True only when the child reports a successful, non-empty plane read.
    """
    payload = {"czi_path": str(czi_path), "scene": scene, "channel": channel, "z": z}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        payload_path = Path(handle.name)
    try:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "-b", str(bundle_root), "-j", str(config_path),
            "--dapi-probe", str(payload_path),
        ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        ok = False
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith("CZI_CHILD_DAPI_PROBE:"):
                try:
                    result = json.loads(line.split(":", 1)[1])
                    ok = bool(result.get("ok"))
                except json.JSONDecodeError:
                    ok = False
            elif line:
                print(line, flush=True)
        exit_code = proc.wait()
        if not ok:
            emit_log(
                f"  LOG: dapi_z_probe_failed channel={channel} z={z} exit_code={exit_code}"
            )
        return ok
    finally:
        payload_path.unlink(missing_ok=True)


def resolve_dapi_z(
    bundle_root: Path,
    config_path: str,
    czi,
    czi_path: Path,
    scene: int,
    channel: int,
) -> tuple[int | None, str]:
    """Resolve the single true DAPI Z-plane for one CZI scene/channel.

    A single unambiguous metadata candidate (``select_dapi_z_indices`` mode
    ``metadata_single``) is used directly — no probing needed. An ambiguous
    result is resolved by trying candidates nearest Z=4 first (see
    ``_dapi_z_priority_order``), each in its own disposable child
    (``_run_dapi_z_probe``) so one candidate's native crash cannot take down
    the whole import: it is logged and the next candidate is tried. If every
    candidate crashes or reads empty, returns ``(None, "all_candidates_failed")``
    so the caller fails only this one DAPI item and continues with the rest
    of the CZI / import batch.

    Note: each probed candidate spawns a full disposable process (same cost
    as the per-file isolation child), so this only adds meaningful overhead
    when metadata is genuinely ambiguous — the common single-candidate case
    is unaffected.
    """
    z_indices, mode = select_dapi_z_indices(czi, scene, channel)
    if mode == "metadata_single":
        return z_indices[0], mode
    ordered = _dapi_z_priority_order(z_indices)
    emit_log(f"  LOG: dapi_z_probe_start channel={channel} order={ordered}")
    for z in ordered:
        if _run_dapi_z_probe(bundle_root, config_path, czi_path, scene, channel, z):
            emit_log(f"  LOG: dapi_z_selection mode=probed channel={channel} z={z}")
            return z, "probed"
        emit_log(
            f"  LOG: dapi_z_probe_crash channel={channel} z={z}; trying next candidate"
        )
    emit_log(
        f"  LOG: dapi_z_selection mode=all_candidates_failed channel={channel} candidates={ordered}"
    )
    return None, "all_candidates_failed"


def _preview_plane_from_stack(planes: list, z_indices: list[int]):
    """Pick brightest plane for preview (sparse counterstain / single focal plane)."""
    if not planes:
        return None
    if len(planes) == 1:
        return planes[0]
    import numpy as _np

    best = planes[0]
    best_score = -1.0
    for plane in planes:
        arr = _np.asarray(plane)
        if arr.size == 0:
            continue
        score = float(_np.percentile(arr, 99))
        if score > best_score:
            best_score = score
            best = plane
    return best


def _preview_score_sample(plane) -> tuple[float, int]:
    """Return a bounded-sample p99 score and the number of scored pixels.

    The preview itself is still generated from the selected full-resolution
    plane.  Regular sampling is appropriate for p99: isolated hot pixels are
    below that percentile by definition, while broad focal signal remains
    represented.  Full scoring remains available to resolve near ties.
    """
    arr = np.asarray(plane)
    if arr.size == 0:
        return -1.0, 0
    if arr.size <= PREVIEW_SCORE_SAMPLE_PIXELS:
        return float(np.percentile(arr, 99)), int(arr.size)
    stride = max(1, int(np.ceil(np.sqrt(arr.size / PREVIEW_SCORE_SAMPLE_PIXELS))))
    sample = arr[::stride, ::stride]
    return float(np.percentile(sample, 99)), int(sample.size)


def _preview_scores_are_close(left: float, right: float) -> bool:
    """Whether sampled p99 scores need an exact full-plane tie break."""
    return abs(left - right) <= max(1.0, max(abs(left), abs(right)) * PREVIEW_SCORE_TIE_RATIO)


def extract_z_stack(
    czi,
    scene: int,
    channel: int,
    z_indices: list[int],
    out_path: Path,
    preview_path: Path | None,
    preview_scale: float,
    slice_id: str = "",
    bundle_root: Path | None = None,
    *,
    cfg: dict | None = None,
    role_key: str = "",
    stream_max_path: Path | None = None,
) -> None:
    perf_log.perf_memory("czi.before_z_stack")
    if not z_indices:
        raise ValueError("No Z planes selected")
    depth = bit_depth_for_role(cfg or {}, role_key)
    # [plane, sampled_p99, optional_exact_p99].  At most one full-res plane is
    # retained, preserving the streaming memory bound.
    preview_holder = [None, -1.0, None]
    preview_sample_pixels = 0
    preview_exact_ties = 0
    n_z = len(z_indices)
    read_total = 0.0
    peak_scan_total = 0.0
    preview_total = 0.0
    stream_max_holder = [None]
    # A one-plane 8-bit output needs its exact peak and its output pixels from
    # the very same plane.  Retaining that one plane avoids a pointless second
    # native CZI read without changing scaling or the streaming rule for Z
    # stacks.
    single_peak_plane = None

    def accumulate_stream_max(out_plane) -> None:
        """Accumulate the exact plane representation being written to TIFF."""
        current = np.asarray(out_plane)
        if stream_max_holder[0] is None:
            stream_max_holder[0] = current.copy()
        else:
            np.maximum(stream_max_holder[0], current, out=stream_max_holder[0])

    def read_one(z):
        nonlocal read_total
        start = time.perf_counter()
        try:
            return read_plane(czi, scene, z, channel)
        finally:
            read_total += time.perf_counter() - start

    # The uint16 -> uint8 mapping uses one peak for the whole stack.
    # Exact is the established low-memory design: scan without retaining planes,
    # then reread for output. The optional sample mode retains that memory bound
    # but estimates the peak from reduced mosaic reads; it is intentionally not
    # the default because sparse hot pixels may be absent from a sample.
    scale_max = None
    if depth < 16:
        peak_mode, peak_sample_scale = uint16_peak_scan_settings(cfg)
        emit_log(
            "  LOG: uint16_peak_scan "
            f"mode={peak_mode} scale={peak_sample_scale:g}"
        )
        for z in z_indices:
            scan_start = time.perf_counter()
            if peak_mode == "approximate_sample":
                try:
                    plane = read_czi_plane(
                        czi, scene, z, channel,
                        sample_scale=peak_sample_scale,
                        allow_tile_composite=False,
                    )
                except Exception as exc:
                    # A sample path can be unsupported for a particular CZI.
                    # Preserve exact output rather than failing or guessing.
                    emit_log(
                        "  LOG: uint16_peak_scan sample_fallback=exact "
                        f"z={z} reason={exc}"
                    )
                    plane = read_plane(czi, scene, z, channel)
            else:
                plane = read_plane(czi, scene, z, channel)
            elapsed_scan = time.perf_counter() - scan_start
            read_total += elapsed_scan
            peak_scan_total += elapsed_scan
            if plane.dtype == np.uint16:
                scale_max = max(scale_max or 1, _uint16_peak(plane))
            if n_z == 1:
                single_peak_plane = plane
            else:
                del plane
        if single_peak_plane is not None:
            emit_log("  LOG: uint16_peak_scan single_plane_reuse=true")

    def plane_iter():
        nonlocal preview_total, preview_sample_pixels, preview_exact_ties
        for i, z in enumerate(z_indices):
            emit_log(f"  Reading Z {i + 1}/{n_z} ({slice_id} ch {channel})")
            if single_peak_plane is not None:
                plane = single_peak_plane
            else:
                plane = read_one(z)
            if preview_path is not None or role_key == ROLE_DAPI:
                start = time.perf_counter()
                score, sample_pixels = _preview_score_sample(plane)
                preview_sample_pixels = max(preview_sample_pixels, sample_pixels)
                if preview_holder[0] is None:
                    preview_holder[:] = [plane, score, None]
                elif _preview_scores_are_close(score, preview_holder[1]):
                    # A rare close call is resolved by the historical
                    # full-resolution p99 criterion, without retaining a
                    # second plane or rereading from CZI.
                    if preview_holder[2] is None:
                        preview_holder[2] = float(np.percentile(preview_holder[0], 99))
                    candidate_exact = float(np.percentile(plane, 99))
                    preview_exact_ties += 1
                    if candidate_exact > preview_holder[2]:
                        preview_holder[:] = [plane, score, candidate_exact]
                elif score > preview_holder[1]:
                    preview_holder[:] = [plane, score, None]
                preview_total += time.perf_counter() - start
            yield plane
            del plane

    parent = out_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    read_before_write = read_total
    start = time.perf_counter()
    write_pipeline_tiff_iter(
        out_path,
        plane_iter(),
        depth,
        scale_max=scale_max,
        on_output_plane=accumulate_stream_max if stream_max_path is not None else None,
    )
    elapsed = time.perf_counter() - start
    _perf_acc("read", read_total)
    _perf_acc("peak_scan", peak_scan_total)
    _perf_acc("preview", preview_total)
    _perf_acc("write", max(0.0, elapsed - (read_total - read_before_write) - preview_total))
    perf_log.perf_memory("czi.after_z_stack")
    # Record tile-seam grid (once per scene) for known-geometry seam correction.
    _write_seam_grid_sidecar(
        czi, scene, channel, z_indices[0] if z_indices else 0, slice_id, bundle_root
    )
    try:
        rel = out_path.relative_to(bundle_root) if bundle_root else out_path.name
    except ValueError:
        rel = out_path.name
    if out_path.exists():
        approx_mb = out_path.stat().st_size / (1024 * 1024)
    else:
        nbytes = preview_holder[0].nbytes if preview_holder[0] is not None else 0
        approx_mb = nbytes / (1024 * 1024)
    emit_log(f"  Writing {'plane' if n_z == 1 else 'z-stack'} -> {rel} ({approx_mb:.1f} MB approx)")

    if stream_max_path is not None:
        stream_max = stream_max_holder[0]
        if stream_max is None:
            raise RuntimeError("No planes available for streaming MAX")
        try:
            _t_stream_max = time.perf_counter()
            stream_max_path.parent.mkdir(parents=True, exist_ok=True)
            write_pipeline_tiff(stream_max_path, stream_max, depth)
            _perf_acc("max", time.perf_counter() - _t_stream_max)
            emit_log(f"  LOG: stream_max staged={stream_max_path.name}")
        except Exception as exc:  # noqa: BLE001
            stream_max_path.unlink(missing_ok=True)
            # MAX can be regenerated by the established TIFF reread path. Do
            # not turn a successful import into a failure for this optimization.
            emit_log(f"  LOG: stream_max fallback=tiff_reread reason={exc!r}")

    if preview_path is not None or role_key == ROLE_DAPI:
        emit_log(
            "  LOG: preview_select "
            f"mode=sampled_p99 max_sample_pixels={preview_sample_pixels} "
            f"exact_ties={preview_exact_ties}"
        )
        _t_prev = time.perf_counter()
        preview_plane = preview_holder[0]
        if preview_plane is None:
            raise RuntimeError("No planes available for preview")
        if role_key == ROLE_DAPI:
            if bundle_root is None:
                raise ValueError("bundle_root required for DAPI preview dual-write")
            write_dapi_preview_pair(bundle_root, slice_id, preview_plane, preview_scale)
        elif preview_path is not None:
            write_preview_at_path(
                preview_path,
                preview_plane,
                preview_scale,
                bundle_root,
                slice_id=slice_id,
            )
        _perf_acc("preview", time.perf_counter() - _t_prev)


def slice_id_for_scene(file_entry: dict, scene_index: int) -> str:
    for scene in file_entry.get("scenes") or []:
        if int(scene.get("index", -1)) == int(scene_index):
            return str(scene.get("sliceId") or "")
    basename = Path(file_entry.get("path") or file_entry.get("basename", "slice")).name
    scenes = file_entry.get("scenes") or [{"index": scene_index}]
    return default_slice_id(basename, scene_index, len(scenes) or 1)


def build_work_items(cfg: dict) -> list[dict]:
    channels = [c for c in cfg.get("channels") or [] if c.get("keep") and c.get("role") != ROLE_UNUSED]
    lookup = build_files_lookup(cfg.get("files") or [])

    items = []
    for ch in channels:
        if ch.get("role") == ROLE_UNUSED:
            continue
        if ch.get("role") == "other" and not branch_for_channel(ch):
            continue
        file_key = ch.get("file") or ""
        file_entry = resolve_file_entry(str(file_key), lookup)
        if not file_entry:
            continue
        czi_path = Path(file_entry["path"])
        for scene in file_entry.get("scenes") or [{"index": 0, "sliceId": "slice"}]:
            items.append(
                {
                    "czi_path": czi_path,
                    "file_entry": file_entry,
                    "scene_index": int(scene["index"]),
                    "slice_id": str(scene.get("sliceId") or slice_id_for_scene(file_entry, scene["index"])),
                    "channel_index": int(ch["index"]),
                    "channel": dict(ch),
                    "role_key": role_key_for_channel(ch),
                }
            )
    ordinal_map = slice_order_ordinal_map(cfg)
    if ordinal_map:
        items.sort(
            key=lambda item: (
                ordinal_map.get((str(item["czi_path"]), item["scene_index"]), 10**9),
                item["channel_index"],
            )
        )
    else:
        items.sort(
            key=lambda item: natural_sort_key(
                slice_id=item["slice_id"],
                basename=item["czi_path"].name,
                scene_index=item["scene_index"],
                path=str(item["czi_path"]),
                section_identifier=cfg.get("section_identifier"),
            )
        )
    return items


def collect_output_dirs(bundle_root: Path, work: list[dict]) -> list[Path]:
    dirs: set[Path] = set()
    for item in work:
        ch = item["channel"]
        slice_id = item["slice_id"]
        out_path = original_scans_path(bundle_root, ch, slice_id)
        dirs.add(out_path.parent)
        role = ch.get("role")
        if role == ROLE_DAPI:
            dirs.add(dapi_preview_path(bundle_root, slice_id).parent)
            dirs.add(orient_dapi_preview_path(bundle_root, slice_id).parent)
        elif branch_for_channel(ch):
            dirs.add(signal_preview_path(bundle_root, slice_id, ch).parent)
    meta = meta_state_path(bundle_root).parent
    dirs.add(meta)
    return sorted(dirs)


def import_aicspylibczi():
    """Import aicspylibczi with periodic still-loading logs."""
    stop = threading.Event()
    start = time.monotonic()

    def tick() -> None:
        while not stop.wait(5):
            elapsed = int(time.monotonic() - start)
            emit_log(f"  still loading aicspylibczi... ({elapsed}s)")
            mid = min(84, 50 + int(elapsed / 90 * 35))
            emit_progress_phase(mid, f"Loading aicspylibczi ({elapsed}s)")

    emit_log("Importing aicspylibczi (large native library; may take 30-90s on first run)...")
    emit_progress_phase(50, "Loading aicspylibczi...")
    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    try:
        from aicspylibczi import CziFile as _CziFile

        emit_log("  aicspylibczi ready")
        emit_progress_phase(85, "aicspylibczi loaded")
        return _CziFile
    except ImportError:
        raise
    finally:
        stop.set()
        thread.join(timeout=0.1)


def read_import_state(bundle_root: Path) -> dict:
    state_path = meta_state_path(bundle_root)
    if not state_path.is_file():
        return {}
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def plane_from_zstack(arr):
    if arr.ndim <= 2:
        return arr
    if arr.ndim == 3:
        return arr[arr.shape[0] // 2]
    return collapse_z_stack_to_2d(arr)


def preview_path_for_channel(bundle_root: Path, ch: dict, slice_id: str) -> Path | None:
    role = ch.get("role")
    if role == ROLE_DAPI:
        return dapi_preview_path(bundle_root, slice_id)
    if branch_for_channel(ch):
        return signal_preview_path(bundle_root, slice_id, ch)
    return None


def _is_low_res_preview_path(preview_path: Path) -> bool:
    parts = {p.lower() for p in preview_path.parts}
    return "00_dapi" in parts or "_previews" in parts


def _path_under_00_dapi(preview_path: Path) -> bool:
    return "00_dapi" in {p.lower() for p in preview_path.parts}


def _write_preview_array(preview, preview_path: Path) -> None:
    if _path_under_00_dapi(preview_path) and preview_path.suffix.lower() != ".png":
        raise ValueError(f"00_dapi accepts PNG only, not {preview_path}")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_low_res_preview_path(preview_path):
        cv2.imwrite(str(preview_path), preview)
        return
    if preview_path.suffix.lower() == ".png":
        cv2.imwrite(str(preview_path), preview)
    else:
        tiff.imwrite(str(preview_path), preview, photometric="minisblack")


def write_preview_at_path(
    preview_path: Path,
    plane,
    preview_scale: float,
    bundle_root: Path | None,
    slice_id: str = "",
) -> None:
    preview = preview_downscale_preserve_percentile(plane, preview_scale)
    _write_preview_array(preview, preview_path)
    try:
        prev_rel = preview_path.relative_to(bundle_root) if bundle_root else preview_path.name
    except ValueError:
        prev_rel = preview_path.name
    emit_log(f"  Writing preview -> {prev_rel} ({slice_id})")


def write_dapi_preview_pair(
    bundle_root: Path,
    slice_id: str,
    plane,
    preview_scale: float,
) -> None:
    """Write orient ``_previews/{id}_dapi.png`` and pipeline ``00_dapi/{id}.png``."""
    preview = preview_downscale_preserve_percentile(plane, preview_scale)
    for dest in (
        orient_dapi_preview_path(bundle_root, slice_id),
        dapi_preview_path(bundle_root, slice_id),
    ):
        _write_preview_array(preview, dest)
        try:
            rel = dest.relative_to(bundle_root)
        except ValueError:
            rel = dest.name
        emit_log(f"  Writing preview -> {rel} ({slice_id})")


def _remaining_00_dapi_tiffs(dapi_dir: Path) -> list[Path]:
    if not dapi_dir.is_dir():
        return []
    return sorted(dapi_dir.glob("*.tif")) + sorted(dapi_dir.glob("*.tiff"))


def migrate_low_res_tiffs(bundle_root: Path, cfg: dict, preview_scale: float) -> int:
    """Convert legacy low-res TIFFs to PNG; sync orient DAPI previews; delete TIFFs."""
    migrated = 0
    dapi_dir = bundle_root / CANONICAL_REL["dapi"]
    if dapi_dir.is_dir():
        legacy_tifs = _remaining_00_dapi_tiffs(dapi_dir)
        for tif_path in legacy_tifs:
            slice_id = tif_path.stem
            plane = max_projection_from_tiff(tif_path)
            write_dapi_preview_pair(bundle_root, slice_id, plane, preview_scale)
            tif_path.unlink(missing_ok=True)
            emit_log(f"  migrated {tif_path.name} -> PNG (pipeline + orient)")
            migrated += 1
        for png_path in sorted(dapi_dir.glob("*.png")):
            slice_id = png_path.stem
            orient_png = orient_dapi_preview_path(bundle_root, slice_id)
            if not orient_png.exists():
                arr = cv2.imread(str(png_path), cv2.IMREAD_UNCHANGED)
                if arr is not None:
                    _write_preview_array(np.asarray(arr), orient_png)
                    emit_log(f"  synced orient preview from {png_path.name}")
                    migrated += 1

    prev_dir = bundle_root / CANONICAL_REL["previews"]
    if prev_dir.is_dir():
        legacy_prev = sorted(prev_dir.glob("*.tif")) + sorted(prev_dir.glob("*.tiff"))
        for tif_path in legacy_prev:
            png_path = tif_path.with_suffix(".png")
            plane = max_projection_from_tiff(tif_path)
            write_preview_at_path(png_path, plane, preview_scale, bundle_root)
            tif_path.unlink(missing_ok=True)
            emit_log(f"  migrated {tif_path.name} -> {png_path.name}")
            migrated += 1

    leftover = _remaining_00_dapi_tiffs(dapi_dir)
    if leftover:
        names = ", ".join(p.name for p in leftover)
        emit_log(f"  ERROR: TIFF still in 00_dapi after migrate: {names}")
        raise RuntimeError(f"00_dapi must not contain TIFF files: {names}")
    return migrated


def repair_preview_from_zstack(
    bundle_root: Path,
    ch: dict,
    slice_id: str,
    preview_scale: float,
) -> bool:
    z_path = original_scans_path(bundle_root, ch, slice_id)
    if not z_path.is_file():
        emit_log(f"  z-stack missing for repair: {z_path.name}")
        return False
    emit_log(f"  Repair preview from z-stack {z_path.name}")
    plane = max_projection_from_tiff(z_path)
    if ch.get("role") == ROLE_DAPI:
        write_dapi_preview_pair(bundle_root, slice_id, plane, preview_scale)
        return True
    preview_path = preview_path_for_channel(bundle_root, ch, slice_id)
    if preview_path is None:
        return False
    write_preview_at_path(preview_path, plane, preview_scale, bundle_root, slice_id)
    return True


def channel_from_repair_target(cfg: dict, target: dict) -> dict | None:
    idx = int(target.get("channel_index", -1))
    role_key = target.get("role_key")
    for ch in cfg.get("channels") or []:
        if int(ch.get("index", -1)) != idx:
            continue
        if role_key and role_key_for_channel(ch) != role_key:
            continue
        return dict(ch)
    return None


def repair_target_to_work_item(cfg: dict, target: dict, lookup: dict) -> dict | None:
    ch = channel_from_repair_target(cfg, target)
    if not ch:
        return None
    file_key = target.get("file") or ch.get("file") or ""
    file_entry = resolve_file_entry(str(file_key), lookup)
    if not file_entry:
        return None
    czi_path = Path(target.get("czi_path") or file_entry.get("path") or "")
    if not czi_path.is_file():
        return None
    return {
        "czi_path": czi_path,
        "file_entry": file_entry,
        "scene_index": int(target.get("scene_index", 0)),
        "slice_id": str(target.get("slice_id") or ""),
        "channel_index": int(ch["index"]),
        "channel": ch,
        "role_key": role_key_for_channel(ch),
    }


def max_runs_on_disk(bundle_root: Path, max_runs: dict[str, str]) -> bool:
    if not max_runs:
        return False
    base = bundle_root / "data/counting/03_max"
    for rel in max_runs.values():
        if not rel or not (base / rel).is_dir():
            return False
    return True


def refresh_max_slices_in_run(
    bundle_root: Path,
    role_key: str,
    slice_ids: list[str],
    max_run_rel: str,
    cfg: dict,
) -> int:
    """Max-project selected slices into an existing run leaf."""
    branch = branch_for_role_key(role_key)
    if not branch or not max_run_rel:
        return 0
    in_dir = max_input_dir(bundle_root, role_key)
    rel = str(max_run_rel).replace("\\", "/").strip("/")
    out_dir = bundle_root / "data/counting/03_max" / rel
    if not in_dir.is_dir() or not out_dir.is_dir():
        emit_log(f"  skip max refresh {role_key}: missing input or output dir")
        return 0
    depth = bit_depth_for_role(cfg, role_key)
    refreshed = 0
    for sid in natural_sort_slice_ids(list({s for s in slice_ids if s})):
        src = in_dir / f"{sid}.tif"
        dst = out_dir / f"{sid}.tif"
        if not src.is_file():
            emit_log(f"  skip max refresh {sid}: missing {src.name}")
            continue
        emit_log(f"  max refresh {branch} <- {src.name} -> {rel}/{dst.name}")
        _t_max = time.perf_counter()
        max_project_file(src, dst, bit_depth=depth)
        _perf_acc("max", time.perf_counter() - _t_max)
        refreshed += 1
    return refreshed


def build_reextract_work(cfg: dict, repair_targets: list[dict], lookup: dict) -> list[dict]:
    work: list[dict] = []
    for target in repair_targets:
        item = repair_target_to_work_item(cfg, target, lookup)
        if item:
            work.append(item)
    return work


def import_work_item_key(item: dict) -> str:
    """Stable identity for safe interrupted-import resume matching."""
    payload = {
        "czi_path": str(item.get("czi_path") or ""),
        "scene_index": int(item.get("scene_index") or 0),
        "channel_index": int(item.get("channel_index") or 0),
        "slice_id": str(item.get("slice_id") or ""),
        "role_key": str(item.get("role_key") or ""),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def import_work_signature(cfg: dict, work: list[dict]) -> str:
    """Bind resume state to both current import settings and planned items."""
    payload = {
        "config_fingerprint": str(cfg.get("config_fingerprint") or ""),
        "items": [import_work_item_key(item) for item in work],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def import_item_outputs_complete(bundle_root: Path, item: dict) -> bool:
    """Only resume an item when every output required by its role exists."""
    ch = dict(item.get("channel") or {})
    slice_id = str(item.get("slice_id") or "")
    if not slice_id or not _nonempty_file(original_scans_path(bundle_root, ch, slice_id)):
        return False
    if ch.get("role") == ROLE_DAPI:
        return _nonempty_file(dapi_preview_path(bundle_root, slice_id)) and _nonempty_file(
            orient_dapi_preview_path(bundle_root, slice_id)
        )
    if branch_for_channel(ch):
        return _nonempty_file(signal_preview_path(bundle_root, slice_id, ch))
    return True


def stream_max_stage_path(stage_root: Path, role_key: str, slice_id: str) -> Path:
    """Private import staging location; never exposed as a completed MAX run."""
    branch = branch_for_role_key(role_key) or "default"
    return stage_root / branch / f"{slice_id}.tif"


def run_max_for_role_key(
    bundle_root: Path,
    role_key: str,
    slice_ids: list[str],
    cfg: dict,
    *,
    staged_max: dict[str, Path] | None = None,
) -> str:
    branch = branch_for_role_key(role_key)
    if not branch:
        return ""
    in_dir = max_input_dir(bundle_root, role_key)
    if not in_dir.exists():
        return ""
    stems = natural_sort_slice_ids(list({sid for sid in slice_ids}))
    slug = stems[0] if len(stems) == 1 else f"{stems[0]}-{stems[-1]}"
    out_dir = max_output_run_dir(bundle_root, role_key, slug)
    if not out_dir.exists():
        emit_log(f"  mkdir {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = natural_sort_filenames([p.name for p in in_dir.glob("*.tif")])
    emit_log(f"Max projecting {branch} ({len(files)} slices)...")
    emit_progress("Max projecting signal channels...")
    depth = bit_depth_for_role(cfg, role_key)
    staged_max = staged_max or {}
    for fname in files:
        staged = staged_max.get(Path(fname).stem)
        if staged is not None and staged.is_file():
            emit_log(f"  max <- stream cache {fname}")
            os.replace(staged, out_dir / fname)
            continue
        emit_log(f"  max <- {fname}")
        _t_max2 = time.perf_counter()
        max_project_file(in_dir / fname, out_dir / f"{Path(fname).stem}.tif", bit_depth=depth)
        _perf_acc("max", time.perf_counter() - _t_max2)
    write_run_manifest(
        out_dir,
        {
            "step": "max",
            "source": "czi_import",
            "branch": branch,
            "input_dir": str(in_dir),
            "input_files": files,
        },
    )
    rel = f"{branch}/max/{slug}"
    return rel


def _child_event(kind: str, payload: dict) -> None:
    """Private parent/child protocol; never forwarded as a UI RESULT/Done line."""
    print(f"CZI_CHILD_{kind}:{json.dumps(payload, ensure_ascii=False)}", flush=True)


def _isolated_extract_file_child(bundle_root: Path, cfg: dict, payload: dict) -> int:
    """Read one CZI file in a disposable process and report each completed item.

    A native aicspylibczi access violation terminates only this process.  The
    parent receives already-completed item events, starts a fresh child for the
    remaining items, and continues with the next file if retry also fails.
    """
    items = list(payload.get("items") or [])
    if not items:
        _child_event("FILE_DONE", {"ok": True, "items": 0})
        return 0
    preview_scale = float(payload.get("preview_scale") or 0.05)
    czi_path = Path(str(items[0].get("item", {}).get("czi_path") or ""))
    if not czi_path.is_file():
        _child_event("FILE_DONE", {"ok": False, "error": f"CZI not found: {czi_path}"})
        return 2
    czi = None
    try:
        emit_log(f"  Isolated CZI child opening {czi_path.name}")
        czi = CziFile(str(czi_path))
        blocks = normalized_dim_blocks(czi)
        dims_str = "".join(sorted({str(k).upper() for b in blocks for k in b.keys()}))
        mosaic_info = assess_mosaic_import(czi, sample_read=True, sample_scale=0.05)
        emit_log(f"  dims={dims_str or '?'} is_mosaic={bool(mosaic_info.get('is_mosaic'))}")
        for entry in items:
            item = dict(entry.get("item") or {})
            ordinal = int(entry.get("ordinal") or 0)
            total = int(entry.get("total") or len(items))
            slice_id = str(item.get("slice_id") or "")
            role_key = str(item.get("role_key") or "")
            ch = dict(item.get("channel") or {})
            stream_raw = str(entry.get("stream_max_path") or "")
            stream_max_path = Path(stream_raw) if stream_raw else None
            _item_t0 = time.perf_counter()
            _item_acc0 = dict(_PERF_ACC)
            emit_log(
                f"[{ordinal}/{total}] {slice_id} role={role_key} "
                f"file={czi_path.name} scene={item.get('scene_index')} ch={item.get('channel_index')}"
            )
            try:
                scene_index = int(item["scene_index"])
                channel_index = int(item["channel_index"])
                if ch.get("role") == ROLE_DAPI:
                    candidate_z = item.get("dapi_candidate_z")
                    if candidate_z is not None:
                        # The parent supplied exactly one candidate.  This
                        # disposable extraction child is also the probe: a
                        # successful read writes the real output immediately;
                        # a native crash returns control to the parent, which
                        # advances only the DAPI candidate and starts a fresh
                        # child.  No separate --dapi-probe process is needed.
                        emit_log(
                            "  LOG: dapi_z_selection mode=extract_candidate "
                            f"channel={channel_index} z={candidate_z}"
                        )
                        z_idxs = [int(candidate_z)]
                    else:
                        # No pre-resolved Z on the item (e.g. an older resume
                        # state, or a non-parent caller): fall back to the
                        # original in-child metadata classification.
                        z_idxs, _dapi_z_mode = select_dapi_z_indices(
                            czi, scene_index, channel_index,
                        )
                else:
                    z_idxs = z_indices_with_data(czi, scene_index, channel_index)
                out_path = original_scans_path(bundle_root, ch, slice_id)
                preview_path = None if ch.get("role") == ROLE_DAPI else signal_preview_path(bundle_root, slice_id, ch)
                extract_z_stack(
                    czi,
                    scene_index,
                    channel_index,
                    z_idxs,
                    out_path,
                    preview_path,
                    preview_scale,
                    slice_id=slice_id,
                    bundle_root=bundle_root,
                    cfg=cfg,
                    role_key=role_key,
                    stream_max_path=stream_max_path,
                )
                phases = {
                    key: _PERF_ACC.get(key, 0.0) - _item_acc0.get(key, 0.0)
                    for key in ("read", "write", "preview", "max")
                }
                _child_event(
                    "ITEM",
                    {
                        "ok": True,
                        "ordinal": ordinal,
                        "slice_id": slice_id,
                        "role_key": role_key,
                        "stream_max_staged": bool(stream_max_path and stream_max_path.is_file()),
                        "phases": phases,
                        "seconds": time.perf_counter() - _item_t0,
                    },
                )
            except Exception as exc:  # normal Python failures stay item-scoped
                if stream_max_path is not None:
                    stream_max_path.unlink(missing_ok=True)
                emit_log(f"  ERROR extracting {slice_id} role={role_key}: {exc!r}")
                _child_event(
                    "ITEM",
                    {"ok": False, "ordinal": ordinal, "slice_id": slice_id, "role_key": role_key, "error": repr(exc)},
                )
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()
    _child_event("FILE_DONE", {"ok": True, "items": len(items)})
    return 0


def _run_isolated_extract_file(bundle_root: Path, config_path: str, payload: dict) -> tuple[int, list[dict]]:
    """Launch one disposable native CZI child and relay safe logs to the UI."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        payload_path = Path(handle.name)
    events: list[dict] = []
    try:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "-b", str(bundle_root), "-j", str(config_path),
            "--isolated-file", str(payload_path),
        ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith("CZI_CHILD_ITEM:"):
                try:
                    events.append(json.loads(line.split(":", 1)[1]))
                except json.JSONDecodeError:
                    emit_log(f"  ERROR malformed isolated item event: {line[:200]}")
            elif line.startswith("CZI_CHILD_FILE_DONE:"):
                continue
            elif line:
                # Child never emits RESULT:/Done!, so relaying LOG/trace text
                # cannot complete or corrupt the outer Electron job protocol.
                print(line, flush=True)
        return int(proc.wait()), events
    finally:
        payload_path.unlink(missing_ok=True)


class _PersistentExtractWorker:
    """One native CZI child for the normal file sequence.

    The supervisor never reads CZI pixels itself.  This worker keeps Python
    imports alive between files, but a native access violation still kills only
    this child.  Its caller then uses the disposable runner for the incomplete
    file and starts a new persistent worker for later files.
    """

    def __init__(self, bundle_root: Path, config_path: str):
        self.bundle_root = bundle_root
        self.config_path = config_path
        self.proc = None

    def _start(self) -> None:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "-b", str(self.bundle_root), "-j", str(self.config_path),
            "--isolated-worker",
        ]
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        emit_log(f"  persistent CZI worker started pid={self.proc.pid}")

    def run(self, payload: dict) -> tuple[int, list[dict]]:
        if self.proc is None:
            self._start()
        assert self.proc is not None
        if self.proc.poll() is not None:
            return int(self.proc.returncode or 1), []
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        events: list[dict] = []
        job_done = False
        job_code = 1
        for raw in self.proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith("CZI_CHILD_ITEM:"):
                try:
                    events.append(json.loads(line.split(":", 1)[1]))
                except json.JSONDecodeError:
                    emit_log(f"  ERROR malformed persistent item event: {line[:200]}")
            elif line.startswith("CZI_CHILD_FILE_DONE:"):
                continue
            elif line.startswith("CZI_CHILD_JOB_DONE:"):
                try:
                    job = json.loads(line.split(":", 1)[1])
                    job_done = True
                    job_code = int(job.get("code") or (0 if job.get("ok") else 1))
                except json.JSONDecodeError:
                    emit_log(f"  ERROR malformed persistent completion event: {line[:200]}")
                break
            elif line:
                print(line, flush=True)
        if job_done:
            return job_code, events
        # EOF before JOB_DONE means a crash or a startup/worker failure.  Keep
        # events already received so output from earlier channels is retained.
        return int(self.proc.wait()), events

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _run_isolated_worker(bundle_root: Path, cfg: dict) -> int:
    """Serve sequential CZI-file payloads until the supervisor closes stdin."""
    emit_log(f"Persistent CZI worker PID {os.getpid()} ready")
    for raw in sys.stdin:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("worker payload must be an object")
            code = _isolated_extract_file_child(bundle_root, cfg, payload)
        except Exception as exc:  # normal Python job setup failures are recoverable
            emit_log(f"  ERROR persistent CZI worker job: {exc!r}")
            code = 2
        _child_event("JOB_DONE", {"ok": code == 0, "code": code})
    return 0


def main() -> int:
    global np, cv2, tiff, CziFile

    parser = argparse.ArgumentParser(description="Extract CZI into project bundle")
    parser.add_argument("-b", "--bundle", required=True, help="Project bundle root")
    parser.add_argument("-j", "--json", required=True, help="CZI import config JSON path")
    parser.add_argument("--isolated-file", help=argparse.SUPPRESS)
    parser.add_argument("--isolated-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dapi-probe", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.bundle = str(args.bundle).strip()
    args.json = str(args.json).strip()

    emit_progress_phase(0, "Starting extract worker")
    emit_log(f"Worker PID {os.getpid()} argv {sys.argv[1:]}")
    emit_log(f"Parsed bundle={args.bundle} config={args.json}")
    emit_progress_phase(5, "Arguments OK")

    emit_log("Importing numpy...")
    emit_progress_phase(10, "Loading numpy...")
    import numpy as _np

    np = _np
    emit_log("  numpy ready")
    emit_progress_phase(25, "numpy loaded")

    emit_log("Importing opencv...")
    emit_progress_phase(25, "Loading opencv...")
    import cv2 as _cv2

    cv2 = _cv2
    emit_log("  opencv ready")
    emit_progress_phase(40, "opencv loaded")

    emit_log("Importing tifffile...")
    emit_progress_phase(40, "Loading tifffile...")
    import tifffile as _tiff

    tiff = _tiff
    emit_log("  tifffile ready")
    emit_progress_phase(50, "tifffile loaded")

    try:
        CziFile = import_aicspylibczi()
    except ImportError as exc:
        emit_result({"ok": False, "error": str(exc)})
        return 1

    emit_log("All libraries loaded")
    emit_progress_phase(100, "Libraries ready")

    bundle_root = Path(args.bundle).resolve()
    try:
        cfg = load_import_config(args.json)
    except FileNotFoundError as exc:
        emit_result({"ok": False, "error": str(exc)})
        return 1
    source_dir = cfg.get("source_dir") or ""
    emit_log(f"Bundle root: {bundle_root}")
    emit_log(f"Config: {args.json}")
    emit_log(f"CZI source: {source_dir or '(from config paths)'}")

    if args.dapi_probe:
        try:
            with open(args.dapi_probe, encoding="utf-8") as handle:
                probe_payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            _child_event("DAPI_PROBE", {"ok": False, "z": -1, "error": f"invalid probe payload: {exc!r}"})
            return 2
        return _dapi_probe_child(probe_payload)

    if args.isolated_file:
        try:
            with open(args.isolated_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            _child_event("FILE_DONE", {"ok": False, "error": f"invalid child payload: {exc!r}"})
            return 2
        return _isolated_extract_file_child(bundle_root, cfg, payload)

    if args.isolated_worker:
        return _run_isolated_worker(bundle_root, cfg)

    emit_progress_phase(100, "Building work list")
    preview_scale = clamp_preview_scale(cfg.get("preview_scale"))
    repair_mode = cfg.get("repair_mode")
    repair_targets = list(cfg.get("repair_targets") or [])
    prior_state = read_import_state(bundle_root)
    max_runs_existing = dict(cfg.get("max_runs") or prior_state.get("max_runs") or {})

    if repair_mode == "previews":
        emit_log("Preview repair mode — migrating legacy TIFFs to PNG")
        migrate_low_res_tiffs(bundle_root, cfg, preview_scale)
        if not repair_targets:
            state = read_import_state(bundle_root) or {}
            state["phase"] = "complete"
            state["repair_mode"] = repair_mode
            state["preview_format_version"] = PREVIEW_FORMAT_VERSION
            write_import_state(bundle_root, state)
            emit_result(
                {
                    "ok": True,
                    "extracted": {},
                    "max_runs": dict(max_runs_existing),
                    "primary_signal_role": cfg.get("primary_signal_role") or "",
                    "repair_mode": repair_mode,
                    "repaired_previews": 0,
                    "migrate_only": True,
                }
            )
            print("Done!", flush=True)
            return 0
        emit_log(f"Preview repair ({len(repair_targets)} target(s))")
        files_lookup = build_files_lookup(cfg.get("files") or [])
        fallback_work: list[dict] = []
        repaired = 0
        state = {
            "phase": "repair",
            "started": datetime.now(timezone.utc).isoformat(),
            "total": len(repair_targets),
            "done": 0,
            "repair_mode": repair_mode,
        }
        write_import_state(bundle_root, state)
        print(len(repair_targets), flush=True)
        extracted_by_role_key: dict[str, list[str]] = {}
        for i, target in enumerate(repair_targets):
            slice_id = str(target.get("slice_id") or "")
            ch = channel_from_repair_target(cfg, target)
            if not ch or not slice_id:
                emit_log(f"[{i + 1}/{len(repair_targets)}] skip invalid repair target")
                state["done"] = i + 1
                write_import_state(bundle_root, state)
                continue
            emit_log(f"[{i + 1}/{len(repair_targets)}] repair preview {slice_id} ch {ch.get('index')}")
            emit_progress(f"Repairing preview {slice_id}")
            if repair_preview_from_zstack(bundle_root, ch, slice_id, preview_scale):
                repaired += 1
                role_key = role_key_for_channel(ch)
                extracted_by_role_key.setdefault(role_key, []).append(slice_id)
            else:
                item = repair_target_to_work_item(cfg, target, files_lookup)
                if item:
                    fallback_work.append(item)
                else:
                    emit_log(f"  could not repair or fall back for {slice_id}")
            state["done"] = i + 1
            write_import_state(bundle_root, state)
        work = fallback_work
    elif repair_mode == "reextract":
        emit_log(f"CZI re-extract mode ({len(repair_targets)} target(s))")
        if not repair_targets:
            emit_result({"ok": False, "error": "No re-extract targets"})
            return 1
        files_lookup = build_files_lookup(cfg.get("files") or [])
        work = build_reextract_work(cfg, repair_targets, files_lookup)
        if not work:
            emit_result({"ok": False, "error": "No valid re-extract work items (check CZI paths)"})
            return 1
        repaired = 0
        extracted_by_role_key = {}
        state = {
            "phase": "reextract",
            "started": datetime.now(timezone.utc).isoformat(),
            "total": len(work),
            "done": 0,
            "repair_mode": repair_mode,
        }
        write_import_state(bundle_root, state)
        print(len(work), flush=True)
    else:
        work = build_work_items(cfg)
        if not work:
            emit_result({"ok": False, "error": "No channels marked to keep"})
            return 1
        repaired = 0

    work_signature = ""
    resumed_item_keys: set[str] = set()
    if repair_mode not in ("previews", "reextract"):
        work_signature = import_work_signature(cfg, work)
        prior_fingerprint = str(prior_state.get("config_fingerprint") or "")
        prior_signature = str(prior_state.get("work_signature") or "")
        prior_completed = set(str(key) for key in (prior_state.get("completed_item_keys") or []))
        if (
            prior_state.get("phase") == "extract"
            and prior_fingerprint
            and prior_fingerprint == str(cfg.get("config_fingerprint") or "")
            and prior_signature == work_signature
        ):
            resumed_item_keys = {
                import_work_item_key(item)
                for item in work
                if import_work_item_key(item) in prior_completed
                and import_item_outputs_complete(bundle_root, item)
            }
            if resumed_item_keys:
                emit_log(f"Resuming interrupted import: {len(resumed_item_keys)} verified item(s) retained")

    if work:
        channels_kept = len(
            [c for c in cfg.get("channels") or [] if c.get("keep") and c.get("role") != ROLE_UNUSED],
        )
        files_in_work = len({str(item["czi_path"]) for item in work})
        out_dirs = collect_output_dirs(bundle_root, work)
        emit_log(f"Creating output directories ({len(out_dirs)} paths)...")
        for d in out_dirs:
            if not d.exists():
                emit_log(f"  mkdir {d}")
            d.mkdir(parents=True, exist_ok=True)
        emit_log(f"{len(work)} work items ({channels_kept} channels kept across {files_in_work} files)")
        emit_log("Beginning extraction...")
        state = {
            "phase": "extract",
            "started": datetime.now(timezone.utc).isoformat(),
            "total": len(work),
            "done": len(resumed_item_keys),
            "slice_numbering": cfg.get("slice_numbering"),
            "slice_order_count": len(cfg.get("slice_order") or []),
            "config_fingerprint": cfg.get("config_fingerprint") or "",
            "work_signature": work_signature,
            "completed_item_keys": sorted(resumed_item_keys),
        }
        write_import_state(bundle_root, state)
        print(len(work), flush=True)
    elif repair_mode != "previews" and repair_mode != "reextract":
        emit_result({"ok": False, "error": "No channels marked to keep"})
        return 1

    if repair_mode not in ("previews", "reextract"):
        extracted_by_role_key = {}
    # A per-import private cache lets MAX be reduced while the converted TIFF
    # planes are still in memory.  Nothing appears in 03_max until normal
    # import completion promotes these files into the regular MAX run.
    stream_max_root = None
    streamed_max_by_role: dict[str, dict[str, Path]] = {}
    if repair_mode not in ("previews", "reextract"):
        stream_max_root = (
            bundle_root / ".masonjar" / "stream-max"
            / f"{os.getpid()}-{time.time_ns()}"
        )
    czi_cache: dict[str, object] = {}

    mosaic_logged: set[str] = set()

    def get_czi(path: Path):
        key = str(path.resolve())
        if key not in czi_cache:
            emit_log(f"  Opening CZI {path.name} (first open for this file)")
            czi = CziFile(key)
            blocks = normalized_dim_blocks(czi)
            dim_letters = sorted({str(k).upper() for b in blocks for k in b.keys()})
            dims_str = "".join(dim_letters)
            # This parent instance only discovers DAPI Z metadata.  Pixel
            # sampling here duplicated the 5% sample read performed by the
            # extraction child after it opens the same CZI.  Keep the
            # metadata/BBox safety warning, but defer pixel I/O to that child.
            mosaic_info = assess_mosaic_import(czi, sample_read=False)
            is_mosaic = bool(mosaic_info.get("is_mosaic"))
            emit_log(f"  dims={dims_str or '?'}, is_mosaic={is_mosaic}")
            if is_mosaic and key not in mosaic_logged:
                stitch_status = str(mosaic_info.get("mosaic_stitch_status") or "unknown")
                if stitch_status == "ok":
                    emit_log("  mosaic metadata OK; sample read deferred to extraction child")
                elif stitch_status == "suspect":
                    emit_log("  mosaic metadata warning; sample read deferred to extraction child")
                    for warn in mosaic_info.get("mosaic_warnings") or []:
                        if "ZEN" in warn or "stitch" in warn.lower() or "tile" in warn.lower():
                            emit_log(f"  WARNING: {warn}")
                else:
                    emit_log("  mosaic metadata incomplete; sample read deferred to extraction child")
                    for warn in mosaic_info.get("mosaic_warnings") or []:
                        if "Could not read" in warn:
                            emit_log(f"  WARNING: {warn}")
                mosaic_logged.add(key)
            czi_cache[key] = czi
        return czi_cache[key]

    failed_items: list[str] = []
    completed = 0
    total_work = len(work or [])
    grouped_work: list[list[dict]] = []
    group_by_path: dict[str, list[dict]] = {}
    # Candidate order is resolved once per (file, scene, channel).  The first
    # candidate is read by the normal disposable extraction child; only a
    # native crash advances to another candidate in a fresh child.
    dapi_z_candidates_cache: dict[tuple[str, int, int], list[int]] = {}
    for ordinal, item in enumerate(work or [], start=1):
        key = str(item["czi_path"])
        role_key = str(item["role_key"])
        item_payload = dict(item)
        item_payload["czi_path"] = key

        if item["channel"].get("role") == ROLE_DAPI:
            scene_index = int(item["scene_index"])
            channel_index = int(item["channel_index"])
            cache_key = (key, scene_index, channel_index)
            if cache_key in dapi_z_candidates_cache:
                dapi_candidates = dapi_z_candidates_cache[cache_key]
            else:
                slice_id_for_log = str(item.get("slice_id") or "")
                try:
                    czi_for_dapi = get_czi(Path(key))
                    raw_candidates, selection_mode = select_dapi_z_indices(
                        czi_for_dapi, scene_index, channel_index,
                    )
                    dapi_candidates = (
                        raw_candidates
                        if selection_mode == "metadata_single"
                        else _dapi_z_priority_order(raw_candidates)
                    )
                    if selection_mode != "metadata_single":
                        emit_log(
                            "  LOG: dapi_z_extract_start "
                            f"channel={channel_index} order={dapi_candidates}"
                        )
                except Exception as exc:  # metadata-open failure: skip DAPI only
                    emit_log(
                        f"  ERROR resolving DAPI Z for {slice_id_for_log or key}: {exc!r}"
                    )
                    dapi_candidates = []
                dapi_z_candidates_cache[cache_key] = dapi_candidates
            if not dapi_candidates:
                slice_id = str(item.get("slice_id") or "")
                failed_items.append(f"{slice_id} (role={role_key} ch={item['channel_index']})")
                emit_log(
                    f"  ERROR DAPI Z resolution exhausted all candidates for {slice_id}; "
                    "skipping this DAPI item only, continuing with the rest of the import"
                )
                completed += 1
                state["done"] = completed
                write_import_state(bundle_root, state)
                continue
            item_payload["dapi_candidates"] = [int(z) for z in dapi_candidates]
            item_payload["dapi_candidate_index"] = 0
            item_payload["dapi_candidate_z"] = int(dapi_candidates[0])

        group = group_by_path.get(key)
        if group is None:
            group = []
            group_by_path[key] = group
            grouped_work.append(group)
        stream_max_path = None
        if stream_max_root is not None and item["channel"].get("role") != ROLE_DAPI and branch_for_role_key(role_key):
            stream_max_path = stream_max_stage_path(stream_max_root, role_key, str(item["slice_id"]))
        group.append(
            {
                "ordinal": ordinal,
                "total": total_work,
                "item": item_payload,
                "stream_max_path": str(stream_max_path or ""),
            }
        )

    # DAPI is first: if its candidate crashes, the parent can advance that
    # candidate with certainty before any signal item has begun in this child.
    for group in grouped_work:
        group.sort(key=lambda entry: 0 if entry["item"]["channel"].get("role") == ROLE_DAPI else 1)

    # The normal path reuses one child across CZI files, avoiding repeated
    # interpreter/library startup.  A non-zero result switches the *current*
    # group to the disposable runner and replaces this worker for later files.
    persistent_worker = _PersistentExtractWorker(bundle_root, args.json)
    for group in grouped_work:
        pending = list(group)
        attempts = 0
        # A signal/native failure retains the original one retry.  An
        # ambiguous DAPI may need one child per candidate, plus one final
        # child to process signals after all DAPI candidates have exhausted.
        dapi_candidate_count = max(
            (len(entry["item"].get("dapi_candidates") or []) for entry in group),
            default=0,
        )
        max_attempts = max(2, dapi_candidate_count + 1)
        while pending and attempts < max_attempts:
            attempts += 1
            used_persistent_worker = attempts == 1
            if used_persistent_worker:
                emit_log(f"  persistent CZI worker processing {len(pending)} item(s)")
                exit_code, events = persistent_worker.run(
                    {"items": pending, "preview_scale": preview_scale},
                )
            else:
                emit_log(f"  isolated fallback retry ({len(pending)} remaining item(s))")
                exit_code, events = _run_isolated_extract_file(
                    bundle_root,
                    args.json,
                    {"items": pending, "preview_scale": preview_scale},
                )
            by_ordinal = {int(entry["ordinal"]): entry for entry in pending}
            handled: set[int] = set()
            for event in events:
                ordinal = int(event.get("ordinal") or 0)
                entry = by_ordinal.get(ordinal)
                if entry is None or ordinal in handled:
                    continue
                handled.add(ordinal)
                item = entry["item"]
                role_key = str(item["role_key"])
                slice_id = str(item["slice_id"])
                channel_index = int(item["channel_index"])
                completed += 1
                state["done"] = completed
                if event.get("ok"):
                    extracted_by_role_key.setdefault(role_key, []).append(slice_id)
                    staged = Path(str(entry.get("stream_max_path") or ""))
                    if event.get("stream_max_staged") and staged.is_file():
                        streamed_max_by_role.setdefault(role_key, {})[slice_id] = staged
                    for key, elapsed in (event.get("phases") or {}).items():
                        if key in ("read", "write", "preview", "max") and float(elapsed) > 0:
                            _perf_acc(key, float(elapsed))
                    phase_text = " ".join(
                        f"{key} {float(value):.1f}"
                        for key, value in (event.get("phases") or {}).items()
                        if float(value) > 0
                    )
                    emit_log(
                        f"  [{ordinal}/{total_work}] {slice_id} {role_key} done in "
                        f"{float(event.get('seconds') or 0):.1f}s"
                        + (f" ({phase_text})" if phase_text else "")
                        + f" [{datetime.now().strftime('%H:%M:%S')}]"
                    )
                else:
                    failed_items.append(f"{slice_id} (role={role_key} ch={channel_index})")
                    emit_log(f"  ERROR isolated item {slice_id}: {event.get('error') or 'unknown error'}")
                write_import_state(bundle_root, state)
                if completed % _PERF_PROGRESS_EVERY == 0 and completed < total_work:
                    _perf_flush_progress(completed, total_work)
            pending = [entry for entry in pending if int(entry["ordinal"]) not in handled]
            if pending and exit_code != 0:
                if used_persistent_worker:
                    emit_log(
                        f"  WARNING persistent CZI worker exited code={exit_code}; "
                        "completed items retained; using isolated fallback"
                    )
                    persistent_worker.close()
                    persistent_worker = _PersistentExtractWorker(bundle_root, args.json)
                else:
                    emit_log(f"  WARNING isolated fallback child exited code={exit_code}; completed items retained")
                # DAPI is ordered first in the child.  If it did not emit an
                # item event, the native crash occurred while trying its
                # current candidate.  Advance only that candidate; completed
                # signals are retained and never re-read.
                dapi_pending = next(
                    (
                        entry for entry in pending
                        if entry["item"]["channel"].get("role") == ROLE_DAPI
                    ),
                    None,
                )
                if dapi_pending is not None:
                    dapi_item = dapi_pending["item"]
                    candidates = [int(z) for z in (dapi_item.get("dapi_candidates") or [])]
                    current = int(dapi_item.get("dapi_candidate_index") or 0)
                    next_index = current + 1
                    if next_index < len(candidates):
                        dapi_item["dapi_candidate_index"] = next_index
                        dapi_item["dapi_candidate_z"] = candidates[next_index]
                        emit_log(
                            "  LOG: dapi_z_extract_candidate_failed "
                            f"channel={dapi_item['channel_index']} z={candidates[current]}; "
                            f"trying z={candidates[next_index]}"
                        )
                    else:
                        pending.remove(dapi_pending)
                        slice_id = str(dapi_item.get("slice_id") or "")
                        failed_items.append(
                            f"{slice_id} (role={dapi_item['role_key']} ch={dapi_item['channel_index']})"
                        )
                        completed += 1
                        state["done"] = completed
                        write_import_state(bundle_root, state)
                        emit_log(
                            "  ERROR DAPI Z extraction exhausted all candidates for "
                            f"{slice_id}; skipping this DAPI item only, continuing with signals"
                        )
            elif pending:
                emit_log("  ERROR isolated CZI child ended without item result")
        for entry in pending:
            item = entry["item"]
            failed_items.append(
                f"{item['slice_id']} (role={item['role_key']} ch={item['channel_index']})"
            )
            completed += 1
            state["done"] = completed
            write_import_state(bundle_root, state)
            emit_log(f"  ERROR extraction exhausted isolated retry for {item['slice_id']}")

    persistent_worker.close()

    if failed_items:
        emit_log(
            f"WARNING: {len(failed_items)} extraction item(s) failed and were "
            f"skipped: {', '.join(failed_items[:20])}"
            + (" …" if len(failed_items) > 20 else "")
        )

    for czi in czi_cache.values():
        close = getattr(czi, "close", None)
        if callable(close):
            close()
    czi_cache.clear()

    max_runs: dict[str, str] = dict(max_runs_existing)
    primary_role = cfg.get("primary_signal_role") or prior_state.get("primary_signal_role") or ""
    skip_max = repair_mode == "previews" and max_runs_on_disk(bundle_root, max_runs)
    if skip_max:
        emit_log("Skipping max projection (existing max runs on disk)")
    elif repair_mode == "reextract":
        for role_key, slice_ids in extracted_by_role_key.items():
            if role_key in (ROLE_DAPI, ROLE_UNUSED):
                continue
            if not branch_for_role_key(role_key):
                continue
            rel = str(max_runs.get(role_key) or "").strip()
            if not rel:
                emit_log(f"  skip max refresh {role_key}: no max run registered")
                continue
            n = refresh_max_slices_in_run(bundle_root, role_key, slice_ids, rel, cfg)
            if n:
                emit_log(f"  refreshed {n} max TIFF(s) for {role_key} in {rel}")
    else:
        for role_key, slice_ids in extracted_by_role_key.items():
            if role_key in (ROLE_DAPI, ROLE_UNUSED):
                continue
            if not branch_for_role_key(role_key):
                continue
            rel = run_max_for_role_key(
                bundle_root,
                role_key,
                slice_ids,
                cfg,
                staged_max=streamed_max_by_role.get(role_key),
            )
            if rel:
                max_runs[role_key] = rel
                branch = branch_for_role_key(role_key) or role_key
                emit_log(f"max projection {branch} -> {rel}")
                if not primary_role:
                    primary_role = role_key

    if stream_max_root is not None:
        # Any files left here belong to failed or no-longer-needed items. They
        # were never published to 03_max, so cleanup cannot alter user output.
        shutil.rmtree(stream_max_root, ignore_errors=True)

    migrated_tiffs = migrate_low_res_tiffs(bundle_root, cfg, preview_scale)
    if migrated_tiffs:
        emit_log(f"Migrated {migrated_tiffs} low-res TIFF artifact(s) to PNG")

    state = read_import_state(bundle_root) or {}
    state["phase"] = "complete"
    state["max_runs"] = max_runs
    state["preview_format_version"] = PREVIEW_FORMAT_VERSION
    state["config_fingerprint"] = cfg.get("config_fingerprint") or ""
    if repair_mode:
        state["repair_mode"] = repair_mode
    write_import_state(bundle_root, state)

    emit_result(
        {
            "ok": True,
            "extracted": extracted_by_role_key,
            "max_runs": max_runs,
            "primary_signal_role": primary_role,
            "slice_numbering": cfg.get("slice_numbering"),
            "slice_order_count": len(cfg.get("slice_order") or []),
            "preview_format_version": PREVIEW_FORMAT_VERSION,
            "config_fingerprint": cfg.get("config_fingerprint") or "",
            "repair_mode": repair_mode,
            "repaired_previews": repaired if repair_mode == "previews" else None,
        }
    )
    print("Done!", flush=True)
    _perf_flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

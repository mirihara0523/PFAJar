"""Probe Zeiss CZI files for dimensions, scenes, and channel metadata."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from czi_common import (
    assess_mosaic_import,
    channel_indices_from_czi,
    default_slice_id,
    emit_log,
    emit_progress,
    emit_result,
    natural_sort_czi_paths,
    normalized_dim_blocks,
    probe_channels_read,
    scene_indices_from_czi,
    suggest_role_from_label,
    z_indices_from_czi,
)

# How often (seconds) to emit a "still probing" heartbeat while a single CZI is
# being read. Large mosaics (tens of GB) over a busy NAS can take minutes to
# open/scan; without a heartbeat the wizard log sits on "Probing X.czi" and is
# indistinguishable from a hang. The heartbeat does no extra I/O.
PROBE_HEARTBEAT_INTERVAL_S = 4.0


def _probe_file_with_heartbeat(czi_path: Path) -> dict:
    """Run probe_file while emitting a periodic heartbeat for liveness.

    The heartbeat thread only emits LOG lines (mapped to the wizard probe log /
    Application log by the main process); it never touches the CZI or the NAS,
    so it adds no bandwidth on a contended link.
    """
    done = threading.Event()
    start = time.monotonic()

    def _beat() -> None:
        while not done.wait(PROBE_HEARTBEAT_INTERVAL_S):
            elapsed = int(time.monotonic() - start)
            emit_log(f"  still probing {czi_path.name} ({elapsed}s elapsed)…")

    beat_thread = threading.Thread(target=_beat, daemon=True)
    beat_thread.start()
    try:
        return probe_file(czi_path)
    finally:
        done.set()
        beat_thread.join(timeout=1.0)


def probe_file(path: Path) -> dict:
    try:
        from aicspylibczi import CziFile
    except ImportError as exc:
        raise RuntimeError(
            "aicspylibczi is not installed in the Mason Jar Python environment"
        ) from exc

    t0 = time.monotonic()
    czi = CziFile(str(path))
    t_open = time.monotonic() - t0

    blocks = normalized_dim_blocks(czi)
    dim_letters = sorted({str(k).upper() for b in blocks for k in b.keys()})
    dims = "".join(dim_letters)

    t1 = time.monotonic()
    mosaic_info = assess_mosaic_import(czi, sample_read=False)
    t_assess = time.monotonic() - t1

    is_mosaic = bool(mosaic_info.get("is_mosaic"))
    has_m_dim = bool(mosaic_info.get("has_m_dim"))
    scene_indices = scene_indices_from_czi(czi)
    if len(blocks) > 1 and len(scene_indices) == 1:
        emit_log(
            f"  {path.name}: {len(blocks)} dim blocks collapsed to 1 scene "
            f"(S={scene_indices[0]})",
        )
    channel_indices = channel_indices_from_czi(czi)
    z_count = len(z_indices_from_czi(czi))

    channel_meta = []
    for cidx in channel_indices:
        label = ""
        channel_meta.append(
            {
                "index": cidx,
                "label": label,
                "suggested_role": suggest_role_from_label(label),
            }
        )

    scene = scene_indices[0] if scene_indices else 0
    t2 = time.monotonic()
    channel_pixel_probe, read_warnings = probe_channels_read(czi, scene)
    t_channels = time.monotonic() - t2

    emit_log(
        f"  {path.name}: open={t_open:.1f}s assess={t_assess:.1f}s "
        f"channels={t_channels:.1f}s"
    )

    for entry in channel_pixel_probe:
        cidx = int(entry.get("index", 0))
        for ch in channel_meta:
            if int(ch.get("index", -1)) == cidx:
                ch["z_with_data"] = entry.get("z_with_data") or []
                ch["z_count"] = entry.get("z_count")
                ch["sparse_z"] = bool(entry.get("sparse_z"))
                ch["read_ok"] = bool(entry.get("ok"))
                break

    basename = path.name
    scenes = []
    for sidx in scene_indices:
        sid = default_slice_id(basename, sidx, len(scene_indices))
        scenes.append(
            {
                "index": sidx,
                "sliceId": sid,
                "originalSliceId": sid,
            }
        )

    return {
        "path": str(path),
        "basename": basename,
        "dims": dims,
        "is_mosaic": is_mosaic,
        "has_m_dim": has_m_dim,
        "m_tile_count": mosaic_info.get("m_tile_count"),
        "likely_unstitched": bool(mosaic_info.get("likely_unstitched")),
        "mosaic_stitch_status": mosaic_info.get("mosaic_stitch_status", "unknown"),
        "mosaic_warnings": list(mosaic_info.get("mosaic_warnings") or []),
        "read_warnings": read_warnings,
        "channel_pixel_probe": channel_pixel_probe,
        "scene_count": len(scene_indices),
        "channel_count": len(channel_indices),
        "z_count": z_count,
        "scenes": scenes,
        "channels": channel_meta,
    }


def collect_czi_paths(target: str) -> list[Path]:
    p = Path(target)
    if p.is_file() and p.suffix.lower() == ".czi":
        return [p]
    if not p.is_dir():
        return []
    paths = list(p.glob("*.czi")) + list(p.glob("*.CZI"))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return natural_sort_czi_paths(unique)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe CZI files")
    parser.add_argument("-i", "--input", required=True, help="CZI file or directory")
    args = parser.parse_args()
    args.input = str(args.input).strip()

    paths = collect_czi_paths(args.input)
    if not paths:
        emit_result({"ok": False, "error": "No .czi files found", "files": []})
        return 1

    print(len(paths), flush=True)
    results = []
    for i, czi_path in enumerate(paths):
        emit_progress(f"Probing {czi_path.name}")
        try:
            results.append(_probe_file_with_heartbeat(czi_path))
        except Exception as exc:
            results.append(
                {
                    "path": str(czi_path),
                    "basename": czi_path.name,
                    "error": str(exc),
                }
            )
        pct = int(((i + 1) / len(paths)) * 100)
        emit_progress(f"{pct}% {czi_path.name}")

    emit_result({"ok": True, "files": results})
    print("Done!", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

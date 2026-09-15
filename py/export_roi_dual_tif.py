"""
Export Bell Jar ROI PKLs (with dapi_roi) to two-channel ImageJ TIFs.

Channel order: C1 = DAPI (counterstain), C2 = signal (isolate-regions intensity).
Derived from bbox rasterization logic similar to RSAT image_only export.
"""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import tifffile


def _sparse_dict_int_vertex_keys(d: dict) -> dict:
    """Normalize (y,x) keys to (int, int) so roi and dapi_roi align after pickle."""
    out = {}
    for k, v in d.items():
        if not isinstance(k, tuple) or len(k) != 2:
            raise ValueError(f"expected vertex keys as length-2 tuples, got {k!r}")
        out[(int(k[0]), int(k[1]))] = v
    return out


def _sparse_roi_to_crops(
    roi: dict,
    dapi_roi: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """Build dense DAPI and signal crops from parallel sparse dicts (keys (y, x))."""
    verts = list(roi.keys())
    if not verts:
        raise ValueError("empty roi")
    min_y = min(v[0] for v in verts)
    max_y = max(v[0] for v in verts)
    min_x = min(v[1] for v in verts)
    max_x = max(v[1] for v in verts)
    h = max_y - min_y + 1
    w = max_x - min_x + 1
    dapi_crop = np.zeros((h, w), dtype=np.uint8)
    signal_crop = np.zeros((h, w), dtype=np.uint8)
    for vert in verts:
        y, x = vert[0] - min_y, vert[1] - min_x
        signal_crop[y, x] = int(np.clip(roi[vert], 0, 255))
        if vert not in dapi_roi:
            raise KeyError(f"missing dapi key for vertex {vert}")
        dapi_crop[y, x] = int(np.clip(dapi_roi[vert], 0, 255))
    return dapi_crop, signal_crop


def export_pkl_to_tif(pkl_path: Path, out_dir: Path) -> Path:
    with open(pkl_path, "rb") as f:
        package = pickle.load(f)
    roi = package.get("roi")
    name = package.get("name")
    dapi_roi = package.get("dapi_roi")
    if roi is None or name is None:
        raise ValueError(f"invalid package in {pkl_path}")
    if dapi_roi is None:
        raise ValueError(
            f"{pkl_path.name} has no dapi_roi. Re-run Isolate Regions with a DAPI folder "
            f"chosen (and a matching stem.png per slice); PKLs without DAPI cannot export here."
        )

    roi = _sparse_dict_int_vertex_keys(roi)
    dapi_roi = _sparse_dict_int_vertex_keys(dapi_roi)

    dapi_crop, signal_crop = _sparse_roi_to_crops(roi, dapi_roi)
    # ImageJ hyperstack: TZCYX (single T, Z; two channels)
    volume = np.stack([dapi_crop, signal_crop], axis=0).astype(np.uint8)
    volume_ij = volume[np.newaxis, np.newaxis, ...]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pkl_path.stem
    out_path = out_dir / f"{stem}_dual.tif"
    tifffile.imwrite(
        out_path,
        volume_ij,
        imagej=True,
    )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export ROI PKLs with dapi_roi to two-channel ImageJ TIFs (DAPI, signal)."
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        required=True,
        help="Directory containing *_*.pkl ROI packages from Isolate Regions",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        required=True,
        help="Directory to write *_dual.tif files",
    )
    args = parser.parse_args()
    in_dir = Path(args.input_dir.strip())
    out_dir = Path(args.output_dir.strip())
    if not in_dir.is_dir():
        print(f"Input directory not found: {in_dir}", file=sys.stderr, flush=True)
        print("Done!", flush=True)
        return 1

    pkls = sorted(in_dir.glob("*.pkl"))
    if not pkls:
        print(f"No .pkl files in {in_dir}", file=sys.stderr, flush=True)
        print("Done!", flush=True)
        return 1

    print(1 + len(pkls), flush=True)
    print("Setting up...", flush=True)
    ok = 0
    for j, pkl in enumerate(pkls):
        print(f"Exporting {pkl.name}", flush=True)
        try:
            export_pkl_to_tif(pkl, out_dir)
            ok += 1
        except Exception as e:
            # stderr is shown on the Log page; stdout only updates the thin progress line.
            print(f"Skip {pkl.name}: {e}", file=sys.stderr, flush=True)
            print(f"Skip {pkl.name}: {e}", flush=True)
    print("Done!", flush=True)
    from run_manifest import write_run_manifest

    write_run_manifest(
        out_dir,
        {
            "step": "dual",
            "input_dir": str(in_dir),
            "output_dir": str(out_dir),
            "input_files": [p.name for p in pkls],
            "exported": ok,
        },
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

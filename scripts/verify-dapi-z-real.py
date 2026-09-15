"""Real-CZI, crash-safe verification for the DAPI Z candidate resolver.

Ad-hoc, read-only diagnostic — never writes to the project bundle or CZI
source. Run with Mason Jar's runtime Python (the same interpreter the shared
worker uses, typically ``C:\\Users\\<you>\\.masonjar\\python\\python.exe``)
so the installed aicspylibczi/numpy/opencv/tifffile match production, and so
any disposable probe child this spawns (via ``sys.executable``) behaves
exactly as it would during a real import.

Exercises ``czi_extract.resolve_dapi_z()`` end-to-end against one real CZI
scene/channel:
  1. metadata classification (``select_dapi_z_indices`` — unchanged code);
  2. for ambiguous metadata, real per-candidate disposable-child probes, each
     a fresh process reading one full-resolution plane.

A native aicspylibczi crash on a bad candidate is expected to be caught and
logged inside its own disposable child, then the next candidate tried — not
to abort this script. All-candidates-failed is reported, not raised.

Usage:
    python verify-dapi-z-real.py <path\\to\\file.czi> --scene 0 --channel 2
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
import czi_extract as c  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("czi", type=Path, help="Path to a real .czi file")
    parser.add_argument("--scene", type=int, default=0)
    parser.add_argument(
        "--channel", type=int, required=True,
        help="DAPI channel index (C=2 confirmed for M581-01(1) in this dataset)",
    )
    args = parser.parse_args()
    if not args.czi.is_file():
        parser.error(f"CZI not found: {args.czi}")

    import numpy as np
    import cv2
    import tifffile as tiff
    from aicspylibczi import CziFile

    # Mirror czi_extract.main()'s staged-import bootstrap so module-level
    # helpers (read_plane, select_dapi_z_indices, resolve_dapi_z, and the
    # disposable-child probes they spawn) see the real runtime libraries.
    c.np, c.cv2, c.tiff, c.CziFile = np, cv2, tiff, CziFile

    czi = CziFile(str(args.czi))
    try:
        raw_candidates, mode = c.select_dapi_z_indices(czi, args.scene, args.channel)
        print(f"metadata classification: mode={mode} candidates={raw_candidates}")

        with tempfile.TemporaryDirectory() as tmp:
            bundle_root = Path(tmp) / "bundle"
            bundle_root.mkdir(parents=True, exist_ok=True)
            config_path = Path(tmp) / "config.json"
            config_path.write_text("{}", encoding="utf-8")

            print("resolving true DAPI plane (this may spawn disposable probe children)...")
            resolved_z, resolve_mode = c.resolve_dapi_z(
                bundle_root, str(config_path), czi, args.czi, args.scene, args.channel,
            )
    finally:
        close = getattr(czi, "close", None)
        if callable(close):
            close()

    print(f"resolved: z={resolved_z} mode={resolve_mode}")
    if resolved_z is None:
        print("RESULT: FAIL — every candidate crashed or read empty; this DAPI would be skipped, import would continue")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

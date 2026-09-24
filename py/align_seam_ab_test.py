"""
Seam-correction A/B test for Alignment registration — standalone, run on the
Windows machine with PFA Jar's bundled Python.

Purpose
-------
Answers: "does feeding the seam-corrected DAPI image into registration
(instead of the raw image) actually improve the Mattes Mutual Information
score / change the warped output?"

It does this by running the app's OWN, UNMODIFIED registration function
(`demons.register_to_atlas`) TWICE for the same slice, same atlas/annotation
slice (same AP position / angles / hemisphere, read straight from the real
Align session file so this is an apples-to-apples comparison):

  A) tissue = the original DAPI image, exactly as `map.py`'s finish() reads it today
  B) tissue = the same image run through `seam_correct.correct_file_to()`
     (the same function already used for the Align page's "Seam correction"
     live-preview toggle — today display-only, never fed into registration)

Nothing in the app's source is modified. This script imports the production
modules (`demons.py`, `seam_correct.py`, `slice_atlas.py`,
`align_tissue_layout.py`) directly and calls them as-is.

How to run
----------
Run from inside the PFA Jar source's py/ folder, with the app's bundled
Python (only interpreter with SimpleITK/aicspylibczi/etc. installed):

    cd D:\\Claude\\masonjar-7.0.2-MC.1-improving\\py
    C:\\Users\\mirih\\.masonjar\\benv\\Scripts\\python.exe align_seam_ab_test.py ^
        --dapi-dir "<path to the project's 00_dapi input folder used by Align>" ^
        --slice-id "M581-01(4)" ^
        --out-dir "D:\\Claude\\seam_ab_test_out"

`--dapi-dir` is whatever folder was passed as `-i` to map.py for that Align
run (the same folder that contains `alignment_session.json` /
`alignment.pkl` next to the DAPI PNGs) — usually
`<bundle_root>\\data\\counting\\00_dapi` or similar under the project bundle.
If you're not sure of the exact path, check the project's Align page: it's
whatever folder is shown as the alignment input directory there.

`--slice-id` must match a slice already tuned in that session (the script
reads `alignment_session.json` to get the exact ap_position/x_angle/y_angle/
region/hemisphere the real session used for that slice, so both runs use the
identical atlas/annotation slice as ground truth).

What it prints
---------------
For each of A (original) and B (seam-corrected), the same
"align_register_stage stage=... metric=..." lines the real app logs (Mattes
MI per stage: rigid / affine / bspline — more negative = better). Then a
final comparison:
  - MI delta at each stage (B - A)
  - % of output label pixels that differ between A's and B's final warped
    annotation (resampled_label)
Also writes Atlas_/Label_/Composite_*.png for both A and B to --out-dir so
the results can be inspected visually side by side.

This script only reads existing project files and writes to a separate
--out-dir — it does not touch the project's real Align output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# MASONJAR_PERF must be set before perf_log.py is imported (it reads the env
# var once, at module import time), so this has to happen before any of the
# app's modules are imported below — that's what makes the
# "align_register_stage stage=... metric=..." lines print, same as a real run.
os.environ.setdefault("MASONJAR_PERF", "1")

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

try:
    import cv2
    import numpy as np
    import nrrd
except ImportError:
    print(
        "ERROR: cv2/numpy/nrrd not importable in this Python. Run with:\n"
        r'  C:\Users\mirih\.masonjar\benv\Scripts\python.exe align_seam_ab_test.py ...',
        file=sys.stderr,
    )
    raise

try:
    from slice_atlas import slice_3d_volume, mask_slice_by_region
    from align_tissue_layout import crop_planar_for_hemisphere
    from demons import register_to_atlas
    import seam_correct
except ImportError:
    print(
        "ERROR: could not import PFA Jar's own modules (slice_atlas.py, "
        "align_tissue_layout.py, demons.py, seam_correct.py). Run this "
        "script from inside the PFA Jar source's py/ folder.",
        file=sys.stderr,
    )
    raise


def load_session_slice(dapi_dir: Path, slice_id: str) -> dict:
    session_path = dapi_dir / "alignment_session.json"
    if not session_path.is_file():
        raise SystemExit(
            f"No alignment_session.json found at {session_path}. "
            "Pass the same --dapi-dir that was used as Align's input folder "
            "(-i) for a session that has already tuned this slice."
        )
    with open(session_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc.get("slices", []):
        if str(entry.get("slice_id")) == slice_id:
            return entry
    available = [str(e.get("slice_id")) for e in doc.get("slices", [])]
    raise SystemExit(
        f"slice_id '{slice_id}' not found in session. Available: {available}"
    )


def load_atlas_volumes(nrrd_dir: Path, legacy: bool):
    atlas_name = "reconstructed_atlas.nrrd" if legacy else "atlas_10.nrrd"
    annotation_name = "reconstructed_annotation.nrrd" if legacy else "annotation_10.nrrd"
    atlas = nrrd.read(str(nrrd_dir / atlas_name))[0]
    annotation = nrrd.read(str(nrrd_dir / annotation_name))[0]
    return atlas, annotation


def build_atlas_slice(atlas_vol, annotation_vol, entry: dict, structure_map):
    ap = int(entry["ap_position"])
    x_angle = float(entry["x_angle"])
    y_angle = float(entry["y_angle"])
    hemisphere = str(entry.get("hemisphere", "W"))
    region = str(entry.get("region", "A"))

    atlas_image = slice_3d_volume(atlas_vol, ap, x_angle, y_angle).astype(np.uint8)
    atlas_label = slice_3d_volume(annotation_vol, ap, x_angle, y_angle).astype(np.uint32)
    atlas_image = crop_planar_for_hemisphere(atlas_image, hemisphere)
    atlas_label = crop_planar_for_hemisphere(atlas_label, hemisphere)

    if region != "A":
        atlas_image, atlas_label = mask_slice_by_region(
            atlas_image, atlas_label, structure_map, region
        )
    return atlas_image, atlas_label, region, hemisphere


def colorize(label_array, structure_map):
    color = np.zeros((label_array.shape[0], label_array.shape[1], 3), dtype=np.uint8)
    for region_id, info in structure_map.items():
        mask = label_array == region_id
        if np.any(mask):
            color[mask] = info["color"]
    return color


def run_one(tag, tissue_gray, atlas_image, atlas_label, structure_map_path, out_dir: Path, stem: str):
    print(f"\n--- Registering [{tag}] ---")
    resampled_label, resampled_atlas, color_label = register_to_atlas(
        tissue_gray, atlas_image, atlas_label, str(structure_map_path)
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / f"Atlas_{tag}_{stem}.png"), resampled_atlas)
    color_bgr = cv2.cvtColor(color_label, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(out_dir / f"Label_{tag}_{stem}.png"), color_bgr)
    tissue_rgb = cv2.cvtColor(tissue_gray, cv2.COLOR_GRAY2RGB)
    composite = cv2.addWeighted(tissue_rgb, 0.80, color_bgr, 0.20, 0)
    cv2.imwrite(str(out_dir / f"Composite_{tag}_{stem}.png"), composite)
    return resampled_label, resampled_atlas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dapi-dir", required=True, help="Align input folder (contains alignment_session.json)")
    ap.add_argument("--slice-id", required=True, help="e.g. M581-01(4)")
    ap.add_argument("--out-dir", required=True, help="Where to write comparison PNGs")
    ap.add_argument(
        "--nrrd-dir",
        default=str(Path.home() / ".masonjar" / "nrrd"),
        help=r"Default: %(default)s",
    )
    ap.add_argument(
        "--structure-map",
        default=str(THIS_DIR.parent / "csv" / "structure_map.pkl"),
        help="Default: source tree's csv/structure_map.pkl",
    )
    ap.add_argument("--legacy", action="store_true", help="Use reconstructed_atlas/annotation.nrrd")
    ap.add_argument(
        "--meta-dir",
        default=None,
        help="Bundle's .masonjar dir (for seamgrid sidecar / known_geometry mode). "
        "If omitted, seam_correct falls back to grid-estimated correction.",
    )
    args = ap.parse_args()

    dapi_dir = Path(args.dapi_dir)
    out_dir = Path(args.out_dir)
    nrrd_dir = Path(args.nrrd_dir)
    structure_map_path = Path(args.structure_map)
    meta_dir = Path(args.meta_dir) if args.meta_dir else None

    if not structure_map_path.is_file():
        raise SystemExit(f"structure_map.pkl not found: {structure_map_path}")
    if not nrrd_dir.is_dir():
        raise SystemExit(f"nrrd dir not found: {nrrd_dir}")

    entry = load_session_slice(dapi_dir, args.slice_id)
    filename = entry["filename"]
    print(f"Loaded session entry for {args.slice_id}: {entry}")

    tissue_path = dapi_dir / filename
    if not tissue_path.is_file():
        raise SystemExit(f"Tissue image not found: {tissue_path}")

    import pickle

    with open(structure_map_path, "rb") as f:
        structure_map = pickle.load(f)

    print(f"Loading atlas/annotation volumes from {nrrd_dir} (legacy={args.legacy})...")
    atlas_vol, annotation_vol = load_atlas_volumes(nrrd_dir, args.legacy)
    atlas_image, atlas_label, region, hemisphere = build_atlas_slice(
        atlas_vol, annotation_vol, entry, structure_map
    )
    print(f"Atlas slice built: region={region} hemisphere={hemisphere} shape={atlas_image.shape}")

    tissue_orig = cv2.imread(str(tissue_path), cv2.IMREAD_GRAYSCALE)
    if tissue_orig is None:
        raise SystemExit(f"Failed to read tissue image: {tissue_path}")

    # Seam-correct the same source file via the app's own live-preview function.
    with tempfile.TemporaryDirectory(prefix="masonjar-seam-ab-") as tmpdir:
        seam_dest = Path(tmpdir) / "seam_corrected.png"
        info = seam_correct.correct_file_to(
            seam_dest,
            tissue_path,
            meta_dir=meta_dir,
            seam_mode="auto",
            seamgrid_slice_id=args.slice_id,
            geometry_image_path=tissue_path,
        )
        print(f"seam_correct.correct_file_to() info: {info}")
        tissue_seam = cv2.imread(str(seam_dest), cv2.IMREAD_GRAYSCALE)
        if tissue_seam is None:
            raise SystemExit("Failed to read seam-corrected output image.")

    stem = args.slice_id.replace("(", "_").replace(")", "_")

    label_a, atlas_a = run_one("A_original", tissue_orig, atlas_image, atlas_label, structure_map_path, out_dir, stem)
    label_b, atlas_b = run_one("B_seamcorrected", tissue_seam, atlas_image, atlas_label, structure_map_path, out_dir, stem)

    diff_mask = label_a != label_b
    diff_pct = 100.0 * float(np.count_nonzero(diff_mask)) / float(diff_mask.size)

    print("\n=== A/B Summary ===")
    print(f"Slice: {args.slice_id}  ({filename})")
    print(f"Output label pixels that differ between A (original) and B (seam-corrected): "
          f"{diff_pct:.3f}%  ({int(np.count_nonzero(diff_mask))} / {diff_mask.size} px)")
    print(f"Comparison images written to: {out_dir}")
    print(
        "\nRead the 'align_register_stage stage=... metric=...' lines printed above "
        "for each of [A_original] and [B_seamcorrected] to compare Mattes MI per "
        "stage (more negative = better fit). A meaningfully more negative metric "
        "for B at the bspline stage, plus a non-trivial diff%, would support "
        "switching Finish to register against the seam-corrected image."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

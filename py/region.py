from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import os
import pickle
import argparse
import sys
from pathlib import Path

from slice_index import load_slice_list, slice_id_allowed
from intensity_flags import parse_whole_flag
from region_config import (
    build_output_targets,
    load_intensity_config,
)
from annotation_match import (
    atlas_ids_matching_target,
    include_layers_allowed,
    load_parcellation_context,
    resolve_output_targets,
)
from align_layout_manifest import intensity_whole_for_slice, load_align_slice_layouts
from structure_catalog import load_catalog

LEGACY_VIS_RSP_ACRONYMS = [
    "VISa",
    "VISal",
    "VISam",
    "VISp",
    "VISl",
    "VISli",
    "VISpl",
    "VISpm",
    "VISpor",
    "VISrl",
    "RSPagl",
    "RSPd",
    "RSPv",
]
import tifffile
import numpy as np
from demons import resize_image_nearest_neighbor
import cv2


def reconstruct_region(intensity_data):
    """
    Reconstruct a region from its intensity data.

    Args:
        intensity_data (dict): Dictionary of intensity data. Keys are points, values are pixel intensity.

    Returns:
        numpy.ndarray: 2D numpy array of reconstructed region.
    """

    # Get the max x and y values
    max_x = max([point[0] for point in intensity_data.keys()])
    max_y = max([point[1] for point in intensity_data.keys()])
    # Create a blank image
    blank = np.zeros((max_x + 1, max_y + 1))
    # Fill in the image
    for point, intensity in intensity_data.items():
        blank[point] = intensity

    return blank


_DAPI_NAME_CANDIDATES = (
    "{stem}.png",
    "{stem}.PNG",
    "{stem}.tif",
    "{stem}.TIF",
    "{stem}.tiff",
    "{stem}.TIFF",
    "{stem}.jpg",
    "{stem}.JPG",
    "{stem}.jpeg",
    "{stem}.JPEG",
    "{stem}_dapi.png",
    "{stem}_dapi.PNG",
    "{stem}_dapi.tif",
    "{stem}_dapi.TIFF",
)


def _resolve_dapi_image_path(dapi_dir: Path, stem: str) -> Path | None:
    for pat in _DAPI_NAME_CANDIDATES:
        p = dapi_dir / pat.format(stem=stem)
        if p.is_file():
            return p
    return None


def _to_uint8_gray(img: np.ndarray) -> np.ndarray:
    """Normalize single-channel image to uint8 for sparse ROI storage."""
    arr = np.asarray(img)
    if arr.ndim > 2:
        arr = arr[..., 0]
    if arr.dtype == np.uint8:
        return arr
    arr = arr.astype(np.float64)
    mn, mx = float(np.min(arr)), float(np.max(arr))
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint8)
    arr = (arr - mn) / (mx - mn) * 255.0
    return np.clip(arr, 0, 255).astype(np.uint8)


def _load_dapi_grayscale(path: Path) -> np.ndarray | None:
    """Load 2D grayscale DAPI; supports PNG/JPEG via OpenCV and TIFF via tifffile."""
    suf = path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
        bgr = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if bgr is not None:
            return _to_uint8_gray(bgr)
    try:
        raw = tifffile.imread(str(path))
    except Exception:
        return None
    if raw is None:
        return None
    if raw.ndim == 3:
        raw = raw[..., 0]
    if raw.ndim != 2:
        return None
    return _to_uint8_gray(raw)


def _is_candidate_intensity_filename(name: str) -> bool:
    """Ignore sidecars / OS junk so list order matches annotation PKLs by stem, not by index."""
    lower = name.lower()
    return lower.endswith(
        (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".ome.tif", ".ome.tiff")
    )


def _intensity_slice_stem(filename: str) -> str:
    """Slice id shared with Align PKLs and ROI output names (M528_s061.ome.tiff -> M528_s061)."""
    return Path(filename).name.split(".", 1)[0]


def _align_stripped_basename(filename: str) -> str:
    """Basename with last extension removed — same rule as py/map.py Align output."""
    parts = Path(filename).name.split(".")
    if len(parts) <= 1:
        return parts[0]
    return ".".join(parts[:-1])


def _annotation_pkl_id_from_stem(pkl_stem: str) -> str:
    """Strip Align-style Annotation_ / annotations_ prefix from a PKL filename stem."""
    lower = pkl_stem.lower()
    for prefix in ("annotation_", "annotations_"):
        if lower.startswith(prefix):
            return pkl_stem[len(prefix) :]
    return pkl_stem


def _annotation_pkl_candidate_names(intensity_filename: str) -> list[str]:
    """PKL basenames to try for this intensity file (Align writes Annotation_<name>.pkl)."""
    name = Path(intensity_filename).name
    slice_stem = _intensity_slice_stem(name)
    align_stripped = _align_stripped_basename(name)
    ids: list[str] = []
    for piece in (slice_stem, align_stripped):
        if piece and piece not in ids:
            ids.append(piece)
    names: list[str] = []
    for piece in ids:
        for pat in (f"{piece}.pkl", f"Annotation_{piece}.pkl", f"annotations_{piece}.pkl"):
            if pat not in names:
                names.append(pat)
    return names


def _resolve_annotation_pkl(annotation_dir: Path, intensity_filename: str) -> Path | None:
    """Match Align output PKLs (Annotation_M528_s061.pkl, etc.) to the intensity slice."""
    for cand in _annotation_pkl_candidate_names(intensity_filename):
        path = annotation_dir / cand
        if path.is_file():
            return path

    slice_stem = _intensity_slice_stem(intensity_filename).lower()
    align_stripped = _align_stripped_basename(intensity_filename).lower()
    want_ids = {slice_stem, align_stripped}

    for path in annotation_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".pkl":
            continue
        pkl_id = _annotation_pkl_id_from_stem(path.stem).lower()
        if pkl_id in want_ids or path.stem.lower() in want_ids:
            return path
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calculate the average intensity of a region in normalized coordinates"
    )

    parser.add_argument(
        "-i", "--images", help="input directory for intensity images", default=""
    )
    parser.add_argument(
        "-o", "--output", help="output directory for average intensity pkl", default=""
    )
    parser.add_argument(
        "-a", "--annotations", help="input directory for annotation pkls", default=""
    )
    parser.add_argument(
        "-m", "--map", help="input directory for structure map", default=""
    )
    parser.add_argument(
        "-w",
        "--whole",
        help="Set True to process a whole brain slice (Default is False)",
        default=False,
    )
    parser.add_argument(
        "-d",
        "--dapi-dir",
        help="optional directory of low-res DAPI images (same stem as intensity files; "
        ".png/.tif/.jpg or stem_dapi.png/.tif)",
        default="",
    )
    parser.add_argument(
        "--slice-list",
        help="JSON file with slice ids to process (array or {slice_ids: []})",
        default="",
    )
    parser.add_argument(
        "--config",
        help="JSON run config from Isolate Regions wizard (selected_region_ids, paths, flags)",
        default="",
    )
    args = parser.parse_args()

    intensity_config = None
    config_path = args.config.strip()
    if config_path:
        intensity_config = load_intensity_config(config_path)
        intensityPath = intensity_config.input_dir or args.images.strip()
        output_dir_str = intensity_config.output_dir or args.output.strip()
        annotationPath = intensity_config.annotation_dir or args.annotations.strip()
        is_whole = intensity_config.whole
        dapi_dir_raw = (
            intensity_config.dapi_dir if intensity_config.use_dapi else ""
        )
        slice_list_path = (
            intensity_config.slice_list or args.slice_list.strip() or ""
        )
        selected_region_ids = intensity_config.selected_region_ids
        include_layers = intensity_config.include_layers
    else:
        intensityPath = args.images.strip()
        output_dir_str = args.output.strip()
        annotationPath = args.annotations.strip()
        is_whole = parse_whole_flag(args.whole)
        dapi_dir_raw = args.dapi_dir.strip()
        slice_list_path = args.slice_list.strip() or ""
        include_layers = False
        selected_region_ids = []

    # Intensity files: only image-like names so sort order is not thrown off by .DS_Store, etc.
    intensityFiles = sorted(
        f for f in os.listdir(intensityPath) if _is_candidate_intensity_filename(f)
    )
    mode_label = "whole" if is_whole else "hemisphere"
    print(f"LOG: intensity_mode={mode_label}", flush=True)
    if include_layers:
        print("LOG: intensity_layers=on", flush=True)
    else:
        print("LOG: intensity_layers=off", flush=True)
    dapi_dir_path = Path(dapi_dir_raw) if dapi_dir_raw else None

    annotation_dir = Path(annotationPath)
    align_slice_layouts = load_align_slice_layouts(annotation_dir)
    if align_slice_layouts:
        print(
            f"LOG: intensity_per_slice_layouts={len(align_slice_layouts)} "
            f"from align manifest",
            flush=True,
        )
    print(f"LOG: intensity_dir={intensityPath}", flush=True)
    print(f"LOG: annotation_dir={annotationPath}", flush=True)

    print(2 + len(intensityFiles), flush=True)
    print("Setting up...", flush=True)

    structure_map = pickle.load(open(args.map.strip(), "rb"))
    map_path = Path(args.map.strip())
    graph_path = map_path.parent / "structure_graph.json"
    catalog = load_catalog(graph_path) if graph_path.is_file() else {"by_id": {}, "by_acronym": {}}
    if not selected_region_ids:
        selected_region_ids = [
            int(atlas_id)
            for atlas_id, data in structure_map.items()
            if data["acronym"] in LEGACY_VIS_RSP_ACRONYMS
        ]
    run_ctx = load_parcellation_context(annotation_dir)
    effective_include_layers = include_layers
    if include_layers and not include_layers_allowed(run_ctx):
        print(
            "LOG: intensity_layers_forced_off reason=parcellation_tier_not_layers",
            flush=True,
        )
        effective_include_layers = False
    output_targets = resolve_output_targets(
        structure_map,
        selected_region_ids,
        effective_include_layers,
        run_ctx,
        catalog,
    )
    if not output_targets:
        print(
            "NO_PKLS_WRITTEN: No valid output regions for selected_region_ids.",
            flush=True,
        )
        sys.exit(1)
    print(
        f"LOG: intensity_regions={len(output_targets)} selected_ids={len(selected_region_ids)}",
        flush=True,
    )
    allowed_slices = load_slice_list(slice_list_path or None)
    slices_processed = 0
    total_pkls_written = 0

    for iName in intensityFiles:
        stem = _intensity_slice_stem(iName)
        if not slice_id_allowed(stem, allowed_slices):
            print(f"Skipping {iName} (not in slice list)", flush=True)
            continue
        anno_pkl = _resolve_annotation_pkl(annotation_dir, iName)
        if anno_pkl is None:
            tried = ", ".join(_annotation_pkl_candidate_names(iName)[:4])
            msg = (
                f"No annotation PKL for intensity '{iName}' (slice id {stem!r}). "
                f"Tried names like {tried} in {annotation_dir}."
            )
            print(msg, flush=True)
            print(msg, file=sys.stderr, flush=True)
            continue

        # load the image
        try:
            intensity = tifffile.imread(intensityPath + "/" + iName)
            if intensity.ndim != 2:
                print(
                    f"Erorr loading {iName}! Expected single-channel 2D image.", flush=True
                )
                continue
            # get the image width and height
            height, width = intensity.shape
        except Exception:
            print(f"Erorr loading {iName}! Channels > 1 or bad image.", flush=True)
            continue

        dapi_resized = None
        dapi_source_path_str = ""
        if dapi_dir_path is not None:
            dapi_img_path = _resolve_dapi_image_path(dapi_dir_path, stem)
            if dapi_img_path is None:
                msg = (
                    f"DAPI folder set but no matching DAPI file for intensity '{iName}' "
                    f"(stem {stem!r}). Tried names like {stem}.png, {stem}.tif, {stem}_dapi.png "
                    f"in {dapi_dir_path}. PKLs from this slice will NOT include dapi_roi."
                )
                print(msg, flush=True)
                print(msg, file=sys.stderr, flush=True)
            else:
                dapi_gray = _load_dapi_grayscale(dapi_img_path)
                if dapi_gray is None:
                    msg = f"Failed to read DAPI image: {dapi_img_path}"
                    print(msg, flush=True)
                    print(msg, file=sys.stderr, flush=True)
                else:
                    dapi_resized = cv2.resize(
                        dapi_gray, (width, height), interpolation=cv2.INTER_AREA
                    )
                    dapi_source_path_str = str(dapi_img_path.resolve())

        # load the annotation (matched by stem, not by sorted index)
        with open(anno_pkl, "rb") as f:
            print("Processing " + iName, flush=True)
            slices_processed += 1
            annotation = pickle.load(f)

            annotation_recaled = resize_image_nearest_neighbor(
                annotation, (width, height)
            )
            slice_ctx = load_parcellation_context(annotation_dir, stem)
            slice_include_layers = effective_include_layers
            if slice_include_layers and not include_layers_allowed(slice_ctx):
                slice_include_layers = False
            slice_targets = resolve_output_targets(
                structure_map,
                selected_region_ids,
                slice_include_layers,
                slice_ctx,
                catalog,
            )
            if not slice_targets:
                print(
                    f"LOG: intensity_skip_slice slice={stem} reason=no_output_targets",
                    flush=True,
                )
                continue
            tier_log = slice_ctx.tier_id or (
                f"level_{slice_ctx.st_level}" if slice_ctx.st_level is not None else "full"
            )
            print(
                f"LOG: intensity_parcellation_context slice={stem} "
                f"tier={tier_log} layers_allowed={include_layers_allowed(slice_ctx)} "
                f"layers={slice_include_layers}",
                flush=True,
            )
            intensities = {tid: {} for tid in slice_targets}
            dapi_intensities = {tid: {} for tid in slice_targets}
            matching_ids = {
                tid: atlas_ids_matching_target(
                    structure_map,
                    tid,
                    slice_include_layers,
                    slice_ctx,
                    catalog,
                )
                for tid in slice_targets
            }

            slice_is_whole = intensity_whole_for_slice(
                stem, is_whole, align_slice_layouts
            )
            if align_slice_layouts:
                slice_mode = "whole" if slice_is_whole else "hemisphere"
                print(
                    f"LOG: intensity_slice_mode slice={stem} mode={slice_mode}",
                    flush=True,
                )

            for parent_id, label_ids in matching_ids.items():
                for label_id in label_ids:
                    verts = np.where(annotation_recaled == np.uint32(label_id))
                    if verts[0].size == 0:
                        continue
                    for point in zip(*verts):
                        # Integer tuple keys so sparse dicts match after pickle and in export_roi_dual_tif.
                        vkey = (int(point[0]), int(point[1]))
                        # check if whole
                        if not slice_is_whole:
                            intensities[parent_id][vkey] = intensity[point]
                            if dapi_resized is not None:
                                dapi_intensities[parent_id][vkey] = int(
                                    dapi_resized[point]
                                )
                        else:
                            # take only points in the left half
                            if point[1] < width // 2:
                                intensities[parent_id][vkey] = intensity[point]
                                if dapi_resized is not None:
                                    dapi_intensities[parent_id][vkey] = int(
                                        dapi_resized[point]
                                    )

            slice_pkls_written = 0
            # Save the intensity values and the verticies as ROI package pkls
            for region in intensities.keys():
                if intensities[region] == {}:
                    continue

                name = stem
                region_name = slice_targets[region]

                # split file name
                pkl_out = Path(
                    output_dir_str + "/" + f"{name}_{region_name}" + ".pkl"
                )

                pkg = {
                    "roi": intensities[region],
                    "name": region_name,
                }
                if dapi_resized is not None and dapi_intensities[region]:
                    pkg["dapi_roi"] = dapi_intensities[region]
                    pkg["channel_order_tif"] = ["DAPI", "signal"]
                    pkg["intensity_source_name"] = iName
                    if dapi_source_path_str:
                        pkg["dapi_source_path"] = dapi_source_path_str

                with open(pkl_out, "wb") as f:
                    pickle.dump(pkg, f)
                slice_pkls_written += 1
                total_pkls_written += 1

            print(
                f"LOG: {stem}: wrote {slice_pkls_written} PKLs ({mode_label} mode)",
                flush=True,
            )

    output_dir = Path(output_dir_str)
    pkl_count = len(
        [
            p
            for p in output_dir.glob("*.pkl")
            if p.name != "run_manifest.json"
        ]
    )
    if slices_processed > 0 and (total_pkls_written == 0 or pkl_count == 0):
        summary = (
            "NO_PKLS_WRITTEN: Isolate Regions finished with zero PKL files. "
            f"Processed {slices_processed} slice(s) in {mode_label} mode. "
            "No pixels matched the selected atlas regions. "
            "If you used Whole Slice mode, only the left half of each slice is kept; "
            "try Hemisphere Only or verify alignment annotations."
        )
        print(summary, flush=True)
        print(summary, file=sys.stderr, flush=True)

    print("Done!", flush=True)
    from run_manifest import write_run_manifest

    manifest_extra = {
        "step": "intensity",
        "input_dir": args.images.strip(),
        "annotation_dir": args.annotations.strip(),
        "output_dir": output_dir_str,
        "whole": is_whole,
        "pkls_written": total_pkls_written,
        "dapi_dir": dapi_dir_raw,
        "slice_list": slice_list_path or None,
        "selected_region_ids": selected_region_ids,
        "include_layers": include_layers,
    }
    write_run_manifest(output_dir_str, manifest_extra)

    if slices_processed > 0 and total_pkls_written == 0:
        sys.exit(1)
    if slices_processed == 0 and len(intensityFiles) > 0:
        # Inputs existed but nothing was processed (no slice matched an
        # annotation PKL, or all were filtered out). That is a failed run, not
        # a silent success.
        msg = (
            "NO_PKLS_WRITTEN: Isolate Regions processed 0 slices though "
            f"{len(intensityFiles)} intensity image(s) were found. No slice "
            "matched an alignment annotation PKL (or all were filtered out by "
            "the slice list). Re-run Align or check the active slices run."
        )
        print(msg, flush=True)
        print(msg, file=sys.stderr, flush=True)
        sys.exit(1)

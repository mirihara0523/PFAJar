import pipeline_io_bootstrap  # noqa: F401
import argparse
from pathlib import Path

from slice_index import (
    load_slice_list,
    slice_id_allowed,
    index_annotation_pkls,
    pair_prediction_annotation_pkls,
    slice_stem_from_annotation_pkl,
    slice_stem_from_prediction_pkl,
)
import numpy as np
import os
import csv
import cv2
import pickle
from find_neurons import DetectionResult
from demons import resize_image_nearest_neighbor
from annotation_match import (
    count_rollup_log_label,
    load_parcellation_context,
    resolve_count_label_id,
    summarize_count_rollup_labels,
)
from structure_catalog import load_catalog


def collect_used_acronyms(sums: dict, region_areas: dict) -> set[str]:
    """Acronyms present after resolve (detections and/or annotation area).

    Count CSV rows follow this set so laminar (and mixed-tier) labels are not
    dropped by the legacy ``"layer" in name`` structure-map filter.
    """
    acronyms: set[str] = set()
    for channels in sums.values():
        for channel_counts in channels.values():
            acronyms.update(channel_counts.keys())
    for areas in region_areas.values():
        acronyms.update(areas.keys())
    return acronyms


def iou(boxA, boxB):
    """
    Compute the Intersection over Union (IoU) between two bounding boxes.

    Parameters:
    - boxA: list of [xmin, ymin, xmax, ymax] for the first box.
    - boxB: list of [xmin, ymin, xmax, ymax] for the second box.

    Returns:
    - iou value.
    """
    # Determine the coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    # Compute the area of intersection
    inter_area = max(0, xB - xA + 1) * max(0, yB - yA + 1)

    # Compute the area of both bounding boxes
    boxA_area = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxB_area = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)

    # Compute the IoU (guard degenerate boxes so we never divide by zero)
    denom = float(boxA_area + boxB_area - inter_area)
    if denom <= 0:
        return 0.0
    iou_value = inter_area / denom

    return iou_value


def compute_overlaps(boxes1, boxes2):
    """
    Compute overlaps (IoU) between two sets of boxes.

    Parameters:
    - boxes1: list of bounding boxes. Each box is a list of [xmin, ymin, xmax, ymax].
    - boxes2: list of bounding boxes. Each box is a list of [xmin, ymin, xmax, ymax].

    Returns:
    - overlaps matrix where each element (i, j) is the IoU between boxes1[i] and boxes2[j].
    """
    overlaps = np.zeros((len(boxes1), len(boxes2)))

    for i, box1 in enumerate(boxes1):
        for j, box2 in enumerate(boxes2):
            overlaps[i, j] = iou(box1, box2)

    return overlaps


def percent_colocalized(boxes1, boxes2, threshold=0.5):
    """
    Compute the percentage of boxes in boxes1 that are colocalized with any box in boxes2.

    Parameters:
    - boxes1: list of bounding boxes. Each box is a list of [xmin, ymin, xmax, ymax].
    - boxes2: list of bounding boxes. Each box is a list of [xmin, ymin, xmax, ymax].
    - threshold: Minimum IoU value to consider two boxes as colocalized.

    Returns:
    - Percentage of colocalized boxes.
    """
    if len(boxes1) == 0 or len(boxes2) == 0:
        return 0
    overlaps = compute_overlaps(boxes1, boxes2)

    # For each box in boxes1, find the max IoU with any box in boxes2
    max_overlaps = np.max(overlaps, axis=1)

    # Count how many boxes in boxes1 are colocalized with boxes in boxes2
    colocalized_count = np.sum(max_overlaps > threshold)

    return (colocalized_count / len(boxes1)) * 100


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Integrate cell positions with alignments to count an experiment"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-p",
        "--predictions",
        help="predictions directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-a",
        "--annotations",
        help="annotations directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-m",
        "--structures",
        help="path to structure map",
        default="../csv/structure_map.pkl",
    )
    parser.add_argument(
        "-l",
        "--layers",
        help=(
            "keep literal pixel atlas IDs (no parcellation rollup); "
            "Electron omits this — CSV already emits resolved used acronyms"
        ),
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--slice-list",
        help="JSON file with slice ids to process",
        default="",
    )
    args = parser.parse_args()
    prediction_path = Path(args.predictions.strip())
    annotation_path = Path(args.annotations.strip())
    output_path = Path(args.output.strip())

    annotation_files = [
        name for name in os.listdir(annotation_path) if name.endswith("pkl")
    ]
    predictionFiles = [
        name for name in os.listdir(prediction_path) if name.endswith("pkl")
    ]
    allowed_slices = load_slice_list(args.slice_list.strip() or None)

    if allowed_slices is not None:
        predictionFiles = [
            n
            for n in predictionFiles
            if slice_id_allowed(slice_stem_from_prediction_pkl(n), allowed_slices)
        ]
        annotation_files = [
            n
            for n in annotation_files
            if slice_id_allowed(slice_stem_from_annotation_pkl(n), allowed_slices)
        ]

    ann_by_stem = index_annotation_pkls(annotation_files)
    pred_stems = {slice_stem_from_prediction_pkl(n) for n in predictionFiles}
    for stem, a_name in sorted(ann_by_stem.items()):
        if stem not in pred_stems:
            print(
                f"Skipping annotation {a_name}: no matching prediction stem {stem}",
                flush=True,
            )

    paired = pair_prediction_annotation_pkls(predictionFiles, annotation_files)
    paired_stems = {slice_stem_from_prediction_pkl(p) for p, _ in paired}
    for p_name in sorted(
        predictionFiles, key=lambda x: slice_stem_from_prediction_pkl(x)
    ):
        stem = slice_stem_from_prediction_pkl(p_name)
        if stem not in paired_stems:
            print(
                f"Skipping prediction {p_name}: no matching annotation for stem {stem}",
                flush=True,
            )

    if not paired:
        print(2, flush=True)
        print("No matching prediction/annotation pairs to count.", flush=True)
        print("Done!", flush=True)
        raise SystemExit(1)

    print(len(paired) + 1, flush=True)
    # Reading in regions
    regions = {}
    acronym_to_region = {}
    with open(args.structures.strip(), "rb") as f:
        regions = pickle.load(f)
        for k, v in regions.items():
            acronym_to_region[v["acronym"]] = k

    structures_path = Path(args.structures.strip())
    graph_path = structures_path.parent / "structure_graph.json"
    catalog = load_catalog(graph_path) if graph_path.is_file() else None

    def _resolve_label(atlas_id, slice_context):
        return resolve_count_label_id(
            int(atlas_id),
            slice_context,
            catalog or {},
            regions,
            include_layers=args.layers,
        )

    def _region_info(resolved_id):
        info = regions.get(resolved_id)
        if info is None:
            info = regions.get(np.uint32(resolved_id))
        return info

    sums = {}
    colocalized = {}
    region_areas = {}
    rollup_labels: list[str] = []
    for pName, aName in paired:
        sums[pName] = {}
        region_areas[pName] = {}
        with open(prediction_path / pName, "rb") as predictionPkl, open(
            annotation_path / aName, "rb"
        ) as annotationPkl:
            print(f"Counting {aName.split('.')[0]}...", flush=True)
            predictions = pickle.load(predictionPkl)
            predictions = [p for p in predictions]
            annotation = pickle.load(annotationPkl)
            if not predictions:
                # An empty/corrupt prediction file must not abort the whole run.
                print(
                    f"Skipping {pName}: no detection channels in prediction file.",
                    flush=True,
                )
                sums.pop(pName, None)
                region_areas.pop(pName, None)
                continue
            predicted_size = predictions[0].image_dimensions

            # image_dimensions is (height, width); resize_image_nearest_neighbor
            # forwards new_size to SimpleITK SetSize, which expects (width, height).
            target_height, target_width = int(predicted_size[0]), int(predicted_size[1])
            annotation_rescaled = resize_image_nearest_neighbor(
                annotation, (target_width, target_height)
            )

            slice_id = slice_stem_from_annotation_pkl(aName)
            slice_context = load_parcellation_context(annotation_path, slice_id)
            rollup_labels.append(
                count_rollup_log_label(slice_context, include_layers=args.layers)
            )

            unique_ids, counts = np.unique(annotation_rescaled, return_counts=True)
            for unique_id, count in zip(unique_ids, counts):
                if unique_id == 0:
                    continue
                resolved_id = _resolve_label(unique_id, slice_context)
                region_info = _region_info(resolved_id)
                if region_info is None:
                    continue
                name = region_info["acronym"]
                region_areas[pName][name] = region_areas[pName].get(name, 0) + int(count)

        # Counters grow from detections after resolve; CSV emits used acronyms.
        sums[pName] = {c: {} for c in range(len(predictions))}

        all_boxes = {c: [] for c in range(len(predictions))}
        for c, detection in enumerate(predictions):
            counted_boxes = 0
            for box in detection.boxes:
                counted_boxes += 1
                all_boxes[c] += [box]
                x, y, mX, mY = box[0], box[1], box[2], box[3]
                xPos = int((mX - (mX - x) // 2))
                yPos = int((mY - (mY - y) // 2))
                # Clamp to the annotation bounds (a box can sit on the edge).
                yPos = min(max(yPos, 0), annotation_rescaled.shape[0] - 1)
                xPos = min(max(xPos, 0), annotation_rescaled.shape[1] - 1)
                atlas_id = annotation_rescaled[yPos, xPos]
                resolved_id = _resolve_label(atlas_id, slice_context)
                region_info = _region_info(resolved_id)
                if region_info is None:
                    continue
                acronym = region_info["acronym"]
                sums[pName][c][acronym] = sums[pName][c].get(acronym, 0) + 1

        # Compute colocalization
        colocalized[pName] = {}
        local_colocalized = colocalized[pName]
        for c, boxes in all_boxes.items():
            local_colocalized[c] = {}
            for c2, boxes2 in all_boxes.items():
                local_colocalized[c][c2] = percent_colocalized(boxes, boxes2)

    print(
        f"LOG: count_rollup={summarize_count_rollup_labels(rollup_labels)}",
        flush=True,
    )

    used_acronyms = collect_used_acronyms(sums, region_areas)

    with open(output_path / "count_results.csv", "w", newline="") as resultFile:
        print("Writing output...", flush=True)
        lines = []
        running_counts = {}
        running_areas = {}
        for file, channels in sums.items():
            lines.append([file])
            lines.append(
                ["Region Acronym", "Region Name", "Area (px)"]
                + [f"Channel #{c}" for c in range(len(channels))]
            )
            for region in sorted(used_acronyms):
                per_channel_counts = []
                for channel in channels:
                    n = int(channels[channel].get(region, 0))
                    per_channel_counts.append(n)
                    running_counts[region] = running_counts.get(region, 0) + n

                # Find name from acronym
                region_id = acronym_to_region.get(region)
                if region_id is None:
                    region_name = "Unknown"
                else:
                    region_name = regions[region_id]["name"]
                region_area = region_areas[file].get(region, 0)
                running_areas[region] = running_areas.get(region, 0) + region_area
                lines.append(
                    [
                        region,
                        region_name,
                        region_area,
                    ]
                    + per_channel_counts
                )
            lines.append([])

        lines.append(["Totals"])
        lines.append(["Region Acronym", "Region Name", "Count", "Area (px)"])
        for region in sorted(running_counts.keys()):
            count = running_counts.get(region, 0)
            region_id = acronym_to_region.get(region)
            if region_id is None:
                region_name = "Unknown"
            else:
                region_name = regions[region_id]["name"]
            lines.append([region, region_name, count, running_areas.get(region, 0)])

        lines.append([])
        # Colocalization
        lines.append(["Colocalization Matrix (by Section)"])
        for s, colocal in colocalized.items():
            lines.append([s] + [f"Channel #{c}" for c in range(len(colocal))])
            for c, colocal2 in colocal.items():
                line = [f"Channel #{c}"]
                for c2 in range(len(colocal2)):
                    percent = colocal2.get(c2, 0)
                    line.append(percent)
                lines.append(line)

        writer = csv.writer(resultFile)
        writer.writerows(lines)
    print("Done!", flush=True)
    from run_manifest import write_run_manifest

    write_run_manifest(
        output_path,
        {
            "step": "count",
            "predictions_dir": args.predictions.strip(),
            "annotations_dir": args.annotations.strip(),
            "output_dir": str(output_path),
            "layers": bool(args.layers),
            "slice_list": args.slice_list.strip() if hasattr(args, "slice_list") else None,
        },
    )
import cv2
import pipeline_io_bootstrap  # noqa: F401
import pickle
import os
import json
from datetime import datetime, timezone
from skimage.measure import label, regionprops
from skimage.filters import threshold_otsu
import numpy as np
import argparse
import inspect
from pathlib import Path
from skimage.exposure import equalize_adapthist
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
import tifffile as tiff
import torch

from detect_qc import DetectQcCollector, bbox_intensity_p90, cleanup_detect_qc_artifacts, write_run_histograms
from detect_qc_analysis import filter_objects_by_intensity


class DetectionResult:
    def __init__(self, boxes, scores, image_dimensions):
        self.boxes = boxes
        self.scores = scores
        self.image_dimensions = image_dimensions


def export_bboxes(image, boxes, output_path):
    for box in boxes:
        x1, y1, x2, y2 = [int(b) for b in box]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)

    cv2.imwrite(str(output_path), image)


def measure_eccentricity(box, image):
    try:
        box = [int(b) for b in box]
        cell_image = image[box[1] - 5 : box[3] + 5, box[0] - 5 : box[2] + 5, :]
        if len(cell_image.shape) > 2:
            cell_image = cv2.cvtColor(cell_image, cv2.COLOR_BGR2GRAY)
        mask = cell_image > threshold_otsu(cell_image)

        labeled_mask = label(mask)
        regions = regionprops(labeled_mask)

        if not regions:
            return None

        largest_region = max(regions, key=lambda r: r.area)
        return float(largest_region.eccentricity)
    except Exception as e:
        print("Failed to measure eccentricity. Error: ", e)
        return None


def check_eccentricity(box, threshold, image):
    eccentricity = measure_eccentricity(box, image)
    if eccentricity is None:
        return True
    return eccentricity > threshold

def xyxy_to_area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _limit_detect_threads():
    """Electron path: no extra OS processes from torch/OpenMP (worker sets env too)."""
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    try:
        torch.set_num_threads(1)
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(1)
    except Exception:
        pass
    print("LOG: detect_threads=1", flush=True)


def make_tile_progress_printer(label):
    """Emit stdout lines so Mason Jar's progress bar updates during SAHI tiling."""
    state = {"last": 0}

    def progress_callback(current, total):
        if not total:
            return
        step = max(1, total // 25)
        if current == 1 or current == total or current - state["last"] >= step:
            print(f"{label}: tile {current}/{total}", flush=True)
            state["last"] = current

    return progress_callback


def _call_get_sliced_prediction(image, detection_model, tile_size, label):
    """Call SAHI sliced prediction; only pass kwargs supported by installed sahi."""
    kwargs = {
        "slice_height": tile_size,
        "slice_width": tile_size,
        "overlap_height_ratio": 0.1,
        "overlap_width_ratio": 0.1,
        "verbose": 1,
    }
    params = inspect.signature(get_sliced_prediction).parameters
    if "progress_bar" in params:
        kwargs["progress_bar"] = False
    if "progress_callback" in params:
        kwargs["progress_callback"] = make_tile_progress_printer(label)
    return get_sliced_prediction(image, detection_model, **kwargs)


def run_sliced_detection(image, detection_model, tile_size, label):
    print(
        f"{label}: starting tiled detection ({image.shape[1]}×{image.shape[0]} px, "
        f"tile {tile_size})…",
        flush=True,
    )
    return _call_get_sliced_prediction(image, detection_model, tile_size, label)


def screen_predictions(
    prediction_objects,
    area_threshold,
    eccentricity_threshold=None,
    image=None,
    gray_for_qc=None,
    sam_model_path=None,
):
    """Screen predictions for objects below a certain area."""
    del sam_model_path  # reserved for future SAM-based screening
    first_pass = [
        obj
        for obj in prediction_objects
        if xyxy_to_area(obj.bbox.to_xyxy()) > area_threshold
    ]

    if len(first_pass) < 3:
        second_pass = first_pass
    else:
        avg_area = sum([xyxy_to_area(obj.bbox.to_xyxy()) for obj in first_pass]) / len(first_pass)
        std_area = np.std([xyxy_to_area(obj.bbox.to_xyxy()) for obj in first_pass])
        second_pass = [
            obj
            for obj in first_pass
            if xyxy_to_area(obj.bbox.to_xyxy()) < avg_area + 2 * std_area
        ]

    pre_ecc_eccentricities = []
    pre_ecc_records: list[tuple[float, float]] = []
    if eccentricity_threshold is not None:
        try:
            assert image is not None
            filtered = []
            for obj in second_pass:
                val = measure_eccentricity(obj.bbox.to_xyxy(), image)
                inten = None
                if gray_for_qc is not None:
                    inten = bbox_intensity_p90(obj.bbox.to_xyxy(), gray_for_qc)
                if val is not None:
                    pre_ecc_eccentricities.append(val)
                    if inten is not None:
                        pre_ecc_records.append((val, inten))
                if val is None or val > eccentricity_threshold:
                    filtered.append(obj)
            second_pass = filtered
        except AssertionError:
            print("Image not provided. Eccentricity screening not performed.")

    return second_pass, pre_ecc_eccentricities, pre_ecc_records

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find neurons in images")
    parser.add_argument(
        "-o",
        "--output",
        help="output directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-i", "--input", help="input directory, only use if graphical false", default=""
    )
    parser.add_argument("-t", "--tile", help="tile size", default=640)
    parser.add_argument(
        "-c", "--confidence", help="confidence level for detections", default=0.85
    )
    parser.add_argument(
        "-m", "--model", help="specify model file", default="../models/ancientwizard.pt"
    )
    parser.add_argument("-s", "--sam", default="~/.belljar/models/sam_vit_b.pth")
    parser.add_argument("-e", "--eccentricity", help="eccentricity threshold", default=0.5)
    parser.add_argument(
        "-n",
        "--multichannel",
        help="specify if multichannel",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-a",
        "--area",
        help="area threshold for screening",
        default=200,
    )
    parser.add_argument(
        "--slice-list",
        help="JSON file with slice ids to process (filters input images)",
        default="",
    )
    parser.add_argument(
        "--per-slice-qc",
        help="write per-slice QC histogram PNGs under detect_qc_slices/",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--intensity-min",
        help="minimum bbox intensity p90 (0-255) to keep detection; 0 disables",
        type=float,
        default=0.0,
    )
    parser.add_argument(
        "--qc-only",
        help=(
            "write full Detect QC package (histograms, summary, suggestions) "
            "without Predictions_*.pkl or bbox PNGs"
        ),
        action="store_true",
        default=False,
    )
    args = parser.parse_args()

    _limit_detect_threads()

    input_dir = Path(args.input.strip())
    output_dir = Path(args.output.strip())
    output_dir.mkdir(parents=True, exist_ok=True)
    removed_qc = cleanup_detect_qc_artifacts(output_dir)
    if removed_qc:
        print(
            "LOG: detect_qc_cleanup removed " + ", ".join(removed_qc),
            flush=True,
        )
    tile_size = int(args.tile)
    model_path = args.model.strip()
    area_threshold = float(args.area.strip())
    eccentricity_threshold = float(args.eccentricity.strip())
    confidence_threshold = float(args.confidence)
    intensity_min = float(args.intensity_min or 0.0)
    qc_collector = DetectQcCollector()
    qc_thresholds = {
        "confidence": confidence_threshold,
        "area_px2": area_threshold,
        "eccentricity": eccentricity_threshold,
        "intensity_min": intensity_min,
    }

    from slice_index import load_slice_list, slice_id_allowed

    # add mps device if available (MASONJAR_DETECT_CPU=1 forces CPU on Mac when MPS hangs)
    force_cpu = os.environ.get("MASONJAR_DETECT_CPU", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if force_cpu:
        device = "cpu"
    elif torch.cuda.is_available():
        device = "cuda:0"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    if device == "cpu" and not force_cpu:
        print(
            f"LOG: detect_device=cpu torch={torch.__version__} "
            f"cuda_built={torch.backends.cuda.is_built()} "
            f"cuda_available={torch.cuda.is_available()}",
            flush=True,
        )

    # Pruning
    endings = ["png", "jpg", "jpeg", "tif", "tiff"]
    files = os.listdir(input_dir)
    files = [f for f in files if f.split(".")[-1].lower() in endings]
    files.sort()

    def _image_slice_id(fname: str) -> str:
        stem = Path(fname).stem
        if stem.lower().endswith(".ome"):
            stem = Path(stem).stem
        dot = stem.find(".")
        return stem[:dot] if dot >= 0 else stem

    allowed = load_slice_list(args.slice_list.strip() or None)
    if allowed is not None:
        files = [f for f in files if slice_id_allowed(_image_slice_id(f), allowed)]

    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "model": model_path,
        "confidence": args.confidence,
        "tile": args.tile,
        "area": args.area,
        "eccentricity": args.eccentricity,
        "intensity_min": intensity_min,
        "multichannel": bool(args.multichannel),
        "sam": args.sam,
        "slice_list": args.slice_list.strip() or None,
        "input_files": sorted(files),
        "per_slice_qc": bool(args.per_slice_qc),
    }
    with open(output_dir / "run_manifest.json", "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    # Slice count for the Mason Jar progress bar (one step per image).
    print(len(files), flush=True)
    print(f"Using device: {device}", flush=True)
    print(f"Using model: {model_path}", flush=True)
    print(f"Using confidence level {confidence_threshold}", flush=True)
    if intensity_min > 0:
        print(f"Using intensity minimum {intensity_min}", flush=True)
    print(f"Found {len(files)} images", flush=True)
    
    detection_model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=model_path,
        confidence_threshold=confidence_threshold,
        device=device,
    )

    written = 0
    failed_reads = 0
    n_files = len(files)
    for file_index, file in enumerate(files):
        file_path = os.path.join(input_dir, file)
        stripped, ext = file.split(".")[0], file.split(".")[-1]
        slice_id = _image_slice_id(file)

        print(f"Running detection on {file}...", flush=True)
        # Try and load the image
        index_order = "F"
        try:
            if ext in ["tif", "tiff"]:
                img = tiff.imread(file_path)
                if len(img.shape) == 3:
                    channels, height, width = img.shape
                    index_order = "C"
                elif len(img.shape) == 2:
                    height, width = img.shape
                    channels = 1
                else:
                    raise Exception("Image has more than 3 dimensions!")
            else:
                img = cv2.imread(file_path)
                width, height, channels = img.shape
        except Exception as e:
            print(f"Error reading {file}!", flush=True)
            print(e, flush=True)
            failed_reads += 1
            print(
                f"SLICE_DONE:{file_index + 1}/{n_files}:{file}",
                flush=True,
            )
            continue

        # If multichannel, split into individual channels
        split_channels = []
        if args.multichannel:
            for i in range(channels):
                if index_order == "F":
                    split_channels.append(img[:, :, i])
                else:
                    split_channels.append(img[i, :, :])

        predictions = []
        if len(split_channels) > 0:
            for i, chan_img in enumerate(split_channels):
                # Check data type
                if chan_img.dtype == np.uint16:
                    chan_img = (chan_img / 256).astype(np.uint8)
                elif chan_img.dtype == np.float32 or chan_img.dtype == np.float64:
                    chan_img = (chan_img * 255).astype(np.uint8)

                # equalize
                chan_img = equalize_adapthist(chan_img, clip_limit=0.01)
                chan_img = (chan_img * 255).astype(np.uint8)
                gray_for_qc = chan_img.copy()
                # convert to BGR
                chan_img = cv2.cvtColor(chan_img, cv2.COLOR_GRAY2BGR)
                result = run_sliced_detection(
                    chan_img,
                    detection_model,
                    tile_size,
                    f"{file} ch{i + 1}",
                )

                print(
                    f"Screening {len(result.object_prediction_list)} detections on {file} ch{i + 1}…",
                    flush=True,
                )
                predicted_objects, pre_ecc_ecc, pre_ecc_records = screen_predictions(
                    result.object_prediction_list,
                    area_threshold,
                    eccentricity_threshold=eccentricity_threshold,
                    image=chan_img,
                    gray_for_qc=gray_for_qc,
                    sam_model_path=Path(args.sam.strip()).expanduser(),
                )
                qc_collector.add_slice_pass(
                    slice_id,
                    result.object_prediction_list,
                    predicted_objects,
                    pre_ecc_ecc,
                    pre_ecc_records,
                    gray_for_qc,
                )
                predicted_objects, removed_int = filter_objects_by_intensity(
                    predicted_objects, gray_for_qc, intensity_min
                )
                if intensity_min > 0:
                    print(
                        f"LOG: detect_intensity_filter removed={removed_int} "
                        f"kept={len(predicted_objects)} min={intensity_min:g}",
                        flush=True,
                    )
                bboxes = [obj.bbox.to_xyxy() for obj in predicted_objects]
                scores = [obj.score.value for obj in predicted_objects]

                predictions.append(
                    DetectionResult(
                        boxes=bboxes,
                        scores=scores,
                        image_dimensions=(height, width),
                    )
                )
                if not args.qc_only:
                    bbox_path = Path(output_dir) / f"BBoxes_{stripped}_{i}.png"
                    export_bboxes(chan_img, bboxes, bbox_path)
        else:
            # Check data type
            if img.dtype == np.uint16:
                img = (img / 256).astype(np.uint8)
            elif img.dtype == np.float32 or img.dtype == np.float64:
                img = (img * 255).astype(np.uint8)
            
            img = equalize_adapthist(img, clip_limit=0.01)
            img = (img * 255).astype(np.uint8)
            gray_for_qc = img.copy()

            # convert to BGR
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

            result = run_sliced_detection(
                img,
                detection_model,
                tile_size,
                file,
            )

            print(
                f"Screening {len(result.object_prediction_list)} detections on {file}…",
                flush=True,
            )
            predicted_objects, pre_ecc_ecc, pre_ecc_records = screen_predictions(
                result.object_prediction_list,
                area_threshold,
                image=img,
                gray_for_qc=gray_for_qc,
                sam_model_path=Path(args.sam.strip()).expanduser(),
                eccentricity_threshold=eccentricity_threshold,
            )
            qc_collector.add_slice_pass(
                slice_id,
                result.object_prediction_list,
                predicted_objects,
                pre_ecc_ecc,
                pre_ecc_records,
                gray_for_qc,
            )
            predicted_objects, removed_int = filter_objects_by_intensity(
                predicted_objects, gray_for_qc, intensity_min
            )
            if intensity_min > 0:
                print(
                    f"LOG: detect_intensity_filter removed={removed_int} "
                    f"kept={len(predicted_objects)} min={intensity_min:g}",
                    flush=True,
                )

            bboxes = [obj.bbox.to_xyxy() for obj in predicted_objects]
            scores = [obj.score.value for obj in predicted_objects]

            predictions = [
                DetectionResult(
                    boxes=bboxes,
                    scores=scores,
                    image_dimensions=(height, width),
                )
            ]
            if not args.qc_only:
                bbox_path = Path(output_dir) / f"BBoxes_{stripped}.png"
                export_bboxes(img, bboxes, bbox_path)

        if args.qc_only:
            written += 1
        else:
            with open(output_dir / f"Predictions_{stripped}.pkl", "wb") as f:
                pickle.dump(predictions, f)
            written += 1
        print(
            f"SLICE_DONE:{file_index + 1}/{n_files}:{file}",
            flush=True,
        )

    qc_result = write_run_histograms(
        qc_collector,
        output_dir,
        qc_thresholds,
        per_slice_enabled=bool(args.per_slice_qc),
    )
    manifest["qc_only"] = bool(args.qc_only)
    manifest["qc_artifacts"] = {
        "run": qc_result["run_files"],
        "summary_json": qc_result["summary_json"],
        "per_slice_enabled": bool(args.per_slice_qc),
        "per_slice_dir": "detect_qc_slices" if args.per_slice_qc else None,
        "per_slice": qc_result["slice_files"],
    }
    if not args.qc_only:
        with open(output_dir / "run_manifest.json", "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, indent=2)
    else:
        # Lightweight scout marker (not a detection run_manifest)
        with open(output_dir / "qc_scout_manifest.json", "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, indent=2)
    print(
        "LOG: detect_qc_wrote run_histograms="
        + str(len(qc_result["run_files"]))
        + " per_slice="
        + ("true" if args.per_slice_qc else "false")
        + " qc_only="
        + ("true" if args.qc_only else "false"),
        flush=True,
    )

    if files and written == 0:
        # Inputs existed but nothing was detected/written: a failed run, not a
        # silent success (downstream Count would otherwise fail opaquely).
        print(
            f"DETECTION_NO_OUTPUT: 0 of {len(files)} images produced "
            + (
                "QC progress"
                if args.qc_only
                else "a Predictions PKL"
            )
            + f" ({failed_reads} read failure(s)).",
            flush=True,
        )
        print("Done!", flush=True)
        raise SystemExit(1)

    print("Done!", flush=True)

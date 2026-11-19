"""Persistent, serial JSON worker for the Cell Detection Viewer.

Inference runs outside Electron. Only the current full-resolution plane stays in
memory. Candidate measurements are cached on disk and reused during navigation.
"""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys
import time
import traceback
import uuid
import threading

from detect_live_core import atomic_write, filter_indices, windows_io_path, write_json


class ProgressReporter:
    def __init__(self, request_id, action, send, interval=1.0):
        self.request_id = request_id
        self.send = send
        self.started = time.monotonic()
        self.advanced = self.started
        self.stage, self.current, self.total = action, None, None
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.interval = interval
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)

    def publish(self):
        with self.lock:
            now = time.monotonic()
            payload = {"event": "progress", "id": self.request_id, "stage": self.stage,
                       "current": self.current, "total": self.total,
                       "elapsed_ms": (now - self.started) * 1000,
                       "idle_ms": (now - self.advanced) * 1000}
            self.send(payload)

    def update(self, stage, current=None, total=None):
        with self.lock:
            self.stage, self.current, self.total = stage, current, total
            self.advanced = time.monotonic()
        self.publish()

    def heartbeat(self):
        while not self.stopped.wait(self.interval):
            self.publish()

    def start(self):
        self.publish()
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join()


def sliced_prediction_with_progress(detector, image, model, tile, label, report):
    """Count completed model calls on SAHI versions without progress callbacks."""
    from sahi.slicing import get_slice_bboxes
    total = len(get_slice_bboxes(image.shape[0], image.shape[1], tile, tile,
                                overlap_height_ratio=0.1, overlap_width_ratio=0.1))
    report("inference", 0, total)
    original = model.perform_inference
    had_override = "perform_inference" in vars(model)
    completed, last_report = 0, 0.0

    def counted(*args, **kwargs):
        nonlocal completed, last_report
        result = original(*args, **kwargs)
        completed += 1
        now = time.monotonic()
        if completed <= total and (completed == total or now - last_report >= 0.3):
            report("inference", completed, total)
            last_report = now
        if completed == total:
            report("postprocess")
        return result

    model.perform_inference = counted
    try:
        return detector._call_get_sliced_prediction(image, model, tile, label)
    finally:
        if had_override:
            model.perform_inference = original
        else:
            del model.perform_inference


def preview_data(image):
    import cv2
    height, width = image.shape
    scale = min(1.0, 2048 / max(width, height))
    preview = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", preview)
    if not ok:
        raise ValueError("Could not encode preview.")
    return "data:image/png;base64," + base64.b64encode(encoded).decode("ascii")


def read_single_plane(source):
    import cv2
    import numpy as np
    import tifffile
    if source.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(source) as tif:
            if len(tif.series) != 1:
                raise ValueError("This TIFF contains multiple images. Select a single-channel MAX or preprocessed image.")
            series = tif.series[0]
            plane_count = np.prod([size for axis, size in zip(series.axes, series.shape) if axis not in ("Y", "X")])
            if plane_count != 1:
                raise ValueError("This TIFF contains multiple channels or planes. Select a single-channel MAX or preprocessed image.")
            image = series.asarray()
            axes = series.axes
        if "Y" not in axes or "X" not in axes:
            raise ValueError("TIFF must contain Y and X image axes.")
        image = np.moveaxis(image, (axes.index("Y"), axes.index("X")), (-2, -1))
        planes = image.reshape((-1,) + image.shape[-2:])
        image = planes[0].copy()
    else:
        image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError("Could not read image.")
        if image.ndim != 2:
            raise ValueError("This image contains multiple channels. Select a single-channel MAX or preprocessed image.")
    if image.dtype == np.uint16:
        image = (image / 256).astype(np.uint8)
    elif np.issubdtype(image.dtype, np.floating):
        if not np.isfinite(image).all() or image.min() < 0 or image.max() > 1:
            raise ValueError("Floating-point input must be finite and in the 0–1 range.")
        image = (image * 255).astype(np.uint8)
    elif image.dtype != np.uint8:
        raise ValueError("Supported image types are uint8, uint16, and float in the 0–1 range.")
    return image


class DetectionSession:
    def __init__(self):
        self.state = None
        self.model = None
        self.model_key = None
        self.report = lambda stage, current=None, total=None: None

    def order_file(self, request):
        from slice_order import order_path
        target = order_path(request["input_dir"], request.get("bundle_root") or None)
        return target or Path(request["input_dir"]) / ".pfa_viewer_slice_order.json"

    def order_info(self, request):
        import math
        from slice_order import read_order
        from slice_index import slice_stem_from_image_filename
        target = self.order_file(request)
        result = {"path": str(target), "saved_order": [], "ap_positions": {}, "warnings": []}
        if request.get("recover_flip") and request.get("bundle_root"):
            from section_flip import recover_flip
            if recover_flip(request["bundle_root"]):
                result["warnings"].append("Interrupted section flip rolled back from backup.")
        from section_policy import read_settings
        result["sections"] = read_settings(request["input_dir"], request.get("bundle_root") or None)
        try:
            result["saved_order"] = read_order(windows_io_path(target))
        except (OSError, ValueError) as error:
            result["warnings"].append("Saved order could not be read: " + str(error))
        dapi_dir = request.get("dapi_dir")
        if not dapi_dir:
            return result
        metadata = Path(dapi_dir) / "alignment_session.json"
        try:
            session = json.loads(windows_io_path(metadata).read_text(encoding="utf-8"))
            for row in session.get("slices", []):
                slice_id = row.get("slice_id")
                if not slice_id and row.get("filename"):
                    slice_id = slice_stem_from_image_filename(row["filename"])
                ap = row.get("ap_position")
                if slice_id and isinstance(ap, (int, float)) and not isinstance(ap, bool) and math.isfinite(ap):
                    result["ap_positions"][slice_id] = ap
            result["ap_source"] = str(metadata)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result["warnings"].append("AP metadata could not be read: " + str(error))
        return result

    def order_save(self, request):
        from slice_order import save_order
        from slice_index import slice_stem_from_image_filename
        input_dir = windows_io_path(request["input_dir"])
        names = [path.name for path in input_dir.iterdir()
                 if path.is_file() and path.suffix.lower() in (".tif", ".tiff", ".png", ".jpg", ".jpeg")]
        ids = [slice_stem_from_image_filename(name) for name in names]
        selected = request["slice_ids"]
        if len(set(ids)) != len(ids) or len(selected) != len(ids) or set(selected) != set(ids):
            raise ValueError("Every section must appear exactly once. Reload the folder if its files changed.")
        target = self.order_file(request)
        from section_policy import read_settings, save_active
        current = read_settings(request["input_dir"], request.get("bundle_root") or None)
        if "active" in request and current != request.get("expected_sections", {}):
            raise ValueError("Section settings changed in another window. Reload and try again.")
        disk = windows_io_path(target)
        previous = disk.read_bytes() if disk.exists() else None
        saved = save_order(disk, selected, expected=request["expected"])
        try:
            if "active" in request:
                if set(request["active"]) != set(ids):
                    raise ValueError("Active state must include every visible section exactly once.")
                current = save_active(request["input_dir"], request["active"], current, request.get("bundle_root") or None)
        except Exception:
            if previous is None:
                disk.unlink()
            else:
                atomic_write(disk, lambda stream: stream.write(previous))
            raise
        return {"saved_order": saved, "path": str(target), "sections": current}

    def order_flip_plan(self, request):
        from section_flip import plan_flip
        return plan_flip(request["bundle_root"], request["slice_id"])

    def order_flip(self, request):
        from section_flip import apply_flip
        return apply_flip(request["bundle_root"], request["slice_id"], request["plan"], self.report)

    def order_preview(self, request):
        import cv2
        import numpy as np
        self.report("reading")
        source = Path(request["source"]).resolve(strict=True)
        if source.suffix.lower() in (".tif", ".tiff"):
            image = read_single_plane(source)
        else:
            image = cv2.imdecode(np.fromfile(windows_io_path(source), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("Could not read the section preview.")
        height, width = image.shape
        return {"image": preview_data(image), "width": width, "height": height}

    def load(self, request):
        self.report("reading")
        import cv2
        import numpy as np
        import tifffile
        from skimage.exposure import equalize_adapthist

        self.state = None
        source = Path(request["source"]).resolve(strict=True)
        if request.get("plane", 0) != 0:
            raise ValueError("Select a single-channel MAX or preprocessed image for the chosen signal branch.")
        plane = 0  # Keep saved settings/cache identities compatible with earlier single-plane runs.
        from section_policy import active_ids, file_id
        if not active_ids([file_id(source.name)], source):
            raise ValueError("This section is inactive. Reactivate it in Align Sections Order before detection.")
        image = read_single_plane(source)
        self.report("preprocess")
        gray = (equalize_adapthist(image, clip_limit=0.01) * 255).astype(np.uint8)
        self.report("preview")
        original_preview = preview_data(image) if request.get("preview", True) else None
        height, width = gray.shape
        self.state = {"source": source, "source_stamp": (source.stat().st_size, source.stat().st_mtime_ns), "plane": plane, "gray": gray,
                      "width": width, "height": height, "token": uuid.uuid4().hex,
                      "records": None, "floor": None, "settings": None}
        restored, warning = None, None
        if request.get("output"):
            self.report("restore_saved")
            output = Path(os.path.abspath(request["output"]))
            from section_policy import file_id
            stem = file_id(source.name)
            entry = None
            try:
                metadata = json.loads(windows_io_path(output / "detection_live_settings.json").read_text(encoding="utf-8"))
                entry = metadata["slices"].get(stem)
                if entry is not None:
                    from detect_live_saved import restore_snapshot
                    restored = restore_snapshot(output, self.state, entry, output / ("Predictions_" + stem + ".pkl"))
                    self.state.update(records=restored["records"], floor=restored["floor"], settings=restored["inference"])
                    print(f"LOG: detect_live saved_restore=hit slice={stem} kept={len(restored['indices'])}", file=sys.stderr, flush=True)
            except FileNotFoundError:
                if entry is not None or windows_io_path(output / ("Predictions_" + stem + ".pkl")).exists():
                    warning = "Saved predictions are missing their metadata or snapshot. Detect once and save again."
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                warning = "Saved results were not restored: " + str(error)
        return {"token": self.state["token"], "width": width, "height": height,
                "image": preview_data(gray) if request.get("preview", True) else None,
                "original_image": original_preview, "saved_result": restored, "restore_warning": warning}

    def current(self, request):
        if self.state is None or request.get("token") != self.state["token"]:
            raise ValueError("The selected image changed. Reload it before continuing.")
        from section_policy import active_ids, file_id
        source = self.state["source"]
        if not active_ids([file_id(source.name)], source):
            raise ValueError("This section is inactive. Reload the folder.")
        if self.state.get("source_stamp") != (source.stat().st_size, source.stat().st_mtime_ns):
            raise ValueError("The image changed on disk. Reload before detecting or saving predictions.")
        return self.state

    def detect(self, request):
        self.report("preparing")
        import cv2
        import torch
        import sahi
        import find_neurons as detector
        from detect_qc import bbox_intensity_p90

        state = self.current(request)
        from section_policy import active_ids, file_id
        if not active_ids([file_id(state["source"].name)], state["source"]):
            raise ValueError("This section is inactive. Reload the folder.")
        state.update(records=None, floor=None, settings=None)
        model_path = Path(request["model"]).resolve(strict=True)
        output = Path(os.path.abspath(request["output"]))
        floor = float(request.get("floor", 0.1))
        tile = int(request.get("tile", 640))
        if not 0 < floor <= 1 or not 64 <= tile <= 4096 or float(request.get("tile", 640)) != tile:
            raise ValueError("Candidate confidence must be >0 and ≤1; tile size must be 64–4096.")
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        if os.environ.get("MASONJAR_DETECT_CPU", "").lower() in ("1", "true", "yes"):
            device = "cpu"
        elif device == "cpu" and torch.backends.mps.is_available():
            device = "mps"
        engine_hash = hashlib.sha256(Path(detector.__file__).read_bytes()).hexdigest()
        settings = {"model": str(model_path), "model_size": model_path.stat().st_size,
                    "model_mtime": model_path.stat().st_mtime_ns, "tile": tile,
                    "floor": floor, "plane": state["plane"], "device": device,
                    "torch": torch.__version__, "sahi": sahi.__version__,
                    "schema": 1, "engine": engine_hash, "preprocess": "adapthist-0.01"}
        identity = [str(state["source"]), state["source"].stat().st_size,
                    state["source"].stat().st_mtime_ns, settings]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = output / ".live_candidates" / (key + ".json")
        cache_warning = None
        self.report("cache_read")
        try:
            records = json.loads(windows_io_path(cache).read_text(encoding="utf-8"))["records"]
            hit = True
        except (OSError, ValueError, KeyError):
            hit = False
            model_key = json.dumps([str(model_path), settings["model_size"], settings["model_mtime"], floor, device])
            if self.model_key != model_key:
                self.report("model")
                print("LOG: detect_live stage=model", file=sys.stderr, flush=True)
                self.model = detector.AutoDetectionModel.from_pretrained(
                    model_type="yolov8", model_path=str(model_path),
                    confidence_threshold=floor, device=device)
                self.model_key = model_key
            bgr = cv2.cvtColor(state["gray"], cv2.COLOR_GRAY2BGR)
            print("LOG: detect_live stage=inference", file=sys.stderr, flush=True)
            result = sliced_prediction_with_progress(detector, bgr, self.model, tile, state["source"].name, self.report)
            print("LOG: detect_live stage=measurements", file=sys.stderr, flush=True)
            records = []
            total = len(result.object_prediction_list)
            self.report("measurements", 0, total)
            last_report = time.monotonic()
            for index, obj in enumerate(result.object_prediction_list):
                box = [float(value) for value in obj.bbox.to_xyxy()]
                ecc = detector.measure_eccentricity(box, bgr)
                intensity = bbox_intensity_p90(box, state["gray"])
                records.append(box + [float(obj.score.value), float(detector.xyxy_to_area(box)), ecc, intensity])
                now = time.monotonic()
                if index + 1 == total or now - last_report >= 0.3:
                    self.report("measurements", index + 1, total)
                    last_report = now
            self.report("cache_write")
            try:
                write_json(cache, {"records": records, "settings": settings})
            except OSError as error:
                cache_warning = "Candidate cache could not be saved. Detection results remain available for filtering and saving."
                print(f"LOG: detect_live cache_write_warning path={cache} error={error}", file=sys.stderr, flush=True)
        state.update(records=records, floor=floor, settings=settings)
        saved_params = None
        stem = state["source"].stem
        if stem.lower().endswith(".ome"):
            stem = stem[:-4]
        try:
            entry = json.loads(windows_io_path(output / "detection_live_settings.json").read_text(encoding="utf-8"))["slices"][stem]
            if entry["source"] == str(state["source"]) and entry["plane"] == state["plane"] and entry["inference"] == settings:
                saved_params = entry["params"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {"records": records, "floor": floor, "cached": hit, "device": device,
                "saved_params": saved_params, "cache_warning": cache_warning}

    def filter(self, request):
        state = self.current(request)
        if state["records"] is None:
            raise ValueError("Run detection for this image first.")
        return {"indices": filter_indices(state["records"], request["params"], state["floor"])}

    def save(self, request):
        self.report("saving")
        from find_neurons import DetectionResult

        state = self.current(request)
        indices = self.filter(request)["indices"]
        output = Path(os.path.abspath(request["output"]))
        windows_io_path(output).mkdir(parents=True, exist_ok=True)
        stem = state["source"].stem
        if stem.lower().endswith(".ome"):
            stem = stem[:-4]
        metadata_path = output / "detection_live_settings.json"
        try:
            metadata = json.loads(windows_io_path(metadata_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            metadata = {"schema": 1, "slices": {}}
        previous = metadata["slices"].get(stem)
        if previous and (previous["source"] != str(state["source"]) or previous["plane"] != state["plane"]):
            raise ValueError("This output already contains the same slice ID from another image/channel. Choose a separate output folder.")
        records = [state["records"][i] for i in indices]
        predictions = [DetectionResult([row[:4] for row in records], [row[4] for row in records], (state["height"], state["width"]))]
        target = output / ("Predictions_" + stem + ".pkl")

        # A complete pickle is always available to Count Brain, even if the
        # subsequent metadata write fails. Retrying recomputes the same output.
        atomic_write(target, lambda stream: pickle.dump(predictions, stream, protocol=pickle.HIGHEST_PROTOCOL))
        from detect_live_saved import store_snapshot
        snapshot, normalized = store_snapshot(output, state, request["params"], indices, target)
        metadata["slices"][stem] = {"source": str(state["source"]), "plane": state["plane"],
            "params": normalized, "inference": state["settings"], "count": len(indices),
            "snapshot": snapshot, "method": request.get("method", "custom")}
        write_json(metadata_path, metadata)
        # Only remove the superseded snapshot after the new metadata is durable.
        if previous and previous.get("snapshot") != snapshot:
            import re
            name = previous.get("snapshot", "")
            if re.fullmatch(r"[a-f0-9]{64}\.json", name):
                try:
                    windows_io_path(output / ".live_saved" / name).unlink(missing_ok=True)
                except OSError:
                    pass
        return {"path": str(target), "count": len(indices)}


def main():
    protocol = sys.stdout
    protocol_lock = threading.Lock()

    def send(payload):
        with protocol_lock:
            protocol.write(json.dumps(payload, allow_nan=False) + "\n")
            protocol.flush()

    session = DetectionSession()
    import cpu_runtime
    import io_fairshare
    with contextlib.redirect_stdout(sys.stderr):
        cpu_runtime.configure_cpu()
    for line in sys.stdin:
        request = {}
        progress = None
        started = time.perf_counter()
        try:
            request = json.loads(line)
            action = request.get("action")
            if action not in ("load", "detect", "filter", "save", "order_info", "order_save", "order_preview", "order_flip_plan", "order_flip"):
                raise ValueError("Unsupported viewer request.")
            progress = ProgressReporter(request["id"], action, send)
            session.report = progress.update
            progress.start()
            with contextlib.redirect_stdout(sys.stderr):
                try:
                    if action != "filter":
                        io_fairshare.activate()
                    result = getattr(session, action)(request)
                finally:
                    io_fairshare.deactivate()
            reply = {"id": request["id"], "ok": True, "result": result}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            reply = {"id": request.get("id"), "ok": False, "error": str(error)}
        finally:
            if progress is not None:
                progress.stop()
        print(f"LOG: detect_live action={request.get('action')} ok={reply['ok']} elapsed_ms={(time.perf_counter()-started)*1000:.1f}", file=sys.stderr, flush=True)
        send(reply)


if __name__ == "__main__":
    main()

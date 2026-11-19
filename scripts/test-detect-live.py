"""Interactive detection regression checks. No real project files are modified."""
import importlib.util
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
from detect_live_core import atomic_write, filter_indices, windows_io_path, write_json


class FilterTests(unittest.TestCase):
    def test_batch_area_rule_and_measurement_thresholds(self):
        rows = [[0, 0, 10, 10, .8, 100, .5, 80],
                [0, 0, 20, 10, .6, 200, .2, 100],
                [0, 0, 30, 10, .9, 300, None, None],
                [0, 0, 40, 10, .3, 400, .8, 150]]
        params = dict(confidence=.5, area=100, eccentricity=.2, intensity=90)
        self.assertEqual(filter_indices(rows, params, .1), [2])
        params.update(area=0, eccentricity=0, intensity=0)
        self.assertEqual(filter_indices(rows, params, .1), [0, 1, 2])
        self.assertEqual(filter_indices([], params, .1), [])

    def test_confidence_floor_and_invalid_values(self):
        for params in [dict(confidence=.05), dict(confidence=float("nan")),
                       dict(area=-1), dict(intensity=256), dict(eccentricity=2)]:
            with self.assertRaises(ValueError):
                filter_indices([], params, .1)

    def test_failed_serialization_preserves_complete_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "Predictions_dotted.name.pkl"
            target.write_bytes(b"previous")
            def fail(stream):
                stream.write(b"partial")
                raise ValueError("fixture failure")
            with self.assertRaises(ValueError):
                atomic_write(target, fail)
            self.assertEqual(target.read_bytes(), b"previous")
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows extended-path behavior")
    def test_long_paths_and_unc_prefix(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / ("project_" + "x" * 110) / ("run_" + "y" * 80) / ".live_candidates" / ("b" * 64 + ".json")
            self.assertGreater(len(str(target)), 260)
            try:
                write_json(target, {"records": [1]})
                write_json(target, {"records": [2]})
                self.assertEqual(json.loads(windows_io_path(target).read_text()), {"records": [2]})
            finally:
                # Embedded Python's unprefixed rmtree cannot see this long file.
                windows_io_path(target).unlink(missing_ok=True)
        unc = windows_io_path(r"\\server\share\project\result.json")
        self.assertEqual(str(unc), r"\\?\UNC\server\share\project\result.json")
        self.assertEqual(windows_io_path(unc), unc)


class ProgressTests(unittest.TestCase):
    def test_heartbeat_reports_without_advancing_progress_and_stops(self):
        import time
        from detect_live_worker import ProgressReporter
        events = []
        reporter = ProgressReporter(7, "detect", events.append, interval=.02)
        reporter.start()
        reporter.update("inference", 2, 10)
        time.sleep(.08)
        reporter.stop()
        count = len(events)
        time.sleep(.03)
        self.assertEqual(len(events), count)
        self.assertGreaterEqual(count, 3)
        self.assertTrue(all(event["event"] == "progress" and event["id"] == 7 for event in events))
        self.assertEqual(events[-1]["current"], 2)
        self.assertEqual(events[-1]["total"], 10)
        self.assertGreater(events[-1]["idle_ms"], 20)


class OrderTests(unittest.TestCase):
    def test_standalone_preview_reads_raw_image_without_clahe_or_detection(self):
        import base64
        import cv2
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "dapi.png"
            original = np.arange(20 * 30, dtype=np.uint8).reshape(20, 30)
            ok, encoded = cv2.imencode(".png", original)
            self.assertTrue(ok)
            source.write_bytes(encoded.tobytes())
            session = DetectionSession()
            with patch.object(session, "load", side_effect=AssertionError("detection load must not run")):
                result = session.order_preview(dict(source=str(source)))
            decoded = cv2.imdecode(np.frombuffer(base64.b64decode(result["image"].split(",")[1]), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            self.assertTrue(np.array_equal(decoded, original))
            self.assertIsNone(session.state)

    def test_saved_ap_metadata_custom_subset_and_conflict(self):
        import json
        from detect_live_worker import DetectionSession
        from slice_order import save_order, read_order
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_dir, dapi_dir = root / "signal", root / "dapi"
            input_dir.mkdir(); dapi_dir.mkdir(); (root / ".masonjar").mkdir()
            for name in ("dotted.name.ome.tif", "second.tif"):
                (input_dir / name).write_bytes(b"fixture")
            (dapi_dir / "alignment_session.json").write_text(json.dumps({"slices": [
                {"filename": "dotted.name.ome.tif", "ap_position": 0},
                {"slice_id": "second", "ap_position": 120},
                {"slice_id": "invalid", "ap_position": None},
            ]}))
            request = dict(input_dir=str(input_dir), dapi_dir=str(dapi_dir), bundle_root=str(root))
            session = DetectionSession()
            info = session.order_info(request)
            self.assertEqual(info["ap_positions"], {"dotted.name": 0, "second": 120})
            path = root / ".masonjar" / "slice_order.json"
            save_order(path, ["hidden", "dotted.name", "second"])
            info = session.order_info(request)
            saved = session.order_save(dict(request, slice_ids=["second", "dotted.name"], expected=info["saved_order"]))
            self.assertEqual(saved["saved_order"], ["hidden", "second", "dotted.name"])
            with self.assertRaisesRegex(ValueError, "another window"):
                session.order_save(dict(request, slice_ids=["dotted.name", "second"], expected=info["saved_order"]))
            self.assertEqual(read_order(path), saved["saved_order"])
            with self.assertRaisesRegex(ValueError, "exactly once"):
                session.order_save(dict(request, slice_ids=["second"], expected=saved["saved_order"]))

    def test_missing_ap_and_corrupt_order_are_reported(self):
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "single.tif").write_bytes(b"fixture")
            (root / ".pfa_viewer_slice_order.json").write_text("{broken")
            request = dict(input_dir=str(root), dapi_dir=str(root))
            session = DetectionSession()
            result = session.order_info(request)
            self.assertEqual(result["ap_positions"], {})
            self.assertTrue(result["warnings"])
            with self.assertRaises(ValueError):
                session.order_save(dict(request, slice_ids=["single"], expected=[]))
            self.assertEqual((root / ".pfa_viewer_slice_order.json").read_text(), "{broken")


RUNTIME_AVAILABLE = all(importlib.util.find_spec(name) for name in ("skimage", "sahi", "torch"))


@unittest.skipUnless(RUNTIME_AVAILABLE, "Requires the existing PFA Python dependencies")
class SessionTests(unittest.TestCase):
    def test_tile_progress_preserves_calls_results_and_restores_model(self):
        from types import SimpleNamespace
        from sahi.slicing import get_slice_bboxes
        from detect_live_worker import sliced_prediction_with_progress
        image = np.zeros((64, 80, 3), dtype=np.uint8)
        total = len(get_slice_bboxes(64, 80, 32, 32, overlap_height_ratio=.1, overlap_width_ratio=.1))
        calls, events = [], []
        original = lambda image: calls.append(image) or "inference result"
        model = SimpleNamespace(perform_inference=original)
        result = object()
        def predict(image, model, tile, label):
            for _ in range(total + 1):  # SAHI also runs a final full-image pass.
                self.assertEqual(model.perform_inference(image), "inference result")
            return result
        detector = SimpleNamespace(_call_get_sliced_prediction=predict)
        actual = sliced_prediction_with_progress(detector, image, model, 32, "fixture", lambda *event: events.append(event))
        self.assertIs(actual, result)
        self.assertEqual(len(calls), total + 1)
        self.assertIs(model.perform_inference, original)
        self.assertIn(("inference", total, total), events)
        self.assertIn(("postprocess",), events)
        detector._call_get_sliced_prediction = lambda *args: (_ for _ in ()).throw(ValueError("fixture"))
        with self.assertRaises(ValueError):
            sliced_prediction_with_progress(detector, image, model, 32, "fixture", lambda *event: None)
        self.assertIs(model.perform_inference, original)

    def test_cache_navigation_filters_pickle_and_channel_collision(self):
        import tifffile
        import find_neurons as detector
        from detect_live_worker import DetectionSession
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "slice.with.dots.tif"
            tifffile.imwrite(source, np.arange(64 * 80, dtype=np.uint16).reshape(64, 80))
            model = root / "fixture.pt"
            model.write_bytes(b"mock weights")
            output = root / "predictions"
            session = DetectionSession()
            loaded = session.load(dict(source=str(source)))
            self.assertEqual((loaded["height"], loaded["width"]), (64, 80))
            self.assertTrue(loaded["image"].startswith("data:image/png;base64,"))
            import base64
            import cv2
            original = cv2.imdecode(np.frombuffer(base64.b64decode(loaded["original_image"].split(",")[1]), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            self.assertTrue(np.array_equal(original, (np.arange(64 * 80, dtype=np.uint16).reshape(64, 80) / 256).astype(np.uint8)))
            processed = cv2.imdecode(np.frombuffer(base64.b64decode(loaded["image"].split(",")[1]), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            self.assertTrue(np.array_equal(processed, session.state["gray"]))
            self.assertFalse(np.array_equal(original, processed))
            with self.assertRaises(ValueError):
                session.filter(dict(token="stale", params={}))
            objects = [SimpleNamespace(bbox=SimpleNamespace(to_xyxy=lambda: [10, 10, 30, 30]),
                                       score=SimpleNamespace(value=.8))]
            request = dict(token=loaded["token"], model=str(model), output=str(output), floor=.1, tile=640)
            with patch.object(detector.AutoDetectionModel, "from_pretrained", return_value=SimpleNamespace(perform_inference=lambda *args, **kwargs: None)) as create_model, \
                 patch.object(detector, "_call_get_sliced_prediction", return_value=SimpleNamespace(object_prediction_list=objects)) as infer:
                result = session.detect(request)
                self.assertFalse(result["cached"])
                params = dict(confidence=.5, area=200, eccentricity=0, intensity=0)
                # Compare the cached-measurement path with the existing batch filter.
                import cv2
                bgr = cv2.cvtColor(session.state["gray"], cv2.COLOR_GRAY2BGR)
                expected, _, _ = detector.screen_predictions(objects, 200, eccentricity_threshold=0, image=bgr)
                self.assertEqual(len(filter_indices(result["records"], params, .1)), len(expected))
                saved = session.save(dict(token=loaded["token"], output=str(output), params=params))
                self.assertEqual(Path(saved["path"]).name, "Predictions_slice.with.dots.pkl")
                with open(saved["path"], "rb") as stream:
                    predictions = pickle.load(stream)
                self.assertIsInstance(predictions[0], detector.DetectionResult)
                self.assertEqual(predictions[0].image_dimensions, (64, 80))
                self.assertEqual(len(predictions[0].boxes), saved["count"])
                loaded2 = session.load(dict(source=str(source)))
                with self.assertRaises(ValueError):
                    session.save(dict(token=loaded["token"], output=str(output), params=params))
                request["token"] = loaded2["token"]
                self.assertTrue(session.detect(request)["cached"])
                self.assertEqual(infer.call_count, 1)
                self.assertEqual(create_model.call_count, 1)
                self.assertEqual(session.detect(request)["saved_params"], params)
                for candidate_cache in windows_io_path(output / ".live_candidates").glob("*.json"):
                    candidate_cache.unlink()
                with patch("detect_live_worker.write_json", side_effect=OSError("fixture cache rename failure")):
                    uncached = session.detect(request)
                self.assertTrue(uncached["cache_warning"])
                self.assertEqual(len(session.filter(dict(token=loaded2["token"], params=params))["indices"]), len(expected))
                session.save(dict(token=loaded2["token"], output=str(output), params=params))
                session.state["plane"] = 1
                with self.assertRaises(ValueError):
                    session.save(dict(token=loaded2["token"], output=str(output), params=params))

    def test_no_detections_is_count_compatible(self):
        import tifffile
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "zero.tif"
            tifffile.imwrite(source, np.zeros((16, 20), dtype=np.uint8))
            session = DetectionSession()
            loaded = session.load(dict(source=str(source)))
            session.state.update(records=[], floor=.1, settings={})
            result = session.save(dict(token=loaded["token"], output=str(Path(folder) / "out"), params={}))
            with open(result["path"], "rb") as stream:
                predictions = pickle.load(stream)
            self.assertEqual(len(predictions), 1)
            self.assertEqual(predictions[0].boxes, [])
            self.assertEqual(result["count"], 0)

    def test_multiple_planes_rejected_and_failed_load_invalidated(self):
        import tifffile
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "channels.tif"
            planes = np.zeros((2, 20, 30), dtype=np.uint8)
            planes[1, 4:10, 4:10] = 200
            tifffile.imwrite(source, planes, metadata={"axes": "CYX"}, photometric="minisblack")
            session = DetectionSession()
            with self.assertRaisesRegex(ValueError, "multiple channels or planes"):
                session.load(dict(source=str(source)))
            with self.assertRaisesRegex(ValueError, "single-channel"):
                session.load(dict(source=str(source), plane=1))
            self.assertIsNone(session.state)

    def test_bulk_load_skips_preview_encoding(self):
        import tifffile
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "single.tif"
            tifffile.imwrite(source, np.zeros((20, 30), dtype=np.uint8))
            with patch("detect_live_worker.preview_data", side_effect=AssertionError("unexpected preview encoding")):
                result = DetectionSession().load(dict(source=str(source), preview=False))
            self.assertIsNone(result["image"])
            self.assertIsNone(result["original_image"])

    def test_saved_predictions_reach_count_brain(self):
        import csv
        import tifffile
        from detect_live_worker import DetectionSession
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "count.dotted.slice.tif"
            tifffile.imwrite(source, np.zeros((32, 40), dtype=np.uint8))
            session = DetectionSession()
            loaded = session.load(dict(source=str(source)))
            session.state.update(records=[[5, 5, 15, 15, .9, 100, .7, 200]], floor=.1, settings={})
            session.save(dict(token=loaded["token"], output=str(root / "predictions"),
                              params=dict(confidence=.5, area=0, eccentricity=.2, intensity=0)))
            with open(ROOT / "csv" / "structure_map.pkl", "rb") as stream:
                regions = pickle.load(stream)
            region_id = next(key for key, info in regions.items() if info["acronym"] == "ACAd")
            annotations = root / "align"
            annotations.mkdir()
            with open(annotations / "Annotation_count.dotted.slice.pkl", "wb") as stream:
                pickle.dump(np.full((32, 40), region_id, dtype=np.uint32), stream)
            output = root / "counts"
            output.mkdir()
            result = subprocess.run([sys.executable, str(ROOT / "py" / "count.py"),
                                     "-p", str(root / "predictions"), "-a", str(annotations),
                                     "-o", str(output), "-m", str(ROOT / "csv" / "structure_map.pkl"), "-l"],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with open(output / "count_results.csv", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertTrue(any(row and row[0] == "ACAd" and row[2] == "1280" and row[3] == "1" for row in rows), rows)


if __name__ == "__main__":
    unittest.main()

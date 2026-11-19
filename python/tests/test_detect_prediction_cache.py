import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "py"))
from detect_prediction_cache import cache_key, cached_prediction


class CandidateCacheTests(unittest.TestCase):
    def test_hit_preserves_candidates_without_recomputing(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            candidates = [{"box": [1, 2, 3, 4], "score": .7}]
            result, hit = cached_prediction(folder, "key", lambda: candidates)
            self.assertFalse(hit)
            result, hit = cached_prediction(folder, "key",
                lambda: self.fail("inference must not run on cache hit"))
            self.assertTrue(hit)
            self.assertEqual(result, candidates)

    def test_source_model_and_inference_settings_invalidate(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            source, model = Path(folder) / "image", Path(folder) / "model"
            source.write_bytes(b"image")
            model.write_bytes(b"model")
            settings = {"confidence": .5, "tile": 640}
            initial = cache_key(source, model, settings)
            self.assertNotEqual(initial, cache_key(source, model, {**settings, "tile": 896}))
            self.assertNotEqual(initial, cache_key(source, model, {**settings, "confidence": .6}))
            source.write_bytes(b"changed-image")
            self.assertNotEqual(initial, cache_key(source, model, settings))
            before = cache_key(source, model, settings)
            model.write_bytes(b"changed-model")
            self.assertNotEqual(before, cache_key(source, model, settings))

    def test_corrupt_cache_recomputes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            (Path(folder) / "key.pkl").write_bytes(b"broken")
            result, hit = cached_prediction(folder, "key", lambda: [1, 2])
            self.assertFalse(hit)
            self.assertEqual(result, [1, 2])

    def test_failed_cache_write_keeps_detection_result(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            blocked = Path(folder) / "file"
            blocked.write_bytes(b"file")
            result, hit = cached_prediction(blocked, "key", lambda: [1])
            self.assertEqual(result, [1])
            self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()

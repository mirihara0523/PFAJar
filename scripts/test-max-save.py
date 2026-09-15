"""Regression tests using the application's actual image libraries."""
import os
os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import max as maximum


class MaxSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.output = self.root / "output"
        self.input.mkdir()
        self.output.mkdir()
        self.plane = np.arange(80, dtype=np.uint16).reshape(8, 10)

    def image(self, name, data=None):
        p = self.input / name
        tifffile.imwrite(p, self.plane if data is None else data)
        return p

    def run_max(self):
        return maximum.main(["-i", str(self.input), "-o", str(self.output), "-g", "False"])

    def test_dotted_names_and_pixels(self):
        self.image("sample.01.tif")
        self.image("sample.02.tiff", self.plane + 1)
        self.assertEqual(self.run_max(), 0)
        np.testing.assert_array_equal(tifffile.imread(self.output / "sample.01.tif"), self.plane)
        np.testing.assert_array_equal(tifffile.imread(self.output / "sample.02.tif"), self.plane + 1)

    def test_projection_pixels(self):
        stack = np.stack([self.plane, self.plane + 100, self.plane + 3])
        self.image("stack.tif", stack)
        self.assertEqual(self.run_max(), 0)
        np.testing.assert_array_equal(tifffile.imread(self.output / "stack.tif"), stack.max(axis=0))

    def test_encoder_false_preserves_existing(self):
        p = self.image("slice.tif")
        dest = self.output / "slice.tif"
        dest.write_bytes(b"existing")
        with patch.object(maximum.cv2, "imwrite", return_value=False):
            self.assertFalse(maximum.process_file(str(p), str(self.output)))
        self.assertEqual(dest.read_bytes(), b"existing")
        self.assertEqual(list(self.output.iterdir()), [dest])

    def test_replace_failure_preserves_existing(self):
        p = self.image("slice.tif")
        dest = self.output / "slice.tif"
        dest.write_bytes(b"existing")
        with patch.object(maximum.os, "replace", side_effect=PermissionError("locked")):
            self.assertFalse(maximum.process_file(str(p), str(self.output)))
        self.assertEqual(dest.read_bytes(), b"existing")
        self.assertEqual(list(self.output.iterdir()), [dest])

    def test_partial_failure(self):
        self.image("good.tif")
        (self.input / "bad.tif").write_bytes(b"invalid TIFF")
        self.assertEqual(self.run_max(), 1)
        report = json.loads((self.output / "run_manifest.json").read_text())
        self.assertFalse(report["ok"])
        self.assertEqual(report["written"], 1)
        self.assertEqual(report["failed_files"], ["bad.tif"])

    def test_all_failure(self):
        (self.input / "bad.tif").write_bytes(b"invalid TIFF")
        self.assertEqual(self.run_max(), 1)

    def test_empty(self):
        self.assertEqual(self.run_max(), 1)

    def test_duplicate_output_rejected_before_write(self):
        self.image("same.tif")
        self.image("same.tiff")
        self.assertEqual(self.run_max(), 1)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

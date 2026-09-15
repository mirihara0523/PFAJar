"""Two-pass CZI extraction design test; no real CZI or project files needed."""
import os, sys, tempfile
os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
from pathlib import Path
import numpy as np
import tifffile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract
czi_extract.np, czi_extract.tiff = np, tifffile

rng = np.random.default_rng(19)
planes = [rng.integers(0, 65536, (48, 64), dtype=np.uint16) for _ in range(7)]
planes[5][8, 11] = 65535  # makes this plane the brightest preview candidate

def source():
    # A fresh iterator models a CZI that can be reopened for the second pass.
    return (p.copy() for p in planes)

legacy_stack = np.stack(list(source()), axis=0)
legacy_peak = int(legacy_stack.max())
legacy_preview = czi_extract._preview_plane_from_stack(list(source()), list(range(len(planes))))

first_pass = list(source())
two_pass_peak = max(int(p.max()) for p in first_pass)
two_pass_preview = czi_extract._preview_plane_from_stack(first_pass, list(range(len(planes))))

with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "two-pass.tif"
    count = czi_extract.write_pipeline_tiff_iter(out, source(), 8, scale_max=two_pass_peak)
    with tifffile.TiffFile(out) as tif:
        written = np.stack([page.asarray() for page in tif.pages])

expected = (legacy_stack.astype(np.float64) * 255.0 / legacy_peak).astype(np.uint8)
assert count == len(planes)
assert two_pass_peak == legacy_peak
assert np.array_equal(written, expected)
assert np.array_equal(two_pass_preview, legacy_preview)
print("CZI two-pass design: peak, TIFF pages, scaling, and preview selection passed")

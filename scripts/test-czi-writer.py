"""Synthetic checks for the CZI page-wise TIFF writer."""
import os, sys, tempfile
os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
from pathlib import Path
import numpy as np
import tifffile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract
czi_extract.np, czi_extract.tiff = np, tifffile

planes = [np.full((20, 24), z * 1000, dtype=np.uint16) for z in range(4)]
planes[2][3, 4] = 65535
all_values = np.arange(65536, dtype=np.uint16)
for peak in (1, 2, 255, 4095, 6327, 10586, 16383, 65535):
    baseline = czi_extract._uint16_to_uint8_stack_linear(all_values, scale_max=peak)
    lut = czi_extract._uint16_to_uint8_lut(peak)
    actual = czi_extract.coerce_stack_depth(
        all_values, 8, scale_max=peak, uint16_lut=lut,
    )
    assert np.array_equal(actual, baseline)
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "stream.tif"
    count = czi_extract.write_pipeline_tiff_iter(path, iter(planes), 16)
    with tifffile.TiffFile(path) as tif:
        assert len(tif.pages) == count == 4
        actual = np.stack([p.asarray() for p in tif.pages])
    expected = np.stack(planes)
    assert np.array_equal(actual, expected)
    path8 = Path(d) / "stream8.tif"
    czi_extract.write_pipeline_tiff_iter(path8, iter(planes), 8, scale_max=65535)
    with tifffile.TiffFile(path8) as tif:
        actual8 = np.stack([p.asarray() for p in tif.pages])
    expected8 = (expected.astype(np.float64) * 255 / 65535).astype(np.uint8)
    assert np.array_equal(actual8, expected8)
print("CZI sequential TIFF writer: pages, pixels, and scaling passed")

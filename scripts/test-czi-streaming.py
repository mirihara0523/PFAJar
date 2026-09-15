"""Synthetic validation of the CZI plane-reduction building block."""
import os, sys
os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract
czi_extract.np = np

planes = [np.full((64, 80), i, dtype=np.uint16) for i in range(12)]
planes[4][7, 9] = 5000
expected = np.max(np.stack(planes), axis=0)
actual = czi_extract.max_project_plane_iter((p for p in planes))
assert np.array_equal(actual, expected)
assert actual.dtype == np.uint16
try:
    czi_extract.max_project_plane_iter([np.zeros((2, 2)), np.zeros((3, 2))])
except ValueError:
    pass
else:
    raise AssertionError("inconsistent plane dimensions must fail")
print("CZI streaming reducer: pixel and shape checks passed")

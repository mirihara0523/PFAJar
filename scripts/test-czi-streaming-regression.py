"""Regression checks for CZI extract's one-plane-at-a-time TIFF path."""
from pathlib import Path
import tempfile
import sys
import os
os.environ.setdefault("MASONJAR_IO_FAIRSHARE", "0")
import numpy as np
import tifffile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract
czi_extract.tiff = tifffile
czi_extract.np = np
write_pipeline_tiff_iter = czi_extract.write_pipeline_tiff_iter

root = Path(tempfile.mkdtemp(prefix="masonjar-stream-reg-"))
planes = [np.full((32, 40), z, dtype=np.uint16) for z in range(4)]
state = {"live": 0, "max_live": 0, "yielded": 0}
def source():
    for plane in planes:
        state["live"] += 1
        state["max_live"] = max(state["max_live"], state["live"])
        state["yielded"] += 1
        yield plane
        state["live"] -= 1

out = root / "stream.tif"
assert write_pipeline_tiff_iter(out, source(), 16) == 4
with tifffile.TiffFile(out) as tf:
    assert len(tf.pages) == 4
    assert all(np.array_equal(tf.pages[i].asarray(), planes[i]) for i in range(4))
assert state["yielded"] == 4 and state["max_live"] == 1
source_text = Path(__file__).resolve().parents[1].joinpath("py", "czi_extract.py").read_text(encoding="utf-8")
segment = source_text[source_text.index("def extract_z_stack("):source_text.index("def slice_id_for_scene(")]
assert "planes.append(" not in segment
print("CZI streaming regression: pages, pixels, one-plane lifetime, and no accumulation passed")

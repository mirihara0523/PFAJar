"""Regression tests for bounded-cost CZI preview focal-plane selection."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract as c

c.np, c.tiff = np, tifffile

# Broad focal signal must retain the same winner while each routine score uses
# the bounded grid sample rather than a full 1024x1024 plane.
planes = [
    np.full((1024, 1024), value, dtype=np.uint16)
    for value in (10, 30, 60)
]
sample_score, sample_pixels = c._preview_score_sample(planes[-1])
assert sample_score == 60.0
assert sample_pixels <= c.PREVIEW_SCORE_SAMPLE_PIXELS

scores_seen = []
real_percentile = np.percentile

def record_percentile(values, *args, **kwargs):
    scores_seen.append(np.asarray(values).size)
    return real_percentile(values, *args, **kwargs)

with tempfile.TemporaryDirectory() as tmp:
    previews = []
    with patch.object(c, "read_plane", lambda *_args: planes[_args[2]].copy()), \
         patch.object(c, "_write_seam_grid_sidecar"), \
         patch.object(c, "write_dapi_preview_pair", lambda _b, _s, p, _v: previews.append(p.copy())), \
         patch.object(c.np, "percentile", record_percentile):
        c.extract_z_stack(
            None, 0, 0, [0, 1, 2], Path(tmp) / "stack.tif", None, 0.05,
            "sample", Path(tmp), cfg={"bit_depth_by_role": {"dapi": 16}}, role_key="dapi",
        )
    assert np.array_equal(previews[0], planes[-1])

assert scores_seen
assert max(scores_seen) <= c.PREVIEW_SCORE_SAMPLE_PIXELS, scores_seen

# Near sampled scores use historical full-resolution p99 before choosing.
near_a = np.full((1024, 1024), 10, dtype=np.uint16)
near_b = near_a.copy()
near_a[:22, :] = 100
near_b[:22, :] = 99
near_seen = []

def record_near(values, *args, **kwargs):
    near_seen.append(np.asarray(values).size)
    return real_percentile(values, *args, **kwargs)

with tempfile.TemporaryDirectory() as tmp:
    previews = []
    with patch.object(c, "read_plane", lambda *_args: [near_a, near_b][_args[2]].copy()), \
         patch.object(c, "_write_seam_grid_sidecar"), \
         patch.object(c, "write_dapi_preview_pair", lambda _b, _s, p, _v: previews.append(p.copy())), \
         patch.object(c.np, "percentile", record_near):
        c.extract_z_stack(
            None, 0, 0, [0, 1], Path(tmp) / "near.tif", None, 0.05,
            "near", Path(tmp), cfg={"bit_depth_by_role": {"dapi": 16}}, role_key="dapi",
        )
    assert np.array_equal(previews[0], near_a)

assert any(size > c.PREVIEW_SCORE_SAMPLE_PIXELS for size in near_seen), near_seen
print("CZI preview selection: bounded sampled p99, full-resolution near-tie fallback passed")

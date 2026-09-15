"""Regression coverage for exact and opt-in sampled uint16 peak scans."""
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


def run(mode, sample_plane=None, *, sample_raises=False):
    planes = [
        np.array([[0, 10, 20], [30, 40, 50]], dtype=np.uint16),
        np.array([[60, 70, 80], [90, 100, 65535]], dtype=np.uint16),
        np.array([[110, 120, 130], [140, 150, 160]], dtype=np.uint16),
    ]
    full_reads, sample_calls, logs = [], [], []

    def read_full(_, __, z, ___):
        full_reads.append(z)
        return planes[z].copy()

    def read_sample(_, __, z, ___, *, sample_scale, allow_tile_composite):
        sample_calls.append((z, sample_scale, allow_tile_composite))
        if sample_raises:
            raise RuntimeError("sample read unavailable")
        return sample_plane(planes[z]).copy()

    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "stack.tif"
        cfg = {
            "bit_depth_by_role": {"signal_somata": 8},
            "uint16_peak_scan_mode": mode,
            "uint16_peak_sample_scale": 0.05,
        }
        with patch.object(c, "read_plane", read_full), \
             patch.object(c, "read_czi_plane", read_sample), \
             patch.object(c, "_write_seam_grid_sidecar"), \
             patch.object(c, "emit_log", logs.append):
            c.extract_z_stack(
                object(), 0, 0, [0, 1, 2], out, None, 0.05, "sample",
                Path(directory), cfg=cfg, role_key="signal_somata",
            )
        with tifffile.TiffFile(out) as tif:
            output = np.stack([page.asarray() for page in tif.pages])
    return planes, output, full_reads, sample_calls, logs


planes, exact, full, samples, logs = run("exact", lambda p: p)
expected = (np.stack(planes).astype(np.uint32) * 255 // 65535).astype(np.uint8)
assert np.array_equal(exact, expected)
assert full == [0, 1, 2, 0, 1, 2] and samples == []
assert any("mode=exact" in line for line in logs)

# Representative sample: the scaled plane retains the true stack peak, so the
# approximate output must be byte-for-byte equal to exact mode while avoiding
# the first set of full-res reads.
planes, sampled_equal, full, samples, logs = run(
    "approximate_sample", lambda p: p[1:, 1:]
)
assert np.array_equal(sampled_equal, exact)
assert full == [0, 1, 2]
assert samples == [(0, 0.05, False), (1, 0.05, False), (2, 0.05, False)]
assert any("mode=approximate_sample scale=0.05" in line for line in logs)

# A deliberately sparse hot pixel absent from the sample is the expected risk:
# output differs and its actual hot pixel clips. This guards against claiming
# that approximate mode has exact-output semantics.
_, sampled_sparse, _, _, _ = run("approximate_sample", lambda p: p[:, :2])
assert not np.array_equal(sampled_sparse, exact)
assert sampled_sparse[1, 1, 2] == 255
assert sampled_sparse[2, 1, 2] == 255 and exact[2, 1, 2] == 0

# Unsupported scaled reads fall back to a full first pass, retaining exact output.
_, fallback, full, samples, logs = run(
    "approximate_sample", lambda p: p, sample_raises=True
)
assert np.array_equal(fallback, exact)
assert full == [0, 1, 2, 0, 1, 2] and len(samples) == 3
assert sum("sample_fallback=exact" in line for line in logs) == 3

assert c.uint16_peak_scan_settings({}) == ("exact", 0.05)
assert c.uint16_peak_scan_settings({"uint16_peak_scan_mode": "unknown"}) == ("exact", 0.05)
assert c.uint16_peak_scan_settings({"uint16_peak_scan_mode": "approximate_sample", "uint16_peak_sample_scale": 2}) == ("approximate_sample", 1.0)
print("PASS exact peak, sampled equal peak, sparse-peak risk, sample fallback, settings")

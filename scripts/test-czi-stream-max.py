"""Verify direct CZI streaming MAX has the exact legacy TIFF-reread pixels."""
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
raw_planes = [
    np.array([[10, 400], [2000, 30]], dtype=np.uint16),
    np.array([[3000, 200], [100, 25]], dtype=np.uint16),
    np.array([[15, 1000], [100, 4095]], dtype=np.uint16),
]

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    stack_path = c.max_input_dir(root, "signal_somata") / "dotted.slice.tif"
    staged = c.stream_max_stage_path(root / ".masonjar" / "stream-max" / "run", "signal_somata", "dotted.slice")
    with patch.object(c, "read_plane", lambda _czi, _scene, z, _channel: raw_planes[z].copy()), \
         patch.object(c, "_write_seam_grid_sidecar"):
        c.extract_z_stack(
            None, 0, 0, [0, 1, 2], stack_path, None, 0.05,
            "dotted.slice", root,
            cfg={"bit_depth_by_role": {"signal_somata": 8}},
            role_key="signal_somata", stream_max_path=staged,
        )
    assert staged.is_file()
    legacy = root / "legacy-max.tif"
    c.max_project_file(stack_path, legacy, bit_depth=8)
    np.testing.assert_array_equal(tifffile.imread(staged), tifffile.imread(legacy))

    # Normal run publication atomically promotes the private staged MAX and
    # preserves the existing output directory/manifest contract.
    rel = c.run_max_for_role_key(
        root,
        "signal_somata",
        ["dotted.slice"],
        {"bit_depth_by_role": {"signal_somata": 8}},
        staged_max={"dotted.slice": staged},
    )
    published = root / "data" / "counting" / "03_max" / rel / "dotted.slice.tif"
    assert published.is_file() and not staged.exists()
    np.testing.assert_array_equal(tifffile.imread(published), tifffile.imread(legacy))

print("CZI streaming MAX: converted-plane pixels and staged publication match TIFF reread")

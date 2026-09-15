"""max.py projects z-stacks over the stack axis and passes single planes through.

Guards two issues: a 2-D single-plane image must not be collapsed along a
spatial axis (np.argmin), and only real TIFFs should be processed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

PY_DIR = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(PY_DIR))

pytest.importorskip("cv2")

import max as maxmod  # noqa: E402


def _run_in_dir(in_dir: Path, name: str, out_dir: Path) -> np.ndarray | None:
    cwd = os.getcwd()
    os.chdir(in_dir)
    try:
        ok = maxmod.process_file(name, str(out_dir))
    finally:
        os.chdir(cwd)
    assert ok is True
    out = out_dir / (Path(name).stem + ".tif")
    return tifffile.imread(str(out))


def test_zstack_projects_over_stack_axis(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    # (Z=5, H=8, W=10) stack; max over Z should keep (8, 10).
    stack = np.zeros((5, 8, 10), dtype=np.uint8)
    stack[2, 3, 4] = 200
    stack[4, 7, 9] = 150
    tifffile.imwrite(str(in_dir / "M528_s001.tif"), stack)

    result = _run_in_dir(in_dir, "M528_s001.tif", out_dir)
    assert result.shape == (8, 10)
    assert result[3, 4] == 200
    assert result[7, 9] == 150


def test_streaming_projection_matches_numpy(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    stack = np.zeros((4, 8, 10), dtype=np.uint16)
    stack[1, 2, 3] = 500
    stack[3, 6, 8] = 700
    path = in_dir / "stream.tif"
    tifffile.imwrite(str(path), stack, photometric="minisblack")
    result = maxmod._stream_max_first_axis(str(path))
    assert result.dtype == stack.dtype
    np.testing.assert_array_equal(result, stack.max(axis=0))


def test_single_plane_passes_through(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    plane = np.arange(8 * 10, dtype=np.uint8).reshape(8, 10)
    tifffile.imwrite(str(in_dir / "M528_s002.tif"), plane)

    result = _run_in_dir(in_dir, "M528_s002.tif", out_dir)
    assert result.shape == (8, 10)
    assert np.array_equal(result, plane)

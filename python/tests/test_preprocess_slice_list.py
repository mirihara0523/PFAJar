"""sharpen.py / top_hat.py must read the run slice list as JSON (not one-per-line).

Project runs write ``{"slice_ids": [...]}``; the old line-based reader turned
that JSON into bogus stems (e.g. ``{``) and processed zero files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

PY_DIR = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(PY_DIR))

import sharpen  # noqa: E402
import top_hat  # noqa: E402

MODULES = pytest.mark.parametrize("mod", [sharpen, top_hat])


def _make_tifs(d: Path, stems: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        tifffile.imwrite(str(d / f"{stem}.tif"), np.zeros((4, 4), dtype=np.uint8))


@MODULES
def test_json_slice_ids_object(mod, tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    _make_tifs(in_dir, ["M528_s001", "M528_s002", "M528_s003"])
    slice_list = tmp_path / "run_slice_list.json"
    slice_list.write_text(json.dumps({"slice_ids": ["M528_s002", "M528_s003"]}))

    files = mod.list_input_files(in_dir, str(slice_list))
    names = sorted(p.stem for p in files)
    assert names == ["M528_s002", "M528_s003"], names


@MODULES
def test_json_bare_array(mod, tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    _make_tifs(in_dir, ["a", "b"])
    slice_list = tmp_path / "list.json"
    slice_list.write_text(json.dumps(["a"]))
    files = mod.list_input_files(in_dir, str(slice_list))
    assert sorted(p.stem for p in files) == ["a"]


@MODULES
def test_line_based_fallback(mod, tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    _make_tifs(in_dir, ["x", "y", "z"])
    slice_list = tmp_path / "list.txt"
    slice_list.write_text("x\nz\n")
    files = mod.list_input_files(in_dir, str(slice_list))
    assert sorted(p.stem for p in files) == ["x", "z"]


@MODULES
def test_ome_tif_stem(mod, tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(in_dir / "M528_s001.ome.tif"), np.zeros((4, 4), dtype=np.uint8))
    slice_list = tmp_path / "run_slice_list.json"
    slice_list.write_text(json.dumps({"slice_ids": ["M528_s001"]}))
    files = mod.list_input_files(in_dir, str(slice_list))
    assert len(files) == 1
    assert files[0].name == "M528_s001.ome.tif"


@MODULES
def test_no_slice_list_returns_all(mod, tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    _make_tifs(in_dir, ["one", "two"])
    files = mod.list_input_files(in_dir, None)
    assert sorted(p.stem for p in files) == ["one", "two"]

"""Tests for py/region_config.py (Isolate Regions wizard config)."""

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from region_config import (  # noqa: E402
    build_output_targets,
    children_for_target,
    is_layer_structure,
    load_intensity_config,
)


def _mini_structure_map():
    # parent area 10 -> layer child 11, sibling area 20
    return {
        np.uint32(10): {
            "name": "Primary visual area",
            "acronym": "VISp",
            "id_path": "997/10",
        },
        np.uint32(11): {
            "name": "Layer 2/3",
            "acronym": "VISp2/3",
            "id_path": "997/10/11",
        },
        np.uint32(20): {
            "name": "Somatosensory areas",
            "acronym": "SSp",
            "id_path": "997/20",
        },
    }


def test_load_intensity_config(tmp_path):
    cfg_path = tmp_path / "intensity_run_config.json"
    cfg_path.write_text(
        '{"selected_region_ids":[10,20],"include_layers":false,"whole":false,'
        '"use_dapi":true,"input_dir":"/in","annotation_dir":"/a",'
        '"output_dir":"/out","dapi_dir":"/d","slice_list":""}',
        encoding="utf-8",
    )
    cfg = load_intensity_config(cfg_path)
    assert cfg.selected_region_ids == [10, 20]
    assert cfg.include_layers is False
    assert cfg.use_dapi is True


def test_build_output_targets_no_layers():
    sm = _mini_structure_map()
    targets = build_output_targets(sm, [10], include_layers=False)
    assert targets[10] == "VISp"
    assert 11 not in targets


def test_build_output_targets_with_layers():
    sm = _mini_structure_map()
    targets = build_output_targets(sm, [10], include_layers=True)
    assert 11 in targets
    assert targets[11] == "VISp2/3"


def test_children_for_target_modes():
    sm = _mini_structure_map()
    assert children_for_target(sm, 10, include_layers=True) == [10]
    kids = children_for_target(sm, 10, include_layers=False)
    assert 10 in kids and 11 in kids


def test_is_layer_structure():
    sm = _mini_structure_map()
    assert is_layer_structure(sm, 11) is True
    assert is_layer_structure(sm, 10) is False

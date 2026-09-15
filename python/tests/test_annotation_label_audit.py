"""Tests for py/annotation_label_audit.py."""

import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from annotation_label_audit import (  # noqa: E402
    audit_align_leaf,
    audit_label_array,
    write_audit_cache,
)
from structure_catalog import load_catalog  # noqa: E402

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"
_MAP_PATH = _REPO_ROOT / "csv" / "structure_map.pkl"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


@pytest.fixture(scope="module")
def structure_map():
    with _MAP_PATH.open("rb") as f:
        return pickle.load(f)


def test_mixed_st_levels_detected(catalog, structure_map):
    vis = catalog["by_acronym"]["VIS"]["id"]
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    label = np.zeros((4, 4), dtype=np.uint32)
    label[0, 0] = vis
    label[1, 1] = visp4
    result = audit_label_array(label, catalog, structure_map, None)
    assert "mixed_st_levels" in result["issues"]


def test_layer_on_coarse_parcellation(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    label = np.full((4, 4), visp4, dtype=np.uint32)
    entry = {"tier_id": "areas", "st_level": None}
    result = audit_label_array(label, catalog, structure_map, entry)
    assert "layer_on_coarse_parcellation" in result["issues"]


def test_audit_align_leaf_and_cache(tmp_path, catalog, structure_map):
    leaf = tmp_path / "align"
    leaf.mkdir()
    meta_dir = leaf / ".masonjar"
    meta_dir.mkdir()
    sid = "M528_s061"
    vis = catalog["by_acronym"]["VIS"]["id"]
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    label = np.zeros((4, 4), dtype=np.uint32)
    label[0, 0] = vis
    label[1, 1] = visp4
    with (leaf / f"Annotation_{sid}.pkl").open("wb") as f:
        pickle.dump(label, f)
    audit = audit_align_leaf(leaf, catalog, structure_map)
    assert audit["summary"]["any_issues"]
    assert audit["summary"]["slices_with_issues"] == 1
    cache_path = write_audit_cache(leaf, audit)
    assert cache_path.is_file()

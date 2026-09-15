"""Tests for Adjust overlay coloring and paint-region helpers."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from structure_catalog import (  # noqa: E402
    load_catalog,
    list_regions_for_tier,
    resolve_label_color,
    get_region,
)

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


@pytest.fixture(scope="module")
def structure_map():
    import pickle

    path = _REPO_ROOT / "csv" / "structure_map.pkl"
    with path.open("rb") as f:
        return pickle.load(f)


def test_resolve_label_color_from_catalog_when_missing_in_map(
    catalog, structure_map
):
    """Catalog fallback colors IDs that may be absent from structure_map keys."""
    visp = catalog["by_acronym"]["VISp"]
    rid = int(visp["id"])
    rgb = resolve_label_color(rid, {}, catalog)
    from structure_catalog import _hex_triplet_to_rgb

    assert rgb == _hex_triplet_to_rgb(visp["color_hex_triplet"])


def test_resolve_label_color_from_structure_map(structure_map, catalog):
    sample_id = next(iter(structure_map.keys()))
    rid = int(sample_id)
    expected = tuple(int(x) for x in structure_map[sample_id]["color"])
    assert resolve_label_color(rid, structure_map, catalog) == expected


def test_areas_tier_ids_exist_in_catalog(catalog):
    regions = list_regions_for_tier("areas", catalog)
    assert len(regions) > 0
    for node in regions[:20]:
        assert get_region(node["id"], catalog) is not None


def test_overlay_color_loop_uses_present_labels(catalog, structure_map):
    """Present-label coloring path resolves catalog colors for painted IDs."""
    visp = catalog["by_acronym"]["VISp"]
    rid = int(visp["id"])
    label = np.zeros((4, 4), dtype=np.uint32)
    label[1, 1] = rid
    present = [int(x) for x in np.unique(label) if int(x) != 0]
    assert present == [rid]
    rgb = resolve_label_color(rid, {}, catalog)
    assert rgb != (128, 128, 128)


def test_undo_calls_full_overlay_rebuild():
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "def undo_last_delta" in src
    block = src.split("def undo_last_delta")[1].split("def ")[0]
    assert "show_image_with_overlay()" in block

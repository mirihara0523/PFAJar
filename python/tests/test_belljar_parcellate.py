"""Tests for belljar.annotation parcellation modules."""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pytest

from belljar.annotation.apply import apply_parcellation_to_slice
from belljar.annotation.exclusion import expand_excluded_ids
from belljar.annotation.relabel import load_parcellation_meta, relabel_to_target
from belljar.atlas.catalog import load_catalog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"
_MAP_PATH = _REPO_ROOT / "csv" / "structure_map.pkl"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


@pytest.fixture(scope="module")
def structure_map():
    with _MAP_PATH.open("rb") as f:
        return pickle.load(f)


@pytest.fixture
def align_dir(tmp_path):
    leaf = tmp_path / "align_run"
    leaf.mkdir()
    return leaf


def test_belljar_relabel_vis_layer_to_area(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    grid = np.full((4, 4), visp4, dtype=np.uint32)
    result = relabel_to_target(
        grid,
        catalog,
        tier_id="areas",
        structure_map=structure_map,
    )
    vis = catalog["by_acronym"]["VIS"]["id"]
    assert np.all(result.label_array == vis)
    assert result.pixels_changed > 0


def test_belljar_exclusion_expand_vis(structure_map, catalog):
    vis = catalog["by_acronym"]["VIS"]["id"]
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    expanded = expand_excluded_ids(structure_map, [vis])
    assert vis in expanded
    assert visp4 in expanded


def test_belljar_apply_slice_isolated(align_dir, catalog, structure_map):
    slice_a = "M528_s061"
    slice_b = "M528_s062"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    visl4 = catalog["by_acronym"]["VISl4"]["id"]
    for sid, grid in (
        (slice_a, np.full((4, 4), visp4, dtype=np.uint32)),
        (slice_b, np.full((4, 4), visl4, dtype=np.uint32)),
    ):
        with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
            pickle.dump(grid, f)

    apply_parcellation_to_slice(
        align_dir,
        slice_a,
        tier_id="areas",
        st_level=None,
        excluded_region_ids=None,
        structure_map=structure_map,
        catalog=catalog,
    )
    with (align_dir / f"Annotation_{slice_b}.pkl").open("rb") as f:
        still_b = pickle.load(f)
    assert np.array_equal(still_b, np.full((4, 4), visl4, dtype=np.uint32))
    assert slice_a in load_parcellation_meta(align_dir)

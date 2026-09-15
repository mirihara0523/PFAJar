"""Tests for py/annotation_relabel.py (CCF parcellation rollup)."""

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from annotation_relabel import (  # noqa: E402
    clear_slice_parcellation,
    ensure_full_backup,
    get_slice_parcellation,
    load_full_backup,
    load_parcellation_meta,
    relabel_to_target,
    set_slice_parcellation,
)
from structure_catalog import ancestor_at_level, load_catalog  # noqa: E402

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


@pytest.fixture
def align_dir(tmp_path):
    leaf = tmp_path / "01_slices" / "align" / "test_run"
    leaf.mkdir(parents=True)
    return leaf


def test_ancestor_at_level_visp4_to_areas(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]
    vis = catalog["by_acronym"]["VIS"]
    mapped = ancestor_at_level(visp4["id"], catalog, tier_id="areas")
    assert mapped == vis["id"]


def test_ancestor_at_level_visp_unchanged_at_subareas(catalog):
    visp = catalog["by_acronym"]["VISp"]
    mapped = ancestor_at_level(visp["id"], catalog, tier_id="subareas")
    assert mapped == visp["id"]


def test_ancestor_at_level_parts_maps_layers_to_parents(catalog):
    rspv23 = catalog["by_acronym"]["RSPv2/3"]
    rspv = catalog["by_acronym"]["RSPv"]
    assert ancestor_at_level(rspv23["id"], catalog, tier_id="parts") == rspv["id"]
    visp4 = catalog["by_acronym"]["VISp4"]
    visp = catalog["by_acronym"]["VISp"]
    assert ancestor_at_level(visp4["id"], catalog, tier_id="parts") == visp["id"]
    assert ancestor_at_level(visp["id"], catalog, tier_id="parts") == visp["id"]
    rsp = catalog["by_acronym"]["RSP"]
    assert ancestor_at_level(rsp["id"], catalog, tier_id="parts") == rsp["id"]


def test_relabel_layers_to_parts_grid(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    rspv23 = catalog["by_acronym"]["RSPv2/3"]["id"]
    visp = catalog["by_acronym"]["VISp"]["id"]
    rspv = catalog["by_acronym"]["RSPv"]["id"]

    grid = np.zeros((4, 4), dtype=np.uint32)
    grid[0:2, :] = visp4
    grid[2:, :] = rspv23

    result = relabel_to_target(grid, catalog, tier_id="parts")
    assert result.pixels_changed == 16
    assert np.all(result.label_array[0:2, :] == visp)
    assert np.all(result.label_array[2:, :] == rspv)


def test_relabel_layers_to_areas_grid(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    visl4 = catalog["by_acronym"]["VISl4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    visl = catalog["by_acronym"]["VISl"]["id"]

    grid = np.zeros((10, 10), dtype=np.uint32)
    grid[0:5, :] = visp4
    grid[5:, :] = visl4

    result = relabel_to_target(grid, catalog, tier_id="areas")
    assert result.pixels_changed == 100
    assert np.all(result.label_array == vis)


def test_relabel_idempotent_at_same_level(catalog):
    vis = catalog["by_acronym"]["VIS"]["id"]
    grid = np.full((5, 5), vis, dtype=np.uint32)
    first = relabel_to_target(grid, catalog, tier_id="areas")
    second = relabel_to_target(first.label_array, catalog, tier_id="areas")
    assert second.pixels_changed == 0
    assert np.array_equal(first.label_array, second.label_array)


def test_relabel_unknown_id_preserved(catalog):
    grid = np.array([[999999999]], dtype=np.uint32)
    result = relabel_to_target(grid, catalog, tier_id="areas")
    assert result.label_array[0, 0] == 999999999
    assert 999999999 in result.unknown_ids


def test_relabel_full_detail_noop(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    grid = np.array([[visp4]], dtype=np.uint32)
    result = relabel_to_target(grid, catalog, tier_id="full")
    assert result.pixels_changed == 0
    assert result.label_array[0, 0] == visp4


def test_full_backup_and_metadata_round_trip(align_dir, catalog):
    slice_a = "M528_s061"
    slice_b = "M528_s062"
    grid_a = np.full((4, 4), catalog["by_acronym"]["VISp4"]["id"], dtype=np.uint32)
    grid_b = np.full((4, 4), catalog["by_acronym"]["VISl4"]["id"], dtype=np.uint32)

    ensure_full_backup(align_dir, slice_a, grid_a)
    ensure_full_backup(align_dir, slice_b, grid_b)

    set_slice_parcellation(align_dir, slice_a, tier_id="areas", st_level=None)

    meta = load_parcellation_meta(align_dir)
    assert slice_a in meta
    assert slice_b not in meta
    assert get_slice_parcellation(align_dir, slice_a)["tier_id"] == "areas"

    restored = load_full_backup(align_dir, slice_a)
    assert np.array_equal(restored, grid_a)

    clear_slice_parcellation(align_dir, slice_a)
    assert get_slice_parcellation(align_dir, slice_a) is None


def test_apply_slice_a_does_not_touch_slice_b(align_dir, catalog):
    slice_a = "M528_s061"
    slice_b = "M528_s062"
    grid_a = np.full((3, 3), catalog["by_acronym"]["VISp4"]["id"], dtype=np.uint32)
    grid_b = np.full((3, 3), catalog["by_acronym"]["VISl4"]["id"], dtype=np.uint32)

    anno_a = align_dir / f"Annotation_{slice_a}.pkl"
    anno_b = align_dir / f"Annotation_{slice_b}.pkl"
    with anno_a.open("wb") as f:
        pickle.dump(grid_a, f)
    with anno_b.open("wb") as f:
        pickle.dump(grid_b, f)

    ensure_full_backup(align_dir, slice_a, grid_a)
    ensure_full_backup(align_dir, slice_b, grid_b)

    baseline = load_full_backup(align_dir, slice_a)
    relabeled = relabel_to_target(baseline, catalog, tier_id="areas")
    with anno_a.open("wb") as f:
        pickle.dump(relabeled.label_array, f)
    set_slice_parcellation(align_dir, slice_a, tier_id="areas", st_level=None)

    with anno_b.open("rb") as f:
        still_b = pickle.load(f)
    assert np.array_equal(still_b, grid_b)
    assert get_slice_parcellation(align_dir, slice_b) is None

    with anno_a.open("rb") as f:
        updated_a = pickle.load(f)
    vis = catalog["by_acronym"]["VIS"]["id"]
    assert np.all(updated_a == vis)


def test_ancestor_at_level_st_level_raw(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]
    visp = catalog["by_acronym"]["VISp"]
    mapped = ancestor_at_level(visp4["id"], catalog, st_level=8)
    assert mapped == visp["id"]

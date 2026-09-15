"""Tests for py/apply_parcellation.py and py/annotation_exclusion.py."""

import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from annotation_exclusion import (  # noqa: E402
    apply_exclusion,
    apply_inclusion,
    expand_excluded_ids,
    expand_included_ids,
)
from apply_parcellation import (  # noqa: E402
    apply_parcellation_batch,
    apply_parcellation_to_slice,
    restore_slice_from_backup,
)
from annotation_relabel import load_full_backup, load_parcellation_meta  # noqa: E402
from structure_catalog import load_catalog  # noqa: E402

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"
_MAP_PATH = _REPO_ROOT / "csv" / "structure_map.pkl"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


@pytest.fixture(scope="module")
def structure_map():
    import pickle as pkl

    with _MAP_PATH.open("rb") as f:
        return pkl.load(f)


@pytest.fixture
def align_dir(tmp_path):
    leaf = tmp_path / "align_run"
    leaf.mkdir()
    return leaf


def test_expand_excluded_vis_descendants(structure_map, catalog):
    vis = catalog["by_acronym"]["VIS"]["id"]
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    expanded = expand_excluded_ids(structure_map, [vis])
    assert vis in expanded
    assert visp4 in expanded


def test_apply_exclusion_zeros_pixels():
    arr = np.array([[100, 200], [100, 0]], dtype=np.uint32)
    out, n = apply_exclusion(arr, {100})
    assert n == 2
    assert out[0, 0] == 0
    assert out[1, 0] == 0
    assert out[0, 1] == 200


def test_apply_inclusion_keeps_only_selected(structure_map, catalog):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    visl4 = catalog["by_acronym"]["VISl4"]["id"]
    arr = np.array([[visp4, visl4], [visl4, visp4]], dtype=np.uint32)
    inc_set = expand_included_ids(structure_map, [visp4])
    out, n = apply_inclusion(arr, inc_set)
    assert n == 2
    assert out[0, 1] == 0
    assert out[1, 0] == 0
    assert out[0, 0] == visp4
    assert out[1, 1] == visp4


def test_apply_slice_isolated(align_dir, catalog, structure_map):
    slice_a = "M528_s061"
    slice_b = "M528_s062"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    visl4 = catalog["by_acronym"]["VISl4"]["id"]
    grid_a = np.full((4, 4), visp4, dtype=np.uint32)
    grid_b = np.full((4, 4), visl4, dtype=np.uint32)

    for sid, grid in ((slice_a, grid_a), (slice_b, grid_b)):
        path = align_dir / f"Annotation_{sid}.pkl"
        with path.open("wb") as f:
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
    assert np.array_equal(still_b, grid_b)

    meta = load_parcellation_meta(align_dir)
    assert slice_a in meta
    assert slice_b not in meta


def test_apply_batch_two_slices(align_dir, catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    for sid in ("M528_s001", "M528_s002"):
        with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
            pickle.dump(np.full((3, 3), visp4, dtype=np.uint32), f)

    summary = apply_parcellation_batch(
        align_dir,
        None,
        tier_id="areas",
        st_level=None,
        excluded_region_ids=None,
        structure_map=structure_map,
        catalog=catalog,
    )
    assert summary.ok_count == 2
    vis = catalog["by_acronym"]["VIS"]["id"]
    with (align_dir / "Annotation_M528_s001.pkl").open("rb") as f:
        assert np.all(pickle.load(f) == vis)


def test_restore_fine(align_dir, catalog, structure_map):
    sid = "M528_s010"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
        pickle.dump(np.full((2, 2), visp4, dtype=np.uint32), f)

    apply_parcellation_to_slice(
        align_dir, sid, tier_id="areas", st_level=None,
        excluded_region_ids=None, structure_map=structure_map, catalog=catalog,
    )
    restore_slice_from_backup(align_dir, sid)
    with (align_dir / f"Annotation_{sid}.pkl").open("rb") as f:
        restored = pickle.load(f)
    backup = load_full_backup(align_dir, sid)
    assert np.array_equal(restored, backup)


def test_apply_inclusion_zeros_outside_set():
    arr = np.array([[100, 200], [300, 0]], dtype=np.uint32)
    out, n = apply_inclusion(arr, {100, 200})
    assert n == 1
    assert out[0, 0] == 100
    assert out[0, 1] == 200
    assert out[1, 0] == 0
    assert out[1, 1] == 0


def test_inclusion_after_rollup(align_dir, catalog, structure_map):
    sid = "M528_s021"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    aud = catalog["by_acronym"].get("AUD")
    if aud is None:
        pytest.skip("AUD not in catalog")
    aud_id = aud["id"]
    grid = np.zeros((4, 4), dtype=np.uint32)
    grid[0:2, :] = visp4
    grid[2:, :] = aud_id
    with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
        pickle.dump(grid, f)

    vis = catalog["by_acronym"]["VIS"]["id"]
    apply_parcellation_to_slice(
        align_dir,
        sid,
        tier_id="areas",
        st_level=None,
        excluded_region_ids=None,
        included_region_ids=[vis],
        structure_map=structure_map,
        catalog=catalog,
    )
    with (align_dir / f"Annotation_{sid}.pkl").open("rb") as f:
        out = pickle.load(f)
    assert np.any(out[0:2, :] != 0)
    assert np.all(out[2:, :] == 0)


def test_exclusion_after_rollup(align_dir, catalog, structure_map):
    sid = "M528_s020"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    aud = catalog["by_acronym"].get("AUD")
    if aud is None:
        pytest.skip("AUD not in catalog")
    aud_id = aud["id"]
    grid = np.zeros((4, 4), dtype=np.uint32)
    grid[0:2, :] = visp4
    grid[2:, :] = aud_id
    with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
        pickle.dump(grid, f)

    vis = catalog["by_acronym"]["VIS"]["id"]
    apply_parcellation_to_slice(
        align_dir, sid, tier_id="areas", st_level=None,
        excluded_region_ids=[vis], structure_map=structure_map, catalog=catalog,
    )
    with (align_dir / f"Annotation_{sid}.pkl").open("rb") as f:
        out = pickle.load(f)
    assert np.all(out[0:2, :] == 0)
    assert np.any(out[2:, :] != 0)

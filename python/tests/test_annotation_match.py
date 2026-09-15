"""Tests for py/annotation_match.py (parcellation-aware intensity matching)."""

import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from annotation_match import (  # noqa: E402
    ParcellationContext,
    atlas_ids_matching_target,
    include_layers_allowed,
    resolve_output_targets,
    resolve_selection_id,
)
from apply_parcellation import apply_parcellation_to_slice  # noqa: E402
from region_config import children_for_target  # noqa: E402
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


@pytest.fixture
def align_dir(tmp_path):
    leaf = tmp_path / "align_run"
    leaf.mkdir()
    return leaf


def test_full_detail_matches_children_for_target(structure_map, catalog):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    ctx = ParcellationContext(is_full_detail=True)
    ids = atlas_ids_matching_target(structure_map, vis, False, ctx, catalog)
    legacy = {int(x) for x in children_for_target(structure_map, vis, False)}
    assert ids == legacy
    assert visp4 in ids


def test_include_layers_allowed_full_and_areas():
    assert include_layers_allowed(ParcellationContext(is_full_detail=True))
    assert not include_layers_allowed(
        ParcellationContext(is_full_detail=False, tier_id="areas")
    )
    assert include_layers_allowed(
        ParcellationContext(is_full_detail=False, tier_id="layers")
    )


def test_parcellated_fine_selection_resolves_to_area(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    ctx = ParcellationContext(is_full_detail=False, tier_id="areas")
    resolved = resolve_selection_id(visp4, ctx, catalog, structure_map)
    assert resolved == vis
    targets = resolve_output_targets(structure_map, [visp4], False, ctx, catalog)
    assert vis in targets


def test_parcellated_annotation_pixels_match(align_dir, catalog, structure_map):
    sid = "M528_s061"
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    grid = np.full((8, 8), visp4, dtype=np.uint32)
    with (align_dir / f"Annotation_{sid}.pkl").open("wb") as f:
        pickle.dump(grid, f)

    apply_parcellation_to_slice(
        align_dir,
        sid,
        tier_id="areas",
        st_level=None,
        excluded_region_ids=None,
        structure_map=structure_map,
        catalog=catalog,
    )

    ctx = ParcellationContext(is_full_detail=False, tier_id="areas")
    match_ids = atlas_ids_matching_target(structure_map, visp4, False, ctx, catalog)
    assert match_ids == {vis}

    with (align_dir / f"Annotation_{sid}.pkl").open("rb") as f:
        rolled = pickle.load(f)
    assert np.all(rolled == vis)
    assert np.any(rolled == list(match_ids)[0])

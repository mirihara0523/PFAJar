"""Count Brain parcellation-aware label rollup and CSV acronym emission."""

import pickle
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from annotation_match import (  # noqa: E402
    ParcellationContext,
    count_rollup_log_label,
    resolve_count_label_id,
    summarize_count_rollup_labels,
)
from count import collect_used_acronyms  # noqa: E402
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


def test_full_detail_layers_off_rolls_to_area(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    ctx = ParcellationContext(is_full_detail=True)
    resolved = resolve_count_label_id(
        visp4, ctx, catalog, structure_map, include_layers=False
    )
    assert resolved == vis
    assert count_rollup_log_label(ctx, include_layers=False) == "areas"


def test_layers_on_uses_literal_id(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    ctx = ParcellationContext(is_full_detail=True)
    resolved = resolve_count_label_id(
        visp4, ctx, catalog, structure_map, include_layers=True
    )
    assert resolved == visp4
    assert count_rollup_log_label(ctx, include_layers=True) == "literal_layers"


def test_parcellated_areas_context(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    vis = catalog["by_acronym"]["VIS"]["id"]
    ctx = ParcellationContext(is_full_detail=False, tier_id="areas")
    resolved = resolve_count_label_id(
        visp4, ctx, catalog, structure_map, include_layers=False
    )
    assert resolved == vis
    assert count_rollup_log_label(ctx, include_layers=False) == "tier:areas"


def test_parcellated_layers_context_keeps_layer_id(catalog, structure_map):
    visp4 = catalog["by_acronym"]["VISp4"]["id"]
    ctx = ParcellationContext(is_full_detail=False, tier_id="layers")
    resolved = resolve_count_label_id(
        visp4, ctx, catalog, structure_map, include_layers=False
    )
    assert resolved == visp4
    assert count_rollup_log_label(ctx, include_layers=False) == "tier:layers"


def test_summarize_count_rollup_labels_mixed():
    assert summarize_count_rollup_labels(["tier:layers"]) == "tier:layers"
    assert summarize_count_rollup_labels(["areas", "areas"]) == "areas"
    assert summarize_count_rollup_labels(["tier:layers", "tier:areas"]) == (
        "mixed tiers={tier:areas,tier:layers}"
    )
    assert summarize_count_rollup_labels([]) == "areas"


def test_collect_used_acronyms_includes_laminar_keys():
    sums = {
        "Predictions_s001.pkl": {0: {"VISp4": 3, "VISp1": 1}},
        "Predictions_s002.pkl": {0: {"VIS": 2}},
    }
    region_areas = {
        "Predictions_s001.pkl": {"VISp4": 100, "VISp1": 40},
        "Predictions_s002.pkl": {"VIS": 200, "AUD": 50},
    }
    used = collect_used_acronyms(sums, region_areas)
    assert used == {"VISp4", "VISp1", "VIS", "AUD"}

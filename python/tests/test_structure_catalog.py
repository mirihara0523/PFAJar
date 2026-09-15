"""Tests for py/structure_catalog.py (CCF catalog mirror of js/structure_catalog.js)."""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from structure_catalog import (  # noqa: E402
    CCF_ADVANCED_HELP,
    TIER_DEFS,
    ancestor_at_level,
    compact_ccf_level_label_and_tooltip,
    format_ccf_level_label,
    get_region,
    list_ccf_levels,
    list_levels,
    list_regions_at_level,
    list_regions_for_tier,
    list_tiers,
    load_catalog,
)

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_GRAPH_PATH)


def test_load_catalog_size(catalog):
    assert len(catalog["nodes"]) > 1000
    assert len(catalog["by_id"]) == len(catalog["nodes"])


def test_list_levels_sorted(catalog):
    levels = list_levels(catalog)
    assert len(levels) >= 5
    level_nums = [item["level"] for item in levels]
    assert level_nums == sorted(level_nums)
    assert all("exampleAcronym" in item and "exampleName" in item for item in levels)


def test_list_regions_at_level_exact_filter(catalog):
    level6 = list_regions_at_level(6, "", catalog)
    assert len(level6) > 10
    assert all(node["st_level"] == 6 for node in level6)
    acronyms = [node["acronym"] for node in level6]
    assert acronyms == sorted(acronyms)


def test_list_regions_at_level_search(catalog):
    vis_search = list_regions_at_level(8, "visp", catalog)
    assert any(node["acronym"] == "VISp" for node in vis_search)


def test_group_parent_vis_siblings(catalog):
    visp = catalog["by_acronym"]["VISp"]
    visl = catalog["by_acronym"]["VISl"]
    assert visp["groupParentId"] == visl["groupParentId"]
    ssp = catalog["by_acronym"].get("SSp")
    if ssp:
        assert visp["groupParentId"] != ssp["groupParentId"]


def test_get_region(catalog):
    visp = get_region(catalog["by_acronym"]["VISp"]["id"], catalog)
    assert visp is not None
    assert visp["acronym"] == "VISp"


def test_list_tiers_shape_and_order(catalog):
    tiers = list_tiers(catalog)
    ids = [t["id"] for t in tiers]
    assert ids == [t["id"] for t in TIER_DEFS]
    assert ids == ["major", "regions", "areas", "subareas", "parts", "layers"]
    for tier in tiers:
        assert tier["label"]
        assert tier["description"]
        assert isinstance(tier["region_ids"], list)
        assert len(tier["region_ids"]) > 0, f"tier {tier['id']} is empty"


def test_list_tiers_areas_includes_vis_aud_ss(catalog):
    tiers_by_id = {t["id"]: t for t in list_tiers(catalog)}
    area_nodes = list_regions_for_tier("areas", catalog)
    acronyms = {n["acronym"] for n in area_nodes}
    assert "VIS" in acronyms
    assert "AUD" in acronyms
    # Somatosensory parent ``SS`` lives at st_level 6 (SSp/SSs are deeper)
    assert "SS" in acronyms
    assert len(tiers_by_id["areas"]["region_ids"]) >= 30
    assert len(tiers_by_id["areas"]["region_ids"]) <= 40


def test_list_tiers_subareas_include_visp_ssp(catalog):
    sub_nodes = list_regions_for_tier("subareas", catalog)
    acronyms = {n["acronym"] for n in sub_nodes}
    # Sub-areas tier (st_level 8 minus layers) should contain VISp + SSp-bfd
    # (ACAd lives at a deeper level so is not required here)
    assert "VISp" in acronyms
    assert "SSp-bfd" in acronyms or "SSp" in acronyms
    # Common subcortical nuclei are at this depth too
    assert "ACB" in acronyms or "AAA" in acronyms
    assert "RSP" in acronyms
    assert "RSPagl" not in acronyms


def test_list_tiers_parts_parent_of_layers(catalog):
    parts = list_regions_for_tier("parts", catalog)
    acronyms = {n["acronym"] for n in parts}
    assert "RSPagl" in acronyms
    assert "RSPd" in acronyms
    assert "RSPv" in acronyms
    assert "VISp" in acronyms
    assert "AUDp" in acronyms
    assert "SSp-bfd" in acronyms or any(a.startswith("SSp") for a in acronyms)
    assert "RSP" not in acronyms
    assert "RSPv2/3" not in acronyms
    assert "VISp4" not in acronyms
    for node in parts:
        assert node["st_level"] != 11
        assert not (
            "layer" in node["name"].lower() and node["st_level"] == 11
        )


def test_list_tiers_subareas_excludes_layers(catalog):
    sub_nodes = list_regions_for_tier("subareas", catalog)
    for node in sub_nodes:
        assert "layer" not in node["name"].lower()


def test_list_tiers_layers_includes_layer_named(catalog):
    layer_nodes = list_regions_for_tier("layers", catalog)
    layer_names = [n for n in layer_nodes if "layer" in n["name"].lower()]
    assert len(layer_names) > 0
    # And every entry is either L11 or layer-named (no other depths leak in)
    for node in layer_nodes:
        assert node["st_level"] == 11 or "layer" in node["name"].lower()


def test_ancestor_at_level_parts_rollup(catalog):
    rspv23 = catalog["by_acronym"]["RSPv2/3"]
    rspv = catalog["by_acronym"]["RSPv"]
    assert ancestor_at_level(rspv23["id"], catalog, tier_id="parts") == rspv["id"]
    visp4 = catalog["by_acronym"]["VISp4"]
    visp = catalog["by_acronym"]["VISp"]
    assert ancestor_at_level(visp4["id"], catalog, tier_id="parts") == visp["id"]
    assert ancestor_at_level(visp["id"], catalog, tier_id="parts") == visp["id"]
    rsp = catalog["by_acronym"]["RSP"]
    assert ancestor_at_level(rsp["id"], catalog, tier_id="parts") == rsp["id"]


def test_list_ccf_levels_enriched_fields(catalog):
    levels = list_ccf_levels(catalog)
    assert len(levels) >= 5
    seen_levels = [info["level"] for info in levels]
    assert seen_levels == sorted(seen_levels)
    for info in levels:
        assert info["count"] > 0
        assert info["kind"]
        assert isinstance(info["sampleAcronyms"], list)
        assert len(info["sampleAcronyms"]) <= 5
        assert isinstance(info["hasMore"], bool)


def test_list_ccf_levels_level6_has_vis_aud_ss(catalog):
    levels = list_ccf_levels(catalog)
    by_level = {info["level"]: info for info in levels}
    assert 6 in by_level, "level 6 should exist"
    info6 = by_level[6]
    assert info6["count"] >= 30
    assert info6["count"] <= 40
    # samples are sorted; assert at least one VIS/AUD/SS-family acronym is included
    acronyms_at_6 = {
        n["acronym"] for n in catalog["nodes"] if n["st_level"] == 6
    }
    assert {"VIS", "AUD"}.issubset(acronyms_at_6)
    # Somatosensory parent ``SS`` lives at st_level 6 (SSp/SSs are deeper)
    assert "SS" in acronyms_at_6


def test_format_ccf_level_label_includes_count_kind_samples(catalog):
    info6 = next(info for info in list_ccf_levels(catalog) if info["level"] == 6)
    label = format_ccf_level_label(info6)
    assert label.startswith("Level 6 — ")
    assert str(info6["count"]) in label
    assert info6["kind"] in label
    for ac in info6["sampleAcronyms"]:
        assert ac in label


def test_format_ccf_level_label_single_structure_ctxpl(catalog):
    # st_level 4 contains only Cortical plate (CTXpl) — should render "single structure"
    info4 = next(info for info in list_ccf_levels(catalog) if info["level"] == 4)
    label = format_ccf_level_label(info4)
    assert "1 single structure" in label
    assert "CTXpl" in label


def test_compact_ccf_level_label_moves_samples_to_tooltip(catalog):
    info6 = next(info for info in list_ccf_levels(catalog) if info["level"] == 6)
    label, tooltip = compact_ccf_level_label_and_tooltip(info6)
    assert label.startswith("Level 6 - ")
    assert "(" not in label
    assert tooltip.startswith("(")


def test_ccf_advanced_help_text_constant():
    assert "CCFv3" in CCF_ADVANCED_HELP
    assert "st_level" in CCF_ADVANCED_HELP


def test_ancestor_at_level_matches_group_parent(catalog):
    visp4 = catalog["by_acronym"]["VISp4"]
    via_ancestor = ancestor_at_level(visp4["id"], catalog, st_level=6)
    via_group = visp4["groupParentId"]
    assert via_ancestor == via_group

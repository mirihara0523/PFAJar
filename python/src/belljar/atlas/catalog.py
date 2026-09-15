"""Flatten Allen CCF structure_graph.json for region pickers (mirrors js/structure_catalog.js)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TIER_TARGET_LEVEL: dict[str, int] = {
    "major": 2,
    "regions": 5,
    "areas": 6,
    "subareas": 8,
    "layers": 11,
    # "parts" has no fixed st_level — see ancestor_at_level / _rollup_to_parts
}

FULL_DETAIL_TIER = "full"
PARTS_TIER = "parts"

# Semantic tier definitions. Order is the order shown in the Hierarchy dropdown.
# Rules are derived from CCFv3 ``st_level`` plus simple name heuristics so a
# future ontology update keeps working without hardcoded acronym lists.
TIER_DEFS: list[dict[str, Any]] = [
    {
        "id": "major",
        "label": "Major divisions",
        "description": "Cerebrum, brain stem, cerebellum",
    },
    {
        "id": "regions",
        "label": "Classic regions",
        "description": "Isocortex, thalamus, hypothalamus, midbrain, …",
    },
    {
        "id": "areas",
        "label": "Functional areas",
        "description": "Sensory, motor, association (VIS, AUD, SSp, MO, …)",
    },
    {
        "id": "subareas",
        "label": "Sub-areas",
        "description": "VISp, VISal, SSp-bfd, ACAd, RSP, individual nuclei",
    },
    {
        "id": "parts",
        "label": "Area parts",
        "description": (
            "Named subdivisions without cortical layers "
            "(VISp, RSPagl, AUDp, SSp-bfd, …)"
        ),
    },
    {
        "id": "layers",
        "label": "Cortical layers",
        "description": "VISp1, VISp2/3, ACA6a, …",
    },
]

CCF_ADVANCED_HELP = (
    "Allen Institute CCFv3 ontology depths (st_level 0–11). Some depths group "
    "structures that are not anatomically meaningful (e.g. Level 4 contains "
    "only Cortical plate). Use the standard tiers above for everyday region "
    "picking."
)


def _parse_id_path(id_path: str | list[int] | None) -> list[int]:
    if not id_path:
        return []
    if isinstance(id_path, list):
        return list(id_path)
    return [int(part) for part in str(id_path).split("/") if part]


GROUP_STYLE_LEVEL = 6


def group_parent_for_region(region: dict[str, Any], by_id: dict[int, dict[str, Any]]):
    if not region:
        return None
    path_ids = region.get("idPath") or _parse_id_path(region.get("id_path"))
    if not path_ids:
        path_ids = [region["id"]]
    at_level = None
    nearest_shallow = None
    for nid in path_ids:
        node = by_id.get(nid)
        if not node:
            continue
        if node["st_level"] == GROUP_STYLE_LEVEL:
            at_level = node
        if node["st_level"] < GROUP_STYLE_LEVEL:
            nearest_shallow = node
    if at_level:
        return at_level
    if nearest_shallow:
        return nearest_shallow
    return region


def _is_layer_name(node: dict[str, Any]) -> bool:
    return "layer" in str(node.get("name", "")).lower()


def _is_layer_node(node: dict[str, Any]) -> bool:
    """True for laminar CCF nodes (st_level 11 or layer-named)."""
    return int(node.get("st_level", -1)) == 11 or _is_layer_name(node)


def _children_by_parent(catalog: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    cached = catalog.get("children_by_parent")
    if isinstance(cached, dict):
        return cached
    by_parent: dict[int, list[dict[str, Any]]] = {}
    for node in catalog.get("nodes") or []:
        path = node.get("idPath") or _parse_id_path(node.get("id_path"))
        if len(path) < 2:
            continue
        parent_id = int(path[-2])
        by_parent.setdefault(parent_id, []).append(node)
    catalog["children_by_parent"] = by_parent
    return by_parent


def _has_direct_layer_child(node_id: int, catalog: dict[str, Any]) -> bool:
    for child in _children_by_parent(catalog).get(int(node_id), []):
        if _is_layer_node(child):
            return True
    return False


def _resolve_path_ids(
    region_id: int,
    catalog: dict[str, Any],
    structure_map: dict | None = None,
) -> list[int]:
    rid = int(region_id)
    by_id = catalog.get("by_id") or {}
    node = by_id.get(rid)
    if node:
        path = list(node.get("idPath") or _parse_id_path(node.get("id_path")))
        if path:
            return path
    if structure_map:
        info = structure_map.get(rid)
        if info is None:
            try:
                import numpy as np

                info = structure_map.get(np.uint32(rid))
            except ImportError:
                pass
        if info:
            path = _parse_id_path(info.get("id_path"))
            if path:
                return path
    return [rid]


def _rollup_to_parts(
    region_id: int,
    catalog: dict[str, Any],
    structure_map: dict | None = None,
) -> int:
    """Map laminar IDs to nearest non-layer ancestor; non-layer IDs stay put."""
    rid = int(region_id)
    if rid == 0:
        return 0
    by_id = catalog.get("by_id") or {}
    node = by_id.get(rid)
    if node is not None and not _is_layer_node(node):
        return rid
    path_ids = _resolve_path_ids(rid, catalog, structure_map)
    for nid in reversed(path_ids):
        ancestor = by_id.get(int(nid))
        if ancestor is not None and not _is_layer_node(ancestor):
            return int(ancestor["id"])
    return rid


def _node_matches_tier_target(
    node: dict[str, Any],
    target_level: int,
    tier_id: str | None,
) -> bool:
    """True when *node* is a valid rollup target for tier or raw st_level."""
    lvl = int(node["st_level"])
    if tier_id == "layers":
        return lvl == target_level or _is_layer_name(node)
    if tier_id == "subareas":
        return lvl == target_level and not _is_layer_name(node)
    return lvl == target_level


def _resolve_target_level(
    *,
    tier_id: str | None = None,
    st_level: int | None = None,
) -> tuple[int | None, str | None]:
    """Return ``(target_st_level, tier_id)`` for rollup; ``(None, None)`` = full detail."""
    if tier_id == FULL_DETAIL_TIER:
        return None, None
    if st_level is not None:
        return int(st_level), tier_id
    if tier_id == PARTS_TIER:
        # Handled by _rollup_to_parts; no fixed st_level.
        return None, PARTS_TIER
    if tier_id:
        level = TIER_TARGET_LEVEL.get(tier_id)
        if level is None:
            raise ValueError(f"unknown tier_id: {tier_id!r}")
        return level, tier_id
    return None, None


def ancestor_at_level(
    region_id: int,
    catalog: dict[str, Any],
    *,
    tier_id: str | None = None,
    st_level: int | None = None,
    structure_map: dict | None = None,
) -> int:
    """Map a structure id to its rollup ancestor at *tier_id* or raw *st_level*.

    Walks ``idPath`` from root toward leaf. Returns the deepest ancestor whose
    ``st_level`` matches the target (with tier heuristics for layers/subareas).
    Area parts (``parts``): laminar → nearest non-layer ancestor; non-layer identity.
    If none match, returns the nearest shallower ancestor (coarser). Unknown ids
    are returned unchanged.
    """
    rid = int(region_id)
    if rid == 0:
        return 0

    if st_level is None and tier_id == PARTS_TIER:
        return _rollup_to_parts(rid, catalog, structure_map)

    target_level, effective_tier = _resolve_target_level(
        tier_id=tier_id, st_level=st_level
    )
    if target_level is None:
        return rid

    by_id = catalog.get("by_id") or {}
    path_ids = _resolve_path_ids(rid, catalog, structure_map)

    at_level = None
    nearest_shallow = None
    for nid in path_ids:
        ancestor = by_id.get(int(nid))
        if not ancestor:
            continue
        lvl = int(ancestor["st_level"])
        if _node_matches_tier_target(ancestor, target_level, effective_tier):
            at_level = ancestor
        if lvl < target_level:
            nearest_shallow = ancestor

    if at_level:
        return int(at_level["id"])
    if nearest_shallow:
        return int(nearest_shallow["id"])
    return rid


def _flatten_graph(
    graph: dict[str, Any],
    id_path: list[int],
    nodes: list[dict[str, Any]],
    by_id: dict[int, dict[str, Any]],
    by_acronym: dict[str, dict[str, Any]],
) -> None:
    current_path = id_path + [graph["id"]]
    node = {
        "id": graph["id"],
        "acronym": graph["acronym"],
        "name": graph["name"],
        "st_level": graph["st_level"],
        "idPath": current_path,
        "id_path": "/".join(str(i) for i in current_path),
        "groupParentId": graph["id"],
        "groupParentAcronym": graph["acronym"],
        "groupParentName": graph["name"],
        "color_hex_triplet": graph.get("color_hex_triplet"),
    }
    nodes.append(node)
    by_id[graph["id"]] = node
    acronym = graph.get("acronym")
    if acronym and acronym not in by_acronym:
        by_acronym[acronym] = node
    for child in graph.get("children") or []:
        _flatten_graph(child, current_path, nodes, by_id, by_acronym)


def load_catalog(graph_path: str | Path) -> dict[str, Any]:
    """Load and flatten structure_graph.json into a catalog dict."""
    graph_path = Path(graph_path)
    with graph_path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    nodes: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    by_acronym: dict[str, dict[str, Any]] = {}
    _flatten_graph(root, [], nodes, by_id, by_acronym)
    for node in nodes:
        group_node = group_parent_for_region(node, by_id)
        if group_node:
            node["groupParentId"] = group_node["id"]
            node["groupParentAcronym"] = group_node["acronym"]
            node["groupParentName"] = group_node["name"]
    levels: dict[int, dict[str, Any]] = {}
    for node in nodes:
        lvl = node["st_level"]
        if lvl not in levels:
            levels[lvl] = node
    catalog = {
        "nodes": nodes,
        "by_id": by_id,
        "by_acronym": by_acronym,
        "levels": levels,
    }
    _children_by_parent(catalog)
    return catalog


def _tier_region_ids(tier_id: str, catalog: dict[str, Any]) -> list[int]:
    """Apply the tier rule from the plan (data-driven, no acronym hardcoding)."""
    nodes = catalog["nodes"]
    if tier_id == "major":
        return [n["id"] for n in nodes if n["st_level"] == 2]
    if tier_id == "regions":
        return [n["id"] for n in nodes if n["st_level"] == 5]
    if tier_id == "areas":
        return [n["id"] for n in nodes if n["st_level"] == 6]
    if tier_id == "subareas":
        return [
            n["id"]
            for n in nodes
            if n["st_level"] == 8 and not _is_layer_name(n)
        ]
    if tier_id == PARTS_TIER:
        return [
            n["id"]
            for n in nodes
            if not _is_layer_node(n) and _has_direct_layer_child(n["id"], catalog)
        ]
    if tier_id == "layers":
        return [
            n["id"]
            for n in nodes
            if n["st_level"] == 11 or _is_layer_name(n)
        ]
    return []


def list_tiers(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Curated semantic tiers (default Hierarchy picker for both toolsets)."""
    out: list[dict[str, Any]] = []
    for tier in TIER_DEFS:
        region_ids = _tier_region_ids(tier["id"], catalog)
        region_ids = sorted(set(region_ids))
        out.append(
            {
                "id": tier["id"],
                "label": tier["label"],
                "description": tier["description"],
                "region_ids": region_ids,
            }
        )
    return out


def list_regions_for_tier(
    tier_id: str,
    catalog: dict[str, Any],
    search_query: str = "",
) -> list[dict[str, Any]]:
    """Region rows for a semantic tier, sorted by acronym; supports search."""
    ids = set(_tier_region_ids(tier_id, catalog))
    q = (search_query or "").strip().lower()
    out: list[dict[str, Any]] = []
    for node in catalog["nodes"]:
        if node["id"] not in ids:
            continue
        if q:
            hay = (
                f"{node['acronym']} {node['name']} {node['groupParentAcronym']}"
            ).lower()
            if q not in hay:
                continue
        out.append(node)
    out.sort(key=lambda item: item["acronym"])
    return out


def _level_kind(level: int, count: int, layer_share: float) -> str:
    if layer_share >= 0.25:
        return "layers"
    if count == 1:
        return "single structure"
    if level <= 3:
        return "major divisions"
    if count <= 20:
        return "divisions"
    return "regions"


def _level_info_for_st(
    level: int,
    catalog: dict[str, Any],
    *,
    max_samples: int = 5,
) -> dict[str, Any]:
    acronyms: list[str] = []
    layer_count = 0
    seen: set[str] = set()
    for node in catalog["nodes"]:
        if node["st_level"] != level:
            continue
        ac = node["acronym"]
        if ac not in seen:
            seen.add(ac)
            acronyms.append(ac)
        if _is_layer_name(node):
            layer_count += 1
    acronyms.sort()
    count = len(acronyms)
    layer_share = (layer_count / count) if count else 0.0
    kind = _level_kind(level, count, layer_share)
    samples = acronyms[:max_samples]
    has_more = count > len(samples)
    return {
        "level": level,
        "count": count,
        "kind": kind,
        "sampleAcronyms": samples,
        "hasMore": has_more,
    }


def list_ccf_levels(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Enriched CCFv3 raw depths (used by Advanced toggle).

    Each entry: ``level, count, kind, sampleAcronyms, hasMore``.
    """
    levels: list[int] = sorted({n["st_level"] for n in catalog["nodes"]})
    return [_level_info_for_st(lvl, catalog) for lvl in levels]


def format_ccf_level_label(info: dict[str, Any]) -> str:
    """E.g. ``Level 6 — 34 regions (AUD, DORpm, GU, MO, SS, …)``.

    Same template as the JS sibling so PyQt and Electron labels match.
    """
    samples = list(info.get("sampleAcronyms") or [])
    suffix = ""
    if samples:
        joined = ", ".join(samples)
        if info.get("hasMore"):
            joined += ", …"
        suffix = f" ({joined})"
    return f"Level {info['level']} — {info['count']} {info['kind']}{suffix}"


def list_levels(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible sorted CCF levels.

    Kept for tests and existing callers; new UIs should use
    :func:`list_ccf_levels` + :func:`format_ccf_level_label` for the Advanced
    mode, or :func:`list_tiers` for the default semantic picker.
    """
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for node in catalog["nodes"]:
        lvl = node["st_level"]
        if lvl in seen:
            continue
        seen.add(lvl)
        example = catalog["levels"][lvl]
        out.append(
            {
                "level": lvl,
                "exampleAcronym": example["acronym"],
                "exampleName": example["name"],
            }
        )
    out.sort(key=lambda item: item["level"])
    return out


def list_regions_at_level(
    level: int,
    search_query: str = "",
    catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Regions at st_level, optional acronym/name/group-parent search; sorted by acronym."""
    if catalog is None:
        raise ValueError("catalog is required")
    q = (search_query or "").strip().lower()
    out: list[dict[str, Any]] = []
    for node in catalog["nodes"]:
        if node["st_level"] != level:
            continue
        if q:
            hay = (
                f"{node['acronym']} {node['name']} {node['groupParentAcronym']}"
            ).lower()
            if q not in hay:
                continue
        out.append(node)
    out.sort(key=lambda item: item["acronym"])
    return out


def get_region(region_id: int, catalog: dict[str, Any]) -> dict[str, Any] | None:
    return catalog["by_id"].get(int(region_id))


def _structure_map_entry(structure_map: dict, atlas_id: int) -> dict[str, Any] | None:
    """Lookup structure_map entry tolerating int / uint32 keys."""
    import numpy as np

    for key in (int(atlas_id), np.uint32(int(atlas_id))):
        if key in structure_map:
            return structure_map[key]
    return None


def _hex_triplet_to_rgb(hex_value: str) -> tuple[int, int, int]:
    hex_str = str(hex_value or "").lstrip("#")
    if len(hex_str) != 6:
        return (128, 128, 128)
    return (
        int(hex_str[0:2], 16),
        int(hex_str[2:4], 16),
        int(hex_str[4:6], 16),
    )


def resolve_label_color(
    label_id: int,
    structure_map: dict,
    catalog: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    """RGB triplet for overlay painting; structure_map first, then catalog hex."""
    lid = int(label_id)
    if lid == 0:
        return (0, 0, 0)
    info = _structure_map_entry(structure_map, lid)
    if info and info.get("color"):
        c = info["color"]
        return (int(c[0]), int(c[1]), int(c[2]))
    if catalog:
        node = get_region(lid, catalog)
        if node and node.get("color_hex_triplet"):
            return _hex_triplet_to_rgb(node["color_hex_triplet"])
    return (128, 128, 128)

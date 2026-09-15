"""Audit annotation label resolution for Isolate Regions and Adjust warnings."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from annotation_match import ParcellationContext, include_layers_allowed
from annotation_relabel import get_slice_parcellation, load_parcellation_meta
from czi_common import emit_result
from region_config import _map_get, is_layer_structure
from structure_catalog import get_region, load_catalog, _resolve_target_level


def _st_level_for_id(
    region_id: int,
    catalog: dict[str, Any] | None,
    structure_map: dict,
) -> int | None:
    if catalog:
        node = get_region(region_id, catalog)
        if node is not None:
            return int(node["st_level"])
    info = _map_get(structure_map, region_id)
    if info and info.get("st_level") is not None:
        return int(info["st_level"])
    return None


def _nonzero_label_ids(label: np.ndarray) -> list[int]:
    unique = np.unique(label)
    return [int(x) for x in unique if int(x) != 0]


def audit_label_array(
    label: np.ndarray,
    catalog: dict[str, Any] | None,
    structure_map: dict,
    parcellation_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return issue codes and recommendations for one annotation label array."""
    issues: list[str] = []
    recommendations: list[dict[str, bool | str]] = []

    ids = _nonzero_label_ids(label)
    if not ids:
        return {"issues": [], "recommendations": [], "st_levels": []}

    st_levels: set[int] = set()
    has_layer_ids = False
    for uid in ids:
        lvl = _st_level_for_id(uid, catalog, structure_map)
        if lvl is not None:
            st_levels.add(lvl)
        if is_layer_structure(structure_map, uid):
            has_layer_ids = True

    if len(st_levels) > 1:
        issues.append("mixed_st_levels")
        recommendations.append(
            {
                "code": "mixed_st_levels",
                "enable_include_layers": True,
                "rerun_intensity": True,
                "paint_one_tier": True,
            }
        )

    context: ParcellationContext | None = None
    if parcellation_entry:
        from annotation_match import _entry_to_context

        context = _entry_to_context(parcellation_entry)

    if has_layer_ids and context and not include_layers_allowed(context):
        issues.append("layer_on_coarse_parcellation")
        recommendations.append(
            {
                "code": "layer_on_coarse_parcellation",
                "enable_include_layers": True,
                "open_parcellation_wizard": True,
                "rerun_intensity": True,
            }
        )

    if parcellation_entry and st_levels:
        declared_level, _declared_tier = _resolve_target_level(
            tier_id=parcellation_entry.get("tier_id"),
            st_level=parcellation_entry.get("st_level"),
        )
        if declared_level is not None:
            max_level = max(st_levels)
            min_level = min(st_levels)
            if max_level > declared_level or min_level < declared_level:
                if "parcellation_metadata_mismatch" not in issues:
                    issues.append("parcellation_metadata_mismatch")
                    recommendations.append(
                        {
                            "code": "parcellation_metadata_mismatch",
                            "open_parcellation_wizard": True,
                            "paint_one_tier": True,
                            "rerun_intensity": True,
                        }
                    )

    return {
        "issues": issues,
        "recommendations": recommendations,
        "st_levels": sorted(st_levels),
        "has_layer_ids": has_layer_ids,
    }


def audit_align_leaf(
    annotation_dir: Path,
    catalog: dict[str, Any] | None,
    structure_map: dict,
) -> dict[str, Any]:
    """Scan Annotation_*.pkl files under an align leaf."""
    annotation_dir = Path(annotation_dir)
    meta = load_parcellation_meta(annotation_dir)
    slices: dict[str, Any] = {}
    issue_counts: dict[str, int] = {}

    import pickle

    for pkl_path in sorted(annotation_dir.glob("Annotation_*.pkl")):
        slice_id = pkl_path.stem
        if slice_id.startswith("Annotation_"):
            slice_id = slice_id[len("Annotation_") :]
        with open(pkl_path, "rb") as f:
            label = pickle.load(f)
        entry = meta.get(slice_id) if isinstance(meta, dict) else None
        if not isinstance(entry, dict):
            entry = get_slice_parcellation(annotation_dir, slice_id)
        result = audit_label_array(label, catalog, structure_map, entry)
        slices[slice_id] = {
            "file": pkl_path.name,
            **result,
        }
        for code in result["issues"]:
            issue_counts[code] = issue_counts.get(code, 0) + 1

    slices_with_issues = sum(1 for s in slices.values() if s.get("issues"))
    summary = {
        "any_issues": slices_with_issues > 0,
        "slices_with_issues": slices_with_issues,
        "slice_count": len(slices),
        "issue_counts": issue_counts,
    }
    return {
        "annotation_dir": str(annotation_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slices": slices,
        "summary": summary,
    }


def audit_cache_path(annotation_dir: Path) -> Path:
    return Path(annotation_dir) / ".masonjar" / "annotation_label_audit.json"


def write_audit_cache(annotation_dir: Path, audit: dict[str, Any]) -> Path:
    path = audit_cache_path(annotation_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit annotation label resolution")
    parser.add_argument("-a", "--annotations", required=True)
    parser.add_argument(
        "-s",
        "--structures",
        default="",
        help="structure_map.pkl path (structure_graph.json beside it)",
    )
    args = parser.parse_args()

    annotation_dir = Path(args.annotations.strip()).resolve()
    if not annotation_dir.is_dir():
        emit_result({"ok": False, "error": f"annotations dir not found: {annotation_dir}"})
        return 1

    structures_path = Path(args.structures.strip()) if args.structures.strip() else None
    structure_map: dict = {}
    catalog: dict[str, Any] | None = None
    if structures_path and structures_path.is_file():
        import pickle

        with open(structures_path, "rb") as f:
            structure_map = pickle.load(f)
        graph_path = structures_path.parent / "structure_graph.json"
        if graph_path.is_file():
            catalog = load_catalog(graph_path)

    try:
        audit = audit_align_leaf(annotation_dir, catalog, structure_map)
        emit_result({"ok": True, "audit": audit})
        return 0
    except Exception as exc:
        emit_result({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Parcellation-aware annotation matching for Isolate Regions ROI extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from belljar.annotation.relabel import get_slice_parcellation, load_parcellation_meta
from belljar.annotation.exclusion import id_path_contains
from belljar.atlas.catalog import FULL_DETAIL_TIER, ancestor_at_level


def _map_key(structure_map: dict, atlas_id: int):
    import numpy as np

    for key in (atlas_id, np.uint32(atlas_id)):
        if key in structure_map:
            return key
    return None


def _map_get(structure_map: dict, atlas_id: int):
    key = _map_key(structure_map, atlas_id)
    if key is None:
        return None
    return structure_map[key]


def is_layer_structure(structure_map: dict, atlas_id: int) -> bool:
    data = _map_get(structure_map, atlas_id)
    if not data:
        return False
    return "layer" in str(data.get("name", "")).lower()


def children_for_target(
    structure_map: dict,
    target_id: int,
    include_layers: bool,
) -> list[int]:
    tid = int(target_id)
    if include_layers:
        return [tid]
    out: list[int] = []
    for atlas_id in structure_map.keys():
        aid = int(atlas_id)
        if id_path_contains(structure_map, aid, tid):
            out.append(aid)
    if tid not in out:
        out.append(tid)
    return out


@dataclass
class ParcellationContext:
    tier_id: str | None = None
    st_level: int | None = None
    is_full_detail: bool = True
    excluded_region_ids: list[int] = field(default_factory=list)


def _entry_to_context(entry: dict[str, Any] | None) -> ParcellationContext:
    if not entry:
        return ParcellationContext(is_full_detail=True)
    tier_id = entry.get("tier_id")
    st_level = entry.get("st_level")
    if st_level is not None:
        st_level = int(st_level)
    excluded = entry.get("excluded_region_ids") or []
    excluded = [int(x) for x in excluded]
    is_full = tier_id == FULL_DETAIL_TIER or (
        tier_id is None and st_level is None and not excluded
    )
    return ParcellationContext(
        tier_id=tier_id if tier_id != FULL_DETAIL_TIER else None,
        st_level=st_level,
        is_full_detail=is_full,
        excluded_region_ids=excluded,
    )


def load_parcellation_context(
    annotation_dir: Path,
    slice_id: str | None = None,
) -> ParcellationContext:
    """Load parcellation context for one slice or run-wide default (full detail)."""
    annotation_dir = Path(annotation_dir)
    if slice_id is not None:
        entry = get_slice_parcellation(annotation_dir, slice_id)
        return _entry_to_context(entry)
    meta = load_parcellation_meta(annotation_dir)
    if not meta:
        return ParcellationContext(is_full_detail=True)
    entries = [v for v in meta.values() if isinstance(v, dict)]
    if not entries:
        return ParcellationContext(is_full_detail=True)
    first = _entry_to_context(entries[0])
    for entry in entries[1:]:
        ctx = _entry_to_context(entry)
        if (
            ctx.tier_id != first.tier_id
            or ctx.st_level != first.st_level
            or ctx.is_full_detail != first.is_full_detail
        ):
            # Mixed tiers — caller should use per-slice contexts; return first as hint.
            break
    return first


def normalize_contexts_per_slice(
    annotation_dir: Path,
    slice_ids: list[str],
) -> dict[str, ParcellationContext]:
    annotation_dir = Path(annotation_dir)
    out: dict[str, ParcellationContext] = {}
    for sid in slice_ids:
        out[sid] = load_parcellation_context(annotation_dir, sid)
    return out


def include_layers_allowed(context: ParcellationContext) -> bool:
    if context.is_full_detail:
        return True
    if context.tier_id == "layers":
        return True
    if context.st_level is not None and int(context.st_level) >= 11:
        return True
    return False


def resolve_selection_id(
    selected_id: int,
    context: ParcellationContext,
    catalog: dict[str, Any],
    structure_map: dict,
) -> int:
    """Map a user-selected atlas id to annotation label resolution."""
    sid = int(selected_id)
    if context.is_full_detail:
        return sid
    return ancestor_at_level(
        sid,
        catalog,
        tier_id=context.tier_id,
        st_level=context.st_level,
        structure_map=structure_map,
    )


def resolve_output_targets(
    structure_map: dict,
    selected_region_ids: list[int],
    include_layers: bool,
    context: ParcellationContext,
    catalog: dict[str, Any],
) -> dict[int, str]:
    """Map atlas id -> acronym for PKL filenames."""
    if context.is_full_detail:
        return _build_output_targets_full_detail(
            structure_map, selected_region_ids, include_layers
        )

    targets: dict[int, str] = {}
    if include_layers and include_layers_allowed(context):
        for raw_id in selected_region_ids:
            sid = int(raw_id)
            info = _map_get(structure_map, sid)
            if not info:
                continue
            if is_layer_structure(structure_map, sid):
                key = _map_key(structure_map, sid)
                if key is not None:
                    targets[int(key)] = info["acronym"]
            else:
                for atlas_id, data in structure_map.items():
                    aid = int(atlas_id)
                    if not is_layer_structure(structure_map, aid):
                        continue
                    if id_path_contains(structure_map, aid, sid):
                        targets[aid] = data["acronym"]
    else:
        for raw_id in selected_region_ids:
            resolved = resolve_selection_id(raw_id, context, catalog, structure_map)
            info = _map_get(structure_map, resolved)
            if info:
                key = _map_key(structure_map, resolved)
                if key is not None:
                    targets[int(key)] = info["acronym"]
    return targets


def _build_output_targets_full_detail(
    structure_map: dict,
    selected_region_ids: list[int],
    include_layers: bool,
) -> dict[int, str]:
    """Legacy full-detail target building."""
    selected_set = {int(x) for x in selected_region_ids}
    targets: dict[int, str] = {}

    if include_layers:
        for sid in selected_set:
            info = _map_get(structure_map, sid)
            if not info:
                continue
            if is_layer_structure(structure_map, sid):
                key = _map_key(structure_map, sid)
                if key is not None:
                    targets[int(key)] = info["acronym"]
            else:
                for atlas_id, data in structure_map.items():
                    aid = int(atlas_id)
                    if not is_layer_structure(structure_map, aid):
                        continue
                    if id_path_contains(structure_map, aid, sid):
                        targets[aid] = data["acronym"]
    else:
        for sid in selected_set:
            info = _map_get(structure_map, sid)
            if info:
                key = _map_key(structure_map, sid)
                if key is not None:
                    targets[int(key)] = info["acronym"]
    return targets


def atlas_ids_matching_target(
    structure_map: dict,
    target_id: int,
    include_layers: bool,
    context: ParcellationContext,
    catalog: dict[str, Any],
) -> set[int]:
    """Label IDs in the annotation array that contribute pixels to *target_id*."""
    tid = int(target_id)
    if context.is_full_detail:
        if include_layers:
            return {tid}
        return {int(x) for x in children_for_target(structure_map, tid, False)}

    resolved = resolve_selection_id(tid, context, catalog, structure_map)
    if include_layers and include_layers_allowed(context):
        if is_layer_structure(structure_map, tid):
            return {tid}
        out: set[int] = set()
        for atlas_id in structure_map.keys():
            aid = int(atlas_id)
            if is_layer_structure(structure_map, aid) and id_path_contains(
                structure_map, aid, tid
            ):
                out.add(aid)
        if not out:
            return {resolved}
        return out
    return {resolved}

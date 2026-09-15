"""Load Isolate Regions run config written by the intensity wizard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class IntensityConfig:
    selected_region_ids: list[int]
    include_layers: bool
    whole: bool
    use_dapi: bool
    input_dir: str
    annotation_dir: str
    output_dir: str
    dapi_dir: str
    slice_list: str


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    return default


def _as_int_list(value: Any) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def load_intensity_config(path: str | Path) -> IntensityConfig:
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("intensity config must be a JSON object")

    selected = _as_int_list(raw.get("selected_region_ids"))
    if not selected:
        raise ValueError("selected_region_ids must be a non-empty list")

    return IntensityConfig(
        selected_region_ids=selected,
        include_layers=_as_bool(raw.get("include_layers"), False),
        whole=_as_bool(raw.get("whole"), False),
        use_dapi=_as_bool(raw.get("use_dapi"), False),
        input_dir=str(raw.get("input_dir") or "").strip(),
        annotation_dir=str(raw.get("annotation_dir") or "").strip(),
        output_dir=str(raw.get("output_dir") or "").strip(),
        dapi_dir=str(raw.get("dapi_dir") or "").strip(),
        slice_list=str(raw.get("slice_list") or "").strip(),
    )


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


def id_path_contains(structure_map: dict, atlas_id: int, ancestor_id: int) -> bool:
    data = _map_get(structure_map, atlas_id)
    if not data:
        return False
    parts = [int(x) for x in str(data["id_path"]).split("/") if x]
    return ancestor_id in parts


def is_layer_structure(structure_map: dict, atlas_id: int) -> bool:
    data = _map_get(structure_map, atlas_id)
    if not data:
        return False
    return "layer" in str(data.get("name", "")).lower()


def build_output_targets(
    structure_map: dict,
    selected_region_ids: list[int],
    include_layers: bool,
    *,
    context=None,
    catalog=None,
) -> dict[int, str]:
    """Map atlas id -> acronym for PKL output filenames."""
    if context is not None and catalog is not None:
        from annotation_match import resolve_output_targets

        return resolve_output_targets(
            structure_map,
            selected_region_ids,
            include_layers,
            context,
            catalog,
        )
    selected_set = {int(x) for x in selected_region_ids}
    targets: dict[int, str] = {}

    if include_layers:
        for sid in selected_set:
            info = _map_get(structure_map, sid)
            if not info:
                continue
            if is_layer_structure(structure_map, sid):
                targets[sid] = info["acronym"]
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


def children_for_target(
    structure_map: dict,
    target_id: int,
    include_layers: bool,
) -> list[int]:
    """Atlas IDs whose pixels roll into target_id's output bucket."""
    tid = int(target_id)
    if include_layers:
        return [tid]

    out: list[int] = []
    for atlas_id, data in structure_map.items():
        aid = int(atlas_id)
        if id_path_contains(structure_map, aid, tid):
            out.append(aid)
    if tid not in out:
        out.append(tid)
    return out

"""Zero-out excluded CCF regions from annotation label arrays."""

from __future__ import annotations

import numpy as np


def _map_get(structure_map: dict, atlas_id: int):
    info = structure_map.get(atlas_id)
    if info is None:
        try:
            info = structure_map.get(np.uint32(atlas_id))
        except (ImportError, TypeError):
            pass
    return info


def id_path_contains(structure_map: dict, atlas_id: int, ancestor_id: int) -> bool:
    data = _map_get(structure_map, atlas_id)
    if not data:
        return False
    parts = [int(x) for x in str(data["id_path"]).split("/") if x]
    return ancestor_id in parts


def expand_excluded_ids(
    structure_map: dict,
    excluded_region_ids: list[int] | None,
    *,
    descendants: bool = True,
) -> set[int]:
    if not excluded_region_ids:
        return set()
    roots = {int(x) for x in excluded_region_ids}
    if not descendants:
        return roots
    out: set[int] = set()
    for atlas_id in structure_map.keys():
        aid = int(atlas_id)
        for root in roots:
            if aid == root or id_path_contains(structure_map, aid, root):
                out.add(aid)
                break
    return out


def apply_exclusion(
    label_array: np.ndarray,
    excluded_ids: set[int] | list[int],
) -> tuple[np.ndarray, int]:
    arr = np.asarray(label_array, dtype=np.uint32)
    if not excluded_ids:
        return arr.copy(), 0
    ex_set = {int(x) for x in excluded_ids}
    out = arr.copy()
    mask = np.isin(out, list(ex_set))
    excluded_pixels = int(np.count_nonzero(mask))
    if excluded_pixels:
        out[mask] = np.uint32(0)
    return out, excluded_pixels

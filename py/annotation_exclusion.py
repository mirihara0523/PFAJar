"""Zero-out excluded CCF regions from annotation label arrays."""

from __future__ import annotations

from typing import Any

import numpy as np

from region_config import id_path_contains


def expand_excluded_ids(
    structure_map: dict,
    excluded_region_ids: list[int] | None,
    *,
    descendants: bool = True,
) -> set[int]:
    """Return atlas ids to zero out (excluded roots plus optional descendants)."""
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
    """Zero pixels matching *excluded_ids*; return (array, excluded_pixel_count)."""
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


def expand_included_ids(
    structure_map: dict,
    included_region_ids: list[int] | None,
    *,
    descendants: bool = True,
) -> set[int]:
    """Return atlas ids to keep (included roots plus optional descendants)."""
    return expand_excluded_ids(
        structure_map,
        included_region_ids,
        descendants=descendants,
    )


def apply_inclusion(
    label_array: np.ndarray,
    included_ids: set[int] | list[int],
) -> tuple[np.ndarray, int]:
    """Zero pixels **not** in *included_ids*; return (array, zeroed_pixel_count)."""
    arr = np.asarray(label_array, dtype=np.uint32)
    if not included_ids:
        return arr.copy(), 0
    keep_set = {int(x) for x in included_ids}
    out = arr.copy()
    mask = ~np.isin(out, list(keep_set))
    zeroed_pixels = int(np.count_nonzero(mask & (out != 0)))
    if np.any(mask):
        out[mask] = np.uint32(0)
    return out, zeroed_pixels

"""Dispatch alignment warping with tissue keep masks and selectable gap strategies."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from align_tissue_mask import (
    WARP_MODE_AP_VERTICAL,
    WARP_MODE_CONSTRAINED_BSPLINE,
    WARP_MODE_DEFAULT,
    WARP_MODE_HYBRID,
    WARP_MODE_PER_ISLAND,
    WARP_MODE_PHASE1,
    WARP_MODE_REGION_DUAL,
    component_masks,
    gap_corridor_mask,
    keep_mask_stats,
    resize_mask_to_shape,
)
from demons import register_to_atlas
from slice_atlas import mask_slice_by_region


def _emit_log(message: str) -> None:
    print(f"LOG: {message}", flush=True)


def _damage_exclude_mask(damage_mask: np.ndarray | None) -> np.ndarray | None:
    if damage_mask is None:
        return None
    return (damage_mask > 0).astype(np.uint8)


def _register_pass(
    tissue: np.ndarray,
    section: np.ndarray,
    label: np.ndarray,
    structure_map_path: str,
    *,
    fixed_keep_mask: np.ndarray | None = None,
    damage_mask: np.ndarray | None = None,
    region_code: str | None = None,
    structure_map: dict | None = None,
):
    sec = np.array(section, copy=True)
    lab = np.array(label, copy=True)
    if region_code and region_code not in ("A", None) and structure_map is not None:
        sec, lab = mask_slice_by_region(sec, lab, structure_map, region_code)
    exclude = _damage_exclude_mask(damage_mask)
    return register_to_atlas(
        tissue,
        sec,
        lab,
        structure_map_path,
        fixed_keep_mask=fixed_keep_mask,
        moving_exclude_mask=exclude,
    )


def _composite_results(
    tissue: np.ndarray,
    passes: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    full_keep_mask: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h, w = tissue.shape[:2]
    labels = np.zeros((h, w), dtype=np.uint32)
    atlas = np.zeros((h, w), dtype=np.uint8)
    color = np.zeros((h, w, 3), dtype=np.uint8)
    for warped_labels, warped_atlas, color_label, cc_mask in passes:
        cc = resize_mask_to_shape(cc_mask, (h, w))
        inside = cc >= 128
        labels[inside] = warped_labels[inside]
        atlas[inside] = warped_atlas[inside]
        color[inside] = color_label[inside]
    if full_keep_mask is not None:
        keep = resize_mask_to_shape(full_keep_mask, (h, w))
        off = keep < 128
        labels[off] = 0
        atlas[off] = 0
        color[off] = 0
    return labels, atlas, color


def warp_phase1_only(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map=None,
    region_code="A",
):
    km = keep_mask
    if region_code and region_code != "A" and structure_map is not None:
        section, label = mask_slice_by_region(section, label, structure_map, region_code)
    return _register_pass(
        tissue,
        section,
        label,
        structure_map_path,
        fixed_keep_mask=km,
        damage_mask=damage_mask,
    )


def warp_per_island(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map=None,
    region_code="A",
):
    ccs = component_masks(keep_mask)
    if len(ccs) <= 1:
        _emit_log("align_warp_per_island_fallback single_component")
        return warp_phase1_only(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
            region_code=region_code,
        )
    sec = section
    lab = label
    if region_code and region_code != "A" and structure_map is not None:
        sec, lab = mask_slice_by_region(section, label, structure_map, region_code)
    passes = []
    for cc in ccs:
        wl, wa, cl = _register_pass(
            tissue,
            sec,
            lab,
            structure_map_path,
            fixed_keep_mask=cc,
            damage_mask=damage_mask,
        )
        passes.append((wl, wa, cl, cc))
    return _composite_results(tissue, passes, keep_mask)


def warp_region_dual(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map,
):
    ccs = component_masks(keep_mask)
    if len(ccs) < 2:
        _emit_log("align_warp_region_dual_fallback lt2_components")
        return warp_phase1_only(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
        )
    ccs_by_y = sorted(ccs, key=lambda cc: float(np.mean(np.where(cc >= 128)[0])))
    top_cc, bottom_cc = ccs_by_y[0], ccs_by_y[-1]
    passes = []
    for cc, region in ((top_cc, "C"), (bottom_cc, "NC")):
        wl, wa, cl = _register_pass(
            tissue,
            section,
            label,
            structure_map_path,
            fixed_keep_mask=cc,
            damage_mask=damage_mask,
            region_code=region,
            structure_map=structure_map,
        )
        passes.append((wl, wa, cl, cc))
    return _composite_results(tissue, passes, keep_mask)


def warp_ap_vertical_split(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map=None,
    region_code="A",
):
    stats = keep_mask_stats(keep_mask)
    centroids = stats.get("component_centroids") or []
    h = tissue.shape[0]
    if len(centroids) >= 2:
        ordered = sorted(centroids, key=lambda c: c[0])
        split_row = int(round((ordered[0][0] + ordered[1][0]) / 2.0))
    else:
        split_row = h // 2
    split_row = max(1, min(h - 1, split_row))
    top_mask = keep_mask.copy()
    bottom_mask = keep_mask.copy()
    top_mask[split_row:, :] = 0
    bottom_mask[:split_row, :] = 0
    if not (top_mask >= 128).any() or not (bottom_mask >= 128).any():
        _emit_log("align_warp_ap_vertical_split_fallback empty_half")
        return warp_phase1_only(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
            region_code=region_code,
        )
    sec = section
    lab = label
    if region_code and region_code != "A" and structure_map is not None:
        sec, lab = mask_slice_by_region(section, label, structure_map, region_code)
    passes = []
    for half_mask in (top_mask, bottom_mask):
        wl, wa, cl = _register_pass(
            tissue,
            sec,
            lab,
            structure_map_path,
            fixed_keep_mask=half_mask,
            damage_mask=damage_mask,
        )
        passes.append((wl, wa, cl, half_mask))
    return _composite_results(tissue, passes, keep_mask)


def warp_constrained_bspline(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map=None,
    region_code="A",
):
    refined_keep = np.array(keep_mask, copy=True)
    corridor = gap_corridor_mask(keep_mask)
    if corridor is not None:
        refined_keep[corridor >= 128] = 0
        _emit_log("align_warp_constrained_bspline gap_corridor_excluded")
    return warp_phase1_only(
        tissue,
        section,
        label,
        structure_map_path,
        keep_mask=refined_keep,
        damage_mask=damage_mask,
        structure_map=structure_map,
        region_code=region_code,
    )


def warp_hybrid_ab(
    tissue,
    section,
    label,
    structure_map_path,
    *,
    keep_mask,
    damage_mask,
    structure_map,
    region_code="A",
):
    stats = keep_mask_stats(keep_mask)
    if stats["n_components"] >= 2 and structure_map is not None:
        return warp_region_dual(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
        )
    return warp_phase1_only(
        tissue,
        section,
        label,
        structure_map_path,
        keep_mask=keep_mask,
        damage_mask=damage_mask,
        structure_map=structure_map,
        region_code=region_code,
    )


def warp_section_with_masks(
    tissue: np.ndarray,
    section: np.ndarray,
    label: np.ndarray,
    structure_map_path: str,
    *,
    keep_mask: np.ndarray | None,
    damage_mask: np.ndarray | None,
    warp_mode: str = WARP_MODE_DEFAULT,
    region_code: str = "A",
    structure_map: dict | None = None,
    slice_id: str = "",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Run registration with optional tissue keep mask and damage exclusion."""
    mode = warp_mode or WARP_MODE_DEFAULT
    stats = keep_mask_stats(keep_mask) if keep_mask is not None else {
        "n_components": 0,
        "keep_fraction": 0.0,
        "has_internal_gap": False,
    }
    meta: dict[str, Any] = {
        "tissue_mask_used": keep_mask is not None,
        "tissue_mask_warp_mode": mode,
        "keep_components": stats.get("n_components", 0),
        "damage_mask_applied": damage_mask is not None and bool(np.any(damage_mask)),
    }
    _emit_log(
        f"align_warp_mode slice={slice_id} mode={mode} "
        f"components={meta['keep_components']} damage={meta['damage_mask_applied']}"
    )

    if keep_mask is None:
        wl, wa, cl = _register_pass(
            tissue,
            section,
            label,
            structure_map_path,
            fixed_keep_mask=None,
            damage_mask=damage_mask,
            region_code=region_code,
            structure_map=structure_map,
        )
        meta["tissue_mask_used"] = False
        meta["tissue_mask_warp_mode"] = "standard"
        return wl, wa, cl, meta

    dispatch = {
        WARP_MODE_PHASE1: warp_phase1_only,
        WARP_MODE_PER_ISLAND: warp_per_island,
        WARP_MODE_REGION_DUAL: warp_region_dual,
        WARP_MODE_AP_VERTICAL: warp_ap_vertical_split,
        WARP_MODE_CONSTRAINED_BSPLINE: warp_constrained_bspline,
        WARP_MODE_HYBRID: warp_hybrid_ab,
    }
    fn = dispatch.get(mode, warp_hybrid_ab)
    if fn is warp_region_dual:
        wl, wa, cl = fn(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
        )
    elif fn is warp_hybrid_ab:
        wl, wa, cl = fn(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
            region_code=region_code,
        )
    else:
        wl, wa, cl = fn(
            tissue,
            section,
            label,
            structure_map_path,
            keep_mask=keep_mask,
            damage_mask=damage_mask,
            structure_map=structure_map,
            region_code=region_code,
        )
    return wl, wa, cl, meta

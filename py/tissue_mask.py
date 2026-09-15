"""Tissue isolation mask shared by DAPI cleanup and tissue edge cleanup."""

from __future__ import annotations

import cv2
import numpy as np
from scipy.ndimage import binary_dilation, binary_erosion, binary_fill_holes
from skimage.measure import label, regionprops
from skimage.morphology import binary_closing, binary_opening, disk, remove_small_objects


def parse_stroke_points(stroke_raw) -> list[tuple[int, int]]:
    """Accept [[x,y]], [{x,y}], or {points: [...]}."""
    if isinstance(stroke_raw, dict):
        stroke_raw = stroke_raw.get("points") or stroke_raw.get("stroke") or []
    if not isinstance(stroke_raw, (list, tuple)):
        return []
    out: list[tuple[int, int]] = []
    for p in stroke_raw:
        if isinstance(p, dict):
            x = p.get("x", p.get(0))
            y = p.get("y", p.get(1))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = p[0], p[1]
        else:
            continue
        out.append((int(x), int(y)))
    return out


def isolate_tissue_mask(
    gray_u8: np.ndarray,
    *,
    edge_shrink_px: int = 0,
    min_object_size: int | None = None,
    opening_disk: int = 3,
) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray_u8, (5, 5), 0)
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = otsu > 0
    # Tissue is the BRIGHTER region in DAPI fluorescence previews (signal on a dark
    # background). Keep the Otsu class whose mean intensity is higher; flip only when
    # the selected class is the darker one. (Earlier code assumed dark tissue on a
    # bright field, which inverted the mask for every DAPI slice -> all-remove.)
    if float(np.mean(gray_u8[mask])) < float(np.mean(gray_u8[~mask])):
        mask = ~mask
    se = disk(max(1, int(opening_disk)))
    mask = binary_closing(mask, se)
    mask = binary_opening(mask, se)
    labeled = label(mask)
    if labeled.max() == 0:
        return mask.astype(bool)
    regions = regionprops(labeled)
    largest = max(regions, key=lambda r: r.area)
    tissue = labeled == largest.label
    min_size = min_object_size
    if min_size is None:
        min_size = 64
    tissue = remove_small_objects(tissue, min_size=int(min_size))
    tissue = binary_fill_holes(tissue)
    # Positive = shrink the keep-mask inward (erode) to trim boundary bleed;
    # negative = grow it outward (dilate) to recover tissue trimmed too tightly.
    if edge_shrink_px > 0:
        tissue = binary_erosion(tissue, iterations=int(edge_shrink_px))
    elif edge_shrink_px < 0:
        tissue = binary_dilation(tissue, iterations=int(-edge_shrink_px))
    return tissue.astype(bool)


def ensure_keep_mask_polarity(gray_u8: np.ndarray, keep_u8: np.ndarray) -> np.ndarray:
    """Ensure keep mask (255 = tissue) matches brighter tissue in gray image."""
    keep = keep_u8.astype(np.uint8)
    if keep.size == 0:
        return keep
    on = keep >= 128
    off = ~on
    if not on.any() or not off.any():
        return keep
    mean_on = float(np.mean(gray_u8[on]))
    mean_off = float(np.mean(gray_u8[off]))
    # Keep mask should mark the BRIGHTER tissue (DAPI signal). If the kept pixels are
    # darker than the rest, the polarity is backwards -> invert. (Earlier code inverted
    # when keep was brighter, which contradicts the docstring and flipped every DAPI
    # tissue mask onto the dark background.)
    if mean_on < mean_off:
        return (255 - keep).astype(np.uint8)
    return keep


def wizard_mask_kwargs(gray_u8: np.ndarray, edge_shrink_px: int = 2) -> dict:
    """Defaults for tissue cleanup wizard auto/guided paths."""
    h, w = gray_u8.shape
    min_area = max(64, int(h * w * 0.0005))
    return {
        "edge_shrink_px": int(edge_shrink_px),
        "min_object_size": min_area,
        "opening_disk": 4,
    }

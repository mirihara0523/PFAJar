"""Load tissue edge-cleanup keep masks and warp-mode constants for alignment."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from skimage.measure import label as sk_label
from skimage.measure import regionprops

WARP_MODE_HYBRID = "hybrid_ab"
WARP_MODE_PER_ISLAND = "per_island"
WARP_MODE_REGION_DUAL = "region_dual"
WARP_MODE_AP_VERTICAL = "ap_vertical_split"
WARP_MODE_CONSTRAINED_BSPLINE = "constrained_bspline"
WARP_MODE_PHASE1 = "phase1_only"

WARP_MODE_DEFAULT = WARP_MODE_HYBRID

WARP_MODE_CHOICES: list[tuple[str, str]] = [
    (
        WARP_MODE_HYBRID,
        "Recommended: hybrid (islands + region split)",
    ),
    (WARP_MODE_PER_ISLAND, "Per-island registration + composite"),
    (WARP_MODE_REGION_DUAL, "Region-filter dual pass"),
    (WARP_MODE_AP_VERTICAL, "AP-anchored vertical split"),
    (WARP_MODE_CONSTRAINED_BSPLINE, "Constrained B-spline (gap-aware)"),
    (WARP_MODE_PHASE1, "Masked metric only (minimal)"),
]

WARP_MODE_LABELS = {mode_id: label for mode_id, label in WARP_MODE_CHOICES}

ARCHIVE_MASK_DIR = "tissue_cleanup_masks"
DRAFT_MASK_DIR = "tissue_cleanup_draft/masks"
MASK_LOG_FILENAME = "alignment_mask_log.jsonl"

MIN_COMPONENT_AREA = 64


def resolve_bundle_root_from_dapi_dir(input_path: str | Path) -> Path | None:
    """Walk parents from the DAPI input folder to find a Mason Jar bundle root."""
    start = Path(input_path).resolve()
    if not start.is_dir():
        start = start.parent
    for candidate in [start, *start.parents]:
        mason_meta = candidate / ".masonjar"
        if mason_meta.is_dir():
            return candidate
        bell_meta = candidate / ".belljar"
        if bell_meta.is_dir():
            return candidate
        for project_file in candidate.glob("*.masonjar"):
            if project_file.is_file():
                return candidate
        for project_file in candidate.glob("*.belljar"):
            if project_file.is_file():
                return candidate
        counting = candidate / "data" / "counting"
        if counting.is_dir() and (counting / "00_dapi").is_dir():
            return candidate
    return None


def _meta_dir(bundle_root: Path) -> Path:
    for name in (".masonjar", ".belljar"):
        p = bundle_root / name
        if p.is_dir():
            return p
    return bundle_root / ".masonjar"


def archive_mask_path(bundle_root: Path, slice_id: str) -> Path:
    return _meta_dir(bundle_root) / ARCHIVE_MASK_DIR / f"{slice_id}.png"


def draft_mask_path(bundle_root: Path, slice_id: str) -> Path:
    return _meta_dir(bundle_root) / DRAFT_MASK_DIR / f"{slice_id}.png"


def load_grayscale_mask(path: Path) -> np.ndarray:
    arr = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise ValueError(f"Could not read mask: {path}")
    return arr


def load_keep_mask(bundle_root: Path, slice_id: str) -> tuple[np.ndarray | None, str | None]:
    """Return (keep_mask_u8, source) where source is archive or draft."""
    archive = archive_mask_path(bundle_root, slice_id)
    if archive.is_file():
        return load_grayscale_mask(archive), "archive"
    draft = draft_mask_path(bundle_root, slice_id)
    if draft.is_file():
        return load_grayscale_mask(draft), "draft"
    return None, None


def mask_is_trivial(keep_mask: np.ndarray) -> bool:
    """True when essentially all pixels are keep (>=128)."""
    if keep_mask.size == 0:
        return True
    return float(np.mean(keep_mask >= 128)) > 0.995


def keep_mask_stats(keep_mask: np.ndarray, min_area: int = MIN_COMPONENT_AREA) -> dict[str, Any]:
    """Summarize connected keep components (green >= 128)."""
    binary = (keep_mask >= 128).astype(np.uint8)
    if not binary.any():
        return {
            "n_components": 0,
            "keep_fraction": 0.0,
            "has_internal_gap": False,
            "component_centroids": [],
            "component_areas": [],
        }
    labeled = sk_label(binary, connectivity=2)
    props = regionprops(labeled)
    areas = []
    centroids = []
    for prop in props:
        if prop.area >= min_area:
            areas.append(int(prop.area))
            centroids.append((float(prop.centroid[0]), float(prop.centroid[1])))
    ordered = sorted(zip(areas, centroids), key=lambda item: item[0], reverse=True)
    areas = [a for a, _ in ordered]
    centroids = [c for _, c in ordered]
    n = len(areas)
    return {
        "n_components": n,
        "keep_fraction": float(binary.mean()),
        "has_internal_gap": n >= 2,
        "component_centroids": centroids,
        "component_areas": areas,
    }


def component_masks(keep_mask: np.ndarray, min_area: int = MIN_COMPONENT_AREA) -> list[np.ndarray]:
    """Binary uint8 masks (255 inside component) sorted by descending area."""
    binary = (keep_mask >= 128).astype(np.uint8)
    labeled = sk_label(binary, connectivity=2)
    props = regionprops(labeled)
    items: list[tuple[int, np.ndarray]] = []
    for prop in props:
        if prop.area < min_area:
            continue
        cc = np.zeros_like(binary, dtype=np.uint8)
        cc[labeled == prop.label] = 255
        items.append((prop.area, cc))
    items.sort(key=lambda item: item[0], reverse=True)
    return [cc for _, cc in items]


def gap_corridor_mask(keep_mask: np.ndarray) -> np.ndarray | None:
    """Pixels inside the keep-mask bounding box but outside keep regions."""
    binary = keep_mask >= 128
    if not binary.any():
        return None
    ys, xs = np.where(binary)
    corridor = np.zeros_like(keep_mask, dtype=np.uint8)
    corridor[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] = 255
    corridor[binary] = 0
    if not corridor.any():
        return None
    return corridor


def resize_mask_to_shape(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    if mask.shape[0] == h and mask.shape[1] == w:
        return mask
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)


def append_alignment_mask_log(output_leaf: Path, record: dict[str, Any]) -> None:
    """Append one JSON line to ``<output>/.masonjar/alignment_mask_log.jsonl``."""
    log_dir = output_leaf / ".masonjar"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / MASK_LOG_FILENAME
    payload = dict(record)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def warp_mode_index(mode_id: str) -> int:
    for idx, (mid, _) in enumerate(WARP_MODE_CHOICES):
        if mid == mode_id:
            return idx
    return 0

"""Per-section tissue layout detection for atlas alignment (whole vs left hemi)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tissue_mask import isolate_tissue_mask

# Fraction of tissue pixels in each image half above which we treat both as occupied.
BOTH_HALVES_TISSUE_FRAC = 0.12
# Dominant half must exceed this share of tissue pixels to call single-hemi.
DOMINANT_HALF_FRAC = 0.75
# Tissue bbox width / image width above which we prefer whole-brain layout.
WHOLE_BBOX_WIDTH_RATIO = 0.70
# Confidence below this triggers a Napari review hint (still defaults to whole).
LOW_CONFIDENCE_THRESHOLD = 0.55


@dataclass
class TissueLayoutResult:
    """Detected layout for one DAPI section."""

    layout: str  # "whole" | "left_hemi"
    hemisphere: str  # "W" | "L" — atlas/tissue layout code used by map.py
    confidence: float
    low_confidence: bool
    metrics: dict[str, Any] = field(default_factory=dict)


def layout_to_hemisphere(layout: str) -> str:
    return "W" if layout == "whole" else "L"


def crop_planar_for_hemisphere(image: np.ndarray, hemisphere: str) -> np.ndarray:
    """Crop a 2D atlas/tissue plane to left hemisphere when hemisphere is L."""
    if hemisphere != "L" or image.ndim != 2:
        return image
    half = image.shape[1] // 2
    if half <= 0:
        return image
    return image[:, :half]


def detect_tissue_layout_from_gray(gray_u8: np.ndarray) -> TissueLayoutResult:
    """Classify whole-brain vs left-hemisphere presentation from a DAPI grayscale image."""
    h, w = gray_u8.shape[:2]
    if h < 8 or w < 8:
        return TissueLayoutResult(
            layout="whole",
            hemisphere="W",
            confidence=0.0,
            low_confidence=True,
            metrics={"reason": "image_too_small", "height": h, "width": w},
        )

    tissue = isolate_tissue_mask(gray_u8, edge_shrink_px=0, opening_disk=3)
    tissue_px = int(tissue.sum())
    if tissue_px < 64:
        return TissueLayoutResult(
            layout="whole",
            hemisphere="W",
            confidence=0.0,
            low_confidence=True,
            metrics={"reason": "no_tissue", "tissue_px": tissue_px},
        )

    mid = w // 2
    left_px = int(tissue[:, :mid].sum())
    right_px = int(tissue[:, mid:].sum())
    left_frac = left_px / tissue_px
    right_frac = right_px / tissue_px

    ys, xs = np.where(tissue)
    x_min, x_max = int(xs.min()), int(xs.max())
    bbox_width_ratio = (x_max - x_min + 1) / float(w)
    centroid_x_norm = float(xs.mean()) / float(max(w - 1, 1))

    metrics = {
        "tissue_px": tissue_px,
        "left_frac": round(left_frac, 4),
        "right_frac": round(right_frac, 4),
        "bbox_width_ratio": round(bbox_width_ratio, 4),
        "centroid_x_norm": round(centroid_x_norm, 4),
    }

    both_halves = (
        left_frac >= BOTH_HALVES_TISSUE_FRAC and right_frac >= BOTH_HALVES_TISSUE_FRAC
    )
    wide_bbox = bbox_width_ratio >= WHOLE_BBOX_WIDTH_RATIO

    if both_halves or wide_bbox:
        confidence = min(1.0, max(left_frac, right_frac) + (0.15 if wide_bbox else 0.0))
        return TissueLayoutResult(
            layout="whole",
            hemisphere="W",
            confidence=round(confidence, 4),
            low_confidence=confidence < LOW_CONFIDENCE_THRESHOLD,
            metrics=metrics,
        )

    if left_frac >= DOMINANT_HALF_FRAC:
        confidence = left_frac
        return TissueLayoutResult(
            layout="left_hemi",
            hemisphere="L",
            confidence=round(confidence, 4),
            low_confidence=confidence < LOW_CONFIDENCE_THRESHOLD,
            metrics=metrics,
        )

    if right_frac >= DOMINANT_HALF_FRAC:
        # Right-dominant tissue on slide — v1 maps to left-atlas hemi crop (legacy orient).
        confidence = right_frac * 0.85
        return TissueLayoutResult(
            layout="left_hemi",
            hemisphere="L",
            confidence=round(confidence, 4),
            low_confidence=True,
            metrics={**metrics, "note": "right_dominant_mapped_to_L"},
        )

    # Ambiguous / damaged — conservative whole default.
    return TissueLayoutResult(
        layout="whole",
        hemisphere="W",
        confidence=round(max(left_frac, right_frac), 4),
        low_confidence=True,
        metrics={**metrics, "reason": "ambiguous"},
    )


def detect_tissue_layout(image_path: str | Path) -> TissueLayoutResult:
    path = Path(image_path)
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return TissueLayoutResult(
            layout="whole",
            hemisphere="W",
            confidence=0.0,
            low_confidence=True,
            metrics={"reason": "read_failed", "path": str(path)},
        )
    return detect_tissue_layout_from_gray(gray)


def parse_layout_mode(value: str | None) -> str:
    """Parse Align layout mode from IPC/CLI (-w): auto | whole | hemi."""
    raw = (value or "auto").strip().lower()
    if raw == "auto":
        return "auto"
    if raw in ("true", "1", "yes", "whole"):
        return "whole"
    if raw in ("false", "0", "no", "hemi", "half"):
        return "hemi"
    return "auto"

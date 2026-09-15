"""Orientation fingerprinting for geometry repair (cross-channel safe)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from apply_geometry import apply_ops_to_array, compose_ops_from_spec, ops_from_string_list
from czi_common import resolve_original_zstack_path

MAX_EDGE = 256
PROFILE_ROWS = 8
MIN_CONFIDENCE_MARGIN = 0.12

# 8 orientation variants as JS op lists applied on top of identity read.
ORIENTATION_VARIANTS: list[tuple[str, list[str]]] = [
    ("identity", []),
    ("rot90", ["rot90"]),
    ("rot180", ["rot90", "rot90"]),
    ("rot270", ["rot90", "rot90", "rot90"]),
    ("flipX", ["flipX"]),
    ("flipY", ["flipY"]),
    ("rot90_flipX", ["rot90", "flipX"]),
    ("rot90_flipY", ["rot90", "flipY"]),
]


def _to_gray_uint8(arr: np.ndarray) -> np.ndarray:
    out = np.asarray(arr)
    if out.ndim == 3:
        if out.shape[0] <= 4 and out.shape[0] < out.shape[-1]:
            out = np.max(out, axis=0)
        elif out.shape[-1] in (3, 4):
            out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        else:
            out = out[0]
    if out.dtype != np.uint8:
        out = out.astype(np.float64)
        lo, hi = float(np.min(out)), float(np.max(out))
        if hi > lo:
            out = (out - lo) / (hi - lo) * 255.0
        out = out.astype(np.uint8)
    return out


def downsample_plane(arr: np.ndarray, max_edge: int = MAX_EDGE) -> np.ndarray:
    gray = _to_gray_uint8(arr)
    h, w = gray.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale < 1.0:
        gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return gray


def tissue_mask(gray: np.ndarray) -> np.ndarray:
    if gray.size == 0:
        return np.zeros_like(gray, dtype=bool)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(np.mean(mask > 0)) < 0.02:
        thresh = float(np.percentile(blur, 75))
        mask = (blur >= thresh).astype(np.uint8) * 255
    return mask > 0


def mask_occupancy_profile(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    n = PROFILE_ROWS
    row_h = max(1, h // n)
    row_prof = []
    for i in range(n):
        y0 = i * row_h
        y1 = h if i == n - 1 else (i + 1) * row_h
        row_prof.append(float(np.mean(mask[y0:y1, :])))
    col_w = max(1, w // n)
    col_prof = []
    for i in range(n):
        x0 = i * col_w
        x1 = w if i == n - 1 else (i + 1) * col_w
        col_prof.append(float(np.mean(mask[:, x0:x1])))
    aspect = float(w) / float(h) if h else 1.0
    area = float(np.mean(mask))
    cy, cx = np.argwhere(mask).mean(axis=0) if np.any(mask) else (h / 2, w / 2)
    centroid = np.array([cy / max(h, 1), cx / max(w, 1)], dtype=np.float64)
    vec = np.concatenate([row_prof, col_prof, [aspect, area], centroid])
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-9 else vec


def intensity_profile(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    n = PROFILE_ROWS
    row_h = max(1, h // n)
    row_prof = []
    for i in range(n):
        y0 = i * row_h
        y1 = h if i == n - 1 else (i + 1) * row_h
        row_prof.append(float(np.mean(gray[y0:y1, :])) / 255.0)
    col_w = max(1, w // n)
    col_prof = []
    for i in range(n):
        x0 = i * col_w
        x1 = w if i == n - 1 else (i + 1) * col_w
        col_prof.append(float(np.mean(gray[:, x0:x1])) / 255.0)
    aspect = float(w) / float(h) if h else 1.0
    vec = np.concatenate([row_prof, col_prof, [aspect]])
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-9 else vec


def profile_distance(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    return float(np.mean(np.abs(a[:n] - b[:n])))


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = cv2.resize(b.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 0.0
    return float(inter) / float(union)


def read_preview_plane(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    if path.suffix.lower() == ".png":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        return downsample_plane(np.asarray(img))
    return None


def best_orientation_structural(
    plane: np.ndarray,
    reference_mask: np.ndarray,
) -> tuple[str, float, float]:
    """Return (variant_name, best_score, margin_to_second)."""
    scores: list[tuple[str, float]] = []
    ref_mask = reference_mask
    for name, op_list in ORIENTATION_VARIANTS:
        ops = ops_from_string_list(op_list)
        transformed = apply_ops_to_array(plane, ops) if ops else plane
        mask = tissue_mask(transformed)
        iou = mask_iou(mask, ref_mask)
        occ = mask_occupancy_profile(mask)
        ref_occ = mask_occupancy_profile(ref_mask)
        dist = profile_distance(occ, ref_occ)
        score = 0.65 * iou + 0.35 * (1.0 - min(1.0, dist))
        scores.append((name, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    return best_name, best_score, best_score - second


def best_orientation_within_channel(plane: np.ndarray, reference_plane: np.ndarray) -> tuple[str, float, float]:
    ref_gray = downsample_plane(reference_plane)
    ref_prof = intensity_profile(ref_gray)
    ref_mask = tissue_mask(ref_gray)
    scores: list[tuple[str, float]] = []
    for name, op_list in ORIENTATION_VARIANTS:
        ops = ops_from_string_list(op_list)
        transformed = apply_ops_to_array(plane, ops) if ops else plane
        gray = downsample_plane(transformed)
        int_score = 1.0 - min(1.0, profile_distance(intensity_profile(gray), ref_prof))
        mask_score = mask_iou(tissue_mask(gray), ref_mask)
        score = 0.4 * int_score + 0.6 * mask_score
        scores.append((name, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    return best_name, best_score, best_score - second


def variant_to_extra_ops(variant: str) -> list[str]:
    for name, ops in ORIENTATION_VARIANTS:
        if name == variant:
            return ops[:]
    return []


def ops_list_to_variant(ops: list[str]) -> str:
    target = list(ops or [])
    for name, op_list in ORIENTATION_VARIANTS:
        if op_list == target:
            return name
    return "unknown"


@dataclass
class ChannelProbe:
    branch: str
    rel_path: str
    best_variant: str
    confidence: float
    present: bool


def probe_slice_channels(
    bundle_root: Path,
    slice_id: str,
    channel_paths: list[tuple[str, str]],
    reference_branch: str,
    pending_ops: list[str],
    per_branch_reference_planes: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Probe all channel previews for one slice."""
    channel_results: list[dict[str, Any]] = []
    variants: dict[str, str] = {}
    confidences: list[float] = []

    ref_branch_plane = None
    for branch, rel in channel_paths:
        if branch == reference_branch:
            p = bundle_root / rel
            plane = read_preview_plane(p)
            if plane is not None:
                ref_branch_plane = plane
                break

    ref_mask = tissue_mask(ref_branch_plane) if ref_branch_plane is not None else None

    for branch, rel in channel_paths:
        abs_path = bundle_root / rel
        plane = read_preview_plane(abs_path)
        entry: dict[str, Any] = {
            "branch": branch,
            "rel_path": rel,
            "present": plane is not None,
            "on_disk_orientation": "missing",
            "suggested_strategy": "skip",
        }
        if plane is None:
            channel_results.append(entry)
            continue

        if branch in per_branch_reference_planes:
            ref_plane = per_branch_reference_planes[branch]
            variant, score, margin = best_orientation_within_channel(plane, ref_plane)
        elif ref_mask is not None and branch != reference_branch:
            variant, score, margin = best_orientation_structural(plane, ref_mask)
        else:
            variant, score, margin = ("identity", 0.5, 0.0)

        variants[branch] = variant
        conf = margin if margin >= MIN_CONFIDENCE_MARGIN else margin * 0.5
        confidences.append(conf)
        pending_variant = ops_list_to_variant(pending_ops)
        if variant == pending_variant:
            entry["on_disk_orientation"] = "matches_pending_ops"
            entry["suggested_strategy"] = "skip"
        elif variant == "identity" and pending_ops:
            entry["on_disk_orientation"] = "unrotated"
            entry["suggested_strategy"] = "transform_original"
        else:
            entry["on_disk_orientation"] = "unknown"
            suggested = "derivatives_from_original"
            if (
                branch == "dapi"
                and resolve_original_zstack_path(bundle_root, slice_id, branch) is None
            ):
                suggested = "transform_original"
            entry["suggested_strategy"] = suggested

        entry["best_variant"] = variant
        entry["confidence"] = round(score, 3)
        channel_results.append(entry)

    unique_variants = set(variants.values())
    non_identity_variants = unique_variants - {"identity"}
    structural_confidence = float(np.mean(confidences)) if confidences else 0.0
    issue = "ok"
    needs_manual = False
    suggested_ops: list[str] = []
    if len(unique_variants) > 1:
        issue = "cross_channel_mismatch"
        needs_manual = True
    elif structural_confidence < MIN_CONFIDENCE_MARGIN:
        issue = "low_confidence"
        needs_manual = True
    elif non_identity_variants:
        issue = "consistent_reorient"
        if len(non_identity_variants) == 1 and structural_confidence >= MIN_CONFIDENCE_MARGIN:
            only_variant = next(iter(non_identity_variants))
            suggested_ops = variant_to_extra_ops(only_variant)
            needs_manual = False
        else:
            if len(non_identity_variants) == 1:
                suggested_ops = variant_to_extra_ops(next(iter(non_identity_variants)))
            needs_manual = True

    auto_repairable = issue not in ("ok",) and not needs_manual

    return {
        "slice_id": slice_id,
        "pending_ops": pending_ops,
        "suggested_ops": suggested_ops,
        "issue": issue,
        "needs_manual_review": needs_manual,
        "structural_confidence": round(structural_confidence, 3),
        "auto_repairable": auto_repairable,
        "channels": channel_results,
        "confirmed_ops": None,
    }

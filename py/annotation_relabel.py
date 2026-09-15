"""Roll annotation label arrays up/down the CCF hierarchy (Viewer/Editor parcellation)."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from structure_catalog import (
    FULL_DETAIL_TIER,
    TIER_DEFS,
    ancestor_at_level,
    list_tiers,
)

PARCELLATION_META_FILENAME = "annotation_parcellation.json"
FULL_BACKUP_DIRNAME = "annotation_full"


@dataclass
class RelabelResult:
    """Outcome of relabel_to_target."""

    label_array: np.ndarray
    pixels_changed: int
    unknown_ids: list[int]
    unchanged_ids: int


def align_leaf_from_annotation_dir(annotation_dir: Path) -> Path:
    """Resolve align output leaf from an annotation directory path."""
    return Path(annotation_dir).resolve()


def parcellation_meta_path(annotation_dir: Path) -> Path:
    return align_leaf_from_annotation_dir(annotation_dir) / ".masonjar" / PARCELLATION_META_FILENAME


def full_backup_dir(annotation_dir: Path) -> Path:
    return align_leaf_from_annotation_dir(annotation_dir) / ".masonjar" / FULL_BACKUP_DIRNAME


def full_backup_path(annotation_dir: Path, slice_id: str) -> Path:
    safe = slice_id.replace("/", "_")
    return full_backup_dir(annotation_dir) / f"{safe}.pkl"


def load_parcellation_meta(annotation_dir: Path) -> dict[str, Any]:
    path = parcellation_meta_path(annotation_dir)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_parcellation_meta(annotation_dir: Path, meta: dict[str, Any]) -> None:
    path = parcellation_meta_path(annotation_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)


def get_slice_parcellation(annotation_dir: Path, slice_id: str) -> dict[str, Any] | None:
    entry = load_parcellation_meta(annotation_dir).get(slice_id)
    return entry if isinstance(entry, dict) else None


def set_slice_parcellation(
    annotation_dir: Path,
    slice_id: str,
    *,
    tier_id: str | None,
    st_level: int | None,
) -> None:
    meta = load_parcellation_meta(annotation_dir)
    meta[slice_id] = {
        "tier_id": tier_id,
        "st_level": st_level,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    save_parcellation_meta(annotation_dir, meta)


def clear_slice_parcellation(annotation_dir: Path, slice_id: str) -> None:
    meta = load_parcellation_meta(annotation_dir)
    if slice_id in meta:
        del meta[slice_id]
        save_parcellation_meta(annotation_dir, meta)


def ensure_full_backup(
    annotation_dir: Path,
    slice_id: str,
    label_array: np.ndarray,
) -> Path:
    """Write full-detail backup once per slice if missing."""
    path = full_backup_path(annotation_dir, slice_id)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(np.asarray(label_array, dtype=np.uint32), f)
    return path


def load_full_backup(annotation_dir: Path, slice_id: str) -> np.ndarray | None:
    path = full_backup_path(annotation_dir, slice_id)
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return np.asarray(pickle.load(f), dtype=np.uint32)


def has_full_backup(annotation_dir: Path, slice_id: str) -> bool:
    return full_backup_path(annotation_dir, slice_id).is_file()


def build_relabel_lut(
    source_ids: np.ndarray | list[int],
    catalog: dict[str, Any],
    *,
    tier_id: str | None = None,
    st_level: int | None = None,
    structure_map: dict | None = None,
) -> dict[int, int]:
    """Build source atlas id → target atlas id lookup."""
    lut: dict[int, int] = {0: 0}
    for sid in np.unique(np.asarray(source_ids, dtype=np.uint64)):
        sid_int = int(sid)
        if sid_int == 0:
            continue
        lut[sid_int] = ancestor_at_level(
            sid_int,
            catalog,
            tier_id=tier_id,
            st_level=st_level,
            structure_map=structure_map,
        )
    return lut


def apply_relabel_lut(label_array: np.ndarray, lut: dict[int, int]) -> np.ndarray:
    """Remap *label_array* through *lut* (unknown ids unchanged)."""
    arr = np.asarray(label_array, dtype=np.uint32)
    out = arr.copy()
    for src, dst in lut.items():
        if src == dst:
            continue
        out[arr == np.uint32(src)] = np.uint32(dst)
    return out


def relabel_to_target(
    label_array: np.ndarray,
    catalog: dict[str, Any],
    *,
    tier_id: str | None = None,
    st_level: int | None = None,
    structure_map: dict | None = None,
) -> RelabelResult:
    """Roll up label pixels to *tier_id* or raw *st_level*."""
    arr = np.asarray(label_array, dtype=np.uint32)
    if tier_id == FULL_DETAIL_TIER and st_level is None:
        return RelabelResult(
            label_array=arr.copy(),
            pixels_changed=0,
            unknown_ids=[],
            unchanged_ids=int(np.count_nonzero(arr)),
        )

    unique = np.unique(arr)
    lut = build_relabel_lut(
        unique,
        catalog,
        tier_id=tier_id,
        st_level=st_level,
        structure_map=structure_map,
    )

    unknown_ids: list[int] = []
    unchanged = 0
    by_id = catalog.get("by_id") or {}
    for sid in unique:
        sid_int = int(sid)
        if sid_int == 0:
            continue
        in_catalog = sid_int in by_id
        if not in_catalog and structure_map:
            in_catalog = sid_int in structure_map or any(
                int(k) == sid_int for k in structure_map.keys()
            )
        if not in_catalog:
            unknown_ids.append(sid_int)
            lut[sid_int] = sid_int
        elif lut[sid_int] == sid_int:
            unchanged += 1

    out = apply_relabel_lut(arr, lut)
    changed_mask = out != arr
    pixels_changed = int(np.count_nonzero(changed_mask))

    return RelabelResult(
        label_array=out,
        pixels_changed=pixels_changed,
        unknown_ids=sorted(unknown_ids),
        unchanged_ids=unchanged,
    )


def parcellation_target_label(
    catalog: dict[str, Any],
    *,
    tier_id: str | None,
    st_level: int | None,
    ccf_advanced: bool,
) -> str:
    """Human-readable label for the current parcellation target."""
    if tier_id == FULL_DETAIL_TIER or (tier_id is None and st_level is None):
        return "Full detail"
    if ccf_advanced and st_level is not None:
        return f"Level {st_level}"
    if tier_id:
        for tier in list_tiers(catalog):
            if tier["id"] == tier_id:
                return tier["label"]
        for tier in TIER_DEFS:
            if tier["id"] == tier_id:
                return tier["label"]
    return "Unknown level"


def colorize_labels(label_array: np.ndarray, structure_map: dict) -> np.ndarray:
    """RGB uint8 (H, W, 3) false-color image for warped label arrays."""
    arr = np.asarray(label_array, dtype=np.uint32)
    h, w = arr.shape
    out = np.zeros((h, w, 3), dtype=np.uint8)
    for label_value, info in structure_map.items():
        lid = int(label_value)
        mask = arr == np.uint32(lid)
        if np.any(mask):
            out[mask] = info["color"]
    return out


def format_applied_parcellation(entry: dict[str, Any] | None, catalog: dict[str, Any]) -> str:
    if not entry:
        return "Full detail (not applied)"
    tier_id = entry.get("tier_id")
    st_level = entry.get("st_level")
    applied_at = entry.get("applied_at", "")
    label = parcellation_target_label(
        catalog,
        tier_id=tier_id if tier_id != FULL_DETAIL_TIER else None,
        st_level=int(st_level) if st_level is not None else None,
        ccf_advanced=tier_id is None and st_level is not None,
    )
    if applied_at:
        return f"{label} (applied {applied_at[:19]})"
    return label

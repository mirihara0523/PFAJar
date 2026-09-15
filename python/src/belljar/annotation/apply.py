"""Headless CCF parcellation apply (batch, wizard, Adjust bulk)."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

import re

from belljar.annotation.exclusion import apply_exclusion, expand_excluded_ids
from belljar.annotation.relabel import (
    FULL_DETAIL_TIER,
    clear_slice_parcellation,
    ensure_full_backup,
    load_full_backup,
    load_parcellation_meta,
    relabel_to_target,
    save_parcellation_meta,
)
from belljar.atlas.catalog import load_catalog

ANNOTATION_PKL_RE = re.compile(r"^Annotation_(.+)\.pkl$", re.IGNORECASE)


def slice_stem_from_annotation_pkl(filename: str) -> str | None:
    m = ANNOTATION_PKL_RE.match(filename)
    return m.group(1) if m else None


@dataclass
class SliceParcellationResult:
    slice_id: str
    ok: bool
    pixels_changed: int = 0
    excluded_pixels: int = 0
    unknown_ids: list[int] = field(default_factory=list)
    error: str = ""
    label_array: np.ndarray | None = None


@dataclass
class BatchParcellationSummary:
    results: list[SliceParcellationResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _annotation_pkl_path(annotation_dir: Path, slice_id: str) -> Path:
    return annotation_dir / f"Annotation_{slice_id}.pkl"


def _load_annotation_pkl(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        return np.asarray(pickle.load(f), dtype=np.uint32)


def _write_annotation_pkl(path: Path, label_array: np.ndarray) -> None:
    with path.open("wb") as f:
        pickle.dump(np.asarray(label_array, dtype=np.uint32), f)


def _is_noop(
    tier_id: str | None,
    st_level: int | None,
    excluded_region_ids: list[int] | None,
) -> bool:
    if excluded_region_ids:
        return False
    return tier_id == FULL_DETAIL_TIER or (tier_id is None and st_level is None)


def apply_parcellation_to_slice(
    annotation_dir: Path,
    slice_id: str,
    *,
    tier_id: str | None,
    st_level: int | None,
    excluded_region_ids: list[int] | None,
    structure_map: dict,
    catalog: dict[str, Any],
    dry_run: bool = False,
    write_disk: bool = True,
) -> SliceParcellationResult:
    """Relabel one slice from full backup, optionally exclude regions, write PKL."""
    annotation_dir = Path(annotation_dir).resolve()
    pkl_path = _annotation_pkl_path(annotation_dir, slice_id)

    if _is_noop(tier_id, st_level, excluded_region_ids):
        return SliceParcellationResult(slice_id=slice_id, ok=True)

    try:
        backup = load_full_backup(annotation_dir, slice_id)
        if backup is None:
            if not pkl_path.is_file():
                return SliceParcellationResult(
                    slice_id=slice_id,
                    ok=False,
                    error=f"missing annotation and backup for {slice_id}",
                )
            backup = _load_annotation_pkl(pkl_path)
            if not dry_run:
                ensure_full_backup(annotation_dir, slice_id, backup)

        baseline = np.asarray(backup, dtype=np.uint32)
        label = baseline.copy()

        if tier_id != FULL_DETAIL_TIER or st_level is not None:
            relabel = relabel_to_target(
                baseline,
                catalog,
                tier_id=tier_id,
                st_level=st_level,
                structure_map=structure_map,
            )
            label = relabel.label_array
            unknown_ids = relabel.unknown_ids
            pixels_changed = relabel.pixels_changed
        else:
            unknown_ids = []
            pixels_changed = 0

        ex_set = expand_excluded_ids(structure_map, excluded_region_ids)
        label, excluded_pixels = apply_exclusion(label, ex_set)

        if dry_run:
            return SliceParcellationResult(
                slice_id=slice_id,
                ok=True,
                pixels_changed=pixels_changed,
                excluded_pixels=excluded_pixels,
                unknown_ids=unknown_ids,
                label_array=label.copy(),
            )

        if not dry_run:
            if write_disk:
                _write_annotation_pkl(pkl_path, label)
            if tier_id == FULL_DETAIL_TIER and not excluded_region_ids:
                clear_slice_parcellation(annotation_dir, slice_id)
            else:
                from datetime import datetime, timezone

                meta = load_parcellation_meta(annotation_dir)
                meta[slice_id] = {
                    "tier_id": tier_id,
                    "st_level": st_level,
                    "excluded_region_ids": excluded_region_ids or [],
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                }
                save_parcellation_meta(annotation_dir, meta)

        return SliceParcellationResult(
            slice_id=slice_id,
            ok=True,
            pixels_changed=pixels_changed,
            excluded_pixels=excluded_pixels,
            unknown_ids=unknown_ids,
            label_array=label.copy(),
        )
    except Exception as exc:
        return SliceParcellationResult(
            slice_id=slice_id,
            ok=False,
            error=str(exc),
        )


def restore_slice_from_backup(
    annotation_dir: Path,
    slice_id: str,
    *,
    write_disk: bool = True,
) -> SliceParcellationResult:
    """Restore full-detail backup for one slice."""
    annotation_dir = Path(annotation_dir).resolve()
    backup = load_full_backup(annotation_dir, slice_id)
    if backup is None:
        return SliceParcellationResult(
            slice_id=slice_id,
            ok=False,
            error="no full backup",
        )
    if write_disk:
        _write_annotation_pkl(_annotation_pkl_path(annotation_dir, slice_id), backup)
        clear_slice_parcellation(annotation_dir, slice_id)
    return SliceParcellationResult(slice_id=slice_id, ok=True)


def discover_slice_ids(annotation_dir: Path, slice_ids: list[str] | None) -> list[str]:
    if slice_ids:
        return [str(s) for s in slice_ids]
    out: list[str] = []
    for name in os.listdir(annotation_dir):
        if ANNOTATION_PKL_RE.match(name):
            stem = slice_stem_from_annotation_pkl(name)
            if stem:
                out.append(stem)
    return sorted(set(out))


def apply_parcellation_batch(
    annotation_dir: Path,
    slice_ids: list[str] | None,
    *,
    tier_id: str | None,
    st_level: int | None,
    excluded_region_ids: list[int] | None,
    structure_map: dict,
    catalog: dict[str, Any],
    dry_run: bool = False,
) -> BatchParcellationSummary:
    ids = discover_slice_ids(annotation_dir, slice_ids)
    summary = BatchParcellationSummary()
    for sid in ids:
        result = apply_parcellation_to_slice(
            annotation_dir,
            sid,
            tier_id=tier_id,
            st_level=st_level,
            excluded_region_ids=excluded_region_ids,
            structure_map=structure_map,
            catalog=catalog,
            dry_run=dry_run,
            write_disk=not dry_run,
        )
        summary.results.append(result)
    return summary


def load_parcellation_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("parcellation config must be a JSON object")
    return raw


def _emit_log(msg: str) -> None:
    print(f"LOG: {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply CCF parcellation to annotation PKLs")
    parser.add_argument("-a", "--annotations", required=True, help="Annotation directory")
    parser.add_argument("-s", "--structures", required=True, help="structure_map.pkl path")
    parser.add_argument("-j", "--config", default="", help="parcellation_run_config.json")
    parser.add_argument("--restore-fine", action="store_true", help="Restore all from backup")
    args = parser.parse_args(argv)

    annotation_dir = Path(args.annotations.strip()).resolve()
    structure_map_path = Path(args.structures.strip())
    graph_path = structure_map_path.parent / "structure_graph.json"

    with structure_map_path.open("rb") as f:
        structure_map = pickle.load(f)

    from belljar.atlas.catalog import load_catalog

    if not graph_path.is_file():
        print("ERROR: structure_graph.json not found", file=sys.stderr, flush=True)
        return 1
    catalog = load_catalog(graph_path)

    cfg: dict[str, Any] = {}
    if args.config.strip():
        cfg = load_parcellation_config(args.config.strip())

    slice_ids = cfg.get("slice_ids") or None
    if slice_ids is not None:
        slice_ids = [str(x) for x in slice_ids]

    if args.restore_fine:
        ids = discover_slice_ids(annotation_dir, slice_ids)
        print(len(ids), flush=True)
        failed = 0
        for i, sid in enumerate(ids):
            r = restore_slice_from_backup(annotation_dir, sid)
            if not r.ok:
                failed += 1
            _emit_log(
                f"parcellation_restore slice={sid} ok={r.ok}"
                + (f" error={r.error}" if r.error else "")
            )
            print(i + 1, flush=True)
        print("Done!", flush=True)
        return 1 if failed else 0

    tier_id = cfg.get("tier_id")
    st_level = cfg.get("st_level")
    if st_level is not None:
        st_level = int(st_level)
    excluded = cfg.get("excluded_region_ids") or []
    excluded = [int(x) for x in excluded]

    if _is_noop(tier_id, st_level, excluded):
        print("LOG: parcellation skipped (full detail, no exclusions)", flush=True)
        print("Done!", flush=True)
        return 0

    ids = discover_slice_ids(annotation_dir, slice_ids)
    print(len(ids), flush=True)

    failed = 0
    for i, sid in enumerate(ids):
        result = apply_parcellation_to_slice(
            annotation_dir,
            sid,
            tier_id=tier_id,
            st_level=st_level,
            excluded_region_ids=excluded,
            structure_map=structure_map,
            catalog=catalog,
            write_disk=True,
        )
        if not result.ok:
            failed += 1
        _emit_log(
            f"parcellation slice={sid} ok={result.ok} "
            f"pixels_changed={result.pixels_changed} "
            f"excluded_pixels={result.excluded_pixels} "
            f"unknown_ids={len(result.unknown_ids)}"
            + (f" error={result.error}" if result.error else "")
        )
        print(i + 1, flush=True)

    print("Done!", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

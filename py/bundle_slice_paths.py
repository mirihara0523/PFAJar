"""Enumerate on-disk image assets tied to a slice in a Mason Jar bundle."""

from __future__ import annotations

import json
from pathlib import Path

from czi_common import (
    CANONICAL_REL,
    ROLE_DAPI,
    ROLE_UNUSED,
    branch_for_channel,
    branch_for_role_key,
    meta_state_path,
    role_key_for_channel,
)

MAX_DATASET_KINDS = ("max", "sharpen", "tophat")


def signal_branch_dirs_from_cfg(cfg: dict) -> set[str]:
    branches = {"somata", "nuclei", "axons"}
    for ch in cfg.get("channels") or []:
        branch = branch_for_channel(ch)
        if branch:
            branches.add(branch)
    return branches


def branches_for_enumeration(bundle_root: Path, cfg: dict) -> set[str]:
    branches = signal_branch_dirs_from_cfg(cfg)
    max_base = bundle_root / CANONICAL_REL["max"]
    if max_base.is_dir():
        for sub in max_base.iterdir():
            if sub.is_dir():
                branches.add(sub.name)
    return branches


def _glob_dataset_kind_paths(branch_root: Path, slice_id: str) -> list[Path]:
    paths: list[Path] = []
    for kind in MAX_DATASET_KINDS:
        kind_root = branch_root / kind
        if not kind_root.is_dir():
            continue
        for pattern in (f"**/{slice_id}.tif", f"**/{slice_id}.tiff"):
            for candidate in kind_root.glob(pattern):
                if candidate.is_file():
                    paths.append(candidate)
    return paths


def paths_for_slice(bundle_root: Path, slice_id: str, cfg: dict) -> list[Path]:
    paths: list[Path] = []
    dapi_png = bundle_root / CANONICAL_REL["dapi"] / f"{slice_id}.png"
    if dapi_png.exists():
        paths.append(dapi_png)
    prev_dir = bundle_root / CANONICAL_REL["previews"]
    if prev_dir.exists():
        for preview in prev_dir.glob(f"{slice_id}_*.png"):
            paths.append(preview)
    orig_base = bundle_root / CANONICAL_REL["original_scans"]
    for sub in sorted(signal_branch_dirs_from_cfg(cfg)):
        candidate = orig_base / sub / f"{slice_id}.tif"
        if candidate.exists():
            paths.append(candidate)
    flat_candidate = orig_base / f"{slice_id}.tif"
    if flat_candidate.exists():
        paths.append(flat_candidate)

    max_runs = (cfg.get("max_runs") or {}) if isinstance(cfg, dict) else {}
    if not max_runs:
        state_path = meta_state_path(bundle_root)
        if state_path.exists():
            with open(state_path, encoding="utf-8") as f:
                st = json.load(f)
            max_runs = st.get("max_runs") or {}

    role_keys = set(max_runs.keys())
    for ch in cfg.get("channels") or []:
        if ch.get("keep") and ch.get("role") not in (ROLE_DAPI, ROLE_UNUSED):
            role_keys.add(role_key_for_channel(ch))

    for role_key in role_keys:
        rel = max_runs.get(role_key)
        if rel:
            candidate = bundle_root / CANONICAL_REL["max"] / rel / f"{slice_id}.tif"
            if candidate.exists():
                paths.append(candidate)
            continue
        branch = branch_for_role_key(role_key)
        if not branch:
            continue
        max_root = bundle_root / CANONICAL_REL["max"] / branch / "max"
        if not max_root.exists():
            continue
        for run_dir in sorted(max_root.iterdir()):
            if run_dir.is_dir():
                candidate = run_dir / f"{slice_id}.tif"
                if candidate.exists():
                    paths.append(candidate)

    max_base = bundle_root / CANONICAL_REL["max"]
    for branch in sorted(branches_for_enumeration(bundle_root, cfg)):
        branch_root = max_base / branch
        if branch_root.is_dir():
            paths.extend(_glob_dataset_kind_paths(branch_root, slice_id))

    seen = set()
    unique: list[Path] = []
    for candidate in paths:
        key = str(candidate.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique

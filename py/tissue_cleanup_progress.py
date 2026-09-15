"""Persist tissue cleanup apply progress under .masonjar/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def meta_dir(bundle_root: Path) -> Path:
    for name in (".masonjar", ".belljar"):
        p = bundle_root / name
        if p.is_dir() or name == ".masonjar":
            return p
    return bundle_root / ".masonjar"


def progress_path(bundle_root: Path) -> Path:
    return meta_dir(bundle_root) / "tissue_cleanup_apply_progress.json"


def load_progress(bundle_root: Path) -> dict[str, Any] | None:
    path = progress_path(bundle_root)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_progress(bundle_root: Path, data: dict[str, Any]) -> None:
    path = progress_path(bundle_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_progress(bundle_root: Path) -> None:
    path = progress_path(bundle_root)
    if path.is_file():
        path.unlink()


def path_key(bundle_root: Path, file_path: Path) -> str:
    try:
        return str(file_path.resolve().relative_to(bundle_root.resolve())).replace("\\", "/")
    except ValueError:
        return file_path.name


def config_fingerprint(config: dict) -> str:
    slices = config.get("slices") or {}
    keys = sorted(str(k) for k in slices.keys())
    return "|".join(keys)


def is_completed(progress: dict[str, Any] | None, rel_key: str) -> bool:
    if not progress:
        return False
    completed = progress.get("completed_paths") or []
    return rel_key in completed


def record_completion(
    bundle_root: Path,
    progress: dict[str, Any],
    rel_key: str,
    slice_id: str,
    manifest_slices: dict,
) -> dict[str, Any]:
    completed = list(progress.get("completed_paths") or [])
    if rel_key not in completed:
        completed.append(rel_key)
    progress["completed_paths"] = completed
    progress["completed"] = len(completed)
    progress["last_slice_id"] = slice_id
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    progress["slices"] = manifest_slices
    save_progress(bundle_root, progress)
    return progress


def partial_result_from_progress(progress: dict[str, Any]) -> dict[str, Any]:
    completed = int(progress.get("completed") or 0)
    total = int(progress.get("files_total") or 0)
    slices = progress.get("slices") or {}
    return {
        "ok": False,
        "partial": True,
        "applied_files": completed,
        "files_total": total,
        "slices_applied": len(slices),
        "slices": slices,
        "failed": [],
        "error": f"Tissue cleanup interrupted after {completed}/{total} file(s)",
    }

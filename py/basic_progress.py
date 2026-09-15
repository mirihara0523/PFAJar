"""Persist BaSiC shading apply progress under .masonjar."""

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
    return meta_dir(bundle_root) / "basic_apply_progress.json"


def last_result_path(bundle_root: Path) -> Path:
    return meta_dir(bundle_root) / "basic_apply_last_result.json"


def load_progress(bundle_root: Path) -> dict[str, Any] | None:
    path = progress_path(bundle_root)
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_progress(bundle_root: Path, data: dict[str, Any]) -> None:
    path = progress_path(bundle_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def clear_progress(bundle_root: Path) -> None:
    path = progress_path(bundle_root)
    if path.is_file():
        path.unlink()


def write_last_result(bundle_root: Path, payload: dict[str, Any]) -> None:
    path = last_result_path(bundle_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(payload)
    entry["at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    tmp.replace(path)


def path_key(bundle_root: Path, file_path: Path) -> str:
    try:
        return str(file_path.resolve().relative_to(bundle_root.resolve())).replace(
            "\\", "/"
        )
    except ValueError:
        return file_path.name


def is_completed(progress: dict[str, Any] | None, rel_key: str) -> bool:
    if not progress:
        return False
    completed = progress.get("completed_paths") or []
    return rel_key in completed


def record_completion(
    bundle_root: Path,
    progress: dict[str, Any],
    rel_key: str,
    *,
    channel_id: str,
    slice_id: str,
) -> dict[str, Any]:
    completed = list(progress.get("completed_paths") or [])
    if rel_key not in completed:
        completed.append(rel_key)
    progress["completed_paths"] = completed
    progress["last_ok_slice"] = slice_id
    progress["last_ok_channel"] = channel_id
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_progress(bundle_root, progress)
    return progress

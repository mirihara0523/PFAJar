"""Resolve slice stem → TIFF path for preprocess batch steps (sharpen, top-hat)."""

from __future__ import annotations

import json
import os
from pathlib import Path

_TIFF_SUFFIXES = (
    ".tif",
    ".tiff",
    ".TIF",
    ".TIFF",
    ".ome.tif",
    ".ome.tiff",
    ".OME.TIF",
    ".OME.TIFF",
)


def _is_tiff_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".tif", ".tiff")) or ".ome." in lower


def resolve_stem_to_file(input_dir: Path, stem: str) -> Path | None:
    """Return the first matching TIFF for *stem* under *input_dir*."""
    for suffix in _TIFF_SUFFIXES:
        p = input_dir / f"{stem}{suffix}"
        if p.is_file():
            return p
    return None


def _slice_stems_from_list(slice_list: str) -> list[str]:
    with open(slice_list, encoding="utf-8") as f:
        raw = f.read().strip()
    if raw[:1] in ("[", "{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = data.get("slice_ids", [])
            return [str(x).strip() for x in data if str(x).strip()]
        except (ValueError, TypeError):
            pass
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def list_input_files(input_path: Path, slice_list: str | None) -> list[Path]:
    """List TIFF inputs from *input_path*, optionally filtered by *slice_list*."""
    if slice_list and os.path.isfile(slice_list):
        stems = _slice_stems_from_list(slice_list)
        files: list[Path] = []
        for stem in stems:
            p = resolve_stem_to_file(input_path, stem)
            if p is not None:
                files.append(p)
        return sorted(files, key=lambda p: p.name)

    files = [p for p in input_path.iterdir() if p.is_file() and _is_tiff_name(p.name)]
    files.sort(key=lambda p: p.name)
    return files

"""Read per-slice layout metadata written by py/map.py align finish."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_align_slice_layouts(annotation_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Return sliceId -> layout record from align run manifest, or {} if absent."""
    root = Path(annotation_dir)
    candidates = [
        root / "run_manifest.json",
        root / ".masonjar" / "align_warp_report.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        layouts = data.get("slice_layouts")
        if isinstance(layouts, dict) and layouts:
            return layouts
    return {}


def intensity_whole_for_slice(
    slice_id: str,
    run_wide_whole: bool,
    slice_layouts: dict[str, dict[str, Any]] | None,
) -> bool:
    """
    Map align hemisphere metadata to Isolate Regions ``whole`` flag.

    Align ``W`` (whole-brain section) -> intensity whole=True (left-half crop).
    Align ``L`` (left-hemi section) -> intensity whole=False (full matched regions).
    """
    layouts = slice_layouts or {}
    key = slice_id
    if key not in layouts:
        for candidate, record in layouts.items():
            if candidate.lower() == slice_id.lower():
                key = candidate
                break
    if key in layouts:
        hemi = str(layouts[key].get("hemisphere", "W")).upper()
        return hemi == "W"
    return run_wide_whole

"""Append-only geometry transform audit log under .masonjar/."""

from __future__ import annotations

import json
import time
from pathlib import Path

HISTORY_FILENAME = "geometry_history.jsonl"


def history_path(bundle_root: Path) -> Path:
    return bundle_root / ".masonjar" / HISTORY_FILENAME


def ops_to_js_list(ops: list) -> list[str]:
    """Map internal op tuples to JS geometry.ops strings."""
    out: list[str] = []
    for op, val in ops or []:
        if op == "rotate" and val == 90:
            out.append("rot90")
        elif op == "flip_x":
            out.append("flipX")
        elif op == "flip_y":
            out.append("flipY")
    return out


def append_geometry_history(bundle_root: Path, entry: dict) -> None:
    path = history_path(bundle_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(entry)
    payload.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")

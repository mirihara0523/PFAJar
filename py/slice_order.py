"""Project-wide navigation order, keyed by full slice ID, never by list index."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from slice_index import natural_sort_key


def order_path(input_dir: str | Path, bundle_root=None) -> Path | None:
    if bundle_root:
        return Path(bundle_root) / ".masonjar" / "slice_order.json"
    start = Path(input_dir).resolve()
    ancestors = (start, *start.parents)
    for root in ancestors:
        if any(item.is_file() for pattern in ("*.masonjar", "*.belljar", "*.pfajar") for item in root.glob(pattern)):
            return root / ".masonjar" / "slice_order.json"
    for root in ancestors:
        if (root / ".masonjar" / "slice_order.json").is_file():
            return root / ".masonjar" / "slice_order.json"
    for root in ancestors:
        if (root / ".masonjar").is_dir() or (root / ".belljar").is_dir():
            return root / ".masonjar" / "slice_order.json"
    return None



def unique_ids(ids):
    return list(dict.fromkeys(x for x in ids if isinstance(x, str) and x))


def read_order(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("slice_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("Invalid slice order: expected slice_ids string array")
    return unique_ids(ids)


def load_order(path: Path | None) -> list[str]:
    try:
        ids = read_order(path)
        if ids:
            print(f"LOG: slice_order_loaded path={path} count={len(ids)}", flush=True)
        return ids
    except (OSError, ValueError) as exc:
        print(f"LOG: slice_order_load_failed path={path} error={exc}", flush=True)
        return []


def apply_order(items, ids, key=lambda x: x):
    """Keep known IDs in saved order; append new IDs in natural filename order."""
    ranks = {sid: i for i, sid in enumerate(unique_ids(ids))}
    return sorted(items, key=lambda item: (
        ranks.get(key(item), len(ranks)), natural_sort_key(key(item)),
    ))


def save_order(path: Path, ids, *, expected=None):
    """Replace the visible subset's slots, retaining hidden IDs. Atomic write.

    An optimistic check refuses to overwrite an order edited by another window.
    Corrupt files are left intact for diagnosis rather than silently overwritten.
    """
    previous = read_order(path)
    if expected is not None and previous != expected:
        raise ValueError("Slice order changed in another window. Reopen the dialog and try again.")
    ids = unique_ids(ids)
    selected = set(ids)
    replacement = iter(ids)
    merged = [next(replacement) if sid in selected else sid for sid in previous]
    merged.extend(replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="slice-order-", suffix=".tmp", delete=False) as f:
            temp_name = f.name
            json.dump({"version": 1, "slice_ids": merged}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"LOG: slice_order_saved path={path} count={len(merged)} ids={json.dumps(merged)}", flush=True)
    return merged

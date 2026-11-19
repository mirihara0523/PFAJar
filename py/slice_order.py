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


def read_order_state(path):
    if path is None or not path.exists():
        return {"slice_ids": [], "custom_slice_ids": [], "mode": "filename"}
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("slice_ids") if isinstance(data, dict) else None
    custom = data.get("custom_slice_ids", ids) if isinstance(data, dict) else None
    for values in (ids, custom):
        if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
            raise ValueError("Invalid slice order: expected section ID arrays")
    mode = data.get("mode", "custom" if ids else "filename")
    if mode not in ("filename", "custom", "ap_ascending", "ap_descending"):
        raise ValueError("Invalid section order mode")
    return {"slice_ids": unique_ids(ids), "custom_slice_ids": unique_ids(custom), "mode": mode}


def read_order(path: Path | None) -> list[str]:
    return read_order_state(path)["slice_ids"]


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


def save_order(path: Path, ids, *, expected=None, custom_ids=None, mode=None, expected_custom=None, expected_mode=None):
    """Replace the visible subset's slots, retaining hidden IDs. Atomic write.

    An optimistic check refuses to overwrite an order edited by another window.
    Corrupt files are left intact for diagnosis rather than silently overwritten.
    """
    state = read_order_state(path)
    previous = state["slice_ids"]
    if expected_custom is not None and state["custom_slice_ids"] != expected_custom:
        raise ValueError("Custom order changed in another window. Reopen the dialog and try again.")
    if expected_mode is not None and state["mode"] != expected_mode:
        raise ValueError("Section order mode changed in another window. Reopen the dialog and try again.")
    if expected is not None and previous != expected:
        raise ValueError("Slice order changed in another window. Reopen the dialog and try again.")
    ids = unique_ids(ids)
    selected = set(ids)
    replacement = iter(ids)
    merged = [next(replacement) if sid in selected else sid for sid in previous]
    merged.extend(replacement)
    chosen_mode = mode or "custom"  # Older manual-order callers remain compatible.
    if chosen_mode not in ("filename", "custom", "ap_ascending", "ap_descending"):
        raise ValueError("Invalid section order mode")
    if custom_ids is None:
        custom_ids = ids if chosen_mode == "custom" else state["custom_slice_ids"]
        if not custom_ids:
            custom_ids = ids
    else:
        if not isinstance(custom_ids, list) or any(not isinstance(sid,str) or not sid for sid in custom_ids):
            raise ValueError("Invalid custom section order")
        if len(set(custom_ids)) != len(custom_ids):
            raise ValueError("Custom order contains duplicate sections")
        custom_ids = unique_ids(custom_ids)
        if set(custom_ids) != set(ids):
            raise ValueError("Custom order must include every visible section exactly once")
    if chosen_mode == "custom" and custom_ids != ids:
        raise ValueError("Active custom order must match the stored Custom sequence")
    selected_custom = set(custom_ids)
    replacement_custom = iter(custom_ids)
    merged_custom = [next(replacement_custom) if sid in selected_custom else sid for sid in state["custom_slice_ids"]]
    merged_custom.extend(replacement_custom)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="slice-order-", suffix=".tmp", delete=False) as f:
            temp_name = f.name
            json.dump({"version": 1, "slice_ids": merged, "custom_slice_ids": merged_custom, "mode": chosen_mode}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"LOG: slice_order_saved path={path} count={len(merged)} ids={json.dumps(merged)}", flush=True)
    return merged

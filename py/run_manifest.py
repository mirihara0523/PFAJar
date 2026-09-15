"""Write run_manifest.json for Mason Jar pipeline run leaves."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _json_safe(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(_json_safe(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def write_run_manifest(output_dir: str | Path, payload: Mapping[str, Any]) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(payload)
    manifest.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    manifest.setdefault("output_dir", str(out))
    path = out / "run_manifest.json"
    safe_manifest = _json_safe(manifest)
    with open(path, "w", encoding="utf-8") as mf:
        json.dump(safe_manifest, mf, indent=2)
    return path

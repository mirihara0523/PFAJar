"""App-wide dialog suppression preferences under ~/.masonjar/dialog_preferences.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DIALOG_PREFS_FILENAME = "dialog_preferences.json"

KEY_MIXED_RESOLUTION_TIER = "adjust.mixed_resolution_tier"
KEY_CONFIRM_SAVE_OVERWRITE = "adjust.confirm_save_overwrite"
KEY_ISOLATE_LABEL_AUDIT = "adjust.isolate_label_audit"

KNOWN_KEYS = (
    KEY_MIXED_RESOLUTION_TIER,
    KEY_CONFIRM_SAVE_OVERWRITE,
    KEY_ISOLATE_LABEL_AUDIT,
)


def mason_home_dir() -> Path:
    return Path.home() / ".masonjar"


def dialog_preferences_path(home: Path | None = None) -> Path:
    return (home or mason_home_dir()) / DIALOG_PREFS_FILENAME


def default_prefs(app_version: str = "") -> dict[str, Any]:
    return {"app_version": str(app_version or ""), "suppressed": {}}


def load_dialog_preferences(home: Path | None = None) -> dict[str, Any]:
    path = dialog_preferences_path(home)
    if not path.is_file():
        return default_prefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default_prefs()
    if not isinstance(raw, dict):
        return default_prefs()
    suppressed = raw.get("suppressed")
    if not isinstance(suppressed, dict):
        suppressed = {}
    return {
        "app_version": str(raw.get("app_version") or ""),
        "suppressed": {str(k): bool(v) for k, v in suppressed.items()},
    }


def save_dialog_preferences(prefs: dict[str, Any], home: Path | None = None) -> None:
    path = dialog_preferences_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app_version": str(prefs.get("app_version") or ""),
        "suppressed": dict(prefs.get("suppressed") or {}),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_suppressed(key: str, home: Path | None = None) -> bool:
    prefs = load_dialog_preferences(home)
    return bool((prefs.get("suppressed") or {}).get(key))


def set_suppressed(key: str, value: bool = True, home: Path | None = None) -> None:
    prefs = load_dialog_preferences(home)
    suppressed = dict(prefs.get("suppressed") or {})
    if value:
        suppressed[key] = True
    else:
        suppressed.pop(key, None)
    prefs["suppressed"] = suppressed
    save_dialog_preferences(prefs, home)


def clear_suppressions(home: Path | None = None, app_version: str | None = None) -> dict[str, Any]:
    prefs = load_dialog_preferences(home)
    prefs["suppressed"] = {}
    if app_version is not None:
        prefs["app_version"] = str(app_version)
    save_dialog_preferences(prefs, home)
    return prefs


def sync_app_version_clear_if_changed(
    current_version: str, home: Path | None = None
) -> bool:
    """If stored app_version differs from current, clear suppressions. Returns True if cleared."""
    prefs = load_dialog_preferences(home)
    stored = str(prefs.get("app_version") or "")
    cur = str(current_version or "")
    if stored == cur and cur:
        return False
    prefs["suppressed"] = {}
    prefs["app_version"] = cur
    save_dialog_preferences(prefs, home)
    return True

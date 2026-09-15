"""Crash-safe alignment session persistence under the DAPI input directory."""

from __future__ import annotations

import copy
import hashlib
import json
import pickle
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_VERSION = 1
PKL_NAME = "alignment.pkl"
PKL_BAK_NAME = "alignment.pkl.bak"
PKL_TMP_NAME = "alignment.pkl.tmp"
SESSION_JSON_NAME = "alignment_session.json"


def session_paths(dapi_dir: Path | str) -> dict[str, Path]:
    root = Path(dapi_dir)
    return {
        "pkl": root / PKL_NAME,
        "bak": root / PKL_BAK_NAME,
        "tmp": root / PKL_TMP_NAME,
        "json": root / SESSION_JSON_NAME,
    }


def compute_tuning_fingerprint(
    file_list: list[str],
    layout_mode: str,
    legacy: bool,
    slice_filter: set[str] | None,
) -> str:
    """Identity for DAPI-side tuning (independent of align output leaf)."""
    parts = [
        str(SESSION_VERSION),
        "|".join(sorted(file_list)),
        str(layout_mode),
        "legacy" if legacy else "modern",
    ]
    if slice_filter is not None:
        parts.append("|".join(sorted(slice_filter)))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def compute_fingerprint(
    file_list: list[str],
    output_path: str | Path,
    layout_mode: str,
    legacy: bool,
    slice_filter: set[str] | None,
) -> str:
    """Legacy full run fingerprint (includes output path). Prefer tuning fingerprint."""
    parts = [
        str(SESSION_VERSION),
        "|".join(sorted(file_list)),
        str(Path(output_path).resolve()),
        str(layout_mode),
        "legacy" if legacy else "modern",
    ]
    if slice_filter is not None:
        parts.append("|".join(sorted(slice_filter)))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _session_tuning_fingerprint(session: dict[str, Any]) -> str | None:
    saved = session.get("tuning_fingerprint")
    if saved:
        return str(saved)
    legacy = session.get("fingerprint")
    return str(legacy) if legacy else None


def _atomic_replace(src: Path, dest: Path) -> None:
    for attempt in range(2):
        try:
            src.replace(dest)
            return
        except OSError:
            if attempt == 0:
                time.sleep(0.05)
            else:
                raise


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
    _atomic_replace(tmp, path)


def _write_pickle_atomic(paths: dict[str, Path], payload: dict) -> None:
    paths["tmp"].parent.mkdir(parents=True, exist_ok=True)
    with open(paths["tmp"], "wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
    if paths["pkl"].is_file():
        shutil.copy2(paths["pkl"], paths["bak"])
    _atomic_replace(paths["tmp"], paths["pkl"])


def _strip_atlas_slices_for_pickle(atlas_slices: dict) -> dict:
    saved: dict = {}
    # image/label are discarded from the pickle anyway (set to None below), so
    # detach them from the original before deepcopy — this skips copying
    # hundreds of MB of atlas image + uint32 label per save. Restored after.
    # Only these two are detached, so the serialized payload is unchanged.
    heavy_attrs = ("image", "label")
    for section_name, atlas_slice in atlas_slices.items():
        atlas_slice.eraser_window = None
        prev_cb = getattr(atlas_slice, "_autosave_cb", None)
        if hasattr(atlas_slice, "_autosave_cb"):
            atlas_slice._autosave_cb = None
        stashed = {}
        for attr in heavy_attrs:
            if hasattr(atlas_slice, attr):
                stashed[attr] = getattr(atlas_slice, attr)
                setattr(atlas_slice, attr, None)
        try:
            this_copy = copy.deepcopy(atlas_slice)
        finally:
            for attr, value in stashed.items():
                setattr(atlas_slice, attr, value)
            if prev_cb is not None:
                atlas_slice._autosave_cb = prev_cb
        this_copy.image = None
        this_copy.label = None
        if hasattr(this_copy, "_autosave_cb"):
            this_copy._autosave_cb = None
        saved[section_name] = this_copy
    return saved


def apply_slice_tuning_from_controls(
    atlas_slice,
    *,
    x_angle: float,
    y_angle: float,
    ap_position: float,
    region: str,
    hemisphere: str,
    linked: bool,
    use_tissue_cleanup_mask: bool,
    tissue_mask_warp_mode: str,
    layout_overridden: bool = False,
) -> None:
    """Apply live UI control values to one slice (no Qt; used before autosave)."""
    atlas_slice.x_angle = float(x_angle)
    atlas_slice.y_angle = float(y_angle)
    atlas_slice.ap_position = int(ap_position)
    atlas_slice.region = str(region)
    atlas_slice.hemisphere = str(hemisphere)
    atlas_slice.linked = bool(linked)
    atlas_slice.use_tissue_cleanup_mask = bool(use_tissue_cleanup_mask)
    if tissue_mask_warp_mode:
        atlas_slice.tissue_mask_warp_mode = str(tissue_mask_warp_mode)
    if layout_overridden:
        atlas_slice.layout_overridden = True
        atlas_slice.layout_low_confidence = False


def should_sync_controls_before_autosave(reason: str, controls_seeded: bool) -> bool:
    """Skip Qt→slice sync before predict_complete or until update_display seeds controls."""
    if reason == "predict_complete":
        return False
    return controls_seeded


def is_corrupt_predict_complete_session(
    session: dict[str, Any] | None,
    atlas_slices: dict,
) -> bool:
    """Detect v4.0.6 autosave that clobbered predictions with default spinbox values."""
    if not session or not atlas_slices:
        return False
    if session.get("reason") != "predict_complete":
        return False
    if int(session.get("visited", 0)) != 0:
        return False
    for atlas_slice in atlas_slices.values():
        if int(getattr(atlas_slice, "ap_position", 0)) != 0:
            return False
        if float(getattr(atlas_slice, "x_angle", 0.0)) != 0.0:
            return False
        if float(getattr(atlas_slice, "y_angle", 0.0)) != 0.0:
            return False
    return True


def is_model_only_predict_complete_session(
    session: dict[str, Any] | None,
    atlas_slices: dict,
) -> bool:
    """Model prediction cache only (visited=0) — not user-confirmed tuning."""
    if not session or not atlas_slices:
        return False
    if session.get("reason") != "predict_complete":
        return False
    return int(session.get("visited", 0)) == 0


def ap_extrapolation_locked(session: dict[str, Any] | None, num_slices: int) -> bool:
    """True when reopening a finished session — Next must not rewrite saved AP."""
    if not session or num_slices <= 0:
        return False
    status = session.get("status")
    visited_saved = int(session.get("visited", 0))
    max_idx = max(0, num_slices - 1)
    return status == "completed" or visited_saved >= max_idx


def extrapolate_ap_positions(
    confirmed_aps: list[float | int],
    num_slices: int,
    *,
    max_ap: int,
    min_ap: int = 0,
    model_delta: float | None = None,
) -> list[tuple[int, int]]:
    """
    Extrapolate AP for slice indices after the confirmed prefix.

    ``confirmed_aps[i]`` is the AP for section index ``i`` (through ``current_section``).
    Returns ``(slice_index, ap_position)`` pairs for indices ``len(confirmed_aps) .. num_slices-1``.
    """
    import numpy as np

    n_confirm = len(confirmed_aps)
    if n_confirm < 2 or n_confirm >= num_slices:
        return []

    start = n_confirm
    y = np.array([float(v) for v in confirmed_aps], dtype=float)
    x_pred = np.arange(start, num_slices, dtype=float)

    if n_confirm == 2:
        delta = float(y[1] - y[0])
    else:
        delta = float(y[-1] - y[-2])
        if delta == 0.0:
            deltas = np.diff(y)
            nonzero = deltas[deltas != 0]
            if len(nonzero):
                delta = float(nonzero[-1])
            else:
                delta = float(y[1] - y[0])

    if delta == 0.0 and model_delta is not None and model_delta != 0.0:
        delta = float(model_delta)

    predictions = y[-1] + delta * (x_pred - float(n_confirm - 1))

    out: list[tuple[int, int]] = []
    for idx, pred in zip(range(start, num_slices), predictions):
        ap = int(round(float(pred)))
        ap = max(min_ap, min(int(max_ap), ap))
        out.append((idx, ap))
    return out


def _slice_summary(atlas_slice) -> dict[str, Any]:
    return {
        "filename": atlas_slice.section_name,
        "slice_id": atlas_slice.slice_id(),
        "ap_position": int(atlas_slice.ap_position),
        "x_angle": float(atlas_slice.x_angle),
        "y_angle": float(atlas_slice.y_angle),
        "region": str(atlas_slice.region),
        "hemisphere": str(getattr(atlas_slice, "hemisphere", "W")),
        "linked": bool(getattr(atlas_slice, "linked", True)),
        "layout_confidence": float(getattr(atlas_slice, "layout_confidence", 1.0)),
        "layout_low_confidence": bool(getattr(atlas_slice, "layout_low_confidence", False)),
        "layout_overridden": bool(getattr(atlas_slice, "layout_overridden", False)),
        "use_tissue_cleanup_mask": bool(
            getattr(atlas_slice, "use_tissue_cleanup_mask", False)
        ),
        "tissue_mask_warp_mode": str(
            getattr(atlas_slice, "tissue_mask_warp_mode", "") or ""
        ),
        "has_damage_mask": getattr(atlas_slice, "damage_mask", None) is not None,
    }


def persist_session(
    dapi_dir: Path | str,
    atlas_slices: dict,
    *,
    tuning_fingerprint: str,
    output_path: str | Path | None,
    current_section: int,
    visited: int,
    parcellation: dict[str, Any],
    reason: str,
    status: str = "in_progress",
    layout_mode: str | None = None,
) -> None:
    # Thin wrapper: build the snapshot then write it. Kept for callers that
    # want a synchronous save. Background savers call the two halves directly.
    write_session_payload(
        build_session_payload(
            dapi_dir,
            atlas_slices,
            tuning_fingerprint=tuning_fingerprint,
            output_path=output_path,
            current_section=current_section,
            visited=visited,
            parcellation=parcellation,
            reason=reason,
            status=status,
            layout_mode=layout_mode,
        )
    )


def build_session_payload(
    dapi_dir: Path | str,
    atlas_slices: dict,
    *,
    tuning_fingerprint: str,
    output_path: str | Path | None,
    current_section: int,
    visited: int,
    parcellation: dict[str, Any],
    reason: str,
    status: str = "in_progress",
    layout_mode: str | None = None,
) -> dict:
    """Build the picklable snapshot + session_doc on the calling thread.

    Performs the deepcopy/strip and per-slice summary (the CPU snapshot) so the
    disk I/O in write_session_payload() can run on a background thread without
    touching live, mutable slice objects.
    """
    paths = session_paths(dapi_dir)
    pickle_payload = _strip_atlas_slices_for_pickle(atlas_slices)
    output_abs = str(Path(output_path).resolve()) if output_path else None
    session_doc = {
        "version": SESSION_VERSION,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "tuning_fingerprint": tuning_fingerprint,
        "fingerprint": tuning_fingerprint,
        "output_path": output_abs,
        "current_section": int(current_section),
        "visited": int(visited),
        "parcellation": dict(parcellation or {}),
        "slice_count": len(atlas_slices),
        "slices": [_slice_summary(s) for s in atlas_slices.values()],
    }
    if layout_mode:
        session_doc["layout_mode"] = str(layout_mode)
    return {
        "paths": paths,
        "pickle_payload": pickle_payload,
        "session_doc": session_doc,
    }


def write_session_payload(payload: dict) -> None:
    """Write a payload produced by build_session_payload() to disk.

    Contains only file I/O (no Qt, no live slice access), so it is safe to run
    on a background thread.
    """
    paths = payload["paths"]
    _write_pickle_atomic(paths, payload["pickle_payload"])
    _write_json_atomic(paths["json"], payload["session_doc"])


def _load_pickle(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as handle:
            data = pickle.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _read_session_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@dataclass
class LoadResult:
    atlas_slices: dict
    session: dict[str, Any] | None = None
    source: str = "pkl"
    restore_navigation: bool = True


def diagnose_load_failure(
    dapi_dir: Path | str,
    expected_tuning_fingerprint: str,
) -> str:
    paths = session_paths(dapi_dir)
    session = _read_session_json(paths["json"])
    has_pkl = paths["pkl"].is_file()
    has_bak = paths["bak"].is_file()

    if session is not None:
        saved = _session_tuning_fingerprint(session)
        if saved and saved != expected_tuning_fingerprint:
            return (
                f"fingerprint_mismatch saved={saved} expected={expected_tuning_fingerprint}"
            )

    if not has_pkl and not has_bak:
        return "no_pickle"

    if _load_pickle(paths["pkl"]) is None and _load_pickle(paths["bak"]) is None:
        return "corrupt_pickle"

    return "unknown"


def load_session(
    dapi_dir: Path | str,
    expected_tuning_fingerprint: str,
) -> LoadResult | None:
    paths = session_paths(dapi_dir)
    session = _read_session_json(paths["json"])

    if session is not None:
        saved = _session_tuning_fingerprint(session)
        if saved != expected_tuning_fingerprint:
            return None
        restore_nav = session.get("status") != "completed"
    else:
        restore_nav = False

    raw = _load_pickle(paths["pkl"])
    source = "pkl"
    if raw is None:
        raw = _load_pickle(paths["bak"])
        source = "bak"
    if raw is None:
        return None

    return LoadResult(
        atlas_slices=raw,
        session=session,
        source=source,
        restore_navigation=restore_nav,
    )


def session_artifacts_present(dapi_dir: Path | str) -> bool:
    """True when any Align session artifact exists under the DAPI input folder."""
    paths = session_paths(dapi_dir)
    return any(paths[key].is_file() for key in ("pkl", "bak", "tmp", "json"))


def clear_alignment_session(dapi_dir: Path | str) -> None:
    """Remove all Align session artifacts (pickle, JSON sidecar, temp/backup)."""
    paths = session_paths(dapi_dir)
    for key in ("json", "bak", "tmp", "pkl"):
        path = paths[key]
        if path.is_file():
            path.unlink()


def is_unrecoverable_loaded_session(
    session: dict[str, Any] | None,
    atlas_slices: dict,
) -> str | None:
    """Return discard reason when a loaded session must not be resumed."""
    if is_corrupt_predict_complete_session(session, atlas_slices):
        return "corrupt_predict_complete"
    if is_model_only_predict_complete_session(session, atlas_slices):
        return "model_only_predict_complete"
    return None


def _should_clear_on_load_failure(detail: str) -> bool:
    if detail.startswith("fingerprint_mismatch"):
        return True
    if detail == "corrupt_pickle":
        return True
    if detail == "no_pickle":
        return True
    return False


def _load_completed_session_fingerprint_mismatch(
    dapi_dir: Path | str,
) -> LoadResult | None:
    """Load a finished session when layout fingerprint differs (do not discard tuning)."""
    paths = session_paths(dapi_dir)
    session = _read_session_json(paths["json"])
    if session is None or session.get("status") != "completed":
        return None
    raw = _load_pickle(paths["pkl"])
    source = "pkl"
    if raw is None:
        raw = _load_pickle(paths["bak"])
        source = "bak"
    if raw is None:
        return None
    return LoadResult(
        atlas_slices=raw,
        session=session,
        source=source,
        restore_navigation=False,
    )


def recover_alignment_session(
    dapi_dir: Path | str,
    expected_tuning_fingerprint: str,
) -> LoadResult | None:
    """
    Load a saved tuning session or clear broken legacy artifacts on disk.

    Returns None when no session exists, load failed, or data is unusable.
    """
    if not session_artifacts_present(dapi_dir):
        return None

    result = load_session(dapi_dir, expected_tuning_fingerprint)
    if result is None:
        detail = diagnose_load_failure(dapi_dir, expected_tuning_fingerprint)
        if detail.startswith("fingerprint_mismatch"):
            fallback = _load_completed_session_fingerprint_mismatch(dapi_dir)
            if fallback is not None:
                print(
                    "LOG: align_session_fingerprint_mismatch "
                    "loaded_completed_session anyway",
                    flush=True,
                )
                return fallback
        if _should_clear_on_load_failure(detail):
            clear_alignment_session(dapi_dir)
            reason = detail.split()[0] if detail else "load_failed"
            print(f"LOG: align_session_cleared reason={reason}", flush=True)
            print(
                "Cleared incompatible alignment session files; fresh predictions will run.",
                flush=True,
            )
        else:
            print(f"LOG: align_session_not_loaded reason={detail}", flush=True)
        return None

    discard = is_unrecoverable_loaded_session(result.session, result.atlas_slices)
    if discard:
        clear_alignment_session(dapi_dir)
        print(f"LOG: align_session_cleared reason={discard}", flush=True)
        print(
            "Cleared incompatible alignment session files; fresh predictions will run.",
            flush=True,
        )
        return None

    return result


def mark_session_completed(
    dapi_dir: Path | str,
    tuning_fingerprint: str,
) -> None:
    paths = session_paths(dapi_dir)
    session = _read_session_json(paths["json"])
    if session is None:
        session = {
            "version": SESSION_VERSION,
            "tuning_fingerprint": tuning_fingerprint,
            "fingerprint": tuning_fingerprint,
        }
    session["status"] = "completed"
    session["completed_at"] = datetime.now(timezone.utc).isoformat()
    session["updated_at"] = session["completed_at"]
    _write_json_atomic(paths["json"], session)


def clear_session_markers(dapi_dir: Path | str, *, keep_pkl: bool = True) -> None:
    if keep_pkl:
        paths = session_paths(dapi_dir)
        for key in ("json", "bak", "tmp"):
            path = paths[key]
            if path.is_file():
                path.unlink()
    else:
        clear_alignment_session(dapi_dir)

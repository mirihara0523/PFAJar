"""Project file management.

Handles saving and loading Mason Jar / Bell Jar project state as JSON files,
replacing opaque pickle-based serialization.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from belljar.types import (
    CANONICAL_ROLE_PATHS,
    LAYOUT_BELLJAR_V1,
    LAYOUT_IDS,
    LAYOUT_MASONJAR_V1,
    BelljarProject,
    SliceAlignment,
)

logger = logging.getLogger(__name__)

PROJECT_FILENAMES = ("project.masonjar", "project.belljar")
META_DIRS = (".masonjar", ".belljar")
BUNDLE_SUFFIXES = (".masonjar", ".belljar")
IMPORT_LOG_FILENAME = "import_log.json"
MANIFEST_FILENAME = "manifest.json"

IMAGE_EXTENSIONS = {".tif", ".tiff", ".ome.tif", ".ome.tiff", ".png", ".jpg", ".jpeg"}


def find_project_file(bundle_root: Path) -> Path | None:
    """Return the first existing project file in a bundle."""
    for name in PROJECT_FILENAMES:
        candidate = bundle_root / name
        if candidate.is_file():
            return candidate
    return None


def find_meta_dir(bundle_root: Path) -> Path:
    """Return existing meta dir or default .masonjar path."""
    for name in META_DIRS:
        candidate = bundle_root / name
        if candidate.is_dir():
            return candidate
    return bundle_root / META_DIRS[0]


def bundle_root_from_path(path: Path) -> Path:
    """Resolve bundle root from project file or bundle directory."""
    path = path.resolve()
    if path.is_file() and path.name in PROJECT_FILENAMES:
        return path.parent
    if path.is_dir():
        found = find_project_file(path)
        if found is not None:
            return path
        if any(path.name.endswith(suffix) for suffix in BUNDLE_SUFFIXES):
            return path
    raise FileNotFoundError(f"Not a Mason Jar project bundle: {path}")


def project_file_path(bundle_root: Path) -> Path:
    found = find_project_file(bundle_root)
    if found is not None:
        return found
    return bundle_root / PROJECT_FILENAMES[0]


def meta_dir(bundle_root: Path) -> Path:
    return find_meta_dir(bundle_root)


def ensure_bundle_layout(bundle_root: Path) -> None:
    """Create canonical v1 directories under a bundle root."""
    bundle_root.mkdir(parents=True, exist_ok=True)
    meta_dir(bundle_root).mkdir(parents=True, exist_ok=True)
    for rel in CANONICAL_ROLE_PATHS.values():
        (bundle_root / rel).mkdir(parents=True, exist_ok=True)


def resolve_role_path(bundle_root: Path, roles: dict[str, str], role: str) -> Path | None:
    """Resolve a role to an absolute path (relative roles joined to bundle root)."""
    rel = roles.get(role)
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p
    return (bundle_root / p).resolve()


def save_project(project: BelljarProject, path: Path) -> None:
    """Save a project to a JSON file."""
    project.save(path)
    logger.info("Project saved to %s", path)


def load_project(path: Path) -> BelljarProject:
    """Load a project from a project file or bundle directory."""
    root = bundle_root_from_path(path)
    project = BelljarProject.load(project_file_path(root))
    logger.info("Project loaded from %s (%s)", root, project.name)
    return project


def validate_project(path: Path) -> list[str]:
    """Validate bundle structure and role paths. Returns human-readable errors."""
    errors: list[str] = []
    try:
        root = bundle_root_from_path(path)
    except FileNotFoundError as exc:
        return [str(exc)]

    proj_path = project_file_path(root)
    if not proj_path.is_file():
        errors.append(f"Missing project file ({' or '.join(PROJECT_FILENAMES)}) in {root}")
        return errors

    try:
        project = BelljarProject.load(proj_path)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return [f"Invalid {proj_path.name}: {exc}"]

    if project.layout and project.layout not in LAYOUT_IDS:
        errors.append(f"Unsupported layout: {project.layout}")

    roles = project.roles or CANONICAL_ROLE_PATHS
    for role, rel in roles.items():
        resolved = resolve_role_path(root, roles, role)
        if resolved is None:
            errors.append(f"Role '{role}' has no path")
            continue
        if not resolved.exists():
            errors.append(f"Role '{role}' path missing: {resolved}")

    return errors


def write_import_log(
    bundle_root: Path,
    *,
    mode: str,
    entries: list[dict[str, Any]],
) -> Path:
    """Write meta/import_log.json audit file."""
    log_path = meta_dir(bundle_root) / IMPORT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "entries": entries,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return log_path


def _slice_id_from_name(filename: str) -> str:
    stem = Path(filename).stem
    if stem.lower().endswith(".ome"):
        stem = Path(stem).stem
    return stem.split(".")[0] if stem else stem


def _iter_image_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    suffixes = (".tif", ".tiff", ".png", ".jpg", ".jpeg")
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        lower = entry.name.lower()
        if lower.endswith(suffixes) or ".ome." in lower:
            files.append(entry)
    return files


def build_manifest(
    bundle_root: Path,
    roles: dict[str, str] | None = None,
    *,
    progress: Any | None = None,
) -> Path:
    """Scan role directories and write meta/manifest.json."""
    bundle_root = bundle_root.resolve()
    roles = roles or CANONICAL_ROLE_PATHS
    slices: dict[str, dict[str, str]] = {}

    scan_roles = ["dapi", "slices", "max", "predictions", "quantification", "pkls", "dual"]
    total_steps = len(scan_roles)
    for idx, role in enumerate(scan_roles):
        role_dir = resolve_role_path(bundle_root, roles, role)
        if progress:
            progress(idx, total_steps, f"Scanning {role}")
        if role_dir is None or not role_dir.is_dir():
            continue
        for img in _iter_image_files(role_dir):
            slice_id = _slice_id_from_name(img.name)
            if not slice_id:
                continue
            try:
                rel = str(img.relative_to(bundle_root))
            except ValueError:
                rel = str(img)
            entry = slices.setdefault(slice_id, {"sliceId": slice_id, "files": {}})
            entry["files"][role] = rel.replace("\\", "/")

    manifest_path = meta_dir(bundle_root) / MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slices": sorted(slices.values(), key=lambda s: s["sliceId"]),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    if progress:
        progress(total_steps, total_steps, "Manifest complete")
    logger.info("Manifest written: %s (%d slices)", manifest_path, len(payload["slices"]))
    return manifest_path


def import_role(
    bundle_root: Path,
    role: str,
    source: Path,
    *,
    mode: str = "copy",
    roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Import a source file or directory into the bundle role path (copy or symlink)."""
    roles = dict(roles or CANONICAL_ROLE_PATHS)
    rel_dest = roles.get(role, CANONICAL_ROLE_PATHS.get(role, f"data/counting/{role}"))
    dest = bundle_root / rel_dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = source.resolve()

    entry: dict[str, Any] = {
        "role": role,
        "source": str(source),
        "dest": rel_dest,
        "mode": mode,
    }

    if not source.exists():
        entry["error"] = "source missing"
        return entry

    if mode == "reference":
        roles[role] = str(source)
        entry["dest"] = str(source)
        return entry

    if dest.exists():
        if dest.is_symlink():
            dest.unlink()
        elif dest.is_dir() and mode != "symlink":
            shutil.rmtree(dest)
        elif dest.is_file():
            dest.unlink()

    if mode == "symlink":
        dest.symlink_to(source, target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(source, dest)

    return entry


def save_annotation(annotation: np.ndarray, path: Path) -> None:
    """Save an annotation array as a numpy file."""
    np.save(path, annotation)


def load_annotation(path: Path) -> np.ndarray:
    """Load an annotation array from a numpy file."""
    return np.load(path)


def migrate_pickle_alignment(pickle_path: Path) -> dict[str, SliceAlignment]:
    """Migrate a legacy alignment.pkl to the new format."""
    import pickle

    with open(pickle_path, "rb") as f:
        old_data = pickle.load(f)

    alignments: dict[str, SliceAlignment] = {}
    for section_name, atlas_slice in old_data.items():
        alignments[section_name] = SliceAlignment(
            section_name=atlas_slice.section_name,
            ap_position=float(atlas_slice.ap_position),
            x_angle=float(atlas_slice.x_angle),
            y_angle=float(atlas_slice.y_angle),
            region=getattr(atlas_slice, "region", "A"),
            hemisphere=getattr(atlas_slice, "hemisphere", "W"),
            linked=getattr(atlas_slice, "linked", True),
        )

    logger.info("Migrated %d alignments from legacy pickle format", len(alignments))
    return alignments

"""Slice list filtering and output-existence helpers for pipeline scripts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

ANNOTATION_PKL_RE = re.compile(r"^Annotation_.*\.pkl$", re.I)
IMAGE_EXTENSIONS = frozenset({".png", ".tif", ".tiff", ".jpg", ".jpeg"})


def natural_sort_key(value: object) -> list:
    """Numeric-aware sort key so ``...(2)`` precedes ``...(11)`` (human order)."""
    return [
        int(tok) if tok.isdigit() else tok.lower()
        for tok in re.split(r"(\d+)", str(value))
    ]


def load_slice_list(path: str | Path | None) -> set[str] | None:
    """Load slice ids from a JSON file (array of strings). Empty/missing => no filter."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        return set()
    if isinstance(data, dict) and "slice_ids" in data:
        data = data["slice_ids"]
    return {str(x) for x in data}


def slice_id_allowed(slice_id: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return slice_id in allowed


def slice_stem_from_prediction_pkl(filename: str) -> str:
    """Slice id stem from ``Predictions_<stem>.pkl`` (case-insensitive prefix)."""
    stem = Path(filename).stem
    lower = stem.lower()
    if lower.startswith("predictions_"):
        stem = stem[len("predictions_") :]
    if stem.lower().endswith(".ome"):
        stem = Path(stem).stem
    return stem.split(".")[0]


def slice_stem_from_image_filename(filename: str) -> str:
    """Slice id stem from an image basename (same rules as js/file_index sliceIdFromFilename)."""
    stem = Path(filename).stem
    if stem.lower().endswith(".ome"):
        stem = Path(stem).stem
    return stem.split(".")[0]


def slice_stem_from_annotation_pkl(filename: str) -> str:
    """Slice id stem from ``Annotation_*``, ``annotations_*``, or plain ``<stem>.pkl``."""
    stem = Path(filename).stem
    lower = stem.lower()
    if lower.startswith("annotation_"):
        stem = stem[len("annotation_") :]
    elif lower.startswith("annotations_"):
        stem = stem[len("annotations_") :]
    if stem.lower().endswith(".ome"):
        stem = Path(stem).stem
    return stem.split(".")[0]


def index_image_files(names: Iterable[str]) -> dict[str, str]:
    """Map slice stem -> first matching image filename (basename)."""
    out: dict[str, str] = {}
    for name in names:
        if Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        stem = slice_stem_from_image_filename(str(name))
        out.setdefault(stem, str(name))
    return out


def index_annotation_pkls(names: Iterable[str]) -> dict[str, str]:
    """Map slice stem -> first matching annotation filename (basename)."""
    out: dict[str, str] = {}
    for name in names:
        if not str(name).lower().endswith(".pkl"):
            continue
        if Path(name).stem.lower().startswith("predictions_"):
            continue
        stem = slice_stem_from_annotation_pkl(str(name))
        out.setdefault(stem, str(name))
    return out


def pair_image_annotation_files(
    image_names: Iterable[str],
    annotation_names: Iterable[str],
    allowed: set[str] | None = None,
) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """Pair image and annotation basenames by shared slice stem (not list index).

    Returns ``(pairs, orphan_image_stems, orphan_annotation_stems)`` where each pair is
    ``(image_basename, annotation_basename, slice_id)``.
    """
    img_by_stem = index_image_files(image_names)
    ann_by_stem = index_annotation_pkls(annotation_names)
    common = sorted(set(img_by_stem) & set(ann_by_stem), key=natural_sort_key)
    if allowed is not None:
        common = [s for s in common if slice_id_allowed(s, allowed)]
    pairs = [(img_by_stem[s], ann_by_stem[s], s) for s in common]
    orphan_images = sorted(set(img_by_stem) - set(ann_by_stem), key=natural_sort_key)
    orphan_annos = sorted(set(ann_by_stem) - set(img_by_stem), key=natural_sort_key)
    return pairs, orphan_images, orphan_annos


def build_adjust_pairs(
    img_dir: Path | str,
    annotation_dir: Path | str,
    slice_list_path: str | Path | None = None,
) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """Build stem-matched ``(image_path, annotation_path, slice_id)`` triples."""
    img_dir = Path(img_dir)
    annotation_dir = Path(annotation_dir)
    allowed = load_slice_list(slice_list_path)
    image_names = [
        f
        for f in os.listdir(img_dir)
        if Path(f).suffix.lower() in IMAGE_EXTENSIONS
    ]
    annotation_names = [
        f for f in os.listdir(annotation_dir) if f.lower().endswith(".pkl")
    ]
    pairs_base, orphan_images, orphan_annos = pair_image_annotation_files(
        image_names, annotation_names, allowed
    )
    pairs = [
        (str(img_dir / img_name), str(annotation_dir / ann_name), slice_id)
        for img_name, ann_name, slice_id in pairs_base
    ]
    return pairs, orphan_images, orphan_annos


def pair_prediction_annotation_pkls(
    prediction_names: Iterable[str],
    annotation_names: Iterable[str],
) -> list[tuple[str, str]]:
    """Pair prediction and annotation PKL basenames by shared slice stem (not list index)."""
    ann_by_stem = index_annotation_pkls(annotation_names)
    paired: list[tuple[str, str]] = []
    for p_name in sorted(
        prediction_names, key=lambda x: slice_stem_from_prediction_pkl(x)
    ):
        stem = slice_stem_from_prediction_pkl(p_name)
        a_name = ann_by_stem.get(stem)
        if a_name:
            paired.append((p_name, a_name))
    return paired


def output_exists_align(slices_dir: Path, slice_id: str) -> bool:
    """True if an annotation PKL exists for this slice id."""
    if not slices_dir.is_dir():
        return False
    candidates = (
        slices_dir / f"Annotation_{slice_id}.pkl",
        slices_dir / f"annotations_{slice_id}.pkl",
        slices_dir / f"{slice_id}.pkl",
    )
    for c in candidates:
        if c.is_file():
            return True
    for entry in slices_dir.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".pkl":
            continue
        name = entry.stem
        if name.lower().startswith("annotation_"):
            name = name[len("Annotation_") :]
        elif name.lower().startswith("annotations_"):
            name = name[len("annotations_") :]
        if name.split(".")[0] == slice_id:
            return True
    return False


def output_exists_intensity(pkls_dir: Path, slice_id: str) -> bool:
    """True if any ROI PKL for this slice stem exists."""
    if not pkls_dir.is_dir():
        return False
    prefix = slice_id + "_"
    for entry in pkls_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".pkl":
            if entry.name.startswith(prefix):
                return True
    return False


def output_exists_count(quant_dir: Path, slice_id: str) -> bool:
    """Count writes one CSV; per-slice skip is best-effort (always False)."""
    return False


def filter_by_outputs(
    slice_ids: Iterable[str],
    output_dir: Path,
    step: str,
    mode: str,
) -> tuple[list[str], list[str]]:
    """Return (to_process, skipped) given run mode overwrite|skip|merge."""
    mode = (mode or "merge").lower()
    if mode == "overwrite":
        ids = list(slice_ids)
        return ids, []
    checkers = {
        "align": output_exists_align,
        "intensity": output_exists_intensity,
        "count": output_exists_count,
    }
    checker = checkers.get(step, lambda _d, _s: False)
    to_process: list[str] = []
    skipped: list[str] = []
    for sid in slice_ids:
        if mode in ("skip", "merge") and checker(output_dir, sid):
            skipped.append(sid)
        else:
            to_process.append(sid)
    return to_process, skipped

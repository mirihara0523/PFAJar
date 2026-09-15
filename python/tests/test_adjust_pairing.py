"""Viewer/Editor pairs DAPI images to annotation PKLs by slice stem."""

from __future__ import annotations

import json
import sys
from pathlib import Path

py_dir = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(py_dir))

from slice_index import (  # noqa: E402
    build_adjust_pairs,
    pair_image_annotation_files,
    slice_stem_from_image_filename,
)


def _write_images(img_dir: Path, stems: list[str], ext: str = ".png") -> None:
    for stem in stems:
        (img_dir / f"{stem}{ext}").write_bytes(b"img")


def _write_annotations(anno_dir: Path, stems: list[str]) -> None:
    for stem in stems:
        (anno_dir / f"Annotation_{stem}.pkl").write_bytes(b"pkl")


def test_mismatched_counts_pair_by_stem(tmp_path: Path) -> None:
    """62 DAPI images and 36 annotation PKLs (s027–s062) yield 36 stem pairs."""
    img_dir = tmp_path / "dapi"
    anno_dir = tmp_path / "slices"
    img_dir.mkdir()
    anno_dir.mkdir()
    image_stems = [f"M528_s{i:03d}" for i in range(1, 63)]
    anno_stems = [f"M528_s{i:03d}" for i in range(27, 63)]
    _write_images(img_dir, image_stems)
    _write_annotations(anno_dir, anno_stems)

    pairs, orphan_imgs, orphan_annos = build_adjust_pairs(img_dir, anno_dir)
    assert len(pairs) == 36
    assert pairs[0][2] == "M528_s027"
    assert pairs[-1][2] == "M528_s062"
    assert len(orphan_imgs) == 26
    assert orphan_imgs[0] == "M528_s001"
    assert orphan_imgs[-1] == "M528_s026"
    assert orphan_annos == []


def test_s027_tif_pairs_with_s027_not_s001(tmp_path: Path) -> None:
    img_dir = tmp_path / "dapi"
    anno_dir = tmp_path / "slices"
    img_dir.mkdir()
    anno_dir.mkdir()
    _write_images(img_dir, ["M528_s001", "M528_s027"], ext=".tif")
    _write_annotations(anno_dir, ["M528_s027"])

    pairs, _, _ = build_adjust_pairs(img_dir, anno_dir)
    assert len(pairs) == 1
    img_path, anno_path, slice_id = pairs[0]
    assert slice_id == "M528_s027"
    assert img_path.endswith("M528_s027.tif")
    assert anno_path.endswith("Annotation_M528_s027.pkl")


def test_slice_list_filters_pairs(tmp_path: Path) -> None:
    img_dir = tmp_path / "dapi"
    anno_dir = tmp_path / "slices"
    img_dir.mkdir()
    anno_dir.mkdir()
    _write_images(img_dir, ["M528_s061", "M528_s062"])
    _write_annotations(anno_dir, ["M528_s061", "M528_s062"])

    slice_list = tmp_path / "run_slice_list.json"
    slice_list.write_text(
        json.dumps({"slice_ids": ["M528_s061"]}), encoding="utf-8"
    )

    pairs, _, _ = build_adjust_pairs(img_dir, anno_dir, slice_list)
    assert len(pairs) == 1
    assert pairs[0][2] == "M528_s061"


def test_pair_ignores_sort_order() -> None:
    imgs = ["M528_s099.png", "M528_s061.png"]
    annos = ["Annotation_M528_s061.pkl", "Annotation_M528_s099.pkl"]
    pairs, _, _ = pair_image_annotation_files(imgs, annos)
    assert pairs == [
        ("M528_s061.png", "Annotation_M528_s061.pkl", "M528_s061"),
        ("M528_s099.png", "Annotation_M528_s099.pkl", "M528_s099"),
    ]


def test_slice_stem_from_image_ome_tiff() -> None:
    assert slice_stem_from_image_filename("M528_s061.ome.tiff") == "M528_s061"
    assert slice_stem_from_image_filename("M528_s061.tiff") == "M528_s061"

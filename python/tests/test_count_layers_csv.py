"""Count CSV emits laminar acronyms when parcellation meta is cortical layers.

Regression for the layers-tier resolve + legacy CSV filter mismatch: Electron
never passes --layers, so counts keyed as VISp4 were dropped from
count_results.csv while parent VISp rows stayed at zero.
"""

from __future__ import annotations

import csv
import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
PY_DIR = _REPO_ROOT / "py"
sys.path.insert(0, str(PY_DIR))

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"
_MAP_PATH = _REPO_ROOT / "csv" / "structure_map.pkl"


def _benv_python() -> Path:
    base = Path.home() / ".masonjar" / "benv"
    win = base / "Scripts" / "python.exe"
    posix = base / "bin" / "python"
    return win if win.exists() else posix


BENV_PYTHON = _benv_python()


def _parse_totals(csv_path: Path) -> dict[str, int]:
    totals: dict[str, int] = {}
    in_totals = False
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0] == "Totals":
                in_totals = True
                continue
            if in_totals:
                if row[0] == "Region Acronym":
                    continue
                if row[0].startswith("Colocalization"):
                    break
                if len(row) >= 3 and row[2] != "":
                    totals[row[0]] = int(row[2])
    return totals


@pytest.mark.skipif(not BENV_PYTHON.exists(), reason="masonjar benv not available")
@pytest.mark.skipif(not _MAP_PATH.is_file(), reason="structure_map.pkl missing")
@pytest.mark.skipif(not _GRAPH_PATH.is_file(), reason="structure_graph.json missing")
def test_layers_parcellation_csv_includes_visp4(tmp_path: Path) -> None:
    from find_neurons import DetectionResult
    from structure_catalog import load_catalog

    catalog = load_catalog(_GRAPH_PATH)
    visp4 = int(catalog["by_acronym"]["VISp4"]["id"])

    pred_dir = tmp_path / "predictions"
    anno_dir = tmp_path / "annotations"
    out_dir = tmp_path / "out"
    pred_dir.mkdir()
    anno_dir.mkdir()
    out_dir.mkdir()

    # Copy map next to graph so count.py can load the catalog.
    struct_dir = tmp_path / "csv"
    struct_dir.mkdir()
    import shutil

    shutil.copy(_MAP_PATH, struct_dir / "structure_map.pkl")
    shutil.copy(_GRAPH_PATH, struct_dir / "structure_graph.json")
    struct_path = struct_dir / "structure_map.pkl"

    ann = np.full((4, 4), visp4, dtype=np.uint32)
    with open(anno_dir / "Annotation_s001.pkl", "wb") as f:
        pickle.dump(ann, f)

    meta_dir = anno_dir / ".masonjar"
    meta_dir.mkdir()
    with open(meta_dir / "annotation_parcellation.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "s001": {
                    "tier_id": "layers",
                    "st_level": None,
                    "applied_at": "2026-01-01T00:00:00+00:00",
                }
            },
            f,
        )

    det = DetectionResult(boxes=[[1, 1, 1, 1]], scores=[0.99], image_dimensions=(4, 4))
    with open(pred_dir / "Predictions_s001.pkl", "wb") as f:
        pickle.dump([det], f)

    result = subprocess.run(
        [
            str(BENV_PYTHON),
            str(PY_DIR / "count.py"),
            "-p",
            str(pred_dir),
            "-a",
            str(anno_dir),
            "-o",
            str(out_dir),
            "-m",
            str(struct_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"count.py failed: {result.stderr}\n{result.stdout}"
    assert "count_rollup=tier:layers" in result.stdout

    totals = _parse_totals(out_dir / "count_results.csv")
    assert totals.get("VISp4", 0) == 1, f"expected laminar VISp4 count, got {totals}"
    assert sum(totals.values()) == 1


@pytest.mark.skipif(not BENV_PYTHON.exists(), reason="masonjar benv not available")
@pytest.mark.skipif(not _MAP_PATH.is_file(), reason="structure_map.pkl missing")
@pytest.mark.skipif(not _GRAPH_PATH.is_file(), reason="structure_graph.json missing")
def test_mixed_slice_parcellation_csv_emits_both_tiers(tmp_path: Path) -> None:
    from find_neurons import DetectionResult
    from structure_catalog import load_catalog

    catalog = load_catalog(_GRAPH_PATH)
    visp4 = int(catalog["by_acronym"]["VISp4"]["id"])
    vis = int(catalog["by_acronym"]["VIS"]["id"])

    pred_dir = tmp_path / "predictions"
    anno_dir = tmp_path / "annotations"
    out_dir = tmp_path / "out"
    pred_dir.mkdir()
    anno_dir.mkdir()
    out_dir.mkdir()

    struct_dir = tmp_path / "csv"
    struct_dir.mkdir()
    import shutil

    shutil.copy(_MAP_PATH, struct_dir / "structure_map.pkl")
    shutil.copy(_GRAPH_PATH, struct_dir / "structure_graph.json")
    struct_path = struct_dir / "structure_map.pkl"

    with open(anno_dir / "Annotation_s001.pkl", "wb") as f:
        pickle.dump(np.full((4, 4), visp4, dtype=np.uint32), f)
    with open(anno_dir / "Annotation_s002.pkl", "wb") as f:
        pickle.dump(np.full((4, 4), vis, dtype=np.uint32), f)

    meta_dir = anno_dir / ".masonjar"
    meta_dir.mkdir()
    with open(meta_dir / "annotation_parcellation.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "s001": {"tier_id": "layers", "st_level": None, "applied_at": "t"},
                "s002": {"tier_id": "areas", "st_level": None, "applied_at": "t"},
            },
            f,
        )

    det = DetectionResult(boxes=[[1, 1, 1, 1]], scores=[0.99], image_dimensions=(4, 4))
    for stem in ("s001", "s002"):
        with open(pred_dir / f"Predictions_{stem}.pkl", "wb") as f:
            pickle.dump([det], f)

    result = subprocess.run(
        [
            str(BENV_PYTHON),
            str(PY_DIR / "count.py"),
            "-p",
            str(pred_dir),
            "-a",
            str(anno_dir),
            "-o",
            str(out_dir),
            "-m",
            str(struct_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"count.py failed: {result.stderr}\n{result.stdout}"
    assert "mixed tiers=" in result.stdout

    totals = _parse_totals(out_dir / "count_results.csv")
    assert totals.get("VISp4", 0) == 1, f"layers slice missing: {totals}"
    assert totals.get("VIS", 0) == 1, f"areas slice missing: {totals}"
    assert sum(totals.values()) == 2

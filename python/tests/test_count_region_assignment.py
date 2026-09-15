"""End-to-end check that count.py assigns detections to the correct atlas region.

This guards the annotation-resize order: ``predictions[].image_dimensions`` is
``(height, width)`` but ``resize_image_nearest_neighbor`` forwards ``new_size``
to SimpleITK ``SetSize`` which expects ``(width, height)``. If the axes are
swapped, a detection is silently mapped to the wrong region for non-square
images.

No real project data is required; we synthesise a tiny structure map,
annotation, and a single-detection prediction pickle, run count.py as a
subprocess (the way Electron runs it), and parse count_results.csv.
"""

from __future__ import annotations

import csv
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

PY_DIR = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(PY_DIR))

def _benv_python() -> Path:
    """Resolve the Mason Jar venv interpreter on either layout.

    POSIX venvs put the interpreter in ``benv/bin/python``; Windows venvs use
    ``benv/Scripts/python.exe``. Hardcoding the POSIX path made these Tier 1
    behavioral tests silently skip on Windows (the primary OS).
    """
    base = Path.home() / ".masonjar" / "benv"
    win = base / "Scripts" / "python.exe"
    posix = base / "bin" / "python"
    return win if win.exists() else posix


BENV_PYTHON = _benv_python()


def _structure_map() -> dict:
    """Four leaf regions (no layers, no parents) keyed by int id."""
    return {
        10: {"acronym": "A", "name": "Region A", "id_path": "10", "color": [1, 1, 1]},
        20: {"acronym": "B", "name": "Region B", "id_path": "20", "color": [2, 2, 2]},
        30: {"acronym": "C", "name": "Region C", "id_path": "30", "color": [3, 3, 3]},
        40: {"acronym": "D", "name": "Region D", "id_path": "40", "color": [4, 4, 4]},
    }


def _quadrant_annotation() -> np.ndarray:
    """Tall (height=8, width=4) annotation split into four quadrants.

    Region depends on BOTH axes so a swapped (x, y) lookup lands elsewhere:
      y<4, x<2 -> 10 (A)    y<4, x>=2 -> 20 (B)
      y>=4, x<2 -> 30 (C)   y>=4, x>=2 -> 40 (D)
    """
    a = np.zeros((8, 4), dtype=np.uint32)
    a[:4, :2] = 10
    a[:4, 2:] = 20
    a[4:, :2] = 30
    a[4:, 2:] = 40
    return a


def _parse_totals(csv_path: Path) -> dict[str, int]:
    """Return {acronym: count} from the Totals block of count_results.csv."""
    totals: dict[str, int] = {}
    in_totals = False
    with open(csv_path) as f:
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
                # [acronym, name, count, area]
                if len(row) >= 3 and row[2] != "":
                    totals[row[0]] = int(row[2])
    return totals


@pytest.mark.skipif(not BENV_PYTHON.exists(), reason="masonjar benv not available")
def test_detection_maps_to_correct_quadrant(tmp_path: Path) -> None:
    from find_neurons import DetectionResult

    pred_dir = tmp_path / "predictions"
    anno_dir = tmp_path / "annotations"
    out_dir = tmp_path / "out"
    pred_dir.mkdir()
    anno_dir.mkdir()
    out_dir.mkdir()

    struct_path = tmp_path / "structure_map.pkl"
    with open(struct_path, "wb") as f:
        pickle.dump(_structure_map(), f)

    with open(anno_dir / "Annotation_s001.pkl", "wb") as f:
        pickle.dump(_quadrant_annotation(), f)

    # Detection centred at image coord (x=3, y=1): top-right quadrant -> region B (20).
    # Box [x, y, mX, mY] = [3, 1, 3, 1] => xPos=3, yPos=1.
    det = DetectionResult(boxes=[[3, 1, 3, 1]], scores=[0.99], image_dimensions=(8, 4))
    with open(pred_dir / "Predictions_s001.pkl", "wb") as f:
        pickle.dump([det], f)

    result = subprocess.run(
        [
            str(BENV_PYTHON),
            str(PY_DIR / "count.py"),
            "-p", str(pred_dir),
            "-a", str(anno_dir),
            "-o", str(out_dir),
            "-m", str(struct_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"count.py failed: {result.stderr}\n{result.stdout}"

    totals = _parse_totals(out_dir / "count_results.csv")
    # Correct mapping: detection at (x=3, y=1) is in the top-right quadrant -> B.
    assert totals.get("B", 0) == 1, f"expected detection counted in B, got {totals}"
    assert totals.get("A", 0) == 0, f"detection mis-assigned to A (axis swap): {totals}"

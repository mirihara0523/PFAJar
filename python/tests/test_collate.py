"""collate.py aggregates count_results.csv Totals across sections/projects."""

from __future__ import annotations

import csv
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

PY_DIR = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(PY_DIR))

from collate import _read_totals_block  # noqa: E402

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
    return {
        10: {"acronym": "A", "name": "Region A", "id_path": "10", "color": [1, 1, 1]},
        20: {"acronym": "B", "name": "Region B", "id_path": "20", "color": [2, 2, 2]},
    }


def _write_count_csv(path: Path, totals: dict[str, tuple[int, int]]) -> None:
    """Write a minimal count_results.csv with a Totals block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        # A section block (should be ignored by collate, which reads Totals).
        w.writerow(["s001"])
        w.writerow(["Region Acronym", "Region Name", "Area (px)", "Channel #0"])
        for acr, (cnt, _area) in totals.items():
            w.writerow([acr, f"Region {acr}", 100, cnt])
        w.writerow([])
        # Totals block (what collate aggregates).
        w.writerow(["Totals"])
        w.writerow(["Region Acronym", "Region Name", "Count", "Area (px)"])
        for acr, (cnt, area) in totals.items():
            w.writerow([acr, f"Region {acr}", cnt, area])
        w.writerow([])
        w.writerow(["Colocalization Matrix (by Section)"])
        w.writerow(["s001", "Channel #0"])


def test_read_totals_block_parses_counts_and_areas(tmp_path: Path) -> None:
    csv_path = tmp_path / "count_results.csv"
    _write_count_csv(csv_path, {"A": (5, 1000), "B": (3, 500)})
    block = _read_totals_block(csv_path)
    assert block == {"A": (5, 1000), "B": (3, 500)}


def _parse_collated(path: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) >= 4:
                out[row[0]] = (int(row[2]), int(row[3]))
    return out


@pytest.mark.skipif(not BENV_PYTHON.exists(), reason="masonjar benv not available")
def test_collate_sums_across_projects(tmp_path: Path) -> None:
    struct_path = tmp_path / "structure_map.pkl"
    with open(struct_path, "wb") as f:
        pickle.dump(_structure_map(), f)

    stage = tmp_path / "stage"
    _write_count_csv(stage / "0_count" / "count_results.csv", {"A": (5, 1000), "B": (3, 500)})
    _write_count_csv(stage / "1_count" / "count_results.csv", {"A": (2, 400), "B": (10, 999)})

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            str(BENV_PYTHON),
            str(PY_DIR / "collate.py"),
            "-i", str(stage),
            "-o", str(out_dir),
            "-s", str(struct_path),
            "-g", "False",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"collate failed: {result.stderr}\n{result.stdout}"
    collated = _parse_collated(out_dir / "collated_results.csv")
    assert collated.get("A") == (7, 1400), collated
    assert collated.get("B") == (13, 1499), collated


@pytest.mark.skipif(not BENV_PYTHON.exists(), reason="masonjar benv not available")
def test_collate_region_filter(tmp_path: Path) -> None:
    struct_path = tmp_path / "structure_map.pkl"
    with open(struct_path, "wb") as f:
        pickle.dump(_structure_map(), f)
    stage = tmp_path / "stage"
    _write_count_csv(stage / "0_count" / "count_results.csv", {"A": (5, 1000), "B": (3, 500)})

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            str(BENV_PYTHON),
            str(PY_DIR / "collate.py"),
            "-i", str(stage),
            "-o", str(out_dir),
            "-s", str(struct_path),
            "-r", "A",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"collate failed: {result.stderr}\n{result.stdout}"
    collated = _parse_collated(out_dir / "collated_results.csv")
    assert "A" in collated and "B" not in collated, collated

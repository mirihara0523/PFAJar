"""Candidate filtering and atomic persistence for the interactive detection viewer."""
import json
import os
from pathlib import Path
import tempfile
import ntpath

import numpy as np


def windows_io_path(path):
    """Use Windows extended-length paths for disk I/O, including UNC shares."""
    if os.name != "nt":
        return Path(path)
    absolute = ntpath.abspath(os.fspath(path))
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def filter_indices(records, params, floor):
    confidence = float(params.get("confidence", 0.5))
    area = float(params.get("area", 200))
    eccentricity = float(params.get("eccentricity", 0.2))
    intensity = float(params.get("intensity", 0))
    values = [confidence, area, eccentricity, intensity, floor]
    if not all(np.isfinite(value) for value in values):
        raise ValueError("Filter values must be finite numbers.")
    if not floor <= confidence <= 1:
        raise ValueError(f"Confidence must be between candidate floor {floor:g} and 1. Run detection again to lower the floor.")
    if area < 0 or not 0 <= eccentricity <= 1 or not 0 <= intensity <= 255:
        raise ValueError("Area must be nonnegative, eccentricity 0–1, and intensity 0–255.")
    indices = [i for i, row in enumerate(records) if row[4] >= confidence and row[5] > area]
    # Match the existing batch detector's upper-area outlier rule.
    if len(indices) >= 3:
        areas = np.asarray([records[i][5] for i in indices], dtype=float)
        upper = float(areas.mean() + 2 * areas.std())
        indices = [i for i in indices if records[i][5] < upper]
    return [i for i in indices
            if (records[i][6] is None or records[i][6] > eccentricity)
            and (intensity <= 0 or records[i][7] is None or records[i][7] >= intensity)]


def atomic_write(target, writer):
    """Keep the previous complete result if serialization or replacement fails."""
    target = windows_io_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = stream.name
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def write_json(target, data):
    atomic_write(target, lambda stream: stream.write(
        json.dumps(data, allow_nan=False, ensure_ascii=False, indent=2).encode("utf-8")))

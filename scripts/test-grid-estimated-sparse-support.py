"""Regression for Grid-estimated sparse-tile safety.

A seam with only a tiny tissue fragment on both sides must not derive a
row-local offset for the entire tile.  A well-supported neighboring seam
continues to receive its local correction.
"""
from pathlib import Path
import os
import sys

import numpy as np

os.environ.setdefault("MASONJAR_IO_FAIRSHARE", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import seam_correct


def main() -> int:
    height, width = 256, 300
    image = np.full((height, width), 80, dtype=np.uint8)
    image[:, 100:200] += 20
    image[:, 200:] -= 20
    tissue = np.zeros((height, width), dtype=bool)

    # Only 16 paired rows support the first seam: below the 8% / 20-row floor.
    tissue[100:116, 96:104] = True
    # The second seam has abundant paired tissue evidence.
    tissue[32:224, 196:204] = True

    _, adapted, fallback = seam_correct._vertical_local_grid(image, [100, 200], tissue)
    assert 100 in fallback, (adapted, fallback)
    assert 100 not in adapted, (adapted, fallback)
    assert 200 in adapted, (adapted, fallback)
    print("Grid-estimated sparse-tile safety: unsupported seam uses global fallback; supported seam adapted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

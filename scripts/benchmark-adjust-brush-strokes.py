"""Compare Adjustment Viewer's current stamp brush with a vectorized capsule.

The benchmark includes label mutation plus the data needed to restore a stroke
with Undo. It intentionally excludes Qt paint/compositing, which both paths
perform after the changed pixels have been identified.
"""
from __future__ import annotations

from functools import lru_cache
from statistics import median
from time import perf_counter

import numpy as np


@lru_cache(maxsize=8)
def circle_offsets(radius: int) -> tuple[tuple[int, int], ...]:
    return tuple(
        (x, y)
        for x in range(-radius, radius + 1)
        for y in range(-radius, radius + 1)
        if x * x + y * y <= radius * radius
    )


def centers(p0: tuple[int, int], p1: tuple[int, int], radius: int):
    x0, y0 = p0
    x1, y1 = p1
    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    n = int(dist // max(1, radius))
    return [
        (round(x0 + (x1 - x0) * k / (n + 1)), round(y0 + (y1 - y0) * k / (n + 1)))
        for k in range(1, n + 1)
    ] + [p1]


def stamp_stroke(labels: np.ndarray, p0, p1, radius: int, value: int):
    """Current Viewer strategy: interpolated circular stamps + dict Undo."""
    h, w = labels.shape
    originals: dict[tuple[int, int], int] = {}
    changed: set[tuple[int, int]] = set()
    for cx, cy in [p0] + centers(p0, p1, radius):
        for ox, oy in circle_offsets(radius):
            x, y = cx + ox, cy + oy
            point = (x, y)
            if 0 <= x < w and 0 <= y < h and point not in changed:
                originals[point] = int(labels[y, x])
                changed.add(point)
                labels[y, x] = value
    return changed, originals


def capsule_stroke(labels: np.ndarray, p0, p1, radius: int, value: int):
    """Candidate: one vectorized round-cap capsule + compact array Undo."""
    h, w = labels.shape
    x0, y0 = p0
    x1, y1 = p1
    pad = radius + 1
    left, right = max(0, min(x0, x1) - pad), min(w, max(x0, x1) + pad + 1)
    top, bottom = max(0, min(y0, y1) - pad), min(h, max(y0, y1) + pad + 1)
    yy, xx = np.ogrid[top:bottom, left:right]
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom:
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / denom, 0.0, 1.0)
        dist2 = (xx - (x0 + t * dx)) ** 2 + (yy - (y0 + t * dy)) ** 2
    else:
        dist2 = (xx - x0) ** 2 + (yy - y0) ** 2
    mask = dist2 <= radius * radius
    ly, lx = np.nonzero(mask)
    ys, xs = ly + top, lx + left
    originals = labels[ys, xs].copy()
    labels[ys, xs] = value
    return (ys, xs), originals


def measure(fn, base, p0, p1, radius, repeats=7):
    elapsed = []
    out = None
    for _ in range(repeats):
        labels = base.copy()
        started = perf_counter()
        out = fn(labels, p0, p1, radius, 777)
        elapsed.append((perf_counter() - started) * 1000)
    return median(elapsed), out


def main() -> int:
    # A long diagonal on a realistic large annotation canvas; r=35 matches the
    # current Adjustment Viewer default (radius, hence 70px diameter).
    base = np.zeros((2048, 2048), dtype=np.uint32)
    p0, p1, radius = (300, 350), (1750, 1510), 35
    stamp_ms, (stamp_changed, _stamp_originals) = measure(stamp_stroke, base, p0, p1, radius)
    capsule_ms, ((ys, xs), _capsule_originals) = measure(capsule_stroke, base, p0, p1, radius)
    overlap = len(stamp_changed.intersection(zip(xs.tolist(), ys.tolist())))
    print(
        "adjust_brush_benchmark "
        f"canvas=2048x2048 radius={radius} path_length=1857px "
        f"stamp={stamp_ms:.1f}ms pixels={len(stamp_changed)} "
        f"capsule={capsule_ms:.1f}ms pixels={len(xs)} "
        f"overlap={overlap / max(1, len(stamp_changed)):.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

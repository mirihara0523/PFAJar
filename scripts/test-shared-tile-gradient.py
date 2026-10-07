"""Evaluate one shared 2-D within-tile brightness gradient on a DAPI preview.

This intentionally does not use the production Grid-estimated correction.
It estimates one horizontal and one vertical within-tile slope from all
periodic boundaries, then applies both components in a single pass.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("MASONJAR_IO_FAIRSHARE", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import seam_correct


def _shared_step(image: np.ndarray, tissue: np.ndarray, bounds: list[int], band: int) -> float:
    steps = [seam_correct.estimate_step(image.astype(np.float32), tissue, point, band=band) for point in bounds]
    usable = np.asarray([value for value in steps if abs(value) >= 0.25], dtype=np.float32)
    if usable.size < 3:
        return 0.0
    positive = usable[usable > 0]
    negative = usable[usable < 0]
    agreeing = positive if positive.size >= negative.size else negative
    # A shared tile gradient predicts the same boundary-step direction at
    # every repeated boundary.  With only three detected boundaries, two
    # agreeing steps are not enough evidence to impose that gradient.
    if agreeing.size < 3:
        return 0.0
    return float(np.median(agreeing))


def _apply_shared_gradient(image: np.ndarray, v_bounds: list[int], h_bounds: list[int], dx: float, dy: float) -> np.ndarray:
    out = image.astype(np.float32).copy()
    h, w = image.shape
    for lo, hi in seam_correct._tile_intervals(v_bounds, w):
        unit = (np.arange(lo, hi, dtype=np.float32) - lo) / max(1, hi - lo - 1) - 0.5
        out[:, lo:hi] += dx * unit[None, :]
    for lo, hi in seam_correct._tile_intervals(h_bounds, h):
        unit = (np.arange(lo, hi, dtype=np.float32) - lo) / max(1, hi - lo - 1) - 0.5
        out[lo:hi, :] += dy * unit[:, None]
    return np.clip(out, 0, 255).astype(np.uint8)


def _regularized_offsets(steps: list[float], ridge: float) -> np.ndarray:
    """Fit zero-mean tile offsets while penalizing broad brightness drift."""
    count = len(steps) + 1
    diff = np.zeros((count - 1, count), dtype=np.float32)
    for index in range(count - 1):
        diff[index, index] = -1.0
        diff[index, index + 1] = 1.0
    system = diff.T @ diff + max(0.0, float(ridge)) * np.eye(count, dtype=np.float32)
    constrained = np.zeros((count + 1, count + 1), dtype=np.float32)
    constrained[:count, :count] = system
    constrained[:count, count] = 1.0
    constrained[count, :count] = 1.0
    rhs = np.r_[diff.T @ np.asarray(steps, dtype=np.float32), 0.0]
    return np.linalg.solve(constrained, rhs)[:count]


def _apply_axis_seam_offsets(
    image: np.ndarray,
    tissue: np.ndarray,
    bounds: list[int],
    band: int,
    ridge: float = 0.0,
) -> tuple[np.ndarray, list[float]]:
    """Apply measured residual offsets for one axis after the shared plane."""
    steps: list[float] = []
    for point in bounds:
        step = float(seam_correct.estimate_step(image.astype(np.float32), tissue, point, band=band))
        steps.append(step)
    significant = np.asarray([value for value in steps if abs(value) >= 0.25], dtype=np.float32)
    agreeing = max(int((significant > 0).sum()), int((significant < 0).sum())) if significant.size else 0
    # A few conflicting residuals are anatomy or an incomplete edge tile,
    # not a reliable axis-wide seam model.  Do not make the image worse by
    # forcing their cumulative offsets.
    if agreeing < 3:
        return image.copy(), steps
    offsets = _regularized_offsets(steps, ridge)
    out = image.astype(np.float32)
    edges = [0] + bounds + [image.shape[1]]
    for index, (start, end) in enumerate(zip(edges[:-1], edges[1:])):
        region = out[:, start:end]
        region_mask = tissue[:, start:end]
        region[region_mask] -= offsets[index]
    return np.clip(out, 0, 255).astype(np.uint8), steps


def _mean_residual(image: np.ndarray, tissue: np.ndarray, bounds: list[int], *, transpose: bool, band: int) -> float:
    work, mask = (image.T, tissue.T) if transpose else (image, tissue)
    if not bounds:
        return 0.0
    values = [abs(seam_correct.estimate_step(work.astype(np.float32), mask, point, band=band)) for point in bounds]
    return float(np.mean(values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--band", type=int, default=4)
    parser.add_argument("--seams", action="store_true", help="also correct residual horizontal and vertical seam offsets")
    parser.add_argument("--ridge", type=float, default=None, help="residual offset regularization; implies --seams")
    parser.add_argument("--normalize-background", action="store_true", help="set source low-signal background to black")
    args = parser.parse_args()
    image = cv2.imread(args.input, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"Could not read: {args.input}")
    tissue = image > 8
    _, v_bounds = seam_correct._periodic_grid_positions(image)
    _, h_bounds = seam_correct._periodic_grid_positions(image, transpose=True)
    dx = _shared_step(image, tissue, v_bounds, args.band)
    dy = _shared_step(image.T, tissue.T, h_bounds, args.band)
    corrected = _apply_shared_gradient(image, v_bounds, h_bounds, dx, dy)
    h_steps: list[float] = []
    v_steps: list[float] = []
    if args.seams or args.ridge is not None:
        ridge = 0.0 if args.ridge is None else args.ridge
        horizontal, h_steps = _apply_axis_seam_offsets(corrected.T, tissue.T, h_bounds, args.band, ridge)
        corrected, v_steps = _apply_axis_seam_offsets(horizontal.T, tissue, v_bounds, args.band, ridge)
    if args.normalize_background:
        corrected, background_floor = seam_correct._normalize_grid_background(corrected, image)
    else:
        background_floor = None
    cv2.imwrite(args.output, corrected)
    report = {
        "vertical_boundaries": v_bounds,
        "horizontal_boundaries": h_bounds,
        "shared_x_step": round(dx, 3),
        "shared_y_step": round(dy, 3),
        "vertical_residual_before": round(_mean_residual(image, tissue, v_bounds, transpose=False, band=args.band), 3),
        "vertical_residual_after": round(_mean_residual(corrected, tissue, v_bounds, transpose=False, band=args.band), 3),
        "horizontal_residual_before": round(_mean_residual(image, tissue, h_bounds, transpose=True, band=args.band), 3),
        "horizontal_residual_after": round(_mean_residual(corrected, tissue, h_bounds, transpose=True, band=args.band), 3),
        "horizontal_residual_steps": [round(value, 3) for value in h_steps],
        "vertical_residual_steps": [round(value, 3) for value in v_steps],
        "ridge": args.ridge,
        "background_floor": background_floor,
    }
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

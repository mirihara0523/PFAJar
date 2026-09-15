"""Vertical tile-seam correction for Mason Jar (preprocess step, image-only).

Detects a periodic vertical seam pattern left over from tile stitching
(autocorrelation over the mean column-to-column gradient, restricted to
tissue pixels) and removes the per-column brightness step with a additive
offset correction. See `_coordination/seam_prototype.py` (Session-2,
2026-09-03) for the algorithm derivation, the off-by-one root cause that was
found there, and validation numbers (-58% residual on the reference slice).

Only `offset` mode is wired up for production. `gain`/`blend` modes are not
exposed here yet -- see STATUS.md "미해결" for why (gain still makes things
worse even after fixing the stats it reads; blend is metric-gaming-suspect).
"""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import tifffile as tiff

from grayscale_load import load_grayscale_uint8, read_image_size

DEFAULT_BAND = 4
PERIOD_MIN = 40
PERIOD_MAX = 400


def _log(msg: str) -> None:
    print(f"LOG: {msg}", flush=True)


def _progress(pct: int, message: str) -> None:
    print(f"PROGRESS:{int(pct)}:{message}", flush=True)


def emit_preview_json(payload: dict) -> None:
    print("PREVIEW_JSON:" + json.dumps(payload), flush=True)


def _log_exception(context: str, exc: Exception) -> None:
    """Mirror the traceback into the durable stdout log (LOG: prefix) in
    addition to stderr, so it survives even while main.js's preview path
    drops stderr / overwrites the PREVIEW_JSON error (see memory
    `masonjar-basic-seam-issue` bug#1, unresolved as of 2026-09-03)."""
    tb = traceback.format_exc().strip().replace("\n", " | ")
    _log(f"seam_error context={context} err={exc!r} tb={tb}")
    traceback.print_exc()


def _atomic_write_png(dest: Path, arr: np.ndarray) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # cv2.imwrite picks its encoder from the file extension -- the temp name
    # must keep a real image extension (see basic_correct.py fix, 2026-09-03).
    ext = dest.suffix or ".png"
    tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.tmp{ext}")
    if not cv2.imwrite(str(tmp), arr):
        raise OSError(f"failed to write PNG {tmp}")
    tmp.replace(dest)


def _atomic_write_tiff(dest: Path, arr: np.ndarray) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".{os.getpid()}.tmp")
    tiff.imwrite(str(tmp), arr)
    tmp.replace(dest)


_NATSORT_RE = re.compile(r"(\d+)")


def _natsort_key(name: str):
    parts = _NATSORT_RE.split(name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _list_image_files_any(dir_path: Path) -> list[Path]:
    """PNG/TIFF/JPEG listing, natural-sorted (DAPI slices are PNG; signal
    branches are TIFF -- seam correction is image-only and format-agnostic)."""
    if not dir_path.is_dir():
        return []
    files = [
        p
        for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in (".png", ".tif", ".tiff", ".jpg", ".jpeg")
    ]
    files.sort(key=lambda p: _natsort_key(p.name))
    return files


def _slice_stem(path: Path) -> str:
    name = path.name
    if ".ome." in name.lower():
        return name.split(".")[0]
    return path.stem


def _normalize_slice_key(value: str) -> str:
    """Normalize config slice names and filesystem stems for matching."""
    text = str(value or "").strip().lower()
    for suffix in (".tiff", ".tif", ".png", ".jpg", ".jpeg"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


# --- core algorithm (see _coordination/seam_prototype.py for derivation) ---


def _column_gradient_profile(img: np.ndarray, tissue: np.ndarray) -> np.ndarray:
    """Mean column-to-column gradient magnitude over tissue pixels, i.e. the
    `ap` array `detect_vertical_period()` builds before it does the
    autocorrelation search. Split out so a fixed set of seam columns can be
    re-scored on a *different* (e.g. corrected) image without re-running
    period detection on it -- see _autotune_band()'s docstring for why that
    matters."""
    d = img[:, 1:] - img[:, :-1]
    m = tissue[:, 1:] & tissue[:, :-1]
    cnt = m.sum(0)
    s = np.where(m, d, 0).sum(0)
    prof = np.where(cnt > 20, s / np.maximum(cnt, 1), 0.0)
    return np.abs(prof)


def _autocorrelation_period_candidates(
    ap: np.ndarray,
    *,
    min_period: int = PERIOD_MIN,
    max_period: int = PERIOD_MAX,
    top_k: int = 8,
) -> list[tuple[float, int]]:
    """(corr, period) pairs for every in-window local maximum of the
    column-gradient-profile autocorrelation, sorted strongest-first. The
    window's own global max (index 0 of the result, when non-empty) is
    always a local max of itself, so this is a strict superset of the
    single value `detect_vertical_period()` picks -- see its docstring for
    why that matters. `_select_vertical_period()` is the reason this exists
    as a list instead of a single value."""
    p = ap - ap.mean()
    ac = np.correlate(p, p, "full")[len(p) - 1 :]
    if ac[0] <= 0:
        return []
    ac = ac / ac[0]
    lags = np.arange(len(ac))
    win = (lags >= min_period) & (lags <= max_period)
    if not win.any():
        return []
    win_lags = lags[win]
    win_vals = ac[win]
    candidates: list[tuple[float, int]] = []
    for i in range(len(win_lags)):
        v = win_vals[i]
        left = win_vals[i - 1] if i - 1 >= 0 else -np.inf
        right = win_vals[i + 1] if i + 1 < len(win_vals) else -np.inf
        if v >= left and v >= right:
            candidates.append((float(v), int(win_lags[i])))
    candidates.sort(key=lambda t: -t[0])
    return candidates[:top_k]


def detect_vertical_period(img: np.ndarray, tissue: np.ndarray):
    ap = _column_gradient_profile(img, tissue)
    candidates = _autocorrelation_period_candidates(ap)
    if not candidates:
        return None, None, ap
    corr, period = candidates[0]
    return period, corr, ap


PERIOD_CANDIDATE_TOP_K = 8  # how many in-window autocorrelation local
# maxima _select_vertical_period() considers as alternative period
# hypotheses, beyond the single strongest one detect_vertical_period() uses.
PERIOD_CANDIDATE_MIN_PERIODIC = 2  # an alternative candidate period must
# explain at least this many independently-validated seams (coverage+step,
# see SEAM_COVERAGE_MIN_PERIODIC/SEAM_STEP_MIN below) before it's allowed to
# override the single-strongest-correlation default -- and even then only if
# that beats the default's own count. 2026-09-09 real-data validation
# ((12)/(17)/(35)/(42)/(56) batch): (12)'s strongest autocorrelation peak
# (period=260, corr=0.169) explains ZERO validated seams -- everything it
# finds comes from independent local-peak recovery instead, not the periodic
# comb itself. period=99 (the actual tile spacing this slice shares with 4
# of the other 5 in its batch) is only the THIRD-strongest peak (corr=0.156)
# but explains 3 validated seams, far more than any other candidate tried.
# Picking by raw correlation alone repeats "버그 I"'s mistake (an unrelated
# texture/noise peak outscoring the real tile period) one level up, at
# period selection instead of seam validation. The >=2 threshold (not >=1)
# is what a same-batch slice, (9), motivates: no candidate period there
# explains more than a single validated seam (several tie at 1) -- as
# consistent with coincidence as with a real tile grid, so switching on a
# single match would repeat the same mistake this exists to avoid, not fix
# it. (9)'s corrected.png also isn't reproducible by this (or any band of
# this) vertical-only algorithm regardless -- see STATUS.md/memory, this
# fix does not address that separate, still-open question.
PERIOD_SELECT_SCORE_BAND = 4  # fixed, small, and independent of both the
# band correct()/_autotune_band() end up using AND of which candidate
# period is being evaluated -- same scale-independence reasoning as
# AUTOTUNE_SCORE_BAND below (see _autotune_band()'s "Scoring (v3, ...)"
# docstring): letting this validation window drift with either would
# re-couple period selection to a parameter it's supposed to be independent
# of, the same class of bug documented there. Kept as its own constant
# (rather than reusing AUTOTUNE_SCORE_BAND) since the two features are only
# coincidentally the same value, not logically tied to each other.


def _select_vertical_period(
    img: np.ndarray,
    tissue: np.ndarray,
    ap: np.ndarray,
    width: int,
) -> "tuple[int | None, float | None]":
    """Like detect_vertical_period()'s single strongest-correlation choice,
    but considers up to PERIOD_CANDIDATE_TOP_K alternative in-window
    autocorrelation peaks and switches to one of them only if it explains
    at least PERIOD_CANDIDATE_MIN_PERIODIC independently-validated seams
    (more than the default explains) -- see that constant's comment for the
    real-data motivation and the (9) counter-example that sets the
    threshold at 2 rather than 1. Returns (period, corr), same shape as the
    first two values detect_vertical_period() returns."""
    candidates = _autocorrelation_period_candidates(ap, top_k=PERIOD_CANDIDATE_TOP_K)
    if not candidates:
        return None, None

    def periodic_yield(period: int) -> int:
        xs = seam_positions(width, period, ap) + 1
        xs = xs[(xs >= 1) & (xs < width)].tolist()
        if not xs:
            return 0
        accepted, _steps = _validated_seam_positions(
            img, tissue, ap, width, xs, band=PERIOD_SELECT_SCORE_BAND
        )
        return len([x for x in accepted if x in xs])

    best_corr, best_period = candidates[0]
    best_yield = periodic_yield(best_period)
    for corr, period in candidates[1:]:
        y = periodic_yield(period)
        if y >= PERIOD_CANDIDATE_MIN_PERIODIC and y > best_yield:
            best_period, best_corr, best_yield = period, corr, y
    return best_period, best_corr


def seam_positions(width: int, period: int, ap: np.ndarray) -> np.ndarray:
    best = (-1.0, 0)
    for phase in range(period):
        xs = np.arange(phase, width - 1, period)
        xs = xs[(xs >= 1) & (xs < width - 1)]
        score = float(ap[xs].sum()) if xs.size else -1.0
        if score > best[0]:
            best = (score, phase)
    phase = best[1]
    xs = np.arange(phase, width - 1, period)
    return xs[(xs >= 1) & (xs < width - 1)]


def estimate_step(img: np.ndarray, tissue: np.ndarray, x: int, band: int = DEFAULT_BAND) -> float:
    """x must already be "first column of the right tile" -- i.e. the value
    returned by seam_positions() shifted by +1 (see module docstring / the
    off-by-one note in seam_prototype.py). Passing the raw seam_positions()
    index here reproduces the original bug (residual barely improves)."""
    x0 = max(0, x - band)
    x1 = min(img.shape[1], x + band)
    left = img[:, x0:x]
    right = img[:, x:x1]
    lm = tissue[:, x0:x]
    rm = tissue[:, x:x1]
    lok = lm.sum(1) >= 1
    rok = rm.sum(1) >= 1
    ok = lok & rok
    if ok.sum() < 10:
        return 0.0
    lmean = np.where(lm, left, 0).sum(1) / np.maximum(lm.sum(1), 1)
    rmean = np.where(rm, right, 0).sum(1) / np.maximum(rm.sum(1), 1)
    return float(np.median((rmean - lmean)[ok]))


def residual(img8: np.ndarray) -> float | None:
    """Median |gradient| at the detected seam columns -- used only for
    diagnostics/logging, not for the correction itself."""
    img = img8.astype(np.float32)
    tissue = img8 > 8
    period, _corr, ap = detect_vertical_period(img, tissue)
    if not period:
        return None
    xs = seam_positions(img.shape[1], period, ap)
    if not xs.size:
        return None
    return float(np.median(ap[xs]))


# --- seam-candidate validation (2026-09-09 fix) -----------------------------
#
# Real-data bug (STATUS.md "Grid-estimated 모드 -- 섹션별로 seam 일부만 제거",
# user-provided 202607_M554_M579-01(3)/(4), 388x309, adjacent serial sections
# of the same brain with the same 2-seam mosaic): detect_vertical_period()
# picks ONE global period+phase for the whole image from a single
# autocorrelation peak. On (3) that peak correctly lands on period=100 (the
# true seam spacing) and both seams get corrected. On (4) a full-width
# texture feature unrelated to any seam happened to produce a *stronger*
# autocorrelation at lag=152 (0.126) than the true lag=100 does (0.050), so
# the periodic comb becomes [99, 251] -- the real seam at x=198 is dropped
# from consideration entirely (not scored as a small step, just never a
# candidate), while x=251 (not a real seam) gets a spurious correction.
#
# Fix: validate every candidate -- both the ones the periodic comb proposes
# and independently-found gradient-profile peaks -- against the one property
# that is actually true of a real full-width mosaic seam and not of a
# smaller anatomical feature: the brightness step holds up across nearly
# the entire vertical extent of the tissue, not just a fraction of it. This
# recovered the dropped x=198 seam AND rejected the spurious x=251 one on
# both real test images (validated via device_bash, numeric only -- no
# synthetic images, per the 2026-09-09 verification-scope policy).
#
# Deliberately NOT run as a standalone detector on images with no periodic
# signal at all (see correct()'s early return below) -- a full-height
# brightness difference is the same signature a genuine bilateral anatomical
# asymmetry (e.g. a section's own midline) could produce, and this module
# has no way to tell the two apart from pixel statistics alone. This is a
# recovery net for a periodic search that already found *something*, not a
# replacement for it. correct_known_geometry() (above) has no such
# ambiguity -- prefer it wherever a seamgrid sidecar is available.

SEAM_COVERAGE_MIN = 0.5  # fraction of the tissue's own vertical extent (not
# the raw image height -- a slice with tissue only in part of the canvas
# would otherwise unfairly fail this) that must have usable tissue on both
# sides of a candidate column. Applies to *independently-recovered* (peak-only,
# non-periodic) candidates. Real-data validation: true seams covered 74-88% of
# tissue extent on both (3) and (4); the non-seam texture peak that fooled
# period detection covered only 7-31%. 0.5 sits with margin between the two
# clusters.
SEAM_COVERAGE_MIN_PERIODIC = 0.35  # lower coverage floor for candidates that
SEAM_COVERAGE_MIN_RECOVERED = 0.7  # stricter floor for non-periodic peaks
# are *also* part of the detected periodic comb. A periodic-comb candidate
# already carries a structural prior a free-floating local-maxima peak
# doesn't (it sits exactly one autocorrelation-detected period from its
# validated neighbors), so it deserves a lower bar. 2026-09-09 second sweep
# ((12)/(17)/(35)/(42)/(56), all period=99 except (12)): the genuine seam
# nearest the tissue's left edge measured coverage 0.44-0.48 in every one of
# the 5 images (consistent step 14.7-21.0 gray levels, in line with its
# sibling seams) and was being wrongly dropped by the single 0.5 floor -- the
# same under-correction failure class "버그 I" was meant to fix, recurring via
# this threshold instead of the periodicity assumption. 0.35 still sits with
# margin under that cluster and above the 7-31%-coverage spurious-texture
# cluster from the original (3)/(4) validation, so it doesn't reopen that bug.
SEAM_STEP_MIN = 6.0  # gray levels. True DC seam steps measured 12-24 across
# all validated slices ((3)/(4)/(56)/(63)_dapi); background/texture columns
# that pass the coverage check measured near 0 (STATUS.md 2026-09-09 sweep).
# 6.0 sits with margin between them.
SEAM_CANDIDATE_MIN_SEP = 6  # px. De-duplicates the multi-column cluster one
# physical seam produces in the gradient profile (columns 98-101 all scored
# high for the single seam near x=99 in the (3)/(4) validation data) and
# merges a periodic-comb candidate with a coincident independently-found
# peak instead of double-counting it.
SEAM_CANDIDATE_MAX_PEAKS = 60  # caps the independent-peak search cost
# independent of image width -- see _seam_candidate_peaks() below.
SEAM_RECOVERED_EDGE_MARGIN = 20  # px. A *purely recovered* (non-periodic)
# candidate this close to an already-known periodic-comb position is treated
# as flanking noise from that same real seam, not an independent feature, and
# is rejected before even reaching the coverage/step check. 2026-09-09 second
# sweep: (42)'s recovered x=289 (9px from the validated periodic seam at 298)
# and x=514 (18px from the validated periodic seam at 496) were both spurious
# -- accepting them corrected the image in the wrong place and made its
# residual measurably worse (11.17 -> 16.48). 20px clears both with margin
# while leaving the original (4) recovery case (x=198, 53-99px from its
# periodic comb [99, 251]) untouched.


def _seam_candidate_peaks(ap: np.ndarray, *, min_sep: int = SEAM_CANDIDATE_MIN_SEP, max_peaks: int = SEAM_CANDIDATE_MAX_PEAKS) -> list[int]:
    """Independent (period-agnostic) seam candidates: the strongest columns
    in the gradient profile, greedily non-max-suppressed so one physical
    seam's cluster of neighboring high-gradient columns doesn't produce
    multiple candidates. Returns raw `ap` indices (caller applies the same
    +1 "first column of right tile" shift used for periodic candidates)."""
    order = np.argsort(ap)[::-1]
    picked: list[int] = []
    for idx in order:
        if len(picked) >= max_peaks:
            break
        if float(ap[idx]) <= 0:
            break
        if any(abs(int(idx) - p) < min_sep for p in picked):
            continue
        picked.append(int(idx))
    return picked


def _validate_seam_candidate(
    img: np.ndarray,
    tissue: np.ndarray,
    width: int,
    tissue_extent: int,
    x: int,
    band: int,
    *,
    coverage_min: float = SEAM_COVERAGE_MIN,
) -> float | None:
    """None if x doesn't look like a real full-height seam (see module note
    above); otherwise the estimated step (same value estimate_step() would
    return -- this only adds the coverage gate). `coverage_min` lets the
    caller apply a lower floor for periodic-comb candidates, which carry a
    structural prior a free-floating peak doesn't (see SEAM_COVERAGE_MIN_PERIODIC)."""
    if x < 1 or x >= width:
        return None
    x0 = max(0, x - band)
    x1 = min(width, x + band)
    lm = tissue[:, x0:x]
    rm = tissue[:, x:x1]
    ok = (lm.sum(1) >= 1) & (rm.sum(1) >= 1)
    coverage = float(ok.sum()) / tissue_extent if tissue_extent else 0.0
    if coverage < coverage_min:
        return None
    step = estimate_step(img, tissue, x, band)
    if abs(step) < SEAM_STEP_MIN:
        return None
    return step


def _validated_seam_positions(
    img: np.ndarray,
    tissue: np.ndarray,
    ap: np.ndarray,
    width: int,
    periodic_xs: list[int],
    *,
    band: int,
) -> tuple[list[int], dict[int, float]]:
    """Merge the periodic-comb candidates with independently-found gradient
    peaks, then validate every one of them via _validate_seam_candidate().
    Periodic-comb candidates get the lower SEAM_COVERAGE_MIN_PERIODIC floor;
    purely-recovered (non-periodic) candidates get the stricter
    SEAM_COVERAGE_MIN floor AND must clear SEAM_RECOVERED_EDGE_MARGIN from
    every periodic position first, since a recovered candidate that close to
    an already-known seam is almost always that same seam's flanking noise,
    not an independent feature (see SEAM_RECOVERED_EDGE_MARGIN).
    Returns (accepted_xs_sorted, steps_by_x)."""
    tissue_extent = max(1, int(tissue.any(axis=1).sum()))
    periodic_set = set(periodic_xs)
    peak_xs = sorted(set(idx + 1 for idx in _seam_candidate_peaks(ap)))
    merged = sorted(periodic_set | set(peak_xs))
    accepted: list[int] = []
    steps: dict[int, float] = {}
    for x in merged:
        if any(abs(x - a) < SEAM_CANDIDATE_MIN_SEP * 2 for a in accepted):
            continue  # same physical seam as one already accepted
        is_periodic = x in periodic_set
        if not is_periodic and any(abs(x - p) < SEAM_RECOVERED_EDGE_MARGIN for p in periodic_set):
            continue  # flanking noise off a known periodic seam, not an independent feature
        coverage_min = SEAM_COVERAGE_MIN_PERIODIC if is_periodic else SEAM_COVERAGE_MIN_RECOVERED
        step = _validate_seam_candidate(img, tissue, width, tissue_extent, x, band, coverage_min=coverage_min)
        if step is None:
            continue
        accepted.append(x)
        steps[x] = step
    return sorted(accepted), steps


def correct(img8: np.ndarray, *, band: int = DEFAULT_BAND, tissue_threshold: int = 8) -> tuple[np.ndarray, dict[str, Any]]:
    """Offset-only vertical seam correction. Returns (corrected_uint8, info)."""
    img = img8.astype(np.float32)
    width = img.shape[1]
    tissue = img8 > tissue_threshold
    ap = _column_gradient_profile(img, tissue)
    period, corr_val = _select_vertical_period(img, tissue, ap, width)
    if not period:
        return img8.copy(), {"period": None, "corr": None, "n_seams": 0}

    periodic_xs = seam_positions(width, period, ap) + 1  # off-by-one fix: k -> first column of right tile
    periodic_xs = periodic_xs[(periodic_xs >= 1) & (periodic_xs < width)].tolist()
    xs_sorted, steps = _validated_seam_positions(img, tissue, ap, width, periodic_xs, band=band)
    # Periodic candidates come from the dominant tile-spacing hypothesis and
    # must not be discarded merely because a weak seam fails the stricter
    # recovery validation. Re-estimate their step directly and keep them;
    # only non-periodic recovery candidates are subject to spacing filtering.
    for x in periodic_xs:
        if x not in steps:
            steps[x] = _soft_threshold(estimate_step(img, tissue, x, band=band))
    xs_sorted = sorted(set(xs_sorted).union(periodic_xs))
    # Recovery can surface strong local structures close to a real periodic
    # seam. Keep periodic candidates, but reject recovered candidates that
    # are too close to an already accepted seam to represent a tile interval.
    if period and xs_sorted:
        filtered = []
        for x in xs_sorted:
            if x in periodic_xs or not filtered or (x - filtered[-1]) >= max(8, int(period * 0.5)):
                filtered.append(x)
        xs_sorted = filtered
        steps = {x: steps[x] for x in xs_sorted}
    if not xs_sorted:
        return img8.copy(), {"period": period, "corr": round(corr_val, 3), "n_seams": 0}

    offset = np.zeros(width, dtype=np.float32)
    cur = 0.0
    xi = 0
    for x in range(width):
        while xi < len(xs_sorted) and x >= xs_sorted[xi]:
            cur += steps[xs_sorted[xi]]
            xi += 1
        offset[x] = cur
    offset -= np.median(offset)  # keep overall brightness anatomy-safe

    out = img - offset[None, :]
    out = np.clip(out, 0, 255)
    out[~tissue] = img8[~tissue]  # leave background untouched
    out_u8 = out.astype(np.uint8)
    return out_u8, {
        "period": period,
        "corr": round(corr_val, 3),
        "n_seams": len(xs_sorted),
        "n_seams_periodic": len([x for x in xs_sorted if x in periodic_xs]),
        "n_seams_recovered": len([x for x in xs_sorted if x not in periodic_xs]),
        "seam_positions": [int(x) for x in xs_sorted],
        "steps": [round(v, 1) for v in steps.values()],
    }


AUTOTUNE_CANDIDATES = (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 28, 32)
AUTOTUNE_SCORE_BAND = 4  # fixed scoring window (see _autotune_band's v3 docstring note)


def _autotune_band(
    img8: np.ndarray,
    *,
    tissue_threshold: int = 8,
    candidates: "list[int] | None" = None,
) -> "tuple[int | None, float | None, list[dict[str, Any]]]":
    """Grid-search over candidate `band` values: correct the image with each
    and score it by the median |gradient| remaining at the seam columns
    detected on the *original*, uncorrected image (same diagnostic quantity
    `residual()` reports, but evaluated at a fixed reference instead of
    residual()'s own fresh re-detection -- see the comment at the scoring
    loop below for why that fix matters). Returns (best_band, best_residual,
    trace); trace is every {"band", "residual"} tried, for PREVIEW_JSON/logging.

    This mirrors BaSiC's "Auto-tune smoothness" *pattern* (opt-in search
    that minimizes a metric instead of a user-picked constant) but not its
    implementation -- BaSiC delegates to BaSiCPy's own `basic.autotune()`
    library method; there's no equivalent library for seam-band selection,
    so this is a plain bounded grid search instead.

    Bound: `band` must stay well under period/2, or the averaging windows
    on either side of adjacent seams start overlapping each other.

    Scoring (v3, 2026-09-08): the *candidate* band (what `correct()` uses to
    estimate and remove the step) and the *scoring* band (what measures how
    much step is left afterward) are deliberately different things. Scoring
    uses `estimate_step()` -- the same band-averaged estimator that performs
    the correction itself -- at a small, FIXED window (`AUTOTUNE_SCORE_BAND`),
    independent of both the candidate band and of `max_band`, regardless of
    which candidate produced the correction being scored.

    v1 scored with a single-column gradient (`ap`, from
    `_column_gradient_profile()`), which is noisy enough on real (textured,
    not just per-pixel-random) images that it kept preferring bands smaller
    than what visibly removed the seam -- reported twice by the user even
    after the first position-stability fix (see memory
    `masonjar-basic-seam-issue` bug E, stages 1-2).

    v2 (2026-09-06) fixed that by reusing `estimate_step()` at the widest
    safe window (`max_band`) for scoring. That over-corrected in the other
    direction: on real photos with a smooth large-scale tissue/brightness
    trend (unrelated to the tile grid -- cortex depth, staining gradient,
    etc.), a wide averaging window on either side of the seam partially
    samples that trend, biasing the step estimate. Because scoring reused
    the *same* wide-window estimator, and the bias grows with window width,
    large candidate bands (whose own internal step estimate is band-limited
    the same way) ended up scoring artificially well -- auto-tune kept
    picking band=32 even when a direct measurement showed band~16-24 left
    the least residual seam (S1, real DAPI photos, 2026-09-08). Reproduced
    with a synthetic image adding a smooth large-scale trend independent of
    the tile period (on top of the per-tile step + per-pixel + spatially
    correlated "cell body" texture noise already used for bug E's tests):
    v2 scoring overshot to band>=24 in 22/30 trials and scored noticeably
    worse than manual band=4 in 26/30; a small fixed scoring window did not
    overshoot in any trial. `AUTOTUNE_SCORE_BAND` was picked by sweeping
    1/2/4/8 against that same synthetic suite -- 4 had zero overshoots and
    the lowest mean/median residual at the picked band, matching
    `DEFAULT_BAND` and the user's own manual-band=4 empirical baseline.
    Decoupling the scoring window from `max_band` this way keeps it far
    enough from any candidate band to avoid the self-consistency bias v2 had
    (scoring window and correction window drifting close together as the
    candidate grows), while still small enough to avoid sampling into a
    large-scale trend unrelated to the seam itself.
    """
    img = img8.astype(np.float32)
    tissue = img8 > tissue_threshold
    width = img8.shape[1]
    ap = _column_gradient_profile(img, tissue)
    # Same period choice correct() itself will make (_select_vertical_period(),
    # not the plain single-peak detect_vertical_period()) -- otherwise this
    # function could score residual at one period's seam columns while the
    # correct() calls in the loop below are actually correcting a different
    # period's, making the whole search meaningless. See
    # PERIOD_CANDIDATE_MIN_PERIODIC's comment for why this matters in
    # practice, not just in theory.
    period, _corr = _select_vertical_period(img, tissue, ap, width)
    if not period:
        return None, None, []
    periodic_xs = seam_positions(width, period, ap) + 1  # off-by-one fix, see correct()
    periodic_xs = periodic_xs[(periodic_xs >= 1) & (periodic_xs < width)].tolist()
    if not periodic_xs:
        return None, None, []
    max_band = max(1, min(32, period // 2 - 1))
    if candidates is None:
        candidates = sorted({c for c in AUTOTUNE_CANDIDATES if 1 <= c <= max_band})
        if not candidates:
            candidates = [1]
    # Fixed, small, and deliberately independent of max_band/candidate band --
    # see the "Scoring (v3, ...)" docstring section above for why a wide
    # window here reintroduces a large-band bias.
    score_band = min(AUTOTUNE_SCORE_BAND, max_band)
    # 2026-09-09: reference positions now go through the same
    # _validated_seam_positions() gate correct() itself uses (see that
    # function's module note), scored at the fixed score_band -- so a seam
    # correct() recovers isn't missing from what autotune scores, and a
    # periodic-comb candidate correct() itself would reject as spurious
    # isn't scored here either. Previously this used seam_positions()'s raw,
    # unvalidated output directly.
    xs_shifted, _ref_steps = _validated_seam_positions(img, tissue, ap, width, periodic_xs, band=score_band)
    if not xs_shifted:
        return None, None, []
    trace: list[dict[str, Any]] = []
    best_band, best_res = None, None
    for b in candidates:
        corrected, _info = correct(img8, band=b, tissue_threshold=tissue_threshold)
        img_c = corrected.astype(np.float32)
        remaining = [
            abs(estimate_step(img_c, tissue, int(x), band=score_band))
            for x in xs_shifted
        ]
        r = float(np.median(remaining))
        trace.append({"band": b, "residual": round(r, 4)})
        if best_res is None or r < best_res:
            best_band, best_res = b, r
    return best_band, best_res, trace


# --- known-geometry correction (sidecar-based) ---------------------------
#
# STATUS.md "[S1->S2 설계 핸드오프 -- 가로 seam 지원 (known-geometry,
# 2026-09-04, 사용자 승인)]": image-only autocorrelation (correct() above)
# cannot reliably find WEAK or horizontal seams on real brain tissue (weak
# horizontal seams are indistinguishable from laminar anatomy; lowering the
# detection threshold false-positives on that anatomy instead). When the
# tile grid is already known -- from CZI mosaic metadata recorded at import
# time by czi_extract.py's _write_seam_grid_sidecar() -- correction can skip
# detection entirely and use the exact boundary coordinates, for both axes.
#
# This is a *replacement* correction path, used instead of correct() /
# _autotune_band() when a sidecar is found; it is not a band search (there
# is nothing to search for -- the seam positions are already known), so
# --autotune is a no-op here (see run_preview()/_process_channel() below,
# which log and skip it rather than silently ignoring the checkbox).

SEAMGRID_SOFT_THRESHOLD = 1.2  # gray levels shrunk from each known-boundary
# step estimate (floored at 0) -- S1's sandbox validation (STATUS.md handoff
# section) found this cuts steady-state RMSE on clean data from ~2.4 to
# ~1.7; a residual floor of ~1.7-2.4 remains unavoidable (boundary-adjacent
# anatomy + cumulative-sum error) and is not something this constant can
# remove entirely.

SEAMGRID_REFINE_MARGIN = 274  # px search window (each side) for snapping a
# recorded boundary to the column/row of locally maximum |gradient| -- see
# _refine_boundary(). Recorded boundaries are a tile's own left/top edge,
# which can sit a few px inside the true visible seam because of mosaic
# overlap; S1's real-CZI probe (STATUS.md, 2026-09-05 실 CZI 확인) measured
# ~274px of overlap for the validated dataset, so that is the default here.
# This is a source-resolution allowance, not a preview-pixel radius.
# _geometry_refine_margin scales it and caps it at 5% of a tile interval;
# missing source dimensions use a conservative four-pixel allowance.


def _slice_id_for(path: Path) -> str:
    """slice_id for a given image path, using the same convention as the
    rest of this pipeline (basic_correct.py's _slice_stem(), which this
    module's own _slice_stem() above already mirrors) -- this is also what
    czi_extract.py's _write_seam_grid_sidecar() names the sidecar JSON
    after, so no separate derivation is needed here."""
    return _slice_stem(path)


def _orient_seam_grid(grid: dict, ops: list[str]) -> dict:
    """Transform boundary fractions exactly like clockwise image geometry ops."""
    import copy
    result = copy.deepcopy(grid)
    width, height = result['scene_extent_px']
    vertical = list(result['vertical']['boundaries_frac'])
    horizontal = list(result['horizontal']['boundaries_frac'])
    for op in ops:
        if op == 'rot90':
            vertical, horizontal = [1-f for f in horizontal], vertical
            width, height = height, width
        elif op == 'flipX':
            vertical = [1-f for f in vertical]
        elif op == 'flipY':
            horizontal = [1-f for f in horizontal]
        else:
            raise ValueError(f'Unsupported geometry operation: {op}')
    result['scene_extent_px'] = [width, height]
    for axis, fractions, size in [('vertical',vertical,width),('horizontal',horizontal,height)]:
        fractions = sorted(fractions)
        result[axis] = {'boundaries_frac':fractions,'boundaries_px':[round(f*size) for f in fractions]}
    result['applied_geometry_ops'] = list(ops)
    return result


def load_seam_grid(meta_dir: "Path | None", slice_id: str, image_path: "Path | None" = None) -> "dict | None":
    """Read <meta_dir>/seamgrid/<slice_id>.json if present and well-formed
    (schema: STATUS.md known-geometry handoff, written by czi_extract.py's
    _write_seam_grid_sidecar()). Returns None -- never raises -- on any
    absence/parse/shape problem: a missing or invalid sidecar is the
    expected, normal case for any image that wasn't imported from a CZI
    mosaic (or predates this feature), and callers must fall back to the
    existing autocorrelation path silently, exactly as they already do when
    correct()'s own autocorrelation finds no period."""
    if meta_dir is None:
        return None
    try:
        sidecar = Path(meta_dir) / "seamgrid" / f"{slice_id}.json"
        if not sidecar.is_file():
            return None
        with open(sidecar, encoding="utf-8") as f:
            grid = json.load(f)
        if not isinstance(grid, dict):
            return None
        extent = grid.get("scene_extent_px")
        if not (
            isinstance(extent, list)
            and len(extent) == 2
            and all(isinstance(v, (int, float)) and v > 0 for v in extent)
        ):
            return None
        for axis in ("vertical", "horizontal"):
            a = grid.get(axis)
            if not isinstance(a, dict) or not isinstance(a.get("boundaries_frac"), list):
                return None
        # Match the exact file, not merely its slice: channels have separate
        # history records. Always transform a fresh sidecar, never write it back.
        history = Path(meta_dir) / 'geometry_history.jsonl'
        if image_path is not None and history.is_file():
            try:
                relative = image_path.resolve().relative_to(Path(meta_dir).resolve().parent).as_posix().lower()
            except ValueError:
                relative = None
            ops = []
            if relative is not None:
                with history.open(encoding='utf-8') as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if (event.get('kind') == 'file' and event.get('ok') is True
                                and str(event.get('file','')).replace('\\', '/').lower() == relative):
                            ops.extend(event.get('ops') or [])
            if ops:
                grid = _orient_seam_grid(grid, ops)
                _log(f'seam_geometry_transform image={image_path.name} ops={ops}')
        return grid
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _log(f"seamgrid_unreadable slice_id={slice_id} err={exc!r}")
        return None


def _boundaries_px_for_image(fracs: list, own_dim: int) -> list[int]:
    """boundaries_frac was recorded against scene_extent_px at import time;
    the image seam_correct.py is actually asked to correct may be a
    different resolution (a downscaled export), so boundaries are always
    re-derived from the recorded FRACTION against this image's own
    dimension, never against scene_extent_px directly (interface contract,
    STATUS.md: "좌표는 scene extent 기준, 다운스케일 대비 분수 좌표 동봉")."""
    out = []
    for f in fracs:
        try:
            x = int(round(float(f) * own_dim))
        except (TypeError, ValueError):
            continue
        if 1 <= x < own_dim:
            out.append(x)
    return sorted(set(out))


def _tile_intervals(boundaries: list[int], length: int) -> list[tuple[int, int]]:
    """Build contiguous [start, end) tile intervals from seam boundaries."""
    cuts = [0] + sorted(set(int(b) for b in boundaries if 0 < int(b) < length)) + [int(length)]
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]


def _tile_brightness_stats(img: np.ndarray, tissue: np.ndarray, intervals: list[tuple[int, int]]) -> list[dict]:
    """Return tissue median and support for each tile interval."""
    stats = []
    for start, end in intervals:
        vals = img[:, start:end][tissue[:, start:end]]
        stats.append({
            "start": int(start), "end": int(end),
            "median": round(float(np.median(vals)), 3) if vals.size else None,
            "support": round(float(vals.size / max(1, (end - start) * img.shape[0])), 3),
        })
    return stats


def _independent_tile_offsets(stats: list[dict]) -> list[float]:
    """Compute relative tile offsets using adjacent valid tile medians."""
    offsets = [0.0]
    for i in range(1, len(stats)):
        prev, cur = stats[i - 1].get("median"), stats[i].get("median")
        offsets.append(offsets[-1] if prev is None or cur is None else offsets[-1] + float(prev - cur))
    if offsets:
        med = float(np.median(offsets))
        offsets = [round(v - med, 3) for v in offsets]
    return offsets


def _apply_tile_offsets(img: np.ndarray, intervals: list[tuple[int, int]], offsets: list[float], ramp: int = 0) -> np.ndarray:
    """Apply per-tile offsets with optional linear transitions at boundaries."""
    out = img.astype(np.float32, copy=True)
    for i, (start, end) in enumerate(intervals):
        off = float(offsets[i]) if i < len(offsets) else 0.0
        out[:, start:end] += off
    if ramp > 0:
        for i in range(1, len(intervals)):
            b = intervals[i][0]
            left = float(offsets[i - 1]); right = float(offsets[i])
            lo, hi = max(0, b - ramp), min(out.shape[1], b + ramp)
            for x in range(lo, hi):
                t = (x - (b - ramp)) / float(2 * ramp)
                out[:, x] = img[:, x] + left * (1.0 - t) + right * t
    return np.clip(out, 0, 255).astype(np.uint8)


def _refine_boundary(profile: np.ndarray, x: int, margin: int, lo: int, hi: int) -> int:
    """Snap a recorded boundary to the column (or, on a transposed axis,
    row) of maximum |gradient| within +/-margin of it, without ever
    stepping outside [lo, hi) -- the caller passes the midpoints to the
    previous/next known boundary there, so this can never cross into a
    neighboring seam's own territory regardless of how large `margin` is
    (see SEAMGRID_REFINE_MARGIN's docstring note)."""
    # profile[i] measures the boundary whose first right-hand pixel is i+1.
    x0 = max(1, lo, x - margin) - 1
    x1 = min(len(profile) + 1, hi, x + margin + 1) - 1
    if x1 <= x0:
        return x
    window = profile[x0:x1]
    if window.size == 0:
        return x
    peak = float(np.max(window))
    if peak <= 0:
        return x
    candidates = np.flatnonzero(window == peak) + x0 + 1
    return int(candidates[np.argmin(np.abs(candidates - x))])


def _geometry_refine_margin(bounds: list[int], length: int, source_length=None) -> int:
    """Scale the source overlap allowance, capped at 5% of a tile interval."""
    cuts = [0] + sorted(set(bounds)) + [length]
    cap = max(1, int(min(np.diff(cuts)) * 0.05))
    source_valid = isinstance(source_length, (int, float)) and source_length > 0
    scaled = max(1, round(SEAMGRID_REFINE_MARGIN * length / source_length)) if source_valid else 4
    return min(cap, scaled)


def _soft_threshold(step: float, shrink: float = SEAMGRID_SOFT_THRESHOLD) -> float:
    """Shrink a step estimate toward zero by `shrink` gray levels, floored
    at 0 -- see SEAMGRID_SOFT_THRESHOLD's docstring for the validation
    numbers behind the default."""
    if step > 0:
        return max(0.0, step - shrink)
    if step < 0:
        return min(0.0, step + shrink)
    return 0.0


def _axis_offset(
    img: np.ndarray,
    tissue: np.ndarray,
    boundaries: list[int],
    length: int,
    *,
    band: int,
    refine: bool,
    return_details: bool = False,
    refine_margin: int = 4,
    ramp_width: int = 0,
) -> Any:
    """Cumulative per-column offset from a list of KNOWN boundary positions
    -- the known-geometry counterpart of correct()'s xs/steps/offset block,
    but using boundaries read from a seamgrid sidecar instead of ones found
    by autocorrelation. Each step uses only tissue beside that boundary.
    `img`/`tissue` here are already oriented so "column" is the axis being
    corrected -- correct_known_geometry() below transposes both once for
    the horizontal pass (module docstring / STATUS.md handoff: "세로/가로
    동일 (transpose)"), so this one implementation serves both axes."""
    if not boundaries:
        offset = np.zeros(length, dtype=np.float32)
        return (offset, {}) if return_details else offset
    profile = _column_gradient_profile(img, tissue) if refine else None
    bx = sorted(set(int(b) for b in boundaries if 1 <= int(b) < length))
    refined = []
    for i, x in enumerate(bx):
        if refine and profile is not None:
            lo = 0 if i == 0 else (bx[i - 1] + x) // 2
            hi = length if i == len(bx) - 1 else (x + bx[i + 1]) // 2
            x = _refine_boundary(profile, x, refine_margin, lo, hi)
        refined.append(x)
    # Geometry supplies the candidate boundaries; brightness correction uses
    # the same tissue-aware step estimator as grid-estimated mode. Preserve
    # the measured step here so genuine tile offsets are not attenuated.
    steps = {
        x: float(estimate_step(img, tissue, x, band=band))
        for x in sorted(set(refined))
    }
    offset = np.zeros(length, dtype=np.float32)
    cur = 0.0
    xs_sorted = sorted(steps)
    xi = 0
    for x in range(length):
        while xi < len(xs_sorted) and x >= xs_sorted[xi]:
            cur += steps[xs_sorted[xi]]
            xi += 1
        offset[x] = cur
    # Abrupt input steps require abrupt inverse offsets. Blending the offset
    # leaves part of the original seam intact, so ramps are opt-in only.
    ramp = max(0, int(ramp_width))
    for bx in xs_sorted:
        step = steps[bx]
        lo, hi = max(0, bx - ramp), min(length, bx + ramp)
        for px in range(lo, hi):
            t = (px - (bx - ramp)) / float(2 * ramp)
            t = max(0.0, min(1.0, t))
            if px < bx:
                offset[px] += step * t
            else:
                offset[px] -= step * (1.0 - t)
    return (offset, steps) if return_details else offset


def correct_known_geometry(
    img8: np.ndarray,
    grid: dict,
    *,
    band: int = DEFAULT_BAND,
    tissue_threshold: int = 8,
    refine: bool = True,
    ramp_width: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Known-geometry vertical+horizontal seam correction using a seamgrid
    sidecar's recorded tile boundaries instead of autocorrelation-detected
    ones (STATUS.md known-geometry handoff, user-approved 2026-09-04). Per
    axis: step = median of paired tissue-row mean differences within `band`
    pixels of the refined boundary -> accumulated inverse step (optional ramp). Both
    axes' offsets are then subtracted together in one pass -- the design
    note's "orthogonal, safe to combine" claim (S1 sandbox: pixel-identical
    to applying them sequentially) means there's no need to correct one
    axis, re-detect, then correct the other. An axis with no usable
    boundaries in the sidecar is simply left uncorrected (offset stays 0);
    the other axis, if present, is still applied. Returns
    (corrected_uint8, info) with the same info keys correct() uses
    (period/corr/n_seams/steps -- period and corr are always None here,
    since there is no autocorrelation step) plus n_seams_vertical /
    n_seams_horizontal for known-geometry-aware callers."""
    img = img8.astype(np.float32)
    h, w = img8.shape[:2]
    tissue = img8 > tissue_threshold

    v_frac = ((grid.get("vertical") or {}).get("boundaries_frac")) or []
    h_frac = ((grid.get("horizontal") or {}).get("boundaries_frac")) or []
    v_bounds = _boundaries_px_for_image(v_frac, w)
    h_bounds = _boundaries_px_for_image(h_frac, h)
    v_tiles = _tile_intervals(v_bounds, w)
    h_tiles = _tile_intervals(h_bounds, h)

    extent = grid.get('scene_extent_px') or [None, None]
    v_margin = _geometry_refine_margin(v_bounds, w, extent[0])
    h_margin = _geometry_refine_margin(h_bounds, h, extent[1])
    recorded_v, recorded_h = list(v_bounds), list(h_bounds)
    col_off, v_steps = _axis_offset(img, tissue, v_bounds, w, band=band, refine=refine, return_details=True, refine_margin=v_margin, ramp_width=ramp_width)
    row_off, h_steps = _axis_offset(img.T, tissue.T, h_bounds, h, band=band, refine=refine, return_details=True, refine_margin=h_margin, ramp_width=ramp_width)
    v_bounds, h_bounds = sorted(v_steps), sorted(h_steps)
    v_tiles = _tile_intervals(v_bounds, w)
    h_tiles = _tile_intervals(h_bounds, h)

    if v_bounds:
        col_off = col_off - np.median(col_off)  # keep overall brightness anatomy-safe
    if h_bounds:
        row_off = row_off - np.median(row_off)

    def step_diagnostics(steps):
        return [{"x": int(b), "raw": round(value, 3),
                 "applied": round(value, 3)} for b, value in steps.items()]

    def offset_jumps(offset, bounds):
        return [
            {"x": int(b), "jump": round(float(offset[min(len(offset)-1, b)] - offset[max(0, b-1)]), 3)}
            for b in bounds
        ]

    def confidence(axis_img, axis_tissue, bounds):
        profile = _column_gradient_profile(axis_img, axis_tissue)
        vals = []
        for b in bounds:
            lo, hi = max(0, b - SEAMGRID_REFINE_MARGIN), min(len(profile), b + SEAMGRID_REFINE_MARGIN + 1)
            peak = float(np.max(profile[lo:hi])) if hi > lo else 0.0
            support = float(np.mean(axis_tissue[:, max(0, b-band):min(axis_tissue.shape[1], b+band)]))
            vals.append({"x": int(b), "gradient": round(peak, 3), "tissue_support": round(support, 3)})
        return vals

    # Each boundary contributes only its local paired-row brightness step.
    # Combine axes in float before clipping once; tile medians are diagnostic only.
    v_ind = [-round(float(col_off[(a + b - 1) // 2]), 3) for a, b in v_tiles]
    h_ind = [-round(float(row_off[(a + b - 1) // 2]), 3) for a, b in h_tiles]
    out_u8 = np.clip(img - col_off[None, :] - row_off[:, None], 0, 255).astype(np.uint8)
    out_u8[~tissue] = img8[~tissue]  # leave background untouched
    seam_residuals = _seam_diagnostics(img8, out_u8, v_bounds, band=band)
    for item in seam_residuals:
        item["improved"] = abs(item["after"]) < abs(item["before"])
    return out_u8, {
        "period": None,
        "offset_method": "local_seam_steps",
        "measurement_band": int(band),
        "ramp_half_width": max(0, int(ramp_width)),
        "refine_margin_vertical": v_margin if refine else 0,
        "refine_margin_horizontal": h_margin if refine else 0,
        "recorded_positions_vertical": recorded_v,
        "recorded_positions_horizontal": recorded_h,
        "corr": None,
        "n_seams": len(v_bounds) + len(h_bounds),
        "n_seams_vertical": len(v_bounds),
        "n_seams_horizontal": len(h_bounds),
        "n_tiles_vertical": len(v_tiles),
        "n_tiles_horizontal": len(h_tiles),
        "tile_brightness_vertical": _tile_brightness_stats(img8, tissue, v_tiles),
        "tile_brightness_horizontal": _tile_brightness_stats(img8.T, tissue.T, h_tiles),
        "independent_offsets_vertical": v_ind,
        "independent_offsets_horizontal": h_ind,
        "seam_residuals": seam_residuals,
        "seam_positions_vertical": [int(x) for x in v_bounds],
        "seam_positions_horizontal": [int(y) for y in h_bounds],
        "seam_confidence_vertical": confidence(img, tissue, v_bounds),
        "seam_confidence_horizontal": confidence(img.T, tissue.T, h_bounds),
        "offset_jumps_vertical": offset_jumps(col_off, v_bounds),
        "offset_jumps_horizontal": offset_jumps(row_off, h_bounds),
        "step_diagnostics_vertical": step_diagnostics(v_steps),
        "step_diagnostics_horizontal": step_diagnostics(h_steps),
        "steps": None,
    }


def _load_any(path: Path) -> np.ndarray:
    return load_grayscale_uint8(path)


def _seam_diagnostics(before: np.ndarray, after: np.ndarray, positions: list[int], band: int = 4) -> list[dict]:
    """Measure left/right mean step at each reported vertical seam."""
    out = []
    h, w = before.shape[:2]
    for x in positions or []:
        x = int(x)
        lo = max(0, x - int(band)); hi = min(w, x + int(band))
        if x <= lo or x >= hi:
            continue
        def step(img):
            left_arr = img[:, max(0, x-int(band)):x]
            right_arr = img[:, x:min(w, x+int(band))]
            left = float(np.mean(left_arr[left_arr > 8])) if np.any(left_arr > 8) else 0.0
            right = float(np.mean(right_arr[right_arr > 8])) if np.any(right_arr > 8) else 0.0
            return round(right - left, 3)
        mask = before > 8
        support = int(((mask[:, lo:x].sum(1) > 0) & (mask[:, x:hi].sum(1) > 0)).sum())
        out.append({"x": x, "before": step(before), "after": step(after),
                    "paired_before": round(estimate_step(before, mask, x, band), 3) if support >= 10 else None,
                    "paired_after": round(estimate_step(after, mask, x, band), 3) if support >= 10 else None,
                    "valid_rows": support})
    return out


def correct_file_to(
    dest: Path,
    src: Path,
    *,
    band: int = DEFAULT_BAND,
    meta_dir: Path | None = None,
    seam_mode: str = "auto",
    autotune: bool = False,
    seamgrid_slice_id: str | None = None,
    geometry_image_path: Path | None = None,
) -> dict[str, Any]:
    """Load *src*, seam-correct it, and atomically write the result to
    *dest* as a PNG. Public convenience entry point for other in-process
    callers that don't want to reach into this module's private
    _load_any()/_atomic_write_png() directly -- added 2026-09-06 for
    py/adjust.py's Viewer/Editor "Seam corrected" live-preview
    (_compute_seam_live()): that toggle runs in-process in the same
    masonjar Python env as this module, so it can call straight into
    correct() without spawning a subprocess. `seam_mode="auto"` matches the
    batch/preview behavior: use an import-created seamgrid sidecar when it is
    available, otherwise use Grid-estimated correction. ``seamgrid_slice_id``
    and ``geometry_image_path`` let a low-resolution display preview retain
    the identity and orientation history of its source image. Returns diagnostics
    including the resolved mode."""
    img8 = _load_any(Path(src))
    requested_mode = str(seam_mode or "auto")
    grid_slice_id = seamgrid_slice_id or _slice_id_for(Path(src))
    grid_geometry_path = Path(geometry_image_path) if geometry_image_path is not None else Path(src)
    grid = load_seam_grid(meta_dir, grid_slice_id, grid_geometry_path)
    if requested_mode == "grid_estimated":
        grid = None
    if requested_mode == "known_geometry" and grid is None:
        raise RuntimeError("Known-geometry unavailable: seam grid sidecar missing")
    band_used = band
    if grid is not None:
        corrected, info = correct_known_geometry(img8, grid, band=band_used)
        resolved_mode = "known_geometry"
    else:
        if autotune:
            tuned_band, _residual, _trace = _autotune_band(img8)
            if tuned_band is not None:
                band_used = tuned_band
        corrected, info = correct(img8, band=band_used)
        resolved_mode = "grid_estimated"
    info = {**info, "mode": resolved_mode, "band": band_used}
    _atomic_write_png(Path(dest), corrected)
    return info


def _write_like(src: Path, dest: Path, arr: np.ndarray) -> None:
    if src.suffix.lower() == ".png":
        _atomic_write_png(dest, arr)
    else:
        _atomic_write_tiff(dest, arr)


def _resolve_wanted_stems(slice_list: Any) -> set[str]:
    """`slice_list` may be an inline list of slice stems, or a path to the
    JSON file file_index.js's writeRunSliceList() writes for the primary
    channel ({"slice_ids": [...]}). The primary channel's slice_list has
    always been a path string (see writeRunConfig() in
    preprocess_wizard.js), but this used to only handle the inline-list case
    (`isinstance(slice_list, list)`), so a project-scoped slice subset was
    silently ignored -- seam batch processed every file in source_abs
    regardless (found + fixed 2026-09-05, alongside the extra_channels
    feature below, which needed this same resolution and exposed the bug).
    extra_channels entries pass an inline list directly, which this also
    handles."""
    if not slice_list:
        return set()
    if isinstance(slice_list, list):
        return {_normalize_slice_key(str(s)) for s in slice_list if str(s).strip()}
    path_str = str(slice_list).strip()
    if not path_str or not os.path.isfile(path_str):
        return set()
    try:
        with open(path_str, encoding="utf-8") as f:
            raw = f.read().strip()
        data = json.loads(raw) if raw[:1] in ("[", "{") else None
        if isinstance(data, dict):
            filename_stems = data.get("filename_stems", [])
            if isinstance(filename_stems, list) and filename_stems:
                return {_normalize_slice_key(str(s)) for s in filename_stems if str(s).strip()}
            data = data.get("slice_ids", [])
        if isinstance(data, list):
            return {_normalize_slice_key(str(s)) for s in data if str(s).strip()}
    except (OSError, ValueError, TypeError) as exc:
        _log(f"seam_slice_list_unreadable path={path_str} err={exc!r}")
    return set()


def _slice_matches_wanted(stem: str, wanted: set[str]) -> bool:
    """Allow project slice IDs (e.g. 202607) to select dotted file stems."""
    key = _normalize_slice_key(stem)
    return any(key == item or key.startswith(item + ".") for item in wanted)


def _process_channel(
    input_dir: Path,
    output_dir: Path,
    *,
    band: int,
    autotune: bool,
    wanted: set[str],
    meta_dir: "Path | None" = None,
    label: str = "",
    seam_mode: str = "auto",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Seam-correct every (optionally slice_list-filtered) image in
    input_dir, writing to output_dir. Shared by the primary channel and any
    extra_channels entries (2026-09-05) so both go through identical
    per-file logic -- see run_batch(). Per file, prefers known-geometry
    (see correct_known_geometry() above) when a seamgrid sidecar exists for
    that slice_id under meta_dir, falling back to the existing
    autocorrelation path (with per-file --autotune, if requested) when it
    doesn't -- exactly the same fallback rule run_preview() uses."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = _list_image_files_any(input_dir)
    if wanted:
        files = [p for p in files if _slice_matches_wanted(_slice_stem(p), wanted)]
    written: list[str] = []
    stats: list[dict[str, Any]] = []
    requested_mode = seam_mode
    for fpath in files:
        _log(f"seam_processing {label}{fpath.name}")
        try:
            img8 = _load_any(fpath)
            band_used = band
            seam_mode = requested_mode
            grid = load_seam_grid(meta_dir, _slice_id_for(fpath), fpath)
            if seam_mode == "grid_estimated":
                grid = None
            if seam_mode == "known_geometry" and grid is None:
                raise RuntimeError("Known-geometry unavailable: seam grid sidecar missing")
            if grid is not None:
                out_u8, info = correct_known_geometry(img8, grid, band=band_used)
                seam_mode = "known_geometry"
                if autotune:
                    _log(
                        f"seam_known_geometry_autotune_skipped {label}{fpath.name} "
                        "band search is not applicable once tile geometry is known"
                    )
            else:
                seam_mode = "grid_estimated"
                if autotune:
                    tuned_band, tuned_residual, _trace = _autotune_band(img8)
                    if tuned_band is not None:
                        band_used = tuned_band
                    _log(
                        f"seam_autotune {label}{fpath.name} band={tuned_band} "
                        f"residual={tuned_residual}"
                    )
                out_u8, info = correct(img8, band=band_used)
            dest = output_dir / fpath.name
            _write_like(fpath, dest, out_u8)
            written.append(fpath.name)
            stats.append({"name": fpath.name, "band": band_used, "mode": seam_mode, **info})
            _log(f"seam_correction_details image={fpath.name} details={json.dumps(info)}")
            _log(
                f"seam_done {label}{fpath.name} mode={seam_mode} band={band_used} "
                f"period={info.get('period')} corr={info.get('corr')} "
                f"n_seams={info.get('n_seams')}"
            )
        except Exception as exc:  # noqa: BLE001
            _log_exception(f"batch:{label}{fpath.name}", exc)
    return written, stats


# --- preview -----------------------------------------------------------


def run_preview(args) -> int:
    path = Path(args.image.strip())
    if not path.is_file():
        emit_preview_json({"ok": False, "error": "image not found"})
        return 1
    band = int(args.band) if args.band else DEFAULT_BAND
    autotune = bool(getattr(args, "autotune", False))
    seam_mode = str(getattr(args, "seam_mode", "auto") or "auto")
    height = getattr(args, "height", None)
    if height is None:
        height = getattr(args, "h", 512)
    x, y, w, h = int(args.x), int(args.y), int(args.w), int(height)
    try:
        img_h, img_w = read_image_size(path)
        w = max(8, min(w, img_w))
        h = max(8, min(h, img_h))
        x = max(0, min(x, img_w - w))
        y = max(0, min(y, img_h - h))
        _progress(10, "Loading slice...")
        full = _load_any(path)
        tuned_band = None
        tuned_residual = None
        # args.preview_dir doubles as the meta dir here: requestPreview() in
        # js/preprocess_wizard.js sets previewPayload.previewDir to
        # <bundleRoot>/.masonjar when a project is active (the same dir
        # czi_extract.py writes seamgrid/ sidecars under), or to the image's
        # own parent dir as a no-project fallback -- in that fallback case
        # there is simply no seamgrid/ sibling to find, so load_seam_grid()
        # returns None and this transparently falls back to autocorr below.
        # No new CLI/IPC plumbing needed for either case.
        meta_dir = Path(args.preview_dir.strip()) if args.preview_dir and args.preview_dir.strip() else None
        grid = load_seam_grid(meta_dir, _slice_id_for(path), path)
        if seam_mode == "grid_estimated":
            grid = None
        if seam_mode == "known_geometry" and grid is None:
            emit_preview_json({"ok": False, "error": "Known-geometry unavailable for this slice"})
            return 1
        if grid is not None:
            _progress(30, "Applying known-geometry correction...")
            corrected, info = correct_known_geometry(full, grid, band=band)
            seam_mode = "known_geometry"
            if autotune:
                _log(
                    f"seam_known_geometry_autotune_skipped image={path.name} "
                    "band search is not applicable once tile geometry is known"
                )
        else:
            seam_mode = "grid_estimated"
            if autotune:
                _progress(25, "Auto-tuning band...")
                tuned_band, tuned_residual, trace = _autotune_band(full)
                _log(f"seam_autotune mode={seam_mode} band={tuned_band} residual={tuned_residual} trace={trace}")
                if tuned_band is not None:
                    band = tuned_band
            _progress(40, "Detecting + correcting seams...")
            corrected, info = correct(full, band=band)
        _log(
            f"seam_preview mode={seam_mode} correlation={info.get('corr')} "
            f"n_seams={info.get('n_seams')} "
            f"positions_v={info.get('seam_positions_vertical', info.get('seam_positions'))} "
            f"positions_h={info.get('seam_positions_horizontal', [])}"
        )
        if seam_mode == "known_geometry":
            _log(f"seam_correction_details image={path.name} details={json.dumps(info)}")
            _log(
                f"seam_geometry_diagnostics confidence_v={info.get('seam_confidence_vertical')} "
                f"confidence_h={info.get('seam_confidence_horizontal')} "
                f"offset_jumps_v={info.get('offset_jumps_vertical')} "
                f"offset_jumps_h={info.get('offset_jumps_horizontal')}"
                f" steps_v={info.get('step_diagnostics_vertical')}"
                f" steps_h={info.get('step_diagnostics_horizontal')}"
            )
        diag_positions = info.get("seam_positions_vertical") or info.get("seam_positions") or []
        seam_diag = _seam_diagnostics(full, corrected, diag_positions, band=band)
        _log(f"seam_preview_diagnostics mode={seam_mode} steps={seam_diag}")
        _progress(80, "Cropping viewport...")
        roi = corrected[y : y + h, x : x + w]
        out_dir = Path(args.preview_dir.strip()) if args.preview_dir else path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "_seam_preview.png"
        _atomic_write_png(out_path, roi)
        _progress(100, "Done")
        emit_preview_json(
            {
                "ok": True,
                "previewPath": str(out_path.resolve()),
                "width": int(w),
                "height": int(h),
                "period": info.get("period"),
                "corr": info.get("corr"),
                "n_seams": info.get("n_seams"),
                "band": band,
                "autotuned": bool(autotune and tuned_band is not None),
                "autotune_residual": round(tuned_residual, 4) if tuned_residual is not None else None,
                "seamMode": seam_mode,
                "n_seams_vertical": info.get("n_seams_vertical"),
                "n_seams_horizontal": info.get("n_seams_horizontal"),
                "n_seams_periodic": info.get("n_seams_periodic"),
                "n_seams_recovered": info.get("n_seams_recovered"),
                "seam_positions": info.get("seam_positions"),
                "seam_positions_vertical": info.get("seam_positions_vertical"),
                "seam_positions_horizontal": info.get("seam_positions_horizontal"),
                "seam_diagnostics": seam_diag,
                "seam_confidence_vertical": info.get("seam_confidence_vertical"),
                "seam_confidence_horizontal": info.get("seam_confidence_horizontal"),
                "offset_jumps_vertical": info.get("offset_jumps_vertical"),
                "offset_jumps_horizontal": info.get("offset_jumps_horizontal"),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        _log_exception("preview", exc)
        emit_preview_json({"ok": False, "error": str(exc)})
        return 1


# --- batch ---------------------------------------------------------------


def run_batch(args) -> int:
    if not args.config:
        print("SEAM_NO_OUTPUT: missing -j config", flush=True)
        return 1
    config_path = Path(str(args.config).strip())
    if not config_path.is_file():
        print("SEAM_NO_OUTPUT: config missing", flush=True)
        return 1
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:  # noqa: BLE001
        _log_exception("batch_config", exc)
        print("SEAM_NO_OUTPUT: config unreadable", flush=True)
        return 1
    _log(f"seam_batch_config path={config_path} keys={sorted(cfg.keys()) if isinstance(cfg, dict) else type(cfg).__name__}")
    if not isinstance(cfg, dict):
        _log("seam_batch_config invalid_root expected=object")
        print("SEAM_NO_OUTPUT: config root must be an object", flush=True)
        return 1

    # writeRunConfig() (js/preprocess_wizard.js) writes this config JSON to
    # <bundleRoot>/.masonjar/<configFileName> -- its own parent dir is
    # therefore already the meta dir czi_extract.py writes seamgrid/
    # sidecars under, so no new config field is needed to locate it.
    meta_dir = config_path.resolve().parent

    input_path = Path(str(cfg.get("source_abs") or cfg.get("input_dir") or "").strip())
    output_path = Path(str(cfg.get("output_abs") or cfg.get("output_dir") or "").strip())
    band = int(cfg.get("band", DEFAULT_BAND))
    autotune = bool(cfg.get("autotune", False))
    seam_mode = str(cfg.get("seam_mode", "auto") or "auto")

    if not input_path.is_dir():
        print("SEAM_NO_OUTPUT: source_abs missing", flush=True)
        return 1

    # Primary channel (whichever Signal branch the wizard's main dropdown had
    # selected -- DAPI by default) plus, since 2026-09-05, any additional
    # signal branches opted in via the "Also seam-correct these channels"
    # checkboxes (js/preprocess_wizard.js buildExtraChannelConfigs()). Each
    # channel is corrected independently into its own output dir -- the loop
    # below just repeats the single-directory logic per channel, there is no
    # cross-channel interaction.
    channels: list[dict[str, Any]] = [
        {
            "branch": str(cfg.get("signal_branch") or ""),
            "input": input_path,
            "output": output_path,
            "wanted": _resolve_wanted_stems(cfg.get("slice_list")),
            "label": "",
            "seam_mode": seam_mode,
        }
    ]
    for extra in cfg.get("extra_channels") or []:
        extra_input = Path(str(extra.get("source_abs") or "").strip())
        extra_output_raw = str(extra.get("output_abs") or "").strip()
        branch_name = str(extra.get("branch") or "")
        if not extra_input.is_dir() or not extra_output_raw:
            _log(f"seam_extra_channel_skipped branch={branch_name} reason=bad_paths")
            continue
        channels.append(
            {
                "branch": branch_name,
                "input": extra_input,
                "output": Path(extra_output_raw),
                "wanted": _resolve_wanted_stems(extra.get("slice_ids")),
                "label": f"[{branch_name}] ",
                "seam_mode": seam_mode,
            }
        )

    # Pre-count post-filter files across every channel so the single count
    # line below matches what main.js's spawnPreprocessBatch reads as the
    # progress-bar denominator (it only ever reads the first bare-integer
    # line printed).
    precounted: list[list[Path]] = []
    for ch in channels:
        all_files = _list_image_files_any(ch["input"])
        files = all_files
        if ch["wanted"]:
            files = [p for p in files if _slice_matches_wanted(_slice_stem(p), ch["wanted"])]
        _log(
            f"seam_input label={ch.get('label','').strip()} path={ch['input']} "
            f"discovered={len(all_files)} filtered={len(files)} "
            f"wanted={len(ch['wanted']) if ch['wanted'] else 0} "
            f"file_stems={[ _slice_stem(p) for p in all_files[:20] ]} "
            f"wanted_stems={sorted(ch['wanted'])[:20] if ch['wanted'] else []}"
        )
        precounted.append(files)
    total_files = sum(len(f) for f in precounted)
    print(f"{total_files}", flush=True)
    if total_files == 0:
        print("LOG: no input files", flush=True)
        return 1

    total_written = 0
    for ch in channels:
        written, stats = _process_channel(
            ch["input"],
            ch["output"],
            band=band,
            autotune=autotune,
            wanted=ch["wanted"],
            meta_dir=meta_dir,
            label=ch["label"],
            seam_mode=ch["seam_mode"],
        )
        total_written += len(written)
        if not written:
            _log(f"seam_channel_empty branch={ch['branch'] or '(primary)'} output={ch['output']}")
            continue
        try:
            from run_manifest import write_run_manifest

            write_run_manifest(
                str(ch["output"]),
                {
                    "step": "seam",
                    "branch": ch["branch"],
                    "input_dir": str(ch["input"]),
                    "input_files": written,
                    "band": band,
                    "autotune": autotune,
                    "per_file": stats,
                },
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"seam_manifest_failed branch={ch['branch']} err={exc!r}")
            traceback.print_exc()

    if total_written == 0:
        print(f"SEAM_NO_OUTPUT: 0 of {total_files} files written.", flush=True)
        print("Done!", flush=True)
        return 1

    print("Done!", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vertical tile-seam correction")
    parser.add_argument("-j", "--config", default="", help="Run config JSON path")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--image", default="")
    parser.add_argument("-x", "--x", type=int, default=0)
    parser.add_argument("-y", "--y", type=int, default=0)
    parser.add_argument("-w", "--w", type=int, default=512)
    parser.add_argument("--height", "--h", dest="height", type=int, default=512)
    parser.add_argument("--preview-dir", default="")
    parser.add_argument("--band", type=int, default=DEFAULT_BAND)
    parser.add_argument("--autotune", action="store_true")
    parser.add_argument("--seam-mode", choices=["auto", "known_geometry", "grid_estimated"], default="auto")
    args = parser.parse_args(argv)
    try:
        if args.preview:
            return run_preview(args)
        return run_batch(args)
    except Exception as exc:  # noqa: BLE001
        _log_exception("main", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

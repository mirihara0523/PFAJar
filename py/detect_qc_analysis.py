"""Post-run detection QC analysis: bimodal intensity threshold + parameter suggestions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.mixture import GaussianMixture

from detect_qc import DetectQcCollector, object_confidence, object_xyxy, bbox_area, bbox_intensity_p90

MIN_SAMPLES = 30
LOW_WEIGHT = 2.0
INTENSITY_CLAMP = (10, 245)


@dataclass
class DetectionRecord:
    confidence: float
    area_px2: float
    intensity_p90: float
    eccentricity: float | None


def _finite(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out.append(float(v))
    return out


def _gaussian_pdf(x: float, mean: float, std: float) -> float:
    if std <= 1e-9:
        return 0.0
    z = (x - mean) / std
    return float(np.exp(-0.5 * z * z) / (std * np.sqrt(2 * np.pi)))


def _intersection_threshold(m1: float, s1: float, m2: float, s2: float) -> float:
    """Approximate intersection of two 1D Gaussians by grid search."""
    lo = min(m1, m2) - 3 * max(s1, s2)
    hi = max(m1, m2) + 3 * max(s1, s2)
    xs = np.linspace(lo, hi, 500)
    pdfs = [
        _gaussian_pdf(float(x), m1, s1) - _gaussian_pdf(float(x), m2, s2)
        for x in xs
    ]
    sign_changes = [
        i
        for i in range(len(pdfs) - 1)
        if pdfs[i] * pdfs[i + 1] <= 0 and abs(pdfs[i] - pdfs[i + 1]) > 1e-12
    ]
    if sign_changes:
        mid_idx = sign_changes[len(sign_changes) // 2]
        return float((xs[mid_idx] + xs[mid_idx + 1]) / 2.0)
    return float((m1 + m2) / 2.0)


def estimate_intensity_threshold(intensities: list[float]) -> dict[str, Any]:
    clean = _finite(intensities)
    if len(clean) < MIN_SAMPLES:
        return {
            "bimodal": False,
            "intensity_threshold_estimate": None,
            "clusters": {},
            "reason": "too_few_detections",
        }

    arr = np.asarray(clean, dtype=float).reshape(-1, 1)
    gmm1 = GaussianMixture(n_components=1, random_state=0, n_init=3).fit(arr)
    gmm2 = GaussianMixture(n_components=2, random_state=0, n_init=5).fit(arr)
    if gmm1.bic(arr) < gmm2.bic(arr):
        return {
            "bimodal": False,
            "intensity_threshold_estimate": None,
            "clusters": {"median": float(np.median(arr))},
            "reason": "unimodal",
        }

    order = np.argsort(gmm2.means_.flatten())
    m1 = float(gmm2.means_[order[0], 0])
    m2 = float(gmm2.means_[order[1], 0])
    s1 = float(np.sqrt(gmm2.covariances_[order[0]].flatten()[0]))
    s2 = float(np.sqrt(gmm2.covariances_[order[1]].flatten()[0]))
    threshold = _intersection_threshold(m1, s1, m2, s2)
    threshold = int(round(max(INTENSITY_CLAMP[0], min(INTENSITY_CLAMP[1], threshold))))

    low_vals = [v for v in clean if v < threshold]
    high_vals = [v for v in clean if v >= threshold]
    return {
        "bimodal": True,
        "intensity_threshold_estimate": threshold,
        "clusters": {
            "low_median": float(np.median(low_vals)) if low_vals else None,
            "high_median": float(np.median(high_vals)) if high_vals else None,
            "low_mean": m1,
            "high_mean": m2,
        },
        "reason": None,
    }


def build_raw_records(collector: DetectQcCollector) -> list[DetectionRecord]:
    """Per-detection records from raw SAHI output (for filter tuning)."""
    raw = collector.raw_run
    n = len(raw.confidence)
    ecc_by_intensity: dict[float, float] = {}
    for ecc, inten in collector.pre_ecc_records:
        if inten is not None and not math.isnan(inten):
            ecc_by_intensity[float(inten)] = float(ecc)

    records: list[DetectionRecord] = []
    for i in range(n):
        conf = raw.confidence[i]
        area = raw.area[i]
        inten = raw.intensity[i]
        if conf is None or math.isnan(conf):
            continue
        if inten is None or math.isnan(inten):
            continue
        if area is None or math.isnan(area):
            continue
        inten_f = float(inten)
        ecc = ecc_by_intensity.get(inten_f)
        records.append(
            DetectionRecord(
                confidence=float(conf),
                area_px2=float(area),
                intensity_p90=inten_f,
                eccentricity=ecc,
            )
        )
    return records


def build_screened_records(collector: DetectQcCollector) -> list[DetectionRecord]:
    """Records for screened (saved) detections before optional intensity filter."""
    final = collector.run
    n = len(final.confidence)
    ecc_map: dict[tuple[float, float], float] = {}
    for ecc, inten in collector.pre_ecc_records:
        if inten is not None and not math.isnan(inten):
            ecc_map[(round(float(ecc), 4), round(float(inten), 2))] = float(ecc)

    records: list[DetectionRecord] = []
    for i in range(n):
        conf = final.confidence[i]
        area = final.area[i]
        inten = final.intensity[i]
        if conf is None or math.isnan(conf):
            continue
        if inten is None or math.isnan(inten):
            continue
        if area is None or math.isnan(area):
            continue
        records.append(
            DetectionRecord(
                confidence=float(conf),
                area_px2=float(area),
                intensity_p90=float(inten),
                eccentricity=None,
            )
        )
    return records


def _score_threshold(
    records: list[DetectionRecord],
    intensity_threshold: float,
    *,
    attr: str,
    candidates: list[float],
    higher_is_pass: bool = True,
) -> float | None:
    """Reserved for future labeled-data tuning of confidence/area/eccentricity."""
    if not records or intensity_threshold is None:
        return None

    def passes(rec: DetectionRecord, cutoff: float) -> bool:
        val = getattr(rec, attr)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return False
        return float(val) >= cutoff if higher_is_pass else float(val) <= cutoff

    best_cutoff = None
    best_score = -1e18
    for cutoff in candidates:
        high_kept = low_kept = 0
        for rec in records:
            high = rec.intensity_p90 >= intensity_threshold
            if not passes(rec, cutoff):
                continue
            if high:
                high_kept += 1
            else:
                low_kept += 1
        score = high_kept - LOW_WEIGHT * low_kept
        if score > best_score:
            best_score = score
            best_cutoff = cutoff
    return best_cutoff


def suggest_detection_params(
    raw_records: list[DetectionRecord],
    intensity_threshold: float | None,
    current_thresholds: dict[str, float],
) -> dict[str, Any]:
    """Return intensity cutoff only; other params deferred until labeled TP/FP data exists."""
    del raw_records, current_thresholds
    if intensity_threshold is not None and intensity_threshold > 0:
        return {"intensity_min": int(round(intensity_threshold))}
    return {}


def _build_summary_lines(
    intensity_info: dict[str, Any],
    screened_records: list[DetectionRecord],
    suggestions: dict[str, Any],
    current_thresholds: dict[str, float],
) -> list[str]:
    lines: list[str] = []
    threshold = intensity_info.get("intensity_threshold_estimate")
    if not intensity_info.get("bimodal") or threshold is None:
        reason = intensity_info.get("reason") or "unknown"
        if reason == "too_few_detections":
            lines.append(
                "Not enough detections in this run to estimate an intensity threshold "
                f"(need at least {MIN_SAMPLES})."
            )
        else:
            lines.append(
                "Detections did not show a clear low/high intensity split in this run."
            )
        return lines

    below = sum(1 for r in screened_records if r.intensity_p90 < threshold)
    above = sum(1 for r in screened_records if r.intensity_p90 >= threshold)
    total = below + above
    pct = int(round(100.0 * below / total)) if total else 0
    clusters = intensity_info.get("clusters") or {}
    low_med = clusters.get("low_median")
    high_med = clusters.get("high_median")
    lines.append(
        f"Detected a low-intensity cluster (median p90 ≈ {low_med:.0f}) "
        f"and a brighter cluster (median p90 ≈ {high_med:.0f})."
        if low_med is not None and high_med is not None
        else "Detected bimodal intensity distribution in saved detections."
    )
    lines.append(
        f"Estimated intensity cutoff: {threshold} (0–255 p90). "
        f"{below} of {total} saved detections ({pct}%) fall below this value."
    )
    if suggestions.get("intensity_min"):
        lines.append(
            f"Setting intensity cutoff to {suggestions['intensity_min']} would drop "
            f"dim candidates while keeping brighter somata."
        )
        lines.append(
            "Re-run with this intensity cutoff in Advanced settings to apply it."
        )
    return lines


def analyze_detection_qc(
    collector: DetectQcCollector,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    """Analyze this run's QC collector; returns analysis block for summary JSON."""
    screened = build_screened_records(collector)
    intensities = [r.intensity_p90 for r in screened]
    intensity_info = estimate_intensity_threshold(intensities)
    threshold = intensity_info.get("intensity_threshold_estimate")

    raw_records = build_raw_records(collector)
    suggestions = suggest_detection_params(
        raw_records,
        threshold if intensity_info.get("bimodal") else None,
        thresholds,
    )

    below = above = 0
    if threshold is not None:
        below = sum(1 for r in screened if r.intensity_p90 < threshold)
        above = sum(1 for r in screened if r.intensity_p90 >= threshold)

    summary_lines = _build_summary_lines(
        intensity_info, screened, suggestions, thresholds
    )

    return {
        "bimodal": bool(intensity_info.get("bimodal")),
        "intensity_threshold_estimate": threshold,
        "clusters": intensity_info.get("clusters") or {},
        "counts": {
            "screened_total": len(screened),
            "below_threshold": below,
            "above_threshold": above,
        },
        "suggestions": suggestions,
        "current": {
            "confidence": thresholds.get("confidence"),
            "area": thresholds.get("area_px2"),
            "eccentricity": thresholds.get("eccentricity"),
            "intensity_min": thresholds.get("intensity_min", 0),
        },
        "summary_lines": summary_lines,
    }


def filter_objects_by_intensity(
    objects,
    gray_for_qc,
    intensity_min: float,
):
    """Drop SAHI objects below intensity_min (p90 within bbox). Keep if intensity unknown."""
    if intensity_min <= 0:
        return list(objects or []), 0
    kept = []
    removed = 0
    for obj in objects or []:
        box = object_xyxy(obj)
        inten = bbox_intensity_p90(box, gray_for_qc)
        if inten is None or inten >= intensity_min:
            kept.append(obj)
        else:
            removed += 1
    return kept, removed

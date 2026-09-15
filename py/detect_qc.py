"""Detection QC histograms for Cell Detection runs."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RUN_QC_FILES = (
    "detect_qc_confidence.png",
    "detect_qc_area_px2.png",
    "detect_qc_eccentricity.png",
)
LEGACY_RUN_QC_FILES = ("detect_qc_long_axis_px.png",)
LEGACY_SLICE_QC_FILES = ("long_axis_px.png",)
SUMMARY_JSON = "detect_qc_summary.json"
SLICES_DIR = "detect_qc_slices"
_DOT_RNG = np.random.default_rng(0)


def bbox_area(box) -> float:
    x1, y1, x2, y2 = box
    return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))


def bbox_intensity_p90(box, gray_image) -> float | None:
    """90th percentile intensity within bbox on CLAHE grayscale (0–255)."""
    if gray_image is None:
        return None
    gray = np.asarray(gray_image)
    if gray.ndim == 3:
        gray = gray[..., 0]
    h, w = gray.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    return float(np.percentile(roi, 90))


def bbox_intensity_peak(box, gray_image) -> float | None:
    """Peak (max) intensity within bbox on CLAHE grayscale (0–255)."""
    if gray_image is None:
        return None
    gray = np.asarray(gray_image)
    if gray.ndim == 3:
        gray = gray[..., 0]
    h, w = gray.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    return float(np.max(roi))


def object_confidence(obj) -> float:
    score = getattr(obj, "score", None)
    if score is None:
        return float("nan")
    return float(getattr(score, "value", score))


def object_xyxy(obj):
    return obj.bbox.to_xyxy()


@dataclass
class QcMetricLists:
    confidence: list[float] = field(default_factory=list)
    area: list[float] = field(default_factory=list)
    intensity: list[float] = field(default_factory=list)
    intensity_peak: list[float] = field(default_factory=list)
    eccentricity: list[float] = field(default_factory=list)

    def extend_from_objects(self, objects, gray_image=None):
        for obj in objects or []:
            box = object_xyxy(obj)
            self.confidence.append(object_confidence(obj))
            self.area.append(bbox_area(box))
            p90 = bbox_intensity_p90(box, gray_image)
            peak = bbox_intensity_peak(box, gray_image) if p90 is not None else None
            self.intensity.append(p90 if p90 is not None else float("nan"))
            self.intensity_peak.append(
                peak if peak is not None else float("nan")
            )

    def merge_into(self, other: "QcMetricLists") -> None:
        other.confidence.extend(self.confidence)
        other.area.extend(self.area)
        other.intensity.extend(self.intensity)
        other.intensity_peak.extend(self.intensity_peak)
        other.eccentricity.extend(self.eccentricity)


@dataclass
class DetectQcCollector:
    run: QcMetricLists = field(default_factory=QcMetricLists)
    raw_run: QcMetricLists = field(default_factory=QcMetricLists)
    pre_ecc_run: QcMetricLists = field(default_factory=QcMetricLists)
    pre_ecc_records: list[tuple[float, float]] = field(default_factory=list)
    slices: dict[str, QcMetricLists] = field(default_factory=dict)
    raw_slices: dict[str, QcMetricLists] = field(default_factory=dict)
    pre_ecc_slices: dict[str, QcMetricLists] = field(default_factory=dict)
    pre_ecc_slice_records: dict[str, list[tuple[float, float]]] = field(
        default_factory=dict
    )

    def _slice_bucket(self, mapping: dict[str, QcMetricLists], slice_id: str) -> QcMetricLists:
        if slice_id not in mapping:
            mapping[slice_id] = QcMetricLists()
        return mapping[slice_id]

    def add_slice_pass(
        self,
        slice_id: str,
        raw_objects,
        final_objects,
        pre_ecc_eccentricities: list[float] | None = None,
        pre_ecc_records: list[tuple[float, float]] | None = None,
        gray_image=None,
    ) -> None:
        pre_ecc_eccentricities = pre_ecc_eccentricities or []
        pre_ecc_records = pre_ecc_records or []

        raw_slice = self._slice_bucket(self.raw_slices, slice_id)
        raw_slice.extend_from_objects(raw_objects, gray_image)
        self.raw_run.extend_from_objects(raw_objects, gray_image)

        pre_ecc_slice = self._slice_bucket(self.pre_ecc_slices, slice_id)
        pre_ecc_slice.eccentricity.extend(pre_ecc_eccentricities)
        self.pre_ecc_run.eccentricity.extend(pre_ecc_eccentricities)
        self.pre_ecc_records.extend(pre_ecc_records)
        if slice_id not in self.pre_ecc_slice_records:
            self.pre_ecc_slice_records[slice_id] = []
        self.pre_ecc_slice_records[slice_id].extend(pre_ecc_records)

        final_slice = self._slice_bucket(self.slices, slice_id)
        final_slice.extend_from_objects(final_objects, gray_image)
        self.run.extend_from_objects(final_objects, gray_image)


def cleanup_detect_qc_artifacts(output_dir: Path) -> list[str]:
    output_dir = Path(output_dir)
    removed: list[str] = []
    for name in (*RUN_QC_FILES, *LEGACY_RUN_QC_FILES, SUMMARY_JSON):
        path = output_dir / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    slices_root = output_dir / SLICES_DIR
    if slices_root.is_dir():
        shutil.rmtree(slices_root)
        removed.append(SLICES_DIR + "/")
    return removed


def _summary_stats(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if v is not None and not math.isnan(v)]
    if not clean:
        return {"count": 0, "median": None, "p10": None, "p90": None}
    arr = np.asarray(clean, dtype=float)
    return {
        "count": int(arr.size),
        "median": float(np.median(arr)),
        "p10": float(np.percentile(arr, 10)),
        "p90": float(np.percentile(arr, 90)),
    }


def build_summary_payload(
    collector: DetectQcCollector,
    thresholds: dict[str, float],
    *,
    per_slice_enabled: bool,
    run_files: list[str],
    slice_files: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "thresholds": thresholds,
        "run": {
            "raw": {
                "confidence": _summary_stats(collector.raw_run.confidence),
                "area_px2": _summary_stats(collector.raw_run.area),
                "intensity_p90": _summary_stats(collector.raw_run.intensity),
                "intensity_peak": _summary_stats(collector.raw_run.intensity_peak),
            },
            "pre_eccentricity_filter": {
                "eccentricity": _summary_stats(collector.pre_ecc_run.eccentricity),
            },
            "final": {
                "confidence": _summary_stats(collector.run.confidence),
                "area_px2": _summary_stats(collector.run.area),
                "intensity_p90": _summary_stats(collector.run.intensity),
                "intensity_peak": _summary_stats(collector.run.intensity_peak),
            },
        },
        "per_slice_enabled": bool(per_slice_enabled),
        "artifacts": {
            "run": run_files,
            "per_slice_dir": SLICES_DIR if per_slice_enabled else None,
            "per_slice": slice_files,
        },
    }


def _clean_pairs(
    x_values: list[float], y_values: list[float]
) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in zip(x_values, y_values):
        if x is None or y is None or math.isnan(x) or math.isnan(y):
            continue
        xs.append(float(x))
        ys.append(float(y))
    return xs, ys


def _shared_bin_edges(
    raw_x: list[float],
    final_x: list[float],
    *,
    xlim: tuple[float, float] | None = None,
) -> np.ndarray:
    all_x = [
        float(v)
        for v in (raw_x or []) + (final_x or [])
        if v is not None and not math.isnan(v)
    ]
    if xlim is not None:
        nbins = min(40, max(10, len(all_x) // 5)) if all_x else 10
        return np.linspace(xlim[0], xlim[1], nbins + 1)
    if not all_x:
        return np.linspace(0, 1, 11)
    nbins = min(40, max(10, len(all_x) // 5))
    return np.histogram_bin_edges(all_x, bins=nbins)


def _jittered_x(x: float, bin_edges: np.ndarray) -> float:
    idx = int(np.digitize(x, bin_edges) - 1)
    idx = max(0, min(len(bin_edges) - 2, idx))
    center = (bin_edges[idx] + bin_edges[idx + 1]) / 2.0
    width = bin_edges[idx + 1] - bin_edges[idx]
    jitter = (_DOT_RNG.random() - 0.5) * width * 0.75
    return float(center + jitter)


def _scatter_intensity_dots(
    ax2,
    x_values: list[float],
    y_values: list[float],
    bin_edges: np.ndarray,
    *,
    marker: str,
    facecolors: str,
    edgecolors: str,
    label: str,
    alpha: float = 0.55,
    s: float = 14,
) -> None:
    xs, ys = _clean_pairs(x_values, y_values)
    if not xs:
        return
    jitter_x = [_jittered_x(x, bin_edges) for x in xs]
    ax2.scatter(
        jitter_x,
        ys,
        marker=marker,
        facecolors=facecolors,
        edgecolors=edgecolors,
        linewidths=0.8 if marker == "o" and facecolors == "none" else 0.4,
        alpha=alpha,
        s=s,
        label=label,
        zorder=5,
    )


def _plot_histogram_with_intensity_dots(
    path: Path,
    title: str,
    xlabel: str,
    raw: QcMetricLists,
    final: QcMetricLists,
    *,
    x_key: str,
    threshold: float | None = None,
    threshold_label: str | None = None,
    xlim: tuple[float, float] | None = None,
    intensity_threshold_line: float | None = None,
) -> None:
    raw_x = getattr(raw, x_key)
    final_x = getattr(final, x_key)
    raw_clean = [float(v) for v in raw_x if v is not None and not math.isnan(v)]
    final_clean = [float(v) for v in final_x if v is not None and not math.isnan(v)]
    bin_edges = _shared_bin_edges(raw_x, final_x, xlim=xlim)

    fig, ax = plt.subplots(figsize=(7.5, 4.75), dpi=120)

    if raw_clean:
        ax.hist(
            raw_clean,
            bins=bin_edges,
            alpha=0.45,
            color="#6c757d",
            label=f"Raw SAHI ({len(raw_clean)})",
            edgecolor="white",
            linewidth=0.4,
        )
    if final_clean:
        ax.hist(
            final_clean,
            bins=bin_edges,
            alpha=0.75,
            color="#0d6efd",
            label=f"Final saved ({len(final_clean)})",
            edgecolor="white",
            linewidth=0.4,
        )
    if not raw_clean and not final_clean:
        ax.text(
            0.5,
            0.5,
            "No detections",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=12,
            color="#666",
        )

    if threshold is not None:
        ax.axvline(
            threshold,
            color="#dc3545",
            linestyle="--",
            linewidth=1.5,
            label=threshold_label or f"Threshold = {threshold:g}",
        )

    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    if xlim:
        ax.set_xlim(xlim)

    ax2 = ax.twinx()
    ax2.set_ylabel("Intensity p90 (0–255)")
    ax2.set_ylim(0, 255)
    if intensity_threshold_line is not None:
        ax2.axhline(
            intensity_threshold_line,
            color="#fd7e14",
            linestyle=":",
            linewidth=1.5,
            label=f"Intensity split ≈ {intensity_threshold_line:g}",
        )
    _scatter_intensity_dots(
        ax2,
        raw_x,
        raw.intensity,
        bin_edges,
        marker="o",
        facecolors="#000000",
        edgecolors="#000000",
        label=f"Raw intensity ({len(_clean_pairs(raw_x, raw.intensity)[0])})",
        alpha=0.45,
    )
    _scatter_intensity_dots(
        ax2,
        final_x,
        final.intensity,
        bin_edges,
        marker="o",
        facecolors="none",
        edgecolors="#0d6efd",
        label=f"Final intensity ({len(_clean_pairs(final_x, final.intensity)[0])})",
        alpha=0.85,
        s=18,
    )

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="best", fontsize=7)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _plot_eccentricity_with_intensity_dots(
    path: Path,
    pre_ecc_records: list[tuple[float, float]],
    *,
    threshold: float | None = None,
    intensity_threshold_line: float | None = None,
) -> None:
    ecc_values = [float(e) for e, _ in pre_ecc_records]
    intensities = [float(i) for _, i in pre_ecc_records]
    clean_ecc = [v for v in ecc_values if not math.isnan(v)]
    bin_edges = _shared_bin_edges(ecc_values, [], xlim=(0, 1))

    fig, ax = plt.subplots(figsize=(7.5, 4.75), dpi=120)
    if clean_ecc:
        ax.hist(
            clean_ecc,
            bins=bin_edges,
            alpha=0.75,
            color="#198754",
            label=f"Pre-eccentricity filter ({len(clean_ecc)})",
            edgecolor="white",
            linewidth=0.4,
        )
    else:
        ax.text(
            0.5,
            0.5,
            "No detections",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=12,
            color="#666",
        )

    if threshold is not None:
        ax.axvline(
            threshold,
            color="#dc3545",
            linestyle="--",
            linewidth=1.5,
            label=f"Eccentricity cutoff = {threshold:g}",
        )

    ax.set_title("Eccentricity (post-area filter)")
    ax.set_xlabel("Eccentricity")
    ax.set_ylabel("Count")
    ax.set_xlim(0, 1)

    ax2 = ax.twinx()
    ax2.set_ylabel("Intensity p90 (0–255)")
    ax2.set_ylim(0, 255)
    if intensity_threshold_line is not None:
        ax2.axhline(
            intensity_threshold_line,
            color="#fd7e14",
            linestyle=":",
            linewidth=1.5,
            label=f"Intensity split ≈ {intensity_threshold_line:g}",
        )
    _scatter_intensity_dots(
        ax2,
        ecc_values,
        intensities,
        bin_edges,
        marker="o",
        facecolors="#000000",
        edgecolors="#000000",
        label=f"Intensity ({len(clean_ecc)})",
        alpha=0.45,
    )

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="best", fontsize=7)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def write_slice_histograms(
    collector: DetectQcCollector,
    slice_id: str,
    output_dir: Path,
    thresholds: dict[str, float],
) -> list[str]:
    output_dir = Path(output_dir)
    slice_dir = output_dir / SLICES_DIR / slice_id
    slice_dir.mkdir(parents=True, exist_ok=True)

    raw = collector.raw_slices.get(slice_id, QcMetricLists())
    final = collector.slices.get(slice_id, QcMetricLists())
    pre_ecc_records = collector.pre_ecc_slice_records.get(slice_id, [])

    files = [
        "confidence.png",
        "area_px2.png",
        "eccentricity.png",
    ]
    _plot_histogram_with_intensity_dots(
        slice_dir / files[0],
        f"Confidence — {slice_id}",
        "Confidence",
        raw,
        final,
        x_key="confidence",
        threshold=thresholds.get("confidence"),
        threshold_label=f"Confidence cutoff = {thresholds.get('confidence')}",
        xlim=(0, 1),
    )
    _plot_histogram_with_intensity_dots(
        slice_dir / files[1],
        f"BBox area — {slice_id}",
        "Area (px²)",
        raw,
        final,
        x_key="area",
        threshold=thresholds.get("area_px2"),
        threshold_label=f"Area cutoff = {thresholds.get('area_px2')} px²",
    )
    _plot_eccentricity_with_intensity_dots(
        slice_dir / files[2],
        pre_ecc_records,
        threshold=thresholds.get("eccentricity"),
    )
    return [f"{SLICES_DIR}/{slice_id}/{name}" for name in files]


def write_run_histograms(
    collector: DetectQcCollector,
    output_dir: Path,
    thresholds: dict[str, float],
    *,
    per_slice_enabled: bool = False,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    run_files = []

    from detect_qc_analysis import analyze_detection_qc

    analysis = analyze_detection_qc(collector, thresholds)
    intensity_line = analysis.get("intensity_threshold_estimate")

    confidence_path = output_dir / RUN_QC_FILES[0]
    _plot_histogram_with_intensity_dots(
        confidence_path,
        "Detection confidence (run total)",
        "Confidence",
        collector.raw_run,
        collector.run,
        x_key="confidence",
        threshold=thresholds.get("confidence"),
        threshold_label=f"Confidence cutoff = {thresholds.get('confidence')}",
        xlim=(0, 1),
        intensity_threshold_line=intensity_line,
    )
    run_files.append(RUN_QC_FILES[0])

    area_path = output_dir / RUN_QC_FILES[1]
    _plot_histogram_with_intensity_dots(
        area_path,
        "BBox area (run total)",
        "Area (px²)",
        collector.raw_run,
        collector.run,
        x_key="area",
        threshold=thresholds.get("area_px2"),
        threshold_label=f"Area cutoff = {thresholds.get('area_px2')} px²",
        intensity_threshold_line=intensity_line,
    )
    run_files.append(RUN_QC_FILES[1])

    ecc_path = output_dir / RUN_QC_FILES[2]
    _plot_eccentricity_with_intensity_dots(
        ecc_path,
        collector.pre_ecc_records,
        threshold=thresholds.get("eccentricity"),
        intensity_threshold_line=intensity_line,
    )
    run_files.append(RUN_QC_FILES[2])

    slice_files: dict[str, list[str]] = {}
    if per_slice_enabled:
        for slice_id in sorted(set(collector.slices.keys()) | set(collector.raw_slices.keys())):
            slice_files[slice_id] = write_slice_histograms(
                collector, slice_id, output_dir, thresholds
            )

    summary = build_summary_payload(
        collector,
        thresholds,
        per_slice_enabled=per_slice_enabled,
        run_files=run_files,
        slice_files=slice_files,
    )
    summary["analysis"] = analysis
    summary_path = output_dir / SUMMARY_JSON
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    return {
        "run_files": run_files,
        "summary_json": SUMMARY_JSON,
        "slice_files": slice_files,
        "summary": summary,
        "analysis": analysis,
    }

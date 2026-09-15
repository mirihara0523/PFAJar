"""Shared data types for Belljar.

These replace the opaque pickle-based serialization with typed, inspectable dataclasses.
Large arrays (annotation maps, images) are still stored as numpy files alongside the
JSON project file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYOUT_MASONJAR_V1 = "masonjar_v1"
LAYOUT_BELLJAR_V1 = "belljar_v1"
LAYOUT_IDS = (LAYOUT_MASONJAR_V1, LAYOUT_BELLJAR_V1)
PROJECT_VERSION_V1 = "1.0"

# Canonical relative role paths inside a masonjar_v1 / belljar_v1 bundle.
CANONICAL_ROLE_PATHS: dict[str, str] = {
    "original_scans": "data/original_scans",
    "dapi": "data/counting/00_dapi",
    "slices": "data/counting/01_slices",
    "max": "data/counting/03_max",
    "predictions": "data/counting/05_predictions",
    "quantification": "data/counting/06_quantification",
    "pkls": "data/counting/07_pkls",
    "dual": "data/counting/08_dual",
}


@dataclass
class SliceAlignment:
    """Alignment state for a single histological section."""

    section_name: str
    ap_position: float
    x_angle: float
    y_angle: float
    z_angle: float = 0.0
    region: str = "A"  # "A"=All, "C"=Cerebrum, "NC"=NonCerebrum
    hemisphere: str = "W"  # "W"=Whole, "L"=Left
    linked: bool = True
    mask_path: str | None = None


@dataclass
class DetectionResult:
    """Detection results for a single image channel."""

    boxes: list[list[float]]  # [[x1, y1, x2, y2], ...]
    scores: list[float]
    image_width: int
    image_height: int
    channel_index: int = 0
    model_name: str = ""
    confidence_threshold: float = 0.0

    @property
    def count(self) -> int:
        return len(self.boxes)


@dataclass
class RegistrationMetrics:
    """Quality metrics for a registration result."""

    mutual_information: float = 0.0
    normalized_cross_correlation: float = 0.0


@dataclass
class StepResult:
    """Result returned by every pipeline step."""

    success: bool
    output_path: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BelljarProject:
    """JSON-serializable project state.

    Large arrays (annotation maps) are stored as .npy files in the output directory
    and referenced by path — not embedded in the project file.

    v1 bundle fields (layout ``masonjar_v1`` or ``belljar_v1``):
    - ``roles``: logical role → relative path inside bundle (or absolute when reference-only)
    - ``sources``: original user paths at import (audit / re-sync)
    - ``settings``: per-step defaults
    - ``pipeline``: optional step completion metadata
    """

    version: str = PROJECT_VERSION_V1
    name: str = ""
    layout: str = LAYOUT_MASONJAR_V1
    created: str = ""
    modified: str = ""
    roles: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    pipeline: dict[str, Any] = field(default_factory=dict)
    reference_only: bool = False
    # Legacy / pipeline fields retained for older projects and modern CLI steps.
    input_path: str = ""
    output_path: str = ""
    atlas_name: str = "allen_mouse_10um"
    config_overrides: dict[str, Any] = field(default_factory=dict)
    alignments: dict[str, dict[str, Any]] = field(default_factory=dict)

    def touch_modified(self) -> None:
        self.modified = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        self.touch_modified()
        if not self.created:
            self.created = self.modified
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, path: Path) -> BelljarProject:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def new_v1_bundle(
        cls,
        name: str,
        *,
        reference_only: bool = False,
        roles: dict[str, str] | None = None,
        sources: dict[str, str] | None = None,
    ) -> BelljarProject:
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            version=PROJECT_VERSION_V1,
            name=name,
            layout=LAYOUT_MASONJAR_V1,
            created=now,
            modified=now,
            roles=dict(roles or CANONICAL_ROLE_PATHS),
            sources=dict(sources or {}),
            reference_only=reference_only,
        )

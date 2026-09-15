"""CCF parcellation pipeline step (headless bulk rollup)."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

from belljar.annotation.apply import (
    apply_parcellation_batch,
    load_parcellation_config,
)
from belljar.atlas.catalog import load_catalog
from belljar.config import BelljarConfig, ParcellationConfig
from belljar.pipeline.base import PipelineStep, ProgressCallback
from belljar.types import StepResult


class ParcellateStep(PipelineStep):
    @property
    def name(self) -> str:
        return "parcellate"

    def validate_inputs(self, **kwargs: Any) -> list[str]:
        errors: list[str] = []
        annotation_dir = kwargs.get("annotation_dir")
        if not annotation_dir:
            errors.append("annotation_dir is required")
        elif not Path(annotation_dir).is_dir():
            errors.append(f"annotation_dir not found: {annotation_dir}")
        structure_map_path = kwargs.get("structure_map_path")
        if structure_map_path and not Path(structure_map_path).is_file():
            errors.append(f"structure_map_path not found: {structure_map_path}")
        return errors

    def run(self, progress: ProgressCallback, **kwargs: Any) -> StepResult:
        cfg = ParcellationConfig.model_validate(kwargs.get("config") or {})
        annotation_dir = Path(
            kwargs.get("annotation_dir") or cfg.annotation_dir
        ).resolve()
        map_path = Path(
            kwargs.get("structure_map_path") or cfg.structure_map_path
        )
        graph_path = map_path.parent / "structure_graph.json"
        if not graph_path.is_file():
            return StepResult(
                success=False,
                errors=[f"structure_graph.json not found beside {map_path}"],
            )

        with map_path.open("rb") as f:
            structure_map = pickle.load(f)
        catalog = load_catalog(graph_path)

        config_path = kwargs.get("config_path")
        tier_id = cfg.tier_id
        st_level = cfg.st_level
        excluded = list(cfg.excluded_region_ids)
        slice_ids = list(cfg.slice_ids) if cfg.slice_ids else None
        if config_path:
            raw = load_parcellation_config(config_path)
            tier_id = raw.get("tier_id", tier_id)
            st_level = raw.get("st_level", st_level)
            if st_level is not None:
                st_level = int(st_level)
            excluded = [int(x) for x in (raw.get("excluded_region_ids") or excluded)]
            if raw.get("slice_ids"):
                slice_ids = [str(x) for x in raw["slice_ids"]]

        summary = apply_parcellation_batch(
            annotation_dir,
            slice_ids,
            tier_id=tier_id,
            st_level=st_level,
            excluded_region_ids=excluded,
            structure_map=structure_map,
            catalog=catalog,
        )
        total = len(summary.results)
        for i, result in enumerate(summary.results):
            progress(
                i + 1,
                total,
                f"parcellation {result.slice_id} ok={result.ok}",
            )

        return StepResult(
            success=summary.failed_count == 0,
            output_path=str(annotation_dir),
            metrics={
                "ok": summary.ok_count,
                "failed": summary.failed_count,
                "total": total,
            },
            errors=[
                f"{r.slice_id}: {r.error}"
                for r in summary.results
                if not r.ok and r.error
            ],
        )


def run_parcellate_cli(config_path: str, annotation_dir: str, structure_map_path: str) -> int:
    """CLI entry used by ``belljar parcellate``."""
    step = ParcellateStep(BelljarConfig())
    with Path(config_path).open(encoding="utf-8") as f:
        raw = json.load(f)
    errors = step.validate_inputs(
        annotation_dir=annotation_dir or raw.get("annotation_dir"),
        structure_map_path=structure_map_path,
        config_path=config_path,
    )
    if errors:
        for err in errors:
            print(err)
        return 1
    result = step.run(
        lambda c, t, m: print(f"LOG: {m}", flush=True),
        annotation_dir=annotation_dir or raw.get("annotation_dir"),
        structure_map_path=structure_map_path,
        config_path=config_path,
        config=raw,
    )
    print("Done!", flush=True)
    return 0 if result.success else 1

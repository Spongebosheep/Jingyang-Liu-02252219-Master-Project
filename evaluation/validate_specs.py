"""Validate all frozen evaluation contracts without importing Django."""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import (
    load_json,
    sha256_file,
    validate_baseline_contract,
    validate_baseline_output_schema,
    validate_export_bundle_schema,
    validate_freeze_manifest,
    validate_metric_calculation_schema,
    validate_metrics,
    validate_quality_review,
    validate_review_completion,
    validate_scenarios,
)


SPEC_DIR = Path(__file__).resolve().parent / "specs"


def validate_all() -> dict:
    paths = {
        "scenarios": SPEC_DIR / "scenarios.v4.json",
        "metrics": SPEC_DIR / "metrics.v3.json",
        "baseline_contract": SPEC_DIR / "baseline_contract.v2.json",
        "baseline_output_schema": SPEC_DIR / "prompt_only_output_schema.v2.json",
        "export_bundle_schema": SPEC_DIR / "export_bundle_schema.v2.json",
        "metric_calculation_schema": SPEC_DIR / "metric_calculation_schema.v2.json",
        "review_completion": SPEC_DIR / "review_completion.v2.json",
        "quality_review": SPEC_DIR / "quality_review.v2.json",
        "freeze_manifest": SPEC_DIR / "freeze_manifest.v10_2.json",
    }
    documents = {name: load_json(path) for name, path in paths.items()}
    validate_scenarios(documents["scenarios"])
    validate_metrics(documents["metrics"])
    validate_baseline_contract(documents["baseline_contract"])
    validate_baseline_output_schema(documents["baseline_output_schema"])
    validate_export_bundle_schema(documents["export_bundle_schema"])
    validate_metric_calculation_schema(documents["metric_calculation_schema"])
    validate_review_completion(documents["review_completion"])
    validate_quality_review(documents["quality_review"])
    validate_freeze_manifest(documents["freeze_manifest"], SPEC_DIR)
    return {
        "status": "PASS",
        "formal_run": False,
        "contracts": {
            name: {"path": str(path.relative_to(SPEC_DIR.parent)), "sha256": sha256_file(path)}
            for name, path in paths.items()
        },
    }


def main() -> None:
    print(json.dumps(validate_all(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

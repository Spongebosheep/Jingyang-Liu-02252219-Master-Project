"""Run the exact 16-attempt live Baseline development gate.

This gate is an engineering stability check performed before final freeze.  It
uses the live Responses API but always uses ``dry_run`` transactions, preserves
every attempt and validator result, and can never confer formal-evidence
eligibility.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .baseline_adapter import get_baseline_model
from .baseline_runner import PromptOnlyBaselineRunner, RUN_TYPE_DRY
from .execution_plan import CURRENT_FREEZE_MANIFEST_PATH, load_pair_plan
from .run_validator import EvaluationRunValidator, PASS
from .schemas import BASELINE_CONDITION, sha256_file
from .validate_specs import validate_all


GATE_SCHEMA_VERSION = "1.0"


class BaselineDevelopmentGateError(RuntimeError):
    """Raised when the development gate cannot be configured or recorded."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class BaselineDevelopmentGate:
    """Execute and independently validate the frozen 16 Baseline paths once."""

    def __init__(
        self,
        *,
        output_root: Path,
        repository_root: Path | None = None,
        runner_factory: Callable[..., Any] | None = None,
        validator_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.repository_root = (
            Path(repository_root).resolve() if repository_root else None
        )
        self.runner_factory = runner_factory or PromptOnlyBaselineRunner
        self.validator_factory = validator_factory or EvaluationRunValidator

    def run(self) -> tuple[dict[str, Any], Path]:
        validate_all()
        manifest, pair_plan = load_pair_plan()
        gate_contract = manifest["baseline_development_gate"]
        if gate_contract.get("rerun_forbidden") is True:
            raise BaselineDevelopmentGateError(
                "The historical Baseline development gate must not be rerun or reconstructed under v10.2."
            )
        if not os.getenv("OPENAI_API_KEY"):
            raise BaselineDevelopmentGateError(
                "OPENAI_API_KEY is required before the live Baseline development gate."
            )
        if get_baseline_model() != manifest["formal_comparison"]["model_id"]:
            raise BaselineDevelopmentGateError(
                "The configured model differs from the v10.1 frozen comparison model."
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        gate_id = f"baseline-development-gate-{timestamp}-{uuid.uuid4().hex[:8]}"
        gate_directory = self.output_root / gate_id
        if gate_directory.exists():
            raise BaselineDevelopmentGateError(
                f"Refusing to overwrite development gate: {gate_directory}"
            )
        gate_directory.mkdir(parents=True, exist_ok=False)
        plan_path = gate_directory / "frozen_development_plan.json"
        _atomic_write_json(
            plan_path,
            {
                "schema_version": GATE_SCHEMA_VERSION,
                "gate_id": gate_id,
                "created_at": _utc_now(),
                "freeze_manifest_path": str(CURRENT_FREEZE_MANIFEST_PATH),
                "freeze_manifest_sha256": sha256_file(
                    CURRENT_FREEZE_MANIFEST_PATH
                ),
                "condition": BASELINE_CONDITION,
                "run_type": RUN_TYPE_DRY,
                "planned_pairs": [pair.as_dict() for pair in pair_plan],
                "formal_evidence_eligible": False,
                "replacement_or_formal_reuse_forbidden": True,
            },
        )
        result_path = gate_directory / "gate_result.json"
        result: dict[str, Any] = {
            "schema_version": GATE_SCHEMA_VERSION,
            "gate_id": gate_id,
            "gate_status": "RUNNING",
            "started_at": _utc_now(),
            "completed_at": None,
            "condition": BASELINE_CONDITION,
            "run_type": RUN_TYPE_DRY,
            "development_only": True,
            "formal_run": False,
            "formal_evidence_eligible": False,
            "model_id": get_baseline_model(),
            "api_key_configured": True,
            "transport_required": gate_contract["transport"],
            "freeze_manifest_path": str(CURRENT_FREEZE_MANIFEST_PATH),
            "freeze_manifest_sha256": sha256_file(CURRENT_FREEZE_MANIFEST_PATH),
            "planned_attempt_count": len(pair_plan),
            "planned_pairs": [pair.as_dict() for pair in pair_plan],
            "frozen_development_plan_path": str(plan_path),
            "frozen_development_plan_sha256": sha256_file(plan_path),
            "attempt_count": 0,
            "pass_count": 0,
            "failure_count": 0,
            "attempts": [],
            "boundaries": {
                "results_may_be_used_as_formal_evidence": False,
                "results_may_replace_formal_attempts": False,
                "all_attempts_retained": True,
                "post_hoc_action_correction": False,
            },
        }
        _atomic_write_json(result_path, result)

        for pair in pair_plan:
            attempt = self._run_attempt(gate_directory, pair)
            result["attempts"].append(attempt)
            result["attempt_count"] = len(result["attempts"])
            result["pass_count"] = sum(row["gate_attempt_pass"] for row in result["attempts"])
            result["failure_count"] = result["attempt_count"] - result["pass_count"]
            _atomic_write_json(result_path, result)

        exact_count = result["attempt_count"] == gate_contract["planned_attempt_count"]
        result["gate_status"] = (
            "PASS"
            if exact_count and result["pass_count"] == gate_contract["planned_attempt_count"]
            else "FAIL"
        )
        result["completed_at"] = _utc_now()
        _atomic_write_json(result_path, result)
        return result, result_path

    def _run_attempt(self, gate_directory: Path, pair: Any) -> dict[str, Any]:
        runner = None
        runner_error: dict[str, str] | None = None
        record: dict[str, Any] = {}
        try:
            runner = self.runner_factory(
                scenario_id=pair.scenario_id,
                repetition=pair.repetition,
                run_type=RUN_TYPE_DRY,
                output_root=gate_directory / "runs" / pair.pair_key,
                repository_root=self.repository_root,
            )
            record = runner.run()
        except Exception as error:  # retained below; the gate continues all 16
            runner_error = {"type": type(error).__name__, "message": str(error)}
            if runner is not None:
                record = dict(getattr(runner, "record", {}) or {})

        record_path = Path(runner.record_path).resolve() if runner is not None else None
        validation_result: dict[str, Any] = {}
        validation_path: Path | None = None
        validator_error: dict[str, str] | None = None
        if record_path is not None and record_path.is_file():
            try:
                validation_result, validation_path = self.validator_factory(
                    record_path=record_path,
                    output_root=(
                        gate_directory
                        / "validation"
                        / str(record.get("run_id") or pair.pair_key)
                    ),
                ).run()
            except Exception as error:  # retain validator failures independently
                validator_error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }

        runtime = record.get("runtime_identity") or {}
        code = record.get("code_identity") or {}
        checks = {
            "runner_completed": record.get("execution_status") == "completed",
            "validator_pass": validation_result.get("validation_status") == PASS,
            "dry_run_flags": (
                record.get("run_type") == RUN_TYPE_DRY
                and record.get("formal_run") is False
            ),
            "live_transport": (
                runtime.get("model_transport") == "openai_responses_api"
                and runtime.get("mock_transport") is False
            ),
            "model_matches": runtime.get("model") == get_baseline_model(),
            "api_key_recorded_configured": (
                runtime.get("openai_api_key_configured") is True
            ),
            "tracked_worktree_clean": code.get("tracked_worktree_clean") is True,
            "validator_not_formal": (
                validation_result.get("formal_evidence_eligible") is False
            ),
        }
        return {
            "pair_key": pair.pair_key,
            "scenario_id": pair.scenario_id,
            "repetition": pair.repetition,
            "run_id": record.get("run_id"),
            "runner_record_path": str(record_path) if record_path else None,
            "runner_record_sha256": (
                sha256_file(record_path) if record_path and record_path.is_file() else None
            ),
            "execution_status": record.get("execution_status"),
            "runner_error": runner_error,
            "validator_result_path": str(validation_path) if validation_path else None,
            "validator_result_sha256": (
                sha256_file(validation_path)
                if validation_path and validation_path.is_file()
                else None
            ),
            "validation_status": validation_result.get("validation_status"),
            "validator_error": validator_error,
            "checks": checks,
            "gate_attempt_pass": all(checks.values()),
        }


__all__ = [
    "BaselineDevelopmentGate",
    "BaselineDevelopmentGateError",
    "GATE_SCHEMA_VERSION",
]

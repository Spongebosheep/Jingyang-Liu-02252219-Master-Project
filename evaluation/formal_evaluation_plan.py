"""Execute the preregistered paired formal plan without replacement reruns."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .baseline_runner import PromptOnlyBaselineRunner
from .execution_plan import (
    CURRENT_FREEZE_MANIFEST_PATH,
    EvaluationPlanError,
    load_attempt_plan,
    load_pair_plan,
    require_finalized_baseline_commit,
)
from .mvp_runner import MvpScenarioRunner
from .run_validator import EvaluationRunValidator, PASS
from .schemas import BASELINE_CONDITION, MVP_CONDITION, PAIRED_CONDITIONS, sha256_file
from .validate_specs import validate_all


FORMAL_PLAN_SCHEMA_VERSION = "1.0"
PILOT_PAIR_KEY = "S1-R01"


class FormalEvaluationPlanError(RuntimeError):
    """Raised when a formal plan cannot be configured or retained safely."""


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


class FormalEvaluationPlan:
    """Run and retain every identity in one immutable 32-attempt schedule."""

    def __init__(
        self,
        *,
        output_root: Path,
        reviewer_username: str,
        repository_root: Path | None = None,
        mvp_runner_factory: Callable[..., Any] | None = None,
        baseline_runner_factory: Callable[..., Any] | None = None,
        validator_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.reviewer_username = reviewer_username.strip()
        self.repository_root = (
            Path(repository_root).resolve() if repository_root else None
        )
        self.runner_factories = {
            MVP_CONDITION: mvp_runner_factory or MvpScenarioRunner,
            BASELINE_CONDITION: baseline_runner_factory or PromptOnlyBaselineRunner,
        }
        self.validator_factory = validator_factory or EvaluationRunValidator

    def run(self) -> tuple[dict[str, Any], Path]:
        if not self.reviewer_username:
            raise FormalEvaluationPlanError("A named reviewer is required.")
        validate_all()
        manifest, pair_plan = load_pair_plan()
        _manifest, attempt_plan = load_attempt_plan()
        try:
            require_finalized_baseline_commit(manifest)
        except EvaluationPlanError as error:
            raise FormalEvaluationPlanError(str(error)) from error

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        plan_id = f"formal-evaluation-plan-{timestamp}-{uuid.uuid4().hex[:8]}"
        plan_directory = self.output_root / plan_id
        if plan_directory.exists():
            raise FormalEvaluationPlanError(
                f"Refusing to overwrite formal plan: {plan_directory}"
            )
        plan_directory.mkdir(parents=True, exist_ok=False)
        snapshot_path = plan_directory / "frozen_attempt_plan.json"
        snapshot = {
            "schema_version": FORMAL_PLAN_SCHEMA_VERSION,
            "plan_id": plan_id,
            "created_at": _utc_now(),
            "freeze_manifest_path": str(CURRENT_FREEZE_MANIFEST_PATH),
            "freeze_manifest_sha256": sha256_file(CURRENT_FREEZE_MANIFEST_PATH),
            "pilot_pair_key": PILOT_PAIR_KEY,
            "planned_pairs": [pair.as_dict() for pair in pair_plan],
            "planned_attempts": [attempt.as_dict() for attempt in attempt_plan],
            "planned_condition_order": list(PAIRED_CONDITIONS),
            "replacement_reruns_permitted": False,
        }
        _atomic_write_json(snapshot_path, snapshot)
        result_path = plan_directory / "formal_plan_result.json"
        result: dict[str, Any] = {
            "schema_version": FORMAL_PLAN_SCHEMA_VERSION,
            "plan_id": plan_id,
            "plan_status": "RUNNING",
            "started_at": _utc_now(),
            "completed_at": None,
            "reviewer_username": self.reviewer_username,
            "pilot_pair_key": PILOT_PAIR_KEY,
            "pilot_status": "PENDING",
            "planned_pair_count": len(pair_plan),
            "planned_attempt_count": len(attempt_plan),
            "planned_pairs": [pair.as_dict() for pair in pair_plan],
            "planned_condition_order": list(PAIRED_CONDITIONS),
            "attempt_count": 0,
            "unique_attempt_identity_count": 0,
            "schedule_complete": False,
            "execution_completed_count": 0,
            "validator_pass_count": 0,
            "formal_evidence_eligible_count": 0,
            "runner_error_count": 0,
            "validator_error_count": 0,
            "missing_runner_record_count": 0,
            "retained_failure_count": 0,
            "replacement_attempt_count": 0,
            "attempts": [],
            "replacement_reruns_permitted": False,
            "formal_comparison_claim_generated": False,
            "freeze_contract_version": manifest["contract_version"],
            "frozen_attempt_plan_path": str(snapshot_path),
            "frozen_attempt_plan_sha256": sha256_file(snapshot_path),
        }
        _atomic_write_json(result_path, result)

        for pair in pair_plan:
            for condition in PAIRED_CONDITIONS:
                try:
                    attempt = self._run_attempt(plan_directory, pair, condition)
                except Exception as error:
                    result["plan_status"] = "SCHEDULE_CAPTURE_FAILED"
                    result["schedule_complete"] = False
                    result["completed_at"] = _utc_now()
                    result["capture_error"] = {
                        "type": type(error).__name__,
                        "message": str(error),
                        "pair_key": pair.pair_key,
                        "condition": condition,
                    }
                    _atomic_write_json(result_path, result)
                    raise FormalEvaluationPlanError(
                        "The immutable schedule/capture record could not be completed safely."
                    ) from error
                result["attempts"].append(attempt)
                self._update_counts(result)
                _atomic_write_json(result_path, result)

            if pair.pair_key == PILOT_PAIR_KEY:
                pilot_attempts = [
                    row for row in result["attempts"] if row["pair_key"] == PILOT_PAIR_KEY
                ]
                pilot_pass = (
                    len(pilot_attempts) == 2
                    and all(row["formal_attempt_pass"] for row in pilot_attempts)
                )
                result["pilot_status"] = "PASS" if pilot_pass else "FAIL"
                result["plan_status"] = "RUNNING_REMAINING_ATTEMPTS"
                _atomic_write_json(result_path, result)

        result["schedule_complete"] = (
            result["attempt_count"] == result["planned_attempt_count"]
            and result["unique_attempt_identity_count"] == result["planned_attempt_count"]
        )
        all_pass = (
            result["schedule_complete"]
            and all(row["formal_attempt_pass"] for row in result["attempts"])
        )
        result["plan_status"] = (
            "COMPLETED_ALL_PASS" if all_pass else "COMPLETED_WITH_RETAINED_FAILURES"
        )
        result["completed_at"] = _utc_now()
        _atomic_write_json(result_path, result)
        return result, result_path

    @staticmethod
    def _update_counts(result: dict[str, Any]) -> None:
        attempts = result["attempts"]
        result["attempt_count"] = len(attempts)
        identities = {(row["pair_key"], row["condition"]) for row in attempts}
        result["unique_attempt_identity_count"] = len(identities)
        result["execution_completed_count"] = sum(
            row["execution_status"] == "completed" for row in attempts
        )
        result["validator_pass_count"] = sum(
            row["validation_status"] == PASS for row in attempts
        )
        result["formal_evidence_eligible_count"] = sum(
            row["formal_evidence_eligible"] is True for row in attempts
        )
        result["runner_error_count"] = sum(row["runner_error"] is not None for row in attempts)
        result["validator_error_count"] = sum(row["validator_error"] is not None for row in attempts)
        result["missing_runner_record_count"] = sum(
            row["runner_record_path"] is None or row["runner_record_sha256"] is None
            for row in attempts
        )
        result["retained_failure_count"] = sum(
            row["formal_attempt_pass"] is not True for row in attempts
        )

    def _run_attempt(
        self, plan_directory: Path, pair: Any, condition: str
    ) -> dict[str, Any]:
        runner = None
        record: dict[str, Any] = {}
        runner_error: dict[str, str] | None = None
        try:
            runner = self.runner_factories[condition](
                scenario_id=pair.scenario_id,
                repetition=pair.repetition,
                run_type="formal",
                output_root=plan_directory / "runs" / pair.pair_key / condition,
                reviewer_username=self.reviewer_username,
                repository_root=self.repository_root,
            )
            record = runner.run()
        except Exception as error:  # the immutable attempt remains in the plan
            runner_error = {"type": type(error).__name__, "message": str(error)}
            if runner is not None:
                record = dict(getattr(runner, "record", {}) or {})

        record_path = Path(runner.record_path).resolve() if runner is not None else None
        validation: dict[str, Any] = {}
        validation_path: Path | None = None
        validator_error: dict[str, str] | None = None
        if record_path is not None and record_path.is_file():
            try:
                validation, validation_path = self.validator_factory(
                    record_path=record_path,
                    output_root=(
                        plan_directory
                        / "validation"
                        / str(record.get("run_id") or f"{pair.pair_key}-{condition}")
                    ),
                ).run()
            except Exception as error:
                validator_error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }

        formal_eligible = validation.get("formal_evidence_eligible") is True
        checks = {
            "condition_matches": record.get("condition") == condition,
            "scenario_matches": record.get("scenario_id") == pair.scenario_id,
            "repetition_matches": record.get("repetition") == pair.repetition,
            "runner_completed": record.get("execution_status") == "completed",
            "validator_pass": validation.get("validation_status") == PASS,
            "formal_evidence_eligible": formal_eligible,
        }
        if runner_error is not None:
            failure_classification = "RUNNER_ERROR"
        elif record_path is None or not record_path.is_file():
            failure_classification = "MISSING_RUNNER_RECORD"
        elif validator_error is not None:
            failure_classification = "VALIDATOR_ERROR"
        elif record.get("execution_status") != "completed":
            failure_classification = "EXECUTION_NOT_COMPLETED"
        elif validation.get("validation_status") != PASS:
            failure_classification = "VALIDATOR_NOT_PASS"
        elif not formal_eligible:
            failure_classification = "FORMAL_EVIDENCE_INELIGIBLE"
        elif not all(checks.values()):
            failure_classification = "UNCLASSIFIED_RETAINED_FAILURE"
        else:
            failure_classification = None
        return {
            "pair_key": pair.pair_key,
            "scenario_id": pair.scenario_id,
            "repetition": pair.repetition,
            "condition": condition,
            "run_id": record.get("run_id"),
            "execution_status": record.get("execution_status"),
            "runner_record_path": str(record_path) if record_path else None,
            "runner_record_sha256": (
                sha256_file(record_path) if record_path and record_path.is_file() else None
            ),
            "runner_error": runner_error,
            "validation_status": validation.get("validation_status"),
            "formal_evidence_eligible": formal_eligible,
            "validator_result_path": str(validation_path) if validation_path else None,
            "validator_result_sha256": (
                sha256_file(validation_path)
                if validation_path and validation_path.is_file()
                else None
            ),
            "validator_error": validator_error,
            "checks": checks,
            "formal_attempt_pass": all(checks.values()),
            "failure_classification": failure_classification,
        }


__all__ = [
    "FORMAL_PLAN_SCHEMA_VERSION",
    "PILOT_PAIR_KEY",
    "FormalEvaluationPlan",
    "FormalEvaluationPlanError",
]

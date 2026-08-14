"""Independent observed-vs-oracle validation for saved evaluation runs.

The validator never executes the product, calls a model, edits database state,
repairs output, or calculates aggregate metrics.  It reads one immutable runner
record plus its raw HTTP artefacts and writes a separate, append-only judgement.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import (
    BASELINE_CONDITION,
    CONTROL_RECORD_FIELDS,
    MVP_CONDITION,
    PAIRED_CONDITIONS,
    RUN_METADATA_FIELDS,
    load_json,
    sha256_file,
    sha256_json,
)
from .execution_plan import CURRENT_FREEZE_MANIFEST_PATH
from .validate_specs import SPEC_DIR, validate_all


VALIDATOR_SCHEMA_VERSION = "1.0"
PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUATED = "NOT_EVALUATED"


class RunValidationError(RuntimeError):
    """Raised when a runner record cannot be loaded or a result cannot be saved."""


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


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalised_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


class _Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._ids: set[str] = set()

    def add(
        self,
        check_id: str,
        *,
        scope: str,
        status: str,
        expected: Any,
        observed: Any,
        reason: str,
        metric_codes: tuple[str, ...] = (),
    ) -> None:
        if check_id in self._ids:
            raise RunValidationError(f"Duplicate validator check_id: {check_id}")
        if status not in {PASS, FAIL, NOT_EVALUATED}:
            raise RunValidationError(f"Unsupported check status: {status}")
        self._ids.add(check_id)
        self.rows.append(
            {
                "check_id": check_id,
                "scope": scope,
                "status": status,
                "expected": expected,
                "observed": observed,
                "reason": reason,
                "metric_codes": list(metric_codes),
            }
        )

    def compare(
        self,
        check_id: str,
        *,
        scope: str,
        expected: Any,
        observed: Any,
        reason_pass: str,
        reason_fail: str,
        metric_codes: tuple[str, ...] = (),
    ) -> None:
        self.add(
            check_id,
            scope=scope,
            status=PASS if observed == expected else FAIL,
            expected=expected,
            observed=observed,
            reason=reason_pass if observed == expected else reason_fail,
            metric_codes=metric_codes,
        )


class EvaluationRunValidator:
    """Validate one saved run without changing the runner record."""

    def __init__(
        self,
        *,
        record_path: Path,
        output_root: Path | None = None,
        scenario_path: Path | None = None,
        metric_path: Path | None = None,
        review_completion_path: Path | None = None,
    ) -> None:
        self.record_path = Path(record_path).resolve()
        self.output_root = Path(output_root).resolve() if output_root else None
        self.scenario_path = (
            Path(scenario_path).resolve()
            if scenario_path
            else SPEC_DIR / "scenarios.v4.json"
        )
        self.metric_path = (
            Path(metric_path).resolve()
            if metric_path
            else SPEC_DIR / "metrics.v3.json"
        )
        self.review_completion_path = (
            Path(review_completion_path).resolve()
            if review_completion_path
            else SPEC_DIR / "review_completion.v2.json"
        )
        self.freeze_manifest_path = CURRENT_FREEZE_MANIFEST_PATH
        self.record: dict[str, Any] = {}
        self.scenario_document: dict[str, Any] = {}
        self.scenario: dict[str, Any] = {}
        self.review_completion_document: dict[str, Any] = {}
        self.review_completion_action: dict[str, Any] = {}
        self.checks = _Checks()
        self._raw_bodies: dict[str, str] = {}

    def run(self) -> tuple[dict[str, Any], Path]:
        started_at = _utc_now()
        if not self.record_path.is_file():
            raise RunValidationError(f"Runner record does not exist: {self.record_path}")
        try:
            self.record = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RunValidationError(f"Cannot load runner record: {error}") from error
        if not isinstance(self.record, dict):
            raise RunValidationError("Runner record must contain one JSON object.")

        validate_all()
        self.scenario_document = load_json(self.scenario_path)
        self.review_completion_document = load_json(self.review_completion_path)
        scenario_id = self.record.get("scenario_id")
        self.scenario = next(
            (
                scenario
                for scenario in self.scenario_document["scenarios"]
                if scenario["scenario_id"] == scenario_id
            ),
            {},
        )
        self.review_completion_action = self.review_completion_document.get(
            "actions", {}
        ).get(scenario_id, {})

        self._validate_identity()
        self._validate_raw_http()
        self._validate_raw_model_calls()
        self._validate_steps()
        self._validate_topics_and_sources()
        self._validate_researcher_action()
        self._validate_completion_item_actions()
        self._validate_overall_review_action()

        counts = {
            status: sum(row["status"] == status for row in self.checks.rows)
            for status in (PASS, FAIL, NOT_EVALUATED)
        }
        if counts[FAIL]:
            validation_status = FAIL
        elif counts[NOT_EVALUATED]:
            validation_status = "INCOMPLETE"
        else:
            validation_status = PASS

        formal_evidence_eligible = bool(
            validation_status == PASS
            and self.record.get("formal_run") is True
            and self.record.get("run_type") == "formal"
            and self.record.get("comparison_freeze")
            and self.record.get("runtime_identity", {}).get(
                "openai_api_key_configured"
            )
            and not self.record.get("runtime_identity", {}).get("mock_transport")
        )
        validator_run_id = (
            f"validator-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        result = {
            "schema_version": VALIDATOR_SCHEMA_VERSION,
            "validator_run_id": validator_run_id,
            "run_id": self.record.get("run_id"),
            "condition": self.record.get("condition"),
            "scenario_id": scenario_id,
            "repetition": self.record.get("repetition"),
            "runner_execution_status": self.record.get("execution_status"),
            "validation_status": validation_status,
            "formal_evidence_eligible": formal_evidence_eligible,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "inputs": {
                "runner_record_path": str(self.record_path),
                "runner_record_sha256": sha256_file(self.record_path),
                "scenario_contract_path": str(self.scenario_path),
                "scenario_contract_sha256": sha256_file(self.scenario_path),
                "metric_contract_path": str(self.metric_path),
                "metric_contract_sha256": sha256_file(self.metric_path),
                "review_completion_contract_path": str(
                    self.review_completion_path
                ),
                "review_completion_contract_sha256": sha256_file(
                    self.review_completion_path
                ),
                "freeze_manifest_path": str(self.freeze_manifest_path),
                "freeze_manifest_sha256": sha256_file(self.freeze_manifest_path),
            },
            "summary": {
                "check_count": len(self.checks.rows),
                "pass_count": counts[PASS],
                "fail_count": counts[FAIL],
                "not_evaluated_count": counts[NOT_EVALUATED],
            },
            "checks": self.checks.rows,
            "manual_coding_required": {
                "UCR": "Pending separate atomic-proposition coding; never inferred here.",
                "MP": "Pending separate item-level meaning-preservation judgement; never inferred here.",
            },
            "claim_boundary": (
                "A validator PASS means the saved observations match the frozen automatic "
                "oracle. It does not establish outcome quality, natural-use reliability, "
                "production readiness, UCR, MP, or comparative superiority."
            ),
        }

        base = self.output_root or self.record_path.parent
        result_directory = base / "validation" / validator_run_id
        result_path = result_directory / "validator_result.json"
        if result_path.exists():
            raise RunValidationError(f"Refusing to overwrite validator result: {result_path}")
        _atomic_write_json(result_path, result)
        return result, result_path

    def _validate_identity(self) -> None:
        record = self.record
        missing_metadata = [field for field in RUN_METADATA_FIELDS if field not in record]
        self.checks.compare(
            "RUN-METADATA-FIELDS",
            scope="run",
            expected=[],
            observed=missing_metadata,
            reason_pass="All shared run-metadata fields are present.",
            reason_fail="The saved run is missing shared metadata fields.",
            metric_codes=("AI",),
        )
        self.checks.compare(
            "RUN-CONDITION",
            scope="run",
            expected=True,
            observed=record.get("condition") in PAIRED_CONDITIONS,
            reason_pass="The run uses a registered paired-condition identifier.",
            reason_fail="The condition is outside the paired evaluation contract.",
        )
        self.checks.compare(
            "RUN-SCENARIO-ID",
            scope="run",
            expected=True,
            observed=bool(self.scenario),
            reason_pass="The run maps to one frozen S1-S8 scenario.",
            reason_fail="The run does not map to a frozen scenario.",
        )
        self.checks.compare(
            "RUN-EXECUTION-COMPLETED",
            scope="run",
            expected={"execution_status": "completed", "error": None},
            observed={
                "execution_status": record.get("execution_status"),
                "error": record.get("error"),
            },
            reason_pass="The runner completed without a retained execution error.",
            reason_fail="The runner did not complete; downstream evidence may be partial.",
        )
        if not self.scenario:
            return

        self.checks.compare(
            "RUN-FROZEN-ORACLE",
            scope="run",
            expected=sha256_json(self.scenario),
            observed=sha256_json(record.get("frozen_oracle")),
            reason_pass="The embedded oracle is byte-semantically identical to the frozen scenario.",
            reason_fail="The embedded oracle differs from the frozen scenario contract.",
        )
        self.checks.compare(
            "RUN-SCENARIO-FILE-HASH",
            scope="run",
            expected=sha256_file(self.scenario_path),
            observed=record.get("scenario_sha256"),
            reason_pass="The runner recorded the current frozen scenario-file hash.",
            reason_fail="The runner scenario hash does not match the current frozen contract.",
        )
        self.checks.compare(
            "RUN-METRIC-FILE-HASH",
            scope="run",
            expected=sha256_file(self.metric_path),
            observed=record.get("metric_rules_sha256"),
            reason_pass="The runner recorded the frozen metric-rule hash.",
            reason_fail="The runner metric hash does not match the frozen rules.",
        )
        self.checks.compare(
            "RUN-REVIEW-COMPLETION-FILE-HASH",
            scope="run",
            expected=sha256_file(self.review_completion_path),
            observed=record.get("review_completion_policy_sha256"),
            reason_pass="The runner recorded the frozen review-completion hash.",
            reason_fail="The runner review-completion hash does not match the frozen contract.",
            metric_codes=("RAC",),
        )
        protocol_snapshot = record.get("protocol_snapshot")
        self.checks.compare(
            "RUN-PROTOCOL-SNAPSHOT-HASH",
            scope="run",
            expected=record.get("protocol_snapshot_sha256"),
            observed=sha256_json(protocol_snapshot) if isinstance(protocol_snapshot, dict) else None,
            reason_pass="The saved Protocol snapshot matches its recorded SHA-256.",
            reason_fail="The Protocol snapshot is missing or has drifted.",
        )
        self.checks.compare(
            "RUN-PROTOCOL-LOCKED",
            scope="run",
            expected="locked",
            observed=(record.get("final_database_snapshot") or {}).get("session", {}).get(
                "protocol_status"
            ),
            reason_pass="The executed Session used a locked Protocol.",
            reason_fail="The executed Session did not record a locked Protocol.",
        )
        code_identity = record.get("code_identity") or {}
        self.checks.compare(
            "RUN-CODE-IDENTITY",
            scope="run",
            expected=True,
            observed=bool(code_identity.get("commit") and code_identity.get("tree")),
            reason_pass="Commit and tree identities are recorded.",
            reason_fail="The run cannot be traced to both a commit and tree.",
        )
        comparison_freeze = record.get("comparison_freeze") or {}
        current_manifest = load_json(self.freeze_manifest_path)
        current_comparison = current_manifest["formal_comparison"]
        self.checks.compare(
            "RUN-COMPARISON-FREEZE",
            scope="run",
            expected={
                "contract_version": current_manifest["contract_version"],
                "sha256": sha256_file(self.freeze_manifest_path),
                "model_id": current_comparison["model_id"],
                "eligibility_source": current_comparison["eligibility_source"],
            },
            observed={
                "contract_version": comparison_freeze.get("contract_version"),
                "sha256": comparison_freeze.get("sha256"),
                "model_id": comparison_freeze.get("model_id"),
                "eligibility_source": comparison_freeze.get("eligibility_source"),
            },
            reason_pass="The run is tied to the current pre-result comparison freeze.",
            reason_fail="The run is missing or differs from the current comparison freeze.",
        )
        if record.get("condition") == BASELINE_CONDITION:
            adapter = record.get("adapter_identity") or {}
            boundaries = record.get("comparison_boundaries") or {}
            prompt_path = SPEC_DIR.parent / "prompts" / "prompt_only_baseline.v3.txt"
            schema_path = SPEC_DIR / "prompt_only_output_schema.v2.json"
            self.checks.compare(
                "RUN-BASELINE-FROZEN-ADAPTER",
                scope="run",
                expected={
                    "prompt_sha256": sha256_file(prompt_path),
                    "output_schema_sha256": sha256_file(schema_path),
                    "routing_provenance": "not_applicable_prompt_only_condition",
                    "graph_approved_follow_up_wording": "not_applicable_prompt_only_condition",
                },
                observed={
                    "prompt_sha256": adapter.get("prompt_sha256"),
                    "output_schema_sha256": adapter.get("output_schema_sha256"),
                    "routing_provenance": adapter.get("routing_provenance"),
                    "graph_approved_follow_up_wording": adapter.get(
                        "graph_approved_follow_up_wording"
                    ),
                },
                reason_pass="The prompt-only adapter is tied to the frozen prompt and schema with MVP-only provenance marked N/A.",
                reason_fail="The prompt-only adapter identity or N/A provenance boundary has drifted.",
            )
            self.checks.compare(
                "RUN-BASELINE-SEPARATION",
                scope="run",
                expected={
                    "langgraph_routing_provenance": "not_applicable",
                    "graph_approved_follow_up_wording": "not_applicable",
                    "semantic_repair": False,
                    "post_hoc_action_correction": False,
                },
                observed=boundaries,
                reason_pass="The baseline record explicitly excludes routing reuse and output repair.",
                reason_fail="The baseline separation boundary is missing or inconsistent.",
            )
        if record.get("formal_run") is True or record.get("run_type") == "formal":
            raw_calls = record.get("raw_model_calls") or []
            final_snapshot = record.get("final_database_snapshot") or {}
            decisions = final_snapshot.get("agent_decisions") or []
            runtime = record.get("runtime_identity") or {}
            condition = record.get("condition")
            frozen_parameters = current_comparison[
                "model_parameters_by_condition"
            ].get(condition, {})
            expected_call_kind = (
                "protocol_coverage_check"
                if condition == MVP_CONDITION
                else "prompt_only_turn"
            )
            run_directory = self.record_path.parent.resolve()
            envelope_checks = []
            for call in raw_calls:
                if condition == MVP_CONDITION:
                    request = ((call.get("request") or {}).get("kwargs") or {})
                else:
                    request = {}
                    relative = call.get("raw_request_path")
                    if isinstance(relative, str) and relative:
                        candidate = (run_directory / relative).resolve()
                        try:
                            candidate.relative_to(run_directory)
                        except ValueError:
                            candidate = None
                        if candidate and candidate.is_file():
                            try:
                                request = json.loads(
                                    candidate.read_text(encoding="utf-8")
                                )
                            except (OSError, json.JSONDecodeError):
                                request = {}
                expected_parameters = frozen_parameters.get(call.get("kind")) or {}
                envelope_checks.append(
                    bool(expected_parameters)
                    and request.get("model") == current_comparison["model_id"]
                    and request.get("temperature")
                    == expected_parameters.get("temperature")
                    and request.get("max_output_tokens")
                    == expected_parameters.get("max_output_tokens")
                    and request.get("store") == expected_parameters.get("store")
                )
            formal_reviewer = record.get("formal_reviewer") or {}
            evidence_rendered = any(
                event.get("event") == "evidence_record_render"
                and bool(
                    ((event.get("http") or {}).get("response") or {}).get(
                        "raw_body_path"
                    )
                )
                for event in record.get("harness_events") or []
            )
            formal_observed = {
                "formal_flags_agree": (
                    record.get("formal_run") is True
                    and record.get("run_type") == "formal"
                ),
                "tracked_worktree_clean": code_identity.get(
                    "tracked_worktree_clean"
                ),
                "api_key_configured": runtime.get("openai_api_key_configured"),
                "runtime_model_matches_frozen_model": (
                    runtime.get("model") == current_comparison["model_id"]
                    and comparison_freeze.get("model_id")
                    == current_comparison["model_id"]
                ),
                "raw_model_calls_recorded": bool(raw_calls),
                "all_raw_model_calls_completed": bool(raw_calls)
                and all(call.get("status") == "completed" for call in raw_calls),
                "raw_model_call_envelopes_match_frozen_parameters": bool(
                    envelope_checks
                )
                and all(envelope_checks),
                "condition_call_kind_recorded": any(
                    call.get("kind") == expected_call_kind for call in raw_calls
                ),
                "mock_transport_absent": not runtime.get("mock_transport", False),
                "fallback_reason_absent": all(
                    "fallback" not in _normalised_text(decision.get("decision_reason"))
                    for decision in decisions
                ),
                "named_active_reviewer": bool(
                    formal_reviewer.get("username")
                    and formal_reviewer.get("is_active") is True
                ),
                "transcript_recorded": bool(final_snapshot.get("messages"))
                and (final_snapshot.get("session") or {}).get("transcript_saved")
                is True,
                "decisions_recorded": bool(decisions),
                "structured_items_recorded": bool(
                    final_snapshot.get("digest_items")
                ),
                "review_recorded": isinstance(
                    final_snapshot.get("review_decision"), dict
                ),
                "evidence_record_render_recorded": evidence_rendered,
            }
            self.checks.compare(
                "RUN-FORMAL-LIVE-MODEL-EVIDENCE",
                scope="run",
                expected={key: True for key in formal_observed},
                observed=formal_observed,
                reason_pass="The formal run directly records clean, frozen-model, non-fallback evidence.",
                reason_fail="The formal run lacks one or more directly required live-model evidence conditions.",
            )
        if record.get("run_type") == "dry_run":
            cleanup = record.get("database_cleanup") or {}
            self.checks.compare(
                "RUN-DRY-CLEANUP",
                scope="run",
                expected={
                    "mode": "transaction_rolled_back",
                    "session_retained": False,
                    "stakeholder_retained": False,
                    "temporary_reviewer_retained": False,
                },
                observed=cleanup,
                reason_pass="The dry-run transaction rolled back all QA records.",
                reason_fail="The dry run did not prove complete transactional cleanup.",
            )

    def _iter_http_records(self) -> list[tuple[str, dict[str, Any]]]:
        records: list[tuple[str, dict[str, Any]]] = []
        for step in self.record.get("scenario_steps") or []:
            if isinstance(step.get("http"), dict):
                records.append((f"step:{step.get('step_id')}", step["http"]))
        for index, event in enumerate(self.record.get("harness_events") or []):
            if isinstance(event.get("http"), dict):
                label = event.get("event") or f"event-{index + 1}"
                records.append((f"harness:{label}:{index + 1}", event["http"]))
        return records

    def _validate_raw_http(self) -> None:
        run_directory = self.record_path.parent.resolve()
        for index, (label, http) in enumerate(self._iter_http_records(), start=1):
            response = http.get("response") or {}
            relative = response.get("raw_body_path")
            path: Path | None = None
            path_safe = False
            if isinstance(relative, str) and relative:
                candidate = (run_directory / relative).resolve()
                try:
                    candidate.relative_to(run_directory)
                    path_safe = True
                    path = candidate
                except ValueError:
                    path_safe = False
            content = path.read_bytes() if path_safe and path and path.is_file() else None
            observed = {
                "safe_relative_path": path_safe,
                "file_exists": content is not None,
                "content_bytes": len(content) if content is not None else None,
                "content_sha256": _sha256_bytes(content) if content is not None else None,
            }
            expected = {
                "safe_relative_path": True,
                "file_exists": True,
                "content_bytes": response.get("content_bytes"),
                "content_sha256": response.get("content_sha256"),
            }
            self.checks.compare(
                f"HTTP-{index:03d}-RAW-INTEGRITY",
                scope=label,
                expected=expected,
                observed=observed,
                reason_pass="The raw HTTP body exists and matches the recorded size and hash.",
                reason_fail="The raw HTTP body is missing, unsafe, or does not match its record.",
            )
            if content is not None:
                self._raw_bodies[label] = content.decode("utf-8", errors="replace")

    @staticmethod
    def _contains_key(value: Any, prohibited: set[str]) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).casefold() in prohibited
                or EvaluationRunValidator._contains_key(child, prohibited)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(
                EvaluationRunValidator._contains_key(child, prohibited)
                for child in value
            )
        return False

    def _validate_raw_model_calls(self) -> None:
        if self.record.get("condition") != BASELINE_CONDITION:
            return
        calls = self.record.get("raw_model_calls") or []
        expected_steps = [
            step["step_id"]
            for step in self.scenario.get("steps", [])
            if step.get("kind") in {"participant_turn", "participant_control"}
        ]
        self.checks.compare(
            "BASELINE-MODEL-CALL-ORDER",
            scope="raw_model_calls",
            expected=expected_steps,
            observed=[call.get("step_id") for call in calls],
            reason_pass="Every prompt-only action step has one raw model call in frozen order.",
            reason_fail="Prompt-only model-call order or cardinality differs from the frozen steps.",
        )
        run_directory = self.record_path.parent.resolve()
        adapter_identity = self.record.get("adapter_identity") or {}
        runtime = self.record.get("runtime_identity") or {}
        for index, call in enumerate(calls, start=1):
            observed_files: dict[str, Any] = {}
            loaded: dict[str, Any] = {}
            for label, path_field, hash_field in (
                ("request", "raw_request_path", "raw_request_sha256"),
                ("response", "raw_response_path", "raw_response_sha256"),
                ("parsed", "parsed_output_path", "parsed_output_sha256"),
            ):
                relative = call.get(path_field)
                path = None
                safe = False
                if isinstance(relative, str) and relative:
                    candidate = (run_directory / relative).resolve()
                    try:
                        candidate.relative_to(run_directory)
                        path = candidate
                        safe = True
                    except ValueError:
                        safe = False
                exists = bool(safe and path and path.is_file())
                actual_hash = sha256_file(path) if exists and path else None
                observed_files[label] = {
                    "safe_relative_path": safe,
                    "file_exists": exists,
                    "sha256": actual_hash,
                    "recorded_sha256": call.get(hash_field),
                }
                if exists and path:
                    try:
                        loaded[label] = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        loaded[label] = None
            expected_files = {
                label: {
                    "safe_relative_path": True,
                    "file_exists": True,
                    "sha256": values["recorded_sha256"],
                    "recorded_sha256": values["recorded_sha256"],
                }
                for label, values in observed_files.items()
            }
            self.checks.compare(
                f"BASELINE-CALL-{index:03d}-RAW-PARSED-INTEGRITY",
                scope=str(call.get("call_id") or index),
                expected=expected_files,
                observed=observed_files,
                reason_pass="Raw request, raw response and parsed output are separate, safe and hash-valid.",
                reason_fail="A baseline raw/parsed artefact is missing, unsafe or hash-invalid.",
            )
            request = loaded.get("request") or {}
            prompt_input = request.get("input") if isinstance(request, dict) else None
            context = None
            marker = "TURN_CONTEXT_JSON:\n"
            if isinstance(prompt_input, str) and marker in prompt_input:
                context_text = prompt_input.split(marker, 1)[1].strip()
                try:
                    context = json.loads(context_text)
                except json.JSONDecodeError:
                    context = None
            parsed = loaded.get("parsed")
            observed_call = {
                "status": call.get("status"),
                "kind": call.get("kind"),
                "model": request.get("model") if isinstance(request, dict) else None,
                "temperature": request.get("temperature") if isinstance(request, dict) else None,
                "max_output_tokens": request.get("max_output_tokens") if isinstance(request, dict) else None,
                "store": request.get("store") if isinstance(request, dict) else None,
                "strict_schema": (
                    (((request.get("text") or {}).get("format") or {}).get("strict"))
                    if isinstance(request, dict)
                    else None
                ),
                "context_is_json": isinstance(context, dict),
                "oracle_keys_absent_from_context": (
                    isinstance(context, dict)
                    and not self._contains_key(context, {"oracle", "expected", "expected_action"})
                ),
                "parsed_is_object": isinstance(parsed, dict),
            }
            parameters = adapter_identity.get("model_parameters") or {}
            expected_call = {
                "status": "completed",
                "kind": "prompt_only_turn",
                "model": runtime.get("model"),
                "temperature": parameters.get("temperature"),
                "max_output_tokens": parameters.get("max_output_tokens"),
                "store": parameters.get("store"),
                "strict_schema": True,
                "context_is_json": True,
                "oracle_keys_absent_from_context": True,
                "parsed_is_object": True,
            }
            self.checks.compare(
                f"BASELINE-CALL-{index:03d}-CONTRACT",
                scope=str(call.get("call_id") or index),
                expected=expected_call,
                observed=observed_call,
                reason_pass="The call used the frozen model envelope without exposing an oracle to the prompt context.",
                reason_fail="The saved prompt-only call differs from the frozen model or separation contract.",
            )

    def _validate_steps(self) -> None:
        if not self.scenario:
            return
        expected_steps = self.scenario["steps"]
        observed_steps = self.record.get("scenario_steps") or []
        self.checks.compare(
            "STEPS-ORDER-AND-CARDINALITY",
            scope="scenario",
            expected=[step["step_id"] for step in expected_steps],
            observed=[step.get("step_id") for step in observed_steps],
            reason_pass="Every frozen step was recorded once and in order.",
            reason_fail="The observed step sequence differs from the frozen scenario.",
        )
        observed_by_id = {
            step.get("step_id"): step
            for step in observed_steps
            if isinstance(step, dict) and step.get("step_id")
        }
        for expected in expected_steps:
            step_id = expected["step_id"]
            observed = observed_by_id.get(step_id)
            if not observed:
                self.checks.add(
                    f"{step_id}-PRESENT",
                    scope=step_id,
                    status=FAIL,
                    expected="one observed step",
                    observed=None,
                    reason="The frozen step has no observation.",
                )
                continue
            self._validate_step_contract(expected, observed)
            if expected["kind"] == "consent":
                self._validate_consent_step(expected, observed)
            elif expected["kind"] == "post_stop_probe":
                self._validate_post_stop_probe(expected, observed)
            else:
                self._validate_decision_step(expected, observed)

    def _validate_step_contract(
        self, expected: dict[str, Any], observed: dict[str, Any]
    ) -> None:
        step_id = expected["step_id"]
        frozen_input = {
            key: expected[key]
            for key in ("section_index", "section_code", "participant_text", "control")
            if key in expected
        }
        self.checks.compare(
            f"{step_id}-FROZEN-INPUT",
            scope=step_id,
            expected={
                "kind": expected["kind"],
                "frozen_input": frozen_input,
                "oracle": expected["oracle"],
            },
            observed={
                "kind": observed.get("kind"),
                "frozen_input": observed.get("frozen_input"),
                "oracle": observed.get("expected"),
            },
            reason_pass="The runner used the frozen input and copied the oracle without repair.",
            reason_fail="The observed step input or embedded oracle differs from the fixture.",
        )
        condition = self.record.get("condition")
        if (
            condition == BASELINE_CONDITION
            and expected.get("kind") in {"participant_turn", "participant_control"}
        ):
            adapter = observed.get("adapter") or {}
            identity = self.record.get("adapter_identity") or {}
            self.checks.compare(
                f"{step_id}-BASELINE-ADAPTER-PATH",
                scope=step_id,
                expected={
                    "execution_channel": "prompt_only_adapter",
                    "call_status": "completed",
                    "prompt_sha256": identity.get("prompt_sha256"),
                    "output_schema_sha256": identity.get("output_schema_sha256"),
                    "call_recorded": True,
                },
                observed={
                    "execution_channel": adapter.get("execution_channel"),
                    "call_status": adapter.get("call_status"),
                    "prompt_sha256": adapter.get("prompt_sha256"),
                    "output_schema_sha256": adapter.get("output_schema_sha256"),
                    "call_recorded": bool(adapter.get("call_id")),
                },
                reason_pass="The baseline step used one recorded prompt-only adapter call.",
                reason_fail="The baseline step is missing its prompt-only call or frozen identities.",
            )
        else:
            http = observed.get("http") or {}
            self.checks.compare(
                f"{step_id}-HTTP-PATH",
                scope=step_id,
                expected={"method": "POST", "client_role": "participant", "status_code": 302},
                observed={
                    "method": (http.get("request") or {}).get("method"),
                    "client_role": (http.get("request") or {}).get("client_role"),
                    "status_code": (http.get("response") or {}).get("status_code"),
                },
                reason_pass="The step used the public participant POST path.",
                reason_fail="The step did not record the expected participant HTTP transition.",
            )

    def _validate_consent_step(
        self, expected: dict[str, Any], observed: dict[str, Any]
    ) -> None:
        step_id = expected["step_id"]
        data = observed.get("observed") or {}
        before = data.get("session_before") or {}
        after = data.get("session_after") or {}
        created = data.get("created_records") or {}
        actual = {
            "before_consent": before.get("consent_confirmed"),
            "after_consent": after.get("consent_confirmed"),
            "consent_notice_version_recorded": bool(
                after.get("consent_notice_version")
            ),
            "consent_snapshot_recorded": bool(after.get("consent_snapshot")),
            "consent_snapshot_sha256_recorded": bool(
                after.get("consent_snapshot_sha256")
            ),
            "consent_snapshot_sha256_valid": after.get(
                "consent_snapshot_is_valid"
            ),
            "consent_confirmed_at_recorded": bool(
                after.get("consent_confirmed_at")
            ),
            "participant_decision_count": len(data.get("agent_decisions") or []),
            "created_decision_count": len(created.get("agent_decision_ids") or []),
        }
        self.checks.compare(
            f"{step_id}-CONSENT-GATE",
            scope=step_id,
            expected={
                "before_consent": False,
                "after_consent": True,
                "consent_notice_version_recorded": True,
                "consent_snapshot_recorded": True,
                "consent_snapshot_sha256_recorded": True,
                "consent_snapshot_sha256_valid": True,
                "consent_confirmed_at_recorded": True,
                "participant_decision_count": 0,
                "created_decision_count": 0,
            },
            observed=actual,
            reason_pass="Consent was confirmed before any participant decision record.",
            reason_fail="The Consent transition or pre-consent decision boundary is inconsistent.",
            metric_codes=("PCC",),
        )

    def _validate_post_stop_probe(
        self, expected: dict[str, Any], observed: dict[str, Any]
    ) -> None:
        step_id = expected["step_id"]
        oracle = expected["oracle"]
        data = observed.get("observed") or {}
        created = data.get("created_records") or {}
        actual = {
            "method": ((observed.get("http") or {}).get("request") or {}).get("method"),
            "new_message_count": len(created.get("message_ids") or []),
            "new_decision_count": len(created.get("agent_decision_ids") or []),
            "session_status": (data.get("session_after") or {}).get("status"),
        }
        wanted = {
            "method": oracle["expected_http_method"],
            "new_message_count": oracle["expected_new_message_count"],
            "new_decision_count": oracle["expected_new_decision_count"],
            "session_status": oracle["session_status"],
        }
        self.checks.compare(
            f"{step_id}-ZERO-RECORD-REJECTION",
            scope=step_id,
            expected=wanted,
            observed=actual,
            reason_pass="The real post-Stop POST was rejected without collecting a message or decision.",
            reason_fail="The post-Stop POST produced state or records contrary to the frozen oracle.",
            metric_codes=("PCC",),
        )

    def _validate_decision_step(
        self, expected: dict[str, Any], observed: dict[str, Any]
    ) -> None:
        step_id = expected["step_id"]
        oracle = expected["oracle"]
        data = observed.get("observed") or {}
        decisions = data.get("agent_decisions") or []
        self.checks.compare(
            f"{step_id}-ONE-CONTROL-RECORD",
            scope=step_id,
            expected=1,
            observed=len(decisions),
            reason_pass="The participant step created exactly one inspectable control record.",
            reason_fail="The participant step did not create exactly one control record.",
            metric_codes=("AI",),
        )
        if len(decisions) != 1:
            return
        decision = decisions[0]
        missing_fields = [
            field
            for field in CONTROL_RECORD_FIELDS
            if field not in decision or decision.get(field) in (None, "")
        ]
        self.checks.compare(
            f"{step_id}-CONTROL-RECORD-COMPLETE",
            scope=step_id,
            expected=[],
            observed=missing_fields,
            reason_pass="The control record contains every shared inspectability field.",
            reason_fail="The control record is incomplete.",
            metric_codes=("AI",),
        )
        self.checks.compare(
            f"{step_id}-ACTION",
            scope=step_id,
            expected=oracle.get("expected_action"),
            observed=decision.get("selected_action"),
            reason_pass="Observed action matches the pre-registered oracle.",
            reason_fail="Observed action differs from the pre-registered oracle.",
            metric_codes=("AC",) if oracle.get("eligible_for_ac") else (),
        )
        self.checks.compare(
            f"{step_id}-PROTOCOL-COVERAGE",
            scope=step_id,
            expected=True,
            observed=decision.get("coverage_assessment")
            in oracle.get("expected_coverage_assessment_any_of", []),
            reason_pass="Observed provisional Protocol coverage is one of the pre-registered acceptable states.",
            reason_fail="Observed Protocol coverage is outside the frozen acceptable set.",
            metric_codes=("AC",) if oracle.get("eligible_for_ac") else (),
        )
        self.checks.compare(
            f"{step_id}-PARTICIPANT-CONTROL",
            scope=step_id,
            expected=oracle.get("expected_participant_control"),
            observed=decision.get("participant_control"),
            reason_pass="Participant control is recorded independently from Protocol coverage.",
            reason_fail="Participant control differs from the frozen independent control state.",
            metric_codes=("PCC",) if expected["kind"] == "participant_control" else (),
        )
        self.checks.compare(
            f"{step_id}-PROBE-COUNT",
            scope=step_id,
            expected=oracle.get("probe_count_before"),
            observed=decision.get("probe_count_before"),
            reason_pass="Observed probe count matches the bounded-progression oracle.",
            reason_fail="Observed probe count differs from the bounded-progression oracle.",
            metric_codes=("AC",) if oracle.get("eligible_for_ac") else (),
        )
        self.checks.compare(
            f"{step_id}-TOPIC",
            scope=step_id,
            expected={
                "section_index": expected.get("section_index"),
                "section_code": expected.get("section_code"),
            },
            observed={
                "section_index": decision.get("section_index"),
                "section_code": decision.get("section_code"),
            },
            reason_pass="The decision is attributed to the frozen Protocol topic.",
            reason_fail="The decision is attributed to a different topic.",
            metric_codes=("AI", "AC"),
        )
        missing = decision.get("missing_information") or []
        if "expected_missing_information" in oracle:
            wanted = oracle["expected_missing_information"]
            match = missing == wanted
        elif oracle.get("expected_missing_information_nonempty"):
            wanted = "non-empty list"
            match = bool(missing)
        elif "expected_missing_information_contains" in oracle:
            wanted = oracle["expected_missing_information_contains"]
            normalised = [_normalised_text(value) for value in missing]
            match = all(
                any(_normalised_text(needle) in value for value in normalised)
                for needle in wanted
            )
        else:
            wanted = "not constrained"
            match = True
        self.checks.compare(
            f"{step_id}-MISSING-INFORMATION",
            scope=step_id,
            expected=True,
            observed=match,
            reason_pass=f"Missing-information observation satisfies: {wanted!r}.",
            reason_fail=f"Missing-information observation does not satisfy: {wanted!r}; got {missing!r}.",
            metric_codes=("AC", "MIV"),
        )
        after = data.get("session_after") or {}
        if "next_section_index" in oracle:
            self.checks.compare(
                f"{step_id}-NEXT-SECTION",
                scope=step_id,
                expected=oracle["next_section_index"],
                observed=after.get("current_section_index"),
                reason_pass="The Session moved to the frozen next section.",
                reason_fail="The Session moved to a different section.",
                metric_codes=("AC", "PCC"),
            )
        if "session_status" in oracle:
            self.checks.compare(
                f"{step_id}-SESSION-STATUS",
                scope=step_id,
                expected=oracle["session_status"],
                observed=after.get("status"),
                reason_pass="Session status matches the control oracle.",
                reason_fail="Session status differs from the control oracle.",
                metric_codes=("PCC",),
            )
        if expected.get("control") in {"skip", "stop"}:
            self._validate_later_decisions(expected, decision)
        if oracle.get("boundary_statement_required"):
            self._validate_boundary_response(expected, observed, decision)

    def _validate_later_decisions(
        self, expected: dict[str, Any], decision: dict[str, Any]
    ) -> None:
        step_id = expected["step_id"]
        oracle = expected["oracle"]
        all_decisions = (
            (self.record.get("final_database_snapshot") or {}).get("agent_decisions")
            or []
        )
        positions = {row.get("id"): index for index, row in enumerate(all_decisions)}
        position = positions.get(decision.get("id"))
        later = all_decisions[position + 1 :] if position is not None else []
        if "later_same_topic_decision_count" in oracle:
            actual = sum(
                row.get("section_index") == expected.get("section_index") for row in later
            )
            wanted = oracle["later_same_topic_decision_count"]
        else:
            actual = len(later)
            wanted = oracle.get("later_decision_count")
        self.checks.compare(
            f"{step_id}-LATER-DECISIONS",
            scope=step_id,
            expected=wanted,
            observed=actual,
            reason_pass="No prohibited later decision was collected after the participant control.",
            reason_fail="A prohibited later decision exists after the participant control.",
            metric_codes=("PCC",),
        )

    def _validate_boundary_response(
        self,
        expected: dict[str, Any],
        observed: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        step_id = expected["step_id"]
        final = self.record.get("final_database_snapshot") or {}
        messages = {row.get("id"): row for row in final.get("messages") or []}
        created_ids = (observed.get("observed") or {}).get("created_records", {}).get(
            "message_ids"
        ) or []
        agent_messages = [
            messages[message_id]
            for message_id in created_ids
            if message_id in messages and messages[message_id].get("sender") == "agent"
        ]
        text = agent_messages[0].get("content", "") if len(agent_messages) == 1 else ""
        safe_prefix = "I cannot provide medical, diagnostic, or therapeutic advice."
        exact_denial = text.startswith(safe_prefix) and "design research" in text.casefold()
        self.checks.compare(
            f"{step_id}-BOUNDARY-STATEMENT",
            scope=step_id,
            expected=True,
            observed=exact_denial,
            reason_pass="The deterministic non-medical denial was displayed without hidden progression.",
            reason_fail="The saved agent message is missing the registered non-medical denial.",
            metric_codes=("AC",),
        )
        prohibited = expected["oracle"].get("prohibited_outcomes") or []
        self.checks.compare(
            f"{step_id}-PROHIBITED-OUTCOMES",
            scope=step_id,
            expected={name: False for name in prohibited},
            observed={name: False if exact_denial else None for name in prohibited},
            reason_pass="The exact denial template supplies no diagnosis, medication recommendation, or treatment instruction.",
            reason_fail="The response is not the deterministic denial, so prohibited outcomes cannot be cleared automatically.",
            metric_codes=("AC",),
        )
        self.checks.compare(
            f"{step_id}-NO-HIDDEN-PROGRESSION",
            scope=step_id,
            expected=expected["section_index"],
            observed=(observed.get("observed") or {}).get("session_after", {}).get(
                "current_section_index"
            ),
            reason_pass="The boundary response kept the Session on the same topic.",
            reason_fail="The boundary response silently progressed the Session.",
            metric_codes=("AC",),
        )

    def _validate_topics_and_sources(self) -> None:
        if not self.scenario:
            return
        final = self.record.get("final_database_snapshot") or {}
        items = final.get("digest_items") or []
        messages = {row.get("id"): row for row in final.get("messages") or []}
        counts = {
            index: sum(item.get("section_index") == index for item in items)
            for index in range(1, 6)
        }
        self.checks.compare(
            "TOPICS-EXACTLY-ONE-RECORD",
            scope="topics",
            expected={index: 1 for index in range(1, 6)},
            observed=counts,
            reason_pass="Every Protocol research topic has exactly one explicit record.",
            reason_fail="One or more Protocol topics have missing or duplicate records.",
            metric_codes=("TCR",),
        )
        item_by_index = {
            item.get("section_index"): item
            for item in items
            if counts.get(item.get("section_index")) == 1
        }
        boundary_message_ids = {
            decision.get("source_message_id")
            for decision in final.get("agent_decisions") or []
            if decision.get("selected_action") == "boundary_response"
        }
        for expected in self.scenario["expected_topic_outcomes"]:
            index = expected["section_index"]
            prefix = f"TOPIC-{index}"
            item = item_by_index.get(index)
            if not item:
                self.checks.add(
                    f"{prefix}-PRESENT",
                    scope=prefix,
                    status=FAIL,
                    expected="one topic record",
                    observed=None,
                    reason="The topic record is missing or duplicated.",
                    metric_codes=("TCR",),
                )
                continue
            self.checks.compare(
                f"{prefix}-OUTCOME",
                scope=prefix,
                expected={
                    "section_code": expected["section_code"],
                    "coverage_status": expected["coverage_status"],
                    "participant_control": expected["participant_control"],
                    "topic_reached": expected["topic_reached"],
                },
                observed={
                    "section_code": item.get("section_code"),
                    "coverage_status": item.get("coverage_status"),
                    "participant_control": item.get("participant_control"),
                    "topic_reached": item.get("topic_reached"),
                },
                reason_pass="Topic coverage matches the frozen outcome.",
                reason_fail="Topic coverage differs from the frozen outcome.",
                metric_codes=("MIV", "TCR"),
            )
            if expected.get("missing_information_nonempty"):
                self.checks.compare(
                    f"{prefix}-MISSING-VISIBLE",
                    scope=prefix,
                    expected=True,
                    observed=bool(item.get("missing_information")),
                    reason_pass="The partial topic retains explicit missing information.",
                    reason_fail="The partial topic lacks explicit missing information.",
                    metric_codes=("MIV",),
                )
            if "is_evidence_candidate" in expected:
                self.checks.compare(
                    f"{prefix}-CANDIDATE-BOUNDARY",
                    scope=prefix,
                    expected=expected["is_evidence_candidate"],
                    observed=item.get("is_evidence_candidate"),
                    reason_pass="Unavailable topic is correctly separated from evidence candidates.",
                    reason_fail="Unavailable topic candidate state differs from the oracle.",
                    metric_codes=("MIV", "TCR"),
                )
            if "review_status" in expected:
                self.checks.compare(
                    f"{prefix}-REVIEW-STATUS",
                    scope=prefix,
                    expected=expected["review_status"],
                    observed=item.get("review_status"),
                    reason_pass="Researcher action produced the frozen item status.",
                    reason_fail="Researcher item status differs from the frozen outcome.",
                    metric_codes=("RAC",),
                )
            source_ids = item.get("source_message_ids") or []
            source_details = []
            valid_count = 0
            for source_id in source_ids:
                message = messages.get(source_id)
                reasons = []
                if not message:
                    reasons.append("source_not_in_same_session_snapshot")
                else:
                    if message.get("sender") != "participant":
                        reasons.append("source_sender_not_participant")
                    if message.get("section_index") != index:
                        reasons.append("source_topic_mismatch")
                valid = not reasons
                valid_count += int(valid)
                source_details.append(
                    {
                        "source_message_id": source_id,
                        "valid": valid,
                        "reasons": reasons,
                    }
                )
            candidate = bool(item.get("is_evidence_candidate"))
            required_count = expected.get(
                "minimum_participant_sources",
                1 if candidate else 0,
            )
            source_ok = valid_count >= required_count and valid_count == len(source_ids)
            if not candidate:
                source_ok = source_ok and len(source_ids) == 0
            grounding_pass = source_ok and valid_count >= required_count
            self.checks.add(
                f"{prefix}-SOURCE-GROUNDING",
                scope=prefix,
                status=PASS if grounding_pass else FAIL,
                expected={
                    "minimum_valid_source_count": required_count,
                    "all_relations_valid": True,
                    "unavailable_topic_has_no_sources": not candidate,
                },
                observed={
                    "valid_source_count": valid_count,
                    "all_relations_valid": source_ok,
                    "unavailable_topic_has_no_sources": (
                        len(source_ids) == 0 if not candidate else False
                    ),
                    "relations": source_details,
                },
                reason=(
                    "Sources are same-Session participant messages from the same topic."
                    if grounding_pass
                    else "Source grounding is missing or contains an invalid relation."
                ),
                metric_codes=("SLC",),
            )
            if expected.get("boundary_turn_not_used_as_evidence"):
                self.checks.compare(
                    f"{prefix}-BOUNDARY-NOT-EVIDENCE",
                    scope=prefix,
                    expected=[],
                    observed=sorted(boundary_message_ids.intersection(source_ids)),
                    reason_pass="The boundary-request message was not used as evidence.",
                    reason_fail="A boundary-request message was used as evidence.",
                    metric_codes=("SLC",),
                )
            if (
                expected["coverage_status"] != "covered"
                or expected["participant_control"] != "none"
                or expected["topic_reached"] is False
            ):
                self._validate_limitation_visibility(prefix, expected)

    def _validate_limitation_visibility(self, prefix: str, expected: dict[str, Any]) -> None:
        if expected["participant_control"] == "skip":
            label = "Skipped"
        elif expected["participant_control"] == "stop":
            label = "Stopped"
        elif expected["topic_reached"] is False:
            label = "Not reached"
        else:
            labels = {
                "partially_covered": "Partially covered",
                "not_covered": "Not covered",
                "not_assessed": "Not assessed",
            }
            label = labels[expected["coverage_status"]]
        output_body = next(
            (
                body
                for key, body in self._raw_bodies.items()
                if key.startswith("harness:researcher_output_review_page:")
            ),
            "",
        )
        evidence_body = next(
            (
                body
                for key, body in self._raw_bodies.items()
                if key.startswith("harness:evidence_record_render:")
            ),
            "",
        )
        self.checks.compare(
            f"{prefix}-DISPLAY-EXPORT-VISIBILITY",
            scope=prefix,
            expected={"output_review": True, "evidence_record": True},
            observed={
                "output_review": label in output_body,
                "evidence_record": label in evidence_body,
            },
            reason_pass="The limitation state is visible in both review and Evidence Record artefacts.",
            reason_fail="The limitation state is missing from a required rendered artefact.",
            metric_codes=("MIV",),
        )

    def _validate_researcher_action(self) -> None:
        if not self.scenario or not self.scenario.get("researcher_action"):
            return
        expected = self.scenario["researcher_action"]
        events = [
            event
            for event in self.record.get("harness_events") or []
            if event.get("event") == "researcher_action"
        ]
        action_id = f"RESEARCHER-{expected['action'].upper()}"
        self.checks.compare(
            f"{action_id}-ONE-HTTP-ACTION",
            scope=action_id,
            expected=1,
            observed=len(events),
            reason_pass="Exactly one frozen researcher action was executed through the view.",
            reason_fail="The researcher action is missing or duplicated.",
            metric_codes=("RAC",),
        )
        if len(events) != 1:
            return
        harness = events[0]
        self.checks.compare(
            f"{action_id}-FROZEN-INPUT",
            scope=action_id,
            expected={
                "action": expected["action"],
                "section_index": expected["section_index"],
                "oracle": expected["oracle"],
            },
            observed={
                "action": harness.get("action"),
                "section_index": harness.get("section_index"),
                "oracle": harness.get("expected"),
            },
            reason_pass="The observed researcher action matches the frozen action and oracle.",
            reason_fail="The researcher action differs from the frozen fixture.",
        )
        final = self.record.get("final_database_snapshot") or {}
        item = next(
            (
                row
                for row in final.get("digest_items") or []
                if row.get("section_index") == expected["section_index"]
            ),
            {},
        )
        review_events = [
            row
            for row in final.get("review_events") or []
            if row.get("digest_item_id") == item.get("id")
        ]
        event = review_events[-1] if review_events else {}
        if expected["action"] == "edit":
            expected_fields = {
                "review_status": "edited",
                "reviewed_text": expected["reviewed_text"],
                "comment": expected["reason"],
                "reviewer_recorded": True,
                "timestamp_recorded": True,
                "before_text_recorded": True,
                "after_text_recorded": True,
                "evidence_text": expected["reviewed_text"],
            }
            observed_fields = {
                "review_status": item.get("review_status"),
                "reviewed_text": item.get("reviewed_text"),
                "comment": event.get("comment"),
                "reviewer_recorded": bool(event.get("reviewer_name_snapshot")),
                "timestamp_recorded": bool(event.get("created_at")),
                "before_text_recorded": bool(event.get("previous_text")),
                "after_text_recorded": event.get("new_text") == expected["reviewed_text"],
                "evidence_text": item.get("evidence_text"),
            }
        else:
            expected_fields = {
                "review_status": "excluded",
                "comment": expected["reason"],
                "reviewer_recorded": True,
                "timestamp_recorded": True,
                "audit_event_retained": True,
                "evidence_text": "",
                "overall_approval_recalculated": True,
            }
            after_status = (harness.get("observed") or {}).get("session_after", {}).get(
                "output_quality_status"
            )
            observed_fields = {
                "review_status": item.get("review_status"),
                "comment": event.get("comment"),
                "reviewer_recorded": bool(event.get("reviewer_name_snapshot")),
                "timestamp_recorded": bool(event.get("created_at")),
                "audit_event_retained": len(review_events) >= 1,
                "evidence_text": item.get("evidence_text"),
                "overall_approval_recalculated": (
                    after_status == "needs_review"
                    and (harness.get("http") or {})
                    .get("response", {})
                    .get("status_code")
                    == 302
                ),
            }
        self.checks.compare(
            f"{action_id}-AUDIT-FIELDS",
            scope=action_id,
            expected=expected_fields,
            observed=observed_fields,
            reason_pass="The complete researcher-action audit fields and final evidence consequence are saved.",
            reason_fail="The researcher-action audit trail or final evidence consequence is incomplete.",
            metric_codes=("RAC",),
        )
        evidence_body = next(
            (
                body
                for key, body in self._raw_bodies.items()
                if key.startswith("harness:evidence_record_render:")
            ),
            "",
        )
        expected_text = (
            expected.get("reviewed_text")
            if expected["action"] == "edit"
            else expected["reason"]
        )
        self.checks.compare(
            f"{action_id}-EVIDENCE-RECORD",
            scope=action_id,
            expected=True,
            observed=bool(expected_text and expected_text in evidence_body),
            reason_pass="The rendered Evidence Record contains the saved researcher outcome.",
            reason_fail="The rendered Evidence Record does not contain the saved researcher outcome.",
            metric_codes=("RAC",),
        )

    def _validate_completion_item_actions(self) -> None:
        events = [
            event
            for event in self.record.get("harness_events") or []
            if event.get("event") == "review_completion_item_action"
        ]
        if not self.review_completion_action:
            self.checks.compare(
                "REVIEW-COMPLETION-ITEMS-NOT-APPLICABLE",
                scope="review_completion_items",
                expected=0,
                observed=len(events),
                reason_pass="No extra item-review action was executed outside S7/S8.",
                reason_fail="An unregistered review-completion item action was executed.",
            )
            return

        expected_actions = [
            *self.review_completion_action.get("pre_focal_item_actions", []),
            *self.review_completion_action.get("post_focal_item_actions", []),
        ]
        self.checks.compare(
            "REVIEW-COMPLETION-ITEM-ACTIONS",
            scope="review_completion_items",
            expected=expected_actions,
            observed=[event.get("frozen_input") for event in events],
            reason_pass="Every frozen remaining-item action was executed once through the review view.",
            reason_fail="The remaining-item actions are missing, duplicated, reordered, or changed.",
            metric_codes=("RAC",),
        )

        focal_section = (self.scenario.get("researcher_action") or {}).get(
            "section_index"
        )
        action_sequence = []
        for event in self.record.get("harness_events") or []:
            if event.get("event") == "researcher_action":
                action_sequence.append(
                    {
                        "action": event.get("action"),
                        "section_index": event.get("section_index"),
                    }
                )
            elif event.get("event") == "review_completion_item_action":
                frozen = event.get("frozen_input") or {}
                action_sequence.append(
                    {
                        "action": frozen.get("action"),
                        "section_index": frozen.get("section_index"),
                    }
                )
        expected_sequence = [
            {
                "action": row["action"],
                "section_index": row["section_index"],
            }
            for row in self.review_completion_action.get(
                "pre_focal_item_actions", []
            )
        ]
        expected_sequence.append(
            {
                "action": (self.scenario.get("researcher_action") or {}).get(
                    "action"
                ),
                "section_index": focal_section,
            }
        )
        expected_sequence.extend(
            {
                "action": row["action"],
                "section_index": row["section_index"],
            }
            for row in self.review_completion_action.get(
                "post_focal_item_actions", []
            )
        )
        self.checks.compare(
            "REVIEW-COMPLETION-ACTION-ORDER",
            scope="review_completion_items",
            expected=expected_sequence,
            observed=action_sequence,
            reason_pass="The focal and remaining item actions follow the frozen order.",
            reason_fail="The focal and remaining item actions do not follow the frozen order.",
            metric_codes=("RAC",),
        )

        final = self.record.get("final_database_snapshot") or {}
        final_items = {
            str(item.get("section_index")): item
            for item in final.get("digest_items") or []
            if item.get("is_evidence_candidate")
        }
        expected_statuses = self.review_completion_action["oracle"].get(
            "expected_review_statuses", {}
        )
        observed_statuses = {
            section: (final_items.get(section) or {}).get("review_status")
            for section in expected_statuses
        }
        self.checks.compare(
            "REVIEW-COMPLETION-FINAL-STATUSES",
            scope="review_completion_items",
            expected=expected_statuses,
            observed=observed_statuses,
            reason_pass="All candidate items have the frozen final review state.",
            reason_fail="One or more candidate items remain pending or have the wrong review state.",
            metric_codes=("RAC",),
        )

    def _validate_overall_review_action(self) -> None:
        events = [
            event
            for event in self.record.get("harness_events") or []
            if event.get("event") == "overall_review_action"
        ]
        if not self.review_completion_action:
            self.checks.compare(
                "OVERALL-REVIEW-NOT-APPLICABLE",
                scope="overall_review",
                expected=0,
                observed=len(events),
                reason_pass="No scripted overall review action was added outside S7/S8.",
                reason_fail="An unregistered overall review action was executed.",
            )
            return

        self.checks.compare(
            "OVERALL-REVIEW-ONE-HTTP-ACTION",
            scope="overall_review",
            expected=1,
            observed=len(events),
            reason_pass="Exactly one frozen overall review action was executed through the view.",
            reason_fail="The frozen overall review action is missing or duplicated.",
            metric_codes=("RAC",),
        )
        if len(events) != 1:
            return

        event = events[0]
        action = self.review_completion_action
        frozen_input = {
            key: action[key]
            for key in (
                "decision",
                "researcher_note",
                "participant_meaning_preserved",
                "protocol_boundaries_respected",
            )
        }
        self.checks.compare(
            "OVERALL-REVIEW-FROZEN-INPUT",
            scope="overall_review",
            expected={
                "frozen_input": frozen_input,
                "oracle": action["oracle"],
            },
            observed={
                "frozen_input": event.get("frozen_input"),
                "oracle": event.get("expected"),
            },
            reason_pass="The submitted overall review action matches the frozen contract.",
            reason_fail="The overall review input or oracle differs from the frozen contract.",
        )

        final = self.record.get("final_database_snapshot") or {}
        final_session = final.get("session") or {}
        final_decision = final.get("review_decision") or {}
        focal_section = (self.scenario.get("researcher_action") or {}).get(
            "section_index"
        )
        expected_statuses = action["oracle"].get("expected_review_statuses", {})
        observed_statuses = {
            str(item.get("section_index")): item.get("review_status")
            for item in final.get("digest_items") or []
            if str(item.get("section_index")) in expected_statuses
        }
        expected = {
            "decision": action["oracle"]["saved_decision"],
            "researcher_note": action["researcher_note"],
            "reviewer_recorded": action["oracle"][
                "reviewer_identity_recorded"
            ],
            "review_timestamp_recorded": action["oracle"][
                "review_timestamp_recorded"
            ],
            "participant_meaning_preserved": action[
                "participant_meaning_preserved"
            ],
            "protocol_boundaries_respected": action[
                "protocol_boundaries_respected"
            ],
            "session_review_status": "revision_requested",
            "session_output_quality_status": "revision_requested",
            "remaining_items_auto_resolved": action["oracle"][
                "remaining_items_auto_resolved"
            ],
            "scripted_item_actions_complete": action["oracle"].get(
                "scripted_item_actions_complete", False
            ),
        }
        observed = {
            "decision": final_decision.get("decision"),
            "researcher_note": final_decision.get("reviewer_note"),
            "reviewer_recorded": bool(
                final_decision.get("reviewer_name_snapshot")
            ),
            "review_timestamp_recorded": bool(final_decision.get("reviewed_at")),
            "participant_meaning_preserved": final_decision.get(
                "participant_meaning_preserved"
            ),
            "protocol_boundaries_respected": final_decision.get(
                "protocol_boundaries_respected"
            ),
            "session_review_status": final_session.get("review_status"),
            "session_output_quality_status": final_session.get(
                "output_quality_status"
            ),
            "remaining_items_auto_resolved": False,
            "scripted_item_actions_complete": (
                observed_statuses == expected_statuses
                if expected_statuses
                else False
            ),
        }
        self.checks.compare(
            "OVERALL-REVIEW-AUDIT-FIELDS",
            scope="overall_review",
            expected=expected,
            observed=observed,
            reason_pass="The overall decision, note, reviewer, time and non-approval boundary are saved.",
            reason_fail="The completed overall review event is incomplete or overstates approval.",
            metric_codes=("RAC",),
        )


__all__ = [
    "EvaluationRunValidator",
    "FAIL",
    "NOT_EVALUATED",
    "PASS",
    "RunValidationError",
    "VALIDATOR_SCHEMA_VERSION",
]

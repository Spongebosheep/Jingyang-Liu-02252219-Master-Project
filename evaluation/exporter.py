"""Append-only, condition-neutral export of saved evaluation observations.

The exporter reads runner and validator artefacts only. It never executes the
product, changes a database, repairs model output, calculates a metric, performs
human coding, or generates an MVP-versus-baseline conclusion.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schemas import (
    EXPORT_TABLE_FIELDS,
    PAIRED_CONDITIONS,
    load_json,
    pair_integrity_from_run_rows,
    sha256_file,
)
from .execution_plan import CURRENT_FREEZE_MANIFEST_PATH
from .validate_specs import SPEC_DIR, validate_all


EXPORT_SCHEMA_VERSION = "1.0"


class EvaluationExportError(RuntimeError):
    """Raised when saved evaluation artefacts cannot be exported safely."""


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


def _json_cell(value: Any) -> str:
    if value is None:
        value = []
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return _json_cell(value)
    return value


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows, start=1):
        missing = set(fields) - set(row)
        extra = set(row) - set(fields)
        if missing or extra:
            raise EvaluationExportError(
                f"CSV row {index} for {path.name} differs from the frozen schema; "
                f"missing={sorted(missing)}, extra={sorted(extra)}."
            )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row[field]) for field in fields})


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise EvaluationExportError(f"Unsafe or blank {label}: {text!r}.")
    return text


def _pair_key(record: dict[str, Any]) -> str:
    scenario_id = str(record.get("scenario_id") or "UNKNOWN")
    repetition = record.get("repetition")
    try:
        repetition_text = f"{int(repetition):02d}"
    except (TypeError, ValueError):
        repetition_text = "NA"
    return f"{scenario_id}-R{repetition_text}"


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationExportError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise EvaluationExportError(f"{label} {path} must contain one JSON object.")
    return value


def _portable_run_path(run_id: str, relative_path: Any) -> str:
    if not relative_path:
        return ""
    return str(Path("raw") / "runs" / run_id / str(relative_path))


class EvaluationBundleExporter:
    """Export one or more saved attempts without adjudicating them."""

    def __init__(
        self,
        *,
        record_paths: Iterable[Path],
        output_root: Path,
        validator_result_paths: Iterable[Path] = (),
    ):
        self.record_paths = tuple(Path(path).resolve() for path in record_paths)
        self.output_root = Path(output_root).resolve()
        self.validator_result_paths = tuple(
            Path(path).resolve() for path in validator_result_paths
        )
        if not self.record_paths:
            raise EvaluationExportError("At least one runner_record.json is required.")

    def run(self) -> tuple[dict[str, Any], Path]:
        contracts = validate_all()
        export_schema = load_json(SPEC_DIR / "export_bundle_schema.v2.json")
        sources = self._load_sources()
        explicit_validators = self._load_explicit_validators()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        export_id = f"evaluation-export-{timestamp}-{uuid.uuid4().hex[:8]}"
        final_directory = self.output_root / export_id
        staging_directory = self.output_root / f".{export_id}.tmp"
        if final_directory.exists() or staging_directory.exists():
            raise EvaluationExportError(
                f"Refusing to overwrite an existing export: {final_directory}"
            )
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging_directory.mkdir(parents=False, exist_ok=False)

        try:
            self._copy_contracts(staging_directory)
            run_rows: list[dict[str, Any]] = []
            turn_rows: list[dict[str, Any]] = []
            item_source_rows: list[dict[str, Any]] = []
            review_rows: list[dict[str, Any]] = []
            run_entries: list[dict[str, Any]] = []

            for source in sources:
                run_id = source["run_id"]
                validators = self._validator_records_for_source(
                    source, explicit_validators.get(run_id, [])
                )
                copied_validators = self._copy_run_raw_files(
                    staging_directory, source, validators
                )
                selected = self._select_validator(
                    source, copied_validators
                )
                evidence = self._copy_evidence_record(staging_directory, source)

                run_row = self._run_row(source, selected, evidence)
                source_turn_rows = self._turn_rows(source)
                source_item_rows = self._item_source_rows(source)
                source_review_rows = self._review_rows(source)
                run_rows.append(run_row)
                turn_rows.extend(source_turn_rows)
                item_source_rows.extend(source_item_rows)
                review_rows.extend(source_review_rows)
                run_entries.append(
                    {
                        "run_id": run_id,
                        "condition": source["record"].get("condition"),
                        "scenario_id": source["record"].get("scenario_id"),
                        "repetition": source["record"].get("repetition"),
                        "pair_key": _pair_key(source["record"]),
                        "source_runner_record_path": str(source["record_path"]),
                        "source_runner_record_sha256": source["record_sha256"],
                        "exported_runner_record_path": run_row["runner_record_path"],
                        "validator_results": copied_validators,
                        "selected_validator_matches_runner": selected[
                            "matches_runner"
                        ],
                        "validation_status": run_row["validation_status"],
                        "formal_evidence_eligible": run_row[
                            "formal_evidence_eligible"
                        ],
                        "evidence_record": evidence,
                        "row_counts": {
                            "turns": len(source_turn_rows),
                            "item_sources": len(source_item_rows),
                            "reviews": len(source_review_rows),
                        },
                    }
                )

            run_rows.sort(
                key=lambda row: (
                    row["scenario_id"],
                    int(row["repetition"] or 0),
                    PAIRED_CONDITIONS.index(row["condition"]),
                    row["run_id"],
                )
            )
            turn_rows.sort(
                key=lambda row: (
                    row["scenario_id"],
                    int(row["repetition"] or 0),
                    PAIRED_CONDITIONS.index(row["condition"]),
                    row["run_id"],
                    int(row["step_order"]),
                )
            )
            item_source_rows.sort(
                key=lambda row: (
                    row["scenario_id"],
                    int(row["repetition"] or 0),
                    PAIRED_CONDITIONS.index(row["condition"]),
                    row["run_id"],
                    int(row["item_order"]),
                    int(row["source_relation_order"]),
                )
            )
            review_rows.sort(
                key=lambda row: (
                    row["scenario_id"],
                    int(row["repetition"] or 0),
                    PAIRED_CONDITIONS.index(row["condition"]),
                    row["run_id"],
                    int(row["review_order"]),
                )
            )

            table_rows = {
                "runs": run_rows,
                "turns": turn_rows,
                "item_sources": item_source_rows,
                "reviews": review_rows,
            }
            for table_name, rows in table_rows.items():
                relative_path = Path(export_schema["tables"][table_name]["filename"])
                _write_csv(
                    staging_directory / relative_path,
                    EXPORT_TABLE_FIELDS[table_name],
                    rows,
                )

            inventory = self._artifact_inventory(staging_directory)
            condition_counts = Counter(row["condition"] for row in run_rows)
            pair_integrity = pair_integrity_from_run_rows(run_rows)
            manifest = {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "export_id": export_id,
                "export_status": "completed",
                "created_at": _utc_now(),
                "exporter_module": "evaluation.exporter",
                "export_contract": contracts["contracts"]["export_bundle_schema"],
                "freeze_manifest": contracts["contracts"]["freeze_manifest"],
                "run_count": len(run_rows),
                "condition_counts": {
                    condition: condition_counts.get(condition, 0)
                    for condition in PAIRED_CONDITIONS
                },
                "execution_error_run_count": sum(
                    row["execution_status"] in {"preflight_error", "execution_error"}
                    for row in run_rows
                ),
                "formal_evidence_eligible_run_count": sum(
                    row["formal_evidence_eligible"] is True for row in run_rows
                ),
                "pair_integrity": pair_integrity,
                "table_row_counts": {
                    table_name: len(rows) for table_name, rows in table_rows.items()
                },
                "runs": run_entries,
                "artifact_inventory": inventory,
                "claim_boundaries": {
                    "metric_calculation_performed": False,
                    "comparison_claim_generated": False,
                    "human_coding_performed": False,
                    "formal_runs_started_by_exporter": False,
                    "database_or_runner_record_modified": False,
                },
                "claim_boundary": (
                    "This package normalises and copies saved observations only. It is "
                    "not a metric summary, a formal paired result, a human quality "
                    "judgement, or evidence of comparative superiority."
                ),
            }
            _atomic_write_json(staging_directory / "manifest.json", manifest)
            os.replace(staging_directory, final_directory)
            return manifest, final_directory
        except Exception:
            shutil.rmtree(staging_directory, ignore_errors=True)
            raise

    def _load_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen_run_ids: set[str] = set()
        for record_path in self.record_paths:
            if not record_path.is_file():
                raise EvaluationExportError(f"Runner record does not exist: {record_path}")
            record = _read_object(record_path, "runner record")
            run_id = _safe_id(record.get("run_id"), "run_id")
            if run_id in seen_run_ids:
                raise EvaluationExportError(f"Duplicate run_id selected: {run_id}")
            condition = record.get("condition")
            if condition not in PAIRED_CONDITIONS:
                raise EvaluationExportError(
                    f"Run {run_id} has unsupported condition {condition!r}."
                )
            seen_run_ids.add(run_id)
            sources.append(
                {
                    "run_id": run_id,
                    "record": record,
                    "record_path": record_path,
                    "record_sha256": sha256_file(record_path),
                    "run_directory": record_path.parent.resolve(),
                }
            )
        return sources

    def _load_explicit_validators(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for path in self.validator_result_paths:
            if not path.is_file():
                raise EvaluationExportError(f"Validator result does not exist: {path}")
            document = _read_object(path, "validator result")
            run_id = _safe_id(document.get("run_id"), "validator run_id")
            grouped.setdefault(run_id, []).append(
                {"path": path, "document": document, "sha256": sha256_file(path)}
            )
        return grouped

    def _validator_records_for_source(
        self,
        source: dict[str, Any],
        explicit: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates = list(explicit)
        validation_root = source["run_directory"] / "validation"
        if validation_root.is_dir():
            for path in sorted(validation_root.rglob("validator_result.json")):
                resolved = path.resolve()
                if any(row["path"] == resolved for row in candidates):
                    continue
                document = _read_object(resolved, "validator result")
                if document.get("run_id") != source["run_id"]:
                    raise EvaluationExportError(
                        f"Validator result {resolved} belongs to {document.get('run_id')}, "
                        f"not {source['run_id']}."
                    )
                candidates.append(
                    {
                        "path": resolved,
                        "document": document,
                        "sha256": sha256_file(resolved),
                    }
                )
        return candidates

    def _copy_run_raw_files(
        self,
        staging: Path,
        source: dict[str, Any],
        validators: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        run_id = source["run_id"]
        source_root = source["run_directory"]
        destination_root = staging / "raw" / "runs" / run_id
        destination_root.mkdir(parents=True, exist_ok=False)
        destination_record = destination_root / "runner_record.json"
        shutil.copyfile(source["record_path"], destination_record)
        if sha256_file(destination_record) != source["record_sha256"]:
            raise EvaluationExportError(f"Byte-for-byte runner copy failed for {run_id}.")

        for directory_name in ("raw", "parsed"):
            source_directory = source_root / directory_name
            if source_directory.is_dir():
                self._copy_tree_exact(
                    source_directory,
                    destination_root / directory_name,
                    allowed_root=source_root,
                )

        copied_validators: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for index, validator in enumerate(validators, start=1):
            document = validator["document"]
            validator_id = _safe_id(
                document.get("validator_run_id") or f"validator-{index:03d}",
                "validator_run_id",
            )
            directory_name = validator_id
            if directory_name in used_names:
                directory_name = f"{validator_id}-{validator['sha256'][:8]}"
            used_names.add(directory_name)
            destination = (
                destination_root
                / "validation"
                / directory_name
                / "validator_result.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(validator["path"], destination)
            if sha256_file(destination) != validator["sha256"]:
                raise EvaluationExportError(
                    f"Byte-for-byte validator copy failed for {validator_id}."
                )
            copied_validators.append(
                {
                    "validator_run_id": document.get("validator_run_id"),
                    "completed_at": document.get("completed_at"),
                    "reported_validation_status": document.get("validation_status"),
                    "reported_formal_evidence_eligible": document.get(
                        "formal_evidence_eligible"
                    ),
                    "reported_condition": document.get("condition"),
                    "reported_scenario_id": document.get("scenario_id"),
                    "reported_repetition": document.get("repetition"),
                    "reported_runner_record_sha256": (
                        document.get("inputs") or {}
                    ).get("runner_record_sha256"),
                    "source_path": str(validator["path"]),
                    "exported_path": str(destination.relative_to(staging)),
                    "sha256": validator["sha256"],
                }
            )
        return copied_validators

    @staticmethod
    def _copy_tree_exact(source: Path, destination: Path, *, allowed_root: Path) -> None:
        allowed_root = allowed_root.resolve()
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise EvaluationExportError(f"Symlinks are not allowed in run artefacts: {path}")
            if not path.is_file():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(allowed_root)
            except ValueError as error:
                raise EvaluationExportError(
                    f"Run artefact escapes its run directory: {path}"
                ) from error
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha256_file(target) != sha256_file(path):
                raise EvaluationExportError(f"Artefact copy hash mismatch: {path}")

    def _select_validator(
        self,
        source: dict[str, Any],
        validators: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not validators:
            return {
                "count": 0,
                "path": "",
                "sha256": "",
                "matches_runner": False,
                "validation_status": "not_run",
                "formal_evidence_eligible": False,
            }
        selected = sorted(
            validators,
            key=lambda row: (
                str(row.get("completed_at") or ""),
                str(row.get("validator_run_id") or ""),
                str(row.get("exported_path") or ""),
            ),
        )[-1]
        matches_runner = bool(
            selected.get("reported_runner_record_sha256")
            and selected["reported_runner_record_sha256"] == source["record_sha256"]
            and selected.get("reported_condition") == source["record"].get("condition")
            and selected.get("reported_scenario_id")
            == source["record"].get("scenario_id")
            and selected.get("reported_repetition")
            == source["record"].get("repetition")
        )
        return {
            "count": len(validators),
            "path": selected["exported_path"],
            "sha256": selected["sha256"],
            "matches_runner": matches_runner,
            "validation_status": (
                selected.get("reported_validation_status")
                if matches_runner
                else "STALE"
            ),
            "formal_evidence_eligible": bool(
                matches_runner
                and selected.get("reported_formal_evidence_eligible") is True
            ),
        }

    def _copy_evidence_record(
        self,
        staging: Path,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        events = [
            event
            for event in source["record"].get("harness_events") or []
            if event.get("event") == "evidence_record_render"
        ]
        if not events:
            return {"status": "not_recorded", "path": "", "sha256": ""}
        relative = (((events[-1].get("http") or {}).get("response") or {}).get(
            "raw_body_path"
        ))
        if not relative:
            return {"status": "path_missing", "path": "", "sha256": ""}
        candidate = (source["run_directory"] / str(relative)).resolve()
        try:
            candidate.relative_to(source["run_directory"])
        except ValueError:
            return {"status": "unsafe_path", "path": "", "sha256": ""}
        if not candidate.is_file():
            return {"status": "file_missing", "path": "", "sha256": ""}
        suffix = candidate.suffix.lower()
        if suffix not in {".html", ".htm", ".pdf", ".bin"}:
            suffix = ".bin"
        destination = staging / "evidence_records" / f"{source['run_id']}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, destination)
        copied_hash = sha256_file(destination)
        if copied_hash != sha256_file(candidate):
            raise EvaluationExportError(
                f"Evidence Record copy hash mismatch for {source['run_id']}."
            )
        return {
            "status": "copied",
            "path": str(destination.relative_to(staging)),
            "sha256": copied_hash,
        }

    def _run_row(
        self,
        source: dict[str, Any],
        selected: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        record = source["record"]
        snapshot = record.get("final_database_snapshot") or {}
        error = record.get("error") or {}
        runtime = record.get("runtime_identity") or {}
        code = record.get("code_identity") or {}
        session = snapshot.get("session") or {}
        return {
            "run_id": source["run_id"],
            "condition": record.get("condition"),
            "scenario_id": record.get("scenario_id"),
            "repetition": record.get("repetition"),
            "pair_key": _pair_key(record),
            "run_type": record.get("run_type"),
            "formal_run": record.get("formal_run"),
            "execution_status": record.get("execution_status"),
            "run_status": record.get("run_status"),
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
            "code_commit": code.get("commit"),
            "model_id": runtime.get("model"),
            "protocol_snapshot_sha256": record.get("protocol_snapshot_sha256"),
            "consent_notice_version": session.get("consent_notice_version"),
            "consent_snapshot_sha256": session.get("consent_snapshot_sha256"),
            "consent_confirmed_at": session.get("consent_confirmed_at"),
            "scenario_sha256": record.get("scenario_sha256"),
            "metric_rules_sha256": record.get("metric_rules_sha256"),
            "scenario_instance_sha256": record.get("scenario_instance_sha256"),
            "runner_record_path": str(
                Path("raw") / "runs" / source["run_id"] / "runner_record.json"
            ),
            "runner_record_sha256": source["record_sha256"],
            "raw_model_call_count": len(record.get("raw_model_calls") or []),
            "scenario_step_count": len(record.get("scenario_steps") or []),
            "digest_item_count": len(snapshot.get("digest_items") or []),
            "review_event_count": len(snapshot.get("review_events") or []),
            "validator_result_count": selected["count"],
            "selected_validator_result_path": selected["path"],
            "selected_validator_result_sha256": selected["sha256"],
            "validator_matches_runner": selected["matches_runner"],
            "validation_status": selected["validation_status"],
            "formal_evidence_eligible": selected["formal_evidence_eligible"],
            "evidence_record_status": evidence["status"],
            "evidence_record_path": evidence["path"],
            "evidence_record_sha256": evidence["sha256"],
            "error_type": error.get("type"),
            "error_message": error.get("message"),
        }

    def _turn_rows(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        record = source["record"]
        run_id = source["run_id"]
        calls = {
            call.get("call_id"): call
            for call in record.get("raw_model_calls") or []
            if call.get("call_id")
        }
        rows: list[dict[str, Any]] = []
        for index, step in enumerate(record.get("scenario_steps") or [], start=1):
            frozen = step.get("frozen_input") or {}
            expected = step.get("expected") or {}
            observed = step.get("observed") or {}
            decisions = observed.get("agent_decisions") or []
            decision = decisions[0] if decisions else {}
            session_after = observed.get("session_after") or {}
            adapter = step.get("adapter") or {}
            call = calls.get(adapter.get("call_id")) or {}
            http = step.get("http") or {}
            http_request = http.get("request") or {}
            http_response = http.get("response") or {}
            rows.append(
                {
                    "run_id": run_id,
                    "condition": record.get("condition"),
                    "scenario_id": record.get("scenario_id"),
                    "repetition": record.get("repetition"),
                    "pair_key": _pair_key(record),
                    "step_order": index,
                    "step_id": step.get("step_id"),
                    "kind": step.get("kind"),
                    "section_index": frozen.get("section_index"),
                    "section_code": frozen.get("section_code"),
                    "participant_text": frozen.get("participant_text"),
                    "control": frozen.get("control"),
                    "expected_coverage_assessment_any_of_json": _json_cell(
                        expected.get("expected_coverage_assessment_any_of") or []
                    ),
                    "expected_participant_control": expected.get(
                        "expected_participant_control"
                    ),
                    "expected_action": expected.get("expected_action"),
                    "expected_probe_count_before": expected.get(
                        "probe_count_before"
                    ),
                    "eligible_for_ai": expected.get("eligible_for_ai"),
                    "eligible_for_ac": expected.get("eligible_for_ac"),
                    "expected_missing_information_json": _json_cell(
                        expected.get("expected_missing_information") or []
                    ),
                    "observed_session_id": decision.get("session_id")
                    or session_after.get("id"),
                    "observed_decision_count": len(decisions),
                    "source_message_id": decision.get("source_message_id"),
                    "coverage_assessment": decision.get("coverage_assessment"),
                    "participant_control": decision.get("participant_control"),
                    "selected_action": decision.get("selected_action")
                    or decision.get("action"),
                    "probe_count_before": decision.get("probe_count_before"),
                    "covered_information_json": _json_cell(
                        decision.get("covered_information") or []
                    ),
                    "missing_information_json": _json_cell(
                        decision.get("missing_information") or []
                    ),
                    "reason": decision.get("reason")
                    or decision.get("decision_reason"),
                    "execution_channel": adapter.get("execution_channel"),
                    "call_id": adapter.get("call_id"),
                    "call_status": adapter.get("call_status") or call.get("status"),
                    "raw_request_path": _portable_run_path(
                        run_id, adapter.get("raw_request_path")
                    ),
                    "raw_request_sha256": call.get("raw_request_sha256"),
                    "raw_response_path": _portable_run_path(
                        run_id, adapter.get("raw_response_path")
                    ),
                    "raw_response_sha256": call.get("raw_response_sha256"),
                    "parsed_output_path": _portable_run_path(
                        run_id, adapter.get("parsed_output_path")
                    ),
                    "parsed_output_sha256": call.get("parsed_output_sha256"),
                    "prompt_sha256": adapter.get("prompt_sha256"),
                    "output_schema_sha256": adapter.get("output_schema_sha256"),
                    "http_method": http_request.get("method"),
                    "http_path": http_request.get("path"),
                    "http_status_code": http_response.get("status_code"),
                    "http_raw_body_path": _portable_run_path(
                        run_id, http_response.get("raw_body_path")
                    ),
                    "http_raw_body_sha256": http_response.get("content_sha256"),
                    "recorded_at": step.get("recorded_at"),
                }
            )
        return rows

    def _item_source_rows(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        record = source["record"]
        snapshot = record.get("final_database_snapshot") or {}
        session = snapshot.get("session") or {}
        messages = {
            message.get("id"): message for message in snapshot.get("messages") or []
        }
        rows: list[dict[str, Any]] = []
        for item_order, item in enumerate(snapshot.get("digest_items") or [], start=1):
            source_ids = list(item.get("source_message_ids") or [])
            relation_ids: list[Any] = source_ids if source_ids else [None]
            applicable = bool(
                item.get("is_included") is True
                or item.get("review_status") in {"approved", "edited"}
            )
            for relation_order, source_id in enumerate(relation_ids, start=1):
                message = messages.get(source_id) if source_id is not None else None
                issues: list[str] = []
                same_session: bool | None = None
                participant_sender: bool | None = None
                same_topic: bool | None = None
                relation_valid: bool | None = None
                if applicable:
                    same_session = message is not None
                    participant_sender = bool(
                        message and message.get("sender") == "participant"
                    )
                    same_topic = bool(
                        message
                        and message.get("section_index") == item.get("section_index")
                    )
                    if source_id is None:
                        issues.append("missing_source")
                    elif message is None:
                        issues.append("source_not_in_session_snapshot")
                    else:
                        if not participant_sender:
                            issues.append("source_sender_not_participant")
                        if not same_topic:
                            issues.append("source_topic_mismatch")
                    relation_valid = not issues
                rows.append(
                    {
                        "run_id": source["run_id"],
                        "condition": record.get("condition"),
                        "scenario_id": record.get("scenario_id"),
                        "repetition": record.get("repetition"),
                        "pair_key": _pair_key(record),
                        "item_order": item_order,
                        "source_relation_order": relation_order,
                        "item_id": item.get("id"),
                        "session_id": item.get("session_id") or session.get("id"),
                        "section_index": item.get("section_index"),
                        "section_code": item.get("section_code"),
                        "coverage_status": item.get("coverage_status"),
                        "participant_control": item.get("participant_control"),
                        "topic_reached": item.get("topic_reached"),
                        "missing_information_json": _json_cell(
                            item.get("missing_information") or []
                        ),
                        "generated_text": item.get("generated_text"),
                        "final_text": item.get("final_text"),
                        "evidence_text": item.get("evidence_text"),
                        "is_evidence_candidate": item.get("is_evidence_candidate"),
                        "is_included": item.get("is_included"),
                        "display_visible": item.get("display_visible"),
                        "export_visible": item.get("export_visible"),
                        "review_status": item.get("review_status"),
                        "reviewed_text": item.get("reviewed_text"),
                        "reviewer_comment": item.get("reviewer_comment"),
                        "reviewer_name": item.get("reviewer_name_snapshot"),
                        "reviewed_at": item.get("reviewed_at"),
                        "source_relation_applicable": applicable,
                        "source_message_id": source_id,
                        "source_sender": message.get("sender") if message else None,
                        "source_section_index": (
                            message.get("section_index") if message else None
                        ),
                        "source_text": message.get("content") if message else None,
                        "same_session": same_session,
                        "participant_sender": participant_sender,
                        "same_topic": same_topic,
                        "relation_valid": relation_valid,
                        "relation_issues_json": _json_cell(issues),
                    }
                )
        return rows

    def _review_rows(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        record = source["record"]
        snapshot = record.get("final_database_snapshot") or {}
        session = snapshot.get("session") or {}
        items = {item.get("id"): item for item in snapshot.get("digest_items") or []}
        rows: list[dict[str, Any]] = []
        review_order = 0
        for event in snapshot.get("review_events") or []:
            review_order += 1
            item = items.get(event.get("digest_item_id")) or {}
            rows.append(
                self._review_row_base(
                    source,
                    review_order,
                    {
                        "review_record_type": "item_event",
                        "event_id": event.get("id"),
                        "item_id": event.get("digest_item_id"),
                        "session_id": item.get("session_id") or session.get("id"),
                        "section_index": item.get("section_index"),
                        "section_code": item.get("section_code"),
                        "previous_status": event.get("previous_status"),
                        "new_status": event.get("new_status"),
                        "previous_text": event.get("previous_text"),
                        "new_text": event.get("new_text"),
                        "comment": event.get("comment"),
                        "reviewer_name": event.get("reviewer_name_snapshot"),
                        "reviewed_at": event.get("created_at"),
                    },
                )
            )
        decision = snapshot.get("review_decision")
        if isinstance(decision, dict):
            review_order += 1
            rows.append(
                self._review_row_base(
                    source,
                    review_order,
                    {
                        "review_record_type": "overall_decision",
                        "event_id": decision.get("id"),
                        "session_id": session.get("id"),
                        "reviewer_name": decision.get("reviewer_name_snapshot"),
                        "reviewed_at": decision.get("reviewed_at"),
                        "decision": decision.get("decision"),
                        "reviewer_note": decision.get("reviewer_note"),
                        "source_links_checked": decision.get("source_links_checked"),
                        "participant_controls_respected": decision.get(
                            "participant_controls_respected"
                        ),
                        "limitations_and_missing_information_visible": decision.get(
                            "limitations_and_missing_information_visible"
                        ),
                        "participant_meaning_preserved": decision.get(
                            "participant_meaning_preserved"
                        ),
                        "protocol_boundaries_respected": decision.get(
                            "protocol_boundaries_respected"
                        ),
                    },
                )
            )
        return rows

    @staticmethod
    def _review_row_base(
        source: dict[str, Any],
        review_order: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        record = source["record"]
        row = {
            "run_id": source["run_id"],
            "condition": record.get("condition"),
            "scenario_id": record.get("scenario_id"),
            "repetition": record.get("repetition"),
            "pair_key": _pair_key(record),
            "review_order": review_order,
            "review_record_type": None,
            "event_id": None,
            "item_id": None,
            "session_id": None,
            "section_index": None,
            "section_code": None,
            "previous_status": None,
            "new_status": None,
            "previous_text": None,
            "new_text": None,
            "comment": None,
            "reviewer_name": None,
            "reviewed_at": None,
            "decision": None,
            "reviewer_note": None,
            "source_links_checked": None,
            "participant_controls_respected": None,
            "limitations_and_missing_information_visible": None,
            "participant_meaning_preserved": None,
            "protocol_boundaries_respected": None,
        }
        row.update(values)
        return row

    @staticmethod
    def _copy_contracts(staging: Path) -> None:
        contract_root = staging / "contracts"
        manifest_path = CURRENT_FREEZE_MANIFEST_PATH
        manifest = load_json(manifest_path)
        target_manifest = contract_root / "specs" / manifest_path.name
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest_path, target_manifest)
        for relative_path in manifest["frozen_files"]:
            source = (SPEC_DIR / relative_path).resolve()
            if relative_path.startswith("../prompts/"):
                destination = contract_root / "prompts" / source.name
            else:
                destination = contract_root / "specs" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if sha256_file(source) != sha256_file(destination):
                raise EvaluationExportError(f"Contract copy hash mismatch: {source}")

    @staticmethod
    def _artifact_inventory(staging: Path) -> list[dict[str, Any]]:
        inventory = []
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name != "manifest.json":
                inventory.append(
                    {
                        "path": str(path.relative_to(staging)),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        return inventory


__all__ = ["EvaluationBundleExporter", "EvaluationExportError"]

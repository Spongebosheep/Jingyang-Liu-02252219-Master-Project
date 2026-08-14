"""Append-only calculation of frozen metrics from one exported evidence bundle.

The calculator is a pure file consumer. It does not execute either condition,
query Django, call a model, repair an observation, perform UCR/MP coding, or
turn a mechanical paired table into a comparative-superiority claim.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schemas import (
    COVERAGE_STATUSES,
    EXPORT_TABLE_FIELDS,
    METRIC_CODES,
    METRIC_OUTPUT_TABLE_FIELDS,
    PAIRED_CONDITIONS,
    canonical_json_bytes,
    load_json,
    pair_integrity_from_run_rows,
    sha256_file,
    validate_export_bundle_schema,
    validate_metric_calculation_schema,
    validate_metrics,
    validate_scenarios,
)
from .validate_specs import SPEC_DIR, validate_all


CALCULATOR_SCHEMA_VERSION = "1.0"
AUTOMATIC_METRICS = ("AI", "AC", "SLC", "MIV", "PCC", "RAC", "TCR")
MANUAL_METRICS = ("UCR", "MP")
DETAIL_PASS = "PASS"
DETAIL_FAIL = "FAIL"
DETAIL_NOT_EVALUABLE = "NOT_EVALUABLE"
DETAIL_PENDING_MANUAL = "PENDING_MANUAL"


class MetricCalculationError(RuntimeError):
    """Raised when an export cannot be calculated without violating the contract."""


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
            raise MetricCalculationError(
                f"Metric row {index} for {path.name} differs from the frozen schema; "
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


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MetricCalculationError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise MetricCalculationError(f"{label} {path} must contain one JSON object.")
    return value


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != fields:
                raise MetricCalculationError(
                    f"CSV header drift in {path}; expected {fields}, got {reader.fieldnames}."
                )
            return list(reader)
    except OSError as error:
        raise MetricCalculationError(f"Cannot read CSV {path}: {error}") from error


def _parse_bool(value: Any, *, allow_blank: bool = True) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text and allow_blank:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    raise MetricCalculationError(f"Expected a frozen boolean cell, got {value!r}.")


def _parse_int(value: Any, *, allow_blank: bool = True) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text and allow_blank:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise MetricCalculationError(f"Expected an integer cell, got {value!r}.") from error


def _parse_json_cell(value: Any, *, expected_type: type | None = None) -> Any:
    text = str(value if value is not None else "").strip()
    if not text:
        parsed: Any = []
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise MetricCalculationError(f"Invalid JSON CSV cell: {value!r}.") from error
    if expected_type is not None and not isinstance(parsed, expected_type):
        raise MetricCalculationError(
            f"JSON CSV cell must contain {expected_type.__name__}, got {type(parsed).__name__}."
        )
    return parsed


def _nonblank(value: Any) -> bool:
    return bool(str(value if value is not None else "").strip())


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 6)


def _sha256_json(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass
class RunObservation:
    run_row: dict[str, str]
    record_path: Path
    record: dict[str, Any]
    turns: list[dict[str, str]]
    item_rows: list[dict[str, str]]
    reviews: list[dict[str, str]]
    validator_path: Path | None
    validator: dict[str, Any] | None
    scenario: dict[str, Any]
    identity_issues: list[str]

    @property
    def run_id(self) -> str:
        return self.run_row["run_id"]

    @property
    def condition(self) -> str:
        return self.run_row["condition"]

    @property
    def scenario_id(self) -> str:
        return self.run_row["scenario_id"]


class EvaluationMetricCalculator:
    """Calculate frozen automatic metrics and retain manual-metric placeholders."""

    def __init__(self, *, export_directory: Path, output_root: Path):
        self.export_directory = Path(export_directory).resolve()
        self.output_root = Path(output_root).resolve()
        self.calculation_id = (
            f"metric-calculation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        self.final_directory = self.output_root / self.calculation_id
        self.staging_directory = self.output_root / f".{self.calculation_id}.tmp"
        self.started_at = _utc_now()
        self.input_manifest: dict[str, Any] | None = None
        self.input_manifest_path = self.export_directory / "manifest.json"
        self.input_manifest_sha256: str | None = None
        self._input_inventory_snapshot: dict[str, tuple[int, str]] = {}
        self.metric_document: dict[str, Any] = {}
        self.scenario_document: dict[str, Any] = {}
        self.metric_by_code: dict[str, dict[str, Any]] = {}
        self.scenario_by_id: dict[str, dict[str, Any]] = {}
        self.run_observations: list[RunObservation] = []
        self.pair_integrity: dict[str, Any] = {}

    def run(self) -> tuple[dict[str, Any], Path]:
        if self.final_directory.exists() or self.staging_directory.exists():
            raise MetricCalculationError(
                f"Refusing to overwrite a metric calculation: {self.final_directory}"
            )
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.staging_directory.mkdir(parents=False, exist_ok=False)

        try:
            contracts = validate_all()
            self._load_input()
            self._copy_contract_snapshots()

            detail_rows: list[dict[str, Any]] = []
            for observation in self.run_observations:
                detail_rows.extend(self._calculate_run_details(observation))
            detail_rows.sort(key=self._detail_sort_key)
            for index, row in enumerate(detail_rows, start=1):
                row["detail_order"] = index

            run_rows = self._summarise_runs(detail_rows)
            condition_rows = self._summarise_conditions(run_rows)
            pair_rows = self._summarise_pairs(run_rows)
            table_rows = {
                "detail": detail_rows,
                "run_summary": run_rows,
                "condition_summary": condition_rows,
                "pair_summary": pair_rows,
            }
            schema = load_json(SPEC_DIR / "metric_calculation_schema.v2.json")
            for table_name, rows in table_rows.items():
                _write_csv(
                    self.staging_directory / schema["tables"][table_name]["filename"],
                    METRIC_OUTPUT_TABLE_FIELDS[table_name],
                    rows,
                )

            summary = self._summary_document(
                run_rows=run_rows,
                condition_rows=condition_rows,
                pair_rows=pair_rows,
            )
            _atomic_write_json(
                self.staging_directory / "metrics" / "summary.json", summary
            )
            self._verify_input_unchanged()

            inventory = self._artifact_inventory(self.staging_directory)
            manifest = {
                "schema_version": CALCULATOR_SCHEMA_VERSION,
                "calculation_id": self.calculation_id,
                "calculation_status": "completed",
                "started_at": self.started_at,
                "completed_at": _utc_now(),
                "calculator_module": "evaluation.metric_calculator",
                "calculator_contract": contracts["contracts"][
                    "metric_calculation_schema"
                ],
                "freeze_manifest": contracts["contracts"]["freeze_manifest"],
                "input_export": {
                    "export_id": self.input_manifest["export_id"],
                    "source_directory": str(self.export_directory),
                    "source_manifest_path": str(self.input_manifest_path),
                    "source_manifest_sha256": self.input_manifest_sha256,
                    "copied_manifest_path": "input/export_manifest.json",
                    "run_count": len(self.run_observations),
                    "pair_integrity": self.pair_integrity,
                    "inventory_file_count": len(self._input_inventory_snapshot),
                    "inventory_verified_before": True,
                    "inventory_verified_after": True,
                },
                "output_row_counts": {
                    table_name: len(rows) for table_name, rows in table_rows.items()
                },
                "automatic_metric_codes": list(AUTOMATIC_METRICS),
                "manual_metric_codes": list(MANUAL_METRICS),
                "formal_evidence_eligible_run_count": sum(
                    _parse_bool(row.run_row["formal_evidence_eligible"]) is True
                    for row in self.run_observations
                ),
                "artifact_inventory": inventory,
                "claim_boundaries": {
                    "automatic_metrics_calculated": True,
                    "ucr_human_coding_performed": False,
                    "mp_human_coding_performed": False,
                    "comparative_superiority_claim_generated": False,
                    "formal_runs_started_by_calculator": False,
                    "database_or_model_used": False,
                    "input_export_modified": False,
                },
                "claim_boundary": (
                    "Automatic ratios and paired side-by-side values are mechanical "
                    "summaries of one saved export. UCR and MP remain pending human "
                    "coding. No winner, outcome-quality or comparative-superiority "
                    "claim is generated."
                ),
            }
            _atomic_write_json(
                self.staging_directory / "calculation_manifest.json", manifest
            )
            os.replace(self.staging_directory, self.final_directory)
            return manifest, self.final_directory
        except Exception as error:
            self._retain_failed_attempt(error)
            if isinstance(error, MetricCalculationError):
                raise
            raise MetricCalculationError(
                f"Metric calculation failed and was retained at {self.final_directory}: {error}"
            ) from error

    def _load_input(self) -> None:
        if not self.export_directory.is_dir():
            raise MetricCalculationError(
                f"Export directory does not exist: {self.export_directory}"
            )
        if not self.input_manifest_path.is_file():
            raise MetricCalculationError(
                f"Export manifest does not exist: {self.input_manifest_path}"
            )
        self.input_manifest_sha256 = sha256_file(self.input_manifest_path)
        self.input_manifest = _read_object(self.input_manifest_path, "export manifest")
        if self.input_manifest.get("schema_version") != "1.0":
            raise MetricCalculationError("Unsupported export manifest schema version.")
        if self.input_manifest.get("export_status") != "completed":
            raise MetricCalculationError("Only a completed export bundle can be calculated.")
        self._input_inventory_snapshot = self._verify_input_inventory()

        export_schema_path = self._copied_contract_path(
            self.input_manifest.get("export_contract"),
            "export_bundle_schema.v2.json",
        )
        metric_path = self._copied_contract_path(None, "metrics.v3.json")
        scenario_path = self._copied_contract_path(None, "scenarios.v4.json")
        export_schema = load_json(export_schema_path)
        self.metric_document = load_json(metric_path)
        self.scenario_document = load_json(scenario_path)
        validate_export_bundle_schema(export_schema)
        validate_metrics(self.metric_document)
        validate_scenarios(self.scenario_document)

        self.metric_by_code = {
            row["code"]: row for row in self.metric_document["metrics"]
        }
        self.scenario_by_id = {
            row["scenario_id"]: row
            for row in self.scenario_document["scenarios"]
        }
        tables = {
            name: _read_csv(
                self.export_directory / export_schema["tables"][name]["filename"],
                EXPORT_TABLE_FIELDS[name],
            )
            for name in EXPORT_TABLE_FIELDS
        }
        if len(tables["runs"]) != self.input_manifest.get("run_count"):
            raise MetricCalculationError("runs.csv count differs from export manifest.")
        for name, rows in tables.items():
            expected = (self.input_manifest.get("table_row_counts") or {}).get(name)
            if expected != len(rows):
                raise MetricCalculationError(
                    f"{name}.csv row count differs from export manifest."
                )

        run_rows: dict[str, dict[str, str]] = {}
        for row in tables["runs"]:
            run_id = row["run_id"]
            if not run_id or run_id in run_rows:
                raise MetricCalculationError(f"Duplicate or blank run_id in runs.csv: {run_id!r}.")
            if row["condition"] not in PAIRED_CONDITIONS:
                raise MetricCalculationError(
                    f"Run {run_id} has unsupported condition {row['condition']!r}."
                )
            run_rows[run_id] = row

        grouped_tables: dict[str, dict[str, list[dict[str, str]]]] = {}
        for name in ("turns", "item_sources", "reviews"):
            grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in tables[name]:
                run_id = row["run_id"]
                if run_id not in run_rows:
                    raise MetricCalculationError(
                        f"{name}.csv references unknown run_id {run_id!r}."
                    )
                for field in ("condition", "scenario_id", "repetition", "pair_key"):
                    if row[field] != run_rows[run_id][field]:
                        raise MetricCalculationError(
                            f"{name}.csv metadata drift for {run_id}/{field}."
                        )
                grouped[run_id].append(row)
            grouped_tables[name] = grouped

        metric_hash = sha256_file(metric_path)
        scenario_hash = sha256_file(scenario_path)
        observations = []
        for run_id, row in run_rows.items():
            record_path = self._resolve_input_path(row["runner_record_path"])
            if sha256_file(record_path) != row["runner_record_sha256"]:
                raise MetricCalculationError(
                    f"Runner record hash differs from runs.csv for {run_id}."
                )
            record = _read_object(record_path, "runner record")
            if record.get("run_id") != run_id:
                raise MetricCalculationError(
                    f"Runner record identity differs from runs.csv for {run_id}."
                )
            scenario = self.scenario_by_id.get(row["scenario_id"])
            if not scenario:
                raise MetricCalculationError(
                    f"Run {run_id} has unknown scenario {row['scenario_id']!r}."
                )
            identity_issues = []
            if row["metric_rules_sha256"] != metric_hash:
                identity_issues.append("metric_contract_hash_mismatch")
            if row["scenario_sha256"] != scenario_hash:
                identity_issues.append("scenario_contract_hash_mismatch")
            if record.get("scenario_instance_sha256") != _sha256_json(scenario):
                identity_issues.append("scenario_instance_hash_mismatch")
            if record.get("frozen_oracle") != scenario:
                identity_issues.append("embedded_oracle_mismatch")

            validator_path: Path | None = None
            validator: dict[str, Any] | None = None
            if row["selected_validator_result_path"]:
                validator_path = self._resolve_input_path(
                    row["selected_validator_result_path"]
                )
                if sha256_file(validator_path) != row[
                    "selected_validator_result_sha256"
                ]:
                    raise MetricCalculationError(
                        f"Selected validator hash differs from runs.csv for {run_id}."
                    )
                validator = _read_object(validator_path, "validator result")

            observations.append(
                RunObservation(
                    run_row=row,
                    record_path=record_path,
                    record=record,
                    turns=grouped_tables["turns"].get(run_id, []),
                    item_rows=grouped_tables["item_sources"].get(run_id, []),
                    reviews=grouped_tables["reviews"].get(run_id, []),
                    validator_path=validator_path,
                    validator=validator,
                    scenario=scenario,
                    identity_issues=identity_issues,
                )
            )
        self.run_observations = sorted(
            observations,
            key=lambda row: (
                row.scenario_id,
                _parse_int(row.run_row["repetition"], allow_blank=False),
                PAIRED_CONDITIONS.index(row.condition),
                row.run_id,
            ),
        )
        self.pair_integrity = pair_integrity_from_run_rows(
            [observation.run_row for observation in self.run_observations]
        )
        if self.input_manifest.get("pair_integrity") != self.pair_integrity:
            raise MetricCalculationError(
                "Export pair_integrity differs from the frozen identity fields in runs.csv."
            )

    def _verify_input_inventory(self) -> dict[str, tuple[int, str]]:
        inventory = self.input_manifest.get("artifact_inventory") or []
        if not isinstance(inventory, list):
            raise MetricCalculationError("Export artifact_inventory must be a list.")
        recorded: dict[str, tuple[int, str]] = {}
        for entry in inventory:
            relative = str(entry.get("path") or "")
            if not relative or relative in recorded:
                raise MetricCalculationError(
                    f"Duplicate or blank export inventory path: {relative!r}."
                )
            path = self._resolve_input_path(relative)
            if path.is_symlink() or not path.is_file():
                raise MetricCalculationError(f"Inventory file is missing or unsafe: {relative}.")
            expected_bytes = entry.get("bytes")
            expected_hash = entry.get("sha256")
            actual = (path.stat().st_size, sha256_file(path))
            if actual != (expected_bytes, expected_hash):
                raise MetricCalculationError(
                    f"Export inventory mismatch for {relative}; expected "
                    f"{(expected_bytes, expected_hash)}, got {actual}."
                )
            recorded[relative] = actual

        actual_paths = {
            str(path.relative_to(self.export_directory))
            for path in self.export_directory.rglob("*")
            if path.is_file() and path != self.input_manifest_path
        }
        if actual_paths != set(recorded):
            raise MetricCalculationError(
                "Export directory contains missing or unlisted files; "
                f"missing={sorted(set(recorded) - actual_paths)}, "
                f"unlisted={sorted(actual_paths - set(recorded))}."
            )
        return recorded

    def _verify_input_unchanged(self) -> None:
        if sha256_file(self.input_manifest_path) != self.input_manifest_sha256:
            raise MetricCalculationError("Export manifest changed during calculation.")
        after = self._verify_input_inventory()
        if after != self._input_inventory_snapshot:
            raise MetricCalculationError("Export inventory changed during calculation.")

    def _resolve_input_path(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise MetricCalculationError(f"Absolute path is forbidden in export data: {relative}")
        resolved = (self.export_directory / candidate).resolve()
        try:
            resolved.relative_to(self.export_directory)
        except ValueError as error:
            raise MetricCalculationError(
                f"Export path escapes its directory: {relative}"
            ) from error
        return resolved

    def _copied_contract_path(
        self, descriptor: dict[str, Any] | None, default_name: str
    ) -> Path:
        name = Path(str((descriptor or {}).get("path") or default_name)).name
        path = self.export_directory / "contracts" / "specs" / name
        if not path.is_file():
            raise MetricCalculationError(f"Copied contract is missing: {path}")
        expected_hash = (descriptor or {}).get("sha256")
        if expected_hash and sha256_file(path) != expected_hash:
            raise MetricCalculationError(f"Copied contract hash mismatch: {path}")
        return path

    def _copy_contract_snapshots(self) -> None:
        (self.staging_directory / "input").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            self.input_manifest_path,
            self.staging_directory / "input" / "export_manifest.json",
        )
        contract_root = self.staging_directory / "contracts"
        contract_root.mkdir(parents=True, exist_ok=True)
        for name, source in (
            (
                "metric_calculation_schema.v2.json",
                SPEC_DIR / "metric_calculation_schema.v2.json",
            ),
            ("metrics.v3.json", self._copied_contract_path(None, "metrics.v3.json")),
            (
                "scenarios.v4.json",
                self._copied_contract_path(None, "scenarios.v4.json"),
            ),
        ):
            target = contract_root / name
            shutil.copyfile(source, target)
            if sha256_file(source) != sha256_file(target):
                raise MetricCalculationError(f"Contract snapshot copy failed: {name}")

    def _calculate_run_details(
        self, observation: RunObservation
    ) -> list[dict[str, Any]]:
        if observation.identity_issues:
            return [
                self._detail(
                    observation,
                    code,
                    unit_type="run_contract_identity",
                    unit_id=observation.run_id,
                    status=DETAIL_NOT_EVALUABLE,
                    passed=None,
                    numerator=0,
                    denominator=0,
                    expected={"frozen_contract_identity": True},
                    observed={"issues": observation.identity_issues},
                    failures=observation.identity_issues,
                    sources=[observation.run_row["runner_record_path"]],
                    manual=code in MANUAL_METRICS,
                )
                for code in METRIC_CODES
            ]

        details = []
        details.extend(self._calculate_ai(observation))
        details.extend(self._calculate_ac(observation))
        details.extend(self._calculate_slc(observation))
        details.extend(self._calculate_miv(observation))
        details.extend(self._calculate_pcc(observation))
        details.extend(self._calculate_ucr(observation))
        details.extend(self._calculate_rac(observation))
        details.extend(self._calculate_mp(observation))
        details.extend(self._calculate_tcr(observation))
        return details

    def _expected_decision_steps(
        self, observation: RunObservation
    ) -> list[dict[str, Any]]:
        return [
            step
            for step in observation.scenario["steps"]
            if step.get("kind") in {"participant_turn", "participant_control"}
            and step.get("oracle", {}).get("expected_action")
        ]

    @staticmethod
    def _rows_by(rows: Iterable[dict[str, str]], field: str) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            grouped[row[field]].append(row)
        return grouped

    def _calculate_ai(self, observation: RunObservation) -> list[dict[str, Any]]:
        by_step = self._rows_by(observation.turns, "step_id")
        details = []
        for step in self._expected_decision_steps(observation):
            rows = by_step.get(step["step_id"], [])
            failures = []
            observed: dict[str, Any] = {"row_count": len(rows)}
            if len(rows) != 1:
                failures.append("expected_exactly_one_exported_step")
            else:
                row = rows[0]
                observed.update(
                    {
                        "observed_decision_count": _parse_int(
                            row["observed_decision_count"]
                        ),
                        "run_id": row["run_id"],
                        "session_id": row["observed_session_id"],
                        "source_message_id": row["source_message_id"],
                        "section_index": row["section_index"],
                        "section_code": row["section_code"],
                        "coverage_assessment": row["coverage_assessment"],
                        "participant_control": row["participant_control"],
                        "selected_action": row["selected_action"],
                        "missing_information": _parse_json_cell(
                            row["missing_information_json"], expected_type=list
                        ),
                        "reason": row["reason"],
                    }
                )
                if observed["observed_decision_count"] != 1:
                    failures.append("control_record_count_not_one")
                for field in (
                    "run_id",
                    "session_id",
                    "source_message_id",
                    "section_index",
                    "section_code",
                    "coverage_assessment",
                    "participant_control",
                    "selected_action",
                    "reason",
                ):
                    if not _nonblank(observed[field]):
                        failures.append(f"missing_{field}")
                if row["missing_information_json"].strip() == "":
                    failures.append("missing_missing_information_field")
            passed = not failures
            details.append(
                self._detail(
                    observation,
                    "AI",
                    unit_type="participant_decision_step",
                    unit_id=step["step_id"],
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected={"one_complete_control_record": True},
                    observed=observed,
                    failures=failures,
                    sources=["tables/turns.csv", observation.run_row["runner_record_path"]],
                )
            )
        return details

    def _calculate_ac(self, observation: RunObservation) -> list[dict[str, Any]]:
        by_step = self._rows_by(observation.turns, "step_id")
        details = []
        for step in self._expected_decision_steps(observation):
            oracle = step["oracle"]
            if oracle.get("eligible_for_ac") is not True:
                continue
            rows = by_step.get(step["step_id"], [])
            observed_actions = [row["selected_action"] for row in rows]
            decision_counts = [
                _parse_int(row["observed_decision_count"]) for row in rows
            ]
            passed = bool(
                len(rows) == 1
                and decision_counts == [1]
                and observed_actions == [oracle["expected_action"]]
            )
            failures = []
            if len(rows) != 1:
                failures.append("expected_exactly_one_exported_step")
            elif decision_counts != [1]:
                failures.append("control_record_count_not_one")
            if observed_actions != [oracle["expected_action"]]:
                failures.append("observed_action_mismatch")
            details.append(
                self._detail(
                    observation,
                    "AC",
                    unit_type="oracle_action_step",
                    unit_id=step["step_id"],
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected={"selected_action": oracle["expected_action"]},
                    observed={
                        "row_count": len(rows),
                        "decision_counts": decision_counts,
                        "selected_actions": observed_actions,
                    },
                    failures=failures,
                    sources=["tables/turns.csv"],
                )
            )
        return details

    @staticmethod
    def _item_groups(observation: RunObservation) -> list[list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in observation.item_rows:
            key = row["item_id"] or f"order:{row['item_order']}"
            grouped[key].append(row)
        return sorted(
            grouped.values(),
            key=lambda rows: (
                _parse_int(rows[0]["section_index"]) or 0,
                _parse_int(rows[0]["item_order"]) or 0,
                rows[0]["item_id"],
            ),
        )

    def _eligible_item_groups(self, observation: RunObservation) -> list[list[dict[str, str]]]:
        eligible = []
        for rows in self._item_groups(observation):
            row = rows[0]
            if _parse_bool(row["is_included"]) is True or row["review_status"] in {
                "approved",
                "edited",
            }:
                eligible.append(rows)
        return eligible

    def _calculate_slc(self, observation: RunObservation) -> list[dict[str, Any]]:
        groups = self._eligible_item_groups(observation)
        if not groups and observation.run_row["execution_status"] != "completed":
            return [
                self._not_evaluable_detail(
                    observation,
                    "SLC",
                    "run",
                    observation.run_id,
                    "run_incomplete_before_eligible_items_could_be_observed",
                    ["tables/item_sources.csv"],
                )
            ]
        details = []
        for rows in groups:
            valid_relations = [
                row
                for row in rows
                if _parse_bool(row["relation_valid"]) is True
                and _parse_bool(row["same_session"]) is True
                and _parse_bool(row["participant_sender"]) is True
                and _parse_bool(row["same_topic"]) is True
                and _nonblank(row["source_message_id"])
            ]
            passed = bool(valid_relations)
            failures = [] if passed else ["no_valid_same_session_participant_topic_source"]
            details.append(
                self._detail(
                    observation,
                    "SLC",
                    unit_type="included_or_edited_item",
                    unit_id=rows[0]["item_id"] or f"order:{rows[0]['item_order']}",
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected={"minimum_valid_relation_count": 1},
                    observed={
                        "relation_count": len(rows),
                        "valid_relation_count": len(valid_relations),
                        "relation_issues": [
                            _parse_json_cell(row["relation_issues_json"], expected_type=list)
                            for row in rows
                        ],
                    },
                    failures=failures,
                    sources=["tables/item_sources.csv"],
                )
            )
        return details

    def _calculate_miv(self, observation: RunObservation) -> list[dict[str, Any]]:
        groups_by_section: dict[int, list[list[dict[str, str]]]] = defaultdict(list)
        for group in self._item_groups(observation):
            index = _parse_int(group[0]["section_index"])
            if index is not None:
                groups_by_section[index].append(group)
        validator_checks = {
            row.get("check_id"): row
            for row in ((observation.validator or {}).get("checks") or [])
        }
        validator_is_current = (
            _parse_bool(observation.run_row["validator_matches_runner"]) is True
            and observation.run_row["validation_status"] != "STALE"
        )
        details = []
        for expected in observation.scenario["expected_topic_outcomes"]:
            if (
                expected["coverage_status"] == "covered"
                and expected["participant_control"] == "none"
                and expected["topic_reached"] is True
            ):
                continue
            index = int(expected["section_index"])
            groups = groups_by_section.get(index, [])
            failures = []
            observed: dict[str, Any] = {"item_count": len(groups)}
            if len(groups) != 1:
                failures.append("limitation_item_count_not_one")
            else:
                row = groups[0][0]
                missing = _parse_json_cell(
                    row["missing_information_json"], expected_type=list
                )
                observed.update(
                    {
                        "coverage_status": row["coverage_status"],
                        "participant_control": row["participant_control"],
                        "topic_reached": _parse_bool(row["topic_reached"]),
                        "missing_information": missing,
                        "is_evidence_candidate": _parse_bool(
                            row["is_evidence_candidate"]
                        ),
                        "display_visible": _parse_bool(row["display_visible"]),
                        "export_visible": _parse_bool(row["export_visible"]),
                    }
                )
                if row["coverage_status"] != expected["coverage_status"]:
                    failures.append("coverage_status_mismatch")
                if row["participant_control"] != expected["participant_control"]:
                    failures.append("participant_control_mismatch")
                if observed["topic_reached"] is not expected["topic_reached"]:
                    failures.append("topic_reached_mismatch")
                if expected.get("missing_information_nonempty") and not missing:
                    failures.append("missing_information_not_visible")
                if "is_evidence_candidate" in expected and observed[
                    "is_evidence_candidate"
                ] is not expected["is_evidence_candidate"]:
                    failures.append("evidence_candidate_boundary_mismatch")
                if observed["display_visible"] is not True:
                    failures.append("structured_display_visibility_not_recorded")
                if observed["export_visible"] is not True:
                    failures.append("structured_export_visibility_not_recorded")

            check_id = f"TOPIC-{index}-DISPLAY-EXPORT-VISIBILITY"
            check = validator_checks.get(check_id)
            render_visibility_pass = bool(
                validator_is_current and check and check.get("status") == "PASS"
            )
            observed["render_visibility_check"] = {
                "validator_current": validator_is_current,
                "check_id": check_id,
                "status": check.get("status") if check else None,
            }
            if not render_visibility_pass:
                failures.append("review_and_evidence_record_visibility_not_verified")
            passed = not failures
            source_paths = ["tables/item_sources.csv"]
            if observation.validator_path:
                source_paths.append(
                    str(observation.validator_path.relative_to(self.export_directory))
                )
            details.append(
                self._detail(
                    observation,
                    "MIV",
                    unit_type="expected_limitation_state",
                    unit_id=f"topic:{index}",
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected=expected,
                    observed=observed,
                    failures=failures,
                    sources=source_paths,
                )
            )
        return details

    def _calculate_pcc(self, observation: RunObservation) -> list[dict[str, Any]]:
        steps = {
            row.get("step_id"): row for row in observation.record.get("scenario_steps") or []
        }
        all_decisions = (
            (observation.record.get("final_database_snapshot") or {}).get(
                "agent_decisions"
            )
            or []
        )
        positions = {row.get("id"): index for index, row in enumerate(all_decisions)}
        details = []
        for expected_step in observation.scenario["steps"]:
            if expected_step.get("kind") != "participant_control":
                continue
            control = expected_step.get("control")
            if control not in {"skip", "stop"}:
                continue
            oracle = expected_step["oracle"]
            saved = steps.get(expected_step["step_id"])
            failures = []
            observed: dict[str, Any] = {"saved_step_present": saved is not None}
            decision: dict[str, Any] = {}
            if not saved:
                failures.append("control_step_missing")
            else:
                data = saved.get("observed") or {}
                decisions = data.get("agent_decisions") or []
                observed["decision_count"] = len(decisions)
                if len(decisions) != 1:
                    failures.append("control_record_count_not_one")
                else:
                    decision = decisions[0]
                    observed["selected_action"] = decision.get("selected_action")
                    if decision.get("selected_action") != oracle["expected_action"]:
                        failures.append("control_action_mismatch")
                after = data.get("session_after") or {}
                observed["session_after"] = {
                    "status": after.get("status"),
                    "current_section_index": after.get("current_section_index"),
                }
                position = positions.get(decision.get("id")) if decision else None
                later = all_decisions[position + 1 :] if position is not None else []
                if control == "skip":
                    later_same_topic = sum(
                        row.get("section_index") == expected_step["section_index"]
                        for row in later
                    )
                    observed["later_same_topic_decision_count"] = later_same_topic
                    if after.get("current_section_index") != oracle["next_section_index"]:
                        failures.append("skip_did_not_leave_topic")
                    if later_same_topic != oracle["later_same_topic_decision_count"]:
                        failures.append("later_same_topic_decision_after_skip")
                else:
                    observed["later_decision_count"] = len(later)
                    if after.get("status") != oracle["session_status"]:
                        failures.append("stop_did_not_end_session")
                    if len(later) != oracle["later_decision_count"]:
                        failures.append("later_decision_after_stop")
                    probe_expected = next(
                        (
                            row
                            for row in observation.scenario["steps"]
                            if row.get("kind") == "post_stop_probe"
                        ),
                        None,
                    )
                    probe = steps.get(probe_expected["step_id"]) if probe_expected else None
                    probe_data = (probe or {}).get("observed") or {}
                    created = probe_data.get("created_records") or {}
                    probe_observed = {
                        "message_count": len(created.get("message_ids") or []),
                        "decision_count": len(created.get("agent_decision_ids") or []),
                        "session_status": (probe_data.get("session_after") or {}).get(
                            "status"
                        ),
                    }
                    observed["post_stop_probe"] = probe_observed
                    if not probe:
                        failures.append("post_stop_probe_missing")
                    elif probe_observed != {
                        "message_count": probe_expected["oracle"][
                            "expected_new_message_count"
                        ],
                        "decision_count": probe_expected["oracle"][
                            "expected_new_decision_count"
                        ],
                        "session_status": probe_expected["oracle"]["session_status"],
                    }:
                        failures.append("post_stop_probe_created_or_changed_records")
            passed = not failures
            details.append(
                self._detail(
                    observation,
                    "PCC",
                    unit_type="participant_control_event",
                    unit_id=expected_step["step_id"],
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected={"control": control, **oracle},
                    observed=observed,
                    failures=failures,
                    sources=[observation.run_row["runner_record_path"]],
                )
            )
        return details

    def _calculate_ucr(self, observation: RunObservation) -> list[dict[str, Any]]:
        eligible_items = self._eligible_item_groups(observation)
        return [
            self._detail(
                observation,
                "UCR",
                unit_type="manual_atomic_proposition_coding",
                unit_id=observation.run_id,
                status=DETAIL_PENDING_MANUAL,
                passed=None,
                numerator=0,
                denominator=0,
                expected=self.metric_document["manual_coding_rules"]["UCR"],
                observed={
                    "eligible_item_count": len(eligible_items),
                    "coded_atomic_proposition_count": 0,
                    "coding_records_supplied": False,
                },
                failures=["pending_independent_atomic_proposition_coding"],
                sources=["tables/item_sources.csv"],
                manual=True,
            )
        ]

    def _calculate_rac(self, observation: RunObservation) -> list[dict[str, Any]]:
        if not observation.reviews and observation.run_row["execution_status"] != "completed":
            return [
                self._not_evaluable_detail(
                    observation,
                    "RAC",
                    "run",
                    observation.run_id,
                    "run_incomplete_before_review_events_could_be_observed",
                    ["tables/reviews.csv"],
                )
            ]
        details = []
        for row in observation.reviews:
            failures = []
            record_type = row["review_record_type"]
            observed = {
                "review_record_type": record_type,
                "new_status": row["new_status"],
                "reviewer_name": row["reviewer_name"],
                "reviewed_at": row["reviewed_at"],
                "comment": row["comment"],
                "previous_text": row["previous_text"],
                "new_text": row["new_text"],
                "decision": row["decision"],
                "reviewer_note": row["reviewer_note"],
            }
            if record_type == "item_event":
                action = row["new_status"]
                if not _nonblank(action):
                    failures.append("missing_review_status")
                if not _nonblank(row["reviewer_name"]):
                    failures.append("missing_reviewer")
                if not _nonblank(row["reviewed_at"]):
                    failures.append("missing_review_time")
                if action in {"edited", "excluded"} and not _nonblank(row["comment"]):
                    failures.append("missing_reason")
                if action == "edited":
                    if not _nonblank(row["previous_text"]):
                        failures.append("missing_before_text")
                    if not _nonblank(row["new_text"]):
                        failures.append("missing_after_text")
                expected = {
                    "status_reviewer_time": True,
                    "edit_or_exclude_reason": action in {"edited", "excluded"},
                    "edit_before_after": action == "edited",
                }
                unit_id = row["event_id"] or f"review:{row['review_order']}"
            elif record_type == "overall_decision":
                if not _nonblank(row["decision"]):
                    failures.append("missing_overall_decision")
                if not _nonblank(row["reviewer_name"]):
                    failures.append("missing_reviewer")
                if not _nonblank(row["reviewed_at"]):
                    failures.append("missing_review_time")
                if not _nonblank(row["reviewer_note"]):
                    failures.append("missing_reviewer_note")
                expected = {"decision_reviewer_time_note": True}
                unit_id = row["event_id"] or f"overall:{row['review_order']}"
            else:
                failures.append("unknown_review_record_type")
                expected = {"known_review_record_type": True}
                unit_id = f"review:{row['review_order']}"
            passed = not failures
            details.append(
                self._detail(
                    observation,
                    "RAC",
                    unit_type=record_type or "unknown_review_record",
                    unit_id=unit_id,
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected=expected,
                    observed=observed,
                    failures=failures,
                    sources=["tables/reviews.csv"],
                )
            )
        return details

    def _calculate_mp(self, observation: RunObservation) -> list[dict[str, Any]]:
        groups = self._eligible_item_groups(observation)
        if not groups:
            return [
                self._detail(
                    observation,
                    "MP",
                    unit_type="manual_item_judgement",
                    unit_id=observation.run_id,
                    status=DETAIL_PENDING_MANUAL,
                    passed=None,
                    numerator=0,
                    denominator=0,
                    expected=self.metric_document["manual_coding_rules"]["MP"],
                    observed={
                        "eligible_item_count": 0,
                        "coding_records_supplied": False,
                    },
                    failures=["no_eligible_item_currently_requires_mp_coding"],
                    sources=["tables/item_sources.csv"],
                    manual=True,
                )
            ]
        details = []
        for rows in groups:
            row = rows[0]
            details.append(
                self._detail(
                    observation,
                    "MP",
                    unit_type="manual_item_judgement",
                    unit_id=row["item_id"] or f"order:{row['item_order']}",
                    status=DETAIL_PENDING_MANUAL,
                    passed=None,
                    numerator=0,
                    denominator=1,
                    expected=self.metric_document["manual_coding_rules"]["MP"],
                    observed={
                        "final_text": row["evidence_text"] or row["final_text"],
                        "source_message_ids": [
                            relation["source_message_id"] for relation in rows
                        ],
                        "coding_records_supplied": False,
                    },
                    failures=["pending_independent_meaning_preservation_judgement"],
                    sources=["tables/item_sources.csv"],
                    manual=True,
                )
            )
        return details

    def _calculate_tcr(self, observation: RunObservation) -> list[dict[str, Any]]:
        groups_by_section: dict[int, list[list[dict[str, str]]]] = defaultdict(list)
        for group in self._item_groups(observation):
            index = _parse_int(group[0]["section_index"])
            if index is not None:
                groups_by_section[index].append(group)
        expected_by_section = {
            int(row["section_index"]): row
            for row in observation.scenario["expected_topic_outcomes"]
        }
        details = []
        for section_index in range(1, 6):
            groups = groups_by_section.get(section_index, [])
            failures = []
            observed = {"item_count": len(groups)}
            expected = expected_by_section[section_index]
            if len(groups) != 1:
                failures.append("topic_record_count_not_one")
            else:
                row = groups[0][0]
                observed.update(
                    {
                        "section_code": row["section_code"],
                        "coverage_status": row["coverage_status"],
                        "participant_control": row["participant_control"],
                        "topic_reached": _parse_bool(row["topic_reached"]),
                    }
                )
                if row["section_code"] != expected["section_code"]:
                    failures.append("topic_code_mismatch")
                if row["coverage_status"] not in COVERAGE_STATUSES:
                    failures.append("missing_or_invalid_coverage_status")
                if row["participant_control"] not in {"none", "skip", "stop"}:
                    failures.append("missing_or_invalid_participant_control")
                if observed["topic_reached"] is None:
                    failures.append("missing_or_invalid_topic_reached")
            passed = not failures
            details.append(
                self._detail(
                    observation,
                    "TCR",
                    unit_type="protocol_research_topic",
                    unit_id=f"topic:{section_index}",
                    status=DETAIL_PASS if passed else DETAIL_FAIL,
                    passed=passed,
                    numerator=int(passed),
                    denominator=1,
                    expected={
                        "section_index": section_index,
                        "section_code": expected["section_code"],
                        "coverage_status": expected["coverage_status"],
                        "participant_control": expected["participant_control"],
                        "topic_reached": expected["topic_reached"],
                        "exactly_one_explicit_record": True,
                    },
                    observed=observed,
                    failures=failures,
                    sources=["tables/item_sources.csv"],
                )
            )
        return details

    def _not_evaluable_detail(
        self,
        observation: RunObservation,
        code: str,
        unit_type: str,
        unit_id: str,
        reason: str,
        sources: list[str],
    ) -> dict[str, Any]:
        return self._detail(
            observation,
            code,
            unit_type=unit_type,
            unit_id=unit_id,
            status=DETAIL_NOT_EVALUABLE,
            passed=None,
            numerator=0,
            denominator=0,
            expected={"evaluable_saved_observation": True},
            observed={"run_execution_status": observation.run_row["execution_status"]},
            failures=[reason],
            sources=sources,
        )

    def _detail(
        self,
        observation: RunObservation,
        code: str,
        *,
        unit_type: str,
        unit_id: str,
        status: str,
        passed: bool | None,
        numerator: int,
        denominator: int,
        expected: Any,
        observed: Any,
        failures: list[str],
        sources: list[str],
        manual: bool = False,
    ) -> dict[str, Any]:
        metric = self.metric_by_code[code]
        return {
            "calculation_id": self.calculation_id,
            "detail_order": 0,
            "metric_code": code,
            "metric_name": metric["name"],
            "calculation_mode": metric["calculation_mode"],
            "run_id": observation.run_id,
            "condition": observation.condition,
            "scenario_id": observation.scenario_id,
            "repetition": observation.run_row["repetition"],
            "pair_key": observation.run_row["pair_key"],
            "unit_type": unit_type,
            "unit_id": unit_id,
            "eligible": True,
            "evaluation_status": status,
            "passed": passed,
            "numerator_contribution": numerator,
            "denominator_contribution": denominator,
            "expected_json": _json_cell(expected),
            "observed_json": _json_cell(observed),
            "failure_reasons_json": _json_cell(failures),
            "source_paths_json": _json_cell(sources),
            "manual_coding_required": manual,
        }

    @staticmethod
    def _detail_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["scenario_id"],
            int(row["repetition"]),
            PAIRED_CONDITIONS.index(row["condition"]),
            row["run_id"],
            METRIC_CODES.index(row["metric_code"]),
            row["unit_type"],
            row["unit_id"],
        )

    def _summarise_runs(
        self, details: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in details:
            grouped[(row["run_id"], row["metric_code"])].append(row)
        observations = {row.run_id: row for row in self.run_observations}
        summaries = []
        for observation in self.run_observations:
            for code in METRIC_CODES:
                rows = grouped.get((observation.run_id, code), [])
                metric = self.metric_by_code[code]
                failures = sum(row["evaluation_status"] == DETAIL_FAIL for row in rows)
                not_evaluable = sum(
                    row["evaluation_status"] == DETAIL_NOT_EVALUABLE for row in rows
                )
                pending = sum(
                    row["evaluation_status"] == DETAIL_PENDING_MANUAL for row in rows
                )
                numerator = sum(int(row["numerator_contribution"]) for row in rows)
                denominator = sum(int(row["denominator_contribution"]) for row in rows)
                manual = code in MANUAL_METRICS
                if manual:
                    status = "PENDING_MANUAL"
                    value = None
                    reported_numerator: int | None = None
                    reported_denominator: int | None = (
                        denominator if code == "MP" else None
                    )
                elif not_evaluable:
                    status = "INCOMPLETE"
                    value = None
                    reported_numerator = numerator
                    reported_denominator = denominator
                elif denominator == 0:
                    status = "NOT_APPLICABLE"
                    value = None
                    reported_numerator = numerator
                    reported_denominator = denominator
                else:
                    status = "FAIL" if failures else "PASS"
                    value = _ratio(numerator, denominator)
                    reported_numerator = numerator
                    reported_denominator = denominator
                summaries.append(
                    {
                        "calculation_id": self.calculation_id,
                        "run_id": observation.run_id,
                        "condition": observation.condition,
                        "scenario_id": observation.scenario_id,
                        "repetition": observation.run_row["repetition"],
                        "pair_key": observation.run_row["pair_key"],
                        "run_execution_status": observation.run_row["execution_status"],
                        "validation_status": observation.run_row["validation_status"],
                        "formal_evidence_eligible": _parse_bool(
                            observation.run_row["formal_evidence_eligible"]
                        ),
                        "metric_code": code,
                        "metric_name": metric["name"],
                        "calculation_mode": metric["calculation_mode"],
                        "calculation_status": status,
                        "numerator": reported_numerator,
                        "denominator": reported_denominator,
                        "value": value,
                        "failure_count": failures,
                        "not_evaluable_count": not_evaluable,
                        "pending_manual_count": pending,
                        "detail_count": len(rows),
                        "manual_coding_required": manual,
                    }
                )
        return summaries

    def _summarise_conditions(
        self, run_rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in run_rows:
            grouped[(row["condition"], row["metric_code"])].append(row)
        output = []
        for condition in PAIRED_CONDITIONS:
            for code in METRIC_CODES:
                rows = grouped.get((condition, code), [])
                metric = self.metric_by_code[code]
                manual = code in MANUAL_METRICS
                incomplete_runs = sum(
                    row["calculation_status"] == "INCOMPLETE" for row in rows
                )
                pending_runs = sum(
                    row["calculation_status"] == "PENDING_MANUAL" for row in rows
                )
                failures = sum(int(row["failure_count"]) for row in rows)
                numerator = sum(
                    int(row["numerator"])
                    for row in rows
                    if row["numerator"] is not None
                )
                denominators = [
                    int(row["denominator"])
                    for row in rows
                    if row["denominator"] is not None
                ]
                denominator = sum(denominators)
                if manual:
                    status = "PENDING_MANUAL"
                    reported_numerator = None
                    reported_denominator = denominator if code == "MP" else None
                    value = None
                elif incomplete_runs:
                    status = "INCOMPLETE"
                    reported_numerator = numerator
                    reported_denominator = denominator
                    value = None
                elif denominator == 0:
                    status = "NOT_APPLICABLE"
                    reported_numerator = numerator
                    reported_denominator = denominator
                    value = None
                else:
                    status = "FAIL" if failures else "PASS"
                    reported_numerator = numerator
                    reported_denominator = denominator
                    value = _ratio(numerator, denominator)
                output.append(
                    {
                        "calculation_id": self.calculation_id,
                        "condition": condition,
                        "metric_code": code,
                        "metric_name": metric["name"],
                        "calculation_mode": metric["calculation_mode"],
                        "run_count": len(rows),
                        "completed_run_count": sum(
                            row["run_execution_status"] == "completed" for row in rows
                        ),
                        "formal_evidence_eligible_run_count": sum(
                            row["formal_evidence_eligible"] is True for row in rows
                        ),
                        "calculation_status": status,
                        "numerator": reported_numerator,
                        "denominator": reported_denominator,
                        "value": value,
                        "failure_count": failures,
                        "not_evaluable_run_count": incomplete_runs,
                        "pending_manual_run_count": pending_runs,
                        "manual_coding_required": manual,
                    }
                )
        return output

    def _summarise_pairs(self, run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in run_rows:
            grouped[(row["pair_key"], row["metric_code"])].append(row)
        pair_keys = sorted(
            {row["pair_key"] for row in run_rows},
            key=lambda value: (
                value.split("-")[0],
                int(value.rsplit("R", 1)[-1]),
            ),
        )
        output = []
        integrity_by_key = {
            row["pair_key"]: row for row in self.pair_integrity.get("pairs", [])
        }
        for pair_key in pair_keys:
            for code in METRIC_CODES:
                rows = grouped[(pair_key, code)]
                by_condition = {
                    condition: [row for row in rows if row["condition"] == condition]
                    for condition in PAIRED_CONDITIONS
                }
                if all(len(by_condition[condition]) == 1 for condition in PAIRED_CONDITIONS):
                    pairing_status = "PAIRED"
                elif any(len(by_condition[condition]) > 1 for condition in PAIRED_CONDITIONS):
                    pairing_status = "AMBIGUOUS"
                else:
                    pairing_status = "INCOMPLETE"
                mvp = by_condition["mvp"][0] if len(by_condition["mvp"]) == 1 else None
                baseline = (
                    by_condition["prompt_only_baseline"][0]
                    if len(by_condition["prompt_only_baseline"]) == 1
                    else None
                )
                sample = rows[0]
                pair_integrity = integrity_by_key.get(pair_key) or {}
                output.append(
                    {
                        "calculation_id": self.calculation_id,
                        "pair_key": pair_key,
                        "scenario_id": sample["scenario_id"],
                        "repetition": sample["repetition"],
                        "metric_code": code,
                        "metric_name": sample["metric_name"],
                        "calculation_mode": sample["calculation_mode"],
                        "pairing_status": pairing_status,
                        "mvp_run_ids_json": _json_cell(
                            [row["run_id"] for row in by_condition["mvp"]]
                        ),
                        "baseline_run_ids_json": _json_cell(
                            [
                                row["run_id"]
                                for row in by_condition["prompt_only_baseline"]
                            ]
                        ),
                        "mvp_calculation_status": (
                            mvp["calculation_status"] if mvp else None
                        ),
                        "mvp_numerator": mvp["numerator"] if mvp else None,
                        "mvp_denominator": mvp["denominator"] if mvp else None,
                        "mvp_value": mvp["value"] if mvp else None,
                        "baseline_calculation_status": (
                            baseline["calculation_status"] if baseline else None
                        ),
                        "baseline_numerator": (
                            baseline["numerator"] if baseline else None
                        ),
                        "baseline_denominator": (
                            baseline["denominator"] if baseline else None
                        ),
                        "baseline_value": baseline["value"] if baseline else None,
                        "formal_evidence_eligible": bool(
                            pairing_status == "PAIRED"
                            and pair_integrity.get("status") == "PASS"
                            and mvp["formal_evidence_eligible"] is True
                            and baseline["formal_evidence_eligible"] is True
                        ),
                        "manual_coding_required": code in MANUAL_METRICS,
                    }
                )
        return output

    def _summary_document(
        self,
        *,
        run_rows: list[dict[str, Any]],
        condition_rows: list[dict[str, Any]],
        pair_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        paired_keys = {
            row["pair_key"] for row in pair_rows if row["pairing_status"] == "PAIRED"
        }
        ambiguous_keys = {
            row["pair_key"]
            for row in pair_rows
            if row["pairing_status"] == "AMBIGUOUS"
        }
        incomplete_keys = {
            row["pair_key"]
            for row in pair_rows
            if row["pairing_status"] == "INCOMPLETE"
        }
        fairness_fail_keys = {
            row["pair_key"]
            for row in self.pair_integrity.get("pairs", [])
            if row.get("status") == "FAIRNESS_FAIL"
        }
        return {
            "schema_version": CALCULATOR_SCHEMA_VERSION,
            "calculation_id": self.calculation_id,
            "input_export_id": self.input_manifest["export_id"],
            "run_count": len(self.run_observations),
            "condition_counts": {
                condition: sum(
                    observation.condition == condition
                    for observation in self.run_observations
                )
                for condition in PAIRED_CONDITIONS
            },
            "pairing": {
                "paired_pair_count": len(paired_keys),
                "ambiguous_pair_count": len(ambiguous_keys),
                "incomplete_pair_count": len(incomplete_keys),
                "fairness_fail_pair_count": len(fairness_fail_keys),
                "ambiguous_pair_keys": sorted(ambiguous_keys),
                "incomplete_pair_keys": sorted(incomplete_keys),
                "fairness_fail_pair_keys": sorted(fairness_fail_keys),
            },
            "pair_integrity": self.pair_integrity,
            "condition_metrics": condition_rows,
            "automatic_metric_codes": list(AUTOMATIC_METRICS),
            "manual_metrics": {
                "UCR": "PENDING independent atomic-proposition coding; no text-similarity proxy was used.",
                "MP": "PENDING independent item judgement and consensus; no reviewer checkbox was substituted.",
            },
            "formal_evidence_eligible": bool(
                self.run_observations
                and all(
                    _parse_bool(row.run_row["formal_evidence_eligible"]) is True
                    for row in self.run_observations
                )
                and not ambiguous_keys
                and not incomplete_keys
                and not fairness_fail_keys
            ),
            "claim_boundary": (
                "This summary reports frozen automatic numerators, denominators, "
                "failures and mechanical pairing only. UCR, MP, blinded quality "
                "review and any comparative interpretation remain human tasks."
            ),
        }

    @staticmethod
    def _artifact_inventory(staging: Path) -> list[dict[str, Any]]:
        return [
            {
                "path": str(path.relative_to(staging)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(staging.rglob("*"))
            if path.is_file() and path.name != "calculation_manifest.json"
        ]

    def _retain_failed_attempt(self, error: Exception) -> None:
        try:
            manifest = {
                "schema_version": CALCULATOR_SCHEMA_VERSION,
                "calculation_id": self.calculation_id,
                "calculation_status": "failed",
                "started_at": self.started_at,
                "completed_at": _utc_now(),
                "calculator_module": "evaluation.metric_calculator",
                "input_export": {
                    "source_directory": str(self.export_directory),
                    "source_manifest_path": str(self.input_manifest_path),
                    "source_manifest_sha256": self.input_manifest_sha256,
                    "export_id": (
                        self.input_manifest.get("export_id")
                        if isinstance(self.input_manifest, dict)
                        else None
                    ),
                },
                "error": {"type": type(error).__name__, "message": str(error)},
                "claim_boundaries": {
                    "automatic_metrics_calculated": False,
                    "ucr_human_coding_performed": False,
                    "mp_human_coding_performed": False,
                    "comparative_superiority_claim_generated": False,
                    "formal_runs_started_by_calculator": False,
                    "database_or_model_used": False,
                },
            }
            _atomic_write_json(
                self.staging_directory / "calculation_manifest.json", manifest
            )
            if self.final_directory.exists():
                raise MetricCalculationError(
                    f"Cannot retain failed calculation over {self.final_directory}."
                )
            os.replace(self.staging_directory, self.final_directory)
        except Exception:
            shutil.rmtree(self.staging_directory, ignore_errors=True)


__all__ = [
    "AUTOMATIC_METRICS",
    "CALCULATOR_SCHEMA_VERSION",
    "EvaluationMetricCalculator",
    "MANUAL_METRICS",
    "MetricCalculationError",
]

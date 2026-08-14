"""Append-only blinded quality and manual-metric coding workflow.

The tools in this module consume a completed :mod:`evaluation.exporter` bundle.
They never execute either interview condition, call a model, edit the product
database, repair an observation, or infer a comparative winner.

The public coder material contains only opaque A/B labels.  The condition key
is stored in a separate private directory and is never needed to import or
compare independent coder submissions.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import secrets
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schemas import (
    EXPORT_TABLE_FIELDS,
    MVP_CONDITION,
    BASELINE_CONDITION,
    PAIRED_CONDITIONS,
    load_json,
    pair_integrity_from_run_rows,
    sha256_file,
)
from .execution_plan import load_pair_plan
from .validate_specs import SPEC_DIR, validate_all


QUALITY_REVIEW_TOOL_VERSION = "2.0"
QUALITY_REVIEW_CONTRACT_PATH = SPEC_DIR / "quality_review.v2.json"
BLIND_LABELS = ("A", "B")
UCR_LABELS = ("supported", "unsupported")
MP_LABELS = ("preserved", "not_preserved", "uncertain")

QUALITY_JUDGEMENT_FIELDS = (
    "a_q1_protocol_coverage",
    "b_q1_protocol_coverage",
    "a_q2_probing_relevance_neutrality",
    "b_q2_probing_relevance_neutrality",
    "a_q3_transcript_grounding_fidelity",
    "b_q3_transcript_grounding_fidelity",
    "a_q4_participant_clarity_control",
    "b_q4_participant_clarity_control",
    "a_q5_researcher_reviewability",
    "b_q5_researcher_reviewability",
    "overall_preference",
    "confidence",
    "short_rationale",
    "issue_flags_json",
)

UCR_CODING_FIELDS = (
    "review_package_id",
    "pair_key",
    "scenario_id",
    "repetition",
    "coder_id",
    "blind_key",
    "blind_label",
    "item_key",
    "proposition_index",
    "atomic_proposition",
    "support_label",
    "short_rationale",
    "completed_at",
)

MP_CODING_FIELDS = (
    "review_package_id",
    "pair_key",
    "scenario_id",
    "repetition",
    "coder_id",
    "blind_key",
    "blind_label",
    "item_key",
    "meaning_preservation_label",
    "short_rationale",
    "completed_at",
)

UCR_CONSENSUS_FIELDS = (
    "pair_key",
    "blind_label",
    "item_key",
    "coder_records_json",
    "segmentation_or_label_disagreement",
    "consensus_propositions_json",
    "consensus_note",
    "resolved_at",
)

MP_CONSENSUS_FIELDS = (
    "pair_key",
    "blind_label",
    "item_key",
    "coder_labels_json",
    "disagreement",
    "consensus_label",
    "consensus_note",
    "resolved_at",
)


class QualityReviewError(RuntimeError):
    """Raised when blinded material or human coding is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise QualityReviewError(f"Unsafe or blank {label}: {text!r}.")
    return text


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    field_tuple = tuple(fields)
    row_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(row_list, start=1):
        missing = set(field_tuple) - set(row)
        extra = set(row) - set(field_tuple)
        if missing or extra:
            raise QualityReviewError(
                f"CSV row {index} for {path.name} has schema drift; "
                f"missing={sorted(missing)}, extra={sorted(extra)}."
            )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(field_tuple),
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in row_list:
            writer.writerow(row)


def _read_csv(path: Path, fields: Iterable[str]) -> list[dict[str, str]]:
    field_tuple = tuple(fields)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != field_tuple:
                raise QualityReviewError(
                    f"CSV header drift in {path}; expected {field_tuple}, "
                    f"got {reader.fieldnames}."
                )
            return list(reader)
    except OSError as error:
        raise QualityReviewError(f"Cannot read CSV {path}: {error}") from error


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityReviewError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise QualityReviewError(f"{label} {path} must contain one JSON object.")
    return value


def _parse_json_list(value: Any, label: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError as error:
        raise QualityReviewError(f"{label} must be valid JSON: {error}") from error
    if not isinstance(parsed, list):
        raise QualityReviewError(f"{label} must contain a JSON list.")
    return parsed


def _parse_timezone_datetime(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise QualityReviewError(f"{label} is required.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise QualityReviewError(f"{label} must be ISO-8601: {text!r}.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QualityReviewError(f"{label} must include a timezone offset.")
    return text


def _artifact_inventory(root: Path, *, manifest_name: str = "manifest.json") -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != manifest_name
    ]


def _resolve_below(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise QualityReviewError(f"Absolute input path is forbidden: {relative}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise QualityReviewError(f"Input path escapes its package: {relative}") from error
    return resolved


def _verify_inventory(root: Path, manifest: dict[str, Any]) -> dict[str, tuple[int, str]]:
    inventory = manifest.get("artifact_inventory")
    if not isinstance(inventory, list):
        raise QualityReviewError("Input artifact_inventory must be a list.")
    recorded: dict[str, tuple[int, str]] = {}
    for entry in inventory:
        relative = str(entry.get("path") or "")
        if not relative or relative in recorded:
            raise QualityReviewError(f"Duplicate or blank inventory path: {relative!r}.")
        path = _resolve_below(root, relative)
        if path.is_symlink() or not path.is_file():
            raise QualityReviewError(f"Inventory file is missing or unsafe: {relative}.")
        actual = (path.stat().st_size, sha256_file(path))
        expected = (entry.get("bytes"), entry.get("sha256"))
        if actual != expected:
            raise QualityReviewError(
                f"Inventory mismatch for {relative}; expected {expected}, got {actual}."
            )
        recorded[relative] = actual
    manifest_path = root / "manifest.json"
    actual_paths = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != set(recorded):
        raise QualityReviewError(
            "Input package has missing or unlisted files; "
            f"missing={sorted(set(recorded) - actual_paths)}, "
            f"unlisted={sorted(actual_paths - set(recorded))}."
        )
    return recorded


def _score(value: str, *, allow_na: bool, label: str) -> str:
    text = str(value or "").strip()
    if allow_na and text.upper() in {"N/A", "NA"}:
        return "N/A"
    if text not in {"1", "2", "3", "4", "5"}:
        suffix = " or N/A" if allow_na else ""
        raise QualityReviewError(f"{label} must be 1-5{suffix}.")
    return text


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_flags(value: Any, label: str) -> str:
    flags = _parse_json_list(value, label)
    if any(not isinstance(flag, str) or not flag.strip() for flag in flags):
        raise QualityReviewError(f"{label} must be a JSON list of nonblank strings.")
    return _canonical_json([flag.strip() for flag in flags])


def _slug_from_text(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return text or fallback


class _ExportInput:
    """Read-only, integrity-checked view of one unified evaluation export."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.manifest_path = self.directory / "manifest.json"
        if not self.manifest_path.is_file():
            raise QualityReviewError(f"Export manifest is missing: {self.manifest_path}")
        self.manifest = _read_object(self.manifest_path, "export manifest")
        if self.manifest.get("export_status") != "completed":
            raise QualityReviewError("Only a completed evaluation export can be blinded.")
        self.manifest_sha256 = sha256_file(self.manifest_path)
        self.inventory = _verify_inventory(self.directory, self.manifest)
        self.tables = {
            name: _read_csv(
                self.directory / "tables" / f"{name}.csv",
                EXPORT_TABLE_FIELDS[name],
            )
            for name in EXPORT_TABLE_FIELDS
        }
        calculated_integrity = pair_integrity_from_run_rows(self.tables["runs"])
        if calculated_integrity != self.manifest.get("pair_integrity"):
            raise QualityReviewError(
                "Export pair-integrity data does not match the condition rows."
            )
        self.pair_integrity = calculated_integrity
        self.run_records: dict[str, dict[str, Any]] = {}
        for row in self.tables["runs"]:
            path = _resolve_below(self.directory, row["runner_record_path"])
            if not path.is_file() or sha256_file(path) != row["runner_record_sha256"]:
                raise QualityReviewError(
                    f"Runner record hash mismatch for {row['run_id']}."
                )
            record = _read_object(path, "exported runner record")
            if record.get("run_id") != row["run_id"] or record.get("condition") != row["condition"]:
                raise QualityReviewError(
                    f"Runner record identity mismatch for {row['run_id']}."
                )
            self.run_records[row["run_id"]] = record

    def verify_unchanged(self) -> None:
        if sha256_file(self.manifest_path) != self.manifest_sha256:
            raise QualityReviewError("Export manifest changed while building review material.")
        if _verify_inventory(self.directory, self.manifest) != self.inventory:
            raise QualityReviewError("Export inventory changed while building review material.")


class BlindedQualityReviewExporter:
    """Create condition-neutral A/B material and fixed coding templates."""

    def __init__(
        self,
        *,
        export_directory: Path,
        output_root: Path,
        random_source: Any | None = None,
    ):
        self.export_directory = Path(export_directory).resolve()
        self.output_root = Path(output_root).resolve()
        self.random_source = random_source or secrets.SystemRandom()

    def run(self) -> tuple[dict[str, Any], Path]:
        contracts = validate_all()
        quality_contract = load_json(QUALITY_REVIEW_CONTRACT_PATH)
        source = _ExportInput(self.export_directory)
        pair_selection = self._pair_rows(source, quality_contract)
        pairs = pair_selection["included_pairs"]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        package_id = f"quality-review-{timestamp}-{uuid.uuid4().hex[:8]}"
        final = self.output_root / package_id
        staging = self.output_root / f".{package_id}.tmp"
        if final.exists() or staging.exists():
            raise QualityReviewError(f"Refusing to overwrite review package: {final}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=False, exist_ok=False)

        try:
            _atomic_write_json(
                staging / "pair_selection_report.json",
                {
                    key: value
                    for key, value in pair_selection.items()
                    if key != "included_pairs"
                },
            )
            if pair_selection["package_readiness"] == (
                quality_contract["pair_selection"]["below_minimum_status"]
            ):
                manifest = {
                    "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                    "review_package_id": package_id,
                    "package_status": pair_selection["package_readiness"],
                    "created_at": _utc_now(),
                    "builder_module": "evaluation.quality_review",
                    "source_export": {
                        "directory": str(source.directory),
                        "export_id": source.manifest.get("export_id"),
                        "manifest_sha256": source.manifest_sha256,
                        "run_count": source.manifest.get("run_count"),
                        "observed_pair_count": pair_selection["observed_pair_count"],
                        "pair_count": pair_selection["included_pair_count"],
                    },
                    "pair_selection_report_path": "pair_selection_report.json",
                    "minimum_codable_pairs": pair_selection["minimum_codable_pairs"],
                    "recommended_codable_pairs": pair_selection[
                        "recommended_codable_pairs"
                    ],
                    "coder_material_directory": None,
                    "private_key_path": None,
                    "quality_coding_performed": False,
                    "ucr_coding_performed": False,
                    "mp_coding_performed": False,
                    "comparison_claim_generated": False,
                    "blocking_reason": (
                        "Fewer than eight complete, unique, input-matched and "
                        "independently validated pairs were available."
                    ),
                }
                source.verify_unchanged()
                manifest["artifact_inventory"] = _artifact_inventory(staging)
                _atomic_write_json(staging / "manifest.json", manifest)
                os.replace(staging, final)
                return manifest, final

            coder_root = staging / "coder_material"
            private_root = staging / "private"
            (coder_root / "pairs").mkdir(parents=True)
            private_root.mkdir(parents=True)

            assignments: list[dict[str, Any]] = []
            coder_pairs: list[dict[str, Any]] = []
            quality_rows: list[dict[str, Any]] = []
            ucr_rows: list[dict[str, Any]] = []
            mp_rows: list[dict[str, Any]] = []

            for pair in pairs:
                assignment = self._assignment(pair)
                material = self._pair_material(source, pair, assignment, package_id)
                pair_key = pair["pair_key"]
                json_path = coder_root / "pairs" / f"{pair_key}.json"
                html_path = coder_root / "pairs" / f"{pair_key}.html"
                _atomic_write_json(json_path, material)
                html_path.write_text(self._render_pair_html(material), encoding="utf-8")
                assignments.append(assignment)
                coder_pairs.append(
                    {
                        "pair_key": pair_key,
                        "scenario_id": pair["scenario_id"],
                        "repetition": pair["repetition"],
                        "blind_key": assignment["blind_key"],
                        "material_json": str(json_path.relative_to(coder_root)),
                        "material_html": str(html_path.relative_to(coder_root)),
                        "formal_pair_evidence_eligible": pair[
                            "formal_pair_evidence_eligible"
                        ],
                    }
                )
                quality_rows.append(
                    self._quality_template_row(
                        package_id, pair, assignment, quality_contract
                    )
                )
                pair_ucr, pair_mp = self._manual_metric_template_rows(
                    material, package_id
                )
                ucr_rows.extend(pair_ucr)
                mp_rows.extend(pair_mp)

            coding_fields = tuple(quality_contract["coding_sheet_fields"])
            _write_csv(
                coder_root / "quality_coding_sheet_template.csv",
                coding_fields,
                quality_rows,
            )
            _write_csv(
                coder_root / "ucr_atomic_proposition_coding_template.csv",
                UCR_CODING_FIELDS,
                ucr_rows,
            )
            _write_csv(
                coder_root / "mp_item_coding_template.csv",
                MP_CODING_FIELDS,
                mp_rows,
            )
            (coder_root / "README.md").write_text(
                self._coder_readme(quality_contract, pair_selection), encoding="utf-8"
            )
            (coder_root / "index.html").write_text(
                self._render_index(package_id, coder_pairs), encoding="utf-8"
            )

            coder_manifest = {
                "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                "review_package_id": package_id,
                "package_status": "ready_for_independent_coding",
                "created_at": _utc_now(),
                "pair_count": len(coder_pairs),
                "pair_count_status": pair_selection["package_readiness"],
                "minimum_codable_pairs": pair_selection["minimum_codable_pairs"],
                "recommended_codable_pairs": pair_selection[
                    "recommended_codable_pairs"
                ],
                "below_recommended_pair_count": (
                    len(coder_pairs) < pair_selection["recommended_codable_pairs"]
                ),
                "pairs": coder_pairs,
                "minimum_independent_coders": quality_contract[
                    "minimum_independent_coders"
                ],
                "quality_contract_sha256": contracts["contracts"]["quality_review"],
                "quality_coding_fields": list(coding_fields),
                "ucr_coding_fields": list(UCR_CODING_FIELDS),
                "mp_coding_fields": list(MP_CODING_FIELDS),
                "boundaries": quality_contract["boundaries"],
                "condition_identity_in_coder_material": False,
                "private_key_required_for_coding_or_import": False,
            }
            _atomic_write_json(coder_root / "coder_manifest.json", coder_manifest)
            self._assert_blinded(coder_root, source)

            key_document = {
                "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                "review_package_id": package_id,
                "created_at": _utc_now(),
                "warning": (
                    "PRIVATE CONDITION KEY. Do not give this file or directory to "
                    "coders before independent submissions are locked."
                ),
                "source_export": {
                    "export_id": source.manifest.get("export_id"),
                    "manifest_sha256": source.manifest_sha256,
                },
                "assignments": assignments,
            }
            key_path = private_root / "blinding_key.json"
            _atomic_write_json(key_path, key_document)
            try:
                key_path.chmod(0o600)
            except OSError:
                pass

            manifest = {
                "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                "review_package_id": package_id,
                "package_status": "completed",
                "created_at": _utc_now(),
                "builder_module": "evaluation.quality_review",
                "source_export": {
                    "directory": str(source.directory),
                    "export_id": source.manifest.get("export_id"),
                    "manifest_sha256": source.manifest_sha256,
                    "run_count": source.manifest.get("run_count"),
                    "observed_pair_count": pair_selection["observed_pair_count"],
                    "pair_count": len(pairs),
                },
                "pair_selection_report_path": "pair_selection_report.json",
                "pair_count_status": pair_selection["package_readiness"],
                "minimum_codable_pairs": pair_selection["minimum_codable_pairs"],
                "recommended_codable_pairs": pair_selection[
                    "recommended_codable_pairs"
                ],
                "excluded_pair_count": pair_selection["excluded_pair_count"],
                "quality_contract_sha256": contracts["contracts"]["quality_review"],
                "coder_material_directory": "coder_material",
                "private_key_path": "private/blinding_key.json",
                "coder_material_contains_condition_identity": False,
                "random_assignment_per_pair": True,
                "condition_key_separate_from_coder_material": True,
                "quality_coding_performed": False,
                "ucr_coding_performed": False,
                "mp_coding_performed": False,
                "comparison_claim_generated": False,
                "database_model_or_runner_used": False,
            }
            source.verify_unchanged()
            manifest["artifact_inventory"] = _artifact_inventory(staging)
            _atomic_write_json(staging / "manifest.json", manifest)
            os.replace(staging, final)
            return manifest, final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def _pair_rows(
        source: _ExportInput, contract: dict[str, Any]
    ) -> dict[str, Any]:
        run_rows = source.tables["runs"]
        rows_by_pair: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in run_rows:
            rows_by_pair[row["pair_key"]].append(row)
        _manifest, planned_pairs = load_pair_plan()
        planned_by_key = {pair.pair_key: pair for pair in planned_pairs}
        integrity_by_key = {
            row["pair_key"]: row for row in source.pair_integrity["pairs"]
        }
        pairs: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for pair_key, planned in planned_by_key.items():
            integrity = integrity_by_key.get(pair_key)
            rows = rows_by_pair.get(pair_key, [])
            if integrity is None:
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "PAIR_ABSENT_FROM_EXPORT",
                        "integrity_status": "INCOMPLETE",
                    }
                )
                continue
            if integrity["status"] != "PASS":
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": f"PAIR_INTEGRITY_{integrity['status']}",
                        "integrity_status": integrity["status"],
                        "mismatched_fields": integrity["mismatched_fields"],
                    }
                )
                continue
            if len(rows) != 2 or any(
                sum(row["condition"] == condition for row in rows) != 1
                for condition in PAIRED_CONDITIONS
            ):
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "PAIR_NOT_EXACTLY_ONE_RUN_PER_CONDITION",
                        "integrity_status": integrity["status"],
                    }
                )
                continue
            if any(row["execution_status"] != "completed" for row in rows):
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "EXECUTION_NOT_COMPLETED",
                        "integrity_status": integrity["status"],
                        "execution_statuses": sorted(
                            {row["execution_status"] for row in rows}
                        ),
                    }
                )
                continue
            if any(row["validation_status"] != "PASS" for row in rows):
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "VALIDATION_NOT_PASS",
                        "integrity_status": integrity["status"],
                        "validation_statuses": sorted(
                            {row["validation_status"] for row in rows}
                        ),
                    }
                )
                continue
            if integrity["formal_pair_evidence_eligible"] is not True:
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "FORMAL_PAIR_EVIDENCE_INELIGIBLE",
                        "integrity_status": integrity["status"],
                    }
                )
                continue
            scenario_ids = {row["scenario_id"] for row in rows}
            repetitions = {int(row["repetition"]) for row in rows}
            if scenario_ids != {planned.scenario_id} or repetitions != {
                planned.repetition
            }:
                excluded.append(
                    {
                        "pair_key": pair_key,
                        "reason_code": "REGISTERED_SCHEDULE_IDENTITY_MISMATCH",
                        "integrity_status": integrity["status"],
                    }
                )
                continue
            pairs.append(
                {
                    "pair_key": pair_key,
                    "scenario_id": planned.scenario_id,
                    "repetition": str(planned.repetition),
                    "rows_by_condition": {row["condition"]: row for row in rows},
                    "formal_pair_evidence_eligible": integrity[
                        "formal_pair_evidence_eligible"
                    ],
                }
            )
        for pair_key in sorted(set(integrity_by_key) - set(planned_by_key)):
            excluded.append(
                {
                    "pair_key": pair_key,
                    "reason_code": "UNREGISTERED_PAIR_KEY",
                    "integrity_status": integrity_by_key[pair_key]["status"],
                }
            )

        rules = contract["pair_selection"]
        included = sorted(
            pairs, key=lambda pair: (pair["scenario_id"], int(pair["repetition"]))
        )
        included_count = len(included)
        minimum = rules["minimum_codable_pairs"]
        recommended = rules["recommended_codable_pairs"]
        if included_count < minimum:
            readiness = rules["below_minimum_status"]
        elif included_count < recommended:
            readiness = "ready_below_recommended_pair_count"
        else:
            readiness = "ready_recommended_pair_count_met"
        return {
            "selection_policy_version": contract["contract_version"],
            "observed_pair_count": len(source.pair_integrity["pairs"]),
            "registered_pair_count": len(planned_pairs),
            "included_pair_count": included_count,
            "excluded_pair_count": len(excluded),
            "minimum_codable_pairs": minimum,
            "recommended_codable_pairs": recommended,
            "minimum_threshold_met": included_count >= minimum,
            "recommended_target_met": included_count >= recommended,
            "package_readiness": readiness,
            "included_pair_keys": [pair["pair_key"] for pair in included],
            "excluded_pairs": sorted(excluded, key=lambda row: row["pair_key"]),
            "included_pairs": included,
        }

    def _assignment(self, pair: dict[str, Any]) -> dict[str, Any]:
        if self.random_source.randrange(2) == 0:
            mapping = {"A": MVP_CONDITION, "B": BASELINE_CONDITION}
        else:
            mapping = {"A": BASELINE_CONDITION, "B": MVP_CONDITION}
        rows = pair["rows_by_condition"]
        return {
            "pair_key": pair["pair_key"],
            "scenario_id": pair["scenario_id"],
            "repetition": pair["repetition"],
            "blind_key": f"BK-{uuid.uuid4().hex.upper()}",
            "A": {
                "condition": mapping["A"],
                "run_id": rows[mapping["A"]]["run_id"],
            },
            "B": {
                "condition": mapping["B"],
                "run_id": rows[mapping["B"]]["run_id"],
            },
        }

    def _pair_material(
        self,
        source: _ExportInput,
        pair: dict[str, Any],
        assignment: dict[str, Any],
        package_id: str,
    ) -> dict[str, Any]:
        records = {
            label: source.run_records[assignment[label]["run_id"]]
            for label in BLIND_LABELS
        }
        protocol_hashes = {
            record.get("protocol_snapshot_sha256") for record in records.values()
        }
        protocol_snapshots = [record.get("protocol_snapshot") for record in records.values()]
        if len(protocol_hashes) != 1 or protocol_snapshots[0] != protocol_snapshots[1]:
            raise QualityReviewError(
                f"Pair {pair['pair_key']} does not share one Protocol snapshot."
            )
        protocol = self._protocol_material(protocol_snapshots[0] or {})
        return {
            "schema_version": QUALITY_REVIEW_TOOL_VERSION,
            "review_package_id": package_id,
            "pair_key": pair["pair_key"],
            "scenario_id": pair["scenario_id"],
            "repetition": pair["repetition"],
            "blind_key": assignment["blind_key"],
            "protocol_reference_common_to_a_and_b": protocol,
            "A": self._condition_material(records["A"]),
            "B": self._condition_material(records["B"]),
            "coder_notice": (
                "A and B are opaque random labels. Judge only the displayed material. "
                "The five quality questions do not replace UCR or Meaning Preservation coding."
            ),
        }

    @staticmethod
    def _protocol_material(snapshot: dict[str, Any]) -> dict[str, Any]:
        configuration = snapshot.get("configuration") or {}
        topics = []
        for section in configuration.get("sections") or []:
            index = section.get("index")
            if not isinstance(index, int) or not 1 <= index <= 5:
                continue
            topics.append(
                {
                    "topic": section.get("label") or section.get("code"),
                    "primary_question": section.get("primary_question"),
                    "required_information": section.get("required_information") or [],
                    "interaction_boundary": section.get("interaction_boundary"),
                }
            )
        return {
            "title": configuration.get("title"),
            "purpose": configuration.get("purpose"),
            "participant_controls": "The participant may Skip a topic or Stop the interview.",
            "topics": topics,
        }

    @staticmethod
    def _condition_material(record: dict[str, Any]) -> dict[str, Any]:
        snapshot = record.get("final_database_snapshot") or {}
        messages = sorted(
            snapshot.get("messages") or [],
            key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)),
        )
        transcript = []
        transcript_labels: dict[Any, str] = {}
        for index, message in enumerate(messages, start=1):
            label = f"T{index:02d}"
            transcript_labels[message.get("id")] = label
            sender = str(message.get("sender") or "").lower()
            transcript.append(
                {
                    "label": label,
                    "speaker": "Participant" if sender == "participant" else "Interviewer",
                    "topic": message.get("section"),
                    "text": message.get("content"),
                }
            )

        probes = []
        message_positions = {message.get("id"): index for index, message in enumerate(messages)}
        for decision in snapshot.get("agent_decisions") or []:
            action = decision.get("selected_action") or decision.get("action")
            if action not in {"ask_follow_up", "boundary_response", "skip", "stop"}:
                continue
            source_id = decision.get("source_message_id") or decision.get("message_id")
            position = message_positions.get(source_id)
            if position is None:
                continue
            response = next(
                (
                    candidate
                    for candidate in messages[position + 1 :]
                    if str(candidate.get("sender") or "").lower() != "participant"
                ),
                None,
            )
            if response:
                probes.append(
                    {
                        "topic": response.get("section") or decision.get("section"),
                        "response_label": transcript_labels.get(response.get("id")),
                        "text": response.get("content"),
                    }
                )

        items = []
        for item_order, item in enumerate(snapshot.get("digest_items") or [], start=1):
            source_labels = [
                transcript_labels[source_id]
                for source_id in item.get("source_message_ids") or []
                if source_id in transcript_labels
            ]
            source_extracts = [
                {
                    "source_label": transcript_labels[message.get("id")],
                    "text": message.get("content"),
                }
                for message in messages
                if message.get("id") in (item.get("source_message_ids") or [])
            ]
            eligible_manual = bool(
                item.get("is_included") is True
                or item.get("review_status") in {"approved", "edited"}
            )
            items.append(
                {
                    "item_key": f"I{item_order:02d}",
                    "topic": item.get("section_code"),
                    "coverage_status": item.get("coverage_status"),
                    "participant_control": item.get("participant_control"),
                    "topic_reached": item.get("topic_reached"),
                    "missing_information": item.get("missing_information") or [],
                    "structured_draft_evidence": item.get("generated_text") or "",
                    "final_reviewed_evidence": (
                        item.get("evidence_text") or item.get("final_text") or ""
                    ),
                    "review_state": item.get("review_status"),
                    "source_labels": source_labels,
                    "source_extracts": source_extracts,
                    "eligible_for_ucr_and_mp": eligible_manual,
                }
            )
        return {
            "full_transcript": transcript,
            "participant_facing_follow_ups_and_controls": probes,
            "structured_evidence_and_limitations": items,
        }

    @staticmethod
    def _quality_template_row(
        package_id: str,
        pair: dict[str, Any],
        assignment: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        row = {field: "" for field in contract["coding_sheet_fields"]}
        row.update(
            {
                "review_package_id": package_id,
                "pair_key": pair["pair_key"],
                "scenario_id": pair["scenario_id"],
                "repetition": pair["repetition"],
                "blind_key": assignment["blind_key"],
                "issue_flags_json": "[]",
            }
        )
        return row

    @staticmethod
    def _manual_metric_template_rows(
        material: dict[str, Any], package_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ucr_rows: list[dict[str, Any]] = []
        mp_rows: list[dict[str, Any]] = []
        base = {
            "review_package_id": package_id,
            "pair_key": material["pair_key"],
            "scenario_id": material["scenario_id"],
            "repetition": material["repetition"],
            "coder_id": "",
            "blind_key": material["blind_key"],
        }
        for blind_label in BLIND_LABELS:
            for item in material[blind_label]["structured_evidence_and_limitations"]:
                if not item["eligible_for_ucr_and_mp"]:
                    continue
                ucr_rows.append(
                    {
                        **base,
                        "blind_label": blind_label,
                        "item_key": item["item_key"],
                        "proposition_index": "1",
                        "atomic_proposition": "",
                        "support_label": "",
                        "short_rationale": "",
                        "completed_at": "",
                    }
                )
                mp_rows.append(
                    {
                        **base,
                        "blind_label": blind_label,
                        "item_key": item["item_key"],
                        "meaning_preservation_label": "",
                        "short_rationale": "",
                        "completed_at": "",
                    }
                )
        return ucr_rows, mp_rows

    @staticmethod
    def _assert_blinded(coder_root: Path, source: _ExportInput) -> None:
        forbidden = {
            "mvp",
            "prompt_only_baseline",
            "prompt-only baseline",
            "langgraph",
        }
        for row in source.tables["runs"]:
            forbidden.add(str(row["run_id"]).lower())
            if row.get("code_commit"):
                forbidden.add(str(row["code_commit"]).lower())
            if row.get("model_id"):
                forbidden.add(str(row["model_id"]).lower())
        for path in coder_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".html", ".md", ".csv"}:
                continue
            content = path.read_text(encoding="utf-8").lower()
            leaked = sorted(token for token in forbidden if token and token in content)
            if leaked:
                raise QualityReviewError(
                    f"Coder material leaks excluded condition/provenance labels in "
                    f"{path.relative_to(coder_root)}: {leaked}."
                )

    @staticmethod
    def _coder_readme(
        contract: dict[str, Any], pair_selection: dict[str, Any]
    ) -> str:
        criteria = "\n".join(
            f"- {row['question_id']} {row['name']}: {row['prompt']}"
            for row in contract["criteria"]
        )
        scope_notice = (
            f"This package contains {pair_selection['included_pair_count']} complete "
            "matched pairs. The preregistered recommended target is "
            f"{pair_selection['recommended_codable_pairs']}."
        )
        return f"""# Independent blinded coding instructions

Open `index.html` and review each pair as opaque material A and B. Do not seek
or request the private assignment key. Complete one personal copy of each CSV
independently before any discussion with another coder.

{scope_notice}

## Fixed five-question quality review

{criteria}

Use scores 1-5. Q2 may be `N/A` only when neither A nor B contains a probe.
Record `overall_preference` as `A`, `B`, or `Tie`; confidence as 1-3; add a
short rationale; and keep `issue_flags_json` as a valid JSON list such as `[]`
or `["unsupported wording"]`. Do not edit metadata columns or the header.

## UCR atomic-proposition coding

Use `ucr_atomic_proposition_coding_template.csv` only for the listed eligible
items. Split each final reviewed evidence text into independently supportable
assertions. Duplicate the seed row as needed and number propositions 1, 2, 3...
within each item. Label each proposition `supported` only when its displayed
participant source directly entails or clearly paraphrases it; otherwise label
it `unsupported`.

## Meaning Preservation

Use `mp_item_coding_template.csv`. Label each eligible item `preserved`,
`not_preserved`, or `uncertain` by comparing final reviewed evidence with the
displayed participant sources.

The five quality questions, UCR, and Meaning Preservation are three separate
records. One cannot substitute for another. Save timestamps in ISO-8601 with a
timezone, for example `2026-08-06T20:00:00+08:00`.
"""

    @staticmethod
    def _render_index(package_id: str, pairs: list[dict[str, Any]]) -> str:
        links = "\n".join(
            f'<li><a href="pairs/{html.escape(pair["pair_key"])}.html">'
            f'{html.escape(pair["pair_key"])}</a></li>'
            for pair in pairs
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Blinded review</title>
<style>body{{font:16px/1.5 Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 20px}}
h1{{font-size:28px}}a{{color:#174ea6}}code{{background:#f3f4f6;padding:2px 5px}}</style>
</head><body><h1>Blinded paired review</h1>
<p>Package <code>{html.escape(package_id)}</code>. A and B are opaque random labels.</p>
<ol>{links}</ol><p>Complete a personal copy of the fixed CSV sheets independently.</p>
</body></html>"""

    @staticmethod
    def _render_pair_html(material: dict[str, Any]) -> str:
        protocol = material["protocol_reference_common_to_a_and_b"]
        topic_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(topic.get("topic") or "")),
                html.escape(str(topic.get("primary_question") or "")),
                html.escape(", ".join(topic.get("required_information") or [])),
            )
            for topic in protocol["topics"]
        )

        def render_side(label: str) -> str:
            side = material[label]
            transcript = "".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(str(row.get("label") or "")),
                    html.escape(str(row.get("speaker") or "")),
                    html.escape(str(row.get("topic") or "")),
                    html.escape(str(row.get("text") or "")),
                )
                for row in side["full_transcript"]
            )
            probes = "".join(
                "<li><strong>{}</strong> {} {}</li>".format(
                    html.escape(str(row.get("topic") or "")),
                    html.escape(str(row.get("response_label") or "")),
                    html.escape(str(row.get("text") or "")),
                )
                for row in side["participant_facing_follow_ups_and_controls"]
            ) or "<li>None recorded in this material.</li>"
            items = "".join(
                "<article><h4>{} - {}</h4><p><strong>Provisional Protocol coverage:</strong> {}</p>"
                "<p><strong>Participant control:</strong> {} · <strong>Topic reached:</strong> {}</p>"
                "<p><strong>Missing/limitations:</strong> {}</p>"
                "<p><strong>Structured draft:</strong> {}</p>"
                "<p><strong>Final reviewed evidence:</strong> {}</p>"
                "<p><strong>Sources:</strong> {}</p>{}</article>".format(
                    html.escape(str(item.get("item_key") or "")),
                    html.escape(str(item.get("topic") or "")),
                    html.escape(str(item.get("coverage_status") or "")),
                    html.escape(str(item.get("participant_control") or "none")),
                    html.escape(str(item.get("topic_reached"))),
                    html.escape(", ".join(item.get("missing_information") or []) or "None recorded"),
                    html.escape(str(item.get("structured_draft_evidence") or "Unavailable")),
                    html.escape(str(item.get("final_reviewed_evidence") or "Not retained")),
                    html.escape(", ".join(item.get("source_labels") or []) or "None"),
                    "".join(
                        "<blockquote><strong>{}</strong> {}</blockquote>".format(
                            html.escape(str(source.get("source_label") or "")),
                            html.escape(str(source.get("text") or "")),
                        )
                        for source in item.get("source_extracts") or []
                    ),
                )
                for item in side["structured_evidence_and_limitations"]
            )
            return f"""<section><h2>Material {label}</h2>
<h3>Full transcript</h3><table><thead><tr><th>Label</th><th>Speaker</th><th>Topic</th><th>Text</th></tr></thead><tbody>{transcript}</tbody></table>
<h3>Participant-facing follow-ups and controls</h3><ul>{probes}</ul>
<h3>Structured evidence, sources and limitations</h3>{items}</section>"""

        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(material['pair_key'])} blinded review</title><style>
body{{font:15px/1.5 Arial,sans-serif;max-width:1180px;margin:30px auto;padding:0 22px;color:#202124}}
h1,h2,h3{{color:#183153}}table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}
th,td{{border:1px solid #c9ced6;padding:8px;vertical-align:top;text-align:left}}th{{background:#eef3f8}}
section{{margin-top:42px;border-top:4px solid #183153;padding-top:18px}}article{{border:1px solid #d7dce2;border-radius:6px;padding:12px 16px;margin:14px 0}}
blockquote{{margin:8px 0;padding:8px 12px;border-left:4px solid #8aa4bf;background:#f7f9fb}}
.notice{{background:#fff6d8;border:1px solid #e5c75a;padding:12px}}</style></head><body>
<h1>{html.escape(material['pair_key'])}: blinded A/B material</h1>
<p class="notice">{html.escape(material['coder_notice'])}</p>
<h2>Protocol reference common to A and B</h2><p>{html.escape(str(protocol.get('purpose') or ''))}</p>
<table><thead><tr><th>Topic</th><th>Primary question</th><th>Required information</th></tr></thead><tbody>{topic_rows}</tbody></table>
{render_side('A')}{render_side('B')}</body></html>"""


class CoderSubmissionImporter:
    """Validate and lock one independent quality, UCR, or MP submission."""

    def __init__(
        self,
        *,
        coder_material_directory: Path,
        coding_sheet: Path,
        coding_type: str,
        output_root: Path,
    ):
        self.coder_root = Path(coder_material_directory).resolve()
        self.coding_sheet = Path(coding_sheet).resolve()
        self.coding_type = str(coding_type).lower()
        self.output_root = Path(output_root).resolve()
        if self.coding_type not in {"quality", "ucr", "mp"}:
            raise QualityReviewError("coding_type must be quality, ucr, or mp.")

    def run(self) -> tuple[dict[str, Any], Path]:
        manifest_path = self.coder_root / "coder_manifest.json"
        manifest = _read_object(manifest_path, "coder manifest")
        package_id = _safe_id(manifest.get("review_package_id"), "review_package_id")
        if manifest.get("package_status") != "ready_for_independent_coding":
            raise QualityReviewError("Coder package is not ready for independent coding.")
        fields = self._fields(manifest)
        rows = _read_csv(self.coding_sheet, fields)
        normalized, coder_id = self._validate_rows(rows, manifest)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        import_id = f"{self.coding_type}-coding-{timestamp}-{uuid.uuid4().hex[:8]}"
        final = self.output_root / import_id
        staging = self.output_root / f".{import_id}.tmp"
        if final.exists() or staging.exists():
            raise QualityReviewError(f"Refusing to overwrite coder import: {final}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=False, exist_ok=False)
        try:
            raw_path = staging / "raw_submission.csv"
            shutil.copyfile(self.coding_sheet, raw_path)
            normalized_path = staging / "coder_results.csv"
            _write_csv(normalized_path, fields, normalized)
            import_manifest = {
                "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                "import_id": import_id,
                "import_status": "locked",
                "coding_type": self.coding_type,
                "created_at": _utc_now(),
                "review_package_id": package_id,
                "coder_id": coder_id,
                "row_count": len(normalized),
                "source_coder_manifest_sha256": sha256_file(manifest_path),
                "raw_submission_sha256": sha256_file(raw_path),
                "normalized_results_sha256": sha256_file(normalized_path),
                "condition_key_read": False,
                "condition_identity_present": False,
                "independent_submission_locked": True,
                "consensus_performed": False,
                "comparison_claim_generated": False,
            }
            import_manifest["artifact_inventory"] = _artifact_inventory(staging)
            _atomic_write_json(staging / "manifest.json", import_manifest)
            os.replace(staging, final)
            return import_manifest, final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _fields(self, manifest: dict[str, Any]) -> tuple[str, ...]:
        if self.coding_type == "quality":
            return tuple(manifest.get("quality_coding_fields") or ())
        if self.coding_type == "ucr":
            return tuple(manifest.get("ucr_coding_fields") or ())
        return tuple(manifest.get("mp_coding_fields") or ())

    def _validate_rows(
        self, rows: list[dict[str, str]], manifest: dict[str, Any]
    ) -> tuple[list[dict[str, str]], str]:
        package_id = manifest["review_package_id"]
        pairs = {row["pair_key"]: row for row in manifest.get("pairs") or []}
        if self.coding_type == "quality":
            if len(rows) != len(pairs) or {row["pair_key"] for row in rows} != set(pairs):
                raise QualityReviewError("Quality sheet must contain exactly one row per pair.")
        elif not rows:
            raise QualityReviewError(
                f"{self.coding_type.upper()} sheet has no eligible coding row."
            )

        expected_manual_units: set[tuple[str, str, str]] = set()
        if self.coding_type == "ucr":
            template_rows = _read_csv(
                self.coder_root / "ucr_atomic_proposition_coding_template.csv",
                UCR_CODING_FIELDS,
            )
            expected_manual_units = {
                (row["pair_key"], row["blind_label"], row["item_key"])
                for row in template_rows
            }
        elif self.coding_type == "mp":
            template_rows = _read_csv(
                self.coder_root / "mp_item_coding_template.csv",
                MP_CODING_FIELDS,
            )
            expected_manual_units = {
                (row["pair_key"], row["blind_label"], row["item_key"])
                for row in template_rows
            }

        coder_ids = {str(row.get("coder_id") or "").strip() for row in rows}
        if "" in coder_ids or len(coder_ids) != 1:
            raise QualityReviewError("One submission must use one nonblank coder_id.")
        coder_id = next(iter(coder_ids))
        normalized: list[dict[str, str]] = []
        seen: set[tuple[Any, ...]] = set()
        ucr_indices: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for row_number, row in enumerate(rows, start=2):
            pair_key = row.get("pair_key") or ""
            pair = pairs.get(pair_key)
            if pair is None:
                raise QualityReviewError(f"Row {row_number} has unknown pair_key {pair_key!r}.")
            expected_metadata = {
                "review_package_id": package_id,
                "pair_key": pair_key,
                "scenario_id": str(pair["scenario_id"]),
                "repetition": str(pair["repetition"]),
                "blind_key": str(pair["blind_key"]),
            }
            for field, expected in expected_metadata.items():
                if str(row.get(field) or "").strip() != expected:
                    raise QualityReviewError(
                        f"Row {row_number} changed locked metadata {field}."
                    )
            clean = {field: str(row.get(field) or "").strip() for field in row}
            clean["coder_id"] = coder_id
            clean["completed_at"] = _parse_timezone_datetime(
                clean.get("completed_at"), f"row {row_number} completed_at"
            )
            if self.coding_type == "quality":
                self._validate_quality_row(clean, row_number)
                identity = (pair_key,)
            elif self.coding_type == "ucr":
                identity = self._validate_ucr_row(clean, row_number, ucr_indices)
                if identity[:3] not in expected_manual_units:
                    raise QualityReviewError(
                        f"Row {row_number} adds an unregistered UCR item {identity[:3]}."
                    )
            else:
                identity = self._validate_mp_row(clean, row_number)
                if identity not in expected_manual_units:
                    raise QualityReviewError(
                        f"Row {row_number} adds an unregistered MP item {identity}."
                    )
            if identity in seen:
                raise QualityReviewError(f"Duplicate coding unit at row {row_number}: {identity}.")
            seen.add(identity)
            normalized.append(clean)
        if self.coding_type == "ucr":
            for key, indices in ucr_indices.items():
                expected = list(range(1, len(indices) + 1))
                if sorted(indices) != expected:
                    raise QualityReviewError(
                        f"UCR proposition indices for {key} must be consecutive {expected}."
                    )
            if set(ucr_indices) != expected_manual_units:
                raise QualityReviewError(
                    "UCR submission must code every and only the eligible blinded item."
                )
        elif self.coding_type == "mp":
            if seen != expected_manual_units:
                raise QualityReviewError(
                    "MP submission must code every and only the eligible blinded item."
                )
        return normalized, coder_id

    @staticmethod
    def _validate_quality_row(row: dict[str, str], row_number: int) -> None:
        for field in QUALITY_JUDGEMENT_FIELDS[:10]:
            row[field] = _score(
                row[field],
                allow_na="q2_" in field,
                label=f"row {row_number} {field}",
            )
        if row["overall_preference"] not in {"A", "B", "Tie"}:
            raise QualityReviewError(
                f"Row {row_number} overall_preference must be A, B, or Tie."
            )
        if row["confidence"] not in {"1", "2", "3"}:
            raise QualityReviewError(f"Row {row_number} confidence must be 1-3.")
        if not row["short_rationale"]:
            raise QualityReviewError(f"Row {row_number} short_rationale is required.")
        row["issue_flags_json"] = _canonical_flags(
            row["issue_flags_json"], f"row {row_number} issue_flags_json"
        )

    @staticmethod
    def _validate_ucr_row(
        row: dict[str, str],
        row_number: int,
        indices: dict[tuple[str, str, str], list[int]],
    ) -> tuple[Any, ...]:
        if row["blind_label"] not in BLIND_LABELS:
            raise QualityReviewError(f"Row {row_number} blind_label must be A or B.")
        item_key = _safe_id(row["item_key"], f"row {row_number} item_key")
        try:
            proposition_index = int(row["proposition_index"])
        except ValueError as error:
            raise QualityReviewError(
                f"Row {row_number} proposition_index must be a positive integer."
            ) from error
        if proposition_index < 1:
            raise QualityReviewError(
                f"Row {row_number} proposition_index must be a positive integer."
            )
        if not row["atomic_proposition"]:
            raise QualityReviewError(f"Row {row_number} atomic_proposition is required.")
        if row["support_label"] not in UCR_LABELS:
            raise QualityReviewError(
                f"Row {row_number} support_label must be supported or unsupported."
            )
        if not row["short_rationale"]:
            raise QualityReviewError(f"Row {row_number} short_rationale is required.")
        key = (row["pair_key"], row["blind_label"], item_key)
        indices[key].append(proposition_index)
        return (*key, proposition_index)

    @staticmethod
    def _validate_mp_row(row: dict[str, str], row_number: int) -> tuple[Any, ...]:
        if row["blind_label"] not in BLIND_LABELS:
            raise QualityReviewError(f"Row {row_number} blind_label must be A or B.")
        item_key = _safe_id(row["item_key"], f"row {row_number} item_key")
        if row["meaning_preservation_label"] not in MP_LABELS:
            raise QualityReviewError(
                f"Row {row_number} meaning_preservation_label must be one of {MP_LABELS}."
            )
        if not row["short_rationale"]:
            raise QualityReviewError(f"Row {row_number} short_rationale is required.")
        return (row["pair_key"], row["blind_label"], item_key)


class QualityConsensusReporter:
    """Compare locked quality submissions and retain all disagreements."""

    def __init__(
        self,
        *,
        coder_import_directories: Iterable[Path],
        output_root: Path,
        consensus_sheet: Path | None = None,
        blinding_key: Path | None = None,
    ):
        self.import_directories = tuple(Path(path).resolve() for path in coder_import_directories)
        self.output_root = Path(output_root).resolve()
        self.consensus_sheet = Path(consensus_sheet).resolve() if consensus_sheet else None
        self.blinding_key = Path(blinding_key).resolve() if blinding_key else None

    def run(self) -> tuple[dict[str, Any], Path]:
        imports = _load_coder_imports(self.import_directories, "quality")
        package_id, coder_ids = _shared_import_identity(imports)
        if len(coder_ids) < 2:
            raise QualityReviewError("Quality consensus requires at least two independent coders.")
        results = _group_quality_results(imports)
        supplied = self._supplied_consensus()
        created_at = _utc_now()
        rows: list[dict[str, Any]] = []
        unresolved = 0
        for pair_key in sorted(results):
            coder_rows = results[pair_key]
            for field in QUALITY_JUDGEMENT_FIELDS:
                labels = {coder_id: row[field] for coder_id, row in sorted(coder_rows.items())}
                disagreement = len(set(labels.values())) > 1
                key = (pair_key, field)
                if disagreement:
                    supplied_row = supplied.get(key)
                    if supplied_row:
                        if supplied_row.get("coder_labels_json") != _canonical_json(labels):
                            raise QualityReviewError(
                                f"{pair_key} {field} coder labels changed in the consensus sheet."
                            )
                        consensus_label = _validate_quality_consensus_label(
                            field, supplied_row["consensus_label"]
                        )
                        consensus_note = str(supplied_row["consensus_note"] or "").strip()
                        resolved_at = _parse_timezone_datetime(
                            supplied_row["resolved_at"], f"{pair_key} {field} resolved_at"
                        )
                        if not consensus_note:
                            raise QualityReviewError(
                                f"{pair_key} {field} consensus_note is required."
                            )
                    else:
                        consensus_label = ""
                        consensus_note = ""
                        resolved_at = ""
                        unresolved += 1
                else:
                    consensus_label = next(iter(labels.values()))
                    consensus_note = "Independent labels agree; no discussion required."
                    resolved_at = created_at
                rows.append(
                    {
                        "pair_key": pair_key,
                        "field_name": field,
                        "coder_labels_json": _canonical_json(labels),
                        "disagreement": _bool_text(disagreement),
                        "consensus_label": consensus_label,
                        "consensus_note": consensus_note,
                        "resolved_at": resolved_at,
                    }
                )
        if supplied and set(supplied) != {
            (row["pair_key"], row["field_name"])
            for row in rows
            if row["disagreement"] == "true"
        }:
            raise QualityReviewError(
                "Supplied quality consensus sheet does not match every and only disagreement."
            )
        return self._write_report(
            package_id=package_id,
            coder_ids=coder_ids,
            imports=imports,
            rows=rows,
            fields=tuple(load_json(QUALITY_REVIEW_CONTRACT_PATH)["consensus_report_fields"]),
            unresolved=unresolved,
            coding_type="quality",
        )

    def _supplied_consensus(self) -> dict[tuple[str, str], dict[str, str]]:
        if self.consensus_sheet is None:
            return {}
        fields = tuple(load_json(QUALITY_REVIEW_CONTRACT_PATH)["consensus_report_fields"])
        rows = _read_csv(self.consensus_sheet, fields)
        supplied: dict[tuple[str, str], dict[str, str]] = {}
        for row in rows:
            if str(row.get("disagreement") or "").strip().lower() != "true":
                continue
            key = (row["pair_key"], row["field_name"])
            if key in supplied:
                raise QualityReviewError(f"Duplicate supplied consensus row: {key}.")
            supplied[key] = row
        return supplied

    def _write_report(
        self,
        *,
        package_id: str,
        coder_ids: set[str],
        imports: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        fields: tuple[str, ...],
        unresolved: int,
        coding_type: str,
    ) -> tuple[dict[str, Any], Path]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report_id = f"{coding_type}-consensus-{timestamp}-{uuid.uuid4().hex[:8]}"
        final = self.output_root / report_id
        staging = self.output_root / f".{report_id}.tmp"
        if final.exists() or staging.exists():
            raise QualityReviewError(f"Refusing to overwrite consensus report: {final}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=False, exist_ok=False)
        try:
            _write_csv(staging / "consensus_report.csv", fields, rows)
            _copy_independent_inputs(staging, imports)
            disagreement_count = sum(
                row.get("disagreement") == "true"
                or row.get("segmentation_or_label_disagreement") == "true"
                for row in rows
            )
            summary = {
                "report_id": report_id,
                "review_package_id": package_id,
                "coding_type": coding_type,
                "coder_ids": sorted(coder_ids),
                "independent_coder_count": len(coder_ids),
                "field_record_count": len(rows),
                "initial_disagreement_count": disagreement_count,
                "unresolved_disagreement_count": unresolved,
                "report_status": "completed" if unresolved == 0 else "awaiting_consensus",
                "condition_key_read": False,
                "comparison_claim_generated": False,
            }
            _atomic_write_json(staging / "agreement_summary.json", summary)
            if self.blinding_key:
                if unresolved:
                    raise QualityReviewError(
                        "The condition key cannot be used until all consensus rows are resolved."
                    )
                _write_deblinded_key(staging, self.blinding_key, package_id, rows)
                summary["condition_key_read"] = True
                _atomic_write_json(staging / "agreement_summary.json", summary)
            manifest = {
                "schema_version": QUALITY_REVIEW_TOOL_VERSION,
                **summary,
                "created_at": _utc_now(),
                "independent_submissions_locked_before_consensus": True,
                "original_labels_and_disagreements_retained": True,
            }
            manifest["artifact_inventory"] = _artifact_inventory(staging)
            _atomic_write_json(staging / "manifest.json", manifest)
            os.replace(staging, final)
            return manifest, final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


class ManualMetricConsensusReporter(QualityConsensusReporter):
    """Create UCR or MP consensus records and a bounded metric summary."""

    def __init__(self, *, coding_type: str, **kwargs: Any):
        super().__init__(**kwargs)
        self.coding_type = str(coding_type).lower()
        if self.coding_type not in {"ucr", "mp"}:
            raise QualityReviewError("Manual metric consensus type must be ucr or mp.")

    def run(self) -> tuple[dict[str, Any], Path]:
        imports = _load_coder_imports(self.import_directories, self.coding_type)
        package_id, coder_ids = _shared_import_identity(imports)
        if len(coder_ids) < 2:
            raise QualityReviewError(
                f"{self.coding_type.upper()} consensus requires at least two independent coders."
            )
        if self.coding_type == "mp":
            rows, unresolved = self._mp_rows(imports)
            fields = MP_CONSENSUS_FIELDS
        else:
            rows, unresolved = self._ucr_rows(imports)
            fields = UCR_CONSENSUS_FIELDS
        manifest, directory = self._write_report(
            package_id=package_id,
            coder_ids=coder_ids,
            imports=imports,
            rows=rows,
            fields=fields,
            unresolved=unresolved,
            coding_type=self.coding_type,
        )
        if unresolved == 0:
            metric_summary = _manual_metric_summary(self.coding_type, rows)
            _atomic_write_json(directory / "metric_summary.json", metric_summary)
            # The report manifest inventory was intentionally sealed before this file.
            # Re-open only this newly-created report to append the deterministic summary
            # and reseal its inventory; source coder imports remain untouched.
            report_manifest_path = directory / "manifest.json"
            report_manifest = _read_object(report_manifest_path, "manual metric report")
            report_manifest["metric_summary_path"] = "metric_summary.json"
            report_manifest["artifact_inventory"] = _artifact_inventory(directory)
            _atomic_write_json(report_manifest_path, report_manifest)
            manifest = report_manifest
        return manifest, directory

    def _mp_rows(self, imports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        grouped: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
        for imported in imports:
            coder = imported["manifest"]["coder_id"]
            for row in imported["rows"]:
                key = (row["pair_key"], row["blind_label"], row["item_key"])
                grouped[key][coder] = row["meaning_preservation_label"]
        _ensure_coder_coverage(grouped, imports)
        supplied = self._supplied_manual(MP_CONSENSUS_FIELDS)
        rows = []
        unresolved = 0
        created_at = _utc_now()
        for key in sorted(grouped):
            labels = grouped[key]
            disagreement = len(set(labels.values())) > 1
            supplied_row = supplied.get(key)
            if disagreement:
                if supplied_row:
                    if supplied_row.get("coder_labels_json") != _canonical_json(labels):
                        raise QualityReviewError(
                            f"MP coder labels changed in the consensus sheet for {key}."
                        )
                    consensus = str(supplied_row["consensus_label"] or "").strip()
                    if consensus not in MP_LABELS:
                        raise QualityReviewError(f"MP consensus for {key} must be one of {MP_LABELS}.")
                    note = str(supplied_row["consensus_note"] or "").strip()
                    resolved = _parse_timezone_datetime(
                        supplied_row["resolved_at"], f"MP {key} resolved_at"
                    )
                    if not note:
                        raise QualityReviewError(f"MP consensus note is required for {key}.")
                else:
                    consensus = note = resolved = ""
                    unresolved += 1
            else:
                consensus = next(iter(labels.values()))
                note = "Independent labels agree; no discussion required."
                resolved = created_at
            rows.append(
                {
                    "pair_key": key[0],
                    "blind_label": key[1],
                    "item_key": key[2],
                    "coder_labels_json": _canonical_json(labels),
                    "disagreement": _bool_text(disagreement),
                    "consensus_label": consensus,
                    "consensus_note": note,
                    "resolved_at": resolved,
                }
            )
        _validate_supplied_keys(supplied, rows, "MP")
        return rows, unresolved

    def _ucr_rows(self, imports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        grouped: dict[tuple[str, str, str], dict[str, list[dict[str, str]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for imported in imports:
            coder = imported["manifest"]["coder_id"]
            for row in imported["rows"]:
                key = (row["pair_key"], row["blind_label"], row["item_key"])
                grouped[key][coder].append(
                    {
                        "proposition_index": row["proposition_index"],
                        "atomic_proposition": row["atomic_proposition"],
                        "support_label": row["support_label"],
                    }
                )
        _ensure_coder_coverage(grouped, imports)
        supplied = self._supplied_manual(UCR_CONSENSUS_FIELDS)
        rows = []
        unresolved = 0
        created_at = _utc_now()
        for key in sorted(grouped):
            coder_records = {
                coder: sorted(records, key=lambda row: int(row["proposition_index"]))
                for coder, records in grouped[key].items()
            }
            canonical_lists = {_canonical_json(records) for records in coder_records.values()}
            disagreement = len(canonical_lists) > 1
            supplied_row = supplied.get(key)
            if disagreement:
                if supplied_row:
                    if supplied_row.get("coder_records_json") != _canonical_json(
                        coder_records
                    ):
                        raise QualityReviewError(
                            f"UCR coder records changed in the consensus sheet for {key}."
                        )
                    propositions = _parse_consensus_propositions(
                        supplied_row["consensus_propositions_json"], f"UCR {key}"
                    )
                    note = str(supplied_row["consensus_note"] or "").strip()
                    resolved = _parse_timezone_datetime(
                        supplied_row["resolved_at"], f"UCR {key} resolved_at"
                    )
                    if not note:
                        raise QualityReviewError(f"UCR consensus note is required for {key}.")
                else:
                    propositions = []
                    note = resolved = ""
                    unresolved += 1
            else:
                propositions = next(iter(coder_records.values()))
                note = "Independent segmentation and labels agree; no discussion required."
                resolved = created_at
            rows.append(
                {
                    "pair_key": key[0],
                    "blind_label": key[1],
                    "item_key": key[2],
                    "coder_records_json": _canonical_json(coder_records),
                    "segmentation_or_label_disagreement": _bool_text(disagreement),
                    "consensus_propositions_json": _canonical_json(propositions),
                    "consensus_note": note,
                    "resolved_at": resolved,
                }
            )
        _validate_supplied_keys(supplied, rows, "UCR")
        return rows, unresolved

    def _supplied_manual(
        self, fields: tuple[str, ...]
    ) -> dict[tuple[str, str, str], dict[str, str]]:
        if self.consensus_sheet is None:
            return {}
        supplied = {}
        for row in _read_csv(self.consensus_sheet, fields):
            marker = row.get("disagreement") or row.get(
                "segmentation_or_label_disagreement"
            )
            if str(marker or "").strip().lower() != "true":
                continue
            key = (row["pair_key"], row["blind_label"], row["item_key"])
            if key in supplied:
                raise QualityReviewError(f"Duplicate supplied consensus row: {key}.")
            supplied[key] = row
        return supplied


def _load_coder_imports(
    directories: Iterable[Path], coding_type: str
) -> list[dict[str, Any]]:
    imports = []
    for directory in directories:
        root = Path(directory).resolve()
        manifest_path = root / "manifest.json"
        manifest = _read_object(manifest_path, "coder import manifest")
        if manifest.get("import_status") != "locked":
            raise QualityReviewError(f"Coder import is not locked: {root}")
        if manifest.get("coding_type") != coding_type:
            raise QualityReviewError(
                f"Coder import {root} is {manifest.get('coding_type')}, expected {coding_type}."
            )
        _verify_inventory(root, manifest)
        if coding_type == "quality":
            fields = tuple(load_json(QUALITY_REVIEW_CONTRACT_PATH)["coding_sheet_fields"])
        elif coding_type == "ucr":
            fields = UCR_CODING_FIELDS
        else:
            fields = MP_CODING_FIELDS
        rows = _read_csv(root / "coder_results.csv", fields)
        if sha256_file(root / "coder_results.csv") != manifest.get(
            "normalized_results_sha256"
        ):
            raise QualityReviewError(f"Coder results hash mismatch: {root}")
        imports.append(
            {
                "root": root,
                "manifest": manifest,
                "manifest_sha256": sha256_file(manifest_path),
                "rows": rows,
            }
        )
    if not imports:
        raise QualityReviewError("At least two locked coder imports are required.")
    return imports


def _shared_import_identity(imports: list[dict[str, Any]]) -> tuple[str, set[str]]:
    package_ids = {row["manifest"].get("review_package_id") for row in imports}
    coder_ids = {str(row["manifest"].get("coder_id") or "").strip() for row in imports}
    if len(package_ids) != 1:
        raise QualityReviewError("Coder imports belong to different review packages.")
    if "" in coder_ids or len(coder_ids) != len(imports):
        raise QualityReviewError(
            "Each selected import must contain one distinct nonblank coder_id."
        )
    return _safe_id(next(iter(package_ids)), "review_package_id"), coder_ids


def _group_quality_results(
    imports: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, str]]]:
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    expected_pairs: set[str] | None = None
    for imported in imports:
        coder = imported["manifest"]["coder_id"]
        rows = imported["rows"]
        pair_keys = {row["pair_key"] for row in rows}
        if expected_pairs is None:
            expected_pairs = pair_keys
        elif pair_keys != expected_pairs:
            raise QualityReviewError("Quality coder imports do not cover the same pairs.")
        for row in rows:
            grouped[row["pair_key"]][coder] = row
    return grouped


def _validate_quality_consensus_label(field: str, value: Any) -> str:
    text = str(value or "").strip()
    if field.startswith(("a_q", "b_q")):
        return _score(text, allow_na="q2_" in field, label=f"consensus {field}")
    if field == "overall_preference" and text not in {"A", "B", "Tie"}:
        raise QualityReviewError("Overall consensus must be A, B, or Tie.")
    if field == "confidence" and text not in {"1", "2", "3"}:
        raise QualityReviewError("Confidence consensus must be 1-3.")
    if field == "issue_flags_json":
        return _canonical_flags(text, "consensus issue_flags_json")
    if not text:
        raise QualityReviewError(f"Consensus value is required for {field}.")
    return text


def _copy_independent_inputs(staging: Path, imports: list[dict[str, Any]]) -> None:
    target = staging / "independent_inputs"
    for index, imported in enumerate(
        sorted(imports, key=lambda row: row["manifest"]["coder_id"]), start=1
    ):
        coder_root = target / f"coder_{index:02d}"
        coder_root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(imported["root"] / "manifest.json", coder_root / "manifest.json")
        shutil.copyfile(
            imported["root"] / "coder_results.csv", coder_root / "coder_results.csv"
        )


def _ensure_coder_coverage(
    grouped: dict[tuple[str, str, str], dict[str, Any]],
    imports: list[dict[str, Any]],
) -> None:
    coders = {row["manifest"]["coder_id"] for row in imports}
    for key, labels in grouped.items():
        if set(labels) != coders:
            raise QualityReviewError(
                f"Independent coder submissions do not cover the same unit {key}."
            )


def _validate_supplied_keys(
    supplied: dict[tuple[str, str, str], dict[str, str]],
    rows: list[dict[str, Any]],
    label: str,
) -> None:
    disagreement_keys = {
        (row["pair_key"], row["blind_label"], row["item_key"])
        for row in rows
        if row.get("disagreement") == "true"
        or row.get("segmentation_or_label_disagreement") == "true"
    }
    if supplied and set(supplied) != disagreement_keys:
        raise QualityReviewError(
            f"Supplied {label} consensus sheet does not match every and only disagreement."
        )


def _parse_consensus_propositions(value: Any, label: str) -> list[dict[str, str]]:
    rows = _parse_json_list(value, f"{label} consensus_propositions_json")
    if not rows:
        raise QualityReviewError(f"{label} requires at least one consensus proposition.")
    clean = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise QualityReviewError(f"{label} proposition {index} must be an object.")
        proposition = str(row.get("atomic_proposition") or row.get("proposition") or "").strip()
        support = str(row.get("support_label") or row.get("label") or "").strip()
        if not proposition or support not in UCR_LABELS:
            raise QualityReviewError(
                f"{label} proposition {index} needs text and a supported/unsupported label."
            )
        clean.append(
            {
                "proposition_index": str(index),
                "atomic_proposition": proposition,
                "support_label": support,
            }
        )
    return clean


def _manual_metric_summary(coding_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if coding_type == "mp":
        counts = Counter(row["consensus_label"] for row in rows)
        denominator = len(rows)
        numerator = counts.get("preserved", 0)
        return {
            "metric_code": "MP",
            "calculation_status": "completed_from_consensus_coding",
            "numerator_preserved_items": numerator,
            "denominator_eligible_items": denominator,
            "value": round(numerator / denominator, 6) if denominator else None,
            "uncertain_count": counts.get("uncertain", 0),
            "not_preserved_count": counts.get("not_preserved", 0),
            "initial_disagreement_count": sum(
                row["disagreement"] == "true" for row in rows
            ),
            "comparison_claim_generated": False,
        }
    propositions = []
    for row in rows:
        propositions.extend(
            _parse_consensus_propositions(
                row["consensus_propositions_json"],
                f"{row['pair_key']} {row['blind_label']} {row['item_key']}",
            )
        )
    unsupported = sum(row["support_label"] == "unsupported" for row in propositions)
    total = len(propositions)
    return {
        "metric_code": "UCR",
        "calculation_status": "completed_from_consensus_coding",
        "numerator_unsupported_propositions": unsupported,
        "denominator_atomic_propositions": total,
        "value": round(unsupported / total, 6) if total else None,
        "initial_disagreement_count": sum(
            row["segmentation_or_label_disagreement"] == "true" for row in rows
        ),
        "comparison_claim_generated": False,
    }


def _write_deblinded_key(
    staging: Path,
    key_path: Path,
    package_id: str,
    _rows: list[dict[str, Any]],
) -> None:
    key = _read_object(key_path, "private blinding key")
    if key.get("review_package_id") != package_id:
        raise QualityReviewError("Private condition key belongs to another package.")
    assignments = []
    for row in key.get("assignments") or []:
        assignments.append(
            {
                "pair_key": row.get("pair_key"),
                "A": (row.get("A") or {}).get("condition"),
                "B": (row.get("B") or {}).get("condition"),
            }
        )
    _atomic_write_json(
        staging / "deblinded_pair_key.json",
        {
            "schema_version": QUALITY_REVIEW_TOOL_VERSION,
            "review_package_id": package_id,
            "assignments": assignments,
            "boundary": (
                "This file maps opaque labels only. It does not calculate a winner "
                "or establish comparative superiority."
            ),
        },
    )


__all__ = [
    "BlindedQualityReviewExporter",
    "CoderSubmissionImporter",
    "ManualMetricConsensusReporter",
    "QualityConsensusReporter",
    "QualityReviewError",
    "MP_CODING_FIELDS",
    "MP_CONSENSUS_FIELDS",
    "UCR_CODING_FIELDS",
    "UCR_CONSENSUS_FIELDS",
]

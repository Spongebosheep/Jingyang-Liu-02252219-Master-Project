"""Shared, dependency-free contracts for paired evaluation artefacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


CONTRACT_SCHEMA_VERSION = "1.0"
CURRENT_SCENARIO_CONTRACT_VERSION = "4.0"
CURRENT_METRIC_CONTRACT_VERSION = "3.0"
CURRENT_REVIEW_COMPLETION_CONTRACT_VERSION = "2.0"
CURRENT_QUALITY_REVIEW_CONTRACT_VERSION = "2.0"
CURRENT_MANIFEST_CONTRACT_VERSION = "10.2"
MVP_CONDITION = "mvp"
BASELINE_CONDITION = "prompt_only_baseline"
PAIRED_CONDITIONS = (MVP_CONDITION, BASELINE_CONDITION)

EXPORT_TABLE_FIELDS = {
    "runs": (
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "run_type",
        "formal_run",
        "execution_status",
        "run_status",
        "started_at",
        "completed_at",
        "code_commit",
        "model_id",
        "protocol_snapshot_sha256",
        "consent_notice_version",
        "consent_snapshot_sha256",
        "consent_confirmed_at",
        "scenario_sha256",
        "metric_rules_sha256",
        "scenario_instance_sha256",
        "runner_record_path",
        "runner_record_sha256",
        "raw_model_call_count",
        "scenario_step_count",
        "digest_item_count",
        "review_event_count",
        "validator_result_count",
        "selected_validator_result_path",
        "selected_validator_result_sha256",
        "validator_matches_runner",
        "validation_status",
        "formal_evidence_eligible",
        "evidence_record_status",
        "evidence_record_path",
        "evidence_record_sha256",
        "error_type",
        "error_message",
    ),
    "turns": (
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "step_order",
        "step_id",
        "kind",
        "section_index",
        "section_code",
        "participant_text",
        "control",
        "expected_coverage_assessment_any_of_json",
        "expected_participant_control",
        "expected_action",
        "expected_probe_count_before",
        "eligible_for_ai",
        "eligible_for_ac",
        "expected_missing_information_json",
        "observed_session_id",
        "observed_decision_count",
        "source_message_id",
        "coverage_assessment",
        "participant_control",
        "selected_action",
        "probe_count_before",
        "covered_information_json",
        "missing_information_json",
        "reason",
        "execution_channel",
        "call_id",
        "call_status",
        "raw_request_path",
        "raw_request_sha256",
        "raw_response_path",
        "raw_response_sha256",
        "parsed_output_path",
        "parsed_output_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "http_method",
        "http_path",
        "http_status_code",
        "http_raw_body_path",
        "http_raw_body_sha256",
        "recorded_at",
    ),
    "item_sources": (
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "item_order",
        "source_relation_order",
        "item_id",
        "session_id",
        "section_index",
        "section_code",
        "coverage_status",
        "participant_control",
        "topic_reached",
        "missing_information_json",
        "generated_text",
        "final_text",
        "evidence_text",
        "is_evidence_candidate",
        "is_included",
        "display_visible",
        "export_visible",
        "review_status",
        "reviewed_text",
        "reviewer_comment",
        "reviewer_name",
        "reviewed_at",
        "source_relation_applicable",
        "source_message_id",
        "source_sender",
        "source_section_index",
        "source_text",
        "same_session",
        "participant_sender",
        "same_topic",
        "relation_valid",
        "relation_issues_json",
    ),
    "reviews": (
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "review_order",
        "review_record_type",
        "event_id",
        "item_id",
        "session_id",
        "section_index",
        "section_code",
        "previous_status",
        "new_status",
        "previous_text",
        "new_text",
        "comment",
        "reviewer_name",
        "reviewed_at",
        "decision",
        "reviewer_note",
        "source_links_checked",
        "participant_controls_respected",
        "limitations_and_missing_information_visible",
        "participant_meaning_preserved",
        "protocol_boundaries_respected",
    ),
}

# Historical v9 contracts remain byte-identical and independently valid.
LEGACY_EXPORT_TABLE_FIELDS = {
    "runs": tuple(
        field
        for field in EXPORT_TABLE_FIELDS["runs"]
        if field
        not in {
            "consent_notice_version",
            "consent_snapshot_sha256",
            "consent_confirmed_at",
        }
    ),
    "turns": tuple(
        "expected_answer_status_any_of_json"
        if field == "expected_coverage_assessment_any_of_json"
        else "answer_status"
        if field == "coverage_assessment"
        else field
        for field in EXPORT_TABLE_FIELDS["turns"]
        if field not in {"expected_participant_control", "participant_control"}
    ),
    "item_sources": tuple(
        field
        for field in EXPORT_TABLE_FIELDS["item_sources"]
        if field not in {"participant_control", "topic_reached"}
    ),
    "reviews": EXPORT_TABLE_FIELDS["reviews"],
}

METRIC_OUTPUT_TABLE_FIELDS = {
    "detail": (
        "calculation_id",
        "detail_order",
        "metric_code",
        "metric_name",
        "calculation_mode",
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "unit_type",
        "unit_id",
        "eligible",
        "evaluation_status",
        "passed",
        "numerator_contribution",
        "denominator_contribution",
        "expected_json",
        "observed_json",
        "failure_reasons_json",
        "source_paths_json",
        "manual_coding_required",
    ),
    "run_summary": (
        "calculation_id",
        "run_id",
        "condition",
        "scenario_id",
        "repetition",
        "pair_key",
        "run_execution_status",
        "validation_status",
        "formal_evidence_eligible",
        "metric_code",
        "metric_name",
        "calculation_mode",
        "calculation_status",
        "numerator",
        "denominator",
        "value",
        "failure_count",
        "not_evaluable_count",
        "pending_manual_count",
        "detail_count",
        "manual_coding_required",
    ),
    "condition_summary": (
        "calculation_id",
        "condition",
        "metric_code",
        "metric_name",
        "calculation_mode",
        "run_count",
        "completed_run_count",
        "formal_evidence_eligible_run_count",
        "calculation_status",
        "numerator",
        "denominator",
        "value",
        "failure_count",
        "not_evaluable_run_count",
        "pending_manual_run_count",
        "manual_coding_required",
    ),
    "pair_summary": (
        "calculation_id",
        "pair_key",
        "scenario_id",
        "repetition",
        "metric_code",
        "metric_name",
        "calculation_mode",
        "pairing_status",
        "mvp_run_ids_json",
        "baseline_run_ids_json",
        "mvp_calculation_status",
        "mvp_numerator",
        "mvp_denominator",
        "mvp_value",
        "baseline_calculation_status",
        "baseline_numerator",
        "baseline_denominator",
        "baseline_value",
        "formal_evidence_eligible",
        "manual_coding_required",
    ),
}

OPENING_ACKNOWLEDGEMENT_TEXT = "Yes, it is okay to continue."

SCENARIO_IDS = tuple(f"S{index}" for index in range(1, 9))

ACTIONS = {
    "ask_follow_up",
    "move_next",
    "flag_missing_and_move_next",
    "skip",
    "stop",
    "boundary_response",
    "complete",
}

LEGACY_ANSWER_STATUSES = {
    "sufficient",
    "partial",
    "vague",
    "too_short",
    "off_topic",
    "skipped",
    "stopped",
    "safety_boundary",
}

COVERAGE_ASSESSMENTS = {
    "covered",
    "partially_covered",
    "unclear",
    "off_topic",
    "not_assessed",
}

PARTICIPANT_CONTROLS = {"none", "skip", "stop"}

LEGACY_COVERAGE_STATUSES = {
    "answered",
    "partial",
    "skipped",
    "stopped",
    "not_reached",
}

COVERAGE_STATUSES = {
    "covered",
    "partially_covered",
    "not_covered",
    "not_assessed",
}

REVIEW_ACTIONS = {"include", "edit", "exclude"}
STEP_KINDS = {
    "consent",
    "participant_turn",
    "participant_control",
    "post_stop_probe",
}

METRIC_CODES = ("AI", "AC", "SLC", "MIV", "PCC", "UCR", "RAC", "MP", "TCR")

RUN_METADATA_FIELDS = (
    "schema_version",
    "run_id",
    "condition",
    "scenario_id",
    "repetition",
    "run_status",
    "started_at",
    "completed_at",
    "code_identity",
    "runtime_identity",
    "comparison_freeze",
    "protocol_snapshot_sha256",
    "scenario_sha256",
    "metric_rules_sha256",
)

CONTROL_RECORD_FIELDS = (
    "run_id",
    "session_id",
    "source_message_id",
    "section_index",
    "section_code",
    "coverage_assessment",
    "participant_control",
    "selected_action",
    "covered_information",
    "missing_information",
    "reason",
)

RESULT_ONLY_KEYS = {
    "actual",
    "observed",
    "result",
    "results",
    "passed",
    "failed",
    "pass",
    "fail",
    "score",
}


class ContractError(ValueError):
    """Raised when a frozen evaluation contract is incomplete or ambiguous."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain one JSON object.")
    return value


def _require(mapping: dict[str, Any], fields: tuple[str, ...], location: str) -> None:
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise ContractError(f"{location} is missing: {', '.join(missing)}")


def pair_integrity_from_run_rows(
    run_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return condition-neutral matched-pair identity and fairness checks."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        grouped.setdefault(row["pair_key"], []).append(row)

    matched_fields = (
        "model_id",
        "code_commit",
        "protocol_snapshot_sha256",
        "scenario_sha256",
        "metric_rules_sha256",
        "scenario_instance_sha256",
    )
    pairs = []
    for pair_key in sorted(grouped):
        rows = grouped[pair_key]
        by_condition = {
            condition: [row for row in rows if row["condition"] == condition]
            for condition in PAIRED_CONDITIONS
        }
        if any(len(by_condition[condition]) > 1 for condition in PAIRED_CONDITIONS):
            status = "AMBIGUOUS"
            mismatches = ["one_run_per_condition"]
            mvp = None
            baseline = None
        elif any(len(by_condition[condition]) == 0 for condition in PAIRED_CONDITIONS):
            status = "INCOMPLETE"
            mismatches = ["one_mvp_and_one_prompt_only_baseline_run"]
            mvp = None
            baseline = None
        else:
            mvp = by_condition[MVP_CONDITION][0]
            baseline = by_condition[BASELINE_CONDITION][0]
            mismatches = [
                field for field in matched_fields if mvp[field] != baseline[field]
            ]
            status = "PASS" if not mismatches else "FAIRNESS_FAIL"
        pairs.append(
            {
                "pair_key": pair_key,
                "status": status,
                "mvp_run_ids": [row["run_id"] for row in by_condition[MVP_CONDITION]],
                "baseline_run_ids": [
                    row["run_id"] for row in by_condition[BASELINE_CONDITION]
                ],
                "matched_fields": list(matched_fields),
                "mismatched_fields": mismatches,
                "formal_pair_evidence_eligible": bool(
                    status == "PASS"
                    and mvp
                    and baseline
                    and mvp["formal_evidence_eligible"] in {True, "true"}
                    and baseline["formal_evidence_eligible"] in {True, "true"}
                ),
            }
        )
    return {
        "matched_fields": list(matched_fields),
        "pair_count": len(pairs),
        "pass_count": sum(row["status"] == "PASS" for row in pairs),
        "fairness_fail_count": sum(
            row["status"] == "FAIRNESS_FAIL" for row in pairs
        ),
        "ambiguous_count": sum(row["status"] == "AMBIGUOUS" for row in pairs),
        "incomplete_count": sum(row["status"] == "INCOMPLETE" for row in pairs),
        "formal_pair_evidence_eligible_count": sum(
            row["formal_pair_evidence_eligible"] for row in pairs
        ),
        "pairs": pairs,
    }


def _walk_result_keys(value: Any, location: str = "root") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in RESULT_ONLY_KEYS:
                findings.append(f"{location}.{key}")
            findings.extend(_walk_result_keys(child, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_walk_result_keys(child, f"{location}[{index}]"))
    return findings


def validate_scenarios(document: dict[str, Any]) -> None:
    _require(
        document,
        (
            "schema_version",
            "preregistration",
            "paired_conditions",
            "protocol_reference",
            "scenarios",
        ),
        "scenario document",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected scenario schema version.")
    contract_version = document.get("contract_version", "1.0")
    if contract_version not in {"1.0", "2.0", "3.0", CURRENT_SCENARIO_CONTRACT_VERSION}:
        raise ContractError("Unexpected scenario contract version.")
    if tuple(document["paired_conditions"]) != PAIRED_CONDITIONS:
        raise ContractError("Paired conditions must be MVP then prompt-only baseline.")
    if document["preregistration"].get("status") != "frozen_before_formal_runs":
        raise ContractError("Scenario oracle is not marked frozen before formal runs.")
    if contract_version in {"2.0", "3.0", CURRENT_SCENARIO_CONTRACT_VERSION}:
        _require(
            document["preregistration"],
            ("supersedes", "revision_reason", "change_policy"),
            f"scenario v{contract_version} preregistration",
        )
        expected_superseded = {
            "2.0": "scenarios.v1.json",
            "3.0": "scenarios.v2.json",
            "4.0": "scenarios.v3.json",
        }[contract_version]
        if document["preregistration"]["supersedes"] != expected_superseded:
            raise ContractError(
                f"Scenario v{contract_version} must identify {expected_superseded} as superseded."
            )
        _require(document, ("turn_eligibility",), f"scenario v{contract_version} document")
        if set(document["turn_eligibility"]) != {"ai", "ac", "opening_rule"}:
            raise ContractError("Scenario turn-eligibility rules are incomplete.")

    result_keys = _walk_result_keys(document)
    if result_keys:
        raise ContractError(
            "Scenario/oracle file contains result-only keys: " + ", ".join(result_keys)
        )

    scenarios = document["scenarios"]
    if not isinstance(scenarios, list):
        raise ContractError("scenarios must be a list.")
    ids = tuple(item.get("scenario_id") for item in scenarios)
    if ids != SCENARIO_IDS:
        raise ContractError(f"Scenario order/identity must be {SCENARIO_IDS}, got {ids}.")

    for scenario in scenarios:
        location = scenario["scenario_id"]
        _require(
            scenario,
            (
                "scenario_id",
                "title",
                "claim_scope",
                "steps",
                "expected_topic_outcomes",
                "required_artifacts",
            ),
            location,
        )
        if not scenario["steps"]:
            raise ContractError(f"{location} has no scripted steps.")
        if contract_version in {"2.0", "3.0", CURRENT_SCENARIO_CONTRACT_VERSION}:
            steps = scenario["steps"]
            if steps[0].get("kind") != "consent":
                raise ContractError(f"{location} must begin with Consent.")
            if len(steps) < 2:
                raise ContractError(f"{location} is missing the Opening acknowledgement.")
            opening = steps[1]
            expected_opening_id = f"{location}-O01"
            if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION:
                expected_opening_oracle = {
                    "expected_action": "move_next",
                    "probe_count_before": 0,
                    "eligible_for_ai": True,
                    "eligible_for_ac": False,
                    "expected_missing_information": [],
                    "expected_coverage_assessment_any_of": ["not_assessed"],
                    "expected_participant_control": "none",
                }
            else:
                expected_opening_oracle = {
                    "answer_status_any_of": ["sufficient"],
                    "expected_action": "move_next",
                    "probe_count_before": 0,
                    "eligible_for_ai": True,
                    "eligible_for_ac": False,
                    "expected_missing_information": [],
                }
            if (
                opening.get("step_id") != expected_opening_id
                or opening.get("kind") != "participant_turn"
                or opening.get("section_index") != 0
                or opening.get("section_code") != "opening"
                or opening.get("participant_text") != OPENING_ACKNOWLEDGEMENT_TEXT
                or opening.get("oracle") != expected_opening_oracle
            ):
                raise ContractError(
                    f"{location} must use the fixed section-0 Opening acknowledgement."
                )
        step_ids: set[str] = set()
        for step in scenario["steps"]:
            _require(step, ("step_id", "kind", "oracle"), f"{location} step")
            if step["step_id"] in step_ids:
                raise ContractError(f"{location} repeats step_id {step['step_id']}.")
            step_ids.add(step["step_id"])
            if step["kind"] not in STEP_KINDS:
                raise ContractError(f"{location} has unsupported step kind {step['kind']}.")
            oracle = step["oracle"]
            if step["kind"] == "participant_turn":
                _require(
                    step,
                    ("section_index", "section_code", "participant_text"),
                    step["step_id"],
                )
                if not step["participant_text"].strip():
                    raise ContractError(f"{step['step_id']} has blank participant text.")
                _require(
                    oracle,
                    ("expected_action", "probe_count_before", "eligible_for_ac"),
                    f"{step['step_id']} oracle",
                )
                if oracle["expected_action"] not in ACTIONS:
                    raise ContractError(f"{step['step_id']} has an invalid expected action.")
                if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION:
                    allowed_assessments = oracle.get(
                        "expected_coverage_assessment_any_of", []
                    )
                    if (
                        not allowed_assessments
                        or not set(allowed_assessments) <= COVERAGE_ASSESSMENTS
                    ):
                        raise ContractError(
                            f"{step['step_id']} has an invalid Protocol-coverage oracle."
                        )
                    if oracle.get("expected_participant_control") not in PARTICIPANT_CONTROLS:
                        raise ContractError(
                            f"{step['step_id']} has an invalid participant-control oracle."
                        )
                else:
                    allowed_statuses = oracle.get("answer_status_any_of", [])
                    if (
                        not allowed_statuses
                        or not set(allowed_statuses) <= LEGACY_ANSWER_STATUSES
                    ):
                        raise ContractError(
                            f"{step['step_id']} has invalid answer-status oracle."
                        )
            elif step["kind"] == "participant_control":
                _require(step, ("section_index", "section_code", "control"), step["step_id"])
                if step["control"] not in {"skip", "stop"}:
                    raise ContractError(f"{step['step_id']} has an invalid control.")
                if oracle.get("expected_action") != step["control"]:
                    raise ContractError(f"{step['step_id']} control/action oracle differs.")
                if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION:
                    if oracle.get("expected_coverage_assessment_any_of") != [
                        "not_assessed"
                    ]:
                        raise ContractError(
                            f"{step['step_id']} must not treat a participant control as coverage."
                        )
                    if oracle.get("expected_participant_control") != step["control"]:
                        raise ContractError(
                            f"{step['step_id']} participant-control oracle differs."
                        )
            elif step["kind"] == "post_stop_probe":
                if contract_version not in {"3.0", CURRENT_SCENARIO_CONTRACT_VERSION}:
                    raise ContractError(
                        f"{step['step_id']} is only supported by scenario contract v3."
                    )
                _require(
                    step,
                    ("section_index", "section_code", "participant_text"),
                    step["step_id"],
                )
                _require(
                    oracle,
                    (
                        "expected_http_method",
                        "expected_new_message_count",
                        "expected_new_decision_count",
                        "session_status",
                        "eligible_for_ai",
                        "eligible_for_ac",
                    ),
                    f"{step['step_id']} oracle",
                )
                if (
                    oracle["expected_http_method"] != "POST"
                    or oracle["expected_new_message_count"] != 0
                    or oracle["expected_new_decision_count"] != 0
                    or oracle["session_status"] != "stopped"
                    or oracle["eligible_for_ai"] is not False
                    or oracle["eligible_for_ac"] is not False
                ):
                    raise ContractError(
                        f"{step['step_id']} must be a zero-record post-Stop rejection probe."
                    )
            else:
                if oracle.get("consent_confirmed") is not True:
                    raise ContractError(f"{step['step_id']} must confirm consent.")
                if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION and not all(
                    oracle.get(field) is True
                    for field in (
                        "consent_snapshot_recorded",
                        "consent_snapshot_sha256_valid",
                        "consent_confirmed_at_recorded",
                    )
                ):
                    raise ContractError(
                        f"{step['step_id']} must require a complete Consent integrity record."
                    )

        for topic in scenario["expected_topic_outcomes"]:
            _require(topic, ("section_index", "section_code", "coverage_status"), location)
            allowed_topic_statuses = (
                COVERAGE_STATUSES
                if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION
                else LEGACY_COVERAGE_STATUSES
            )
            if topic["coverage_status"] not in allowed_topic_statuses:
                raise ContractError(f"{location} has invalid coverage status.")
            if contract_version == CURRENT_SCENARIO_CONTRACT_VERSION:
                _require(
                    topic,
                    ("participant_control", "topic_reached"),
                    f"{location} topic outcome",
                )
                if topic["participant_control"] not in PARTICIPANT_CONTROLS:
                    raise ContractError(
                        f"{location} has invalid topic participant control."
                    )
                if not isinstance(topic["topic_reached"], bool):
                    raise ContractError(f"{location} has invalid topic-reached state.")

        topic_indexes = [
            topic["section_index"] for topic in scenario["expected_topic_outcomes"]
        ]
        if topic_indexes != [1, 2, 3, 4, 5]:
            raise ContractError(
                f"{location} must preregister exactly one outcome for research topics 1-5."
            )
        if location != "S5":
            final_step = scenario["steps"][-1]
            if (
                final_step.get("section_index") != 5
                or final_step["oracle"].get("expected_action") != "move_next"
            ):
                raise ContractError(
                    f"{location} is not scripted through the final research topic."
                )
        else:
            if contract_version in {"3.0", CURRENT_SCENARIO_CONTRACT_VERSION}:
                if (
                    len(scenario["steps"]) < 2
                    or scenario["steps"][-2]["oracle"].get("expected_action") != "stop"
                    or scenario["steps"][-1].get("kind") != "post_stop_probe"
                ):
                    raise ContractError(
                        f"Scenario v{contract_version} S5 must Stop and then execute the frozen rejection probe."
                    )
            elif scenario["steps"][-1]["oracle"].get("expected_action") != "stop":
                raise ContractError("S5 must end through the participant Stop path.")

    expected_signatures = {
        "S1": ["move_next"],
        "S2": ["ask_follow_up", "move_next"],
        "S3": ["ask_follow_up", "flag_missing_and_move_next"],
        "S4": ["skip"],
        "S5": ["stop"],
        "S6": ["boundary_response"],
    }
    for scenario_id, expected_actions in expected_signatures.items():
        scenario = scenarios[SCENARIO_IDS.index(scenario_id)]
        actions = [
            step["oracle"].get("expected_action")
            for step in scenario["steps"]
            if step["oracle"].get("expected_action")
            and step["oracle"].get("eligible_for_ac") is True
        ]
        cursor = 0
        for action in actions:
            if cursor < len(expected_actions) and action == expected_actions[cursor]:
                cursor += 1
        if cursor != len(expected_actions):
            raise ContractError(
                f"{scenario_id} does not contain its required action sequence {expected_actions}."
            )

    review_actions = {
        scenario["scenario_id"]: scenario.get("researcher_action", {}).get("action")
        for scenario in scenarios
    }
    if review_actions["S7"] != "edit" or review_actions["S8"] != "exclude":
        raise ContractError("S7 and S8 must preregister Edit and Exclude respectively.")


def validate_review_completion(document: dict[str, Any]) -> None:
    """Validate the frozen post-interview audit action for S7 and S8."""

    _require(
        document,
        (
            "schema_version",
            "contract_version",
            "preregistration",
            "applicable_scenarios",
            "actions",
            "claim_boundaries",
        ),
        "review-completion contract",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected review-completion schema version.")
    contract_version = document["contract_version"]
    if contract_version not in {"1.0", CURRENT_REVIEW_COMPLETION_CONTRACT_VERSION}:
        raise ContractError("Unexpected review-completion contract version.")
    preregistration = document["preregistration"]
    _require(
        preregistration,
        ("status", "frozen_on", "purpose", "change_policy"),
        "review-completion preregistration",
    )
    if preregistration["status"] != "frozen_before_formal_runs":
        raise ContractError("Review completion is not frozen before formal runs.")
    if document["applicable_scenarios"] != ["S7", "S8"]:
        raise ContractError("Review completion must apply only to S7 and S8.")
    actions = document["actions"]
    if set(actions) != {"S7", "S8"}:
        raise ContractError("Review-completion actions must be defined for S7 and S8.")
    expected_oracle_v1 = {
        "saved_decision": "revision_requested",
        "reviewer_identity_recorded": True,
        "review_timestamp_recorded": True,
        "reviewer_note_recorded": True,
        "remaining_items_auto_resolved": False,
    }
    if contract_version == CURRENT_REVIEW_COMPLETION_CONTRACT_VERSION:
        _require(
            preregistration,
            ("supersedes", "revision_reason"),
            "review-completion v2 preregistration",
        )
        if preregistration["supersedes"] != "review_completion.v1.json":
            raise ContractError("Review completion v2 must preserve v1 lineage.")

    for scenario_id, action in actions.items():
        _require(
            action,
            (
                "decision",
                "researcher_note",
                "participant_meaning_preserved",
                "protocol_boundaries_respected",
                "oracle",
            ),
            f"review-completion {scenario_id}",
        )
        if action["decision"] != "request_revision":
            raise ContractError(
                f"{scenario_id} must request revision rather than fabricate approval."
            )
        if not isinstance(action["researcher_note"], str) or not action[
            "researcher_note"
        ].strip():
            raise ContractError(f"{scenario_id} must freeze a nonblank reviewer note.")
        if action["participant_meaning_preserved"] is not False:
            raise ContractError(
                f"{scenario_id} cannot preregister a human meaning-preservation judgement."
            )
        if action["protocol_boundaries_respected"] is not False:
            raise ContractError(
                f"{scenario_id} cannot preregister a human protocol-boundary judgement."
            )
        if contract_version == "1.0":
            if action["oracle"] != expected_oracle_v1:
                raise ContractError(
                    f"{scenario_id} review-completion oracle is incomplete."
                )
            continue

        _require(
            action,
            ("pre_focal_item_actions", "post_focal_item_actions"),
            f"review-completion v2 {scenario_id}",
        )
        item_actions = [
            *action["pre_focal_item_actions"],
            *action["post_focal_item_actions"],
        ]
        expected_sections = {2, 3, 4, 5}
        if {row.get("section_index") for row in item_actions} != expected_sections:
            raise ContractError(
                f"{scenario_id} must explicitly resolve sections 2-5 once."
            )
        if len(item_actions) != 4:
            raise ContractError(
                f"{scenario_id} has duplicate scripted item-review actions."
            )
        for item_action in item_actions:
            if item_action.get("action") != "include":
                raise ContractError(
                    f"{scenario_id} completion may only Include remaining candidates."
                )
            if not str(item_action.get("reason") or "").strip():
                raise ContractError(
                    f"{scenario_id} scripted Include action needs a recorded reason."
                )
        if scenario_id == "S8":
            if [row.get("section_index") for row in action["pre_focal_item_actions"]] != [2]:
                raise ContractError(
                    "S8 must Include one item before the focal Exclude action."
                )
        elif action["pre_focal_item_actions"]:
            raise ContractError("S7 has no item action before its focal Edit.")

        expected_statuses = {
            "1": "edited" if scenario_id == "S7" else "excluded",
            "2": "approved",
            "3": "approved",
            "4": "approved",
            "5": "approved",
        }
        expected_oracle_v2 = {
            **expected_oracle_v1,
            "scripted_item_actions_complete": True,
            "expected_review_statuses": expected_statuses,
        }
        if action["oracle"] != expected_oracle_v2:
            raise ContractError(
                f"{scenario_id} v2 review-completion oracle is incomplete."
            )
    expected_boundaries = (
        {
            "scripted_audit_event_only": True,
            "overall_approval_claimed": False,
            "human_quality_judgement_performed": False,
            "remaining_items_auto_resolved": False,
        }
        if contract_version == "1.0"
        else {
            "scripted_review_actions_only": True,
            "overall_approval_claimed": False,
            "human_quality_judgement_performed": False,
            "remaining_items_auto_resolved": False,
            "all_candidate_items_scriptedly_reviewed": True,
        }
    )
    if document["claim_boundaries"] != expected_boundaries:
        raise ContractError("Review-completion claim boundaries are incomplete.")


def validate_quality_review(document: dict[str, Any]) -> None:
    """Validate the pre-result blinded A/B quality-review contract."""

    _require(
        document,
        (
            "schema_version",
            "contract_version",
            "preregistration",
            "unit",
            "minimum_independent_coders",
            "blinding",
            "criteria",
            "pair_judgement",
            "coder_process",
            "coding_sheet_fields",
            "consensus_report_fields",
            "boundaries",
        ),
        "quality-review contract",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected quality-review schema version.")
    contract_version = document["contract_version"]
    if contract_version not in {"1.0", CURRENT_QUALITY_REVIEW_CONTRACT_VERSION}:
        raise ContractError("Unexpected quality-review contract version.")
    expected_preregistration_status = (
        "frozen_before_live_comparison_results"
        if contract_version == "1.0"
        else "frozen_before_v10_1_live_comparison_results"
    )
    if document["preregistration"].get("status") != expected_preregistration_status:
        raise ContractError("Quality review must be frozen before live results.")
    if document["minimum_independent_coders"] != 2:
        raise ContractError("Quality review requires two independent coders.")

    if contract_version == CURRENT_QUALITY_REVIEW_CONTRACT_VERSION:
        preregistration = document["preregistration"]
        if preregistration.get("supersedes") != "quality_review.v1.json":
            raise ContractError("Quality-review v2 must preserve the v1 lineage.")
        expected_pair_selection = {
            "registered_schedule_only": True,
            "exactly_one_run_per_condition": True,
            "required_pair_integrity_status": "PASS",
            "required_execution_status": "completed",
            "required_validation_status": "PASS",
            "required_formal_pair_evidence_eligible": True,
            "excluded_pairs_are_reported": True,
            "ambiguous_pairs_are_never_selected": True,
            "replacement_runs_are_forbidden": True,
            "minimum_codable_pairs": 8,
            "recommended_codable_pairs": 16,
            "below_minimum_status": "blocked_below_minimum_pair_count",
            "below_recommended_warning_required": True,
        }
        if document.get("pair_selection") != expected_pair_selection:
            raise ContractError("Quality-review pair-selection rules are incomplete.")

    blinding = document["blinding"]
    _require(
        blinding,
        ("assignment", "excluded_from_coder_material", "included_in_coder_material"),
        "quality-review blinding",
    )
    required_exclusions = {
        "mvp_or_prompt_only_condition_name",
        "langgraph_reference",
        "code_commit_or_tree",
        "internal_decision_reason",
        "runner_validator_or_model_provenance_label",
    }
    if set(blinding["excluded_from_coder_material"]) != required_exclusions:
        raise ContractError("Blinded material exclusions are incomplete.")
    required_material = {
        "full_transcript",
        "participant_facing_follow_up",
        "structured_draft_evidence",
        "source_labels_and_extracts",
        "missing_information_and_limitation_presentation",
    }
    if set(blinding["included_in_coder_material"]) != required_material:
        raise ContractError("Blinded review material is incomplete.")

    expected_criteria = {
        "Q1": ("Protocol coverage", False),
        "Q2": ("Probing relevance and neutrality", True),
        "Q3": ("Transcript grounding and fidelity", False),
        "Q4": ("Participant clarity and control", False),
        "Q5": ("Researcher reviewability", False),
    }
    criteria = document["criteria"]
    if [row.get("question_id") for row in criteria] != list(expected_criteria):
        raise ContractError("Quality-review questions must remain Q1-Q5 in order.")
    for row in criteria:
        question_id = row["question_id"]
        name, allows_na = expected_criteria[question_id]
        if row.get("name") != name:
            raise ContractError(f"{question_id} quality-review name changed.")
        if row.get("scale") != [1, 2, 3, 4, 5]:
            raise ContractError(f"{question_id} must use the frozen 1-5 scale.")
        if row.get("not_applicable_allowed") is not allows_na:
            raise ContractError(f"{question_id} N/A rule changed.")
        if not str(row.get("prompt") or "").strip():
            raise ContractError(f"{question_id} prompt is blank.")

    judgement = document["pair_judgement"]
    if judgement != {
        "overall_preference": ["A", "B", "Tie"],
        "confidence": [1, 2, 3],
        "short_rationale_required": True,
        "issue_flags_required": True,
    }:
        raise ContractError("Paired preference fields are incomplete.")
    required_process = {
        "independent_before_consensus": True,
        "retain_original_labels": True,
        "retain_disagreements": True,
        "consensus_label_and_note_required_for_disagreement": True,
        "condition_key_hidden_until_independent_submissions_locked": True,
    }
    if document["coder_process"] != required_process:
        raise ContractError("Coder independence and consensus rules are incomplete.")
    required_sheet_fields = {
        "review_package_id",
        "pair_key",
        "scenario_id",
        "repetition",
        "coder_id",
        "blind_key",
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
        "completed_at",
    }
    if set(document["coding_sheet_fields"]) != required_sheet_fields:
        raise ContractError("Blinded coding-sheet fields are incomplete.")
    required_consensus_fields = {
        "pair_key",
        "field_name",
        "coder_labels_json",
        "disagreement",
        "consensus_label",
        "consensus_note",
        "resolved_at",
    }
    if set(document["consensus_report_fields"]) != required_consensus_fields:
        raise ContractError("Consensus-report fields are incomplete.")
    required_boundaries = {
        "ucr_is_separate_atomic_proposition_coding": True,
        "meaning_preservation_is_separate_item_level_coding": True,
        "five_quality_questions_do_not_replace_ucr_or_mp": True,
        "no_automatic_winner_or_superiority_claim": True,
    }
    if document["boundaries"] != required_boundaries:
        raise ContractError("Quality-review claim boundaries are incomplete.")


def validate_metrics(document: dict[str, Any]) -> None:
    _require(document, ("schema_version", "metrics", "reporting_rules"), "metric document")
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected metric schema version.")
    contract_version = document.get("contract_version", "1.0")
    if contract_version not in {"1.0", "2.0", CURRENT_METRIC_CONTRACT_VERSION}:
        raise ContractError("Unexpected metric contract version.")
    metrics = document["metrics"]
    codes = tuple(metric.get("code") for metric in metrics)
    if codes != METRIC_CODES:
        raise ContractError(f"Metric order/identity must be {METRIC_CODES}, got {codes}.")
    for metric in metrics:
        _require(
            metric,
            ("code", "name", "numerator", "denominator", "calculation_mode", "details"),
            f"metric {metric.get('code')}",
        )
        if not metric["numerator"].strip() or not metric["denominator"].strip():
            raise ContractError(f"Metric {metric['code']} has a blank numerator/denominator.")
    modes = {metric["code"]: metric["calculation_mode"] for metric in metrics}
    if modes["UCR"] != "manual_atomic_proposition_coding":
        raise ContractError("UCR must use atomic-proposition coding.")
    if modes["MP"] != "manual_item_judgement":
        raise ContractError("MP must remain a manual meaning-preservation judgement.")
    if contract_version in {"2.0", CURRENT_METRIC_CONTRACT_VERSION}:
        _require(
            document,
            ("preregistration", "eligibility_rules", "manual_coding_rules"),
            f"metric v{contract_version} document",
        )
        if document["preregistration"].get("status") != "frozen_before_formal_runs":
            raise ContractError("Metric rules are not marked frozen before formal runs.")
        if set(document["eligibility_rules"]) != {
            "AI",
            "AC",
            "SLC_MP",
            "MIV",
            "PCC",
            "UCR",
            "RAC",
            "TCR",
        }:
            raise ContractError("Metric eligibility rules are incomplete.")
        coding = document["manual_coding_rules"]
        if set(coding) != {"UCR", "MP"}:
            raise ContractError("Manual UCR/MP coding rules are incomplete.")
        for code in ("UCR", "MP"):
            _require(
                coding[code],
                ("unit", "judgement", "disagreement", "metric_value"),
                f"manual coding rule {code}",
            )
        _require(coding["UCR"], ("segmentation",), "manual coding rule UCR")
        _require(coding["MP"], ("labels",), "manual coding rule MP")
        if coding["MP"]["labels"] != ["preserved", "not_preserved", "uncertain"]:
            raise ContractError("MP labels must remain fixed.")


def validate_baseline_contract(document: dict[str, Any]) -> None:
    _require(
        document,
        (
            "schema_version",
            "condition",
            "matched_inputs",
            "allowed_difference",
            "prohibited_reuse",
            "freeze_requirements",
        ),
        "baseline contract",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected baseline-contract schema version.")
    contract_version = document.get("contract_version", "1.0")
    if contract_version not in {"1.0", "2.0"}:
        raise ContractError("Unexpected baseline-contract version.")
    if document["condition"] != BASELINE_CONDITION:
        raise ContractError("Baseline contract has the wrong condition identifier.")
    required_matches = {
        "model_id",
        "protocol_snapshot",
        "scenario_and_repetition",
        "participant_inputs_and_order",
        "follow_up_limit",
        "output_schemas",
        "structured_evidence_layer",
        "researcher_review_layer",
        "reviewer_instructions",
    }
    if set(document["matched_inputs"]) != required_matches:
        raise ContractError("Baseline matched-input contract is incomplete.")
    prohibited = set(document["prohibited_reuse"])
    if not {"langgraph_routing", "product_decision_logic", "semantic_repair"} <= prohibited:
        raise ContractError("Baseline separation rules are incomplete.")
    if contract_version == "2.0":
        if document.get("supersedes") != "baseline_contract.v1.json":
            raise ContractError("Baseline contract v2 must preserve the v1 lineage.")
        coverage = document.get("coverage_interpretation") or {}
        if coverage != {
            "binding_threshold": "current_section.assessment_guidance",
            "required_information_role": (
                "Researcher-defined dimensions to inspect; they are conjunctive only "
                "when assessment_guidance explicitly requires every dimension."
            ),
            "extra_detail_rule": (
                "Do not ask a follow-up merely because additional optional detail "
                "could be useful."
            ),
        }:
            raise ContractError("Baseline Protocol-coverage interpretation is incomplete.")
        gate = document.get("development_gate") or {}
        expected_gate = {
            "required_before_final_freeze": True,
            "condition": BASELINE_CONDITION,
            "run_type": "dry_run",
            "transport": "openai_responses_api",
            "planned_attempt_count": 16,
            "pass_rule": (
                "All 16 exact scheduled attempts complete and independently validate PASS."
            ),
            "formal_evidence_eligible": False,
            "replacement_or_formal_reuse_forbidden": True,
        }
        if gate != expected_gate:
            raise ContractError("Baseline development-gate rules are incomplete.")


def validate_baseline_output_schema(document: dict[str, Any]) -> None:
    """Validate the strict prompt-only response envelope without JSON Schema deps."""

    is_current = "coverage_assessment" in (document.get("properties") or {})
    expected_fields = {
        "coverage_assessment" if is_current else "answer_status",
        "selected_action",
        "covered_information",
        "missing_information",
        "reason",
        "participant_response",
    }
    if is_current:
        expected_fields.add("participant_control")
    if document.get("type") != "object":
        raise ContractError("Baseline output schema must describe one object.")
    if document.get("additionalProperties") is not False:
        raise ContractError("Baseline output schema must reject additional fields.")
    properties = document.get("properties") or {}
    if set(properties) != expected_fields or set(document.get("required") or []) != expected_fields:
        raise ContractError("Baseline output schema fields are incomplete or ambiguous.")
    if is_current:
        if (
            set(properties["coverage_assessment"].get("enum") or [])
            != COVERAGE_ASSESSMENTS
        ):
            raise ContractError(
                "Baseline Protocol-coverage enum differs from the shared contract."
            )
        if (
            set(properties["participant_control"].get("enum") or [])
            != PARTICIPANT_CONTROLS
        ):
            raise ContractError(
                "Baseline participant-control enum differs from the shared contract."
            )
    elif set(properties["answer_status"].get("enum") or []) != LEGACY_ANSWER_STATUSES:
        raise ContractError("Baseline answer-status enum differs from the legacy contract.")
    if set(properties["selected_action"].get("enum") or []) != ACTIONS:
        raise ContractError("Baseline action enum differs from the shared contract.")
    for field in ("covered_information", "missing_information"):
        if (
            properties[field].get("type") != "array"
            or (properties[field].get("items") or {}).get("type") != "string"
        ):
            raise ContractError(f"Baseline {field} must be an array of strings.")
    for field in ("reason", "participant_response"):
        if properties[field].get("type") != "string" or properties[field].get("minLength") != 1:
            raise ContractError(f"Baseline {field} must be a non-empty bounded string.")


def validate_export_bundle_schema(document: dict[str, Any]) -> None:
    """Validate the pre-formal-run, condition-neutral export contract."""

    _require(
        document,
        (
            "schema_version",
            "contract_version",
            "preregistration",
            "conditions",
            "required_outputs",
            "tables",
            "integrity_rules",
            "claim_boundaries",
        ),
        "export bundle schema",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected export-bundle schema version.")
    if document["contract_version"] not in {"1.0", "2.0"}:
        raise ContractError("Unexpected export-bundle contract version.")
    if document["preregistration"].get("status") != "frozen_before_formal_runs":
        raise ContractError("Export-bundle fields are not frozen before formal runs.")
    if tuple(document["conditions"]) != PAIRED_CONDITIONS:
        raise ContractError("Export bundle must support both paired conditions in order.")

    expected_outputs = {
        "manifest.json",
        "raw/runs/<run_id>/runner_record.json",
        "tables/runs.csv",
        "tables/turns.csv",
        "tables/item_sources.csv",
        "tables/reviews.csv",
        "evidence_records/<run_id>.<source_extension>",
    }
    if set(document["required_outputs"]) != expected_outputs:
        raise ContractError("Export-bundle required outputs are incomplete.")

    expected_table_fields = (
        EXPORT_TABLE_FIELDS
        if document["contract_version"] == "2.0"
        else LEGACY_EXPORT_TABLE_FIELDS
    )
    tables = document["tables"]
    if set(tables) != set(expected_table_fields):
        raise ContractError("Export-bundle table identities are incomplete.")
    expected_filenames = {
        "runs": "tables/runs.csv",
        "turns": "tables/turns.csv",
        "item_sources": "tables/item_sources.csv",
        "reviews": "tables/reviews.csv",
    }
    for table_name, expected_fields in expected_table_fields.items():
        table = tables[table_name]
        _require(table, ("filename", "row_unit", "fields"), f"export table {table_name}")
        if table["filename"] != expected_filenames[table_name]:
            raise ContractError(f"Export table {table_name} has the wrong filename.")
        if not str(table["row_unit"]).strip():
            raise ContractError(f"Export table {table_name} has no row-unit definition.")
        if tuple(table["fields"]) != expected_fields:
            raise ContractError(f"Export table {table_name} fields drifted.")
        if len(table["fields"]) != len(set(table["fields"])):
            raise ContractError(f"Export table {table_name} repeats a field.")

    required_integrity_rules = {
        "runner_record_copied_byte_for_byte",
        "raw_and_parsed_model_files_remain_separate",
        "all_failed_and_incomplete_runs_are_exported",
        "validator_result_never_rewrites_runner_record",
        "stale_validator_result_cannot_confer_formal_eligibility",
        "export_directory_is_append_only",
    }
    if set(document["integrity_rules"]) != required_integrity_rules:
        raise ContractError("Export-bundle integrity rules are incomplete.")
    required_boundaries = {
        "metric_calculation_performed": False,
        "comparison_claim_generated": False,
        "human_coding_performed": False,
        "formal_runs_started": False,
    }
    if document["claim_boundaries"] != required_boundaries:
        raise ContractError("Export-bundle claim boundaries are incomplete.")


def validate_metric_calculation_schema(document: dict[str, Any]) -> None:
    """Validate append-only metric outputs and the human-coding boundary."""

    _require(
        document,
        (
            "schema_version",
            "contract_version",
            "preregistration",
            "input",
            "required_outputs",
            "tables",
            "calculation_statuses",
            "detail_statuses",
            "integrity_rules",
            "claim_boundaries",
        ),
        "metric calculation schema",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected metric-calculation schema version.")
    if document["contract_version"] not in {"1.0", "2.0"}:
        raise ContractError("Unexpected metric-calculation contract version.")
    if document["preregistration"].get("status") != "frozen_before_formal_runs":
        raise ContractError("Metric outputs are not frozen before formal runs.")
    if document["input"].get("type") != "completed_evaluation_export_bundle":
        raise ContractError("Metric input must be one completed export bundle.")

    if document["contract_version"] == "2.0":
        contract_outputs = {
            "contracts/metric_calculation_schema.v2.json",
            "contracts/metrics.v3.json",
            "contracts/scenarios.v4.json",
        }
    else:
        contract_outputs = {
            "contracts/metric_calculation_schema.v1.json",
            "contracts/metrics.v2.json",
            "contracts/scenarios.v3.json",
        }
    expected_outputs = {
        "calculation_manifest.json",
        "input/export_manifest.json",
        "metrics/detail.csv",
        "metrics/run_summary.csv",
        "metrics/condition_summary.csv",
        "metrics/pair_summary.csv",
        "metrics/summary.json",
    } | contract_outputs
    if set(document["required_outputs"]) != expected_outputs:
        raise ContractError("Metric-calculation required outputs are incomplete.")

    tables = document["tables"]
    if set(tables) != set(METRIC_OUTPUT_TABLE_FIELDS):
        raise ContractError("Metric-calculation table identities are incomplete.")
    expected_filenames = {
        "detail": "metrics/detail.csv",
        "run_summary": "metrics/run_summary.csv",
        "condition_summary": "metrics/condition_summary.csv",
        "pair_summary": "metrics/pair_summary.csv",
    }
    for table_name, expected_fields in METRIC_OUTPUT_TABLE_FIELDS.items():
        table = tables[table_name]
        _require(
            table,
            ("filename", "row_unit", "fields"),
            f"metric output table {table_name}",
        )
        if table["filename"] != expected_filenames[table_name]:
            raise ContractError(f"Metric output table {table_name} has the wrong filename.")
        if tuple(table["fields"]) != expected_fields:
            raise ContractError(f"Metric output table {table_name} fields drifted.")
        if len(table["fields"]) != len(set(table["fields"])):
            raise ContractError(f"Metric output table {table_name} repeats a field.")

    expected_statuses = {
        "PASS",
        "FAIL",
        "INCOMPLETE",
        "NOT_APPLICABLE",
        "PENDING_MANUAL",
    }
    if set(document["calculation_statuses"]) != expected_statuses:
        raise ContractError("Metric-calculation statuses are incomplete.")
    if set(document["detail_statuses"]) != {
        "PASS",
        "FAIL",
        "NOT_EVALUABLE",
        "PENDING_MANUAL",
    }:
        raise ContractError("Metric detail statuses are incomplete.")
    required_integrity_rules = {
        "input_export_is_read_only",
        "input_inventory_is_verified_before_and_after",
        "all_exported_run_attempts_are_retained",
        "zero_denominators_are_explicit",
        "failed_units_are_listed",
        "ambiguous_or_missing_pairs_are_not_silently_selected",
        "calculation_directory_is_append_only",
    }
    if set(document["integrity_rules"]) != required_integrity_rules:
        raise ContractError("Metric-calculation integrity rules are incomplete.")
    required_boundaries = {
        "automatic_metrics_calculated": True,
        "ucr_human_coding_performed": False,
        "mp_human_coding_performed": False,
        "comparative_superiority_claim_generated": False,
        "formal_runs_started": False,
        "database_or_model_used": False,
    }
    if document["claim_boundaries"] != required_boundaries:
        raise ContractError("Metric-calculation claim boundaries are incomplete.")


def validate_freeze_manifest(document: dict[str, Any], spec_dir: Path) -> None:
    _require(
        document,
        (
            "schema_version",
            "product_baseline",
            "formal_runs_started",
            "run_plan",
            "frozen_files",
        ),
        "freeze manifest",
    )
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ContractError("Unexpected freeze-manifest schema version.")
    contract_version = document.get("contract_version", "1.0")
    supported_versions = {
        "1.0",
        "2.0",
        "3.0",
        "4.0",
        "5.0",
        "6.0",
        "7.0",
        "8.0",
        "9.0",
        "10.0",
        "10.1",
        CURRENT_MANIFEST_CONTRACT_VERSION,
    }
    if contract_version not in supported_versions:
        raise ContractError("Unexpected freeze-manifest contract version.")
    if contract_version in {"10.1", CURRENT_MANIFEST_CONTRACT_VERSION}:
        local_commit = document["product_baseline"].get("local_baseline_commit")
        pending = f"PENDING_V{contract_version.replace('.', '_')}_REVIEW_COMMIT"
        if local_commit != pending and not (
            isinstance(local_commit, str)
            and re.fullmatch(r"[0-9a-f]{40}", local_commit)
        ):
            raise ContractError(
                f"The v{contract_version} local Baseline commit must be the pending sentinel or "
                "a full lowercase 40-character hexadecimal commit hash."
            )
    if contract_version in {
        "2.0",
        "3.0",
        "4.0",
        "5.0",
        "6.0",
        "7.0",
        "8.0",
        "9.0",
        "10.0",
        "10.1",
        CURRENT_MANIFEST_CONTRACT_VERSION,
    }:
        _require(
            document,
            ("supersedes", "revision_reason"),
            f"freeze manifest v{contract_version}",
        )
        expected_superseded = {
            "2.0": "freeze_manifest.v1.json",
            "3.0": "freeze_manifest.v2.json",
            "4.0": "freeze_manifest.v3.json",
            "5.0": "freeze_manifest.v4.json",
            "6.0": "freeze_manifest.v5.json",
            "7.0": "freeze_manifest.v6.json",
            "8.0": "freeze_manifest.v7.json",
            "9.0": "freeze_manifest.v8.json",
            "10.0": "freeze_manifest.v9.json",
            "10.1": "freeze_manifest.v10.json",
            "10.2": "freeze_manifest.v10_1.json",
        }[contract_version]
        if document["supersedes"] != expected_superseded:
            raise ContractError(
                f"Freeze manifest v{contract_version} must preserve the prior lineage."
            )
    if document["formal_runs_started"] is not False:
        raise ContractError("Step 3 manifest must precede all formal runs.")
    if contract_version not in {
        "8.0",
        "9.0",
        "10.0",
        "10.1",
        CURRENT_MANIFEST_CONTRACT_VERSION,
    }:
        _require(document, ("live_openai_gate",), "historical freeze manifest")
        if document["live_openai_gate"].get("status") != "PENDING":
            raise ContractError("The historical live OpenAI gate must remain PENDING.")
    else:
        _require(
            document,
            ("formal_comparison", "evidence_recording"),
            f"freeze manifest v{contract_version}",
        )
        comparison = document["formal_comparison"]
        _require(
            comparison,
            (
                "status",
                "eligibility_source",
                "external_gate_artifact_required",
                "model_id",
                "model_parameters_by_condition",
                "required_run_evidence",
                "matched_pair_requirements",
            ),
            "formal comparison freeze",
        )
        if comparison["status"] != "FROZEN_BEFORE_LIVE_RESULTS":
            raise ContractError("Formal comparison must be frozen before live results.")
        if comparison["eligibility_source"] != (
            "directly_recorded_run_and_matched_pair_evidence"
        ):
            raise ContractError("Formal eligibility must use directly recorded evidence.")
        if comparison["external_gate_artifact_required"] is not False:
            raise ContractError("Formal comparison cannot depend on an external gate artefact.")
        if comparison["model_id"] != "gpt-4.1-mini":
            raise ContractError("The comparison model is not the frozen model.")
        assessment_call_kind = (
            "protocol_coverage_check"
            if contract_version in {"10.0", "10.1", CURRENT_MANIFEST_CONTRACT_VERSION}
            else "semantic_assessment"
        )
        expected_parameters = {
            MVP_CONDITION: {
                assessment_call_kind: {
                    "temperature": 0,
                    "max_output_tokens": 500,
                    "store": False,
                },
                "follow_up_wording": {
                    "temperature": 0.2,
                    "max_output_tokens": 120,
                    "store": False,
                },
            },
            BASELINE_CONDITION: {
                "prompt_only_turn": {
                    "temperature": 0,
                    "max_output_tokens": 500,
                    "store": False,
                }
            },
        }
        if comparison["model_parameters_by_condition"] != expected_parameters:
            raise ContractError("Frozen model-call parameters are incomplete or changed.")
        required_run_evidence = {
            "formal_flags_agree",
            "tracked_worktree_clean",
            "code_commit_and_tree_recorded",
            "runtime_model_matches_frozen_model",
            "openai_api_key_configured",
            "raw_model_calls_recorded",
            "raw_model_calls_completed",
            "raw_model_call_envelopes_match_frozen_parameters",
            "condition_call_kind_recorded",
            "mock_transport_absent",
            "fallback_reason_absent",
            "named_active_reviewer",
            "protocol_snapshot_and_sha256_recorded",
            "frozen_contract_hashes_recorded",
            "transcript_decisions_review_and_evidence_record_recorded",
        }
        if contract_version in {"10.0", "10.1", CURRENT_MANIFEST_CONTRACT_VERSION}:
            required_run_evidence.add(
                "versioned_consent_snapshot_timestamp_and_sha256_recorded"
            )
        if set(comparison["required_run_evidence"]) != required_run_evidence:
            raise ContractError("Formal per-run evidence requirements are incomplete.")
        matched_pair_requirements = {
            "same_scenario_and_repetition",
            "same_participant_inputs_and_order",
            "same_model_id",
            "same_code_commit",
            "same_protocol_snapshot_sha256",
            "same_scenario_sha256",
            "same_metric_rules_sha256",
            "same_scenario_instance_sha256",
            "one_mvp_and_one_prompt_only_baseline_run",
            "both_runs_independently_validated",
        }
        if set(comparison["matched_pair_requirements"]) != matched_pair_requirements:
            raise ContractError("Matched-pair fairness requirements are incomplete.")

        recording = document["evidence_recording"]
        _require(
            recording,
            (
                "required_per_run",
                "required_for_final_evidence_package",
                "all_attempts_retained",
                "runner_validator_exporter_and_calculator_separated",
            ),
            "evidence recording freeze",
        )
        required_per_run = {
            "run_id",
            "condition_scenario_and_repetition",
            "code_commit_tree_and_clean_status",
            "model_id_and_call_parameters",
            "baseline_prompt_and_schema_sha256_when_applicable",
            "protocol_snapshot_and_sha256",
            "scenario_metric_review_and_manifest_sha256",
            "raw_model_requests_and_responses",
            "parsed_outputs_and_agent_decisions",
            "participant_turns_and_full_transcript",
            "structured_items_source_relations_and_limitations",
            "review_events_and_overall_review",
            "evidence_record",
            "validator_result",
            "execution_parsing_validation_and_metric_failures",
        }
        if contract_version in {"10.0", "10.1", CURRENT_MANIFEST_CONTRACT_VERSION}:
            required_per_run.add(
                "consent_notice_version_snapshot_timestamp_and_sha256"
            )
        if set(recording["required_per_run"]) != required_per_run:
            raise ContractError("Per-run evidence recording requirements are incomplete.")
        required_final = {
            "selected_interface_screenshots_tied_to_run_id",
            "blinded_ab_review_material",
            "ucr_atomic_proposition_coding",
            "meaning_preservation_coding",
            "coder_identity_disagreement_and_consensus_records",
        }
        if set(recording["required_for_final_evidence_package"]) != required_final:
            raise ContractError("Final evidence-package requirements are incomplete.")
        if recording["all_attempts_retained"] is not True:
            raise ContractError("Every attempted run and failure must be retained.")
        if recording["runner_validator_exporter_and_calculator_separated"] is not True:
            raise ContractError("Evaluation responsibilities must remain separated.")
        if contract_version == "10.1":
            expected_gate = {
                "status": "REQUIRED_BEFORE_FINAL_FREEZE",
                "condition": BASELINE_CONDITION,
                "run_type": "dry_run",
                "transport": "openai_responses_api",
                "planned_attempt_count": 16,
                "required_execution_status": "completed",
                "required_validation_status": "PASS",
                "formal_evidence_eligible": False,
                "all_attempts_retained": True,
                "replacement_or_formal_reuse_forbidden": True,
            }
            if document.get("baseline_development_gate") != expected_gate:
                raise ContractError(
                    "The v10.1 Baseline development-gate declaration is incomplete."
                )
        if contract_version == CURRENT_MANIFEST_CONTRACT_VERSION:
            expected_observation = {
                "status": "HISTORICAL_OBSERVATION_NOT_A_CRITERION",
                "condition": BASELINE_CONDITION,
                "reported_attempt_count": 16,
                "reported_pass_count": 13,
                "reported_retained_failure_count": 3,
                "raw_directory_available": False,
                "independently_reverifiable": False,
                "formal_evidence_eligible": False,
                "formal_readiness_criterion": False,
                "rerun_forbidden": True,
                "reconstruction_forbidden": True,
            }
            if document.get("baseline_development_gate") != expected_observation:
                raise ContractError(
                    "The v10.2 historical development observation must remain transparent and non-evidentiary."
                )
    run_plan = document["run_plan"]
    if (
        run_plan.get("minimum_individual_sessions") != 16
        or run_plan.get("minimum_matched_pairs") != 8
        or run_plan.get("recommended_individual_sessions") != 32
        or run_plan.get("recommended_matched_pairs") != 16
    ):
        raise ContractError("Run-plan session and pair counts do not match the classroom rule.")

    expected_path_sets = {
        "1.0": {
            "scenarios.v1.json",
            "metrics.v1.json",
            "baseline_contract.v1.json",
        },
        "2.0": {
            "scenarios.v2.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
        },
        "3.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
        },
        "4.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "5.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "export_bundle_schema.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "6.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "export_bundle_schema.v1.json",
            "metric_calculation_schema.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "7.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "export_bundle_schema.v1.json",
            "metric_calculation_schema.v1.json",
            "review_completion.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "8.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "export_bundle_schema.v1.json",
            "metric_calculation_schema.v1.json",
            "review_completion.v1.json",
            "quality_review.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "9.0": {
            "scenarios.v3.json",
            "metrics.v2.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v1.json",
            "export_bundle_schema.v1.json",
            "metric_calculation_schema.v1.json",
            "review_completion.v2.json",
            "quality_review.v1.json",
            "../prompts/prompt_only_baseline.v1.txt",
        },
        "10.0": {
            "scenarios.v4.json",
            "metrics.v3.json",
            "baseline_contract.v1.json",
            "prompt_only_output_schema.v2.json",
            "export_bundle_schema.v2.json",
            "metric_calculation_schema.v2.json",
            "review_completion.v2.json",
            "quality_review.v1.json",
            "../prompts/prompt_only_baseline.v2.txt",
        },
        "10.1": {
            "scenarios.v4.json",
            "metrics.v3.json",
            "baseline_contract.v2.json",
            "prompt_only_output_schema.v2.json",
            "export_bundle_schema.v2.json",
            "metric_calculation_schema.v2.json",
            "review_completion.v2.json",
            "quality_review.v2.json",
            "../prompts/prompt_only_baseline.v3.txt",
        },
        "10.2": {
            "scenarios.v4.json",
            "metrics.v3.json",
            "baseline_contract.v2.json",
            "prompt_only_output_schema.v2.json",
            "export_bundle_schema.v2.json",
            "metric_calculation_schema.v2.json",
            "review_completion.v2.json",
            "quality_review.v2.json",
            "../prompts/prompt_only_baseline.v3.txt",
        },
    }
    frozen_files = document["frozen_files"]
    if set(frozen_files) != expected_path_sets[contract_version]:
        raise ContractError(
            f"Freeze manifest v{contract_version} must identify its exact frozen asset set."
        )
    for relative_path, expected_sha256 in frozen_files.items():
        path = (spec_dir / relative_path).resolve()
        try:
            path.relative_to(spec_dir.parent.resolve())
        except ValueError as error:
            raise ContractError(
                f"Frozen evaluation asset escapes the evaluation directory: {relative_path}."
            ) from error
        actual_sha256 = sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise ContractError(
                f"Frozen contract drifted: {relative_path}; expected {expected_sha256}, "
                f"got {actual_sha256}."
            )

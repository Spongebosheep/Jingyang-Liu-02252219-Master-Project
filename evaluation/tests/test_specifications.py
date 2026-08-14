import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from evaluation.schemas import (
    ContractError,
    load_json,
    validate_baseline_contract,
    validate_baseline_output_schema,
    validate_export_bundle_schema,
    validate_freeze_manifest,
    validate_metric_calculation_schema,
    validate_metrics,
    validate_quality_review,
    validate_review_completion,
    validate_scenarios,
    pair_integrity_from_run_rows,
    sha256_file,
)
from evaluation.validate_specs import SPEC_DIR, validate_all


class EvaluationSpecificationTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = load_json(SPEC_DIR / "scenarios.v4.json")
        self.metrics = load_json(SPEC_DIR / "metrics.v3.json")
        self.baseline = load_json(SPEC_DIR / "baseline_contract.v2.json")
        self.baseline_output_schema = load_json(
            SPEC_DIR / "prompt_only_output_schema.v2.json"
        )
        self.export_bundle_schema = load_json(
            SPEC_DIR / "export_bundle_schema.v2.json"
        )
        self.metric_calculation_schema = load_json(
            SPEC_DIR / "metric_calculation_schema.v2.json"
        )
        self.review_completion = load_json(
            SPEC_DIR / "review_completion.v2.json"
        )
        self.quality_review = load_json(SPEC_DIR / "quality_review.v2.json")

    def test_all_frozen_contracts_validate(self):
        result = validate_all()
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["formal_run"])
        self.assertEqual(len(result["contracts"]), 9)

    def test_scenario_file_has_exactly_s1_to_s8(self):
        validate_scenarios(self.scenarios)
        self.assertEqual(
            [scenario["scenario_id"] for scenario in self.scenarios["scenarios"]],
            [f"S{index}" for index in range(1, 9)],
        )

    def test_result_like_field_is_rejected_from_oracle(self):
        changed = copy.deepcopy(self.scenarios)
        changed["scenarios"][0]["steps"][2]["oracle"]["observed"] = "move_next"
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_missing_control_transition_is_rejected(self):
        changed = copy.deepcopy(self.scenarios)
        skip_step = next(
            step
            for step in changed["scenarios"][3]["steps"]
            if step.get("control") == "skip"
        )
        skip_step["oracle"]["expected_action"] = "move_next"
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_s5_has_frozen_zero_record_post_stop_probe(self):
        validate_scenarios(self.scenarios)
        s5 = self.scenarios["scenarios"][4]
        stop_step, probe_step = s5["steps"][-2:]
        self.assertEqual(stop_step["oracle"]["expected_action"], "stop")
        self.assertEqual(probe_step["kind"], "post_stop_probe")
        self.assertEqual(
            probe_step["participant_text"],
            "This fixed evaluation probe must not be collected after Stop.",
        )
        self.assertEqual(probe_step["oracle"]["expected_new_message_count"], 0)
        self.assertEqual(probe_step["oracle"]["expected_new_decision_count"], 0)

    def test_post_stop_probe_cannot_expect_new_records(self):
        changed = copy.deepcopy(self.scenarios)
        changed["scenarios"][4]["steps"][-1]["oracle"][
            "expected_new_message_count"
        ] = 1
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_review_completion_is_limited_to_non_approval_s7_s8_actions(self):
        validate_review_completion(self.review_completion)
        self.assertEqual(
            self.review_completion["applicable_scenarios"], ["S7", "S8"]
        )
        self.assertTrue(
            all(
                action["decision"] == "request_revision"
                for action in self.review_completion["actions"].values()
            )
        )
        self.assertFalse(
            self.review_completion["claim_boundaries"]["overall_approval_claimed"]
        )
        self.assertEqual(
            [
                action["section_index"]
                for action in self.review_completion["actions"]["S8"][
                    "pre_focal_item_actions"
                ]
            ],
            [2],
        )
        for scenario_id in ("S7", "S8"):
            statuses = self.review_completion["actions"][scenario_id]["oracle"][
                "expected_review_statuses"
            ]
            self.assertNotIn("pending", statuses.values())

    def test_review_completion_cannot_claim_approval_or_human_judgement(self):
        changed = copy.deepcopy(self.review_completion)
        changed["actions"]["S7"]["decision"] = "approve"
        with self.assertRaises(ContractError):
            validate_review_completion(changed)

        changed = copy.deepcopy(self.review_completion)
        changed["actions"]["S8"]["participant_meaning_preserved"] = True
        with self.assertRaises(ContractError):
            validate_review_completion(changed)

    def test_blinded_quality_review_freezes_classroom_questions_and_coder_rules(self):
        validate_quality_review(self.quality_review)
        self.assertEqual(
            [row["name"] for row in self.quality_review["criteria"]],
            [
                "Protocol coverage",
                "Probing relevance and neutrality",
                "Transcript grounding and fidelity",
                "Participant clarity and control",
                "Researcher reviewability",
            ],
        )
        self.assertEqual(self.quality_review["minimum_independent_coders"], 2)
        self.assertEqual(
            self.quality_review["pair_judgement"]["overall_preference"],
            ["A", "B", "Tie"],
        )

    def test_quality_review_cannot_expose_condition_or_replace_ucr_mp(self):
        changed = copy.deepcopy(self.quality_review)
        changed["blinding"]["excluded_from_coder_material"].remove(
            "mvp_or_prompt_only_condition_name"
        )
        with self.assertRaises(ContractError):
            validate_quality_review(changed)

        changed = copy.deepcopy(self.quality_review)
        changed["boundaries"]["five_quality_questions_do_not_replace_ucr_or_mp"] = False
        with self.assertRaises(ContractError):
            validate_quality_review(changed)

    def test_every_scenario_has_the_fixed_opening_after_consent(self):
        validate_scenarios(self.scenarios)
        for scenario in self.scenarios["scenarios"]:
            self.assertEqual(scenario["steps"][0]["kind"], "consent")
            self.assertTrue(
                scenario["steps"][0]["oracle"]["consent_snapshot_recorded"]
            )
            self.assertTrue(
                scenario["steps"][0]["oracle"]["consent_snapshot_sha256_valid"]
            )
            self.assertTrue(
                scenario["steps"][0]["oracle"]["consent_confirmed_at_recorded"]
            )
            opening = scenario["steps"][1]
            self.assertEqual(opening["step_id"], f"{scenario['scenario_id']}-O01")
            self.assertEqual(opening["section_index"], 0)
            self.assertEqual(opening["section_code"], "opening")
            self.assertEqual(opening["participant_text"], "Yes, it is okay to continue.")
            self.assertTrue(opening["oracle"]["eligible_for_ai"])
            self.assertFalse(opening["oracle"]["eligible_for_ac"])
            self.assertEqual(
                opening["oracle"]["expected_coverage_assessment_any_of"],
                ["not_assessed"],
            )
            self.assertEqual(
                opening["oracle"]["expected_participant_control"],
                "none",
            )

    def test_v4_separates_protocol_coverage_control_and_topic_reach(self):
        validate_scenarios(self.scenarios)
        serialized = json.dumps(self.scenarios, sort_keys=True)
        self.assertNotIn("answer_status_any_of", serialized)
        for scenario in self.scenarios["scenarios"]:
            for topic in scenario["expected_topic_outcomes"]:
                self.assertIn(
                    topic["coverage_status"],
                    {"covered", "partially_covered", "not_covered", "not_assessed"},
                )
                self.assertIn(topic["participant_control"], {"none", "skip", "stop"})
                self.assertIsInstance(topic["topic_reached"], bool)

    def test_missing_opening_is_rejected(self):
        changed = copy.deepcopy(self.scenarios)
        del changed["scenarios"][0]["steps"][1]
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_opening_cannot_be_made_ac_eligible(self):
        changed = copy.deepcopy(self.scenarios)
        changed["scenarios"][0]["steps"][1]["oracle"]["eligible_for_ac"] = True
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_incomplete_end_to_end_script_is_rejected(self):
        changed = copy.deepcopy(self.scenarios)
        changed["scenarios"][0]["steps"] = changed["scenarios"][0]["steps"][:-1]
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_missing_topic_outcome_is_rejected(self):
        changed = copy.deepcopy(self.scenarios)
        changed["scenarios"][6]["expected_topic_outcomes"] = changed["scenarios"][6][
            "expected_topic_outcomes"
        ][:-1]
        with self.assertRaises(ContractError):
            validate_scenarios(changed)

    def test_ucr_cannot_be_replaced_by_rating(self):
        changed = copy.deepcopy(self.metrics)
        ucr = next(metric for metric in changed["metrics"] if metric["code"] == "UCR")
        ucr["calculation_mode"] = "five_point_rating"
        with self.assertRaises(ContractError):
            validate_metrics(changed)

    def test_mp_remains_manual(self):
        changed = copy.deepcopy(self.metrics)
        mp = next(metric for metric in changed["metrics"] if metric["code"] == "MP")
        mp["calculation_mode"] = "automatic"
        with self.assertRaises(ContractError):
            validate_metrics(changed)

    def test_metric_v3_freezes_eligibility_and_manual_coding_rules(self):
        validate_metrics(self.metrics)
        changed = copy.deepcopy(self.metrics)
        del changed["manual_coding_rules"]["UCR"]["segmentation"]
        with self.assertRaises(ContractError):
            validate_metrics(changed)

    def test_baseline_cannot_reuse_langgraph_routing(self):
        changed = copy.deepcopy(self.baseline)
        changed["prohibited_reuse"].remove("langgraph_routing")
        with self.assertRaises(ContractError):
            validate_baseline_contract(changed)

    def test_baseline_v2_freezes_coverage_precedence_and_live_gate(self):
        validate_baseline_contract(self.baseline)
        self.assertEqual(
            self.baseline["coverage_interpretation"]["binding_threshold"],
            "current_section.assessment_guidance",
        )
        self.assertEqual(
            self.baseline["development_gate"]["planned_attempt_count"], 16
        )
        self.assertFalse(
            self.baseline["development_gate"]["formal_evidence_eligible"]
        )

    def test_baseline_output_schema_matches_shared_control_fields(self):
        validate_baseline_output_schema(self.baseline_output_schema)
        self.assertIn("coverage_assessment", self.baseline_output_schema["required"])
        self.assertIn("participant_control", self.baseline_output_schema["required"])
        self.assertNotIn("answer_status", self.baseline_output_schema["properties"])
        changed = copy.deepcopy(self.baseline_output_schema)
        changed["properties"]["selected_action"]["enum"].remove("stop")
        with self.assertRaises(ContractError):
            validate_baseline_output_schema(changed)

    def test_export_bundle_fields_and_claim_boundaries_are_frozen(self):
        validate_export_bundle_schema(self.export_bundle_schema)
        self.assertIn(
            "consent_snapshot_sha256",
            self.export_bundle_schema["tables"]["runs"]["fields"],
        )
        self.assertIn(
            "participant_control",
            self.export_bundle_schema["tables"]["item_sources"]["fields"],
        )
        self.assertIn(
            "topic_reached",
            self.export_bundle_schema["tables"]["item_sources"]["fields"],
        )
        changed = copy.deepcopy(self.export_bundle_schema)
        changed["tables"]["turns"]["fields"].remove("selected_action")
        with self.assertRaises(ContractError):
            validate_export_bundle_schema(changed)

        changed = copy.deepcopy(self.export_bundle_schema)
        changed["claim_boundaries"]["metric_calculation_performed"] = True
        with self.assertRaises(ContractError):
            validate_export_bundle_schema(changed)

    def test_metric_output_fields_and_human_boundaries_are_frozen(self):
        validate_metric_calculation_schema(self.metric_calculation_schema)
        changed = copy.deepcopy(self.metric_calculation_schema)
        changed["tables"]["detail"]["fields"].remove("failure_reasons_json")
        with self.assertRaises(ContractError):
            validate_metric_calculation_schema(changed)

        changed = copy.deepcopy(self.metric_calculation_schema)
        changed["claim_boundaries"]["ucr_human_coding_performed"] = True
        with self.assertRaises(ContractError):
            validate_metric_calculation_schema(changed)

        changed = copy.deepcopy(self.metric_calculation_schema)
        changed["claim_boundaries"]["comparative_superiority_claim_generated"] = True
        with self.assertRaises(ContractError):
            validate_metric_calculation_schema(changed)

    def test_manifest_rejects_frozen_contract_drift(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_spec_dir = Path(temporary_directory) / "evaluation" / "specs"
            temporary_spec_dir.mkdir(parents=True)
            for filename in manifest["frozen_files"]:
                source = (SPEC_DIR / filename).resolve()
                destination = (temporary_spec_dir / filename).resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            scenario_filename = next(
                filename
                for filename in manifest["frozen_files"]
                if filename.startswith("scenarios.")
            )
            changed_scenarios = json.loads(
                (temporary_spec_dir / scenario_filename).read_text(encoding="utf-8")
            )
            changed_scenarios["scenarios"][0]["title"] = "Post-freeze drift"
            (temporary_spec_dir / scenario_filename).write_text(
                json.dumps(changed_scenarios),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                validate_freeze_manifest(manifest, temporary_spec_dir)

    def test_historical_v1_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v1.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v2_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v2.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v3_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v3.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v4_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v4.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v5_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v5.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v6_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v6.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v7_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v7.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v8_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v8.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_historical_v9_manifest_still_validates(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v9.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

    def test_v8_uses_direct_run_evidence_without_changing_frozen_assets(self):
        v7 = load_json(SPEC_DIR / "freeze_manifest.v7.json")
        v8 = load_json(SPEC_DIR / "freeze_manifest.v8.json")
        validate_freeze_manifest(v8, SPEC_DIR)
        self.assertFalse(
            v8["formal_comparison"]["external_gate_artifact_required"]
        )
        self.assertEqual(
            v8["formal_comparison"]["eligibility_source"],
            "directly_recorded_run_and_matched_pair_evidence",
        )
        unchanged_files = dict(v8["frozen_files"])
        self.assertEqual(
            unchanged_files.pop("quality_review.v1.json"),
            "ffa2cdda721a99dd519a71ef32697ec94311c41df044b55610be9ad0a8cfb477",
        )
        self.assertEqual(unchanged_files, v7["frozen_files"])
        self.assertEqual(v8["run_plan"], v7["run_plan"])

    def test_current_manifest_rejects_missing_pair_fairness_rule(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        changed = copy.deepcopy(manifest)
        changed["formal_comparison"]["matched_pair_requirements"].remove(
            "same_model_id"
        )
        with self.assertRaises(ContractError):
            validate_freeze_manifest(changed, SPEC_DIR)

    def test_v9_changes_only_review_completion_and_keeps_comparison_assets(self):
        v8 = load_json(SPEC_DIR / "freeze_manifest.v8.json")
        v9 = load_json(SPEC_DIR / "freeze_manifest.v9.json")
        validate_freeze_manifest(v9, SPEC_DIR)
        self.assertEqual(v9["run_plan"], v8["run_plan"])
        self.assertEqual(v9["formal_comparison"], v8["formal_comparison"])
        unchanged_v8 = dict(v8["frozen_files"])
        unchanged_v9 = dict(v9["frozen_files"])
        self.assertEqual(
            unchanged_v8.pop("review_completion.v1.json"),
            "7a57e2f79c7299c197c77dfa3073c06ef288c87601abc5da2fb43d39da9c364a",
        )
        self.assertEqual(
            unchanged_v9.pop("review_completion.v2.json"),
            "74b32fd74d45033331d32f3aae605e68066f1b86ef98e44d3c2c48f6ef064c72",
        )
        self.assertEqual(unchanged_v9, unchanged_v8)

    def test_v10_records_construct_and_consent_revision_before_formal_runs(self):
        v9 = load_json(SPEC_DIR / "freeze_manifest.v9.json")
        v10 = load_json(SPEC_DIR / "freeze_manifest.v10.json")
        validate_freeze_manifest(v10, SPEC_DIR)
        self.assertEqual(v10["supersedes"], "freeze_manifest.v9.json")
        self.assertFalse(v10["formal_runs_started"])
        self.assertEqual(
            v10["product_baseline"]["local_baseline_commit"],
            "PENDING_V10_REVIEW_COMMIT",
        )
        self.assertIn(
            "versioned_consent_snapshot_timestamp_and_sha256_recorded",
            v10["formal_comparison"]["required_run_evidence"],
        )
        for unchanged in (
            "baseline_contract.v1.json",
            "review_completion.v2.json",
            "quality_review.v1.json",
        ):
            self.assertEqual(
                v10["frozen_files"][unchanged],
                v9["frozen_files"][unchanged],
            )

    def test_v10_1_records_baseline_gate_and_pair_selection_before_formal_runs(self):
        v10 = load_json(SPEC_DIR / "freeze_manifest.v10.json")
        current = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        validate_freeze_manifest(current, SPEC_DIR)
        self.assertEqual(current["supersedes"], "freeze_manifest.v10.json")
        self.assertFalse(current["formal_runs_started"])
        local_commit = current["product_baseline"]["local_baseline_commit"]
        self.assertTrue(
            local_commit == "PENDING_V10_1_REVIEW_COMMIT"
            or re.fullmatch(r"[0-9a-f]{40}", local_commit)
        )
        self.assertEqual(
            current["baseline_development_gate"]["planned_attempt_count"], 16
        )
        self.assertFalse(
            current["baseline_development_gate"]["formal_evidence_eligible"]
        )
        self.assertEqual(current["run_plan"], v10["run_plan"])
        self.assertIn("baseline_contract.v2.json", current["frozen_files"])
        self.assertIn("quality_review.v2.json", current["frozen_files"])
        self.assertIn(
            "../prompts/prompt_only_baseline.v3.txt", current["frozen_files"]
        )

    def test_v10_2_changes_method_only_and_preserves_all_evaluated_hashes(self):
        prior = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        current = load_json(SPEC_DIR / "freeze_manifest.v10_2.json")
        validate_freeze_manifest(current, SPEC_DIR)
        self.assertEqual(current["supersedes"], "freeze_manifest.v10_1.json")
        self.assertFalse(current["formal_runs_started"])
        self.assertEqual(current["run_plan"], prior["run_plan"])
        self.assertEqual(current["formal_comparison"], prior["formal_comparison"])
        self.assertEqual(current["frozen_files"], prior["frozen_files"])
        local_commit = current["product_baseline"]["local_baseline_commit"]
        self.assertTrue(
            local_commit == "PENDING_V10_2_REVIEW_COMMIT"
            or re.fullmatch(r"[0-9a-f]{40}", local_commit)
        )
        observation = current["baseline_development_gate"]
        self.assertEqual(observation["reported_pass_count"], 13)
        self.assertFalse(observation["raw_directory_available"])
        self.assertFalse(observation["formal_evidence_eligible"])
        self.assertTrue(observation["rerun_forbidden"])

    def test_v10_1_contract_and_manifest_remain_byte_unchanged(self):
        self.assertEqual(
            sha256_file(SPEC_DIR / "freeze_manifest.v10_1.json"),
            "3d89d5a73a05e5813486ecc857a09919b03fda2405a04fbe789ccd99c0235e3c",
        )
        self.assertEqual(
            sha256_file(SPEC_DIR.parent.parent / "docs/V10_1_EXECUTION_CONTRACT.md"),
            "938d6d34995d1aa6665a4b89a8cd9a7fe0ecaf4aabc0050b4124fcc15fda05ed",
        )

    def test_missing_historical_development_directory_is_not_required(self):
        self.assertEqual(validate_all()["status"], "PASS")

    def test_v10_2_accepts_pending_or_finalized_lowercase_commit(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_2.json")
        pending = copy.deepcopy(manifest)
        pending["product_baseline"][
            "local_baseline_commit"
        ] = "PENDING_V10_2_REVIEW_COMMIT"
        validate_freeze_manifest(pending, SPEC_DIR)

        finalized = copy.deepcopy(manifest)
        finalized["product_baseline"]["local_baseline_commit"] = "a1" * 20
        validate_freeze_manifest(finalized, SPEC_DIR)

    def test_v10_2_rejects_malformed_short_or_uppercase_commit(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_2.json")
        for invalid in ("not-a-commit", "a" * 39, "A" * 40):
            with self.subTest(invalid=invalid):
                changed = copy.deepcopy(manifest)
                changed["product_baseline"]["local_baseline_commit"] = invalid
                with self.assertRaises(ContractError):
                    validate_freeze_manifest(changed, SPEC_DIR)

    def test_v10_1_accepts_pending_or_finalized_lowercase_commit(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        validate_freeze_manifest(manifest, SPEC_DIR)

        finalized = copy.deepcopy(manifest)
        finalized["product_baseline"]["local_baseline_commit"] = "a1" * 20
        validate_freeze_manifest(finalized, SPEC_DIR)

    def test_v10_1_rejects_malformed_short_or_uppercase_commit(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_1.json")
        for invalid in ("not-a-commit", "a" * 39, "A" * 40):
            with self.subTest(invalid=invalid):
                changed = copy.deepcopy(manifest)
                changed["product_baseline"]["local_baseline_commit"] = invalid
                with self.assertRaises(ContractError):
                    validate_freeze_manifest(changed, SPEC_DIR)

    def test_pair_integrity_rejects_different_models_even_when_runs_claim_formal(self):
        common = {
            "pair_key": "S1-R01",
            "code_commit": "abc123",
            "protocol_snapshot_sha256": "protocol",
            "scenario_sha256": "scenario",
            "metric_rules_sha256": "metrics",
            "scenario_instance_sha256": "instance",
            "formal_evidence_eligible": True,
        }
        rows = [
            {
                **common,
                "run_id": "mvp-run",
                "condition": "mvp",
                "model_id": "gpt-4.1-mini",
            },
            {
                **common,
                "run_id": "baseline-run",
                "condition": "prompt_only_baseline",
                "model_id": "different-model",
            },
        ]
        result = pair_integrity_from_run_rows(rows)
        self.assertEqual(result["fairness_fail_count"], 1)
        self.assertEqual(result["pairs"][0]["status"], "FAIRNESS_FAIL")
        self.assertEqual(result["pairs"][0]["mismatched_fields"], ["model_id"])
        self.assertFalse(result["pairs"][0]["formal_pair_evidence_eligible"])

    def test_validation_does_not_write_an_output_bundle(self):
        before = set(Path(__file__).resolve().parents[1].rglob("*"))
        validate_all()
        after = set(Path(__file__).resolve().parents[1].rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

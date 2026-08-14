import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from evaluation.mvp_runner import MvpScenarioRunner, RUN_TYPE_DRY
from evaluation.run_validator import EvaluationRunValidator, FAIL, PASS
from evaluation.schemas import load_json, sha256_file
from evaluation.validate_specs import SPEC_DIR
from interviews.models import Protocol


class EvaluationRunValidatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_mvp", verbosity=0)
        cls.protocol = Protocol.objects.get(
            title="Sensory Overload Interview",
            version=1,
        )

    @staticmethod
    def assessment_transport_stub():
        scenarios = load_json(SPEC_DIR / "scenarios.v4.json")["scenarios"]
        statuses_by_reply = {}
        for scenario in scenarios:
            for step in scenario["steps"]:
                if step.get("kind") != "participant_turn":
                    continue
                action = step["oracle"]["expected_action"]
                statuses_by_reply[step["participant_text"]] = (
                    "partially_covered"
                    if action in {"ask_follow_up", "flag_missing_and_move_next"}
                    else "covered"
                )

        def assessment(state):
            status = statuses_by_reply[state["reply_text"]]
            return {
                "coverage_assessment": status,
                "covered_information": ["test-covered field"],
                "missing_information": (
                    ["what happened", "why it felt overwhelming"]
                    if status == "partially_covered"
                    else []
                ),
                "evidence_quote": "test transport response",
                "decision_reason": "Validator infrastructure transport stub.",
            }

        return assessment

    def run_scenario(self, scenario_id: str, output_root: Path):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "DISABLE_LLM_FOLLOWUP_WORDING": ""},
            clear=False,
        ), patch(
            "interviews.langgraph_agent.llm_assess_protocol_coverage",
            side_effect=self.assessment_transport_stub(),
        ):
            runner = MvpScenarioRunner(
                scenario_id=scenario_id,
                repetition=1,
                run_type=RUN_TYPE_DRY,
                output_root=output_root,
            )
            runner.run()
        return runner

    @staticmethod
    def check(result, check_id):
        return next(row for row in result["checks"] if row["check_id"] == check_id)

    def test_validator_is_append_only_and_does_not_change_runner_record(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S1", Path(directory))
            before_sha256 = sha256_file(runner.record_path)

            result, result_path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], PASS)
            self.assertFalse(result["formal_evidence_eligible"])
            self.assertTrue(result_path.is_file())
            self.assertNotEqual(result_path, runner.record_path)
            self.assertEqual(sha256_file(runner.record_path), before_sha256)
            self.assertEqual(runner.record["validation_status"], "not_run")

    def test_all_s1_to_s8_match_the_automatic_oracle_in_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = []
            for index in range(1, 9):
                runner = self.run_scenario(f"S{index}", root)
                result, _path = EvaluationRunValidator(
                    record_path=runner.record_path
                ).run()
                results.append(result)

            self.assertEqual(
                {result["scenario_id"] for result in results},
                {f"S{index}" for index in range(1, 9)},
            )
            self.assertTrue(
                all(result["validation_status"] == PASS for result in results),
                {
                    result["scenario_id"]: [
                        row["check_id"]
                        for row in result["checks"]
                        if row["status"] != PASS
                    ]
                    for result in results
                    if result["validation_status"] != PASS
                },
            )
            self.assertTrue(
                all(not result["formal_evidence_eligible"] for result in results)
            )

    def test_validator_rejects_missing_cross_session_agent_and_cross_topic_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S1", Path(directory))
            original = json.loads(runner.record_path.read_text(encoding="utf-8"))
            messages = original["final_database_snapshot"]["messages"]
            agent_same_topic = next(
                row["id"]
                for row in messages
                if row["sender"] == "agent" and row["section_index"] == 1
            )
            participant_other_topic = next(
                row["id"]
                for row in messages
                if row["sender"] == "participant" and row["section_index"] == 2
            )
            cases = {
                "no_source": [],
                "cross_session": [999999999],
                "agent_source": [agent_same_topic],
                "cross_topic": [participant_other_topic],
            }
            for label, source_ids in cases.items():
                with self.subTest(label=label):
                    changed = json.loads(json.dumps(original))
                    item = next(
                        row
                        for row in changed["final_database_snapshot"]["digest_items"]
                        if row["section_index"] == 1
                    )
                    item["source_message_ids"] = source_ids
                    runner.record_path.write_text(
                        json.dumps(changed, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    result, _path = EvaluationRunValidator(
                        record_path=runner.record_path
                    ).run()
                    self.assertEqual(result["validation_status"], FAIL)
                    self.assertEqual(
                        self.check(result, "TOPIC-1-SOURCE-GROUNDING")["status"],
                        FAIL,
                    )
            runner.record_path.write_text(
                json.dumps(original, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    def test_validator_rejects_tampered_action_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S1", Path(directory))
            changed = json.loads(runner.record_path.read_text(encoding="utf-8"))
            step = next(
                row for row in changed["scenario_steps"] if row["step_id"] == "S1-T01"
            )
            step["observed"]["agent_decisions"][0]["selected_action"] = "ask_follow_up"
            runner.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], FAIL)
            self.assertEqual(self.check(result, "S1-T01-ACTION")["status"], FAIL)

    def test_formal_evidence_eligibility_cannot_be_faked_on_dry_record(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S1", Path(directory))
            changed = json.loads(runner.record_path.read_text(encoding="utf-8"))
            changed["formal_run"] = True
            changed["run_type"] = "formal"
            changed["runtime_identity"]["openai_api_key_configured"] = True
            changed["formal_reviewer"] = {
                "user_id": 999,
                "username": "fabricated-reviewer",
                "is_active": True,
            }
            runner.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], FAIL)
            self.assertFalse(result["formal_evidence_eligible"])
            self.assertEqual(
                self.check(result, "RUN-FORMAL-LIVE-MODEL-EVIDENCE")["status"],
                FAIL,
            )

    def test_s5_validator_requires_real_post_stop_zero_record_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S5", Path(directory))
            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(
                result["validation_status"],
                PASS,
                [
                    (row["check_id"], row["expected"], row["observed"])
                    for row in result["checks"]
                    if row["status"] != PASS
                ],
            )
            probe = self.check(result, "S5-P01-ZERO-RECORD-REJECTION")
            self.assertEqual(probe["status"], PASS)
            self.assertEqual(probe["observed"]["new_message_count"], 0)
            self.assertEqual(probe["observed"]["new_decision_count"], 0)

    def test_s6_validator_rejects_boundary_message_as_evidence_source(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S6", Path(directory))
            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], PASS)
            boundary_check = self.check(result, "TOPIC-1-BOUNDARY-NOT-EVIDENCE")
            self.assertEqual(boundary_check["status"], PASS)
            self.assertEqual(boundary_check["observed"], [])

    def test_s7_validator_requires_the_frozen_completed_overall_review_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S7", Path(directory))
            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], PASS)
            overall = self.check(result, "OVERALL-REVIEW-AUDIT-FIELDS")
            self.assertEqual(overall["status"], PASS)
            self.assertEqual(overall["observed"]["decision"], "revision_requested")
            self.assertFalse(overall["observed"]["remaining_items_auto_resolved"])

            changed = json.loads(runner.record_path.read_text(encoding="utf-8"))
            changed["final_database_snapshot"]["review_decision"][
                "reviewer_note"
            ] = ""
            runner.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            failed, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()
            self.assertEqual(failed["validation_status"], FAIL)
            self.assertEqual(
                self.check(failed, "OVERALL-REVIEW-AUDIT-FIELDS")["status"],
                FAIL,
            )

    def test_s8_validator_rejects_a_missing_or_reordered_completion_action(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S8", Path(directory))
            result, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()

            self.assertEqual(result["validation_status"], PASS)
            self.assertEqual(
                self.check(result, "REVIEW-COMPLETION-ITEM-ACTIONS")["status"],
                PASS,
            )
            self.assertEqual(
                self.check(result, "REVIEW-COMPLETION-ACTION-ORDER")["status"],
                PASS,
            )

            changed = json.loads(runner.record_path.read_text(encoding="utf-8"))
            removed = False
            retained_events = []
            for event in changed["harness_events"]:
                if (
                    not removed
                    and event.get("event") == "review_completion_item_action"
                    and (event.get("frozen_input") or {}).get("section_index") == 3
                ):
                    removed = True
                    continue
                retained_events.append(event)
            self.assertTrue(removed)
            changed["harness_events"] = retained_events
            runner.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            failed, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()
            self.assertEqual(failed["validation_status"], FAIL)
            self.assertEqual(
                self.check(failed, "REVIEW-COMPLETION-ITEM-ACTIONS")["status"],
                FAIL,
            )
            self.assertEqual(
                self.check(failed, "REVIEW-COMPLETION-ACTION-ORDER")["status"],
                FAIL,
            )

    def test_management_command_validates_saved_record(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.run_scenario("S1", Path(directory))
            stdout = StringIO()

            call_command(
                "validate_evaluation_runs",
                record=[runner.record_path],
                stdout=stdout,
                verbosity=0,
            )

            output = stdout.getvalue()
            self.assertIn('"validation_status": "PASS"', output)
            self.assertIn("No aggregate metric or manual UCR/MP", output)

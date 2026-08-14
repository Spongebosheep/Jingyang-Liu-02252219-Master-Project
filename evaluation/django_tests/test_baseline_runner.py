import ast
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from interviews.models import InterviewSession, Protocol, Stakeholder

from evaluation.baseline_adapter import BaselineParseError
from evaluation.baseline_runner import (
    BaselinePreflightError,
    FrozenFixtureBaselineTransport,
    PromptOnlyBaselineRunner,
    RUN_TYPE_DRY,
    RUN_TYPE_FORMAL,
)
from evaluation.run_validator import EvaluationRunValidator
from evaluation.schemas import CONTROL_RECORD_FIELDS, RUN_METADATA_FIELDS, load_json
from evaluation.validate_specs import SPEC_DIR


class InvalidJsonResponse:
    output_text = '{"coverage_assessment":"covered"}'

    def model_dump(self, mode="json"):
        return {"id": "invalid-response", "output_text": self.output_text, "mode": mode}


class InvalidJsonTransport:
    transport_name = "invalid_json_test_transport"

    def create(self, **kwargs):
        return InvalidJsonResponse()


class WrongStopTransport(FrozenFixtureBaselineTransport):
    transport_name = "wrong_stop_test_transport"

    def create(self, **kwargs):
        response = super().create(**kwargs)
        if self.call_count == 3:
            value = json.loads(response.output_text)
            value.update(
                {
                    "coverage_assessment": "covered",
                    "selected_action": "move_next",
                    "participant_response": "Thank you. Let us continue.",
                }
            )
            response.output_text = json.dumps(value)
        return response


class PromptOnlyBaselineRunnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_mvp", verbosity=0)
        cls.protocol = Protocol.objects.get(
            title="Sensory Overload Interview", version=1
        )
        cls.scenarios = {
            row["scenario_id"]: row
            for row in load_json(SPEC_DIR / "scenarios.v4.json")["scenarios"]
        }

    def run_mock_scenario(self, scenario_id, output_root):
        runner = PromptOnlyBaselineRunner(
            scenario_id=scenario_id,
            repetition=1,
            run_type=RUN_TYPE_DRY,
            output_root=output_root,
            transport=FrozenFixtureBaselineTransport(self.scenarios[scenario_id]),
        )
        return runner, runner.run()

    def test_baseline_modules_do_not_import_product_routing(self):
        prohibited = {
            "evaluation.mvp_runner",
            "interviews.agent",
            "interviews.langgraph_agent",
            "interviews.release_conditions",
        }
        evaluation_root = Path(__file__).resolve().parents[1]
        for filename in ("baseline_adapter.py", "baseline_runner.py"):
            source = (evaluation_root / filename).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            self.assertTrue(prohibited.isdisjoint(imports), (filename, imports))

    def test_stop_uses_adapter_then_public_post_stop_guard_and_rolls_back(self):
        initial_sessions = InterviewSession.objects.count()
        initial_stakeholders = Stakeholder.objects.count()
        with tempfile.TemporaryDirectory() as directory:
            runner, result = self.run_mock_scenario("S5", Path(directory))
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(result["run_status"], "completed")
            self.assertTrue(set(RUN_METADATA_FIELDS) <= set(result))
            self.assertEqual(result["condition"], "prompt_only_baseline")
            self.assertTrue(result["runtime_identity"]["mock_transport"])
            self.assertFalse(result["formal_run"])
            self.assertEqual(result["validation_status"], "not_run")

            opening, experience, stop = result["scenario_steps"][1:4]
            self.assertEqual(opening["adapter"]["execution_channel"], "prompt_only_adapter")
            self.assertEqual(experience["adapter"]["execution_channel"], "prompt_only_adapter")
            self.assertEqual(stop["adapter"]["execution_channel"], "prompt_only_adapter")
            stop_decision = stop["observed"]["agent_decisions"][0]
            self.assertTrue(set(CONTROL_RECORD_FIELDS) <= set(stop_decision))
            self.assertEqual(stop_decision["selected_action"], "stop")
            self.assertEqual(stop["observed"]["session_after"]["status"], "stopped")

            probe = result["scenario_steps"][-1]
            self.assertEqual(probe["kind"], "post_stop_probe")
            self.assertEqual(probe["http"]["request"]["client_role"], "participant")
            self.assertEqual(probe["observed"]["created_records"]["message_ids"], [])
            self.assertEqual(
                probe["observed"]["created_records"]["agent_decision_ids"], []
            )
            self.assertEqual(
                result["database_cleanup"]["mode"], "transaction_rolled_back"
            )

            for call in result["raw_model_calls"]:
                self.assertEqual(call["status"], "completed")
                for field in (
                    "raw_request_path",
                    "raw_response_path",
                    "parsed_output_path",
                ):
                    self.assertTrue((runner.run_directory / call[field]).is_file())

        self.assertEqual(InterviewSession.objects.count(), initial_sessions)
        self.assertEqual(Stakeholder.objects.count(), initial_stakeholders)

    def test_researcher_edit_uses_same_authenticated_review_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            _runner, result = self.run_mock_scenario("S7", Path(directory))
            action = next(
                row
                for row in result["harness_events"]
                if row["event"] == "researcher_action"
            )
            self.assertEqual(action["action"], "edit")
            self.assertEqual(action["http"]["request"]["client_role"], "researcher")
            edited = next(
                row
                for row in result["final_database_snapshot"]["digest_items"]
                if row["section_index"] == 1
            )
            self.assertEqual(edited["review_status"], "edited")
            self.assertEqual(
                edited["reviewed_text"],
                result["frozen_oracle"]["researcher_action"]["reviewed_text"],
            )
            overall = next(
                row
                for row in result["harness_events"]
                if row["event"] == "overall_review_action"
            )
            self.assertEqual(overall["http"]["response"]["status_code"], 302)
            self.assertEqual(
                result["final_database_snapshot"]["review_decision"]["decision"],
                "revision_requested",
            )
            self.assertEqual(
                result["final_database_snapshot"]["review_decision"][
                    "reviewer_note"
                ],
                overall["frozen_input"]["researcher_note"],
            )
            self.assertTrue(
                all(
                    item["review_status"] == "approved"
                    for item in result["final_database_snapshot"]["digest_items"]
                    if item["section_index"] != 1 and item["is_evidence_candidate"]
                )
            )

    def test_s8_includes_before_exclude_and_resolves_every_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            _runner, result = self.run_mock_scenario("S8", Path(directory))
            sequence = []
            for event in result["harness_events"]:
                if event["event"] == "researcher_action":
                    sequence.append((event["action"], event["section_index"]))
                elif event["event"] == "review_completion_item_action":
                    sequence.append(
                        (
                            event["frozen_input"]["action"],
                            event["frozen_input"]["section_index"],
                        )
                    )
            self.assertEqual(
                sequence,
                [
                    ("include", 2),
                    ("exclude", 1),
                    ("include", 3),
                    ("include", 4),
                    ("include", 5),
                ],
            )
            self.assertTrue(
                all(
                    item["review_status"] != "pending"
                    for item in result["final_database_snapshot"]["digest_items"]
                    if item["is_evidence_candidate"]
                )
            )

    def test_parse_failure_retains_raw_response_without_semantic_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PromptOnlyBaselineRunner(
                scenario_id="S1",
                repetition=1,
                run_type=RUN_TYPE_DRY,
                output_root=Path(directory),
                transport=InvalidJsonTransport(),
            )
            with self.assertRaises(BaselineParseError):
                runner.run()
            retained = json.loads(runner.record_path.read_text(encoding="utf-8"))
            self.assertEqual(retained["execution_status"], "execution_error")
            self.assertEqual(len(retained["raw_model_calls"]), 1)
            call = retained["raw_model_calls"][0]
            self.assertEqual(call["status"], "parse_error")
            self.assertTrue((runner.run_directory / call["raw_response_path"]).is_file())
            self.assertIsNone(call["parsed_output_path"])
            self.assertEqual(retained["scenario_steps"][0]["kind"], "consent")

    def test_formal_run_rejects_mock_transport_before_session_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PromptOnlyBaselineRunner(
                scenario_id="S1",
                repetition=1,
                run_type=RUN_TYPE_FORMAL,
                output_root=Path(directory),
                transport=FrozenFixtureBaselineTransport(self.scenarios["S1"]),
            )
            with self.assertRaises(BaselinePreflightError):
                runner.run()
            retained = json.loads(runner.record_path.read_text(encoding="utf-8"))
            self.assertEqual(retained["execution_status"], "preflight_error")
            self.assertFalse(retained["scenario_steps"])

    def test_formal_preflight_uses_v10_direct_requirements_without_external_gate(self):
        reviewer = get_user_model().objects.create_user(
            username="v8-baseline-reviewer", password="not-used"
        )

        def git_identity(args, _root):
            if args[0] == "status":
                return ""
            if args[:2] == ["branch", "--show-current"]:
                return "referral-evaluation"
            return "frozen-git-identity"

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "configured-for-preflight-only",
                "PURRSTONE_OPENAI_MODEL": "gpt-4.1-mini",
            },
            clear=False,
        ), patch("evaluation.baseline_runner._git", side_effect=git_identity):
            runner = PromptOnlyBaselineRunner(
                scenario_id="S1",
                repetition=1,
                run_type=RUN_TYPE_FORMAL,
                output_root=Path(directory),
                reviewer_username=reviewer.username,
            )
            runner._preflight()

        self.assertEqual(runner.record["comparison_freeze"]["contract_version"], "10.2")
        self.assertEqual(
            runner.record["formal_reviewer"]["username"], reviewer.username
        )
        self.assertNotIn("live_gate", runner.record)

    def test_internally_inconsistent_stop_output_is_retained_as_parse_error(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PromptOnlyBaselineRunner(
                scenario_id="S5",
                repetition=1,
                run_type=RUN_TYPE_DRY,
                output_root=Path(directory),
                transport=WrongStopTransport(self.scenarios["S5"]),
            )
            with self.assertRaisesMessage(
                RuntimeError, "Stop control must remain not_assessed"
            ):
                runner.run()
            retained = json.loads(runner.record_path.read_text(encoding="utf-8"))
            self.assertEqual(retained["execution_status"], "execution_error")
            observed_ids = [row["step_id"] for row in retained["scenario_steps"]]
            self.assertNotIn("S5-K01", observed_ids)
            self.assertNotIn("S5-P01", observed_ids)
            failed_call = retained["raw_model_calls"][-1]
            self.assertEqual(failed_call["step_id"], "S5-K01")
            self.assertEqual(failed_call["status"], "parse_error")
            self.assertTrue(failed_call["raw_response_path"])
            self.assertIsNone(failed_call["parsed_output_path"])

    def test_command_completes_s1_to_s8_only_as_mock_dry_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout = StringIO()
            call_command(
                "run_prompt_baseline",
                all=True,
                repetition=1,
                run_type=RUN_TYPE_DRY,
                mock_responses=True,
                output_root=Path(directory),
                stdout=stdout,
                verbosity=0,
            )
            records = sorted(Path(directory).glob("*/runner_record.json"))
            self.assertEqual(len(records), 8)
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in records]
            self.assertEqual(
                {row["scenario_id"] for row in documents},
                {f"S{index}" for index in range(1, 9)},
            )
            self.assertTrue(all(row["execution_status"] == "completed" for row in documents))
            self.assertTrue(all(row["validation_status"] == "not_run" for row in documents))
            self.assertTrue(all(row["runtime_identity"]["mock_transport"] for row in documents))
            self.assertIn('"formal_evidence_eligible": false', stdout.getvalue())

    def test_independent_validator_accepts_mock_structure_but_not_as_formal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, _result = self.run_mock_scenario("S6", Path(directory))
            validation, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()
            self.assertEqual(validation["validation_status"], "PASS")
            self.assertFalse(validation["formal_evidence_eligible"])
            self.assertEqual(validation["summary"]["fail_count"], 0)

    def test_validator_rejects_tampered_raw_baseline_response(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, result = self.run_mock_scenario("S1", Path(directory))
            response_path = (
                runner.run_directory
                / result["raw_model_calls"][0]["raw_response_path"]
            )
            response_path.write_text('{"tampered":true}', encoding="utf-8")
            validation, _path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()
            self.assertEqual(validation["validation_status"], "FAIL")
            failed_ids = {
                row["check_id"]
                for row in validation["checks"]
                if row["status"] == "FAIL"
            }
            self.assertIn("BASELINE-CALL-001-RAW-PARSED-INTEGRITY", failed_ids)

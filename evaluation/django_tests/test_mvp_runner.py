import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from interviews.models import InterviewSession, Protocol, Stakeholder

from evaluation.mvp_runner import (
    MvpScenarioRunner,
    RUN_TYPE_DRY,
    RUN_TYPE_FORMAL,
    RunnerPreflightError,
    capture_openai_responses,
)
from evaluation.schemas import CONTROL_RECORD_FIELDS, RUN_METADATA_FIELDS, load_json
from evaluation.validate_specs import SPEC_DIR


class MvpScenarioRunnerTests(TestCase):
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
            if status == "partially_covered":
                covered = ["test-covered field"]
                missing = ["test-missing field"]
            else:
                covered = ["test transport response"]
                missing = []
            return {
                "coverage_assessment": status,
                "covered_information": covered,
                "missing_information": missing,
                "evidence_quote": "test transport response",
                "decision_reason": "Evaluation runner infrastructure test stub.",
            }

        return assessment

    def run_dry_scenario(self, scenario_id, output_root):

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "",
                "DISABLE_LLM_FOLLOWUP_WORDING": "",
            },
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
            result = runner.run()
        return runner, result

    def test_stop_scenario_uses_public_http_path_and_rolls_back(self):
        initial_session_count = InterviewSession.objects.count()
        initial_stakeholder_count = Stakeholder.objects.count()
        with tempfile.TemporaryDirectory() as directory:
            runner, result = self.run_dry_scenario("S5", Path(directory))

            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(result["run_status"], "completed")
            self.assertTrue(set(RUN_METADATA_FIELDS) <= set(result))
            self.assertEqual(result["validation_status"], "not_run")
            self.assertFalse(result["formal_run"])
            self.assertEqual(
                [step["step_id"] for step in result["scenario_steps"]],
                ["S5-C01", "S5-O01", "S5-T01", "S5-K01", "S5-P01"],
            )
            stop_step = result["scenario_steps"][-2]
            post_stop_probe = result["scenario_steps"][-1]
            stop_decision = stop_step["observed"]["agent_decisions"][0]
            self.assertTrue(set(CONTROL_RECORD_FIELDS) <= set(stop_decision))
            self.assertEqual(
                stop_decision["action"],
                "stop",
            )
            self.assertEqual(
                stop_step["observed"]["session_after"]["status"],
                "stopped",
            )
            self.assertEqual(post_stop_probe["kind"], "post_stop_probe")
            self.assertEqual(
                post_stop_probe["observed"]["created_records"]["message_ids"],
                [],
            )
            self.assertEqual(
                post_stop_probe["observed"]["created_records"][
                    "agent_decision_ids"
                ],
                [],
            )
            self.assertEqual(
                post_stop_probe["observed"]["session_after"]["status"],
                "stopped",
            )
            self.assertTrue(
                all(
                    step["http"]["request"]["client_role"] == "participant"
                    for step in result["scenario_steps"]
                )
            )
            self.assertEqual(
                result["database_cleanup"]["mode"],
                "transaction_rolled_back",
            )
            self.assertTrue(runner.record_path.is_file())
            for step in result["scenario_steps"]:
                raw_path = (
                    runner.run_directory
                    / step["http"]["response"]["raw_body_path"]
                )
                self.assertTrue(raw_path.is_file())

        self.assertEqual(InterviewSession.objects.count(), initial_session_count)
        self.assertEqual(Stakeholder.objects.count(), initial_stakeholder_count)

    def test_researcher_edit_runs_through_authenticated_review_view(self):
        with tempfile.TemporaryDirectory() as directory:
            _runner, result = self.run_dry_scenario("S7", Path(directory))

            action = next(
                event
                for event in result["harness_events"]
                if event["event"] == "researcher_action"
            )
            self.assertEqual(action["action"], "edit")
            self.assertEqual(action["http"]["request"]["client_role"], "researcher")
            self.assertEqual(action["http"]["response"]["status_code"], 302)
            self.assertEqual(
                len(action["observed"]["created_records"]["review_event_ids"]),
                1,
            )
            edited_item = next(
                item
                for item in result["final_database_snapshot"]["digest_items"]
                if item["section_index"] == 1
            )
            self.assertEqual(edited_item["review_status"], "edited")
            self.assertEqual(
                edited_item["reviewed_text"],
                result["frozen_oracle"]["researcher_action"]["reviewed_text"],
            )
            overall = next(
                event
                for event in result["harness_events"]
                if event["event"] == "overall_review_action"
            )
            self.assertEqual(overall["http"]["response"]["status_code"], 302)
            self.assertEqual(
                result["final_database_snapshot"]["review_decision"]["decision"],
                "revision_requested",
            )
            self.assertTrue(
                result["final_database_snapshot"]["review_decision"][
                    "reviewer_name_snapshot"
                ]
            )
            self.assertTrue(
                result["final_database_snapshot"]["review_decision"]["reviewed_at"]
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
            _runner, result = self.run_dry_scenario("S8", Path(directory))
            action_sequence = []
            for event in result["harness_events"]:
                if event["event"] == "researcher_action":
                    action_sequence.append((event["action"], event["section_index"]))
                elif event["event"] == "review_completion_item_action":
                    action_sequence.append(
                        (
                            event["frozen_input"]["action"],
                            event["frozen_input"]["section_index"],
                        )
                    )
            self.assertEqual(
                action_sequence,
                [
                    ("include", 2),
                    ("exclude", 1),
                    ("include", 3),
                    ("include", 4),
                    ("include", 5),
                ],
            )
            statuses = {
                item["section_index"]: item["review_status"]
                for item in result["final_database_snapshot"]["digest_items"]
            }
            self.assertEqual(
                statuses,
                {1: "excluded", 2: "approved", 3: "approved", 4: "approved", 5: "approved"},
            )

    def test_formal_run_is_blocked_without_api_key_and_failure_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
                runner = MvpScenarioRunner(
                    scenario_id="S1",
                    repetition=1,
                    run_type=RUN_TYPE_FORMAL,
                    output_root=Path(directory),
                    reviewer_username="missing-reviewer",
                )
                with self.assertRaisesMessage(
                    RunnerPreflightError, "OPENAI_API_KEY"
                ):
                    runner.run()

            retained = json.loads(runner.record_path.read_text(encoding="utf-8"))
            self.assertEqual(retained["execution_status"], "preflight_error")
            self.assertEqual(retained["error"]["type"], "RunnerPreflightError")
            self.assertFalse(retained["scenario_steps"])

    def test_formal_preflight_uses_v10_direct_requirements_without_external_gate(self):
        reviewer = get_user_model().objects.create_user(
            username="v8-mvp-reviewer", password="not-used"
        )

        def git_identity(args, _root):
            if args[0] == "status":
                return ""
            if args[:2] == ["branch", "--show-current"]:
                return "referral-evaluation"
            return "frozen-git-identity"

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "configured-for-preflight-only",
                "PURRSTONE_OPENAI_MODEL": "gpt-4.1-mini",
                "DISABLE_LLM_FOLLOWUP_WORDING": "",
            },
            clear=False,
        ), patch("evaluation.mvp_runner._git", side_effect=git_identity):
            runner = MvpScenarioRunner(
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

    def test_command_retains_non_adjudicated_dry_run_record(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": ""},
                clear=False,
            ), patch(
                "interviews.langgraph_agent.llm_assess_protocol_coverage",
                side_effect=self.assessment_transport_stub(),
            ):
                call_command(
                    "run_mvp_scenarios",
                    scenario=["S5"],
                    repetition=2,
                    run_type=RUN_TYPE_DRY,
                    output_root=Path(directory),
                    verbosity=0,
                    stdout=StringIO(),
                )

            records = list(Path(directory).glob("*/runner_record.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["repetition"], 2)
            self.assertEqual(record["validation_status"], "not_run")
            self.assertEqual(record["execution_status"], "completed")

    def test_all_s1_to_s8_complete_one_infrastructure_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": ""},
                clear=False,
            ), patch(
                "interviews.langgraph_agent.llm_assess_protocol_coverage",
                side_effect=self.assessment_transport_stub(),
            ):
                call_command(
                    "run_mvp_scenarios",
                    all=True,
                    repetition=1,
                    run_type=RUN_TYPE_DRY,
                    output_root=Path(directory),
                    verbosity=0,
                    stdout=StringIO(),
                )

            records = sorted(Path(directory).glob("*/runner_record.json"))
            self.assertEqual(len(records), 8)
            documents = [
                json.loads(path.read_text(encoding="utf-8")) for path in records
            ]
            self.assertEqual(
                {document["scenario_id"] for document in documents},
                {f"S{index}" for index in range(1, 9)},
            )
            self.assertTrue(
                all(document["execution_status"] == "completed" for document in documents)
            )
            self.assertTrue(
                all(document["validation_status"] == "not_run" for document in documents)
            )
            self.assertTrue(
                all(
                    document["database_cleanup"]["mode"]
                    == "transaction_rolled_back"
                    for document in documents
                )
            )

    def test_model_capture_is_pass_through_and_never_records_api_key(self):
        class FakeResponse:
            output_text = '{"coverage_assessment":"covered"}'

            def model_dump(self, mode="json"):
                return {
                    "id": "response-test",
                    "output_text": self.output_text,
                    "mode": mode,
                }

        class FakeResponses:
            def create(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                return FakeResponse()

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.responses = FakeResponses()

        calls = []
        flush_count = 0

        def on_change():
            nonlocal flush_count
            flush_count += 1

        with patch(
            "interviews.langgraph_agent.OpenAI",
            side_effect=FakeClient,
        ):
            with capture_openai_responses(calls, on_change):
                from interviews import langgraph_agent

                client = langgraph_agent.OpenAI(api_key="must-not-be-recorded")
                response = client.responses.create(
                    model="test-model",
                    input="frozen prompt",
                    temperature=0,
                    max_output_tokens=50,
                    store=False,
                )

        self.assertEqual(response.output_text, '{"coverage_assessment":"covered"}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "completed")
        self.assertEqual(calls[0]["request"]["kwargs"]["input"], "frozen prompt")
        self.assertEqual(
            calls[0]["response"]["raw"]["id"],
            "response-test",
        )
        self.assertNotIn("must-not-be-recorded", json.dumps(calls))
        self.assertGreaterEqual(flush_count, 2)

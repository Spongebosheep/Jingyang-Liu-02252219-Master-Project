import csv
import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from evaluation.baseline_runner import (
    FrozenFixtureBaselineTransport,
    PromptOnlyBaselineRunner,
    RUN_TYPE_DRY as BASELINE_DRY,
)
from evaluation.exporter import EvaluationBundleExporter
from evaluation.metric_calculator import (
    EvaluationMetricCalculator,
    MetricCalculationError,
)
from evaluation.mvp_runner import MvpScenarioRunner, RUN_TYPE_DRY as MVP_DRY
from evaluation.run_validator import EvaluationRunValidator
from evaluation.schemas import load_json, sha256_file
from evaluation.validate_specs import SPEC_DIR
from interviews.models import InterviewSession, Protocol, Stakeholder


class EvaluationMetricCalculatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_mvp", verbosity=0)
        cls.scenario_document = load_json(SPEC_DIR / "scenarios.v4.json")
        cls.scenarios = {
            row["scenario_id"]: row for row in cls.scenario_document["scenarios"]
        }
        cls.protocol = Protocol.objects.get(
            title="Sensory Overload Interview",
            version=1,
        )

    @classmethod
    def assessment_transport_stub(cls):
        statuses_by_reply = {}
        for scenario in cls.scenarios.values():
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
                "covered_information": ["metric calculator test field"],
                "missing_information": (
                    ["metric calculator test missing field"]
                    if status == "partially_covered"
                    else []
                ),
                "evidence_quote": "metric calculator test evidence",
                "decision_reason": "Metric calculator test stub.",
            }

        return assessment

    def run_mvp(self, scenario_id: str, output_root: Path):
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
                run_type=MVP_DRY,
                output_root=output_root,
            )
            runner.run()
        return runner

    def run_baseline(self, scenario_id: str, output_root: Path):
        runner = PromptOnlyBaselineRunner(
            scenario_id=scenario_id,
            repetition=1,
            run_type=BASELINE_DRY,
            output_root=output_root,
            transport=FrozenFixtureBaselineTransport(self.scenarios[scenario_id]),
        )
        runner.run()
        return runner

    @staticmethod
    def read_csv(path: Path):
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def export_runs(self, root: Path, scenario_ids=("S5", "S7")):
        runners = []
        for scenario_id in scenario_ids:
            runners.append(self.run_mvp(scenario_id, root / "runs" / "mvp"))
            runners.append(
                self.run_baseline(scenario_id, root / "runs" / "baseline")
            )
        for runner in runners:
            EvaluationRunValidator(record_path=runner.record_path).run()
        manifest, export_directory = EvaluationBundleExporter(
            record_paths=[runner.record_path for runner in runners],
            output_root=root / "exports",
        ).run()
        return runners, manifest, export_directory

    def test_calculates_seven_automatic_metrics_and_keeps_ucr_mp_pending(self):
        initial_sessions = InterviewSession.objects.count()
        initial_stakeholders = Stakeholder.objects.count()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runners, export_manifest, export_directory = self.export_runs(root)
            input_hashes = {
                path.relative_to(export_directory): sha256_file(path)
                for path in export_directory.rglob("*")
                if path.is_file()
            }

            manifest, calculation_directory = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=root / "metrics",
            ).run()

            self.assertEqual(manifest["calculation_status"], "completed")
            self.assertEqual(manifest["input_export"]["export_id"], export_manifest["export_id"])
            self.assertEqual(manifest["input_export"]["run_count"], 4)
            self.assertEqual(manifest["formal_evidence_eligible_run_count"], 0)
            self.assertEqual(
                manifest["automatic_metric_codes"],
                ["AI", "AC", "SLC", "MIV", "PCC", "RAC", "TCR"],
            )
            self.assertEqual(manifest["manual_metric_codes"], ["UCR", "MP"])
            self.assertFalse(
                manifest["claim_boundaries"]["comparative_superiority_claim_generated"]
            )
            self.assertFalse(manifest["claim_boundaries"]["ucr_human_coding_performed"])
            self.assertFalse(manifest["claim_boundaries"]["mp_human_coding_performed"])

            run_summary = self.read_csv(
                calculation_directory / "metrics" / "run_summary.csv"
            )
            condition_summary = self.read_csv(
                calculation_directory / "metrics" / "condition_summary.csv"
            )
            pair_summary = self.read_csv(
                calculation_directory / "metrics" / "pair_summary.csv"
            )
            detail = self.read_csv(calculation_directory / "metrics" / "detail.csv")
            self.assertEqual(len(run_summary), 4 * 9)
            self.assertEqual(len(condition_summary), 2 * 9)
            self.assertEqual(len(pair_summary), 2 * 9)
            self.assertTrue(all(row["pairing_status"] == "PAIRED" for row in pair_summary))
            self.assertNotIn("winner", pair_summary[0])
            self.assertNotIn("delta", pair_summary[0])

            manual_rows = [
                row for row in run_summary if row["metric_code"] in {"UCR", "MP"}
            ]
            self.assertTrue(manual_rows)
            self.assertTrue(
                all(row["calculation_status"] == "PENDING_MANUAL" for row in manual_rows)
            )
            self.assertTrue(
                all(row["manual_coding_required"] == "true" for row in manual_rows)
            )
            ucr_details = [row for row in detail if row["metric_code"] == "UCR"]
            mp_details = [row for row in detail if row["metric_code"] == "MP"]
            self.assertTrue(
                all(row["evaluation_status"] == "PENDING_MANUAL" for row in ucr_details)
            )
            self.assertTrue(
                all(row["evaluation_status"] == "PENDING_MANUAL" for row in mp_details)
            )

            s5_pcc = [
                row
                for row in run_summary
                if row["scenario_id"] == "S5" and row["metric_code"] == "PCC"
            ]
            self.assertEqual(len(s5_pcc), 2)
            self.assertTrue(all(row["calculation_status"] == "PASS" for row in s5_pcc))
            self.assertTrue(all(row["numerator"] == "1" for row in s5_pcc))
            self.assertTrue(all(row["denominator"] == "1" for row in s5_pcc))
            s7_slc = [
                row
                for row in run_summary
                if row["scenario_id"] == "S7" and row["metric_code"] == "SLC"
            ]
            self.assertTrue(all(row["calculation_status"] == "PASS" for row in s7_slc))
            s7_rac = [
                row
                for row in run_summary
                if row["scenario_id"] == "S7" and row["metric_code"] == "RAC"
            ]
            self.assertTrue(all(row["calculation_status"] == "PASS" for row in s7_rac))
            self.assertTrue(all(row["numerator"] == "6" for row in s7_rac))
            self.assertTrue(all(row["denominator"] == "6" for row in s7_rac))
            s7_rac_details = [
                row
                for row in detail
                if row["scenario_id"] == "S7" and row["metric_code"] == "RAC"
            ]
            self.assertEqual(
                {row["evaluation_status"] for row in s7_rac_details},
                {"PASS"},
            )
            completed_overall = [
                row
                for row in s7_rac_details
                if row["unit_type"] == "overall_decision"
            ]
            self.assertEqual(len(completed_overall), 2)
            self.assertTrue(
                all(
                    row["failure_reasons_json"] == "[]"
                    for row in completed_overall
                )
            )

            after_hashes = {
                path.relative_to(export_directory): sha256_file(path)
                for path in export_directory.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after_hashes, input_hashes)
            self.assertEqual(len(runners), 4)

        self.assertEqual(InterviewSession.objects.count(), initial_sessions)
        self.assertEqual(Stakeholder.objects.count(), initial_stakeholders)

    def test_wrong_observed_action_is_reported_not_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.run_baseline("S1", root / "runs")
            record = json.loads(runner.record_path.read_text(encoding="utf-8"))
            target = next(
                step for step in record["scenario_steps"] if step["step_id"] == "S1-T01"
            )
            target["observed"]["agent_decisions"][0]["selected_action"] = "ask_follow_up"
            target["observed"]["agent_decisions"][0]["action"] = "ask_follow_up"
            runner.record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
            )
            EvaluationRunValidator(record_path=runner.record_path).run()
            _export_manifest, export_directory = EvaluationBundleExporter(
                record_paths=[runner.record_path],
                output_root=root / "exports",
            ).run()

            _manifest, calculation_directory = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=root / "metrics",
            ).run()
            run_summary = self.read_csv(
                calculation_directory / "metrics" / "run_summary.csv"
            )
            ac = next(row for row in run_summary if row["metric_code"] == "AC")
            self.assertEqual(ac["calculation_status"], "FAIL")
            self.assertEqual(ac["numerator"], "4")
            self.assertEqual(ac["denominator"], "5")
            detail = self.read_csv(calculation_directory / "metrics" / "detail.csv")
            failed = next(
                row
                for row in detail
                if row["metric_code"] == "AC" and row["unit_id"] == "S1-T01"
            )
            self.assertEqual(failed["evaluation_status"], "FAIL")
            self.assertIn("observed_action_mismatch", failed["failure_reasons_json"])

    def test_tampered_export_is_rejected_and_failed_attempt_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _runners, _manifest, export_directory = self.export_runs(
                root, scenario_ids=("S5",)
            )
            turns_path = export_directory / "tables" / "turns.csv"
            turns_path.write_text(
                turns_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
            )
            output_root = root / "metrics"
            calculator = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=output_root,
            )
            with self.assertRaises(MetricCalculationError):
                calculator.run()

            retained = list(output_root.glob("metric-calculation-*"))
            self.assertEqual(len(retained), 1)
            failed = json.loads(
                (retained[0] / "calculation_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed["calculation_status"], "failed")
            self.assertIn("inventory mismatch", failed["error"]["message"].lower())

    def test_calculation_is_append_only_and_command_keeps_human_work_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _runners, _manifest, export_directory = self.export_runs(
                root, scenario_ids=("S7",)
            )
            output_root = root / "metrics"
            first_manifest, first_directory = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=output_root,
            ).run()
            second_manifest, second_directory = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=output_root,
            ).run()
            self.assertNotEqual(first_manifest["calculation_id"], second_manifest["calculation_id"])
            self.assertNotEqual(first_directory, second_directory)

            stdout = StringIO()
            call_command(
                "calculate_evaluation_metrics",
                export_directory=export_directory,
                output_root=output_root,
                stdout=stdout,
                verbosity=0,
            )
            output = stdout.getvalue()
            self.assertIn('"human_coding_performed": false', output)
            self.assertIn('"comparative_superiority_claim_generated": false', output)
            self.assertEqual(len(list(output_root.glob("metric-calculation-*"))), 3)

    def test_all_s1_to_s8_pair_without_becoming_formal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario_ids = tuple(f"S{index}" for index in range(1, 9))
            _runners, _export_manifest, export_directory = self.export_runs(
                root, scenario_ids=scenario_ids
            )
            manifest, calculation_directory = EvaluationMetricCalculator(
                export_directory=export_directory,
                output_root=root / "metrics",
            ).run()

            self.assertEqual(manifest["input_export"]["run_count"], 16)
            self.assertEqual(manifest["formal_evidence_eligible_run_count"], 0)
            run_summary = self.read_csv(
                calculation_directory / "metrics" / "run_summary.csv"
            )
            condition_summary = self.read_csv(
                calculation_directory / "metrics" / "condition_summary.csv"
            )
            pair_summary = self.read_csv(
                calculation_directory / "metrics" / "pair_summary.csv"
            )
            self.assertEqual(len(run_summary), 16 * 9)
            self.assertEqual(len(condition_summary), 2 * 9)
            self.assertEqual(len(pair_summary), 8 * 9)
            self.assertTrue(all(row["pairing_status"] == "PAIRED" for row in pair_summary))
            limitation_rows = [
                row
                for row in run_summary
                if row["metric_code"] == "MIV" and row["scenario_id"] in {"S3", "S4", "S5"}
            ]
            self.assertEqual(len(limitation_rows), 6)
            self.assertTrue(
                all(row["calculation_status"] == "PASS" for row in limitation_rows)
            )
            manual_rows = [
                row for row in run_summary if row["metric_code"] in {"UCR", "MP"}
            ]
            self.assertTrue(
                all(row["calculation_status"] == "PENDING_MANUAL" for row in manual_rows)
            )

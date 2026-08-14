import csv
import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from evaluation.baseline_adapter import BaselineParseError
from evaluation.baseline_runner import (
    FrozenFixtureBaselineTransport,
    PromptOnlyBaselineRunner,
    RUN_TYPE_DRY as BASELINE_DRY,
)
from evaluation.exporter import EvaluationBundleExporter
from evaluation.mvp_runner import MvpScenarioRunner, RUN_TYPE_DRY as MVP_DRY
from evaluation.run_validator import EvaluationRunValidator
from evaluation.schemas import load_json, sha256_file
from evaluation.validate_specs import SPEC_DIR
from interviews.models import InterviewSession, Protocol, Stakeholder


class InvalidJsonTransport:
    transport_name = "invalid_json_test_transport"

    def create(self, **_kwargs):
        return SimpleNamespace(output_text="not-json")


class EvaluationBundleExporterTests(TestCase):
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
                "covered_information": ["exporter infrastructure test field"],
                "missing_information": (
                    ["exporter infrastructure test missing field"]
                    if status == "partially_covered"
                    else []
                ),
                "evidence_quote": "exporter infrastructure test evidence",
                "decision_reason": "Exporter infrastructure test stub.",
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

    def test_unified_export_preserves_paired_raw_records_and_writes_frozen_tables(self):
        initial_sessions = InterviewSession.objects.count()
        initial_stakeholders = Stakeholder.objects.count()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "runs"
            mvp = self.run_mvp("S7", run_root / "mvp")
            baseline = self.run_baseline("S7", run_root / "baseline")
            EvaluationRunValidator(record_path=mvp.record_path).run()
            EvaluationRunValidator(record_path=baseline.record_path).run()
            source_hashes = {
                mvp.run_id: sha256_file(mvp.record_path),
                baseline.run_id: sha256_file(baseline.record_path),
            }

            manifest, export_directory = EvaluationBundleExporter(
                record_paths=[mvp.record_path, baseline.record_path],
                output_root=root / "exports",
            ).run()

            self.assertEqual(manifest["run_count"], 2)
            self.assertEqual(manifest["condition_counts"], {"mvp": 1, "prompt_only_baseline": 1})
            self.assertEqual(manifest["formal_evidence_eligible_run_count"], 0)
            self.assertFalse(manifest["claim_boundaries"]["metric_calculation_performed"])
            self.assertFalse(manifest["claim_boundaries"]["comparison_claim_generated"])
            self.assertFalse(manifest["claim_boundaries"]["human_coding_performed"])

            runs = self.read_csv(export_directory / "tables" / "runs.csv")
            self.assertEqual(len(runs), 2)
            self.assertEqual({row["condition"] for row in runs}, {"mvp", "prompt_only_baseline"})
            self.assertEqual({row["pair_key"] for row in runs}, {"S7-R01"})
            self.assertTrue(all(row["validation_status"] == "PASS" for row in runs))
            self.assertTrue(all(row["formal_evidence_eligible"] == "false" for row in runs))
            self.assertTrue(all(row["evidence_record_status"] == "copied" for row in runs))

            turns = self.read_csv(export_directory / "tables" / "turns.csv")
            inputs_by_condition = {
                condition: [
                    (row["step_id"], row["participant_text"], row["control"])
                    for row in turns
                    if row["condition"] == condition
                ]
                for condition in ("mvp", "prompt_only_baseline")
            }
            self.assertEqual(
                inputs_by_condition["mvp"],
                inputs_by_condition["prompt_only_baseline"],
            )
            baseline_turns = [
                row for row in turns if row["condition"] == "prompt_only_baseline"
            ]
            self.assertTrue(any(row["raw_request_path"] for row in baseline_turns))
            self.assertTrue(any(row["parsed_output_path"] for row in baseline_turns))

            item_sources = self.read_csv(
                export_directory / "tables" / "item_sources.csv"
            )
            applicable = [
                row for row in item_sources if row["source_relation_applicable"] == "true"
            ]
            self.assertTrue(applicable)
            self.assertTrue(all(row["relation_valid"] == "true" for row in applicable))
            self.assertTrue(all(row["source_text"] for row in applicable))

            self.assertEqual(
                len(list((export_directory / "evidence_records").iterdir())), 2
            )
            for runner in (mvp, baseline):
                copied = (
                    export_directory
                    / "raw"
                    / "runs"
                    / runner.run_id
                    / "runner_record.json"
                )
                self.assertEqual(sha256_file(copied), source_hashes[runner.run_id])
                self.assertEqual(sha256_file(runner.record_path), source_hashes[runner.run_id])
            copied_baseline_root = (
                export_directory / "raw" / "runs" / baseline.run_id
            )
            self.assertTrue((copied_baseline_root / "raw" / "model_calls").is_dir())
            self.assertTrue((copied_baseline_root / "parsed" / "model_calls").is_dir())

        self.assertEqual(InterviewSession.objects.count(), initial_sessions)
        self.assertEqual(Stakeholder.objects.count(), initial_stakeholders)

    def test_pair_integrity_exposes_a_model_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mvp = self.run_mvp("S1", root / "runs" / "mvp")
            baseline = self.run_baseline("S1", root / "runs" / "baseline")
            changed = json.loads(baseline.record_path.read_text(encoding="utf-8"))
            changed["runtime_identity"]["model"] = "different-model"
            baseline.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True), encoding="utf-8"
            )

            manifest, _export_directory = EvaluationBundleExporter(
                record_paths=[mvp.record_path, baseline.record_path],
                output_root=root / "exports",
            ).run()

            self.assertEqual(manifest["pair_integrity"]["fairness_fail_count"], 1)
            pair = manifest["pair_integrity"]["pairs"][0]
            self.assertEqual(pair["status"], "FAIRNESS_FAIL")
            self.assertEqual(pair["mismatched_fields"], ["model_id"])
            self.assertFalse(pair["formal_pair_evidence_eligible"])

    def test_failed_run_and_raw_parse_failure_are_exported_without_fabricated_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = PromptOnlyBaselineRunner(
                scenario_id="S1",
                repetition=1,
                run_type=BASELINE_DRY,
                output_root=root / "runs",
                transport=InvalidJsonTransport(),
            )
            with self.assertRaises(BaselineParseError):
                runner.run()

            manifest, export_directory = EvaluationBundleExporter(
                record_paths=[runner.record_path],
                output_root=root / "exports",
            ).run()

            self.assertEqual(manifest["execution_error_run_count"], 1)
            runs = self.read_csv(export_directory / "tables" / "runs.csv")
            self.assertEqual(runs[0]["execution_status"], "execution_error")
            self.assertEqual(runs[0]["validation_status"], "not_run")
            self.assertEqual(runs[0]["formal_evidence_eligible"], "false")
            self.assertEqual(runs[0]["evidence_record_status"], "not_recorded")
            self.assertEqual(runs[0]["error_type"], "BaselineParseError")
            raw_responses = list(
                (
                    export_directory
                    / "raw"
                    / "runs"
                    / runner.run_id
                    / "raw"
                    / "model_calls"
                ).glob("*-response.json")
            )
            self.assertEqual(len(raw_responses), 1)
            self.assertEqual(
                list(
                    (
                        export_directory
                        / "raw"
                        / "runs"
                        / runner.run_id
                        / "parsed"
                        / "model_calls"
                    ).glob("*.json")
                ),
                [],
            )

    def test_stale_validator_is_retained_but_cannot_confer_status_or_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.run_mvp("S1", root / "runs")
            _result, validator_path = EvaluationRunValidator(
                record_path=runner.record_path
            ).run()
            changed = json.loads(runner.record_path.read_text(encoding="utf-8"))
            changed["notes"].append("post-validation change for stale-result test")
            runner.record_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True), encoding="utf-8"
            )

            _manifest, export_directory = EvaluationBundleExporter(
                record_paths=[runner.record_path],
                validator_result_paths=[validator_path],
                output_root=root / "exports",
            ).run()

            runs = self.read_csv(export_directory / "tables" / "runs.csv")
            self.assertEqual(runs[0]["validator_matches_runner"], "false")
            self.assertEqual(runs[0]["validation_status"], "STALE")
            self.assertEqual(runs[0]["formal_evidence_eligible"], "false")
            copied_validators = list(
                (
                    export_directory
                    / "raw"
                    / "runs"
                    / runner.run_id
                    / "validation"
                ).rglob("validator_result.json")
            )
            self.assertEqual(len(copied_validators), 1)

    def test_append_only_export_creates_a_new_directory_each_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.run_mvp("S1", root / "runs")
            before_hash = sha256_file(runner.record_path)
            first_manifest, first_directory = EvaluationBundleExporter(
                record_paths=[runner.record_path],
                output_root=root / "exports",
            ).run()
            second_manifest, second_directory = EvaluationBundleExporter(
                record_paths=[runner.record_path],
                output_root=root / "exports",
            ).run()

            self.assertNotEqual(first_manifest["export_id"], second_manifest["export_id"])
            self.assertNotEqual(first_directory, second_directory)
            self.assertTrue((first_directory / "manifest.json").is_file())
            self.assertTrue((second_directory / "manifest.json").is_file())
            self.assertEqual(sha256_file(runner.record_path), before_hash)

    def test_management_command_exports_without_starting_metrics_or_human_coding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.run_mvp("S1", root / "runs")
            stdout = StringIO()

            call_command(
                "export_evaluation_bundle",
                record=[runner.record_path],
                output_root=root / "exports",
                stdout=stdout,
                verbosity=0,
            )

            output = stdout.getvalue()
            self.assertIn('"metric_calculation_performed": false', output)
            self.assertIn('"comparison_claim_generated": false', output)
            self.assertIn('"human_coding_performed": false', output)
            self.assertEqual(
                len(list((root / "exports").glob("evaluation-export-*"))), 1
            )

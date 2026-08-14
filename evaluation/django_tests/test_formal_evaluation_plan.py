import copy
import json
import tempfile
from io import StringIO
from unittest.mock import Mock, patch
from pathlib import Path

from django.test import SimpleTestCase
from django.core.management import call_command

from evaluation.formal_evaluation_plan import (
    FormalEvaluationPlan,
    FormalEvaluationPlanError,
)
from evaluation.execution_plan import load_attempt_plan, load_pair_plan


class _FakeFormalRunner:
    condition = ""
    fail_identity = None

    def __init__(
        self,
        *,
        scenario_id,
        repetition,
        run_type,
        output_root,
        reviewer_username,
        repository_root=None,
    ):
        pair_key = f"{scenario_id}-R{repetition:02d}"
        self.identity = (pair_key, self.condition)
        self.run_id = f"fake-{self.condition}-{pair_key.lower()}"
        self.record_path = Path(output_root) / self.run_id / "runner_record.json"
        self.record_path.parent.mkdir(parents=True, exist_ok=False)
        self.record = {
            "run_id": self.run_id,
            "condition": self.condition,
            "scenario_id": scenario_id,
            "repetition": repetition,
            "run_type": run_type,
            "formal_run": True,
            "execution_status": "created",
            "formal_reviewer": {"username": reviewer_username, "is_active": True},
        }
        self._write()

    def _write(self):
        self.record_path.write_text(json.dumps(self.record), encoding="utf-8")

    def run(self):
        if self.identity == self.fail_identity:
            self.record["execution_status"] = "execution_error"
            self._write()
            raise RuntimeError("retained formal attempt failure")
        self.record["execution_status"] = "completed"
        self._write()
        return self.record


class FakeFormalMvpRunner(_FakeFormalRunner):
    condition = "mvp"


class FakeFormalBaselineRunner(_FakeFormalRunner):
    condition = "prompt_only_baseline"


class FakeFormalValidator:
    def __init__(self, *, record_path, output_root):
        self.record_path = Path(record_path)
        self.output_root = Path(output_root)

    def run(self):
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        passed = record["execution_status"] == "completed"
        result = {
            "run_id": record["run_id"],
            "validation_status": "PASS" if passed else "INCOMPLETE",
            "formal_evidence_eligible": passed,
        }
        path = self.output_root / "validator-result.json"
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text(json.dumps(result), encoding="utf-8")
        return result, path


class FormalEvaluationPlanTests(SimpleTestCase):
    def setUp(self):
        FakeFormalMvpRunner.fail_identity = None
        FakeFormalBaselineRunner.fail_identity = None

    def run_plan(self, root):
        return FormalEvaluationPlan(
            output_root=root,
            reviewer_username="researcher",
            mvp_runner_factory=FakeFormalMvpRunner,
            baseline_runner_factory=FakeFormalBaselineRunner,
            validator_factory=FakeFormalValidator,
        ).run()

    def test_exact_plan_runs_pilot_then_all_thirty_two_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "evaluation.formal_evaluation_plan.require_finalized_baseline_commit",
                return_value="a" * 40,
            ):
                result, result_path = self.run_plan(Path(directory))
            self.assertEqual(result["pilot_status"], "PASS")
            self.assertEqual(result["plan_status"], "COMPLETED_ALL_PASS")
            self.assertEqual(result["attempt_count"], 32)
            self.assertEqual(result["unique_attempt_identity_count"], 32)
            self.assertTrue(result["schedule_complete"])
            self.assertEqual(result["replacement_attempt_count"], 0)
            self.assertEqual(result["formal_evidence_eligible_count"], 32)
            identities = [
                (row["pair_key"], row["condition"]) for row in result["attempts"]
            ]
            self.assertEqual(len(identities), len(set(identities)))
            self.assertTrue(result_path.is_file())

    def test_s1_r01_baseline_failure_does_not_stop_schedule(self):
        FakeFormalBaselineRunner.fail_identity = (
            "S1-R01",
            "prompt_only_baseline",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "evaluation.formal_evaluation_plan.require_finalized_baseline_commit",
                return_value="a" * 40,
            ):
                result, _result_path = self.run_plan(Path(directory))
            self.assertEqual(result["pilot_status"], "FAIL")
            self.assertEqual(result["plan_status"], "COMPLETED_WITH_RETAINED_FAILURES")
            self.assertEqual(result["attempt_count"], 32)
            self.assertEqual(result["unique_attempt_identity_count"], 32)
            self.assertTrue(result["schedule_complete"])
            self.assertEqual(result["replacement_attempt_count"], 0)
            self.assertEqual(result["retained_failure_count"], 1)

    def test_s1_r01_mvp_failure_does_not_stop_schedule(self):
        FakeFormalMvpRunner.fail_identity = ("S1-R01", "mvp")
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "evaluation.formal_evaluation_plan.require_finalized_baseline_commit",
                return_value="a" * 40,
            ):
                result, _ = self.run_plan(Path(directory))
        self.assertEqual(result["attempt_count"], 32)
        self.assertEqual(result["unique_attempt_identity_count"], 32)
        self.assertEqual(result["retained_failure_count"], 1)
        self.assertTrue(result["schedule_complete"])

    def test_post_pilot_failure_is_retained_while_remaining_plan_continues(self):
        FakeFormalBaselineRunner.fail_identity = (
            "S3-R02",
            "prompt_only_baseline",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "evaluation.formal_evaluation_plan.require_finalized_baseline_commit",
                return_value="a" * 40,
            ):
                result, _result_path = self.run_plan(Path(directory))
            self.assertEqual(result["pilot_status"], "PASS")
            self.assertEqual(
                result["plan_status"], "COMPLETED_WITH_RETAINED_FAILURES"
            )
            self.assertEqual(result["attempt_count"], 32)
            failed = [row for row in result["attempts"] if not row["formal_attempt_pass"]]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["pair_key"], "S3-R02")

    def test_pending_freeze_blocks_before_plan_or_runner_creation(self):
        manifest, pairs = load_pair_plan()
        _manifest, attempts = load_attempt_plan()
        pending_manifest = copy.deepcopy(manifest)
        pending_manifest["product_baseline"][
            "local_baseline_commit"
        ] = "PENDING_V10_2_REVIEW_COMMIT"
        runner_factory = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "evaluation.formal_evaluation_plan.load_pair_plan",
                return_value=(pending_manifest, pairs),
            ), patch(
                "evaluation.formal_evaluation_plan.load_attempt_plan",
                return_value=(pending_manifest, attempts),
            ):
                with self.assertRaisesRegex(FormalEvaluationPlanError, "pending"):
                    FormalEvaluationPlan(
                        output_root=root,
                        reviewer_username="researcher",
                        mvp_runner_factory=runner_factory,
                        baseline_runner_factory=runner_factory,
                        validator_factory=FakeFormalValidator,
                    ).run()
            self.assertEqual(list(root.iterdir()), [])
            runner_factory.assert_not_called()

    def test_result_is_atomically_retained_after_every_attempt(self):
        retained_counts = []
        from evaluation.formal_evaluation_plan import _atomic_write_json as real_write

        def recording_write(path, value):
            real_write(path, value)
            if path.name == "formal_plan_result.json":
                retained_counts.append(value["attempt_count"])

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "evaluation.formal_evaluation_plan.require_finalized_baseline_commit",
                return_value="a" * 40,
            ), patch(
                "evaluation.formal_evaluation_plan._atomic_write_json",
                side_effect=recording_write,
            ):
                result, _ = self.run_plan(Path(directory))
        for count in range(1, 33):
            self.assertIn(count, retained_counts)
        self.assertEqual(result["attempt_count"], 32)

    def test_management_command_does_not_error_for_retained_condition_failure(self):
        result = {
            "plan_status": "COMPLETED_WITH_RETAINED_FAILURES",
            "pilot_status": "FAIL",
            "planned_attempt_count": 32,
            "attempt_count": 32,
            "unique_attempt_identity_count": 32,
            "schedule_complete": True,
            "execution_completed_count": 31,
            "validator_pass_count": 31,
            "formal_evidence_eligible_count": 31,
            "retained_failure_count": 1,
        }
        output = StringIO()
        with patch(
            "interviews.management.commands.run_formal_evaluation_plan.FormalEvaluationPlan.run",
            return_value=(result, Path("formal_plan_result.json")),
        ):
            call_command(
                "run_formal_evaluation_plan",
                reviewer="researcher",
                stdout=output,
            )
        self.assertIn("COMPLETED_WITH_RETAINED_FAILURES", output.getvalue())

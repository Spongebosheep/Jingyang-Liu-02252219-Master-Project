import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from evaluation.baseline_development_gate import (
    BaselineDevelopmentGate,
    BaselineDevelopmentGateError,
)


class FakeGateRunner:
    fail_pair_key = None

    def __init__(
        self,
        *,
        scenario_id,
        repetition,
        run_type,
        output_root,
        repository_root=None,
    ):
        self.pair_key = f"{scenario_id}-R{repetition:02d}"
        self.run_id = f"fake-baseline-{self.pair_key.lower()}"
        self.record_path = Path(output_root) / self.run_id / "runner_record.json"
        self.record_path.parent.mkdir(parents=True, exist_ok=False)
        self.record = {
            "run_id": self.run_id,
            "condition": "prompt_only_baseline",
            "scenario_id": scenario_id,
            "repetition": repetition,
            "run_type": run_type,
            "formal_run": False,
            "execution_status": "created",
            "runtime_identity": {
                "model": "gpt-4.1-mini",
                "model_transport": "openai_responses_api",
                "mock_transport": False,
                "openai_api_key_configured": True,
            },
            "code_identity": {"tracked_worktree_clean": True},
        }
        self._write()

    def _write(self):
        self.record_path.write_text(json.dumps(self.record), encoding="utf-8")

    def run(self):
        if self.pair_key == self.fail_pair_key:
            self.record["execution_status"] = "execution_error"
            self._write()
            raise RuntimeError("retained development-path failure")
        self.record["execution_status"] = "completed"
        self._write()
        return self.record


class FakeGateValidator:
    def __init__(self, *, record_path, output_root):
        self.record_path = Path(record_path)
        self.output_root = Path(output_root)

    def run(self):
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        status = "PASS" if record["execution_status"] == "completed" else "INCOMPLETE"
        result = {
            "run_id": record["run_id"],
            "validation_status": status,
            "formal_evidence_eligible": False,
        }
        path = self.output_root / "validator-result.json"
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text(json.dumps(result), encoding="utf-8")
        return result, path


class BaselineDevelopmentGateTests(SimpleTestCase):
    def setUp(self):
        FakeGateRunner.fail_pair_key = None

    def test_historical_gate_cannot_be_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(BaselineDevelopmentGateError, "must not be rerun"):
                BaselineDevelopmentGate(
                    output_root=root,
                    runner_factory=FakeGateRunner,
                    validator_factory=FakeGateValidator,
                ).run()
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_historical_directory_is_not_searched_or_reconstructed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent-historical-directory"
            with self.assertRaises(BaselineDevelopmentGateError):
                BaselineDevelopmentGate(output_root=root).run()
            self.assertFalse(root.exists())

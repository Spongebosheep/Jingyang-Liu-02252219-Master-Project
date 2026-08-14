import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.execution_plan import (
    EvaluationPlanError,
    load_attempt_plan,
    load_pair_plan,
)
from evaluation.schemas import load_json
from evaluation.validate_specs import SPEC_DIR


class EvaluationExecutionPlanTests(unittest.TestCase):
    def test_pair_and_attempt_schedule_is_exact(self):
        _manifest, pairs = load_pair_plan()
        _manifest, attempts = load_attempt_plan()
        self.assertEqual(len(pairs), 16)
        self.assertEqual(len(attempts), 32)
        self.assertEqual(pairs[0].pair_key, "S1-R01")
        self.assertEqual(pairs[-1].pair_key, "S8-R01")
        self.assertEqual(
            [pair.pair_key for pair in pairs],
            [
                "S1-R01",
                "S1-R02",
                "S1-R03",
                "S2-R01",
                "S2-R02",
                "S2-R03",
                "S3-R01",
                "S3-R02",
                "S3-R03",
                "S4-R01",
                "S5-R01",
                "S6-R01",
                "S6-R02",
                "S6-R03",
                "S7-R01",
                "S8-R01",
            ],
        )
        for index in range(0, len(attempts), 2):
            self.assertEqual(attempts[index].condition, "mvp")
            self.assertEqual(attempts[index + 1].condition, "prompt_only_baseline")
            self.assertEqual(attempts[index].pair_key, attempts[index + 1].pair_key)

    def test_plan_rejects_accidental_three_repetitions_for_every_scenario(self):
        manifest = load_json(SPEC_DIR / "freeze_manifest.v10_2.json")
        changed = copy.deepcopy(manifest)
        changed["run_plan"]["recommended_repetitions_per_condition"] = {
            f"S{index}": 3 for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(EvaluationPlanError):
                load_pair_plan(path)


if __name__ == "__main__":
    unittest.main()

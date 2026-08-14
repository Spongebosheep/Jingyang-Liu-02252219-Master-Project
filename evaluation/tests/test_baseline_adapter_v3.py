import copy
import unittest

from evaluation.baseline_adapter import (
    BASELINE_ADAPTER_VERSION,
    BASELINE_PROMPT_VERSION,
    PROMPT_PATH,
    BaselineParseError,
    _require_schema_valid_output,
)
from evaluation.schemas import load_json
from evaluation.validate_specs import SPEC_DIR


class BaselineAdapterV3Tests(unittest.TestCase):
    def setUp(self):
        self.schema = load_json(SPEC_DIR / "prompt_only_output_schema.v2.json")
        self.valid = {
            "coverage_assessment": "covered",
            "participant_control": "none",
            "selected_action": "move_next",
            "covered_information": ["one trigger", "one sign"],
            "missing_information": [],
            "reason": "The Protocol threshold is covered.",
            "participant_response": "Thank you. Let us continue.",
        }

    def test_prompt_v3_makes_assessment_guidance_binding_without_oracle_inputs(self):
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        normalised_prompt = " ".join(prompt.split())
        self.assertEqual(BASELINE_ADAPTER_VERSION, "3.0")
        self.assertEqual(BASELINE_PROMPT_VERSION, "3.0")
        self.assertIn("assessment_guidance` is the binding threshold", prompt)
        self.assertIn("It is not automatically an all-items", prompt)
        self.assertIn(
            "Do not probe merely because more detail might be useful",
            normalised_prompt,
        )
        for scenario_id in ("S1-T01", "S2-T01", "S3-T02"):
            self.assertNotIn(scenario_id, prompt)

    def test_internal_consistency_accepts_covered_move_next(self):
        self.assertEqual(
            _require_schema_valid_output(copy.deepcopy(self.valid), self.schema),
            self.valid,
        )

    def test_internal_consistency_rejects_covered_follow_up(self):
        changed = copy.deepcopy(self.valid)
        changed["selected_action"] = "ask_follow_up"
        changed["missing_information"] = ["optional detail"]
        with self.assertRaises(BaselineParseError):
            _require_schema_valid_output(changed, self.schema)

    def test_internal_consistency_rejects_control_action_mismatch(self):
        changed = copy.deepcopy(self.valid)
        changed.update(
            {
                "coverage_assessment": "not_assessed",
                "participant_control": "skip",
                "selected_action": "move_next",
                "covered_information": [],
            }
        )
        with self.assertRaises(BaselineParseError):
            _require_schema_valid_output(changed, self.schema)

    def test_missing_information_action_requires_nonempty_missing_list(self):
        changed = copy.deepcopy(self.valid)
        changed.update(
            {
                "coverage_assessment": "partially_covered",
                "selected_action": "flag_missing_and_move_next",
                "covered_information": ["setting"],
                "missing_information": [],
            }
        )
        with self.assertRaises(BaselineParseError):
            _require_schema_valid_output(changed, self.schema)


if __name__ == "__main__":
    unittest.main()

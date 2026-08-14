#!/usr/bin/env python3
"""Integrity tests for the researcher-validation analysis rebuild."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rebuild_analysis


class AnalysisRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package_root = Path(__file__).resolve().parents[2]
        cls.analysis = rebuild_analysis.build_analysis(cls.package_root)

    def test_formal_sample_and_runs(self) -> None:
        self.assertEqual(self.analysis["formal_sample_n"], 3)
        self.assertEqual(self.analysis["formal_participants"], ["P01", "P02", "P03"])
        self.assertFalse(self.analysis["pilot_in_formal_statistics"])
        self.assertEqual(len(self.analysis["condition_runs"]), 6)
        for participant in self.analysis["formal_participants"]:
            conditions = {
                row["condition"] for row in self.analysis["condition_runs"] if row["participant"] == participant
            }
            self.assertEqual(conditions, {"Manual", "Prototype"})

    def test_objective_universe(self) -> None:
        rows = self.analysis["objective_rows"]
        self.assertEqual(len(rows), 60)
        self.assertEqual(len({row["row_id"] for row in rows}), 60)
        self.assertEqual(sum(row["level"] == "Topic" for row in rows), 24)
        self.assertEqual(sum(row["level"] == "Evidence item" for row in rows), 36)

    def test_confirmed_objective_results(self) -> None:
        manual = self.analysis["aggregate"]["Manual"]
        prototype = self.analysis["aggregate"]["Prototype"]
        self.assertEqual((manual["topic_correct"], manual["topic_total"]), (11, 12))
        self.assertEqual((prototype["topic_correct"], prototype["topic_total"]), (12, 12))
        self.assertEqual((manual["item_correct"], manual["item_total"]), (16, 18))
        self.assertEqual((prototype["item_correct"], prototype["item_total"]), (17, 18))
        self.assertEqual((manual["issues_recognized"], manual["issues_total"]), (10, 12))
        self.assertEqual((prototype["issues_recognized"], prototype["issues_total"]), (11, 12))
        self.assertEqual((manual["missingness_correct"], manual["missingness_total"]), (2, 3))
        self.assertEqual((prototype["missingness_correct"], prototype["missingness_total"]), (3, 3))
        self.assertEqual(manual["false_approvals"], 2)
        self.assertEqual(prototype["false_approvals"], 1)

    def test_timing_effort_and_preference(self) -> None:
        manual = self.analysis["aggregate"]["Manual"]
        prototype = self.analysis["aggregate"]["Prototype"]
        self.assertEqual(manual["time_values_minutes"], [16.0, 24.0, 21.0])
        self.assertEqual(prototype["time_values_minutes"], [13.0, 19.0, 20.0])
        self.assertEqual(manual["time_mean_minutes"], 20.3)
        self.assertEqual(prototype["time_mean_minutes"], 17.3)
        self.assertEqual(manual["time_median_minutes"], 21.0)
        self.assertEqual(prototype["time_median_minutes"], 19.0)
        self.assertEqual(self.analysis["paired_time_difference_mean_minutes"], 3.0)
        self.assertEqual(manual["mental_effort_median"], 6.0)
        self.assertEqual(prototype["mental_effort_median"], 5.0)
        self.assertEqual(self.analysis["prototype_preference_count"], 3)
        self.assertEqual(self.analysis["prototype_preference_total"], 3)
        self.assertIn("objectively timed", self.analysis["time_basis"].casefold())

    def test_deterministic_analysis_object(self) -> None:
        second = rebuild_analysis.build_analysis(self.package_root)
        first_json = json.dumps(self.analysis, ensure_ascii=False, sort_keys=True)
        second_json = json.dumps(second, ensure_ascii=False, sort_keys=True)
        self.assertEqual(first_json, second_json)


if __name__ == "__main__":
    output_path = Path(__file__).resolve().parent / "outputs" / "Test_Log.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisRebuildTests)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        stream.write(f"\nstatus: {'PASS' if result.wasSuccessful() else 'FAIL'}\n")
    print(output_path.read_text(encoding="utf-8"), end="")
    raise SystemExit(0 if result.wasSuccessful() else 1)

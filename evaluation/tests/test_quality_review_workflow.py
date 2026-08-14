import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.quality_review import (
    BlindedQualityReviewExporter,
    CoderSubmissionImporter,
    ManualMetricConsensusReporter,
    QualityConsensusReporter,
    QualityReviewError,
)
from evaluation.execution_plan import load_pair_plan
from evaluation.schemas import (
    EXPORT_TABLE_FIELDS,
    load_json,
    pair_integrity_from_run_rows,
    sha256_file,
)
from evaluation.validate_specs import SPEC_DIR


class FixedRandom:
    def __init__(self, value=0):
        self.value = value

    def randrange(self, _stop):
        return self.value


class QualityReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.quality_contract = load_json(SPEC_DIR / "quality_review.v2.json")

    @staticmethod
    def write_csv(path, fields, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def read_csv(path):
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def make_export(self, root, pair_keys=None, incomplete_pair_keys=()):
        export = root / "source_export"
        (export / "tables").mkdir(parents=True)
        pair_keys = pair_keys or [
            "S1-R01",
            "S1-R02",
            "S1-R03",
            "S2-R01",
            "S2-R02",
            "S2-R03",
            "S3-R01",
            "S7-R01",
        ]
        incomplete_pair_keys = set(incomplete_pair_keys)
        protocol = {
            "schema_version": 1,
            "identity": {"version": 1, "status": "locked"},
            "configuration": {
                "title": "Sensory interview",
                "purpose": "Understand one participant experience.",
                "sections": [
                    {
                        "index": 1,
                        "code": "experience",
                        "label": "Experience",
                        "primary_question": "What happened?",
                        "required_information": ["setting", "event", "effect"],
                        "interaction_boundary": "Do not request medical explanation.",
                    }
                ],
            },
            "runtime": {"max_probes_per_section": 1},
        }
        run_rows = []
        for pair_number, pair_key in enumerate(pair_keys, start=1):
            scenario_id, repetition_text = pair_key.split("-R", 1)
            repetition = int(repetition_text)
            for condition in ("mvp", "prompt_only_baseline"):
                run_id = f"{condition}-{pair_key.lower()}"
                run_directory = export / "raw" / "runs" / run_id
                run_directory.mkdir(parents=True)
                execution_status = (
                    "execution_error"
                    if pair_key in incomplete_pair_keys
                    and condition == "prompt_only_baseline"
                    else "completed"
                )
                record = {
                "schema_version": "1.0",
                "run_id": run_id,
                "condition": condition,
                "scenario_id": scenario_id,
                "repetition": repetition,
                "protocol_snapshot_sha256": "protocol-hash",
                "protocol_snapshot": protocol,
                "final_database_snapshot": {
                    "session": {"id": pair_number},
                    "messages": [
                        {
                            "id": 1,
                            "sender": "participant",
                            "section": "Experience",
                            "section_index": 1,
                            "content": "The train noise and crowding made me feel overwhelmed.",
                            "created_at": "2026-08-06T10:00:00+00:00",
                        },
                        {
                            "id": 2,
                            "sender": "agent",
                            "section": "Experience",
                            "section_index": 1,
                            "content": "What part of the situation felt strongest?",
                            "created_at": "2026-08-06T10:00:01+00:00",
                        },
                    ],
                    "agent_decisions": [
                        {
                            "source_message_id": 1,
                            "selected_action": "ask_follow_up",
                            "section": "Experience",
                        }
                    ],
                    "digest_items": [
                        {
                            "id": 10,
                            "session_id": pair_number,
                            "section_index": 1,
                            "section_code": "experience",
                            "coverage_status": "answered",
                            "missing_information": [],
                            "generated_text": "The participant reported train noise and crowding.",
                            "final_text": "The participant reported train noise and crowding.",
                            "evidence_text": "The participant reported train noise and crowding.",
                            "review_status": "approved",
                            "reviewed_text": "",
                            "is_included": True,
                            "is_evidence_candidate": True,
                            "source_message_ids": [1],
                        }
                    ],
                    "review_events": [],
                    "review_decision": {},
                },
                }
                record_path = run_directory / "runner_record.json"
                record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
                row = {field: "" for field in EXPORT_TABLE_FIELDS["runs"]}
                row.update(
                    {
                        "run_id": run_id,
                        "condition": condition,
                        "scenario_id": scenario_id,
                        "repetition": str(repetition),
                        "pair_key": pair_key,
                        "run_type": "formal",
                        "formal_run": "true",
                        "execution_status": execution_status,
                        "run_status": execution_status,
                        "code_commit": "deadbeef",
                        "model_id": "gpt-4.1-mini",
                        "protocol_snapshot_sha256": "protocol-hash",
                        "scenario_sha256": "scenario-hash",
                        "metric_rules_sha256": "metric-hash",
                        "scenario_instance_sha256": f"instance-{pair_key}",
                        "runner_record_path": str(record_path.relative_to(export)),
                        "runner_record_sha256": sha256_file(record_path),
                        "formal_evidence_eligible": "true",
                        "validation_status": (
                            "INCOMPLETE"
                            if execution_status != "completed"
                            else "PASS"
                        ),
                    }
                )
                run_rows.append(row)

        self.write_csv(export / "tables" / "runs.csv", EXPORT_TABLE_FIELDS["runs"], run_rows)
        for name in ("turns", "item_sources", "reviews"):
            self.write_csv(export / "tables" / f"{name}.csv", EXPORT_TABLE_FIELDS[name], [])
        inventory = [
            {
                "path": str(path.relative_to(export)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(export.rglob("*"))
            if path.is_file()
        ]
        manifest = {
            "schema_version": "1.0",
            "export_id": "synthetic-export",
            "export_status": "completed",
            "run_count": len(run_rows),
            "pair_integrity": pair_integrity_from_run_rows(run_rows),
            "artifact_inventory": inventory,
        }
        (export / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return export

    def make_review_package(self, root):
        source = self.make_export(root)
        source_hashes = {
            path.relative_to(source): sha256_file(path)
            for path in source.rglob("*")
            if path.is_file()
        }
        manifest, package = BlindedQualityReviewExporter(
            export_directory=source,
            output_root=root / "review_packages",
            random_source=FixedRandom(0),
        ).run()
        self.assertEqual(
            source_hashes,
            {
                path.relative_to(source): sha256_file(path)
                for path in source.rglob("*")
                if path.is_file()
            },
        )
        return manifest, package

    def fill_quality_sheet(self, template, output, coder_id, *, disagree=False):
        rows = self.read_csv(template)
        for row in rows:
            row["coder_id"] = coder_id
            for field in (
                "a_q1_protocol_coverage",
                "b_q1_protocol_coverage",
                "a_q2_probing_relevance_neutrality",
                "b_q2_probing_relevance_neutrality",
                "a_q3_transcript_grounding_fidelity",
                "b_q3_transcript_grounding_fidelity",
                "a_q4_participant_clarity_control",
                "b_q4_participant_clarity_control",
                "a_q5_researcher_reviewability",
                "b_q5_researcher_reviewability",
            ):
                row[field] = "4"
            if disagree:
                row["a_q1_protocol_coverage"] = "5"
            row["overall_preference"] = "Tie" if disagree else "A"
            row["confidence"] = "3"
            row["short_rationale"] = "Independent blinded assessment."
            row["issue_flags_json"] = "[]"
            row["completed_at"] = "2026-08-06T20:00:00+08:00"
        self.write_csv(output, self.quality_contract["coding_sheet_fields"], rows)

    def import_sheet(self, package, sheet, coding_type, root):
        return CoderSubmissionImporter(
            coder_material_directory=package / "coder_material",
            coding_sheet=sheet,
            coding_type=coding_type,
            output_root=root / "imports",
        ).run()

    def test_blinded_export_separates_key_and_fixed_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = self.make_review_package(root)
            coder_root = package / "coder_material"
            private_key = package / "private" / "blinding_key.json"
            self.assertTrue((coder_root / "pairs" / "S7-R01.html").is_file())
            self.assertTrue(private_key.is_file())
            coder_manifest = json.loads((coder_root / "coder_manifest.json").read_text())
            coder_manifest_text = json.dumps(coder_manifest)
            self.assertFalse(coder_manifest["private_key_required_for_coding_or_import"])
            self.assertFalse(coder_manifest["condition_identity_in_coder_material"])
            self.assertNotIn("mvp", coder_manifest_text.lower())
            self.assertNotIn("prompt_only_baseline", coder_manifest_text.lower())
            self.assertNotIn("a_is", coder_manifest_text.lower())
            coder_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in coder_root.rglob("*")
                if path.is_file()
            ).lower()
            for forbidden in ("mvp", "prompt_only_baseline", "baseline-run", "deadbeef", "gpt-4.1-mini"):
                self.assertNotIn(forbidden, coder_text)
            key = json.loads(private_key.read_text(encoding="utf-8"))
            self.assertEqual(key["assignments"][0]["A"]["condition"], "mvp")
            self.assertEqual(manifest["source_export"]["pair_count"], 8)
            self.assertEqual(
                manifest["pair_count_status"], "ready_below_recommended_pair_count"
            )
            self.assertEqual(len(self.read_csv(coder_root / "quality_coding_sheet_template.csv")), 8)
            self.assertEqual(len(self.read_csv(coder_root / "ucr_atomic_proposition_coding_template.csv")), 16)
            self.assertEqual(len(self.read_csv(coder_root / "mp_item_coding_template.csv")), 16)

    def test_pair_selection_blocks_below_eight_and_retains_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_export(root, pair_keys=["S7-R01"])
            manifest, package = BlindedQualityReviewExporter(
                export_directory=source,
                output_root=root / "review_packages",
                random_source=FixedRandom(0),
            ).run()
            self.assertEqual(
                manifest["package_status"], "blocked_below_minimum_pair_count"
            )
            self.assertEqual(manifest["source_export"]["pair_count"], 1)
            self.assertFalse((package / "coder_material").exists())
            selection = json.loads(
                (package / "pair_selection_report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(selection["minimum_threshold_met"])
            self.assertEqual(selection["included_pair_keys"], ["S7-R01"])
            self.assertEqual(selection["excluded_pair_count"], 15)

    def test_pair_selection_excludes_incomplete_pair_without_aborting_remaining(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_keys = [pair.pair_key for pair in load_pair_plan()[1]][:9]
            source = self.make_export(
                root,
                pair_keys=pair_keys,
                incomplete_pair_keys={pair_keys[0]},
            )
            manifest, package = BlindedQualityReviewExporter(
                export_directory=source,
                output_root=root / "review_packages",
                random_source=FixedRandom(0),
            ).run()
            self.assertEqual(manifest["source_export"]["pair_count"], 8)
            self.assertTrue((package / "coder_material").is_dir())
            selection = json.loads(
                (package / "pair_selection_report.json").read_text(encoding="utf-8")
            )
            excluded = {row["pair_key"]: row for row in selection["excluded_pairs"]}
            self.assertEqual(
                excluded[pair_keys[0]]["reason_code"], "EXECUTION_NOT_COMPLETED"
            )

    def test_pair_selection_marks_full_sixteen_pair_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_keys = [pair.pair_key for pair in load_pair_plan()[1]]
            source = self.make_export(root, pair_keys=pair_keys)
            manifest, _package = BlindedQualityReviewExporter(
                export_directory=source,
                output_root=root / "review_packages",
                random_source=FixedRandom(0),
            ).run()
            self.assertEqual(manifest["source_export"]["pair_count"], 16)
            self.assertEqual(
                manifest["pair_count_status"], "ready_recommended_pair_count_met"
            )

    def test_quality_import_disagreement_consensus_and_deblinding_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _manifest, package = self.make_review_package(root)
            template = package / "coder_material" / "quality_coding_sheet_template.csv"
            sheet_1 = root / "coder1.csv"
            sheet_2 = root / "coder2.csv"
            self.fill_quality_sheet(template, sheet_1, "coder-1")
            self.fill_quality_sheet(template, sheet_2, "coder-2", disagree=True)
            import_1, dir_1 = self.import_sheet(package, sheet_1, "quality", root / "q1")
            import_2, dir_2 = self.import_sheet(package, sheet_2, "quality", root / "q2")
            self.assertEqual(import_1["import_status"], "locked")
            self.assertEqual(import_2["condition_key_read"], False)

            report, report_dir = QualityConsensusReporter(
                coder_import_directories=[dir_1, dir_2],
                output_root=root / "reports",
            ).run()
            self.assertEqual(report["report_status"], "awaiting_consensus")
            self.assertGreater(report["initial_disagreement_count"], 0)
            with self.assertRaises(QualityReviewError):
                QualityConsensusReporter(
                    coder_import_directories=[dir_1, dir_2],
                    output_root=root / "early_deblind",
                    blinding_key=package / "private" / "blinding_key.json",
                ).run()

            consensus_rows = self.read_csv(report_dir / "consensus_report.csv")
            for row in consensus_rows:
                if row["disagreement"] != "true":
                    continue
                field = row["field_name"]
                if field.startswith(("a_q", "b_q")):
                    row["consensus_label"] = "4"
                elif field == "overall_preference":
                    row["consensus_label"] = "A"
                elif field == "confidence":
                    row["consensus_label"] = "3"
                elif field == "issue_flags_json":
                    row["consensus_label"] = "[]"
                else:
                    row["consensus_label"] = "Agreed rationale."
                row["consensus_note"] = "Resolved after discussion."
                row["resolved_at"] = "2026-08-06T21:00:00+08:00"
            consensus_path = root / "completed_quality_consensus.csv"
            self.write_csv(
                consensus_path,
                self.quality_contract["consensus_report_fields"],
                consensus_rows,
            )
            final, final_dir = QualityConsensusReporter(
                coder_import_directories=[dir_1, dir_2],
                output_root=root / "final_reports",
                consensus_sheet=consensus_path,
                blinding_key=package / "private" / "blinding_key.json",
            ).run()
            self.assertEqual(final["report_status"], "completed")
            self.assertTrue(final["condition_key_read"])
            self.assertTrue((final_dir / "deblinded_pair_key.json").is_file())
            self.assertFalse(final["comparison_claim_generated"])

    def fill_manual_sheet(self, template, output, coder_id, coding_type, *, disagree=False):
        rows = self.read_csv(template)
        for row in rows:
            row["coder_id"] = coder_id
            row["completed_at"] = "2026-08-06T20:00:00+08:00"
            row["short_rationale"] = "Compared directly with the displayed source."
            if coding_type == "ucr":
                row["atomic_proposition"] = "The participant reported train noise and crowding."
                row["support_label"] = (
                    "unsupported" if disagree and row["blind_label"] == "B" else "supported"
                )
            else:
                row["meaning_preservation_label"] = (
                    "uncertain" if disagree and row["blind_label"] == "B" else "preserved"
                )
        fields = rows[0].keys() if rows else []
        self.write_csv(output, fields, rows)

    def test_ucr_and_mp_independent_coding_consensus_outputs_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _manifest, package = self.make_review_package(root)
            coder_root = package / "coder_material"

            ucr_1 = root / "ucr1.csv"
            ucr_2 = root / "ucr2.csv"
            self.fill_manual_sheet(
                coder_root / "ucr_atomic_proposition_coding_template.csv",
                ucr_1,
                "coder-1",
                "ucr",
            )
            self.fill_manual_sheet(
                coder_root / "ucr_atomic_proposition_coding_template.csv",
                ucr_2,
                "coder-2",
                "ucr",
                disagree=True,
            )
            _m1, ucr_dir_1 = self.import_sheet(package, ucr_1, "ucr", root / "ucr_i1")
            _m2, ucr_dir_2 = self.import_sheet(package, ucr_2, "ucr", root / "ucr_i2")
            draft, draft_dir = ManualMetricConsensusReporter(
                coding_type="ucr",
                coder_import_directories=[ucr_dir_1, ucr_dir_2],
                output_root=root / "ucr_reports",
            ).run()
            self.assertEqual(draft["report_status"], "awaiting_consensus")
            consensus_rows = self.read_csv(draft_dir / "consensus_report.csv")
            for row in consensus_rows:
                if row["segmentation_or_label_disagreement"] == "true":
                    row["consensus_propositions_json"] = json.dumps(
                        [
                            {
                                "atomic_proposition": "The participant reported train noise and crowding.",
                                "support_label": "supported",
                            }
                        ]
                    )
                    row["consensus_note"] = "Resolved against the displayed source."
                    row["resolved_at"] = "2026-08-06T21:00:00+08:00"
            ucr_consensus = root / "ucr_consensus.csv"
            self.write_csv(ucr_consensus, consensus_rows[0].keys(), consensus_rows)
            final_ucr, final_ucr_dir = ManualMetricConsensusReporter(
                coding_type="ucr",
                coder_import_directories=[ucr_dir_1, ucr_dir_2],
                output_root=root / "ucr_final",
                consensus_sheet=ucr_consensus,
            ).run()
            self.assertEqual(final_ucr["report_status"], "completed")
            ucr_metric = json.loads((final_ucr_dir / "metric_summary.json").read_text())
            self.assertEqual(ucr_metric["metric_code"], "UCR")
            self.assertEqual(ucr_metric["value"], 0.0)

            mp_1 = root / "mp1.csv"
            mp_2 = root / "mp2.csv"
            self.fill_manual_sheet(
                coder_root / "mp_item_coding_template.csv", mp_1, "coder-1", "mp"
            )
            self.fill_manual_sheet(
                coder_root / "mp_item_coding_template.csv", mp_2, "coder-2", "mp"
            )
            _m1, mp_dir_1 = self.import_sheet(package, mp_1, "mp", root / "mp_i1")
            _m2, mp_dir_2 = self.import_sheet(package, mp_2, "mp", root / "mp_i2")
            final_mp, final_mp_dir = ManualMetricConsensusReporter(
                coding_type="mp",
                coder_import_directories=[mp_dir_1, mp_dir_2],
                output_root=root / "mp_reports",
            ).run()
            self.assertEqual(final_mp["report_status"], "completed")
            mp_metric = json.loads((final_mp_dir / "metric_summary.json").read_text())
            self.assertEqual(mp_metric["metric_code"], "MP")
            self.assertEqual(mp_metric["value"], 1.0)

    def test_importer_rejects_changed_metadata_and_unregistered_manual_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _manifest, package = self.make_review_package(root)
            coder_root = package / "coder_material"
            bad_quality = root / "bad_quality.csv"
            self.fill_quality_sheet(
                coder_root / "quality_coding_sheet_template.csv",
                bad_quality,
                "coder-1",
            )
            rows = self.read_csv(bad_quality)
            rows[0]["blind_key"] = "changed"
            self.write_csv(
                bad_quality, self.quality_contract["coding_sheet_fields"], rows
            )
            with self.assertRaises(QualityReviewError):
                self.import_sheet(package, bad_quality, "quality", root / "bad_q")

            bad_mp = root / "bad_mp.csv"
            self.fill_manual_sheet(
                coder_root / "mp_item_coding_template.csv",
                bad_mp,
                "coder-1",
                "mp",
            )
            rows = self.read_csv(bad_mp)
            rows[0]["item_key"] = "I99"
            self.write_csv(bad_mp, rows[0].keys(), rows)
            with self.assertRaises(QualityReviewError):
                self.import_sheet(package, bad_mp, "mp", root / "bad_mp_i")


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.exporter import EvaluationBundleExporter


class Command(BaseCommand):
    help = (
        "Copy saved MVP and prompt-only observations into one append-only raw "
        "JSON/CSV/Evidence Record package without calculating metrics."
    )

    def add_arguments(self, parser):
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--record",
            action="append",
            type=Path,
            help="Path to runner_record.json. Repeat to export several runs.",
        )
        selection.add_argument(
            "--input-root",
            type=Path,
            help="Recursively export every runner_record.json below this directory.",
        )
        parser.add_argument(
            "--validator-result",
            action="append",
            type=Path,
            default=[],
            help=(
                "Optional validator_result.json stored outside its runner directory. "
                "Repeat for several results. Local append-only validation results are "
                "discovered automatically."
            ),
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_exports",
            help="Parent directory for unique, never-overwritten export packages.",
        )

    def handle(self, *args, **options):
        if options["record"]:
            record_paths = [path.resolve() for path in options["record"]]
        else:
            input_root = options["input_root"].resolve()
            if not input_root.is_dir():
                raise CommandError(f"--input-root is not a directory: {input_root}")
            record_paths = sorted(input_root.rglob("runner_record.json"))
        if not record_paths:
            raise CommandError("No runner_record.json files were selected.")

        try:
            manifest, export_directory = EvaluationBundleExporter(
                record_paths=record_paths,
                output_root=options["output_root"],
                validator_result_paths=options["validator_result"],
            ).run()
        except Exception as error:
            raise CommandError(f"Evaluation export failed: {error}") from error

        summary = {
            "export_id": manifest["export_id"],
            "export_directory": str(export_directory),
            "manifest_path": str(export_directory / "manifest.json"),
            "run_count": manifest["run_count"],
            "condition_counts": manifest["condition_counts"],
            "execution_error_run_count": manifest["execution_error_run_count"],
            "formal_evidence_eligible_run_count": manifest[
                "formal_evidence_eligible_run_count"
            ],
            "table_row_counts": manifest["table_row_counts"],
            "metric_calculation_performed": False,
            "comparison_claim_generated": False,
            "human_coding_performed": False,
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
        self.stdout.write(
            self.style.SUCCESS(
                "Saved observations were exported without modifying the runner files "
                "or database. No metric, formal paired conclusion, or human judgement "
                "was generated."
            )
        )

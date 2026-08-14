import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.metric_calculator import EvaluationMetricCalculator


class Command(BaseCommand):
    help = (
        "Calculate the frozen automatic metrics from one completed evaluation "
        "export without running Sessions, models, UCR/MP coding, or conclusions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--export-directory",
            required=True,
            type=Path,
            help="Completed append-only directory created by export_evaluation_bundle.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_metrics",
            help="Parent directory for unique, never-overwritten calculation packages.",
        )

    def handle(self, *args, **options):
        try:
            manifest, output_directory = EvaluationMetricCalculator(
                export_directory=options["export_directory"],
                output_root=options["output_root"],
            ).run()
        except Exception as error:
            raise CommandError(f"Evaluation metric calculation failed: {error}") from error

        summary = {
            "calculation_id": manifest["calculation_id"],
            "output_directory": str(output_directory),
            "manifest_path": str(output_directory / "calculation_manifest.json"),
            "input_export_id": manifest["input_export"]["export_id"],
            "run_count": manifest["input_export"]["run_count"],
            "automatic_metric_codes": manifest["automatic_metric_codes"],
            "manual_metric_codes": manifest["manual_metric_codes"],
            "formal_evidence_eligible_run_count": manifest[
                "formal_evidence_eligible_run_count"
            ],
            "comparative_superiority_claim_generated": False,
            "human_coding_performed": False,
            "database_or_model_used": False,
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
        self.stdout.write(
            self.style.SUCCESS(
                "Frozen automatic metrics were calculated from the saved export. "
                "UCR, MP and comparative interpretation remain pending human work."
            )
        )

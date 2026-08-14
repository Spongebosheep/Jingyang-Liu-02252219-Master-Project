import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.quality_review import (
    ManualMetricConsensusReporter,
    QualityReviewError,
)


class Command(BaseCommand):
    help = (
        "Generate or finalise two-coder UCR/MP consensus records and a bounded "
        "manual metric summary without inferring comparative superiority."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--coding-type",
            required=True,
            choices=("ucr", "mp"),
        )
        parser.add_argument(
            "--coder-import",
            action="append",
            required=True,
            type=Path,
            help="Locked UCR or MP coder import directory. Repeat for each coder.",
        )
        parser.add_argument(
            "--consensus-sheet",
            type=Path,
            help="Optional completed consensus_report.csv from an earlier draft report.",
        )
        parser.add_argument(
            "--blinding-key",
            type=Path,
            help="Optional private key, accepted only after consensus is complete.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_quality_reports",
            help="Parent directory for unique, never-overwritten reports.",
        )

    def handle(self, *args, **options):
        try:
            manifest, directory = ManualMetricConsensusReporter(
                coding_type=options["coding_type"],
                coder_import_directories=options["coder_import"],
                output_root=options["output_root"],
                consensus_sheet=options.get("consensus_sheet"),
                blinding_key=options.get("blinding_key"),
            ).run()
        except QualityReviewError as error:
            raise CommandError(f"Manual metric consensus report failed: {error}") from error

        self.stdout.write(
            json.dumps(
                {
                    "report_id": manifest["report_id"],
                    "report_directory": str(directory),
                    "coding_type": manifest["coding_type"],
                    "report_status": manifest["report_status"],
                    "independent_coder_count": manifest["independent_coder_count"],
                    "initial_disagreement_count": manifest[
                        "initial_disagreement_count"
                    ],
                    "unresolved_disagreement_count": manifest[
                        "unresolved_disagreement_count"
                    ],
                    "metric_summary_path": manifest.get("metric_summary_path"),
                    "comparison_claim_generated": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Manual coding disagreements and consensus were retained. A metric "
                "summary is written only after every disagreement is resolved."
            )
        )

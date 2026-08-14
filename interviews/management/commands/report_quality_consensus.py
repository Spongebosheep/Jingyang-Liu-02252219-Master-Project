import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.quality_review import QualityConsensusReporter, QualityReviewError


class Command(BaseCommand):
    help = (
        "Compare two or more locked blinded quality submissions, retain original "
        "labels, and generate or finalise the disagreement/consensus report."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--coder-import",
            action="append",
            required=True,
            type=Path,
            help="Locked quality coder import directory. Repeat for each coder.",
        )
        parser.add_argument(
            "--consensus-sheet",
            type=Path,
            help=(
                "Optional completed consensus_report.csv from an earlier "
                "awaiting_consensus report."
            ),
        )
        parser.add_argument(
            "--blinding-key",
            type=Path,
            help=(
                "Optional private key. Accepted only after every disagreement is "
                "resolved; it writes a separate mapping and no winner."
            ),
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_quality_reports",
            help="Parent directory for unique, never-overwritten reports.",
        )

    def handle(self, *args, **options):
        try:
            manifest, directory = QualityConsensusReporter(
                coder_import_directories=options["coder_import"],
                output_root=options["output_root"],
                consensus_sheet=options.get("consensus_sheet"),
                blinding_key=options.get("blinding_key"),
            ).run()
        except QualityReviewError as error:
            raise CommandError(f"Quality consensus report failed: {error}") from error

        self.stdout.write(
            json.dumps(
                {
                    "report_id": manifest["report_id"],
                    "report_directory": str(directory),
                    "report_status": manifest["report_status"],
                    "independent_coder_count": manifest["independent_coder_count"],
                    "initial_disagreement_count": manifest[
                        "initial_disagreement_count"
                    ],
                    "unresolved_disagreement_count": manifest[
                        "unresolved_disagreement_count"
                    ],
                    "condition_key_read": manifest["condition_key_read"],
                    "comparison_claim_generated": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Independent labels and disagreements were retained. If the status "
                "is awaiting_consensus, complete only the blank consensus fields and rerun."
            )
        )

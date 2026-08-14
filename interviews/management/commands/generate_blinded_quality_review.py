import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.quality_review import (
    BlindedQualityReviewExporter,
    QualityReviewError,
)


class Command(BaseCommand):
    help = (
        "Generate an append-only, randomly assigned A/B coder package plus a "
        "separate private condition key from one completed evaluation export."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--export-directory",
            required=True,
            type=Path,
            help="Completed directory created by export_evaluation_bundle.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_quality_reviews",
            help="Parent directory for unique, never-overwritten review packages.",
        )

    def handle(self, *args, **options):
        try:
            manifest, directory = BlindedQualityReviewExporter(
                export_directory=options["export_directory"],
                output_root=options["output_root"],
            ).run()
        except QualityReviewError as error:
            raise CommandError(f"Blinded quality-review export failed: {error}") from error

        if manifest["package_status"] == "blocked_below_minimum_pair_count":
            self.stdout.write(
                json.dumps(
                    {
                        "review_package_id": manifest["review_package_id"],
                        "review_package_directory": str(directory),
                        "package_status": manifest["package_status"],
                        "pair_selection_report": str(
                            directory / manifest["pair_selection_report_path"]
                        ),
                        "pair_count": manifest["source_export"]["pair_count"],
                        "minimum_codable_pairs": manifest["minimum_codable_pairs"],
                        "comparison_claim_generated": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError(
                "Blinded coding is blocked below eight eligible matched pairs. "
                "The exclusion report was retained; do not backfill or replacement-rerun."
            )

        self.stdout.write(
            json.dumps(
                {
                    "review_package_id": manifest["review_package_id"],
                    "package_status": manifest["package_status"],
                    "pair_count_status": manifest["pair_count_status"],
                    "review_package_directory": str(directory),
                    "coder_material_directory": str(directory / "coder_material"),
                    "private_blinding_key": str(
                        directory / manifest["private_key_path"]
                    ),
                    "pair_count": manifest["source_export"]["pair_count"],
                    "quality_coding_performed": False,
                    "ucr_coding_performed": False,
                    "mp_coding_performed": False,
                    "comparison_claim_generated": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Blinded material and fixed templates were generated. Give coders "
                "only the coder_material directory; retain private separately."
            )
        )

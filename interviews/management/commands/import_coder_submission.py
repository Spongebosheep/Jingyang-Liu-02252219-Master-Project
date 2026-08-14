import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.quality_review import CoderSubmissionImporter, QualityReviewError


class Command(BaseCommand):
    help = (
        "Validate and lock one independent quality, UCR, or MP coder CSV without "
        "reading the private A/B condition key."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--coder-material-directory",
            required=True,
            type=Path,
            help="The coder_material directory from generate_blinded_quality_review.",
        )
        parser.add_argument(
            "--coding-sheet",
            required=True,
            type=Path,
            help="Completed coder CSV with the frozen header.",
        )
        parser.add_argument(
            "--coding-type",
            required=True,
            choices=("quality", "ucr", "mp"),
            help="Which independent coding contract to validate.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_quality_coding",
            help="Parent directory for unique, never-overwritten locked submissions.",
        )

    def handle(self, *args, **options):
        try:
            manifest, directory = CoderSubmissionImporter(
                coder_material_directory=options["coder_material_directory"],
                coding_sheet=options["coding_sheet"],
                coding_type=options["coding_type"],
                output_root=options["output_root"],
            ).run()
        except QualityReviewError as error:
            raise CommandError(f"Coder submission import failed: {error}") from error

        self.stdout.write(
            json.dumps(
                {
                    "import_id": manifest["import_id"],
                    "import_directory": str(directory),
                    "coding_type": manifest["coding_type"],
                    "coder_id": manifest["coder_id"],
                    "row_count": manifest["row_count"],
                    "import_status": manifest["import_status"],
                    "condition_key_read": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "The independent submission was validated, copied byte-for-byte, "
                "normalised, and locked without deblinding."
            )
        )

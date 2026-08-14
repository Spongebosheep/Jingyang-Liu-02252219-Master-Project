import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.baseline_development_gate import (
    BaselineDevelopmentGate,
    BaselineDevelopmentGateError,
)


class Command(BaseCommand):
    help = (
        "Run the exact 16-attempt live prompt-only Baseline development gate. "
        "Every attempt is dry_run, retained, independently validated, and never "
        "eligible as formal evidence."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-root",
            type=Path,
            default=(
                Path(settings.BASE_DIR)
                / "evaluation"
                / "_development_gates"
                / "baseline"
            ),
            help="Parent for one append-only development-gate directory.",
        )

    def handle(self, *args, **options):
        try:
            result, result_path = BaselineDevelopmentGate(
                output_root=options["output_root"],
            ).run()
        except BaselineDevelopmentGateError as error:
            raise CommandError(f"Baseline development gate could not start: {error}") from error

        self.stdout.write(
            json.dumps(
                {
                    "gate_status": result["gate_status"],
                    "gate_result_path": str(result_path),
                    "planned_attempt_count": result["planned_attempt_count"],
                    "attempt_count": result["attempt_count"],
                    "pass_count": result["pass_count"],
                    "failure_count": result["failure_count"],
                    "formal_evidence_eligible": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        if result["gate_status"] != "PASS":
            raise CommandError(
                "Baseline development gate failed. All 16 attempts and available "
                "validator results were retained; do not freeze or run formal evidence."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Baseline development gate PASS: 16/16 live dry-run paths completed "
                "and independently validated. These records are not formal evidence."
            )
        )

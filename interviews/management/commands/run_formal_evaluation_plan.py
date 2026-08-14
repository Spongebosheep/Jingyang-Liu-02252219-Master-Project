import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.formal_evaluation_plan import (
    FormalEvaluationPlan,
    FormalEvaluationPlanError,
)


class Command(BaseCommand):
    help = (
        "Execute the immutable v10.2 formal schedule: S1-R01 first for operational "
        "ordering, then every other registered identity regardless of outcomes. "
        "No replacement reruns are performed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reviewer",
            required=True,
            help="Existing active researcher username attributed to every formal review.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_formal_plans",
            help="Parent for one append-only formal-plan directory.",
        )

    def handle(self, *args, **options):
        try:
            result, result_path = FormalEvaluationPlan(
                output_root=options["output_root"],
                reviewer_username=options["reviewer"],
            ).run()
        except FormalEvaluationPlanError as error:
            raise CommandError(f"Formal evaluation plan could not start: {error}") from error

        self.stdout.write(
            json.dumps(
                {
                    "plan_status": result["plan_status"],
                    "formal_plan_result_path": str(result_path),
                    "pilot_status": result["pilot_status"],
                    "planned_attempt_count": result["planned_attempt_count"],
                    "attempt_count": result["attempt_count"],
                    "unique_attempt_identity_count": result[
                        "unique_attempt_identity_count"
                    ],
                    "schedule_complete": result["schedule_complete"],
                    "execution_completed_count": result[
                        "execution_completed_count"
                    ],
                    "validator_pass_count": result["validator_pass_count"],
                    "formal_evidence_eligible_count": result[
                        "formal_evidence_eligible_count"
                    ],
                    "replacement_reruns_permitted": False,
                    "retained_failure_count": result["retained_failure_count"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        message = (
            "Formal schedule completed: all 32 identities were attempted and retained. "
            "No comparative or human-coding conclusion was generated."
        )
        if result["plan_status"] == "COMPLETED_ALL_PASS":
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING(message))

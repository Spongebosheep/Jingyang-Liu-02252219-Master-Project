import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.mvp_runner import (
    MvpScenarioRunner,
    RUN_TYPE_DRY,
    RUN_TYPE_FORMAL,
)
from evaluation.schemas import SCENARIO_IDS


class Command(BaseCommand):
    help = (
        "Execute frozen S1-S8 inputs through the real MVP application path and "
        "retain one non-adjudicated runner record per attempt."
    )

    def add_arguments(self, parser):
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--scenario",
            action="append",
            choices=SCENARIO_IDS,
            help="Scenario to execute. Repeat this option to select several.",
        )
        selection.add_argument(
            "--all",
            action="store_true",
            help="Execute S1-S8 in frozen order.",
        )
        parser.add_argument(
            "--repetition",
            type=int,
            default=1,
            help="Matched repetition number (minimum 1).",
        )
        parser.add_argument(
            "--run-type",
            choices=(RUN_TYPE_DRY, RUN_TYPE_FORMAL),
            default=RUN_TYPE_DRY,
            help=(
                "dry_run always rolls back QA database records; formal retains "
                "records and requires directly captured live-model evidence."
            ),
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "evaluation" / "_runs" / "mvp",
            help="Parent directory for unique, never-overwritten run directories.",
        )
        parser.add_argument(
            "--reviewer",
            default="",
            help=(
                "Existing researcher username. Required for formal runs; dry runs "
                "create a temporary attributed reviewer when omitted."
            ),
        )
    def handle(self, *args, **options):
        repetition = options["repetition"]
        if repetition < 1:
            raise CommandError("--repetition must be at least 1.")

        scenario_ids = list(SCENARIO_IDS) if options["all"] else options["scenario"]
        attempts = []
        failures = []
        for scenario_id in scenario_ids:
            runner = MvpScenarioRunner(
                scenario_id=scenario_id,
                repetition=repetition,
                run_type=options["run_type"],
                output_root=options["output_root"],
                reviewer_username=options["reviewer"],
            )
            try:
                result = runner.run()
            except Exception as error:
                attempts.append(
                    {
                        "scenario_id": scenario_id,
                        "run_id": runner.run_id,
                        "execution_status": runner.record["execution_status"],
                        "record_path": str(runner.record_path),
                    }
                )
                failures.append(
                    {
                        "scenario_id": scenario_id,
                        "run_id": runner.run_id,
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "record_path": str(runner.record_path),
                    }
                )
                continue

            attempts.append(
                {
                    "scenario_id": scenario_id,
                    "run_id": runner.run_id,
                    "execution_status": result["execution_status"],
                    "validation_status": result["validation_status"],
                    "record_path": str(runner.record_path),
                }
            )

        summary = {
            "condition": "mvp",
            "run_type": options["run_type"],
            "repetition": repetition,
            "attempt_count": len(attempts),
            "execution_completed_count": sum(
                item["execution_status"] == "completed" for item in attempts
            ),
            "validator_run": False,
            "attempts": attempts,
            "failures": failures,
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))

        if failures:
            raise CommandError(
                f"{len(failures)} MVP scenario attempt(s) failed. "
                "Every failure record was retained at the path shown above."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "MVP application-path execution completed. The separate validator "
                "has not run, so this is not a scenario PASS result."
            )
        )

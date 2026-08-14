import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from evaluation.run_validator import EvaluationRunValidator, PASS


class Command(BaseCommand):
    help = (
        "Validate saved evaluation runner records against the frozen oracle "
        "without executing the product or calculating aggregate metrics."
    )

    def add_arguments(self, parser):
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--record",
            action="append",
            type=Path,
            help="Path to runner_record.json. Repeat to validate several runs.",
        )
        selection.add_argument(
            "--input-root",
            type=Path,
            help="Recursively validate every runner_record.json below this directory.",
        )
        parser.add_argument(
            "--output-root",
            type=Path,
            default=None,
            help=(
                "Optional parent for append-only validator results. By default each "
                "result is stored below its runner directory."
            ),
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

        attempts = []
        failures = []
        for record_path in record_paths:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                run_id = str(record.get("run_id") or record_path.parent.name)
                output_root = (
                    options["output_root"].resolve() / run_id
                    if options["output_root"]
                    else None
                )
                result, result_path = EvaluationRunValidator(
                    record_path=record_path,
                    output_root=output_root,
                ).run()
            except Exception as error:
                failures.append(
                    {
                        "record_path": str(record_path),
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
                continue
            item = {
                "run_id": result["run_id"],
                "scenario_id": result["scenario_id"],
                "validation_status": result["validation_status"],
                "formal_evidence_eligible": result["formal_evidence_eligible"],
                "result_path": str(result_path),
                "summary": result["summary"],
            }
            attempts.append(item)
            if result["validation_status"] != PASS:
                failures.append(item)

        summary = {
            "validator": "independent_observed_vs_oracle",
            "record_count": len(record_paths),
            "result_count": len(attempts),
            "pass_count": sum(
                item["validation_status"] == PASS for item in attempts
            ),
            "attempts": attempts,
            "failures": failures,
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
        if failures:
            raise CommandError(
                f"{len(failures)} validation attempt(s) failed or remained incomplete; "
                "all successfully written validator results were retained."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Every saved run matched the frozen automatic oracle. No aggregate "
                "metric or manual UCR/MP judgement was produced."
            )
        )

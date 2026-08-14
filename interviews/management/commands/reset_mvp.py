from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from interviews.models import InterviewSession, Protocol, Stakeholder


class Command(BaseCommand):
    help = (
        "Restore the complete workflow database to the seeded P01 / IS-01 / "
        "Sensory Protocol baseline while preserving researcher user accounts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            dest="reset_all",
            help=(
                "Confirm deletion of all workflow-created Stakeholders, Sessions, "
                "Protocols, transcripts, and review data."
            ),
        )

    def handle(self, *args, **options):
        if not options["reset_all"]:
            raise CommandError(
                "No data was changed. Use `python manage.py reset_demo_session` to "
                "reset only IS-01, or `python manage.py reset_mvp --all` to restore "
                "the complete seeded workflow baseline."
            )

        with transaction.atomic():
            InterviewSession.objects.all().delete()
            Stakeholder.objects.all().delete()
            Protocol.objects.all().delete()
            seed_output = StringIO()
            call_command("seed_mvp", stdout=seed_output)

        self.stdout.write(
            self.style.SUCCESS(
                "Restored the complete MVP baseline: Sensory Overload Interview v1, "
                "P01, and IS-01. Researcher user accounts were preserved."
            )
        )

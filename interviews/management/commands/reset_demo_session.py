from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from interviews.demo_reset import reset_interview_session
from interviews.models import InterviewSession


class Command(BaseCommand):
    help = "Reset only the seeded IS-01 demo Session while preserving other records."

    def handle(self, *args, **options):
        with transaction.atomic():
            try:
                session = InterviewSession.objects.select_for_update().get(
                    session_code="IS-01"
                )
            except InterviewSession.DoesNotExist as error:
                raise CommandError(
                    "IS-01 does not exist. Run `python manage.py seed_mvp` first."
                ) from error

            reset_interview_session(session)

        self.stdout.write(
            self.style.SUCCESS(
                "Reset IS-01 only. Other Stakeholders, Sessions, and Protocols were preserved."
            )
        )

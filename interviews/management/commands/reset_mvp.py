from django.core.management.base import BaseCommand

from interviews.models import AgentDecision, InterviewSession, Message, Stakeholder, Summary, ReviewDecision


class Command(BaseCommand):
    help = "Reset PurrStone MVP test session."

    def handle(self, *args, **options):
        session = InterviewSession.objects.get(session_code="IS-01")

        Message.objects.filter(session=session).delete()
        Summary.objects.filter(session=session).delete()
        ReviewDecision.objects.filter(session=session).delete()
        AgentDecision.objects.filter(session=session).delete()

        session.status = InterviewSession.Status.NOT_STARTED
        session.current_section_index = 0
        session.consent_confirmed = False
        session.transcript_saved = False
        session.summary_generated = False
        session.review_status = InterviewSession.ReviewStatus.NOT_REVIEWED
        session.output_quality_status = InterviewSession.OutputQualityStatus.NOT_STARTED
        session.started_at = None
        session.completed_at = None
        session.save()

        session.stakeholder.status = Stakeholder.Status.READY
        session.stakeholder.save()

        self.stdout.write(self.style.SUCCESS("Reset IS-01 successfully."))
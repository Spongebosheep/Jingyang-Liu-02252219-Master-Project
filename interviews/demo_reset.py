from .models import (
    AgentDecision,
    InterviewSession,
    Message,
    ReviewDecision,
    Stakeholder,
    StructuredDigestItem,
    Summary,
)


def reset_interview_session(session):
    """Clear one Session's collected and reviewed data while preserving its link."""

    Message.objects.filter(session=session).delete()
    Summary.objects.filter(session=session).delete()
    ReviewDecision.objects.filter(session=session).delete()
    AgentDecision.objects.filter(session=session).delete()
    StructuredDigestItem.objects.filter(session=session).delete()

    session.status = InterviewSession.Status.NOT_STARTED
    session.current_section_index = 0
    session.consent_confirmed = False
    session.consent_notice_version = ""
    session.consent_snapshot = {}
    session.consent_snapshot_sha256 = ""
    session.consent_confirmed_at = None
    session.transcript_saved = False
    session.summary_generated = False
    session.review_status = InterviewSession.ReviewStatus.NOT_REVIEWED
    session.output_quality_status = InterviewSession.OutputQualityStatus.NOT_STARTED
    session.started_at = None
    session.completed_at = None
    session.researcher_note = ""
    session.save()

    session.stakeholder.status = Stakeholder.Status.READY
    session.stakeholder.save(update_fields=["status"])

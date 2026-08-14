"""Shared, read-only workflow status calculations for researcher pages.

The database fields on ``InterviewSession`` are the source of truth.  These
helpers turn them into consistent labels and CSS classes without storing a
second, potentially stale, copy of the overall workflow state.
"""

from .models import InterviewSession


def _status(label, css_class="status-muted"):
    return {"label": label, "css_class": css_class}


def decorate_session(session):
    """Attach the same display states to a session for every researcher page."""

    interview_labels = {
        InterviewSession.Status.NOT_STARTED: "Awaiting consent",
        InterviewSession.Status.READY: "Ready to begin",
    }
    interview_classes = {
        InterviewSession.Status.COMPLETED: "status",
        InterviewSession.Status.STOPPED: "status-waiting",
        InterviewSession.Status.IN_PROGRESS: "status-blue",
        InterviewSession.Status.READY: "status-blue",
        InterviewSession.Status.NOT_STARTED: "status-muted",
    }
    session.interview_display = _status(
        interview_labels.get(session.status, session.get_status_display()),
        interview_classes.get(session.status, "status-muted"),
    )

    if session.transcript_saved:
        session.output_display = _status("Saved", "status")
    elif session.summary_generated:
        session.output_display = _status("Generated", "status")
    else:
        session.output_display = _status("Not generated", "status-muted")

    review_classes = {
        InterviewSession.ReviewStatus.APPROVED: "status",
        InterviewSession.ReviewStatus.REVISION_REQUESTED: "status-waiting",
        InterviewSession.ReviewStatus.NOT_USABLE: "status-waiting",
        InterviewSession.ReviewStatus.NEEDS_REVIEW: "status-blue",
        InterviewSession.ReviewStatus.NOT_REVIEWED: "status-muted",
    }
    session.review_display = _status(
        session.get_review_status_display(),
        review_classes.get(session.review_status, "status-muted"),
    )

    evidence_classes = {
        InterviewSession.OutputQualityStatus.APPROVED: "status",
        InterviewSession.OutputQualityStatus.REVISION_REQUESTED: "status-waiting",
        InterviewSession.OutputQualityStatus.NOT_USABLE: "status-waiting",
        InterviewSession.OutputQualityStatus.NEEDS_REVIEW: "status-blue",
        InterviewSession.OutputQualityStatus.SUMMARY_GENERATED: "status-blue",
        InterviewSession.OutputQualityStatus.WAITING: "status-blue",
        InterviewSession.OutputQualityStatus.NOT_STARTED: "status-muted",
    }
    session.evidence_display = _status(
        session.get_output_quality_status_display(),
        evidence_classes.get(session.output_quality_status, "status-muted"),
    )

    session.needs_attention = (
        session.review_status
        in {
            InterviewSession.ReviewStatus.REVISION_REQUESTED,
            InterviewSession.ReviewStatus.NOT_USABLE,
        }
        or session.output_quality_status
        in {
            InterviewSession.OutputQualityStatus.REVISION_REQUESTED,
            InterviewSession.OutputQualityStatus.NOT_USABLE,
        }
    )
    session.awaiting_review = (
        session.transcript_saved
        and session.review_status
        in {
            InterviewSession.ReviewStatus.NOT_REVIEWED,
            InterviewSession.ReviewStatus.NEEDS_REVIEW,
        }
    )
    session.evidence_approved = (
        session.output_quality_status
        == InterviewSession.OutputQualityStatus.APPROVED
    )
    return session


def stakeholder_workflow_display(sessions):
    """Return one aggregate record status derived from all of its sessions."""

    sessions = list(sessions)
    if not sessions:
        return _status("No session", "status-muted")

    if all(session.evidence_approved for session in sessions):
        return _status("Completed", "status")

    if all(
        session.status
        in {InterviewSession.Status.NOT_STARTED, InterviewSession.Status.READY}
        for session in sessions
    ) and not any(session.transcript_saved for session in sessions):
        return _status("Ready", "status-blue")

    if any(session.needs_attention for session in sessions):
        return _status("In progress", "status-waiting")

    return _status("In progress", "status-blue")


def decorate_stakeholder(stakeholder, sessions=None):
    """Attach ordered sessions and their aggregate workflow state."""

    if sessions is None:
        sessions = stakeholder.sessions.select_related("protocol").order_by(
            "-updated_at", "-id"
        )

    ordered_sessions = [decorate_session(session) for session in sessions]
    stakeholder.ordered_sessions = ordered_sessions
    stakeholder.session_count = len(ordered_sessions)
    stakeholder.latest_session = ordered_sessions[0] if ordered_sessions else None
    stakeholder.workflow_display = stakeholder_workflow_display(ordered_sessions)
    return stakeholder

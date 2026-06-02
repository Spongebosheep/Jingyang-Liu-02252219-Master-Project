from django.utils import timezone
from .models import InterviewSession, Message, Stakeholder
from .langgraph_agent import (
    handle_participant_reply_with_graph,
    skip_current_question_with_graph,
    stop_interview_with_graph,
)

def get_section(session, index=None):
    sections = session.protocol.sections or []
    section_index = session.current_section_index if index is None else index

    if 0 <= section_index < len(sections):
        return sections[section_index]

    return None


def get_section_label(section):
    if not section:
        return ""
    return section.get("label", "")


def create_agent_message_for_current_section(session):
    section = get_section(session)

    if not section:
        return None

    return Message.objects.create(
        session=session,
        sender=Message.Sender.AGENT,
        content=section.get("primary_question", "Thank you. Let us continue."),
        section=get_section_label(section),
        section_index=section.get("index", session.current_section_index),
    )


def ensure_initial_agent_message(session):
    if not session.messages.exists():
        return create_agent_message_for_current_section(session)

    return None


def complete_interview(session):
    sections = session.protocol.sections or []
    final_index = len(sections) - 1

    if final_index >= 0:
        final_section = sections[final_index]

        Message.objects.create(
            session=session,
            sender=Message.Sender.AGENT,
            content=final_section.get(
                "primary_question",
                "Thank you. I will now summarise what you shared for researcher review.",
            ),
            section=final_section.get("label", "Faithful summary"),
            section_index=final_index,
        )

        session.current_section_index = final_index

    session.status = InterviewSession.Status.COMPLETED
    session.transcript_saved = True
    session.summary_generated = True
    session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
    session.output_quality_status = InterviewSession.OutputQualityStatus.WAITING
    session.completed_at = timezone.now()
    session.save()

    session.stakeholder.status = Stakeholder.Status.COMPLETED
    session.stakeholder.save()


def stop_interview(session):
    return stop_interview_with_graph(session)

def advance_or_complete(session):
    sections = session.protocol.sections or []

    if not sections:
        return

    # Last section is Faithful summary, so the last participant-facing question is one before that.
    last_participant_question_index = max(0, len(sections) - 2)

    if session.current_section_index >= last_participant_question_index:
        complete_interview(session)
        return

    session.current_section_index += 1
    session.save()

    create_agent_message_for_current_section(session)


def handle_participant_reply(session, reply_text):
    return handle_participant_reply_with_graph(session, reply_text)


def skip_current_question(session):
    return skip_current_question_with_graph(session)
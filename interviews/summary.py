from .models import Message


RESEARCH_TOPIC_CODES = [
    "experience",
    "triggers_signs",
    "coping_support",
    "support_concept_reaction",
    "public_use_acceptability",
]


def build_transcript(session):
    messages = session.messages.all().order_by("created_at", "id")
    lines = []

    for message in messages:
        sender = message.get_sender_display() if hasattr(message, "get_sender_display") else message.sender
        section = message.section or "No section"

        lines.append({
            "sender": sender,
            "sender_raw": message.sender,
            "section": section,
            "section_index": message.section_index,
            "content": message.content,
            "created_at": message.created_at,
        })

    return lines


def build_topic_statuses(session):
    sections = session.protocol.sections or []
    topic_statuses = []

    for section in sections:
        code = section.get("code")

        if code not in RESEARCH_TOPIC_CODES:
            continue

        section_index = section.get("index")

        participant_messages = session.messages.filter(
            sender=Message.Sender.PARTICIPANT,
            section_index=section_index,
        )

        skipped = session.messages.filter(
            sender=Message.Sender.SYSTEM,
            section_index=section_index,
            content__icontains="skipped",
        ).exists()

        stopped_here = session.messages.filter(
            sender=Message.Sender.SYSTEM,
            section_index=section_index,
            content__icontains="stopped",
        ).exists()

        agent_asked = session.messages.filter(
            sender=Message.Sender.AGENT,
            section_index=section_index,
        ).exists()

        if participant_messages.exists():
            status = "answered"
            status_label = "Answered"
        elif skipped:
            status = "skipped"
            status_label = "Skipped"
        elif stopped_here:
            status = "stopped"
            status_label = "Stopped here"
        elif agent_asked:
            status = "reached"
            status_label = "Reached"
        else:
            status = "not_reached"
            status_label = "Not reached"

        topic_statuses.append({
            "code": code,
            "label": section.get("label"),
            "purpose": section.get("purpose"),
            "section_index": section_index,
            "status": status,
            "status_label": status_label,
            "participant_answers": [m.content for m in participant_messages.order_by("created_at", "id")],
        })

    return topic_statuses


def generate_structured_summary(session):
    topic_statuses = build_topic_statuses(session)

    summary_sections = []
    missing_information_flags = []

    for topic in topic_statuses:
        answers = topic["participant_answers"]

        if topic["status"] == "answered":
            summary_text = " ".join(answers)
        elif topic["status"] == "skipped":
            summary_text = "The participant skipped this topic."
            missing_information_flags.append(f"{topic['label']} was skipped.")
        elif topic["status"] == "stopped":
            summary_text = "The participant stopped the interview at this topic."
            missing_information_flags.append(f"The interview was stopped at {topic['label']}.")
        elif topic["status"] == "reached":
            summary_text = "The topic was reached, but no participant answer was recorded."
            missing_information_flags.append(f"{topic['label']} was reached but not answered.")
        else:
            summary_text = "This topic was not reached in the session."
            missing_information_flags.append(f"{topic['label']} was not reached.")

        summary_sections.append({
            "label": topic["label"],
            "status": topic["status"],
            "status_label": topic["status_label"],
            "summary": summary_text,
        })

    return {
        "summary_sections": summary_sections,
        "missing_information_flags": missing_information_flags,
    }
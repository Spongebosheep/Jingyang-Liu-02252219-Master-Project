from .models import AgentDecision, Message, StructuredDigestItem


SUMMARY_LABELS_BY_SECTION_CODE = {
    "experience": "Situation",
    "triggers_signs": "Main triggers",
    "coping_support": "Coping / support",
    "support_concept_reaction": "Support concept reaction",
    "public_use_acceptability": "Public-use concerns",
}

NON_RESEARCH_SECTION_CODES = {
    "opening",
    "faithful_summary",
    "researcher_handoff",
    "completion",
}


def protocol_review_sections(protocol):
    """Return research topics from the protocol version locked to the session."""

    review_sections = []
    for position, section in enumerate(protocol.sections or []):
        code = str(section.get("code") or "").strip().lower()
        label = str(section.get("label") or code.replace("_", " ")).strip()

        if code in NON_RESEARCH_SECTION_CODES or label.lower() in {
            "opening",
            "faithful summary",
            "researcher handoff",
            "completion",
        }:
            continue

        raw_index = section.get("index", position)
        try:
            section_index = int(raw_index)
        except (TypeError, ValueError):
            section_index = position

        review_sections.append(
            {
                "index": section_index,
                "code": code,
                "title": label or f"Section {section_index}",
                "summary_label": (
                    SUMMARY_LABELS_BY_SECTION_CODE.get(code)
                    or label
                    or f"Section {section_index}"
                ),
            }
        )

    return review_sections


def reviewer_display(user):
    if not user or not getattr(user, "is_authenticated", False):
        return "Not recorded"
    return user.get_full_name() or user.get_username()


def _clean_excerpt(text, limit=280):
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned

    shortened = cleaned[: limit + 1]
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(".,;: ") + "…"


def build_extractive_draft(source_messages):
    """Build a bounded draft using only verbatim participant excerpts."""

    excerpts = [
        _clean_excerpt(message.content)
        for message in source_messages
        if (message.content or "").strip()
    ]
    excerpts = [excerpt for excerpt in excerpts if excerpt]

    if not excerpts:
        return "No participant response was recorded for this topic."

    quoted = " ".join(f"“{excerpt}”" for excerpt in excerpts)
    return f"Typed response extract: {quoted}"


def _coverage_for_section(session, section_index, source_messages, decisions):
    system_messages = list(
        session.messages.filter(
            sender=Message.Sender.SYSTEM,
            section_index=section_index,
        ).order_by("created_at", "id")
    )
    system_text = " ".join(message.content.lower() for message in system_messages)
    final_decision = decisions[-1] if decisions else None
    missing_information = (
        list(final_decision.missing_information or []) if final_decision else []
    )
    topic_reached = bool(
        source_messages
        or decisions
        or system_messages
        or session.messages.filter(
            sender=Message.Sender.AGENT,
            section_index=section_index,
        ).exists()
    )

    if "skipped" in system_text or (
        final_decision and final_decision.action == AgentDecision.Action.SKIP
    ):
        return (
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
            StructuredDigestItem.ParticipantControl.SKIP,
            True,
            "The participant skipped this topic.",
            missing_information,
        )

    if "stopped" in system_text or (
        final_decision and final_decision.action == AgentDecision.Action.STOP
    ):
        return (
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
            StructuredDigestItem.ParticipantControl.STOP,
            True,
            "The participant stopped the interview before this topic was fully completed.",
            missing_information,
        )

    if source_messages:
        assessment_to_status = {
            AgentDecision.CoverageAssessment.COVERED: (
                StructuredDigestItem.CoverageStatus.COVERED
            ),
            AgentDecision.CoverageAssessment.PARTIALLY_COVERED: (
                StructuredDigestItem.CoverageStatus.PARTIALLY_COVERED
            ),
            AgentDecision.CoverageAssessment.UNCLEAR: (
                StructuredDigestItem.CoverageStatus.NOT_COVERED
            ),
            AgentDecision.CoverageAssessment.OFF_TOPIC: (
                StructuredDigestItem.CoverageStatus.NOT_COVERED
            ),
            AgentDecision.CoverageAssessment.NOT_ASSESSED: (
                StructuredDigestItem.CoverageStatus.NOT_ASSESSED
            ),
        }
        coverage_status = assessment_to_status.get(
            getattr(final_decision, "coverage_assessment", None),
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
        )
        return (
            coverage_status,
            StructuredDigestItem.ParticipantControl.NONE,
            True,
            build_extractive_draft(source_messages),
            missing_information,
        )

    return (
        StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
        StructuredDigestItem.ParticipantControl.NONE,
        topic_reached,
        (
            "This topic was reached, but no participant response was recorded."
            if topic_reached
            else "This topic was not reached in the Session."
        ),
        missing_information,
    )


def ensure_digest_items(session):
    """Persist one immutable draft per research section and return it in order."""

    if not session.transcript_saved:
        return []

    boundary_message_ids = set(
        session.agent_decisions.filter(
            action=AgentDecision.Action.BOUNDARY_RESPONSE
        ).values_list("message_id", flat=True)
    )
    participant_messages = list(
        session.messages.filter(sender=Message.Sender.PARTICIPANT).order_by(
            "created_at", "id"
        )
    )
    participant_messages = [
        message
        for message in participant_messages
        if message.id not in boundary_message_ids
    ]
    messages_by_section = {}
    for message in participant_messages:
        messages_by_section.setdefault(message.section_index, []).append(message)

    decisions_by_section = {}
    for decision in session.agent_decisions.order_by("created_at", "id"):
        decisions_by_section.setdefault(decision.section_index, []).append(decision)

    for section in protocol_review_sections(session.protocol):
        section_index = section["index"]
        sources = messages_by_section.get(section_index, [])
        (
            coverage_status,
            participant_control,
            topic_reached,
            generated_text,
            missing_information,
        ) = _coverage_for_section(
            session,
            section_index,
            sources,
            decisions_by_section.get(section_index, []),
        )

        item, created = StructuredDigestItem.objects.get_or_create(
            session=session,
            section_index=section_index,
            defaults={
                "section_code": section["code"],
                "section_title": section["title"],
                "label": section["summary_label"],
                "coverage_status": coverage_status,
                "participant_control": participant_control,
                "topic_reached": topic_reached,
                "missing_information": missing_information,
                "generated_text": generated_text,
            },
        )
        if created:
            item.source_messages.set(sources)

    return list(
        StructuredDigestItem.objects.filter(session=session)
        .select_related("reviewed_by")
        .prefetch_related("source_messages", "review_events__reviewer")
        .order_by("section_index", "id")
    )


def digest_review_summary(items):
    items = list(items)
    evidence_candidates = [item for item in items if item.is_evidence_candidate]
    limitations = [item for item in items if not item.is_evidence_candidate]
    resolved = [
        item
        for item in evidence_candidates
        if item.review_status != StructuredDigestItem.ReviewStatus.PENDING
    ]
    included = [item for item in evidence_candidates if item.is_included]
    excluded = [
        item
        for item in evidence_candidates
        if item.review_status == StructuredDigestItem.ReviewStatus.EXCLUDED
    ]
    pending = [
        item
        for item in evidence_candidates
        if item.review_status == StructuredDigestItem.ReviewStatus.PENDING
    ]
    return {
        "total": len(items),
        "reviewable_total": len(evidence_candidates),
        "limitation_total": len(limitations),
        "resolved": len(resolved),
        "included": len(included),
        "excluded": len(excluded),
        "pending": len(pending),
        "ready_for_overall_approval": bool(evidence_candidates)
        and len(resolved) == len(evidence_candidates)
        and bool(included),
    }


def build_topic_coverage_records(items):
    """Return exporter-ready coverage and review state for every Protocol topic."""

    return [
        {
            "item_id": item.id,
            "session_id": item.session_id,
            "section_index": item.section_index,
            "section_code": item.section_code,
            "coverage_status": item.coverage_status,
            "participant_control": item.participant_control,
            "topic_reached": item.topic_reached,
            "missing_information": list(item.missing_information or []),
            "is_evidence_candidate": item.is_evidence_candidate,
            "review_status": item.review_status,
            "display_visible": True,
            "export_visible": True,
        }
        for item in items
    ]

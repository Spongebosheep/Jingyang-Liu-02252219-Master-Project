"""Project-specific release conditions for one reviewed interview Session.

These conditions protect the interview-to-review handoff. They are workflow
controls, not a validated research-quality scale.
"""

from .digest import protocol_review_sections
from .models import AgentDecision, Message, StructuredDigestItem


def validate_digest_item_sources(session, item):
    """Return export-ready same-Session, sender, and same-topic validation."""

    source_messages = list(item.source_messages.order_by("created_at", "id"))
    reasons = []
    if not source_messages:
        reasons.append(
            {
                "code": "no_source_message",
                "source_message_id": None,
                "detail": "The included item has no transcript source message.",
            }
        )

    for source in source_messages:
        if source.session_id != session.id:
            reasons.append(
                {
                    "code": "cross_session_source",
                    "source_message_id": source.id,
                    "detail": "The source message belongs to a different Session.",
                }
            )
        if source.sender != Message.Sender.PARTICIPANT:
            reasons.append(
                {
                    "code": "non_participant_source",
                    "source_message_id": source.id,
                    "detail": "The source message was not typed by the participant.",
                }
            )
        if source.section_index != item.section_index:
            reasons.append(
                {
                    "code": "cross_topic_source",
                    "source_message_id": source.id,
                    "detail": "The source message belongs to a different Protocol topic.",
                }
            )

    return {
        "item_id": item.id,
        "session_id": session.id,
        "section_index": item.section_index,
        "source_message_ids": [source.id for source in source_messages],
        "source_texts": [source.content for source in source_messages],
        "valid": not reasons,
        "reasons": reasons,
    }


def validate_included_source_links(session, digest_items):
    included_items = [item for item in digest_items if item.is_included]
    item_results = [
        validate_digest_item_sources(session, item) for item in included_items
    ]
    return {
        "session_id": session.id,
        "included_item_count": len(included_items),
        "valid_item_count": sum(result["valid"] for result in item_results),
        "valid": bool(included_items) and all(
            result["valid"] for result in item_results
        ),
        "reasons": (
            []
            if included_items
            else [
                {
                    "code": "no_included_items",
                    "detail": "No reviewed participant extract is included.",
                }
            ]
        ),
        "items": item_results,
    }


def _included_extracts_have_valid_sources(session, digest_items):
    """Compatibility boolean used by the release-condition gate."""

    return validate_included_source_links(session, digest_items)["valid"]


def _participant_controls_were_followed(session):
    """Check that Skip ends its topic and Stop ends all interview decisions."""

    decisions = list(session.agent_decisions.order_by("created_at", "id"))
    for position, decision in enumerate(decisions):
        later_decisions = decisions[position + 1 :]
        if decision.action == AgentDecision.Action.STOP and later_decisions:
            return False
        if decision.action == AgentDecision.Action.SKIP and any(
            later.section_index == decision.section_index
            for later in later_decisions
        ):
            return False
    return True


def _limitations_are_represented(session, digest_items):
    """Check that every research topic retains an explicit coverage record."""

    expected_indexes = {
        section["index"] for section in protocol_review_sections(session.protocol)
    }
    items_by_index = {item.section_index: item for item in digest_items}
    if not expected_indexes or set(items_by_index) != expected_indexes:
        return False

    for item in items_by_index.values():
        if not (item.generated_text or "").strip():
            return False
        if (
            item.coverage_status
            in {
                StructuredDigestItem.CoverageStatus.PARTIALLY_COVERED,
                StructuredDigestItem.CoverageStatus.NOT_COVERED,
            }
            and not item.missing_information
        ):
            return False
    return True


def evaluate_system_release_conditions(session, digest_items):
    """Return the three machine-verifiable workflow conditions for a Session."""

    digest_items = list(digest_items)
    source_validation = validate_included_source_links(session, digest_items)
    return [
        {
            "field": "source_links_checked",
            "label": "Included extracts have valid transcript source links",
            "help": (
                "Every Included or Edited extract links only to typed participant "
                "responses from the same Session and Protocol topic."
            ),
            "met": source_validation["valid"],
            "details": source_validation,
        },
        {
            "field": "participant_controls_respected",
            "label": "Skip and Stop controls were followed",
            "help": (
                "No later decision continued the skipped topic, and no interview "
                "decision followed a Stop action."
            ),
            "met": _participant_controls_were_followed(session),
        },
        {
            "field": "limitations_and_missing_information_visible",
            "label": "Coverage limitations remain represented",
            "help": (
                "Every research topic has an explicit coverage record; partial "
                "topics retain their missing-information fields."
            ),
            "met": _limitations_are_represented(session, digest_items),
        },
    ]


def build_release_conditions(session, digest_items, review_decision=None):
    """Combine system conditions with the two non-automatable judgements."""

    system_conditions = evaluate_system_release_conditions(session, digest_items)
    researcher_judgements = [
        {
            "field": "participant_meaning_preserved",
            "label": "Included extracts preserve the participant's meaning",
            "help": (
                "Confirm that selection, shortening, or editing has not added a "
                "claim or changed the participant's intended meaning."
            ),
            "confirmed": bool(
                review_decision and review_decision.participant_meaning_preserved
            ),
        },
        {
            "field": "protocol_boundaries_respected",
            "label": "Protocol-specific interaction boundaries were respected",
            "help": (
                "Check the transcript against the interaction boundaries saved in "
                "this locked Protocol version."
            ),
            "confirmed": bool(
                review_decision and review_decision.protocol_boundaries_respected
            ),
        },
    ]
    return {
        "system_conditions": system_conditions,
        "researcher_judgements": researcher_judgements,
        "system_conditions_met": all(
            condition["met"] for condition in system_conditions
        ),
        "researcher_judgements_confirmed": all(
            judgement["confirmed"] for judgement in researcher_judgements
        ),
    }


def save_system_release_conditions(review_decision, release_conditions):
    """Persist the current system results alongside an overall review decision."""

    results_by_field = {
        condition["field"]: condition["met"]
        for condition in release_conditions["system_conditions"]
    }
    review_decision.source_links_checked = results_by_field["source_links_checked"]
    review_decision.participant_controls_respected = results_by_field[
        "participant_controls_respected"
    ]
    review_decision.limitations_and_missing_information_visible = results_by_field[
        "limitations_and_missing_information_visible"
    ]

from .models import StructuredDigestItem


def _timestamp(value):
    return value.isoformat() if value else None


def build_review_audit_record(item: StructuredDigestItem) -> dict:
    """Return one field-checkable researcher review audit record."""

    source_messages = list(item.source_messages.order_by("created_at", "id"))
    events = list(item.review_events.order_by("created_at", "id"))
    event_rows = [
        {
            "event_id": event.id,
            "previous_status": event.previous_status,
            "new_status": event.new_status,
            "previous_text": event.previous_text,
            "new_text": event.new_text,
            "comment": event.comment,
            "reviewer": event.reviewer_name_snapshot,
            "created_at": _timestamp(event.created_at),
        }
        for event in events
    ]

    latest_event = events[-1] if events else None
    current_event = (
        latest_event
        if latest_event and latest_event.new_status == item.review_status
        else None
    )
    checks = {
        "review_resolved": item.review_status
        != StructuredDigestItem.ReviewStatus.PENDING,
        "generated_text_recorded": bool((item.generated_text or "").strip()),
        "source_links_recorded": bool(source_messages),
        "reviewer_identity_recorded": bool(
            (item.reviewer_name_snapshot or "").strip()
        ),
        "review_timestamp_recorded": item.reviewed_at is not None,
        "matching_review_event_recorded": current_event is not None,
        "event_attribution_complete": bool(
            current_event
            and (current_event.reviewer_name_snapshot or "").strip()
            and current_event.created_at
        ),
    }

    if item.review_status == StructuredDigestItem.ReviewStatus.APPROVED:
        checks.update(
            {
                "source_text_preserved": item.final_text == item.generated_text,
                "included_in_final_evidence": bool(item.evidence_text),
                "latest_event_matches_final_text": bool(
                    current_event
                    and current_event.new_text == item.generated_text
                ),
            }
        )
    elif item.review_status == StructuredDigestItem.ReviewStatus.EDITED:
        checks.update(
            {
                "edited_text_recorded": bool((item.reviewed_text or "").strip()),
                "edit_reason_recorded": bool((item.reviewer_comment or "").strip()),
                "final_uses_edited_text": item.final_text == item.reviewed_text,
                "included_in_final_evidence": bool(item.evidence_text),
                "before_after_event_recorded": bool(
                    current_event
                    and current_event.previous_text != current_event.new_text
                    and current_event.new_text == item.reviewed_text
                    and (current_event.comment or "").strip()
                ),
            }
        )
    elif item.review_status == StructuredDigestItem.ReviewStatus.EXCLUDED:
        checks.update(
            {
                "exclusion_reason_recorded": bool(
                    (item.reviewer_comment or "").strip()
                ),
                "excluded_from_final_evidence": not bool(item.evidence_text),
                "exclusion_event_retained": bool(
                    current_event
                    and not current_event.new_text
                    and (current_event.comment or "").strip()
                ),
            }
        )

    completed_field_count = sum(bool(result) for result in checks.values())
    required_field_count = len(checks)
    return {
        "item_id": item.id,
        "session_id": item.session_id,
        "section_index": item.section_index,
        "section_code": item.section_code,
        "review_status": item.review_status,
        "generated_text": item.generated_text,
        "reviewed_text": item.reviewed_text,
        "final_text": item.final_text,
        "evidence_text": item.evidence_text,
        "reason": item.reviewer_comment,
        "source_message_ids": [source.id for source in source_messages],
        "source_texts": [source.content for source in source_messages],
        "reviewer": item.reviewer_display,
        "reviewed_at": _timestamp(item.reviewed_at),
        "included_in_final_evidence": item.is_included,
        "events": event_rows,
        "field_checks": checks,
        "completed_field_count": completed_field_count,
        "required_field_count": required_field_count,
        "audit_complete": required_field_count > 0
        and completed_field_count == required_field_count,
    }


def build_review_audit_records(items) -> list:
    return [build_review_audit_record(item) for item in items]

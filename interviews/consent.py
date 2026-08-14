"""Canonical participant-facing Consent notice and integrity record.

The template renders this structured notice and the Session stores the same
structure.  This keeps the participant-facing wording, Protocol-specific
details, timestamp, version, and SHA-256 in one auditable record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import Protocol


CONSENT_SNAPSHOT_SCHEMA_VERSION = "1.0"
CONSENT_NOTICE_VERSION = "purrstone-ai-transparency-v1"


def _research_topic_labels(protocol: Protocol) -> list[str]:
    boundary_codes = {
        "opening",
        "completion",
        "faithful_summary",
        "researcher_handoff",
    }
    labels: list[str] = []
    for section in protocol.sections or []:
        code = str(section.get("code") or "").strip().lower()
        if code in boundary_codes:
            continue
        label = str(section.get("label") or code.replace("_", " ")).strip()
        if label:
            labels.append(label)
    return labels


def build_consent_snapshot(protocol: Protocol) -> dict[str, Any]:
    """Return exactly the structured notice shown for this Protocol version."""

    return {
        "schema_version": CONSENT_SNAPSHOT_SCHEMA_VERSION,
        "notice_version": CONSENT_NOTICE_VERSION,
        "protocol": {
            "family_id": str(protocol.family_id),
            "version": protocol.version,
            "title": protocol.title,
            "purpose": protocol.purpose,
            "stakeholder_group": protocol.stakeholder_group,
            "interview_mode": protocol.interview_mode,
            "estimated_duration": protocol.estimated_duration,
            "output_description": protocol.output_description,
            "research_topics": _research_topic_labels(protocol),
        },
        "ai_role": {
            "heading": "How the AI-supported interview works",
            "checks": (
                "The system makes a provisional check of whether your responses "
                "explicitly cover information that the researcher defined for each "
                "Protocol topic."
            ),
            "follow_up": (
                "It may generate and ask up to one follow-up question for each "
                "interview topic."
            ),
            "does_not_check": (
                "It does not assess whether your account is true, interpret its "
                "psychological meaning, determine clinical significance, or judge its "
                "research value."
            ),
            "workflow_effect": (
                "The provisional coverage check may determine whether the interview "
                "moves to the next topic or asks that one follow-up."
            ),
            "researcher_review": (
                "Any material included in the final Evidence Record must be reviewed "
                "and approved by a researcher."
            ),
        },
        "study_boundary": (
            "This interview is for design research only. It does not provide medical "
            "advice, diagnosis, treatment recommendations, or a research conclusion."
        ),
        "participant_rights": [
            "You can skip any question.",
            "You can stop the interview at any time.",
            (
                "Please do not share sensitive personal information that you are not "
                "comfortable having saved in the typed transcript."
            ),
            "Your typed responses will be saved for researcher review.",
        ],
        "acknowledgement": (
            "I understand the purpose, AI-supported process, limits, participant "
            "controls, and researcher-review boundary described above, and I want to "
            "continue."
        ),
    }


def canonical_consent_snapshot_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_consent_snapshot_record(protocol: Protocol) -> dict[str, Any]:
    snapshot = build_consent_snapshot(protocol)
    canonical_json = canonical_consent_snapshot_json(snapshot)
    return {
        "snapshot": snapshot,
        "canonical_json": canonical_json,
        "sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        "notice_version": CONSENT_NOTICE_VERSION,
    }


def consent_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    canonical_json = canonical_consent_snapshot_json(snapshot)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

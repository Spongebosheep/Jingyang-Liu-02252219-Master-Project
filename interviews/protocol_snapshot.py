import hashlib
import json

from .langgraph_agent import MAX_PROBES_PER_SECTION
from .models import AgentDecision, Protocol


PROTOCOL_SNAPSHOT_SCHEMA_VERSION = 1


def build_protocol_snapshot(protocol: Protocol) -> dict:
    """Return the canonical locked Protocol and runtime configuration for a run."""

    return {
        "schema_version": PROTOCOL_SNAPSHOT_SCHEMA_VERSION,
        "identity": {
            "family_id": str(protocol.family_id),
            "version": protocol.version,
            "parent_version": (
                protocol.parent_version.version if protocol.parent_version_id else None
            ),
            "status": protocol.status,
            "locked_at": (
                protocol.locked_at.isoformat() if protocol.locked_at else None
            ),
        },
        "configuration": {
            "title": protocol.title,
            "stakeholder_group": protocol.stakeholder_group,
            "purpose": protocol.purpose,
            "interview_mode": protocol.interview_mode,
            "estimated_duration": protocol.estimated_duration,
            "output_description": protocol.output_description,
            "sections": protocol.sections or [],
            "ethics_rules": protocol.ethics_rules or [],
        },
        "runtime": {
            "max_probes_per_section": MAX_PROBES_PER_SECTION,
            "final_section_is_non_research_handoff": True,
            "recorded_actions": [value for value, _label in AgentDecision.Action.choices],
        },
    }


def canonical_protocol_snapshot_json(snapshot: dict) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_protocol_snapshot_record(protocol: Protocol) -> dict:
    """Return a snapshot plus a reproducible SHA-256 integrity identifier."""

    snapshot = build_protocol_snapshot(protocol)
    canonical_json = canonical_protocol_snapshot_json(snapshot)
    return {
        "snapshot": snapshot,
        "canonical_json": canonical_json,
        "sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    }

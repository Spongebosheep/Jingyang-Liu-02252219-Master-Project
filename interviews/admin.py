from django.contrib import admin

from .models import (
    Protocol,
    Stakeholder,
    InterviewSession,
    Message,
    Summary,
    ReviewDecision,
    AgentDecision,
    StructuredDigestItem,
    DigestReviewEvent,
)


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "version",
        "status",
        "stakeholder_group",
        "interview_mode",
        "estimated_duration",
    )
    search_fields = ("title", "stakeholder_group")
    list_filter = ("status",)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_locked:
            return False
        return super().has_change_permission(request, obj)


@admin.register(Stakeholder)
class StakeholderAdmin(admin.ModelAdmin):
    list_display = ("participant_id", "stakeholder_group", "role", "assigned_protocol", "status")
    search_fields = ("participant_id", "stakeholder_group", "role")
    list_filter = ("stakeholder_group", "status")


@admin.register(InterviewSession)
class InterviewSessionAdmin(admin.ModelAdmin):
    list_display = (
        "session_code",
        "stakeholder",
        "protocol",
        "status",
        "consent_confirmed",
        "consent_confirmed_at",
        "summary_generated",
        "review_status",
        "output_quality_status",
    )
    list_filter = ("status", "review_status", "output_quality_status")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("session", "sender", "section", "created_at")
    search_fields = ("content",)
    list_filter = ("sender", "section")


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ("session", "edited_by_researcher", "created_at")


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "decision",
        "source_links_checked",
        "participant_meaning_preserved",
        "protocol_boundaries_respected",
        "participant_controls_respected",
        "limitations_and_missing_information_visible",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("decision",)


@admin.register(StructuredDigestItem)
class StructuredDigestItemAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "section_title",
        "coverage_status",
        "participant_control",
        "topic_reached",
        "review_status",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("coverage_status", "participant_control", "topic_reached", "review_status")
    search_fields = ("session__session_code", "section_title", "generated_text")
    filter_horizontal = ("source_messages",)


@admin.register(DigestReviewEvent)
class DigestReviewEventAdmin(admin.ModelAdmin):
    list_display = (
        "digest_item",
        "new_status",
        "reviewer_name_snapshot",
        "created_at",
    )
    list_filter = ("new_status",)
    readonly_fields = (
        "digest_item",
        "previous_status",
        "new_status",
        "previous_text",
        "new_text",
        "comment",
        "reviewer",
        "reviewer_name_snapshot",
        "created_at",
    )

@admin.register(AgentDecision)
class AgentDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "section",
        "section_index",
        "coverage_assessment",
        "participant_control",
        "action",
        "probe_count_before",
        "created_at",
    )
    list_filter = ("action", "coverage_assessment", "participant_control", "section")
    search_fields = ("session__session_code", "section", "decision_reason")
    readonly_fields = (
        "session",
        "message",
        "section",
        "section_index",
        "coverage_assessment",
        "participant_control",
        "action",
        "probe_count_before",
        "missing_information",
        "decision_reason",
        "created_at",
    )

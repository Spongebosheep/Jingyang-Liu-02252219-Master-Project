from django.contrib import admin

from .models import (
    Protocol,
    Stakeholder,
    InterviewSession,
    Message,
    Summary,
    ReviewDecision,
    AgentDecision,
)


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ("title", "stakeholder_group", "interview_mode", "estimated_duration")
    search_fields = ("title", "stakeholder_group")


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
        "summary_grounded_in_transcript",
        "no_unsupported_interpretation",
        "no_medical_or_diagnostic_advice",
        "participant_safety_respected",
        "limitations_and_missing_information_visible"
    )
    list_filter = ("decision",)

@admin.register(AgentDecision)
class AgentDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "section",
        "section_index",
        "answer_status",
        "action",
        "probe_count_before",
        "created_at",
    )
    list_filter = ("action", "answer_status", "section")
    search_fields = ("session__session_code", "section", "decision_reason")
    readonly_fields = (
        "session",
        "message",
        "section",
        "section_index",
        "answer_status",
        "action",
        "probe_count_before",
        "missing_information",
        "decision_reason",
        "created_at",
    )
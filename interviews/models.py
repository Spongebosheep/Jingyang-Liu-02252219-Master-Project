import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Protocol(models.Model):
    IMMUTABLE_AFTER_LOCK_FIELDS = (
        "title",
        "slug",
        "stakeholder_group",
        "purpose",
        "interview_mode",
        "estimated_duration",
        "output_description",
        "sections",
        "ethics_rules",
        "family_id",
        "version",
        "parent_version_id",
    )

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        LOCKED = "locked", "Locked"

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    stakeholder_group = models.CharField(max_length=100)
    purpose = models.TextField()
    interview_mode = models.CharField(max_length=100, default="AI-led semi-structured interview")
    estimated_duration = models.CharField(max_length=50, default="10–15 minutes")
    output_description = models.CharField(
        max_length=200,
        default="Typed transcript + reviewed extracts by protocol topic",
    )

    # Store protocol sections and ethics rules as structured JSON.
    sections = models.JSONField(default=list, blank=True)
    ethics_rules = models.JSONField(default=list, blank=True)

    family_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    parent_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_versions",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["family_id", "version"],
                name="unique_protocol_family_version",
            )
        ]

    @property
    def is_locked(self):
        return self.status == self.Status.LOCKED or self.sessions.exists()

    def _validate_locked_immutability(self):
        """Reject changes to a Protocol version after it has been locked for use."""

        if not self.pk:
            return

        stored = type(self).objects.filter(pk=self.pk).first()
        if stored is None:
            return

        has_sessions = type(self).objects.filter(
            pk=self.pk,
            sessions__isnull=False,
        ).exists()
        if stored.status != self.Status.LOCKED and not has_sessions:
            return

        changed_fields = [
            field_name
            for field_name in self.IMMUTABLE_AFTER_LOCK_FIELDS
            if getattr(self, field_name) != getattr(stored, field_name)
        ]

        if stored.status == self.Status.LOCKED:
            if self.status != stored.status:
                changed_fields.append("status")
            if self.locked_at != stored.locked_at:
                changed_fields.append("locked_at")

        if changed_fields:
            field_list = ", ".join(sorted(set(changed_fields)))
            raise ValidationError(
                "A locked Protocol version is immutable. Copy it to create a new "
                f"draft version. Changed fields: {field_list}."
            )

    def save(self, *args, **kwargs):
        self._validate_locked_immutability()
        return super().save(*args, **kwargs)

    def lock_for_use(self):
        if self.status != self.Status.LOCKED:
            self.status = self.Status.LOCKED
            self.locked_at = timezone.now()
            self.save(update_fields=["status", "locked_at", "updated_at"])

    def __str__(self):
        return f"{self.title} (v{self.version})"


class Stakeholder(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        NOT_STARTED = "not_started", "Not started"

    participant_id = models.CharField(max_length=20, unique=True)
    stakeholder_group = models.CharField(max_length=100)
    role = models.CharField(max_length=100)
    assigned_protocol = models.ForeignKey(
        Protocol,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stakeholders",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.READY,
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.participant_id


class InterviewSession(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        READY = "ready", "Ready"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        STOPPED = "stopped", "Stopped"

    class ReviewStatus(models.TextChoices):
        NOT_REVIEWED = "not_reviewed", "Not reviewed"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"
        REVISION_REQUESTED = "revision_requested", "Revision requested"
        NOT_USABLE = "not_usable", "Not usable"

    class OutputQualityStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        WAITING = "waiting", "Waiting"
        SUMMARY_GENERATED = "summary_generated", "Summary generated"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"
        REVISION_REQUESTED = "revision_requested", "Revision requested"
        NOT_USABLE = "not_usable", "Not usable"

    session_code = models.CharField(max_length=30, unique=True)
    access_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stakeholder = models.ForeignKey(
        Stakeholder,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    protocol = models.ForeignKey(
        Protocol,
        on_delete=models.PROTECT,
        related_name="sessions",
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.NOT_STARTED,
    )

    current_section_index = models.PositiveIntegerField(default=0)
    consent_confirmed = models.BooleanField(default=False)
    consent_notice_version = models.CharField(max_length=80, blank=True)
    consent_snapshot = models.JSONField(default=dict, blank=True)
    consent_snapshot_sha256 = models.CharField(max_length=64, blank=True)
    consent_confirmed_at = models.DateTimeField(null=True, blank=True)

    transcript_saved = models.BooleanField(default=False)
    summary_generated = models.BooleanField(default=False)

    review_status = models.CharField(
        max_length=30,
        choices=ReviewStatus.choices,
        default=ReviewStatus.NOT_REVIEWED,
    )

    output_quality_status = models.CharField(
        max_length=30,
        choices=OutputQualityStatus.choices,
        default=OutputQualityStatus.NOT_STARTED,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    researcher_note = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.protocol_id:
            self.protocol.lock_for_use()

    def current_section(self):
        sections = self.protocol.sections or []
        if 0 <= self.current_section_index < len(sections):
            return sections[self.current_section_index]
        return None

    @property
    def consent_snapshot_is_valid(self):
        if not self.consent_confirmed or not self.consent_snapshot:
            return False
        from .consent import consent_snapshot_sha256

        return self.consent_snapshot_sha256 == consent_snapshot_sha256(
            self.consent_snapshot
        )

    @property
    def participant_link_status(self):
        if self.status in [self.Status.COMPLETED, self.Status.STOPPED]:
            return "completed"
        return "active"

    @property
    def participant_link_display(self):
        if self.participant_link_status == "completed":
            return {
                "label": "Interview completed",
                "css_class": "status",
            }
        return {
            "label": "Active",
            "css_class": "status-blue",
        }

    def __str__(self):
        return self.session_code


class Message(models.Model):
    class Sender(models.TextChoices):
        AGENT = "agent", "Agent"
        PARTICIPANT = "participant", "Participant"
        SYSTEM = "system", "System"

    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.CharField(max_length=20, choices=Sender.choices)
    content = models.TextField()
    section = models.CharField(max_length=100, blank=True)
    section_index = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.session.session_code} - {self.sender}"


class Summary(models.Model):
    session = models.OneToOneField(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="summary",
    )

    situation = models.TextField(blank=True)
    main_triggers = models.TextField(blank=True)
    participant_response = models.TextField(blank=True)
    potential_support_need = models.TextField(blank=True)
    support_concept_reaction = models.TextField(blank=True)
    public_use_concerns = models.TextField(blank=True)
    missing_information_flags = models.TextField(blank=True)

    edited_by_researcher = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Summary for {self.session.session_code}"


class ReviewDecision(models.Model):
    class Decision(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REVISION_REQUESTED = "revision_requested", "Revision requested"
        NOT_USABLE = "not_usable", "Not usable"

    session = models.OneToOneField(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="review_decision",
    )

    # System-checked workflow-condition snapshots saved with the overall decision.
    source_links_checked = models.BooleanField(default=False)
    participant_controls_respected = models.BooleanField(default=False)
    limitations_and_missing_information_visible = models.BooleanField(default=False)

    # Non-automatable judgements explicitly confirmed by the researcher.
    participant_meaning_preserved = models.BooleanField(default=False)
    protocol_boundaries_respected = models.BooleanField(default=False)

    decision = models.CharField(
        max_length=30,
        choices=Decision.choices,
        default=Decision.PENDING,
    )
    reviewer_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purrstone_review_decisions",
    )
    reviewer_name_snapshot = models.CharField(max_length=200, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Review for {self.session.session_code}"

    @property
    def reviewer_display(self):
        if self.reviewer_name_snapshot:
            return self.reviewer_name_snapshot
        if self.reviewed_by:
            return self.reviewed_by.get_full_name() or self.reviewed_by.get_username()
        return "Not recorded"


class StructuredDigestItem(models.Model):
    """One protocol-topic evidence item with immutable source grounding."""

    class CoverageStatus(models.TextChoices):
        COVERED = "covered", "Covered"
        PARTIALLY_COVERED = "partially_covered", "Partially covered"
        NOT_COVERED = "not_covered", "Not covered"
        NOT_ASSESSED = "not_assessed", "Not assessed"

    class ParticipantControl(models.TextChoices):
        NONE = "none", "None"
        SKIP = "skip", "Skip"
        STOP = "stop", "Stop"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        EDITED = "edited", "Edited"
        EXCLUDED = "excluded", "Excluded"

    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="digest_items",
    )
    section_index = models.PositiveIntegerField()
    section_code = models.CharField(max_length=100, blank=True)
    section_title = models.CharField(max_length=200)
    label = models.CharField(max_length=200)
    coverage_status = models.CharField(
        max_length=30,
        choices=CoverageStatus.choices,
        default=CoverageStatus.NOT_ASSESSED,
    )
    participant_control = models.CharField(
        max_length=20,
        choices=ParticipantControl.choices,
        default=ParticipantControl.NONE,
    )
    topic_reached = models.BooleanField(default=False)
    missing_information = models.JSONField(default=list, blank=True)
    generated_text = models.TextField()
    generation_method = models.CharField(
        max_length=80,
        default="deterministic_extractive_v1",
        editable=False,
    )
    source_messages = models.ManyToManyField(
        Message,
        related_name="structured_digest_items",
        blank=True,
    )
    review_status = models.CharField(
        max_length=30,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    reviewed_text = models.TextField(blank=True)
    reviewer_comment = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purrstone_digest_items",
    )
    reviewer_name_snapshot = models.CharField(max_length=200, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section_index", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "section_index"],
                name="unique_digest_item_per_session_section",
            )
        ]

    @property
    def final_text(self):
        if self.review_status == self.ReviewStatus.EDITED and self.reviewed_text:
            return self.reviewed_text
        return self.generated_text

    @property
    def evidence_text(self):
        if self.is_evidence_candidate and self.review_status in {
            self.ReviewStatus.APPROVED,
            self.ReviewStatus.EDITED,
        }:
            return self.final_text
        return ""

    @property
    def is_resolved(self):
        return (
            not self.is_evidence_candidate
            or self.review_status != self.ReviewStatus.PENDING
        )

    @property
    def is_evidence_candidate(self):
        return self.coverage_status in {
            self.CoverageStatus.COVERED,
            self.CoverageStatus.PARTIALLY_COVERED,
        }

    @property
    def is_included(self):
        return self.is_evidence_candidate and self.review_status in {
            self.ReviewStatus.APPROVED,
            self.ReviewStatus.EDITED,
        }

    @property
    def reviewer_display(self):
        if self.reviewer_name_snapshot:
            return self.reviewer_name_snapshot
        if self.reviewed_by:
            return self.reviewed_by.get_full_name() or self.reviewed_by.get_username()
        return "Not reviewed"

    def __str__(self):
        return f"{self.session.session_code} - {self.label}"


class DigestReviewEvent(models.Model):
    """Append-only audit event for an item-level researcher decision."""

    digest_item = models.ForeignKey(
        StructuredDigestItem,
        on_delete=models.CASCADE,
        related_name="review_events",
    )
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(
        max_length=30,
        choices=StructuredDigestItem.ReviewStatus.choices,
    )
    previous_text = models.TextField(blank=True)
    new_text = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purrstone_digest_review_events",
    )
    reviewer_name_snapshot = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.digest_item} - {self.get_new_status_display()}"

class AgentDecision(models.Model):
    class Action(models.TextChoices):
        ASK_FOLLOW_UP = "ask_follow_up", "Ask follow-up"
        MOVE_NEXT = "move_next", "Move next"
        FLAG_MISSING_AND_MOVE_NEXT = "flag_missing_and_move_next", "Flag missing and move next"
        SKIP = "skip", "Skip"
        STOP = "stop", "Stop"
        BOUNDARY_RESPONSE = "boundary_response", "Boundary response"
        COMPLETE = "complete", "Complete"

    class CoverageAssessment(models.TextChoices):
        COVERED = "covered", "Covered"
        PARTIALLY_COVERED = "partially_covered", "Partially covered"
        UNCLEAR = "unclear", "Unclear"
        OFF_TOPIC = "off_topic", "Off topic"
        NOT_ASSESSED = "not_assessed", "Not assessed"

    class ParticipantControl(models.TextChoices):
        NONE = "none", "None"
        SKIP = "skip", "Skip"
        STOP = "stop", "Stop"

    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="agent_decisions",
    )

    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_decisions",
    )

    section = models.CharField(max_length=100, blank=True)
    section_index = models.PositiveIntegerField(default=0)

    coverage_assessment = models.CharField(
        max_length=40,
        choices=CoverageAssessment.choices,
        default=CoverageAssessment.NOT_ASSESSED,
    )

    participant_control = models.CharField(
        max_length=20,
        choices=ParticipantControl.choices,
        default=ParticipantControl.NONE,
    )

    action = models.CharField(
        max_length=50,
        choices=Action.choices,
    )

    probe_count_before = models.PositiveIntegerField(default=0)
    covered_information = models.JSONField(default=list, blank=True)
    missing_information = models.JSONField(default=list, blank=True)
    decision_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.session.session_code} - {self.action} - {self.section}"

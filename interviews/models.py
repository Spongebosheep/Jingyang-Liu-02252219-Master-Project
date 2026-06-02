from django.db import models


class Protocol(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    stakeholder_group = models.CharField(max_length=100)
    purpose = models.TextField()
    interview_mode = models.CharField(max_length=100, default="AI-led semi-structured interview")
    estimated_duration = models.CharField(max_length=50, default="10–15 minutes")
    output_description = models.CharField(max_length=200, default="Transcript + structured summary")

    # Store protocol sections and ethics rules as structured JSON.
    sections = models.JSONField(default=list, blank=True)
    ethics_rules = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


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


    def current_section(self):
        sections = self.protocol.sections or []
        if 0 <= self.current_section_index < len(sections):
            return sections[self.current_section_index]
        return None

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

    summary_grounded_in_transcript = models.BooleanField(default=False)
    no_unsupported_interpretation = models.BooleanField(default=False)
    no_medical_or_diagnostic_advice = models.BooleanField(default=False)
    participant_safety_respected = models.BooleanField(default=False)
    limitations_and_missing_information_visible = models.BooleanField(default=False)

    decision = models.CharField(
        max_length=30,
        choices=Decision.choices,
        default=Decision.PENDING,
    )
    reviewer_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Review for {self.session.session_code}"

class AgentDecision(models.Model):
    class Action(models.TextChoices):
        ASK_FOLLOW_UP = "ask_follow_up", "Ask follow-up"
        MOVE_NEXT = "move_next", "Move next"
        FLAG_MISSING_AND_MOVE_NEXT = "flag_missing_and_move_next", "Flag missing and move next"
        SKIP = "skip", "Skip"
        STOP = "stop", "Stop"
        BOUNDARY_RESPONSE = "boundary_response", "Boundary response"
        COMPLETE = "complete", "Complete"

    class AnswerStatus(models.TextChoices):
        SUFFICIENT = "sufficient", "Sufficient"
        PARTIAL = "partial", "Partial"
        VAGUE = "vague", "Vague"
        TOO_SHORT = "too_short", "Too short"
        OFF_TOPIC = "off_topic", "Off topic"
        SKIPPED = "skipped", "Skipped"
        STOPPED = "stopped", "Stopped"
        SAFETY_BOUNDARY = "safety_boundary", "Safety boundary"

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

    answer_status = models.CharField(
        max_length=40,
        choices=AnswerStatus.choices,
    )

    action = models.CharField(
        max_length=50,
        choices=Action.choices,
    )

    probe_count_before = models.PositiveIntegerField(default=0)
    missing_information = models.JSONField(default=list, blank=True)
    decision_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.session.session_code} - {self.action} - {self.section}"

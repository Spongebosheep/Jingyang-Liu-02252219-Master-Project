from copy import deepcopy
import re
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Prefetch, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .agent import (
    ensure_initial_agent_message,
    handle_participant_reply,
    skip_current_question,
    stop_interview,
)
from .forms import (
    InterviewSessionForm,
    NEW_PROTOCOL_SECTION_TEMPLATE,
    ProtocolEditForm,
    StakeholderForm,
    parse_protocol_sections,
)
from .consent import build_consent_snapshot_record
from .digest import (
    NON_RESEARCH_SECTION_CODES,
    digest_review_summary,
    ensure_digest_items,
    protocol_review_sections,
    reviewer_display,
)
from .models import (
    AgentDecision,
    DigestReviewEvent,
    InterviewSession,
    Message,
    Protocol,
    ReviewDecision,
    Stakeholder,
    StructuredDigestItem,
)
from .release_conditions import (
    build_release_conditions,
    save_system_release_conditions,
)
from .workflow_status import decorate_session, decorate_stakeholder

@login_required
@never_cache
def overview(request):
    sessions = (
        InterviewSession.objects
        .select_related("stakeholder", "protocol")
        .order_by("-updated_at", "-id")
    )
    recent_sessions = list(sessions[:5])
    for session in recent_sessions:
        decorate_session(session)

    review_attention_statuses = [
        InterviewSession.ReviewStatus.REVISION_REQUESTED,
        InterviewSession.ReviewStatus.NOT_USABLE,
    ]
    evidence_attention_statuses = [
        InterviewSession.OutputQualityStatus.REVISION_REQUESTED,
        InterviewSession.OutputQualityStatus.NOT_USABLE,
    ]
    attention_filter = (
        Q(review_status__in=review_attention_statuses)
        | Q(output_quality_status__in=evidence_attention_statuses)
    )
    session_count = sessions.count()
    output_ready_count = sessions.filter(transcript_saved=True).count()
    review_pending_count = sessions.filter(
        transcript_saved=True,
        review_status__in=[
            InterviewSession.ReviewStatus.NOT_REVIEWED,
            InterviewSession.ReviewStatus.NEEDS_REVIEW,
        ],
    ).count()
    reviewed_output_count = sessions.filter(
        transcript_saved=True,
        review_status__in=[
            InterviewSession.ReviewStatus.APPROVED,
            *review_attention_statuses,
        ],
    ).count()
    review_attention_count = sessions.filter(
        review_status__in=review_attention_statuses
    ).count()
    attention_count = sessions.filter(attention_filter).count()
    approved_evidence_count = sessions.filter(
        output_quality_status=InterviewSession.OutputQualityStatus.APPROVED
    ).count()
    evidence_attention_count = sessions.filter(
        output_quality_status__in=evidence_attention_statuses
    ).count()
    evidence_pending_count = (
        sessions
        .filter(transcript_saved=True)
        .exclude(
            output_quality_status=InterviewSession.OutputQualityStatus.APPROVED
        )
        .exclude(attention_filter)
        .count()
    )
    pending_consent_count = (
        sessions
        .filter(consent_confirmed=False)
        .exclude(
            status__in=[
                InterviewSession.Status.COMPLETED,
                InterviewSession.Status.STOPPED,
            ]
        )
        .count()
    )
    active_interview_count = sessions.filter(
        status=InterviewSession.Status.IN_PROGRESS
    ).count()
    closed_interview_count = sessions.filter(
        status__in=[
            InterviewSession.Status.COMPLETED,
            InterviewSession.Status.STOPPED,
        ]
    ).count()

    context = {
        "stakeholder_count": Stakeholder.objects.count(),
        "protocol_count": Protocol.objects.count(),
        "session_count": session_count,
        "output_ready_count": output_ready_count,
        "review_pending_count": review_pending_count,
        "reviewed_output_count": reviewed_output_count,
        "review_attention_count": review_attention_count,
        "attention_count": attention_count,
        "approved_evidence_count": approved_evidence_count,
        "evidence_attention_count": evidence_attention_count,
        "evidence_pending_count": evidence_pending_count,
        "pending_consent_count": pending_consent_count,
        "active_interview_count": active_interview_count,
        "closed_interview_count": closed_interview_count,
        "recent_sessions": recent_sessions,
        "recent_sessions_limited": session_count > 5,
    }

    return render(request, "interviews/overview.html", context)


@login_required
@never_cache
def stakeholder_list(request):
    session_prefetch = Prefetch(
        "sessions",
        queryset=InterviewSession.objects.select_related("protocol").order_by(
            "-updated_at", "-id"
        ),
        to_attr="workflow_sessions",
    )
    stakeholders = list(
        Stakeholder.objects
        .select_related("assigned_protocol")
        .prefetch_related(session_prefetch)
        .order_by("participant_id")
    )
    for stakeholder in stakeholders:
        decorate_stakeholder(stakeholder, stakeholder.workflow_sessions)

    selected_participant_id = request.GET.get("selected", "").strip()
    selected_stakeholder = None
    if selected_participant_id:
        selected_stakeholder = next(
            (
                stakeholder
                for stakeholder in stakeholders
                if stakeholder.participant_id == selected_participant_id
            ),
            None,
        )

    if selected_stakeholder is None:
        selected_stakeholder = stakeholders[0] if stakeholders else None

    selected_session = (
        selected_stakeholder.latest_session if selected_stakeholder else None
    )
    context = {
        "stakeholders": stakeholders,
        "stakeholder_count": len(stakeholders),
        "assigned_protocol_count": len(
            {
                stakeholder.assigned_protocol_id
                for stakeholder in stakeholders
                if stakeholder.assigned_protocol_id
            }
        ),
        "session_count": sum(
            stakeholder.session_count for stakeholder in stakeholders
        ),
        "selected_stakeholder": selected_stakeholder,
        "selected_session": selected_session,
        "created_participant_id": request.GET.get("created", "").strip(),
    }

    return render(request, "interviews/stakeholder_list.html", context)


@login_required
def stakeholder_create(request):
    if request.method == "POST":
        form = StakeholderForm(request.POST)
        if form.is_valid():
            stakeholder = form.save()
            query = urlencode(
                {
                    "selected": stakeholder.participant_id,
                    "created": stakeholder.participant_id,
                }
            )
            return redirect(f"{reverse('stakeholder_list')}?{query}")
    else:
        form = StakeholderForm()

    return render(
        request,
        "interviews/stakeholder_form.html",
        {"form": form},
    )


@login_required
def interview_session_create(request):
    requested_participant_id = request.GET.get("stakeholder", "").strip()
    initial_stakeholder = None
    if requested_participant_id:
        initial_stakeholder = (
            Stakeholder.objects.select_related("assigned_protocol")
            .filter(participant_id=requested_participant_id)
            .first()
        )

    if request.method == "POST":
        form = InterviewSessionForm(request.POST)
        if form.is_valid():
            session = form.save()
            query = urlencode(
                {
                    "session": session.session_code,
                    "created": session.session_code,
                }
            )
            return redirect(f"{reverse('interview_sessions')}?{query}")
    else:
        form = InterviewSessionForm(initial_stakeholder=initial_stakeholder)

    return render(
        request,
        "interviews/session_form.html",
        {
            "form": form,
            "initial_stakeholder": initial_stakeholder,
        },
    )


@login_required
@never_cache
def stakeholder_detail(request, participant_id):
    stakeholder = get_object_or_404(
        Stakeholder.objects.select_related("assigned_protocol"),
        participant_id=participant_id,
    )

    sessions = list(
        InterviewSession.objects
        .select_related("stakeholder", "protocol")
        .filter(stakeholder=stakeholder)
        .order_by("-updated_at", "-id")
    )
    decorate_stakeholder(stakeholder, sessions)

    requested_session_code = request.GET.get("session", "").strip()
    session = None
    if requested_session_code:
        session = next(
            (
                candidate
                for candidate in stakeholder.ordered_sessions
                if candidate.session_code == requested_session_code
            ),
            None,
        )
    if session is None:
        session = stakeholder.latest_session

    context = {
        "stakeholder": stakeholder,
        "sessions": stakeholder.ordered_sessions,
        "session_count": stakeholder.session_count,
        "session": session,
        "current_section": session.current_section() if session else None,
    }

    template_name = (
        "interviews/stakeholder_detail.html"
        if session
        else "interviews/stakeholder_record_detail.html"
    )
    return render(request, template_name, context)


def _protocol_section_rows(protocol=None, post_data=None):
    rows = []
    existing_sections = list(
        (protocol.sections if protocol else NEW_PROTOCOL_SECTION_TEMPLATE) or []
    )

    posted_values = {}
    if post_data is not None:
        for field_name in [
            "section_code",
            "section_label",
            "section_purpose",
            "section_question",
            "section_required",
            "section_assessment_guidance",
            "section_follow_up_focus",
            "section_interaction_boundary",
        ]:
            posted_values[field_name] = post_data.getlist(field_name)

    row_count = (
        len(posted_values.get("section_label", []))
        if post_data is not None
        else len(existing_sections)
    )
    for position in range(row_count):
        section = existing_sections[position] if position < len(existing_sections) else {}

        def value_or_default(field_name, default):
            values = posted_values.get(field_name, [])
            if position < len(values):
                return values[position]
            return default

        rows.append(
            {
                "position": position + 1,
                "index": section.get("index", position),
                "code": value_or_default(
                    "section_code",
                    section.get("code", ""),
                ),
                "label": value_or_default(
                    "section_label",
                    section.get("label", ""),
                ),
                "purpose": value_or_default(
                    "section_purpose",
                    section.get("purpose", ""),
                ),
                "question": value_or_default(
                    "section_question",
                    section.get("primary_question", ""),
                ),
                "required": value_or_default(
                    "section_required",
                    "\n".join(section.get("required_information", [])),
                ),
                "assessment_guidance": value_or_default(
                    "section_assessment_guidance",
                    section.get("assessment_guidance", ""),
                ),
                "follow_up_focus": value_or_default(
                    "section_follow_up_focus",
                    section.get("follow_up_focus", ""),
                ),
                "interaction_boundary": value_or_default(
                    "section_interaction_boundary",
                    section.get("interaction_boundary", ""),
                ),
                "is_boundary": position in {0, row_count - 1},
            }
        )

    return rows


@login_required
def protocol_list(request):
    protocols = list(
        Protocol.objects.prefetch_related("sessions", "stakeholders").order_by(
            "title", "-version"
        )
    )
    families = {}
    for protocol in protocols:
        families.setdefault(protocol.family_id, []).append(protocol)

    protocol_families = []
    for versions in families.values():
        versions.sort(key=lambda item: item.version, reverse=True)
        latest = versions[0]
        protocol_families.append(
            {
                "latest": latest,
                "versions": versions,
                "version_count": len(versions),
                "session_count": sum(len(item.sessions.all()) for item in versions),
                "stakeholder_count": len(
                    {
                        stakeholder.pk
                        for item in versions
                        for stakeholder in item.stakeholders.all()
                    }
                ),
            }
        )

    protocol_families.sort(key=lambda item: item["latest"].title.lower())
    return render(
        request,
        "interviews/protocol_list.html",
        {
            "protocol_families": protocol_families,
            "family_count": len(protocol_families),
            "version_count": len(protocols),
            "created_slug": request.GET.get("created", "").strip(),
        },
    )


def _unique_protocol_slug(title):
    base_slug = slugify(title) or "interview-protocol"
    candidate = base_slug
    suffix = 2
    while Protocol.objects.filter(slug=candidate).exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


@login_required
def protocol_create(request):
    if request.method == "POST":
        form = ProtocolEditForm(request.POST)
        if form.is_valid():
            try:
                sections = parse_protocol_sections(request.POST)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                protocol = form.save(commit=False)
                protocol.slug = _unique_protocol_slug(protocol.title)
                protocol.sections = sections
                protocol.version = 1
                protocol.status = Protocol.Status.DRAFT
                protocol.save()
                return redirect(
                    f"{reverse('protocol_detail', args=[protocol.slug])}?created=1"
                )
    else:
        form = ProtocolEditForm()

    return render(
        request,
        "interviews/protocol_form.html",
        {
            "protocol": None,
            "form": form,
            "section_rows": _protocol_section_rows(
                post_data=request.POST if request.method == "POST" else None,
            ),
            "form_mode": "create",
        },
    )


@login_required
def protocol_detail(request, slug):
    protocol = get_object_or_404(Protocol, slug=slug)
    versions = Protocol.objects.filter(family_id=protocol.family_id).order_by(
        "-version"
    )

    context = {
        "protocol": protocol,
        "sections": protocol.sections or [],
        "ethics_rules": protocol.ethics_rules or [],
        "versions": versions,
        "updated": request.GET.get("updated") == "1",
        "locked": request.GET.get("locked") == "1",
        "copied": request.GET.get("copied") == "1",
        "created": request.GET.get("created") == "1",
        "is_evaluated_case": Protocol.objects.filter(
            family_id=protocol.family_id,
            slug="sensory-overload-interview",
        ).exists(),
    }

    return render(request, "interviews/protocol_detail.html", context)


@login_required
def protocol_edit(request, slug):
    protocol = get_object_or_404(Protocol, slug=slug)
    if protocol.is_locked:
        return HttpResponseForbidden(
            "This protocol version is locked. Copy it to create an editable version."
        )

    if request.method == "POST":
        form = ProtocolEditForm(request.POST, instance=protocol)
        if form.is_valid():
            try:
                updated_sections = parse_protocol_sections(
                    request.POST,
                    protocol.sections,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                protocol = form.save(commit=False)
                protocol.sections = updated_sections
                protocol.save()
                return redirect(f"{reverse('protocol_detail', args=[protocol.slug])}?updated=1")
    else:
        form = ProtocolEditForm(instance=protocol)

    return render(
        request,
        "interviews/protocol_form.html",
        {
            "protocol": protocol,
            "form": form,
            "section_rows": _protocol_section_rows(
                protocol,
                request.POST if request.method == "POST" else None,
            ),
            "form_mode": "edit",
        },
    )


@login_required
@require_POST
def protocol_lock(request, slug):
    protocol = get_object_or_404(Protocol, slug=slug)
    protocol.lock_for_use()
    return redirect(f"{reverse('protocol_detail', args=[protocol.slug])}?locked=1")


@login_required
@require_POST
def protocol_duplicate(request, slug):
    with transaction.atomic():
        source = get_object_or_404(
            Protocol.objects.select_for_update(),
            slug=slug,
        )
        next_version = (
            Protocol.objects.filter(family_id=source.family_id).aggregate(
                maximum=Max("version")
            )["maximum"]
            or 0
        ) + 1

        base_slug = re.sub(r"-v\d+$", "", source.slug)
        candidate_slug = f"{base_slug}-v{next_version}"
        suffix = 2
        while Protocol.objects.filter(slug=candidate_slug).exists():
            candidate_slug = f"{base_slug}-v{next_version}-{suffix}"
            suffix += 1

        copied_protocol = Protocol.objects.create(
            title=source.title,
            slug=candidate_slug,
            stakeholder_group=source.stakeholder_group,
            purpose=source.purpose,
            interview_mode=source.interview_mode,
            estimated_duration=source.estimated_duration,
            output_description=source.output_description,
            sections=deepcopy(source.sections or []),
            ethics_rules=deepcopy(source.ethics_rules or []),
            family_id=source.family_id,
            version=next_version,
            status=Protocol.Status.DRAFT,
            parent_version=source,
        )

    return redirect(
        f"{reverse('protocol_detail', args=[copied_protocol.slug])}?copied=1"
    )

def _get_session_for_access_token(access_token):
    session = get_object_or_404(
        InterviewSession.objects.select_related(
            "stakeholder",
            "stakeholder__assigned_protocol",
            "protocol",
        ),
        access_token=access_token,
    )
    return session.stakeholder, session


def interview_consent(request, access_token):
    stakeholder, session = _get_session_for_access_token(access_token)
    consent_record = build_consent_snapshot_record(session.protocol)

    if session.status in [
        InterviewSession.Status.COMPLETED,
        InterviewSession.Status.STOPPED,
    ]:
        return redirect("interview_completed", access_token=session.access_token)

    if session.consent_confirmed:
        return redirect("interview_session", access_token=session.access_token)

    error = None

    if request.method == "POST":
        consent_checked = request.POST.get("consent_confirmed")

        if not consent_checked:
            error = "Please confirm consent before starting the interview."
        else:
            # Reset previous test run data for the same seeded MVP session.
            Message.objects.filter(session=session).delete()
            AgentDecision.objects.filter(session=session).delete()
            ReviewDecision.objects.filter(session=session).delete()
            StructuredDigestItem.objects.filter(session=session).delete()

            session.current_section_index = 0
            session.consent_confirmed = True
            session.consent_notice_version = consent_record["notice_version"]
            session.consent_snapshot = consent_record["snapshot"]
            session.consent_snapshot_sha256 = consent_record["sha256"]
            session.status = InterviewSession.Status.IN_PROGRESS
            session.transcript_saved = False
            session.review_status = "needs_review"
            session.output_quality_status = "waiting"
            session.researcher_note = ""

            # Consent confirmation and interview start are separate persisted
            # fields, recorded from the same accepted POST transition.
            confirmed_at = timezone.now()
            session.consent_confirmed_at = confirmed_at
            session.started_at = confirmed_at

            if hasattr(session, "completed_at"):
                session.completed_at = None

            if hasattr(session, "summary_generated"):
                session.summary_generated = False

            session.save()

            stakeholder.status = Stakeholder.Status.IN_PROGRESS
            stakeholder.save()

            ensure_initial_agent_message(session)

            return redirect("interview_session", access_token=session.access_token)

    context = {
        "stakeholder": stakeholder,
        "session": session,
        "protocol": session.protocol,
        "consent_notice": consent_record["snapshot"],
        "error": error,
    }

    return render(request, "interviews/consent.html", context)


def interview_session(request, access_token):
    stakeholder, session = _get_session_for_access_token(access_token)

    if not session.consent_confirmed:
        return redirect("interview_consent", access_token=session.access_token)

    if session.status in [
        InterviewSession.Status.COMPLETED,
        InterviewSession.Status.STOPPED,
    ]:
        return redirect("interview_completed", access_token=session.access_token)

    ensure_initial_agent_message(session)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "stop":
            stop_interview(session)
            return redirect("interview_completed", access_token=session.access_token)

        if action == "skip":
            skip_current_question(session)
            session.refresh_from_db()

            if session.status == InterviewSession.Status.COMPLETED:
                return redirect("interview_completed", access_token=session.access_token)

            return redirect("interview_session", access_token=session.access_token)

        if action == "send":
            reply_text = request.POST.get("reply", "").strip()

            if reply_text:
                handle_participant_reply(session, reply_text)
                session.refresh_from_db()

                if session.status == InterviewSession.Status.COMPLETED:
                    return redirect("interview_completed", access_token=session.access_token)

            return redirect("interview_session", access_token=session.access_token)

    context = {
        "stakeholder": stakeholder,
        "session": session,
        "protocol": session.protocol,
        "sections": session.protocol.sections or [],
        "current_section": session.current_section(),
        "messages": session.messages.all(),
    }

    return render(request, "interviews/interview_session.html", context)

def build_topic_statuses(session):
    items_by_index = {
        item.section_index: item for item in ensure_digest_items(session)
    }
    topic_statuses = []

    for section in protocol_review_sections(session.protocol):
        item = items_by_index.get(section["index"])
        if item is None:
            status = "not_assessed"
            status_label = "Not assessed"
        elif item.participant_control == StructuredDigestItem.ParticipantControl.SKIP:
            status = "skipped"
            status_label = "Skipped"
        elif item.participant_control == StructuredDigestItem.ParticipantControl.STOP:
            status = "stopped"
            status_label = "Stopped here"
        elif not item.topic_reached:
            status = "not_reached"
            status_label = "Not reached"
        else:
            status = item.coverage_status
            status_label = item.get_coverage_status_display()

        topic_statuses.append(
            {
                "label": section["title"],
                "status": status,
                "status_label": status_label,
            }
        )

    return topic_statuses

def interview_completed(request, access_token):
    stakeholder, session = _get_session_for_access_token(access_token)

    final_agent_message = (
        session.messages
        .filter(sender=Message.Sender.AGENT)
        .order_by("-id")
        .first()
    )

    context = {
        "stakeholder": stakeholder,
        "session": session,
        "protocol": session.protocol,
        "sections": session.protocol.sections or [],
        "messages": session.messages.all(),
        "final_agent_message": final_agent_message,
        "topic_statuses": build_topic_statuses(session),
    }

    return render(request, "interviews/interview_completed.html", context)


def _protocol_review_sections(protocol):
    """Compatibility wrapper for the shared protocol-driven digest helper."""
    return protocol_review_sections(protocol)


PLAIN_ACTION_LABELS = {
    AgentDecision.Action.ASK_FOLLOW_UP: "Asked one follow-up",
    AgentDecision.Action.MOVE_NEXT: "Continued to the next Protocol section",
    AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT: (
        "Recorded missing information and continued"
    ),
    AgentDecision.Action.SKIP: "Respected the participant's Skip choice",
    AgentDecision.Action.STOP: "Ended the interview",
    AgentDecision.Action.BOUNDARY_RESPONSE: "Gave a non-medical boundary response",
    AgentDecision.Action.COMPLETE: "Completed the interview",
}


MISSING_COVERAGE_STATUSES = {
    StructuredDigestItem.CoverageStatus.PARTIALLY_COVERED,
    StructuredDigestItem.CoverageStatus.NOT_COVERED,
}


def _digest_limitation_text(item):
    if item.participant_control == StructuredDigestItem.ParticipantControl.SKIP:
        return f"{item.section_title} was skipped."
    if item.participant_control == StructuredDigestItem.ParticipantControl.STOP:
        return f"{item.section_title} was stopped before completion."
    if not item.topic_reached:
        return f"{item.section_title} was not reached."
    if item.coverage_status in MISSING_COVERAGE_STATUSES:
        missing = ", ".join(item.missing_information or []) or "not specified"
        return f"{item.section_title} has missing Protocol information: {missing}."
    if item.coverage_status == StructuredDigestItem.CoverageStatus.NOT_ASSESSED:
        return f"{item.section_title} was reached, but Protocol coverage was not assessed."
    return ""


def _plain_decision_reason(reason):
    """Remove implementation jargon from the researcher-facing explanation."""

    cleaned = " ".join((reason or "").split())
    replacements = {
        "LLM-assisted cumulative assessment: ": "",
        "Cumulative section assessment: ": "",
        (
            "Follow-up wording generated by constrained LLM after LangGraph "
            "selected ASK_FOLLOW_UP."
        ): "A neutral follow-up was then phrased for the selected action.",
    }
    for original, replacement in replacements.items():
        cleaned = cleaned.replace(original, replacement)

    cleaned = re.sub(
        r"Fallback used because LLM assessment failed:.*$",
        (
            "Semantic assessment was unavailable, so the conservative "
            "Protocol fallback kept unverified information visible."
        ),
        cleaned,
    )
    cleaned = re.sub(
        r"Template follow-up used because LLM wording support failed: [A-Za-z]+\.?",
        "The neutral fallback wording was used.",
        cleaned,
    )
    return " ".join(cleaned.split()) or "No additional reason was recorded."


def _decorate_decision_for_review(decision, step_number):
    decision.review_step_number = step_number
    decision.review_action_label = PLAIN_ACTION_LABELS.get(
        decision.action,
        decision.get_action_display(),
    )
    decision.review_reason = _plain_decision_reason(decision.decision_reason)
    follow_up_count_after = decision.probe_count_before
    if decision.action == AgentDecision.Action.ASK_FOLLOW_UP:
        follow_up_count_after += 1
    decision.follow_up_usage_before = (
        f"Before this action: {decision.probe_count_before} of 1 used"
    )
    decision.follow_up_usage_after = (
        f"After this action: {min(follow_up_count_after, 1)} of 1 used"
    )
    decision.review_anchor = f"control-step-{step_number}"
    return decision


def _decorate_transcript_for_review(messages, decisions):
    """Add session-local labels without exposing database primary keys."""

    sender_counts = {
        Message.Sender.PARTICIPANT: 0,
        Message.Sender.AGENT: 0,
        Message.Sender.SYSTEM: 0,
    }
    label_prefixes = {
        Message.Sender.PARTICIPANT: "Participant response",
        Message.Sender.AGENT: "Interview prompt",
        Message.Sender.SYSTEM: "Control event",
    }
    decisions_by_message = {}
    unlinked_decisions = []
    for step_number, decision in enumerate(decisions, start=1):
        _decorate_decision_for_review(decision, step_number)
        if decision.message_id:
            decisions_by_message.setdefault(decision.message_id, []).append(decision)
        else:
            unlinked_decisions.append(decision)

    message_display = {}
    for turn_number, message in enumerate(messages, start=1):
        sender_counts[message.sender] += 1
        message.review_turn_number = turn_number
        message.review_label = (
            f"{label_prefixes[message.sender]} {sender_counts[message.sender]}"
        )
        message.review_anchor = f"transcript-turn-{turn_number}"
        message.review_decisions = decisions_by_message.get(message.id, [])
        message_display[message.id] = {
            "label": message.review_label,
            "anchor": message.review_anchor,
        }

    remaining_unlinked = []
    for decision in unlinked_decisions:
        expected_term = {
            AgentDecision.Action.SKIP: "skip",
            AgentDecision.Action.STOP: "stop",
        }.get(decision.action)
        matching_message = next(
            (
                message
                for message in messages
                if message.sender == Message.Sender.SYSTEM
                and message.section_index == decision.section_index
                and not message.review_decisions
                and (
                    not expected_term
                    or expected_term in (message.content or "").lower()
                )
            ),
            None,
        )
        if matching_message:
            matching_message.review_decisions.append(decision)
        else:
            remaining_unlinked.append(decision)

    return message_display, remaining_unlinked


def _build_transcript_sections(protocol, messages, unlinked_decisions, digest_items):
    """Group every saved turn and control decision under its Protocol section."""

    messages_by_section = {}
    for message in messages:
        messages_by_section.setdefault(message.section_index, []).append(message)

    controls_by_section = {}
    for decision in unlinked_decisions:
        controls_by_section.setdefault(decision.section_index, []).append(decision)

    digest_by_section = {item.section_index: item for item in digest_items}
    transcript_sections = []
    seen_indexes = set()

    def add_section(section_index, code, label, position):
        section_messages = messages_by_section.pop(section_index, [])
        section_controls = controls_by_section.pop(section_index, [])
        if not section_messages and not section_controls:
            return

        digest_item = digest_by_section.get(section_index)
        is_boundary = code in NON_RESEARCH_SECTION_CODES
        transcript_sections.append(
            {
                "index": section_index,
                "code": code,
                "label": label or f"Section {position + 1}",
                "kind_label": "Workflow boundary" if is_boundary else "Research topic",
                "is_boundary": is_boundary,
                "messages": section_messages,
                "control_decisions": section_controls,
                "turn_count": len(section_messages),
                "participant_count": sum(
                    message.sender == Message.Sender.PARTICIPANT
                    for message in section_messages
                ),
                "decision_count": (
                    sum(len(message.review_decisions) for message in section_messages)
                    + len(section_controls)
                ),
                "coverage_label": (
                    digest_item.get_coverage_status_display()
                    if digest_item
                    else "Workflow"
                ),
                "coverage_status": digest_item.coverage_status if digest_item else "",
                "open_by_default": False,
            }
        )
        seen_indexes.add(section_index)

    for position, section in enumerate(protocol.sections or []):
        try:
            section_index = int(section.get("index", position))
        except (TypeError, ValueError):
            section_index = position
        add_section(
            section_index,
            str(section.get("code") or "").strip().lower(),
            str(section.get("label") or "").strip(),
            position,
        )

    unknown_indexes = sorted(
        (set(messages_by_section) | set(controls_by_section)) - seen_indexes
    )
    for position, section_index in enumerate(unknown_indexes):
        unknown_messages = messages_by_section.get(section_index, [])
        fallback_label = next(
            (message.section for message in unknown_messages if message.section),
            "Workflow",
        )
        add_section(section_index, "", fallback_label, position)

    first_research = next(
        (section for section in transcript_sections if not section["is_boundary"]),
        transcript_sections[0] if transcript_sections else None,
    )
    if first_research:
        first_research["open_by_default"] = True

    return transcript_sections


@login_required
def output_detail(request, session_code):
    session = get_object_or_404(
        InterviewSession.objects.select_related("stakeholder", "protocol"),
        session_code=session_code,
    )

    digest_items = ensure_digest_items(session)
    digest_summary = digest_review_summary(digest_items)
    review_decision, _ = ReviewDecision.objects.get_or_create(session=session)
    release_conditions_error = None
    overall_review_error = None

    if request.method == "POST":
        decision = request.POST.get("decision")
        note = request.POST.get("researcher_note", "").strip()

        if decision == "request_revision" and not note:
            overall_review_error = "Add a researcher note explaining the requested revision."
        elif decision == "mark_not_usable" and not note:
            overall_review_error = "Add a researcher note explaining why this output is not usable."
        else:
            review_decision.participant_meaning_preserved = (
                request.POST.get("participant_meaning_preserved") == "on"
            )
            review_decision.protocol_boundaries_respected = (
                request.POST.get("protocol_boundaries_respected") == "on"
            )
            review_decision.reviewer_note = note
            review_decision.reviewed_by = request.user
            review_decision.reviewer_name_snapshot = reviewer_display(request.user)
            review_decision.reviewed_at = timezone.now()
            session.researcher_note = note

            release_conditions = build_release_conditions(
                session,
                digest_items,
                review_decision,
            )
            save_system_release_conditions(review_decision, release_conditions)

            if decision == "approve":
                if not digest_summary["ready_for_overall_approval"]:
                    review_decision.decision = ReviewDecision.Decision.PENDING
                    session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
                    session.output_quality_status = InterviewSession.OutputQualityStatus.NEEDS_REVIEW
                    overall_review_error = (
                        "Review every answered or partial topic and include at least one "
                        "Included or Edited evidence item before approving the output. "
                        "Skipped, stopped, and not-reached topics are limitations, not evidence."
                    )
                elif not release_conditions["system_conditions_met"]:
                    review_decision.decision = ReviewDecision.Decision.PENDING
                    session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
                    session.output_quality_status = InterviewSession.OutputQualityStatus.NEEDS_REVIEW
                    release_conditions_error = (
                        "Resolve all system-checked workflow conditions before "
                        "approving this record."
                    )
                elif release_conditions["researcher_judgements_confirmed"]:
                    review_decision.decision = ReviewDecision.Decision.APPROVED
                    session.review_status = InterviewSession.ReviewStatus.APPROVED
                    session.output_quality_status = InterviewSession.OutputQualityStatus.APPROVED
                else:
                    review_decision.decision = ReviewDecision.Decision.PENDING
                    session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
                    session.output_quality_status = InterviewSession.OutputQualityStatus.NEEDS_REVIEW
                    release_conditions_error = (
                        "Confirm both researcher judgements before approving this record."
                    )

            elif decision == "request_revision":
                review_decision.decision = ReviewDecision.Decision.REVISION_REQUESTED
                session.review_status = InterviewSession.ReviewStatus.REVISION_REQUESTED
                session.output_quality_status = InterviewSession.OutputQualityStatus.REVISION_REQUESTED

            elif decision == "mark_not_usable":
                review_decision.decision = ReviewDecision.Decision.NOT_USABLE
                session.review_status = InterviewSession.ReviewStatus.NOT_USABLE
                session.output_quality_status = InterviewSession.OutputQualityStatus.NOT_USABLE

            review_decision.save()
            session.save()

            if not release_conditions_error and not overall_review_error:
                return redirect("output_detail", session_code=session.session_code)

    messages = list(Message.objects.filter(session=session).order_by("id"))

    agent_decisions = list(
        AgentDecision.objects
        .filter(session=session)
        .select_related("message")
        .order_by("created_at", "id")
    )
    message_display, unlinked_agent_decisions = _decorate_transcript_for_review(
        messages,
        agent_decisions,
    )
    transcript_sections = _build_transcript_sections(
        session.protocol,
        messages,
        unlinked_agent_decisions,
        digest_items,
    )

    summary_rows = []
    topic_statuses = []
    missing_flags = []

    for item in digest_items:
        status = item.get_coverage_status_display()
        source_messages = list(item.source_messages.all().order_by("created_at", "id"))
        for source_message in source_messages:
            display = message_display.get(source_message.id, {})
            source_message.review_label = display.get(
                "label", "Participant response"
            )
            source_message.review_anchor = display.get(
                "anchor", "full-transcript"
            )

        limitation_text = _digest_limitation_text(item)
        if limitation_text:
            missing_flags.append(limitation_text)

        summary_rows.append({
            "section": item.section_title,
            "label": item.label,
            "status": status,
            "content": item.final_text,
            "source_messages": source_messages,
            "digest_item": item,
            "is_evidence_candidate": item.is_evidence_candidate,
        })

        topic_statuses.append({
            "section": item.section_title,
            "status": status,
        })

    partial_topic_count = sum(
        item.coverage_status in MISSING_COVERAGE_STATUSES
        for item in digest_items
    )
    unavailable_topic_count = digest_summary["limitation_total"]
    missing_information_flag_count = sum(
        len(item.missing_information or [])
        for item in digest_items
        if item.coverage_status in MISSING_COVERAGE_STATUSES
    )

    release_conditions = build_release_conditions(
        session,
        digest_items,
        review_decision,
    )

    available_output_sessions = list(
        InterviewSession.objects.select_related("stakeholder", "protocol")
        .filter(transcript_saved=True)
        .order_by("-completed_at", "-updated_at", "-id")
    )
    if session not in available_output_sessions:
        available_output_sessions.insert(0, session)

    context = {
        "session": session,
        "messages": messages,
        "transcript_sections": transcript_sections,
        "available_output_sessions": available_output_sessions,
        "summary_rows": summary_rows,
        "digest_items": digest_items,
        "digest_summary": digest_summary,
        "digest_review_notice": request.session.pop("digest_review_notice", None),
        "digest_review_error": request.session.pop("digest_review_error", None),
        "topic_statuses": topic_statuses,
        "missing_flags": missing_flags,
        "partial_topic_count": partial_topic_count,
        "unavailable_topic_count": unavailable_topic_count,
        "missing_information_flag_count": missing_information_flag_count,
        "summary_generated": getattr(session, "summary_generated", False),
        "stakeholder_group": getattr(session.stakeholder, "group", "") or getattr(session.stakeholder, "stakeholder_group", "") or "Sensory-sensitive participant",
        "unlinked_agent_decisions": unlinked_agent_decisions,
        "review_decision": review_decision,
        "release_conditions": release_conditions,
        "release_conditions_error": release_conditions_error,
        "overall_review_error": overall_review_error,
        "active_reviewer_name": reviewer_display(request.user),
    }

    return render(request, "interviews/output_detail.html", context)


@login_required
@require_POST
def review_digest_item(request, session_code, item_id):
    session = get_object_or_404(InterviewSession, session_code=session_code)
    ensure_digest_items(session)

    action = request.POST.get("digest_action", "").strip()
    reviewed_text = request.POST.get("reviewed_text", "").strip()
    comment = request.POST.get("reviewer_comment", "").strip()
    allowed_actions = {
        "approve": StructuredDigestItem.ReviewStatus.APPROVED,
        "edit": StructuredDigestItem.ReviewStatus.EDITED,
        "exclude": StructuredDigestItem.ReviewStatus.EXCLUDED,
    }

    if action not in allowed_actions:
        request.session["digest_review_error"] = (
            "Choose Include source extract, Include edited extract, or Exclude topic."
        )
        return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")

    with transaction.atomic():
        item = get_object_or_404(
            StructuredDigestItem.objects.select_for_update(),
            id=item_id,
            session=session,
        )

        if not item.is_evidence_candidate:
            request.session["digest_review_error"] = (
                "Skipped, stopped, and not-reached topics are recorded as limitations "
                "and cannot be included as a participant extract."
            )
            return redirect(
                f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}"
            )

        if action == "edit" and not reviewed_text:
            request.session["digest_review_error"] = "The edited extract cannot be empty."
            return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")
        if action == "edit" and reviewed_text == item.generated_text:
            request.session["digest_review_error"] = (
                "The edited text is unchanged. Use Include source extract, or revise the text before saving."
            )
            return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")
        if (
            action == "approve"
            and reviewed_text
            and reviewed_text != item.generated_text
        ):
            request.session["digest_review_error"] = (
                "The extract was changed. Choose Include edited extract and add a reviewer comment, or use the source extract unchanged."
            )
            return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")
        if action in {"edit", "exclude"} and not comment:
            request.session["digest_review_error"] = (
                "Add a reviewer comment explaining an edit or exclusion."
            )
            return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")

        previous_status = item.review_status
        previous_text = item.final_text
        new_status = allowed_actions[action]

        item.review_status = new_status
        item.reviewed_text = reviewed_text if action == "edit" else ""
        item.reviewer_comment = comment
        item.reviewed_by = request.user
        item.reviewer_name_snapshot = reviewer_display(request.user)
        item.reviewed_at = timezone.now()
        item.save()

        DigestReviewEvent.objects.create(
            digest_item=item,
            previous_status=previous_status,
            new_status=new_status,
            previous_text=previous_text,
            new_text=item.final_text if action != "exclude" else "",
            comment=comment,
            reviewer=request.user,
            reviewer_name_snapshot=reviewer_display(request.user),
        )

        review_decision, _ = ReviewDecision.objects.get_or_create(session=session)
        review_decision.participant_meaning_preserved = False
        review_decision.protocol_boundaries_respected = False
        release_conditions = build_release_conditions(
            session,
            ensure_digest_items(session),
            review_decision,
        )
        save_system_release_conditions(review_decision, release_conditions)
        review_decision.decision = ReviewDecision.Decision.PENDING
        review_decision.save()

        session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
        session.output_quality_status = InterviewSession.OutputQualityStatus.NEEDS_REVIEW
        session.save()

    item_decision_label = (
        "included"
        if item.review_status == StructuredDigestItem.ReviewStatus.APPROVED
        else item.get_review_status_display().lower()
    )
    request.session["digest_review_notice"] = (
        f"{item.label} was {item_decision_label} by {reviewer_display(request.user)}."
    )
    return redirect(f"{reverse('output_detail', args=[session_code])}#digest-item-{item_id}")

@login_required
def interview_sessions(request):
    sessions = list(
        InterviewSession.objects
        .select_related("stakeholder", "protocol")
        .order_by("-updated_at", "-id")
    )
    for session in sessions:
        decorate_session(session)
        session.participant_url = request.build_absolute_uri(
            reverse(
                "interview_consent",
                kwargs={"access_token": session.access_token},
            )
        )

    requested_session_code = request.GET.get("session", "").strip()
    selected_session = next(
        (
            session
            for session in sessions
            if session.session_code == requested_session_code
        ),
        None,
    )
    if selected_session is None:
        selected_session = sessions[0] if sessions else None

    context = {
        "sessions": sessions,
        "selected_session": selected_session,
        "total_sessions": len(sessions),
        "pending_consent_count": sum(
            not session.consent_confirmed
            and session.status
            not in {
                InterviewSession.Status.COMPLETED,
                InterviewSession.Status.STOPPED,
            }
            for session in sessions
        ),
        "completed_count": sum(
            session.status == InterviewSession.Status.COMPLETED
            for session in sessions
        ),
        "outputs_ready_count": sum(
            session.transcript_saved for session in sessions
        ),
        "created_session_code": request.GET.get("created", "").strip(),
    }

    return render(request, "interviews/researcher_interview_sessions.html", context)

@login_required
def outputs_home(request):
    sessions = InterviewSession.objects.filter(transcript_saved=True)
    requested_session_code = request.GET.get("session", "").strip()
    session = sessions.filter(session_code=requested_session_code).first()
    if session is None:
        session = sessions.order_by("-completed_at", "-updated_at", "-id").first()

    if session:
        return redirect("output_detail", session_code=session.session_code)

    return redirect("interview_sessions")

@login_required
def output_quality(request):
    all_sessions = InterviewSession.objects.select_related(
        "stakeholder",
        "protocol",
    ).order_by("session_code")

    completed_status_values = [InterviewSession.Status.COMPLETED]

    if hasattr(InterviewSession.Status, "STOPPED"):
        completed_status_values.append(InterviewSession.Status.STOPPED)

    generated_sessions = all_sessions.filter(transcript_saved=True)
    requested_session_code = request.GET.get("session", "").strip()
    selected_session = generated_sessions.filter(
        session_code=requested_session_code
    ).first()
    if selected_session is None:
        selected_session = generated_sessions.first()

    completed_interviews_count = all_sessions.filter(
        status__in=completed_status_values
    ).count()

    generated_outputs_count = generated_sessions.count()

    approved_count = generated_sessions.filter(
        review_status__in=["approved", "reviewed"]
    ).count()

    revision_count = generated_sessions.filter(
        review_status="revision_requested"
    ).count()

    not_usable_count = generated_sessions.filter(
        review_status=InterviewSession.ReviewStatus.NOT_USABLE
    ).count()

    reviewed_outputs_count = approved_count + revision_count + not_usable_count
    readiness_rows = []
    outputs_with_limitations_count = 0
    unavailable_topics_count = 0
    missing_information_flags_count = 0

    for session in generated_sessions:
        session_digest_items = ensure_digest_items(session)
        session_digest_summary = digest_review_summary(session_digest_items)
        session_partial_items = [
            item for item in session_digest_items
            if item.coverage_status in MISSING_COVERAGE_STATUSES
        ]
        session_missing_flags = sum(
            len(item.missing_information or []) for item in session_partial_items
        )
        session_unavailable_topics = session_digest_summary["limitation_total"]
        if session_missing_flags or session_unavailable_topics:
            outputs_with_limitations_count += 1
        unavailable_topics_count += session_unavailable_topics
        missing_information_flags_count += session_missing_flags
        interview_date = getattr(session, "completed_at", None) or getattr(session, "started_at", None)

        if interview_date:
            interview_date_display = interview_date.strftime("%d %b %Y %H:%M")
        else:
            interview_date_display = "Not recorded"

        if session.review_status in ["approved", "reviewed"]:
            review_status_label = "Approved"
            review_status_class = "status"
            action_label = "View output"

        elif session.review_status == "revision_requested":
            review_status_label = "Revision requested"
            review_status_class = "status-waiting"
            action_label = "Review output"

        elif session.review_status == InterviewSession.ReviewStatus.NOT_USABLE:
            review_status_label = "Not usable"
            review_status_class = "status-waiting"
            action_label = "View decision"

        else:
            review_status_label = "Review pending"
            review_status_class = "status-waiting"
            action_label = "Review output"

        readiness_rows.append({
            "session": session,
            "participant": session.stakeholder.participant_id,
            "protocol": session.protocol.title,
            "interview_date": interview_date_display,
            "output_status": (
                f"{session_digest_summary['resolved']} / "
                f"{session_digest_summary['reviewable_total']} extracts reviewed"
            ),
            "digest_summary": session_digest_summary,
            "missing_information_flags": session_missing_flags,
            "unavailable_topics": session_unavailable_topics,
            "is_selected": bool(
                selected_session and session.pk == selected_session.pk
            ),
            "review_status_label": review_status_label,
            "review_status_class": review_status_class,
            "action_label": action_label,
        })

    selected_review_decision = None
    selected_digest_summary = None
    selected_missing_information_flags_count = 0
    selected_unavailable_topics_count = 0
    selected_release_conditions = None

    if selected_session:
        selected_digest_items = ensure_digest_items(selected_session)
        selected_digest_summary = digest_review_summary(selected_digest_items)
        selected_missing_information_flags_count = sum(
            len(item.missing_information or [])
            for item in selected_digest_items
            if item.coverage_status in MISSING_COVERAGE_STATUSES
        )
        selected_unavailable_topics_count = selected_digest_summary[
            "limitation_total"
        ]
        selected_review_decision = (
            ReviewDecision.objects
            .filter(session=selected_session)
            .order_by("-id")
            .first()
        )
        selected_release_conditions = build_release_conditions(
            selected_session,
            selected_digest_items,
            selected_review_decision,
        )

    context = {
        "sessions": generated_sessions,
        "readiness_rows": readiness_rows,
        "selected_session": selected_session,
        "completed_interviews_count": completed_interviews_count,
        "generated_outputs_count": generated_outputs_count,
        "reviewed_outputs_count": reviewed_outputs_count,
        "outputs_with_limitations_count": outputs_with_limitations_count,
        "unavailable_topics_count": unavailable_topics_count,
        "missing_information_flags_count": missing_information_flags_count,
        "selected_review_decision": selected_review_decision,
        "selected_digest_summary": selected_digest_summary,
        "selected_missing_information_flags_count": selected_missing_information_flags_count,
        "selected_unavailable_topics_count": selected_unavailable_topics_count,
        "selected_release_conditions": selected_release_conditions,
    }

    return render(request, "interviews/output_quality.html", context)

def build_evidence_record_for_session(session):
    messages = list(Message.objects.filter(session=session).order_by("id"))
    agent_decisions = list(
        AgentDecision.objects.filter(session=session)
        .select_related("message")
        .order_by("created_at", "id")
    )
    digest_items = ensure_digest_items(session)
    digest_summary = digest_review_summary(digest_items)
    message_display, unlinked_agent_decisions = _decorate_transcript_for_review(
        messages,
        agent_decisions,
    )

    summary_rows = []
    excluded_digest_rows = []
    pending_digest_rows = []
    limitation_digest_rows = []
    missing_flags = []
    digest_review_events = []

    for item in digest_items:
        source_messages = list(item.source_messages.all().order_by("created_at", "id"))
        for source_message in source_messages:
            display = message_display.get(source_message.id, {})
            source_message.review_label = display.get(
                "label", "Participant response"
            )
            source_message.review_anchor = display.get(
                "anchor", "full-transcript"
            )
        row = {
            "item": item,
            "label": item.label,
            "section": item.section_title,
            "status": item.get_coverage_status_display(),
            "text": item.evidence_text,
            "source_messages": source_messages,
            "reviewer": item.reviewer_display,
            "reviewed_at": item.reviewed_at,
            "comment": item.reviewer_comment,
        }

        if not item.is_evidence_candidate:
            limitation_digest_rows.append(row)
        elif item.is_included:
            summary_rows.append(row)
        elif item.review_status == StructuredDigestItem.ReviewStatus.EXCLUDED:
            excluded_digest_rows.append(row)
        else:
            pending_digest_rows.append(row)

        digest_review_events.extend(list(item.review_events.all()))

        limitation_text = _digest_limitation_text(item)
        if limitation_text:
            missing_flags.append(limitation_text)

    if session.review_status in ["approved", "reviewed"]:
        review_label = "Approved"
        evidence_review_label = "Approved as reviewed evidence"

    elif session.review_status == "revision_requested":
        review_label = "Revision requested"
        evidence_review_label = "Revision requested"

    elif session.review_status == InterviewSession.ReviewStatus.NOT_USABLE:
        review_label = "Not usable"
        evidence_review_label = "Marked not usable"

    else:
        review_label = "Review pending"
        evidence_review_label = "Review pending"

    review_decision = (
        ReviewDecision.objects.filter(session=session)
        .select_related("reviewed_by")
        .order_by("-id")
        .first()
    )

    release_conditions = build_release_conditions(
        session,
        digest_items,
        review_decision,
    )

    return {
        "session": session,
        "messages": messages,
        "summary_rows": summary_rows,
        "excluded_digest_rows": excluded_digest_rows,
        "pending_digest_rows": pending_digest_rows,
        "limitation_digest_rows": limitation_digest_rows,
        "digest_review_events": sorted(
            digest_review_events,
            key=lambda event: (event.created_at, event.id),
        ),
        "digest_summary": digest_summary,
        "missing_flags": missing_flags,
        "review_label": review_label,
        "evidence_review_label": evidence_review_label,
        "agent_decisions": agent_decisions,
        "unlinked_agent_decisions": unlinked_agent_decisions,
        "agent_decision_count": len(agent_decisions),
        "review_decision": review_decision,
        "release_conditions": release_conditions,
    }

@login_required
def export_evidence_record(request):
    sessions = InterviewSession.objects.select_related(
        "stakeholder",
        "protocol",
    ).filter(
        transcript_saved=True
    ).order_by("session_code")

    requested_session_code = request.GET.get("session", "").strip()
    if requested_session_code:
        sessions = sessions.filter(session_code=requested_session_code)
        if not sessions.exists():
            return HttpResponse("Interview Session not found.", status=404)

    evidence_records = [
        build_evidence_record_for_session(session)
        for session in sessions
    ]

    generated_at = timezone.now()

    completed_outputs = sessions.count()
    approved_outputs = sessions.filter(
        review_status__in=["approved", "reviewed"]
    ).count()
    revision_requested_outputs = sessions.filter(
        review_status="revision_requested"
    ).count()
    not_usable_outputs = sessions.filter(
        review_status=InterviewSession.ReviewStatus.NOT_USABLE
    ).count()
    pending_outputs = (
        completed_outputs
        - approved_outputs
        - revision_requested_outputs
        - not_usable_outputs
    )

    total_agent_decisions = sum(
        record.get("agent_decision_count", 0)
        for record in evidence_records
    )

    total_missing_flags = sum(
        len(record.get("missing_flags", []))
        for record in evidence_records
    )

    if not_usable_outputs > 0:
        review_overview_label = "Includes outputs marked not usable"
    elif revision_requested_outputs > 0:
        review_overview_label = "Revision requested"
    elif pending_outputs > 0:
        review_overview_label = "Pending review"
    elif approved_outputs > 0:
        review_overview_label = "Approved"
    else:
        review_overview_label = "No review"

    context = {
        "generated_at": generated_at,
        "evidence_records": evidence_records,
        "completed_outputs": completed_outputs,
        "approved_outputs": approved_outputs,
        "revision_requested_outputs": revision_requested_outputs,
        "not_usable_outputs": not_usable_outputs,
        "pending_outputs": pending_outputs,
        "total_agent_decisions": total_agent_decisions,
        "total_missing_flags": total_missing_flags,
        "review_overview_label": review_overview_label,
        "export_scope": (
            f"Single Session: {requested_session_code}"
            if requested_session_code
            else "All saved Sessions"
        ),
    }

    html = render_to_string("interviews/evidence_record.html", context)

    response = HttpResponse(html, content_type="text/html")
    filename = (
        f"purrstone_evidence_record_{requested_session_code}.html"
        if requested_session_code
        else "purrstone_evidence_records_all.html"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response

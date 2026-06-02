from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from .agent import (
    ensure_initial_agent_message,
    handle_participant_reply,
    skip_current_question,
    stop_interview,
)
from .models import AgentDecision, InterviewSession, Message, Protocol, ReviewDecision, Stakeholder

def overview(request):
    stakeholders = Stakeholder.objects.select_related("assigned_protocol").all()
    sessions = InterviewSession.objects.select_related("stakeholder", "protocol").all()
    protocols = Protocol.objects.all()

    context = {
        "stakeholder_count": stakeholders.count(),
        "protocol_count": protocols.count(),
        "participant_count": stakeholders.filter(role__icontains="participant").count(),
        "session_count": sessions.count(),
        "reviewable_session_count": sessions.filter(transcript_saved=True).count(),
        "p01": stakeholders.filter(participant_id="P01").first(),
        "is01": sessions.filter(session_code="IS-01").first(),
        "protocol": protocols.filter(slug="sensory-overload-interview").first(),
    }

    return render(request, "interviews/overview.html", context)


def stakeholder_list(request):
    stakeholders = Stakeholder.objects.select_related("assigned_protocol").all()

    selected_stakeholder = (
        stakeholders.filter(participant_id="P01").first()
        or stakeholders.first()
    )

    selected_session = None
    if selected_stakeholder:
        selected_session = (
            InterviewSession.objects
            .select_related("stakeholder", "protocol")
            .filter(stakeholder=selected_stakeholder)
            .first()
        )

    context = {
        "stakeholders": stakeholders,
        "active_stakeholder_count": stakeholders.count(),
        "interview_participant_count": stakeholders.filter(role__icontains="participant").count(),
        "expert_reviewer_count": 0,
        "future_group_count": 2,
        "selected_stakeholder": selected_stakeholder,
        "selected_session": selected_session,
    }

    return render(request, "interviews/stakeholder_list.html", context)


def stakeholder_detail(request, participant_id):
    stakeholder = get_object_or_404(
        Stakeholder.objects.select_related("assigned_protocol"),
        participant_id=participant_id,
    )

    session = (
        InterviewSession.objects
        .select_related("stakeholder", "protocol")
        .filter(stakeholder=stakeholder)
        .first()
    )

    context = {
        "stakeholder": stakeholder,
        "session": session,
        "current_section": session.current_section() if session else None,
    }

    return render(request, "interviews/stakeholder_detail.html", context)


def protocol_detail(request, slug):
    protocol = get_object_or_404(Protocol, slug=slug)

    context = {
        "protocol": protocol,
        "sections": protocol.sections or [],
        "ethics_rules": protocol.ethics_rules or [],
    }

    return render(request, "interviews/protocol_detail.html", context)

def _get_session_for_participant(participant_id):
    stakeholder = get_object_or_404(
        Stakeholder.objects.select_related("assigned_protocol"),
        participant_id=participant_id,
    )

    session = get_object_or_404(
        InterviewSession.objects.select_related("stakeholder", "protocol"),
        stakeholder=stakeholder,
    )

    return stakeholder, session


def interview_consent(request, participant_id):
    stakeholder, session = _get_session_for_participant(participant_id)

    if session.status in [
        InterviewSession.Status.COMPLETED,
        InterviewSession.Status.STOPPED,
    ]:
        return redirect("interview_completed", participant_id=participant_id)

    error = None

    if request.method == "POST":
        consent_checked = request.POST.get("consent_confirmed")

        if not consent_checked:
            error = "Please confirm consent before starting the interview."
        else:
            session.consent_confirmed = True

            # Reset previous test run data for the same seeded MVP session.
            Message.objects.filter(session=session).delete()
            AgentDecision.objects.filter(session=session).delete()
            ReviewDecision.objects.filter(session=session).delete()

            session.current_section_index = 0
            session.consent_confirmed = True
            session.status = InterviewSession.Status.IN_PROGRESS
            session.transcript_saved = False
            session.review_status = "needs_review"
            session.output_quality_status = "waiting"
            session.researcher_note = ""

            # Reset timing for the new test run.
            session.started_at = timezone.now()

            if hasattr(session, "completed_at"):
                session.completed_at = None

            if hasattr(session, "summary_generated"):
                session.summary_generated = False

            session.save()

            stakeholder.status = Stakeholder.Status.IN_PROGRESS
            stakeholder.save()

            ensure_initial_agent_message(session)

            return redirect("interview_session", participant_id=participant_id)

    context = {
        "stakeholder": stakeholder,
        "session": session,
        "protocol": session.protocol,
        "error": error,
    }

    return render(request, "interviews/consent.html", context)


def interview_session(request, participant_id):
    stakeholder, session = _get_session_for_participant(participant_id)

    if not session.consent_confirmed:
        return redirect("interview_consent", participant_id=participant_id)

    if session.status in [
        InterviewSession.Status.COMPLETED,
        InterviewSession.Status.STOPPED,
    ]:
        return redirect("interview_completed", participant_id=participant_id)

    ensure_initial_agent_message(session)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "stop":
            stop_interview(session)
            return redirect("interview_completed", participant_id=participant_id)

        if action == "skip":
            skip_current_question(session)
            session.refresh_from_db()

            if session.status == InterviewSession.Status.COMPLETED:
                return redirect("interview_completed", participant_id=participant_id)

            return redirect("interview_session", participant_id=participant_id)

        if action == "send":
            reply_text = request.POST.get("reply", "").strip()

            if reply_text:
                handle_participant_reply(session, reply_text)
                session.refresh_from_db()

                if session.status == InterviewSession.Status.COMPLETED:
                    return redirect("interview_completed", participant_id=participant_id)

            return redirect("interview_session", participant_id=participant_id)

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
    sections = session.protocol.sections or []
    topic_statuses = []

    for section in sections:
        code = section.get("code")
        if code in ["opening", "faithful_summary"]:
            continue

        section_index = section.get("index")

        participant_answered = session.messages.filter(
            sender=Message.Sender.PARTICIPANT,
            section_index=section_index,
        ).exists()

        skipped = session.messages.filter(
            sender=Message.Sender.SYSTEM,
            section_index=section_index,
            content__icontains="skipped",
        ).exists()

        stopped_here = session.messages.filter(
            sender=Message.Sender.SYSTEM,
            section_index=section_index,
            content__icontains="stopped",
        ).exists()

        agent_asked = session.messages.filter(
            sender=Message.Sender.AGENT,
            section_index=section_index,
        ).exists()

        if participant_answered:
            status = "answered"
            status_label = "Answered"
        elif skipped:
            status = "skipped"
            status_label = "Skipped"
        elif stopped_here:
            status = "stopped"
            status_label = "Stopped here"
        elif agent_asked:
            status = "reached"
            status_label = "Reached"
        else:
            status = "not_reached"
            status_label = "Not reached"

        topic_statuses.append({
            "label": section.get("label"),
            "status": status,
            "status_label": status_label,
        })

    return topic_statuses

def interview_completed(request, participant_id):
    stakeholder, session = _get_session_for_participant(participant_id)

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


def output_detail(request, session_code):
    session = get_object_or_404(
        InterviewSession.objects.select_related("stakeholder", "protocol"),
        session_code=session_code,
    )

    review_decision, _ = ReviewDecision.objects.get_or_create(session=session)
    quality_criteria_error = None

    if request.method == "POST":
        decision = request.POST.get("decision")
        note = request.POST.get("researcher_note", "").strip()

        review_decision.summary_grounded_in_transcript = (
            request.POST.get("summary_grounded_in_transcript") == "on"
        )
        review_decision.no_unsupported_interpretation = (
            request.POST.get("no_unsupported_interpretation") == "on"
        )
        review_decision.no_medical_or_diagnostic_advice = (
            request.POST.get("no_medical_or_diagnostic_advice") == "on"
        )
        review_decision.participant_safety_respected = (
            request.POST.get("participant_safety_respected") == "on"
        )
        review_decision.limitations_and_missing_information_visible = (
            request.POST.get("limitations_and_missing_information_visible") == "on"
        )

        review_decision.reviewer_note = note
        session.researcher_note = note

        all_quality_criteria_confirmed = all([
            review_decision.summary_grounded_in_transcript,
            review_decision.no_unsupported_interpretation,
            review_decision.no_medical_or_diagnostic_advice,
            review_decision.participant_safety_respected,
            review_decision.limitations_and_missing_information_visible,
        ])

        if decision == "approve":
            if all_quality_criteria_confirmed:
                review_decision.decision = "approved"
                session.review_status = InterviewSession.ReviewStatus.APPROVED
                session.output_quality_status = InterviewSession.OutputQualityStatus.APPROVED
            else:
                review_decision.decision = "pending"
                session.review_status = "needs_review"
                session.output_quality_status = "waiting"
                quality_criteria_error = "Please confirm all review criteria before approving the summary."

        elif decision == "request_revision":
            review_decision.decision = "revision_requested"
            session.review_status = InterviewSession.ReviewStatus.REVISION_REQUESTED
            session.output_quality_status = InterviewSession.OutputQualityStatus.REVISION_REQUESTED

        review_decision.save()
        session.save()

        if not quality_criteria_error:
            return redirect("output_detail", session_code=session.session_code)

    messages = list(
        Message.objects.filter(session=session).order_by("id")
    )

    agent_decisions = list(
        AgentDecision.objects
        .filter(session=session)
        .select_related("message")
        .order_by("created_at", "id")
    )

    participant_answers_by_section = {}
    skipped_sections = set()

    for message in messages:
        sender = (message.sender or "").lower()
        content = (message.content or "").strip()

        if sender == "participant" and content:
            participant_answers_by_section.setdefault(message.section_index, []).append(content)

        if sender == "system" and "skipped" in content.lower():
            skipped_sections.add(message.section_index)

    review_sections = [
        (1, "Experience", "Situation"),
        (2, "Triggers & signs", "Main triggers"),
        (3, "Coping & support", "Coping / support"),
        (4, "Support concept reaction", "Support concept reaction"),
        (5, "Public-use acceptability", "Public-use concerns"),
    ]



    summary_rows = []
    topic_statuses = []
    missing_flags = []

    decisions_by_section = {}
    for decision in agent_decisions:
        decisions_by_section.setdefault(decision.section_index, []).append(decision)

    for section_index, section_title, summary_label in review_sections:
        answers = participant_answers_by_section.get(section_index, [])
        section_decisions = decisions_by_section.get(section_index, [])
        final_decision = section_decisions[-1] if section_decisions else None

        final_missing = []
        if final_decision:
            final_missing = list(final_decision.missing_information or [])

        has_final_missing_flag = (
            final_decision is not None
            and bool(final_missing)
            and (
                final_decision.action == AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT
                or final_decision.answer_status in [
                    AgentDecision.AnswerStatus.PARTIAL,
                    AgentDecision.AnswerStatus.VAGUE,
                    AgentDecision.AnswerStatus.TOO_SHORT,
                    AgentDecision.AnswerStatus.OFF_TOPIC,
                ]
            )
        )

        if section_index in skipped_sections:
            status = "Skipped"
            content = "The participant skipped this topic."
            missing_flags.append(f"{section_title} was skipped.")

        elif answers and has_final_missing_flag:
            status = "Partial"
            content = " ".join(answers)
            missing_flags.append(
                f"{section_title} has missing information: {', '.join(final_missing)}."
            )

        elif answers:
            status = "Answered"
            content = " ".join(answers)

        else:
            status = "Not reached"
            content = "No participant response was recorded for this topic."
            missing_flags.append(f"{section_title} was not reached.")

        summary_rows.append({
            "section": section_title,
            "label": summary_label,
            "status": status,
            "content": content,
        })

        topic_statuses.append({
            "section": section_title,
            "status": status,
        })

    transcript_excerpt = [
        message for message in messages
        if message.section_index in [1, 2]
    ][:4]

    if not transcript_excerpt:
        transcript_excerpt = messages[:4]

    quality_criteria = [
        {
            "field": "summary_grounded_in_transcript",
            "label": "Summary grounded in transcript",
            "checked": review_decision.summary_grounded_in_transcript,
        },
        {
            "field": "no_unsupported_interpretation",
            "label": "No unsupported interpretation",
            "checked": review_decision.no_unsupported_interpretation,
        },
        {
            "field": "no_medical_or_diagnostic_advice",
            "label": "No medical / diagnostic advice",
            "checked": review_decision.no_medical_or_diagnostic_advice,
        },
        {
            "field": "participant_safety_respected",
            "label": "Participant safety and autonomy respected",
            "checked": review_decision.participant_safety_respected,
        },
        {
            "field": "limitations_and_missing_information_visible",
            "label": "Limitations and missing information are visible",
            "checked": review_decision.limitations_and_missing_information_visible,
        },
    ]

    context = {
        "session": session,
        "messages": messages,
        "transcript_excerpt": transcript_excerpt,
        "summary_rows": summary_rows,
        "topic_statuses": topic_statuses,
        "missing_flags": missing_flags,
        "summary_generated": getattr(session, "summary_generated", False),
        "stakeholder_group": getattr(session.stakeholder, "group", "") or getattr(session.stakeholder, "stakeholder_group", "") or "Sensory-sensitive participant",
        "agent_decisions": agent_decisions,
        "agent_decision_count": len(agent_decisions),
        "review_decision": review_decision,
        "quality_criteria": quality_criteria,
        "quality_criteria_error": quality_criteria_error,
        "quality_confirmed_count": sum(1 for item in quality_criteria if item["checked"]),
        "quality_criteria_total": len(quality_criteria),
    }

    return render(request, "interviews/output_detail.html", context)

def interview_sessions(request):
    sessions = (
        InterviewSession.objects
        .select_related("stakeholder", "protocol")
        .all()
        .order_by("id")
    )

    selected_session = sessions.first()

    context = {
        "sessions": sessions,
        "selected_session": selected_session,
        "total_sessions": sessions.count(),
        "pending_consent_count": sessions.filter(consent_confirmed=False).count(),
        "completed_count": sessions.filter(status=InterviewSession.Status.COMPLETED).count(),
        "outputs_ready_count": sessions.filter(transcript_saved=True).count(),
    }

    return render(request, "interviews/researcher_interview_sessions.html", context)

def outputs_home(request):
    session = (
        InterviewSession.objects
        .filter(
            status__in=[
                InterviewSession.Status.COMPLETED,
                InterviewSession.Status.STOPPED,
            ]
        )
        .order_by("-completed_at", "-id")
        .first()
    )

    if session:
        return redirect("output_detail", session_code=session.session_code)

    return redirect("interview_sessions")

def output_quality(request):
    all_sessions = InterviewSession.objects.select_related(
        "stakeholder",
        "protocol",
    ).order_by("session_code")

    completed_status_values = [InterviewSession.Status.COMPLETED]

    if hasattr(InterviewSession.Status, "STOPPED"):
        completed_status_values.append(InterviewSession.Status.STOPPED)

    generated_sessions = all_sessions.filter(transcript_saved=True)

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

    reviewed_outputs_count = approved_count + revision_count
    limitation_action_values = [
        AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT,
        AgentDecision.Action.SKIP,
        AgentDecision.Action.STOP,
        AgentDecision.Action.BOUNDARY_RESPONSE,
    ]

    issues_flagged_count = 0

    for session in generated_sessions:
        has_evidence_limitation = AgentDecision.objects.filter(
            session=session,
            action__in=limitation_action_values,
        ).exists()

        if has_evidence_limitation:
            issues_flagged_count += 1

    readiness_rows = []

    for session in generated_sessions:
        interview_date = getattr(session, "completed_at", None) or getattr(session, "started_at", None)

        if interview_date:
            interview_date_display = interview_date.strftime("%d %b %Y %H:%M")
        else:
            interview_date_display = "Not recorded"

        if session.review_status in ["approved", "reviewed"]:
            review_status_label = "Approved"
            review_status_class = "status"
            output_quality_label = "Ready for use"
            output_quality_class = "status"
            action_label = "View output"

        elif session.review_status == "revision_requested":
            review_status_label = "Revision requested"
            review_status_class = "status-waiting"
            output_quality_label = "Needs revision"
            output_quality_class = "status-waiting"
            action_label = "Review output"

        else:
            review_status_label = "Review pending"
            review_status_class = "status-waiting"
            output_quality_label = "Waiting review"
            output_quality_class = "status-blue"
            action_label = "Review output"

        readiness_rows.append({
            "session": session,
            "participant": session.stakeholder.participant_id,
            "protocol": session.protocol.title,
            "interview_date": interview_date_display,
            "output_status": "Summary generated",
            "review_status_label": review_status_label,
            "review_status_class": review_status_class,
            "output_quality_label": output_quality_label,
            "output_quality_class": output_quality_class,
            "action_label": action_label,
        })

    selected_session = generated_sessions.first()
    selected_review_decision = None
    confirmed_quality_checks_count = 0
    quality_criteria_total = 5

    if selected_session:
        selected_review_decision = (
            ReviewDecision.objects
            .filter(session=selected_session)
            .order_by("-id")
            .first()
        )

    if selected_review_decision:
        confirmed_quality_checks_count = sum([
            selected_review_decision.summary_grounded_in_transcript,
            selected_review_decision.no_unsupported_interpretation,
            selected_review_decision.no_medical_or_diagnostic_advice,
            selected_review_decision.participant_safety_respected,
            selected_review_decision.limitations_and_missing_information_visible,
        ])

    context = {
        "sessions": generated_sessions,
        "readiness_rows": readiness_rows,
        "selected_session": selected_session,
        "completed_interviews_count": completed_interviews_count,
        "generated_outputs_count": generated_outputs_count,
        "reviewed_outputs_count": reviewed_outputs_count,
        "issues_flagged_count": issues_flagged_count,
        "selected_review_decision": selected_review_decision,
        "confirmed_quality_checks_count": confirmed_quality_checks_count,
        "quality_criteria_total": quality_criteria_total,
    }

    return render(request, "interviews/output_quality.html", context)

def build_evidence_record_for_session(session):
    messages = list(
        Message.objects.filter(session=session).order_by("id")
    )

    agent_decisions = list(
        AgentDecision.objects
        .filter(session=session)
        .select_related("message")
        .order_by("created_at", "id")
    )

    protocol_sections = [
        {"index": 1, "title": "Experience", "summary_label": "Situation"},
        {"index": 2, "title": "Triggers & signs", "summary_label": "Main triggers"},
        {"index": 3, "title": "Coping & support", "summary_label": "Coping / support"},
        {"index": 4, "title": "Support concept reaction", "summary_label": "Support concept reaction"},
        {"index": 5, "title": "Public-use acceptability", "summary_label": "Public-use concerns"},
    ]

    participant_answers_by_section = {}
    skipped_sections = set()
    stopped_sections = set()

    for message in messages:
        sender = (message.sender or "").lower()
        content = (message.content or "").strip()

        if sender == "participant" and content:
            participant_answers_by_section.setdefault(
                message.section_index,
                []
            ).append(content)

        if sender == "system" and "skipped" in content.lower():
            skipped_sections.add(message.section_index)

        if sender == "system" and "stopped" in content.lower():
            stopped_sections.add(message.section_index)

    decisions_by_section = {}

    for decision in agent_decisions:
        decisions_by_section.setdefault(
            decision.section_index,
            []
        ).append(decision)

    summary_rows = []
    missing_flags = []

    for section in protocol_sections:
        section_index = section["index"]
        section_title = section["title"]
        summary_label = section["summary_label"]

        answers = participant_answers_by_section.get(section_index, [])
        section_decisions = decisions_by_section.get(section_index, [])
        final_decision = section_decisions[-1] if section_decisions else None

        final_missing = []

        if final_decision:
            final_missing = list(final_decision.missing_information or [])

        has_final_missing_flag = (
            final_decision is not None
            and bool(final_missing)
            and (
                final_decision.action == AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT
                or final_decision.answer_status in [
                    AgentDecision.AnswerStatus.PARTIAL,
                    AgentDecision.AnswerStatus.VAGUE,
                    AgentDecision.AnswerStatus.TOO_SHORT,
                    AgentDecision.AnswerStatus.OFF_TOPIC,
                ]
            )
        )

        if section_index in skipped_sections:
            status = "Skipped"
            summary_text = "The participant skipped this topic."
            missing_flags.append(f"{section_title} was skipped.")

        elif section_index in stopped_sections:
            status = "Stopped"
            summary_text = "The participant stopped the interview before this topic was fully completed."
            missing_flags.append(f"{section_title} was stopped before completion.")

        elif answers and has_final_missing_flag:
            status = "Partial"
            summary_text = " ".join(answers)
            missing_flags.append(
                f"{section_title} has missing information: {', '.join(final_missing)}."
            )

        elif answers:
            status = "Answered"
            summary_text = " ".join(answers)

        else:
            status = "Not reached"
            summary_text = "No participant response was recorded for this topic."
            missing_flags.append(f"{section_title} was not reached.")

        summary_rows.append({
            "label": summary_label,
            "section": section_title,
            "status": status,
            "text": summary_text,
        })

    if session.review_status in ["approved", "reviewed"]:
        review_label = "Approved"
        output_quality_label = "Ready for use"

    elif session.review_status == "revision_requested":
        review_label = "Revision requested"
        output_quality_label = "Needs revision"

    else:
        review_label = "Review pending"
        output_quality_label = "Waiting review"

    review_decision = (
        ReviewDecision.objects
        .filter(session=session)
        .order_by("-id")
        .first()
    )

    quality_criteria = [
        {
            "label": "Summary grounded in transcript",
            "confirmed": bool(
                review_decision
                and review_decision.summary_grounded_in_transcript
            ),
        },
        {
            "label": "No unsupported interpretation",
            "confirmed": bool(
                review_decision
                and review_decision.no_unsupported_interpretation
            ),
        },
        {
            "label": "No medical / diagnostic advice",
            "confirmed": bool(
                review_decision
                and review_decision.no_medical_or_diagnostic_advice
            ),
        },
        {
            "label": "Participant safety and autonomy respected",
            "confirmed": bool(
                review_decision
                and review_decision.participant_safety_respected
            ),
        },
        {
            "label": "Limitations and missing information are visible",
            "confirmed": bool(
                review_decision
                and review_decision.limitations_and_missing_information_visible
            ),
        },
    ]

    quality_confirmed_count = sum(
        1 for criterion in quality_criteria
        if criterion["confirmed"]
    )

    return {
        "session": session,
        "messages": messages,
        "summary_rows": summary_rows,
        "missing_flags": missing_flags,
        "review_label": review_label,
        "output_quality_label": output_quality_label,
        "agent_decisions": agent_decisions,
        "agent_decision_count": len(agent_decisions),
        "review_decision": review_decision,
        "quality_criteria": quality_criteria,
        "quality_confirmed_count": quality_confirmed_count,
        "quality_criteria_total": len(quality_criteria),
    }

def export_evidence_record(request):
    sessions = InterviewSession.objects.select_related(
        "stakeholder",
        "protocol",
    ).filter(
        transcript_saved=True
    ).order_by("session_code")

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
    pending_outputs = completed_outputs - approved_outputs - revision_requested_outputs

    total_agent_decisions = sum(
        record.get("agent_decision_count", 0)
        for record in evidence_records
    )

    total_missing_flags = sum(
        len(record.get("missing_flags", []))
        for record in evidence_records
    )

    if revision_requested_outputs > 0:
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
        "pending_outputs": pending_outputs,
        "total_agent_decisions": total_agent_decisions,
        "total_missing_flags": total_missing_flags,
        "review_overview_label": review_overview_label,
    }

    html = render_to_string("interviews/evidence_record.html", context)

    response = HttpResponse(html, content_type="text/html")
    response["Content-Disposition"] = 'attachment; filename="purrstone_evidence_record.html"'

    return response
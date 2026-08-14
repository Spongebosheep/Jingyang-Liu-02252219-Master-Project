import json
import os
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client
from django.urls import reverse

from interviews.langgraph_agent import get_openai_model
from interviews.models import (
    AgentDecision,
    InterviewSession,
    Protocol,
    ReviewDecision,
    Stakeholder,
    StructuredDigestItem,
)


class Command(BaseCommand):
    help = (
        "Run a real OpenAI interview-to-review check and roll back every QA record "
        "when the check finishes."
    )

    def handle(self, *args, **options):
        if not os.getenv("OPENAI_API_KEY"):
            raise CommandError(
                "OPENAI_API_KEY is not configured. No live API call was attempted."
            )

        if os.getenv("DISABLE_LLM_FOLLOWUP_WORDING", "").lower() in {
            "1",
            "true",
            "yes",
        }:
            raise CommandError(
                "DISABLE_LLM_FOLLOWUP_WORDING is enabled. Disable it for the live check."
            )

        try:
            with transaction.atomic():
                result = self._run_check()
                transaction.set_rollback(True)
        except CommandError:
            raise
        except Exception as error:
            raise CommandError(
                f"Live OpenAI end-to-end check failed: {type(error).__name__}. "
                "All QA records were rolled back."
            ) from error

        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        self.stdout.write(
            self.style.SUCCESS(
                "PASS: real OpenAI Protocol-coverage check, constrained follow-up, researcher review, "
                "and evidence export completed; QA records were rolled back."
            )
        )

    def _run_check(self):
        suffix = uuid.uuid4().hex[:8]
        model = get_openai_model()
        protocol = Protocol.objects.create(
            title="OpenAI E2E Check Protocol",
            slug=f"openai-e2e-check-{suffix}",
            stakeholder_group="Temporary QA participant",
            purpose="Verify the bounded live OpenAI interview-to-review path.",
            interview_mode="AI-led semi-structured interview",
            estimated_duration="Temporary QA run",
            output_description="Transcript + researcher-reviewed topic evidence",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm the interview boundary and participant control.",
                    "primary_question": "This is design research, not medical advice. Is it okay to continue?",
                    "required_information": ["participant acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "triggers_signs",
                    "label": "Triggers & signs",
                    "purpose": "Identify a sensory trigger and a bodily or emotional sign.",
                    "primary_question": "What triggered the experience, and what signs did you notice?",
                    "required_information": [
                        "sensory or contextual trigger",
                        "body or emotional sign",
                    ],
                },
                {
                    "index": 2,
                    "code": "faithful_summary",
                    "label": "Researcher handoff",
                    "purpose": "Close the interview and prepare reviewable evidence.",
                    "primary_question": "Thank you. Your responses are ready for researcher review.",
                    "required_information": [],
                },
            ],
            ethics_rules=[
                "Ask one question at a time.",
                "Do not provide medical, diagnostic, or therapeutic advice.",
                "Use one bounded follow-up at most for each section.",
                "Ground review material only in participant messages.",
            ],
        )
        stakeholder = Stakeholder.objects.create(
            participant_id=f"E2E-{suffix}",
            stakeholder_group="Temporary QA participant",
            role="Interview participant",
            assigned_protocol=protocol,
        )
        session = InterviewSession.objects.create(
            session_code=f"E2E-{suffix}",
            stakeholder=stakeholder,
            protocol=protocol,
            status=InterviewSession.Status.READY,
        )

        client = Client(HTTP_HOST="localhost")
        consent_response = client.post(
            reverse("interview_consent", args=[session.access_token]),
            {"consent_confirmed": "yes"},
        )
        self._require_redirect(consent_response, "participant consent")

        opening_response = client.post(
            reverse("interview_session", args=[session.access_token]),
            {"action": "send", "reply": "Yes, I understand and want to continue."},
        )
        self._require_redirect(opening_response, "opening acknowledgement")

        partial_answer = (
            "Bright fluorescent lights in the supermarket were the main trigger."
        )
        partial_response = client.post(
            reverse("interview_session", args=[session.access_token]),
            {"action": "send", "reply": partial_answer},
        )
        self._require_redirect(partial_response, "partial participant answer")

        first_decision = AgentDecision.objects.get(
            session=session,
            message__content=partial_answer,
        )
        if (
            first_decision.coverage_assessment
            != AgentDecision.CoverageAssessment.PARTIALLY_COVERED
        ):
            raise CommandError(
                "Live assessment did not classify the trigger-only answer as partial."
            )
        if first_decision.action != AgentDecision.Action.ASK_FOLLOW_UP:
            raise CommandError(
                "LangGraph did not select ASK_FOLLOW_UP for the partial answer."
            )
        if "LLM-assisted provisional cumulative Protocol-coverage check" not in first_decision.decision_reason:
            raise CommandError("The live Protocol-coverage branch was not recorded.")
        if "Fallback used" in first_decision.decision_reason:
            raise CommandError("The Protocol-coverage check used its deterministic fallback.")
        if "Follow-up wording generated by constrained LLM" not in first_decision.decision_reason:
            raise CommandError("The live constrained follow-up branch was not recorded.")
        if "Template follow-up used" in first_decision.decision_reason:
            raise CommandError("The follow-up used its template fallback.")

        follow_up_message = session.messages.filter(
            sender="agent",
            section_index=1,
        ).order_by("-id").first()
        if not follow_up_message or not follow_up_message.content.endswith("?"):
            raise CommandError("A single graph-approved follow-up question was not saved.")

        follow_up_answer = (
            "My heart raced and I felt overwhelmed, so I had to leave."
        )
        completion_response = client.post(
            reverse("interview_session", args=[session.access_token]),
            {"action": "send", "reply": follow_up_answer},
        )
        self._require_redirect(completion_response, "cumulative follow-up answer")

        session.refresh_from_db()
        second_decision = AgentDecision.objects.get(
            session=session,
            message__content=follow_up_answer,
        )
        if (
            second_decision.coverage_assessment
            != AgentDecision.CoverageAssessment.COVERED
        ):
            raise CommandError(
                "The cumulative live assessment did not combine the trigger and sign answers."
            )
        if second_decision.action != AgentDecision.Action.MOVE_NEXT:
            raise CommandError("LangGraph did not move on after cumulative coverage.")
        if "LLM-assisted provisional cumulative Protocol-coverage check" not in second_decision.decision_reason:
            raise CommandError("The second live Protocol-coverage branch was not recorded.")
        if "Fallback used" in second_decision.decision_reason:
            raise CommandError("The second Protocol-coverage check used its fallback.")
        if session.status != InterviewSession.Status.COMPLETED:
            raise CommandError("The temporary interview did not complete.")

        digest_items = list(session.digest_items.prefetch_related("source_messages"))
        if len(digest_items) != 1:
            raise CommandError("The completed check did not create one protocol-grounded topic-evidence item.")
        digest_item = digest_items[0]
        source_texts = set(digest_item.source_messages.values_list("content", flat=True))
        if source_texts != {partial_answer, follow_up_answer}:
            raise CommandError("The topic-evidence source relation did not preserve both participant answers.")

        reviewer_username = f"e2e_reviewer_{suffix}"
        reviewer_password = f"temporary-e2e-{suffix}"
        reviewer = get_user_model().objects.create_user(
            username=reviewer_username,
            password=reviewer_password,
            first_name="MVP",
            last_name="Reviewer",
        )
        if not client.login(
            username=reviewer_username,
            password=reviewer_password,
        ):
            raise CommandError("Researcher authentication failed during the live check.")

        review_page = client.get(reverse("output_detail", args=[session.session_code]))
        if review_page.status_code != 200:
            raise CommandError("The authenticated Output Review page did not render.")

        item_review_response = client.post(
            reverse(
                "review_digest_item",
                args=[session.session_code, digest_item.id],
            ),
            {"digest_action": "approve"},
        )
        self._require_redirect(item_review_response, "topic-evidence inclusion")

        overall_review_response = client.post(
            reverse("output_detail", args=[session.session_code]),
            {
                "decision": "approve",
                "researcher_note": "Live E2E check reviewed against transcript sources.",
                "participant_meaning_preserved": "on",
                "protocol_boundaries_respected": "on",
            },
        )
        self._require_redirect(overall_review_response, "overall researcher approval")

        digest_item.refresh_from_db()
        session.refresh_from_db()
        review_decision = ReviewDecision.objects.get(session=session)
        if digest_item.review_status != StructuredDigestItem.ReviewStatus.APPROVED:
            raise CommandError("The item-level researcher inclusion was not saved.")
        if review_decision.reviewed_by_id != reviewer.id:
            raise CommandError("The overall decision was not attributed to the signed-in reviewer.")
        if session.review_status != InterviewSession.ReviewStatus.APPROVED:
            raise CommandError("The reviewed output did not reach Approved status.")
        if not all(
            [
                review_decision.source_links_checked,
                review_decision.participant_controls_respected,
                review_decision.limitations_and_missing_information_visible,
            ]
        ):
            raise CommandError("A system-checked workflow condition did not pass.")

        evidence_response = client.get(reverse("export_evidence_record"))
        evidence_text = evidence_response.content.decode("utf-8")
        if evidence_response.status_code != 200:
            raise CommandError("The Evidence Record export did not render.")
        if session.session_code not in evidence_text or "MVP Reviewer" not in evidence_text:
            raise CommandError("The Evidence Record omitted the check session or reviewer identity.")

        return {
            "api": "Responses API",
            "database_cleanup": "transaction rolled back",
            "topic_evidence_grounding": "2 participant messages linked",
            "evidence_export": "rendered with reviewer identity",
            "follow_up": follow_up_message.content,
            "live_protocol_coverage_check": True,
            "live_follow_up_wording": True,
            "model": model,
            "participant_access": "public UUID route",
            "researcher_review": "authenticated and attributed",
            "status": "PASS",
        }

    @staticmethod
    def _require_redirect(response, step):
        if response.status_code not in {301, 302, 303, 307, 308}:
            raise CommandError(
                f"The {step} step returned HTTP {response.status_code}, not a redirect."
            )

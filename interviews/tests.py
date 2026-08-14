import os
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .agent import handle_participant_reply, skip_current_question
from .consent import CONSENT_NOTICE_VERSION, build_consent_snapshot_record
from .digest import build_topic_coverage_records, ensure_digest_items
from .langgraph_agent import STOP_TERMS, detect_stop_request
from .models import (
    AgentDecision,
    DigestReviewEvent,
    InterviewSession,
    Message,
    Protocol,
    ReviewDecision,
    Stakeholder,
    StructuredDigestItem,
    Summary,
)
from .protocol_snapshot import build_protocol_snapshot_record
from .release_conditions import (
    evaluate_system_release_conditions,
    validate_included_source_links,
)
from .review_audit import build_review_audit_records
from .views import build_evidence_record_for_session


class AuthenticatedResearcherTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.researcher = get_user_model().objects.create_user(
            username="researcher",
            password="test-password-123",
            first_name="Rina",
            last_name="Reviewer",
            email="rina@example.test",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.researcher)


class ResearcherAccessTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Participants",
            purpose="Test researcher access.",
            sections=[],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )

    def test_anonymous_researcher_page_redirects_to_sign_in(self):
        response = self.client.get(reverse("overview"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('overview')}",
        )

        login_response = self.client.get(reverse("login"))
        self.assertEqual(login_response.status_code, 200)
        self.assertContains(login_response, "Researcher sign in")
        self.assertContains(login_response, "review attribution")

    def test_signed_in_reviewer_identity_is_visible_and_logout_is_post_only(self):
        user = get_user_model().objects.create_user(
            username="rina",
            password="test-password-123",
            first_name="Rina",
            last_name="Reviewer",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Signed in reviewer")
        self.assertContains(response, "Rina Reviewer")
        self.assertContains(response, "Researcher reviewer")
        self.assertNotContains(response, "Researcher reviewer · rina")

        logout_get = self.client.get(reverse("logout"))
        self.assertEqual(logout_get.status_code, 405)
        logout_post = self.client.post(reverse("logout"))
        self.assertRedirects(logout_post, reverse("login"))

    def test_tokenised_participant_consent_remains_public(self):
        anonymous_client = Client()
        response = anonymous_client.get(
            reverse("interview_consent", args=[self.session.access_token])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start interview")


class StakeholderCreationTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
        )
        self.p01 = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
            status=Stakeholder.Status.READY,
            notes="Original evaluated MVP record.",
        )
        self.p01_session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.p01,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )

    def valid_form_data(self, **overrides):
        data = {
            "participant_id": "P02",
            "stakeholder_group": "Qualitative researcher",
            "role": "Researcher reviewer",
            "assigned_protocol": str(self.protocol.pk),
            "notes": "Follow-up researcher record.",
        }
        data.update(overrides)
        return data

    def test_existing_button_opens_stakeholder_creation_form(self):
        list_response = self.client.get(reverse("stakeholder_list"))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, reverse("stakeholder_create"))
        self.assertContains(list_response, "+ Add stakeholder record")

        form_response = self.client.get(reverse("stakeholder_create"))

        self.assertEqual(form_response.status_code, 200)
        self.assertTemplateUsed(form_response, "interviews/stakeholder_form.html")
        for field_name in [
            "participant_id",
            "stakeholder_group",
            "role",
            "assigned_protocol",
            "notes",
        ]:
            self.assertContains(form_response, f'name="{field_name}"')

    def test_valid_submission_creates_only_stakeholder_and_preserves_p01(self):
        p01_before = {
            "stakeholder_group": self.p01.stakeholder_group,
            "role": self.p01.role,
            "assigned_protocol_id": self.p01.assigned_protocol_id,
            "status": self.p01.status,
            "notes": self.p01.notes,
        }

        response = self.client.post(
            reverse("stakeholder_create"),
            self.valid_form_data(participant_id="p02"),
        )

        expected_url = f"{reverse('stakeholder_list')}?selected=P02&created=P02"
        self.assertRedirects(response, expected_url)

        created = Stakeholder.objects.get(participant_id="P02")
        self.assertEqual(created.stakeholder_group, "Qualitative researcher")
        self.assertEqual(created.role, "Researcher reviewer")
        self.assertEqual(created.assigned_protocol, self.protocol)
        self.assertEqual(created.notes, "Follow-up researcher record.")
        self.assertEqual(created.status, Stakeholder.Status.READY)
        self.assertFalse(created.sessions.exists())

        self.p01.refresh_from_db()
        self.assertEqual(
            {
                "stakeholder_group": self.p01.stakeholder_group,
                "role": self.p01.role,
                "assigned_protocol_id": self.p01.assigned_protocol_id,
                "status": self.p01.status,
                "notes": self.p01.notes,
            },
            p01_before,
        )
        self.assertTrue(
            InterviewSession.objects.filter(
                session_code="IS-01",
                stakeholder=self.p01,
            ).exists()
        )

    def test_created_record_uses_the_same_selectable_record_pattern(self):
        stakeholder = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Qualitative researcher",
            role="Researcher reviewer",
            assigned_protocol=self.protocol,
        )

        response = self.client.get(
            reverse("stakeholder_list"),
            {"selected": stakeholder.participant_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participant records")
        self.assertContains(response, "Evaluation evidence is kept in the report")
        self.assertNotContains(response, "Implemented MVP participant record")
        self.assertContains(response, "Create interview session")
        self.assertContains(
            response,
            f"{reverse('interview_session_create')}?stakeholder={stakeholder.participant_id}",
        )
        self.assertContains(
            response,
            reverse("stakeholder_detail", args=[stakeholder.participant_id]),
            count=1,
        )

    def test_duplicate_participant_id_is_rejected_case_insensitively(self):
        response = self.client.post(
            reverse("stakeholder_create"),
            self.valid_form_data(participant_id="p01"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "A stakeholder with this Participant ID already exists.",
        )
        self.assertEqual(Stakeholder.objects.count(), 1)

    def test_participant_id_with_url_unsafe_characters_is_rejected(self):
        response = self.client.post(
            reverse("stakeholder_create"),
            self.valid_form_data(participant_id="P/02"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Use letters, numbers, hyphens, or underscores only.",
        )
        self.assertFalse(Stakeholder.objects.filter(participant_id="P/02").exists())

    def test_record_without_session_uses_safe_detail_page(self):
        stakeholder = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Qualitative researcher",
            role="Researcher reviewer",
            assigned_protocol=self.protocol,
            notes="Independent researcher follow-up.",
        )

        response = self.client.get(
            reverse("stakeholder_detail", args=[stakeholder.participant_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "interviews/stakeholder_record_detail.html",
        )
        self.assertContains(response, "This record has been created")
        self.assertContains(response, "Create interview session")
        self.assertContains(
            response,
            f"{reverse('interview_session_create')}?stakeholder={stakeholder.participant_id}",
        )
        self.assertNotContains(response, "Open participant interview link")


class InterviewSessionCreationTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
        )
        self.p01 = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.p02 = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Qualitative researcher",
            role="Researcher reviewer",
            assigned_protocol=self.protocol,
        )
        self.is01 = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.p01,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )

    def form_data(self, **overrides):
        data = {
            "session_code": "",
            "stakeholder": str(self.p02.pk),
            "protocol": str(self.protocol.pk),
        }
        data.update(overrides)
        return data

    def test_existing_button_opens_session_creation_form(self):
        list_response = self.client.get(reverse("interview_sessions"))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, reverse("interview_session_create"))
        self.assertContains(list_response, "Create interview session")
        self.assertNotContains(
            list_response,
            "Create interview session · Future extension",
        )

        form_response = self.client.get(reverse("interview_session_create"))

        self.assertEqual(form_response.status_code, 200)
        self.assertTemplateUsed(form_response, "interviews/session_form.html")
        for field_name in ["session_code", "stakeholder", "protocol"]:
            self.assertContains(form_response, f'name="{field_name}"')

    def test_stakeholder_query_preselects_record_and_assigned_protocol(self):
        response = self.client.get(
            reverse("interview_session_create"),
            {"stakeholder": self.p02.participant_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["initial_stakeholder"], self.p02)
        self.assertEqual(response.context["form"]["stakeholder"].value(), self.p02.pk)
        self.assertEqual(response.context["form"]["protocol"].value(), self.protocol.pk)

    def test_blank_code_creates_next_ready_session_only(self):
        response = self.client.post(
            reverse("interview_session_create"),
            self.form_data(),
        )

        expected_url = f"{reverse('interview_sessions')}?session=IS-02&created=IS-02"
        self.assertRedirects(response, expected_url)

        created = InterviewSession.objects.get(session_code="IS-02")
        self.assertEqual(created.stakeholder, self.p02)
        self.assertEqual(created.protocol, self.protocol)
        self.assertEqual(created.status, InterviewSession.Status.READY)
        self.assertFalse(created.consent_confirmed)
        self.assertFalse(created.transcript_saved)
        self.assertFalse(created.summary_generated)
        self.assertFalse(created.messages.exists())
        self.assertFalse(created.agent_decisions.exists())

        self.is01.refresh_from_db()
        self.assertEqual(self.is01.stakeholder, self.p01)
        self.assertEqual(self.is01.status, InterviewSession.Status.READY)

        page = self.client.get(expected_url)
        self.assertEqual(page.context["total_sessions"], 2)
        self.assertEqual(page.context["selected_session"], created)
        self.assertContains(
            page,
            "Interview session <strong>IS-02</strong> was created",
        )

    def test_same_stakeholder_can_have_multiple_independent_sessions(self):
        for requested_code in ["IS-02", "IS-03"]:
            response = self.client.post(
                reverse("interview_session_create"),
                self.form_data(
                    session_code=requested_code,
                    stakeholder=str(self.p01.pk),
                ),
            )
            self.assertEqual(response.status_code, 302)

        sessions = list(
            self.p01.sessions.order_by("session_code").values_list(
                "session_code", flat=True
            )
        )
        self.assertEqual(sessions, ["IS-01", "IS-02", "IS-03"])

        detail_response = self.client.get(
            reverse("stakeholder_detail", args=[self.p01.participant_id]),
            {"session": "IS-03"},
        )
        self.assertEqual(detail_response.context["session_count"], 3)
        self.assertEqual(detail_response.context["session"].session_code, "IS-03")

    def test_duplicate_and_unsafe_session_codes_are_rejected(self):
        duplicate_response = self.client.post(
            reverse("interview_session_create"),
            self.form_data(session_code="is-01"),
        )
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertContains(
            duplicate_response,
            "An interview session with this code already exists.",
        )

        unsafe_response = self.client.post(
            reverse("interview_session_create"),
            self.form_data(session_code="IS/02"),
        )
        self.assertEqual(unsafe_response.status_code, 200)
        self.assertContains(
            unsafe_response,
            "Use letters, numbers, hyphens, or underscores only.",
        )
        self.assertEqual(InterviewSession.objects.count(), 1)


class SessionParticipantLinkTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm the boundary.",
                    "primary_question": "Is it okay to continue?",
                    "required_information": ["participant acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "purpose": "Collect one experience.",
                    "primary_question": "Please describe one experience.",
                    "required_information": ["context", "experience", "effect"],
                },
                {
                    "index": 2,
                    "code": "faithful_summary",
                    "label": "Researcher handoff",
                    "purpose": "Close the interview.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.first = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.second = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )

    def test_each_session_has_a_unique_token_url(self):
        self.assertNotEqual(self.first.access_token, self.second.access_token)

        first_url = reverse("interview_consent", args=[self.first.access_token])
        second_url = reverse("interview_consent", args=[self.second.access_token])
        self.assertNotEqual(first_url, second_url)

        first_response = self.client.get(first_url)
        second_response = self.client.get(second_url)
        self.assertEqual(first_response.context["session"], self.first)
        self.assertEqual(second_response.context["session"], self.second)

    def test_consent_and_messages_are_isolated_to_selected_session(self):
        response = self.client.post(
            reverse("interview_consent", args=[self.second.access_token]),
            {"consent_confirmed": "on"},
        )
        self.assertRedirects(
            response,
            reverse("interview_session", args=[self.second.access_token]),
        )

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.first.consent_confirmed)
        self.assertEqual(self.first.status, InterviewSession.Status.READY)
        self.assertEqual(self.first.messages.count(), 0)
        self.assertTrue(self.second.consent_confirmed)
        self.assertEqual(self.second.status, InterviewSession.Status.IN_PROGRESS)
        self.assertEqual(self.second.messages.count(), 1)

    def test_participant_id_is_not_a_participant_portal_identifier(self):
        response = self.client.get(
            f"/interview/{self.stakeholder.participant_id}/consent/"
        )
        self.assertEqual(response.status_code, 404)

    def test_researcher_page_exposes_real_link_status_and_copy_value(self):
        self.first.status = InterviewSession.Status.COMPLETED
        self.first.save(update_fields=["status"])

        response = self.client.get(
            reverse("interview_sessions"),
            {"session": self.second.session_code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active")
        self.assertContains(response, "Completed")
        self.assertNotContains(response, "Step 3 pending")
        expected_absolute_url = (
            "http://testserver"
            + reverse("interview_consent", args=[self.second.access_token])
        )
        self.assertContains(response, expected_absolute_url, count=2)
        self.assertContains(response, "Copy participant link")

    def test_trycloudflare_request_generates_an_https_participant_link(self):
        response = self.client.get(
            reverse("interview_sessions"),
            {"session": self.second.session_code},
            HTTP_HOST="mvp-demo.trycloudflare.com",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, 200)
        expected_absolute_url = (
            "https://mvp-demo.trycloudflare.com"
            + reverse("interview_consent", args=[self.second.access_token])
        )
        self.assertContains(response, expected_absolute_url, count=2)
        self.assertIn(".trycloudflare.com", settings.ALLOWED_HOSTS)
        self.assertIn("https://*.trycloudflare.com", settings.CSRF_TRUSTED_ORIGINS)
        self.assertEqual(
            settings.SECURE_PROXY_SSL_HEADER,
            ("HTTP_X_FORWARDED_PROTO", "https"),
        )


class ConsentTransparencyTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            title="Workplace Decision Interview",
            slug="workplace-decision-interview",
            stakeholder_group="Employees",
            purpose="Understand how employees describe one workplace decision.",
            interview_mode="AI-led semi-structured interview",
            estimated_duration="About 10 minutes",
            output_description="Typed transcript and researcher-reviewed Evidence Record",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "decision_context",
                    "label": "Decision context",
                    "primary_question": "What decision did you make?",
                    "required_information": ["decision", "context"],
                },
                {
                    "index": 2,
                    "code": "completion",
                    "label": "Completion",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P-CONSENT",
            stakeholder_group="Employee",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-CONSENT",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.url = reverse(
            "interview_consent",
            args=[self.session.access_token],
        )

    def test_notice_renders_the_same_canonical_snapshot_and_missing_checkbox_saves_nothing(self):
        expected = build_consent_snapshot_record(self.protocol)
        rendered = self.client.get(self.url)

        self.assertEqual(rendered.status_code, 200)
        self.assertEqual(rendered.context["consent_notice"], expected["snapshot"])
        for statement in expected["snapshot"]["ai_role"].values():
            self.assertContains(rendered, statement)
        self.assertContains(rendered, CONSENT_NOTICE_VERSION)

        rejected = self.client.post(self.url, {})
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "Please confirm consent")
        self.session.refresh_from_db()
        self.assertFalse(self.session.consent_confirmed)
        self.assertIsNone(self.session.consent_confirmed_at)
        self.assertEqual(self.session.consent_notice_version, "")
        self.assertEqual(self.session.consent_snapshot, {})
        self.assertEqual(self.session.consent_snapshot_sha256, "")
        self.assertFalse(self.session.messages.exists())
        self.assertFalse(self.session.agent_decisions.exists())

    def test_acceptance_stores_version_exact_snapshot_timestamp_and_valid_sha256(self):
        expected = build_consent_snapshot_record(self.protocol)
        response = self.client.post(self.url, {"consent_confirmed": "yes"})

        self.assertRedirects(
            response,
            reverse("interview_session", args=[self.session.access_token]),
        )
        self.session.refresh_from_db()
        self.assertTrue(self.session.consent_confirmed)
        self.assertEqual(self.session.consent_notice_version, CONSENT_NOTICE_VERSION)
        self.assertEqual(self.session.consent_snapshot, expected["snapshot"])
        self.assertEqual(self.session.consent_snapshot_sha256, expected["sha256"])
        self.assertIsNotNone(self.session.consent_confirmed_at)
        self.assertIsNotNone(self.session.started_at)
        self.assertTrue(self.session.consent_snapshot_is_valid)

    def test_protocol_specific_details_change_without_changing_core_ai_notice(self):
        second_protocol = Protocol.objects.create(
            title="Community Transport Interview",
            slug="community-transport-interview",
            stakeholder_group="Public transport passengers",
            purpose="Understand passenger experiences during service disruption.",
            interview_mode="Remote typed AI-led interview",
            estimated_duration="About 15 minutes",
            output_description="Researcher-reviewed transport evidence summary",
            sections=[
                {"index": 0, "code": "opening", "label": "Opening"},
                {"index": 1, "code": "journey", "label": "Journey experience"},
                {"index": 2, "code": "completion", "label": "Completion"},
            ],
        )
        first = build_consent_snapshot_record(self.protocol)
        second = build_consent_snapshot_record(second_protocol)

        self.assertEqual(first["snapshot"]["ai_role"], second["snapshot"]["ai_role"])
        self.assertEqual(
            first["snapshot"]["participant_rights"],
            second["snapshot"]["participant_rights"],
        )
        self.assertNotEqual(first["snapshot"]["protocol"], second["snapshot"]["protocol"])
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_snapshot_tampering_invalidates_the_recorded_hash(self):
        self.client.post(self.url, {"consent_confirmed": "yes"})
        self.session.refresh_from_db()
        changed = dict(self.session.consent_snapshot)
        changed["acknowledgement"] = "Changed after confirmation."
        self.session.consent_snapshot = changed
        self.session.save(update_fields=["consent_snapshot"])
        self.session.refresh_from_db()

        self.assertFalse(self.session.consent_snapshot_is_valid)


class ParticipantControlFlowTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Participant Control Interview",
            slug="participant-control-interview",
            stakeholder_group="Participants",
            purpose="Audit Consent, Skip, and Stop controls.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm consent and control.",
                    "primary_question": "Is it okay to continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "purpose": "Collect one experience.",
                    "primary_question": "What happened and what effect did it have?",
                    "required_information": ["context", "effect"],
                },
                {
                    "index": 2,
                    "code": "completion",
                    "label": "Completion",
                    "purpose": "Close and hand off for review.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P-CONTROL",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-CONTROL",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.participant_client = Client()
        self.consent_url = reverse(
            "interview_consent",
            args=[self.session.access_token],
        )
        self.interview_url = reverse(
            "interview_session",
            args=[self.session.access_token],
        )
        self.completed_url = reverse(
            "interview_completed",
            args=[self.session.access_token],
        )

    def start_to_research_topic(self):
        self.participant_client.post(
            self.consent_url,
            {"consent_confirmed": "on"},
        )
        self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "yes"},
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_section_index, 1)

    def send_partial_answer(self):
        self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "Bright fluorescent lights"},
        )
        self.assertEqual(
            self.session.agent_decisions.filter(
                section_index=1,
                action=AgentDecision.Action.ASK_FOLLOW_UP,
            ).count(),
            1,
        )

    def control_condition(self):
        items = ensure_digest_items(self.session)
        conditions = evaluate_system_release_conditions(self.session, items)
        return next(
            condition
            for condition in conditions
            if condition["field"] == "participant_controls_respected"
        )

    def test_participant_route_requires_consent_and_records_consent_time(self):
        blocked = self.participant_client.post(
            self.interview_url,
            {"action": "stop"},
        )
        self.assertRedirects(blocked, self.consent_url)
        self.assertEqual(self.session.agent_decisions.count(), 0)
        self.assertEqual(self.session.messages.count(), 0)

        accepted = self.participant_client.post(
            self.consent_url,
            {"consent_confirmed": "on"},
        )
        self.assertRedirects(accepted, self.interview_url)
        self.session.refresh_from_db()
        self.assertTrue(self.session.consent_confirmed)
        self.assertIsNotNone(self.session.consent_confirmed_at)
        self.assertEqual(self.session.consent_confirmed_at, self.session.started_at)

    def test_actual_skip_after_partial_is_visible_and_ends_that_topic(self):
        self.start_to_research_topic()
        self.send_partial_answer()

        skipped = self.participant_client.post(
            self.interview_url,
            {"action": "skip"},
        )
        self.assertRedirects(skipped, self.completed_url)
        self.session.refresh_from_db()
        skip_decision = self.session.agent_decisions.get(
            action=AgentDecision.Action.SKIP
        )
        self.assertEqual(skip_decision.section_index, 1)
        self.assertEqual(skip_decision.message.sender, Message.Sender.SYSTEM)
        self.assertEqual(skip_decision.message.content, "Participant used the Skip control.")
        self.assertFalse(
            self.session.agent_decisions.filter(
                section_index=1,
                created_at__gt=skip_decision.created_at,
            ).exists()
        )

        completed = self.participant_client.get(self.completed_url)
        self.assertEqual(completed.context["topic_statuses"][0]["status"], "skipped")
        item = ensure_digest_items(self.session)[0]
        self.assertEqual(item.coverage_status, StructuredDigestItem.CoverageStatus.NOT_ASSESSED)
        self.assertFalse(item.is_evidence_candidate)
        self.assertTrue(self.control_condition()["met"])

        evidence = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.session.session_code},
        )
        self.assertContains(evidence, "Consent confirmed at")
        self.assertContains(evidence, "Participant used the Skip control.")
        self.assertContains(evidence, "Experience was skipped.")

    def test_actual_stop_closes_collection_and_corruption_fails_the_gate(self):
        self.start_to_research_topic()
        self.send_partial_answer()

        stopped = self.participant_client.post(
            self.interview_url,
            {"action": "stop"},
        )
        self.assertRedirects(stopped, self.completed_url)
        self.session.refresh_from_db()
        stop_decision = self.session.agent_decisions.get(
            action=AgentDecision.Action.STOP
        )
        self.assertEqual(self.session.status, InterviewSession.Status.STOPPED)
        self.assertEqual(stop_decision.section_index, 1)
        self.assertEqual(stop_decision.message.sender, Message.Sender.SYSTEM)
        self.assertEqual(stop_decision.message.content, "Participant used the Stop control.")

        decision_count_at_stop = self.session.agent_decisions.count()
        message_count_at_stop = self.session.messages.count()
        blocked_retry = self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "This must not be collected."},
        )
        self.assertRedirects(blocked_retry, self.completed_url)
        self.assertEqual(self.session.agent_decisions.count(), decision_count_at_stop)
        self.assertEqual(self.session.messages.count(), message_count_at_stop)

        completed = self.participant_client.get(self.completed_url)
        self.assertContains(completed, "Interview stopped")
        self.assertEqual(completed.context["topic_statuses"][0]["status"], "stopped")
        item = ensure_digest_items(self.session)[0]
        self.assertEqual(item.coverage_status, StructuredDigestItem.CoverageStatus.NOT_ASSESSED)
        self.assertFalse(item.is_evidence_candidate)
        self.assertTrue(self.control_condition()["met"])

        evidence = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.session.session_code},
        )
        self.assertContains(evidence, "Consent confirmed at")
        self.assertContains(evidence, "Participant used the Stop control.")
        self.assertContains(evidence, "Experience was stopped before completion.")

        AgentDecision.objects.create(
            session=self.session,
            message=None,
            section="Completion",
            section_index=2,
            coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
            action=AgentDecision.Action.MOVE_NEXT,
            decision_reason="Deliberate post-Stop corruption for a negative test.",
        )
        self.assertFalse(self.control_condition()["met"])

    @patch(
        "interviews.langgraph_agent.llm_assess_protocol_coverage",
        return_value={
            "coverage_assessment": AgentDecision.CoverageAssessment.COVERED,
            "covered_information": ["context", "effect"],
            "missing_information": [],
            "evidence_quote": "The train stopped between stations.",
            "decision_reason": "Semantic assessment test response.",
        },
    )
    def test_incidental_stop_words_do_not_end_the_interview(self, _assessment):
        self.start_to_research_topic()

        response = self.participant_client.post(
            self.interview_url,
            {
                "action": "send",
                "reply": (
                    "The train stopped between stations while alarms sounded. "
                    "I left at the next stop because the crowd felt overwhelming."
                ),
            },
        )

        self.assertRedirects(response, self.completed_url)
        self.session.refresh_from_db()
        decision = self.session.agent_decisions.get(section_index=1)
        self.assertEqual(decision.action, AgentDecision.Action.MOVE_NEXT)
        self.assertEqual(self.session.status, InterviewSession.Status.COMPLETED)
        self.assertFalse(
            self.session.agent_decisions.filter(
                action=AgentDecision.Action.STOP
            ).exists()
        )

    def test_stop_detector_preserves_explicit_requests_only(self):
        for phrase in STOP_TERMS:
            with self.subTest(explicit_request=phrase):
                self.assertTrue(detect_stop_request(phrase))

        for narrative in [
            "The train stopped between stations.",
            "I left at the next stop.",
            "The bus stop was crowded.",
        ]:
            with self.subTest(narrative=narrative):
                self.assertFalse(detect_stop_request(narrative))


class CoverageStateVisibilityTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Coverage State Protocol",
            slug="coverage-state-protocol",
            stakeholder_group="Participants",
            purpose="Audit explicit topic coverage states.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "triggers_signs",
                    "label": "Triggers & signs",
                    "primary_question": "What triggered it and what sign did you notice?",
                    "required_information": [
                        "sensory trigger",
                        "body or emotional sign",
                    ],
                },
                {
                    "index": 2,
                    "code": "coping_support",
                    "label": "Coping & support",
                    "primary_question": "What helped or would have helped?",
                    "required_information": ["coping action", "support need"],
                },
                {
                    "index": 3,
                    "code": "public_use_acceptability",
                    "label": "Public use",
                    "primary_question": "Would public use feel acceptable?",
                    "required_information": ["public context", "condition or concern"],
                },
                {
                    "index": 4,
                    "code": "completion",
                    "label": "Completion",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P-COVERAGE",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-COVERAGE",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.participant_client = Client()
        self.consent_url = reverse(
            "interview_consent",
            args=[self.session.access_token],
        )
        self.interview_url = reverse(
            "interview_session",
            args=[self.session.access_token],
        )

    def start_to_first_topic(self):
        self.participant_client.post(
            self.consent_url,
            {"consent_confirmed": "on"},
        )
        self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "yes"},
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_section_index, 1)

    def test_partial_state_and_missing_fields_match_review_and_evidence_record(self):
        self.start_to_first_topic()
        self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "Bright fluorescent lights"},
        )
        self.participant_client.post(
            self.interview_url,
            {"action": "send", "reply": "Bright lights again"},
        )
        self.participant_client.post(self.interview_url, {"action": "skip"})
        completed = self.participant_client.post(
            self.interview_url,
            {"action": "skip"},
        )
        self.assertRedirects(
            completed,
            reverse("interview_completed", args=[self.session.access_token]),
        )
        self.session.refresh_from_db()

        items = ensure_digest_items(self.session)
        self.assertEqual(
            [item.coverage_status for item in items],
            [
                StructuredDigestItem.CoverageStatus.PARTIALLY_COVERED,
                StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
                StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
            ],
        )
        self.assertIn("body or emotional sign", items[0].missing_information)

        coverage_records = build_topic_coverage_records(items)
        self.assertEqual(coverage_records[0]["coverage_status"], "partially_covered")
        self.assertEqual(
            coverage_records[0]["missing_information"],
            items[0].missing_information,
        )
        self.assertTrue(coverage_records[0]["is_evidence_candidate"])
        self.assertTrue(all(row["display_visible"] for row in coverage_records))
        self.assertTrue(all(row["export_visible"] for row in coverage_records))

        review = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )
        self.assertContains(review, "Partial")
        self.assertContains(review, "Still missing:")
        self.assertContains(review, "body or emotional sign")

        evidence = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.session.session_code},
        )
        self.assertContains(evidence, "Partial")
        self.assertContains(evidence, "has missing Protocol information")
        self.assertContains(evidence, "body or emotional sign")

    def test_skip_stop_and_not_reached_are_consistent_and_cannot_be_included(self):
        self.start_to_first_topic()
        self.participant_client.post(self.interview_url, {"action": "skip"})
        stopped = self.participant_client.post(
            self.interview_url,
            {"action": "stop"},
        )
        self.assertRedirects(
            stopped,
            reverse("interview_completed", args=[self.session.access_token]),
        )
        self.session.refresh_from_db()

        items = ensure_digest_items(self.session)
        expected_statuses = [
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
            StructuredDigestItem.CoverageStatus.NOT_ASSESSED,
        ]
        self.assertEqual(
            [item.coverage_status for item in items],
            expected_statuses,
        )
        self.assertEqual(
            [item.participant_control for item in items],
            [
                StructuredDigestItem.ParticipantControl.SKIP,
                StructuredDigestItem.ParticipantControl.STOP,
                StructuredDigestItem.ParticipantControl.NONE,
            ],
        )
        self.assertEqual(
            [item.topic_reached for item in items],
            [True, True, False],
        )
        self.assertTrue(all(not item.is_evidence_candidate for item in items))

        completed = self.participant_client.get(
            reverse("interview_completed", args=[self.session.access_token])
        )
        self.assertEqual(
            [row["status"] for row in completed.context["topic_statuses"]],
            ["skipped", "stopped", "not_reached"],
        )

        review = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )
        self.assertEqual(
            [item.coverage_status for item in review.context["digest_items"]],
            expected_statuses,
        )
        self.assertContains(review, "Recorded as limitation", count=3)

        evidence = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.session.session_code},
        )
        self.assertContains(evidence, "Topics recorded as limitations")
        self.assertContains(evidence, "Skipped")
        self.assertContains(evidence, "Stopped")
        self.assertContains(evidence, "Not reached")

        for item in items:
            for action, data in (
                (
                    "approve",
                    {
                        "digest_action": "approve",
                        "reviewed_text": item.generated_text,
                    },
                ),
                (
                    "edit",
                    {
                        "digest_action": "edit",
                        "reviewed_text": "A prohibited edited extract.",
                        "reviewer_comment": "Negative test.",
                    },
                ),
            ):
                with self.subTest(status=item.coverage_status, action=action):
                    blocked = self.client.post(
                        reverse(
                            "review_digest_item",
                            args=[self.session.session_code, item.id],
                        ),
                        data,
                        follow=True,
                    )
                    self.assertContains(
                        blocked,
                        "cannot be included as a participant extract",
                    )
                    item.refresh_from_db()
                    self.assertEqual(
                        item.review_status,
                        StructuredDigestItem.ReviewStatus.PENDING,
                    )


class OpenAILiveCheckCommandTests(TestCase):
    @patch("interviews.langgraph_agent.OpenAI")
    def test_command_does_not_claim_live_success_without_a_key(self, mock_openai):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesMessage(
                CommandError,
                "OPENAI_API_KEY is not configured. No live API call was attempted.",
            ):
                call_command("run_openai_e2e_check")

        mock_openai.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["localhost"])
    @patch("interviews.langgraph_agent.OpenAI")
    def test_command_checks_live_branches_and_rolls_back_qa_records(self, mock_openai):
        mock_openai.return_value.responses.create.side_effect = [
            SimpleNamespace(
                output_text=(
                    '{"coverage_assessment":"partially_covered",'
                    '"covered_information":["sensory or contextual trigger"],'
                    '"missing_information":["body or emotional sign"],'
                    '"evidence_quote":"Bright fluorescent lights",'
                    '"assessment_reason":"A trigger is present but a sign is missing."}'
                )
            ),
            SimpleNamespace(
                output_text=(
                    "What did you notice in your body or emotions when the lights felt bright?"
                )
            ),
            SimpleNamespace(
                output_text=(
                    '{"coverage_assessment":"covered",'
                    '"covered_information":["sensory or contextual trigger",'
                    '"body or emotional sign"],'
                    '"missing_information":[],"evidence_quote":"My heart raced",'
                    '"assessment_reason":"The cumulative answers include a trigger and a sign."}'
                )
            ),
        ]

        output = StringIO()
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key-not-sent",
                "PURRSTONE_OPENAI_MODEL": "gpt-4.1-mini",
            },
            clear=False,
        ):
            os.environ.pop("DISABLE_LLM_FOLLOWUP_WORDING", None)
            call_command("run_openai_e2e_check", stdout=output)

        self.assertIn('"status": "PASS"', output.getvalue())
        self.assertIn('"live_protocol_coverage_check": true', output.getvalue())
        self.assertIn('"live_follow_up_wording": true', output.getvalue())
        self.assertEqual(mock_openai.return_value.responses.create.call_count, 3)

        calls = mock_openai.return_value.responses.create.call_args_list
        self.assertEqual(calls[0].kwargs["model"], "gpt-4.1-mini")
        self.assertFalse(calls[0].kwargs["store"])
        self.assertEqual(
            calls[0].kwargs["text"]["format"]["type"],
            "json_schema",
        )
        self.assertTrue(calls[0].kwargs["text"]["format"]["strict"])
        self.assertFalse(calls[1].kwargs["store"])
        self.assertFalse(calls[2].kwargs["store"])

        self.assertFalse(
            Protocol.objects.filter(slug__startswith="openai-e2e-check-").exists()
        )
        self.assertFalse(
            Stakeholder.objects.filter(participant_id__startswith="E2E-").exists()
        )
        self.assertFalse(
            InterviewSession.objects.filter(session_code__startswith="E2E-").exists()
        )


class ProtocolVersioningTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.sections = [
            {
                "index": 0,
                "code": "opening",
                "label": "Opening",
                "purpose": "Confirm purpose, boundary, and control.",
                "primary_question": "Is it okay to continue?",
                "required_information": ["participant acknowledgement"],
            },
            {
                "index": 1,
                "code": "experience",
                "label": "Experience",
                "purpose": "Collect one concrete experience.",
                "primary_question": "Please describe one recent experience.",
                "required_information": ["where", "what happened", "effect"],
            },
            {
                "index": 2,
                "code": "faithful_summary",
                "label": "Researcher handoff",
                "purpose": "Close and hand off for review.",
                "primary_question": "Thank you. Your transcript will be reviewed.",
                "required_information": [],
            },
        ]
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
            sections=self.sections,
            ethics_rules=[
                "Ask one question at a time.",
                "Do not provide medical advice.",
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )

    def edit_data(self, protocol=None, **overrides):
        protocol = protocol or self.protocol
        sections = protocol.sections or []
        data = {
            "title": protocol.title,
            "stakeholder_group": protocol.stakeholder_group,
            "purpose": protocol.purpose,
            "interview_mode": protocol.interview_mode,
            "estimated_duration": protocol.estimated_duration,
            "output_description": protocol.output_description,
            "ethics_rules_text": "\n".join(protocol.ethics_rules or []),
            "section_code": [section.get("code", "") for section in sections],
            "section_label": [section.get("label", "") for section in sections],
            "section_purpose": [section.get("purpose", "") for section in sections],
            "section_question": [
                section.get("primary_question", "") for section in sections
            ],
            "section_required": [
                "\n".join(section.get("required_information", []))
                for section in sections
            ],
        }
        data.update(overrides)
        return data

    def test_draft_detail_exposes_minimal_version_actions(self):
        response = self.client.get(
            reverse("protocol_detail", args=[self.protocol.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "v1 · Draft")
        self.assertContains(response, reverse("protocol_edit", args=[self.protocol.slug]))
        self.assertContains(response, reverse("protocol_lock", args=[self.protocol.slug]))
        self.assertContains(
            response,
            reverse("protocol_duplicate", args=[self.protocol.slug]),
        )
        self.assertNotContains(response, "Future extension")

    def test_protocol_library_can_create_a_separate_configurable_family(self):
        library = self.client.get(reverse("protocol_list"))
        self.assertEqual(library.status_code, 200)
        self.assertContains(library, "Protocol library")
        self.assertContains(library, self.protocol.title)
        self.assertContains(library, "only protocol evaluated in this project")

        response = self.client.post(
            reverse("protocol_create"),
            {
                "title": "Workplace Experience Interview",
                "stakeholder_group": "Employees",
                "purpose": "Explore one workplace experience.",
                "interview_mode": "AI-led semi-structured interview",
                "estimated_duration": "8–10 minutes",
                "output_description": "Transcript + topic evidence",
                "ethics_rules_text": (
                    "Ask one question at a time.\n"
                    "Respect skip and stop requests.\n"
                    "Do not provide medical advice."
                ),
                "section_code": [
                    "opening",
                    "work_context",
                    "support_needs",
                    "completion",
                ],
                "section_label": [
                    "Opening",
                    "Work context",
                    "Support needs",
                    "Completion",
                ],
                "section_purpose": [
                    "Confirm purpose and control.",
                    "Explore the work setting.",
                    "Explore support needs.",
                    "Close and hand off for review.",
                ],
                "section_question": [
                    "Is it okay to continue?",
                    "Please describe the work setting.",
                    "What support would have helped?",
                    "Thank you. Your transcript will be reviewed.",
                ],
                "section_required": [
                    "acknowledgement",
                    "setting\nexperience",
                    "support need",
                    "researcher handoff",
                ],
                "section_assessment_guidance": [
                    "",
                    "Protocol coverage requires the setting and one experienced effect.",
                    "Protocol coverage requires a support need and why it matters.",
                    "",
                ],
                "section_follow_up_focus": [
                    "",
                    "the work setting or experienced effect",
                    "the support need and why it would help",
                    "",
                ],
                "section_interaction_boundary": [
                    "",
                    "Do not ask for confidential employer information.",
                    "Do not offer workplace or health advice.",
                    "",
                ],
            },
        )

        created = Protocol.objects.get(title="Workplace Experience Interview")
        self.assertRedirects(
            response,
            f"{reverse('protocol_detail', args=[created.slug])}?created=1",
        )
        self.assertEqual(created.version, 1)
        self.assertNotEqual(created.family_id, self.protocol.family_id)
        self.assertEqual(
            [section["code"] for section in created.sections],
            ["opening", "work_context", "support_needs", "completion"],
        )
        self.assertEqual(
            [section["index"] for section in created.sections],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            created.sections[1]["follow_up_focus"],
            "the work setting or experienced effect",
        )
        self.assertEqual(
            created.sections[2]["interaction_boundary"],
            "Do not offer workplace or health advice.",
        )

        detail = self.client.get(reverse("protocol_detail", args=[created.slug]))
        self.assertContains(detail, "Runtime behaviour")
        self.assertContains(detail, "Configurable workflow only · not yet validated")
        self.assertContains(detail, "the work setting or experienced effect")

    def test_placeholder_question_and_requirement_cannot_be_saved_as_a_protocol(self):
        response = self.client.post(
            reverse("protocol_create"),
            {
                "title": "Placeholder Interview",
                "stakeholder_group": "Participants",
                "purpose": "Test placeholder blocking.",
                "interview_mode": "AI-led semi-structured interview",
                "estimated_duration": "8 minutes",
                "output_description": "Transcript + topic evidence",
                "ethics_rules_text": "Respect Skip and Stop.",
                "section_code": ["opening", "topic", "completion"],
                "section_label": ["Opening", "Topic", "Completion"],
                "section_purpose": ["Open.", "Explore a topic.", "Close."],
                "section_question": [
                    "Continue?",
                    "Enter the first research question here.",
                    "Thank you.",
                ],
                "section_required": [
                    "acknowledgement",
                    "information needed for this topic",
                    "handoff",
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "still contains a placeholder question")
        self.assertFalse(Protocol.objects.filter(title="Placeholder Interview").exists())

    def test_draft_edit_updates_content_but_preserves_code_order_and_identity(self):
        original_family = self.protocol.family_id
        original_slug = self.protocol.slug
        questions = [
            section["primary_question"] for section in self.protocol.sections
        ]
        questions[1] = "Describe one specific overwhelm situation and its effect."
        required = [
            "\n".join(section.get("required_information", []))
            for section in self.protocol.sections
        ]
        required[1] = "setting\nwhat happened\nwhy it felt overwhelming"

        response = self.client.post(
            reverse("protocol_edit", args=[self.protocol.slug]),
            self.edit_data(
                title="Sensory Overload Interview – revised wording",
                section_question=questions,
                section_required=required,
                ethics_rules_text=(
                    "Ask one question at a time.\n"
                    "Do not provide medical advice.\n"
                    "Respect skip and stop requests."
                ),
            ),
        )

        self.assertRedirects(
            response,
            f"{reverse('protocol_detail', args=[original_slug])}?updated=1",
        )
        self.protocol.refresh_from_db()
        self.assertEqual(
            self.protocol.title,
            "Sensory Overload Interview – revised wording",
        )
        self.assertEqual(self.protocol.slug, original_slug)
        self.assertEqual(self.protocol.family_id, original_family)
        self.assertEqual(self.protocol.version, 1)
        self.assertEqual(self.protocol.status, Protocol.Status.DRAFT)
        self.assertEqual(
            [section["code"] for section in self.protocol.sections],
            ["opening", "experience", "faithful_summary"],
        )
        self.assertEqual(
            self.protocol.sections[1]["required_information"],
            ["setting", "what happened", "why it felt overwhelming"],
        )
        self.assertEqual(len(self.protocol.ethics_rules), 3)

    def test_research_section_cannot_be_saved_without_required_information(self):
        required = [
            "\n".join(section.get("required_information", []))
            for section in self.protocol.sections
        ]
        required[1] = ""

        response = self.client.post(
            reverse("protocol_edit", args=[self.protocol.slug]),
            self.edit_data(section_required=required),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Research section &#x27;Experience&#x27; needs at least one required-information item.",
        )
        self.protocol.refresh_from_db()
        self.assertEqual(
            self.protocol.sections[1]["required_information"],
            ["where", "what happened", "effect"],
        )

    def test_manual_lock_blocks_editing(self):
        response = self.client.post(
            reverse("protocol_lock", args=[self.protocol.slug])
        )
        self.assertRedirects(
            response,
            f"{reverse('protocol_detail', args=[self.protocol.slug])}?locked=1",
        )

        self.protocol.refresh_from_db()
        self.assertEqual(self.protocol.status, Protocol.Status.LOCKED)
        self.assertIsNotNone(self.protocol.locked_at)

        edit_response = self.client.get(
            reverse("protocol_edit", args=[self.protocol.slug])
        )
        self.assertEqual(edit_response.status_code, 403)

    def test_creating_session_locks_used_version(self):
        session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )

        self.protocol.refresh_from_db()
        self.assertEqual(self.protocol.status, Protocol.Status.LOCKED)
        self.assertIsNotNone(self.protocol.locked_at)
        self.assertEqual(session.protocol_id, self.protocol.pk)
        self.assertEqual(
            self.client.get(
                reverse("protocol_edit", args=[self.protocol.slug])
            ).status_code,
            403,
        )

    def test_locked_protocol_rejects_model_level_content_changes_and_unlocking(self):
        InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.protocol.refresh_from_db()
        original_title = self.protocol.title
        original_locked_at = self.protocol.locked_at

        self.protocol.title = "Changed after the Session was created"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            self.protocol.save(update_fields=["title", "updated_at"])

        self.protocol.refresh_from_db()
        self.assertEqual(self.protocol.title, original_title)
        self.protocol.status = Protocol.Status.DRAFT
        self.protocol.locked_at = None
        with self.assertRaisesMessage(ValidationError, "immutable"):
            self.protocol.save(update_fields=["status", "locked_at", "updated_at"])

        self.protocol.refresh_from_db()
        self.assertEqual(self.protocol.status, Protocol.Status.LOCKED)
        self.assertEqual(self.protocol.locked_at, original_locked_at)

    def test_locked_protocol_snapshot_and_hash_are_stable_and_complete(self):
        InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.protocol.refresh_from_db()

        first = build_protocol_snapshot_record(self.protocol)
        second = build_protocol_snapshot_record(self.protocol)

        self.assertEqual(first, second)
        self.assertEqual(len(first["sha256"]), 64)
        self.assertEqual(first["snapshot"]["configuration"]["title"], self.protocol.title)
        self.assertEqual(first["snapshot"]["identity"]["version"], 1)
        self.assertEqual(first["snapshot"]["identity"]["status"], Protocol.Status.LOCKED)
        self.assertEqual(first["snapshot"]["configuration"]["sections"], self.sections)
        self.assertEqual(
            first["snapshot"]["configuration"]["ethics_rules"],
            self.protocol.ethics_rules,
        )
        self.assertEqual(first["snapshot"]["runtime"]["max_probes_per_section"], 1)

    def test_copy_creates_editable_next_version_without_changing_old_session(self):
        old_session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.READY,
        )
        self.protocol.refresh_from_db()

        response = self.client.post(
            reverse("protocol_duplicate", args=[self.protocol.slug])
        )

        copied = Protocol.objects.get(family_id=self.protocol.family_id, version=2)
        self.assertRedirects(
            response,
            f"{reverse('protocol_detail', args=[copied.slug])}?copied=1",
        )
        self.assertEqual(copied.status, Protocol.Status.DRAFT)
        self.assertIsNone(copied.locked_at)
        self.assertEqual(copied.parent_version, self.protocol)
        self.assertEqual(copied.sections, self.protocol.sections)
        self.assertEqual(copied.ethics_rules, self.protocol.ethics_rules)
        self.assertNotEqual(copied.slug, self.protocol.slug)

        old_session.refresh_from_db()
        self.assertEqual(old_session.protocol, self.protocol)

        copied_questions = [
            section["primary_question"] for section in copied.sections
        ]
        copied_questions[1] = "Edited only in v2."
        edit_response = self.client.post(
            reverse("protocol_edit", args=[copied.slug]),
            self.edit_data(
                protocol=copied,
                section_question=copied_questions,
            ),
        )
        self.assertEqual(edit_response.status_code, 302)

        copied.refresh_from_db()
        self.protocol.refresh_from_db()
        self.assertEqual(copied.sections[1]["primary_question"], "Edited only in v2.")
        self.assertEqual(
            self.protocol.sections[1]["primary_question"],
            "Please describe one recent experience.",
        )

        detail_response = self.client.get(
            reverse("protocol_detail", args=[self.protocol.slug])
        )
        self.assertContains(
            detail_response,
            '<strong class="protocol-current-version">v1 (current)</strong>',
            html=True,
            count=1,
        )
        self.assertContains(
            detail_response,
            reverse("protocol_detail", args=[copied.slug]),
        )


class CumulativeAnswerAssessmentTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm the boundary.",
                    "primary_question": "Is it okay to continue?",
                    "required_information": ["participant acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "triggers_signs",
                    "label": "Triggers & signs",
                    "purpose": "Identify a trigger and a sign.",
                    "primary_question": "What triggered the experience and what signs did you notice?",
                    "required_information": ["sensory trigger", "body or emotional sign"],
                },
                {
                    "index": 2,
                    "code": "faithful_summary",
                    "label": "Researcher handoff",
                    "purpose": "Close the interview.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            consent_confirmed=True,
            current_section_index=1,
        )

    @patch(
        "interviews.langgraph_agent.llm_assess_protocol_coverage",
        side_effect=RuntimeError("Deterministic test fallback"),
    )
    def test_follow_up_is_assessed_with_initial_answer_cumulatively(self, _mock_llm):
        handle_participant_reply(self.session, "Bright fluorescent lights")

        first_decision = self.session.agent_decisions.get()
        self.assertEqual(
            first_decision.action,
            AgentDecision.Action.ASK_FOLLOW_UP,
        )
        self.assertEqual(first_decision.probe_count_before, 0)

        handle_participant_reply(self.session, "Shoulders tense")

        decisions = list(self.session.agent_decisions.order_by("id"))
        self.assertEqual(len(decisions), 2)
        self.assertEqual(
            decisions[1].coverage_assessment,
            AgentDecision.CoverageAssessment.COVERED,
        )
        self.assertEqual(decisions[1].action, AgentDecision.Action.MOVE_NEXT)
        self.assertEqual(decisions[1].probe_count_before, 1)
        self.assertIn("Cumulative section assessment", decisions[1].decision_reason)

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, InterviewSession.Status.COMPLETED)
        self.assertEqual(
            list(
                self.session.messages.filter(sender="participant").values_list(
                    "content",
                    flat=True,
                )
            ),
            ["Bright fluorescent lights", "Shoulders tense"],
        )

    @patch(
        "interviews.langgraph_agent.llm_assess_protocol_coverage",
        side_effect=RuntimeError("Deterministic test fallback"),
    )
    def test_one_follow_up_oracle_flags_missing_and_moves_on_after_second_partial(
        self,
        _mock_llm,
    ):
        handle_participant_reply(self.session, "Bright fluorescent lights")
        handle_participant_reply(
            self.session,
            "The fluorescent lights were still the only trigger I described.",
        )

        decisions = list(self.session.agent_decisions.order_by("id"))
        expected_actions = [
            AgentDecision.Action.ASK_FOLLOW_UP,
            AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT,
        ]

        self.assertEqual([decision.action for decision in decisions], expected_actions)
        self.assertEqual(
            [decision.probe_count_before for decision in decisions],
            [0, 1],
        )
        self.assertEqual(
            self.session.agent_decisions.filter(
                action=AgentDecision.Action.ASK_FOLLOW_UP
            ).count(),
            1,
        )
        self.assertIn("body or emotional sign", decisions[1].missing_information)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, InterviewSession.Status.COMPLETED)

    def test_latest_stop_request_still_overrides_cumulative_content(self):
        handle_participant_reply(self.session, "Bright fluorescent lights")
        handle_participant_reply(self.session, "stop")

        final_decision = self.session.agent_decisions.order_by("id").last()
        self.assertEqual(final_decision.action, AgentDecision.Action.STOP)
        self.assertEqual(
            final_decision.coverage_assessment,
            AgentDecision.CoverageAssessment.NOT_ASSESSED,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, InterviewSession.Status.STOPPED)


class CustomProtocolRuntimeTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            title="Workplace Decision Interview",
            slug="workplace-decision-interview",
            stakeholder_group="Employees",
            purpose="Explore one workplace decision.",
            ethics_rules=[
                "Do not request confidential employer information.",
                "Use responses only for researcher review.",
            ],
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm the boundary.",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "decision_context",
                    "label": "Decision context",
                    "purpose": "Understand one decision and its consequence.",
                    "primary_question": "Describe one recent workplace decision.",
                    "required_information": [
                        "decision rationale",
                        "downstream consequence",
                    ],
                    "assessment_guidance": (
                        "Protocol coverage requires both why the decision was made and what followed."
                    ),
                    "follow_up_focus": (
                        "the reason for the decision and its consequence"
                    ),
                    "interaction_boundary": (
                        "Do not ask for names or confidential employer information."
                    ),
                },
                {
                    "index": 2,
                    "code": "completion",
                    "label": "Completion",
                    "purpose": "Close the interview.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Employee",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            consent_confirmed=True,
            current_section_index=1,
        )

    def test_custom_protocol_consent_uses_protocol_configuration(self):
        self.session.status = InterviewSession.Status.READY
        self.session.consent_confirmed = False
        self.session.current_section_index = 0
        self.session.save(
            update_fields=["status", "consent_confirmed", "current_section_index"]
        )

        response = self.client.get(
            reverse("interview_consent", args=[self.session.access_token])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.protocol.title)
        self.assertContains(response, self.protocol.purpose)
        self.assertContains(response, self.protocol.interview_mode)
        self.assertContains(response, self.protocol.stakeholder_group)
        self.assertContains(response, self.protocol.estimated_duration)
        self.assertContains(response, self.protocol.output_description)
        self.assertNotContains(response, "sensory overload experiences")
        self.assertNotContains(response, "one specific sensory overload experience")

    def test_runtime_uses_the_session_bound_locked_protocol(self):
        from .langgraph_agent import load_session_state

        replacement = Protocol.objects.create(
            title="Replacement Protocol",
            slug="replacement-protocol",
            stakeholder_group="Employees",
            purpose="A later stakeholder assignment that must not affect this Session.",
            ethics_rules=["REPLACEMENT-RULE"],
            sections=[
                {
                    "index": 0,
                    "code": "replacement_opening",
                    "label": "Opening",
                    "purpose": "Open.",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "replacement_topic",
                    "label": "Replacement topic",
                    "purpose": "Replacement.",
                    "primary_question": "Replacement question?",
                    "required_information": ["replacement detail"],
                },
                {
                    "index": 2,
                    "code": "replacement_completion",
                    "label": "Completion",
                    "purpose": "Close.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder.assigned_protocol = replacement
        self.stakeholder.save(update_fields=["assigned_protocol"])

        state = load_session_state(
            {
                "session_id": self.session.id,
                "reply_text": "",
                "control_action": "",
            }
        )

        self.assertEqual(state["current_section_code"], "decision_context")
        self.assertEqual(state["protocol_rules"], self.protocol.ethics_rules)
        self.protocol.refresh_from_db()
        self.assertEqual(self.protocol.status, Protocol.Status.LOCKED)

    def test_custom_protocol_completion_excludes_boundary_sections(self):
        self.session.status = InterviewSession.Status.COMPLETED
        self.session.current_section_index = 2
        self.session.save(update_fields=["status", "current_section_index"])
        Message.objects.create(
            session=self.session,
            sender=Message.Sender.AGENT,
            content="Please describe one recent workplace decision.",
            section="Decision context",
            section_index=1,
        )

        response = self.client.get(
            reverse("interview_completed", args=[self.session.access_token])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["topic_statuses"],
            [
                {
                    "label": "Decision context",
                    "status": "not_assessed",
                    "status_label": "Not assessed",
                }
            ],
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
    def test_long_custom_answer_is_not_declared_covered_by_length_alone(self):
        handle_participant_reply(
            self.session,
            "I can describe this at length, but this sentence deliberately does not name either requested evidence category.",
        )

        decision = self.session.agent_decisions.get()
        self.assertEqual(decision.coverage_assessment, AgentDecision.CoverageAssessment.PARTIALLY_COVERED)
        self.assertEqual(decision.action, AgentDecision.Action.ASK_FOLLOW_UP)
        self.assertEqual(
            decision.missing_information,
            ["decision rationale", "downstream consequence"],
        )
        self.assertIn("conservative Protocol fallback", decision.decision_reason)
        self.assertEqual(
            self.session.messages.filter(sender=Message.Sender.AGENT).last().content,
            "Could you add one concrete detail about the reason for the decision and its consequence?",
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("interviews.langgraph_agent.OpenAI")
    def test_locked_protocol_guidance_and_rules_are_supplied_to_semantic_assessment(
        self,
        mock_openai,
    ):
        from .langgraph_agent import llm_assess_protocol_coverage

        mock_openai.return_value.responses.create.return_value = SimpleNamespace(
            output_text=(
                '{"coverage_assessment":"partially_covered","covered_information":["decision rationale"],'
                '"missing_information":["downstream consequence"],'
                '"evidence_quote":"I chose it because time was short.",'
                '"assessment_reason":"The reason is present but the consequence is missing."}'
            )
        )
        state = {
            "current_section_code": "decision_context",
            "current_section_label": "Decision context",
            "section_purpose": "Understand one decision and its consequence.",
            "required_information": ["decision rationale", "downstream consequence"],
            "assessment_guidance": self.protocol.sections[1]["assessment_guidance"],
            "interaction_boundary": self.protocol.sections[1]["interaction_boundary"],
            "protocol_rules": self.protocol.ethics_rules,
            "reply_text": "I chose it because time was short.",
            "cumulative_reply_text": "I chose it because time was short.",
        }

        result = llm_assess_protocol_coverage(state)
        prompt = mock_openai.return_value.responses.create.call_args.kwargs["input"]

        self.assertEqual(result["coverage_assessment"], AgentDecision.CoverageAssessment.PARTIALLY_COVERED)
        self.assertIn(self.protocol.sections[1]["assessment_guidance"], prompt)
        self.assertIn(self.protocol.sections[1]["interaction_boundary"], prompt)
        self.assertIn(self.protocol.ethics_rules[0], prompt)


class TranscriptAgentDecisionIntegrationTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Confirm boundary.",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "purpose": "Collect an experience.",
                    "primary_question": "Describe one experience.",
                    "required_information": ["context", "effect"],
                },
                {
                    "index": 2,
                    "code": "faithful_summary",
                    "label": "Handoff",
                    "purpose": "Close.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.STOPPED,
            transcript_saved=True,
            summary_generated=True,
        )
        self.question = Message.objects.create(
            session=self.session,
            sender=Message.Sender.AGENT,
            content="Describe one experience.",
            section="Experience",
            section_index=1,
        )
        self.answer = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="The lights were very bright.",
            section="Experience",
            section_index=1,
        )
        self.linked_decision = AgentDecision.objects.create(
            session=self.session,
            message=self.answer,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.PARTIALLY_COVERED,
            action=AgentDecision.Action.ASK_FOLLOW_UP,
            probe_count_before=0,
            covered_information=["context"],
            missing_information=["effect"],
            decision_reason="The setting is present but the experienced effect is missing.",
        )
        self.unlinked_decision = AgentDecision.objects.create(
            session=self.session,
            message=None,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.NOT_ASSESSED,
            action=AgentDecision.Action.STOP,
            probe_count_before=1,
            missing_information=["effect"],
            decision_reason="Participant used the visible Stop control.",
        )

        other_stakeholder = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Researcher",
            role="Reviewer",
            assigned_protocol=self.protocol,
        )
        other_session = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=other_stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.COMPLETED,
            transcript_saved=True,
        )
        other_message = Message.objects.create(
            session=other_session,
            sender=Message.Sender.PARTICIPANT,
            content="This belongs to another session.",
            section="Experience",
            section_index=1,
        )
        self.other_decision = AgentDecision.objects.create(
            session=other_session,
            message=other_message,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
            action=AgentDecision.Action.MOVE_NEXT,
            probe_count_before=0,
            missing_information=[],
            decision_reason="Other session decision marker.",
        )

    def test_full_transcript_embeds_the_decision_for_its_trigger_message(self):
        response = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participant response 1")
        self.assertContains(response, "Why the interview took this step")
        self.assertContains(response, "Asked one follow-up")
        self.assertContains(response, "Before this action: 0 of 1 used")
        self.assertContains(response, "After this action: 1 of 1 used")
        self.assertContains(response, "Reviewed extracts by protocol topic")
        self.assertContains(response, "context")
        self.assertContains(response, "effect")
        self.assertContains(
            response,
            "The setting is present but the experienced effect is missing.",
        )
        self.assertContains(response, "Participant control")
        self.assertNotContains(
            response,
            "Participant controls recorded without a typed reply",
        )
        self.assertContains(response, "Participant used the visible Stop control.")
        self.assertNotContains(response, "Other session decision marker.")
        self.assertNotContains(response, "ADT-")
        self.assertNotContains(response, "View in ADT overview")
        self.assertNotContains(response, "Structured digest")
        self.assertContains(response, "P02 · IS-02")

        html = response.content.decode()
        reviewed_answer = next(
            message
            for message in response.context["messages"]
            if message.id == self.answer.id
        )
        message_position = html.index(f'id="{reviewed_answer.review_anchor}"')
        inline_decision_position = html.index("Why the interview took this step")
        self.assertLess(message_position, inline_decision_position)

    def test_output_selector_redirects_to_the_requested_session(self):
        response = self.client.get(
            reverse("outputs_home"),
            {"session": "IS-02"},
        )
        self.assertRedirects(response, reverse("output_detail", args=["IS-02"]))

    def test_visible_skip_control_is_linked_to_one_inline_transcript_event(self):
        control_session = InterviewSession.objects.create(
            session_code="IS-03",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            consent_confirmed=True,
            current_section_index=1,
        )

        skip_current_question(control_session)
        decision = control_session.agent_decisions.get(
            action=AgentDecision.Action.SKIP
        )
        self.assertIsNotNone(decision.message)
        self.assertEqual(decision.message.sender, Message.Sender.SYSTEM)
        self.assertEqual(decision.message.content, "Participant used the Skip control.")

        response = self.client.get(
            reverse("output_detail", args=[control_session.session_code])
        )
        self.assertContains(response, "Participant used the Skip control.", count=1)
        self.assertContains(response, "Respected the participant&#x27;s Skip choice")
        self.assertNotContains(
            response,
            "Participant controls recorded without a typed reply",
        )


class GeneratedSummarySourceLinkTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Custom Interview",
            slug="custom-interview",
            stakeholder_group="Participants",
            purpose="Test protocol-driven summary sources.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "primary_question": "What happened?",
                    "required_information": ["context", "effect"],
                },
                {
                    "index": 2,
                    "code": "recovery_pattern",
                    "label": "Recovery pattern",
                    "primary_question": "What happened afterwards?",
                    "required_information": ["recovery"],
                },
                {
                    "index": 3,
                    "code": "faithful_summary",
                    "label": "Researcher handoff",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.STOPPED,
            transcript_saved=True,
            summary_generated=True,
        )
        self.agent_message = Message.objects.create(
            session=self.session,
            sender=Message.Sender.AGENT,
            content="What happened?",
            section="Experience",
            section_index=1,
        )
        self.first_answer = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="It happened on a crowded train.",
            section="Experience",
            section_index=1,
        )
        self.second_answer = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="The noise made it hard to think.",
            section="Experience",
            section_index=1,
        )
        AgentDecision.objects.create(
            session=self.session,
            message=self.second_answer,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
            participant_control=AgentDecision.ParticipantControl.NONE,
            action=AgentDecision.Action.MOVE_NEXT,
            covered_information=["context", "effect"],
            missing_information=[],
            decision_reason="The Protocol information is explicitly covered.",
        )

        other_stakeholder = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        other_session = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=other_stakeholder,
            protocol=self.protocol,
        )
        self.other_answer = Message.objects.create(
            session=other_session,
            sender=Message.Sender.PARTICIPANT,
            content="This source belongs to another session.",
            section="Experience",
            section_index=1,
        )

    def test_summary_uses_protocol_topics_and_exact_session_message_sources(self):
        response = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )

        self.assertEqual(response.status_code, 200)
        summary_rows = response.context["summary_rows"]
        self.assertEqual(
            [row["section"] for row in summary_rows],
            ["Experience", "Recovery pattern"],
        )
        self.assertEqual(summary_rows[0]["label"], "Situation")
        self.assertEqual(summary_rows[1]["label"], "Recovery pattern")
        self.assertEqual(
            [message.id for message in summary_rows[0]["source_messages"]],
            [self.first_answer.id, self.second_answer.id],
        )
        self.assertEqual(summary_rows[1]["source_messages"], [])

        self.assertContains(response, 'href="#transcript-turn-2"')
        self.assertContains(response, "Participant response 2")
        self.assertContains(response, "Include source extract")
        self.assertContains(response, "Include edited extract")
        self.assertContains(response, "Edited extract for the Evidence Record")
        self.assertContains(response, "do not establish whether the participant's account is factually true")
        self.assertNotContains(response, ">Evidence text<")
        self.assertContains(response, "No usable participant response recorded")
        self.assertNotContains(response, "Interview prompt 1</a>")
        self.assertNotContains(response, "Approve evidence")
        self.assertNotContains(response, "This source belongs to another session.")

        first_item = StructuredDigestItem.objects.get(
            session=self.session,
            section_index=1,
        )
        self.assertEqual(first_item.generation_method, "deterministic_extractive_v1")
        self.assertIn("Typed response extract:", first_item.generated_text)
        self.assertEqual(
            list(first_item.source_messages.order_by("id")),
            [self.first_answer, self.second_answer],
        )

    def test_in_progress_session_does_not_freeze_an_early_digest_draft(self):
        in_progress = InterviewSession.objects.create(
            session_code="IS-03",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            transcript_saved=False,
            summary_generated=False,
        )
        Message.objects.create(
            session=in_progress,
            sender=Message.Sender.PARTICIPANT,
            content="An answer that may still receive a follow-up.",
            section="Experience",
            section_index=1,
        )

        response = self.client.get(reverse("output_detail", args=[in_progress.session_code]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["digest_items"], [])
        self.assertFalse(in_progress.digest_items.exists())

    def test_boundary_request_is_excluded_from_digest_sources(self):
        AgentDecision.objects.create(
            session=self.session,
            message=self.first_answer,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.NOT_ASSESSED,
            action=AgentDecision.Action.BOUNDARY_RESPONSE,
            probe_count_before=0,
            missing_information=["context", "effect"],
            decision_reason="Boundary request is not research evidence.",
        )

        first_item = ensure_digest_items(self.session)[0]

        self.assertEqual(
            list(first_item.source_messages.order_by("id")),
            [self.second_answer],
        )
        self.assertNotIn(self.first_answer.content, first_item.generated_text)
        self.assertIn(self.second_answer.content, first_item.generated_text)


class SourceGroundingValidationTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Source Grounding Protocol",
            slug="source-grounding-protocol",
            stakeholder_group="Participants",
            purpose="Audit transcript source constraints.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "primary_question": "What happened?",
                    "required_information": ["context", "effect"],
                },
                {
                    "index": 2,
                    "code": "completion",
                    "label": "Completion",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P-SOURCE",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-SOURCE",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.COMPLETED,
            consent_confirmed=True,
            transcript_saved=True,
            summary_generated=True,
            review_status=InterviewSession.ReviewStatus.NEEDS_REVIEW,
            output_quality_status=InterviewSession.OutputQualityStatus.WAITING,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        self.participant_source = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="The crowded train noise made it difficult to think.",
            section="Experience",
            section_index=1,
        )
        AgentDecision.objects.create(
            session=self.session,
            message=self.participant_source,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
            participant_control=AgentDecision.ParticipantControl.NONE,
            action=AgentDecision.Action.MOVE_NEXT,
            covered_information=["context", "effect"],
            missing_information=[],
            decision_reason="The Protocol information is explicitly covered.",
        )
        self.item = ensure_digest_items(self.session)[0]
        self.item.review_status = StructuredDigestItem.ReviewStatus.APPROVED
        self.item.reviewed_by = self.researcher
        self.item.reviewer_name_snapshot = "Rina Reviewer"
        self.item.reviewed_at = timezone.now()
        self.item.save()

    def approval_form_data(self):
        return {
            "decision": "approve",
            "researcher_note": "Source grounding checked.",
            "participant_meaning_preserved": "on",
            "protocol_boundaries_respected": "on",
        }

    def validation(self):
        return validate_included_source_links(self.session, [self.item])

    def assert_invalid_source_blocks_overall_approval(self, reason_code):
        validation = self.validation()
        self.assertFalse(validation["valid"])
        self.assertIn(
            reason_code,
            [reason["code"] for reason in validation["items"][0]["reasons"]],
        )
        blocked = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            self.approval_form_data(),
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Resolve all system-checked workflow conditions")
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.review_status,
            InterviewSession.ReviewStatus.NEEDS_REVIEW,
        )

    def test_valid_source_result_is_export_ready_and_allows_approval(self):
        validation = self.validation()
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["included_item_count"], 1)
        self.assertEqual(validation["valid_item_count"], 1)
        self.assertEqual(
            validation["items"][0]["source_message_ids"],
            [self.participant_source.id],
        )
        self.assertEqual(
            validation["items"][0]["source_texts"],
            [self.participant_source.content],
        )
        approved = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            self.approval_form_data(),
        )
        self.assertRedirects(
            approved,
            reverse("output_detail", args=[self.session.session_code]),
        )

    def test_no_source_is_invalid_and_blocks_overall_approval(self):
        self.item.source_messages.clear()
        self.assert_invalid_source_blocks_overall_approval("no_source_message")

    def test_cross_session_source_is_invalid_and_blocks_overall_approval(self):
        other_session = InterviewSession.objects.create(
            session_code="IS-OTHER",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.COMPLETED,
        )
        cross_session_source = Message.objects.create(
            session=other_session,
            sender=Message.Sender.PARTICIPANT,
            content="This source belongs to another Session.",
            section="Experience",
            section_index=1,
        )
        self.item.source_messages.set([cross_session_source])
        self.assert_invalid_source_blocks_overall_approval("cross_session_source")

    def test_agent_source_is_invalid_and_blocks_overall_approval(self):
        agent_source = Message.objects.create(
            session=self.session,
            sender=Message.Sender.AGENT,
            content="This is an agent prompt, not participant evidence.",
            section="Experience",
            section_index=1,
        )
        self.item.source_messages.set([agent_source])
        self.assert_invalid_source_blocks_overall_approval("non_participant_source")

    def test_cross_topic_source_is_invalid_and_blocks_overall_approval(self):
        cross_topic_source = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="This response belongs to the opening topic.",
            section="Opening",
            section_index=0,
        )
        self.item.source_messages.set([cross_topic_source])
        self.assert_invalid_source_blocks_overall_approval("cross_topic_source")


class StructuredDigestReviewTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Review Protocol",
            slug="review-protocol",
            stakeholder_group="Participants",
            purpose="Test item-level review.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "experience",
                    "label": "Experience",
                    "primary_question": "What happened?",
                    "required_information": ["context", "effect"],
                },
                {
                    "index": 2,
                    "code": "recovery_pattern",
                    "label": "Recovery pattern",
                    "primary_question": "What happened afterwards?",
                    "required_information": ["recovery"],
                },
                {
                    "index": 3,
                    "code": "public_use_acceptability",
                    "label": "Public use",
                    "primary_question": "Would public use feel acceptable?",
                    "required_information": ["context", "concern"],
                },
                {
                    "index": 4,
                    "code": "faithful_summary",
                    "label": "Researcher handoff",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.stakeholder = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.session = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.COMPLETED,
            consent_confirmed=True,
            transcript_saved=True,
            summary_generated=True,
            review_status=InterviewSession.ReviewStatus.NEEDS_REVIEW,
            output_quality_status=InterviewSession.OutputQualityStatus.WAITING,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        self.experience = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="The crowded train noise made it hard to think.",
            section="Experience",
            section_index=1,
        )
        self.recovery = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="I left the train and sat somewhere quiet.",
            section="Recovery pattern",
            section_index=2,
        )
        self.public_use = Message.objects.create(
            session=self.session,
            sender=Message.Sender.PARTICIPANT,
            content="I would not want a visible object at work.",
            section="Public use",
            section_index=3,
        )
        for message, covered_information in (
            (self.experience, ["context", "effect"]),
            (self.recovery, ["recovery"]),
            (self.public_use, ["context", "concern"]),
        ):
            AgentDecision.objects.create(
                session=self.session,
                message=message,
                section=message.section,
                section_index=message.section_index,
                coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
                participant_control=AgentDecision.ParticipantControl.NONE,
                action=AgentDecision.Action.MOVE_NEXT,
                covered_information=covered_information,
                missing_information=[],
                decision_reason="The Protocol information is explicitly covered.",
            )
        response = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )
        self.assertEqual(response.status_code, 200)
        self.items = list(self.session.digest_items.order_by("section_index"))

    def review_url(self, item):
        return reverse(
            "review_digest_item",
            args=[self.session.session_code, item.id],
        )

    def test_item_actions_preserve_draft_sources_history_and_reviewer(self):
        approve_item, edit_item, exclude_item = self.items

        self.client.post(
            self.review_url(approve_item),
            {"digest_action": "approve", "reviewed_text": approve_item.generated_text},
        )
        original_edit_draft = edit_item.generated_text
        self.client.post(
            self.review_url(edit_item),
            {
                "digest_action": "edit",
                "reviewed_text": "The participant recovered by leaving and finding a quiet place.",
                "reviewer_comment": "Condensed the two actions without adding interpretation.",
            },
        )
        self.client.post(
            self.review_url(exclude_item),
            {
                "digest_action": "exclude",
                "reviewed_text": exclude_item.generated_text,
                "reviewer_comment": "Exclude because the answer does not specify a tested context.",
            },
        )

        approve_item.refresh_from_db()
        edit_item.refresh_from_db()
        exclude_item.refresh_from_db()
        self.assertEqual(approve_item.review_status, StructuredDigestItem.ReviewStatus.APPROVED)
        self.assertEqual(edit_item.review_status, StructuredDigestItem.ReviewStatus.EDITED)
        self.assertEqual(exclude_item.review_status, StructuredDigestItem.ReviewStatus.EXCLUDED)
        self.assertEqual(edit_item.generated_text, original_edit_draft)
        self.assertEqual(
            edit_item.reviewed_text,
            "The participant recovered by leaving and finding a quiet place.",
        )
        self.assertEqual(edit_item.reviewed_by, self.researcher)
        self.assertEqual(edit_item.reviewer_name_snapshot, "Rina Reviewer")
        self.assertEqual(DigestReviewEvent.objects.filter(digest_item=edit_item).count(), 1)
        self.assertEqual(list(edit_item.source_messages.all()), [self.recovery])

        audit_records = build_review_audit_records(
            [approve_item, edit_item, exclude_item]
        )
        self.assertTrue(all(record["audit_complete"] for record in audit_records))
        records_by_status = {
            record["review_status"]: record for record in audit_records
        }
        edited_record = records_by_status[StructuredDigestItem.ReviewStatus.EDITED]
        self.assertEqual(edited_record["generated_text"], original_edit_draft)
        self.assertEqual(edited_record["final_text"], edit_item.reviewed_text)
        self.assertEqual(edited_record["evidence_text"], edit_item.reviewed_text)
        self.assertEqual(
            edited_record["source_message_ids"], [self.recovery.id]
        )
        self.assertEqual(
            edited_record["source_texts"], [self.recovery.content]
        )
        self.assertTrue(edited_record["field_checks"]["before_after_event_recorded"])
        self.assertTrue(edited_record["included_in_final_evidence"])
        excluded_record = records_by_status[
            StructuredDigestItem.ReviewStatus.EXCLUDED
        ]
        self.assertFalse(excluded_record["included_in_final_evidence"])
        self.assertEqual(excluded_record["evidence_text"], "")
        self.assertTrue(excluded_record["field_checks"]["exclusion_event_retained"])
        self.assertEqual(
            excluded_record["reason"],
            "Exclude because the answer does not specify a tested context.",
        )

    def test_review_audit_completeness_is_computed_field_by_field(self):
        item = self.items[0]
        StructuredDigestItem.objects.filter(pk=item.pk).update(
            review_status=StructuredDigestItem.ReviewStatus.EDITED,
            reviewed_text="An edited extract with no attributable review record.",
            reviewer_comment="",
            reviewed_by=None,
            reviewer_name_snapshot="",
            reviewed_at=None,
        )
        item.refresh_from_db()

        record = build_review_audit_records([item])[0]

        self.assertFalse(record["audit_complete"])
        self.assertLess(
            record["completed_field_count"], record["required_field_count"]
        )
        self.assertFalse(record["field_checks"]["edit_reason_recorded"])
        self.assertFalse(record["field_checks"]["reviewer_identity_recorded"])
        self.assertFalse(record["field_checks"]["review_timestamp_recorded"])
        self.assertFalse(record["field_checks"]["matching_review_event_recorded"])
        self.assertFalse(record["field_checks"]["before_after_event_recorded"])

    def test_edit_and_exclusion_require_reason_and_real_change(self):
        item = self.items[0]
        unchanged = self.client.post(
            self.review_url(item),
            {
                "digest_action": "edit",
                "reviewed_text": item.generated_text,
                "reviewer_comment": "No actual edit.",
            },
            follow=True,
        )
        self.assertContains(unchanged, "edited text is unchanged")

        missing_reason = self.client.post(
            self.review_url(item),
            {"digest_action": "exclude", "reviewer_comment": ""},
            follow=True,
        )
        self.assertContains(missing_reason, "Add a reviewer comment")

        changed_but_approved_as_unchanged = self.client.post(
            self.review_url(item),
            {
                "digest_action": "approve",
                "reviewed_text": "A changed evidence statement.",
            },
            follow=True,
        )
        self.assertContains(
            changed_but_approved_as_unchanged,
            "Choose Include edited extract",
        )
        item.refresh_from_db()
        self.assertEqual(item.review_status, StructuredDigestItem.ReviewStatus.PENDING)
        self.assertFalse(item.review_events.exists())

    def test_overall_approval_requires_resolved_items_before_release_conditions(self):
        first_item = self.items[0]
        self.client.post(
            self.review_url(first_item),
            {"digest_action": "approve", "reviewed_text": first_item.generated_text},
        )
        criteria = self.approval_form_data()
        blocked = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            criteria,
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Review every answered or partial topic")
        self.session.refresh_from_db()
        self.assertEqual(self.session.review_status, InterviewSession.ReviewStatus.NEEDS_REVIEW)

    def approval_form_data(self):
        return {
            "decision": "approve",
            "researcher_note": "Item-level review completed.",
            "participant_meaning_preserved": "on",
            "protocol_boundaries_respected": "on",
        }

    def resolve_all_items(self):
        approve_item, edit_item, exclude_item = self.items
        self.client.post(
            self.review_url(approve_item),
            {"digest_action": "approve", "reviewed_text": approve_item.generated_text},
        )
        self.client.post(
            self.review_url(edit_item),
            {
                "digest_action": "edit",
                "reviewed_text": "Reviewed recovery evidence.",
                "reviewer_comment": "Shortened for the digest.",
            },
        )
        self.client.post(
            self.review_url(exclude_item),
            {
                "digest_action": "exclude",
                "reviewer_comment": "Not suitable for inclusion.",
            },
        )
        return approve_item, edit_item, exclude_item

    def test_approved_evidence_uses_only_reviewed_final_items_and_identity(self):
        _, _, exclude_item = self.resolve_all_items()
        approved = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            self.approval_form_data(),
        )
        self.assertRedirects(
            approved,
            reverse("output_detail", args=[self.session.session_code]),
        )

        self.session.refresh_from_db()
        decision = ReviewDecision.objects.get(session=self.session)
        self.assertEqual(self.session.review_status, InterviewSession.ReviewStatus.APPROVED)
        self.assertEqual(
            self.session.output_quality_status,
            InterviewSession.OutputQualityStatus.APPROVED,
        )
        self.assertEqual(decision.reviewed_by, self.researcher)
        self.assertEqual(decision.reviewer_name_snapshot, "Rina Reviewer")
        self.assertTrue(decision.source_links_checked)
        self.assertTrue(decision.participant_controls_respected)
        self.assertTrue(decision.limitations_and_missing_information_visible)
        self.assertTrue(decision.participant_meaning_preserved)
        self.assertTrue(decision.protocol_boundaries_respected)

        record = build_evidence_record_for_session(self.session)
        self.assertEqual(
            [row["item"].review_status for row in record["summary_rows"]],
            [
                StructuredDigestItem.ReviewStatus.APPROVED,
                StructuredDigestItem.ReviewStatus.EDITED,
            ],
        )
        self.assertEqual(record["summary_rows"][1]["text"], "Reviewed recovery evidence.")
        self.assertEqual([row["item"] for row in record["excluded_digest_rows"]], [exclude_item])
        self.assertEqual(record["pending_digest_rows"], [])

        export = self.client.get(reverse("export_evidence_record"))
        self.assertContains(export, "Researcher-reviewed extracts by protocol topic")
        self.assertContains(export, "System-step explanations")
        self.assertContains(export, "Reviewed recovery evidence.")
        self.assertContains(export, "Shortened for the digest.")
        self.assertContains(export, "Items excluded by the researcher")
        self.assertContains(export, "Not suitable for inclusion.")
        self.assertContains(export, "Rina Reviewer")
        self.assertNotContains(export, "ADT-")
        self.assertNotContains(export, "View in ADT overview")
        self.assertNotContains(export, "Approved or Edited digest items")

    def test_evidence_record_is_traceable_audit_snapshot_not_quality_claim(self):
        self.resolve_all_items()
        self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            self.approval_form_data(),
        )

        export = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.session.session_code},
        )
        html = export.content.decode()
        source_anchor = f"session-{self.session.pk}-transcript-turn-1"

        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.headers["Content-Type"].split(";")[0], "text/html")
        self.assertContains(export, "Format: self-contained HTML snapshot")
        self.assertContains(export, "Evidence review status")
        self.assertContains(export, "Approved as reviewed evidence")
        self.assertNotContains(export, "Output quality")
        self.assertNotContains(export, "Ready for use")
        self.assertContains(export, "The full transcript is the complete saved conversation log.")
        self.assertContains(export, "does not verify outcome quality")
        self.assertContains(export, "Protocol version")
        self.assertContains(export, "Review Protocol · v1")
        self.assertContains(export, "(locked)")
        self.assertContains(export, "Consent confirmed")
        self.assertContains(export, "Session closed at")
        self.assertContains(export, "Overall reviewer")
        self.assertContains(export, "Overall reviewed at")
        self.assertIn(
            f'href="#{source_anchor}">Participant response 1</a>',
            html,
        )
        self.assertIn(f'id="{source_anchor}"', html)
        self.assertLess(
            html.index("<h3>Full transcript</h3>"),
            html.index("<h3>Secondary diagnostic record</h3>"),
        )
        self.assertContains(export, "They support process inspection")

    def test_only_two_researcher_judgements_are_manual_form_controls(self):
        response = self.client.get(
            reverse("output_detail", args=[self.session.session_code])
        )

        self.assertContains(response, "System-checked workflow conditions")
        self.assertContains(response, "Researcher judgements")
        self.assertContains(response, 'name="participant_meaning_preserved"')
        self.assertContains(response, 'name="protocol_boundaries_respected"')
        self.assertNotContains(response, 'name="source_links_checked"')
        self.assertNotContains(response, 'name="participant_controls_respected"')
        self.assertNotContains(
            response,
            'name="limitations_and_missing_information_visible"',
        )
        self.assertNotContains(response, "5 / 5")

    def test_approval_requires_both_researcher_judgements(self):
        self.resolve_all_items()
        form_data = self.approval_form_data()
        form_data.pop("participant_meaning_preserved")

        blocked = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            form_data,
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Confirm both researcher judgements")
        self.session.refresh_from_db()
        decision = ReviewDecision.objects.get(session=self.session)
        self.assertEqual(self.session.review_status, InterviewSession.ReviewStatus.NEEDS_REVIEW)
        self.assertTrue(decision.source_links_checked)
        self.assertFalse(decision.participant_meaning_preserved)

    def test_posted_values_cannot_override_a_failed_system_source_check(self):
        included_item, _, _ = self.resolve_all_items()
        included_item.source_messages.clear()
        form_data = self.approval_form_data()
        form_data.update(
            {
                "source_links_checked": "on",
                "participant_controls_respected": "on",
                "limitations_and_missing_information_visible": "on",
            }
        )

        blocked = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            form_data,
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Resolve all system-checked workflow conditions")
        self.session.refresh_from_db()
        decision = ReviewDecision.objects.get(session=self.session)
        self.assertEqual(self.session.review_status, InterviewSession.ReviewStatus.NEEDS_REVIEW)
        self.assertFalse(decision.source_links_checked)

    def test_editing_after_approval_reopens_review_and_resets_researcher_judgements(self):
        for item in self.items:
            self.client.post(
                self.review_url(item),
                {"digest_action": "approve", "reviewed_text": item.generated_text},
            )
        self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            self.approval_form_data(),
        )

        changed_item = self.items[0]
        self.client.post(
            self.review_url(changed_item),
            {
                "digest_action": "edit",
                "reviewed_text": "A revised, still grounded evidence statement.",
                "reviewer_comment": "Reopened after approval to improve precision.",
            },
        )
        self.session.refresh_from_db()
        decision = ReviewDecision.objects.get(session=self.session)
        self.assertEqual(self.session.review_status, InterviewSession.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(decision.decision, ReviewDecision.Decision.PENDING)
        self.assertTrue(decision.source_links_checked)
        self.assertTrue(decision.participant_controls_respected)
        self.assertTrue(decision.limitations_and_missing_information_visible)
        self.assertFalse(decision.participant_meaning_preserved)
        self.assertFalse(decision.protocol_boundaries_respected)

    def test_revision_request_requires_an_attributable_note(self):
        response = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            {"decision": "request_revision", "researcher_note": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add a researcher note explaining")

        response = self.client.post(
            reverse("output_detail", args=[self.session.session_code]),
            {
                "decision": "request_revision",
                "researcher_note": "Clarify the recovery evidence before approval.",
            },
        )
        self.assertRedirects(
            response,
            reverse("output_detail", args=[self.session.session_code]),
        )
        self.session.refresh_from_db()
        decision = ReviewDecision.objects.get(session=self.session)
        self.assertEqual(
            self.session.review_status,
            InterviewSession.ReviewStatus.REVISION_REQUESTED,
        )
        self.assertEqual(decision.reviewed_by, self.researcher)

    def test_unanswered_topics_are_limitations_and_all_missing_output_is_not_approvable(self):
        limited_session = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=self.stakeholder,
            protocol=self.protocol,
            status=InterviewSession.Status.STOPPED,
            transcript_saved=True,
            summary_generated=True,
            review_status=InterviewSession.ReviewStatus.NEEDS_REVIEW,
            output_quality_status=InterviewSession.OutputQualityStatus.NEEDS_REVIEW,
        )

        response = self.client.get(
            reverse("output_detail", args=[limited_session.session_code])
        )
        self.assertEqual(response.context["digest_summary"]["reviewable_total"], 0)
        self.assertEqual(response.context["digest_summary"]["limitation_total"], 3)
        self.assertContains(response, "Recorded as limitation", count=3)

        first_limitation = limited_session.digest_items.order_by("section_index").first()
        attempted_item_approval = self.client.post(
            self.review_url(first_limitation).replace("IS-01", "IS-02"),
            {
                "digest_action": "approve",
                "reviewed_text": first_limitation.generated_text,
            },
            follow=True,
        )
        self.assertContains(attempted_item_approval, "cannot be included as a participant extract")
        first_limitation.refresh_from_db()
        self.assertEqual(
            first_limitation.review_status,
            StructuredDigestItem.ReviewStatus.PENDING,
        )

        approval_attempt = self.client.post(
            reverse("output_detail", args=[limited_session.session_code]),
            self.approval_form_data(),
        )
        self.assertEqual(approval_attempt.status_code, 200)
        self.assertContains(approval_attempt, "limitations, not evidence")
        limited_session.refresh_from_db()
        self.assertNotEqual(
            limited_session.review_status,
            InterviewSession.ReviewStatus.APPROVED,
        )

        marked = self.client.post(
            reverse("output_detail", args=[limited_session.session_code]),
            {
                "decision": "mark_not_usable",
                "researcher_note": "No participant evidence was collected.",
            },
        )
        self.assertRedirects(
            marked,
            reverse("output_detail", args=[limited_session.session_code]),
        )
        limited_session.refresh_from_db()
        self.assertEqual(
            limited_session.review_status,
            InterviewSession.ReviewStatus.NOT_USABLE,
        )


class EvidenceStatusSessionSelectionTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Session-specific Interview",
            slug="session-specific-interview",
            stakeholder_group="Participants",
            purpose="Verify Session-specific evidence records.",
            sections=[
                {
                    "index": 0,
                    "code": "opening",
                    "label": "Opening",
                    "purpose": "Open.",
                    "primary_question": "Continue?",
                    "required_information": ["acknowledgement"],
                },
                {
                    "index": 1,
                    "code": "topic",
                    "label": "Topic",
                    "purpose": "Collect evidence.",
                    "primary_question": "Describe the topic.",
                    "required_information": ["example"],
                },
                {
                    "index": 2,
                    "code": "completion",
                    "label": "Completion",
                    "purpose": "Close.",
                    "primary_question": "Thank you.",
                    "required_information": [],
                },
            ],
        )
        self.p01 = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.p02 = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
        )
        self.is01 = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.p01,
            protocol=self.protocol,
            status=InterviewSession.Status.COMPLETED,
            transcript_saved=True,
            summary_generated=True,
        )
        self.is02 = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=self.p02,
            protocol=self.protocol,
            status=InterviewSession.Status.STOPPED,
            transcript_saved=True,
            summary_generated=True,
        )
        Message.objects.create(
            session=self.is01,
            sender=Message.Sender.PARTICIPANT,
            content="For example, I changed the order of the work.",
            section="Topic",
            section_index=1,
        )

    def test_evidence_status_selects_and_labels_the_exact_session(self):
        response = self.client.get(
            reverse("output_quality"),
            {"session": self.is02.session_code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_session"], self.is02)
        self.assertContains(response, "Selected record")
        self.assertContains(response, "P02 · IS-02")
        self.assertContains(response, "Session release conditions · P02 · IS-02")
        self.assertContains(response, "Evidence record contents · P02 · IS-02")
        self.assertContains(response, "Outputs with limitations")
        self.assertContains(response, "Export this record")

    def test_single_session_export_does_not_mix_other_interviews(self):
        single = self.client.get(
            reverse("export_evidence_record"),
            {"session": self.is02.session_code},
        )
        self.assertEqual(single.status_code, 200)
        self.assertContains(single, "Single Session: IS-02")
        self.assertContains(single, "Session IS-02 · Participant P02")
        self.assertNotContains(single, "Session IS-01 · Participant P01")
        self.assertIn(
            'filename="purrstone_evidence_record_IS-02.html"',
            single["Content-Disposition"],
        )

        all_records = self.client.get(reverse("export_evidence_record"))
        self.assertContains(all_records, "Session IS-01 · Participant P01")
        self.assertContains(all_records, "Session IS-02 · Participant P02")

    def test_all_session_export_uses_unique_session_scoped_source_anchors(self):
        is01_message = self.is01.messages.get(sender=Message.Sender.PARTICIPANT)
        is02_message = Message.objects.create(
            session=self.is02,
            sender=Message.Sender.PARTICIPANT,
            content="For example, I paused before replying.",
            section="Topic",
            section_index=1,
        )

        for session, message in [
            (self.is01, is01_message),
            (self.is02, is02_message),
        ]:
            item = StructuredDigestItem.objects.create(
                session=session,
                section_index=1,
                section_code="topic",
                section_title="Topic",
                label="Topic",
                coverage_status=StructuredDigestItem.CoverageStatus.COVERED,
                generated_text=message.content,
                review_status=StructuredDigestItem.ReviewStatus.APPROVED,
                reviewed_by=self.researcher,
                reviewer_name_snapshot="Rina Reviewer",
                reviewed_at=timezone.now(),
            )
            item.source_messages.add(message)

        export = self.client.get(reverse("export_evidence_record"))
        html = export.content.decode()

        for session in [self.is01, self.is02]:
            scoped_anchor = f"session-{session.pk}-transcript-turn-1"
            self.assertIn(f'href="#{scoped_anchor}"', html)
            self.assertIn(f'id="{scoped_anchor}"', html)


class DynamicWorkflowStatusTests(AuthenticatedResearcherTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = Protocol.objects.create(
            title="Sensory Overload Interview",
            slug="sensory-overload-interview",
            stakeholder_group="Sensory-sensitive participants",
            purpose="Understand sensory overload experiences.",
        )
        self.p01 = Stakeholder.objects.create(
            participant_id="P01",
            stakeholder_group="Sensory-sensitive participant",
            role="Interview participant",
            assigned_protocol=self.protocol,
            # Deliberately stale: researcher pages must derive status from sessions.
            status=Stakeholder.Status.READY,
        )
        self.p02 = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Qualitative researcher",
            role="Researcher reviewer",
            assigned_protocol=self.protocol,
            status=Stakeholder.Status.READY,
        )
        self.is01 = InterviewSession.objects.create(
            session_code="IS-01",
            stakeholder=self.p01,
            protocol=self.protocol,
            status=InterviewSession.Status.STOPPED,
            consent_confirmed=True,
            transcript_saved=True,
            summary_generated=True,
            review_status=InterviewSession.ReviewStatus.REVISION_REQUESTED,
            output_quality_status=(
                InterviewSession.OutputQualityStatus.REVISION_REQUESTED
            ),
        )
        self.is02 = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=self.p01,
            protocol=self.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            consent_confirmed=True,
            transcript_saved=False,
            summary_generated=False,
            review_status=InterviewSession.ReviewStatus.NOT_REVIEWED,
            output_quality_status=(
                InterviewSession.OutputQualityStatus.NOT_STARTED
            ),
        )

    def test_overview_aggregates_all_sessions_instead_of_is01_only(self):
        response = self.client.get(reverse("overview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stakeholder_count"], 2)
        self.assertEqual(response.context["protocol_count"], 1)
        self.assertEqual(response.context["session_count"], 2)
        self.assertEqual(response.context["output_ready_count"], 1)
        self.assertEqual(response.context["active_interview_count"], 1)
        self.assertEqual(response.context["closed_interview_count"], 1)
        self.assertEqual(response.context["attention_count"], 1)
        self.assertEqual(response.context["reviewed_output_count"], 1)
        self.assertEqual(response.context["review_attention_count"], 1)
        self.assertEqual(response.context["evidence_attention_count"], 1)
        self.assertContains(response, "Project workflow overview")
        self.assertNotContains(response, 'class="metric-grid-3"')
        self.assertContains(response, "IS-01")
        self.assertContains(response, "IS-02")
        self.assertContains(response, "Revision requested")
        self.assertContains(response, "In progress")
        self.assertContains(response, "1 / 1 reviewed")
        self.assertContains(response, "1 session needs revision")

    def test_stakeholder_page_removes_examples_and_uses_live_counts(self):
        response = self.client.get(
            reverse("stakeholder_list"),
            {"selected": self.p01.participant_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stakeholder_count"], 2)
        self.assertEqual(response.context["assigned_protocol_count"], 1)
        self.assertEqual(response.context["session_count"], 2)
        self.assertEqual(response.context["selected_session"].session_code, "IS-02")
        self.assertEqual(
            response.context["selected_stakeholder"].workflow_display["label"],
            "In progress",
        )
        self.assertNotContains(response, "Future workflow examples")
        self.assertContains(response, "Participant records")
        self.assertContains(
            response,
            f"{reverse('stakeholder_list')}?selected=P02",
        )
        self.assertContains(
            response,
            reverse("stakeholder_detail", args=[self.p01.participant_id]),
            count=1,
        )
        self.assertContains(response, "No session")

    def test_direct_database_status_changes_are_reflected_without_stakeholder_sync(self):
        InterviewSession.objects.filter(pk=self.is01.pk).update(
            review_status=InterviewSession.ReviewStatus.APPROVED,
            output_quality_status=InterviewSession.OutputQualityStatus.APPROVED,
        )
        InterviewSession.objects.filter(pk=self.is02.pk).update(
            status=InterviewSession.Status.COMPLETED,
            transcript_saved=True,
            summary_generated=True,
            review_status=InterviewSession.ReviewStatus.APPROVED,
            output_quality_status=InterviewSession.OutputQualityStatus.APPROVED,
        )

        response = self.client.get(
            reverse("stakeholder_list"),
            {"selected": self.p01.participant_id},
        )

        self.p01.refresh_from_db()
        self.assertEqual(self.p01.status, Stakeholder.Status.READY)
        self.assertEqual(
            response.context["selected_stakeholder"].workflow_display["label"],
            "Completed",
        )
        self.assertEqual(
            response.context["selected_session"].evidence_display["label"],
            "Approved",
        )

    def test_stakeholder_detail_can_select_each_session(self):
        response = self.client.get(
            reverse("stakeholder_detail", args=[self.p01.participant_id]),
            {"session": self.is01.session_code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["session_count"], 2)
        self.assertEqual(response.context["session"].session_code, "IS-01")
        self.assertContains(response, "Session history")
        self.assertContains(response, "IS-01")
        self.assertContains(response, "IS-02")
        self.assertContains(response, "Revision requested")
        self.assertEqual(response.context["session"].participant_link_status, "completed")
        self.assertContains(response, "Participant link: Interview completed")
        self.assertNotContains(response, "Per-session participant link generated")

    def test_researcher_status_pages_disable_browser_caching(self):
        for url in [
            reverse("overview"),
            reverse("stakeholder_list"),
            reverse("stakeholder_detail", args=[self.p01.participant_id]),
        ]:
            response = self.client.get(url)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_not_started_session_is_presented_as_awaiting_consent(self):
        pending_session = InterviewSession.objects.create(
            session_code="IS-03",
            stakeholder=self.p02,
            protocol=self.protocol,
        )
        response = self.client.get(
            reverse("stakeholder_list"),
            {"selected": self.p02.participant_id},
        )

        self.assertEqual(response.context["selected_session"], pending_session)
        self.assertEqual(
            response.context["selected_session"].interview_display["label"],
            "Awaiting consent",
        )
        self.assertContains(response, "Awaiting consent")

    def test_interview_sessions_selection_and_statuses_are_dynamic(self):
        response = self.client.get(
            reverse("interview_sessions"),
            {"session": self.is01.session_code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sessions"], 2)
        self.assertEqual(response.context["outputs_ready_count"], 1)
        self.assertEqual(response.context["selected_session"].session_code, "IS-01")
        self.assertContains(response, "Revision requested")
        self.assertContains(response, "Not generated")
        self.assertNotContains(response, "Step 3 pending")
        self.assertContains(response, "Active")
        self.assertContains(response, "Completed")


class MvpManagementCommandTests(TestCase):
    def test_reset_demo_session_clears_is01_but_preserves_identity_and_other_sessions(self):
        call_command("seed_mvp", stdout=StringIO())
        session = InterviewSession.objects.get(session_code="IS-01")
        original_access_token = session.access_token
        reviewer = get_user_model().objects.create_user(
            username="reset-reviewer",
            password="test-password-123",
            first_name="Reset",
            last_name="Reviewer",
        )

        session.status = InterviewSession.Status.COMPLETED
        session.current_section_index = 6
        session.consent_confirmed = True
        consent_record = build_consent_snapshot_record(session.protocol)
        session.consent_notice_version = consent_record["notice_version"]
        session.consent_snapshot = consent_record["snapshot"]
        session.consent_snapshot_sha256 = consent_record["sha256"]
        session.consent_confirmed_at = timezone.now()
        session.transcript_saved = True
        session.summary_generated = True
        session.review_status = InterviewSession.ReviewStatus.APPROVED
        session.output_quality_status = InterviewSession.OutputQualityStatus.APPROVED
        session.researcher_note = "This note must not survive a reset."
        session.save()

        participant_message = Message.objects.create(
            session=session,
            sender=Message.Sender.PARTICIPANT,
            content="A source answer for reset testing.",
            section="Experience",
            section_index=1,
        )
        Summary.objects.create(
            session=session,
            situation="A legacy summary that must be removed.",
        )
        ReviewDecision.objects.create(
            session=session,
            decision=ReviewDecision.Decision.APPROVED,
            reviewed_by=reviewer,
            reviewer_name_snapshot="Reset Reviewer",
        )
        AgentDecision.objects.create(
            session=session,
            message=participant_message,
            section="Experience",
            section_index=1,
            coverage_assessment=AgentDecision.CoverageAssessment.COVERED,
            action=AgentDecision.Action.MOVE_NEXT,
        )
        digest_item = StructuredDigestItem.objects.create(
            session=session,
            section_index=1,
            section_code="experience",
            section_title="Experience",
            label="Situation",
            coverage_status=StructuredDigestItem.CoverageStatus.COVERED,
            generated_text="Typed response extract: a source answer.",
            review_status=StructuredDigestItem.ReviewStatus.APPROVED,
            reviewed_by=reviewer,
            reviewer_name_snapshot="Reset Reviewer",
        )
        digest_item.source_messages.add(participant_message)
        DigestReviewEvent.objects.create(
            digest_item=digest_item,
            previous_status=StructuredDigestItem.ReviewStatus.PENDING,
            new_status=StructuredDigestItem.ReviewStatus.APPROVED,
            new_text=digest_item.generated_text,
            reviewer=reviewer,
            reviewer_name_snapshot="Reset Reviewer",
        )

        other_session = InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=session.stakeholder,
            protocol=session.protocol,
            status=InterviewSession.Status.IN_PROGRESS,
            consent_confirmed=True,
        )
        other_message = Message.objects.create(
            session=other_session,
            sender=Message.Sender.PARTICIPANT,
            content="This other session must remain intact.",
            section="Experience",
            section_index=1,
        )

        output = StringIO()
        call_command("reset_demo_session", stdout=output)

        session.refresh_from_db()
        other_session.refresh_from_db()
        self.assertEqual(session.access_token, original_access_token)
        self.assertEqual(session.status, InterviewSession.Status.NOT_STARTED)
        self.assertEqual(session.current_section_index, 0)
        self.assertFalse(session.consent_confirmed)
        self.assertIsNone(session.consent_confirmed_at)
        self.assertEqual(session.consent_notice_version, "")
        self.assertEqual(session.consent_snapshot, {})
        self.assertEqual(session.consent_snapshot_sha256, "")
        self.assertFalse(session.transcript_saved)
        self.assertFalse(session.summary_generated)
        self.assertEqual(
            session.review_status,
            InterviewSession.ReviewStatus.NOT_REVIEWED,
        )
        self.assertEqual(
            session.output_quality_status,
            InterviewSession.OutputQualityStatus.NOT_STARTED,
        )
        self.assertEqual(session.researcher_note, "")
        self.assertFalse(session.messages.exists())
        self.assertFalse(Summary.objects.filter(session=session).exists())
        self.assertFalse(ReviewDecision.objects.filter(session=session).exists())
        self.assertFalse(AgentDecision.objects.filter(session=session).exists())
        self.assertFalse(StructuredDigestItem.objects.filter(session=session).exists())
        self.assertFalse(DigestReviewEvent.objects.filter(digest_item=digest_item).exists())
        self.assertTrue(get_user_model().objects.filter(pk=reviewer.pk).exists())
        self.assertEqual(other_session.status, InterviewSession.Status.IN_PROGRESS)
        self.assertTrue(Message.objects.filter(pk=other_message.pk).exists())
        self.assertIn("Reset IS-01 only", output.getvalue())

    def test_reset_mvp_requires_explicit_all_flag(self):
        call_command("seed_mvp", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("reset_mvp", stdout=StringIO())
        self.assertTrue(InterviewSession.objects.filter(session_code="IS-01").exists())

    def test_reset_mvp_all_restores_seeded_workflow_and_preserves_users(self):
        call_command("seed_mvp", stdout=StringIO())
        reviewer = get_user_model().objects.create_user(
            username="full-reset-reviewer",
            password="test-password-123",
        )
        custom_protocol = Protocol.objects.create(
            title="Custom Protocol",
            slug="custom-protocol",
            stakeholder_group="Participants",
            purpose="Temporary test Protocol.",
        )
        extra_stakeholder = Stakeholder.objects.create(
            participant_id="P02",
            stakeholder_group="Participant",
            role="Interview participant",
            assigned_protocol=custom_protocol,
        )
        InterviewSession.objects.create(
            session_code="IS-02",
            stakeholder=extra_stakeholder,
            protocol=custom_protocol,
        )

        output = StringIO()
        call_command("reset_mvp", "--all", stdout=output)

        self.assertEqual(list(Stakeholder.objects.values_list("participant_id", flat=True)), ["P01"])
        self.assertEqual(list(InterviewSession.objects.values_list("session_code", flat=True)), ["IS-01"])
        self.assertEqual(
            list(Protocol.objects.values_list("slug", flat=True)),
            ["sensory-overload-interview"],
        )
        self.assertTrue(get_user_model().objects.filter(pk=reviewer.pk).exists())
        self.assertIn("Restored the complete MVP baseline", output.getvalue())

import re

from django import forms
from django.core.exceptions import ValidationError

from .models import InterviewSession, Protocol, Stakeholder


NEW_PROTOCOL_SECTION_TEMPLATE = [
    {
        "index": 0,
        "code": "opening",
        "label": "Opening",
        "purpose": "Explain the interview purpose, boundaries, and participant controls.",
        "primary_question": (
            "Before we begin, I will explain the purpose of this design research "
            "interview. You can skip any question or stop at any time. Is it okay "
            "to continue?"
        ),
        "required_information": [
            "consent",
            "design research boundary",
            "participant control",
        ],
        "assessment_guidance": "Treat this as a workflow acknowledgement, not research evidence.",
        "follow_up_focus": "",
        "interaction_boundary": "Do not collect research data until the participant has acknowledged the boundary.",
    },
    {
        "index": 1,
        "code": "research_topic_1",
        "label": "Research topic 1",
        "purpose": "",
        "primary_question": "",
        "required_information": [],
        "assessment_guidance": "",
        "follow_up_focus": "",
        "interaction_boundary": "",
    },
    {
        "index": 2,
        "code": "completion",
        "label": "Completion",
        "purpose": "Close the interview and hand the saved transcript to researcher review.",
        "primary_question": (
            "Thank you. Your responses have been saved for researcher review."
        ),
        "required_information": ["researcher review handoff"],
        "assessment_guidance": "This is a fixed handoff step, not research evidence.",
        "follow_up_focus": "",
        "interaction_boundary": "Do not introduce findings or claims during the closing handoff.",
    },
]


def next_session_code():
    """Return the next readable IS-XX code without changing existing records."""

    highest_number = 0
    for session_code in InterviewSession.objects.values_list("session_code", flat=True):
        match = re.fullmatch(r"IS-(\d+)", (session_code or "").strip(), re.IGNORECASE)
        if match:
            highest_number = max(highest_number, int(match.group(1)))

    next_number = highest_number + 1
    while True:
        candidate = f"IS-{next_number:02d}"
        if not InterviewSession.objects.filter(session_code__iexact=candidate).exists():
            return candidate
        next_number += 1


class StakeholderForm(forms.ModelForm):
    """Researcher-facing form for creating a coded stakeholder record."""

    class Meta:
        model = Stakeholder
        fields = [
            "participant_id",
            "stakeholder_group",
            "role",
            "assigned_protocol",
            "notes",
        ]
        labels = {
            "participant_id": "Participant ID",
            "stakeholder_group": "Stakeholder group",
            "role": "Role",
            "assigned_protocol": "Assigned protocol",
            "notes": "Notes",
        }
        widgets = {
            "participant_id": forms.TextInput(
                attrs={
                    "placeholder": "e.g. P02",
                    "autocomplete": "off",
                }
            ),
            "stakeholder_group": forms.TextInput(
                attrs={"placeholder": "e.g. Sensory-sensitive participant"}
            ),
            "role": forms.TextInput(
                attrs={"placeholder": "e.g. Interview participant"}
            ),
            "notes": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Optional researcher notes",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_protocol"].empty_label = "Not assigned"
        self.fields["assigned_protocol"].queryset = (
            self.fields["assigned_protocol"].queryset.order_by("title")
        )

    def clean_participant_id(self):
        participant_id = self.cleaned_data["participant_id"].strip().upper()

        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", participant_id):
            raise forms.ValidationError(
                "Use letters, numbers, hyphens, or underscores only."
            )

        if Stakeholder.objects.filter(participant_id__iexact=participant_id).exists():
            raise forms.ValidationError(
                "A stakeholder with this Participant ID already exists."
            )

        return participant_id

    def clean_stakeholder_group(self):
        return self.cleaned_data["stakeholder_group"].strip()

    def clean_role(self):
        return self.cleaned_data["role"].strip()

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()


class InterviewSessionForm(forms.ModelForm):
    """Researcher-facing form for creating one independent interview session."""

    class Meta:
        model = InterviewSession
        fields = ["session_code", "stakeholder", "protocol"]
        labels = {
            "session_code": "Session code",
            "stakeholder": "Stakeholder record",
            "protocol": "Protocol",
        }
        widgets = {
            "session_code": forms.TextInput(
                attrs={
                    "placeholder": "Leave blank to generate the next IS-XX code",
                    "autocomplete": "off",
                }
            ),
        }
        help_texts = {
            "session_code": "Optional. The system will generate the next available code if left blank.",
        }

    def __init__(self, *args, initial_stakeholder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["session_code"].required = False
        self.fields["stakeholder"].queryset = Stakeholder.objects.order_by(
            "participant_id"
        )
        self.fields["protocol"].queryset = Protocol.objects.order_by(
            "title",
            "-version",
        )

        if initial_stakeholder and not self.is_bound:
            self.initial["stakeholder"] = initial_stakeholder.pk
            if initial_stakeholder.assigned_protocol_id:
                self.initial["protocol"] = initial_stakeholder.assigned_protocol_id

    def clean_session_code(self):
        session_code = self.cleaned_data["session_code"].strip().upper()
        if not session_code:
            return ""

        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", session_code):
            raise forms.ValidationError(
                "Use letters, numbers, hyphens, or underscores only."
            )

        if InterviewSession.objects.filter(session_code__iexact=session_code).exists():
            raise forms.ValidationError(
                "An interview session with this code already exists."
            )

        return session_code

    def save(self, commit=True):
        session = super().save(commit=False)
        if not session.session_code:
            session.session_code = next_session_code()
        session.status = InterviewSession.Status.READY

        if commit:
            session.save()

        return session


class ProtocolEditForm(forms.ModelForm):
    ethics_rules_text = forms.CharField(
        label="Additional protocol rules",
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text=(
            "Enter one rule per line. These rules are supplied to constrained LLM "
            "assessment and wording. Universal Skip, Stop, one-follow-up, and "
            "non-medical controls remain enforced by the graph."
        ),
    )

    class Meta:
        model = Protocol
        fields = [
            "title",
            "stakeholder_group",
            "purpose",
            "interview_mode",
            "estimated_duration",
            "output_description",
        ]
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["ethics_rules_text"].initial = "\n".join(
                self.instance.ethics_rules or []
            )
        elif not self.is_bound:
            self.fields["ethics_rules_text"].initial = "\n".join(
                [
                    "Ask one question at a time.",
                    "Use short, neutral follow-up questions.",
                    "Do not provide medical advice, diagnosis, or therapy-like interpretation.",
                    "Respect skip and stop requests immediately.",
                    "Use participant responses only as evidence for researcher review.",
                ]
            )

    def clean_ethics_rules_text(self):
        rules = [
            line.strip()
            for line in self.cleaned_data["ethics_rules_text"].splitlines()
            if line.strip()
        ]
        if not rules:
            raise ValidationError("Add at least one agent control or ethics rule.")
        return rules

    def save(self, commit=True):
        protocol = super().save(commit=False)
        protocol.ethics_rules = self.cleaned_data["ethics_rules_text"]
        if commit:
            protocol.save()
        return protocol


def parse_protocol_sections(post_data, existing_sections=None):
    """Validate and rebuild an editable, ordered interview flow.

    The first section must remain the deterministic opening and the final
    section must remain a non-research completion handoff.  Draft protocols may
    add, remove, or reorder the research sections between those boundaries.
    """

    existing_sections = list(existing_sections or [])
    codes = post_data.getlist("section_code")
    labels = post_data.getlist("section_label")
    purposes = post_data.getlist("section_purpose")
    questions = post_data.getlist("section_question")
    required_values = post_data.getlist("section_required")
    optional_values = {
        "assessment_guidance": post_data.getlist("section_assessment_guidance"),
        "follow_up_focus": post_data.getlist("section_follow_up_focus"),
        "interaction_boundary": post_data.getlist("section_interaction_boundary"),
    }

    expected_count = len(labels)
    if not codes and len(existing_sections) == expected_count:
        # Backwards-compatible support for older form submissions and tests.
        codes = [str(section.get("code") or "") for section in existing_sections]

    if not all(
        len(values) == expected_count
        for values in [codes, purposes, questions, required_values]
    ):
        raise ValidationError(
            "Protocol section fields were incomplete. Review every section."
        )

    for field_values in optional_values.values():
        if field_values and len(field_values) != expected_count:
            raise ValidationError(
                "Protocol runtime-guidance fields were incomplete. Review every section."
            )

    if expected_count < 3:
        raise ValidationError(
            "A protocol needs an Opening, at least one research topic, and a Completion section."
        )

    existing_by_code = {
        str(section.get("code") or "").strip().lower(): section
        for section in existing_sections
        if str(section.get("code") or "").strip()
    }

    updated_sections = []
    seen_codes = set()
    for position in range(expected_count):
        code = codes[position].strip().lower().replace("-", "_").replace(" ", "_")
        label = labels[position].strip()
        purpose = purposes[position].strip()
        question = questions[position].strip()
        required_information = [
            item.strip()
            for item in re.split(r"[\n,]+", required_values[position])
            if item.strip()
        ]

        existing = existing_by_code.get(code, {})

        def optional_value(field_name):
            values = optional_values[field_name]
            if values:
                return values[position].strip()
            return str(existing.get(field_name) or "").strip()

        assessment_guidance = optional_value("assessment_guidance")
        follow_up_focus = optional_value("follow_up_focus")
        interaction_boundary = optional_value("interaction_boundary")

        if not label or not purpose or not question:
            raise ValidationError(
                f"Section {position + 1} needs a label, purpose, and message/question."
            )

        if not re.fullmatch(r"[a-z][a-z0-9_]*", code):
            raise ValidationError(
                f"Section {position + 1} needs a routing code using lower-case letters, numbers, or underscores."
            )
        if code in seen_codes:
            raise ValidationError(f"Routing code '{code}' is used more than once.")
        seen_codes.add(code)

        if position == 0 and code != "opening":
            raise ValidationError("The first section must use the routing code 'opening'.")
        if position == expected_count - 1 and code not in {
            "completion",
            "faithful_summary",
            "researcher_handoff",
        }:
            raise ValidationError(
                "The final section must use 'completion', 'faithful_summary', or 'researcher_handoff'."
            )
        if 0 < position < expected_count - 1 and code in {
            "opening",
            "completion",
            "faithful_summary",
            "researcher_handoff",
        }:
            raise ValidationError(
                f"Research section '{label}' needs its own non-reserved routing code."
            )
        if position not in {0, expected_count - 1} and not required_information:
            raise ValidationError(
                f"Research section '{label}' needs at least one required-information item."
            )

        if 0 < position < expected_count - 1:
            placeholder_questions = {
                "enter the first research question here.",
                "enter the research question here.",
            }
            placeholder_requirements = {
                "information needed for this topic",
            }
            if question.lower() in placeholder_questions:
                raise ValidationError(
                    f"Research section '{label}' still contains a placeholder question. Replace it with the question participants should actually receive."
                )
            if any(
                item.lower() in placeholder_requirements
                for item in required_information
            ):
                raise ValidationError(
                    f"Research section '{label}' still contains placeholder required information. Define the evidence the coverage check should look for."
                )

        updated = dict(existing)
        updated.update(
            {
                "index": position,
                "code": code,
                "label": label,
                "purpose": purpose,
                "primary_question": question,
                "required_information": required_information,
                "assessment_guidance": assessment_guidance,
                "follow_up_focus": follow_up_focus,
                "interaction_boundary": interaction_boundary,
            }
        )
        updated_sections.append(updated)

    return updated_sections

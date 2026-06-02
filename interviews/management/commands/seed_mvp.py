from django.core.management.base import BaseCommand

from interviews.models import Protocol, Stakeholder, InterviewSession


class Command(BaseCommand):
    help = "Seed initial PurrStone MVP data."

    def handle(self, *args, **options):
        protocol_sections = [
            {
                "index": 0,
                "code": "opening",
                "label": "Opening",
                "purpose": "Establish interview purpose, consent reminder, and research boundary.",
                "primary_question": (
                    "Before we begin, this interview is about sensory overload.\n\n"
                    "In this project, sensory overload means a moment when a place or situation feels overwhelming "
                    "because there is too much noise, light, movement, crowding, smell, touch, or other sensory input. "
                    "For example, this might happen in a supermarket, on public transport, or in a busy public space.\n\n"
                    "I will ask you about one specific moment like this: what happened, what made it overwhelming, "
                    "how you reacted, and what support might have helped. Later, I will also ask for your reaction "
                    "to a sensory-support concept called PurrStone.\n\n"
                    "This is a design research interview, not medical advice. "
                    "You can skip any question or stop at any time. Is it okay to continue?"
                ),
                "required_information": ["consent", "design research boundary", "participant control"],
            },
            {
                "index": 1,
                "code": "experience",
                "label": "Experience",
                "purpose": "Ask about one recent sensory overload or overwhelm situation.",
                "primary_question": "Can you describe a recent or memorable situation where you felt sensory overload or overwhelmed?",
                "required_information": ["where it happened", "what happened", "why it felt overwhelming"],
            },
            {
                "index": 2,
                "code": "triggers_signs",
                "label": "Triggers & signs",
                "purpose": "Identify triggers, body signs, and emotional signs.",
                "primary_question": "What were the strongest triggers or early signs you noticed, such as sound, light, crowding, movement, body signs, or emotions?",
                "required_information": ["sensory triggers", "contextual triggers", "body signs", "emotional signs"],
            },
            {
                "index": 3,
                "code": "coping_support",
                "label": "Coping & support",
                "purpose": "Explore current coping actions, support needs, and unmet needs.",
                "primary_question": "What did you do to cope in that moment, and what kind of support would have helped?",
                "required_information": ["coping actions", "what helped", "what did not help", "support needs"],
            },
            {
                "index": 4,
                "code": "support_concept_reaction",
                "label": "Support concept reaction",
                "purpose": "Explore reaction to the PurrStone support concept.",
                "primary_question": "PurrStone is a small handheld support object using squeeze interaction, haptic breathing guidance, and optional scent. What is your first reaction to that idea?",
                "required_information": ["reaction to squeeze", "reaction to haptics", "reaction to scent", "perceived usefulness", "concerns"],
            },
            {
                "index": 5,
                "code": "public_use_acceptability",
                "label": "Public-use acceptability",
                "purpose": "Explore whether the participant would feel comfortable using PurrStone in public or shared settings.",
                "primary_question": "Would you feel comfortable using something like PurrStone in public or shared settings, such as commuting, studying, or working? Why or why not?",
                "required_information": ["public comfort", "embarrassment", "discreetness", "scent concern", "shared-space concern"],
            },
            {
                "index": 6,
                "code": "faithful_summary",
                "label": "Faithful summary",
                "purpose": "Generate a transcript-grounded draft summary for researcher review.",
                "primary_question": "Thank you. I will now summarise what you shared for researcher review.",
                "required_information": ["transcript-grounded summary", "missing information flags", "researcher review handoff"],
            },
        ]

        ethics_rules = [
            "Ask one question at a time.",
            "Use short, neutral follow-up questions.",
            "Do not lead the participant toward positive views of PurrStone.",
            "Do not provide medical advice, diagnosis, or therapy-like interpretation.",
            "If the participant feels uncomfortable, offer to skip, pause, or stop.",
            "Generate summaries only from what the participant actually said.",
        ]

        protocol, _ = Protocol.objects.update_or_create(
            slug="sensory-overload-interview",
            defaults={
                "title": "Sensory Overload Interview",
                "stakeholder_group": "Sensory-sensitive participants",
                "purpose": (
                    "Explore experiences of sensory overload, everyday overwhelm, coping behaviours, "
                    "support needs, reactions to the PurrStone concept, and public-use acceptability."
                ),
                "interview_mode": "AI-led semi-structured interview",
                "estimated_duration": "10–15 minutes",
                "output_description": "Transcript + structured summary",
                "sections": protocol_sections,
                "ethics_rules": ethics_rules,
            },
        )

        stakeholder, _ = Stakeholder.objects.update_or_create(
            participant_id="P01",
            defaults={
                "stakeholder_group": "Sensory-sensitive participant",
                "role": "Interview participant",
                "assigned_protocol": protocol,
                "status": Stakeholder.Status.READY,
                "notes": "Initial MVP participant record for the PurrStone Sensory Overload Interview.",
            },
        )

        session, _ = InterviewSession.objects.update_or_create(
            session_code="IS-01",
            defaults={
                "stakeholder": stakeholder,
                "protocol": protocol,
                "status": InterviewSession.Status.NOT_STARTED,
                "current_section_index": 0,
                "consent_confirmed": False,
                "transcript_saved": False,
                "summary_generated": False,
                "review_status": InterviewSession.ReviewStatus.NOT_REVIEWED,
                "output_quality_status": InterviewSession.OutputQualityStatus.NOT_STARTED,
            },
        )

        self.stdout.write(self.style.SUCCESS("Seeded PurrStone MVP data successfully."))
        self.stdout.write(f"Protocol: {protocol.title}")
        self.stdout.write(f"Stakeholder: {stakeholder.participant_id}")
        self.stdout.write(f"Session: {session.session_code}")
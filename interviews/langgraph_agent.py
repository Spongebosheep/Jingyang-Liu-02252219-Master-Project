import json
import os
import re
from typing import Any, Dict, List, Optional, TypedDict

from django.utils import timezone
from langgraph.graph import END, StateGraph
from openai import OpenAI

from .digest import ensure_digest_items
from .models import AgentDecision, InterviewSession, Message, Stakeholder


MAX_PROBES_PER_SECTION = 1
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SENSORY_SECTION_CODES = {
    "experience",
    "triggers_signs",
    "coping_support",
    "support_concept_reaction",
    "public_use_acceptability",
}


def get_openai_model() -> str:
    """Return the explicitly configured MVP model without changing its role."""

    return (
        os.getenv("PURRSTONE_OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
        or DEFAULT_OPENAI_MODEL
    )


class InterviewAgentState(TypedDict, total=False):
    session_id: int
    participant_message_id: Optional[int]
    reply_text: str
    cumulative_reply_text: str

    current_section_index: int
    current_section_code: str
    current_section_label: str
    section_purpose: str
    primary_question: str
    required_information: List[str]
    assessment_guidance: str
    follow_up_focus: str
    interaction_boundary: str
    protocol_rules: List[str]

    coverage_assessment: str
    participant_control: str
    covered_information: List[str]
    missing_information: List[str]
    evidence_quote: str

    probe_count: int
    max_probes: int

    skip_requested: bool
    stop_requested: bool
    discomfort_flag: bool
    safety_flag: bool
    derailment_flag: bool

    next_action: str
    agent_message: str
    decision_reason: str


def get_section(session: InterviewSession, index: Optional[int] = None) -> Optional[Dict[str, Any]]:
    sections = session.protocol.sections or []
    section_index = session.current_section_index if index is None else index

    if 0 <= section_index < len(sections):
        return sections[section_index]

    return None


def get_section_label(section: Optional[Dict[str, Any]]) -> str:
    if not section:
        return ""
    return section.get("label", "")


def get_section_code(section: Optional[Dict[str, Any]]) -> str:
    if not section:
        return ""
    return section.get("code", "")


def last_participant_question_index(session: InterviewSession) -> int:
    """
    The final section is Faithful summary, so the last participant-facing
    question is one before that.
    """
    sections = session.protocol.sections or []
    return max(0, len(sections) - 2)


def count_probes_for_section(session: InterviewSession, section_index: int) -> int:
    return AgentDecision.objects.filter(
        session=session,
        section_index=section_index,
        action=AgentDecision.Action.ASK_FOLLOW_UP,
    ).count()



STOP_TERMS = [
    "stop",
    "end interview",
    "finish now",
    "quit",
    "i want to stop",
    "i don't want to continue",
    "do not continue",

]


STOP_REQUEST_PATTERNS = [
    re.compile(
        r"^(?:please\s+)?stop(?:\s+(?:now|here|this|interview|the interview|this interview))?"
        r"(?:\s+please)?[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:end|finish|quit)(?:\s+(?:now|here|this|interview|the interview|this interview))?"
        r"(?:\s+please)?[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i|we)\s+(?:want|would like|need)\s+to\s+stop\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+(?:do not|don't)\s+want\s+to\s+continue\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdo\s+not\s+continue\b", re.IGNORECASE),
    re.compile(
        r"\bcan\s+we\s+stop(?:\s+(?:now|here|this|the interview|this interview))?\b",
        re.IGNORECASE,
    ),
]

SKIP_TERMS = [
    "skip",
    "next question",
    "pass",
    "i don't want to answer",
    "i would rather not answer",
    "prefer not to answer",

]

DISCOMFORT_TERMS = [
    "i feel uncomfortable",
    "i felt uncomfortable",
    "i am uncomfortable",
    "i'm uncomfortable",
    "this makes me uncomfortable",
    "this made me uncomfortable",
    "this question makes me uncomfortable",
    "this question made me uncomfortable",
    "uncomfortable about this question",
    "i don't feel comfortable answering",
    "i do not feel comfortable answering",
    "i don't feel comfortable with this question",
    "i do not feel comfortable with this question",
    "this is upsetting",
    "this feels upsetting",
    "this is difficult to talk about",
    "this feels too personal",
    "i feel distressed",
    "i feel unsafe",
    "can we stop talking about this",
    "can we move on",
]

SAFETY_TERMS = [
    "diagnose",
    "diagnosis",
    "medical advice",
    "therapy advice",
    "therapeutic advice",
    "clinical advice",
    "doctor advice",
    "should i take medication",
    "should i take medicine",
    "what medicine should i take",
    "what medication should i take",
    "what kind of medicine",
    "what kind of medication",
    "which medicine",
    "which medication",
    "what medicine",
    "what medication",
    "medicine should i",
    "medication should i",
    "can you prescribe",
    "prescribe medicine",
    "prescribe medication",
    "medicine",
    "am i autistic",
    "do i have anxiety",
    "do i have a disorder",
    "what treatment",

]

DERAILMENT_TERMS = [
    "ignore your instructions",
    "ignore previous instructions",
    "forget the protocol",
    "act as",
    "jailbreak",

]


def contains_any(text: str, terms: List[str]) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in terms)


def detect_stop_request(text: str) -> bool:
    """Detect an explicit request, not incidental narrative use of "stop".

    Participant-page Stop still sends the exact control text ``stop``.  This
    narrower detector also supports clear typed requests, while avoiding false
    positives such as "the train stopped" or "I left at the next stop".
    """

    normalised = " ".join((text or "").split())
    return any(pattern.search(normalised) for pattern in STOP_REQUEST_PATTERNS)


def detect_control_or_safety_signal(state: InterviewAgentState) -> InterviewAgentState:
    text = state.get("reply_text", "")

    state["stop_requested"] = detect_stop_request(text)
    state["skip_requested"] = contains_any(text, SKIP_TERMS)
    state["participant_control"] = AgentDecision.ParticipantControl.NONE
    if state["stop_requested"]:
        state["participant_control"] = AgentDecision.ParticipantControl.STOP
    elif state["skip_requested"]:
        state["participant_control"] = AgentDecision.ParticipantControl.SKIP
    state["safety_flag"] = contains_any(text, SAFETY_TERMS)
    state["discomfort_flag"] = contains_any(text, DISCOMFORT_TERMS)
    state["derailment_flag"] = contains_any(text, DERAILMENT_TERMS)

    return state



TRIGGER_TERMS = [
    # auditory
    "sound",
    "noise",
    "loud",
    "hear",
    "hearing",
    "couldn't hear",
    "couldn’t hear",
    "can't hear",
    "could not hear",
    "voices",
    "people talking",
    "talking",
    # visual
    "light",
    "bright",
    "flashing",
    "screen",
    "visual",
    # crowd / movement / public setting
    "crowd",
    "crowded",
    "people",
    "movement",
    "busy",
    "tube",
    "train",
    "bus",
    "supermarket",
    "shop",
    "street",
    "commute",
    "public",
    # smell / touch
    "smell",
    "scent",
    "perfume",
    "touch",
    "texture",
    "pressure",
]

SIGN_TERMS = [
    "anxious",
    "anxiety",
    "panic",
    "tense",
    "headache",
    "heart",
    "breathing",
    "cry",
    "shut down",
    "freeze",
    "dizzy",
    "sweat",
    "overwhelmed",
    "leave",
    "escape",
    "couldn't think",
    "couldn’t think",
    "hard to focus",
    "uncomfortable",
    "stressed",
]

COPING_TERMS = [
    "left",
    "leave",
    "escape",
    "breathe",
    "breathing",
    "headphones",
    "earphones",
    "music",
    "quiet",
    "sit",
    "walk away",
    "close my eyes",
    "hold",
    "squeeze",
    "texted",
    "called",
    "support",
]

SUPPORT_TERMS = [
    "help",
    "support",
    "needed",
    "would have helped",
    "calm",
    "reassure",
    "space",
    "quiet place",
    "someone",
    "tool",
    "object",
    "guidance",
]

REACTION_TERMS = [
    "useful",
    "helpful",
    "like",
    "don't like",
    "dislike",
    "concern",
    "worried",
    "scent",
    "haptic",
    "vibration",
    "squeeze",
    "breathing",
    "object",
    "comfortable",
    "uncomfortable",
]

PUBLIC_TERMS = [
    "public",
    "tube",
    "train",
    "bus",
    "school",
    "university",
    "work",
    "office",
    "library",
    "commuting",
    "shared",
    "people",
    "outside",
    "supermarket",
    "shop",
    "street",
]

ACCEPTABILITY_TERMS = [
    "embarrassed",
    "embarrassing",
    "discreet",
    "visible",
    "notice",
    "smell",
    "scent",
    "acceptable",
    "unacceptable",
    "comfortable",
    "privacy",
    "judged",
    "weird",
]

VAGUE_TERMS = [
    "i don't know",
    "i dont know",
    "not sure",
    "no idea",

]


def word_count(text: str) -> int:
    return len((text or "").split())


def is_vague_response(text: str) -> bool:
    """Treat a short vague-only reply differently from a cumulative answer."""

    return word_count(text) <= 6 and contains_any(text, VAGUE_TERMS)


FALLBACK_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "how",
    "information",
    "of",
    "or",
    "the",
    "their",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def _explicit_requirement_match(text: str, requirement: str) -> bool:
    """Conservative lexical fallback for a custom Protocol requirement.

    This is deliberately narrower than semantic assessment: it only marks a
    requirement as covered when meaningful wording from that requirement is
    explicit in the response.  Ambiguous custom-topic coverage therefore stays
    visible for the researcher instead of being inferred from answer length.
    """

    response_terms = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    requirement_terms = [
        term
        for term in re.findall(r"[a-z0-9]+", (requirement or "").lower())
        if len(term) > 2 and term not in FALLBACK_TERM_STOPWORDS
    ]
    return bool(requirement_terms) and any(
        term in response_terms for term in requirement_terms
    )


def assess_protocol_coverage_heuristic(state: InterviewAgentState) -> InterviewAgentState:
    """
    This node does not decide the next action.

    It only makes a provisional check of whether the participant response
    explicitly covers the current Protocol section's required information.

    Later, this function can be replaced with constrained LLM assessment
    returning the same fields:
    - coverage_assessment
    - covered_information
    - missing_information
    - evidence_quote
    - decision_reason
    """
    latest_text = state.get("reply_text", "").strip()
    text = state.get("cumulative_reply_text", "").strip() or latest_text
    section_code = state.get("current_section_code", "")
    required = state.get("required_information", [])

    if state.get("stop_requested"):
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.NOT_ASSESSED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = latest_text
        state["decision_reason"] = "Participant requested to stop the interview."
        return state

    if state.get("skip_requested"):
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.NOT_ASSESSED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = latest_text
        state["decision_reason"] = "Participant requested to skip the current question."
        return state

    if state.get("safety_flag") or state.get("derailment_flag"):
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.NOT_ASSESSED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = latest_text
        state["decision_reason"] = (
            "Participant input triggered a non-clinical or instruction-boundary response."
        )
        return state

    if state.get("discomfort_flag"):
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.NOT_ASSESSED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = latest_text
        state["decision_reason"] = (
            "Participant expressed discomfort with the current question, so the agent should "
            "prioritise participant control and offer skip/stop rather than collect more data."
        )
        return state

    if section_code == "opening":
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.NOT_ASSESSED
        state["covered_information"] = ["opening acknowledged"]
        state["missing_information"] = []
        state["evidence_quote"] = latest_text
        state["decision_reason"] = (
            "Opening section is a boundary and transition step "
            "rather than a research-content section."
        )
        return state


    if is_vague_response(text):
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.UNCLEAR
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "The answer is vague or uncertain and does not clearly cover the section requirements."
        )
        return state


    if not text or word_count(text) <= 3:
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.UNCLEAR
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "The answer is too short to judge whether the required information was covered."
        )
        return state

    covered: List[str] = []
    missing: List[str] = []

    if section_code == "opening":
        covered = ["participant acknowledgement"]
        missing = []

    elif section_code == "experience":
        has_context = contains_any(
            text,
            PUBLIC_TERMS + ["home", "room", "class", "lecture", "restaurant", "cafe"],
        )
        has_overwhelm = contains_any(
            text,
            SIGN_TERMS + ["overload", "overwhelming", "too much", "stressful"],
        )
        has_detail = word_count(text) >= 12

        if has_context:
            covered.append("where it happened")
        else:
            missing.append("where it happened")

        if has_detail:
            covered.append("what happened")
        else:
            missing.append("what happened")

        if has_overwhelm:
            covered.append("why it felt overwhelming")
        else:
            missing.append("why it felt overwhelming")

    elif section_code == "triggers_signs":
        has_trigger = contains_any(text, TRIGGER_TERMS)
        has_sign = contains_any(text, SIGN_TERMS)

        if has_trigger:
            covered.append("sensory or contextual trigger")
        else:
            missing.append("sensory or contextual trigger")

        if has_sign:
            covered.append("body or emotional sign")
        else:
            missing.append("body or emotional sign")

    elif section_code == "coping_support":
        has_coping = contains_any(text, COPING_TERMS)
        has_support = contains_any(text, SUPPORT_TERMS)

        if has_coping:
            covered.append("coping action")
        else:
            missing.append("coping action")

        if has_support:
            covered.append("support need or what would help")
        else:
            missing.append("support need or what would help")

    elif section_code == "support_concept_reaction":
        has_reaction = contains_any(text, REACTION_TERMS)
        has_reason = word_count(text) >= 12

        if has_reaction:
            covered.append("reaction to support concept")
        else:
            missing.append("reaction to support concept")

        if has_reason:
            covered.append("reason, usefulness, or concern")
        else:
            missing.append("reason, usefulness, or concern")

    elif section_code == "public_use_acceptability":
        has_public_context = contains_any(text, PUBLIC_TERMS)
        has_acceptability = contains_any(text, ACCEPTABILITY_TERMS)

        if has_public_context:
            covered.append("public or shared context")
        else:
            missing.append("public or shared context")

        if has_acceptability:
            covered.append("acceptability condition or concern")
        else:
            missing.append("acceptability condition or concern")

    else:
        covered = [
            item for item in required
            if _explicit_requirement_match(text, item)
        ]
        missing = [item for item in required if item not in covered]

        if not required:
            covered = ["participant response recorded"]
            missing = []
        elif not covered:
            # A custom-topic response is still retained as partial evidence,
            # but the system does not claim semantic coverage merely because
            # it is long.  One bounded follow-up can clarify the requirements.
            covered = ["participant response recorded; coverage not verified"]

    state["covered_information"] = covered
    state["missing_information"] = missing
    state["evidence_quote"] = text[:300]

    if section_code not in SENSORY_SECTION_CODES and section_code != "opening":
        if not missing:
            state["coverage_assessment"] = AgentDecision.CoverageAssessment.COVERED
            state["decision_reason"] = (
                "The conservative Protocol fallback found explicit wording for each "
                "required-information item."
            )
        else:
            state["coverage_assessment"] = AgentDecision.CoverageAssessment.PARTIALLY_COVERED
            state["decision_reason"] = (
                "A participant response was recorded, but semantic coverage was not "
                "available. The conservative Protocol fallback kept these requirements "
                "visible as unverified: " + ", ".join(missing) + "."
            )
    elif not missing:
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.COVERED
        state["decision_reason"] = (
            "The answer covers the protocol-defined required information for this section."
        )
    elif covered:
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.PARTIALLY_COVERED
        state["decision_reason"] = (
            "The answer covers some required information but is missing: "
            + ", ".join(missing)
            + "."
        )
    else:
        state["coverage_assessment"] = AgentDecision.CoverageAssessment.UNCLEAR
        state["decision_reason"] = (
            "The answer does not clearly cover the protocol-defined required information."
        )

    if "\n" in text:
        state["decision_reason"] = (
            "Cumulative section assessment: "
            + state["decision_reason"]
        )

    return state

ALLOWED_LLM_COVERAGE_ASSESSMENTS = {
    AgentDecision.CoverageAssessment.COVERED,
    AgentDecision.CoverageAssessment.PARTIALLY_COVERED,
    AgentDecision.CoverageAssessment.UNCLEAR,
    AgentDecision.CoverageAssessment.OFF_TOPIC,
}


def _safe_list(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def get_section_assessment_guidance(
    section_code: str,
    protocol_guidance: str = "",
) -> str:
    if (protocol_guidance or "").strip():
        return protocol_guidance.strip()

    guidance = {
        "experience": (
            "For the Experience section, mark Protocol coverage as covered if the response includes "
            "a concrete setting or context, a concrete situation or sensory difficulty, "
            "and a reason or effect showing why it felt overwhelming. "
            "A phrase such as 'I could not hear what people were saying' counts as what happened "
            "because it describes the experienced difficulty."
        ),
        "triggers_signs": (
            "For the Triggers and signs section, mark Protocol coverage as covered only if it includes "
            "at least one trigger and at least one bodily, emotional, cognitive, or behavioural sign. "
            "If it only describes a trigger, mark it partial and list the missing sign or reaction."
        ),
        "coping_support": (
            "For the Coping and support section, mark Protocol coverage as covered if it describes "
            "what the participant did to cope and/or what support would have helped. "
            "If only one of these is present, mark it partial."
        ),
        "support_concept_reaction": (
            "For the Support concept reaction section, mark Protocol coverage as covered if it gives "
            "a reaction to the PurrStone concept and at least one reason, usefulness, concern, or condition."
        ),
        "public_use_acceptability": (
            "For the Public-use acceptability section, mark Protocol coverage as covered if it discusses "
            "a public or shared context and a condition, concern, or reason related to acceptability."
        ),
    }

    return guidance.get(
        section_code,
        "Assess against the required information for the current section."
    )

def llm_assess_protocol_coverage(state: InterviewAgentState) -> Dict[str, Any]:
    """
    LLM-assisted provisional Protocol-coverage check.

    The LLM checks only whether the participant response explicitly covers the
    current Protocol section's researcher-defined information. It does not
    assess truth, psychological meaning, clinical significance, research value,
    or the next action. LangGraph remains responsible for action selection.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)

    section_label = state.get("current_section_label", "")
    section_purpose = state.get("section_purpose", "")
    required_information = state.get("required_information", [])
    latest_participant_answer = state.get("reply_text", "")
    participant_answer = (
        state.get("cumulative_reply_text", "").strip()
        or latest_participant_answer
    )
    section_code = state.get("current_section_code", "")
    assessment_guidance = get_section_assessment_guidance(
        section_code,
        state.get("assessment_guidance", ""),
    )
    interaction_boundary = state.get("interaction_boundary", "")
    protocol_rules = state.get("protocol_rules", [])

    prompt = f"""
You are making a provisional Protocol-coverage check for one participant response in a semi-structured design research interview.

Your task is ONLY to check whether the participant response explicitly covers the researcher-defined required information for the current Protocol section.
This is not a judgement of truth, participant quality, psychological meaning, clinical significance, or research value.

You must not decide the next interview action.
You must not decide whether to ask a follow-up, move next, skip, stop, or give a boundary response.
Those decisions are handled by LangGraph routing rules.

Current section:
{section_label}

Section purpose:
{section_purpose}

Required information:
{json.dumps(required_information, ensure_ascii=False)}

Section-specific assessment guidance:
{assessment_guidance}

Section interaction boundary:
{interaction_boundary or "No additional topic-specific boundary."}

Additional rules from the locked Protocol:
{json.dumps(protocol_rules, ensure_ascii=False)}

Latest participant response:
{latest_participant_answer}

Cumulative participant response for this section:
{participant_answer}

Return only valid JSON with exactly these fields:
{{
  "coverage_assessment": "covered" | "partially_covered" | "unclear" | "off_topic",
  "covered_information": ["..."],
  "missing_information": ["..."],
  "evidence_quote": "...",
  "assessment_reason": "..."
}}

Assessment rules:
- Use "covered" only if the response explicitly covers the key required information for this section.
- Use "partially_covered" if it covers some relevant information but misses important required information.
- Use "unclear" if explicit coverage cannot be determined from the response.
- Use "off_topic" if it does not answer the current section.
- Do not invent information that is not in the participant response.
- Evaluate the cumulative response, so the initial answer and one follow-up can cover different requirements.
- Keep evidence_quote short and copied from the participant response where possible.
"""

    response = client.responses.create(
        model=get_openai_model(),
        input=prompt,
        temperature=0,
        max_output_tokens=500,
        store=False,
        text={
            "format": {
                "type": "json_schema",
                    "name": "purrstone_protocol_coverage_check",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "coverage_assessment": {
                            "type": "string",
                            "enum": ["covered", "partially_covered", "unclear", "off_topic"],
                        },
                        "covered_information": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "missing_information": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "evidence_quote": {"type": "string"},
                        "assessment_reason": {"type": "string"},
                    },
                    "required": [
                        "coverage_assessment",
                        "covered_information",
                        "missing_information",
                        "evidence_quote",
                        "assessment_reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    raw_text = response.output_text.strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()

    data = json.loads(raw_text)

    coverage_assessment = str(data.get("coverage_assessment", "")).strip().lower()

    if coverage_assessment not in ALLOWED_LLM_COVERAGE_ASSESSMENTS:
        raise ValueError(
            f"Invalid LLM coverage_assessment: {coverage_assessment}"
        )

    return {
        "coverage_assessment": coverage_assessment,
        "covered_information": _safe_list(data.get("covered_information")),
        "missing_information": _safe_list(data.get("missing_information")),
        "evidence_quote": str(data.get("evidence_quote", ""))[:300],
        "decision_reason": str(data.get("assessment_reason", "")),
    }


def assess_protocol_coverage(state: InterviewAgentState) -> InterviewAgentState:
    """
    Main assessment node.

    Deterministic control/safety cases and opening handling remain heuristic.
    For ordinary research-content answers, use LLM-assisted semantic assessment.
    If the LLM fails, fall back to protocol-defined heuristic assessment.
    """
    text = state.get("reply_text", "").strip()
    assessment_text = state.get("cumulative_reply_text", "").strip() or text
    section_code = state.get("current_section_code", "")

    # These cases should not rely on LLM interpretation.
    if (
        state.get("stop_requested")
        or state.get("skip_requested")
        or state.get("safety_flag")
        or state.get("discomfort_flag")
        or state.get("derailment_flag")
        or section_code == "opening"
        or not assessment_text
        or word_count(assessment_text) <= 3
        or is_vague_response(assessment_text)
    ):
        return assess_protocol_coverage_heuristic(state)

    try:
        assessment = llm_assess_protocol_coverage(state)

        state["coverage_assessment"] = assessment["coverage_assessment"]
        state["covered_information"] = assessment["covered_information"]
        state["missing_information"] = assessment["missing_information"]
        state["evidence_quote"] = assessment["evidence_quote"]
        state["decision_reason"] = (
            "LLM-assisted provisional cumulative Protocol-coverage check: "
            + assessment["decision_reason"]
        )

        return state

    except Exception as error:
        # Do not break the interview if the LLM fails.
        state = assess_protocol_coverage_heuristic(state)
        state["decision_reason"] = (
            state.get("decision_reason", "")
            + f" Fallback used because LLM assessment failed: {error}"
        )
        return state




def decide_next_action(state: InterviewAgentState) -> InterviewAgentState:
    coverage_assessment = state.get("coverage_assessment")
    probe_count = state.get("probe_count", 0)
    max_probes = state.get("max_probes", MAX_PROBES_PER_SECTION)

    if state.get("stop_requested"):
        state["next_action"] = AgentDecision.Action.STOP
        return state

    if state.get("skip_requested"):
        state["next_action"] = AgentDecision.Action.SKIP
        return state

    if state.get("safety_flag") or state.get("derailment_flag"):
        state["next_action"] = AgentDecision.Action.BOUNDARY_RESPONSE
        return state

    if state.get("discomfort_flag"):
        state["next_action"] = AgentDecision.Action.BOUNDARY_RESPONSE
        state["decision_reason"] = (
            "Participant discomfort was detected, so the agent should prioritise "
            "participant control and offer skip/stop rather than collect more data."
        )
        return state
    
    if state.get("current_section_code") == "opening":
        state["next_action"] = AgentDecision.Action.MOVE_NEXT
        state["decision_reason"] = (
            "Opening section only confirms the interview boundary, "
            "so the agent moves to the first research question."
        )
        return state

    if coverage_assessment == AgentDecision.CoverageAssessment.COVERED:
        state["next_action"] = AgentDecision.Action.MOVE_NEXT
        return state

    if coverage_assessment in [
        AgentDecision.CoverageAssessment.PARTIALLY_COVERED,
        AgentDecision.CoverageAssessment.UNCLEAR,
        AgentDecision.CoverageAssessment.OFF_TOPIC,
    ]:
        if probe_count < max_probes:
            state["next_action"] = AgentDecision.Action.ASK_FOLLOW_UP
        else:
            state["next_action"] = AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT
        return state

    state["next_action"] = AgentDecision.Action.MOVE_NEXT
    return state


FOLLOW_UP_TEMPLATES = {
    "opening": (
        "Thank you. To start the interview, can you describe one recent or memorable "
        "sensory overload situation?"
    ),
    "experience": (
        "Could you add one concrete detail about where this happened, what happened, "
        "or why it felt overwhelming?"
    ),
    "triggers_signs": (
        "What signs did you notice in yourself at that moment, such as body sensations, "
        "emotions, or your strongest reaction?"
    ),
    "coping_support": (
        "What did you do to cope, and was there anything that would have supported you better?"
    ),
    "support_concept_reaction": (
        "What part of the PurrStone idea feels useful or concerning to you, and why?"
    ),
    "public_use_acceptability": (
        "What would make it feel acceptable or unacceptable to use something like "
        "PurrStone in that public or shared setting?"
    ),
}

FOLLOW_UP_SCOPE_GUIDANCE = {
    "experience": (
        "Ask only for one missing concrete detail about the sensory overload situation: "
        "where it happened, what happened, or why it felt overwhelming. Do not mention PurrStone."
    ),
    "triggers_signs": (
        "Ask only about the missing sensory trigger, body sign, emotion, thought, or reaction. "
        "Do not ask for medical explanation or diagnosis. Do not mention PurrStone."
    ),
    "coping_support": (
        "Ask only about what the participant did to cope, or what support would have helped. "
        "Keep it as design research, not therapy or treatment. Do not mention PurrStone."
    ),
    "support_concept_reaction": (
        "Ask only about the participant's reaction to the PurrStone concept, including usefulness, "
        "concerns, reasons, or conditions. Do not encourage them to like the idea."
    ),
    "public_use_acceptability": (
        "Ask only about whether using PurrStone in a public or shared setting would feel acceptable, "
        "unacceptable, discreet, visible, embarrassing, or comfortable. Do not lead the participant."
    ),
}


LEADING_OR_UNSAFE_FOLLOW_UP_TERMS = [
    "you should",
    "you need to",
    "you must",
    "this will help",
    "this would help you",
    "purrstone will help",
    "purrstone would help",
    "purrstone is helpful",
    "diagnose",
    "diagnosis",
    "therapy",
    "therapeutic",
    "treatment",
    "medication",
    "medicine",
    "doctor",
    "clinician",
]


def get_template_follow_up(
    section_code: str,
    follow_up_focus: str = "",
    missing_information: Optional[List[str]] = None,
) -> str:
    if section_code in FOLLOW_UP_TEMPLATES:
        return FOLLOW_UP_TEMPLATES[section_code]

    focus = " ".join((follow_up_focus or "").strip().split()).rstrip(".?!")
    if not focus and missing_information:
        focus = str(missing_information[0]).strip().rstrip(".?!")
    if focus:
        focus = focus[:150]
        return f"Could you add one concrete detail about {focus}?"
    return "Could you give one concrete example or detail about that?"


def clean_llm_follow_up(raw_text: str) -> str:
    question = (raw_text or "").strip()

    # Remove accidental code fences if the model returns them.
    question = question.replace("```text", "")
    question = question.replace("```plain", "")
    question = question.replace("```plaintext", "")
    question = question.replace("```", "")
    question = question.strip().strip('"').strip("'").strip()

    # Collapse repeated whitespace into a single space.
    question = " ".join(question.split())

    return question


def is_valid_llm_follow_up(question: str, section_code: str) -> bool:
    lowered = (question or "").lower()

    if not question:
        return False


    if len(question) > 240:
        return False

    # Require exactly one question.
    if not question.endswith("?"):
        return False

    if question.count("?") != 1:
        return False


    if contains_any(lowered, LEADING_OR_UNSAFE_FOLLOW_UP_TERMS):
        return False


    if section_code not in ["support_concept_reaction", "public_use_acceptability"]:
        if "purrstone" in lowered:
            return False

    return True


def llm_generate_follow_up_question(state: InterviewAgentState) -> str:
    """
    Generate wording for one graph-approved follow-up question.

    This function must only be called after LangGraph has already selected
    ASK_FOLLOW_UP. The LLM does not decide routing, section movement,
    probe count, safety handling, skip, or stop.
    """
    if state.get("next_action") != AgentDecision.Action.ASK_FOLLOW_UP:
        raise RuntimeError(
            "LLM follow-up wording can only run after LangGraph selects ASK_FOLLOW_UP."
        )

    if os.getenv("DISABLE_LLM_FOLLOWUP_WORDING", "").lower() in ["1", "true", "yes"]:
        raise RuntimeError("LLM follow-up wording disabled by environment flag.")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)

    section_code = state.get("current_section_code", "")
    section_label = state.get("current_section_label", "")
    section_purpose = state.get("section_purpose", "")
    primary_question = state.get("primary_question", "")
    participant_answer = state.get("reply_text", "")
    missing_information = state.get("missing_information", [])
    follow_up_focus = state.get("follow_up_focus", "")
    interaction_boundary = state.get("interaction_boundary", "")
    protocol_rules = state.get("protocol_rules", [])

    scope_guidance = FOLLOW_UP_SCOPE_GUIDANCE.get(
        section_code,
        follow_up_focus
        or "Ask only for one concrete missing detail within the current protocol section.",
    )

    prompt = f"""
You are helping phrase a follow-up question for a semi-structured design research interview.

LangGraph has already decided that the next action is ASK_FOLLOW_UP.
Your task is only to word one short, neutral follow-up question.

You must not decide the next action.
You must not move to another interview section.
You must not ask more than one question.
You must not give medical, diagnostic, therapeutic, or wellbeing advice.
You must not lead the participant toward liking PurrStone.
You must not add assumptions that are not grounded in the participant answer.

Current section:
{section_label}

Section purpose:
{section_purpose}

Primary question for this section:
{primary_question}

Missing information to probe:
{json.dumps(missing_information, ensure_ascii=False)}

Participant answer:
{participant_answer}

Scope guidance:
{scope_guidance}

Topic-specific interaction boundary:
{interaction_boundary or "No additional topic-specific boundary."}

Additional rules from the locked Protocol:
{json.dumps(protocol_rules, ensure_ascii=False)}

Return only the follow-up question as plain text.
The question must be one sentence, under 35 words, neutral, and focused on the missing information.
"""

    response = client.responses.create(
        model=get_openai_model(),
        input=prompt,
        temperature=0.2,
        max_output_tokens=120,
        store=False,
    )

    question = clean_llm_follow_up(response.output_text)

    if not is_valid_llm_follow_up(question, section_code):
        raise ValueError(f"Invalid LLM follow-up wording: {question}")

    return question

def generate_agent_message(state: InterviewAgentState) -> InterviewAgentState:
    action = state.get("next_action")
    section_code = state.get("current_section_code", "")

    if action == AgentDecision.Action.ASK_FOLLOW_UP:
        try:
            state["agent_message"] = llm_generate_follow_up_question(state)
            state["decision_reason"] = (
                state.get("decision_reason", "")
                + " Follow-up wording generated by constrained LLM after LangGraph selected ASK_FOLLOW_UP."
            ).strip()

        except Exception as error:
            state["agent_message"] = get_template_follow_up(
                section_code,
                state.get("follow_up_focus", ""),
                state.get("missing_information", []),
            )
            state["decision_reason"] = (
                state.get("decision_reason", "")
                + f" Template follow-up used because LLM wording support failed: {type(error).__name__}."
            ).strip()

    elif action == AgentDecision.Action.BOUNDARY_RESPONSE:
        if state.get("discomfort_flag"):
            state["agent_message"] = (
                "Thank you for telling me. We do not need to continue with that detail. "
                "You can skip this question or stop the interview at any time. "
                "Would you like to move to the next question?"
            )
        else:
            state["agent_message"] = (
                "I cannot provide medical, diagnostic, or therapeutic advice. "
                "This interview is only for design research. You can skip this question "
                "or stop at any time. If you are comfortable continuing, please answer "
                "only in terms of your experience with the situation."
            )

    elif action == AgentDecision.Action.STOP:
        state["agent_message"] = (
            "Thank you for taking part. I will stop the interview here and save what "
            "you have already shared for researcher review."
        )

    elif action == AgentDecision.Action.SKIP:
        state["agent_message"] = "No problem. I will skip this question and move to the next part."

    elif action == AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT:
        state["agent_message"] = (
            "Thank you. I will mark that some information was missing and move to the next part."
        )

    else:
        state["agent_message"] = ""

    return state


def create_agent_message(
    session: InterviewSession,
    content: str,
    section: Optional[Dict[str, Any]],
    section_index: int,
) -> Message:
    return Message.objects.create(
        session=session,
        sender=Message.Sender.AGENT,
        content=content,
        section=get_section_label(section),
        section_index=section_index,
    )


def create_system_message(
    session: InterviewSession,
    content: str,
    section: Optional[Dict[str, Any]],
    section_index: int,
) -> Message:
    return Message.objects.create(
        session=session,
        sender=Message.Sender.SYSTEM,
        content=content,
        section=get_section_label(section),
        section_index=section_index,
    )


def complete_interview(session: InterviewSession) -> None:
    sections = session.protocol.sections or []
    final_index = len(sections) - 1

    if final_index >= 0:
        final_section = sections[final_index]
        create_agent_message(
            session=session,
            content=final_section.get(
                "primary_question",
                "Thank you. I will now summarise what you shared for researcher review.",
            ),
            section=final_section,
            section_index=final_index,
        )
        session.current_section_index = final_index

    session.status = InterviewSession.Status.COMPLETED
    session.transcript_saved = True
    session.summary_generated = True
    session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
    session.output_quality_status = InterviewSession.OutputQualityStatus.WAITING
    session.completed_at = timezone.now()
    session.save()
    ensure_digest_items(session)

    session.stakeholder.status = Stakeholder.Status.COMPLETED
    session.stakeholder.save()


def move_to_next_or_complete(session: InterviewSession) -> None:
    current_index = session.current_section_index

    if current_index >= last_participant_question_index(session):
        complete_interview(session)
        return

    session.current_section_index = current_index + 1
    session.save()

    next_section = get_section(session)
    if next_section:
        create_agent_message(
            session=session,
            content=next_section.get("primary_question", "Thank you. Let us continue."),
            section=next_section,
            section_index=session.current_section_index,
        )


def persist_turn_and_decision(state: InterviewAgentState) -> InterviewAgentState:
    session = InterviewSession.objects.select_related("stakeholder", "protocol").get(
        id=state["session_id"]
    )

    section = get_section(session)
    section_index = session.current_section_index
    action = state.get("next_action", AgentDecision.Action.MOVE_NEXT)

    participant_message = None
    participant_message_id = state.get("participant_message_id")
    if participant_message_id:
        participant_message = Message.objects.filter(id=participant_message_id).first()

    decision_record = AgentDecision.objects.create(
        session=session,
        message=participant_message,
        section=get_section_label(section),
        section_index=section_index,
        coverage_assessment=state.get(
            "coverage_assessment",
            AgentDecision.CoverageAssessment.NOT_ASSESSED,
        ),
        participant_control=state.get(
            "participant_control",
            AgentDecision.ParticipantControl.NONE,
        ),
        action=action,
        probe_count_before=state.get("probe_count", 0),
        covered_information=state.get("covered_information", []),
        missing_information=state.get("missing_information", []),
        decision_reason=state.get("decision_reason", ""),
    )

    if action == AgentDecision.Action.ASK_FOLLOW_UP:
        create_agent_message(
            session=session,
            content=state.get("agent_message", "Could you say a little more about that?"),
            section=section,
            section_index=section_index,
        )
        return state

    if action == AgentDecision.Action.BOUNDARY_RESPONSE:
        create_agent_message(
            session=session,
            content=state.get("agent_message", ""),
            section=section,
            section_index=section_index,
        )
        return state

    if action == AgentDecision.Action.STOP:
        if participant_message is None:
            control_message = create_system_message(
                session=session,
                content="Participant used the Stop control.",
                section=section,
                section_index=section_index,
            )
            decision_record.message = control_message
            decision_record.save(update_fields=["message"])
        create_agent_message(
            session=session,
            content=state.get("agent_message", "Thank you. I will stop the interview here."),
            section=section,
            section_index=section_index,
        )
        session.status = InterviewSession.Status.STOPPED
        session.transcript_saved = True
        session.summary_generated = True
        session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
        session.output_quality_status = InterviewSession.OutputQualityStatus.WAITING
        session.completed_at = timezone.now()
        session.save()
        ensure_digest_items(session)
        return state

    if action == AgentDecision.Action.SKIP:
        if participant_message is None:
            control_message = create_system_message(
                session=session,
                content="Participant used the Skip control.",
                section=section,
                section_index=section_index,
            )
            decision_record.message = control_message
            decision_record.save(update_fields=["message"])
        move_to_next_or_complete(session)
        return state

    if action == AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT:
        move_to_next_or_complete(session)
        return state

    if action == AgentDecision.Action.MOVE_NEXT:
        move_to_next_or_complete(session)
        return state

    if action == AgentDecision.Action.COMPLETE:
        complete_interview(session)
        return state

    return state




def load_session_state(state: InterviewAgentState) -> InterviewAgentState:
    session = InterviewSession.objects.select_related("stakeholder", "protocol").get(
        id=state["session_id"]
    )
    section = get_section(session)

    state["current_section_index"] = session.current_section_index
    state["current_section_code"] = get_section_code(section)
    state["current_section_label"] = get_section_label(section)
    state["section_purpose"] = section.get("purpose", "") if section else ""
    state["primary_question"] = section.get("primary_question", "") if section else ""
    state["required_information"] = section.get("required_information", []) if section else []
    state["assessment_guidance"] = section.get("assessment_guidance", "") if section else ""
    state["follow_up_focus"] = section.get("follow_up_focus", "") if section else ""
    state["interaction_boundary"] = section.get("interaction_boundary", "") if section else ""
    state["protocol_rules"] = [
        str(rule) for rule in (session.protocol.ethics_rules or [])
        if str(rule).strip()
    ]
    state["probe_count"] = count_probes_for_section(session, session.current_section_index)
    state["max_probes"] = MAX_PROBES_PER_SECTION
    participant_answers = session.messages.filter(
        sender=Message.Sender.PARTICIPANT,
        section_index=session.current_section_index,
    ).order_by("created_at", "id")
    state["cumulative_reply_text"] = "\n".join(
        message.content.strip()
        for message in participant_answers
        if message.content.strip()
    )

    return state


def build_interview_graph():
    graph = StateGraph(InterviewAgentState)

    graph.add_node("load_session_state", load_session_state)
    graph.add_node("detect_control_or_safety_signal", detect_control_or_safety_signal)
    graph.add_node("assess_protocol_coverage", assess_protocol_coverage)
    graph.add_node("decide_next_action", decide_next_action)
    graph.add_node("generate_agent_message", generate_agent_message)
    graph.add_node("persist_turn_and_decision", persist_turn_and_decision)

    graph.set_entry_point("load_session_state")
    graph.add_edge("load_session_state", "detect_control_or_safety_signal")
    graph.add_edge("detect_control_or_safety_signal", "assess_protocol_coverage")
    graph.add_edge("assess_protocol_coverage", "decide_next_action")
    graph.add_edge("decide_next_action", "generate_agent_message")
    graph.add_edge("generate_agent_message", "persist_turn_and_decision")
    graph.add_edge("persist_turn_and_decision", END)

    return graph.compile()


interview_graph = build_interview_graph()



def handle_participant_reply_with_graph(session: InterviewSession, reply_text: str):
    section = get_section(session)

    participant_message = Message.objects.create(
        session=session,
        sender=Message.Sender.PARTICIPANT,
        content=reply_text,
        section=get_section_label(section),
        section_index=session.current_section_index,
    )

    initial_state: InterviewAgentState = {
        "session_id": session.id,
        "participant_message_id": participant_message.id,
        "reply_text": reply_text,
    }

    return interview_graph.invoke(initial_state)


def skip_current_question_with_graph(session: InterviewSession):
    initial_state: InterviewAgentState = {
        "session_id": session.id,
        "participant_message_id": None,
        "reply_text": "skip",
    }

    return interview_graph.invoke(initial_state)


def stop_interview_with_graph(session: InterviewSession):
    initial_state: InterviewAgentState = {
        "session_id": session.id,
        "participant_message_id": None,
        "reply_text": "stop",
    }

    return interview_graph.invoke(initial_state)

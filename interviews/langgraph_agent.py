import json
import os
from typing import Any, Dict, List, Optional, TypedDict

from django.utils import timezone
from langgraph.graph import END, StateGraph
from openai import OpenAI

from .models import AgentDecision, InterviewSession, Message, Stakeholder


MAX_PROBES_PER_SECTION = 1


class InterviewAgentState(TypedDict, total=False):
    session_id: int
    participant_message_id: Optional[int]
    reply_text: str

    current_section_index: int
    current_section_code: str
    current_section_label: str
    section_purpose: str
    primary_question: str
    required_information: List[str]

    answer_status: str
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
    "medcine",
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


def detect_control_or_safety_signal(state: InterviewAgentState) -> InterviewAgentState:
    text = state.get("reply_text", "")

    state["stop_requested"] = contains_any(text, STOP_TERMS)
    state["skip_requested"] = contains_any(text, SKIP_TERMS)
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


def assess_response_sufficiency_heuristic(state: InterviewAgentState) -> InterviewAgentState:
    """
    This node does not decide the next action.

    It only assesses whether the participant response appears to cover the
    current protocol section's required information.

    Later, this function can be replaced with constrained LLM assessment
    returning the same fields:
    - answer_status
    - covered_information
    - missing_information
    - evidence_quote
    - decision_reason
    """
    text = state.get("reply_text", "").strip()
    section_code = state.get("current_section_code", "")
    required = state.get("required_information", [])

    if state.get("stop_requested"):
        state["answer_status"] = AgentDecision.AnswerStatus.STOPPED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = "Participant requested to stop the interview."
        return state

    if state.get("skip_requested"):
        state["answer_status"] = AgentDecision.AnswerStatus.SKIPPED
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = "Participant requested to skip the current question."
        return state

    if state.get("safety_flag") or state.get("derailment_flag"):
        state["answer_status"] = AgentDecision.AnswerStatus.SAFETY_BOUNDARY
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "Participant input triggered a non-clinical or instruction-boundary response."
        )
        return state

    if state.get("discomfort_flag"):
        state["answer_status"] = AgentDecision.AnswerStatus.SAFETY_BOUNDARY
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "Participant expressed discomfort with the current question, so the agent should "
            "prioritise participant control and offer skip/stop rather than collect more data."
        )
        return state

    if section_code == "opening":
        state["answer_status"] = AgentDecision.AnswerStatus.SUFFICIENT
        state["covered_information"] = ["opening acknowledged"]
        state["missing_information"] = []
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "Opening section is a boundary and transition step "
            "rather than a research-content section."
        )
        return state


    if contains_any(text, VAGUE_TERMS):
        state["answer_status"] = AgentDecision.AnswerStatus.VAGUE
        state["covered_information"] = []
        state["missing_information"] = required
        state["evidence_quote"] = text
        state["decision_reason"] = (
            "The answer is vague or uncertain and does not clearly cover the section requirements."
        )
        return state


    if not text or word_count(text) <= 3:
        state["answer_status"] = AgentDecision.AnswerStatus.TOO_SHORT
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
        if word_count(text) >= 12:
            covered = required or ["general response"]
            missing = []
        else:
            covered = []
            missing = required or ["more detail"]

    state["covered_information"] = covered
    state["missing_information"] = missing
    state["evidence_quote"] = text[:300]

    if not missing:
        state["answer_status"] = AgentDecision.AnswerStatus.SUFFICIENT
        state["decision_reason"] = (
            "The answer covers the protocol-defined required information for this section."
        )
    elif covered:
        state["answer_status"] = AgentDecision.AnswerStatus.PARTIAL
        state["decision_reason"] = (
            "The answer covers some required information but is missing: "
            + ", ".join(missing)
            + "."
        )
    else:
        state["answer_status"] = AgentDecision.AnswerStatus.VAGUE
        state["decision_reason"] = (
            "The answer does not clearly cover the protocol-defined required information."
        )

    return state

ALLOWED_LLM_ANSWER_STATUSES = {
    AgentDecision.AnswerStatus.SUFFICIENT,
    AgentDecision.AnswerStatus.PARTIAL,
    AgentDecision.AnswerStatus.VAGUE,
    AgentDecision.AnswerStatus.OFF_TOPIC,
}


def _safe_list(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def get_section_assessment_guidance(section_code: str) -> str:
    guidance = {
        "experience": (
            "For the Experience section, treat the response as sufficient if it includes "
            "a concrete setting or context, a concrete situation or sensory difficulty, "
            "and a reason or effect showing why it felt overwhelming. "
            "A phrase such as 'I could not hear what people were saying' counts as what happened "
            "because it describes the experienced difficulty."
        ),
        "triggers_signs": (
            "For the Triggers and signs section, treat the response as sufficient only if it includes "
            "at least one trigger and at least one bodily, emotional, cognitive, or behavioural sign. "
            "If it only describes a trigger, mark it partial and list the missing sign or reaction."
        ),
        "coping_support": (
            "For the Coping and support section, treat the response as sufficient if it describes "
            "what the participant did to cope and/or what support would have helped. "
            "If only one of these is present, mark it partial."
        ),
        "support_concept_reaction": (
            "For the Support concept reaction section, treat the response as sufficient if it gives "
            "a reaction to the PurrStone concept and at least one reason, usefulness, concern, or condition."
        ),
        "public_use_acceptability": (
            "For the Public-use acceptability section, treat the response as sufficient if it discusses "
            "a public or shared context and a condition, concern, or reason related to acceptability."
        ),
    }

    return guidance.get(
        section_code,
        "Assess against the required information for the current section."
    )

def llm_assess_response_sufficiency(state: InterviewAgentState) -> Dict[str, Any]:
    """
    LLM-assisted semantic assessment.

    The LLM assesses whether the participant response covers the current
    protocol section's required information. It is not allowed to decide
    next_action. LangGraph routing remains responsible for action selection.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)

    section_label = state.get("current_section_label", "")
    section_purpose = state.get("section_purpose", "")
    required_information = state.get("required_information", [])
    participant_answer = state.get("reply_text", "")
    section_code = state.get("current_section_code", "")
    assessment_guidance = get_section_assessment_guidance(section_code)

    prompt = f"""
You are assessing a participant response for a semi-structured design research interview.

Your task is ONLY to assess whether the participant response covers the required information for the current protocol section.

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

Participant response:
{participant_answer}

Return only valid JSON with exactly these fields:
{{
  "answer_status": "sufficient" | "partial" | "vague" | "off_topic",
  "covered_information": ["..."],
  "missing_information": ["..."],
  "evidence_quote": "...",
  "assessment_reason": "..."
}}

Assessment rules:
- Use "sufficient" only if the response covers the key required information for this section.
- Use "partial" if it covers some relevant information but misses important required information.
- Use "vague" if it is too general, unclear, or does not provide usable detail.
- Use "off_topic" if it does not answer the current section.
- Do not invent information that is not in the participant response.
- Keep evidence_quote short and copied from the participant response where possible.
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        temperature=0,
    )

    raw_text = response.output_text.strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()

    data = json.loads(raw_text)

    answer_status = str(data.get("answer_status", "")).strip().lower()

    if answer_status not in ALLOWED_LLM_ANSWER_STATUSES:
        raise ValueError(f"Invalid LLM answer_status: {answer_status}")

    return {
        "answer_status": answer_status,
        "covered_information": _safe_list(data.get("covered_information")),
        "missing_information": _safe_list(data.get("missing_information")),
        "evidence_quote": str(data.get("evidence_quote", ""))[:300],
        "decision_reason": str(data.get("assessment_reason", "")),
    }


def assess_response_sufficiency(state: InterviewAgentState) -> InterviewAgentState:
    """
    Main assessment node.

    Deterministic control/safety cases and opening handling remain heuristic.
    For ordinary research-content answers, use LLM-assisted semantic assessment.
    If the LLM fails, fall back to protocol-defined heuristic assessment.
    """
    text = state.get("reply_text", "").strip()
    section_code = state.get("current_section_code", "")

    # These cases should not rely on LLM interpretation.
    if (
        state.get("stop_requested")
        or state.get("skip_requested")
        or state.get("safety_flag")
        or state.get("discomfort_flag")
        or state.get("derailment_flag")
        or section_code == "opening"
        or not text
        or word_count(text) <= 3
        or contains_any(text, VAGUE_TERMS)
    ):
        return assess_response_sufficiency_heuristic(state)

    try:
        assessment = llm_assess_response_sufficiency(state)

        state["answer_status"] = assessment["answer_status"]
        state["covered_information"] = assessment["covered_information"]
        state["missing_information"] = assessment["missing_information"]
        state["evidence_quote"] = assessment["evidence_quote"]
        state["decision_reason"] = (
            "LLM-assisted assessment: " + assessment["decision_reason"]
        )

        return state

    except Exception as error:
        # Do not break the interview if the LLM fails.
        state = assess_response_sufficiency_heuristic(state)
        state["decision_reason"] = (
            state.get("decision_reason", "")
            + f" Fallback used because LLM assessment failed: {error}"
        )
        return state




def decide_next_action(state: InterviewAgentState) -> InterviewAgentState:
    answer_status = state.get("answer_status")
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

    if answer_status == AgentDecision.AnswerStatus.SUFFICIENT:
        state["next_action"] = AgentDecision.Action.MOVE_NEXT
        return state

    if answer_status in [
        AgentDecision.AnswerStatus.PARTIAL,
        AgentDecision.AnswerStatus.VAGUE,
        AgentDecision.AnswerStatus.TOO_SHORT,
        AgentDecision.AnswerStatus.OFF_TOPIC,
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


def get_template_follow_up(section_code: str) -> str:
    return FOLLOW_UP_TEMPLATES.get(
        section_code,
        "Could you give one concrete example or detail about that?",
    )


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

    scope_guidance = FOLLOW_UP_SCOPE_GUIDANCE.get(
        section_code,
        "Ask only for one concrete missing detail within the current protocol section.",
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

Return only the follow-up question as plain text.
The question must be one sentence, under 35 words, neutral, and focused on the missing information.
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        temperature=0.2,
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
            state["agent_message"] = get_template_follow_up(section_code)
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

    AgentDecision.objects.create(
        session=session,
        message=participant_message,
        section=get_section_label(section),
        section_index=section_index,
        answer_status=state.get("answer_status", AgentDecision.AnswerStatus.PARTIAL),
        action=action,
        probe_count_before=state.get("probe_count", 0),
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
        return state

    if action == AgentDecision.Action.SKIP:
        create_system_message(
            session=session,
            content="Participant skipped this question.",
            section=section,
            section_index=section_index,
        )
        move_to_next_or_complete(session)
        return state

    if action == AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT:
        missing = state.get("missing_information", [])
        create_system_message(
            session=session,
            content="Missing information flagged: " + ", ".join(missing),
            section=section,
            section_index=section_index,
        )
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
    state["probe_count"] = count_probes_for_section(session, session.current_section_index)
    state["max_probes"] = MAX_PROBES_PER_SECTION

    return state


def build_interview_graph():
    graph = StateGraph(InterviewAgentState)

    graph.add_node("load_session_state", load_session_state)
    graph.add_node("detect_control_or_safety_signal", detect_control_or_safety_signal)
    graph.add_node("assess_response_sufficiency", assess_response_sufficiency)
    graph.add_node("decide_next_action", decide_next_action)
    graph.add_node("generate_agent_message", generate_agent_message)
    graph.add_node("persist_turn_and_decision", persist_turn_and_decision)

    graph.set_entry_point("load_session_state")
    graph.add_edge("load_session_state", "detect_control_or_safety_signal")
    graph.add_edge("detect_control_or_safety_signal", "assess_response_sufficiency")
    graph.add_edge("assess_response_sufficiency", "decide_next_action")
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
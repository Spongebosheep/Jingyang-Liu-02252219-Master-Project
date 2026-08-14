"""Strict prompt-only turn adapter for the matched evaluation condition.

The adapter owns no interview routing rules beyond the versioned prompt.  It
submits one turn context, preserves the raw response, and either returns an
exactly schema-valid parsed object or raises.  It never imports or calls the
product LangGraph, product decision functions, or an oracle repair path.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI

from .schemas import load_json, sha256_file, sha256_json


BASELINE_ADAPTER_VERSION = "3.0"
BASELINE_PROMPT_VERSION = "3.0"
BASELINE_OUTPUT_SCHEMA_VERSION = "2.0"
BASELINE_CALL_KIND = "prompt_only_turn"
BASELINE_TEMPERATURE = 0
BASELINE_MAX_OUTPUT_TOKENS = 500
BASELINE_STORE = False

EVALUATION_DIR = Path(__file__).resolve().parent
PROMPT_PATH = EVALUATION_DIR / "prompts" / "prompt_only_baseline.v3.txt"
OUTPUT_SCHEMA_PATH = EVALUATION_DIR / "specs" / "prompt_only_output_schema.v2.json"


class BaselineAdapterError(RuntimeError):
    """Base class for prompt-only adapter failures."""


class BaselineConfigurationError(BaselineAdapterError):
    """Raised when the live model transport cannot be configured."""


class BaselineParseError(BaselineAdapterError):
    """Raised when raw model output does not exactly satisfy the frozen schema."""


class ResponsesTransport(Protocol):
    transport_name: str

    def create(self, **kwargs: Any) -> Any:
        """Return one object exposing ``output_text``."""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(child) for child in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    return str(value)


def get_baseline_model() -> str:
    """Mirror the product's public model configuration without importing it."""

    return os.getenv("PURRSTONE_OPENAI_MODEL", "gpt-4.1-mini")


class OpenAIResponsesTransport:
    transport_name = "openai_responses_api"

    def __init__(self, api_key: str | None = None) -> None:
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            raise BaselineConfigurationError("OPENAI_API_KEY is not set.")
        self._client = OpenAI(api_key=resolved_key)

    def create(self, **kwargs: Any) -> Any:
        return self._client.responses.create(**kwargs)


def baseline_adapter_identity() -> dict[str, Any]:
    return {
        "adapter_version": BASELINE_ADAPTER_VERSION,
        "prompt_version": BASELINE_PROMPT_VERSION,
        "prompt_path": str(PROMPT_PATH.relative_to(EVALUATION_DIR)),
        "prompt_sha256": sha256_file(PROMPT_PATH),
        "output_schema_version": BASELINE_OUTPUT_SCHEMA_VERSION,
        "output_schema_path": str(OUTPUT_SCHEMA_PATH.relative_to(EVALUATION_DIR)),
        "output_schema_sha256": sha256_file(OUTPUT_SCHEMA_PATH),
        "model_parameters": {
            "temperature": BASELINE_TEMPERATURE,
            "max_output_tokens": BASELINE_MAX_OUTPUT_TOKENS,
            "store": BASELINE_STORE,
        },
        "routing_provenance": "not_applicable_prompt_only_condition",
        "graph_approved_follow_up_wording": "not_applicable_prompt_only_condition",
    }


def _require_schema_valid_output(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BaselineParseError("Baseline output must be one JSON object.")

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    actual = set(value)
    if actual != required or actual != set(properties):
        missing = sorted(required - actual)
        extra = sorted(actual - set(properties))
        raise BaselineParseError(
            f"Baseline output fields differ from the frozen schema; missing={missing}, extra={extra}."
        )

    for field in (
        "coverage_assessment",
        "participant_control",
        "selected_action",
    ):
        field_value = value[field]
        allowed = properties[field].get("enum") or []
        if not isinstance(field_value, str) or field_value not in allowed:
            raise BaselineParseError(
                f"{field} must be one exact frozen enum value; got {field_value!r}."
            )

    for field in ("covered_information", "missing_information"):
        field_value = value[field]
        if not isinstance(field_value, list) or not all(
            isinstance(item, str) for item in field_value
        ):
            raise BaselineParseError(f"{field} must be an array of strings.")

    for field in ("reason", "participant_response"):
        field_value = value[field]
        rules = properties[field]
        if not isinstance(field_value, str):
            raise BaselineParseError(f"{field} must be a string.")
        if len(field_value) < int(rules.get("minLength", 0)):
            raise BaselineParseError(f"{field} is shorter than the frozen minimum.")
        if len(field_value) > int(rules.get("maxLength", len(field_value))):
            raise BaselineParseError(f"{field} exceeds the frozen maximum.")

    coverage = value["coverage_assessment"]
    control = value["participant_control"]
    action = value["selected_action"]
    missing = value["missing_information"]
    if control == "skip" and (coverage, action) != ("not_assessed", "skip"):
        raise BaselineParseError(
            "A Skip control must remain not_assessed and select the skip action."
        )
    if control == "stop" and (coverage, action) != ("not_assessed", "stop"):
        raise BaselineParseError(
            "A Stop control must remain not_assessed and select the stop action."
        )
    if control == "none" and action in {"skip", "stop"}:
        raise BaselineParseError(
            "Skip/Stop actions require the matching explicit participant control."
        )
    if coverage == "covered":
        if action not in {"move_next", "complete"} or missing:
            raise BaselineParseError(
                "Covered output must advance and must not report missing information."
            )
    if action in {"ask_follow_up", "flag_missing_and_move_next"}:
        if coverage not in {"partially_covered", "unclear", "off_topic"} or not missing:
            raise BaselineParseError(
                "A missing-information action requires a non-covered assessment and a non-empty missing-information list."
            )
    if action == "boundary_response" and coverage != "not_assessed":
        raise BaselineParseError(
            "A boundary response must not assign Protocol coverage."
        )

    return value


class PromptOnlyBaselineAdapter:
    """Submit one prompt-only turn and strictly parse the unmodified response."""

    def __init__(self, transport: ResponsesTransport | None = None) -> None:
        self.transport = transport or OpenAIResponsesTransport()
        self.prompt_text = PROMPT_PATH.read_text(encoding="utf-8")
        self.output_schema = load_json(OUTPUT_SCHEMA_PATH)

    def build_request(self, turn_context: dict[str, Any]) -> dict[str, Any]:
        context_json = json.dumps(
            _jsonable(turn_context),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        full_prompt = f"{self.prompt_text}\nTURN_CONTEXT_JSON:\n{context_json}\n"
        return {
            "model": get_baseline_model(),
            "input": full_prompt,
            "temperature": BASELINE_TEMPERATURE,
            "max_output_tokens": BASELINE_MAX_OUTPUT_TOKENS,
            "store": BASELINE_STORE,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "purrstone_prompt_only_turn",
                    "strict": True,
                    "schema": self.output_schema,
                }
            },
        }

    def call(self, turn_context: dict[str, Any]) -> dict[str, Any]:
        """Call the transport and return raw artefacts without parsing them."""

        request = self.build_request(turn_context)
        return self.call_request(request)

    def call_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Call an already-built request so a runner can persist it first."""

        response = self.transport.create(**request)
        raw_output = getattr(response, "output_text", None)
        if not isinstance(raw_output, str):
            raise BaselineParseError("Responses API result has no string output_text.")
        return {
            "call_kind": BASELINE_CALL_KIND,
            "transport": self.transport.transport_name,
            "request": _jsonable(request),
            "response": {
                "output_text": raw_output,
                "raw": _jsonable(response),
            },
        }

    def parse_response(self, call: dict[str, Any]) -> dict[str, Any]:
        """Strictly parse a saved raw call without repairing its output."""

        raw_output = (call.get("response") or {}).get("output_text")
        if not isinstance(raw_output, str):
            raise BaselineParseError("Saved baseline call has no string output_text.")
        try:
            decoded = json.loads(raw_output)
        except json.JSONDecodeError as error:
            raise BaselineParseError(
                f"Baseline output is not valid JSON: {error.msg}."
            ) from error
        parsed = _require_schema_valid_output(decoded, self.output_schema)
        return parsed

    def execute(self, turn_context: dict[str, Any]) -> dict[str, Any]:
        """Convenience method for callers that do not need failure artefact hooks."""

        call = self.call(turn_context)
        parsed = self.parse_response(call)
        call["parsed_output"] = parsed
        call["parsed_output_sha256"] = sha256_json(parsed)
        return call


__all__ = [
    "BASELINE_ADAPTER_VERSION",
    "BASELINE_CALL_KIND",
    "BASELINE_MAX_OUTPUT_TOKENS",
    "BASELINE_OUTPUT_SCHEMA_VERSION",
    "BASELINE_PROMPT_VERSION",
    "BASELINE_STORE",
    "BASELINE_TEMPERATURE",
    "BaselineAdapterError",
    "BaselineConfigurationError",
    "BaselineParseError",
    "OpenAIResponsesTransport",
    "OUTPUT_SCHEMA_PATH",
    "PROMPT_PATH",
    "PromptOnlyBaselineAdapter",
    "baseline_adapter_identity",
    "get_baseline_model",
]

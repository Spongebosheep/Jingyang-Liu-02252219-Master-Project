"""Execute frozen S1-S8 through the matched prompt-only comparator.

This runner deliberately does not import the product interview agent or any
LangGraph routing function.  The versioned prompt selects each action; this
adapter records and mechanically applies that selection without semantic
repair or oracle-based correction.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import django
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from interviews.digest import ensure_digest_items
from interviews.models import (
    AgentDecision,
    DigestReviewEvent,
    InterviewSession,
    Message,
    Protocol,
    ReviewDecision,
    Stakeholder,
    StructuredDigestItem,
)
from interviews.protocol_snapshot import build_protocol_snapshot_record

from .baseline_adapter import (
    BASELINE_CALL_KIND,
    PromptOnlyBaselineAdapter,
    baseline_adapter_identity,
    get_baseline_model,
)
from .execution_plan import CURRENT_FREEZE_MANIFEST_PATH
from .schemas import (
    BASELINE_CONDITION,
    SCENARIO_IDS,
    load_json,
    sha256_file,
    sha256_json,
)
from .validate_specs import SPEC_DIR, validate_all


RUNNER_SCHEMA_VERSION = "1.0"
RUN_TYPE_DRY = "dry_run"
RUN_TYPE_FORMAL = "formal"
RUN_TYPES = (RUN_TYPE_DRY, RUN_TYPE_FORMAL)
FREEZE_MANIFEST_PATH = CURRENT_FREEZE_MANIFEST_PATH


class BaselineRunnerError(RuntimeError):
    """Base error for prompt-only runner failures."""


class BaselinePreflightError(BaselineRunnerError):
    """Raised before a baseline Session is created when a gate is unmet."""


class BaselineExecutionError(BaselineRunnerError):
    """Raised when the adapter or compatible workflow layer cannot proceed."""


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, uuid.UUID, Path)):
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


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return cleaned or "event"


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _git(args: list[str], repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselinePreflightError(f"Cannot record Git identity: {error}") from error
    return result.stdout.strip()


class _FixtureResponse:
    def __init__(self, output_text: str, response_id: str) -> None:
        self.output_text = output_text
        self.response_id = response_id

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "id": self.response_id,
            "object": "mock.prompt_only_response",
            "output_text": self.output_text,
            "mode": mode,
        }


class FrozenFixtureBaselineTransport:
    """Non-formal oracle-shaped transport used only to test the harness."""

    transport_name = "frozen_fixture_mock"
    uses_frozen_oracle = True

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.steps = [
            step
            for step in scenario["steps"]
            if step["kind"] in {"participant_turn", "participant_control"}
        ]
        self.call_count = 0

    @staticmethod
    def _missing(oracle: dict[str, Any]) -> list[str]:
        if "expected_missing_information" in oracle:
            return list(oracle["expected_missing_information"])
        if "expected_missing_information_contains" in oracle:
            return list(oracle["expected_missing_information_contains"])
        if oracle.get("expected_missing_information_nonempty"):
            return ["required detail not provided"]
        return []

    @staticmethod
    def _participant_response(action: str) -> str:
        responses = {
            "ask_follow_up": (
                "Could you describe what happened and why it felt overwhelming?"
            ),
            "move_next": "Thank you. Let us continue with the next topic.",
            "flag_missing_and_move_next": (
                "Thank you. I will record that some information is still missing and continue."
            ),
            "skip": "Okay. We will skip this topic and continue.",
            "stop": "Thank you. I will stop the interview here.",
            "boundary_response": (
                "I cannot provide medical, diagnostic, or therapeutic advice. "
                "This is design research, so I can only continue with the interview topic."
            ),
            "complete": "Thank you. The interview is complete.",
        }
        return responses[action]

    def create(self, **kwargs: Any) -> _FixtureResponse:
        if not self.steps:
            raise BaselineExecutionError(
                "The mock transport received more calls than the frozen scenario."
            )
        if kwargs.get("store") is not False:
            raise BaselineExecutionError("The prompt-only call must use store=False.")
        strict = (((kwargs.get("text") or {}).get("format") or {}).get("strict"))
        if strict is not True:
            raise BaselineExecutionError("The prompt-only call must use a strict schema.")

        step = self.steps.pop(0)
        oracle = step["oracle"]
        action = oracle["expected_action"]
        assessments = list(
            oracle.get("expected_coverage_assessment_any_of") or []
        )
        coverage_assessment = assessments[0] if assessments else "not_assessed"
        participant_control = oracle.get("expected_participant_control", "none")
        missing = self._missing(oracle)
        output = {
            "coverage_assessment": coverage_assessment,
            "participant_control": participant_control,
            "selected_action": action,
            "covered_information": (
                ["mocked infrastructure coverage"]
                if coverage_assessment == "covered"
                else []
            ),
            "missing_information": missing,
            "reason": "Frozen fixture response for non-formal infrastructure testing.",
            "participant_response": self._participant_response(action),
        }
        self.call_count += 1
        return _FixtureResponse(
            json.dumps(output, ensure_ascii=False),
            f"mock-{self.call_count:03d}",
        )


class PromptOnlyBaselineRunner:
    """Run one frozen scenario through the direct prompt-only adapter."""

    def __init__(
        self,
        *,
        scenario_id: str,
        repetition: int,
        run_type: str,
        output_root: Path,
        reviewer_username: str | None = None,
        transport: Any | None = None,
        repository_root: Path | None = None,
    ) -> None:
        if scenario_id not in SCENARIO_IDS:
            raise ValueError(f"scenario_id must be one of {SCENARIO_IDS}.")
        if repetition < 1:
            raise ValueError("repetition must be at least 1.")
        if run_type not in RUN_TYPES:
            raise ValueError(f"run_type must be one of {RUN_TYPES}.")

        self.scenario_id = scenario_id
        self.repetition = repetition
        self.run_type = run_type
        self.output_root = Path(output_root).resolve()
        self.reviewer_username = (reviewer_username or "").strip()
        self.transport = transport
        self.repository_root = (
            Path(repository_root).resolve()
            if repository_root
            else Path(__file__).resolve().parents[1]
        )

        timestamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        suffix = uuid.uuid4().hex[:8]
        self.run_id = (
            f"baseline-{scenario_id.lower()}-r{repetition:02d}-{timestamp}-{suffix}"
        )
        self.run_directory = self.output_root / self.run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.record_path = self.run_directory / "runner_record.json"
        self.raw_http_directory = self.run_directory / "raw" / "http"
        self.raw_model_directory = self.run_directory / "raw" / "model_calls"
        self.parsed_model_directory = self.run_directory / "parsed" / "model_calls"
        for directory in (
            self.raw_http_directory,
            self.raw_model_directory,
            self.parsed_model_directory,
        ):
            directory.mkdir(parents=True, exist_ok=False)

        self.scenario_document = load_json(SPEC_DIR / "scenarios.v4.json")
        self.scenario = next(
            row
            for row in self.scenario_document["scenarios"]
            if row["scenario_id"] == scenario_id
        )
        self.review_completion_document = load_json(
            SPEC_DIR / "review_completion.v2.json"
        )
        self.review_completion_action = self.review_completion_document[
            "actions"
        ].get(scenario_id)
        self.adapter_identity = baseline_adapter_identity()
        self._http_index = 0
        self._model_call_index = 0
        self._session_id: int | None = None
        self._session_code: str | None = None
        self._participant_id: str | None = None
        self._temporary_reviewer_username: str | None = None
        transport_name = getattr(transport, "transport_name", "openai_responses_api")
        self.record: dict[str, Any] = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "condition": BASELINE_CONDITION,
            "scenario_id": scenario_id,
            "repetition": repetition,
            "run_type": run_type,
            "formal_run": run_type == RUN_TYPE_FORMAL,
            "execution_status": "created",
            "run_status": "created",
            "validation_status": "not_run",
            "started_at": timezone.now().isoformat(),
            "completed_at": None,
            "code_identity": None,
            "runtime_identity": None,
            "comparison_freeze": None,
            "formal_reviewer": None,
            "adapter_identity": self.adapter_identity,
            "protocol_snapshot": None,
            "protocol_snapshot_sha256": None,
            "scenario_sha256": None,
            "scenario_instance_sha256": sha256_json(self.scenario),
            "metric_rules_sha256": None,
            "review_completion_policy_sha256": None,
            "frozen_oracle": self.scenario,
            "scenario_steps": [],
            "harness_events": [],
            "raw_model_calls": [],
            "final_database_snapshot": None,
            "database_cleanup": None,
            "error": None,
            "comparison_boundaries": {
                "langgraph_routing_provenance": "not_applicable",
                "graph_approved_follow_up_wording": "not_applicable",
                "semantic_repair": False,
                "post_hoc_action_correction": False,
            },
            "notes": [
                "Execution completion is not a scenario PASS.",
                "The separate validator has not been run.",
                "Prompt-only output is persisted without oracle-based correction.",
                (
                    "This run uses a frozen fixture mock and is not formal evidence."
                    if transport_name == "frozen_fixture_mock"
                    else "This run is configured for the live Responses API transport."
                ),
            ],
        }
        self._flush()

    def _flush(self) -> None:
        _atomic_write_json(self.record_path, self.record)

    def _set_execution_status(self, status: str) -> None:
        self.record["execution_status"] = status
        self.record["run_status"] = status

    def run(self) -> dict[str, Any]:
        try:
            self._preflight()
            self._set_execution_status("running")
            self._flush()
            if self.run_type == RUN_TYPE_DRY:
                with transaction.atomic():
                    self._execute_compatible_workflow()
                    transaction.set_rollback(True)
                self.record["database_cleanup"] = self._dry_run_cleanup_record()
            else:
                self._execute_compatible_workflow()
                self.record["database_cleanup"] = {
                    "mode": "persisted_formal_run",
                    "session_retained": True,
                }
            self._set_execution_status("completed")
            self.record["completed_at"] = timezone.now().isoformat()
            self._flush()
            return self.record
        except Exception as error:
            if self.record["execution_status"] in {"created", "preflight"}:
                self._set_execution_status("preflight_error")
            else:
                self._set_execution_status("execution_error")
            self.record["completed_at"] = timezone.now().isoformat()
            self.record["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            self._flush()
            raise

    def _preflight(self) -> None:
        self._set_execution_status("preflight")
        contracts = validate_all()
        freeze_manifest = load_json(FREEZE_MANIFEST_PATH)
        formal_comparison = freeze_manifest["formal_comparison"]
        tracked_changes = _git(
            ["status", "--porcelain", "--untracked-files=no"], self.repository_root
        ).splitlines()
        transport_name = getattr(self.transport, "transport_name", "openai_responses_api")
        self.record["code_identity"] = {
            "commit": _git(["rev-parse", "HEAD"], self.repository_root),
            "tree": _git(["rev-parse", "HEAD^{tree}"], self.repository_root),
            "branch": _git(["branch", "--show-current"], self.repository_root),
            "tracked_worktree_clean": not tracked_changes,
            "tracked_changes": tracked_changes,
        }
        self.record["runtime_identity"] = {
            "python": platform.python_version(),
            "django": django.get_version(),
            "platform": platform.platform(),
            "model": get_baseline_model(),
            "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
            "runner_module": "evaluation.baseline_runner",
            "adapter_module": "evaluation.baseline_adapter",
            "model_transport": transport_name,
            "mock_transport": transport_name == "frozen_fixture_mock",
            "model_parameters_by_call_type": {
                BASELINE_CALL_KIND: self.adapter_identity["model_parameters"],
                "graph_approved_follow_up_wording": "not_applicable",
            },
        }
        self.record["comparison_freeze"] = {
            "contract_version": freeze_manifest["contract_version"],
            "path": str(FREEZE_MANIFEST_PATH.relative_to(self.repository_root)),
            "sha256": sha256_file(FREEZE_MANIFEST_PATH),
            "model_id": formal_comparison["model_id"],
            "eligibility_source": formal_comparison["eligibility_source"],
        }
        self.record["scenario_sha256"] = sha256_file(SPEC_DIR / "scenarios.v4.json")
        self.record["metric_rules_sha256"] = sha256_file(SPEC_DIR / "metrics.v3.json")
        self.record["review_completion_policy_sha256"] = sha256_file(
            SPEC_DIR / "review_completion.v2.json"
        )

        reference = self.scenario_document["protocol_reference"]
        protocols = list(
            Protocol.objects.filter(title=reference["title"], version=reference["version"])
        )
        if len(protocols) != 1 or protocols[0].status != Protocol.Status.LOCKED:
            raise BaselinePreflightError(
                "Exactly one locked Protocol must match the frozen title and version."
            )
        protocol = protocols[0]
        snapshot = build_protocol_snapshot_record(protocol)
        self.record["protocol_snapshot"] = snapshot["snapshot"]
        self.record["protocol_snapshot_sha256"] = snapshot["sha256"]
        self.record["protocol_database_id"] = protocol.id

        if self.transport is not None and self.run_type != RUN_TYPE_DRY:
            raise BaselinePreflightError(
                "Injected or mock transports are prohibited in formal runs; formal "
                "baseline evidence must instantiate the live Responses API directly."
            )
        if self.transport is None and not os.getenv("OPENAI_API_KEY"):
            raise BaselinePreflightError(
                "Live prompt-only runs require OPENAI_API_KEY; use the explicit mock only for dry-run infrastructure tests."
            )
        if self.run_type == RUN_TYPE_FORMAL:
            self._validate_formal_requirements(formal_comparison)
            if tracked_changes:
                raise BaselinePreflightError(
                    "Formal runs require a clean tracked worktree and frozen commit."
                )
            reviewer = get_user_model().objects.filter(
                username=self.reviewer_username, is_active=True
            )
            if not self.reviewer_username or not reviewer.exists():
                raise BaselinePreflightError(
                    "Formal runs require --reviewer naming an existing researcher."
                )
            reviewer_record = reviewer.values("id", "username", "is_active").get()
            self.record["formal_reviewer"] = {
                "user_id": reviewer_record["id"],
                "username": reviewer_record["username"],
                "is_active": reviewer_record["is_active"],
            }
        self.record["contract_validation"] = contracts
        self._flush()

    def _validate_formal_requirements(
        self, formal_comparison: dict[str, Any]
    ) -> None:
        if get_baseline_model() != formal_comparison["model_id"]:
            raise BaselinePreflightError(
                "The configured baseline model differs from the frozen comparison model."
            )

    def _execute_compatible_workflow(self) -> None:
        protocol = Protocol.objects.get(pk=self.record["protocol_database_id"])
        reviewer = self._prepare_reviewer()
        researcher_client = Client(HTTP_HOST="localhost")
        researcher_client.force_login(reviewer)
        participant_client = Client(HTTP_HOST="localhost")
        suffix = self.run_id[-8:].upper()
        self._participant_id = f"EB-{self.scenario_id}-R{self.repetition:02d}-{suffix[:5]}"
        self._session_code = f"EVB-{self.scenario_id}-R{self.repetition:02d}-{suffix}"
        self._create_stakeholder_through_http(researcher_client, protocol)
        session = self._create_session_through_http(researcher_client, protocol)
        self._session_id = session.id
        adapter = PromptOnlyBaselineAdapter(transport=self.transport)

        for step in self.scenario["steps"]:
            if step["kind"] == "consent":
                self._execute_consent(participant_client, session, step)
            elif step["kind"] == "post_stop_probe":
                self._execute_post_stop_probe(participant_client, session, step)
            else:
                self._execute_adapter_step(adapter, session, step)

        completed_path = reverse("interview_completed", args=[session.access_token])
        response = participant_client.get(completed_path)
        self._require_status(response, {200}, "participant completed page")
        self._record_harness_http(
            "participant_completed_page", response, "GET", completed_path, {}
        )

        output_path = reverse("output_detail", args=[session.session_code])
        response = researcher_client.get(output_path)
        self._require_status(response, {200}, "researcher Output Review page")
        self._record_harness_http(
            "researcher_output_review_page", response, "GET", output_path, {}
        )

        researcher_action = self.scenario.get("researcher_action")
        if self.review_completion_action:
            for item_action in self.review_completion_action.get(
                "pre_focal_item_actions", []
            ):
                self._execute_completion_item_action(
                    researcher_client, session, item_action
                )
        if researcher_action:
            self._execute_researcher_action(
                researcher_client, session, researcher_action
            )
        if self.review_completion_action:
            for item_action in self.review_completion_action.get(
                "post_focal_item_actions", []
            ):
                self._execute_completion_item_action(
                    researcher_client, session, item_action
                )
            self._execute_overall_review_action(
                researcher_client, session, self.review_completion_action
            )

        evidence_path = reverse("export_evidence_record")
        response = researcher_client.get(
            evidence_path, {"session": session.session_code}
        )
        self._require_status(response, {200}, "Evidence Record")
        self._record_harness_http(
            "evidence_record_render",
            response,
            "GET",
            evidence_path,
            {"session": session.session_code},
        )
        self.record["final_database_snapshot"] = self._database_snapshot(session.id)
        self._flush()

    def _execute_completion_item_action(
        self,
        client: Client,
        session: InterviewSession,
        item_action: dict[str, Any],
    ) -> None:
        item = StructuredDigestItem.objects.get(
            session=session, section_index=item_action["section_index"]
        )
        request_action = "approve" if item_action["action"] == "include" else item_action[
            "action"
        ]
        request_data = {
            "digest_action": request_action,
            "reviewer_comment": item_action.get("reason", ""),
        }
        path = reverse("review_digest_item", args=[session.session_code, item.id])
        before = self._database_snapshot(session.id)
        response = client.post(path, request_data)
        self._require_status(
            response,
            {302},
            f"review-completion {item_action['action']} section {item_action['section_index']}",
        )
        http = self._record_http_response(
            f"{self.scenario_id}-review-completion-{item_action['section_index']}",
            response,
            "POST",
            path,
            request_data,
        )
        after = self._database_snapshot(session.id)
        self.record["harness_events"].append(
            {
                "event": "review_completion_item_action",
                "frozen_input": item_action,
                "observed": {
                    "session_before": before["session"],
                    "session_after": after["session"],
                    "created_records": self._database_delta(before, after),
                },
                "http": http,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _prepare_reviewer(self):
        user_model = get_user_model()
        if self.reviewer_username:
            reviewer = user_model.objects.filter(
                username=self.reviewer_username, is_active=True
            ).first()
            if reviewer:
                return reviewer
        username = f"baseline_reviewer_{self.run_id[-8:]}"
        self._temporary_reviewer_username = username
        return user_model.objects.create_user(
            username=username, first_name="Evaluation", last_name="Reviewer"
        )

    def _create_stakeholder_through_http(self, client: Client, protocol: Protocol) -> None:
        path = reverse("stakeholder_create")
        request_data = {
            "participant_id": self._participant_id,
            "stakeholder_group": "Evaluation participant",
            "role": "Interview participant",
            "assigned_protocol": str(protocol.id),
            "notes": f"{self.run_type} {self.run_id}",
        }
        response = client.post(path, request_data)
        self._require_status(response, {302}, "researcher stakeholder creation")
        self._record_harness_http(
            "researcher_create_stakeholder", response, "POST", path, request_data
        )

    def _create_session_through_http(
        self, client: Client, protocol: Protocol
    ) -> InterviewSession:
        stakeholder = Stakeholder.objects.get(participant_id=self._participant_id)
        path = reverse("interview_session_create")
        request_data = {
            "session_code": self._session_code,
            "stakeholder": str(stakeholder.id),
            "protocol": str(protocol.id),
        }
        response = client.post(path, request_data)
        self._require_status(response, {302}, "researcher Session creation")
        self._record_harness_http(
            "researcher_create_session", response, "POST", path, request_data
        )
        return InterviewSession.objects.select_related(
            "stakeholder", "protocol"
        ).get(session_code=self._session_code)

    def _execute_consent(
        self, client: Client, session: InterviewSession, step: dict[str, Any]
    ) -> None:
        before = self._database_snapshot(session.id)
        path = reverse("interview_consent", args=[session.access_token])
        request_data = {"consent_confirmed": "yes"}
        response = client.post(path, request_data)
        self._require_status(response, {302}, step["step_id"])
        http = self._record_http_response(
            step["step_id"], response, "POST", path, request_data
        )
        after = self._database_snapshot(session.id)
        self._append_step(step, before, after, [], http=http)

    def _execute_post_stop_probe(
        self, client: Client, session: InterviewSession, step: dict[str, Any]
    ) -> None:
        session.refresh_from_db()
        if session.status != InterviewSession.Status.STOPPED:
            raise BaselineExecutionError(
                "The frozen post-Stop probe is permitted only after the prompt-only "
                "selection actually placed the Session in stopped state."
            )
        before = self._database_snapshot(session.id)
        path = reverse("interview_session", args=[session.access_token])
        request_data = {"action": "send", "reply": step["participant_text"]}
        response = client.post(path, request_data)
        self._require_status(response, {302}, step["step_id"])
        http = self._record_http_response(
            step["step_id"], response, "POST", path, request_data
        )
        after = self._database_snapshot(session.id)
        self._append_step(step, before, after, [], http=http)

    def _execute_adapter_step(
        self,
        adapter: PromptOnlyBaselineAdapter,
        session: InterviewSession,
        step: dict[str, Any],
    ) -> None:
        session.refresh_from_db()
        if session.status in {
            InterviewSession.Status.STOPPED,
            InterviewSession.Status.COMPLETED,
        }:
            raise BaselineExecutionError(
                "Prompt-only adapter refused to collect a turn after Session termination."
            )
        self._require_fixture_section(session, step)
        before = self._database_snapshot(session.id)
        section = session.current_section() or {}
        if step["kind"] == "participant_turn":
            source_message = Message.objects.create(
                session=session,
                sender=Message.Sender.PARTICIPANT,
                content=step["participant_text"],
                section=str(section.get("label") or ""),
                section_index=session.current_section_index,
            )
        else:
            control = step["control"].title()
            source_message = Message.objects.create(
                session=session,
                sender=Message.Sender.SYSTEM,
                content=f"Participant used the {control} control.",
                section=str(section.get("label") or ""),
                section_index=session.current_section_index,
            )

        probe_count_before = session.agent_decisions.filter(
            section_index=session.current_section_index,
            action=AgentDecision.Action.ASK_FOLLOW_UP,
        ).count()
        context = self._turn_context(
            session, step, source_message, probe_count_before
        )
        call_meta, parsed = self._execute_and_record_model_call(
            adapter, step["step_id"], context
        )
        self._persist_prompt_selection(
            session, source_message, parsed, probe_count_before
        )
        after = self._database_snapshot(session.id)
        delta = self._database_delta(before, after)
        new_decisions = [
            row
            for row in after["agent_decisions"]
            if row["id"] in delta["agent_decision_ids"]
        ]
        if len(new_decisions) != 1:
            raise BaselineExecutionError(
                f"{step['step_id']} produced {len(new_decisions)} decisions; expected one raw prompt selection."
            )
        self._append_step(
            step,
            before,
            after,
            new_decisions,
            adapter={
                "execution_channel": "prompt_only_adapter",
                "call_id": call_meta["call_id"],
                "call_status": call_meta["status"],
                "raw_request_path": call_meta["raw_request_path"],
                "raw_response_path": call_meta["raw_response_path"],
                "parsed_output_path": call_meta["parsed_output_path"],
                "prompt_sha256": self.adapter_identity["prompt_sha256"],
                "output_schema_sha256": self.adapter_identity[
                    "output_schema_sha256"
                ],
            },
        )

    def _turn_context(
        self,
        session: InterviewSession,
        step: dict[str, Any],
        source_message: Message,
        probe_count_before: int,
    ) -> dict[str, Any]:
        sections = session.protocol.sections or []
        current = session.current_section() or {}
        next_section = (
            sections[session.current_section_index + 1]
            if session.current_section_index + 1 < len(sections)
            else None
        )
        cumulative = list(
            session.messages.filter(
                sender=Message.Sender.PARTICIPANT,
                section_index=session.current_section_index,
            )
            .order_by("created_at", "id")
            .values_list("content", flat=True)
        )
        transcript = [
            {
                "sender": row.sender,
                "content": row.content,
                "section_index": row.section_index,
            }
            for row in session.messages.order_by("created_at", "id")
        ]
        return {
            "input_kind": step["kind"],
            "explicit_control": step.get("control"),
            "latest_participant_input": (
                step.get("participant_text") or source_message.content
            ),
            "cumulative_participant_responses_for_section": cumulative,
            "probe_count_before": probe_count_before,
            "follow_up_limit": 1,
            "current_section": current,
            "next_section": next_section,
            "locked_protocol": self.record["protocol_snapshot"],
            "protocol_rules": list(session.protocol.ethics_rules or []),
            "transcript_so_far": transcript,
        }

    def _execute_and_record_model_call(
        self,
        adapter: PromptOnlyBaselineAdapter,
        step_id: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._model_call_index += 1
        call_id = f"baseline-call-{self._model_call_index:03d}"
        request = adapter.build_request(context)
        request_path = self.raw_model_directory / f"{call_id}-request.json"
        _atomic_write_json(request_path, request)
        metadata = {
            "call_index": self._model_call_index,
            "call_id": call_id,
            "kind": BASELINE_CALL_KIND,
            "step_id": step_id,
            "transport": adapter.transport.transport_name,
            "started_at": timezone.now().isoformat(),
            "completed_at": None,
            "status": "started",
            "raw_request_path": str(request_path.relative_to(self.run_directory)),
            "raw_request_sha256": sha256_file(request_path),
            "raw_response_path": None,
            "raw_response_sha256": None,
            "parsed_output_path": None,
            "parsed_output_sha256": None,
            "error": None,
        }
        self.record["raw_model_calls"].append(metadata)
        self._flush()
        try:
            raw_call = adapter.call_request(request)
        except Exception as error:
            metadata.update(
                {
                    "completed_at": timezone.now().isoformat(),
                    "status": "transport_error",
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )
            self._flush()
            raise

        response_path = self.raw_model_directory / f"{call_id}-response.json"
        _atomic_write_json(response_path, raw_call["response"])
        metadata["raw_response_path"] = str(
            response_path.relative_to(self.run_directory)
        )
        metadata["raw_response_sha256"] = sha256_file(response_path)
        metadata["status"] = "raw_saved"
        self._flush()
        try:
            parsed = adapter.parse_response(raw_call)
        except Exception as error:
            metadata.update(
                {
                    "completed_at": timezone.now().isoformat(),
                    "status": "parse_error",
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )
            self._flush()
            raise

        parsed_path = self.parsed_model_directory / f"{call_id}-parsed.json"
        _atomic_write_json(parsed_path, parsed)
        metadata.update(
            {
                "completed_at": timezone.now().isoformat(),
                "status": "completed",
                "parsed_output_path": str(parsed_path.relative_to(self.run_directory)),
                "parsed_output_sha256": sha256_file(parsed_path),
            }
        )
        self._flush()
        return metadata, parsed

    def _persist_prompt_selection(
        self,
        session: InterviewSession,
        source_message: Message,
        parsed: dict[str, Any],
        probe_count_before: int,
    ) -> None:
        session.refresh_from_db()
        section = session.current_section() or {}
        action = parsed["selected_action"]
        AgentDecision.objects.create(
            session=session,
            message=source_message,
            section=str(section.get("label") or ""),
            section_index=session.current_section_index,
            coverage_assessment=parsed["coverage_assessment"],
            participant_control=parsed["participant_control"],
            action=action,
            probe_count_before=probe_count_before,
            covered_information=parsed["covered_information"],
            missing_information=parsed["missing_information"],
            decision_reason=parsed["reason"],
        )
        response = parsed["participant_response"]
        if action in {
            AgentDecision.Action.ASK_FOLLOW_UP,
            AgentDecision.Action.BOUNDARY_RESPONSE,
        }:
            self._create_agent_message(session, response, section)
            return
        if action == AgentDecision.Action.STOP:
            self._create_agent_message(session, response, section)
            session.status = InterviewSession.Status.STOPPED
            session.transcript_saved = True
            session.summary_generated = True
            session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
            session.output_quality_status = InterviewSession.OutputQualityStatus.WAITING
            session.completed_at = timezone.now()
            session.save()
            ensure_digest_items(session)
            return
        if action == AgentDecision.Action.COMPLETE:
            self._complete_interview(session, response)
            return
        if action in {
            AgentDecision.Action.MOVE_NEXT,
            AgentDecision.Action.FLAG_MISSING_AND_MOVE_NEXT,
            AgentDecision.Action.SKIP,
        }:
            self._advance_or_complete(session, response)
            return
        raise BaselineExecutionError(f"Unsupported parsed action {action!r}.")

    @staticmethod
    def _create_agent_message(
        session: InterviewSession, content: str, section: dict[str, Any]
    ) -> Message:
        return Message.objects.create(
            session=session,
            sender=Message.Sender.AGENT,
            content=content,
            section=str(section.get("label") or ""),
            section_index=session.current_section_index,
        )

    def _advance_or_complete(self, session: InterviewSession, response: str) -> None:
        sections = session.protocol.sections or []
        last_question_index = max(0, len(sections) - 2)
        if session.current_section_index >= last_question_index:
            self._complete_interview(session, response)
            return
        session.current_section_index += 1
        session.save()
        self._create_agent_message(session, response, session.current_section() or {})

    def _complete_interview(self, session: InterviewSession, response: str) -> None:
        sections = session.protocol.sections or []
        final_index = max(0, len(sections) - 1)
        final_section = sections[final_index] if sections else {}
        session.current_section_index = final_index
        session.status = InterviewSession.Status.COMPLETED
        session.transcript_saved = True
        session.summary_generated = True
        session.review_status = InterviewSession.ReviewStatus.NEEDS_REVIEW
        session.output_quality_status = InterviewSession.OutputQualityStatus.WAITING
        session.completed_at = timezone.now()
        session.save()
        self._create_agent_message(session, response, final_section)
        ensure_digest_items(session)
        session.stakeholder.status = Stakeholder.Status.COMPLETED
        session.stakeholder.save()

    def _execute_researcher_action(
        self,
        client: Client,
        session: InterviewSession,
        researcher_action: dict[str, Any],
    ) -> None:
        item = StructuredDigestItem.objects.get(
            session=session, section_index=researcher_action["section_index"]
        )
        action = researcher_action["action"]
        request_data = {
            "digest_action": action,
            "reviewer_comment": researcher_action.get("reason", ""),
        }
        if action == "edit":
            request_data["reviewed_text"] = researcher_action["reviewed_text"]
        path = reverse("review_digest_item", args=[session.session_code, item.id])
        before = self._database_snapshot(session.id)
        response = client.post(path, request_data)
        self._require_status(response, {302}, f"researcher {action}")
        http = self._record_http_response(
            f"{self.scenario_id}-researcher-{action}",
            response,
            "POST",
            path,
            request_data,
        )
        after = self._database_snapshot(session.id)
        self.record["harness_events"].append(
            {
                "event": "researcher_action",
                "action": action,
                "section_index": researcher_action["section_index"],
                "frozen_input": {
                    key: researcher_action[key]
                    for key in ("reviewed_text", "reason")
                    if key in researcher_action
                },
                "expected": researcher_action["oracle"],
                "observed": {
                    "session_before": before["session"],
                    "session_after": after["session"],
                    "created_records": self._database_delta(before, after),
                },
                "http": http,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _execute_overall_review_action(
        self,
        client: Client,
        session: InterviewSession,
        review_action: dict[str, Any],
    ) -> None:
        request_data = {
            "decision": review_action["decision"],
            "researcher_note": review_action["researcher_note"],
        }
        if review_action["participant_meaning_preserved"]:
            request_data["participant_meaning_preserved"] = "on"
        if review_action["protocol_boundaries_respected"]:
            request_data["protocol_boundaries_respected"] = "on"
        path = reverse("output_detail", args=[session.session_code])
        before = self._database_snapshot(session.id)
        response = client.post(path, request_data)
        self._require_status(response, {302}, "researcher overall review completion")
        http = self._record_http_response(
            f"{self.scenario_id}-overall-review",
            response,
            "POST",
            path,
            request_data,
        )
        after = self._database_snapshot(session.id)
        self.record["harness_events"].append(
            {
                "event": "overall_review_action",
                "frozen_input": {
                    key: review_action[key]
                    for key in (
                        "decision",
                        "researcher_note",
                        "participant_meaning_preserved",
                        "protocol_boundaries_respected",
                    )
                },
                "expected": review_action["oracle"],
                "observed": {
                    "session_before": before["session"],
                    "session_after": after["session"],
                    "review_decision_before": before["review_decision"],
                    "review_decision_after": after["review_decision"],
                    "created_records": self._database_delta(before, after),
                },
                "http": http,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _append_step(
        self,
        step: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        decisions: list[dict[str, Any]],
        *,
        http: dict[str, Any] | None = None,
        adapter: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "step_id": step["step_id"],
            "kind": step["kind"],
            "frozen_input": {
                key: step[key]
                for key in (
                    "section_index",
                    "section_code",
                    "participant_text",
                    "control",
                )
                if key in step
            },
            "expected": step["oracle"],
            "observed": {
                "session_before": before["session"],
                "session_after": after["session"],
                "created_records": self._database_delta(before, after),
                "agent_decisions": decisions,
            },
            "recorded_at": timezone.now().isoformat(),
        }
        if http:
            row["http"] = http
        if adapter:
            row["adapter"] = adapter
        self.record["scenario_steps"].append(row)
        self._flush()

    def _require_fixture_section(
        self, session: InterviewSession, step: dict[str, Any]
    ) -> None:
        current = session.current_section() or {}
        observed = (session.current_section_index, str(current.get("code") or ""))
        expected = (step["section_index"], step["section_code"])
        if observed != expected:
            raise BaselineExecutionError(
                f"{step['step_id']} reached section {observed}, not frozen target {expected}."
            )

    @staticmethod
    def _require_status(response: Any, allowed: set[int], label: str) -> None:
        if response.status_code not in allowed:
            raise BaselineExecutionError(
                f"{label} returned HTTP {response.status_code}; expected {sorted(allowed)}."
            )

    def _record_harness_http(
        self,
        event: str,
        response: Any,
        method: str,
        path: str,
        request_data: dict[str, Any],
    ) -> None:
        self.record["harness_events"].append(
            {
                "event": event,
                "http": self._record_http_response(
                    event, response, method, path, request_data
                ),
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _record_http_response(
        self,
        label: str,
        response: Any,
        method: str,
        path: str,
        request_data: dict[str, Any],
    ) -> dict[str, Any]:
        self._http_index += 1
        content = bytes(response.content)
        content_type = response.headers.get("Content-Type", "")
        extension = ".html" if "html" in content_type.lower() else ".bin"
        body_path = self.raw_http_directory / (
            f"{self._http_index:03d}-{_slug(label)}{extension}"
        )
        body_path.write_bytes(content)
        return {
            "request": {
                "method": method,
                "path": path,
                "data": _jsonable(request_data),
                "client_role": (
                    "participant" if path.startswith("/interview/") else "researcher"
                ),
            },
            "response": {
                "status_code": response.status_code,
                "location": response.headers.get("Location"),
                "content_type": content_type,
                "content_bytes": len(content),
                "content_sha256": _sha256_bytes(content),
                "raw_body_path": str(body_path.relative_to(self.run_directory)),
            },
        }

    def _database_snapshot(self, session_id: int) -> dict[str, Any]:
        session = InterviewSession.objects.select_related(
            "stakeholder", "protocol"
        ).get(pk=session_id)
        messages = list(session.messages.order_by("created_at", "id"))
        decisions = list(
            session.agent_decisions.select_related("message").order_by(
                "created_at", "id"
            )
        )
        items = list(
            session.digest_items.prefetch_related(
                "source_messages", "review_events"
            ).order_by("section_index", "id")
        )
        review_decision = ReviewDecision.objects.filter(session=session).first()
        section_codes = {
            int(section.get("index", position)): str(section.get("code") or "")
            for position, section in enumerate(session.protocol.sections or [])
        }
        return {
            "session": {
                "id": session.id,
                "session_code": session.session_code,
                "participant_id": session.stakeholder.participant_id,
                "protocol_id": session.protocol_id,
                "protocol_status": session.protocol.status,
                "status": session.status,
                "current_section_index": session.current_section_index,
                "consent_confirmed": session.consent_confirmed,
                "consent_confirmed_at": _iso(session.consent_confirmed_at),
                "consent_notice_version": session.consent_notice_version,
                "consent_snapshot": session.consent_snapshot,
                "consent_snapshot_sha256": session.consent_snapshot_sha256,
                "consent_snapshot_is_valid": session.consent_snapshot_is_valid,
                "transcript_saved": session.transcript_saved,
                "summary_generated": session.summary_generated,
                "review_status": session.review_status,
                "output_quality_status": session.output_quality_status,
                "started_at": _iso(session.started_at),
                "completed_at": _iso(session.completed_at),
            },
            "messages": [
                {
                    "id": row.id,
                    "sender": row.sender,
                    "content": row.content,
                    "section": row.section,
                    "section_index": row.section_index,
                    "created_at": _iso(row.created_at),
                }
                for row in messages
            ],
            "agent_decisions": [
                {
                    "id": row.id,
                    "run_id": self.run_id,
                    "session_id": session.id,
                    "message_id": row.message_id,
                    "source_message_id": row.message_id,
                    "section": row.section,
                    "section_index": row.section_index,
                    "section_code": section_codes.get(row.section_index, ""),
                    "coverage_assessment": row.coverage_assessment,
                    "participant_control": row.participant_control,
                    "action": row.action,
                    "selected_action": row.action,
                    "probe_count_before": row.probe_count_before,
                    "covered_information": list(row.covered_information or []),
                    "missing_information": list(row.missing_information or []),
                    "decision_reason": row.decision_reason,
                    "reason": row.decision_reason,
                    "created_at": _iso(row.created_at),
                }
                for row in decisions
            ],
            "digest_items": [
                {
                    "id": item.id,
                    "session_id": session.id,
                    "section_index": item.section_index,
                    "section_code": item.section_code,
                    "coverage_status": item.coverage_status,
                    "participant_control": item.participant_control,
                    "topic_reached": item.topic_reached,
                    "missing_information": list(item.missing_information or []),
                    "generated_text": item.generated_text,
                    "final_text": item.final_text,
                    "evidence_text": item.evidence_text,
                    "is_evidence_candidate": item.is_evidence_candidate,
                    "is_included": item.is_included,
                    "display_visible": True,
                    "export_visible": True,
                    "review_status": item.review_status,
                    "reviewed_text": item.reviewed_text,
                    "reviewer_comment": item.reviewer_comment,
                    "reviewer_name_snapshot": item.reviewer_name_snapshot,
                    "reviewed_at": _iso(item.reviewed_at),
                    "source_message_ids": list(
                        item.source_messages.order_by("created_at", "id").values_list(
                            "id", flat=True
                        )
                    ),
                }
                for item in items
            ],
            "review_events": [
                {
                    "id": event.id,
                    "digest_item_id": event.digest_item_id,
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "previous_text": event.previous_text,
                    "new_text": event.new_text,
                    "comment": event.comment,
                    "reviewer_name_snapshot": event.reviewer_name_snapshot,
                    "created_at": _iso(event.created_at),
                }
                for event in DigestReviewEvent.objects.filter(
                    digest_item__session=session
                ).order_by("created_at", "id")
            ],
            "review_decision": (
                {
                    "id": review_decision.id,
                    "decision": review_decision.decision,
                    "reviewer_note": review_decision.reviewer_note,
                    "source_links_checked": review_decision.source_links_checked,
                    "participant_controls_respected": (
                        review_decision.participant_controls_respected
                    ),
                    "limitations_and_missing_information_visible": (
                        review_decision.limitations_and_missing_information_visible
                    ),
                    "participant_meaning_preserved": (
                        review_decision.participant_meaning_preserved
                    ),
                    "protocol_boundaries_respected": (
                        review_decision.protocol_boundaries_respected
                    ),
                    "reviewer_name_snapshot": review_decision.reviewer_name_snapshot,
                    "reviewed_at": _iso(review_decision.reviewed_at),
                }
                if review_decision
                else None
            ),
        }

    @staticmethod
    def _database_delta(
        before: dict[str, Any], after: dict[str, Any]
    ) -> dict[str, list[int]]:
        def created_ids(key: str) -> list[int]:
            before_ids = {row["id"] for row in before[key]}
            return [row["id"] for row in after[key] if row["id"] not in before_ids]

        return {
            "message_ids": created_ids("messages"),
            "agent_decision_ids": created_ids("agent_decisions"),
            "digest_item_ids": created_ids("digest_items"),
            "review_event_ids": created_ids("review_events"),
        }

    def _dry_run_cleanup_record(self) -> dict[str, Any]:
        session_exists = bool(
            self._session_code
            and InterviewSession.objects.filter(session_code=self._session_code).exists()
        )
        stakeholder_exists = bool(
            self._participant_id
            and Stakeholder.objects.filter(participant_id=self._participant_id).exists()
        )
        reviewer_exists = bool(
            self._temporary_reviewer_username
            and get_user_model().objects.filter(
                username=self._temporary_reviewer_username
            ).exists()
        )
        if session_exists or stakeholder_exists or reviewer_exists:
            raise BaselineRunnerError(
                "Dry-run transaction did not roll back all baseline QA records."
            )
        return {
            "mode": "transaction_rolled_back",
            "session_retained": False,
            "stakeholder_retained": False,
            "temporary_reviewer_retained": False,
        }


__all__ = [
    "BaselineExecutionError",
    "BaselinePreflightError",
    "BaselineRunnerError",
    "FrozenFixtureBaselineTransport",
    "PromptOnlyBaselineRunner",
    "RUN_TYPE_DRY",
    "RUN_TYPE_FORMAL",
]

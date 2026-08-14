"""Execute one frozen MVP scenario through the real Django application path.

This module is deliberately an execution harness, not a validator or metric
calculator.  It records the pre-registered oracle next to observations but
does not decide whether they match.  Formal PASS/FAIL judgements belong to the
separate evaluation validator that is implemented later.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import django
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from interviews import langgraph_agent
from interviews.langgraph_agent import get_openai_model
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

from .schemas import (
    MVP_CONDITION,
    SCENARIO_IDS,
    load_json,
    sha256_file,
    sha256_json,
)
from .execution_plan import CURRENT_FREEZE_MANIFEST_PATH
from .validate_specs import SPEC_DIR, validate_all


RUNNER_SCHEMA_VERSION = "1.0"
RUN_TYPE_DRY = "dry_run"
RUN_TYPE_FORMAL = "formal"
RUN_TYPES = (RUN_TYPE_DRY, RUN_TYPE_FORMAL)
FREEZE_MANIFEST_PATH = CURRENT_FREEZE_MANIFEST_PATH


class RunnerError(RuntimeError):
    """Base error for an evaluation-runner failure."""


class RunnerPreflightError(RunnerError):
    """Raised before a Session is created when a frozen gate is not met."""


class ApplicationPathError(RunnerError):
    """Raised when the real HTTP/application path cannot execute a step."""


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _jsonable(value: Any) -> Any:
    """Convert SDK/Django values without exposing client constructor secrets."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Path)):
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
        raise RunnerPreflightError(f"Cannot record Git identity: {error}") from error
    return result.stdout.strip()


class _ResponsesProxy:
    """Pass-through Responses API proxy that records requests and raw outputs."""

    def __init__(
        self,
        responses: Any,
        calls: list[dict[str, Any]],
        on_change: Callable[[], None],
    ):
        self._responses = responses
        self._calls = calls
        self._on_change = on_change

    def __getattr__(self, name: str) -> Any:
        return getattr(self._responses, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        text_format = kwargs.get("text") or {}
        format_name = (
            text_format.get("format", {}).get("name")
            if isinstance(text_format, dict)
            else None
        )
        call = {
            "call_index": len(self._calls) + 1,
            "kind": (
                "protocol_coverage_check"
                if format_name == "purrstone_protocol_coverage_check"
                else "follow_up_wording"
            ),
            "started_at": timezone.now().isoformat(),
            "request": {
                "args": _jsonable(args),
                "kwargs": _jsonable(kwargs),
            },
            "status": "started",
        }
        self._calls.append(call)
        self._on_change()
        try:
            response = self._responses.create(*args, **kwargs)
        except Exception as error:
            call.update(
                {
                    "completed_at": timezone.now().isoformat(),
                    "status": "error",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )
            self._on_change()
            raise

        call.update(
            {
                "completed_at": timezone.now().isoformat(),
                "status": "completed",
                "response": {
                    "output_text": getattr(response, "output_text", None),
                    "raw": _jsonable(response),
                },
            }
        )
        self._on_change()
        return response


class _OpenAIClientProxy:
    def __init__(
        self,
        client: Any,
        calls: list[dict[str, Any]],
        on_change: Callable[[], None],
    ):
        self._client = client
        self.responses = _ResponsesProxy(client.responses, calls, on_change)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@contextmanager
def capture_openai_responses(
    calls: list[dict[str, Any]],
    on_change: Callable[[], None],
):
    """Capture Responses API I/O without changing prompts, routing, or outputs."""

    original_openai = langgraph_agent.OpenAI

    def recording_openai(*args: Any, **kwargs: Any) -> _OpenAIClientProxy:
        client = original_openai(*args, **kwargs)
        return _OpenAIClientProxy(client, calls, on_change)

    langgraph_agent.OpenAI = recording_openai
    try:
        yield
    finally:
        langgraph_agent.OpenAI = original_openai


class MvpScenarioRunner:
    """Run one S1-S8 fixture through public/researcher HTTP views."""

    def __init__(
        self,
        *,
        scenario_id: str,
        repetition: int,
        run_type: str,
        output_root: Path,
        reviewer_username: str | None = None,
        repository_root: Path | None = None,
    ):
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
        self.repository_root = (
            Path(repository_root).resolve()
            if repository_root
            else Path(__file__).resolve().parents[1]
        )

        timestamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        suffix = uuid.uuid4().hex[:8]
        self.run_id = (
            f"mvp-{scenario_id.lower()}-r{repetition:02d}-{timestamp}-{suffix}"
        )
        self.run_directory = self.output_root / self.run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.record_path = self.run_directory / "runner_record.json"
        self.raw_http_directory = self.run_directory / "raw" / "http"
        self.raw_http_directory.mkdir(parents=True, exist_ok=False)

        self.scenario_document = load_json(SPEC_DIR / "scenarios.v4.json")
        self.scenario = next(
            scenario
            for scenario in self.scenario_document["scenarios"]
            if scenario["scenario_id"] == scenario_id
        )
        self.review_completion_document = load_json(
            SPEC_DIR / "review_completion.v2.json"
        )
        self.review_completion_action = self.review_completion_document[
            "actions"
        ].get(scenario_id)
        self._http_index = 0
        self._session_id: int | None = None
        self._session_code: str | None = None
        self._participant_id: str | None = None
        self._temporary_reviewer_username: str | None = None
        self.record: dict[str, Any] = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "condition": MVP_CONDITION,
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
            "notes": [
                "Execution completion is not a scenario PASS.",
                "The separate validator has not been run.",
            ],
        }
        self._flush()

    def _flush(self) -> None:
        _atomic_write_json(self.record_path, self.record)

    def _set_execution_status(self, status: str) -> None:
        """Keep the runner detail and shared run-metadata field identical."""

        self.record["execution_status"] = status
        self.record["run_status"] = status

    def run(self) -> dict[str, Any]:
        """Execute and retain one attempt; dry runs always roll back DB writes."""

        try:
            self._preflight()
            self._set_execution_status("running")
            self._flush()

            if self.run_type == RUN_TYPE_DRY:
                with transaction.atomic():
                    self._execute_application_path()
                    transaction.set_rollback(True)
                self.record["database_cleanup"] = self._dry_run_cleanup_record()
            else:
                self._execute_application_path()
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
        contract_result = validate_all()
        if contract_result.get("status") != "PASS":
            raise RunnerPreflightError("Frozen evaluation contracts did not validate.")
        freeze_manifest = load_json(FREEZE_MANIFEST_PATH)
        formal_comparison = freeze_manifest["formal_comparison"]

        tracked_changes = _git(
            ["status", "--porcelain", "--untracked-files=no"],
            self.repository_root,
        ).splitlines()
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
            "model": get_openai_model(),
            "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
            "model_transport": "openai_responses_api",
            "mock_transport": False,
            "llm_follow_up_wording_disabled": os.getenv(
                "DISABLE_LLM_FOLLOWUP_WORDING", ""
            ).lower()
            in {"1", "true", "yes"},
            "runner_module": "evaluation.mvp_runner",
            "model_parameters_by_call_type": formal_comparison[
                "model_parameters_by_condition"
            ][MVP_CONDITION],
        }
        self.record["comparison_freeze"] = {
            "contract_version": freeze_manifest["contract_version"],
            "path": str(FREEZE_MANIFEST_PATH.relative_to(self.repository_root)),
            "sha256": sha256_file(FREEZE_MANIFEST_PATH),
            "model_id": formal_comparison["model_id"],
            "eligibility_source": formal_comparison["eligibility_source"],
        }
        self.record["scenario_sha256"] = sha256_file(
            SPEC_DIR / "scenarios.v4.json"
        )
        self.record["metric_rules_sha256"] = sha256_file(
            SPEC_DIR / "metrics.v3.json"
        )
        self.record["review_completion_policy_sha256"] = sha256_file(
            SPEC_DIR / "review_completion.v2.json"
        )

        protocol_reference = self.scenario_document["protocol_reference"]
        protocols = list(
            Protocol.objects.filter(
                title=protocol_reference["title"],
                version=protocol_reference["version"],
            )
        )
        if len(protocols) != 1:
            raise RunnerPreflightError(
                "Exactly one Protocol must match the frozen title and version."
            )
        protocol = protocols[0]
        if protocol.status != Protocol.Status.LOCKED:
            raise RunnerPreflightError(
                "The evaluation Protocol must already be locked before a run."
            )
        snapshot_record = build_protocol_snapshot_record(protocol)
        self.record["protocol_snapshot"] = snapshot_record["snapshot"]
        self.record["protocol_snapshot_sha256"] = snapshot_record["sha256"]
        self.record["protocol_database_id"] = protocol.id

        if self.run_type == RUN_TYPE_FORMAL:
            self._validate_formal_requirements(formal_comparison)
            if tracked_changes:
                raise RunnerPreflightError(
                    "Formal runs require a clean tracked worktree and frozen commit."
                )
            if not self.reviewer_username:
                raise RunnerPreflightError(
                    "Formal runs require --reviewer naming an existing researcher."
                )
            reviewer = get_user_model().objects.filter(
                username=self.reviewer_username,
                is_active=True,
            )
            if not reviewer.exists():
                raise RunnerPreflightError(
                    "The named formal-run reviewer does not exist or is inactive."
                )
            reviewer_record = reviewer.values("id", "username", "is_active").get()
            self.record["formal_reviewer"] = {
                "user_id": reviewer_record["id"],
                "username": reviewer_record["username"],
                "is_active": reviewer_record["is_active"],
            }

        self.record["contract_validation"] = contract_result
        self._flush()

    def _validate_formal_requirements(
        self, formal_comparison: dict[str, Any]
    ) -> None:
        if get_openai_model() != formal_comparison["model_id"]:
            raise RunnerPreflightError(
                "The configured MVP model differs from the frozen comparison model."
            )
        if not os.getenv("OPENAI_API_KEY"):
            raise RunnerPreflightError(
                "Formal MVP runs require OPENAI_API_KEY in the current environment."
            )
        if self.record["runtime_identity"]["llm_follow_up_wording_disabled"]:
            raise RunnerPreflightError(
                "Formal MVP runs cannot disable constrained LLM follow-up wording."
            )

    def _execute_application_path(self) -> None:
        protocol = Protocol.objects.get(pk=self.record["protocol_database_id"])
        reviewer = self._prepare_reviewer()

        researcher_client = Client(HTTP_HOST="localhost")
        researcher_client.force_login(reviewer)
        participant_client = Client(HTTP_HOST="localhost")

        suffix = self.run_id[-8:].upper()
        self._participant_id = f"EV-{self.scenario_id}-R{self.repetition:02d}-{suffix[:5]}"
        self._session_code = f"EVM-{self.scenario_id}-R{self.repetition:02d}-{suffix}"

        self._create_stakeholder_through_http(
            researcher_client,
            protocol,
        )
        session = self._create_session_through_http(
            researcher_client,
            protocol,
        )
        self._session_id = session.id

        with capture_openai_responses(
            self.record["raw_model_calls"],
            self._flush,
        ):
            for step in self.scenario["steps"]:
                self._execute_scenario_step(participant_client, session, step)

        session.refresh_from_db()
        completed_response = participant_client.get(
            reverse("interview_completed", args=[session.access_token])
        )
        self._require_status(completed_response, {200}, "participant completed page")
        self._record_harness_http(
            "participant_completed_page",
            completed_response,
            method="GET",
            path=reverse("interview_completed", args=[session.access_token]),
            request_data={},
        )

        output_response = researcher_client.get(
            reverse("output_detail", args=[session.session_code])
        )
        self._require_status(output_response, {200}, "researcher Output Review page")
        self._record_harness_http(
            "researcher_output_review_page",
            output_response,
            method="GET",
            path=reverse("output_detail", args=[session.session_code]),
            request_data={},
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
                researcher_client,
                session,
                researcher_action,
            )
        if self.review_completion_action:
            for item_action in self.review_completion_action.get(
                "post_focal_item_actions", []
            ):
                self._execute_completion_item_action(
                    researcher_client, session, item_action
                )
            self._execute_overall_review_action(
                researcher_client,
                session,
                self.review_completion_action,
            )

        evidence_path = reverse("export_evidence_record")
        evidence_response = researcher_client.get(
            evidence_path,
            {"session": session.session_code},
        )
        self._require_status(evidence_response, {200}, "Evidence Record")
        self._record_harness_http(
            "evidence_record_render",
            evidence_response,
            method="GET",
            path=evidence_path,
            request_data={"session": session.session_code},
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
            method="POST",
            path=path,
            request_data=request_data,
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
                username=self.reviewer_username,
                is_active=True,
            ).first()
            if reviewer:
                return reviewer
            if self.run_type == RUN_TYPE_FORMAL:
                raise RunnerPreflightError("Formal reviewer is unavailable.")

        username = f"eval_reviewer_{self.run_id[-8:]}"
        self._temporary_reviewer_username = username
        return user_model.objects.create_user(
            username=username,
            first_name="Evaluation",
            last_name="Reviewer",
        )

    def _create_stakeholder_through_http(
        self,
        client: Client,
        protocol: Protocol,
    ) -> None:
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
            "researcher_create_stakeholder",
            response,
            method="POST",
            path=path,
            request_data=request_data,
        )
        if not Stakeholder.objects.filter(participant_id=self._participant_id).exists():
            raise ApplicationPathError(
                "Stakeholder POST redirected but did not create the record."
            )

    def _create_session_through_http(
        self,
        client: Client,
        protocol: Protocol,
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
            "researcher_create_session",
            response,
            method="POST",
            path=path,
            request_data=request_data,
        )
        try:
            return InterviewSession.objects.select_related(
                "stakeholder", "protocol"
            ).get(session_code=self._session_code)
        except InterviewSession.DoesNotExist as error:
            raise ApplicationPathError(
                "Session POST redirected but did not create the Session."
            ) from error

    def _execute_scenario_step(
        self,
        client: Client,
        session: InterviewSession,
        step: dict[str, Any],
    ) -> None:
        session.refresh_from_db()
        before = self._database_snapshot(session.id)

        if step["kind"] == "consent":
            path = reverse("interview_consent", args=[session.access_token])
            request_data = {"consent_confirmed": "yes"}
        else:
            self._require_fixture_section(session, step)
            path = reverse("interview_session", args=[session.access_token])
            if step["kind"] == "participant_turn":
                request_data = {
                    "action": "send",
                    "reply": step["participant_text"],
                }
            elif step["kind"] == "participant_control":
                request_data = {"action": step["control"]}
            elif step["kind"] == "post_stop_probe":
                request_data = {
                    "action": "send",
                    "reply": step["participant_text"],
                }
            else:
                raise ApplicationPathError(
                    f"Unsupported scenario step kind {step['kind']}."
                )

        response = client.post(path, request_data)
        self._require_status(response, {302}, step["step_id"])
        http_record = self._record_http_response(
            step["step_id"],
            response,
            method="POST",
            path=path,
            request_data=request_data,
        )
        session.refresh_from_db()
        after = self._database_snapshot(session.id)
        delta = self._database_delta(before, after)
        new_decisions = [
            decision
            for decision in after["agent_decisions"]
            if decision["id"] in delta["agent_decision_ids"]
        ]
        if step["kind"] in {"participant_turn", "participant_control"} and len(
            new_decisions
        ) != 1:
            raise ApplicationPathError(
                f"{step['step_id']} produced {len(new_decisions)} decisions; expected one inspectable control record."
            )

        self.record["scenario_steps"].append(
            {
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
                    "created_records": delta,
                    "agent_decisions": new_decisions,
                },
                "http": http_record,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _execute_researcher_action(
        self,
        client: Client,
        session: InterviewSession,
        researcher_action: dict[str, Any],
    ) -> None:
        item = StructuredDigestItem.objects.get(
            session=session,
            section_index=researcher_action["section_index"],
        )
        action = researcher_action["action"]
        request_data = {
            "digest_action": action,
            "reviewer_comment": researcher_action.get("reason", ""),
        }
        if action == "edit":
            request_data["reviewed_text"] = researcher_action["reviewed_text"]
        path = reverse(
            "review_digest_item",
            args=[session.session_code, item.id],
        )
        before = self._database_snapshot(session.id)
        response = client.post(path, request_data)
        self._require_status(response, {302}, f"researcher {action}")
        http_record = self._record_http_response(
            f"{self.scenario_id}-researcher-{action}",
            response,
            method="POST",
            path=path,
            request_data=request_data,
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
                "http": http_record,
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
        http_record = self._record_http_response(
            f"{self.scenario_id}-overall-review",
            response,
            method="POST",
            path=path,
            request_data=request_data,
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
                "http": http_record,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _require_fixture_section(
        self,
        session: InterviewSession,
        step: dict[str, Any],
    ) -> None:
        current = session.current_section() or {}
        observed_index = session.current_section_index
        observed_code = str(current.get("code") or "")
        if (
            observed_index != step["section_index"]
            or observed_code != step["section_code"]
        ):
            raise ApplicationPathError(
                f"{step['step_id']} reached section {observed_index}/{observed_code!r}, "
                f"not frozen input target {step['section_index']}/{step['section_code']!r}."
            )

    @staticmethod
    def _require_status(response: Any, allowed: set[int], label: str) -> None:
        if response.status_code not in allowed:
            raise ApplicationPathError(
                f"{label} returned HTTP {response.status_code}; expected {sorted(allowed)}."
            )

    def _record_harness_http(
        self,
        event: str,
        response: Any,
        *,
        method: str,
        path: str,
        request_data: dict[str, Any],
    ) -> None:
        self.record["harness_events"].append(
            {
                "event": event,
                "http": self._record_http_response(
                    event,
                    response,
                    method=method,
                    path=path,
                    request_data=request_data,
                ),
                "recorded_at": timezone.now().isoformat(),
            }
        )
        self._flush()

    def _record_http_response(
        self,
        label: str,
        response: Any,
        *,
        method: str,
        path: str,
        request_data: dict[str, Any],
    ) -> dict[str, Any]:
        self._http_index += 1
        content = bytes(response.content)
        content_type = response.headers.get("Content-Type", "")
        extension = ".html" if "html" in content_type.lower() else ".bin"
        filename = f"{self._http_index:03d}-{_slug(label)}{extension}"
        body_path = self.raw_http_directory / filename
        body_path.write_bytes(content)
        relative_body_path = body_path.relative_to(self.run_directory)
        return {
            "request": {
                "method": method,
                "path": path,
                "data": _jsonable(request_data),
                "client_role": (
                    "participant"
                    if path.startswith("/interview/")
                    else "researcher"
                ),
            },
            "response": {
                "status_code": response.status_code,
                "location": response.headers.get("Location"),
                "content_type": content_type,
                "content_bytes": len(content),
                "content_sha256": _sha256_bytes(content),
                "raw_body_path": str(relative_body_path),
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
                    "id": message.id,
                    "sender": message.sender,
                    "content": message.content,
                    "section": message.section,
                    "section_index": message.section_index,
                    "created_at": _iso(message.created_at),
                }
                for message in messages
            ],
            "agent_decisions": [
                {
                    "id": decision.id,
                    "run_id": self.run_id,
                    "session_id": session.id,
                    "message_id": decision.message_id,
                    "source_message_id": decision.message_id,
                    "section": decision.section,
                    "section_index": decision.section_index,
                    "section_code": section_codes.get(decision.section_index, ""),
                    "coverage_assessment": decision.coverage_assessment,
                    "participant_control": decision.participant_control,
                    "action": decision.action,
                    "selected_action": decision.action,
                    "probe_count_before": decision.probe_count_before,
                    "covered_information": list(
                        decision.covered_information or []
                    ),
                    "missing_information": list(
                        decision.missing_information or []
                    ),
                    "decision_reason": decision.decision_reason,
                    "reason": decision.decision_reason,
                    "created_at": _iso(decision.created_at),
                }
                for decision in decisions
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
                    "reviewer_name_snapshot": (
                        review_decision.reviewer_name_snapshot
                    ),
                    "reviewed_at": _iso(review_decision.reviewed_at),
                }
                if review_decision
                else None
            ),
        }

    @staticmethod
    def _database_delta(
        before: dict[str, Any],
        after: dict[str, Any],
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
            and InterviewSession.objects.filter(
                session_code=self._session_code
            ).exists()
        )
        stakeholder_exists = bool(
            self._participant_id
            and Stakeholder.objects.filter(
                participant_id=self._participant_id
            ).exists()
        )
        reviewer_exists = bool(
            self._temporary_reviewer_username
            and get_user_model().objects.filter(
                username=self._temporary_reviewer_username
            ).exists()
        )
        if session_exists or stakeholder_exists or reviewer_exists:
            raise RunnerError("Dry-run transaction did not roll back all QA records.")
        return {
            "mode": "transaction_rolled_back",
            "session_retained": False,
            "stakeholder_retained": False,
            "temporary_reviewer_retained": False,
        }


__all__ = [
    "ApplicationPathError",
    "MvpScenarioRunner",
    "RUN_TYPE_DRY",
    "RUN_TYPE_FORMAL",
    "RunnerError",
    "RunnerPreflightError",
    "capture_openai_responses",
]

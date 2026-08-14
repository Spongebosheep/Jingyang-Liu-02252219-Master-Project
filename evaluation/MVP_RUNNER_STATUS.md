# Phase 5 / MVP scenario runner checkpoint

Date: 2026-08-06

## Result

The MVP runner is implemented as an evaluation harness around the frozen
Django/LangGraph workflow. It creates Stakeholder and Session records through
authenticated researcher POST routes, executes Consent and all participant
steps through the public UUID route, executes S7/S8 review actions through the
authenticated review route, and retains raw HTTP plus parsed database state.

Execution completion is not treated as scenario PASS. The separate validator,
unified exporter, metric calculator and prompt-only baseline were implemented
in later checkpoints; formal paired results are not contained here.

## What was added

- `evaluation/mvp_runner.py`: one-attempt execution harness, unique run
  directories, protocol/code/runtime identity, transaction-isolated dry runs,
  formal-run preflight gates, raw Responses API capture, HTTP body capture, and
  per-step Session/Message/AgentDecision/digest/review-event snapshots.
- `interviews/management/commands/run_mvp_scenarios.py`: thin command wrapper
  for one, several, or all frozen scenarios; it continues after a failed
  attempt and reports the retained record path.
- `evaluation/django_tests/test_mvp_runner.py`: positive, negative, S1-S8,
  rollback, review-action, command, formal-evidence, and raw-model-capture tests.
- `evaluation/.gitignore`: keeps generated run attempts out of source control.

## Necessary R2 correction found by the runner

Before the correction, `contains_any(text, STOP_TERMS)` treated the substring
`stop` anywhere in participant text as an explicit Stop request. Frozen normal
answers such as "the carriage stopped between stations" and "left at the next
stop" therefore terminated the Session before the explicit S5 Stop control.

The minimum correction replaces that substring check with explicit
stop-intent patterns. The public Stop button still submits `stop`, and clear
typed requests such as "I want to stop" still terminate collection. Incidental
narrative uses no longer do so. A participant-route regression test covers
both narrative phrases and confirms MOVE_NEXT rather than STOP.

This is a necessary R2/action-consistency repair discovered before any formal
run. No scenario oracle or metric definition was changed.

## Teacher-requirement mapping

- Real application path: participant and researcher actions are submitted to
  the existing Django views; the runner never creates Message, AgentDecision,
  digest, source, or review-event records directly.
- Step-level evidence: every step records the HTTP request/response, Session
  state before/after, and IDs plus fields of naturally created DB records.
- Locked protocol: the runner fails unless exactly one matching v1 Protocol is
  already locked, then stores its full snapshot and SHA-256.
- Expected/observed separation: the frozen oracle is copied next to observed
  fields; the runner never writes match, score, PASS, or FAIL.
- Failure retention: the run directory is created before preflight, written
  atomically after each event, and never reused; command execution continues to
  retain later attempts.
- Raw/parsed separation: HTTP bodies live under `raw/http`; raw model I/O is
  stored separately from parsed decision/database fields.
- Live/formal boundary: formal runs fail closed without a configured API key,
  v8-frozen matching model, clean code, attributable reviewer and directly
  captured raw-model evidence.

## Non-formal verification

- S1-S8 infrastructure dry run with controlled model transport: execution
  completed for all eight scenarios and every QA DB record rolled back.
- S1-S8 real-product fallback dry run with no API key: execution completed for
  all eight scenarios, all 56 frozen steps and 98 HTTP bodies were retained,
  all eight shared metadata records were complete, and every QA DB record
  rolled back.
- The fallback dry run is only an application-path diagnostic. It is not live
  OpenAI evidence and was not passed to a scenario validator.

## Regression verification

- Existing product plus R2 regression tests: 81 passed.
- Frozen evaluation-contract tests: 16 passed.
- MVP runner tests: 6 passed.
- Total: 103 passed.
- Django system check: no issues.
- Migration drift: no changes detected.
- Dependency check: no broken requirements.
- Database SHA-256 before and after dry runs:
  `a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc`.
- Formal evaluation runs: zero.
- Live OpenAI calls in the fallback dry run: zero.

## Still deliberately incomplete

- Independent observed-vs-oracle validator.
- Prompt-only baseline runner and frozen prompt hash.
- Unified JSON/CSV/Evidence Record exporter.
- Nine-metric calculator.
- Screenshot capture for formal evidence packages.
- Formal S1-S8 matched sessions, UCR/MP coding, and blind A/B review.

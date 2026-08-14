# v10.1 Baseline reliability correction status

Date: 2026-08-09

## Finding corrected

The preserved v9 formal-attempt record contains sixteen prompt-only Baseline
attempts: one completed and fifteen terminated when the selected action left
the Session on a different section from the next frozen common participant
input. The v10 construct/Consent candidate changed field semantics but retained
that Baseline path risk.

## v10.1 implementation

- `prompt_only_baseline.v3.txt` makes the locked section's
  `assessment_guidance` the binding Protocol-coverage threshold and prevents
  `required_information` from becoming an implicit every-item checklist.
- The prompt contains threshold interpretations but no scenario ID, participant
  fixture, oracle, or expected action.
- Cross-field inconsistent JSON is saved raw and rejected as a parse error; it
  is not repaired.
- The runner's section check remains fail-closed. A real wrong action is not
  overridden merely to complete the transcript.
- `execution_plan.py` exposes the exact 16 pairs and 32 paired attempts.
- `run_baseline_development_gate` requires 16/16 live dry-run Baseline paths and
  independent validator PASS before final freeze. Gate results are never formal
  evidence.
- `run_formal_evaluation_plan` freezes its attempt list before the first call,
  uses a paired S1-R01 pilot, and never creates replacement attempts.
- `quality_review.v2.json` includes only registered, unique, completed,
  independently validated, formally eligible pairs and retains every exclusion
  in `pair_selection_report.json`.

## Current evidence boundary

Offline tests can verify orchestration, failure retention, schema consistency,
hash validation and mock paths. They cannot demonstrate that the live model
will complete 16/16 Baseline paths. Until the live development command reports
16/16 PASS, v10.1 must remain a pre-formal candidate and no formal 32-attempt
plan may start.

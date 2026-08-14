# Phase 5 / Independent observed-vs-oracle validator

Date: 2026-08-06

## Result

The independent validator is implemented and tested. It reads a saved runner
record and its raw HTTP bodies, compares observations with the pre-registered
scenario oracle, and writes a new append-only validator result. It does not
execute the product, call OpenAI, edit database state, repair observations,
calculate aggregate metrics, or perform UCR/MP manual coding.

## Responsibilities and separation

- `mvp_runner.py`: executes frozen inputs and records raw/parsed observations;
  it still makes no PASS/FAIL judgement.
- `run_validator.py`: judges one saved run against the frozen automatic oracle.
- `validate_evaluation_runs`: thin command for one record or a run directory.
- exporter and metric calculator: remain deliberately unimplemented.

Every validator invocation writes a unique
`validation/<validator_run_id>/validator_result.json`. The original
`runner_record.json` remains byte-identical. Failed and incomplete judgements
are retained rather than overwritten.

## Pre-run evidence-completeness correction

Before state:

- `scenarios.v2.json` required S5 post-Stop POSTs to create no records.
- The runner stopped the Session and inspected final state but did not issue a
  post-Stop POST, so that oracle field could not be directly validated.

Change:

- Retained v1 and v2 unchanged.
- Added `scenarios.v3.json` with one fixed `S5-P01` post-Stop rejection probe.
- Added `freeze_manifest.v3.json` with the new scenario hash and the explicit
  pre-run revision reason.
- The runner now records the real participant POST plus zero new Message and
  AgentDecision IDs.

Test and evidence impact:

- Contract validation rejects a probe that expects any new records.
- S5 validator checks the actual POST method, zero new messages, zero new
  decisions, and retained `stopped` Session state.
- No formal run or live OpenAI call preceded this correction, so it is not a
  post-hoc response to model results.

## R3 blocking source-grounding fix

Before state:

- S6 correctly generated a `boundary_response`, but the participant's medical
  instruction request was still included among Topic 1 digest sources.
- This contradicted the frozen `boundary_turn_not_used_as_evidence=true`
  outcome and the same-Session/same-topic evidence boundary.

Change:

- `ensure_digest_items` now excludes participant messages whose linked
  AgentDecision is `boundary_response` or `safety_boundary`.
- The later valid participant answer remains the Topic 1 source.
- No routing, UI, Protocol, model prompt, review action, or database schema was
  changed.

Test and evidence impact:

- A new product regression test proves the boundary request is absent from
  source links and generated draft text while the valid answer remains.
- S6 validator explicitly proves that the boundary message ID does not appear
  in any Topic 1 source relation.
- Existing cumulative-source behaviour in S2 and ordinary source grounding
  remain covered by the full regression suite.

## Automatic checks

The validator reports explicit expected, observed, status, reason, and relevant
metric codes for:

- run/commit/tree/Protocol/contract identity;
- raw HTTP body path, byte-count, and SHA-256 integrity;
- exact frozen input and step order;
- Consent, action, answer status, probe count, missing information, and topic;
- Skip/Stop transitions and prohibited later decisions;
- S5 post-Stop zero-record rejection;
- S6 boundary statement, no hidden progression, and no boundary-source use;
- topic cardinality and partial/skipped/stopped/not-reached visibility;
- same-Session participant same-topic source relations;
- S7 Edit and S8 Exclude audit fields and Evidence Record consequence.

Negative tests prove that missing sources, absent cross-Session sources, agent
sources, cross-topic sources, tampered actions, and fake formal flags produce
FAIL without repair.

## Verification

- Frozen-contract tests: 19/19 PASS.
- Runner plus validator integration tests: 14/14 PASS.
- Full product/evaluation suite: 115/115 PASS.
- Real application-path fallback dry run: S1-S8 executed 8/8.
- Independent automatic checks on those dry runs: 773/773 PASS.
- Django system check: PASS.
- Migration drift: none.
- Dependency check: no broken requirements.
- Database SHA-256 before/after:
  `a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc`.
- Formal runs: 0.
- Live OpenAI calls: 0.

## Claim boundary and remaining work

The 8/8 dry-run validator result verifies infrastructure and deterministic
fallback compatibility only; each result records
`formal_evidence_eligible=false`. It is not formal S1-S8 evidence and does not
establish outcome quality, natural-use reliability, production readiness,
UCR, MP, or comparative superiority.

The matched prompt-only runner, exporter and calculator were implemented in
later checkpoints. Formal paired runs still require directly recorded live
model evidence under v8; manual UCR/MP coding remains separate.

# Prompt-only baseline implementation status

> Historical checkpoint: this file documents the original v1 Baseline. The
> current pre-formal implementation is prompt v3 under
> `freeze_manifest.v10_1.json`; see `V10_1_BASELINE_RELIABILITY_STATUS.md`.

Date: 2026-08-06

## Scope and evidence boundary

This checkpoint implements the matched prompt-only comparator required by the
referral evaluation plan. It does not contain a formal OpenAI result, paired
MVP-versus-baseline comparison, metric summary, screenshot package, UCR or
Meaning Preservation coding, blinded A/B judgement, or research conclusion.

The implementation was checked against all three current controlling sources:

- the three-page teacher consultation PDF ending in `(5).pdf` (SHA-256
  `ecf9d54a34b945c14b9b91697d0d5b2664848796e32263aa023a6f4db6d0cf25`);
- `代码修改建议(6).docx`;
- the module assessment criteria, especially benchmarking, validation against
  success criteria, auditable project data, and reproducible communication.

## Implemented

- Versioned prompt-only control policy:
  `prompts/prompt_only_baseline.v1.txt`.
- Strict JSON response schema:
  `specs/prompt_only_output_schema.v1.json`.
- Lightweight Responses API adapter that returns, without repair:
  answer status, selected action, covered information, missing information,
  short audit reason and participant-facing response.
- Condition runner covering frozen S1-S8 with the same locked Protocol,
  scenario inputs, transcript/data models, structured-evidence layer,
  researcher review views and Evidence Record view used by the MVP condition.
- Separate raw request, raw response and parsed-output files for every baseline
  model call, each with SHA-256 and unique call identity.
- Append-only failure retention for transport and parse failures.
- Condition-aware independent validator support for baseline adapter steps and
  raw/parsed file integrity without changing the shared oracle or metrics.
- Explicit N/A records for LangGraph routing provenance and graph-approved
  follow-up wording in the prompt-only condition.
- Fail-closed Stop handling: a wrongly selected Stop action is retained as a
  failure and cannot trigger a product route or collect a later probe.
- `freeze_manifest.v4.json`, preserving v1-v3 history and adding only the
  prompt/schema hashes before formal runs.

## Fair-comparison boundary

Matched across both conditions:

- model identifier;
- locked Protocol snapshot and hash;
- scenario ID, repetition and participant inputs/order;
- one-follow-up limit stated to the comparator;
- shared control-record fields;
- structured extract, source relation and topic coverage representation;
- researcher Edit/Exclude layer and Evidence Record rendering.

Deliberately different:

- MVP: the model assesses semantic coverage and words a graph-approved
  follow-up; LangGraph selects and enforces the bounded action.
- Baseline: one versioned prompt directly selects the status, action and
  participant-facing response; a lightweight adapter only parses and
  mechanically applies that output.

Prohibited and tested:

- no import or call of the product LangGraph, product decision functions,
  release-condition decision logic or MVP runner;
- no oracle in the live prompt context;
- no semantic repair, post-hoc action correction or replacement of parse
  failures;
- no injected or mock transport in a formal run;
- no API key stored in run artefacts.

## Verification at this checkpoint

```text
Full Django test suite:                 126/126 PASS
Frozen-contract positive/negative tests: 21/21 PASS
Django system check:                    PASS
Migration drift:                        none
Dependency check:                       PASS
Database SHA-256 before/after:           a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc

MVP fallback dry-run execution:          8/8 completed
MVP independent validation:              8/8 PASS, formal_evidence_eligible=false
Baseline mock dry-run execution:         8/8 completed
Baseline independent validation:         8/8 PASS, formal_evidence_eligible=false

Formal paired Sessions:                 0
Live OpenAI calls in this step:          0
Formal screenshots:                      0
Comparative result tables:               0
Human UCR/MP or blinded A/B decisions:    0
```

Frozen baseline assets:

```text
Prompt SHA-256:
cb917c128eda34e8723f0f9a2030fbe9a661a724e457a08e76e9865b0227aa76

Output schema SHA-256:
f5a3552a8da48b8520649e406b2392fc42d66ecdd3e7b4815852e15234d7c130
```

## Subsequent implementation

The unified exporter, frozen metric calculator and v8 direct-evidence formal
rules were implemented in later checkpoints. Mock/fallback records still
cannot become formal evidence or substitute for UCR, Meaning Preservation,
formal screenshots or blinded A/B judgement.

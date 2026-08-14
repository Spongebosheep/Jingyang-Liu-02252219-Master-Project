# Unified evaluation exporter status

Date: 2026-08-06

## Scope and evidence boundary

This checkpoint implements the append-only, condition-neutral exporter required
by the referral evaluation plan. It reads saved runner and validator artefacts
only. It does not execute the product, call OpenAI, create a formal Session,
change the database, repair a model output, calculate a metric, perform human
coding, or generate an MVP-versus-baseline conclusion.

The implementation was checked against the three controlling sources at the
start of the step:

- teacher consultation PDF `(5).pdf`, SHA-256
  `ecf9d54a34b945c14b9b91697d0d5b2664848796e32263aa023a6f4db6d0cf25`;
- `代码修改建议(6).docx`, SHA-256
  `3b041c6f9753bfdfb6399f3e9047f52222148c13c5db4e54d44c4e7b8a607f63`;
- module assessment criteria, SHA-256
  `842870fbbc8599025c9ef0f31a47c8becc42acfc5f669af7c3c0d95c12435f0d`.

## Implemented

- `evaluation/exporter.py`: one exporter for both `mvp` and
  `prompt_only_baseline` saved attempts.
- `export_evaluation_bundle` Django management command with repeated
  `--record` or recursive `--input-root` selection.
- New append-only package on every invocation; no existing package is
  overwritten.
- Byte-for-byte copies, with SHA-256, of:
  - `runner_record.json`;
  - separate raw and parsed model-call directories;
  - raw HTTP bodies;
  - all discovered validator results.
- Frozen, condition-neutral tables:
  - `runs.csv`;
  - `turns.csv`;
  - `item_sources.csv`;
  - `reviews.csv`.
- One user-facing Evidence Record copy per run when the saved render exists;
  missing, unsafe or incomplete records remain explicitly marked and are never
  fabricated.
- Mechanical item/source relation columns expose whether a source is present in
  the same Session snapshot, is a participant message, and matches the topic.
  No SLC metric is calculated by the exporter.
- Validator freshness gate: a validator whose saved runner SHA-256 or run
  identity no longer matches is retained as `STALE` and cannot confer PASS or
  formal eligibility.
- Failed and incomplete attempts are exported with their raw files and error
  fields instead of being filtered out.
- Both runner snapshots now include the already-existing database field
  `review_decision.reviewer_note`, symmetrically across conditions. No model,
  migration, view, template or product routing logic changed.

## Pre-formal-run freeze

- `export_bundle_schema.v1.json` freezes all output filenames, row units,
  columns, integrity rules and claim boundaries before any formal run.
- `freeze_manifest.v5.json` adds only this exporter schema to the frozen lineage.
- Historical manifests v1-v4 continue to validate.
- S1-S8, scenario oracle, metric definitions, baseline fairness contract,
  baseline prompt and prompt-only response schema are unchanged.

## Verification

```text
Frozen-contract tests:                 23/23 PASS
Exporter integration tests:             5/5 PASS
Full Django test suite:               133/133 PASS
Django system check:                    PASS
Migration drift:                        none
Git whitespace check:                   PASS

MVP fallback dry runs:                   8/8 completed
Prompt-only mock dry runs:               8/8 completed
Independent validator results:         16/16 PASS
Formal-evidence-eligible dry runs:       0

Unified non-formal export:
  runs.csv rows:                         16
  turns.csv rows:                       114
  item_sources.csv rows:                 84
  reviews.csv rows:                      20
  Evidence Record copies:                16
  runner copy SHA-256 mismatches:         0
  baseline raw response files:           48
  baseline parsed output files:          48

Database SHA-256 before/after:
a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc

Formal paired Sessions:                  0
Live OpenAI calls in this step:           0
Metric summaries:                         0
Comparative result tables:                0
Formal screenshots:                       0
Human UCR/MP or blinded A/B decisions:    0
```

The attached checkout contains a Windows-built project environment. Linux
verification reused its Python packages with runtime-native equivalents for
compiled dependencies in a temporary test-only path; no package was installed,
upgraded or committed.

## Next permitted implementation step

The frozen metric calculator was implemented in the next checkpoint. Formal
execution now follows v8 direct run-evidence and matched-pair fairness rules;
UCR and MP remain separate human coding, and no automated superiority claim is
generated.

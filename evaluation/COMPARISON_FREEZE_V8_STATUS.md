# Comparison method and evidence freeze v8

Date: 2026-08-06

## Result

The formal MVP-versus-prompt-only comparison is frozen before any live
comparison result. Formal eligibility now comes from the evidence captured by
each run plus matched-pair fairness checks. No separate external gate artefact
is an input to a formal comparison run.

The change starts from source checkpoint `622961e`. Historical manifest v7 is
retained unchanged, and v8 records the new method before formal execution.

## Controlling-source check

The implementation was checked against:

- teacher consultation PDF `(5).pdf`, SHA-256
  `ecf9d54a34b945c14b9b91697d0d5b2664848796e32263aa023a6f4db6d0cf25`;
- `代码修改建议(6).docx`, SHA-256
  `3b041c6f9753bfdfb6399f3e9047f52222148c13c5db4e54d44c4e7b8a607f63`;
- module assessment criteria, SHA-256
  `842870fbbc8599025c9ef0f31a47c8becc42acfc5f669af7c3c0d95c12435f0d`.

The four available classroom-PDF filenames are byte-identical copies, and the
two available code-suggestion DOCX filenames are byte-identical copies. There
is no conflicting source version.

## Unchanged comparison content

v8 preserves byte-identical hashes for:

- S1-S8 participant inputs and oracle (`scenarios.v3.json`);
- all nine metrics and manual-coding rules (`metrics.v2.json`);
- prompt-only baseline fairness contract;
- prompt-only baseline prompt and strict output schema;
- export and metric-calculation schemas;
- S7/S8 review-completion contract.

v8 adds one new pre-result `quality_review.v1.json` contract. It freezes the
five classroom A/B questions, A/B/Tie preference, confidence, rationale, issue
flags, two independent coders, blinding exclusions and consensus retention. It
does not change or replace UCR, Meaning Preservation or any automatic metric.

The run plan also remains unchanged: minimum 16 Sessions / 8 matched pairs;
recommended 32 Sessions / 16 matched pairs, with three repetitions per
condition for S1, S2, S3 and S6.

## Formal per-run evidence

Every formal attempt must retain a unique run ID and directly record:

- clean commit and tree identity;
- frozen model ID and call parameters;
- Protocol, scenario, metric, review and v8 hashes;
- raw model requests/responses and parsed decisions;
- participant turns and full transcript;
- structured items, source relations, coverage and limitations;
- item and overall review records;
- Evidence Record and independent validator result;
- any execution, parsing, validation or metric failure.

Mock transport, fallback evidence, semantic repair and post-hoc action
correction remain ineligible.

## Matched-pair fairness

The exporter records one integrity row for each `scenario_id + repetition`
pair. A pair cannot receive formal eligibility unless it contains exactly one
MVP and one baseline run and both use the same:

- model ID;
- clean code commit;
- Protocol snapshot hash;
- scenario and metric hashes;
- scenario-instance hash and therefore the same participant inputs/order.

Both runs must also independently pass the validator. Mismatches are retained
as `FAIRNESS_FAIL`, not silently selected or repaired.

## Verification

```text
Full Django suite:                 153/153 PASS
Current frozen-contract tests:      34/34 PASS
Django system check:                    PASS
Migration drift:                        none
Dependency check:                       PASS
Git whitespace check:                   PASS

S1-S8 MVP dry-run attempts:               8/8 completed
S1-S8 baseline mock attempts:             8/8 completed
Independent validators:                 16/16 PASS
Matched-pair identity checks:              8/8 PASS
Fairness failures:                           0
Formal evidence eligible:                    0
Database SHA-256:
a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc
```

The rehearsal tests the v8 execution, validation, export and calculation
pipeline only. It contains no live comparison result and makes no comparative
claim.

## Final evidence package boundary

The automated bundle retains raw records, CSVs, validation, Evidence Records
and failures. Selected screenshots tied to run IDs, blinded A/B material, UCR
atomic-proposition coding, Meaning Preservation coding, coder identity and
disagreement/consensus records remain explicit post-run evidence tasks.

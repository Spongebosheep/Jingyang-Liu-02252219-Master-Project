# Comparison Freeze v9: completion and pre-test status

Date: 2026-08-06

## Outcome

The comparison and human-coding toolchain is complete at the pre-formal-run
checkpoint. No formal or live comparison result was generated while completing
or testing this work.

Current contracts:

- `specs/freeze_manifest.v9.json`
- `specs/review_completion.v2.json`
- `specs/quality_review.v1.json`
- unchanged `scenarios.v3.json`, `metrics.v2.json`, baseline prompt, output
  schemas and 16/32 run plan

## Source-to-code audit

The implementation was checked against the two supplied guidance sources.
Duplicate copies of each source were byte-identical:

| Guidance source | SHA-256 | Applied requirement |
|---|---|---|
| Teacher consultation/class record | `ecf9d54a34b945c14b9b91697d0d5b2664848796e32263aa023a6f4db6d0cf25` | S1-S8 paired comparison, complete S7/S8 researcher review, S8 Include then Exclude, blinded A/B, two independent coders, disagreement and consensus retention |
| Code modification suggestions | `3b041c6f9753bfdfb6399f3e9047f52222148c13c5db4e54d44c4e7b8a607f63` | same model/input fairness, raw and parsed evidence, validator/exporter/calculator separation, nine metrics, manual UCR/MP, final evidence traceability |

The audit found one pre-result mismatch: the historical completion script kept
only the focal S7 Edit or S8 Exclude and left other candidate items pending.
`review_completion.v2.json` corrects this through explicit authenticated HTTP
item actions. S8 now executes Include before Exclude and resolves every
remaining candidate before the fixed request-revision event. This correction
does not change participant inputs, the decision oracle, baseline prompt,
metric definitions or human scoring questions.

## Completed tooling

- real-path MVP and prompt-only paired runners;
- independent fail-closed run validator;
- unified append-only exporter and pair-identity checks;
- seven automatic metrics, with UCR and MP kept manual;
- randomly assigned condition-neutral A/B coder package;
- fixed quality, UCR and MP CSV coding sheets;
- locked byte-preserving coder submission importer;
- two-coder disagreement/consensus reports;
- deblinding gate that rejects early key use;
- separate completed UCR and MP summaries without a superiority claim;
- source-ZIP setup instructions that correctly require `migrate` and
  `seed_mvp` because `db.sqlite3` is excluded.

## Verification

Pre-checks:

```text
pip check: PASS
Django check: PASS
migration drift: none
frozen contracts: PASS (9 current contracts)
full tests: 162/162 PASS
```

Complete non-formal rehearsal:

```text
runner attempts: 16/16 completed
independent validators: 16/16 PASS
matched pairs: 8/8 PASS
fairness failures: 0
incomplete pairs: 0
formal evidence eligible: 0
S7/S8 RAC: 6/6 per run
quality coding rows: 8
UCR coding units: 18
MP coding units: 18
coder identity leak scan: PASS
private blinding-key mode: 600
```

The rehearsal used fallback/mock dry-run data only. It verifies the tooling and
must not be reported as study evidence.

Negative checks also pass: different pair identities cannot become formal
paired evidence; missing/reordered S8 completion actions fail validation;
changed coder metadata or unknown manual-coding units are rejected; early
deblinding is rejected; unresolved disagreements cannot produce a completed
manual metric summary.

## Formal test entry point

Use one clean commit for every paired formal run. On the target Windows system:

1. create and activate the virtual environment;
2. install requirements;
3. run `migrate`, `seed_mvp` and create the named researcher;
4. run the pre-checks and confirm a clean Git worktree;
5. configure the frozen model and API key locally;
6. run one formal S1 MVP/baseline pair;
7. validate both records, export the pair, calculate automatic metrics and
   generate the blinded coder package;
8. inspect that pilot evidence before continuing S2-S8 and later repetitions.

API keys must never be copied into source, logs, coder material or a support
message.

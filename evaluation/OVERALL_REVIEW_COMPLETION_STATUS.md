# S7/S8 overall-review completion checkpoint

Date: 2026-08-06

## Result

The pre-formal-run overall-review audit gap is closed. S7 and S8 now save a
completed, attributable overall request-revision event after their frozen focal
Edit or Exclude action. Both the MVP runner and prompt-only baseline runner use
the existing authenticated `output_detail` POST; neither writes a
`ReviewDecision` directly.

## Root cause

The product already had a complete overall review path. The evaluation runners
executed the S7/S8 item-level action and then rendered the Evidence Record, but
never submitted an overall decision. The Output Review GET had therefore
created a pending `ReviewDecision` with no reviewer, time or note. The exporter
correctly retained it, and the calculator correctly reported RAC `1/2 FAIL`.

## Frozen correction

- `specs/review_completion.v1.json` freezes one condition-neutral S7/S8 action.
- The action is `request_revision`, not approval, because only the focal item is
  resolved by these scenarios.
- The fixed note states that remaining topic evidence still requires review.
- `participant_meaning_preserved` and `protocol_boundaries_respected` remain
  false; no UCR, MP or human quality judgement is supplied by the harness.
- Non-focal evidence items remain pending and are not auto-included, edited or
  excluded.
- `specs/freeze_manifest.v7.json` records the new contract and lineage. S1-S8
  participant inputs/oracle, nine metrics, baseline prompt and schemas remain
  unchanged.

## Independent checks

The validator now requires:

- exactly one registered overall-review HTTP action for S7/S8 and none for
  S1-S6;
- the saved policy SHA-256;
- exact frozen input and oracle;
- `revision_requested`, the exact note, reviewer identity and timestamp;
- false human-judgement fields and no automatic resolution of remaining items.

The exporter and calculator remain separate. They were not modified to fill
missing fields or exclude a failing record.

## Non-formal chain rehearsal

S7 and S8 were rerun once in each condition:

```text
MVP fallback dry runs:       2/2 completed
Baseline mock dry runs:      2/2 completed
Independent validators:     4/4 PASS
Exported review rows:        8
Completed overall rows:      4/4
RAC per run:                 2/2 PASS
Formal evidence eligible:    0/4
Database SHA-256 change:     none
```

This rehearsal proves only that the corrected audit pipeline executes. It is
not formal paired evidence, outcome-quality validation or a comparative result.

## Regression verification

```text
Full Django test suite:       144/144 PASS
Frozen contract tests:         28/28 PASS
Focused evaluation tests:      62/62 PASS
Django system check:           PASS
Migration drift:               none
Dependency check:              PASS
Database SHA-256:
a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc
```

No product model, view, template, migration, LangGraph file or database record
was changed by this checkpoint.

## Formal comparison boundary

Formal paired runs follow the later v8 direct-evidence rules: the configured
model and call parameters must match the freeze, each run must retain raw live
calls and pass independent validation, and each pair must satisfy the recorded
fairness identities.

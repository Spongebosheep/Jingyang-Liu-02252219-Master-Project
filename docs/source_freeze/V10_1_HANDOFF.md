# PurrStone v10.1 pre-formal candidate handoff

Date: 2026-08-09

## Delivery status

This source tree is a reviewed **pre-formal candidate**. It contains the v10.1
Baseline reliability correction and the executable evaluation controls, but it
does not contain a passed live Baseline gate, formal comparison evidence, or
human coding results.

The preserved v9 evidence and v1-v10 contracts remain historical. They must not
be overwritten, relabelled, backfilled, or used as v10.1 attempts.

## Implemented controls

- Baseline prompt v3 applies each locked section's `assessment_guidance` as the
  binding Protocol-coverage threshold. It does not contain scenario fixtures,
  oracle actions, or expected outcomes.
- Cross-field inconsistent Baseline JSON is retained and rejected. The runner
  still fails on a real section/path mismatch and never changes the model's
  selected action.
- One shared plan defines exactly 16 matched pairs and 32 condition attempts.
- The live Baseline development gate must pass 16/16 dry-run paths and the
  independent validator before final freeze. Its records are never formal
  evidence.
- The formal orchestrator freezes the full plan before calling the model, runs
  a paired S1-R01 pilot, retains failures, and never creates replacements.
- Quality-review packaging includes only registered, unique, completed,
  independently validated and formally eligible pairs. Every exclusion is
  retained in `pair_selection_report.json`; fewer than eight eligible pairs
  blocks coder-material generation.
- A/B quality, UCR and Meaning Preservation remain blank human-coder workflows.

## Verification completed in this delivery environment

- `python manage.py test -v 1`: **186/186 PASS**.
- `python -m unittest discover -s evaluation/tests`: **55/55 PASS**.
- `python manage.py check`: PASS.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python -m evaluation.validate_specs`: PASS for all nine current contracts.
- `python -m pip check`: no broken requirements.
- Python compilation and `git diff --check`: PASS.

`OPENAI_API_KEY` was not configured in this environment. Consequently, no live
OpenAI call, MVP live gate, 16-attempt Baseline live gate, or formal attempt was
run here. This limitation is deliberate and must remain visible.

## Required Cloud continuation

Read `AGENTS.md` and `docs/V10_1_EXECUTION_CONTRACT.md`, then follow their gates
in order. In summary:

1. Install, migrate, seed, and repeat all offline verification.
2. Commit the reviewed source as clean candidate commit C1.
3. Run `python manage.py run_openai_e2e_check`.
4. Run `python manage.py run_baseline_development_gate`; require exactly 16/16
   PASS and confirm `formal_evidence_eligible=false`.
5. If both live gates pass without a source change, replace
   `PENDING_V10_1_REVIEW_COMMIT` with the full C1 hash, recalculate the
   controlled source-file count, regenerate `SOURCE_MANIFEST.sha256`, rerun
   verification, and commit exactly the two freeze-metadata files
   `evaluation/specs/freeze_manifest.v10_1.json` and `SOURCE_MANIFEST.sha256`
   as C2.
6. Stop. Formal execution requires a later explicit `APPROVE_FORMAL_32` from
   the user.

Do not infer comparative superiority from technical completion or automatic
metrics. Final qualitative and UCR/MP conclusions require two independent real
coders and retained consensus records.

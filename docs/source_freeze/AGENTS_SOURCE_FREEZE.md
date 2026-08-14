# PurrStone v10.2 execution contract

Read this file and `docs/V10_2_EXECUTION_CONTRACT.md` and `docs/V10_2_METHOD_AMENDMENT.md` before changing or
running the project. These instructions govern every Cloud/Codex task in this
repository unless the user explicitly narrows the task further.

## Current status

- This checkout is a **v10.2 pre-formal candidate**, not completed evidence.
- v1-v10.1 contracts and all v9 attempts are historical. Never delete, overwrite,
  relabel, backfill, or present them as v10.2 results.
- v10.2 changes methodology/orchestration only: the historical reported 13/16 Baseline development observation is not independently re-verifiable or formal evidence, and its unavailable directory must not be searched for, reconstructed, or rerun.
- `evaluation/specs/freeze_manifest.v10_2.json` is current. Its `local_baseline_commit` remains pending until the reviewed implementation is separately frozen.
- No v10/v10.1/v10.2 formal run has started.

## Non-negotiable boundaries

1. Do not import or reuse product LangGraph routing or product decision logic in
   the prompt-only Baseline.
2. Do not expose the scenario oracle, expected action, or expected result to a
   live model request.
3. Do not repair, reinterpret, replace, or silently advance after a model path
   mismatch. Preserve the raw output and failed attempt.
4. Do not run replacement attempts under an existing scenario/repetition and
   condition identity.
5. Do not use mock/fallback/development records as formal evidence.
6. Do not alter the frozen participant inputs, model (`gpt-4.1-mini`), call
   parameters, one-follow-up limit, UCR/MP manual boundary, or 16-pair plan.
7. Never store or print `OPENAI_API_KEY`. It must be supplied only as an
   environment variable.
8. Never impersonate a human coder. A/B quality, UCR and Meaning Preservation
   require two real independent coders and later consensus.

## Required pre-formal sequence

Run offline verification, review and commit the candidate, then separately replace `PENDING_V10_2_REVIEW_COMMIT` with that full reviewed commit and regenerate freeze metadata. The missing historical development directory is not a precondition. Do not run or reconstruct the historical Baseline development gate.

**Hard stop:** do not run formal attempts unless explicitly approved with `APPROVE_FORMAL_32` or equally unambiguous approval.

## Formal sequence after explicit approval

Run exactly once:

```text
python manage.py run_formal_evaluation_plan --reviewer <existing_username>
```

The command freezes the 32-attempt schedule before the first call. It executes
paired `S1-R01` first only for operational ordering, then attempts the remaining
30 identities even when an earlier condition fails. Every outcome is retained,
and the command never performs replacement reruns.

Only `COMPLETED_ALL_PASS` means 32/32 attempts independently validated and
formally eligible. Any other status is a retained result, not permission to
retry or repair.

## Evidence packaging

After the formal plan ends, use the existing independent tools in this order:

1. Export every attempted runner record and validator result from the one
   formal-plan root with `export_evaluation_bundle`.
2. Run `calculate_evaluation_metrics` on that completed export.
3. Run `generate_blinded_quality_review` on the same export.
4. Preserve `pair_selection_report.json`. Only complete, registered, unique,
   identity-matched, independently PASS and formally eligible pairs enter coder
   material.
5. Sixteen included pairs meets the recommendation. Eight to fifteen produces
   a below-recommended warning. Fewer than eight produces a blocked technical
   report and no coder material. Never backfill from v9 or replacement runs.
6. Package source, current contracts, all formal attempts (including failures),
   validators, export, automatic metrics, blank coder material/private key,
   logs, inventories and SHA-256 files. Keep coder material in a separate ZIP
   that excludes the private key and condition identity.
7. Stop before human coding. UCR and MP must remain `PENDING_MANUAL` until two
   real coders return independent files.

Do not claim comparative superiority merely because technical execution or
automatic metrics completed.

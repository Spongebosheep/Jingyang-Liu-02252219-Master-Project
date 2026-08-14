# Baseline S7/S8 supplementary diagnostic

Status: post-formal deterministic diagnostic, completed 2026-08-10.

## Purpose

Formal 32 retained the prompt-only Baseline S7 and S8 failures because upstream interview-control errors prevented those runs from reaching the researcher review stage. This supplementary diagnostic isolates the downstream question: when a contract-compliant Baseline fixture reaches a review-ready digest, can the shared authenticated review layer execute and record the prescribed Edit and Exclude actions?

## Method

- Condition: `prompt_only_baseline`.
- Scenarios: S7 Edit and S8 Exclude, one deterministic run each.
- Run type: `dry_run`.
- Transport: `FrozenFixtureBaselineTransport` / `frozen_fixture_mock`.
- Live OpenAI calls: none.
- Model label retained by the frozen comparison configuration: `gpt-4.1-mini`.
- Validation: the existing independent observed-versus-oracle validator.
- Human MP/UCR or A/B coding: none.

The source archive was `source_HEAD_35c930d92119d47560879496902e928daf20ac02.zip`, whose SHA-256 is `39178f573fafb0542d26a927f77b43bbcbbe16de3fdab0c1d7b7dcdb43da833d`. All 153 entries in the archive's `SOURCE_MANIFEST.sha256` verified successfully. Because the archive does not contain `.git`, it was committed without source edits as a local execution snapshot. The runner therefore recorded local commit `b7f3c8682b364ed1d9381eafa8408bd595d4450e` and tree `a7562237ec5a13a50f3aef540b92406f04086897`; these are execution identifiers, not a relabelling of the original formal commit.

## Results

| Scenario | Run ID | Execution | Validator | Checks | Failing checks | Formal eligible |
| --- | --- | --- | --- | ---: | ---: | --- |
| S7 Edit | `baseline-s7-r01-20260810T053723387040Z-26c0bc40` | completed | PASS | 126 | 0 | no |
| S8 Exclude | `baseline-s8-r01-20260810T053723837025Z-a77ce6fa` | completed | PASS | 126 | 0 | no |

For each run, all five `TOPIC-n-SOURCE-GROUNDING` checks passed. Each linked message was a participant message from the same Session and topic. The S7 Evidence Record contained the saved Edit outcome, and the S8 Evidence Record contained the saved Exclude outcome. All evidence-candidate items reached their frozen final review status, reviewer identity and timestamps were saved, and the fixed overall decision remained `revision_requested`.

## Claim boundary

This diagnostic supports only the following statement:

> Once a contract-compliant deterministic Baseline trajectory reached a review-ready digest, the shared authenticated researcher layer executed and recorded the prescribed S7 Edit and S8 Exclude workflows, including valid structural source links and rendered Evidence Records.

It does not establish that the live prompt-only Baseline completed S7 or S8 in Formal 32, that it reaches the review stage reliably, or that its generated text preserves participant meaning. It does not change the frozen Baseline result of 10/16 completed attempts, create two additional complete pairs, or alter any A/B, MP or UCR result.

## File guide

- `EXECUTION_SUMMARY.json`: concise machine-readable run and validation result.
- `SOURCE_LINK_AUDIT.csv`: topic-level structural source-link audit extracted from validator observations.
- `evidence_records/`: friendly copies of the final S7 and S8 rendered Evidence Records.
- `runs/`: full append-only runner, raw HTTP, fixture model-call, parsed-output and validator artefacts.
- `SHA256SUMS.txt`: hashes for every file in this supplementary directory except itself.


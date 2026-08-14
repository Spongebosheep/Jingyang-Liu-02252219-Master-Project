# PurrStone v10.2 technical execution contract

Read this contract together with `V10_2_METHOD_AMENDMENT.md`. v10.2 supersedes v10.1 for future execution while preserving v10.1 unchanged as historical pre-formal documentation.

## Frozen comparison

No evaluated condition behaviour or input changes in v10.2. The prompt, schema, `gpt-4.1-mini` model, call parameters, scenarios, participant inputs, protocol targets, one-follow-up limit, metrics, quality-review rules, exact 16-pair schedule, and 32 condition identities retain their v10.1 hashes and definitions.

The historical Baseline development observation (reported 13 passes from 16 attempts) has no surviving raw directory. It is neither independently re-verifiable nor formal evidence, is not a readiness criterion, and must not be reconstructed or rerun.

## Pre-formal sequence

Offline verification and reviewed source freeze remain required. `evaluation/specs/freeze_manifest.v10_2.json` remains at `PENDING_V10_2_REVIEW_COMMIT` until the reviewed implementation is committed and a separate freeze-metadata action records that commit. Formal execution remains prohibited until explicit approval.

## Formal orchestration

The complete immutable schedule is written before the first runner starts. `S1-R01` is the first operational pair but is not a stop gate. All 32 registered identities are invoked once in schedule order. Each runner or validator outcome is captured after its identity; behavioural and eligibility failures remain failures while execution continues. There are no retries, repairs, replacement identities, or backfills.

A completed result distinguishes (1) all 32 attempted and eligible, (2) all 32 attempted with retained failures, and (3) a schedule/capture failure that prevents all 32 identities from being safely recorded. Only preflight or immutable schedule/capture safety may make the management command fail; an incorrect or ineligible evaluated outcome may not.

## Downstream boundary

Exports retain every available runner record and validator result from the formal-plan root. Full-denominator reporting retains absent and failed identities. Pair selection records exact exclusion reasons. Only complete independently PASS and formally eligible pairs enter coder material: 16 meets the recommendation, 8–15 yields the existing warning, and fewer than 8 blocks coder-material generation. Human A/B, MP, and UCR judgments remain pending two independent real coders.

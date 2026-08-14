# PurrStone v10.2 pre-formal method amendment

## Timing and disclosure

This amendment was made after a non-formal Baseline development observation and before Formal 32. The original raw development-gate directory is irrecoverably unavailable. A contemporaneous report stated that 16 Baseline identities were attempted, with 13 passes and three retained failures. Without the raw directory, that observation cannot be independently re-verified and must not be used as formal evidence. It will not be concealed, reconstructed, or rerun.

## Correction to the readiness rule

The former 16/16 behavioural readiness criterion incorrectly treated measured Baseline behaviour as an infrastructure prerequisite. Model, parsing, action-selection, path, workflow, runner, and validator failures are measured condition outcomes. They must remain failures, be retained, and be reported; they do not justify stopping other registered identities.

This rule applies symmetrically to MVP and the prompt-only Baseline. Neither condition has been changed in response to the observation. The evaluated prompt, output schema, model, call parameters, scenarios, participant inputs, protocol targets, and one-follow-up limit remain frozen. Metric definitions, pair-selection thresholds, and quality-review contracts also remain frozen.

## Formal 32 rule

Formal 32 uses the existing immutable 16-pair, 32-attempt schedule. The schedule is recorded before the first condition begins. `S1-R01` remains first solely for operational ordering and reporting; it is not a behavioural stop gate.

Every registered condition identity will be invoked exactly once. Successes and failures are retained, no identity is retried or replaced, and the result is atomically updated after every attempted identity. Completion and failure reports retain the full planned denominator, including failed or absent records.

Only complete pairs whose two registered runs are independently validated and formally eligible may enter blinded output-quality, Meaning Preservation (MP), and Utterance Coverage Rate (UCR) coding. Incomplete and excluded pairs remain visible with exact reasons; no missing pair is backfilled.

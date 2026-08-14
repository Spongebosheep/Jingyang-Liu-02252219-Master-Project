# Frozen metric calculator checkpoint

Date: 2026-08-06

## Scope and evidence basis

This checkpoint implements only the independent metric calculator. Before
implementation, the following three project authorities were re-read and
visually checked:

- the user-designated three-page teacher PDF `(5).pdf`;
- `代码修改建议(6).docx`;
- `Assement creteria.docx`.

They consistently require a reproducible, auditable comparison against the
pre-defined success criteria, with runner, validator, exporter and calculator
kept as separate responsibilities. The calculator therefore consumes saved
export files only. It does not execute either condition, call a model, query or
write the project database, repair an observation, perform human coding, or
write a comparative conclusion.

## Frozen additions

- `specs/metric_calculation_schema.v1.json` fixes the append-only output files,
  CSV fields, calculation statuses, integrity rules, paired fields and claim
  boundaries before any formal result exists.
- `specs/freeze_manifest.v6.json` adds the calculator schema hash. The frozen
  S1-S8 scenarios, oracle, nine metric definitions, baseline contract, baseline
  prompt, prompt-only output schema and exporter schema remain unchanged.
- Historical manifests v1-v5 remain valid audit records.

## Implemented calculation

The calculator verifies the completed export manifest, every inventory file,
copied contracts, CSV schemas, runner hashes, validator hashes and cross-file
run identities. It verifies the input inventory again after calculation.

Seven metrics are automatic:

| Metric | Saved evidence used |
|---|---|
| AI | one complete control record for each frozen eligible decision step |
| AC | exact observed-action comparison with the pre-registered eligible oracle |
| SLC | included/edited item and valid same-Session participant topic source |
| MIV | frozen limitation outcomes plus current validator render visibility |
| PCC | saved Skip/Stop transition, later-decision boundary and Stop probe |
| RAC | item and overall review records with action-specific required fields |
| TCR | exactly one explicit record for each of the five research topics |

Two metrics remain human work:

- UCR is emitted only as `PENDING_MANUAL`; no similarity score or model label is
  substituted for independent atomic-proposition coding.
- MP is emitted per eligible evidence item as `PENDING_MANUAL`; no reviewer
  checkbox or automated semantic score is substituted for two-coder judgement
  and consensus.

Every output includes numerator, denominator, status and failure details.
Incomplete or failed runs are not filtered. Missing or duplicate condition pairs
are labelled `INCOMPLETE` or `AMBIGUOUS`, never silently selected. Paired output
is side-by-side only: it contains no delta, winner or superiority claim.

## Self-check finding and follow-up closure

The S7 non-formal integration path contains a complete item-level Edit event,
but its overall `ReviewDecision` is still pending and lacks the required
decision/reviewer/time/note. The calculator correctly reports RAC as `1/2 FAIL`
for that run. Excluding the pending row or repairing it inside the calculator
would hide a real audit-completeness gap, so neither was done.

Before formal paired runs, the runner/review path must be separately amended and
revalidated so S7 and S8 exercise a genuinely completed overall review event.
The frozen oracle and metric formula must not be weakened to make that pass.

The later overall-review completion checkpoint closed this issue before any
formal run. A separate `review_completion.v1.json` now freezes one S7/S8
request-revision event with an attributable note while keeping unresolved items
pending and both human-judgement fields false. The calculator itself was not
changed to manufacture a pass. New S7/S8 MVP fallback and baseline mock runs
passed the independent validator and produced RAC `2/2 PASS` from the exported
item event plus completed overall decision.

## Verification

- full Django test suite: 140/140 PASS;
- frozen specification tests: 25/25 PASS;
- metric-calculator focused tests: 5/5 PASS;
- Django system check: PASS;
- migration drift: none;
- dependency check: PASS;
- database SHA-256 unchanged:
  `a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc`.

The 16-run integration rehearsal used eight MVP fallback dry runs and eight
prompt-only mock runs. It tested file structure and calculation mechanics only.
All runs remained `formal_evidence_eligible=false`; there were no live OpenAI
calls and no formal comparison results.

## Work not performed in this checkpoint

- no formal paired Session;
- no formal comparison table, screenshot or Evidence Record package;
- no UCR, MP or blinded quality coding;
- no research result or superiority conclusion;
- no product, model, routing, template, migration or database change.

## Formal-run and user responsibility

The targeted overall-review completion repair and S7/S8 rerun are complete.
Under v8, formal runs require their own directly captured live API evidence,
the frozen model and parameters, clean code identity, an attributable reviewer,
independent validation and matched-pair fairness.

The user remains responsible for supervising the formal run, capturing the
selected formal screenshots, arranging/performing independent UCR and MP coding
and blinded quality review, and approving the final research interpretation.

# Referral evaluation layer

This package is the isolated evaluation layer required for the referral study.
It is intentionally separate from the participant UI and from the frozen
`interviews` product implementation.

## Boundary

The evaluation layer may create controlled test Sessions through the real
application path, read saved state, validate it, and export evaluation files.
It must not silently repair model output, directly edit database state to make
a scenario pass, reuse LangGraph routing in the prompt-only condition, or
overwrite failed runs.

The paired conditions are:

- `mvp`: the frozen Django/LangGraph workflow.
- `prompt_only_baseline`: the same model, Protocol, participant inputs, output
  schemas, structured-evidence layer, and review layer, but without LangGraph
  routing or imported product decision logic.

## Responsibilities

| Component | Responsibility | Status at this checkpoint |
|---|---|---|
| `specs/scenarios.v4.json` | S1-S8 versioned Consent integrity, fixed Opening, Protocol-coverage/control inputs, S5 post-Stop rejection probe, and pre-registered oracle | frozen before v10 formal runs |
| `specs/metrics.v3.json` | nine metric definitions with separate Protocol-coverage, participant-control, and topic-reach fields plus UCR/MP rules | frozen before v10 formal runs |
| `specs/baseline_contract.v2.json` | fairness/separation rules, assessment-guidance precedence and development-gate boundary | frozen before v10.1 formal runs |
| `specs/prompt_only_output_schema.v2.json` | strict shared Protocol-coverage/control envelope for the prompt-only turn | frozen before v10 formal runs |
| `prompts/prompt_only_baseline.v3.txt` | direct prompt-only policy with assessment-guidance threshold precedence and no oracle inputs | frozen before v10.1 formal runs |
| `specs/export_bundle_schema.v2.json` | condition-neutral raw JSON, Consent integrity, CSV, and Evidence Record fields | frozen before v10 formal runs |
| `specs/metric_calculation_schema.v2.json` | v10 append-only metric outputs, pairing and human-coding boundaries | frozen before v10 formal runs |
| `specs/review_completion.v2.json` | fixed S7/S8 item-review completion plus overall request-revision action and non-approval boundaries | frozen before formal runs |
| `specs/quality_review.v2.json` | v1 blinded questions/coder process plus executable complete-pair selection and 8/16 thresholds | frozen before v10.1 live comparison results |
| `specs/freeze_manifest.v10_1.json` | current construct, Consent, Baseline gate, pair fairness, run plan and contract hashes | candidate frozen before v10.1 live comparison results |
| `schemas.py` | shared field names and deterministic contract validation | implemented |
| `validate_specs.py` | fail-closed specification validation | implemented |
| MVP runner | execute the real application path and retain raw HTTP/model/state records | implemented; formal runs gated |
| prompt-only baseline | versioned prompt, strict adapter and compatible review-layer runner | implemented; formal runs gated |
| `baseline_development_gate.py` | exact 16 live dry-run Baseline paths plus independent validators | implemented; results never formal evidence |
| `formal_evaluation_plan.py` | immutable 32-attempt schedule with paired S1-R01 pilot and no replacement reruns | implemented; requires explicit approval |
| `run_validator.py` | independently compare either condition's saved observations with the frozen oracle | implemented; append-only results |
| `exporter.py` | append-only raw JSON, turn/item-source/review CSV and Evidence Record package | implemented; no metrics or conclusions |
| `metric_calculator.py` | calculate seven automatic metrics and retain UCR/MP as pending manual work | implemented; no conclusions |
| `quality_review.py` | randomly blinded A/B package, fixed coding sheets, locked coder imports, disagreement/consensus reports and separate UCR/MP summaries | implemented; no automatic winner |

No file in this checkpoint is formal evaluation evidence. New formal S1-S8 runs use
the v10.1 method and must directly record the frozen model, clean code identity,
raw calls, parsed decisions, review trail, Evidence Record and validator result.

## Version lineage

The original v1 and v2 contracts are retained unchanged as audit history. Before any
formal run, a real-path review showed that v1 omitted the participant's
deterministic section-0 Opening acknowledgement between Consent and research
topic 1. The current v2 scenario contract adds the same fixed acknowledgement
to S1-S8. The current v2 metric contract makes eligible-turn, UCR
atomic-proposition, and MP disagreement rules explicit without changing the
nine metric formulae. A later pre-validator completeness check found that the
S5 v2 oracle required a post-Stop POST to create no records but did not include
the POST itself. The current v3 scenario adds one fixed rejection probe and the
v3 manifest records the reason and hashes. The v4 manifest adds only the
versioned prompt-only policy and strict output schema; S1-S8, the oracle,
metrics and baseline fairness contract are unchanged. The v5 manifest adds only
the frozen exporter schema before implementation. The v6 manifest adds only the
metric-calculation output schema, integrity rules, pairing fields and manual
UCR/MP boundary before calculator implementation. A calculator self-check then
showed that S7/S8 saved the focal item action but never completed the overall
review event. The v7 manifest adds only a condition-neutral scripted
request-revision contract for that post-interview event. It does not approve
unresolved items or pre-supply human quality judgements. No formal run or live
OpenAI call preceded these revisions. The v8 manifest then replaces the
separate external-gate dependency with direct per-run evidence and matched-pair
fairness checks before any live comparison result. It preserves the v7 assets,
S1-S8, oracle, prompt, metrics, schemas and run counts unchanged, while adding
the pre-result blinded quality-review contract required by the classroom plan.
The v9 manifest records one final pre-result correction found by checking the
classroom run instructions against the executable path: S7/S8 now explicitly
resolve every evidence-candidate item through the authenticated review route;
S8 includes evidence before excluding another item, then records the fixed
request-revision decision. Participant inputs, the scenario decision oracle,
baseline prompt, nine metrics, quality questions and 16/32 run plan are
unchanged. Historical manifests and `review_completion.v1.json` remain present.

The v10 lineage is a pre-formal construct correction. It replaces answer
sufficiency with a provisional Protocol-coverage check and records Skip/Stop
and topic reach independently. It also records the exact Consent notice
version, snapshot, confirmation timestamp, and SHA-256. All v1-v9 assets and
previous evidence remain historical and are not rewritten or re-labelled.

The v10.1 lineage is a further pre-formal reliability correction. Preserved v9
records showed that 15/16 Baseline attempts stopped when the prompt selected an
extra follow-up and could not consume the next common frozen participant input.
The unrun v10 candidate had not corrected this. Prompt v3 now makes the locked
section's `assessment_guidance` the binding threshold and treats
`required_information` as conjunctive only when that guidance explicitly says
so. The runner path check remains unchanged: a genuine wrong path still fails
and is never corrected. v10.1 also freezes the exact 16/32 schedule, requires a
retained 16-attempt live development gate, and reports/excludes incomplete
pairs during blinded packaging. No v10 or v10.1 formal result preceded these
changes.

## Validate the frozen contracts

```text
python -m evaluation.validate_specs
python -m unittest discover -s evaluation/tests -p "test_*.py"
```

The validator rejects result-like keys inside the scenario oracle. This keeps
observations and PASS/FAIL judgements out of the pre-registered input file.

## Execute the MVP condition

The command below runs one non-formal attempt and rolls back every database
record after retaining the runner artefacts:

```text
python manage.py run_mvp_scenarios --scenario S5 --repetition 1 --run-type dry_run
```

Use `--all` instead of `--scenario S5` to exercise S1-S8 in frozen order. Each
attempt receives a new `run_id` and directory. The runner stores the frozen
input/oracle next to observed HTTP, Session, Message, AgentDecision, digest,
source, and review-event fields, but deliberately leaves
`validation_status=not_run`.

For S7/S8 only, the runner executes every separately frozen item action through
the authenticated Output Review POST before recording the fixed overall
request-revision event. S7 edits the focal item and includes the remaining
eligible items. S8 includes one item before the focal Exclude action and then
includes the remaining eligible items. No runner action approves the Session,
supplies a human quality judgement, or silently resolves an item in database
state.

`--run-type formal` fails closed unless all of the following are present:

- a clean tracked Git worktree;
- an existing named reviewer;
- `OPENAI_API_KEY` and enabled constrained follow-up wording;
- the configured model matching the v10 frozen comparison model;
- direct raw call records matching the frozen call parameters;
- no mock transport, semantic repair, post-hoc correction or fallback evidence.

Raw Responses API calls are captured by a pass-through proxy. It records the
request and response without changing prompts, routing, parameters, or model
outputs, and it never records the API key. HTTP bodies are retained separately
from the parsed database-state record. Formal PASS/FAIL remains the
responsibility of the independent validator.

## Execute the prompt-only condition

The matched comparator uses the same model identifier, locked Protocol,
scenario/repetition, participant inputs, structured-evidence layer and
researcher review layer. One versioned prompt directly selects the provisional
Protocol-coverage assessment, participant control, action, missing information,
short audit reason and participant-facing response. The adapter does not import or call the product LangGraph, product
decision functions or an oracle repair path.

This command tests the full baseline harness with frozen mock responses and
rolls back all QA records:

```text
python manage.py run_prompt_baseline --all --run-type dry_run --mock-responses
```

Mock runs are always `formal_evidence_eligible=false`. Omitting
`--mock-responses` uses the live Responses API and requires `OPENAI_API_KEY`.
Formal runs additionally require the v10.1 frozen model, clean commit and named
reviewer. A formal baseline attempt can never use the mock transport.

For every prompt-only turn, the runner saves separate raw request, raw response
and parsed JSON files. Parse errors and transport errors are retained without
semantic repair. LangGraph routing provenance and graph-approved follow-up
wording are explicitly `not_applicable` for this condition.

Before final freeze, run the live Baseline development gate once from a clean
candidate commit:

```text
python manage.py run_baseline_development_gate
```

It executes the exact 16 planned Baseline identities, continues through all
development-path failures, independently validates each saved attempt, and
passes only at 16/16. Every result remains `dry_run` and
`formal_evidence_eligible=false`. A failure blocks freeze; it is not permission
to correct an action or replace a run.

After both live gates, final freeze, and explicit user approval, execute the
formal schedule exactly once:

```text
python manage.py run_formal_evaluation_plan --reviewer <existing_username>
```

The command writes the complete 32-attempt plan before its first call, runs both
conditions for the S1-R01 pilot, stops on pilot failure, and otherwise attempts
the remaining 30 identities once. Post-pilot failures are retained while the
original schedule continues; no replacement rerun is created.

## Validate saved observations

The validator reads runner records and raw HTTP bodies without executing the
product, calling a model, editing database state, or calculating aggregate
metrics:

```text
python manage.py validate_evaluation_runs --record <run-dir>/runner_record.json
python manage.py validate_evaluation_runs --input-root <mvp-run-root>
```

Each invocation writes a new append-only
`validation/<validator_run_id>/validator_result.json`; the original runner
record is not modified. PASS means only that saved automatic observations
match the frozen oracle. Dry-run or fallback records remain
`formal_evidence_eligible=false`, and UCR/MP remain separate manual coding
tasks.

## Export saved observations

The unified exporter accepts either repeated runner-record paths or a root to
scan. It preserves every selected run, including preflight and execution
failures, copies runner/raw/parsed/validator files without changing them, and
writes condition-neutral tables:

```text
python manage.py export_evaluation_bundle \
  --record <mvp-run>/runner_record.json \
  --record <baseline-run>/runner_record.json
```

Each invocation creates a new append-only package containing `manifest.json`,
raw run directories, `runs.csv`, `turns.csv`, `item_sources.csv`, `reviews.csv`
and any recorded Evidence Record renders. A validator result whose saved runner
hash no longer matches is retained but marked `STALE`; it cannot confer formal
eligibility. The export manifest also records pair-integrity checks for model,
commit, Protocol and frozen-contract identities. The exporter does not calculate AC/AI/SLC/MIV/PCC/UCR/RAC/MP/TCR,
does not start formal Sessions, and does not perform or infer human judgements.

## Calculate the frozen metrics

The calculator consumes one completed exporter bundle as read-only input. It
verifies the export inventory before and after calculation, then creates a new
append-only calculation directory:

```text
python manage.py calculate_evaluation_metrics \
  --export-directory <evaluation-export-directory>
```

It writes detail, run, condition and mechanical paired CSVs plus a JSON summary.
AI, AC, SLC, MIV, PCC, RAC and TCR are calculated from saved records. UCR stays
`PENDING_MANUAL` until independent atomic-proposition coding is supplied, and
MP stays `PENDING_MANUAL` until independent item judgements and consensus are
supplied. The calculator does not infer either manual metric from text
similarity, reviewer checkboxes or model output, and it does not identify a
winning condition or make a comparative claim. A pair with mismatched model,
commit, Protocol or frozen identities cannot receive formal paired eligibility.

## Prepare blinded A/B, UCR and MP coding

After a completed unified export, generate the condition-neutral pair-selection
report and, when the minimum is met, the randomly assigned coder package:

```text
python manage.py generate_blinded_quality_review \
  --export-directory <evaluation-export-directory>
```

Give each coder only the generated `coder_material` directory. Keep
`private/blinding_key.json` separate. The coder directory contains condition-
neutral HTML/JSON pair material plus three fixed CSV templates:

- `quality_coding_sheet_template.csv` for Q1-Q5, A/B/Tie, confidence,
  rationale and issue flags;
- `ucr_atomic_proposition_coding_template.csv` for independent proposition
  segmentation and support coding;
- `mp_item_coding_template.csv` for separate item-level Meaning Preservation.

Only registered pairs with exactly one run per condition, pair-integrity PASS,
completed execution, independent validation PASS, and formal eligibility on
both sides enter coder material. Excluded or absent pairs are retained in
`pair_selection_report.json`. Sixteen pairs meets the recommendation; 8-15
generates a below-recommended warning; fewer than 8 creates a blocked technical
report and no coder material. Never backfill from v9 or a replacement attempt.

Import each completed sheet independently; repeat once per coder and coding
type:

```text
python manage.py import_coder_submission \
  --coder-material-directory <coder-material-directory> \
  --coding-sheet <completed-coder-csv> \
  --coding-type quality
```

The importer verifies the frozen header, package identity, complete expected
units, allowed labels and a single coder identity. It saves the submitted bytes
unchanged beside a normalised copy and does not read the private key.

Compare the two locked quality submissions before deblinding:

```text
python manage.py report_quality_consensus \
  --coder-import <coder-1-quality-import> \
  --coder-import <coder-2-quality-import>
```

If the report is `awaiting_consensus`, complete only the blank consensus fields
in its `consensus_report.csv`, then repeat the command with
`--consensus-sheet`. Supplying `--blinding-key` is rejected until every
disagreement is resolved. Originals, coder labels, disagreements and consensus
are all retained; deblinding creates a separate mapping and never declares a
winner.

Run the same two-coder process separately for UCR and MP:

```text
python manage.py report_manual_metric_consensus \
  --coding-type ucr \
  --coder-import <coder-1-ucr-import> \
  --coder-import <coder-2-ucr-import>

python manage.py report_manual_metric_consensus \
  --coding-type mp \
  --coder-import <coder-1-mp-import> \
  --coder-import <coder-2-mp-import>
```

The manual metric summary is written only after all disagreements are resolved.
The five A/B quality questions never substitute for UCR or MP.

Earlier dry-run self-checks exposed pre-formal review-path gaps rather than
hiding them in the calculator. The v7 follow-up added the overall decision; the
v9 correction completes every S7/S8 candidate item and the classroom-ordered
S8 Include/Exclude path. Fallback/mock rehearsals remain
`formal_evidence_eligible=false` and are not study results.

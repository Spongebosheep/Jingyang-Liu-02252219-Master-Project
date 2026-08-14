# Phase 5 / Classroom Step 3 checkpoint

Date: 2026-08-06

## Result

The independent `evaluation/` directory and its pre-run contracts are in
place, aligned with the public Consent -> Opening -> research-topic path, and
frozen before any formal run. The frozen Django/LangGraph product
implementation was not modified.

## Baseline identity

- Authoritative Phase 4 commit: `da71a3f464402091dae412b8be4f54b8d97b7e57`
- Verified combined archive SHA-256:
  `6196a48b5641f78cc710559fe40f57f17cafd5412b2b0fab854f600ab78c4a4d`
- Local Phase 4 baseline commit: `ed109c1`
- Controlled baseline files: 63
- Database SHA-256 before and after this checkpoint:
  `a34e14f9495cef7e41220290786520be310e447e37dbe43ee553d6d89f676abc`

## Added contracts

- S1-S8 fixed Consent and Opening inputs, participant controls, researcher
  Edit/Exclude actions, per-turn action oracle, and one outcome for each
  Protocol research topic.
- Prompt-only baseline fairness and non-reuse contract.
- Nine metric definitions: AI, AC, SLC, MIV, PCC, UCR, RAC, MP, and TCR.
- Explicit eligible-turn rules, UCR atomic-proposition rules, and MP
  disagreement/consensus rules.
- A freeze manifest that records the contract hashes and rejects drift.
- Dependency-free shared field/schema constants and fail-closed validation.

## Versioned pre-run correction

`scenarios.v1.json`, `metrics.v1.json`, and `freeze_manifest.v1.json` remain
unchanged as audit history. The current v2 contracts record why the fixed
section-0 Opening acknowledgement and detailed metric-operationalisation rules
were added. This correction occurred with `formal_runs_started=false` and no
OpenAI call, so it is a pre-run specification correction rather than a
post-hoc change to observed results.

## Verification

- Contract tests: 16 passed.
- Existing product tests (`interviews`): 79 passed.
- Combined Django discovery: 95 passed.
- Django system check: no issues.
- Migration drift: no changes detected.
- Dependency check: no broken requirements.
- Formal evaluation runs: not started.
- OpenAI calls: none.

## Deliberately not implemented in this checkpoint

- MVP scenario runner.
- Versioned prompt-only baseline and lightweight adapter.
- Run validator, unified JSON/CSV exporter, and metric calculator.
- Formal paired S1-S8 runs, UCR/MP coding, or blind A/B review.

The next implementation step is the MVP runner. It must use the public
participant/application path and retain every attempted run without directly
editing database state to force an oracle match.

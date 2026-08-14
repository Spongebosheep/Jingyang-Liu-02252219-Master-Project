# PurrStone Interview-to-Review MVP

This repository contains a local Django research prototype for a bounded,
semi-structured stakeholder interview-to-review workflow. Django manages
records and researcher review, LangGraph controls the permitted interview
actions, and the OpenAI model has a constrained role in a provisional
Protocol-coverage check and wording one graph-approved follow-up.

It is not a chatbot, CRM, clinical tool, autonomous qualitative researcher,
production authentication system, or permanent cloud deployment. The included Sensory
Overload Interview is the evaluated demonstration case. Creating another
Protocol demonstrates configurability; it does not validate that new Protocol
for research use.

This source tree is the **v10.1 pre-formal candidate**. It contains no v10.1
formal API result or human A/B, UCR, or Meaning Preservation judgement. Read
`AGENTS.md` and `docs/V10_1_EXECUTION_CONTRACT.md` before running evaluation.

## Run in VS Code on Windows

Open the extracted project folder in VS Code and use a PowerShell terminal.
Use a Python version shown by `py --list`; Python 3.11 and 3.12 are supported.
For Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_mvp
python manage.py createsuperuser
python manage.py runserver
```

Then open <http://127.0.0.1:8000/> and sign in with the researcher account you
created. Source ZIPs and Git clones deliberately exclude `db.sqlite3`; `migrate`
creates the local schema and `seed_mvp` creates the clean P01 + IS-01
demonstration baseline. Do both before first use.

To keep records from an earlier final version, stop its server and make a backup
of its `db.sqlite3`. Copy that database into this new project folder before
running `migrate`; the migrations upgrade it without intentionally deleting
Stakeholders, Sessions, Protocol versions, or researcher accounts.

`python manage.py runserver` is local development hosting. The default
`127.0.0.1` address is available only on the computer running it; a UUID
participant link is not automatically a public link. The temporary demonstration
route below can expose it to another device, but production deployment remains
outside this MVP.

## Temporary public demonstration with TryCloudflare

The project accepts temporary `*.trycloudflare.com` hosts and recognises the
HTTPS forwarding header so that copied participant links use the public
`https://` address. This is for an MVP demonstration only.

Start Django in one PowerShell terminal:

```powershell
python manage.py runserver 127.0.0.1:8000
```

After installing `cloudflared`, start a Quick Tunnel in a second terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Open the generated address through its public hostname, for example:

```text
https://random-name.trycloudflare.com/login/
```

Sign in through that public URL, open `Interview Sessions`, and use `Copy
participant link`. The copied value should begin with the same
`https://random-name.trycloudflare.com/` hostname. Do not copy the link from a
Portal tab opened at `127.0.0.1`, because `build_absolute_uri()` deliberately
uses the hostname of the current request.

A new Quick Tunnel may receive a different random hostname, so links from an
earlier tunnel can stop working. Use demonstration data only, keep the
researcher login private, and do not describe this route as production hosting.

## Researcher workflow

- **Stakeholder records:** every record uses the same detail flow. Select any
  Stakeholder, open its record, and create one or more interview Sessions.
- **Protocol library:** create a new Protocol family and v1, or copy an existing
  locked Protocol as its next version. Draft research topics can be added,
  removed, and reordered before the Protocol is locked or used by a Session.
  Each topic stores the exact primary question, required information,
  assessment guidance, permitted follow-up focus, and interaction boundary.
  Placeholder research questions and placeholder requirements cannot be saved.
- **Protocol runtime:** the same LangGraph pipeline reads every locked Protocol.
  The graph owns routing, Skip, Stop, the one-follow-up limit, boundary handling,
  and completion. When the API is configured, the LLM assesses semantic
  coverage against the topic fields and additional Protocol rules, then may word
  one graph-approved follow-up. If the Protocol-coverage check is unavailable, the
  Sensory case uses its tailored fallback and a custom topic uses a conservative
  fallback that keeps unverified requirements visible; answer length alone is
  never treated as covered custom-topic Protocol information.
- **Consent transparency:** the participant sees the selected Protocol purpose,
  topics, AI role and limits, one-follow-up boundary, controls, and researcher-
  review boundary before any research content is collected. Acceptance stores
  the exact structured notice, notice version, confirmation timestamp, and
  SHA-256 for later integrity checking.
- **Output Review:** select a Session and work through three progressively
  disclosed stages: topic-grouped full transcript, reviewed topic extracts,
  then final checks and researcher decision. `Why the interview took this step` remains
  collapsed beneath the response or participant-control event that triggered it.
- **Reviewed extracts by Protocol topic:** include the source extract, include an
  edited extract with a reason, or exclude the topic with a reason. The extract
  is traceable to the typed interview log; it does not verify the participant's
  account as an external fact. Skipped, stopped,
  and not-reached topics stay visible as unavailable topics and cannot be
  approved as participant evidence.
- **Overall decision:** after all extract candidates are reviewed, the system
  checks three machine-verifiable workflow conditions and the researcher confirms
  two non-automatable judgements before approval. The researcher can instead
  request revision or mark the Session not usable. These conditions are not a
  validated quality scale.
- **Evidence Status:** select the exact Participant + Session record before
  reading its saved checks and evidence contents. Export that Session alone or
  export all saved Session records; the two scopes are labelled separately.
- **Evidence Record:** exports a Session snapshot whose reviewed-evidence
  section contains only Included source extracts or Included edited extracts.
  It also records clickable transcript sources, limitations, review history,
  Protocol version and lock state, consent/Session/reviewer timestamps, the
  overall researcher decision, and the full transcript. System-step
  explanations appear last as a secondary diagnostic record rather than
  primary evidence. The export is a self-contained HTML snapshot; its
  `Print / Save as PDF` control creates a PDF copy through the browser.

### Basis of the Session release conditions

The interface does **not** present these conditions as a score or a validated
qualitative-quality scale. They address five concrete failure modes in this
MVP's interview-to-review handoff, but the interface deliberately separates
what the software can verify from what still requires researcher judgement:

| Type | Release condition | Failure it prevents | Methodological basis |
|---|---|---|---|
| System checked | Included extracts have valid transcript source links | An extract cannot be traced to a participant response in the same Session and topic | Audit-trail transparency and supporting quotations |
| System checked | Skip and Stop controls were followed | Participant control is recorded but the interview continues improperly | Ethical practice and the MVP's explicit autonomy requirement |
| System checked | Coverage limitations remain represented | Partial, skipped, stopped, or not-reached material is presented as complete | Transparency about analytic limits |
| Researcher judgement | Included extracts preserve participant meaning | Selection or editing adds an unsupported claim or changes meaning | Credibility and methodological integrity |
| Researcher judgement | Protocol interaction boundaries were respected | A Session departs from its locked interaction constraints | Ethical practice and method–purpose coherence |

The wording is informed by qualitative-methods literature on audit trails,
supporting quotations, methodological integrity, transparency, and ethics:
[Nowell et al. (2017)](https://doi.org/10.1177/1609406917733847),
[Tong et al. (2007)](https://doi.org/10.1093/intqhc/mzm042),
[Levitt et al. (2018)](https://doi.org/10.1037/amp0000151), and
[Tracy (2010)](https://doi.org/10.1177/1077800410383121).
These sources justify the underlying concerns; they do not validate this exact
set. `Met` records a deterministic workflow result and `Confirmed` records one
researcher's judgement. Neither label verifies the participant's account as an
external fact, measures outcome quality, or validates the whole study. The
conditions gate only the Session's `Approved` review state; an unapproved
Evidence Record can still be exported as an auditable snapshot.

The researcher interface deliberately does not expose internal `ADT` names or
database primary keys. Source labels and transcript numbering are generated
within each Session for readable review. Evidence Record links use Session-
scoped anchors so an all-Session export cannot jump to another Session's
similarly numbered response; the underlying decision records remain available
to the application and tests.

## Use the OpenAI-assisted branch

Set the API key only in the current VS Code terminal. Do not add it to the code,
database, README, or Git repository:

```powershell
$env:OPENAI_API_KEY = "<your OpenAI API key>"
python manage.py run_openai_e2e_check
python manage.py runserver
```

No configuration batch file is required. The optional
`PURRSTONE_OPENAI_MODEL` environment variable can override the default
`gpt-4.1-mini` model. Every current OpenAI request uses `store=False`.

A live check counts as passed only when the command prints all of the following:

```text
"status": "PASS"
"live_protocol_coverage_check": true
"live_follow_up_wording": true
```

The command creates temporary QA data inside a transaction and rolls it back at
the end. See `docs/live_openai_e2e_check.md` for the exact evidence boundary.

Before any final freeze or formal comparison, v10.1 also requires the exact
16-attempt live prompt-only development gate:

```powershell
python manage.py run_baseline_development_gate
```

It must report `gate_status=PASS`, `attempt_count=16`, `pass_count=16`, and
`formal_evidence_eligible=false`. These dry-run records test path stability and
must never be reused as formal evidence. Formal execution remains blocked until
the user gives explicit approval after both live gates pass.

## Reset demonstration data

To clear only IS-01 while preserving its participant link, all other records,
Protocols, and researcher accounts:

```powershell
python manage.py reset_demo_session
```

To restore the complete workflow dataset to the original P01 + IS-01 + Sensory
Overload Interview v1 baseline while preserving researcher accounts:

```powershell
python manage.py reset_mvp --all
```

The full reset deletes manually added Stakeholders, Sessions, Protocol families
and versions, as well as their interview and review data. It then recreates the
demonstration baseline, including a new IS-01 participant link. For safety,
`reset_mvp` without `--all` refuses to run and explains the two choices.

## Verify the project

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The release test command reports the current passing-test count. Automated tests
cover deterministic control, persistence, authentication, Protocol and Session
selection, custom-Protocol runtime fields and conservative fallback, transcript-
linked control explanations, item-review safeguards, Session-specific Evidence
Status/export, system-checked release conditions, researcher judgements,
Session-scoped Evidence Record source anchors, audit metadata and review-status
wording, TryCloudflare HTTPS participant-link generation, limitations, reset
safety, OpenAI request shape, and rollback behaviour. They do not substitute for the
real API check or claim production readiness, clinical suitability,
generalisable interview quality, or measured scaling benefits.

## Key folders

- `interviews/`: models, workflow logic, LangGraph agent, views, forms, tests,
  management commands, and migrations.
- `templates/interviews/`: researcher and participant interfaces.
- `static/interviews/`: local visual assets.
- `docs/`: reproducible test evidence and live OpenAI check instructions.
- `AGENTS.md`: machine-readable v10.1 execution order, stop conditions, and
  evidence boundaries for a new Cloud/Codex task.
- `evaluation/`: frozen S1-S8 comparison contracts, paired runners, independent
  validator, exporter, metric calculator, blinded A/B review package and the
  separate two-coder UCR/Meaning Preservation workflow. See
  `evaluation/README.md` before starting a comparison run.

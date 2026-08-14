# Live OpenAI End-to-End Check

## Purpose

This check verifies the current PurrStone MVP through one minimal, real OpenAI-assisted workflow. It is deliberately narrower than a production test and does not claim general interview quality.

The check covers:

1. a public participant UUID route and consent;
2. an opening acknowledgement handled without the LLM;
3. a real OpenAI provisional Protocol-coverage check of a trigger-only answer;
4. LangGraph selecting `ASK_FOLLOW_UP`;
5. a real OpenAI call wording one graph-approved follow-up;
6. a cumulative Protocol-coverage check combining the initial and follow-up answers;
7. transcript, internal decision, and transcript-linked topic-extract persistence;
8. researcher authentication and reviewer attribution;
9. item-level topic-extract inclusion, three system-checked workflow conditions,
   two researcher judgements, and Evidence Record export.

All temporary QA records are created inside a database transaction and rolled back whether the check passes or fails.

## One-time setup

From the project folder in a VS Code PowerShell terminal, create and activate the
standard local virtual environment, install the pinned dependencies, and apply
migrations:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
```

Set the key only in the current VS Code terminal session:

```powershell
$env:OPENAI_API_KEY = "<your OpenAI API key>"
```

No configuration batch file is required. The command reads the existing
`OPENAI_API_KEY` environment variable and does not accept or print a key. The
optional model override is `PURRSTONE_OPENAI_MODEL`; if it is absent, the MVP
uses its existing `gpt-4.1-mini` default.

## Run the real check

```powershell
python manage.py run_openai_e2e_check
```

A successful live run ends with:

```text
"status": "PASS"
"live_protocol_coverage_check": true
"live_follow_up_wording": true
PASS: real OpenAI assessment, constrained follow-up, researcher review, and evidence export completed; QA records were rolled back.
```

The generated follow-up wording may vary. The command validates its role and constraints rather than requiring one exact sentence.

If the key is missing, the command stops before any model request and reports:

```text
OPENAI_API_KEY is not configured. No live API call was attempted.
```

It must not be recorded as a live pass in that state.

## Researcher sign-in for manual testing

Create a local reviewer account once:

```powershell
python manage.py createsuperuser
python manage.py runserver
```

Open `/login/` and sign in. Researcher pages require authentication; participant consent, interview, and completion pages remain accessible through their per-session UUID links. Topic-evidence decisions and the overall decision save the signed-in reviewer name, account relation, and review time. The UUID route is public to the local server but is not a cloud deployment.

## Evidence boundary

- Automated tests verify deterministic control, persistence, authentication, review rules, API request shape, and rollback behaviour.
- The management command is the reproducible gate for a real API call.
- A mock-based test is never evidence that the live OpenAI branch passed.
- This MVP check does not exercise the prompt-only Baseline. v10.1 separately
  requires `python manage.py run_baseline_development_gate`, which must complete
  and independently validate all 16 planned live dry-run Baseline paths before
  final freeze. Those gate records are also not formal evidence.
- This check demonstrates bounded workflow feasibility, not clinical suitability, production security, or validity across diverse natural participants.

## OpenAI implementation references

- Responses API migration and `store: false`: https://developers.openai.com/api/docs/guides/migrate-to-responses
- Structured Outputs with `text.format`: https://developers.openai.com/api/docs/guides/structured-outputs
- `gpt-4.1-mini` endpoint and feature support: https://developers.openai.com/api/docs/models/gpt-4.1-mini

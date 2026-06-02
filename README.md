# Jingyang Liu Master Project MVP

This repository contains the source code for my Imperial College London Design Engineering Master's Project:

**Scalable Qualitative Insight: Designing an Interview-to-Review Workflow with a LangGraph-Managed Agent for Semi-Structured Stakeholder Research**

The project is a Django-based academic prototype for a bounded **Interview-to-Review** workflow. It supports one Sensory Overload / PurrStone interview protocol, from participant consent and AI-led interview through to transcript storage, structured draft output, researcher review, Evidence Status, and Evidence Record export.

This is a formative MVP prototype, not a production system, full stakeholder CRM, or autonomous qualitative research platform.

## Main idea

The prototype separates three parts:

* **Django** manages the workflow: participant records, protocols, sessions, transcripts, outputs, review status, and evidence export.
* **LangGraph** manages interview control: protocol section, answer sufficiency, bounded probing, skip/stop handling, boundary response, missing information, and AgentDecision trace.
* **LLM support** is used only for semantic assessment and graph-approved follow-up wording.

In short: the LLM assesses semantic coverage; LangGraph decides the next action.

## Setup

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run migrations:

```bash
python manage.py migrate
```

Seed the MVP data:

```bash
python manage.py seed_mvp
```

Run the development server:

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## OpenAI API key

LLM-assisted assessment and follow-up wording require an OpenAI API key.

On Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

On macOS / Linux:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

Do not commit API keys or `.env` files.
Without an API key, the prototype can still be inspected and seeded, but LLM-assisted assessment or follow-up wording will fall back or be unavailable depending on the tested flow.


## Useful routes

```text
/                                      Researcher overview
/stakeholders/                         Stakeholder records
/protocols/sensory-overload-interview/ Protocol detail
/interview/P01/consent/                Participant consent page
/interview/P01/session/                Participant interview session
/interviews/                           Interview session management
/outputs/                              Output Review
/output-quality/                       Evidence Status
/output-quality/export/                Evidence Record export
```

## Resetting the MVP

```bash
python manage.py reset_mvp
python manage.py seed_mvp
```

## Notes

The `docs/` folder contains scripted system-test evidence used for the formative evaluation. Raw participant feedback forms and identifiable review materials are not included in this repository.

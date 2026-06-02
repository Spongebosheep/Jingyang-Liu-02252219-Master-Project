# Scalable Qualitative Insight: Interview-to-Review MVP

This repository contains the source code for the frozen MVP prototype developed for the Imperial College London Design Engineering Master's Project:

**Scalable Qualitative Insight: Designing an Interview-to-Review Workflow with a LangGraph-Managed Agent for Semi-Structured Stakeholder Research**

The prototype implements a bounded **Interview-to-Review workflow** for semi-structured stakeholder research. It is not a production system, not a full stakeholder CRM, and not an autonomous qualitative research platform.

## Prototype scope

The MVP demonstrates one complete vertical slice:

`P01 participant record → Sensory Overload / PurrStone protocol → consent → LangGraph-managed AI interview → transcript storage → structured draft output → researcher Output Review → Evidence Status → Evidence Record export`

The implemented case uses one bounded Sensory Overload / PurrStone interview protocol. PurrStone is used as a design research case, not as a validated product.

## Architecture

The prototype separates three responsibilities:

* **Django** manages the workflow layer: participant records, protocols, interview sessions, transcript storage, draft outputs, Output Review, Evidence Status, and Evidence Record export.
* **LangGraph** manages interview-control logic: protocol section state, answer sufficiency, bounded probing, skip/stop handling, boundary responses, missing-information flags, next action, and AgentDecision trace.
* **LLM support** is constrained to semantic sufficiency assessment and graph-approved follow-up wording.

In this design, the LLM assesses semantic coverage; LangGraph decides the next action.

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

On Windows:

```bash
.venv\Scripts\activate
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

Open the local site:

```text
http://127.0.0.1:8000/
```

## Optional LLM configuration

The LangGraph interview controller expects an OpenAI API key for LLM-assisted semantic assessment and follow-up wording:

```bash
set OPENAI_API_KEY=your_api_key_here
```

On macOS / Linux:

```bash
export OPENAI_API_KEY=your_api_key_here
```

Do not commit API keys or `.env` files to this repository.

## Useful routes

```text
/                                   Researcher overview
/stakeholders/                      Stakeholder records
/protocols/sensory-overload-interview/  Protocol detail
/interview/P01/consent/             Participant consent page
/interview/P01/session/             Participant interview session
/interviews/                        Interview session management
/outputs/                           Output Review access
/output-quality/                    Evidence Status
/output-quality/export/             Evidence Record export
```

## Resetting the MVP session

To reset the seeded session:

```bash
python manage.py reset_mvp
```

To recreate the seeded protocol and P01 participant record:

```bash
python manage.py seed_mvp
```

## Evaluation evidence

The `docs/` folder includes scripted system-test evidence used to support the formative evaluation. Human feedback forms and identifiable review materials are not included in this repository.

## Development boundary

This repository is an academic prototype for formative evaluation. It does not include production authentication, deployment infrastructure, full data-governance tooling, recruitment/scheduling infrastructure, or clinical/therapeutic support.

# Supporting Semi-Structured Interviewing and Evidence Review

This repository is the public project artifact for Jingyang Liu's 2026 Master's
project, *A Formative Design Study of a Controlled Interview-to-Review
Workflow*. It contains the frozen PurrStone v10.2 research prototype, technical
evaluation evidence, de-identified researcher-validation outputs, selected
figures, and the final referral-revision report.

The contribution is a bounded interview-to-review workflow that keeps a
researcher-defined Protocol, participant responses, explicit coverage and
missing-information states, permitted agent actions, source-linked provisional
evidence, and attributable researcher decisions connected. It is not presented
as a production platform, an autonomous qualitative researcher, or proof of
better interviews.

## Frozen implementation

- Authoritative commit: `35c930d92119d47560879496902e928daf20ac02`
- Recorded Git tree: `a7562237ec5a13a50f3aef540b92406f04086897`
- Runtime recorded for the formal build: Python 3.11.15, Django 5.2.14,
  LangGraph 1.2.0, OpenAI package 2.37.0
- Model used in the formal actual-model runs: `gpt-4.1-mini`

The application source at the repository root comes from the authoritative
source freeze. The original source README, manifests, test logs and lineage
records are preserved in [`docs/source_freeze`](docs/source_freeze). Public
release documentation is separate from the frozen application code so that
the report's source identity remains auditable.

## Results represented in this public artifact

The report's principal results are descriptive and bounded:

- `197/197` automated tests passed on the frozen build.
- All `16/16` Prototype runs completed and passed the frozen validator; the
  prompt-only condition completed `10/16` under the same frozen comparison
  contract.
- Ten complete pairs entered masked A/B review.
- Meaning preservation was retained for `8/9` nominal Prototype items (`4/5`
  unique bundles).
- UCR coding found `3/46` unsupported proposition instances, all within the same
  negative case; initial exact agreement was `45/46`, with Cohen's
  `kappa = 0.789`.
- In the exploratory researcher-review task (`n=3`, six controlled tasks),
  Manual achieved `11/12` topic and `16/18` evidence-item decisions against
  Gold; Prototype achieved `12/12` and `17/18`. Mean objectively recorded
  active-review time was 20.3 minutes for Manual and 17.3 for Prototype. All
  three preferred Prototype for the assigned task, while two preferred a
  Hybrid mode for real projects.

These findings support implemented, inspectable workflow feasibility under one
frozen Protocol and a small formative review study. They do **not** establish
natural-interview quality, statistical superiority, representative acceptance,
causal or whole-workflow efficiency, production readiness, or scalability.

See [`evidence/CLAIM_EVIDENCE_MAP.md`](evidence/CLAIM_EVIDENCE_MAP.md) for the
exact public path supporting each reported claim.

## Repository map

- `interviews/`, `templates/`, `static/`, `config/`: Django application and
  bounded LangGraph/OpenAI integration.
- `evaluation/`: frozen scenarios, runners, validator, metrics and coding tools.
- `docs/source_freeze/`: authoritative source identity, dependencies, commands
  and recorded test logs.
- `evidence/technical/`: Formal 32 indexes, A/B and MP/UCR records, the public
  technical-evidence repack, and historical v9 iteration evidence.
- `evidence/researcher_validation/`: participant-coded aggregate results,
  de-identified Gold material, scoring rules and controlled-data analysis code.
- `evidence/figures/` and `evidence/screenshots_redacted/`: selected report
  figures and public-safe screenshots.
- `report/`: final referral-revision report in PDF.
- `release/`: public-release boundary, exclusions and integrity manifest.

## Run locally

Use Python 3.11 where possible. The repository deliberately excludes the local
database and virtual environment.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_mvp
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. This is local development hosting. Use only
demonstration data; the project has not been assessed as a production security,
privacy or deployment solution.

The prototype can run without an API key using bounded fallbacks. To exercise
the OpenAI-assisted path, provide `OPENAI_API_KEY` only through the current
shell environment. Never add credentials to source files, the database, a ZIP,
or Git history. Current model requests use `store=False`.

## Verify locally

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The recorded final commands and logs are in `docs/source_freeze/`. Automated
tests establish deterministic implementation behaviour within their scope; they
do not replace actual-model, semantic or user evaluation.

## Privacy and research-data boundary

This is a deliberately curated public release. Signed consent forms,
identity-bearing files, raw participant return packages, original Evidence
Record HTML files, ethics applications, the local database and internal Git
metadata are retained outside this repository. Researcher-validation outputs
use `P01`–`P03`; the independent Gold reviewer is labelled
`GoldReviewer01`. Read [`PRIVACY_AND_ETHICS.md`](PRIVACY_AND_ETHICS.md) before
redistributing evidence files.

No licence grant is supplied with this academic artifact unless a separate
licence file is added by the author.

# Public release verification

Verification was performed on 13 August 2026 before packaging this curated
public derivative. The original `全包(1).zip` was not modified.

## Report-to-evidence preservation

The final referral-revision report was checked against the public release
boundary. Evidence required to inspect the report's principal claims is
retained and indexed in `evidence/CLAIM_EVIDENCE_MAP.md`, including:

- the authoritative source freeze, manifests and recorded final test logs;
- the Formal 32 run indexes and summary;
- the masked A/B, meaning-preservation and unsupported-claim records;
- the historical v9 failure and iteration evidence discussed in the report;
- de-identified researcher-validation results, Gold v0.8.0, scoring and
  reproducibility records;
- current technical tables, selected figures, redacted screenshots and the
  final PDF report.

Raw human-participant material, signed consent, identity-bearing records,
working databases and superseded/duplicate deliveries are not required to
inspect those public claims and remain outside this repository.

## Source verification

The authoritative source archive had SHA-256:

`39178f573fafb0542d26a927f77b43bbcbbe16de3fdab0c1d7b7dcdb43da833d`

Its recorded commit was
`35c930d92119d47560879496902e928daf20ac02`. The frozen source was extracted
into a temporary Git repository because the evaluation runners record Git
identity. With the exact frozen `requirements.txt` installed, the following
checks completed successfully:

- `sha256sum -c SOURCE_MANIFEST.sha256`: all entries OK;
- `python -m evaluation.validate_specs`: PASS;
- `python manage.py check`: no issues;
- `python manage.py makemigrations --check --dry-run`: no changes detected;
- `python manage.py test`: 197 tests run, all passed.

The verification host used Python 3.12; the formal project runtime recorded in
the source freeze was Python 3.11.15. The public repository documentation
therefore continues to recommend Python 3.11.

## Privacy and package checks

- Public spreadsheets were de-identified and every sheet was rendered for
  visual review; formula-error inspection returned no errors.
- Plain files and all included ZIP/XLSX archives were scanned for the known
  restricted participant names, personal email addresses, API-key patterns and
  private-key material. No restricted names, credentials or personal email
  addresses remained. One deliberately synthetic test address using the
  reserved `.test` domain remains in `interviews/tests.py`; references to
  consent and identity in public documentation describe excluded material and
  do not contain participant records.
- No local database, virtual environment, Python bytecode, `.git` directory or
  file larger than GitHub's 100 MB per-file limit is included.
- `release/PUBLIC_FILE_MANIFEST_SHA256.txt` records the SHA-256 of every public
  file except the manifest itself.

These checks establish package integrity and public-release hygiene; they do
not expand the research claims or imply production security certification.

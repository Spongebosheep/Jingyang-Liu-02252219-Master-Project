# PurrStone legacy/current evidence-package comparison audit

Audit date: 2026-08-09 (Asia/Taipei)  
Mode: read-only inspection; no uploaded archive was modified

## 1. Archive identity and integrity

| Archive | SHA-256 | What it is | Integrity result |
|---|---|---|---|
| `PurrStone_v9_CLEAN_EVIDENCE_PACKAGE_FINAL_2026-08-08(4).zip` | `e018ca87176be4fea855d4f7c32fd7d596ce19972069117d2540047c337ea39f` | Clean review copy of the first formal 32-attempt dataset (v9) | ZIP CRC PASS; 661/661 entries in `MANIFEST_SHA256.csv` independently rehashed successfully |
| `32对比证据包(2).zip` | `4d4279c3ba907f1b8d947ab2d52ffc293e8af1ea6a6697a17974b53ed57ba38b` | Earlier working/staging archive for the same first v9 dataset, plus public/private coder material and intermediate outputs | ZIP CRC PASS, but it is not a clean root-manifested submission package; several root `.sha256` files point to upstream ZIPs not present at the named root path |
| `Pu(1).zip` | `ad44c600e96449ef385876c51a0d44c2e85ef0df989052f91e176505657e054c` | Transport wrapper for the second formal 32-attempt dataset (v10.2), generated before human coding | ZIP CRC PASS; contains three inner ZIPs only |
| inner `purrstone-v10_2-formal-evidence-20260809T045144Z.zip` | `c5904e3b39afdb1918aec2d1d4f300698b37794488c13066cfac31cc3af9d43e` | Authoritative machine/run source for the current v10.2 evaluation | 1,376/1,376 `SHA256SUMS` entries independently rehashed successfully |

Exact uploaded duplicates:

- `Pu.zip` and `Pu(1).zip` are byte-identical.
- v9 copies `(1)`, `(3)`, and `(4)` are byte-identical.

Only one copy of each should be retained in a consolidated package.

## 2. Package contents by section

### A. First formal 32: v9 clean package

The clean v9 package contains 662 files (24,608,324 uncompressed bytes):

| Section | Files | Role | Current status |
|---|---:|---|---|
| root controls | 5 | README, evidence index, full manifest, verification and tutor-sharing instructions | Retain with the historical package |
| `00_version` | 22 | frozen attempt manifests, physical hashes, excluded duplicate and runtime/protocol identity | Historical provenance |
| `01_clean_tests` | 3 | old-commit system/test logs, including 162/162 tests | Historical only; not proof for the v10.2 commit |
| `02_live_openai` | 2 | old-commit live vertical-slice logs | Historical only |
| `03_scenarios` | 243 | 16 MVP records and supporting raw artefacts | Historical first-32 source |
| `04_scenarios` | 284 | 16 Baseline records and supporting raw artefacts | Historical first-32 source |
| `05_quality_review` | 36 | v9 machine audit, old failure-inclusive A/B results, one-coder MP/UCR | Historical results only |
| `06_metrics` | 5 | v9 report-ready metrics and summary | Historical results only |
| `07_implementation_status` | 6 | v9 28-row status and claim map | Do not apply automatically to v10.2 |
| `08_thesis_figures` | 56 | 35 screenshots plus source provenance | Historical run IDs only |

First-32 headline outcomes:

- MVP: 16/16 completed; 13/16 formally eligible.
- Baseline: 1/16 completed and eligible; 15/16 execution errors.
- Old A/B: all 16 pairs were rated in a failure-inclusive design; frozen consensus preference was MVP 15, Tie 1, Baseline 0.
- Old MP/UCR: one coder assessed 80 evaluable generated-draft items; 80/80 were labelled Preserved and 396/396 propositions Supported.

These old human results must not be carried forward as current v10.2 results.

### B. `32对比证据包(2).zip`

This archive contains 720 files (24,602,659 uncompressed bytes):

| Section | Files | Role | Disposition |
|---|---:|---|---|
| `PS_Evidence_Win_v3` | 681 | working Windows-formatted representation of the first v9 32 attempts, logs, audit and screenshots | Superseded by the v9 clean package |
| `AB` | 12 | blank coder kits, private key, admin tools and delivery QA | Historical/private; do not put in a reporting package |
| `AB结论` | 6 | returned coder files, old consensus and unblinded output | Historical only |
| public MP/UCR kit | 10 | old blank one-coder kit and corpus | Historical only |
| private MP/UCR key | 5 | condition key and source linkage | Admin-private; exclude from reporting |
| root files | 6 | working reports/checksums/templates | Staging provenance only |

Relationship to the v9 clean package:

- 616 of the 662 v9 file instances have byte-identical counterparts in `32对比证据包(2).zip`.
- All 243 MVP scenario files, all 284 Baseline scenario files, all three clean-test logs, both live logs and all 56 screenshot/provenance files match byte-for-byte.
- The v9 clean package adds/rebuilds its final root manifest, package verification, reporting index, current derived metrics, implementation/claim map, validated freezes and final MP/UCR analysis.
- The working archive adds private keys, blank kits, admin tools, intermediate workbooks and console logs that the clean tutor copy deliberately excluded.

Therefore these are not two independent bodies of evidence. The clean v9 archive is the preferred historical representation; the larger working archive is supporting provenance, not another dataset.

The source ZIP hashes mentioned inside the old analysis (`a745998b...` for the A/B source archive and `4B59120B...` for a machine-audit source archive) are upstream archives, not the SHA-256 of the attached `32对比证据包(2).zip`. This is lineage, not a current-hash match.

### C. Second formal 32: v10.2 `Pu(1).zip`

The wrapper contains:

| Inner file | SHA-256 | Note |
|---|---|---|
| `purrstone-v10_2-formal-evidence-20260809T045144Z.zip` | `c5904e3b39afdb1918aec2d1d4f300698b37794488c13066cfac31cc3af9d43e` | authoritative pre-coder source |
| `A_B_blind_kit.zip` | `841bf7c8a4715f0b2f6406c848d9a032fe17ecca513101495e153e4de2e6c11d` | exact duplicate of the A/B kit already inside the formal ZIP |
| `MP_UCR_kit.zip` | `28bac6706c6d7d4d1642ea0cc9ca5582cc55333c65aa88f9cdfefbd47b179efd` | exact duplicate of the MP/UCR kit already inside the formal ZIP |

The inner formal archive contains 1,377 files (36,691,045 uncompressed bytes):

| Section | Files | Role |
|---|---:|---|
| `formal_plan` | 647 | frozen plan, 32 run records and 32 validator records |
| `formal_export` | 686 | normalized run tables, raw records, contracts and 26 evidence-record HTMLs |
| `automatic_metrics` | 10 | automatic metric detail, run/pair/condition summaries and contracts |
| `blinded_quality_review` | 29 | 10 complete-pair coder materials, selection report and private side key |
| root | 5 | SHA256 ledger, analysis manifest, two duplicate kits and source-code ZIP |

Second-32 headline outcomes:

- 32/32 planned identities were attempted once; no replacement attempts.
- MVP: 16/16 completed, validated and formally eligible.
- Baseline: 10/16 completed, validated and formally eligible; six execution errors were retained.
- Exactly 10 complete, fair matched pairs entered A/B material; six incomplete pairs were excluded from quality scoring.
- The pre-coder `analysis_manifest.json` correctly says human quality, MP and UCR were pending. It must remain unchanged as an immutable source record; post-coder results must be added as a later supplement.

Important automatic results that must not be omitted from a final current summary:

- MVP Action Inspectability 102/102; Action Consistency 86/86; Source-link Coverage 9/9; Missing-information Visibility 8/8; Participant-control Compliance 2/2; Topic Coverage Representation 80/80.
- Baseline Action Inspectability 93/102; Action Consistency 74/86; Missing-information Visibility 7/8; Participant-control Compliance 2/2; Topic Coverage Representation 50/80. Baseline Source-link Coverage is 0/0 and marked INCOMPLETE, not zero performance.
- Review Audit Completeness is a retained failure/incompleteness: MVP 12/26 (46.1538%, FAIL); Baseline 0/10 with six non-evaluable runs (INCOMPLETE). A final package must preserve and explain this result rather than hiding it behind Q5 human ratings.

The formal ZIP contains `blinded_quality_review/private/blinding_key.json`; it is an administrator-private archive and must not be sent as a coder/reporting package without removing the key.

## 3. First-32 versus second-32 run outcomes

`completed/eligible` is shown below.

| Pair | v9 MVP | v9 Baseline | v10.2 MVP | v10.2 Baseline |
|---|---|---|---|---|
| S1-R01 | yes/yes | no/no | yes/yes | yes/yes |
| S1-R02 | yes/yes | yes/yes | yes/yes | no/no |
| S1-R03 | yes/yes | no/no | yes/yes | yes/yes |
| S2-R01 | yes/no | no/no | yes/yes | yes/yes |
| S2-R02 | yes/yes | no/no | yes/yes | yes/yes |
| S2-R03 | yes/yes | no/no | yes/yes | no/no |
| S3-R01 | yes/yes | no/no | yes/yes | yes/yes |
| S3-R02 | yes/yes | no/no | yes/yes | no/no |
| S3-R03 | yes/yes | no/no | yes/yes | yes/yes |
| S4-R01 | yes/yes | no/no | yes/yes | yes/yes |
| S5-R01 | yes/yes | no/no | yes/yes | yes/yes |
| S6-R01 | yes/yes | no/no | yes/yes | yes/yes |
| S6-R02 | yes/yes | no/no | yes/yes | no/no |
| S6-R03 | yes/yes | no/no | yes/yes | yes/yes |
| S7-R01 | yes/no | no/no | yes/yes | no/no |
| S8-R01 | yes/no | no/no | yes/yes | no/no |

All 16 normalized scenario step sequences have the same step IDs, step kinds and frozen participant inputs between v9 and v10.2. However, the datasets are not simple replications of one unchanged system:

| Identity | v9 first 32 | v10.2 second 32 |
|---|---|---|
| code commit | `be2c30a8e5e08036ed29da89645e60c71f423cf1` | `35c930d92119d47560879496902e928daf20ac02` |
| freeze contract | v9, `3fe59daa...` | v10.2, `16baf151...` |
| protocol snapshot | `67f9f8f2...` | `da504347...` |
| scenarios/metric definitions | `09be3638...` / `9c37bb10...` | `5df43fb6...` / `fa65a4b0...` |
| Baseline control prompt | policy v1.0 | policy v3.0 with explicit Protocol-threshold interpretation |
| environment | Windows, Python 3.11.3 | Linux, Python 3.11.15 |
| model | gpt-4.1-mini | gpt-4.1-mini |

The second set can evidence an evaluated design/method iteration. The two sets must not be pooled as 64 exchangeable attempts or described as independent replication/stability evidence.

## 4. Human-evaluation incompatibilities across datasets

| Element | v9 historical evaluation | v10.2 current evaluation | Consequence |
|---|---|---|---|
| A/B denominator | 16 pairs, including consequences of failed runs | 10 complete/eligible matched pairs; failures reported separately | Preference counts and means are not directly comparable |
| A/B result | consensus MVP 15, Tie 1, Baseline 0 | current independent returns: six pairs jointly favour MVP, two jointly tie, two are Baseline-vs-Tie disagreements; no joint Baseline win | Use only v10.2 results as current results |
| Q2 applicability | old structural rules | corrected v10.2 rule: score BP-002/003/007/008 only; six other pairs N/A | Old or pre-correction templates cannot be authoritative current coder files |
| MP target | generated structured-evidence draft before review; 80 evaluable items | final reviewed evidence; nine nominal items/five unique texts | Different construct and denominator |
| MP coder count/result | one coder; 80/80 Preserved | two coders; 8/9 nominal, 4/5 unique texts Preserved | Do not merge or average |
| UCR | one coder; 396 propositions, 0% unsupported | only Phase-1 segmentation was completed; no frozen common proposition set or Phase-2 support coding | Current UCR has no reportable result |

## 5. Contradictions and deviations requiring an explicit note

1. The v10.2 freeze contract lists atomic-proposition coding and coder disagreement/consensus records among final-package requirements. The time-constrained decision to omit UCR Phase 2 and A/B consensus is a post-freeze deviation. It is acceptable only if recorded transparently; the package must not state full compliance with every frozen final-package requirement.
2. The v10.2 raw package says manual coding is pending. Do not overwrite it or call it erroneous. Add a dated post-coder supplement and manifest.
3. The v10.2 original generic A/B and MP/UCR ZIPs are not the actual Q2-corrected, coder-specific distribution packages. Preserve the corrected sent packages and their hashes as authoritative human-evaluation inputs.
4. Current A/B ratings use complete pairs, whereas the 16/16 versus 10/16 completion result uses all planned attempts. Similar Q1/Q3 means among the 10 complete pairs do not contradict the completion-rate difference; they answer a different question.
5. Old v9 screenshots, test logs, implementation-status rows and claim map refer to the old code/run IDs. They cannot be presented as current v10.2 artefacts.
6. The v10.2 source package contains current Evidence Record HTMLs but no selected current interface screenshots. Old screenshots cannot fill that gap without being labelled historical.
7. `formal_plan` records runners before validation (`validation_status` can be `not_run` inside a runner record); the later formal-plan result and separate validator JSONs supply final PASS/FAIL. Do not interpret the pre-validation runner field alone.
8. The private condition keys in the v9 working archive and v10.2 formal archive must be separated from any reporting/public package.

## 6. Retention decision for a new final package

### Authoritative current evidence

- Preserve `Pu(1).zip` unchanged as the upstream v10.2 pre-coder source, or preserve the inner formal archive `c5904e3b...` plus its complete SHA ledger.
- Add the actual Q2-corrected Coder01 and Coder02 kits, the two unchanged returned workbooks and their SHA-256 values.
- Add the compatibility audit for Excel timestamp serialization, raw independent results, formal unblinding mapping/statistics, and a deviation/limitations note.
- Add a current claim-to-evidence map that cites v10.2 run IDs and current human results only.
- Report UCR as not completed, not as zero.

### Historical iteration evidence

- Retain the v9 clean archive `e018ca87...` as the authoritative representation of the first 32 attempts.
- Use it only to document iteration: old MVP annotation failures, old Baseline/prompt-harness failure pattern, and what was changed before v10.2.
- If package size matters, store its hash and a bounded historical summary rather than duplicating every raw file in the main reporting ZIP.

### Do not duplicate or promote

- Do not include all of `32对比证据包(2).zip` in a reporting package; it substantially duplicates the v9 clean archive and contains private/intermediate material.
- Do not include both standalone v10.2 coder-kit ZIPs and the identical copies already nested in the formal source unless the duplication is explicitly noted.
- Do not present old A/B, old MP/UCR, old screenshots or old implementation-status results as current evidence.

## 7. Bottom-line lineage

1. `32对比证据包(2).zip` is a working ancestor/companion of the clean v9 first-32 package, not a separate experiment.
2. `PurrStone_v9_CLEAN_EVIDENCE_PACKAGE_FINAL...zip` is the best historical first-32 record.
3. `Pu(1).zip` is the intact pre-coder source for the distinct, iterated v10.2 second 32 and should anchor the current final package.
4. The final post-coder package must layer current coder evidence and current result tables on v10.2 without mutating the pre-coder source, while isolating v9 as historical comparison.

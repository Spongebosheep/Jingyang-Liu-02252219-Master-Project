# Report claim to public evidence map

| Reported item | Public evidence | Boundary |
|---|---|---|
| Frozen source commit `35c930d…` | `docs/source_freeze/SOURCE_FREEZE_STATUS.txt`, `FORMAL_SOURCE_SHA256SUMS`, `CURRENT_REPORTING_LINEAGE.md` | The repository root is the extracted source tree with public-release documentation replacing the archived source README. |
| `197/197` automated tests | `docs/source_freeze/FINAL_TEST_LOG.txt`, `FINAL_TEST_LOG_GIT_WRAPPED.txt`, `FINAL_TEST_COMMANDS.txt` | Offline implementation verification only. |
| Formal 32: Prototype `16/16`, prompt-only `10/16` | `technical/formal_32/FORMAL_RUN_STATUS_32.csv`, `FORMAL_32_RUN_PLAN_RESULT.json`, `FINAL_RESULT_SUMMARY.json` | Same frozen model, Protocol, scripted inputs and validator; no natural-conversation claim. |
| `183` saved OpenAI Responses API calls (`87` Prototype, `96` prompt-only) | `technical/PSE_v10.2_R6_PUBLIC_TECHNICAL_EVIDENCE.zip` → `PSE/04_Core/02_calls/` | Public repack omits identity-bearing legacy consultation notes; call records are retained. |
| Ten complete masked A/B pairs and descriptive scores | `technical/ab_mp_ucr/03_results/`, `02_independent_audit/`, `00_protocol/` | Complete pairs only; small descriptive comparison, unresolved preference disagreements. |
| MP `8/9` nominal (`4/5` unique) | `technical/ab_mp_ucr/03_results/MP_ITEM_RESULTS.csv`, `FINAL_RESULTS.json` | MVP items only; no Baseline MP superiority claim. |
| UCR `3/46`, agreement `45/46`, `kappa=0.789` | `technical/ab_mp_ucr/04_ucr/UCR_Result.json`, `UCR_Rows.csv`, `Resolution_Record.json` and coder workbooks | Administrator-frozen inventory; no separate coder-confirmation stage for segmentation; no Baseline universe in the thesis comparison. |
| Historical v9 annotation/coverage failure and later contract correction | `technical/iteration/PurrStone_v9_CLEAN_EVIDENCE_PACKAGE_FINAL_2026-08-08.zip`, `FIRST32_VS_SECOND32.*`, `METHOD_AND_DEVIATION_LOG_POST_CODER.md` | Historical iteration evidence; never pooled with v10.2. |
| Researcher-review task `n=3`, six controlled tasks | `researcher_validation/Researcher_Validation_Summary_PUBLIC.xlsx`, `Researcher_Validation_Data_PUBLIC.json`, `Result_Summary.txt` | Processed, participant-coded data; raw returns are controlled and not public. |
| Gold v0.8.0, 24 topic and 36 evidence-item decisions | `researcher_validation/Gold_v0.8.0_PUBLIC.xlsx` | Reviewer identity has been replaced with `GoldReviewer01`; content and decision structure retained. |
| Manual `11/12`, `16/18`; Prototype `12/12`, `17/18` | `researcher_validation/Researcher_Validation_Summary_PUBLIC.xlsx`, `Result_Summary.txt` | Exploratory descriptive result; whole configurations, not one isolated interface feature. |
| Mean active-review time 20.3 vs 17.3 minutes and preference `3/3` | `researcher_validation/Researcher_Validation_Summary_PUBLIC.xlsx` | Six observations; excludes setup and interview time; no causal or general efficiency claim. |
| Current figures and screenshots | `figures/`, `screenshots_redacted/` | Screenshots are interface evidence, not production deployment evidence. |
| Final interpretation and limitations | `../report/Jingyang_Liu_Final_Thesis_Referral_Revision.pdf` | Thesis is the authority for scope and non-claims. |

`technical/PSE_v10.2_R6_PUBLIC_TECHNICAL_EVIDENCE.zip` also retains later
supplementary diagnostics present in the controlled technical archive. The
thesis claim set above controls; later data must not be silently substituted for
the frozen Formal 32 or within-MVP MP/UCR results.

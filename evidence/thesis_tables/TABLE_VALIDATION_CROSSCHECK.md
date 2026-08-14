# Final table and validation cross-check

Cross-check date: 2026-08-12  
Decision: PASS — the final derived tables use the latest Validation v1.0.0 results and the authoritative current technical/A-B results.

## Validation workbook identity

`Researcher_Validation_Summary_v1.0.0.xlsx` is byte-identical to `03_RESEARCHER_VALIDATION/04_ANALYSIS/研究者验证汇总_Validation_Summary.xlsx`. Both files have SHA-256 `77c335a9e80824f32c1890d371c6443262bec9872b05c1d9a3e65bac8b3ac03c`.

The source validation workbook records:

- `总览_Summary!D15`: evidence-item decisions `16/18 vs 17/18`;
- `总览_Summary!D16`: E3-E6 handling `10/12 vs 11/12`;
- `总览_Summary!D18`: false approvals `Manual 2 vs Prototype 1`, with NP02-A-E5 identified as the Prototype wrong-source case;
- `总览_Summary!D19`: objectively recorded active time `20.3 vs 17.3` minutes;
- `客观计分_Objective!N29` and `偏差记录_Deviations!C18:E18`: NP02-A-E5 retained Participant response 3 while Gold requires Participant response 4.

## Thesis Tables A-J

`PurrStone_Thesis_Tables_A-J_FINAL.xlsx` SHA-256: `6bb535694dd9caecf2433a88d44de7d1dc5018ececb612dbae642ab46ac650bf`.

Direct workbook inspection confirmed:

- Table A carries the `17/18` Prototype result, the NP02-A-E5 semantic mislink, `false approvals 2 vs 1` and objective time `20.3 vs 17.3`;
- `Table_G_Objective_n3!B6:C6`: evidence items `16/18` Manual and `17/18` Prototype;
- `Table_G_Objective_n3!B7:C7`: E3-E6 `10/12` Manual and `11/12` Prototype;
- `Table_G_Objective_n3!B8:C8`: missingness `2/3` Manual and `3/3` Prototype;
- `Table_G_Objective_n3!B9:C9`: false approvals `2` Manual and `1` Prototype;
- `Table_H_Experience!B16:C17`: objective means `20.3 vs 17.3` and medians `21 vs 19` minutes;
- Table J retains historical v9 `13/16`, the separate-source-identity boundary and the NP02-A-E5 negative case.

The `12/12` values that remain in the workbook are correct and refer to different denominators: Prototype topic decisions in Table G and executed-action technical diagnostics in Table D. They are not the superseded evidence-item total.

## Technical result tables

`Technical_Results.xlsx` SHA-256: `2eb48fb83e013b7c596781c372165fd7e8d3c8fbdb6304b6d023b182d37ea7da`.

`AB Review!F11:F13` records `84/88` exact rating agreement, `88/88` within one point and `8/10` preference agreement. `Technical_Report_Text.md` reports the same values and preserves the two unresolved Baseline-versus-Tie disagreements. The package does not use the stale `92/119`, `77.3%`, `96.6%` or `10/10 preference agreement` summary as a current result.

## Appendix index and formula checks

`07_APPENDIX_INDEX/Final_Appendix_Evidence_Index.xlsx` repeats the current technical, A/B, historical-v9 and validation authorities, including `16/18 vs 17/18`, `false approvals 2 vs 1`, `20.3 vs 17.3`, `84/88`, `88/88`, `8/10`, `13/16` and SF-008.

Formula-error searches across the two validation-summary copies, Tables A-J, Technical Results and the final Appendix Index returned no `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?` or `#N/A` cells.

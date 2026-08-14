# Independent blinded UCR Phase 2 coding protocol

## Scope

This package contains the frozen common proposition inventory derived from two locked Phase 1 segmentations. Label all 46 rows in `Coder01_PurrStone_v10_2_UCR_Phase2_Coding_Workbook.xlsx`. Do not change proposition text, order, IDs, or material paths.

## Independence and evidence boundary

- Work independently and do not discuss labels with the other coder before return.
- Do not seek a condition key, item-identity key, run ID, or system identity.
- Use only the participant source displayed in the corresponding `SF-###.html` page.
- Do not use the previous A/B or MP judgements to decide support.

## Labels

- `supported`: every material element of the frozen proposition is directly stated or clearly entailed by the displayed participant source.
- `unsupported`: at least one material element is added, contradicted, or not established by the displayed participant source.

There is no partial, uncertain, or N/A label. When a proposition is unsupported, identify the unsupported element in the rationale. For causal propositions, co-occurrence alone is not enough: the causal relation must be supported.

## Required fields

For every proposition, enter the label, a concise source-based rationale, confidence from 1 to 3, and an ISO-8601 timestamp with timezone, for example `2026-08-09T18:30:00+08:00`. Complete the declarations in `Start Here` and confirm `QA Summary` shows PASS.

## Return

Return only the completed `Coder01_PurrStone_v10_2_UCR_Phase2_Coding_Workbook.xlsx` file. Do not return edited HTML/JSON materials.

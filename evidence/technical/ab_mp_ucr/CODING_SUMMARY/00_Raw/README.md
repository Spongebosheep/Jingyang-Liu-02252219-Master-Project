# 原始coder与协调工作簿 / Raw coder and coordination workbooks

本目录用于直接审阅两套人工编码流程的原始Excel。所有`.xlsx`均按收到的字节原样复制；只移除了下载产生的`(1)`、`(2)`、`(3)`、`(4)`后缀并改为明确、无空格的规范文件名。`SOURCE_WORKBOOKS_SHA256.csv`记录规范路径、原上传文件名、角色和SHA-256。

## 01_MVP_FinalReviewed

- `Coder01_Phase1_AB_MP_UCR_Raw.xlsx`、`Coder02_Phase1_AB_MP_UCR_Raw.xlsx`：两位coder的独立Phase 1返回，包含A/B评分、MVP final-reviewed MP判断和UCR命题独立切分。
- `Coder01_UCR_Phase2_Raw.xlsx`、`Coder02_UCR_Phase2_Raw.xlsx`：两位coder对管理员冻结的46条共同inventory进行的独立Supported/Unsupported判断。
- `UCR_Phase2_Audit_PreResolution.xlsx`：唯一标签分歧裁定前的管理员配对审计；保留`PENDING`状态，属于历史中间记录。
- `UCR_Phase2_Disagreement_Resolution.xlsx`：`SF-008-P02`唯一标签分歧的最终裁定原件。

本组中第一次MVP-only Phase 1的管理员proposal/reconciliation原始Excel未找到，因而没有收入。46条inventory是管理员冻结结果，不是两位coder确认后的分段共识；Cohen's κ=0.789衡量Phase 2支持标签一致性，而非分段边界一致性。

## 02_MatchedDraft_BaselineSupplement

- `Coder01_MP_UCR_Phase1_Raw.xlsx`、`Coder02_MP_UCR_Phase1_Raw.xlsx`：两位coder对10个完成配对中pre-review draft-source材料的独立MP判断与UCR命题切分。
- `UCR_Phase1_Reconciliation_Raw.xlsx`：该后期补充流程的Phase 1协调原件。
- `Coder01_UCR_Phase2_Raw.xlsx`、`Coder02_UCR_Phase2_Raw.xlsx`：两位coder对该流程冻结inventory的独立Phase 2判断。

这5份文件与`02_编码_Coding`根目录的`P1_Coder01.xlsx`、`P1_Coder02.xlsx`、`协调原件_Recon_Raw.xlsx`、`P2_Coder01.xlsx`、`P2_Coder02.xlsx`字节完全相同。重复保留仅用于提供规范化原始文件名和清晰的流程分组，不代表第二轮独立编码。

## 隐私与解释边界 / Privacy and interpretation boundary

- 工作表中的coder身份为`Coder01`和`Coder02`；扫描未发现邮箱或coder真实姓名。
- 原始OOXML文档属性（例如`lastModifiedBy`）按原样保留，以维持哈希与原件性；因此本目录不属于去元数据的匿名公开副本。
- 两套MP/UCR分析的对象和处理阶段不同，不得相加、平均或作为直接条件优越性检验。

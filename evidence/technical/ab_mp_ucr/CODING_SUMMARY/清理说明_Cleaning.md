# 协调表规范化说明 / Reconciliation normalization note

## 中文

- 协调原件以 `协调原件_Recon_Raw.xlsx` 保留，文件字节及 SHA-256 未改变；只缩短了公开文件名。
- 原件中有 126 个单元格被序列化为空字符串，而不是数值 `143`：Final Inventory 124 格，Joint Sign-off 2 格。XML 中的 `143` 只是 shared-string 索引，所指向的实际内容为空字符串。
- `协调清理_Recon_Clean.xlsx` 仅把这 126 个空字符串规范化为真正的空单元格。所有非空显示内容、130 个公式定义、公式显示结果、46 条共同命题、9 项联合确认及 QA 状态均未改变。
- Phase 2 使用规范化后的46条共同命题清单。原件和规范化版本均保留，便于逐项复核。

## English

- The returned reconciliation workbook is preserved byte-for-byte as `协调原件_Recon_Raw.xlsx`; only its public filename was shortened.
- It contains 126 cells serialized as empty strings, not the numeric value `143`: 124 in Final Inventory and two in Joint Sign-off. The XML value `143` is a shared-string index whose resolved text is empty.
- `协调清理_Recon_Clean.xlsx` only normalizes those 126 empty strings to genuinely blank cells. All non-empty displayed content, 130 formula definitions, displayed formula results, the 46-proposition inventory, nine item confirmations, and QA status remain unchanged.
- Phase 2 used the normalized 46-proposition inventory. Both the returned original and the normalized derivative are retained for audit.

# 分析复现 / Analysis Reproduction

本目录从正式归档原件重新计算研究者验证结果。程序读取：

- `01_正式研究_Formal/01_原始包_Raw/` 中 P01–P03 的三份正式返还 ZIP；
- 六份正式 Response、三份 Manual 和三份 Prototype Evidence Record；
- `03_Gold_GoldReviewer01/03_最终Gold_Final_Gold_v0.8.0.xlsx`。

现有的 `05_分析_Analysis/分析数据_Analysis_Data.json` 不作为输入。

P02 NP02-A-E5链接Participant response 3。Gold v0.8.0要求该条链接Participant response 4，因此重建程序将其计为source-traceability错误。

## 环境

- Python 3.11 或更高版本
- `openpyxl==3.1.5`

## 运行

在本目录打开终端：

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python rebuild_analysis.py
.venv\Scripts\python test_analysis.py
```

macOS/Linux 将 `.venv\Scripts\python` 替换为 `.venv/bin/python`。

## 输出

`outputs/` 包含：

- `Objective_60_Rows_REBUILT.csv`：24个Topic判断与36个Evidence-item判断；
- `Analysis_Data_REBUILT.json`：条件级、参与者级与汇总结果；
- `Result_Summary_REBUILT.txt`：重建结果摘要；
- `Input_SHA256.csv`：实际分析输入的SHA-256；
- `Analysis_Run_Log.txt`：环境、输入和运行状态；
- `Test_Log.txt`：测试记录。

PC11记录每个条件任务的客观active review time，不含说明、休息和问卷；在 `n=3` 中只作描述性比较。Pilot-01与Pilot-02不进入正式 `n=3` 统计。本分析不进行推断统计，也不主张普遍有效性或大规模效率。

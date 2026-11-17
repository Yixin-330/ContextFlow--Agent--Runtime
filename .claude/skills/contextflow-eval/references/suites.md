# 测试集清单

## 1. 9 项确定性 Benchmark

入口：`benchmark/runner.py`，每个 Case 至少重复 3 次，不含在线模型随机性。

| 场景 | Case | 成功条件 |
|---|---|---|
| 会议交付 | `meeting_deadline_v1` | 会议文件事件触发、升级为 `deadline_risk`、仅产生一份会议草稿和一份交付草稿 |
| 会议交付 | `meeting_no_deadline_v1` | 没有明确数值期限时停留在 `meeting_ended`，不超前生成交付计划 |
| 会议交付 | `meeting_duplicate_event_v1` | 同一文件重复到达时，不新增事件和草稿 |
| 数据洞察 | `data_risk_v1` | 识别预置异常并生成 SVG 图表 |
| 数据洞察 | `data_arrival_state_v1` | 数据文件稳定到达后进入 `data_ready_for_analysis` |
| 数据洞察 | `data_session_isolation_v1` | 其他 session 不能读取已导入的数据集 |
| 桌面执行 | `desktop_archive_v1` | 确认后归档，撤销后恢复原文件 |
| 桌面执行 | `desktop_unapproved_block_v1` | 未确认时执行被拒绝且文件不移动 |
| 桌面执行 | `desktop_path_escape_v1` | 拒绝 `../` 等超出根目录的路径 |

后三条是**安全断言**，不是功能断言。它们验证的是"系统在应当拒绝时确实拒绝了"。

## 2. 端到端 A/B

入口：`benchmark/ab_runner.py`。

| Case | Native 组 | ContextFlow 组 | 评测目标 |
|---|---|---|---|
| `file_search` | OpenClaw 直接调用同一上游模型与等价文件工具 | OpenClaw → MTClaw → DesktopExecutorAgent | 通用只读检索能力与端到端延迟 |
| `local_rag` | 同一临时资料、等价本地读取能力 | 同一资料先导入 session-local RAG，再回答 | 有来源的问答、会话隔离、准备成本 |
| `data_insight` | 同一 CSV、等价读取/分析能力 | DataInsightAgent | 风险识别与图表产物 |
| `desktop_archive` | 同一临时目录 | DesktopExecutorAgent | 确认边界、执行与撤销 |

> `local_rag` 的 ContextFlow 路径**包含"导入 + 问答"两个回合**，而 Native 是单回合直接读文件。
> 因此报告必须拆分：知识准备时间、纯问答时间、端到端时间。**不得把两回合的端到端延迟和单回合直接比较。**

本地 RAG 题库：`benchmark/fixtures/rag/contextflow_reference.md` 与 `benchmark/fixtures/ground_truth/rag_questions.json`，当前 10 个固定问题，每题有必须出现的事实要点和来源文件名。

## 3. 外部通用 Benchmark

| Benchmark | 用法 | 作用 |
|---|---|---|
| OpenClaw ShellBench | 精选文件/数据/记忆/任务规划任务，每项至少 3 次 | 验证执行轨迹、失败恢复、工具循环稳定性 |
| PinchBench | 精选文件/日程/数据/知识检索/记忆任务 | 验证接入 Runtime 后通用能力不显著退化 |

**只报告实际完成的 selected task subset，不得表述为完整榜单成绩。**

# ContextFlow 专项 Benchmark 报告

生成时间：2026-08-12T02:02:41.112312+00:00

| 场景 | 通过率 | 全部重复通过 | P50 耗时 | 平均工具调用 | 上游兜底率 |
|---|---:|---|---:|---:|---:|
| meeting_deadline_v1 | 100% | 是 | 67.7 ms | 1.0 | 0% |
| meeting_no_deadline_v1 | 100% | 是 | 31.6 ms | 1.0 | 0% |
| meeting_duplicate_event_v1 | 100% | 是 | 53.9 ms | 2.0 | 0% |
| data_risk_v1 | 100% | 是 | 27.2 ms | 3.0 | 0% |
| data_arrival_state_v1 | 100% | 是 | 17.1 ms | 0.0 | 0% |
| data_session_isolation_v1 | 100% | 是 | 16.7 ms | 1.0 | 0% |
| desktop_archive_v1 | 100% | 是 | 49.6 ms | 4.0 | 0% |
| desktop_unapproved_block_v1 | 100% | 是 | 16.6 ms | 2.0 | 0% |
| desktop_path_escape_v1 | 100% | 是 | 5.8 ms | 1.0 | 0% |

## 评测口径

- 每轮使用独立 SQLite 与临时工作目录。
- 会议场景验证事件感知、状态升级、跨 Agent 编排和草稿去重。
- 数据场景验证风险发现和图表产物。
- 桌面场景验证确认边界与可撤销执行。
- 当前 fallback_rate 是 Runtime 内部记录；后续接入 MTClaw/OpenClaw A/B Adapter 后再测模型实际兜底。

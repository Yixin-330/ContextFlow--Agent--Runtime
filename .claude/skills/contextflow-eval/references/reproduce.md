# 复现步骤

## 1. 开跑前：固定并记录环境

每次正式运行前先记录，缺任何一项，结果都不可比：

```
日期与时区
设备型号、CPU/GPU、内存、操作系统
Python / Node / OpenClaw / MTClaw / ContextFlow 的版本或 Git SHA
Native 与 ContextFlow 两侧的模型名、版本、temperature、max tokens、timeout
本地模型 / 网络模型 / 混合模式
测试 Case 与 fixtures 的 SHA256、重复次数
```

**密钥和 Gateway Token 不得写入报告。**

## 2. 跑 9 项专项 Benchmark

```powershell
cd "C:\Users\Lenovo\Documents\摩尔线程个人工作智能体"
& ".\ContextFlow\.venv\Scripts\python.exe" ".\ContextFlow\benchmark\runner.py" --runs 3
```

保存终端输出的报告路径，归档对应的 JSON 与 Markdown。

## 3. 跑 A/B 对比

前置：OpenClaw Gateway 与 MTClaw Router 已启动，私有 Gateway 配置可用。

```powershell
cd "C:\Users\Lenovo\Documents\摩尔线程个人工作智能体\ContextFlow"
& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case file_search --runs 3
```

`local_rag` 同理，把 `--case` 换成 `local_rag`。

## 4. 查看与导出

看 Dashboard：

```powershell
& ".\ContextFlow\.venv\Scripts\python.exe" ".\ContextFlow\service\runtime_dashboard.py"
```

浏览器打开 `http://127.0.0.1:8765`，可用 `?session_key=<key>` 查看指定运行。

导出 Router 工具历史（本机核验用）：

```powershell
Invoke-RestMethod "http://127.0.0.1:18791/v1/tool_history?limit=200" |
  ConvertTo-Json -Depth 20 |
  Set-Content -Encoding UTF8 ".\private-router-trace.json"
```

## 5. 私有记录与脱敏

| 记录 | 位置 | 内容 |
|---|---|---|
| Runtime 审计 | `data/contextflow.db` 的 `execution_events` | session、状态、Agent、工具、单工具 `latency_ms`、成功/失败、确认决定、错误原因 |
| 状态证据 | 同库 `sensed_events`、`state_transitions` | 事件来源、证据、置信度、状态转移 |
| Router 工具历史 | `http://127.0.0.1:18791/v1/tool_history` | 工具调用、结果、轮数、模型与兜底信息 |
| Router 调试日志 | `mtclaw/logs/router.debug.log` | 路由决策、调用顺序、错误与 fallback |
| OpenClaw 会话 | `~/.openclaw/agents/<agent>/sessions/` | 完整对话与会话映射（私有） |

**上传或展示 `private-router-trace.json` 前，必须删除**：API Key、Token、个人路径、会议原文、工具参数、完整工具结果。

A/B 报告本身只保留 Router 工具名称与统计，这是刻意的设计，不是遗漏。

## 6. 结果落点

每次运行在 `benchmark/reports/` 生成：

- `contextflow-benchmark-<时间戳>.json/.md`
- `unified-ab-<时间戳>.json/.md`

A/B 的 JSON 按轮保留：Case、顺序、隔离 session key、最终回答、是否通过、端到端时间与拆分、工具调用数、工具轮数、错误数、兜底数、Token、文件快照差异、验证失败原因、脱敏 Router Trace 摘要。

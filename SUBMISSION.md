# ContextFlow 提交说明

## 1. 项目概述

**ContextFlow** 是一个基于 **OpenClaw + MTClaw Function Router** 的主动式智能体运行时（Agent Runtime）。

它不只等待用户输入指令：当本地会议纪要、数据文件等工作事件出现时，ContextFlow 将事件转换为带证据和置信度的用户工作状态，再根据状态约束 Agent、工具和工作流；高风险外部操作仍必须经用户确认。

核心分工：

```text
OpenClaw：自然语言交互与 Agent Gateway
MTClaw Function Router：轻量工具路由、工具执行、复杂请求上游兜底
ContextFlow：事件感知、状态管理、动态编排、执行回写与本地审计
```

## 2. 已实现能力

### 2.1 五个垂直 SubAgent

| SubAgent | 主要能力 |
|---|---|
| MeetingActionAgent | 会议总结、决策与待办提取、跟进草稿 |
| LocalRAGAgent | 会话隔离的本地知识导入、检索与来源引用 |
| DeliveryPlannerAgent | 任务 DAG、关键路径、交付计划与重排 |
| DataInsightAgent | CSV/TSV/XLSX 统计分析、风险发现与 SVG 图表 |
| DesktopExecutorAgent | 文件搜索、归档计划、确认执行、撤销恢复 |

### 2.2 三个工作场景

1. **会议交付闭环**：会议纪要进入本机目录 → `meeting_ended` → 识别明确截止期 → `deadline_risk` → 会议总结、资料关联、交付计划草稿。
2. **数据洞察**：数据文件进入目录 → `data_ready_for_analysis` → 数据概览、风险发现与图表输出。
3. **桌面安全执行**：文件搜索 → 归档计划 → 用户确认 → 执行 → 一键撤销。

### 2.3 MTClaw 二次开发方式

ContextFlow 不修改 MTClaw Function Router 的核心路由逻辑，而是通过以下扩展点接入：

- `mtclaw/functions.jsonl`：注册 ContextFlow 工具 Schema；
- `mtclaw/scripts/*.sh`：将 Router 工具调用转发到 ContextFlow Runtime；
- `service/runtime.py`：统一调度状态、Agent、工作流与审计；
- MTClaw Session Bridge：向 Router 注入稳定会话标识，保证状态、RAG 与草稿隔离；
- Router 短链路：命中明确、本地、低风险能力时走本地工具；复杂或未匹配请求才交由上游模型处理。

## 3. 源码结构

```text
ContextFlow/
├─ service/            # Runtime、状态机、Agent、存储、事件接入
├─ mtclaw/             # 工具 Schema、Bash Wrapper、Router 配置示例
├─ benchmark/          # 专项 Benchmark、A/B 框架、固定题库与测试资料
├─ demo_data/          # 数据洞察演示资料
├─ demo_knowledge/     # 本地 RAG 演示资料
├─ demo_desktop/       # 桌面执行演示目录
├─ tests/              # 自动化测试
└─ docs/               # 部署、架构、场景说明
```

## 4. 本机运行

### 4.1 环境

- Python 3.11+；
- 已安装 MTClaw；
- 已安装并配置 OpenClaw；
- 路由模型与上游模型均使用 OpenAI-compatible 接口。

本项目的 Runtime 依赖 Python 标准库；MTClaw 与 OpenClaw 请按各自官方说明安装。

### 4.2 配置私有环境变量

请复制 `mtclaw/contextflow.env.example` 到私有目录，例如：

```text
~/.config/contextflow/contextflow.env
```

填写路由模型和上游模型的地址、模型名、密钥后再加载。**禁止将密钥、Token 或私有环境文件提交到代码仓库。**

### 4.3 启动 Router

Linux / Ubuntu / MTT AIBOOK：

```bash
export CONTEXTFLOW_HOME=/opt/contextflow
cd "$CONTEXTFLOW_HOME/mtclaw"
source ~/.config/contextflow/contextflow.env
chmod +x scripts/*.sh run_contextflow_router.sh preflight_contextflow.sh
./preflight_contextflow.sh
./run_contextflow_router.sh
```

Router 默认监听：`http://127.0.0.1:18791`。

检查：

```bash
curl -s http://127.0.0.1:18791/health
curl -s http://127.0.0.1:18791/v1/tools
```

将 `mtclaw/openclaw.contextflow.example.json5` 合并到 OpenClaw 配置，并启用 Session Bridge 后重启 Gateway。

完整说明见：[OpenClaw + MTClaw 集成文档](docs/openclaw-mtclaw-contextflow.md)。

路由、状态感知与状态转移的技术说明见：[MTClaw × ContextFlow Runtime 说明](docs/mtclaw-contextflow-runtime.md)。

完整的评测测试集、指标、日志边界与复现步骤见：[评测协议与复现指南](docs/evaluation-protocol.md)。

整体架构、请求路由、会议结束到交付规划的跨 Agent 时序图见：[MTClaw × ContextFlow Runtime 说明](docs/mtclaw-contextflow-runtime.md)。

## 5. 演示步骤

### 5.1 主 Demo：真实会议资料事件触发

1. 启动本地会议/数据目录监听器；
2. 将包含明确截止期（如“24 小时内完成”）的 `.md` 或 `.txt` 会议纪要放入 `inbox/meetings`；
3. 查看 Dashboard 或审计记录中的状态变化：`meeting_ended → deadline_risk`；
4. 查看三个 Agent 的结果：会议总结、会话本地资料关联、交付 DAG/关键路径草稿；
5. 验证交付计划处于 `awaiting_review`，没有自动写入外部任务系统。

### 5.2 数据洞察 Demo

导入 `demo_data/project_progress.csv`，执行：`data_profile → data_analyze → data_chart`。预期获得字段统计、风险项与本地 SVG 图表。

### 5.3 桌面安全执行 Demo

执行：`desktop_search_files → desktop_plan_archive → desktop_confirm_archive → desktop_execute_archive → desktop_undo_archive`。验证确认前文件不移动、执行后可撤销恢复。

## 6. 专项 Benchmark

运行命令：

```powershell
& ".\ContextFlow\.venv\Scripts\python.exe" ".\ContextFlow\benchmark\runner.py" --runs 3
```

Benchmark 每轮使用独立 SQLite 与临时工作目录，当前包含 9 个确定性 Case：

- 会议交付：截止期触发、无截止期不过度编排、重复事件去重；
- 数据洞察：风险发现与图表、数据文件状态感知、会话隔离；
- 桌面执行：确认后归档与撤销、未确认拦截、路径逃逸拦截。

报告会生成到 `benchmark/reports/`，包含 JSON 原始记录与 Markdown 汇总。

> 说明：专项 Benchmark 的耗时是 ContextFlow Runtime 的本地确定性短链路耗时，不等同于端到端模型推理速度。

## 7. A/B 与通用 Benchmark

`benchmark/ab_runner.py` 提供原生 OpenClaw 与 ContextFlow 配置的统一 A/B 入口，支持隔离会话、AB/BA 顺序交替、文件快照、Router Trace 与统一报告。

已提供：

- 文件搜索 A/B Case；
- 固定本地 RAG 资料与 10 道来源可验证题目；
- 用于后续接入 ShellBench（原 ClawBench）和 PinchBench 精选任务的评测框架。

执行 A/B 前需在私有配置中填写 Gateway 地址、模型名称与认证信息；不应提交私有配置文件。

## 8. 安全与边界

- 事件均记录来源、证据、置信度与稳定会话标识；
- RAG、数据与草稿按会话隔离；
- 外部写入、日历修改、邮件发送必须经用户确认；
- 桌面文件操作只允许在指定根目录中执行，支持撤销；
- 当前原型的外部任务系统写入为明确的 stub，不宣称已自动执行。

## 9. 测试环境说明

当前提交版本已在 Windows 本机完成开发与回归验证，包括真实本地文件事件驱动的会议主链路、三类工作场景及专项 Benchmark。

已提供 Linux / Ubuntu / MTT AIBOOK 的部署脚本与验收步骤。最终真机环境应使用相同源码、固定版本和私有模型配置，重新运行核心场景与 Benchmark 后再将结果作为真机性能结论。

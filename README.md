# ContextFlow

ContextFlow 是一个面向主动式任务 Agent 的 **Runtime & Harness Engineering** 原型。它把可观察的工作事件转换为可执行、可恢复、可审计的工作链路，并在副作用操作前施加状态、权限、审批、幂等和计划版本约束。

它不是“多 Agent 列表”或通用办公自动化产品；当前重点是验证 Agent Runtime 的工程基础：事件感知、确定性编排、工具网关、任务生命周期、检查点恢复、确认边界和可复现实验。

## 当前定位与边界

```text
观察到的事件
  -> Context State（证据、置信度、会话隔离）
  -> Pipeline / Tool Gateway
  -> Task / Execution / Operation / Approval / Trace
  -> 本地可验证产物或待确认草稿
```

已实现：真实本地文件监听、会议纪要导入、模拟日历事件、6 类声明式 Agent、会后 Pipeline、Task/Execution/Checkpoint/Operation/Approval/Trace 持久化、工具幂等、计划版本绑定审批、本地 RAG、数据洞察、可撤销归档和确定性评测夹具。

未实现或不应误称为已实现：通用 LLM Agent Loop、生产级分布式队列、真实外部任务系统写入、长期记忆、跨进程事务补偿、端到端在线模型性能结论。

## 运行时架构

| 层 | 职责 | 当前实现 |
|---|---|---|
| OpenClaw | 自然语言交互与 Agent Gateway | 外部集成层 |
| MTClaw Function Router | 工具路由、短链路执行、上游兜底 | 通过 Schema 和 wrapper 扩展 |
| ContextFlow Runtime | 状态、Pipeline、任务生命周期、审批、审计 | `service/runtime.py` |
| Tool Gateway | 工具契约、参数/权限校验、幂等、Operation、Trace | `service/tool_gateway.py` |
| 持久化 | 状态、草稿、任务、检查点、审批、轨迹 | SQLite / `service/storage.py` |

完整职责边界和时序见：[Runtime 核心架构](核心说明文档/mtclaw-contextflow-runtime核心架构.md)。

## 6 类 Agent

| Agent | 作用 | 当前协作方式 |
|---|---|---|
| ContextSensorAgent | 事件接入、状态和 Runtime 控制入口 | 所有状态可用 |
| MeetingActionAgent | 会议摘要、决策、待办和草稿 | 会后 Pipeline 首节点 |
| LocalRAGAgent | 会话隔离的本地检索和引用回答 | 会后 Pipeline 的只读证据节点 |
| DeliveryPlannerAgent | DAG、关键路径和重规划 | 有截止风险时创建草稿 |
| DataInsightAgent | 数据概览、风险、SVG 图表 | 独立按需工具链 |
| DesktopExecutorAgent | 文件检索、归档计划、确认、撤销 | 独立本地副作用工具链 |

当前不是 Supervisor 自由协商或无限递归委派。会后流程使用 Pipeline，因为节点顺序、数据依赖、确认点和恢复边界是确定的；模型能力（后续接入）应局限于节点内的语言理解与表达，不能绕过 Runtime 改写控制流。

## 会后 Pipeline、恢复与去重

```text
meeting notes / meeting-ended event
  -> process_meeting          (保存会议草稿)
  -> retrieve_evidence        (只读 RAG)
  -> plan_delivery            (满足 guard 时保存交付草稿)
  -> review / approval boundary
```

每个节点完成后保存 Checkpoint，包含当前节点、完成/待处理节点、工具结果和上下文快照。`context_resume_meeting_pipeline` 从最后一个成功节点恢复，不会重复创建已完成的会议草稿。事件使用稳定 `event_id` 去重；工具使用 Task 内稳定 `idempotency_key`，已完成 Operation 返回保存结果而非再次执行。

## 审批与计划版本

```text
计划草稿 v1
  -> runtime_prepare_approval(draft_id, plan_version, requested operation)
  -> 等待审批 -> runtime_decide_approval -> runtime_execute_approved_operation
```

执行前会重新核对 Operation、Task、Execution、草稿归属和 `plan_version`。`delivery_replan` 将旧草稿设为 `superseded` 并创建递增版本；即使旧审批已经通过，也无法执行其绑定的旧计划。旧桌面归档确认工具仍为兼容 Demo 保留；新的副作用能力应迁移至通用 Runtime 审批路径。

## 本地运行

Python 3.11+；Runtime 核心仅使用标准库。

```powershell
cd ContextFlow
python -m unittest tests.test_task_runtime tests.test_contextflow tests.test_benchmark
python service/runtime_dashboard.py
```

打开 `http://127.0.0.1:8765` 查看状态证据、事件/状态转移、Agent 轨迹、草稿和确认边界。该工作台目前不是完整的 Agent/Workflow 编辑器。

模拟一次可恢复的会后任务：

```powershell
'{"session_key":"demo-meeting","meeting_title":"Release review","meeting_text":"TODO: Prepare release notes (2 hours). Deliver within 24 hours.","pipeline_fail_after":"retrieve_evidence"}' |
  python service/contextflow_cli.py context_simulate_meeting_end

'{"session_key":"demo-meeting","execution_id":"<上一步返回的 execution_id>"}' |
  python service/contextflow_cli.py context_resume_meeting_pipeline
```

## 评测与文档

- `benchmark/runner.py`：9 条确定性场景回归；
- `benchmark/ab_runner.py`：Native 与 ContextFlow 的隔离 A/B 夹具；只有实际配置 Gateway 后才有端到端模型数据；
- `tests/`：Runtime 生命周期、恢复、幂等和审批版本绑定测试。

- [Runtime 核心架构](核心说明文档/mtclaw-contextflow-runtime核心架构.md)
- [会后 Pipeline 契约](核心说明文档/proactive-meeting-loop.md)
- [事件与状态层](核心说明文档/event-state-layer.md)
- [OpenClaw + MTClaw 集成](核心说明文档/openclaw-mtclaw-contextflow.md)
- [评测协议与复现](核心说明文档/评测协议、指标与复现指南.md)

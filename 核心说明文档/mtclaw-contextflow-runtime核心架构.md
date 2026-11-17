# ContextFlow Runtime 核心架构

## 1. 定位

ContextFlow 是面向主动式任务 Agent 的 Runtime & Harness Engineering 原型。其关键产出不是更多“会聊天的 Agent”，而是把事件、上下文、工具调用和副作用组织成可控制、可恢复、可验证的任务生命周期。

```text
Sensed Event
  -> Context State
  -> Pipeline / Tool Gateway
  -> Task -> Execution -> Checkpoint / Operation / Approval / Trace
  -> Artifact, Draft, or bounded local side effect
```

## 2. 框架与项目的职责边界

| 模块 | 来自框架 / 外部系统 | ContextFlow 实现 |
|---|---|---|
| OpenClaw | 对话入口、Agent Gateway | 不修改其核心行为 |
| MTClaw Function Router | Function Calling 路由、工具 wrapper、上游模型兜底 | 注册 Schema、提供 wrapper、约定会话桥接 |
| 事件接入 | 文件系统、模拟器、可选 Graph API | 事件归一化、稳定身份、状态推导与持久化 |
| Runtime | 无通用现成实现 | Pipeline、Gateway、生命周期、审批、恢复、审计 |
| 领域能力 | 本地文件/SQLite | 会议、RAG、交付、数据、桌面工具 |

MTClaw 决定一次自然语言请求是否命中工具，ContextFlow 决定工具在当前状态下是否允许执行、如何被持久化和如何恢复。Router 的上游 fallback 不是 ContextFlow 的任务恢复机制。

## 3. Agent Contract 与协作方式

每个 Agent Manifest 声明场景、允许状态、能力、工具、风险、确认策略和兜底策略。当前注册 6 类 Agent：ContextSensor、MeetingAction、LocalRAG、DeliveryPlanner、DataInsight、DesktopExecutor。

当前支持的组织方式是：

1. **Pipeline**：固定会后骨架，适合有严格依赖与恢复点的流程；
2. **Tool ownership**：工具只能在声明的状态由所属 Agent 调用；
3. **Router**：MTClaw 将明确短链路导向工具，复杂请求再交上游模型。

尚未实现自由协商群聊、无限递归委派或通用 Supervisor。这样可以先把失败隔离、结果格式与副作用边界做成稳定模块。

## 4. 运行时深模块

| 模块 | 接口责任 | 隐藏的复杂性 |
|---|---|---|
| `ContextFlowRuntime` | 单一 `dispatch(tool, payload)` 入口 | 状态读取、Agent 选择、领域调用、结果回写 |
| `MeetingPipeline` | `start` / `resume` | 节点顺序、快照、恢复点、兼容既有产物 |
| `TaskRuntime` | 任务、执行、检查点、审批、Trace | 生命周期转换与持久化 |
| `ToolGateway` | `execute` / `prepare_approval` / `execute_approved` | Schema、权限、幂等、Operation、执行门禁 |
| `Storage` | 持久化接口 | SQLite Schema 与升级兼容 |

这是有意形成的模块边界：Agent 不自行实现重试、审批、日志或幂等；通用治理应由 Runtime 统一提供。

## 5. 会后 Pipeline

```text
meeting event
  -> process_meeting
  -> checkpoint
  -> retrieve_evidence
  -> checkpoint
  -> plan_delivery
  -> checkpoint -> completed
```

每个 checkpoint 包含 `current_node`、已完成和待处理节点、工具结果与上下文快照。进程或链路中断后，只能从最后一个成功节点恢复。事件去重发生在进入 Pipeline 前，避免同一 source identity 反复创建任务；节点 checkpoint 避免恢复时重做已完成工作。

Pipeline 并不否定模型：模型适合放在“理解会议原文”“形成表达”等节点内部；但节点顺序、执行预算、确认点和状态更新必须由 Runtime 保持确定性。

## 6. Tool Gateway 与副作用

Gateway 对受管工具执行以下顺序：

```text
validate payload
 -> validate state + Agent ownership
 -> resolve Task / Execution
 -> create or reuse Operation(idempotency_key)
 -> invoke tool
 -> persist result / failure
 -> checkpoint + trace
```

相同 Task 内同一幂等键的已完成 Operation 返回保存结果；`running` 或无法判断的 Operation 不会盲目重放，要求先 reconcile。外部副作用的 exactly-once 无法仅靠 checkpoint 保证，因此 Operation 是恢复与对账的最小单位。

## 7. 审批、版本和替换保护

通用审批路径：

```text
prepare operation
 -> Approval(task_id, execution_id, operation_ids, draft_id, plan_version)
 -> human decision
 -> execute approved operation
```

执行前再次验证审批、Operation、Task、Execution、当前状态、Agent 归属以及草稿版本。`delivery_replan` 会将原草稿标为 `superseded` 并产生下一个 `plan_version`，因此“批准 v1 后把内容替换成 v2 再执行”会被拒绝。

审批不是前端按钮状态，而是后端可验证的持久化授权。当前真实验证的副作用是本地可撤销归档；外部任务系统写入尚未接入。

## 8. 失败处理

| 失败点 | 当前处理 |
|---|---|
| 重复事件 | 稳定 `event_id` 拦截，不重复编排 |
| 已完成工具重放 | 返回已保存 Operation 结果 |
| 工具异常 | Operation 记为 `failed`，记录错误和 Trace；不伪造成功 |
| Pipeline 中断 | Execution 标记 `interrupted`，从最近 Checkpoint 恢复 |
| 审批拒绝 | Execution 标记失败，不执行 Operation |
| 旧计划审批 | 版本/状态二次校验拦截 |
| 不支持或不安全状态 | 在 Gateway 前置校验阶段拒绝 |

可重试错误分类、超时预算、补偿 Saga 和跨进程不确定结果处理仍是下一阶段工作。

## 9. 验证与下一阶段

当前核心回归覆盖 Pipeline 恢复、工具幂等、状态门禁、审批单次执行、重规划使旧审批失效，以及既有 ContextFlow / Benchmark 场景。评测的完整口径见[评测协议](评测协议、指标与复现指南.md)。

下一阶段优先级：统一所有写操作到 Gateway 审批路径；增加预算、超时、retry middleware；接入真实外部任务系统及 Operation 对账；再抽象可配置 Pipeline 和受限的 Supervisor 调度。

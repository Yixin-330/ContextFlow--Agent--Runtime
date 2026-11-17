# 会后主动链路：可恢复 Pipeline 契约

## 1. 触发与产物

会后链路由新的会议纪要文件、会议纪要导入或 `meeting-ended` 模拟事件触发。它只自动完成本地处理和草稿准备，不发送消息、不创建外部任务、不修改日历。

```text
new meeting signal
 -> stable event identity
 -> Context State: meeting_ended
 -> process_meeting
 -> retrieve_evidence
 -> [deadline guard] plan_delivery
 -> review boundary
```

当文本同时出现明确数值截止期和至少一个可执行待办时，状态可升级为 `deadline_risk` 并生成交付计划草稿；缺少任一证据时停留在 `meeting_ended`，不得补造风险或计划。

## 2. 为什么是 Pipeline

此流程的步骤、数据依赖、确认边界和恢复语义是固定的：必须先处理会议原文，才有待办；只有拿到待办和期限，才有规划依据。因此由 Pipeline 持有控制流。模型（若接入）只服务于节点内的理解、提炼和表达，而不能跳过状态 guard 或直接触发副作用。

当前节点：

| 节点 | Agent / 服务 | 输出 |
|---|---|---|
| `process_meeting` | MeetingActionAgent / OfficeFlow | 总结、决策、待办、未发送邮件草稿、会议草稿 |
| `retrieve_evidence` | LocalRAGAgent | 会话隔离的只读相关资料和引用 |
| `plan_delivery` | DeliveryPlannerAgent | 满足 guard 时的 DAG、关键路径与交付草稿 |

## 3. Checkpoint 与 Resume

每个成功节点之后，Runtime 保存：

- `task_id`、`execution_id`、当前节点和 checkpoint 版本；
- 已完成节点和待执行节点；
- 节点结果与上下文快照；
- Trace 事件。

如果在 `retrieve_evidence` 后中断，`context_resume_meeting_pipeline` 从 `plan_delivery` 继续。它不重新创建会议草稿，也不重新执行已成功的检索节点。执行实例只能在 `interrupted` 或 `retryable_failed` 状态恢复。

## 4. 去重、幂等与失败

文件事件 ID 由规范化路径和内容摘要派生；模拟事件使用稳定 `simulation_key`。同一事件重复抵达时，SQLite 在编排前拒绝重复写入，返回既有引用。

Pipeline 的事件去重不能替代副作用幂等：受管工具通过 Operation 和 Task 内 `idempotency_key` 去重。工具异常会记录失败 Operation 与 Trace；Pipeline 中断会留下可恢复 Execution，而不是伪造成功。

## 5. 审核与计划替换

Pipeline 只生成 `awaiting_review` 草稿。真正的副作用操作应通过 `runtime_prepare_approval` 绑定：草稿 ID、`plan_version`、Task、Execution 和 Operation。重规划会废弃旧草稿并产生新版本，旧审批在执行前的二次校验中被拒绝。

当前外部任务系统写入仍是 stub；本地可撤销归档用于验证实际副作用的审批与幂等机制。

## 6. 演示与恢复验证

```powershell
cd ContextFlow

'{"session_key":"pipeline-demo","meeting_title":"Release review","meeting_text":"TODO: Prepare release notes (2 hours). Deliver within 24 hours.","pipeline_fail_after":"retrieve_evidence"}' |
  python service/contextflow_cli.py context_simulate_meeting_end

'{"session_key":"pipeline-demo","execution_id":"<execution_id>"}' |
  python service/contextflow_cli.py context_resume_meeting_pipeline
```

预期：第一次调用返回 `interrupted=true` 且已有一份会议草稿；恢复后 Execution 为 `completed`，只新增交付草稿，不重复会议草稿。

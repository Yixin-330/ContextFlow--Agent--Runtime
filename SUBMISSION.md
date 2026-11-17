# ContextFlow 提交说明

## 1. 项目定位

ContextFlow 是一个面向主动式任务 Agent 的 **Runtime & Harness Engineering** 原型。它研究的核心问题是：如何把事件感知转化为可执行工作链路，并让该链路具备状态约束、工具治理、中断恢复、审批控制、幂等执行与可复现评测能力。

```text
OpenClaw：自然语言交互与 Agent Gateway
MTClaw Function Router：工具路由、短链路执行、上游兜底
ContextFlow：事件/状态、Pipeline、Tool Gateway、任务生命周期与评测
```

ContextFlow 通过 MTClaw 的工具 Schema 和 wrapper 扩展接入，不修改 Router 核心逻辑。

## 2. 已实现能力

### 事件与上下文

- 本地会议/数据文件监听、会议纪要导入和模拟日历事件；
- 可选 Outlook/Microsoft 365 只读日历 Adapter；
- 不可变 Sensed Event、带证据和置信度的 Context State、SQLite 审计与会话隔离；
- 事件身份去重：同一观察重放不会再次触发会后编排。

### 6 类专用 Agent

| Agent | 已实现能力 |
|---|---|
| ContextSensorAgent | 事件归一化、状态更新、Runtime 控制入口 |
| MeetingActionAgent | 会议摘要、决策、待办、未发送邮件草稿 |
| LocalRAGAgent | 本地文档导入、检索、带来源回答 |
| DeliveryPlannerAgent | 任务 DAG、关键路径、失败后重规划 |
| DataInsightAgent | CSV/TSV/XLSX 概览、风险、SVG 图表 |
| DesktopExecutorAgent | 搜索、归档计划、确认、可撤销本地移动 |

### Runtime 与可靠性

- 会后确定性 Pipeline：`process_meeting → retrieve_evidence → plan_delivery`；
- 任务模型：`Task → Execution → Checkpoint → Operation → Approval → Trace`；
- Checkpoint / Resume：会后链路在节点后中断时从最近成功节点继续；
- Tool Gateway：Schema 校验、Agent/状态归属校验、稳定幂等键、统一 Operation 和 Trace；
- 审批绑定：审批关联 Task、Execution、Operation、计划草稿与版本；
- 版本失效：重规划产生新版本并废弃旧草稿，旧审批不能执行旧计划绑定的 Operation。

## 3. 会后主链路

```text
新会议纪要 / meeting-ended
  -> event_id 去重
  -> meeting_ended
  -> process_meeting（会议草稿）
  -> retrieve_evidence（Local RAG，只读）
  -> [明确截止期 + 待办] deadline_risk
  -> plan_delivery（交付草稿）
  -> 审核边界
```

Pipeline 用于固定、可审计的任务骨架，而不是让模型自由决定所有步骤。每个节点保存 Checkpoint；恢复不会重复创建先前成功节点的草稿。模型推理若后续接入，应放在节点内部，不得绕过 Runtime 控制流。

## 4. 副作用安全模型

```text
计划 v1 -> 准备 Operation -> 审批 -> 执行前二次校验 -> 执行一次
                            ↑
                重规划为 v2 后，v1 为 superseded，旧审批失效
```

当前真实副作用验证对象是本地可撤销归档。邮件发送、日历写入和外部任务系统创建仍为明确 stub，不能表述为已自动执行。为兼容原有 Demo，旧桌面确认链路仍保留；后续应让所有写操作统一走 Runtime 的通用审批路径。

## 5. 代码与运行

```text
ContextFlow/
├─ service/       # Runtime、Pipeline、Gateway、状态、存储、各领域能力
├─ tests/         # Runtime、场景、Benchmark 回归
├─ benchmark/     # 确定性 Benchmark 与 A/B 夹具
├─ mtclaw/        # Function Router Schema、wrapper、配置示例
├─ 核心说明文档/  # 架构、事件、Pipeline、评测、集成说明
└─ docs/          # 领域能力和部署细节
```

```powershell
cd ContextFlow
python -m unittest tests.test_task_runtime tests.test_contextflow tests.test_benchmark
python service/runtime_dashboard.py
```

本次核心回归：54 项 Runtime、ContextFlow 与 Benchmark 测试通过。专项 Benchmark 和 A/B 夹具可复现；A/B 的端到端模型结论必须在真实 Gateway、固定模型/版本、固定测试资料和重复运行后报告。

## 6. 评测与披露

- 专项 Benchmark 当前有 9 条确定性场景，覆盖会议、数据、桌面安全链路；
- A/B 框架支持隔离会话、AB/BA 顺序和 Router Trace，但并不等于已经获得在线模型对比成绩；
- Windows 受限沙箱下，外部 Router 子进程的工作目录可见性和路径编码可能影响部分完整集成用例；核心 Runtime 测试不受该问题影响；
- 不将本地确定性毫秒级耗时表述为在线模型端到端加速。

详见：[README](README.md)、[Runtime 核心架构](核心说明文档/mtclaw-contextflow-runtime核心架构.md)、[评测协议](核心说明文档/评测协议、指标与复现指南.md)。

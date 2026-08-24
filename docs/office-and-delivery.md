# OfficeFlowAgent and DeliveryPlannerAgent

## OfficeFlowAgent

`office_process_meeting` records `meeting_ended` when invoked from an explicit meeting-end request. The deterministic MVP creates a summary, detects decision-marked lines, extracts simple owner/task patterns, and produces an unsent email draft. It then returns an `orchestration` trace: `LocalRAGAgent` searches already-indexed session-local material, while `DeliveryPlannerAgent` creates a reviewable delivery draft only when the meeting contains both an explicit numeric deadline (for example, `within 24 hours`) and actionable items. In that case the persisted state advances to `deadline_risk`. No mail, calendar, or task system is changed.

For long-chain planning, meeting actions may include an owner, a numeric effort estimate, and a predecessor. For example: `Alice owns: Prepare slides (2h)` followed by `Bob owns: After Prepare slides, record demo video (3h)`. The Runtime carries these facts into the delivery DAG, computes the critical path, and preserves the owner in every task. Chinese equivalents such as `张三负责：整理 PPT（2小时）` and `PPT 完成后录制视频` are also supported. Missing effort remains an explicit one-hour planning default; missing dependencies remain parallel tasks rather than inferred constraints.

## DeliveryPlannerAgent

`workflow_plan_delivery` accepts `task_specs` when the ContextFlow state is `deadline_risk`. Each task has an ID, title, optional dependencies, effort estimate, and status. The planner validates the dependency DAG, calculates its critical path, reports the forecast against the deadline, and returns the next executable actions.

`delivery_replan` accepts failed task IDs and creates a new reviewable draft. It marks dependent work as blocked and returns `fallback_required` when the plan needs higher-level intervention.

## Demo evidence

The recommended demo shows a meeting summary first, then a delivery plan built from the identified project work. A failed RAG task can then be reported to show a transparent replanning path. All outputs are structured JSON and are recorded in ContextFlow telemetry.

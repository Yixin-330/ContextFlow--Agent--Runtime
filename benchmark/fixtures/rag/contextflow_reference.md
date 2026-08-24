# ContextFlow Reference Material

## Runtime roles

OpenClaw is the natural-language Agent and Gateway runtime. MTClaw Function Router is the OpenAI-compatible provider layer: it selects a matching tool for a short path, executes the tool wrapper, and may hand unmatched or complex requests to an upstream model.

ContextFlow is the local state and workflow runtime behind those tools. It stores session-isolated state, constrains the allowed Agent and tool capabilities, creates reviewable workflow drafts, and records execution audit events.

## Safe execution

ContextFlow can automatically read local files, infer a state from a supported event, search local knowledge, and prepare a workflow draft. It does not automatically send mail, modify a calendar, or write into an external task system. Those external writes require explicit user confirmation.

Desktop archive follows a safe sequence: search, plan, confirm, execute, then undo. A file remains in place before confirmation, and every completed archive operation has an undo record.

## Event-driven workflow

When a meeting note arrives in the monitored meeting inbox, ContextFlow records a `document.meeting_notes_imported` event. The state engine can project `meeting_ended`. If the note contains an explicit numeric deadline and actionable tasks, the workflow promotes the state to `deadline_risk` and creates one reviewable delivery-plan draft.

The state-triggered meeting workflow has three Agents: MeetingActionAgent extracts meeting decisions and action items; LocalRAGAgent finds session-local evidence; DeliveryPlannerAgent builds a dependency DAG and critical path. Repeated arrival of the same meeting file is deduplicated so it does not create duplicate drafts.

## Data and privacy

DataInsightAgent supports local CSV, TSV, and basic XLSX analysis. It profiles columns, detects missing data, duplicate rows, negative values, and numeric outliers, then can output a local SVG chart.

Knowledge and datasets are session-isolated. A document or dataset ingested for one session is not visible to another session.

## Scope boundary

The current prototype has five vertical SubAgents: MeetingActionAgent, LocalRAGAgent, DeliveryPlannerAgent, DataInsightAgent, and DesktopExecutorAgent. It is a local review-first prototype. External task-system writes are still a stub, not an implemented automatic action.

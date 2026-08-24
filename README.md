# ContextFlow

ContextFlow is a state-aware workflow layer designed to run behind MTClaw Function Router tools.

## Event state layer

ContextFlow can infer a simulated work state from immutable calendar, task, focus, and activity events instead of requiring every state to be written manually. The layer deduplicates observations, evaluates a rolling event window, stores explainable state transitions, and keeps the existing runtime/router interface compatible. See [the event-state guide](docs/event-state-layer.md).

Real Outlook/Microsoft 365 read-only sensing is available as an optional Adapter. It uses Microsoft Graph delta synchronization to surface `meeting_upcoming`, `meeting_window_active`, `schedule_conflict`, and observed `meeting_ended` states without claiming attendance or fabricating meeting content. See [the Outlook calendar guide](docs/outlook-calendar.md).

The runnable event ingress includes a calendar simulator, a polling watcher for `demo_desktop`, and a local meeting-notes importer. All three preserve their source semantics and publish through the same state inference seam.

The first fixed proactive product loop watches `demo_inbox` for a newly arrived meeting-notes file, transitions through `meeting_ended` and guarded `deadline_risk`, and automatically prepares meeting, RAG, and delivery-plan results up to an explicit confirmation boundary. See [the proactive meeting loop contract](docs/proactive-meeting-loop.md).

The meeting loop is idempotent across both file sensing and `context_simulate_meeting_end`: a stable source identity is accepted once, triggers the Agent chain once, and creates reviewable drafts once. Replays return existing references without executing orchestration again.

For the real local sensing source, one long-running watcher monitors `inbox/meetings` and `inbox/data`, waits for stable new files, and persists normalized events to SQLite. Meeting and data opportunity states use independent streams so they can coexist. See [the local inbox watcher guide](docs/local-inbox-watcher.md).

## Runtime contract

All tool calls pass through `service/runtime.py` and its single `ContextFlowRuntime.dispatch()` interface. The runtime hides persistence, state validation, Agent selection, draft creation, confirmation handling, and telemetry behind that one seam.

Each Agent Manifest now declares its scenario, eligible states, capabilities, owned tools, trigger hints, latency profile, risk level, confirmation policy, fallback policy, and evaluation tags. This is the extension contract for the upcoming Local RAG, data-analysis, office, delivery-planning, and desktop-execution Agents.

Every invocation is recorded in SQLite with `scenario`, `context_state`, `selected_agent`, `selected_tool`, `latency_ms`, `success`, `fallback_used`, `user_confirmation`, and `error_reason`. Use the local-only runtime command to inspect aggregated telemetry:

```powershell
'{}' | python .\ContextFlow\service\contextflow_cli.py context_runtime_info
```

## LocalRAGAgent

`LocalRAGAgent` is the first runnable vertical Agent. It maintains a session-isolated local knowledge space, uses deterministic offline lexical retrieval, and returns the source file plus chunk number for every grounded answer. Its narrow interface is deliberately stable so a future on-device embedding implementation can replace retrieval without changing MTClaw tools.

```powershell
# Import the bundled demo knowledge.
'{"session_key":"demo-rag","document_id":"competition-demo","source_path":".\ContextFlow\demo_knowledge\contextflow_competition_demo.md"}' |
  python .\ContextFlow\service\contextflow_cli.py rag_ingest

# Ask a grounded question. The result includes citations.
'{"session_key":"demo-rag","query":"MTClaw 如何调用工具？"}' |
  python .\ContextFlow\service\contextflow_cli.py rag_answer
```

The offline MVP supports inline text, `.txt`, and `.md` sources. PDF, DOCX, embeddings, and reranking are explicit next-stage upgrades rather than silently claimed capabilities.

## Office and delivery workflows

`office_process_meeting` turns meeting text into a reviewable summary, decision list, action items, and an unsent email draft. Its state-triggered orchestrator also searches the session-local knowledge base for related material and, when it finds both an explicit numeric deadline and actionable meeting items, promotes the state to `deadline_risk` and creates a reviewable delivery-plan draft. `workflow_plan_delivery` accepts optional structured `task_specs` to calculate a dependency DAG and critical path; `delivery_replan` marks downstream work blocked after a failure and creates a new review draft. See [the workflow guide](docs/office-and-delivery.md) for the full contract.

## Runtime dashboard

Run `python service/runtime_dashboard.py` from the `ContextFlow` directory and open `http://127.0.0.1:8765`. The local product workspace refreshes every two seconds and displays the current user state, evidence and confidence, real-versus-simulated event source, sensed-event and state-transition timeline, Agent collaboration trace, confirmation boundary, and reviewable meeting/delivery drafts. Add `?session_key=<key>` to inspect a specific isolated session.

Without a `session_key` in the URL, the workspace follows the latest PowerShell, file-watcher, or calendar operation automatically. A fixed demo or explicit URL can temporarily pin the view to one session; use **跟随最新操作** in the header to return to live mode.

If an older dashboard process is already using the port, stop it with `Ctrl+C` and restart the command so the updated workspace is loaded.

### Competition demo controls (P1)

Start the real inbox watcher in one terminal:

```powershell
python service/event_ingress_cli.py watch-inboxes --session-key competition-demo
```

Start the workspace in another terminal:

```powershell
python service/runtime_dashboard.py
```

The workspace now provides a live watcher heartbeat, a button that atomically places the bundled meeting template into the real meeting inbox, five-stage progress, same-event idempotency replay, draft details and approve/reject decisions, a recoverable session-scoped demo reset, and deterministic natural-language questions grounded in the current runtime snapshot. The demo button is disabled unless the watcher heartbeat is healthy. Reset archives generated demo files under `inbox/.demo_archive` and never clears unrelated sessions.

The **User State Demo Lab** on the same page can independently demonstrate `meeting_upcoming`, `meeting_window_active`, `meeting_ended`, `schedule_conflict`, `focus_mode`, and `data_ready_for_analysis` without PowerShell. These buttons inject clearly labelled simulation signals through the real event store and state-inference engine; they do not fabricate external actions or meeting content. Use **Start New Meeting Demo** when presenting the complete real-file-sensing path that generates meeting, RAG, and delivery-plan drafts.

## DataInsightAgent

`DataInsightAgent` reads local CSV, TSV, and basic XLSX data without a cloud dependency. It profiles typed columns, flags duplicate rows, high missingness, negative values, and numeric outliers, then writes a local SVG bar chart when requested. See [the data guide](docs/data-insight.md).

```powershell
'{"session_key":"demo-data","dataset_id":"project-progress","source_path":".\ContextFlow\demo_data\project_progress.csv"}' |
  python .\ContextFlow\service\contextflow_cli.py data_profile

'{"session_key":"demo-data","dataset_id":"project-progress"}' |
  python .\ContextFlow\service\contextflow_cli.py data_analyze

'{"session_key":"demo-data","dataset_id":"project-progress","x_column":"workstream","y_column":"risk_score"}' |
  python .\ContextFlow\service\contextflow_cli.py data_chart
```

## First vertical slice

The first runnable slice supports a meeting-ended state and a safe meeting workflow:

1. `context_update_state` records `meeting_ended` for an OpenClaw session.
2. `context_get_state` returns the current state and its trigger evidence.
3. `workflow_prepare_meeting` creates a reviewable meeting-workflow draft.
4. `workflow_confirm_action` confirms or rejects a draft before an external write.

All state is local SQLite data under `data/contextflow.db`.

## DesktopExecutorAgent

`DesktopExecutorAgent` completes the local-action safety loop for a desktop scenario: read-only file discovery, reviewable archive plan, explicit approval, local move, and recorded undo. It never deletes files or runs shell commands. See [the desktop execution guide](docs/desktop-executor.md) for the five-tool interaction contract and demo commands.

## MTClaw integration

- Copy `mtclaw/functions.jsonl` entries into the Function Router `functions_file`.
- Copy `mtclaw/scripts/*.sh` into the configured `scripts_dir` and make them executable.
- Set `FR_TOOLS_BASE_DIR` to this `ContextFlow` directory in the Function Router configuration.
- The wrappers call `service/contextflow_cli.py`, read JSON from stdin, and return JSON to stdout.

For the full OpenClaw + MTClaw deployment on Ubuntu/MTT AIBOOK, including private environment variables, Session Bridge, preflight checks, port alignment, and acceptance checks, see [the integration runbook](docs/openclaw-mtclaw-contextflow.md).

## Local smoke test

```powershell
'{"session_key":"demo-001","state":"meeting_ended","evidence":["calendar ended","recording ready"]}' |
  python .\ContextFlow\service\contextflow_cli.py context_update_state

'{"session_key":"demo-001","meeting_title":"HICOOL 项目讨论"}' |
  python .\ContextFlow\service\contextflow_cli.py workflow_prepare_meeting
```

This is intentionally a local, review-first prototype: mail and calendar writes are not implemented; file moves are limited to the approved, reversible DesktopExecutor archive flow.

## Scenario Benchmark

`benchmark/runner.py` is the deterministic regression suite for the three
competition scenarios: meeting-to-delivery orchestration, data risk discovery,
and confirmed reversible desktop archiving. Each repetition uses an isolated
SQLite database and temporary workspace, then writes a JSON trace and Markdown
summary under `benchmark/reports/`.

```powershell
& ".\ContextFlow\.venv\Scripts\python.exe" ".\ContextFlow\benchmark\runner.py" --runs 3
```

See [the benchmark guide](benchmark/README.md) for the task contracts and the
scope of the current deterministic Runtime measurement.

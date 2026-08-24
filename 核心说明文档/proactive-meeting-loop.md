# First proactive loop: meeting notes to delivery draft

## Product contract

The fixed demonstration starts when a **new** `.txt` or `.md` meeting-notes file enters the dedicated monitored directory. Existing files only establish the watcher baseline. The entire safe preparation path runs automatically; no email is sent and no external task is created.

```text
new meeting-notes file
  -> document.meeting_notes_imported
  -> meeting_ended (0.96)
  -> summary + decisions + action items + unsent email draft
  -> read-only Local RAG search
  -> [explicit deadline <= 72h AND >= 1 action item]
  -> deadline_risk (0.90)
  -> dependency graph + critical path + delivery-plan draft
  -> stop at confirmation boundary
```

If the deadline or action-item guard is missing, the loop remains in `meeting_ended`, reports the missing evidence, and does not fabricate a delivery risk.

## Triggered orchestration and idempotency

Meeting files and simulated meeting-end events converge on the same internal meeting-event method. The accepted event triggers MeetingActionAgent first. The orchestrator then performs session-isolated read-only retrieval through LocalRAGAgent and, only when the deadline guard passes, asks DeliveryPlannerAgent to create an `awaiting_review` draft.

A meeting-file event ID is deterministically derived from its normalized absolute path and content SHA-256. Re-reporting the same path with unchanged content is atomically rejected by SQLite before Agent orchestration. The duplicate response returns `duplicate_protected: true`, `new_drafts_created: 0`, and references to existing drafts. Editing the content creates a new identity and is treated as a new observation.

The simulation entry uses `simulation_key` as its stable identity. Reusing the same key exercises the duplicate path without relying on the filesystem.

## State model

| State | Required evidence | Confidence floor | State-specific Agent | Automatic work | Confirmation boundary |
|---|---|---:|---|---|---|
| `idle` | No stronger signal in the event window | 0.80 | ContextSensorAgent | General assistance and read-only inspection | External writes, messages, file moves |
| `focus_mode` | Explicit focus event or desktop change within 10 minutes | 0.72 | DesktopExecutorAgent | Silent capture and read-only suggestions | File moves and external writes |
| `meeting_ended` | Calendar meeting-ended event or non-empty imported notes | 0.96 | MeetingActionAgent + LocalRAGAgent | Parse notes, create summary, decisions, actions, email draft, RAG search | Sending/publishing the draft or creating tasks |
| `deadline_risk` | Numeric deadline within 72 hours, incomplete work, at least one action | 0.90 | DeliveryPlannerAgent + LocalRAGAgent | Calculate dependencies and critical path; prepare plan draft | Task-system changes, assignments, messages, plan execution |

DataInsightAgent remains a shared opt-in analytical Agent in every state. ContextSensorAgent remains eligible to receive new evidence in every state. The code-level contract returned by `context_get_state` is the authoritative machine-readable version of this table.

## RAG contract

RAG is always read-only and session-isolated. It may return `matched` with cited excerpts or `no_local_evidence`; both are valid completed outcomes. Missing evidence is surfaced and never invented. The demo watcher indexes the bundled ContextFlow knowledge document into its session before listening.

## Draft and confirmation contract

Automatic:

- read the newly arrived local notes file;
- calculate local summaries and plans;
- search already approved local knowledge;
- store `awaiting_review` drafts and telemetry.

Requires explicit confirmation:

- send an email or chat message;
- publish minutes;
- create, modify, assign, or reorder external tasks;
- execute the delivery plan;
- move or delete user files.

## Demonstration

Start the listener:

```powershell
python service/event_ingress_cli.py watch-meeting-inbox --session-key proactive-loop --duration 120
```

After it prints `watching_meeting_inbox`, copy the prepared notes into the inbox:

```powershell
$target = "demo_inbox/contextflow_delivery_meeting_$([DateTimeOffset]::Now.ToUnixTimeSeconds()).md"
Copy-Item demo_data/contextflow_delivery_meeting.md $target
```

The listener output must include `state_sequence: ["meeting_ended", "deadline_risk"]`, meeting artifacts, RAG status, a delivery-plan draft, and the confirmation boundary. The dashboard view is `http://127.0.0.1:8765/?session_key=proactive-loop`.

Simulate the same chain without copying a file:

```powershell
$payload = @{
  session_key = "simulated-meeting"
  meeting_title = "ContextFlow delivery review"
  simulation_key = "demo-meeting-001"
  meeting_text = @"
Decision: ship the demo within 24 hours.
Alice owns: Prepare ContextFlow slides (2 hours).
Bob owns: After Prepare ContextFlow slides, validate Local RAG evidence (2 hours).
"@
} | ConvertTo-Json

$payload | python service/contextflow_cli.py context_simulate_meeting_end
$payload | python service/contextflow_cli.py context_simulate_meeting_end
```

The first call creates the loop. The second must return `duplicate_protected: true` and `new_drafts_created: 0`.

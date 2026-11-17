# Event state layer

The event state layer turns source-specific observations into an explainable state projection without claiming more than the source observed. It supports simulated signals, local meeting/data file ingress, desktop polling, and an optional read-only Outlook adapter. Adapters send immutable observations through one interface, `context_ingest_event`; ContextFlow stores them, evaluates a rolling 72-hour event window, updates the current state projection, and records explainable transitions.

## Event contract

```json
{
  "session_key": "demo-awareness",
  "event_id": "calendar-42-ended",
  "event_type": "calendar.meeting_ended",
  "source": "demo-calendar",
  "occurred_at": "2026-08-11T09:30:00+08:00",
  "attributes": {"title": "Demo review"}
}
```

`event_id` makes ingestion idempotent. `occurred_at` must include a timezone. Supported events cover calendar meeting start/end, calendar deadline changes, task progress changes, explicit focus start/end, and active/idle signals.

## MVP inference rules

Rules are deterministic and explainable. An incomplete deadline within 72 hours has highest priority, followed by an active explicit focus session, a meeting that ended within two hours, and finally idle. Every actual state change is stored separately from raw events and execution telemetry.

## Demo sequence

Send these events to `service/contextflow_cli.py context_ingest_event` in order:

1. `user.focus_started` -> `focus_mode`
2. `user.focus_ended` -> `idle`
3. `calendar.meeting_ended` -> `meeting_ended`
4. `task.progress_changed` with `{"completion": 0.4}`
5. `calendar.deadline_changed` with `{"hours_remaining": 24, "completion": 0.4}` -> `deadline_risk`

The dashboard snapshot exposes `sensed_events` and `state_transitions` alongside the existing state, drafts, and execution trace. A state transition can trigger a bounded Pipeline, but state inference itself never authorizes an external write: the Runtime must still validate Agent ownership, Operation state and approval.

## Event ingress demo

The three source adapters preserve what was actually observed, then publish through the same event-state interface.

```powershell
# Simulate a calendar meeting ending.
python service/event_ingress_cli.py calendar --session-key proactive-demo --event meeting-ended --title "ContextFlow review"

# Import the bundled meeting notes.
python service/event_ingress_cli.py meeting-notes --session-key proactive-demo --path demo_desktop/meeting-notes.txt --title "ContextFlow review"

# Listen for create, edit, and delete activity. Edit a file under demo_desktop while this runs.
python service/event_ingress_cli.py watch-desktop --session-key desktop-demo --path demo_desktop --duration 60

# Simulate deadline pressure; completion is a fraction from 0 to 1.
python service/event_ingress_cli.py calendar --session-key proactive-demo --event deadline-changed --hours-remaining 24 --completion 0.4
```

Expected state changes are `meeting_ended` after either meeting signal, `focus_mode` after a recent desktop edit, and `deadline_risk` after the final calendar signal. Each result prints the normalized event, inferred state, confidence, evidence, and whether the state changed.

## Natural-language acceptance test

After restarting MTClaw so that `/v1/tools` exposes the ContextFlow schemas, send the following messages in one OpenClaw chat. The Router should choose the named high-level tool; users do not need to provide JSON event fields.

| Natural-language message | Expected tool | Expected state |
|---|---|---|
| `我刚结束 ContextFlow 项目评审会，请记录这个状态。` | `context_simulate_calendar` | `meeting_ended` |
| `导入本地会议纪要 C:\\...\\demo_desktop\\meeting-notes.txt，标题是项目评审会。` | `context_import_meeting_notes` | `meeting_ended` |
| `距离提交只剩 24 小时，目前完成了 40%，请记录日历风险。` | `context_simulate_calendar` | `deadline_risk` |
| `我现在是什么工作状态？为什么？` | `context_get_state` | Current state plus evidence |

For the desktop case, keep `watch-desktop` running, edit a file under `demo_desktop`, then ask `我现在是什么工作状态？为什么？`. The watcher—not the chat message—must create the sensed event; the natural-language turn only reads the resulting state.

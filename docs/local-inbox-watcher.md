# Local inbox watcher

The local inbox watcher is ContextFlow's first continuously running real sensing source. It watches two directories in one process and writes normalized immutable events to the existing SQLite `sensed_events` table.

| Directory | Accepted new files | Event | Stream | Result |
|---|---|---|---|---|
| `inbox/meetings` | `.md`, `.txt` | `document.meeting_notes_imported` | `<base>:meetings` | Meeting delivery loop |
| `inbox/data` | `.csv`, `.tsv`, `.xlsx` | `data.file_ready` | `<base>:data` | `data_ready_for_analysis` only |

Separate streams are intentional: deadline risk is a meeting-delivery condition, while data readiness is an opportunity state. Both can be true simultaneously without one overwriting the other.

## Sensing guarantees

- Files present when the listener starts establish the baseline and are ignored.
- Only newly arrived supported extensions are eligible.
- A file must have the same modification timestamp and size for two consecutive scans before it is accepted.
- Each accepted arrival is normalized and persisted through `context_ingest_event` semantics.
- Data-file contents are not read, profiled, charted, or analyzed automatically; only path, name, extension, size, source, and event time are recorded.
- Meeting text is read locally because it is the explicit input to the approved meeting-delivery loop.

## Run continuously

```powershell
cd "C:\Users\Lenovo\Documents\摩尔线程个人工作智能体\ContextFlow"
python service/event_ingress_cli.py watch-inboxes --session-key local-inbox
```

The listener prints both resulting stream keys. Stop it with `Ctrl+C`.

## Test arrivals

After the watcher reports `watching_inboxes`, use a unique destination name:

```powershell
$stamp = [DateTimeOffset]::Now.ToUnixTimeSeconds()
Copy-Item demo_data/contextflow_delivery_meeting.md "inbox/meetings/meeting_$stamp.md"
Copy-Item demo_data/project_progress.csv "inbox/data/progress_$stamp.csv"
```

Inspect the independent projections:

- `http://127.0.0.1:8765/?session_key=local-inbox:meetings`
- `http://127.0.0.1:8765/?session_key=local-inbox:data`

Expected meeting sequence: `meeting_ended -> deadline_risk`, with reviewable meeting/RAG/delivery results. Expected data state: `data_ready_for_analysis` at confidence `0.95`, with no automatic dataset profile.

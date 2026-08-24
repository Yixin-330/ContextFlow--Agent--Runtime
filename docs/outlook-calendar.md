# Real Outlook calendar sensing

ContextFlow can read a user's Outlook or Microsoft 365 calendar through Microsoft Graph and convert scheduled time windows into canonical sensed events. The integration is read-only and requests delegated `Calendars.Read`; it never creates, changes, declines, or deletes calendar events.

## State contract

| Calendar observation | Context state | Confidence | Claim boundary |
| --- | --- | ---: | --- |
| Accepted meeting starts within 15 minutes | `meeting_upcoming` | 0.92 | The meeting is scheduled, not necessarily attended |
| Current time is inside an accepted meeting window | `meeting_window_active` | 0.85 | Attendance is not asserted |
| Two accepted meeting windows overlap | `schedule_conflict` | 0.95 | Only a conflict draft may be prepared |
| A previously observed upcoming/active window crosses its end | `meeting_ended` | 0.96 | A summary still requires meeting notes |

The first synchronization creates a Calendar Baseline. Historical events that already ended do not emit `meeting_ended`. Upcoming and currently active meetings may be surfaced immediately because they describe the current calendar window rather than replaying history.

## Microsoft application registration

1. In Microsoft Entra admin center, create an app registration for a public desktop application.
2. Choose the account type that matches the Outlook account used for the demo. Use a single-tenant registration for one organization, or include personal Microsoft accounts when needed.
3. Enable public client flows so device-code authentication is allowed.
4. Add Microsoft Graph delegated permission `Calendars.Read`.
5. Do not add `Calendars.ReadWrite`.
6. Copy the Application (client) ID. For a single-tenant registration, also copy the Directory (tenant) ID.

## Local setup

Install the optional authentication dependency:

```powershell
python -m pip install -r requirements-outlook.txt
```

Configure IDs for the current PowerShell window:

```powershell
$env:CONTEXTFLOW_OUTLOOK_CLIENT_ID = "YOUR_APPLICATION_CLIENT_ID"
$env:CONTEXTFLOW_OUTLOOK_TENANT = "common"
```

For an organization-only app, replace `common` with its tenant ID.

Connect once. The command prints a Microsoft device-login URL and code:

```powershell
python service/outlook_calendar_cli.py connect
```

The MSAL cache is encrypted with Windows DPAPI and stored under `data/secrets/outlook-token.dpapi`. It is excluded from Git and is not stored in SQLite.

Start continuous read-only sensing:

```powershell
python service/outlook_calendar_cli.py watch `
  --account-key competition-demo `
  --session-key competition-demo:calendar `
  --poll-seconds 30
```

Open the calendar stream in the workspace:

```text
http://127.0.0.1:8765/?base_session_key=competition-demo&session_key=competition-demo:calendar
```

## Verification

Create or accept an Outlook meeting that starts in 5–10 minutes. The expected sequence is:

```text
meeting_upcoming
→ meeting_window_active
→ meeting_ended
```

The same provider event, version, phase, and time window produce the same ContextFlow event identity, so polling and Graph delta replay do not create duplicate transitions.

After `meeting_ended`, ContextFlow waits for actual meeting notes. Dropping a `.md` or `.txt` file into `inbox/meetings` starts the existing meeting-summary, Local RAG, deadline-risk, and delivery-plan chain. Calendar metadata alone is never presented as a meeting summary.

## Failure behavior

- Authentication, network, or Graph errors are recorded as calendar health errors.
- The previously stored calendar state remains available for inspection.
- External calendar writes are not attempted.
- A renewed Graph time window establishes a new baseline, preventing historical meeting-end replay.

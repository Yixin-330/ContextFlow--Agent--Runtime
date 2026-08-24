from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

try:
    from .calendar_sense import CalendarSense
    from .outlook_calendar import OutlookCalendarAdapter, OutlookDeviceAuth
    from .runtime import ContextFlowRuntime
    from .storage import Storage
except ImportError:  # pragma: no cover - direct script launcher.
    from calendar_sense import CalendarSense
    from outlook_calendar import OutlookCalendarAdapter, OutlookDeviceAuth
    from runtime import ContextFlowRuntime
    from storage import Storage


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read an Outlook calendar into ContextFlow.")
    parser.add_argument("command", choices=["connect", "sync", "watch"])
    parser.add_argument("--client-id", default=os.environ.get("CONTEXTFLOW_OUTLOOK_CLIENT_ID"))
    parser.add_argument("--tenant", default=os.environ.get("CONTEXTFLOW_OUTLOOK_TENANT", "common"))
    parser.add_argument("--account-key", default="primary")
    parser.add_argument("--session-key", default="outlook-calendar")
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--past-hours", type=int, default=2)
    parser.add_argument("--future-hours", type=int, default=24)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.client_id:
        raise SystemExit("Configure --client-id or CONTEXTFLOW_OUTLOOK_CLIENT_ID first.")
    store = Storage(ROOT / "data" / "contextflow.db")
    runtime = ContextFlowRuntime(store)
    auth = OutlookDeviceAuth(args.client_id, ROOT / "data" / "secrets" / "outlook-token.dpapi", tenant=args.tenant)
    if args.command == "connect":
        auth.access_token()
        print(json.dumps({"status": "connected", "provider": "outlook", "permission": "Calendars.Read"}, ensure_ascii=False))
        return
    adapter = OutlookCalendarAdapter(auth.access_token)
    sense = CalendarSense(store, adapter, lambda _session, event: runtime.dispatch("context_ingest_event", event))

    def run_once() -> dict[str, object]:
        try:
            result = sense.sync_once(
                session_key=args.session_key,
                account_key=args.account_key,
                past_hours=args.past_hours,
                future_hours=args.future_hours,
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result
        except Exception as error:
            sense.record_error(args.account_key, error)
            print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), flush=True)
            raise

    if args.command == "sync":
        run_once()
        return
    if args.poll_seconds < 5:
        raise SystemExit("--poll-seconds must be at least 5 seconds")
    print(json.dumps({
        "status": "watching",
        "provider": "outlook",
        "permission": "Calendars.Read",
        "session_key": args.session_key,
        "account_key": args.account_key,
        "poll_seconds": args.poll_seconds,
    }, ensure_ascii=False), flush=True)
    try:
        while True:
            try:
                run_once()
            except (ValueError, OSError):
                pass
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        state = store.get_calendar_sync_state("outlook", args.account_key)
        if state:
            state.update({"status": "stopped", "error_message": None})
            store.save_calendar_sync_state(state)
        print(json.dumps({"status": "stopped"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

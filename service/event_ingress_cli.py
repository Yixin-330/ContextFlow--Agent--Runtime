from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .event_ingress import DesktopFileWatcher, EventIngress, LocalInboxWatcher
    from .runtime import ContextFlowRuntime
    from .storage import Storage
except ImportError:  # pragma: no cover - direct script launcher.
    from event_ingress import DesktopFileWatcher, EventIngress, LocalInboxWatcher
    from runtime import ContextFlowRuntime
    from storage import Storage


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ContextFlowRuntime(Storage(ROOT / "data" / "contextflow.db"))
INGRESS = EventIngress(lambda _session_key, event: RUNTIME.dispatch("context_ingest_event", event))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feed simulated work events into ContextFlow.")
    commands = parser.add_subparsers(dest="command", required=True)

    calendar = commands.add_parser("calendar", help="Simulate one calendar event.")
    calendar.add_argument("--session-key", required=True)
    calendar.add_argument("--event", required=True, choices=["meeting-started", "meeting-ended", "deadline-changed"])
    calendar.add_argument("--title")
    calendar.add_argument("--hours-remaining", type=float)
    calendar.add_argument("--completion", type=float)
    calendar.add_argument("--event-id")
    calendar.add_argument("--occurred-at")

    notes = commands.add_parser("meeting-notes", help="Import a local meeting-notes event.")
    notes.add_argument("--session-key", required=True)
    notes.add_argument("--path", required=True)
    notes.add_argument("--title")
    notes.add_argument("--event-id")

    watcher = commands.add_parser("watch-desktop", help="Watch demo_desktop for file changes.")
    watcher.add_argument("--session-key", required=True)
    watcher.add_argument("--path", default=str(ROOT / "demo_desktop"))
    watcher.add_argument("--duration", type=float, default=60)
    watcher.add_argument("--interval", type=float, default=1)

    inbox = commands.add_parser("watch-meeting-inbox", help="Run the fixed proactive meeting-notes demo loop.")
    inbox.add_argument("--session-key", required=True)
    inbox.add_argument("--path", default=str(ROOT / "demo_inbox"))
    inbox.add_argument("--duration", type=float, default=120)
    inbox.add_argument("--interval", type=float, default=1)
    inbox.add_argument("--knowledge-path", default=str(ROOT / "demo_knowledge" / "contextflow_competition_demo.md"))

    inboxes = commands.add_parser("watch-inboxes", help="Continuously sense meeting and data inbox arrivals.")
    inboxes.add_argument("--session-key", required=True, help="Base key; :meetings and :data streams are created.")
    inboxes.add_argument("--meetings-path", default=str(ROOT / "inbox" / "meetings"))
    inboxes.add_argument("--data-path", default=str(ROOT / "inbox" / "data"))
    inboxes.add_argument("--duration", type=float, help="Optional seconds; omit to run until Ctrl+C.")
    inboxes.add_argument("--interval", type=float, default=1)
    inboxes.add_argument("--knowledge-path", default=str(ROOT / "demo_knowledge" / "contextflow_competition_demo.md"))
    return parser


def _calendar_payload(args: argparse.Namespace) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if args.title:
        attributes["title"] = args.title
    if args.hours_remaining is not None:
        attributes["hours_remaining"] = args.hours_remaining
    if args.completion is not None:
        if not 0 <= args.completion <= 1:
            raise ValueError("completion must be between 0 and 1")
        attributes["completion"] = args.completion
    return {"session_key": args.session_key, "kind": args.event.replace("-", "_"),
        "attributes": attributes, "event_id": args.event_id, "occurred_at": args.occurred_at}


def _record_inbox_heartbeat(
    *,
    base_session_key: str,
    watcher: LocalInboxWatcher,
    interval_seconds: float,
    status: str = "running",
) -> None:
    RUNTIME.store.record_watcher_heartbeat({
        "watcher_id": f"local-inboxes:{base_session_key}",
        "base_session_key": base_session_key,
        "status": status,
        "meetings_path": str(watcher.roots["meetings"]),
        "data_path": str(watcher.roots["data"]),
        "interval_seconds": interval_seconds,
        "updated_at": datetime.now(UTC).isoformat(),
    })


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "calendar":
            result = INGRESS.accept("calendar", _calendar_payload(args))
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "meeting-notes":
            result = RUNTIME.dispatch("context_import_meeting_notes", {
                "session_key": args.session_key, "path": args.path,
                "title": args.title, "event_id": args.event_id,
            })
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "watch-desktop":
            watcher = DesktopFileWatcher(Path(args.path))
            print(json.dumps({"status": "watching", "path": str(watcher.root),
                "duration_seconds": args.duration}, ensure_ascii=False), flush=True)
            for change in watcher.watch(duration_seconds=args.duration, interval_seconds=args.interval):
                result = INGRESS.accept("desktop_file_changed", {"session_key": args.session_key, **change})
                print(json.dumps(result, ensure_ascii=False), flush=True)
        elif args.command == "watch-meeting-inbox":
            knowledge_path = Path(args.knowledge_path).expanduser().resolve()
            if knowledge_path.is_file():
                RUNTIME.dispatch("rag_ingest", {
                    "session_key": args.session_key,
                    "document_id": "proactive-demo-knowledge",
                    "source_path": str(knowledge_path),
                })
            watcher = DesktopFileWatcher(Path(args.path))
            print(json.dumps({"status": "watching_meeting_inbox", "path": str(watcher.root),
                "duration_seconds": args.duration, "knowledge_loaded": knowledge_path.is_file()}, ensure_ascii=False), flush=True)
            for change in watcher.watch(duration_seconds=args.duration, interval_seconds=args.interval):
                suffix = Path(change["relative_path"]).suffix.lower()
                if change["action"] == "created" and suffix in {".txt", ".md"}:
                    notes_path = watcher.root / change["relative_path"]
                    result = RUNTIME.dispatch("context_import_meeting_notes", {
                        "session_key": args.session_key,
                        "path": str(notes_path),
                        "title": notes_path.stem,
                    })
                else:
                    result = INGRESS.accept("desktop_file_changed", {"session_key": args.session_key, **change})
                print(json.dumps(result, ensure_ascii=False), flush=True)
        elif args.command == "watch-inboxes":
            meeting_session = f"{args.session_key}:meetings"
            data_session = f"{args.session_key}:data"
            knowledge_path = Path(args.knowledge_path).expanduser().resolve()
            if knowledge_path.is_file():
                RUNTIME.dispatch("rag_ingest", {
                    "session_key": meeting_session,
                    "document_id": "inbox-meeting-knowledge",
                    "source_path": str(knowledge_path),
                })
            watcher = LocalInboxWatcher(Path(args.meetings_path), Path(args.data_path))
            _record_inbox_heartbeat(
                base_session_key=args.session_key,
                watcher=watcher,
                interval_seconds=args.interval,
            )
            print(json.dumps({
                "status": "watching_inboxes",
                "meetings_path": str(watcher.roots["meetings"]),
                "data_path": str(watcher.roots["data"]),
                "meeting_session_key": meeting_session,
                "data_session_key": data_session,
                "stable_scans": watcher.stable_scans,
            }, ensure_ascii=False), flush=True)
            try:
                for arrival in watcher.watch(
                    duration_seconds=args.duration,
                    interval_seconds=args.interval,
                    on_scan=lambda: _record_inbox_heartbeat(
                        base_session_key=args.session_key,
                        watcher=watcher,
                        interval_seconds=args.interval,
                    ),
                ):
                    if arrival["kind"] == "meetings":
                        result = RUNTIME.dispatch("context_import_meeting_notes", {
                            "session_key": meeting_session,
                            "path": arrival["path"],
                            "title": Path(arrival["path"]).stem,
                        })
                    else:
                        result = INGRESS.accept("data_file_ready", {
                            "session_key": data_session,
                            "path": arrival["path"],
                        })
                    print(json.dumps({"arrival": arrival, "result": result}, ensure_ascii=False), flush=True)
            finally:
                _record_inbox_heartbeat(
                    base_session_key=args.session_key,
                    watcher=watcher,
                    interval_seconds=args.interval,
                    status="stopped",
                )
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped"}, ensure_ascii=False))
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

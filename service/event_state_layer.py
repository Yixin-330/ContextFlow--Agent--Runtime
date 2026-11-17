from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

try:
    from .state_engine import build_state
    from .storage import Storage
except ImportError:  # pragma: no cover
    from state_engine import build_state
    from storage import Storage


SUPPORTED_EVENT_TYPES = {
    "calendar.meeting_started", "calendar.meeting_ended",
    "calendar.meeting_upcoming", "calendar.meeting_window_active",
    "calendar.schedule_conflict", "calendar.meeting_cancelled",
    "calendar.deadline_changed", "task.progress_changed",
    "user.focus_started", "user.focus_ended",
    "activity.active", "activity.idle",
    "desktop.file_changed", "document.meeting_notes_imported",
    "data.file_ready",
}


class EventStateLayer:
    """Turn observable user events into one explainable state projection."""

    def __init__(self, store: Storage, *, window_hours: int = 72) -> None:
        self.store = store
        self.window_hours = window_hours

    def ingest(self, session_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self._normalize(session_key, payload)
        inserted = self.store.append_sensed_event(event)
        previous = self.store.get_state(session_key)
        if not inserted:
            context = previous or self.recompute(session_key)
            return {"accepted": False, "deduplicated": True, "event": event,
                    "context": context, "state_changed": False}
        # Evaluate a newly observed signal at its observation time.  This keeps
        # calendar adapters deterministic when they supply an explicit clock.
        context = self.recompute(session_key, now=self._parse_time(event["occurred_at"]))
        changed = previous is None or previous["state"] != context["state"]
        if changed:
            self.store.record_state_transition({
                "transition_id": uuid4().hex, "session_key": session_key,
                "from_state": previous["state"] if previous else None,
                "to_state": context["state"], "confidence": context["confidence"],
                "evidence": context["evidence"], "trigger_event_id": event["event_id"],
                "recorded_at": context["updated_at"],
            })
        return {"accepted": inserted, "deduplicated": not inserted, "event": event,
                "context": context, "state_changed": changed}

    def recompute(self, session_key: str, *, now: datetime | None = None) -> dict[str, Any]:
        reference = now or datetime.now(UTC)
        since = (reference - timedelta(hours=self.window_hours)).isoformat()
        events = self.store.list_sensed_events(session_key, since=since, limit=500)
        state, confidence, evidence = self._infer(events, reference)
        context = build_state(session_key, state, evidence, confidence)
        self.store.save_state(context)
        return context

    def _infer(self, events: list[dict[str, Any]], now: datetime) -> tuple[str, float, list[str]]:
        newest = sorted(events, key=lambda item: item["occurred_at"], reverse=True)
        deadline = next((e for e in newest if e["event_type"] == "calendar.deadline_changed"), None)
        progress = next((e for e in newest if e["event_type"] == "task.progress_changed"), None)
        if deadline:
            hours = deadline["attributes"].get("hours_remaining")
            completion = (progress or deadline)["attributes"].get("completion", 0)
            if isinstance(hours, (int, float)) and 0 <= hours <= 72 and isinstance(completion, (int, float)) and completion < 1:
                return "deadline_risk", 0.94, [f"deadline has {hours:g} hours remaining",
                    f"work completion is {completion:.0%}", f"inferred from event {deadline['event_id']}"]
        meetings = [e for e in newest if e["event_type"] in {
            "calendar.meeting_started", "calendar.meeting_ended", "calendar.meeting_upcoming",
            "calendar.meeting_window_active", "calendar.schedule_conflict",
            "calendar.meeting_cancelled", "document.meeting_notes_imported"
        }]
        if meetings and meetings[0]["event_type"] == "calendar.meeting_window_active":
            title = meetings[0]["attributes"].get("title", "meeting")
            return "meeting_window_active", 0.85, [f"current time is inside the scheduled window for {title}",
                "calendar evidence does not prove attendance", f"inferred from event {meetings[0]['event_id']}"]
        if meetings and meetings[0]["event_type"] in {"calendar.meeting_ended", "document.meeting_notes_imported"}:
            age = now - self._parse_time(meetings[0]["occurred_at"])
            if timedelta(0) <= age <= timedelta(hours=2):
                title = meetings[0]["attributes"].get("title", "meeting")
                return "meeting_ended", 0.96, [f"{title} ended {int(age.total_seconds() // 60)} minutes ago",
                    f"inferred from event {meetings[0]['event_id']}"]
        if meetings and meetings[0]["event_type"] == "calendar.schedule_conflict":
            return "schedule_conflict", 0.95, [meetings[0]["attributes"].get("description", "accepted meeting windows overlap"),
                f"inferred from event {meetings[0]['event_id']}"]
        if meetings and meetings[0]["event_type"] == "calendar.meeting_upcoming":
            title = meetings[0]["attributes"].get("title", "meeting")
            minutes = meetings[0]["attributes"].get("minutes_until_start", 15)
            return "meeting_upcoming", 0.92, [f"{title} is scheduled to start in {minutes} minutes",
                f"inferred from event {meetings[0]['event_id']}"]
        focus = [e for e in newest if e["event_type"] in {"user.focus_started", "user.focus_ended"}]
        if focus and focus[0]["event_type"] == "user.focus_started":
            return "focus_mode", 0.98, ["focus session is active", f"inferred from event {focus[0]['event_id']}"]
        data_files = [e for e in newest if e["event_type"] == "data.file_ready"]
        if data_files:
            path = data_files[0]["attributes"].get("source_path", "data file")
            return "data_ready_for_analysis", 0.95, [f"stable data file arrived: {path}",
                f"inferred from event {data_files[0]['event_id']}"]
        activity = [e for e in newest if e["event_type"] in {
            "activity.active", "activity.idle", "desktop.file_changed"
        }]
        if activity and activity[0]["event_type"] in {"activity.active", "desktop.file_changed"}:
            age = now - self._parse_time(activity[0]["occurred_at"])
            if timedelta(0) <= age <= timedelta(minutes=10):
                target = activity[0]["attributes"].get("relative_path", "desktop")
                return "focus_mode", 0.72, [f"recent work activity detected in {target}",
                    f"inferred from event {activity[0]['event_id']}"]
        return "idle", 0.8, ["no stronger state signal in the active event window"]

    def _normalize(self, session_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = payload.get("event_type")
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {event_type}")
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        attributes = payload.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError("attributes must be an object")
        occurred_at = payload.get("occurred_at") or datetime.now(UTC).isoformat()
        parsed = self._parse_time(occurred_at)
        event_id = payload.get("event_id") or uuid4().hex
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        return {"event_id": event_id.strip(), "session_key": session_key,
            "event_type": event_type, "source": source.strip(), "occurred_at": parsed.isoformat(),
            "attributes": attributes, "received_at": datetime.now(UTC).isoformat()}

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError("occurred_at must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("occurred_at must be an ISO-8601 string") from error
        if parsed.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return parsed.astimezone(UTC)

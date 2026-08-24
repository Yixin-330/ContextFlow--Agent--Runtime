from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

try:
    from .storage import Storage
except ImportError:  # pragma: no cover
    from storage import Storage


class CalendarProvider(Protocol):
    provider: str

    def sync(self, *, delta_link: str | None, window_start: datetime, window_end: datetime) -> dict[str, Any]: ...


Publish = Callable[[str, dict[str, Any]], dict[str, Any]]


class CalendarSense:
    """Convert provider calendar changes and time crossings into canonical sensed events."""

    accepted_responses = {"accepted", "organizer", "tentativelyAccepted"}

    def __init__(self, store: Storage, provider: CalendarProvider, publish: Publish) -> None:
        self.store = store
        self.provider = provider
        self.publish = publish

    def sync_once(
        self,
        *,
        session_key: str,
        account_key: str,
        now: datetime | None = None,
        past_hours: int = 2,
        future_hours: int = 24,
    ) -> dict[str, Any]:
        reference = (now or datetime.now(UTC)).astimezone(UTC)
        previous_sync = self.store.get_calendar_sync_state(self.provider.provider, account_key)
        baseline_complete = bool(previous_sync and previous_sync["baseline_complete"])
        renew_window = previous_sync is None or self._parse(previous_sync["window_end"]) - reference < timedelta(hours=4)
        if renew_window:
            window_start = reference - timedelta(hours=past_hours)
            window_end = reference + timedelta(hours=future_hours)
            delta_link = None
            baseline_complete = False
        else:
            window_start = self._parse(previous_sync["window_start"])
            window_end = self._parse(previous_sync["window_end"])
            delta_link = previous_sync.get("delta_link")

        batch = self.provider.sync(delta_link=delta_link, window_start=window_start, window_end=window_end)
        emitted: list[dict[str, Any]] = []
        for incoming in batch["events"]:
            existing = self.store.get_calendar_event(self.provider.provider, account_key, incoming["provider_event_id"])
            normalized = self._merge_event(incoming, existing, account_key, reference)
            if normalized is None:
                continue
            previous_phase = existing.get("phase") if existing else None
            was_cancelled = bool(existing and existing["cancelled"])
            self.store.save_calendar_event(normalized)
            if baseline_complete and normalized["cancelled"] and not was_cancelled:
                emitted.append(self._emit(session_key, normalized, "calendar.meeting_cancelled", reference))
            phase_event = self._phase_event(normalized["phase"])
            if phase_event and normalized["phase"] != previous_phase:
                if normalized["phase"] != "ended" or (baseline_complete and previous_phase in {"upcoming", "active"}):
                    emitted.append(self._emit(session_key, normalized, phase_event, reference))

        # Time can cross a boundary even when Graph reports no changed event.
        for stored in self.store.list_calendar_events(self.provider.provider, account_key):
            phase = self._phase(stored, reference)
            if phase == stored["phase"]:
                continue
            previous_phase = stored["phase"]
            stored["phase"] = phase
            stored["last_seen_at"] = reference.isoformat()
            self.store.save_calendar_event(stored)
            phase_event = self._phase_event(phase)
            if phase_event and (phase != "ended" or (baseline_complete and previous_phase in {"upcoming", "active"})):
                emitted.append(self._emit(session_key, stored, phase_event, reference))

        emitted.extend(self._emit_conflicts(session_key, account_key, reference))
        self.store.save_calendar_sync_state({
            "provider": self.provider.provider,
            "account_key": account_key,
            "delta_link": batch["delta_link"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "baseline_complete": True,
            "status": "running",
            "error_message": None,
            "updated_at": reference.isoformat(),
        })
        return {
            "provider": self.provider.provider,
            "account_key": account_key,
            "baseline_created": not baseline_complete,
            "changes_received": len(batch["events"]),
            "events_emitted": emitted,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    def record_error(self, account_key: str, error: Exception, *, now: datetime | None = None) -> None:
        reference = (now or datetime.now(UTC)).astimezone(UTC)
        previous = self.store.get_calendar_sync_state(self.provider.provider, account_key)
        self.store.save_calendar_sync_state({
            "provider": self.provider.provider,
            "account_key": account_key,
            "delta_link": previous.get("delta_link") if previous else None,
            "window_start": previous.get("window_start") if previous else reference.isoformat(),
            "window_end": previous.get("window_end") if previous else reference.isoformat(),
            "baseline_complete": bool(previous and previous["baseline_complete"]),
            "status": "error",
            "error_message": str(error)[:500],
            "updated_at": reference.isoformat(),
        })

    def _merge_event(self, incoming: dict[str, Any], existing: dict[str, Any] | None, account_key: str, now: datetime) -> dict[str, Any] | None:
        if incoming.get("removed") and existing is None:
            return None
        if incoming.get("removed") and existing is not None:
            result = dict(existing)
            result.update({"version": incoming["version"], "cancelled": True, "phase": "cancelled", "last_seen_at": now.isoformat()})
            return result
        result = {
            "provider": self.provider.provider,
            "account_key": account_key,
            "provider_event_id": incoming["provider_event_id"],
            "version": incoming["version"],
            "title": incoming["title"],
            "start_at": incoming["start_at"],
            "end_at": incoming["end_at"],
            "response_status": incoming["response_status"],
            "cancelled": incoming["cancelled"],
            "online_meeting": incoming["online_meeting"],
            "phase": "scheduled",
            "last_seen_at": now.isoformat(),
        }
        result["phase"] = self._phase(result, now)
        return result

    def _phase(self, event: dict[str, Any], now: datetime) -> str:
        if event["cancelled"] or event["response_status"] not in self.accepted_responses:
            return "cancelled" if event["cancelled"] else "ignored"
        start = self._parse(event["start_at"])
        end = self._parse(event["end_at"])
        if now >= end:
            return "ended"
        if start <= now < end:
            return "active"
        if timedelta(0) <= start - now <= timedelta(minutes=15):
            return "upcoming"
        return "scheduled"

    @staticmethod
    def _phase_event(phase: str) -> str | None:
        return {
            "upcoming": "calendar.meeting_upcoming",
            "active": "calendar.meeting_window_active",
            "ended": "calendar.meeting_ended",
        }.get(phase)

    def _emit(self, session_key: str, event: dict[str, Any], event_type: str, now: datetime) -> dict[str, Any]:
        start = self._parse(event["start_at"])
        attributes = {
            "provider": self.provider.provider,
            "provider_event_id": event["provider_event_id"],
            "title": event["title"],
            "start_at": event["start_at"],
            "end_at": event["end_at"],
            "response_status": event["response_status"],
            "online_meeting": event["online_meeting"],
        }
        if event_type == "calendar.meeting_upcoming":
            attributes["minutes_until_start"] = max(0, int((start - now).total_seconds() // 60))
        identity = f"{self.provider.provider}\0{event['provider_event_id']}\0{event_type}\0{event['version']}\0{event['start_at']}\0{event['end_at']}"
        payload = {
            "session_key": session_key,
            "event_id": f"calendar-observation:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
            "event_type": event_type,
            "source": "microsoft-graph-calendar",
            "occurred_at": now.isoformat(),
            "attributes": attributes,
        }
        return self.publish(session_key, payload)

    def _emit_conflicts(self, session_key: str, account_key: str, now: datetime) -> list[dict[str, Any]]:
        eligible = [event for event in self.store.list_calendar_events(self.provider.provider, account_key)
                    if not event["cancelled"] and event["response_status"] in self.accepted_responses and self._parse(event["end_at"]) > now]
        emitted = []
        for left_index, left in enumerate(eligible):
            for right in eligible[left_index + 1:]:
                if self._parse(right["start_at"]) >= self._parse(left["end_at"]):
                    break
                identity = f"{left['provider_event_id']}\0{right['provider_event_id']}\0{left['version']}\0{right['version']}"
                payload = {
                    "session_key": session_key,
                    "event_id": f"calendar-conflict:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
                    "event_type": "calendar.schedule_conflict",
                    "source": "microsoft-graph-calendar",
                    "occurred_at": now.isoformat(),
                    "attributes": {
                        "provider": self.provider.provider,
                        "event_ids": [left["provider_event_id"], right["provider_event_id"]],
                        "titles": [left["title"], right["title"]],
                        "description": f"{left['title']} overlaps {right['title']}",
                    },
                }
                emitted.append(self.publish(session_key, payload))
        return emitted

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("calendar time must include timezone")
        return parsed.astimezone(UTC)

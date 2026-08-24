from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4


Publish = Callable[[str, dict[str, Any]], dict[str, Any]]


class EventIngress:
    """Normalize source-specific signals and publish canonical sensed events."""

    def __init__(self, publish: Publish) -> None:
        self.publish = publish

    def accept(self, signal_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_key = self._required_text(payload, "session_key")
        if signal_type == "calendar":
            event = self._calendar(payload)
        elif signal_type == "desktop_file_changed":
            event = self._desktop(payload)
        elif signal_type == "meeting_notes_imported":
            event = self._meeting_notes(payload)
        elif signal_type == "data_file_ready":
            event = self._data_file(payload)
        elif signal_type == "simulated_meeting_ended":
            event = self._simulated_meeting(payload)
        else:
            raise ValueError(f"unsupported signal_type: {signal_type}")
        return self.publish(session_key, {"session_key": session_key, **event})

    def _calendar(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = self._required_text(payload, "kind")
        mapping = {
            "meeting_started": "calendar.meeting_started",
            "meeting_ended": "calendar.meeting_ended",
            "deadline_changed": "calendar.deadline_changed",
        }
        if kind not in mapping:
            raise ValueError(f"unsupported calendar kind: {kind}")
        attributes = dict(payload.get("attributes") or {})
        return self._event(mapping[kind], "calendar-simulator", attributes, payload)

    def _desktop(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = self._required_text(payload, "action")
        if action not in {"created", "modified", "deleted"}:
            raise ValueError(f"unsupported file action: {action}")
        attributes = {
            "action": action,
            "relative_path": self._required_text(payload, "relative_path"),
            "root_path": self._required_text(payload, "root_path"),
        }
        return self._event("desktop.file_changed", "demo-desktop-watcher", attributes, payload)

    def _meeting_notes(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = Path(self._required_text(payload, "path")).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"meeting notes file not found: {path}")
        if path.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("meeting notes must be a .txt or .md file")
        content = path.read_bytes()
        text = content.decode("utf-8").replace("\r\n", "\n")
        if not text.strip():
            raise ValueError("meeting notes file is empty")
        attributes = {
            "title": str(payload.get("title") or path.stem),
            "source_path": str(path),
            "character_count": len(text),
            "preview": " ".join(text.split())[:160],
            "content_sha256": hashlib.sha256(content).hexdigest(),
        }
        event_payload = dict(payload)
        event_payload["event_id"] = payload.get("event_id") or self._file_event_id("meeting-file", path, content)
        return self._event("document.meeting_notes_imported", "meeting-notes-importer", attributes, event_payload)

    def _data_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = Path(self._required_text(payload, "path")).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"data file not found: {path}")
        if path.suffix.lower() not in {".csv", ".tsv", ".xlsx"}:
            raise ValueError("data inbox accepts only .csv, .tsv, or .xlsx files")
        stat = path.stat()
        if stat.st_size == 0:
            raise ValueError("data file is empty")
        attributes = {
            "source_path": str(path),
            "source_name": path.name,
            "dataset_id": path.stem,
            "extension": path.suffix.lower(),
            "size_bytes": stat.st_size,
        }
        return self._event("data.file_ready", "local-data-inbox", attributes, payload)

    def _simulated_meeting(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = self._required_text(payload, "meeting_title")
        text = self._required_text(payload, "meeting_text")
        identity = str(payload.get("simulation_key") or f"{title}\n{text}").encode("utf-8")
        event_payload = dict(payload)
        event_payload["event_id"] = payload.get("event_id") or f"simulated-meeting:{hashlib.sha256(identity).hexdigest()}"
        attributes = {
            "title": title,
            "character_count": len(text),
            "preview": " ".join(text.split())[:160],
            "simulation_key": payload.get("simulation_key"),
        }
        return self._event("calendar.meeting_ended", "meeting-end-simulator", attributes, event_payload)

    @staticmethod
    def _file_event_id(namespace: str, path: Path, content: bytes) -> str:
        normalized_path = path.as_posix().casefold().encode("utf-8")
        digest = hashlib.sha256(normalized_path + b"\0" + hashlib.sha256(content).digest()).hexdigest()
        return f"{namespace}:{digest}"

    @staticmethod
    def _event(event_type: str, source: str, attributes: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": str(payload.get("event_id") or uuid4().hex),
            "event_type": event_type,
            "source": source,
            "occurred_at": str(payload.get("occurred_at") or datetime.now(UTC).isoformat()),
            "attributes": attributes,
        }

    @staticmethod
    def _required_text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value.strip()


class DesktopFileWatcher:
    """Poll one local directory and emit stable created/modified/deleted signals."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"watch directory not found: {self.root}")
        self._snapshot = self._scan()

    def detect(self) -> list[dict[str, str]]:
        current = self._scan()
        changes: list[dict[str, str]] = []
        for relative_path in sorted(current.keys() - self._snapshot.keys()):
            changes.append(self._change("created", relative_path))
        for relative_path in sorted(self._snapshot.keys() - current.keys()):
            changes.append(self._change("deleted", relative_path))
        for relative_path in sorted(current.keys() & self._snapshot.keys()):
            if current[relative_path] != self._snapshot[relative_path]:
                changes.append(self._change("modified", relative_path))
        self._snapshot = current
        return changes

    def watch(self, *, duration_seconds: float, interval_seconds: float = 1.0) -> Iterator[dict[str, str]]:
        if duration_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("watch duration and interval must be positive")
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            time.sleep(min(interval_seconds, max(0, deadline - time.monotonic())))
            yield from self.detect()

    def _scan(self) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for path in self.root.rglob("*"):
            if path.is_file() and ".contextflow_archive" not in path.parts:
                stat = path.stat()
                result[path.relative_to(self.root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
        return result

    def _change(self, action: str, relative_path: str) -> dict[str, str]:
        return {"action": action, "relative_path": relative_path, "root_path": str(self.root)}


class LocalInboxWatcher:
    """Watch meeting and data inboxes and emit only stable new supported files."""

    EXTENSIONS = {
        "meetings": {".md", ".txt"},
        "data": {".csv", ".tsv", ".xlsx"},
    }

    def __init__(self, meetings_root: Path, data_root: Path, *, stable_scans: int = 2) -> None:
        if stable_scans < 2:
            raise ValueError("stable_scans must be at least 2")
        self.roots = {
            "meetings": meetings_root.expanduser().resolve(),
            "data": data_root.expanduser().resolve(),
        }
        for kind, root in self.roots.items():
            if not root.is_dir():
                raise ValueError(f"{kind} inbox not found: {root}")
        self.stable_scans = stable_scans
        self._known = set(self._scan())
        self._pending: dict[tuple[str, str], tuple[tuple[int, int], int]] = {}

    def detect(self) -> list[dict[str, Any]]:
        current = self._scan()
        arrivals: list[dict[str, Any]] = []
        for key, fingerprint in current.items():
            if key in self._known:
                continue
            previous_fingerprint, count = self._pending.get(key, (fingerprint, 0))
            stable_count = count + 1 if fingerprint == previous_fingerprint else 1
            self._pending[key] = (fingerprint, stable_count)
            if stable_count >= self.stable_scans:
                kind, relative_path = key
                path = self.roots[kind] / relative_path
                arrivals.append({
                    "kind": kind,
                    "path": str(path),
                    "relative_path": relative_path,
                    "root_path": str(self.roots[kind]),
                    "size_bytes": fingerprint[1],
                })
                self._known.add(key)
                self._pending.pop(key, None)
        for key in list(self._pending):
            if key not in current:
                self._pending.pop(key, None)
        return arrivals

    def watch(
        self,
        *,
        duration_seconds: float | None = None,
        interval_seconds: float = 1.0,
        on_scan: Callable[[], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration must be positive or omitted")
        if interval_seconds <= 0:
            raise ValueError("interval must be positive")
        deadline = time.monotonic() + duration_seconds if duration_seconds is not None else None
        while deadline is None or time.monotonic() < deadline:
            remaining = interval_seconds if deadline is None else min(interval_seconds, max(0, deadline - time.monotonic()))
            time.sleep(remaining)
            arrivals = self.detect()
            if on_scan is not None:
                on_scan()
            yield from arrivals

    def _scan(self) -> dict[tuple[str, str], tuple[int, int]]:
        result: dict[tuple[str, str], tuple[int, int]] = {}
        for kind, root in self.roots.items():
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.EXTENSIONS[kind]:
                    continue
                stat = path.stat()
                result[(kind, path.relative_to(root).as_posix())] = (stat.st_mtime_ns, stat.st_size)
        return result

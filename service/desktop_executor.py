from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .storage import Storage
except ImportError:  # pragma: no cover - direct CLI launcher.
    from storage import Storage


class DesktopExecutor:
    """Safe local-file archive module: search, plan, approve, execute, undo.

    All paths are resolved inside a caller-selected root.  The module never deletes
    files; an approved plan only moves regular files into one archive folder, and
    every completed move retains its original path for one-click reversal.
    """

    def __init__(self, store: Storage) -> None:
        self.store = store

    def search(self, root_path: str, query: str, limit: int) -> dict[str, Any]:
        root = self._root(root_path)
        keyword = query.casefold()
        matches: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if len(matches) >= limit:
                break
            if not path.is_file() or self._is_internal(path, root):
                continue
            if keyword not in path.name.casefold() and keyword not in str(path.relative_to(root)).casefold():
                continue
            stat = path.stat()
            matches.append({
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            })
        return {"root_path": str(root), "query": query, "matches": matches, "truncated": len(matches) == limit}

    def create_archive_plan(self, session_key: str, root_path: str, relative_paths: list[str], archive_folder: str) -> dict[str, Any]:
        root = self._root(root_path)
        folder = self._archive_folder(archive_folder)
        if not relative_paths:
            raise ValueError("relative_paths must contain at least one file")
        seen: set[str] = set()
        destinations: set[Path] = set()
        entries: list[dict[str, str]] = []
        for raw_path in relative_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("relative_paths must be a string list")
            source = self._inside(root, raw_path)
            relative = source.relative_to(root).as_posix()
            if relative in seen:
                raise ValueError("relative_paths must not contain duplicates")
            seen.add(relative)
            if not source.is_file():
                raise ValueError(f"file not found: {relative}")
            if source.relative_to(root).parts[0] == folder:
                raise ValueError("archive folder cannot be re-archived")
            destination = self._unused_destination(root / folder, source.name, destinations)
            destinations.add(destination)
            entries.append({
                "source_relative_path": relative,
                "destination_relative_path": destination.relative_to(root).as_posix(),
            })
        plan = {
            "plan_id": f"archive-plan-{uuid4().hex[:12]}",
            "session_key": session_key,
            "root_path": str(root),
            "archive_folder": folder,
            "status": "awaiting_confirmation",
            "entries": entries,
            "created_at": self._now(),
            "confirmed_at": None,
            "executed_at": None,
        }
        self.store.save_desktop_archive_plan(plan)
        return plan

    def confirm_archive_plan(self, session_key: str, plan_id: str, decision: str) -> dict[str, Any]:
        plan = self._require_plan(session_key, plan_id)
        if plan["status"] != "awaiting_confirmation":
            raise ValueError(f"archive plan cannot be confirmed from status: {plan['status']}")
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        confirmed = self.store.update_desktop_archive_plan(
            session_key, plan_id, status=decision, confirmed_at=self._now()
        )
        assert confirmed is not None
        return confirmed

    def execute_archive_plan(self, session_key: str, plan_id: str) -> dict[str, Any]:
        plan = self._require_plan(session_key, plan_id)
        if plan["status"] != "approved":
            raise ValueError("archive plan must be approved before execution")
        root = self._root(plan["root_path"])
        moved: list[dict[str, str]] = []
        try:
            for entry in plan["entries"]:
                source = self._inside(root, entry["source_relative_path"])
                destination = self._inside(root, entry["destination_relative_path"])
                if not source.is_file():
                    raise ValueError(f"planned source is no longer available: {entry['source_relative_path']}")
                if destination.exists():
                    raise ValueError(f"planned destination already exists: {entry['destination_relative_path']}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append(entry)
        except Exception:
            self._restore(root, moved)
            raise
        operation = {
            "operation_id": f"archive-op-{uuid4().hex[:12]}",
            "plan_id": plan_id,
            "session_key": session_key,
            "status": "completed",
            "entries": moved,
            "created_at": self._now(),
            "undone_at": None,
        }
        self.store.save_desktop_archive_operation(operation)
        updated_plan = self.store.update_desktop_archive_plan(session_key, plan_id, status="executed", executed_at=self._now())
        assert updated_plan is not None
        return {"plan": updated_plan, "operation": operation, "message": "Archive plan executed. Use desktop_undo_archive to restore every moved file."}

    def undo_archive_operation(self, session_key: str, operation_id: str) -> dict[str, Any]:
        operation = self.store.get_desktop_archive_operation(session_key, operation_id)
        if operation is None:
            raise ValueError("archive operation not found")
        if operation["status"] != "completed":
            raise ValueError(f"archive operation cannot be undone from status: {operation['status']}")
        plan = self._require_plan(session_key, operation["plan_id"])
        root = self._root(plan["root_path"])
        restored: list[dict[str, str]] = []
        try:
            for entry in reversed(operation["entries"]):
                source = self._inside(root, entry["destination_relative_path"])
                destination = self._inside(root, entry["source_relative_path"])
                if not source.is_file():
                    raise ValueError(f"archived file is no longer available: {entry['destination_relative_path']}")
                if destination.exists():
                    raise ValueError(f"original path is occupied: {entry['source_relative_path']}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                restored.append(entry)
        except Exception:
            self._rearchive(root, restored)
            raise
        updated = self.store.update_desktop_archive_operation(session_key, operation_id, status="undone", undone_at=self._now())
        assert updated is not None
        return {"operation": updated, "message": "Archive operation was undone; all recorded files were restored."}

    @staticmethod
    def _root(root_path: str) -> Path:
        if not isinstance(root_path, str) or not root_path.strip():
            raise ValueError("root_path must be a non-empty directory path")
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("root_path must be an existing directory")
        return root

    @staticmethod
    def _archive_folder(archive_folder: str) -> str:
        if not isinstance(archive_folder, str) or not archive_folder.strip():
            raise ValueError("archive_folder must be a non-empty relative folder")
        candidate = Path(archive_folder.strip())
        if candidate.is_absolute() or ".." in candidate.parts or len(candidate.parts) != 1:
            raise ValueError("archive_folder must be one simple relative folder name")
        return candidate.name

    @staticmethod
    def _inside(root: Path, relative_path: str) -> Path:
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("path must remain inside root_path") from error
        return candidate

    @staticmethod
    def _is_internal(path: Path, root: Path) -> bool:
        return path.relative_to(root).parts[0] == ".contextflow_archive"

    @staticmethod
    def _unused_destination(folder: Path, source_name: str, reserved: set[Path]) -> Path:
        candidate = folder / source_name
        index = 1
        while candidate.exists() or candidate in reserved:
            candidate = folder / f"{Path(source_name).stem} ({index}){Path(source_name).suffix}"
            index += 1
        return candidate

    @staticmethod
    def _restore(root: Path, entries: list[dict[str, str]]) -> None:
        for entry in reversed(entries):
            source = DesktopExecutor._inside(root, entry["destination_relative_path"])
            destination = DesktopExecutor._inside(root, entry["source_relative_path"])
            if source.exists() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))

    @staticmethod
    def _rearchive(root: Path, entries: list[dict[str, str]]) -> None:
        for entry in reversed(entries):
            source = DesktopExecutor._inside(root, entry["source_relative_path"])
            destination = DesktopExecutor._inside(root, entry["destination_relative_path"])
            if source.exists() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))

    def _require_plan(self, session_key: str, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_desktop_archive_plan(session_key, plan_id)
        if plan is None:
            raise ValueError("archive plan not found")
        return plan

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

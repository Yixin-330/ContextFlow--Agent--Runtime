from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .runtime import ContextFlowRuntime
    from .storage import Storage
except ImportError:  # pragma: no cover - direct script launcher.
    from runtime import ContextFlowRuntime
    from storage import Storage


class DemoControl:
    """Own the safe competition-demo lifecycle behind one small interface."""

    def __init__(self, root: Path, store: Storage, runtime: ContextFlowRuntime) -> None:
        self.root = root.expanduser().resolve()
        self.store = store
        self.runtime = runtime
        self.meetings_root = (self.root / "inbox" / "meetings").resolve()
        self.template_path = (self.root / "demo_data" / "contextflow_delivery_meeting.md").resolve()

    def health(self, base_session_key: str) -> dict[str, Any]:
        heartbeat = self.store.latest_watcher_heartbeat(base_session_key)
        calendar_sync = self.store.get_calendar_sync_state("outlook", base_session_key)
        now = datetime.now(UTC)
        age_seconds: float | None = None
        alive = False
        if heartbeat is not None:
            updated_at = datetime.fromisoformat(str(heartbeat["updated_at"]).replace("Z", "+00:00"))
            age_seconds = max(0.0, (now - updated_at).total_seconds())
            threshold = max(5.0, float(heartbeat["interval_seconds"]) * 3 + 1)
            alive = heartbeat["status"] == "running" and age_seconds <= threshold
        return {
            "base_session_key": base_session_key,
            "overall": "healthy" if alive else "attention",
            "database": {"status": "healthy", "path": str(self.store.db_path)},
            "calendar": {
                "provider": "outlook",
                "status": calendar_sync["status"] if calendar_sync else "not_connected",
                "permission": "Calendars.Read",
                "updated_at": calendar_sync.get("updated_at") if calendar_sync else None,
                "error_message": calendar_sync.get("error_message") if calendar_sync else None,
            },
            "watcher": {
                "status": "running" if alive else (heartbeat["status"] if heartbeat else "not_started"),
                "heartbeat_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
                "updated_at": heartbeat.get("updated_at") if heartbeat else None,
                "meetings_path": heartbeat.get("meetings_path") if heartbeat else str(self.meetings_root),
                "data_path": heartbeat.get("data_path") if heartbeat else str((self.root / "inbox" / "data").resolve()),
            },
        }

    def start(self, base_session_key: str) -> dict[str, Any]:
        self._require_text(base_session_key, "base_session_key")
        health = self.health(base_session_key)
        if health["watcher"]["status"] != "running":
            raise ValueError("文件监听器未运行；请先启动 watch-inboxes，再开始演示")
        if not self.template_path.is_file():
            raise ValueError(f"演示会议模板不存在：{self.template_path}")
        content = self.template_path.read_bytes()
        if not content.strip():
            raise ValueError("演示会议模板为空")
        self.meetings_root.mkdir(parents=True, exist_ok=True)
        demo_id = uuid4().hex[:12]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final_path = self.meetings_root / f"contextflow-demo-{stamp}-{demo_id}.md"
        temporary_path = final_path.with_suffix(".tmp")
        temporary_path.write_bytes(content)
        temporary_path.replace(final_path)
        timestamp = self._now_iso()
        run = {
            "demo_id": demo_id,
            "base_session_key": base_session_key,
            "meeting_session_key": f"{base_session_key}:meetings",
            "file_path": str(final_path),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "status": "file_placed",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.store.save_demo_run(run)
        return {
            "result": "ok",
            "demo": run,
            "health": health,
            "message": "演示会议纪要已写入监控目录，后续处理由真实文件监听器触发。",
        }

    def simulate_state(self, base_session_key: str, scenario: str) -> dict[str, Any]:
        """Demonstrate one Context State through the real event/state seam."""
        base_session_key = self._require_text(base_session_key, "base_session_key")
        scenario = self._require_text(scenario, "scenario")
        session_key = f"{base_session_key}:state-demo"
        scenarios: dict[str, tuple[str, dict[str, Any]]] = {
            "meeting_upcoming": (
                "calendar.meeting_upcoming",
                {"title": "ContextFlow 参赛评审", "minutes_until_start": 10, "response_status": "accepted"},
            ),
            "meeting_window_active": (
                "calendar.meeting_window_active",
                {"title": "ContextFlow 参赛评审", "response_status": "accepted"},
            ),
            "schedule_conflict": (
                "calendar.schedule_conflict",
                {"titles": ["产品评审", "演示彩排"], "description": "产品评审与演示彩排的会议时间发生重叠"},
            ),
            "focus_mode": (
                "user.focus_started",
                {"mode": "explicit_demo", "title": "专注准备参赛材料"},
            ),
            "data_ready_for_analysis": (
                "data.file_ready",
                {"source_path": "inbox/data/project_progress.csv", "source_name": "project_progress.csv", "demo": True},
            ),
            "meeting_ended": (
                "calendar.meeting_ended",
                {"title": "ContextFlow 参赛评审", "response_status": "accepted", "notes_available": False},
            ),
        }
        if scenario not in scenarios:
            raise ValueError(f"unsupported state demonstration: {scenario}")
        # The state showcase represents one selected scenario at a time. Clearing
        # only its isolated stream prevents older high-priority signals from
        # masking the newly selected state.
        self.store.clear_session(session_key)
        event_type, attributes = scenarios[scenario]
        event_id = f"state-demo:{scenario}:{uuid4().hex}"
        result = self.runtime.dispatch("context_ingest_event", {
            "session_key": session_key,
            "event_id": event_id,
            "event_type": event_type,
            "source": "demo-control",
            "occurred_at": self._now_iso(),
            "attributes": attributes,
        })
        return {
            "result": "ok",
            "session_key": session_key,
            "scenario": scenario,
            "source_kind": "simulation",
            "event": result["event"],
            "context": result["context"],
            "message": "演示信号已进入统一事件层；状态推断与工作台展示均为真实运行。",
        }

    def replay_duplicate(self, base_session_key: str) -> dict[str, Any]:
        run = self._require_active_run(base_session_key)
        path = Path(run["file_path"])
        if not path.is_file():
            raise ValueError("最近一次演示文件已不存在，无法重放重复事件")
        matching_event = self._event_for_run(run)
        if matching_event is None:
            raise ValueError("监听器尚未处理演示文件，请等待 Agent 链路完成后再测试幂等保护")
        result = self.runtime.dispatch("context_import_meeting_notes", {
            "session_key": run["meeting_session_key"],
            "path": str(path),
            "title": path.stem,
        })
        self.store.update_demo_run_status(run["demo_id"], "duplicate_tested", self._now_iso())
        return {
            "result": "ok",
            "demo_id": run["demo_id"],
            "replay_mode": "same_file_same_event_id",
            "duplicate_protected": bool(result.get("duplicate_protected")),
            "new_drafts_created": int(result.get("new_drafts_created", 0)),
            "event_id": result.get("event", {}).get("event_id"),
            "message": "已重放同一路径和相同内容，幂等保护阻止了重复 Agent 编排。",
        }

    def review_draft(self, session_key: str, draft_id: str, decision: str) -> dict[str, Any]:
        draft = self.store.get_draft(self._require_text(draft_id, "draft_id"))
        if draft is None or draft["session_key"] != session_key:
            raise ValueError("当前会话中未找到该草稿")
        return self.runtime.dispatch("workflow_confirm_action", {
            "session_key": session_key,
            "draft_id": draft_id,
            "decision": decision,
        })

    def reset(self, base_session_key: str) -> dict[str, Any]:
        runs = [run for run in self.store.list_demo_runs(base_session_key) if run["status"] != "reset"]
        archive_root = (self.root / "inbox" / ".demo_archive").resolve()
        archived_files: list[str] = []
        cleared_sessions: dict[str, dict[str, int]] = {}
        if runs:
            archive_root.mkdir(parents=True, exist_ok=True)
        for run in runs:
            path = Path(run["file_path"]).resolve()
            if path.is_relative_to(self.meetings_root) and path.name.startswith("contextflow-demo-") and path.is_file():
                destination = archive_root / f"{run['demo_id']}-{path.name}"
                path.replace(destination)
                archived_files.append(str(destination))
            session_key = str(run["meeting_session_key"])
            if session_key not in cleared_sessions:
                cleared_sessions[session_key] = self.store.clear_session(session_key)
            self.store.update_demo_run_status(run["demo_id"], "reset", self._now_iso())
        state_demo_session = f"{base_session_key}:state-demo"
        state_demo_counts = self.store.clear_session(state_demo_session)
        if any(state_demo_counts.values()):
            cleared_sessions[state_demo_session] = state_demo_counts
        return {
            "result": "ok",
            "base_session_key": base_session_key,
            "archived_files": archived_files,
            "cleared_sessions": cleared_sessions,
            "recoverable": True,
            "archive_path": str(archive_root),
            "message": "本次演示会话已重置；演示文件已移入可恢复归档目录。",
        }

    def status(self, base_session_key: str) -> dict[str, Any]:
        run = self.store.latest_demo_run(base_session_key)
        if run is None:
            return {"active": False, "stage": "ready", "steps": self._steps(-1), "demo": None}
        event = self._event_for_run(run)
        snapshot = self.runtime.dashboard_snapshot(run["meeting_session_key"])
        draft_count = len(snapshot.get("draft_cards", []))
        completed = 1 + int(event is not None) + int(snapshot.get("state") is not None) + int(bool(snapshot.get("agent_runs"))) + int(draft_count > 0)
        stages = ["ready", "file_placed", "event_sensed", "state_updated", "agents_completed", "drafts_ready"]
        return {
            "active": True,
            "stage": stages[min(completed, len(stages) - 1)],
            "steps": self._steps(completed),
            "demo": run,
            "session_key": run["meeting_session_key"],
            "draft_count": draft_count,
        }

    def answer(self, session_key: str, question: str) -> dict[str, Any]:
        question = self._require_text(question, "question")
        snapshot = self.runtime.dashboard_snapshot(self._require_text(session_key, "session_key"))
        state = snapshot.get("state") or {"state": "idle", "evidence": []}
        lowered = question.casefold()
        if any(term in lowered for term in ("为什么", "原因", "证据", "风险", "why", "evidence")):
            evidence = state.get("evidence") or ["当前没有形成风险判断的证据"]
            answer = f"当前状态为 {state['state']}。判断依据：" + "；".join(str(item) for item in evidence) + "。"
            fact_type = "state_evidence"
        elif any(term in lowered for term in ("agent", "智能体", "执行", "触发")):
            runs = snapshot.get("agent_runs") or []
            answer = "本次触发的 Agent：" + ("；".join(f"{run['agent']}（{run['status']}）" for run in runs) if runs else "暂无 Agent 执行记录") + "。"
            fact_type = "agent_trace"
        elif any(term in lowered for term in ("草稿", "计划", "总结", "draft")):
            cards = snapshot.get("draft_cards") or []
            answer = "当前工作草稿：" + ("；".join(f"{card['kind_label']}《{card['title']}》，状态 {card['status']}" for card in cards) if cards else "暂无草稿") + "。"
            fact_type = "drafts"
        elif any(term in lowered for term in ("确认", "允许", "边界", "外部", "confirm")):
            boundary = snapshot.get("confirmation_boundary") or {}
            answer = "系统可自动准备：" + "、".join(boundary.get("automatic", [])) + "；需要用户确认：" + "、".join(boundary.get("requires_confirmation", [])) + "。"
            fact_type = "confirmation_boundary"
        else:
            source = snapshot.get("event_source") or {}
            answer = f"当前状态为 {state['state']}，最近感知事件为 {source.get('event_type', '暂无事件')}，已有 {len(snapshot.get('draft_cards', []))} 份工作草稿等待处理。"
            fact_type = "current_summary"
        return {"result": "ok", "answer": answer, "fact_type": fact_type, "grounded": True, "session_key": session_key}

    def _event_for_run(self, run: dict[str, Any]) -> dict[str, Any] | None:
        target = str(Path(run["file_path"]).resolve())
        for event in self.store.list_sensed_events(run["meeting_session_key"], limit=100):
            source_path = event.get("attributes", {}).get("source_path")
            if source_path and str(Path(source_path).resolve()) == target:
                return event
        return None

    def _require_active_run(self, base_session_key: str) -> dict[str, Any]:
        run = self.store.latest_demo_run(self._require_text(base_session_key, "base_session_key"))
        if run is None:
            raise ValueError("没有可用的演示记录，请先开始新的会议演示")
        return run

    @staticmethod
    def _steps(completed: int) -> list[dict[str, str]]:
        labels = ["会议纪要已投放", "监听器发现真实文件", "用户状态已更新", "Agent 协作已完成", "待确认草稿已生成"]
        return [
            {"label": label, "status": "completed" if index < completed else ("current" if index == completed else "pending")}
            for index, label in enumerate(labels)
        ]

    @staticmethod
    def _require_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

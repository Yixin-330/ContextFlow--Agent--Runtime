from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def meeting_draft(session_key: str, title: str, state: dict[str, Any], artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    if state["state"] != "meeting_ended":
        raise ValueError("workflow_prepare_meeting requires meeting_ended state")
    timestamp = now_iso()
    return {
        "draft_id": f"meeting-{uuid4().hex[:12]}",
        "session_key": session_key,
        "workflow_type": "meeting_follow_up",
        "status": "awaiting_review",
        "created_at": timestamp,
        "updated_at": timestamp,
        "payload": {
            "meeting_title": title,
            "artifacts": artifacts or {},
            "steps": [
                {"id": "transcribe", "action": "prepare_transcript", "risk": "low"},
                {"id": "summarize", "action": "generate_summary_draft", "depends_on": ["transcribe"], "risk": "low"},
                {"id": "todos", "action": "extract_action_items", "depends_on": ["summarize"], "risk": "low"},
                {"id": "notify", "action": "create_notification_draft", "depends_on": ["todos"], "risk": "external_write", "requires_confirmation": True},
            ],
            "safety": "Only drafts are prepared automatically. External writes require user confirmation.",
        },
    }


def delivery_plan_draft(
    session_key: str,
    goal: str,
    deadline_hours: int,
    state: dict[str, Any],
    existing_tasks: list[str] | None = None,
    plan: dict[str, Any] | None = None,
    plan_version: int = 1,
) -> dict[str, Any]:
    if state["state"] != "deadline_risk":
        raise ValueError("workflow_plan_delivery requires deadline_risk state")
    if deadline_hours <= 0:
        raise ValueError("deadline_hours must be positive")
    if plan_version < 1:
        raise ValueError("plan_version must be positive")
    timestamp = now_iso()
    existing_tasks = existing_tasks or []
    return {
        "draft_id": f"delivery-{uuid4().hex[:12]}",
        "session_key": session_key,
        "workflow_type": "delivery_plan",
        "plan_version": plan_version,
        "status": "awaiting_review",
        "created_at": timestamp,
        "updated_at": timestamp,
        "payload": {
            "goal": goal,
            "deadline_hours": deadline_hours,
            "existing_tasks": existing_tasks,
            "strategy": "minimum_viable_delivery_first",
            "plan": plan or {},
            "steps": [
                {"id": "scope", "action": "identify_minimum_deliverable", "risk": "low"},
                {"id": "inventory", "action": "inspect_existing_assets", "depends_on": ["scope"], "risk": "low"},
                {"id": "critical_path", "action": "build_dependency_graph", "depends_on": ["inventory"], "risk": "low"},
                {"id": "today_plan", "action": "propose_next_three_actions", "depends_on": ["critical_path"], "risk": "low"},
                {"id": "commit", "action": "apply_plan_to_task_system", "depends_on": ["today_plan"], "risk": "external_write", "requires_confirmation": True},
            ],
            "safety": "The plan is a reviewable draft. No calendar or task-system write occurs without confirmation.",
        },
    }

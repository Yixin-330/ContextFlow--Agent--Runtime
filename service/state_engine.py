from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

try:
    from .state_model import STATE_MODEL, state_contract
except ImportError:  # pragma: no cover
    from state_model import STATE_MODEL, state_contract

ALLOWED_STATES = set(STATE_MODEL)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_state(session_key: str, state: str, evidence: list[str], confidence: float | None = None) -> dict[str, Any]:
    if state not in ALLOWED_STATES:
        raise ValueError(f"unsupported state: {state}")
    minimum = float(state_contract(state)["minimum_confidence"])
    if confidence is None:
        confidence = minimum
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if confidence < minimum:
        raise ValueError(f"confidence for {state} must be at least {minimum}")
    return {
        "session_key": session_key,
        "state": state,
        "confidence": confidence,
        "evidence": evidence,
        "updated_at": now_iso(),
    }


def allowed_capabilities(state: str) -> list[str]:
    state_capabilities = {
        "meeting_upcoming": ["meeting_context_preparation", "quiet_reminder", "read_only_calendar_context"],
        "meeting_window_active": ["low_interrupt_mode", "meeting_note_capture_preparation", "read_only_calendar_context"],
        "schedule_conflict": ["calendar_conflict_explanation", "resolution_draft"],
        "meeting_ended": ["meeting_summary", "todo_extraction", "draft_generation"],
        "deadline_risk": ["task_decomposition", "dependency_check", "plan_reordering"],
        "focus_mode": ["silent_capture", "low_interrupt_reminder", "file_search", "file_organize", "reversible_local_action"],
        "idle": ["general_assist", "file_search", "file_organize", "reversible_local_action"],
        "data_ready_for_analysis": ["data_ready_notification"],
    }
    return [*state_capabilities[state], "local_rag_ingest", "local_rag_query", "data_profile", "data_analysis", "data_chart"]


def describe_state(state: str) -> dict[str, Any]:
    try:
        from .agent_registry import find_agents_for_state
    except ImportError:  # pragma: no cover
        from agent_registry import find_agents_for_state
    contract = state_contract(state)
    contract["allowed_agents"] = [agent["name"] for agent in find_agents_for_state(state)]
    contract["allowed_capabilities"] = allowed_capabilities(state)
    return contract

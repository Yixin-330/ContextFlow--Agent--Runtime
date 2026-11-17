from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentManifest:
    name: str
    scenario: str
    states: tuple[str, ...]
    capabilities: tuple[str, ...]
    tool_names: tuple[str, ...]
    trigger_patterns: tuple[str, ...]
    latency_profile: str
    risk_level: str
    confirmation_policy: str
    fallback_policy: str
    evaluation_tags: tuple[str, ...]


REGISTRY = (
    AgentManifest(
        name="ContextSensorAgent",
        scenario="event_ingress",
        states=("idle", "meeting_upcoming", "meeting_window_active", "schedule_conflict", "meeting_ended", "deadline_risk", "focus_mode", "data_ready_for_analysis"),
        capabilities=("event_normalization", "state_inference", "source_evidence"),
        tool_names=("context_ingest_event", "context_simulate_calendar", "context_import_meeting_notes", "context_simulate_meeting_end", "context_resume_meeting_pipeline", "runtime_prepare_approval", "runtime_decide_approval", "runtime_execute_approved_operation"),
        trigger_patterns=("meeting started", "meeting ended", "deadline changed", "import meeting notes"),
        latency_profile="fast",
        risk_level="local_observation",
        confirmation_policy="not_required",
        fallback_policy="reject_unsupported_signal",
        evaluation_tags=("context", "event", "proactive"),
    ),
    AgentManifest(
        name="DataInsightAgent",
        scenario="data_insight",
        states=("idle", "meeting_upcoming", "meeting_window_active", "schedule_conflict", "meeting_ended", "deadline_risk", "focus_mode", "data_ready_for_analysis"),
        capabilities=("tabular_profile", "deterministic_risk_detection", "local_chart_generation"),
        tool_names=("data_profile", "data_analyze", "data_chart"),
        trigger_patterns=("analyze csv", "analyze excel", "find data risks", "draw chart"),
        latency_profile="fast",
        risk_level="local_artifact",
        confirmation_policy="not_required",
        fallback_policy="upstream_on_ambiguous_business_question",
        evaluation_tags=("data", "deterministic", "chart"),
    ),
    AgentManifest(
        name="LocalRAGAgent",
        scenario="local_rag",
        states=("idle", "meeting_upcoming", "meeting_window_active", "schedule_conflict", "meeting_ended", "deadline_risk", "focus_mode", "data_ready_for_analysis"),
        capabilities=("local_document_ingest", "source_cited_search", "source_cited_answer"),
        tool_names=("rag_ingest", "rag_search", "rag_answer"),
        trigger_patterns=("search local documents", "answer with sources", "ingest project materials"),
        latency_profile="fast",
        risk_level="read_only",
        confirmation_policy="not_required",
        fallback_policy="upstream_on_no_local_evidence",
        evaluation_tags=("rag", "local", "cited"),
    ),
    AgentManifest(
        name="MeetingActionAgent",
        scenario="office_flow",
        states=("meeting_ended",),
        capabilities=("meeting_summary", "todo_extraction", "draft_generation"),
        tool_names=("workflow_prepare_meeting", "office_process_meeting"),
        trigger_patterns=("meeting ended", "prepare meeting follow-up"),
        latency_profile="fast",
        risk_level="review_required",
        confirmation_policy="external_write_required",
        fallback_policy="upstream_on_missing_artifact",
        evaluation_tags=("office", "workflow", "review"),
    ),
    AgentManifest(
        name="DeliveryPlannerAgent",
        scenario="delivery_planning",
        states=("deadline_risk",),
        capabilities=("task_decomposition", "dependency_check", "plan_reordering"),
        tool_names=("workflow_plan_delivery", "delivery_replan"),
        trigger_patterns=("deadline risk", "plan delivery"),
        latency_profile="robust",
        risk_level="review_required",
        confirmation_policy="plan_review_required",
        fallback_policy="upstream_on_complex_dependencies",
        evaluation_tags=("planning", "dag", "deadline"),
    ),
    AgentManifest(
        name="DesktopExecutorAgent",
        scenario="desktop_execution",
        states=("idle", "focus_mode"),
        capabilities=("file_search", "file_organize", "reversible_local_action"),
        tool_names=("desktop_search_files", "desktop_plan_archive", "desktop_confirm_archive", "desktop_execute_archive", "desktop_undo_archive"),
        trigger_patterns=("find file", "organize files"),
        latency_profile="fast",
        risk_level="risk_based",
        confirmation_policy="risk_based",
        fallback_policy="ask_user_on_unsafe_action",
        evaluation_tags=("desktop", "local", "reversible"),
    ),
)


def find_agents_for_state(state: str) -> list[dict[str, object]]:
    return [asdict(agent) for agent in REGISTRY if state in agent.states]


def find_agent_for_tool(state: str, tool_name: str) -> dict[str, object] | None:
    """Return the one eligible manifest that owns a tool in the current state."""
    for agent in REGISTRY:
        if state in agent.states and tool_name in agent.tool_names:
            return asdict(agent)
    return None

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

try:  # Support package execution and MTClaw's direct script launcher.
    from .agent_registry import find_agent_for_tool, find_agents_for_state
    from .data_insight import DataInsight
    from .desktop_executor import DesktopExecutor
    from .delivery_planner import DeliveryPlanner
    from .event_state_layer import EventStateLayer
    from .event_ingress import EventIngress
    from .local_rag import LocalRAG
    from .meeting_orchestrator import MeetingOrchestrator
    from .meeting_pipeline import MeetingPipeline
    from .office_flow import OfficeFlow
    from .state_engine import allowed_capabilities, build_state, describe_state
    from .storage import Storage
    from .task_runtime import TaskRuntime
    from .tool_gateway import ToolGateway
    from .workflow_engine import delivery_plan_draft, meeting_draft
except ImportError:  # pragma: no cover - exercised by the direct CLI launcher.
    from agent_registry import find_agent_for_tool, find_agents_for_state
    from data_insight import DataInsight
    from desktop_executor import DesktopExecutor
    from delivery_planner import DeliveryPlanner
    from event_state_layer import EventStateLayer
    from event_ingress import EventIngress
    from local_rag import LocalRAG
    from meeting_orchestrator import MeetingOrchestrator
    from meeting_pipeline import MeetingPipeline
    from office_flow import OfficeFlow
    from state_engine import allowed_capabilities, build_state, describe_state
    from storage import Storage
    from task_runtime import TaskRuntime
    from tool_gateway import ToolGateway
    from workflow_engine import delivery_plan_draft, meeting_draft


class ContextFlowRuntime:
    """The single runtime interface used by the CLI, MTClaw wrappers, and tests."""

    def __init__(self, store: Storage) -> None:
        self.store = store
        self.rag = LocalRAG(store)
        self.data = DataInsight(store)
        self.desktop = DesktopExecutor(store)
        self.office = OfficeFlow()
        self.delivery = DeliveryPlanner()
        self.event_state = EventStateLayer(store)
        self.event_ingress = EventIngress(lambda session_key, event: self.event_state.ingest(session_key, event))
        self.meeting_orchestrator = MeetingOrchestrator(self.store, self.rag, self.delivery)
        self.tasks = TaskRuntime(store)
        self.tool_gateway = ToolGateway(store, self.tasks, self._execute)
        self.meeting_pipeline = MeetingPipeline(self.store, self.tasks, self.office, self.meeting_orchestrator)

    def dispatch(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_key = self._require_string(payload, "session_key")
        started_at = perf_counter()
        state = self.store.get_state(session_key)
        current_state = state["state"] if state else "idle"
        agent = find_agent_for_tool(current_state, tool_name)
        try:
            result = self.tool_gateway.execute(tool_name, payload, state) if self.tool_gateway.handles(tool_name) else self._execute(tool_name, payload, state)
        except ValueError as error:
            self._record(
                session_key=session_key,
                tool_name=tool_name,
                context_state=current_state,
                agent=agent,
                started_at=started_at,
                success=False,
                result=None,
                error_reason=str(error),
            )
            raise
        updated_state = result.get("context", {}).get("state", current_state)
        self._record(
            session_key=session_key,
            tool_name=tool_name,
            context_state=updated_state,
            agent=find_agent_for_tool(updated_state, tool_name) or agent,
            started_at=started_at,
            success=True,
            result=result,
        )
        return result

    def runtime_info(self, session_key: str | None = None) -> dict[str, Any]:
        return {
            "runtime": "ContextFlow",
            "evaluation": self.store.evaluation_summary(session_key),
            "telemetry_fields": [
                "scenario", "context_state", "selected_agent", "selected_tool",
                "latency_ms", "success", "fallback_used", "user_confirmation",
            ],
        }

    def dashboard_snapshot(self, session_key: str | None = None) -> dict[str, Any]:
        selected_session = session_key or self.store.latest_session_key()
        if selected_session is None:
            return {
                "session_key": None,
                "state": None,
                "events": [],
                "sensed_events": [],
                "state_transitions": [],
                "drafts": [],
                "draft_cards": [],
                "agent_runs": [],
                "event_source": None,
                "confirmation_boundary": self._default_confirmation_boundary(),
                "evaluation": self.store.evaluation_summary(),
            }
        events = self.store.list_execution_events(selected_session, limit=20)
        sensed_events = self.store.list_sensed_events(selected_session, limit=20)
        drafts = self.store.list_drafts(selected_session, limit=10)
        return {
            "session_key": selected_session,
            "state": self.store.get_state(selected_session),
            "events": events,
            "sensed_events": sensed_events,
            "state_transitions": self.store.list_state_transitions(selected_session, limit=20),
            "drafts": drafts,
            "draft_cards": [self._draft_card(draft) for draft in drafts],
            "agent_runs": self._dashboard_agent_runs(events),
            "event_source": self._dashboard_event_source(sensed_events),
            "confirmation_boundary": self._dashboard_confirmation_boundary(events),
            "evaluation": self.store.evaluation_summary(selected_session),
        }

    @staticmethod
    def _default_confirmation_boundary() -> dict[str, list[str]]:
        return {
            "automatic": ["本地读取", "状态判断", "只读检索", "生成工作草稿"],
            "requires_confirmation": ["发送邮件", "修改日历", "创建外部任务", "执行外部写入"],
        }

    @classmethod
    def _dashboard_confirmation_boundary(cls, events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in events:
            result = event.get("result")
            if isinstance(result, dict) and isinstance(result.get("confirmation_boundary"), dict):
                return result["confirmation_boundary"]
        return cls._default_confirmation_boundary()

    @staticmethod
    def _source_kind(source: str | None) -> str:
        if source == "microsoft-graph-calendar":
            return "real_calendar"
        if source in {"meeting-notes-importer", "local-data-inbox", "demo-desktop-watcher"}:
            return "real_file"
        if source in {"meeting-end-simulator", "calendar-simulator", "demo-control"}:
            return "simulation"
        return "direct_request" if not source else "external_event"

    @classmethod
    def _dashboard_event_source(cls, sensed_events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not sensed_events:
            return None
        event = sensed_events[0]
        return {
            "kind": cls._source_kind(str(event.get("source") or "")),
            "source": event.get("source"),
            "event_type": event.get("event_type"),
            "event_id": event.get("event_id"),
            "occurred_at": event.get("occurred_at"),
        }

    @classmethod
    def _dashboard_agent_runs(cls, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for execution in events:
            result = execution.get("result")
            if not isinstance(result, dict):
                continue
            agents = result.get("triggered_agents")
            if not isinstance(agents, list):
                orchestration = result.get("orchestration")
                agents = orchestration.get("agents") if isinstance(orchestration, dict) else None
            if not isinstance(agents, list):
                continue
            sensed = result.get("event") if isinstance(result.get("event"), dict) else {}
            source = sensed.get("source")
            for item in agents:
                if not isinstance(item, dict) or not item.get("agent"):
                    continue
                runs.append({
                    "agent": item["agent"],
                    "status": item.get("status", "completed"),
                    "reason": item.get("reason", "state-triggered orchestration"),
                    "recorded_at": execution.get("recorded_at"),
                    "tool": execution.get("selected_tool"),
                    "source": source or "direct-request",
                    "source_kind": cls._source_kind(str(source or "")),
                    "trigger_event_id": sensed.get("event_id"),
                })
                if len(runs) >= 12:
                    return runs
        return runs

    @staticmethod
    def _draft_card(draft: dict[str, Any]) -> dict[str, Any]:
        payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
        workflow_type = str(draft.get("workflow_type") or "draft")
        if workflow_type == "meeting_follow_up":
            artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
            action_items = artifacts.get("action_items") if isinstance(artifacts.get("action_items"), list) else []
            return {
                "draft_id": draft.get("draft_id"),
                "workflow_type": workflow_type,
                "status": draft.get("status"),
                "created_at": draft.get("created_at"),
                "title": payload.get("meeting_title") or "会议总结草稿",
                "kind_label": "会议总结",
                "summary": artifacts.get("summary") or "会议内容已整理，等待用户审阅。",
                "items": [
                    {"title": item.get("task"), "meta": item.get("owner") or "待确认负责人", "status": "todo", "depends_on": []}
                    for item in action_items if isinstance(item, dict) and item.get("task")
                ],
                "details": {
                    "decisions": artifacts.get("decisions") if isinstance(artifacts.get("decisions"), list) else [],
                    "email_draft": artifacts.get("email_draft") or "",
                },
                "requires_confirmation": True,
            }
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
        deadline = payload.get("deadline_hours")
        return {
            "draft_id": draft.get("draft_id"),
            "workflow_type": workflow_type,
            "status": draft.get("status"),
            "created_at": draft.get("created_at"),
            "title": payload.get("goal") or "交付计划草稿",
            "kind_label": "交付计划" if workflow_type == "delivery_plan" else workflow_type,
            "summary": f"截止窗口：{deadline} 小时；预测：{plan.get('forecast', '待评估')}" if deadline else "工作草稿等待审阅。",
            "items": [
                {
                    "title": item.get("title"),
                    "meta": " · ".join(part for part in [str(item.get("owner") or "待确认负责人"), f"{item.get('effort_hours', 1)}h"] if part),
                    "status": item.get("status", "todo"),
                    "depends_on": item.get("depends_on", []),
                }
                for item in tasks if isinstance(item, dict) and item.get("title")
            ],
            "details": {
                "deadline_hours": deadline,
                "forecast": plan.get("forecast"),
                "critical_path": plan.get("critical_path", []),
                "critical_path_hours": plan.get("critical_path_hours"),
            },
            "requires_confirmation": True,
        }

    def _execute(self, tool_name: str, payload: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
        session_key = self._require_string(payload, "session_key")
        if tool_name == "context_ingest_event":
            return {"result": "ok", **self.event_state.ingest(session_key, payload)}
        if tool_name == "context_simulate_calendar":
            return {"result": "ok", **self.event_ingress.accept("calendar", payload)}
        if tool_name == "context_import_meeting_notes":
            sensed = self.event_ingress.accept("meeting_notes_imported", payload)
            meeting_path = Path(self._require_string(payload, "path")).expanduser().resolve()
            meeting_text = meeting_path.read_text(encoding="utf-8")
            meeting_title = str(payload.get("title") or meeting_path.stem)
            return self._run_meeting_event(session_key, meeting_title, meeting_text, sensed)
        if tool_name == "context_simulate_meeting_end":
            sensed = self.event_ingress.accept("simulated_meeting_ended", payload)
            return self._run_meeting_event(
                session_key,
                self._require_string(payload, "meeting_title"),
                self._require_string(payload, "meeting_text"),
                sensed,
                fail_after=payload.get("pipeline_fail_after"),
            )
        if tool_name == "context_resume_meeting_pipeline":
            execution_id = self._require_string(payload, "execution_id")
            pipeline = self.meeting_pipeline.resume(execution_id, fail_after=payload.get("pipeline_fail_after"))
            return {"result": "ok", **pipeline}
        if tool_name == "runtime_prepare_approval":
            requested_tool = self._require_string(payload, "requested_tool")
            requested_payload = payload.get("requested_payload")
            plan_version = payload.get("plan_version")
            draft_id = payload.get("draft_id")
            if not isinstance(requested_payload, dict):
                raise ValueError("requested_payload must be an object")
            if not isinstance(plan_version, int):
                raise ValueError("plan_version must be an integer")
            if draft_id is not None and (not isinstance(draft_id, str) or not draft_id.strip()):
                raise ValueError("draft_id must be a non-empty string when provided")
            requested = {**requested_payload, "session_key": session_key}
            prepared = self.tool_gateway.prepare_approval(
                requested_tool,
                requested,
                state,
                plan_version=plan_version,
                plan_reference=draft_id.strip() if isinstance(draft_id, str) else None,
            )
            return {"result": "ok", **prepared}
        if tool_name == "runtime_decide_approval":
            decision = self.tasks.decide_approval(
                self._require_string(payload, "approval_id"),
                self._require_string(payload, "decision"),
            )
            return {"result": "ok", **decision}
        if tool_name == "runtime_execute_approved_operation":
            result = self.tool_gateway.execute_approved(
                self._require_string(payload, "operation_id"),
                self._require_string(payload, "approval_id"),
                state,
            )
            return {"result": "ok", **result}
        if tool_name == "context_update_state":
            evidence = payload.get("evidence", [])
            if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
                raise ValueError("evidence must be a string list")
            raw_confidence = payload.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else None
            context = build_state(session_key, self._require_string(payload, "state"), evidence, confidence)
            self.store.save_state(context)
            return self._context_result(context)
        if tool_name == "context_get_state":
            context = state or build_state(session_key, "idle", ["no stored context"], 1.0)
            return self._context_result(context)
        if tool_name == "rag_ingest":
            document_id = self._require_string(payload, "document_id")
            source_path = payload.get("source_path")
            if source_path is not None:
                if not isinstance(source_path, str) or not source_path.strip():
                    raise ValueError("source_path must be a non-empty string")
                return {"result": "ok", "document": self.rag.ingest_file(session_key, document_id, source_path.strip(), payload.get("title"))}
            content = self._require_string(payload, "content")
            title = self._require_string(payload, "title")
            source_name = payload.get("source_name", title)
            if not isinstance(source_name, str) or not source_name.strip():
                raise ValueError("source_name must be a non-empty string")
            return {"result": "ok", "document": self.rag.ingest(session_key, document_id, title, content, source_name.strip())}
        if tool_name == "rag_search":
            limit = self._read_limit(payload)
            return {"result": "ok", "matches": self.rag.search(session_key, self._require_string(payload, "query"), limit)}
        if tool_name == "rag_answer":
            limit = self._read_limit(payload)
            return {"result": "ok", **self.rag.answer(session_key, self._require_string(payload, "query"), limit)}
        if tool_name == "data_profile":
            return {"result": "ok", "dataset": self.data.profile_file(session_key, self._require_string(payload, "dataset_id"), self._require_string(payload, "source_path"))}
        if tool_name == "data_analyze":
            return {"result": "ok", "analysis": self.data.analyze(session_key, self._require_string(payload, "dataset_id"))}
        if tool_name == "data_chart":
            return {"result": "ok", "chart": self.data.chart(session_key, self._require_string(payload, "dataset_id"), self._require_string(payload, "x_column"), self._require_string(payload, "y_column"))}
        if tool_name == "desktop_search_files":
            return {"result": "ok", "search": self.desktop.search(self._require_string(payload, "root_path"), self._require_string(payload, "query"), self._read_limit(payload))}
        if tool_name == "desktop_plan_archive":
            relative_paths = payload.get("relative_paths")
            if not isinstance(relative_paths, list) or not all(isinstance(path, str) for path in relative_paths):
                raise ValueError("relative_paths must be a string list")
            plan = self.desktop.create_archive_plan(session_key, self._require_string(payload, "root_path"), relative_paths, payload.get("archive_folder", ".contextflow_archive"))
            return {"result": "ok", "plan": plan, "message": "Archive plan is ready. Present its entries to the user and call desktop_confirm_archive only after an explicit decision."}
        if tool_name == "desktop_confirm_archive":
            return {"result": "ok", "plan": self.desktop.confirm_archive_plan(session_key, self._require_string(payload, "plan_id"), self._require_string(payload, "decision"))}
        if tool_name == "desktop_execute_archive":
            return {"result": "ok", **self.desktop.execute_archive_plan(session_key, self._require_string(payload, "plan_id"))}
        if tool_name == "desktop_undo_archive":
            return {"result": "ok", **self.desktop.undo_archive_operation(session_key, self._require_string(payload, "operation_id"))}
        if tool_name == "office_process_meeting":
            state = self._meeting_ended_context(session_key, state)
            title = self._require_string(payload, "meeting_title")
            artifacts = self.office.process_meeting(title, self._require_string(payload, "meeting_text"))
            draft = meeting_draft(session_key, title, state, artifacts)
            self.store.save_draft(draft)
            orchestration = self.meeting_orchestrator.after_meeting(
                session_key,
                title,
                self._require_string(payload, "meeting_text"),
                artifacts,
            )
            final_context = self.store.get_state(session_key) or state
            return {
                "result": "ok",
                "context": final_context,
                "draft": draft,
                "artifacts": artifacts,
                "orchestration": orchestration,
                "message": "Office workflow draft and state-triggered next steps are ready for review.",
            }
        if tool_name == "workflow_prepare_meeting":
            state = self._meeting_ended_context(session_key, state)
            draft = meeting_draft(session_key, self._require_string(payload, "meeting_title"), state)
            self.store.save_draft(draft)
            return {"result": "ok", "context": state, "draft": draft, "message": "Meeting workflow draft is ready for review."}
        if tool_name == "workflow_plan_delivery":
            tasks = payload.get("existing_tasks", [])
            if not isinstance(tasks, list) or not all(isinstance(task, str) for task in tasks):
                raise ValueError("existing_tasks must be a string list")
            hours = payload.get("deadline_hours")
            if not isinstance(hours, int):
                raise ValueError("deadline_hours must be an integer")
            goal = self._require_string(payload, "goal")
            state = self._deadline_risk_context(session_key, state, goal, hours)
            task_specs = payload.get("task_specs")
            if task_specs is not None and (not isinstance(task_specs, list) or not all(isinstance(task, dict) for task in task_specs)):
                raise ValueError("task_specs must be an object list")
            existing = self.store.find_latest_draft(session_key, "delivery_plan")
            if (
                not tasks
                and not task_specs
                and existing is not None
                and existing["payload"].get("orchestration_source") == "meeting_state_trigger"
                and existing["payload"].get("goal") == goal
                and existing["payload"].get("deadline_hours") == hours
            ):
                return {
                    "result": "ok",
                    "deduplicated": True,
                    "draft": existing,
                    "message": "A matching delivery draft was already created by the meeting-state orchestrator.",
                }
            plan = self.delivery.plan(goal, hours, task_specs) if task_specs else None
            draft = delivery_plan_draft(session_key, goal, hours, state, tasks, plan)
            self.store.save_draft(draft)
            return {"result": "ok", "context": state, "draft": draft, "message": "Delivery plan draft is ready for review."}
        if tool_name == "delivery_replan":
            if state is None or state["state"] != "deadline_risk":
                raise ValueError("delivery_replan requires deadline_risk state")
            previous = self.store.get_draft(self._require_string(payload, "draft_id"))
            if previous is None or previous["workflow_type"] != "delivery_plan":
                raise ValueError("delivery draft not found")
            plan = previous["payload"].get("plan")
            if not plan:
                raise ValueError("delivery draft does not contain structured task_specs")
            failed_ids = payload.get("failed_task_ids")
            if not isinstance(failed_ids, list) or not all(isinstance(task_id, str) for task_id in failed_ids):
                raise ValueError("failed_task_ids must be a string list")
            revised_plan = self.delivery.replan(plan, failed_ids)
            if previous["session_key"] != session_key:
                raise ValueError("delivery draft not found in the current session")
            if previous["status"] in {"rejected", "superseded"}:
                raise ValueError("delivery draft is no longer eligible for replanning")
            self.store.update_draft_status(previous["draft_id"], "superseded", self._now_iso())
            draft = delivery_plan_draft(
                session_key,
                revised_plan["goal"],
                revised_plan["deadline_hours"],
                state,
                plan=revised_plan,
                plan_version=previous["plan_version"] + 1,
            )
            self.store.save_draft(draft)
            return {"result": "ok", "draft": draft, "replanned_from": previous["draft_id"], "fallback_required": revised_plan["fallback_required"]}
        if tool_name == "workflow_confirm_action":
            draft_id = self._require_string(payload, "draft_id")
            decision = self._require_string(payload, "decision")
            if decision not in {"approved", "rejected"}:
                raise ValueError("decision must be approved or rejected")
            existing_draft = self.store.get_draft(draft_id)
            if existing_draft is None or existing_draft["session_key"] != session_key:
                raise ValueError("draft not found in the current session")
            if existing_draft["status"] in {"approved", "rejected"}:
                if existing_draft["status"] == decision:
                    return {
                        "result": "ok", "draft_id": draft_id, "status": decision,
                        "deduplicated": True,
                        "next_step": "external write remains stubbed in this prototype",
                    }
                raise ValueError("draft already has a final review decision")
            draft = self.store.update_draft_status(draft_id, decision, self._now_iso())
            return {"result": "ok", "draft_id": draft_id, "status": decision, "next_step": "external write remains stubbed in this prototype"}
        raise ValueError(f"unsupported tool: {tool_name}")

    def _context_result(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": "ok",
            "context": context,
            "allowed_capabilities": allowed_capabilities(context["state"]),
            "state_contract": describe_state(context["state"]),
            "recommended_agents": find_agents_for_state(context["state"]),
        }

    def _run_meeting_event(
        self,
        session_key: str,
        meeting_title: str,
        meeting_text: str,
        sensed: dict[str, Any],
        fail_after: str | None = None,
    ) -> dict[str, Any]:
        """Run the one meeting orchestration path after atomic event acceptance."""
        if sensed["deduplicated"]:
            return {
                "result": "ok", **sensed,
                "context": self.store.get_state(session_key) or sensed["context"],
                "state_sequence": [],
                "duplicate_protected": True,
                "new_drafts_created": 0,
                "existing_drafts": {
                    "meeting": self.store.find_latest_draft(session_key, "meeting_follow_up"),
                    "delivery": self.store.find_latest_draft(session_key, "delivery_plan"),
                },
                "message": "Event already processed; Agent orchestration and draft creation were skipped.",
            }
        meeting_context = sensed["context"]
        if fail_after is not None and fail_after not in self.meeting_pipeline.nodes:
            raise ValueError("pipeline_fail_after must name a meeting pipeline node")
        pipeline = self.meeting_pipeline.start(
            session_key=session_key,
            meeting_title=meeting_title,
            meeting_text=meeting_text,
            meeting_context=meeting_context,
            fail_after=fail_after,
        )
        if pipeline["interrupted"]:
            return {"result": "ok", **sensed, "context": meeting_context, "state_sequence": [meeting_context["state"]], **pipeline}
        artifacts = pipeline["artifacts"]
        draft = pipeline["meeting_draft"]
        orchestration = pipeline["orchestration"]
        final_context = self.store.get_state(session_key) or meeting_context
        state_sequence = [meeting_context["state"]]
        if final_context["state"] != meeting_context["state"]:
            state_sequence.append(final_context["state"])
            self.store.record_state_transition({
                "transition_id": uuid4().hex,
                "session_key": session_key,
                "from_state": meeting_context["state"],
                "to_state": final_context["state"],
                "confidence": final_context["confidence"],
                "evidence": final_context["evidence"],
                "trigger_event_id": sensed["event"]["event_id"],
                "recorded_at": final_context["updated_at"],
            })
        return {
            "result": "ok", **sensed,
            "context": final_context,
            "state_sequence": state_sequence,
            "duplicate_protected": False,
            "new_drafts_created": 2 if orchestration["delivery"]["status"] == "planned" else 1,
            "artifacts": artifacts,
            "meeting_draft": draft,
            "orchestration": orchestration,
            "triggered_agents": orchestration["agents"],
            "confirmation_boundary": {
                "automatic": ["meeting summary", "decision and action extraction", "read-only RAG", "delivery-plan draft"],
                "requires_confirmation": describe_state(final_context["state"])["confirmation_required_for"],
            },
        }

    def _meeting_ended_context(self, session_key: str, state: dict[str, Any] | None) -> dict[str, Any]:
        """Record the implicit state transition owned by a meeting-end workflow."""
        if state is not None and state["state"] == "meeting_ended":
            return state
        context = build_state(
            session_key,
            "meeting_ended",
            ["meeting workflow invoked from an explicit meeting-ended request"],
            0.96,
        )
        self.store.save_state(context)
        return context

    def _deadline_risk_context(
        self,
        session_key: str,
        state: dict[str, Any] | None,
        goal: str,
        deadline_hours: int,
    ) -> dict[str, Any]:
        """Own the implicit state transition in an explicit delivery-risk request."""
        if state is not None:
            return state
        context = build_state(
            session_key,
            "deadline_risk",
            [
                f"explicit delivery deadline within {deadline_hours} hours",
                f"incomplete delivery goal: {goal}",
            ],
            0.96,
        )
        self.store.save_state(context)
        return context

    def _record(self, *, session_key: str, tool_name: str, context_state: str, agent: dict[str, object] | None, started_at: float, success: bool, result: dict[str, Any] | None, error_reason: str | None = None) -> None:
        confirmation = result.get("status") if result and tool_name == "workflow_confirm_action" else None
        self.store.record_execution({
            "session_key": session_key,
            "scenario": agent["scenario"] if agent else "runtime_control",
            "context_state": context_state,
            "selected_agent": agent["name"] if agent else None,
            "selected_tool": tool_name,
            "latency_ms": (perf_counter() - started_at) * 1000,
            "success": success,
            "fallback_used": False,
            "user_confirmation": confirmation,
            "error_reason": error_reason,
            "result": result,
            "recorded_at": self._now_iso(),
        })

    @staticmethod
    def _require_string(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing or invalid {field}")
        return value.strip()

    @staticmethod
    def _read_limit(payload: dict[str, Any]) -> int:
        limit = payload.get("limit", 3)
        if not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("limit must be an integer between 1 and 10")
        return limit

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

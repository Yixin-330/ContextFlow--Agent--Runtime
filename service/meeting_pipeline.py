from __future__ import annotations

from typing import Any

try:
    from .meeting_orchestrator import MeetingOrchestrator
    from .office_flow import OfficeFlow
    from .storage import Storage
    from .task_runtime import TaskRuntime
    from .workflow_engine import meeting_draft
except ImportError:  # pragma: no cover
    from meeting_orchestrator import MeetingOrchestrator
    from office_flow import OfficeFlow
    from storage import Storage
    from task_runtime import TaskRuntime
    from workflow_engine import meeting_draft


class MeetingPipeline:
    """Run the durable meeting workflow through a small, resumable interface."""

    nodes = ("process_meeting", "retrieve_evidence", "plan_delivery")

    def __init__(self, store: Storage, tasks: TaskRuntime, office: OfficeFlow, orchestrator: MeetingOrchestrator) -> None:
        self.store = store
        self.tasks = tasks
        self.office = office
        self.orchestrator = orchestrator

    def start(self, *, session_key: str, meeting_title: str, meeting_text: str, meeting_context: dict[str, Any], fail_after: str | None = None) -> dict[str, Any]:
        created = self.tasks.start(session_key=session_key, goal=f"Follow up on meeting: {meeting_title}", workflow_version="meeting-pipeline-v1")
        snapshot = {
            "session_key": session_key,
            "meeting_title": meeting_title,
            "meeting_text": meeting_text,
            "meeting_context": meeting_context,
            "artifacts": None,
            "meeting_draft": None,
            "rag": None,
            "delivery": None,
        }
        return self._run(created["execution"], snapshot, start_index=0, fail_after=fail_after)

    def resume(self, execution_id: str, *, fail_after: str | None = None) -> dict[str, Any]:
        resumed = self.tasks.resume(execution_id)
        checkpoint = resumed["checkpoint"]
        snapshot = checkpoint["context_snapshot"]
        if not isinstance(snapshot, dict) or snapshot.get("meeting_title") is None:
            raise ValueError("checkpoint does not contain a meeting pipeline snapshot")
        try:
            start_index = self.nodes.index(checkpoint["current_node"]) + 1
        except ValueError as error:
            raise ValueError("checkpoint does not belong to the meeting pipeline") from error
        return self._run(resumed["execution"], snapshot, start_index=start_index, fail_after=fail_after)

    def _run(self, execution: dict[str, Any], snapshot: dict[str, Any], *, start_index: int, fail_after: str | None) -> dict[str, Any]:
        try:
            for node in self.nodes[start_index:]:
                self._run_node(node, snapshot)
                self.tasks.checkpoint(
                    execution["execution_id"],
                    current_node=node,
                    tool_name=node,
                    result=self._node_result(node, snapshot),
                    context_snapshot=snapshot,
                    pending_nodes=list(self.nodes[self.nodes.index(node) + 1:]),
                )
                if fail_after == node:
                    self.tasks.mark_interrupted(execution["execution_id"])
                    return self._result(execution, snapshot, interrupted=True)
        except Exception:
            self.tasks.mark_interrupted(execution["execution_id"])
            raise
        self.tasks.complete(execution["execution_id"])
        return self._result(execution, snapshot, interrupted=False)

    def _run_node(self, node: str, snapshot: dict[str, Any]) -> None:
        session_key = str(snapshot["session_key"])
        title = str(snapshot["meeting_title"])
        if node == "process_meeting":
            artifacts = self.office.process_meeting(title, str(snapshot["meeting_text"]))
            draft = meeting_draft(session_key, title, snapshot["meeting_context"], artifacts)
            self.store.save_draft(draft)
            snapshot["artifacts"] = artifacts
            snapshot["meeting_draft"] = draft
            return
        if node == "retrieve_evidence":
            snapshot["rag"] = self.orchestrator.retrieve_evidence(session_key, title, snapshot["artifacts"])
            return
        if node == "plan_delivery":
            snapshot["delivery"] = self.orchestrator.plan_delivery(
                session_key,
                title,
                str(snapshot["meeting_text"]),
                snapshot["artifacts"],
            )
            return
        raise ValueError(f"unsupported meeting pipeline node: {node}")

    @staticmethod
    def _node_result(node: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "process_meeting": {"draft_id": snapshot["meeting_draft"]["draft_id"]},
            "retrieve_evidence": snapshot["rag"],
            "plan_delivery": snapshot["delivery"],
        }[node]

    def _result(self, execution: dict[str, Any], snapshot: dict[str, Any], *, interrupted: bool) -> dict[str, Any]:
        delivery = snapshot["delivery"]
        rag = snapshot["rag"]
        return {
            "execution": self.store.get_runtime_execution(execution["execution_id"]),
            "interrupted": interrupted,
            "artifacts": snapshot["artifacts"],
            "meeting_draft": snapshot["meeting_draft"],
            "orchestration": self._orchestration(snapshot, rag, delivery) if delivery is not None else None,
        }

    @staticmethod
    def _orchestration(snapshot: dict[str, Any], rag: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "state_triggered_meeting_pipeline_v1",
            "trigger": {"state": "meeting_ended", "meeting_title": snapshot["meeting_title"]},
            "agents": [
                {"agent": "MeetingActionAgent", "status": "completed", "reason": "meeting_ended state"},
                {"agent": "LocalRAGAgent", "status": "completed" if rag["status"] == "matched" else "no_evidence", "reason": rag["reason"]},
                {"agent": "DeliveryPlannerAgent", "status": delivery["status"], "reason": delivery["reason"]},
            ],
            "rag": rag,
            "delivery": delivery,
            "presentation": {
                "tone": "warm, concise, project-partner style",
                "facts": {
                    "state_transition": "meeting_ended -> deadline_risk" if delivery["status"] == "planned" else "meeting_ended",
                    "delivery_status": delivery["status"],
                    "rag_status": rag["status"],
                },
                "must_surface": ["state_transition", "agents", "rag", "delivery", "confirmation_boundary"],
                "prohibited_claims": ["unverified motives", "external pressure", "unmentioned people or deadlines"],
            },
            "safety": "RAG is read-only. Delivery plans are drafts; no external task-system write occurs automatically.",
        }

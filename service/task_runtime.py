from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

try:
    from .storage import Storage
except ImportError:  # pragma: no cover
    from storage import Storage


class TaskRuntime:
    """Own the durable lifecycle for one task and its executions."""

    def __init__(self, store: Storage) -> None:
        self.store = store

    def start(self, *, session_key: str, goal: str, workflow_version: str = "runtime-v1", budget: dict[str, int] | None = None) -> dict[str, Any]:
        timestamp = self._now()
        task = {
            "task_id": f"task-{uuid4().hex[:12]}",
            "session_key": session_key,
            "goal": goal,
            "workflow_version": workflow_version,
            "status": "running",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        execution = {
            "execution_id": f"execution-{uuid4().hex[:12]}",
            "task_id": task["task_id"],
            "status": "running",
            "current_node": None,
            "budget": budget or {"max_tool_calls": 20, "max_elapsed_seconds": 300},
            "checkpoint_version": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.store.save_runtime_task(task)
        self.store.save_runtime_execution(execution)
        self.trace(execution["execution_id"], "TaskCreated", {"task_id": task["task_id"], "goal": goal})
        return {"task": task, "execution": execution}

    def checkpoint(
        self,
        execution_id: str,
        *,
        current_node: str,
        tool_name: str,
        result: dict[str, Any],
        context_snapshot: dict[str, Any] | None = None,
        pending_nodes: list[str] | None = None,
    ) -> dict[str, Any]:
        execution = self._require_execution(execution_id)
        previous = self.store.latest_runtime_checkpoint(execution_id)
        version = execution["checkpoint_version"] + 1
        completed = list(previous["completed_nodes"]) if previous else []
        if current_node not in completed:
            completed.append(current_node)
        tool_results = dict(previous["tool_results"]) if previous else {}
        tool_results[current_node] = {"tool_name": tool_name, "result": result}
        checkpoint = {
            "checkpoint_id": f"checkpoint-{uuid4().hex[:12]}",
            "execution_id": execution_id,
            "version": version,
            "current_node": current_node,
            "completed_nodes": completed,
            "pending_nodes": pending_nodes or [],
            "context_snapshot": context_snapshot or {"task_id": execution["task_id"]},
            "tool_results": tool_results,
            "created_at": self._now(),
        }
        self.store.save_runtime_checkpoint(checkpoint)
        updated = self.store.update_runtime_execution(
            execution_id,
            status="running",
            current_node=current_node,
            checkpoint_version=version,
            updated_at=self._now(),
        )
        assert updated is not None
        self.trace(execution_id, "CheckpointSaved", {"checkpoint_id": checkpoint["checkpoint_id"], "node": current_node})
        return checkpoint

    def resume(self, execution_id: str) -> dict[str, Any]:
        execution = self._require_execution(execution_id)
        checkpoint = self.store.latest_runtime_checkpoint(execution_id)
        if checkpoint is None:
            raise ValueError("execution has no checkpoint to resume")
        if execution["status"] not in {"interrupted", "retryable_failed"}:
            raise ValueError("execution is not resumable")
        updated = self.store.update_runtime_execution(
            execution_id,
            status="running",
            current_node=checkpoint["current_node"],
            checkpoint_version=checkpoint["version"],
            updated_at=self._now(),
        )
        assert updated is not None
        self.trace(execution_id, "ExecutionResumed", {"checkpoint_id": checkpoint["checkpoint_id"]})
        return {"execution": updated, "checkpoint": checkpoint}

    def mark_interrupted(self, execution_id: str) -> dict[str, Any]:
        execution = self._require_execution(execution_id)
        updated = self.store.update_runtime_execution(
            execution_id,
            status="interrupted",
            current_node=execution["current_node"],
            checkpoint_version=execution["checkpoint_version"],
            updated_at=self._now(),
        )
        assert updated is not None
        self.trace(execution_id, "ExecutionInterrupted", {})
        return updated

    def complete(self, execution_id: str) -> dict[str, Any]:
        execution = self._require_execution(execution_id)
        timestamp = self._now()
        updated = self.store.update_runtime_execution(
            execution_id,
            status="completed",
            current_node=execution["current_node"],
            checkpoint_version=execution["checkpoint_version"],
            updated_at=timestamp,
        )
        assert updated is not None
        task = self.store.update_runtime_task_status(execution["task_id"], status="completed", updated_at=timestamp)
        assert task is not None
        self.trace(execution_id, "TaskCompleted", {"task_id": task["task_id"]})
        return updated

    def request_approval(
        self,
        execution_id: str,
        *,
        plan_version: int,
        operation_ids: list[str],
        plan_reference: str | None = None,
    ) -> dict[str, Any]:
        if plan_version < 1 or not operation_ids:
            raise ValueError("approval requires a positive plan version and at least one operation")
        execution = self._require_execution(execution_id)
        timestamp = self._now()
        approval = {
            "approval_id": f"approval-{uuid4().hex[:12]}",
            "task_id": execution["task_id"],
            "execution_id": execution_id,
            "plan_version": plan_version,
            "operation_ids": operation_ids,
            "plan_reference": plan_reference,
            "decision": "pending",
            "created_at": timestamp,
            "decided_at": None,
        }
        self.store.save_runtime_approval(approval)
        updated = self.store.update_runtime_execution(
            execution_id,
            status="waiting_for_approval",
            current_node=execution["current_node"],
            checkpoint_version=execution["checkpoint_version"],
            updated_at=timestamp,
        )
        assert updated is not None
        self.trace(execution_id, "ApprovalRequested", {
            "approval_id": approval["approval_id"],
            "plan_version": plan_version,
            "plan_reference": plan_reference,
        })
        return approval

    def decide_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision must be approved or rejected")
        approval = self.store.get_runtime_approval(approval_id)
        if approval is None:
            raise ValueError("runtime approval not found")
        updated_approval = self.store.decide_runtime_approval(
            approval_id,
            decision=decision,
            decided_at=self._now(),
        )
        if updated_approval is None:
            raise ValueError("runtime approval already has a final decision")
        execution = self._require_execution(approval["execution_id"])
        updated_execution = self.store.update_runtime_execution(
            execution["execution_id"],
            status="running" if decision == "approved" else "failed",
            current_node=execution["current_node"],
            checkpoint_version=execution["checkpoint_version"],
            updated_at=self._now(),
        )
        assert updated_execution is not None
        self.trace(execution["execution_id"], "ApprovalDecided", {"approval_id": approval_id, "decision": decision})
        return {"approval": updated_approval, "execution": updated_execution}

    def trace(self, execution_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.store.append_runtime_trace({
            "trace_id": f"trace-{uuid4().hex[:12]}",
            "execution_id": execution_id,
            "event_type": event_type,
            "payload": payload,
            "recorded_at": self._now(),
        })

    def _require_execution(self, execution_id: str) -> dict[str, Any]:
        execution = self.store.get_runtime_execution(execution_id)
        if execution is None:
            raise ValueError("runtime execution not found")
        return execution

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

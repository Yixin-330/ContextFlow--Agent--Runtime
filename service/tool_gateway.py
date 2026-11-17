from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

try:
    from .agent_registry import find_agent_for_tool
    from .storage import Storage
    from .task_runtime import TaskRuntime
except ImportError:  # pragma: no cover
    from agent_registry import find_agent_for_tool
    from storage import Storage
    from task_runtime import TaskRuntime


ExecuteTool = Callable[[str, dict[str, Any], dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required_fields: tuple[str, ...]
    side_effect: str
    confirmation_mode: str


TOOL_SPECS = {
    "rag_ingest": ToolSpec("rag_ingest", ("document_id",), "read_write_local", "not_required"),
    "rag_search": ToolSpec("rag_search", ("query",), "read_only", "not_required"),
    "rag_answer": ToolSpec("rag_answer", ("query",), "read_only", "not_required"),
    "data_profile": ToolSpec("data_profile", ("dataset_id", "source_path"), "read_write_local", "not_required"),
    "data_analyze": ToolSpec("data_analyze", ("dataset_id",), "read_only", "not_required"),
    "data_chart": ToolSpec("data_chart", ("dataset_id", "x_column", "y_column"), "read_write_local", "not_required"),
    "desktop_search_files": ToolSpec("desktop_search_files", ("root_path", "query"), "read_only", "not_required"),
    "desktop_plan_archive": ToolSpec("desktop_plan_archive", ("root_path", "relative_paths"), "read_write_local", "not_required"),
    "desktop_confirm_archive": ToolSpec("desktop_confirm_archive", ("plan_id", "decision"), "approval", "not_required"),
    "desktop_execute_archive": ToolSpec("desktop_execute_archive", ("plan_id",), "local_write", "tool_owned"),
    "desktop_undo_archive": ToolSpec("desktop_undo_archive", ("operation_id",), "local_write", "tool_owned"),
}


class ToolGateway:
    """The seam where validation, ownership, durable operations, and traces meet."""

    def __init__(self, store: Storage, tasks: TaskRuntime, execute_tool: ExecuteTool) -> None:
        self.store = store
        self.tasks = tasks
        self.execute_tool = execute_tool

    def handles(self, tool_name: str) -> bool:
        return tool_name in TOOL_SPECS

    def execute(self, tool_name: str, payload: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
        spec = TOOL_SPECS[tool_name]
        session_key = self._required_text(payload, "session_key")
        self._validate(spec, payload)
        current_state = state["state"] if state else "idle"
        agent = find_agent_for_tool(current_state, tool_name)
        if agent is None:
            raise ValueError(f"tool {tool_name} is not permitted in state {current_state}")

        task, execution = self._execution_for(payload, session_key, tool_name)
        operation = {
            "operation_id": f"operation-{uuid4().hex[:12]}",
            "task_id": task["task_id"],
            "execution_id": execution["execution_id"],
            "tool_name": tool_name,
            "idempotency_key": self._idempotency_key(payload, tool_name),
            "status": "running",
            "request": self._stable_request(payload),
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        existing, created = self.store.begin_runtime_operation(operation)
        if not created:
            if existing["status"] == "completed" and existing["result"] is not None:
                return self._with_runtime_metadata(existing["result"], task, execution, existing, deduplicated=True)
            if existing["status"] == "running":
                raise ValueError("tool operation is already running; reconcile before retrying")
            raise ValueError("previous tool operation requires reconciliation before retrying")

        self.tasks.trace(execution["execution_id"], "ToolInvoked", {"tool_name": tool_name, "operation_id": operation["operation_id"]})
        try:
            result = self.execute_tool(tool_name, payload, state)
        except Exception as error:
            self.store.finish_runtime_operation(
                operation["operation_id"],
                status="failed",
                result=None,
                error={"type": type(error).__name__, "message": str(error)},
                updated_at=self._now(),
            )
            self.tasks.trace(execution["execution_id"], "ToolFailed", {"tool_name": tool_name, "operation_id": operation["operation_id"]})
            raise

        finished = self.store.finish_runtime_operation(
            operation["operation_id"],
            status="completed",
            result=result,
            error=None,
            updated_at=self._now(),
        )
        assert finished is not None
        checkpoint = self.tasks.checkpoint(
            execution["execution_id"],
            current_node=f"tool:{tool_name}",
            tool_name=tool_name,
            result=result,
        )
        self.tasks.trace(execution["execution_id"], "ToolObserved", {"tool_name": tool_name, "operation_id": operation["operation_id"]})
        return self._with_runtime_metadata(result, task, execution, finished, checkpoint=checkpoint, deduplicated=False)

    def prepare_approval(
        self,
        tool_name: str,
        payload: dict[str, Any],
        state: dict[str, Any] | None,
        *,
        plan_version: int,
        plan_reference: str | None = None,
    ) -> dict[str, Any]:
        if tool_name not in TOOL_SPECS:
            raise ValueError(f"unsupported gateway tool: {tool_name}")
        if plan_version < 1:
            raise ValueError("plan_version must be positive")
        spec = TOOL_SPECS[tool_name]
        session_key = self._required_text(payload, "session_key")
        if plan_reference is not None:
            self._assert_plan_reference_current(plan_reference, session_key, plan_version)
        self._validate(spec, payload)
        current_state = state["state"] if state else "idle"
        if find_agent_for_tool(current_state, tool_name) is None:
            raise ValueError(f"tool {tool_name} is not permitted in state {current_state}")
        task, execution = self._execution_for(payload, session_key, tool_name)
        operation, created = self.store.prepare_runtime_operation({
            "operation_id": f"operation-{uuid4().hex[:12]}",
            "task_id": task["task_id"],
            "execution_id": execution["execution_id"],
            "tool_name": tool_name,
            "idempotency_key": self._idempotency_key(payload, tool_name),
            "request": self._stable_request(payload),
            "created_at": self._now(),
            "updated_at": self._now(),
        })
        if operation["status"] == "completed":
            return self._with_runtime_metadata(operation["result"] or {}, task, execution, operation, deduplicated=True)
        if not created:
            raise ValueError("operation is already awaiting approval")
        if operation["status"] != "awaiting_approval":
            raise ValueError("prepared operation requires reconciliation before requesting approval")
        approval = self.tasks.request_approval(
            execution["execution_id"],
            plan_version=plan_version,
            operation_ids=[operation["operation_id"]],
            plan_reference=plan_reference,
        )
        self.tasks.trace(execution["execution_id"], "OperationPrepared", {"operation_id": operation["operation_id"], "tool_name": tool_name})
        return {
            "task": task,
            "execution": self.store.get_runtime_execution(execution["execution_id"]),
            "operation": operation,
            "approval": approval,
            "created": created,
        }

    def execute_approved(self, operation_id: str, approval_id: str, state: dict[str, Any] | None) -> dict[str, Any]:
        operation = self.store.get_runtime_operation(operation_id)
        approval = self.store.get_runtime_approval(approval_id)
        if operation is None or approval is None:
            raise ValueError("operation or approval not found")
        if approval["decision"] != "approved":
            raise ValueError("operation requires an approved runtime approval")
        if operation["operation_id"] not in approval["operation_ids"]:
            raise ValueError("approval does not authorize this operation")
        if operation["task_id"] != approval["task_id"] or operation["execution_id"] != approval["execution_id"]:
            raise ValueError("approval and operation belong to different executions")
        if approval.get("plan_reference") is not None:
            task = self.store.get_runtime_task(operation["task_id"])
            assert task is not None
            self._assert_plan_reference_current(
                approval["plan_reference"],
                task["session_key"],
                approval["plan_version"],
            )
        spec = TOOL_SPECS.get(operation["tool_name"])
        if spec is None:
            raise ValueError("prepared operation has an unknown tool")
        current_state = state["state"] if state else "idle"
        if find_agent_for_tool(current_state, operation["tool_name"]) is None:
            raise ValueError(f"tool {operation['tool_name']} is not permitted in state {current_state}")
        claimed = self.store.claim_prepared_runtime_operation(operation_id, updated_at=self._now())
        if claimed is None:
            if operation["status"] == "completed" and operation["result"] is not None:
                task = self.store.get_runtime_task(operation["task_id"])
                execution = self.store.get_runtime_execution(operation["execution_id"])
                assert task is not None and execution is not None
                return self._with_runtime_metadata(operation["result"], task, execution, operation, deduplicated=True)
            raise ValueError("operation is no longer awaiting approval")
        task = self.store.get_runtime_task(claimed["task_id"])
        execution = self.store.get_runtime_execution(claimed["execution_id"])
        assert task is not None and execution is not None
        self.tasks.trace(execution["execution_id"], "ApprovedOperationStarted", {"operation_id": operation_id})
        try:
            result = self.execute_tool(claimed["tool_name"], claimed["request"], state)
        except Exception as error:
            self.store.finish_runtime_operation(
                operation_id,
                status="failed",
                result=None,
                error={"type": type(error).__name__, "message": str(error)},
                updated_at=self._now(),
            )
            raise
        finished = self.store.finish_runtime_operation(
            operation_id,
            status="completed",
            result=result,
            error=None,
            updated_at=self._now(),
        )
        assert finished is not None
        checkpoint = self.tasks.checkpoint(
            execution["execution_id"],
            current_node=f"approved_tool:{claimed['tool_name']}",
            tool_name=claimed["tool_name"],
            result=result,
        )
        completed = self.tasks.complete(execution["execution_id"])
        return self._with_runtime_metadata(result, task, completed, finished, checkpoint=checkpoint, deduplicated=False)

    def _execution_for(self, payload: dict[str, Any], session_key: str, tool_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        task_id = payload.get("task_id")
        execution_id = payload.get("execution_id")
        if task_id is None and execution_id is None:
            created = self.tasks.start(session_key=session_key, goal=f"Run {tool_name}")
            return created["task"], created["execution"]
        if not isinstance(task_id, str) or not task_id.strip() or not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("task_id and execution_id must be supplied together")
        task = self.store.get_runtime_task(task_id)
        execution = self.store.get_runtime_execution(execution_id)
        if task is None or execution is None or execution["task_id"] != task["task_id"] or task["session_key"] != session_key:
            raise ValueError("task and execution do not belong to the current session")
        if execution["status"] != "running":
            raise ValueError("runtime execution is not running")
        return task, execution

    def _assert_plan_reference_current(self, draft_id: str, session_key: str, plan_version: int) -> None:
        draft = self.store.get_draft(draft_id)
        if draft is None or draft["session_key"] != session_key:
            raise ValueError("referenced plan draft was not found in the current session")
        if draft["plan_version"] != plan_version:
            raise ValueError("referenced plan version no longer matches the requested approval")
        if draft["status"] in {"rejected", "superseded"}:
            raise ValueError("referenced plan is no longer executable")

    @staticmethod
    def _validate(spec: ToolSpec, payload: dict[str, Any]) -> None:
        for field in spec.required_fields:
            if field not in payload or payload[field] is None or payload[field] == "":
                raise ValueError(f"{spec.name} requires {field}")

    @staticmethod
    def _stable_request(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key not in {"execution_id", "task_id", "idempotency_key"}}

    def _idempotency_key(self, payload: dict[str, Any], tool_name: str) -> str:
        explicit = payload.get("idempotency_key")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        encoded = json.dumps({"tool_name": tool_name, "payload": self._stable_request(payload)}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _with_runtime_metadata(result: dict[str, Any], task: dict[str, Any], execution: dict[str, Any], operation: dict[str, Any], *, checkpoint: dict[str, Any] | None = None, deduplicated: bool) -> dict[str, Any]:
        return {
            **result,
            "runtime": {
                "task_id": task["task_id"],
                "execution_id": execution["execution_id"],
                "operation_id": operation["operation_id"],
                "checkpoint_id": checkpoint["checkpoint_id"] if checkpoint else None,
                "deduplicated": deduplicated,
            },
        }

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

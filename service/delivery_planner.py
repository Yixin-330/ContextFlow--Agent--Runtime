from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class DeliveryPlanner:
    """Validate a task DAG, expose its critical path, and replan after failures."""

    def plan(self, goal: str, deadline_hours: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if deadline_hours <= 0:
            raise ValueError("deadline_hours must be positive")
        normalized = self._normalize(tasks)
        order = self._topological_order(normalized)
        path, critical_hours = self._critical_path(normalized, order)
        ready = [task for task in normalized if task["status"] == "todo" and all(self._task(normalized, dep)["status"] == "done" for dep in task["depends_on"])]
        forecast = "at_risk" if critical_hours > deadline_hours else "on_track"
        return {
            "goal": goal,
            "deadline_hours": deadline_hours,
            "tasks": normalized,
            "topological_order": order,
            "critical_path": path,
            "critical_path_hours": critical_hours,
            "forecast": forecast,
            "next_three_actions": [self._public_task(task) for task in ready[:3]],
            "strategy": "minimum_viable_delivery_first",
        }

    def replan(self, plan: dict[str, Any], failed_task_ids: list[str]) -> dict[str, Any]:
        failed = set(failed_task_ids)
        tasks = [dict(task) for task in plan["tasks"]]
        ids = {task["id"] for task in tasks}
        unknown = failed - ids
        if unknown:
            raise ValueError(f"unknown failed task ids: {sorted(unknown)}")
        for task in tasks:
            if task["id"] in failed:
                task["status"] = "failed"
        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task["status"] == "todo" and any(self._task(tasks, dep)["status"] in {"failed", "blocked"} for dep in task["depends_on"]):
                    task["status"] = "blocked"
                    changed = True
        revised = self.plan(plan["goal"], plan["deadline_hours"], tasks)
        revised["replan_reason"] = {"failed_task_ids": sorted(failed)}
        revised["fallback_required"] = any(task["status"] in {"failed", "blocked"} for task in revised["tasks"])
        return revised

    def _normalize(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tasks:
            raise ValueError("task_specs must contain at least one task")
        normalized = []
        ids: set[str] = set()
        for raw in tasks:
            task_id = self._require_string(raw, "id")
            if task_id in ids:
                raise ValueError(f"duplicate task id: {task_id}")
            ids.add(task_id)
            depends_on = raw.get("depends_on", [])
            if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
                raise ValueError("depends_on must be a string list")
            hours = raw.get("effort_hours", 1)
            if not isinstance(hours, int) or hours <= 0:
                raise ValueError("effort_hours must be a positive integer")
            status = raw.get("status", "todo")
            if status not in {"todo", "done", "failed", "blocked"}:
                raise ValueError("task status must be todo, done, failed, or blocked")
            owner = raw.get("owner")
            if owner is not None and (not isinstance(owner, str) or not owner.strip()):
                raise ValueError("task owner must be a non-empty string when provided")
            normalized.append({"id": task_id, "title": self._require_string(raw, "title"), "depends_on": depends_on, "effort_hours": hours, "status": status, "owner": owner.strip() if isinstance(owner, str) else None})
        for task in normalized:
            unknown = set(task["depends_on"]) - ids
            if unknown:
                raise ValueError(f"task {task['id']} references unknown dependencies: {sorted(unknown)}")
        return normalized

    def _topological_order(self, tasks: list[dict[str, Any]]) -> list[str]:
        children: dict[str, list[str]] = defaultdict(list)
        in_degree = {task["id"]: len(task["depends_on"]) for task in tasks}
        for task in tasks:
            for dependency in task["depends_on"]:
                children[dependency].append(task["id"])
        queue = deque(task["id"] for task in tasks if in_degree[task["id"]] == 0)
        order = []
        while queue:
            task_id = queue.popleft()
            order.append(task_id)
            for child in children[task_id]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)
        if len(order) != len(tasks):
            raise ValueError("task_specs must form an acyclic dependency graph")
        return order

    def _critical_path(self, tasks: list[dict[str, Any]], order: list[str]) -> tuple[list[str], int]:
        best_hours: dict[str, int] = {}
        best_parent: dict[str, str | None] = {}
        for task_id in order:
            task = self._task(tasks, task_id)
            parents = task["depends_on"]
            parent = max(parents, key=lambda dep: best_hours[dep]) if parents else None
            best_parent[task_id] = parent
            best_hours[task_id] = (best_hours[parent] if parent else 0) + task["effort_hours"]
        tail = max(order, key=lambda task_id: best_hours[task_id])
        path = []
        while tail is not None:
            path.append(tail)
            tail = best_parent[tail]
        return list(reversed(path)), best_hours[path[0]] if len(path) == 1 else max(best_hours.values())

    @staticmethod
    def _task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
        return next(task for task in tasks if task["id"] == task_id)

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        return {key: task[key] for key in ("id", "title", "owner", "effort_hours", "status")}

    @staticmethod
    def _require_string(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"task field {field} must be a non-empty string")
        return value.strip()

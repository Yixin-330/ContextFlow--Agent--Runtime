from __future__ import annotations

import re
from typing import Any

try:  # Support package execution and MTClaw's direct script launcher.
    from .delivery_planner import DeliveryPlanner
    from .local_rag import LocalRAG
    from .state_engine import build_state
    from .storage import Storage
    from .workflow_engine import delivery_plan_draft
except ImportError:  # pragma: no cover - exercised by the direct CLI launcher.
    from delivery_planner import DeliveryPlanner
    from local_rag import LocalRAG
    from state_engine import build_state
    from storage import Storage
    from workflow_engine import delivery_plan_draft


class MeetingOrchestrator:
    """Turn a completed meeting into safe, deterministic next-step work."""

    def __init__(self, store: Storage, rag: LocalRAG, delivery: DeliveryPlanner) -> None:
        self.store = store
        self.rag = rag
        self.delivery = delivery

    def after_meeting(
        self,
        session_key: str,
        meeting_title: str,
        meeting_text: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the full, reviewable cross-agent trace for one meeting.

        The module only promotes a deadline to a delivery workflow when the
        meeting contains an explicit numeric time window and actionable tasks.
        This keeps scheduling claims deterministic and auditable.
        """
        rag = self.retrieve_evidence(session_key, meeting_title, artifacts)
        delivery = self.plan_delivery(session_key, meeting_title, meeting_text, artifacts)
        agents = [
            {
                "agent": "MeetingActionAgent",
                "status": "completed",
                "reason": "meeting_ended state",
            },
            {
                "agent": "LocalRAGAgent",
                "status": "completed" if rag["status"] == "matched" else "no_evidence",
                "reason": rag["reason"],
            },
            {
                "agent": "DeliveryPlannerAgent",
                "status": delivery["status"],
                "reason": delivery["reason"],
            },
        ]
        return {
            "mode": "state_triggered_meeting_follow_up_v1",
            "trigger": {"state": "meeting_ended", "meeting_title": meeting_title},
            "agents": agents,
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

    def retrieve_evidence(self, session_key: str, meeting_title: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        rag = self._rag_suggestion(session_key, meeting_title, artifacts)
        rag["action_evidence"] = self._action_evidence(session_key, artifacts)
        return rag

    def plan_delivery(self, session_key: str, meeting_title: str, meeting_text: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        return self._delivery_plan(session_key, meeting_title, meeting_text, artifacts)

    def _rag_suggestion(
        self,
        session_key: str,
        meeting_title: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.store.list_rag_chunks(session_key):
            return {
                "status": "no_local_evidence",
                "reason": "no session-isolated local documents have been indexed",
                "query": None,
                "matches": [],
            }
        query = self._rag_query(meeting_title, artifacts)
        matches = self.rag.search(session_key, query, limit=3)
        return {
            "status": "matched" if matches else "no_local_evidence",
            "reason": "meeting terms matched indexed local material" if matches else "no indexed material matched meeting terms",
            "query": query,
            "matches": matches,
        }

    def _action_evidence(
        self, session_key: str, artifacts: dict[str, Any]
    ) -> list[dict[str, Any]]:
        evidence = []
        for index, item in enumerate(artifacts.get("action_items", [])):
            task = item.get("task") if isinstance(item, dict) else None
            if not isinstance(task, str) or not task.strip():
                continue
            query = task.strip()
            matches = self.rag.search(session_key, query) if re.search(
                r"[a-zA-Z0-9_\u4e00-\u9fff]", query
            ) else []
            evidence.append({
                "action_index": index,
                "task": query,
                "status": "candidate_evidence" if matches else "needs_review",
                "matches": matches,
            })
        return evidence

    def _delivery_plan(
        self,
        session_key: str,
        meeting_title: str,
        meeting_text: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        deadline_hours = self._deadline_hours(meeting_text)
        tasks = self._task_specs(artifacts)
        if deadline_hours is None:
            return {
                "status": "suggested",
                "reason": "meeting has no explicit numeric deadline window",
                "deadline_hours": None,
                "draft": None,
            }
        if not tasks:
            return {
                "status": "suggested",
                "reason": "meeting deadline is explicit but no actionable tasks were detected",
                "deadline_hours": deadline_hours,
                "draft": None,
            }

        context = build_state(
            session_key,
            "deadline_risk",
            [f"meeting follow-up detected an explicit deadline within {deadline_hours} hours"],
            0.9,
        )
        self.store.save_state(context)
        plan = self.delivery.plan(meeting_title, deadline_hours, tasks)
        draft = delivery_plan_draft(session_key, meeting_title, deadline_hours, context, plan=plan)
        draft["payload"]["orchestration_source"] = "meeting_state_trigger"
        self.store.save_draft(draft)
        return {
            "status": "planned",
            "reason": "explicit deadline and actionable meeting tasks detected",
            "deadline_hours": deadline_hours,
            "draft": draft,
        }

    @staticmethod
    def _deadline_hours(text: str) -> int | None:
        patterns = (
            r"\b(?:within|in|before)\s+(\d{1,3})\s*(?:hours?|hrs?)\b",
            r"(\d{1,3})\s*(?:\u5c0f\u65f6|\u5c0f\u6642)",
            r"(\d{1,2})\s*(?:\u5929)\b",
        )
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = int(match.group(1))
                return value * 24 if index == 2 else value
        return None

    @classmethod
    def _task_specs(cls, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
        task_specs: list[dict[str, Any]] = []
        for index, item in enumerate(artifacts.get("action_items", []), start=1):
            task = item.get("task") if isinstance(item, dict) else None
            if not isinstance(task, str) or not task.strip():
                continue
            dependency_hint, title = cls._dependency_hint(task.strip())
            task_specs.append({
                "id": f"meeting-task-{index}",
                "title": cls._clean_title(title),
                "depends_on": [],
                "effort_hours": cls._effort_hours(task),
                "status": "todo",
                "owner": item.get("owner") if isinstance(item, dict) else None,
                "dependency_hint": dependency_hint,
            })
        for task in task_specs:
            hint = task.pop("dependency_hint")
            predecessor = cls._find_predecessor(hint, task, task_specs)
            if predecessor is not None:
                task["depends_on"] = [predecessor["id"]]
        return task_specs

    @staticmethod
    def _effort_hours(task: str) -> int:
        match = re.search(r"\b(\d{1,2})\s*(?:h|hours?|hrs?)\b|(?:\(|\uff08)?(\d{1,2})\s*\u5c0f\u65f6", task, re.IGNORECASE)
        if match:
            return int(match.group(1) or match.group(2))
        return 1

    @staticmethod
    def _dependency_hint(task: str) -> tuple[str | None, str]:
        english = re.match(r"\s*after\s+(.+?)[,\uff0c:]\s*(.+)", task, re.IGNORECASE)
        if english:
            return english.group(1).strip(), english.group(2).strip()
        chinese = re.match(r"\s*(?:\u5728)?(.+?)(?:\u5b8c\u6210\u540e|\u4e4b\u540e)[,\uff0c\uff1a:]?\s*(.+)", task)
        if chinese:
            return chinese.group(1).strip(), chinese.group(2).strip()
        return None, task

    @staticmethod
    def _clean_title(task: str) -> str:
        cleaned = re.sub(r"\s*\(?\d{1,2}\s*(?:h|hours?|hrs?)\)?\s*$", "", task, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*[\(\uff08]?\d{1,2}\s*\u5c0f\u65f6[\)\uff09]?\s*$", "", cleaned)
        return cleaned.strip(" .\u3002")

    @classmethod
    def _find_predecessor(
        cls,
        hint: str | None,
        task: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not hint:
            return None
        normalized_hint = cls._clean_title(hint).lower()
        for candidate in candidates:
            if candidate["id"] == task["id"]:
                continue
            normalized_title = candidate["title"].lower()
            if normalized_hint == normalized_title or normalized_hint in normalized_title or normalized_title in normalized_hint:
                return candidate
        return None

    @staticmethod
    def _rag_query(meeting_title: str, artifacts: dict[str, Any]) -> str:
        terms = [meeting_title, artifacts.get("summary", "")]
        terms.extend(
            item.get("task", "")
            for item in artifacts.get("action_items", [])
            if isinstance(item, dict)
        )
        return " ".join(term.strip() for term in terms if isinstance(term, str) and term.strip())

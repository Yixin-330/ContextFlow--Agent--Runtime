from __future__ import annotations

import re
from typing import Any


class OfficeFlow:
    """Create reviewable meeting artifacts without performing an external write."""

    def process_meeting(self, meeting_title: str, meeting_text: str) -> dict[str, Any]:
        lines = [line.strip(" -•\t") for line in meeting_text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("meeting_text must contain at least one non-empty line")
        decisions = [line for line in lines if self._contains(line, ("决定", "确认", "同意", "决议", "decide", "agree"))]
        action_items = self._action_items(lines)
        summary_lines = (decisions or lines)[:3]
        summary = "；".join(summary_lines)
        email_draft = self._email_draft(meeting_title, summary, action_items)
        return {
            "meeting_title": meeting_title,
            "summary": summary,
            "decisions": decisions,
            "action_items": action_items,
            "email_draft": email_draft,
            "parser": "deterministic_meeting_parser_v1",
            "requires_review": True,
        }

    def _action_items(self, lines: list[str]) -> list[dict[str, str | None]]:
        items: list[dict[str, str | None]] = []
        segments = []
        for line in lines:
            segments.extend(
                segment.strip()
                for segment in re.split(
                    r"(?=(?:TODO|\u5f85\u529e|\u884c\u52a8\u9879|action)\s*[:\uff1a]?)",
                    line,
                    flags=re.IGNORECASE,
                )
                if segment.strip()
            )
        for line in segments:
            todo = re.search(r"(?:TODO|待办|行动项|action)\s*[:：-]?\s*(.+)", line, re.IGNORECASE)
            owner_task = re.search(r"([^，,：:\s]{1,16})\s*(?:负责|跟进|will|owns?)\s*[:：-]?\s*(.+)", line, re.IGNORECASE)
            if todo:
                items.append({"owner": None, "task": todo.group(1).strip()})
            elif owner_task:
                items.append({"owner": owner_task.group(1).strip(), "task": owner_task.group(2).strip()})
        return items

    @staticmethod
    def _contains(line: str, markers: tuple[str, ...]) -> bool:
        lowered = line.lower()
        return any(marker.lower() in lowered for marker in markers)

    @staticmethod
    def _email_draft(title: str, summary: str, action_items: list[dict[str, str | None]]) -> str:
        task_lines = [
            f"- {item['owner'] + '：' if item['owner'] else ''}{item['task']}"
            for item in action_items
        ] or ["- 暂未从会议文本中识别到明确待办，请人工补充。"]
        return f"主题：{title}｜会议跟进\n\n会议摘要：{summary}\n\n待办：\n" + "\n".join(task_lines) + "\n\n请确认以上内容后再发送。"

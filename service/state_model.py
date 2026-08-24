from __future__ import annotations

from typing import Any


STATE_MODEL: dict[str, dict[str, Any]] = {
    "idle": {
        "meaning": "No stronger recent work signal is active.",
        "minimum_confidence": 0.8,
        "required_evidence": ["no active higher-priority signal in the event window"],
        "automatic_actions": ["answer general requests", "read local knowledge", "inspect local files"],
        "confirmation_required_for": ["external writes", "file moves", "messages", "task-system changes"],
    },
    "focus_mode": {
        "meaning": "Recent sustained or explicit work activity suggests low-interruption work.",
        "minimum_confidence": 0.72,
        "required_evidence": ["explicit focus signal or recent desktop work activity"],
        "automatic_actions": ["silent capture", "read-only retrieval", "low-interruption suggestions"],
        "confirmation_required_for": ["file moves", "messages", "external writes"],
    },
    "meeting_upcoming": {
        "meaning": "An accepted, non-cancelled calendar meeting is scheduled to begin within 15 minutes.",
        "minimum_confidence": 0.92,
        "required_evidence": ["read-only calendar event with a future start within 15 minutes", "event is accepted and not cancelled"],
        "automatic_actions": ["prepare read-only meeting context", "surface a quiet reminder", "read local knowledge"],
        "confirmation_required_for": ["sending messages", "changing the calendar", "joining a meeting", "external writes"],
    },
    "meeting_window_active": {
        "meaning": "Current time is inside an accepted, non-cancelled scheduled meeting window; attendance is not asserted.",
        "minimum_confidence": 0.85,
        "required_evidence": ["current time is between the calendar event start and end", "event is accepted and not cancelled"],
        "automatic_actions": ["reduce interruption", "prepare a meeting-note capture entry", "read-only retrieval"],
        "confirmation_required_for": ["claiming attendance", "joining or recording a meeting", "sending messages", "external writes"],
    },
    "schedule_conflict": {
        "meaning": "Two accepted, non-cancelled calendar meeting windows overlap.",
        "minimum_confidence": 0.95,
        "required_evidence": ["two accepted calendar events have overlapping start and end times"],
        "automatic_actions": ["surface the conflict", "prepare resolution options"],
        "confirmation_required_for": ["declining events", "rescheduling meetings", "contacting attendees", "external writes"],
    },
    "meeting_ended": {
        "meaning": "A meeting-end signal or imported meeting-notes artifact was observed recently.",
        "minimum_confidence": 0.96,
        "required_evidence": ["calendar meeting-ended event or imported non-empty meeting-notes file"],
        "automatic_actions": ["parse meeting notes", "create summary", "extract decisions and actions", "read-only RAG search", "prepare drafts"],
        "confirmation_required_for": ["sending email", "publishing minutes", "creating external tasks"],
    },
    "deadline_risk": {
        "meaning": "A short explicit deadline and incomplete actionable work were both observed.",
        "minimum_confidence": 0.9,
        "required_evidence": ["explicit numeric deadline within 72 hours", "at least one actionable task", "work is incomplete"],
        "automatic_actions": ["calculate dependency plan", "identify critical path", "prepare delivery-plan draft", "read-only RAG search"],
        "confirmation_required_for": ["changing external task systems", "assigning people", "sending messages", "executing the delivery plan"],
    },
    "data_ready_for_analysis": {
        "meaning": "A stable supported data file arrived and is available for optional analysis.",
        "minimum_confidence": 0.95,
        "required_evidence": ["new stable .csv, .tsv, or .xlsx file in the data inbox"],
        "automatic_actions": ["record file metadata", "surface an analysis opportunity"],
        "confirmation_required_for": ["reading and profiling file contents", "creating charts", "publishing analysis", "external writes"],
    },
}


def state_contract(state: str) -> dict[str, Any]:
    try:
        return {"state": state, **STATE_MODEL[state]}
    except KeyError as error:
        raise ValueError(f"unsupported state: {state}") from error

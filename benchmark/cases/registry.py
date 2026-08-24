from __future__ import annotations

"""Single registry for CLI-selectable online A/B case suites."""

from typing import Any

from ..ab_cases import FileSearchCase, LocalRagCase
from .archive_safety import ArchiveLifecycleCase, ArchiveRejectedCase, ArchiveUnconfirmedCase
from .data_insight import DataInsightCase


def select_cases(selection: str, *, question_id: str = "rag_runtime_roles_v1") -> list[Any]:
    suites = {
        "file_search": [FileSearchCase()],
        "local_rag": [LocalRagCase(question_id)],
        "archive_unconfirmed": [ArchiveUnconfirmedCase()],
        "archive_rejected": [ArchiveRejectedCase()],
        "archive_lifecycle": [ArchiveLifecycleCase()],
        "archive_safety": [ArchiveUnconfirmedCase(), ArchiveRejectedCase(), ArchiveLifecycleCase()],
        "data_risk": [DataInsightCase("project_risk_labeled.csv")],
        "data_clean": [DataInsightCase("project_clean_control.csv")],
        "data_invalid": [DataInsightCase("project_invalid_schema.csv")],
        "data_insight": [
            DataInsightCase("project_risk_labeled.csv"),
            DataInsightCase("project_clean_control.csv"),
            DataInsightCase("project_invalid_schema.csv"),
        ],
    }
    try:
        return suites[selection]
    except KeyError as error:
        raise ValueError(f"unknown A/B case selection: {selection}") from error


def case_choices() -> tuple[str, ...]:
    return (
        "file_search", "local_rag",
        "archive_unconfirmed", "archive_rejected", "archive_lifecycle", "archive_safety",
        "data_risk", "data_clean", "data_invalid", "data_insight",
    )

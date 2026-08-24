from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmark.ab_cases import CaseContext
from benchmark.adapters import BenchmarkRequest, RunResult
from benchmark.cases.archive_safety import (
    DESTINATIONS,
    TARGETS,
    ArchiveLifecycleCase,
    ArchiveRejectedCase,
    ArchiveUnconfirmedCase,
    _plan_failures,
)
from benchmark.evaluators import PhaseRecorder


class ScriptedArchiveAdapter:
    """Exercise case lifecycle and verification without an online Gateway."""

    name = "native_openclaw"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.turns: dict[str, int] = {}
        self.prompts: list[str] = []

    def run(self, request: BenchmarkRequest) -> RunResult:
        self.prompts.append(request.prompt)
        turn = self.turns.get(request.case_id, 0)
        self.turns[request.case_id] = turn + 1
        answer = (
            "meeting-notes.txt -> .contextflow_archive/meeting-notes (1).txt\n"
            "projects/project-meeting.md -> .contextflow_archive/project-meeting.md\n"
            "awaiting confirmation; plan_id=archive-plan-test"
        )
        if request.case_id == "archive_rejected" and turn == 1:
            answer = "The archive plan was rejected and was not executed."
        if request.case_id == "archive_lifecycle" and turn == 1:
            for source, destination in zip(TARGETS, DESTINATIONS):
                target = self.workspace / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                (self.workspace / source).rename(target)
            answer = "Archive operation completed; operation_id=archive-op-test."
        if request.case_id == "archive_lifecycle" and turn == 2:
            for source, destination in zip(TARGETS, DESTINATIONS):
                target = self.workspace / source
                target.parent.mkdir(parents=True, exist_ok=True)
                (self.workspace / destination).rename(target)
            answer = "Archive operation undone; all original paths restored."
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=True,
            latency_ms=1,
            final_answer=answer,
        )


class ArchiveSafetyABCaseTests(unittest.TestCase):
    def _run_case(self, case):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        phases = PhaseRecorder(workspace)
        context = CaseContext(
            case.case_id,
            case.scene,
            1,
            "native_openclaw",
            "AB",
            1,
            "archive-test-session",
            workspace,
            phases,
        )
        fixture = case.prepare(context)
        phases.start()
        adapter = ScriptedArchiveAdapter(workspace)
        outcome = case.execute(adapter, context, fixture)
        final = phases.finalize()
        evaluation = case.verify(context, fixture, outcome, final.from_start)
        return context, outcome, evaluation, adapter

    def test_unconfirmed_plan_makes_no_file_changes(self) -> None:
        context, outcome, evaluation, _ = self._run_case(ArchiveUnconfirmedCase())
        self.assertTrue(evaluation.passed, evaluation.failures)
        self.assertEqual(context.phases.get("after_plan").from_start.change_count, 0)
        self.assertEqual(len(outcome.turns), 1)

    def test_rejected_plan_makes_no_file_changes_in_either_phase(self) -> None:
        context, outcome, evaluation, adapter = self._run_case(ArchiveRejectedCase())
        self.assertTrue(evaluation.passed, evaluation.failures)
        self.assertEqual(context.phases.get("after_plan").from_start.change_count, 0)
        self.assertEqual(context.phases.get("after_rejection").from_start.change_count, 0)
        self.assertEqual(len(outcome.turns), 2)
        self.assertIn("archive-plan-test", adapter.prompts[1])

    def test_lifecycle_moves_exact_targets_and_restores_paths_and_hashes(self) -> None:
        context, outcome, evaluation, adapter = self._run_case(ArchiveLifecycleCase())
        self.assertTrue(evaluation.passed, evaluation.failures)
        execution = context.phases.get("after_execution").from_start
        self.assertEqual(set(execution.created), set(DESTINATIONS))
        self.assertEqual(set(execution.deleted), set(TARGETS))
        self.assertEqual(execution.modified, ())
        self.assertEqual(context.phases.get("after_undo").from_start.change_count, 0)
        self.assertEqual(context.phases.snapshot("after_undo"), context.phases.snapshot("before"))
        self.assertEqual(len(outcome.turns), 3)
        self.assertIn("archive-plan-test", adapter.prompts[1])
        self.assertIn("archive-op-test", adapter.prompts[2])

    def test_plan_verifier_normalizes_windows_path_separators(self) -> None:
        turn = RunResult(
            adapter="native_openclaw",
            case_id="archive_unconfirmed",
            session_key="s",
            success=True,
            latency_ms=1,
            final_answer=(
                r"meeting-notes.txt -> .contextflow_archive\meeting-notes (1).txt" "\n"
                r"projects\project-meeting.md -> .contextflow_archive\project-meeting.md" "\n"
                "等待确认"
            ),
        )
        self.assertEqual(_plan_failures(turn, 0), [])

    def test_each_case_declares_the_contextflow_tool_contract(self) -> None:
        self.assertEqual(
            ArchiveUnconfirmedCase().expected_contextflow_tools,
            ("desktop_search_files", "desktop_plan_archive"),
        )
        self.assertEqual(ArchiveRejectedCase().expected_contextflow_tools[-1], "desktop_confirm_archive")
        self.assertEqual(ArchiveLifecycleCase().expected_contextflow_tools[-1], "desktop_undo_archive")


if __name__ == "__main__":
    unittest.main()

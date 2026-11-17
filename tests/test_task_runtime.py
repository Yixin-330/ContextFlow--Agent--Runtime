from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE_DIR))

from runtime import ContextFlowRuntime
from storage import Storage
from task_runtime import TaskRuntime


class TaskRuntimeTests(unittest.TestCase):
    def test_checkpoint_can_resume_an_interrupted_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "runtime.db")
            tasks = TaskRuntime(store)
            started = tasks.start(session_key="resume-session", goal="Prepare meeting follow-up")
            execution_id = started["execution"]["execution_id"]

            checkpoint = tasks.checkpoint(
                execution_id,
                current_node="extract_actions",
                tool_name="office_process_meeting",
                result={"action_count": 2},
            )
            tasks.mark_interrupted(execution_id)
            resumed = tasks.resume(execution_id)

            self.assertEqual(resumed["checkpoint"]["checkpoint_id"], checkpoint["checkpoint_id"])
            self.assertEqual(resumed["execution"]["status"], "running")
            self.assertEqual(resumed["execution"]["current_node"], "extract_actions")

    def test_gateway_deduplicates_a_completed_rag_operation_for_one_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "runtime.db")
            runtime = ContextFlowRuntime(store)
            first = runtime.dispatch("rag_ingest", {
                "session_key": "rag-session",
                "document_id": "project-brief",
                "title": "Project brief",
                "source_name": "brief.md",
                "content": "The release review is due Friday.",
            })
            runtime_ids = first["runtime"]
            repeated = runtime.dispatch("rag_ingest", {
                "session_key": "rag-session",
                "document_id": "project-brief",
                "title": "Project brief",
                "source_name": "brief.md",
                "content": "The release review is due Friday.",
                "task_id": runtime_ids["task_id"],
                "execution_id": runtime_ids["execution_id"],
            })

            self.assertFalse(first["runtime"]["deduplicated"])
            self.assertTrue(repeated["runtime"]["deduplicated"])
            self.assertEqual(first["runtime"]["operation_id"], repeated["runtime"]["operation_id"])
            checkpoint = store.latest_runtime_checkpoint(runtime_ids["execution_id"])
            self.assertEqual(checkpoint["completed_nodes"], ["tool:rag_ingest"])

    def test_gateway_rejects_desktop_tool_outside_its_declared_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Storage(root / "runtime.db")
            runtime = ContextFlowRuntime(store)
            store.save_state({
                "session_key": "state-session",
                "state": "meeting_ended",
                "confidence": 1.0,
                "evidence": ["test"],
                "updated_at": "2026-11-16T00:00:00+00:00",
            })
            with self.assertRaisesRegex(ValueError, "not permitted"):
                runtime.dispatch("desktop_search_files", {
                    "session_key": "state-session",
                    "root_path": str(root),
                    "query": "notes",
                })

    def test_runtime_approval_is_bound_to_one_execution_and_plan_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "runtime.db")
            tasks = TaskRuntime(store)
            started = tasks.start(session_key="approval-session", goal="Create project tasks")
            execution_id = started["execution"]["execution_id"]
            approval = tasks.request_approval(
                execution_id,
                plan_version=2,
                operation_ids=["operation-create-task-a"],
            )
            decided = tasks.decide_approval(approval["approval_id"], "approved")

            self.assertEqual(decided["approval"]["plan_version"], 2)
            self.assertEqual(decided["approval"]["operation_ids"], ["operation-create-task-a"])
            self.assertEqual(decided["execution"]["status"], "running")
            with self.assertRaisesRegex(ValueError, "final decision"):
                tasks.decide_approval(approval["approval_id"], "approved")

    def test_meeting_pipeline_resumes_after_evidence_without_duplicate_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "runtime.db")
            runtime = ContextFlowRuntime(store)
            interrupted = runtime.dispatch("context_simulate_meeting_end", {
                "session_key": "meeting-pipeline-session",
                "meeting_title": "Release review",
                "meeting_text": "TODO: Prepare release notes (2 hours). Deliver within 24 hours.",
                "pipeline_fail_after": "retrieve_evidence",
            })

            self.assertTrue(interrupted["interrupted"])
            execution_id = interrupted["execution"]["execution_id"]
            self.assertEqual(store.get_runtime_execution(execution_id)["status"], "interrupted")
            self.assertEqual(len(store.list_drafts("meeting-pipeline-session")), 1)

            resumed = runtime.dispatch("context_resume_meeting_pipeline", {
                "session_key": "meeting-pipeline-session",
                "execution_id": execution_id,
            })

            self.assertFalse(resumed["interrupted"])
            self.assertEqual(resumed["execution"]["status"], "completed")
            self.assertEqual(len(store.list_drafts("meeting-pipeline-session")), 2)
            self.assertEqual(resumed["orchestration"]["delivery"]["status"], "planned")
            checkpoint = store.latest_runtime_checkpoint(execution_id)
            self.assertEqual(checkpoint["completed_nodes"], ["process_meeting", "retrieve_evidence", "plan_delivery"])

    def test_approved_operation_executes_once_for_the_bound_plan_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "completed-notes.md"
            source.write_text("ready", encoding="utf-8")
            store = Storage(root / "runtime.db")
            runtime = ContextFlowRuntime(store)
            plan = runtime.dispatch("desktop_plan_archive", {
                "session_key": "approval-operation-session",
                "root_path": str(root),
                "relative_paths": [source.name],
            })["plan"]
            runtime.dispatch("desktop_confirm_archive", {
                "session_key": "approval-operation-session",
                "plan_id": plan["plan_id"],
                "decision": "approved",
            })
            prepared = runtime.dispatch("runtime_prepare_approval", {
                "session_key": "approval-operation-session",
                "requested_tool": "desktop_execute_archive",
                "requested_payload": {"plan_id": plan["plan_id"]},
                "plan_version": 3,
            })
            approval_id = prepared["approval"]["approval_id"]
            operation_id = prepared["operation"]["operation_id"]
            runtime.dispatch("runtime_decide_approval", {
                "session_key": "approval-operation-session",
                "approval_id": approval_id,
                "decision": "approved",
            })
            first = runtime.dispatch("runtime_execute_approved_operation", {
                "session_key": "approval-operation-session",
                "approval_id": approval_id,
                "operation_id": operation_id,
            })
            repeated = runtime.dispatch("runtime_execute_approved_operation", {
                "session_key": "approval-operation-session",
                "approval_id": approval_id,
                "operation_id": operation_id,
            })

            self.assertFalse(source.exists())
            self.assertTrue((root / ".contextflow_archive" / source.name).is_file())
            self.assertEqual(first["runtime"]["operation_id"], operation_id)
            self.assertTrue(repeated["runtime"]["deduplicated"])
            self.assertEqual(store.get_runtime_approval(approval_id)["plan_version"], 3)

    def test_replanning_invalidates_an_approval_bound_to_the_old_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old-plan-notes.md"
            source.write_text("ready", encoding="utf-8")
            store = Storage(root / "runtime.db")
            runtime = ContextFlowRuntime(store)
            runtime.dispatch("context_update_state", {
                "session_key": "plan-binding-session",
                "state": "deadline_risk",
                "evidence": ["deadline in 8 hours"],
            })
            draft = runtime.dispatch("workflow_plan_delivery", {
                "session_key": "plan-binding-session",
                "goal": "Ship a demo",
                "deadline_hours": 8,
                "task_specs": [{"id": "build", "title": "Build demo", "effort_hours": 4}],
            })["draft"]
            self.assertEqual(draft["plan_version"], 1)

            runtime.dispatch("context_update_state", {
                "session_key": "plan-binding-session", "state": "idle", "evidence": ["prepare reversible archive"],
            })
            archive = runtime.dispatch("desktop_plan_archive", {
                "session_key": "plan-binding-session",
                "root_path": str(root),
                "relative_paths": [source.name],
            })["plan"]
            runtime.dispatch("desktop_confirm_archive", {
                "session_key": "plan-binding-session", "plan_id": archive["plan_id"], "decision": "approved",
            })
            prepared = runtime.dispatch("runtime_prepare_approval", {
                "session_key": "plan-binding-session",
                "requested_tool": "desktop_execute_archive",
                "requested_payload": {"plan_id": archive["plan_id"]},
                "draft_id": draft["draft_id"],
                "plan_version": 1,
            })
            approval_id = prepared["approval"]["approval_id"]
            runtime.dispatch("runtime_decide_approval", {
                "session_key": "plan-binding-session", "approval_id": approval_id, "decision": "approved",
            })

            runtime.dispatch("context_update_state", {
                "session_key": "plan-binding-session", "state": "deadline_risk", "evidence": ["build failed"],
            })
            replanned = runtime.dispatch("delivery_replan", {
                "session_key": "plan-binding-session",
                "draft_id": draft["draft_id"],
                "failed_task_ids": ["build"],
            })
            self.assertEqual(store.get_draft(draft["draft_id"])["status"], "superseded")
            self.assertEqual(replanned["draft"]["plan_version"], 2)

            runtime.dispatch("context_update_state", {
                "session_key": "plan-binding-session", "state": "idle", "evidence": ["attempt old approval"],
            })
            with self.assertRaisesRegex(ValueError, "no longer executable"):
                runtime.dispatch("runtime_execute_approved_operation", {
                    "session_key": "plan-binding-session",
                    "approval_id": approval_id,
                    "operation_id": prepared["operation"]["operation_id"],
                })
            self.assertTrue(source.is_file())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import io
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE_DIR))

from agent_registry import find_agents_for_state
from calendar_sense import CalendarSense
from contextflow_cli import _read_payload
from demo_control import DemoControl
from event_ingress import DesktopFileWatcher, EventIngress, LocalInboxWatcher
from runtime import ContextFlowRuntime
from runtime_dashboard import _page
from outlook_calendar import OutlookCalendarAdapter
from state_engine import build_state
from state_engine import describe_state
from storage import Storage
from workflow_engine import delivery_plan_draft, meeting_draft


class ContextFlowTests(unittest.TestCase):
    def test_workbench_defaults_to_following_latest_powershell_session(self) -> None:
        page = _page()

        self.assertIn('let activeSession = urlParams.get("session_key") || null;', page)
        self.assertIn('const snapshotPath = activeSession', page)
        self.assertIn('id="follow-latest"', page)

    def test_workbench_can_demonstrate_every_core_state_without_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inbox" / "meetings").mkdir(parents=True)
            (root / "inbox" / "data").mkdir(parents=True)
            (root / "demo_data").mkdir()
            (root / "demo_data" / "contextflow_delivery_meeting.md").write_text("demo", encoding="utf-8")
            store = Storage(root / "data" / "contextflow.db")
            runtime = ContextFlowRuntime(store)
            control = DemoControl(root, store, runtime)

            expected = {
                "meeting_upcoming": "meeting_upcoming",
                "meeting_window_active": "meeting_window_active",
                "schedule_conflict": "schedule_conflict",
                "focus_mode": "focus_mode",
                "data_ready_for_analysis": "data_ready_for_analysis",
                "meeting_ended": "meeting_ended",
            }
            for scenario, state in expected.items():
                result = control.simulate_state("state-showcase", scenario)
                self.assertEqual(result["context"]["state"], state)
                self.assertEqual(result["source_kind"], "simulation")
                self.assertEqual(result["session_key"], "state-showcase:state-demo")

    def test_cli_reads_unicode_tool_arguments_as_utf8_bytes(self) -> None:
        source_path = "C:\\资料\\contextflow_reference.md"
        payload = json.dumps({"source_path": source_path}, ensure_ascii=False).encode("utf-8")

        self.assertEqual(_read_payload(io.BytesIO(payload)), {"source_path": source_path})

    def test_outlook_adapter_normalizes_graph_delta_pages(self) -> None:
        pages = {
            "initial": {
                "value": [{
                    "id": "event-1", "changeKey": "v1", "subject": "Project review",
                    "start": {"dateTime": "2026-08-12T10:00:00", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-08-12T11:00:00", "timeZone": "UTC"},
                    "isCancelled": False, "isOnlineMeeting": True,
                    "responseStatus": {"response": "accepted"},
                }],
                "@odata.nextLink": "next",
            },
            "next": {"value": [{"id": "event-removed", "@removed": {"reason": "deleted"}}], "@odata.deltaLink": "delta-final"},
        }
        requested_urls = []
        def request_page(url: str, _token: str) -> dict[str, object]:
            requested_urls.append(url)
            return pages["next" if url == "next" else "initial"]
        adapter = OutlookCalendarAdapter(lambda: "token", request_json=request_page)
        result = adapter.sync(
            delta_link=None,
            window_start=datetime(2026, 8, 12, 8, tzinfo=UTC),
            window_end=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )
        self.assertEqual(result["delta_link"], "delta-final")
        self.assertEqual(result["events"][0]["response_status"], "accepted")
        self.assertEqual(result["events"][0]["start_at"], "2026-08-12T10:00:00+00:00")
        self.assertTrue(result["events"][1]["removed"])
        self.assertNotIn("%24select", requested_urls[0])

    def test_outlook_calendar_baseline_and_time_crossings_drive_explainable_states(self) -> None:
        class FakeOutlook:
            provider = "outlook"

            def __init__(self) -> None:
                self.first = True

            def sync(self, **_kwargs: object) -> dict[str, object]:
                if not self.first:
                    return {"events": [], "delta_link": "delta-2"}
                self.first = False
                return {"events": [{
                    "provider_event_id": "meeting-1", "version": "v1", "title": "Competition review",
                    "start_at": "2026-08-12T10:10:00+00:00", "end_at": "2026-08-12T11:00:00+00:00",
                    "response_status": "accepted", "cancelled": False, "online_meeting": True, "removed": False,
                }], "delta_link": "delta-1"}

        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            sense = CalendarSense(store, FakeOutlook(), lambda _session, event: runtime.dispatch("context_ingest_event", event))
            upcoming = sense.sync_once(
                session_key="calendar-a", account_key="primary",
                now=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
            )
            self.assertTrue(upcoming["baseline_created"])
            self.assertEqual(store.get_state("calendar-a")["state"], "meeting_upcoming")
            self.assertEqual(len(upcoming["events_emitted"]), 1)

            active = sense.sync_once(
                session_key="calendar-a", account_key="primary",
                now=datetime(2026, 8, 12, 10, 15, tzinfo=UTC),
            )
            self.assertEqual(store.get_state("calendar-a")["state"], "meeting_window_active")
            self.assertIn("does not prove attendance", " ".join(store.get_state("calendar-a")["evidence"]))
            self.assertEqual(len(active["events_emitted"]), 1)

            ended = sense.sync_once(
                session_key="calendar-a", account_key="primary",
                now=datetime(2026, 8, 12, 11, 1, tzinfo=UTC),
            )
            runtime.event_state.recompute("calendar-a", now=datetime(2026, 8, 12, 11, 1, tzinfo=UTC))
            self.assertEqual(store.get_state("calendar-a")["state"], "meeting_ended")
            self.assertEqual(len(ended["events_emitted"]), 1)
            self.assertEqual(len(store.list_sensed_events("calendar-a")), 3)

    def test_outlook_baseline_does_not_replay_a_historical_meeting_end(self) -> None:
        class HistoricalOutlook:
            provider = "outlook"

            def sync(self, **_kwargs: object) -> dict[str, object]:
                return {"events": [{
                    "provider_event_id": "old", "version": "v1", "title": "Old meeting",
                    "start_at": "2026-08-12T08:00:00+00:00", "end_at": "2026-08-12T09:00:00+00:00",
                    "response_status": "accepted", "cancelled": False, "online_meeting": False, "removed": False,
                }], "delta_link": "delta"}

        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            sense = CalendarSense(store, HistoricalOutlook(), lambda _session, event: runtime.dispatch("context_ingest_event", event))
            result = sense.sync_once(session_key="calendar-old", account_key="primary", now=datetime(2026, 8, 12, 10, tzinfo=UTC))
            self.assertEqual(result["events_emitted"], [])
            self.assertIsNone(store.get_state("calendar-old"))

    def test_p1_demo_control_uses_real_file_chain_and_resets_only_demo_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meetings = root / "inbox" / "meetings"
            data = root / "inbox" / "data"
            demo_data = root / "demo_data"
            meetings.mkdir(parents=True)
            data.mkdir(parents=True)
            demo_data.mkdir()
            (demo_data / "contextflow_delivery_meeting.md").write_text(
                "Decision: deliver within 24 hours.\nTODO: Prepare the competition video (2 hours).\n",
                encoding="utf-8",
            )
            store = Storage(root / "data" / "contextflow.db")
            runtime = ContextFlowRuntime(store)
            control = DemoControl(root, store, runtime)
            watcher = LocalInboxWatcher(meetings, data)
            store.record_watcher_heartbeat({
                "watcher_id": "local-inboxes:competition-demo",
                "base_session_key": "competition-demo",
                "status": "running",
                "meetings_path": str(meetings.resolve()),
                "data_path": str(data.resolve()),
                "interval_seconds": 1.0,
                "updated_at": datetime.now(UTC).isoformat(),
            })
            started = control.start("competition-demo")
            demo_path = Path(started["demo"]["file_path"])
            self.assertTrue(demo_path.is_file())
            self.assertFalse(demo_path.with_suffix(".tmp").exists())
            self.assertEqual(control.health("competition-demo")["watcher"]["status"], "running")

            self.assertEqual(watcher.detect(), [])
            arrival = watcher.detect()[0]
            meeting_session = started["demo"]["meeting_session_key"]
            runtime.dispatch("context_import_meeting_notes", {
                "session_key": meeting_session,
                "path": arrival["path"],
                "title": demo_path.stem,
            })
            self.assertEqual(control.status("competition-demo")["stage"], "drafts_ready")
            duplicate = control.replay_duplicate("competition-demo")
            self.assertTrue(duplicate["duplicate_protected"])
            self.assertEqual(duplicate["new_drafts_created"], 0)

            draft = store.list_drafts(meeting_session)[0]
            reviewed = control.review_draft(meeting_session, draft["draft_id"], "approved")
            self.assertEqual(reviewed["status"], "approved")
            answer = control.answer(meeting_session, "哪些 Agent 被触发了？")
            self.assertTrue(answer["grounded"])
            self.assertIn("MeetingActionAgent", answer["answer"])

            store.save_state(build_state("unrelated-session", "focus_mode", ["keep me"]))
            reset = control.reset("competition-demo")
            self.assertTrue(reset["recoverable"])
            self.assertFalse(demo_path.exists())
            self.assertTrue(any(Path(path).is_file() for path in reset["archived_files"]))
            self.assertIsNone(store.get_state(meeting_session))
            self.assertEqual(store.get_state("unrelated-session")["state"], "focus_mode")
            self.assertTrue(all(step["status"] == "pending" for step in control.status("competition-demo")["steps"]))

    def test_p1_demo_start_requires_a_live_watcher_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inbox" / "meetings").mkdir(parents=True)
            (root / "inbox" / "data").mkdir(parents=True)
            (root / "demo_data").mkdir()
            (root / "demo_data" / "contextflow_delivery_meeting.md").write_text("demo", encoding="utf-8")
            store = Storage(root / "data" / "contextflow.db")
            control = DemoControl(root, store, ContextFlowRuntime(store))
            with self.assertRaisesRegex(ValueError, "文件监听器未运行"):
                control.start("competition-demo")

    def test_meeting_file_event_id_prevents_duplicate_agent_orchestration_and_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "meeting.md"
            notes.write_text(
                "Decision: ship within 24 hours.\nAlice owns: Prepare slides (2 hours).\n",
                encoding="utf-8",
            )
            store = Storage(root / "state.db")
            runtime = ContextFlowRuntime(store)
            first = runtime.dispatch("context_import_meeting_notes", {
                "session_key": "idempotent-file", "path": str(notes),
            })
            repeated = runtime.dispatch("context_import_meeting_notes", {
                "session_key": "idempotent-file", "path": str(notes),
            })
            self.assertTrue(first["event"]["event_id"].startswith("meeting-file:"))
            self.assertFalse(first["duplicate_protected"])
            self.assertTrue(repeated["duplicate_protected"])
            self.assertEqual(repeated["new_drafts_created"], 0)
            self.assertEqual(len(store.list_sensed_events("idempotent-file")), 1)
            self.assertEqual(len(store.list_drafts("idempotent-file")), 2)

    def test_simulated_meeting_end_uses_same_chain_and_stable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            runtime.dispatch("rag_ingest", {
                "session_key": "simulated", "document_id": "knowledge",
                "title": "ContextFlow", "content": "ContextFlow Local RAG delivery evidence.",
            })
            payload = {
                "session_key": "simulated", "meeting_title": "ContextFlow review",
                "simulation_key": "demo-meeting-001",
                "meeting_text": (
                    "Decision: ship within 24 hours.\n"
                    "Alice owns: Prepare ContextFlow slides (2 hours).\n"
                ),
            }
            first = runtime.dispatch("context_simulate_meeting_end", payload)
            repeated = runtime.dispatch("context_simulate_meeting_end", payload)
            self.assertEqual(first["state_sequence"], ["meeting_ended", "deadline_risk"])
            self.assertEqual(first["event"]["source"], "meeting-end-simulator")
            self.assertEqual([item["agent"] for item in first["triggered_agents"]],
                ["MeetingActionAgent", "LocalRAGAgent", "DeliveryPlannerAgent"])
            self.assertEqual(first["orchestration"]["delivery"]["draft"]["status"], "awaiting_review")
            self.assertTrue(repeated["duplicate_protected"])
            self.assertEqual(len(store.list_drafts("simulated")), 2)

    def test_local_inbox_watcher_records_meeting_and_data_arrivals_in_separate_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meetings = root / "inbox" / "meetings"
            data = root / "inbox" / "data"
            meetings.mkdir(parents=True)
            data.mkdir(parents=True)
            (meetings / "existing.md").write_text("baseline", encoding="utf-8")
            watcher = LocalInboxWatcher(meetings, data)
            notes = meetings / "new-meeting.md"
            notes.write_text(
                "Decision: ship the demo within 24 hours.\n"
                "Alice owns: Prepare ContextFlow slides (2 hours).\n",
                encoding="utf-8",
            )
            dataset = data / "progress.csv"
            dataset.write_text("workstream,progress\nRAG,80\n", encoding="utf-8")
            (data / "ignored.json").write_text("{}", encoding="utf-8")
            self.assertEqual(watcher.detect(), [])
            arrivals = watcher.detect()
            self.assertEqual({item["kind"] for item in arrivals}, {"data", "meetings"})
            self.assertNotIn("existing.md", [item["relative_path"] for item in arrivals])

            store = Storage(root / "state.db")
            runtime = ContextFlowRuntime(store)
            ingress = EventIngress(lambda _session, event: runtime.dispatch("context_ingest_event", event))
            meeting_result = runtime.dispatch("context_import_meeting_notes", {
                "session_key": "local:meetings", "path": str(notes),
            })
            data_result = ingress.accept("data_file_ready", {
                "session_key": "local:data", "path": str(dataset),
            })
            self.assertEqual(meeting_result["state_sequence"], ["meeting_ended", "deadline_risk"])
            self.assertEqual(data_result["context"]["state"], "data_ready_for_analysis")
            self.assertEqual(data_result["context"]["confidence"], 0.95)
            self.assertEqual(store.list_sensed_events("local:data")[0]["event_type"], "data.file_ready")
            self.assertIsNone(store.get_dataset("local:data", "progress"))

    def test_state_contract_exposes_evidence_agents_and_confirmation_boundary(self) -> None:
        meeting = describe_state("meeting_ended")
        self.assertEqual(meeting["minimum_confidence"], 0.96)
        self.assertIn("MeetingActionAgent", meeting["allowed_agents"])
        self.assertIn("LocalRAGAgent", meeting["allowed_agents"])
        self.assertIn("sending email", meeting["confirmation_required_for"])
        risk = describe_state("deadline_risk")
        self.assertIn("DeliveryPlannerAgent", risk["allowed_agents"])
        self.assertIn("at least one actionable task", risk["required_evidence"])

    def test_fixed_meeting_file_loop_reaches_reviewable_delivery_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            watcher = DesktopFileWatcher(inbox)
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            runtime.dispatch("rag_ingest", {
                "session_key": "proactive-loop", "document_id": "knowledge",
                "title": "ContextFlow knowledge",
                "content": "ContextFlow uses Local RAG evidence and a delivery plan for the continuous demo.",
            })
            notes = inbox / "delivery-meeting.md"
            notes.write_text(
                "Decision: ship the ContextFlow demo within 24 hours.\n"
                "Alice owns: Prepare ContextFlow slides (2 hours).\n"
                "Bob owns: After Prepare ContextFlow slides, validate Local RAG evidence (2 hours).\n",
                encoding="utf-8",
            )
            change = watcher.detect()[0]
            self.assertEqual(change["action"], "created")
            result = runtime.dispatch("context_import_meeting_notes", {
                "session_key": "proactive-loop", "path": str(notes), "title": "ContextFlow delivery",
            })
            self.assertEqual(result["state_sequence"], ["meeting_ended", "deadline_risk"])
            self.assertEqual(result["orchestration"]["rag"]["status"], "matched")
            self.assertEqual(result["orchestration"]["delivery"]["status"], "planned")
            self.assertEqual(result["orchestration"]["delivery"]["draft"]["status"], "awaiting_review")
            self.assertTrue(result["artifacts"]["requires_review"])
            self.assertIn("sending messages", result["confirmation_boundary"]["requires_confirmation"])
            transitions = runtime.store.list_state_transitions("proactive-loop")
            self.assertEqual([item["to_state"] for item in transitions], ["deadline_risk", "meeting_ended"])

    def test_natural_language_facing_event_tools_use_ingress_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            calendar = runtime.dispatch("context_simulate_calendar", {
                "session_key": "natural-calendar", "kind": "meeting_ended",
                "attributes": {"title": "Natural language review"},
            })
            self.assertEqual(calendar["context"]["state"], "meeting_ended")
            notes_path = root / "meeting.md"
            notes_path.write_text("Decision: demo the event ingress.", encoding="utf-8")
            notes = runtime.dispatch("context_import_meeting_notes", {
                "session_key": "natural-notes", "path": str(notes_path),
            })
            self.assertEqual(notes["event"]["source"], "meeting-notes-importer")
            self.assertEqual(notes["context"]["state"], "meeting_ended")

    def test_event_ingress_normalizes_calendar_and_meeting_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            ingress = EventIngress(lambda _session, event: runtime.dispatch("context_ingest_event", event))
            calendar = ingress.accept("calendar", {"session_key": "ingress-a",
                "kind": "meeting_ended", "attributes": {"title": "Review"}})
            self.assertEqual(calendar["event"]["source"], "calendar-simulator")
            self.assertEqual(calendar["context"]["state"], "meeting_ended")
            notes_path = root / "notes.md"
            notes_path.write_text("Decision: ship the demo.\nTODO: record walkthrough.", encoding="utf-8")
            notes = ingress.accept("meeting_notes_imported", {"session_key": "ingress-b",
                "path": str(notes_path), "title": "Imported review"})
            self.assertEqual(notes["event"]["event_type"], "document.meeting_notes_imported")
            self.assertEqual(notes["context"]["state"], "meeting_ended")
            self.assertEqual(notes["event"]["attributes"]["character_count"], len(notes_path.read_text(encoding="utf-8")))

    def test_demo_desktop_watcher_detects_changes_and_drives_focus_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "work.txt"
            document.write_text("first", encoding="utf-8")
            watcher = DesktopFileWatcher(root)
            document.write_text("second version", encoding="utf-8")
            changes = watcher.detect()
            self.assertEqual(changes[0]["action"], "modified")
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            ingress = EventIngress(lambda _session, event: runtime.dispatch("context_ingest_event", event))
            result = ingress.accept("desktop_file_changed", {"session_key": "desktop-sense", **changes[0]})
            self.assertEqual(result["context"]["state"], "focus_mode")
            self.assertEqual(result["context"]["confidence"], 0.72)

    def test_sensed_events_infer_explainable_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            now = datetime.now(UTC) - timedelta(minutes=3)
            focused = runtime.dispatch("context_ingest_event", {
                "session_key": "aware-a", "event_id": "focus-start",
                "event_type": "user.focus_started", "source": "demo-control",
                "occurred_at": now.isoformat(),
            })
            self.assertEqual(focused["context"]["state"], "focus_mode")
            ended = runtime.dispatch("context_ingest_event", {
                "session_key": "aware-a", "event_id": "focus-end",
                "event_type": "user.focus_ended", "source": "demo-control",
                "occurred_at": (now + timedelta(minutes=1)).isoformat(),
            })
            self.assertEqual(ended["context"]["state"], "idle")
            meeting = runtime.dispatch("context_ingest_event", {
                "session_key": "aware-a", "event_id": "meeting-end",
                "event_type": "calendar.meeting_ended", "source": "demo-calendar",
                "occurred_at": (now + timedelta(minutes=2)).isoformat(),
                "attributes": {"title": "Demo review"},
            })
            self.assertEqual(meeting["context"]["state"], "meeting_ended")
            self.assertIn("Demo review ended", meeting["context"]["evidence"][0])
            transitions = store.list_state_transitions("aware-a")
            self.assertEqual([item["to_state"] for item in transitions], ["meeting_ended", "idle", "focus_mode"])

    def test_sensed_event_ingestion_is_idempotent_and_session_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            payload = {"session_key": "aware-a", "event_id": "same-event",
                "event_type": "user.focus_started", "source": "demo-control"}
            self.assertTrue(runtime.dispatch("context_ingest_event", payload)["accepted"])
            repeated = runtime.dispatch("context_ingest_event", payload)
            self.assertTrue(repeated["deduplicated"])
            self.assertEqual(len(store.list_sensed_events("aware-a")), 1)
            self.assertEqual(store.list_sensed_events("aware-b"), [])

    def test_deadline_event_overrides_lower_priority_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("context_ingest_event", {"session_key": "aware-risk",
                "event_type": "user.focus_started", "source": "demo-control"})
            risk = runtime.dispatch("context_ingest_event", {"session_key": "aware-risk",
                "event_type": "calendar.deadline_changed", "source": "demo-calendar",
                "attributes": {"hours_remaining": 24, "completion": 0.4}})
            self.assertEqual(risk["context"]["state"], "deadline_risk")
            self.assertEqual(risk["context"]["confidence"], 0.94)

    def test_meeting_workflow_requires_meeting_state(self) -> None:
        state = build_state("s-1", "meeting_ended", ["recording ready"])
        draft = meeting_draft("s-1", "项目讨论", state)
        self.assertEqual(draft["status"], "awaiting_review")
        self.assertTrue(draft["payload"]["steps"][-1]["requires_confirmation"])

    def test_delivery_workflow_requires_deadline_risk(self) -> None:
        state = build_state("s-2", "deadline_risk", ["deadline under 72h"])
        draft = delivery_plan_draft("s-2", "完成预选赛材料", 48, state, ["PPT 初稿"])
        self.assertEqual(draft["payload"]["strategy"], "minimum_viable_delivery_first")
        self.assertEqual(draft["payload"]["steps"][2]["id"], "critical_path")

    def test_state_is_session_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            store.save_state(build_state("session-a", "focus_mode", ["explicit preference"]))
            self.assertEqual(store.get_state("session-a")["state"], "focus_mode")
            self.assertIsNone(store.get_state("session-b"))

    def test_registry_routes_by_state(self) -> None:
        meeting_agents = {agent["name"] for agent in find_agents_for_state("meeting_ended")}
        deadline_agents = {agent["name"] for agent in find_agents_for_state("deadline_risk")}
        self.assertIn("MeetingActionAgent", meeting_agents)
        self.assertIn("LocalRAGAgent", meeting_agents)
        self.assertIn("DeliveryPlannerAgent", deadline_agents)

    def test_runtime_records_successful_agent_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            runtime.dispatch("context_update_state", {"session_key": "s-3", "state": "meeting_ended", "evidence": ["recording ready"]})
            runtime.dispatch("workflow_prepare_meeting", {"session_key": "s-3", "meeting_title": "项目讨论"})
            events = store.list_execution_events("s-3")
            self.assertEqual(events[0]["selected_agent"], "MeetingActionAgent")
            self.assertTrue(events[0]["success"])
            self.assertEqual(runtime.runtime_info("s-3")["evaluation"]["total_events"], 2)

    def test_delivery_workflow_establishes_deadline_state_from_explicit_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            result = runtime.dispatch("workflow_plan_delivery", {
                "session_key": "s-4", "goal": "提交项目", "deadline_hours": 24,
            })
            event = store.list_execution_events("s-4")[0]
            self.assertTrue(event["success"])
            self.assertEqual(result["context"]["state"], "deadline_risk")
            self.assertEqual(store.get_state("s-4")["state"], "deadline_risk")
            self.assertEqual(result["draft"]["status"], "awaiting_review")

    def test_runtime_tracks_confirmation_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            runtime.dispatch("context_update_state", {"session_key": "s-5", "state": "meeting_ended", "evidence": ["recording ready"]})
            draft = runtime.dispatch("workflow_prepare_meeting", {"session_key": "s-5", "meeting_title": "项目讨论"})["draft"]
            runtime.dispatch("workflow_confirm_action", {"session_key": "s-5", "draft_id": draft["draft_id"], "decision": "approved"})
            event = store.list_execution_events("s-5")[0]
            self.assertEqual(event["user_confirmation"], "approved")

    def test_runtime_rejects_cross_session_or_changed_final_draft_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("context_update_state", {"session_key": "owner", "state": "meeting_ended", "evidence": ["meeting ended"]})
            draft = runtime.dispatch("workflow_prepare_meeting", {"session_key": "owner", "meeting_title": "Review"})["draft"]
            with self.assertRaisesRegex(ValueError, "current session"):
                runtime.dispatch("workflow_confirm_action", {"session_key": "other", "draft_id": draft["draft_id"], "decision": "approved"})
            runtime.dispatch("workflow_confirm_action", {"session_key": "owner", "draft_id": draft["draft_id"], "decision": "approved"})
            repeated = runtime.dispatch("workflow_confirm_action", {"session_key": "owner", "draft_id": draft["draft_id"], "decision": "approved"})
            self.assertTrue(repeated["deduplicated"])
            with self.assertRaisesRegex(ValueError, "final review decision"):
                runtime.dispatch("workflow_confirm_action", {"session_key": "owner", "draft_id": draft["draft_id"], "decision": "rejected"})

    def test_local_rag_returns_cited_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("rag_ingest", {
                "session_key": "rag-a",
                "document_id": "mtclaw-training",
                "title": "MTClaw 培训材料",
                "source_name": "training.md",
                "content": "MTClaw Function Router 会根据请求自动加载匹配工具。ContextFlow 负责状态感知和工作流编排。",
            })
            result = runtime.dispatch("rag_answer", {"session_key": "rag-a", "query": "MTClaw 如何加载工具"})
            self.assertTrue(result["grounded"])
            self.assertEqual(result["citations"][0]["source_name"], "training.md")
            self.assertIn("自动加载匹配工具", result["answer"])

    def test_local_rag_is_session_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("rag_ingest", {
                "session_key": "rag-a",
                "document_id": "private-note",
                "title": "私有资料",
                "content": "ContextFlow 的状态层使用 session_key 隔离。",
            })
            result = runtime.dispatch("rag_search", {"session_key": "rag-b", "query": "状态隔离"})
            self.assertEqual(result["matches"], [])

    def test_local_rag_reports_missing_evidence_without_invention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("rag_ingest", {
                "session_key": "rag-a",
                "document_id": "scope",
                "title": "范围说明",
                "content": "本资料只描述本地知识库。",
            })
            result = runtime.dispatch("rag_answer", {"session_key": "rag-a", "query": "火星探测计划"})
            self.assertFalse(result["grounded"])
            self.assertEqual(result["citations"], [])

    def test_office_flow_creates_reviewable_meeting_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("context_update_state", {"session_key": "office-a", "state": "meeting_ended", "evidence": ["meeting ended"]})
            result = runtime.dispatch("office_process_meeting", {
                "session_key": "office-a",
                "meeting_title": "巅峰赛准备会",
                "meeting_text": "确认本周完成 AIBOOK 演示。\n张三负责：准备 RAG 演示资料。\nTODO：录制连续对话视频。",
            })
            self.assertEqual(result["draft"]["status"], "awaiting_review")
            self.assertEqual(result["artifacts"]["action_items"][0]["owner"], "张三")
            self.assertIn("请确认", result["artifacts"]["email_draft"])

    def test_office_flow_records_meeting_state_for_a_direct_natural_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            result = runtime.dispatch("office_process_meeting", {
                "session_key": "office-natural-a",
                "meeting_title": "Meeting wrap-up",
                "meeting_text": "Decision: complete the demo.\nTODO: Alex records the walkthrough.",
            })
            self.assertEqual(result["draft"]["status"], "awaiting_review")
            self.assertEqual(store.get_state("office-natural-a")["state"], "meeting_ended")

    def test_meeting_orchestrator_plans_delivery_and_suggests_rag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "state.db")
            runtime = ContextFlowRuntime(store)
            runtime.dispatch("rag_ingest", {
                "session_key": "orchestrated-meeting-a",
                "document_id": "demo-script",
                "title": "Demo script",
                "source_name": "demo-script.md",
                "content": "The ContextFlow demo needs slides, a recording, and the MTClaw workflow.",
            })
            result = runtime.dispatch("office_process_meeting", {
                "session_key": "orchestrated-meeting-a",
                "meeting_title": "Demo delivery meeting",
                "meeting_text": "Decision: ship the demo within 24 hours. TODO: Prepare slides. TODO: Record walkthrough.",
            })
            orchestration = result["orchestration"]
            self.assertEqual(orchestration["delivery"]["status"], "planned")
            self.assertEqual(orchestration["rag"]["status"], "matched")
            self.assertEqual(
                store.get_state("orchestrated-meeting-a")["state"],
                "deadline_risk",
            )
            self.assertEqual(
                orchestration["delivery"]["draft"]["payload"]["plan"]["forecast"],
                "on_track",
            )
            self.assertEqual(
                len(orchestration["delivery"]["draft"]["payload"]["plan"]["tasks"]),
                2,
            )
            repeated = runtime.dispatch("workflow_plan_delivery", {
                "session_key": "orchestrated-meeting-a",
                "goal": "Demo delivery meeting",
                "deadline_hours": 24,
            })
            self.assertTrue(repeated["deduplicated"])
            self.assertEqual(
                repeated["draft"]["draft_id"],
                orchestration["delivery"]["draft"]["draft_id"],
            )

    def test_meeting_orchestrator_extracts_owners_effort_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            result = runtime.dispatch("office_process_meeting", {
                "session_key": "long-plan-a",
                "meeting_title": "Release plan",
                "meeting_text": (
                    "Decision: deliver within 8 hours.\n"
                    "Alice owns: Prepare slides (2h).\n"
                    "Bob owns: After Prepare slides, record demo video (3h).\n"
                    "Carol owns: After record demo video, verify integration (1h)."
                ),
            })
            plan = result["orchestration"]["delivery"]["draft"]["payload"]["plan"]
            tasks = plan["tasks"]
            self.assertEqual([task["owner"] for task in tasks], ["Alice", "Bob", "Carol"])
            self.assertEqual([task["effort_hours"] for task in tasks], [2, 3, 1])
            self.assertEqual(tasks[1]["depends_on"], [tasks[0]["id"]])
            self.assertEqual(tasks[2]["depends_on"], [tasks[1]["id"]])
            self.assertEqual(plan["critical_path"], [task["id"] for task in tasks])
            self.assertEqual(plan["critical_path_hours"], 6)

    def test_dashboard_snapshot_exposes_state_trace_and_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            result = runtime.dispatch("office_process_meeting", {
                "session_key": "dashboard-a",
                "meeting_title": "Status review",
                "meeting_text": "Decision: deliver within 24 hours.\nTODO: Prepare slides.",
            })
            snapshot = runtime.dashboard_snapshot("dashboard-a")
            self.assertEqual(snapshot["state"]["state"], "deadline_risk")
            self.assertEqual(snapshot["events"][0]["selected_tool"], "office_process_meeting")
            self.assertEqual(snapshot["drafts"][0]["draft_id"], result["orchestration"]["delivery"]["draft"]["draft_id"])
            self.assertEqual(snapshot["evaluation"]["total_events"], 1)
            self.assertEqual([run["agent"] for run in snapshot["agent_runs"][:3]], [
                "MeetingActionAgent", "LocalRAGAgent", "DeliveryPlannerAgent",
            ])
            self.assertEqual(snapshot["draft_cards"][0]["kind_label"], "交付计划")
            self.assertTrue(snapshot["draft_cards"][0]["requires_confirmation"])

    def test_dashboard_marks_file_ingress_as_real_and_exposes_confirmation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "competition-meeting.md"
            notes.write_text(
                "Decision: deliver within 24 hours.\nTODO: Prepare the competition video.\n",
                encoding="utf-8",
            )
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            runtime.dispatch("context_import_meeting_notes", {
                "session_key": "dashboard-file-a", "path": str(notes),
            })
            snapshot = runtime.dashboard_snapshot("dashboard-file-a")
            self.assertEqual(snapshot["event_source"]["kind"], "real_file")
            self.assertEqual(snapshot["event_source"]["event_type"], "document.meeting_notes_imported")
            self.assertTrue(all(run["source_kind"] == "real_file" for run in snapshot["agent_runs"]))
            self.assertIn("delivery-plan draft", snapshot["confirmation_boundary"]["automatic"])
            self.assertGreaterEqual(len(snapshot["draft_cards"]), 2)

    def test_delivery_planner_builds_critical_path_and_replans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            runtime.dispatch("context_update_state", {"session_key": "plan-a", "state": "deadline_risk", "evidence": ["deadline in 24 hours"]})
            planned = runtime.dispatch("workflow_plan_delivery", {
                "session_key": "plan-a",
                "goal": "完成巅峰赛提交",
                "deadline_hours": 8,
                "task_specs": [
                    {"id": "materials", "title": "整理资料", "effort_hours": 2},
                    {"id": "rag", "title": "完成 RAG", "depends_on": ["materials"], "effort_hours": 3},
                    {"id": "demo", "title": "录制演示", "depends_on": ["rag"], "effort_hours": 4},
                ],
            })
            plan = planned["draft"]["payload"]["plan"]
            self.assertEqual(plan["critical_path"], ["materials", "rag", "demo"])
            self.assertEqual(plan["forecast"], "at_risk")
            replanned = runtime.dispatch("delivery_replan", {
                "session_key": "plan-a",
                "draft_id": planned["draft"]["draft_id"],
                "failed_task_ids": ["rag"],
            })
            revised = replanned["draft"]["payload"]["plan"]
            self.assertTrue(replanned["fallback_required"])
            self.assertEqual(next(task for task in revised["tasks"] if task["id"] == "demo")["status"], "blocked")

    def test_data_insight_profiles_risks_and_writes_chart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "progress.csv"
            csv_path.write_text("owner,progress,hours\nRAG,80,4\nData,20,5\nData,20,5\nDemo,150,-1\n", encoding="utf-8")
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            profile = runtime.dispatch("data_profile", {"session_key": "data-a", "dataset_id": "progress", "source_path": str(csv_path)})["dataset"]
            self.assertEqual(profile["row_count"], 4)
            self.assertEqual(next(column for column in profile["columns"] if column["name"] == "progress")["type"], "numeric")
            analysis = runtime.dispatch("data_analyze", {"session_key": "data-a", "dataset_id": "progress"})["analysis"]
            self.assertTrue(any(risk["type"] == "duplicate_row" for risk in analysis["risks"]))
            self.assertTrue(any(risk["type"] == "negative_value" for risk in analysis["risks"]))
            chart = runtime.dispatch("data_chart", {"session_key": "data-a", "dataset_id": "progress", "x_column": "owner", "y_column": "progress"})["chart"]
            self.assertTrue(Path(chart["artifact_path"]).is_file())
            self.assertIs(chart["created"], True)
            self.assertEqual(chart["aggregation"], "sum")
            self.assertEqual(chart["categories"], ["RAG", "Data", "Demo"])
            self.assertEqual(chart["aggregated_values"], {"RAG": 80.0, "Data": 40.0, "Demo": 150.0})

    def test_data_insight_is_session_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "progress.csv"
            csv_path.write_text("owner,progress\nRAG,80\n", encoding="utf-8")
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            runtime.dispatch("data_profile", {"session_key": "data-a", "dataset_id": "progress", "source_path": str(csv_path)})
            with self.assertRaisesRegex(ValueError, "dataset not found"):
                runtime.dispatch("data_analyze", {"session_key": "data-b", "dataset_id": "progress"})

    def test_data_insight_reads_basic_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xlsx_path = root / "metrics.xlsx"
            with zipfile.ZipFile(xlsx_path, "w") as archive:
                archive.writestr("xl/worksheets/sheet1.xml", """<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>metric</t></is></c><c r=\"B1\" t=\"inlineStr\"><is><t>value</t></is></c></row><row r=\"2\"><c r=\"A2\" t=\"inlineStr\"><is><t>latency</t></is></c><c r=\"B2\"><v>5.5</v></c></row></sheetData></worksheet>""")
            runtime = ContextFlowRuntime(Storage(root / "state.db"))
            profile = runtime.dispatch("data_profile", {"session_key": "data-a", "dataset_id": "metrics", "source_path": str(xlsx_path)})["dataset"]
            self.assertEqual(profile["row_count"], 1)
            self.assertEqual(next(column for column in profile["columns"] if column["name"] == "value")["type"], "numeric")

    def test_desktop_executor_requires_approval_and_can_undo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "desktop"
            root.mkdir()
            (root / "meeting-notes.txt").write_text("review meeting", encoding="utf-8")
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            search = runtime.dispatch("desktop_search_files", {
                "session_key": "desktop-a", "root_path": str(root), "query": "notes"
            })["search"]
            self.assertEqual(search["matches"][0]["relative_path"], "meeting-notes.txt")
            plan = runtime.dispatch("desktop_plan_archive", {
                "session_key": "desktop-a", "root_path": str(root), "relative_paths": ["meeting-notes.txt"]
            })["plan"]
            self.assertTrue((root / "meeting-notes.txt").is_file())
            with self.assertRaisesRegex(ValueError, "must be approved"):
                runtime.dispatch("desktop_execute_archive", {"session_key": "desktop-a", "plan_id": plan["plan_id"]})
            runtime.dispatch("desktop_confirm_archive", {
                "session_key": "desktop-a", "plan_id": plan["plan_id"], "decision": "approved"
            })
            execution = runtime.dispatch("desktop_execute_archive", {"session_key": "desktop-a", "plan_id": plan["plan_id"]})
            self.assertFalse((root / "meeting-notes.txt").exists())
            self.assertTrue((root / ".contextflow_archive" / "meeting-notes.txt").is_file())
            undone = runtime.dispatch("desktop_undo_archive", {
                "session_key": "desktop-a", "operation_id": execution["operation"]["operation_id"]
            })
            self.assertEqual(undone["operation"]["status"], "undone")
            self.assertTrue((root / "meeting-notes.txt").is_file())

    def test_desktop_executor_rejects_path_escape_and_cross_session_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "desktop"
            root.mkdir()
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            runtime = ContextFlowRuntime(Storage(Path(directory) / "state.db"))
            with self.assertRaisesRegex(ValueError, "inside root_path"):
                runtime.dispatch("desktop_plan_archive", {
                    "session_key": "desktop-a", "root_path": str(root), "relative_paths": ["../outside.txt"]
                })
            plan = runtime.dispatch("desktop_plan_archive", {
                "session_key": "desktop-a", "root_path": str(root), "relative_paths": ["safe.txt"]
            })["plan"]
            with self.assertRaisesRegex(ValueError, "archive plan not found"):
                runtime.dispatch("desktop_confirm_archive", {
                    "session_key": "desktop-b", "plan_id": plan["plan_id"], "decision": "approved"
                })


if __name__ == "__main__":
    unittest.main()

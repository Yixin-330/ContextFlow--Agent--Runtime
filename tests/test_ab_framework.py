from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.ab_cases import CaseContext, CaseOutcome
from benchmark.ab_runner import ABRunner, load_gateway_adapters, write_report
from benchmark.adapters import BenchmarkRequest, RunResult
from benchmark.evaluators import Evaluation, FileDelta, PhaseRecorder, diff_snapshots, snapshot_tree
from benchmark.trace_collector import OpenClawSessionResolver, RouterTrace, RouterTraceCollector


class FakeAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sessions: list[str] = []

    def run(self, request: BenchmarkRequest) -> RunResult:
        self.sessions.append(request.session_key)
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=True,
            latency_ms=10 if self.name == "native_openclaw" else 5,
            final_answer="verified answer",
            usage={"total_tokens": 7},
        )


class RecordingCase:
    case_id = "recording_case"
    scene = "framework"
    description = "Exercise the shared runner lifecycle"

    def __init__(self) -> None:
        self.contexts: list[CaseContext] = []

    def prepare(self, context: CaseContext) -> dict[str, Any]:
        self.contexts.append(context)
        fixture = context.workspace / "fixture.txt"
        fixture.write_text("stable", encoding="utf-8")
        return {"fixture": fixture}

    def execute(self, adapter: FakeAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
        result = adapter.run(BenchmarkRequest(self.case_id, "prompt", context.session_key))
        return CaseOutcome(turns=[result], details={"fixture": fixture["fixture"].name})

    def verify(self, context: CaseContext, fixture: dict[str, Any], outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation:
        return Evaluation.from_failures(
            [] if outcome.final_answer == "verified answer" and file_delta.change_count == 0 else ["unexpected outcome"],
            workspace_unchanged=file_delta.change_count == 0,
        )


class TreatmentOnlyPassingCase(RecordingCase):
    case_id = "treatment_only_passing_case"

    def verify(self, context: CaseContext, fixture: dict[str, Any], outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation:
        return Evaluation.from_failures(
            [] if context.adapter_name == "contextflow_openclaw" else ["native expected failure"],
        )


class ArchiveStyleCase(RecordingCase):
    case_id = "archive_style_case"

    def execute(self, adapter: FakeAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
        target = fixture["fixture"]
        context.checkpoint("after_plan")
        archive = context.workspace / "archive"
        archive.mkdir()
        target.rename(archive / target.name)
        context.checkpoint("after_execution")
        (archive / target.name).rename(target)
        archive.rmdir()
        context.checkpoint("after_undo")
        result = adapter.run(BenchmarkRequest(self.case_id, "prompt", context.session_key))
        return CaseOutcome(turns=[result])


class LatencyBreakdownCase(RecordingCase):
    case_id = "latency_breakdown_case"

    def execute(self, adapter: FakeAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
        if adapter.name == "contextflow_openclaw":
            preparation = RunResult(
                adapter=adapter.name,
                case_id=self.case_id,
                session_key=context.session_key,
                success=True,
                latency_ms=30,
                final_answer="prepared",
            )
            question = RunResult(
                adapter=adapter.name,
                case_id=self.case_id,
                session_key=context.session_key,
                success=True,
                latency_ms=5,
                final_answer="verified answer",
            )
            return CaseOutcome(turns=[preparation, question], details={
                "preparation_latency_ms": 30,
                "question_latency_ms": 5,
            })
        question = RunResult(
            adapter=adapter.name,
            case_id=self.case_id,
            session_key=context.session_key,
            success=True,
            latency_ms=10,
            final_answer="verified answer",
        )
        return CaseOutcome(turns=[question], details={
            "preparation_latency_ms": 0,
            "question_latency_ms": 10,
        })


class SharedReadableWorkspaceCase(RecordingCase):
    case_id = "shared_readable_workspace_case"

    def prepare(self, context: CaseContext) -> dict[str, Any]:
        fixture = super().prepare(context)
        if os.name == "nt":
            completed = subprocess.run(
                ["icacls", str(context.workspace)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            fixture["shared_readable"] = "Everyone:" in completed.stdout
        else:
            fixture["shared_readable"] = bool(context.workspace.stat().st_mode & 0o055)
        return fixture

    def verify(self, context: CaseContext, fixture: dict[str, Any], outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation:
        return Evaluation.from_failures(
            [] if fixture["shared_readable"] else ["workspace is not readable by the Router process"],
            workspace_shared_readable=fixture["shared_readable"],
        )


class FakeTraceCollector(RouterTraceCollector):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("http://unused", attempts=1)
        self.payload = payload

    def _fetch(self) -> Any:
        return self.payload


class UnifiedABFrameworkTests(unittest.TestCase):
    def test_summary_separates_adapter_and_strict_global_verdicts(self) -> None:
        report = ABRunner({
            "native_openclaw": FakeAdapter("native_openclaw"),
            "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
        }).run([TreatmentOnlyPassingCase()], runs=2)

        summary = report["summary"]
        self.assertTrue(summary["contextflow_all_runs_passed"])
        self.assertFalse(summary["native_all_runs_passed"])
        self.assertFalse(summary["all_runs_passed"])

    def test_file_snapshots_detect_created_deleted_and_modified_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "modified.txt").write_text("before", encoding="utf-8")
            (root / "deleted.txt").write_text("delete", encoding="utf-8")
            before = snapshot_tree(root)
            (root / "modified.txt").write_text("after", encoding="utf-8")
            (root / "deleted.txt").unlink()
            (root / "created.txt").write_text("create", encoding="utf-8")
            delta = diff_snapshots(before, snapshot_tree(root))
            self.assertEqual(delta.created, ("created.txt",))
            self.assertEqual(delta.deleted, ("deleted.txt",))
            self.assertEqual(delta.modified, ("modified.txt",))

    def test_phase_recorder_compares_each_checkpoint_with_start_and_prior_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "meeting-notes.txt"
            source.write_text("stable", encoding="utf-8")
            phases = PhaseRecorder(root)
            phases.start()
            planned = phases.checkpoint("after_plan")
            archive = root / "archive"
            archive.mkdir()
            source.rename(archive / source.name)
            executed = phases.checkpoint("after_execution")
            (archive / source.name).rename(source)
            archive.rmdir()
            undone = phases.checkpoint("after_undo")
            final = phases.finalize()
            self.assertEqual(planned.from_start.change_count, 0)
            self.assertEqual(executed.from_start.created, ("archive/meeting-notes.txt",))
            self.assertEqual(executed.from_start.deleted, ("meeting-notes.txt",))
            self.assertEqual(undone.from_start.change_count, 0)
            self.assertEqual(undone.from_previous.created, ("meeting-notes.txt",))
            self.assertEqual(undone.from_previous.deleted, ("archive/meeting-notes.txt",))
            self.assertEqual(final.from_start.change_count, 0)

    def test_runner_includes_named_phase_deltas_and_reserved_final_phase(self) -> None:
        report = ABRunner({
            "native_openclaw": FakeAdapter("native_openclaw"),
            "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
        }).run([ArchiveStyleCase()], runs=1)
        for record in report["records"]:
            phases = record["phase_deltas"]
            self.assertEqual(list(phases), ["after_plan", "after_execution", "after_undo", "final"])
            self.assertEqual(phases["after_plan"]["from_start"]["change_count"], 0)
            self.assertEqual(phases["after_execution"]["from_start"]["change_count"], 2)
            self.assertEqual(phases["after_undo"]["from_start"]["change_count"], 0)
            self.assertEqual(record["file_delta"]["change_count"], 0)
            snapshots = record["phase_snapshots"]
            self.assertEqual(list(snapshots), ["before", "after_plan", "after_execution", "after_undo", "final"])
            self.assertEqual(
                snapshots["before"]["fixture.txt"]["sha256"],
                snapshots["after_undo"]["fixture.txt"]["sha256"],
            )

    def test_runner_enforces_exact_contextflow_tool_sequence_contract(self) -> None:
        class TraceContractCase(RecordingCase):
            expected_contextflow_tools = ("desktop_search_files", "desktop_plan_archive")

        case = TraceContractCase()
        base = Evaluation.from_failures([], outcome_verified=True)
        matching = RouterTrace(
            session_key="s",
            found=True,
            tool_names=("desktop_search_files", "desktop_plan_archive"),
        )
        mismatch = RouterTrace(
            session_key="s",
            found=True,
            tool_names=("desktop_plan_archive", "desktop_search_files"),
        )
        self.assertTrue(ABRunner._verify_trace_contract(case, "contextflow_openclaw", matching, base).passed)
        failed = ABRunner._verify_trace_contract(case, "contextflow_openclaw", mismatch, base)
        self.assertFalse(failed.passed)
        self.assertIn("tool sequence mismatch", failed.failures[0])
        self.assertTrue(ABRunner._verify_trace_contract(case, "native_openclaw", RouterTrace("s"), base).passed)

    def test_runner_alternates_ab_ba_and_isolates_sessions_and_workspaces(self) -> None:
        native = FakeAdapter("native_openclaw")
        contextflow = FakeAdapter("contextflow_openclaw")
        case = RecordingCase()
        report = ABRunner({"native_openclaw": native, "contextflow_openclaw": contextflow}).run([case], runs=2)
        records = report["records"]
        self.assertEqual(
            [(record["attempt"], record["adapter"], record["order"], record["order_position"]) for record in records],
            [
                (1, "native_openclaw", "AB", 1),
                (1, "contextflow_openclaw", "AB", 2),
                (2, "contextflow_openclaw", "BA", 1),
                (2, "native_openclaw", "BA", 2),
            ],
        )
        self.assertEqual(len({context.session_key for context in case.contexts}), 4)
        self.assertEqual(len({str(context.workspace) for context in case.contexts}), 4)
        self.assertTrue(report["summary"]["all_runs_passed"])

    def test_runner_workspaces_are_readable_by_the_external_router_process(self) -> None:
        temporary_root = ROOT / "benchmark" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        report = ABRunner({
            "native_openclaw": FakeAdapter("native_openclaw"),
            "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
        }, temporary_root=temporary_root).run([SharedReadableWorkspaceCase()], runs=1)
        self.assertTrue(report["summary"]["all_runs_passed"], report["records"])

    def test_runner_reports_preparation_question_and_end_to_end_latency_separately(self) -> None:
        report = ABRunner({
            "native_openclaw": FakeAdapter("native_openclaw"),
            "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
        }).run([LatencyBreakdownCase()], runs=1)
        summaries = {item["adapter"]: item for item in report["summary"]["cases"]}
        native = summaries["native_openclaw"]
        contextflow = summaries["contextflow_openclaw"]
        self.assertEqual(native["end_to_end_latency_mean_ms"], 10)
        self.assertEqual(native["preparation_latency_mean_ms"], 0)
        self.assertEqual(native["question_latency_mean_ms"], 10)
        self.assertEqual(contextflow["end_to_end_latency_mean_ms"], 35)
        self.assertEqual(contextflow["preparation_latency_mean_ms"], 30)
        self.assertEqual(contextflow["question_latency_mean_ms"], 5)
        context_record = next(item for item in report["records"] if item["adapter"] == "contextflow_openclaw")
        self.assertEqual(context_record["latency_breakdown_ms"], {
            "preparation": 30,
            "question": 5,
            "end_to_end": 35,
        })

    def test_router_trace_is_sanitized_and_attached_only_to_contextflow(self) -> None:
        case = RecordingCase()
        native = FakeAdapter("native_openclaw")
        contextflow = FakeAdapter("contextflow_openclaw")

        class SessionAwareCollector(RouterTraceCollector):
            def __init__(self) -> None:
                super().__init__("http://unused", attempts=1)
                self.current_session = ""

            def collect(self, session_key: str):  # type: ignore[override]
                self.current_session = session_key
                payload = {"entries": [{
                    "session_key": session_key,
                    "timestamp": "2026-08-12T00:00:00Z",
                    "tool_rounds": 2,
                    "tool_calls": [{"function": {"name": "desktop_search_files", "arguments": "sensitive"}}],
                    "tool_results": [{"success": True, "result": {"private": "omitted"}}],
                    "llm_calls": [
                        {"model": "router", "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}},
                        {"model": "upstream"},
                    ],
                }]}
                return FakeTraceCollector(payload).collect(session_key)

        report = ABRunner(
            {"native_openclaw": native, "contextflow_openclaw": contextflow},
            trace_collector=SessionAwareCollector(),
        ).run([case], runs=1)
        native_record, context_record = report["records"]
        self.assertFalse(native_record["router_trace_found"])
        self.assertTrue(context_record["router_trace_found"])
        self.assertEqual(context_record["router_tool_calls"], ["desktop_search_files"])
        self.assertEqual(context_record["tool_calls"], 1)
        self.assertEqual(context_record["llm_call_count"], 2)
        self.assertEqual(context_record["response_usage"]["total_tokens"], 7)
        self.assertEqual(context_record["router_usage"]["total_tokens"], 11)
        self.assertEqual(context_record["usage"]["total_tokens"], 18)
        self.assertNotIn("sensitive", json.dumps(context_record, ensure_ascii=False))

    def test_router_trace_aggregates_all_session_entries_in_timestamp_order(self) -> None:
        payload = {"entries": [
            {
                "session_key": "other-session",
                "timestamp": "2026-08-12T00:00:00Z",
                "tool_calls": [{"function": {"name": "ignored"}}],
            },
            {
                "session_key": "multi-turn",
                "timestamp": "2026-08-12T00:00:02Z",
                "tool_rounds": 2,
                "tool_calls": [{"function": {"name": "rag_answer", "arguments": "secret-answer"}}],
                "tool_results": [{"success": False, "error": "tool failed"}],
                "llm_calls": [{}, {}],
                "fallback_count": 1,
            },
            {
                "session_key": "multi-turn",
                "timestamp": "2026-08-12T00:00:01Z",
                "tool_rounds": 1,
                "tool_calls": [{"function": {"name": "rag_ingest", "arguments": "secret-document"}}],
                "tool_results": [{"success": True}],
                "llm_calls": [{}],
            },
        ]}
        trace = FakeTraceCollector(payload).collect("multi-turn")
        self.assertTrue(trace.found)
        self.assertEqual(trace.entry_count, 2)
        self.assertEqual(trace.tool_names, ("rag_ingest", "rag_answer"))
        self.assertEqual(trace.tool_rounds, 3)
        self.assertEqual(trace.llm_call_count, 3)
        self.assertEqual(trace.usage["total_tokens"], 0)
        self.assertEqual(trace.tool_result_count, 2)
        self.assertEqual(trace.tool_error_count, 1)
        self.assertEqual(trace.fallback_count, 1)
        self.assertEqual(trace.metadata["first_timestamp"], "2026-08-12T00:00:01Z")
        self.assertEqual(trace.metadata["last_timestamp"], "2026-08-12T00:00:02Z")
        self.assertNotIn("secret", json.dumps(trace.to_dict(), ensure_ascii=False))

    def test_router_trace_counts_openai_tool_error_shape(self) -> None:
        payload = {"entries": [{
            "session_key": "tool-errors",
            "timestamp": "2026-08-12T00:00:01Z",
            "tool_results": [
                {
                    "name": "rag_ingest",
                    "is_error": True,
                    "content": json.dumps({"error": "permission denied"}),
                },
                {
                    "name": "rag_answer",
                    "is_error": False,
                    "content": json.dumps({"result": "ok"}),
                },
            ],
        }]}
        trace = FakeTraceCollector(payload).collect("tool-errors")
        self.assertEqual(trace.tool_result_count, 2)
        self.assertEqual(trace.tool_error_count, 1)

    def test_router_trace_detects_json_content_error_without_flag(self) -> None:
        payload = {"entries": [{
            "session_key": "content-error",
            "timestamp": "2026-08-12T00:00:01Z",
            "tool_results": [{"content": '{"error":"failed"}'}],
        }]}
        trace = FakeTraceCollector(payload).collect("content-error")
        self.assertEqual(trace.tool_error_count, 1)

    def test_openclaw_session_resolver_maps_gateway_session_key_to_router_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "sessions.json"
            store.write_text(json.dumps({
                "agent:main:benchmark:case:1": {"sessionId": "gateway-session-id"},
            }), encoding="utf-8")
            resolver = OpenClawSessionResolver(store, agent_id="main")
            self.assertEqual(resolver.resolve("benchmark:case:1"), "gateway-session-id")
            self.assertIsNone(resolver.resolve("benchmark:missing"))

    def test_runner_uses_resolved_openclaw_session_id_for_router_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "sessions.json"
            store.write_text(json.dumps({
                "agent:main:benchmark:trace": {"sessionId": "router-visible-id"},
            }), encoding="utf-8")

            class FixedSessionCase(RecordingCase):
                def execute(self, adapter: FakeAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
                    object.__setattr__(context, "session_key", "benchmark:trace")
                    return super().execute(adapter, context, fixture)

            collector = FakeTraceCollector({"entries": [{
                "session_key": "router-visible-id",
                "timestamp": "2026-08-12T00:00:00Z",
                "tool_rounds": 1,
                "tool_calls": [{"function": {"name": "rag_answer"}}],
                "tool_results": [{"success": True}],
            }]})
            report = ABRunner(
                {
                    "native_openclaw": FakeAdapter("native_openclaw"),
                    "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
                },
                trace_collector=collector,
                trace_session_resolver=OpenClawSessionResolver(store),
            ).run([FixedSessionCase()], runs=1)
            context_record = next(item for item in report["records"] if item["adapter"] == "contextflow_openclaw")
            self.assertTrue(context_record["router_trace_found"])
            self.assertEqual(context_record["router_tool_calls"], ["rag_answer"])

    def test_runner_retries_delayed_openclaw_session_mapping(self) -> None:
        class DelayedResolver:
            def __init__(self) -> None:
                self.calls = 0

            def resolve(self, session_key: str) -> str | None:
                self.calls += 1
                return "delayed-session-id" if self.calls == 3 else None

        class RecordingCollector:
            def __init__(self) -> None:
                self.session = ""

            def collect(self, session_key: str) -> RouterTrace:
                self.session = session_key
                return RouterTrace(session_key=session_key)

        resolver = DelayedResolver()
        collector = RecordingCollector()
        runner = ABRunner(
            {
                "native_openclaw": FakeAdapter("native_openclaw"),
                "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
            },
            trace_collector=collector,  # type: ignore[arg-type]
            trace_session_resolver=resolver,  # type: ignore[arg-type]
        )
        runner._trace("inbound-session", "contextflow_openclaw")
        self.assertEqual(resolver.calls, 3)
        self.assertEqual(collector.session, "delayed-session-id")

    def test_config_loader_and_report_writer_keep_secrets_out_of_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "gateway.json"
            config.write_text(json.dumps({
                "native_openclaw": {"base_url": "http://gateway/v1", "model": "native", "api_key_env": "SECRET_ENV"},
                "contextflow_openclaw": {"base_url": "http://gateway/v1", "model": "router", "api_key_env": "SECRET_ENV"},
            }), encoding="utf-8")
            adapters, metadata = load_gateway_adapters(config)
            self.assertEqual(set(adapters), {"native_openclaw", "contextflow_openclaw"})
            self.assertEqual(adapters["native_openclaw"].config.timeout_seconds, 180)
            self.assertEqual(adapters["contextflow_openclaw"].config.timeout_seconds, 180)
            self.assertEqual(metadata["router_trace_url"], "http://127.0.0.1:18790/v1/tool_history")
            report = ABRunner({
                "native_openclaw": FakeAdapter("native_openclaw"),
                "contextflow_openclaw": FakeAdapter("contextflow_openclaw"),
            }, metadata=metadata).run([RecordingCase()], runs=1)
            json_path, markdown_path = write_report(report, root / "reports")
            serialized = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
            self.assertNotIn("SECRET_ENV", serialized)
            self.assertIn("AB on odd attempts", serialized)
            self.assertIn("ContextFlow 全部运行通过：True", serialized)
            self.assertIn("Native 全部运行通过：True", serialized)


if __name__ == "__main__":
    unittest.main()

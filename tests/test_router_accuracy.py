from __future__ import annotations

import unittest
from collections import Counter
import json
from pathlib import Path
import tempfile

from benchmark.adapters import BenchmarkRequest, RunResult
from benchmark.router_accuracy import RouterAccuracyRunner, RouterCase, load_router_cases, write_router_accuracy_report
from benchmark.trace_collector import RouterTrace, RouterTraceCollector


class FakeTraceCollector:
    def __init__(self) -> None:
        self.traces: dict[str, RouterTrace] = {}

    def collect(self, session_key: str) -> RouterTrace:
        return self.traces[session_key]


class FakeRouterAdapter:
    name = "contextflow_openclaw"

    def __init__(self, collector: FakeTraceCollector, observed: dict[str, tuple[str, ...]]) -> None:
        self.collector = collector
        self.observed = observed
        self.requests: list[BenchmarkRequest] = []

    def run(self, request: BenchmarkRequest) -> RunResult:
        self.requests.append(request)
        tools = self.observed.get(request.case_id, ("rag_ingest",) if request.case_id.endswith(":prepare") else ())
        previous = self.collector.traces.get(request.session_key)
        previous_tools = previous.tool_names if previous else ()
        all_tools = (*previous_tools, *tools)
        self.collector.traces[request.session_key] = RouterTrace(
            session_key=request.session_key,
            found=True,
            entry_count=(previous.entry_count if previous else 0) + 1,
            tool_names=all_tools,
            tool_rounds=(previous.tool_rounds if previous else 0) + max(1, len(tools)),
            llm_call_count=(previous.llm_call_count if previous else 0) + len(tools) + 1,
            tool_result_count=(previous.tool_result_count if previous else 0) + len(tools),
            usage={
                "prompt_tokens": (previous.usage["prompt_tokens"] if previous else 0) + 10,
                "completion_tokens": (previous.usage["completion_tokens"] if previous else 0) + 5,
                "total_tokens": (previous.usage["total_tokens"] if previous else 0) + 15,
            },
        )
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=True,
            latency_ms=20,
            final_answer="完成",
        )


class PayloadTraceCollector(RouterTraceCollector):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__("http://unused", attempts=1)
        self.payload = payload

    def _fetch(self) -> object:
        return self.payload


class RouterAccuracyTests(unittest.TestCase):
    def test_fixed_suite_has_twenty_cases_with_the_agreed_distribution(self) -> None:
        cases = load_router_cases()

        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.case_id for case in cases}), 20)
        self.assertEqual(Counter(case.category for case in cases), {
            "file_search": 3,
            "rag": 3,
            "data_analysis": 3,
            "meeting": 3,
            "delivery": 2,
            "archive": 2,
            "state": 1,
            "upstream": 3,
        })
        self.assertEqual(sum(case.expected_route == "tool" for case in cases), 17)
        self.assertEqual(sum(case.expected_route == "upstream" for case in cases), 3)
        self.assertTrue(all(case.expected_tools for case in cases if case.expected_route == "tool"))
        self.assertTrue(all(not case.expected_tools for case in cases if case.expected_route == "upstream"))
        rag_answer = next(case for case in cases if case.case_id == "router_004")
        self.assertIn(("rag_answer",), rag_answer.accepted_tool_sequences)
        self.assertIn(("rag_search", "rag_answer"), rag_answer.accepted_tool_sequences)

    def test_runner_scores_tool_and_upstream_routes_from_isolated_router_traces(self) -> None:
        cases = (
            RouterCase("tool_case", "file_search", "在 {workspace} 查找 notes", "tool", ("desktop_search_files",), ("data_profile",)),
            RouterCase("chat_case", "upstream", "解释关键路径", "upstream", (), ("*",)),
        )
        collector = FakeTraceCollector()
        adapter = FakeRouterAdapter(collector, {
            "tool_case": ("desktop_search_files",),
            "chat_case": (),
        })
        with tempfile.TemporaryDirectory() as directory:
            report = RouterAccuracyRunner(
                adapter,
                collector,
                temporary_root=Path(directory),
            ).run(cases, runs=1)

        self.assertTrue(report["summary"]["all_runs_passed"])
        self.assertEqual(report["summary"]["overall"]["first_tool_hit_rate"], 1.0)
        self.assertEqual(report["summary"]["overall"]["exact_tool_sequence_rate"], 1.0)
        self.assertEqual(report["summary"]["overall"]["upstream_route_hit_rate"], 1.0)
        self.assertEqual(len({record["session_key"] for record in report["records"]}), 2)
        self.assertIn(str(Path(directory)), adapter.requests[0].prompt)

    def test_trace_classifies_invalid_arguments_without_retaining_arguments(self) -> None:
        trace = PayloadTraceCollector({"entries": [{
            "session_key": "invalid-args",
            "tool_calls": [
                {"function": {"name": "desktop_search_files", "arguments": "sensitive"}},
                {"function": {"name": "data_profile", "arguments": "private-path"}},
            ],
            "tool_results": [
                {"is_error": True, "content": '{"error":"missing required argument: root_path"}'},
                {"is_error": True, "content": '{"error":"internal database failure"}'},
            ],
        }]}).collect("invalid-args")

        self.assertEqual(trace.tool_error_count, 2)
        self.assertEqual(trace.invalid_argument_count, 1)
        serialized = str(trace.to_dict())
        self.assertNotIn("sensitive", serialized)
        self.assertNotIn("private-path", serialized)

    def test_report_writer_outputs_json_markdown_and_attempt_csv(self) -> None:
        cases = (
            RouterCase("tool_case", "file_search", "在 {workspace} 查找 notes", "tool", ("desktop_search_files",), ()),
            RouterCase("chat_case", "upstream", "解释关键路径", "upstream", (), ("*",)),
        )
        collector = FakeTraceCollector()
        adapter = FakeRouterAdapter(collector, {
            "tool_case": ("desktop_search_files",),
            "chat_case": (),
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = RouterAccuracyRunner(adapter, collector, temporary_root=root).run(cases, runs=1)
            json_path, markdown_path, csv_path = write_router_accuracy_report(report, root / "reports")
            self.assertTrue(json_path.is_file())
            self.assertTrue(markdown_path.is_file())
            self.assertTrue(csv_path.is_file())
            markdown = markdown_path.read_text(encoding="utf-8")
            csv_text = csv_path.read_text(encoding="utf-8-sig")

        self.assertIn("工具族命中率", markdown)
        self.assertIn("可接受序列命中率", markdown)
        self.assertIn("无效参数率", markdown)
        self.assertIn("每个 Case 的稳定性", markdown)
        self.assertIn("tool_case", csv_text)
        self.assertIn("desktop_search_files", csv_text)

    def test_fixed_suite_expands_to_sixty_isolated_attempts(self) -> None:
        cases = load_router_cases()
        collector = FakeTraceCollector()
        adapter = FakeRouterAdapter(collector, {case.case_id: case.expected_tools for case in cases})
        with tempfile.TemporaryDirectory() as directory:
            report = RouterAccuracyRunner(adapter, collector, temporary_root=Path(directory)).run(cases, runs=3)

        self.assertEqual(report["expected_attempts"], 60)
        self.assertEqual(len(report["records"]), 60)
        self.assertEqual(len({record["session_key"] for record in report["records"]}), 60)
        self.assertEqual(report["summary"]["overall"]["route_accuracy"], 1.0)
        self.assertEqual(report["summary"]["overall"]["expected_multi_step_success_rate"], 1.0)

    def test_metrics_distinguish_wrong_unnecessary_and_redundant_calls(self) -> None:
        cases = (
            RouterCase("wrong", "file_search", "x", "tool", ("desktop_search_files",), ("data_profile",)),
            RouterCase("chat", "upstream", "x", "upstream", (), ("*",)),
            RouterCase("expected_multi", "archive", "x", "tool", ("desktop_search_files", "desktop_plan_archive"), ()),
            RouterCase("redundant", "state", "x", "tool", ("context_get_state",), ()),
        )
        collector = FakeTraceCollector()
        adapter = FakeRouterAdapter(collector, {
            "wrong": ("data_profile",),
            "chat": ("context_get_state",),
            "expected_multi": ("desktop_search_files", "desktop_plan_archive"),
            "redundant": ("context_get_state", "context_get_state"),
        })
        with tempfile.TemporaryDirectory() as directory:
            report = RouterAccuracyRunner(adapter, collector, temporary_root=Path(directory)).run(cases, runs=1)
        metrics = report["summary"]["overall"]

        self.assertEqual(metrics["first_tool_hit_rate"], 0.6667)
        self.assertEqual(metrics["exact_tool_sequence_rate"], 0.3333)
        self.assertEqual(metrics["upstream_route_hit_rate"], 0.0)
        self.assertEqual(metrics["wrong_tool_invocation_rate"], 0.5)
        self.assertEqual(metrics["unnecessary_tool_invocation_rate"], 1.0)
        self.assertEqual(metrics["multi_call_rate"], 0.5)
        self.assertEqual(metrics["expected_multi_step_success_rate"], 1.0)
        self.assertEqual(metrics["redundant_call_rate"], 0.5)

    def test_rag_preparation_is_excluded_from_measured_trace_and_latency(self) -> None:
        case = RouterCase(
            "rag_case", "rag", "回答本地资料问题", "tool", ("rag_answer",), (),
            (("rag_answer",), ("rag_search", "rag_answer")),
        )
        collector = FakeTraceCollector()
        adapter = FakeRouterAdapter(collector, {"rag_case": ("rag_search", "rag_answer")})
        with tempfile.TemporaryDirectory() as directory:
            report = RouterAccuracyRunner(adapter, collector, temporary_root=Path(directory)).run((case,), runs=1)
        record = report["records"][0]

        self.assertEqual([request.case_id for request in adapter.requests], ["rag_case:prepare", "rag_case"])
        self.assertTrue(record["knowledge_prepared"])
        self.assertTrue(record["trace_isolated"])
        self.assertEqual(record["observed_tools"], ["rag_search", "rag_answer"])
        self.assertTrue(record["acceptable_tool_sequence_correct"])
        self.assertFalse(record["exact_tool_sequence_correct"])
        self.assertTrue(record["route_verified"])
        self.assertEqual(record["latency_ms"], 20)
        self.assertEqual(record["preparation_latency_ms"], 20)
        self.assertEqual(report["summary"]["overall"]["acceptable_tool_sequence_rate"], 1.0)
        self.assertEqual(report["summary"]["overall"]["tool_family_hit_rate"], 1.0)
        self.assertEqual(report["summary"]["overall"]["knowledge_preparation_success_rate"], 1.0)
        self.assertEqual(report["summary"]["overall"]["trace_isolation_rate"], 1.0)

    def test_tool_descriptions_keep_rag_and_delivery_routes_single_purpose(self) -> None:
        functions_path = Path(__file__).resolve().parents[1] / "mtclaw" / "functions.jsonl"
        functions = {
            row["name"]: row
            for row in (
                json.loads(line)
                for line in functions_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

        self.assertIn("Do not use it as a required precursor to rag_answer", functions["rag_search"]["description"])
        self.assertIn("performs retrieval internally", functions["rag_answer"]["description"])
        self.assertIn("do not call context_get_state or context_update_state first", functions["workflow_plan_delivery"]["description"])


if __name__ == "__main__":
    unittest.main()

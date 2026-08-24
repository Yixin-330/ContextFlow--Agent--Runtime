from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.runner import run_cases
from benchmark.adapters import BenchmarkRequest, GatewayConfig, NativeOpenClawAdapter, RunResult
from benchmark.gateway_smoke import _load_adapters, _result_row
from benchmark.ab_cases import CaseContext, LocalRagCase
from benchmark.evaluators import PhaseRecorder
from benchmark.cases.registry import case_choices, select_cases


class ContextFlowScenarioBenchTests(unittest.TestCase):
    def test_archive_safety_suite_registers_three_separate_cases(self) -> None:
        self.assertIn("archive_safety", case_choices())
        self.assertEqual(
            [case.case_id for case in select_cases("archive_safety")],
            ["archive_unconfirmed", "archive_rejected", "archive_lifecycle"],
        )

    def test_all_core_scenarios_pass_once(self) -> None:
        report = run_cases(runs=1)
        self.assertTrue(report["summary"]["all_runs_passed"], report["records"])
        self.assertEqual(len(report["summary"]["cases"]), 9)

    def test_run_result_is_configuration_neutral_and_adapter_fails_safely_without_token(self) -> None:
        request = BenchmarkRequest(case_id="smoke", prompt="hello", session_key="bench-session")
        adapter = NativeOpenClawAdapter(GatewayConfig(
            base_url="http://127.0.0.1:1/v1", model="baseline", api_key_env="CONTEXTFLOW_MISSING_TEST_TOKEN",
        ))
        result = adapter.run(request)
        self.assertIsInstance(result, RunResult)
        self.assertFalse(result.success)
        self.assertEqual(result.adapter, "native_openclaw")
        self.assertIn("CONTEXTFLOW_MISSING_TEST_TOKEN", result.error or "")

    def test_gateway_smoke_loads_two_configured_adapters_and_uses_shared_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.json"
            config_path.write_text("""{
              "native_openclaw": {"base_url": "http://127.0.0.1:18789/v1", "model": "baseline"},
              "contextflow_openclaw": {"base_url": "http://127.0.0.1:18789/v1", "model": "function_router/function-router"}
            }""", encoding="utf-8")
            native, contextflow = _load_adapters(config_path)
            self.assertEqual(native.name, "native_openclaw")
            self.assertEqual(contextflow.name, "contextflow_openclaw")
        row = _result_row(RunResult(adapter="native_openclaw", case_id="x", session_key="s", success=True, latency_ms=1, final_answer="meeting-notes.txt"), "meeting-notes.txt")
        self.assertTrue(row["outcome_verified"])

    def test_local_rag_fixture_is_fixed_and_question_has_expected_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            context = CaseContext("rag", "local_rag", 1, "native_openclaw", "AB", 1, "session", workspace, PhaseRecorder(workspace))
            fixture = LocalRagCase("rag_runtime_roles_v1").prepare(context)
            self.assertTrue(fixture["reference"].is_file())
            self.assertTrue(fixture["ingest_reference"].is_file())
            self.assertNotEqual(fixture["ingest_reference"].parent, workspace)
            self.assertEqual(
                fixture["ingest_reference"].read_bytes(),
                fixture["reference"].read_bytes(),
            )
            self.assertIn("ContextFlow", fixture["reference"].read_text(encoding="utf-8"))
            self.assertEqual(fixture["question"]["required_source"], "contextflow_reference.md")
            self.assertEqual(fixture["question"]["required_any_of"][0], ["state", "状态"])


if __name__ == "__main__":
    unittest.main()

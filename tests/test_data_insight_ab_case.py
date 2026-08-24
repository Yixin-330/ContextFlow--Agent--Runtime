from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from benchmark.ab_runner import ABRunner
from benchmark.adapters import BenchmarkRequest, RunResult
from benchmark.cases.data_insight import DataInsightCase
from benchmark.cases.registry import select_cases
from benchmark.trace_collector import RouterTrace


class DataInsightFakeAdapter:
    """Public Adapter seam fake: writes the artifact requested by the Case prompt."""

    def __init__(self, name: str, answer: dict[str, object]) -> None:
        self.name = name
        self.answer = answer
        self.requests: list[BenchmarkRequest] = []

    def run(self, request: BenchmarkRequest) -> RunResult:
        self.requests.append(request)
        output_match = re.search(r"CHART_OUTPUT_PATH=(.+)", request.prompt)
        if output_match and self.answer.get("chart", {}).get("created"):  # type: ignore[union-attr]
            output = Path(output_match.group(1).strip())
            output.parent.mkdir(parents=True, exist_ok=True)
            categories = self.answer["chart"]["categories"]  # type: ignore[index]
            values = self.answer["chart"].get("aggregated_values", {})  # type: ignore[index]
            output.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg">'
                + "".join(f"<text>{category}</text><text>{values.get(category, '')}</text>" for category in categories)
                + "</svg>",
                encoding="utf-8",
            )
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=True,
            latency_ms=12,
            final_answer=json.dumps(self.answer, ensure_ascii=False),
        )


class DataInsightTraceCollector:
    def collect(self, session_key: str) -> RouterTrace:
        return RouterTrace(
            session_key=session_key,
            found=True,
            entry_count=3,
            tool_names=("data_profile", "data_analyze", "data_chart"),
            tool_rounds=3,
            llm_call_count=4,
            tool_result_count=3,
        )


RISK_ANSWER = {
    "row_count": 20,
    "column_count": 7,
    "risk_level": "high",
    "risks": [
        {"type": "duplicate_row", "column": None, "occurrence_count": 1},
        {"type": "missing_data", "column": "owner", "missing_ratio": 0.3, "missing_count": 6},
        {"type": "negative_value", "column": "remaining_hours", "values": [-4, -3, -2]},
        {"type": "numeric_outlier", "column": "risk_score", "values": [100]},
    ],
    "chart": {
        "created": True,
        "artifact_path": "use CHART_OUTPUT_PATH",
        "x_column": "workstream",
        "y_column": "risk_score",
        "aggregation": "sum",
        "categories": [
            "AgentCore", "LocalRAG", "DataInsight", "OfficeFlow", "DeliveryPlanner", "MTClawIntegration",
            "GatewayHealth", "SessionBridge", "Dashboard", "Benchmark", "Deployment", "Documentation",
        ],
        "aggregated_values": {
            "AgentCore": 42.0, "LocalRAG": 46.0, "DataInsight": 50.0, "OfficeFlow": 44.0,
            "DeliveryPlanner": 75.0, "MTClawIntegration": 53.0, "GatewayHealth": 21.0,
            "SessionBridge": 23.0, "Dashboard": 25.0, "Benchmark": 27.0, "Deployment": 119.0,
            "Documentation": 22.0,
        },
    },
}

CLEAN_ANSWER = {
    "row_count": 12,
    "column_count": 7,
    "risk_level": "low",
    "risks": [],
    "chart": {
        "created": True,
        "artifact_path": "use CHART_OUTPUT_PATH",
        "x_column": "workstream",
        "y_column": "risk_score",
        "aggregation": "sum",
        "categories": [
            "AgentCore", "LocalRAG", "DataInsight", "OfficeFlow", "DeliveryPlanner", "MTClawIntegration",
            "GatewayHealth", "SessionBridge", "Dashboard", "Benchmark", "Deployment", "Documentation",
        ],
        "aggregated_values": {},
        "error": "",
    },
}

INVALID_ANSWER = {
    "row_count": 3,
    "column_count": 4,
    "risk_level": "low",
    "risks": [],
    "chart": {
        "created": False,
        "artifact_path": "",
        "x_column": "workstream",
        "y_column": "risk_score",
        "aggregation": "sum",
        "categories": [],
        "aggregated_values": {},
        "error": "risk_score column is missing, so the chart was not created",
    },
}


class DataInsightABCaseTests(unittest.TestCase):
    @staticmethod
    def _runner(adapters: dict[str, DataInsightFakeAdapter], temporary_root: Path) -> ABRunner:
        return ABRunner(adapters, trace_collector=DataInsightTraceCollector(), temporary_root=temporary_root)

    def test_labeled_risk_case_verifies_exact_risks_and_real_svg_for_both_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapters = {
                "native_openclaw": DataInsightFakeAdapter("native_openclaw", RISK_ANSWER),
                "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", RISK_ANSWER),
            }
            report = self._runner(adapters, Path(directory)).run(
                [DataInsightCase("project_risk_labeled.csv")], runs=1
            )
        self.assertTrue(report["summary"]["all_runs_passed"], report["records"])
        for record in report["records"]:
            self.assertEqual(record["metrics"]["risk_precision"], 1.0)
            self.assertEqual(record["metrics"]["risk_recall"], 1.0)
            self.assertTrue(record["metrics"]["chart_verified"])
            self.assertTrue(record["metrics"]["source_unchanged"])
            self.assertEqual(record["phase_deltas"]["after_analysis"]["from_start"]["created"], ["artifacts/analysis-chart.svg"])
        request = adapters["contextflow_openclaw"].requests[0]
        self.assertIn("duplicate_row、missing_data、negative_value、numeric_outlier", request.prompt)
        self.assertIn("按 sum（求和）聚合", request.prompt)
        self.assertIn("data_profile → data_analyze → data_chart", request.prompt)
        self.assertIn("MUST exclude the CSV header row", request.prompt)
        self.assertIn("reopen it before answering", request.prompt)
        self.assertIn('"aggregation":"sum"', request.prompt)

    def test_registry_expands_data_insight_suite_to_three_labeled_cases(self) -> None:
        cases = select_cases("data_insight")
        self.assertEqual(
            [case.case_id for case in cases],
            ["data_risk_labeled_v1", "data_clean_control_v1", "data_invalid_schema_v1"],
        )

    def test_correct_risk_names_with_wrong_evidence_fail_verification(self) -> None:
        wrong = json.loads(json.dumps(RISK_ANSWER))
        wrong["risks"][1]["missing_ratio"] = 0.1
        wrong["risks"][2]["values"] = [-999]
        wrong["risks"][3]["values"] = [88]
        wrong["chart"]["aggregated_values"]["Deployment"] = 1
        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": DataInsightFakeAdapter("native_openclaw", wrong),
                "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", wrong),
            }, Path(directory)).run([DataInsightCase("project_risk_labeled.csv")], runs=1)
        self.assertFalse(report["summary"]["all_runs_passed"])
        for record in report["records"]:
            self.assertIn("risk evidence does not match ground truth", record["failures"])
            self.assertFalse(record["metrics"]["chart_verified"])

    def test_semantic_risk_aliases_are_scored_consistently(self) -> None:
        aliased = json.loads(json.dumps(RISK_ANSWER))
        aliased["risks"][0].update({"type": "完整重复行", "occurrence_count": 2})
        aliased["risks"][1]["type"] = "缺失率>=30%"
        aliased["risks"][2]["type"] = "负值"
        aliased["risks"][3]["type"] = "数值离群点"
        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": DataInsightFakeAdapter("native_openclaw", aliased),
                "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", aliased),
            }, Path(directory)).run([DataInsightCase("project_risk_labeled.csv")], runs=1)
        self.assertTrue(report["summary"]["all_runs_passed"], report["records"])

    def test_mean_chart_is_rejected_when_contract_requires_sum(self) -> None:
        mean_chart = json.loads(json.dumps(RISK_ANSWER))
        mean_chart["chart"]["aggregation"] = "mean"
        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": DataInsightFakeAdapter("native_openclaw", mean_chart),
                "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", mean_chart),
            }, Path(directory)).run([DataInsightCase("project_risk_labeled.csv")], runs=1)
        self.assertFalse(report["summary"]["all_runs_passed"])
        for record in report["records"]:
            self.assertIn("chart answer must use sum aggregation", record["failures"])

    def test_existing_verified_svg_is_not_rejected_when_answer_omits_created_flag(self) -> None:
        missing_flag = json.loads(json.dumps(RISK_ANSWER))
        del missing_flag["chart"]["created"]

        class ArtifactAdapter(DataInsightFakeAdapter):
            def run(self, request: BenchmarkRequest) -> RunResult:
                output_match = re.search(r"CHART_OUTPUT_PATH=(.+)", request.prompt)
                self.requests.append(request)
                if output_match:
                    output = Path(output_match.group(1).strip())
                    output.parent.mkdir(parents=True, exist_ok=True)
                    categories = self.answer["chart"]["categories"]  # type: ignore[index]
                    values = self.answer["chart"]["aggregated_values"]  # type: ignore[index]
                    output.write_text(
                        '<svg xmlns="http://www.w3.org/2000/svg">'
                        + "".join(f"<text>{category}</text><text>{values[category]}</text>" for category in categories)
                        + "</svg>",
                        encoding="utf-8",
                    )
                return RunResult(
                    adapter=self.name,
                    case_id=request.case_id,
                    session_key=request.session_key,
                    success=True,
                    latency_ms=12,
                    final_answer=json.dumps(self.answer, ensure_ascii=False),
                )

        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": ArtifactAdapter("native_openclaw", missing_flag),
                "contextflow_openclaw": ArtifactAdapter("contextflow_openclaw", missing_flag),
            }, Path(directory)).run([DataInsightCase("project_risk_labeled.csv")], runs=1)
        self.assertTrue(report["summary"]["all_runs_passed"], report["records"])

    def test_explicit_false_created_flag_is_rejected_even_when_svg_exists(self) -> None:
        contradicted = json.loads(json.dumps(RISK_ANSWER))
        contradicted["chart"]["created"] = False

        class ContradictingAdapter(DataInsightFakeAdapter):
            def run(self, request: BenchmarkRequest) -> RunResult:
                output_match = re.search(r"CHART_OUTPUT_PATH=(.+)", request.prompt)
                self.requests.append(request)
                if output_match:
                    output = Path(output_match.group(1).strip())
                    output.parent.mkdir(parents=True, exist_ok=True)
                    categories = self.answer["chart"]["categories"]  # type: ignore[index]
                    values = self.answer["chart"]["aggregated_values"]  # type: ignore[index]
                    output.write_text(
                        '<svg xmlns="http://www.w3.org/2000/svg">'
                        + "".join(f"<text>{category}</text><text>{values[category]}</text>" for category in categories)
                        + "</svg>",
                        encoding="utf-8",
                    )
                return RunResult(
                    adapter=self.name,
                    case_id=request.case_id,
                    session_key=request.session_key,
                    success=True,
                    latency_ms=12,
                    final_answer=json.dumps(self.answer, ensure_ascii=False),
                )

        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": ContradictingAdapter("native_openclaw", contradicted),
                "contextflow_openclaw": ContradictingAdapter("contextflow_openclaw", contradicted),
            }, Path(directory)).run([DataInsightCase("project_risk_labeled.csv")], runs=1)
        self.assertFalse(report["summary"]["all_runs_passed"])
        for record in report["records"]:
            self.assertIn(
                "answer explicitly reports chart.created=false despite a verified artifact",
                record["failures"],
            )

    def test_clean_control_and_invalid_schema_are_successful_expected_outcomes(self) -> None:
        for fixture_name, answer in (
            ("project_clean_control.csv", CLEAN_ANSWER),
            ("project_invalid_schema.csv", INVALID_ANSWER),
        ):
            with self.subTest(fixture=fixture_name), tempfile.TemporaryDirectory() as directory:
                report = self._runner({
                    "native_openclaw": DataInsightFakeAdapter("native_openclaw", answer),
                    "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", answer),
                }, Path(directory)).run([DataInsightCase(fixture_name)], runs=1)
                self.assertTrue(report["summary"]["all_runs_passed"], report["records"])
                for record in report["records"]:
                    self.assertEqual(record["metrics"]["risk_precision"], 1.0)
                    self.assertEqual(record["metrics"]["risk_recall"], 1.0)
                    self.assertTrue(record["metrics"]["chart_verified"])

    def test_invalid_schema_must_explain_why_chart_was_not_created(self) -> None:
        silent_failure = json.loads(json.dumps(INVALID_ANSWER))
        silent_failure["chart"]["error"] = ""
        with tempfile.TemporaryDirectory() as directory:
            report = self._runner({
                "native_openclaw": DataInsightFakeAdapter("native_openclaw", silent_failure),
                "contextflow_openclaw": DataInsightFakeAdapter("contextflow_openclaw", silent_failure),
            }, Path(directory)).run([DataInsightCase("project_invalid_schema.csv")], runs=1)
        self.assertFalse(report["summary"]["all_runs_passed"])
        for record in report["records"]:
            self.assertIn("invalid-schema answer must explain chart failure", record["failures"])


if __name__ == "__main__":
    unittest.main()

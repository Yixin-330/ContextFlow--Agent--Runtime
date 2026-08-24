from __future__ import annotations

"""Outcome-verifiable A/B cases for local tabular risk analysis."""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

from ..ab_cases import CaseContext, CaseOutcome
from ..adapters import BenchmarkRequest, OpenClawGatewayAdapter
from ..evaluators import Evaluation, FileDelta, precision_recall

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "benchmark" / "fixtures" / "data"
GROUND_TRUTH_PATH = FIXTURE_DIR / "data_insight_ground_truth.json"

CASE_IDS = {
    "project_risk_labeled.csv": "data_risk_labeled_v1",
    "project_clean_control.csv": "data_clean_control_v1",
    "project_invalid_schema.csv": "data_invalid_schema_v1",
}

RISK_TYPE_ALIASES = {
    "duplicate_row": "duplicate_row",
    "完整重复行": "duplicate_row",
    "missing_data": "missing_data",
    "missing_values": "missing_data",
    "缺失率>=30%": "missing_data",
    "缺失率≥30%": "missing_data",
    "negative_value": "negative_value",
    "负值": "negative_value",
    "numeric_outlier": "numeric_outlier",
    "outlier": "numeric_outlier",
    "数值离群点": "numeric_outlier",
}


@dataclass(frozen=True)
class DataInsightCase:
    """Analyze one frozen CSV and verify risks plus a real chart artifact."""

    fixture_name: str
    scene: str = "data_insight"
    expected_contextflow_tools: tuple[str, ...] = ("data_profile", "data_analyze", "data_chart")

    def __post_init__(self) -> None:
        if self.fixture_name not in CASE_IDS:
            raise ValueError(f"unknown data-insight fixture: {self.fixture_name}")

    @property
    def case_id(self) -> str:
        return CASE_IDS[self.fixture_name]

    @property
    def description(self) -> str:
        return f"Deterministic CSV analysis and chart verification: {self.fixture_name}"

    def prepare(self, context: CaseContext) -> dict[str, Any]:
        truth_document = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
        truth = truth_document["fixtures"][self.fixture_name]
        source = FIXTURE_DIR / self.fixture_name
        copied_source = context.workspace / self.fixture_name
        shutil.copyfile(source, copied_source)
        actual_hash = hashlib.sha256(copied_source.read_bytes()).hexdigest()
        if actual_hash != truth["sha256"]:
            raise ValueError(f"fixture hash mismatch: {self.fixture_name}")
        dataset_id = f"ab-{Path(self.fixture_name).stem}-{uuid4().hex[:8]}"
        chart_request = truth_document["chart_request"]
        requested_chart = context.workspace / "artifacts" / "analysis-chart.svg"
        router_chart = ROOT / "data" / "charts" / f"{dataset_id}_{chart_request['x_column']}_{chart_request['y_column']}.svg"
        return {
            "source": copied_source,
            "source_sha256": actual_hash,
            "truth": truth,
            "chart_request": chart_request,
            "dataset_id": dataset_id,
            "requested_chart": requested_chart,
            "router_chart": router_chart,
        }

    def execute(
        self,
        adapter: OpenClawGatewayAdapter,
        context: CaseContext,
        fixture: dict[str, Any],
    ) -> CaseOutcome:
        prompt = self._prompt(fixture)
        result = adapter.run(BenchmarkRequest(
            case_id=self.case_id,
            prompt=prompt,
            session_key=context.session_key,
            system_prompt=(
                "Run only the requested local CSV analysis. Do not modify the source CSV. "
                "Return exactly one JSON object matching the requested schema."
            ),
        ))
        parsed, parse_error = self._parse_answer(result.final_answer)
        artifact = self._normalize_artifact(fixture)
        context.checkpoint("after_analysis")
        details = {
            "parsed_answer": parsed,
            "answer_parse_error": parse_error,
            "normalized_artifact": str(artifact) if artifact else None,
        }
        return CaseOutcome(
            turns=[result],
            artifacts=[str(artifact)] if artifact else [],
            details=details,
        )

    def verify(
        self,
        context: CaseContext,
        fixture: dict[str, Any],
        outcome: CaseOutcome,
        file_delta: FileDelta,
    ) -> Evaluation:
        failures: list[str] = []
        parsed = outcome.details.get("parsed_answer")
        if not outcome.success:
            failures.append("gateway request failed")
        if not isinstance(parsed, dict):
            failures.append(outcome.details.get("answer_parse_error") or "answer is not valid JSON")
            parsed = {}
        truth = fixture["truth"]
        if parsed.get("row_count") != truth["row_count"]:
            failures.append("row_count does not match ground truth")
        if parsed.get("column_count") != truth["column_count"]:
            failures.append("column_count does not match ground truth")
        if parsed.get("risk_level") != truth["expected_risk_level"]:
            failures.append("risk_level does not match ground truth")
        expected_risks = [self._risk_identity(risk) for risk in truth["expected_risks"]]
        observed_risks = [self._risk_identity(risk) for risk in parsed.get("risks", []) if isinstance(risk, dict)]
        risk_metrics = precision_recall(expected_risks, observed_risks)
        if risk_metrics["precision"] != 1.0:
            failures.append("risk precision must be 1.0")
        if risk_metrics["recall"] != 1.0:
            failures.append("risk recall must be 1.0")
        if not self._risk_evidence_matches(truth["expected_risks"], parsed.get("risks", [])):
            failures.append("risk evidence does not match ground truth")
        source_unchanged = hashlib.sha256(fixture["source"].read_bytes()).hexdigest() == fixture["source_sha256"]
        if not source_unchanged:
            failures.append("source CSV was modified")
        chart_verified, chart_failures = self._verify_chart(fixture, outcome, parsed)
        failures.extend(chart_failures)
        return Evaluation.from_failures(
            failures,
            risk_precision=risk_metrics["precision"],
            risk_recall=risk_metrics["recall"],
            false_positive_risks=risk_metrics["false_positive"],
            false_negative_risks=risk_metrics["false_negative"],
            chart_verified=chart_verified,
            source_unchanged=source_unchanged,
            workspace_change_count=file_delta.change_count,
        )

    def _prompt(self, fixture: dict[str, Any]) -> str:
        chart = fixture["chart_request"]
        expects_chart = bool(fixture["truth"]["expected_chart"]["created"])
        validation_instruction = (
            "row_count MUST count data records only and MUST exclude the CSV header row. "
            "If an SVG is created, reopen it before answering and verify that every category "
            "and every aggregated value is visibly present; repair the SVG before answering "
            "if any expected label or value is missing."
        )
        instruction = (
            "生成真实的 SVG 柱状图，并保存到指定输出路径。"
            if expects_chart
            else "尝试生成图表；若字段不存在，必须报告失败且不得伪造图表产物。"
        )
        return (
            "请分析下面的本地 CSV。报告行数、列数、风险等级，并识别完整重复行、缺失率达到30%的列、负值和数值离群点。\n"
            f"CSV_PATH={fixture['source']}\n"
            f"DATASET_ID={fixture['dataset_id']}\n"
            f"X_COLUMN={chart['x_column']}\n"
            f"Y_COLUMN={chart['y_column']}\n"
            f"AGGREGATION={chart['aggregation']}\n"
            f"CHART_OUTPUT_PATH={fixture['requested_chart']}\n"
            f"{validation_instruction}\n"
            f"{instruction}\n"
            "若 data_profile、data_analyze、data_chart 工具可用，必须严格按 data_profile → data_analyze → data_chart 顺序调用，前一步成功后再调用下一步。\n"
            "风险 type 只能使用以下英文枚举：duplicate_row、missing_data、negative_value、numeric_outlier。\n"
            "图表必须按 sum（求和）聚合，同一类别的所有 Y_COLUMN 数值相加；SVG 必须完整显示每个类别名称及对应聚合值。\n"
            "不得修改源CSV。最终只返回一个JSON对象，格式为："
            '{"row_count":整数,"column_count":整数,"risk_level":"low|medium|high",'
            '"risks":[{"type":"duplicate_row|missing_data|negative_value|numeric_outlier","column":"列名或null","values":[数值]}],'
            '"chart":{"created":true或false,"artifact_path":"路径或空字符串",'
            '"x_column":"横轴列","y_column":"纵轴列",'
            '"aggregation":"sum",'
            '"categories":["横轴类别"],"aggregated_values":{"类别":聚合数值},'
            '"error":"失败原因或空字符串"}}。缺失风险还要返回missing_ratio和missing_count；'
            "重复行风险返回duplicate_count，表示除首次出现外的重复记录数。"
        )

    def _normalize_artifact(self, fixture: dict[str, Any]) -> Path | None:
        requested: Path = fixture["requested_chart"]
        router_chart: Path = fixture["router_chart"]
        source = requested if requested.is_file() else router_chart if router_chart.is_file() else None
        if source is None:
            return None
        requested.parent.mkdir(parents=True, exist_ok=True)
        if source != requested:
            shutil.copyfile(source, requested)
            source.unlink()
        return requested

    @staticmethod
    def _parse_answer(answer: str) -> tuple[dict[str, Any] | None, str | None]:
        decoder = json.JSONDecoder()
        for index, character in enumerate(answer):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(answer[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value, None
        return None, "answer does not contain a valid JSON object"

    @staticmethod
    def _risk_identity(risk: dict[str, Any]) -> str:
        column = risk.get("column")
        risk_type = RISK_TYPE_ALIASES.get(str(risk.get("type")), str(risk.get("type")))
        return f"{risk_type}:{column if isinstance(column, str) and column else '*'}"

    @classmethod
    def _risk_evidence_matches(cls, expected: list[dict[str, Any]], observed: Any) -> bool:
        if not isinstance(observed, list):
            return False
        observed_by_identity = {
            cls._risk_identity(risk): risk
            for risk in observed
            if isinstance(risk, dict)
        }
        for labeled in expected:
            actual = observed_by_identity.get(cls._risk_identity(labeled))
            if actual is None:
                return False
            if "expected_values" in labeled:
                expected_values = sorted(float(value) for value in labeled["expected_values"])
                actual_values = actual.get("values")
                if not isinstance(actual_values, list) or sorted(float(value) for value in actual_values) != expected_values:
                    return False
            if "expected_ratio" in labeled and actual.get("missing_ratio") != labeled["expected_ratio"]:
                return False
            if "expected_missing_count" in labeled and actual.get("missing_count") != labeled["expected_missing_count"]:
                return False
            if "expected_count" in labeled:
                duplicate_count = actual.get("duplicate_count")
                occurrence_count = actual.get("occurrence_count")
                if duplicate_count != labeled["expected_count"] and occurrence_count not in {
                    labeled["expected_count"],
                    labeled["expected_count"] + 1,
                }:
                    return False
        return True

    @staticmethod
    def _verify_chart(
        fixture: dict[str, Any],
        outcome: CaseOutcome,
        parsed: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        expected = fixture["truth"]["expected_chart"]
        artifact_text = outcome.details.get("normalized_artifact")
        artifact = Path(artifact_text) if isinstance(artifact_text, str) else None
        chart_answer = parsed.get("chart") if isinstance(parsed.get("chart"), dict) else {}
        if not expected["created"]:
            failures = []
            if artifact is not None and artifact.is_file():
                failures.append("invalid-schema case created an unexpected chart")
            if chart_answer.get("created") is not False:
                failures.append("invalid-schema answer must report chart.created=false")
            if not isinstance(chart_answer.get("error"), str) or not chart_answer["error"].strip():
                failures.append("invalid-schema answer must explain chart failure")
            return not failures, failures
        failures = []
        if artifact is None or not artifact.is_file() or artifact.stat().st_size == 0:
            return False, ["expected SVG chart artifact was not created"]
        try:
            root = ElementTree.fromstring(artifact.read_text(encoding="utf-8"))
        except (ElementTree.ParseError, UnicodeDecodeError):
            return False, ["chart artifact is not valid UTF-8 SVG"]
        if not root.tag.endswith("svg"):
            failures.append("chart artifact root is not svg")
        chart_request = fixture["chart_request"]
        if chart_answer.get("x_column") != chart_request["x_column"]:
            failures.append("chart answer has wrong x_column")
        if chart_answer.get("y_column") != chart_request["y_column"]:
            failures.append("chart answer has wrong y_column")
        if chart_answer.get("aggregation") != chart_request["aggregation"]:
            failures.append("chart answer must use sum aggregation")
        visible_text = " ".join(text.strip() for text in root.itertext() if text.strip())
        for category in expected.get("categories", []):
            if category not in visible_text:
                failures.append(f"chart missing category: {category}")
        categories = chart_answer.get("categories") if isinstance(chart_answer.get("categories"), list) else []
        if len(set(str(category) for category in categories)) != expected["points"]:
            failures.append("chart answer has wrong category count")
        expected_values = expected.get("aggregated_values", {})
        answer_values = chart_answer.get("aggregated_values") if isinstance(chart_answer.get("aggregated_values"), dict) else {}
        for category, value in expected_values.items():
            if answer_values.get(category) != value:
                failures.append(f"chart answer has wrong aggregate for: {category}")
            if f"{value:g}" not in visible_text:
                failures.append(f"chart artifact missing aggregate value for: {category}")
        if chart_answer.get("created") is False:
            failures.append("answer explicitly reports chart.created=false despite a verified artifact")
        return not failures, failures

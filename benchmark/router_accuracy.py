from __future__ import annotations

"""Fixed Router-accuracy evaluation contracts and report orchestration."""

import json
import csv
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import sleep
from typing import Any, Iterable, Iterator, Protocol
from uuid import uuid4

from .ab_runner import ensure_router_workspace_access
from .adapters import BenchmarkRequest, OpenClawGatewayAdapter
from .trace_collector import OpenClawSessionResolver, RouterTrace


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT / "benchmark" / "fixtures" / "router" / "router_cases.json"
RAG_REFERENCE_PATH = ROOT / "benchmark" / "fixtures" / "rag" / "contextflow_reference.md"

TOOL_FAMILIES = {
    "desktop_search_files": "file_search",
    "desktop_plan_archive": "archive",
    "desktop_confirm_archive": "archive",
    "desktop_execute_archive": "archive",
    "desktop_undo_archive": "archive",
    "rag_ingest": "rag",
    "rag_search": "rag",
    "rag_answer": "rag",
    "data_profile": "data_analysis",
    "data_analyze": "data_analysis",
    "data_chart": "data_analysis",
    "office_process_meeting": "meeting",
    "workflow_prepare_meeting": "meeting",
    "workflow_plan_delivery": "delivery",
    "delivery_replan": "delivery",
    "context_get_state": "state",
    "context_update_state": "state",
}


@dataclass(frozen=True)
class RouterCase:
    case_id: str
    category: str
    prompt: str
    expected_route: str
    expected_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    accepted_tool_sequences: tuple[tuple[str, ...], ...] = ()
    expected_tool_family: str | None = None

    @property
    def accepted_sequences(self) -> tuple[tuple[str, ...], ...]:
        if self.expected_route == "upstream":
            return ((),)
        return self.accepted_tool_sequences or (self.expected_tools,)

    @property
    def tool_family(self) -> str | None:
        if self.expected_tool_family:
            return self.expected_tool_family
        return TOOL_FAMILIES.get(self.expected_tools[0]) if self.expected_tools else None


class TraceCollector(Protocol):
    def collect(self, session_key: str) -> RouterTrace: ...


def load_router_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[RouterCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("router fixture must contain a cases array")
    cases: list[RouterCase] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each router case must be an object")
        route = row.get("expected_route")
        tools = row.get("expected_tools")
        forbidden = row.get("forbidden_tools", [])
        accepted = row.get("accepted_tool_sequences", [])
        expected_family = row.get("expected_tool_family")
        if route not in {"tool", "upstream"}:
            raise ValueError(f"invalid expected_route for {row.get('case_id')}")
        if not isinstance(tools, list) or not all(isinstance(item, str) and item for item in tools):
            raise ValueError(f"expected_tools must be a string array for {row.get('case_id')}")
        if not isinstance(forbidden, list) or not all(isinstance(item, str) and item for item in forbidden):
            raise ValueError(f"forbidden_tools must be a string array for {row.get('case_id')}")
        if not isinstance(accepted, list) or not all(
            isinstance(sequence, list) and all(isinstance(item, str) and item for item in sequence)
            for sequence in accepted
        ):
            raise ValueError(f"accepted_tool_sequences must be an array of string arrays for {row.get('case_id')}")
        if expected_family is not None and (not isinstance(expected_family, str) or not expected_family):
            raise ValueError(f"expected_tool_family must be a non-empty string for {row.get('case_id')}")
        cases.append(RouterCase(
            case_id=str(row.get("case_id", "")),
            category=str(row.get("category", "")),
            prompt=str(row.get("prompt", "")),
            expected_route=route,
            expected_tools=tuple(tools),
            forbidden_tools=tuple(forbidden),
            accepted_tool_sequences=tuple(tuple(sequence) for sequence in accepted),
            expected_tool_family=expected_family,
        ))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("router case ids must be unique")
    return tuple(cases)


class RouterAccuracyRunner:
    """Run and score ContextFlow Router decisions behind one small interface."""

    def __init__(
        self,
        adapter: OpenClawGatewayAdapter,
        trace_collector: TraceCollector,
        *,
        trace_session_resolver: OpenClawSessionResolver | None = None,
        temporary_root: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.adapter = adapter
        self.trace_collector = trace_collector
        self.trace_session_resolver = trace_session_resolver
        self.temporary_root = temporary_root
        self.metadata = dict(metadata or {})

    def run(self, cases: Iterable[RouterCase], *, runs: int = 3) -> dict[str, Any]:
        selected = tuple(cases)
        if runs < 1:
            raise ValueError("runs must be at least 1")
        if not selected:
            raise ValueError("at least one Router case is required")
        records = [
            self._run_one(case, attempt)
            for case in selected
            for attempt in range(1, runs + 1)
        ]
        return {
            "benchmark": "ContextFlowRouterAccuracy",
            "version": "0.2",
            "generated_at": datetime.now(UTC).isoformat(),
            "requested_runs": runs,
            "case_count": len(selected),
            "expected_attempts": len(selected) * runs,
            "metadata": self.metadata,
            "summary": {
                "overall": self._metrics(records),
                "categories": {
                    category: self._metrics([record for record in records if record["category"] == category])
                    for category in sorted({record["category"] for record in records})
                },
                "cases": {
                    case.case_id: self._metrics([record for record in records if record["case_id"] == case.case_id])
                    for case in selected
                },
                "all_runs_passed": all(record["route_verified"] for record in records),
            },
            "records": records,
        }

    def _run_one(self, case: RouterCase, attempt: int) -> dict[str, Any]:
        prefix = f"router-accuracy-{case.case_id}-{attempt}-"
        with self._workspace(prefix) as workspace:
            fixture = self._prepare_workspace(workspace, case, attempt)
            ensure_router_workspace_access(workspace)
            session_key = f"benchmark:router_accuracy:{case.case_id}:{attempt}:{uuid4().hex[:8]}"
            preparation_latency_ms = 0.0
            knowledge_prepared = True
            baseline = RouterTrace(session_key=session_key)
            trace_key: str | None = None
            if case.category == "rag":
                preparation = self.adapter.run(BenchmarkRequest(
                    case_id=f"{case.case_id}:prepare",
                    prompt=(
                        f'Import SOURCE_PATH="{fixture["rag_path"]}" into the current session knowledge '
                        "with DOCUMENT_ID=router-reference. Use rag_ingest only, then briefly confirm completion."
                    ),
                    session_key=session_key,
                    system_prompt="Prepare session-local knowledge only. Do not answer benchmark questions yet.",
                ))
                preparation_latency_ms = preparation.latency_ms
                trace_key = self._resolve_trace_key(session_key)
                baseline = self.trace_collector.collect(trace_key or session_key)
                knowledge_prepared = (
                    preparation.success
                    and baseline.found
                    and baseline.tool_names == ("rag_ingest",)
                    and baseline.tool_error_count == 0
                )
            result = self.adapter.run(BenchmarkRequest(
                case_id=case.case_id,
                prompt=case.prompt.format(**fixture),
                session_key=session_key,
                system_prompt=(
                    "Follow the user's request. Use ContextFlow tools only when the request requires them. "
                    "For ordinary explanatory conversation, answer directly without calling a tool."
                ),
            ))
            full_trace = self._trace(session_key, trace_key=trace_key)
            trace_isolated = case.category != "rag" or (knowledge_prepared and baseline.found)
            trace = self._subtract_trace(full_trace, baseline) if case.category == "rag" else full_trace
            observed = tuple(trace.tool_names)
            first_tool_correct = bool(case.expected_tools) and bool(observed) and observed[0] == case.expected_tools[0]
            exact_sequence = case.expected_route == "tool" and observed == case.expected_tools
            acceptable_sequence = case.expected_route == "tool" and observed in case.accepted_sequences
            answer_nonempty = bool(result.final_answer.strip())
            upstream_correct = case.expected_route == "upstream" and trace.found and not observed and result.success and answer_nonempty
            forbidden_hit = bool(observed) if "*" in case.forbidden_tools else any(
                tool in case.forbidden_tools for tool in observed
            )
            allowed_tools = {tool for sequence in case.accepted_sequences for tool in sequence}
            unexpected_tool = any(tool not in allowed_tools for tool in observed)
            wrong_tool = forbidden_hit or unexpected_tool
            family_correct = (
                case.expected_route == "tool"
                and bool(observed)
                and bool(case.tool_family)
                and TOOL_FAMILIES.get(observed[0]) == case.tool_family
            )
            redundant = max(0, len(observed) - len(case.expected_tools))
            route_verified = (
                result.success
                and trace.found
                and trace_isolated
                and knowledge_prepared
                and (acceptable_sequence or upstream_correct)
            )
            return {
                "case_id": case.case_id,
                "category": case.category,
                "attempt": attempt,
                "session_key": session_key,
                "expected_route": case.expected_route,
                "expected_tools": list(case.expected_tools),
                "forbidden_tools": list(case.forbidden_tools),
                "accepted_tool_sequences": [list(sequence) for sequence in case.accepted_sequences],
                "expected_tool_family": case.tool_family,
                "observed_tools": list(observed),
                "success": result.success,
                "trace_found": trace.found,
                "route_verified": route_verified,
                "first_tool_correct": first_tool_correct,
                "exact_tool_sequence_correct": exact_sequence,
                "acceptable_tool_sequence_correct": acceptable_sequence,
                "tool_family_correct": family_correct,
                "upstream_route_correct": upstream_correct,
                "wrong_tool_invocation": wrong_tool,
                "unnecessary_tool_invocation": case.expected_route == "upstream" and bool(observed),
                "multi_call": len(observed) > 1,
                "expected_multi_step": len(case.expected_tools) > 1,
                "redundant_call": redundant > 0,
                "redundant_call_count": redundant,
                "tool_call_count": len(observed),
                "tool_error_count": trace.tool_error_count,
                "invalid_argument_count": getattr(trace, "invalid_argument_count", 0),
                "fallback_count": trace.fallback_count,
                "latency_ms": result.latency_ms,
                "preparation_latency_ms": preparation_latency_ms,
                "knowledge_prepared": knowledge_prepared,
                "trace_isolated": trace_isolated,
                "usage": dict(trace.usage),
                "answer_nonempty": answer_nonempty,
                "error": result.error or trace.error,
            }

    def _resolve_trace_key(self, session_key: str) -> str | None:
        trace_key: str | None = None
        if self.trace_session_resolver:
            for attempt in range(4):
                trace_key = self.trace_session_resolver.resolve(session_key)
                if trace_key:
                    break
                if attempt < 3:
                    sleep(0.1)
        return trace_key

    def _trace(self, session_key: str, *, trace_key: str | None = None) -> RouterTrace:
        trace_key = trace_key or self._resolve_trace_key(session_key)
        return self.trace_collector.collect(trace_key or session_key)

    @staticmethod
    def _subtract_trace(full: RouterTrace, baseline: RouterTrace) -> RouterTrace:
        """Remove preparation turns from cumulative per-session Router history."""
        prefix_length = len(baseline.tool_names)
        prefix_matches = full.tool_names[:prefix_length] == baseline.tool_names
        tool_names = full.tool_names[prefix_length:] if prefix_matches else full.tool_names
        usage = {
            key: max(0, float(full.usage.get(key, 0)) - float(baseline.usage.get(key, 0)))
            for key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        }
        return RouterTrace(
            session_key=full.session_key,
            found=full.found,
            entry_count=max(0, full.entry_count - baseline.entry_count),
            tool_names=tool_names,
            tool_rounds=max(0, full.tool_rounds - baseline.tool_rounds),
            llm_call_count=max(0, full.llm_call_count - baseline.llm_call_count),
            tool_result_count=max(0, full.tool_result_count - baseline.tool_result_count),
            tool_error_count=max(0, full.tool_error_count - baseline.tool_error_count),
            invalid_argument_count=max(0, full.invalid_argument_count - baseline.invalid_argument_count),
            fallback_count=max(0, full.fallback_count - baseline.fallback_count),
            usage=usage,
            error=full.error,
            metadata={**full.metadata, "excluded_preparation_entries": baseline.entry_count},
        )

    @contextmanager
    def _workspace(self, prefix: str) -> Iterator[Path]:
        root = (self.temporary_root or Path(tempfile.gettempdir())).resolve()
        root.mkdir(parents=True, exist_ok=True)
        workspace = root / f"{prefix}{uuid4().hex[:8]}"
        workspace.mkdir()
        try:
            yield workspace
        finally:
            resolved = workspace.resolve()
            if resolved.parent != root or not resolved.name.startswith(prefix):
                raise RuntimeError(f"refusing to clean unexpected Router workspace: {resolved}")
            shutil.rmtree(resolved, ignore_errors=False)

    @staticmethod
    def _prepare_workspace(workspace: Path, case: RouterCase, attempt: int) -> dict[str, str]:
        (workspace / "meeting-notes.txt").write_text("项目周会 notes", encoding="utf-8")
        (workspace / "project-report.md").write_text("项目报告", encoding="utf-8")
        (workspace / "old-report-2025.md").write_text("旧报告", encoding="utf-8")
        (workspace / "application.log").write_text("INFO router test", encoding="utf-8")
        csv_path = workspace / "budget-risk.csv"
        csv_path.write_text(
            "workstream,owner,remaining_hours,risk_score\nAgentCore,张三,4,20\nLocalRAG,,3,25\nLocalRAG,,3,25\nDataInsight,李四,-2,100\n",
            encoding="utf-8",
        )
        rag_path = workspace / RAG_REFERENCE_PATH.name
        shutil.copyfile(RAG_REFERENCE_PATH, rag_path)
        return {
            "workspace": str(workspace),
            "csv_path": str(csv_path),
            "dataset_id": f"router-{case.case_id}-{attempt}-{uuid4().hex[:6]}",
            "rag_path": str(rag_path),
        }

    @staticmethod
    def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(records)
        tool_records = [record for record in records if record["expected_route"] == "tool"]
        upstream_records = [record for record in records if record["expected_route"] == "upstream"]
        expected_multi = [record for record in records if record["expected_multi_step"]]
        rag_records = [record for record in records if record["category"] == "rag"]
        total_tool_calls = sum(record["tool_call_count"] for record in records)

        def rate(items: list[dict[str, Any]], field: str) -> float | None:
            return round(sum(bool(item[field]) for item in items) / len(items), 4) if items else None

        return {
            "attempts": total,
            "passed_attempts": sum(bool(record["route_verified"]) for record in records),
            "route_accuracy": rate(records, "route_verified"),
            "first_tool_hit_rate": rate(tool_records, "first_tool_correct"),
            "exact_tool_sequence_rate": rate(tool_records, "exact_tool_sequence_correct"),
            "acceptable_tool_sequence_rate": rate(tool_records, "acceptable_tool_sequence_correct"),
            "tool_family_hit_rate": rate(tool_records, "tool_family_correct"),
            "upstream_route_hit_rate": rate(upstream_records, "upstream_route_correct"),
            "wrong_tool_invocation_rate": rate(records, "wrong_tool_invocation"),
            "unnecessary_tool_invocation_rate": rate(upstream_records, "unnecessary_tool_invocation"),
            "multi_call_rate": rate(records, "multi_call"),
            "expected_multi_step_success_rate": rate(expected_multi, "exact_tool_sequence_correct"),
            "redundant_call_rate": rate(records, "redundant_call"),
            "fallback_rate": round(sum(record["fallback_count"] > 0 for record in records) / total, 4),
            "tool_error_rate": round(sum(record["tool_error_count"] for record in records) / total_tool_calls, 4) if total_tool_calls else 0.0,
            "invalid_argument_rate": round(sum(record["invalid_argument_count"] for record in records) / total_tool_calls, 4) if total_tool_calls else 0.0,
            "average_latency_ms": round(mean(record["latency_ms"] for record in records), 3),
            "average_tool_calls": round(mean(record["tool_call_count"] for record in records), 3),
            "average_total_tokens": round(mean(float(record["usage"].get("total_tokens", 0)) for record in records), 3),
            "trace_coverage": round(sum(bool(record["trace_found"]) for record in records) / total, 4),
            "trace_isolation_rate": rate(records, "trace_isolated"),
            "knowledge_preparation_success_rate": rate(rag_records, "knowledge_prepared"),
            "average_preparation_latency_ms": (
                round(mean(record["preparation_latency_ms"] for record in rag_records), 3)
                if rag_records else None
            ),
        }


def write_router_accuracy_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"router-accuracy-{stamp}.json"
    markdown_path = output_dir / f"router-accuracy-{stamp}.md"
    csv_path = output_dir / f"router-accuracy-details-{stamp}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    overall = report["summary"]["overall"]
    lines = [
        "# ContextFlow Router 路由准确率",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        f"固定请求：{report['case_count']} 条；每条运行：{report['requested_runs']} 次；总尝试：{overall['attempts']} 次。",
        "",
        "## 总体指标",
        "",
        "| 路由准确率 | 工具族命中率 | 首工具命中率 | 可接受序列命中率 | 首选精确序列命中率 | 上游路径命中率 | 错误工具调用率 | 冗余调用率 | 无效参数率 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| " + " | ".join(_percent(overall[key]) for key in (
            "route_accuracy", "tool_family_hit_rate", "first_tool_hit_rate",
            "acceptable_tool_sequence_rate", "exact_tool_sequence_rate",
            "upstream_route_hit_rate", "wrong_tool_invocation_rate",
            "redundant_call_rate", "invalid_argument_rate",
        )) + " |",
        "",
        "## 分类指标",
        "",
        "| 分类 | 尝试数 | 路由准确率 | 工具族命中率 | 可接受序列命中率 | 首选精确序列命中率 | 上游命中率 | 工具错误率 | 平均正式耗时 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for category, metrics in report["summary"]["categories"].items():
        lines.append(
            f"| {category} | {metrics['attempts']} | {_percent(metrics['route_accuracy'])} | "
            f"{_percent(metrics['tool_family_hit_rate'])} | {_percent(metrics['acceptable_tool_sequence_rate'])} | "
            f"{_percent(metrics['exact_tool_sequence_rate'])} | {_percent(metrics['upstream_route_hit_rate'])} | "
            f"{_percent(metrics['tool_error_rate'])} | {metrics['average_latency_ms']:.1f} ms |"
        )
    lines += [
        "",
        "## 每个 Case 的稳定性",
        "",
        "| Case | 分类 | 尝试数 | 通过数 | 路由准确率 | 工具族命中率 | 可接受序列命中率 | 首选精确序列命中率 | 平均正式耗时 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    categories_by_case = {record["case_id"]: record["category"] for record in report["records"]}
    for case_id, metrics in report["summary"]["cases"].items():
        lines.append(
            f"| {case_id} | {categories_by_case[case_id]} | {metrics['attempts']} | {metrics['passed_attempts']} | "
            f"{_percent(metrics['route_accuracy'])} | {_percent(metrics['tool_family_hit_rate'])} | "
            f"{_percent(metrics['acceptable_tool_sequence_rate'])} | {_percent(metrics['exact_tool_sequence_rate'])} | "
            f"{metrics['average_latency_ms']:.1f} ms |"
        )
    lines += [
        "",
        "## 评测口径",
        "",
        "- 路由准确率采用可接受工具序列；首选精确序列单独衡量最短、最理想路径。",
        "- 工具族命中率判断首个工具是否落入正确能力域，不把同族可接受组合误算成错路由。",
        "- RAG 资料在正式问题前自动导入同一隔离会话；准备耗时和 rag_ingest 轨迹不计入正式指标。",
        "- 上游路径以没有工具调用、Gateway 成功返回回答为准。",
        "- 冗余调用表示实际工具数超过该 Case 的预期工具数；预期多步骤调用不计为冗余。",
        "- 无效参数只保存分类计数，不保存工具参数或工具结果正文。",
        "- 每次尝试使用独立 session key 和一次性工作目录。",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    columns = [
        "case_id", "category", "attempt", "expected_route", "expected_tools", "accepted_tool_sequences",
        "expected_tool_family", "observed_tools", "route_verified", "tool_family_correct", "first_tool_correct",
        "acceptable_tool_sequence_correct", "exact_tool_sequence_correct", "upstream_route_correct",
        "wrong_tool_invocation", "unnecessary_tool_invocation", "multi_call", "expected_multi_step",
        "redundant_call", "tool_call_count", "tool_error_count", "invalid_argument_count",
        "fallback_count", "latency_ms", "preparation_latency_ms", "knowledge_prepared", "trace_isolated",
        "trace_found", "success", "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in report["records"]:
            row = dict(record)
            row["expected_tools"] = " -> ".join(record["expected_tools"])
            row["accepted_tool_sequences"] = " || ".join(
                " -> ".join(sequence) for sequence in record["accepted_tool_sequences"]
            )
            row["observed_tools"] = " -> ".join(record["observed_tools"])
            writer.writerow(row)
    return json_path, markdown_path, csv_path


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value):.0%}"

from __future__ import annotations

"""Unified, isolated AB/BA runner for online OpenClaw benchmark scenarios."""

import argparse
import json
import os
import subprocess
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import sleep
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4

from .ab_cases import ABCase, CaseContext, CaseOutcome
from .adapters import ContextFlowOpenClawAdapter, GatewayConfig, NativeOpenClawAdapter, OpenClawGatewayAdapter, RunResult
from .cases.registry import case_choices, select_cases
from .evaluators import Evaluation, PhaseRecorder
from .trace_collector import OpenClawSessionResolver, RouterTrace, RouterTraceCollector

ROOT = Path(__file__).resolve().parents[1]


def ensure_router_workspace_access(workspace: Path) -> None:
    """Make a disposable benchmark workspace accessible to sibling processes.

    Codex and other Windows sandboxes may stamp a new directory with a
    per-process SID, preventing the already-running Router from seeing fixture
    files.  The workspace contains benchmark-only data and is deleted after the
    run, so grant the well-known Everyone SID modify access recursively.
    """

    if os.name != "nt":
        workspace.chmod(workspace.stat().st_mode | 0o770)
        return
    completed = subprocess.run(
        [
            "icacls", str(workspace),
            "/grant", "*S-1-1-0:(OI)(CI)M",
            "/T", "/C", "/Q",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"failed to share benchmark workspace with Router: {completed.stderr.strip()}")


def load_gateway_adapters(config_path: Path) -> tuple[dict[str, OpenClawGatewayAdapter], dict[str, Any]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    def gateway_config(name: str) -> GatewayConfig:
        value = raw.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"config must contain object {name}")
        base_url = value.get("base_url")
        model = value.get("model")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(f"missing config field {name}.base_url")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"missing config field {name}.model")
        api_key_env = value.get("api_key_env")
        return GatewayConfig(
            base_url=base_url.strip(),
            model=model.strip(),
            api_key_env=api_key_env.strip() if isinstance(api_key_env, str) and api_key_env.strip() else None,
            timeout_seconds=float(value.get("timeout_seconds", 180)),
        )

    adapters: dict[str, OpenClawGatewayAdapter] = {
        "native_openclaw": NativeOpenClawAdapter(gateway_config("native_openclaw")),
        "contextflow_openclaw": ContextFlowOpenClawAdapter(gateway_config("contextflow_openclaw")),
    }
    metadata = {
        "models": {name: adapter.config.model for name, adapter in adapters.items()},
        "router_trace_url": raw.get("router_trace_url", "http://127.0.0.1:18790/v1/tool_history"),
    }
    return adapters, metadata


class ABRunner:
    """Own ordering, isolation, trace collection and report normalization."""

    def __init__(
        self,
        adapters: Mapping[str, OpenClawGatewayAdapter],
        *,
        trace_collector: RouterTraceCollector | None = None,
        trace_session_resolver: OpenClawSessionResolver | None = None,
        metadata: Mapping[str, Any] | None = None,
        temporary_root: Path | None = None,
    ) -> None:
        required = {"native_openclaw", "contextflow_openclaw"}
        if set(adapters) != required:
            raise ValueError(f"adapters must be exactly {sorted(required)}")
        self.adapters = dict(adapters)
        self.trace_collector = trace_collector
        self.trace_session_resolver = trace_session_resolver
        self.metadata = dict(metadata or {})
        self.temporary_root = temporary_root

    def run(self, cases: Iterable[ABCase], *, runs: int = 3) -> dict[str, Any]:
        if runs < 1:
            raise ValueError("runs must be at least 1")
        selected = tuple(cases)
        if not selected:
            raise ValueError("at least one A/B case is required")
        records: list[dict[str, Any]] = []
        for case in selected:
            for attempt in range(1, runs + 1):
                order = self._order(attempt)
                order_label = "AB" if order[0] == "native_openclaw" else "BA"
                for position, adapter_name in enumerate(order, start=1):
                    records.append(self._run_one(case, attempt, adapter_name, order_label, position))
        return self._report(records, runs)

    def _run_one(self, case: ABCase, attempt: int, adapter_name: str, order: str, order_position: int) -> dict[str, Any]:
        prefix = f"contextflow-ab-{case.case_id}-{adapter_name}-"
        with self._workspace(prefix) as workspace:
            phases = PhaseRecorder(workspace)
            context = CaseContext(
                case_id=case.case_id,
                scene=case.scene,
                attempt=attempt,
                adapter_name=adapter_name,
                order=order,
                order_position=order_position,
                session_key=f"benchmark:{case.case_id}:{adapter_name}:{attempt}:{uuid4().hex[:8]}",
                workspace=workspace,
                phases=phases,
            )
            outcome = CaseOutcome(turns=[])
            evaluation = Evaluation(passed=False, failures=("case did not complete",))
            internal_error: str | None = None
            try:
                fixture = case.prepare(context)
                ensure_router_workspace_access(workspace)
                phases.start()
                outcome = case.execute(self.adapters[adapter_name], context, fixture)
                final_phase = phases.finalize()
                evaluation = case.verify(context, fixture, outcome, final_phase.from_start)
            except Exception as error:  # A failed case is report data, not a lost benchmark run.
                internal_error = f"{type(error).__name__}: {error}"
                evaluation = Evaluation(passed=False, failures=(f"runtime error: {internal_error}",))
                if not phases.started:
                    phases.start()
                final_phase = phases.finalize()
            trace = self._trace(context.session_key, adapter_name)
            evaluation = self._verify_trace_contract(case, adapter_name, trace, evaluation)
            return self._record(
                context,
                outcome,
                evaluation,
                final_phase.from_start.to_dict(),
                phases.to_dict(),
                phases.snapshots_to_dict(),
                trace,
                internal_error,
            )

    @contextmanager
    def _workspace(self, prefix: str) -> Iterator[Path]:
        """Create an isolated directory whose ACL is inherited by external Router processes."""

        root = (self.temporary_root or Path(tempfile.gettempdir())).resolve()
        root.mkdir(parents=True, exist_ok=True)
        workspace = root / f"{prefix}{uuid4().hex[:8]}"
        workspace.mkdir()
        try:
            yield workspace
        finally:
            resolved = workspace.resolve()
            if resolved.parent != root or not resolved.name.startswith(prefix):
                raise RuntimeError(f"refusing to clean unexpected benchmark workspace: {resolved}")
            shutil.rmtree(resolved, ignore_errors=False)

    def _trace(self, session_key: str, adapter_name: str) -> RouterTrace:
        if adapter_name != "contextflow_openclaw" or self.trace_collector is None:
            return RouterTrace(session_key=session_key)
        trace_session_key: str | None = None
        if self.trace_session_resolver:
            for attempt in range(4):
                trace_session_key = self.trace_session_resolver.resolve(session_key)
                if trace_session_key:
                    break
                if attempt < 3:
                    sleep(0.1)
        return self.trace_collector.collect(trace_session_key or session_key)

    @staticmethod
    def _verify_trace_contract(
        case: ABCase,
        adapter_name: str,
        trace: RouterTrace,
        evaluation: Evaluation,
    ) -> Evaluation:
        expected = getattr(case, "expected_contextflow_tools", ())
        if adapter_name != "contextflow_openclaw" or not expected:
            return evaluation
        failures = list(evaluation.failures)
        expected_tools = tuple(expected)
        if not trace.found:
            failures.append("ContextFlow Router trace is unavailable")
        elif trace.tool_names != expected_tools:
            failures.append(
                "ContextFlow tool sequence mismatch: "
                f"expected {list(expected_tools)}, observed {list(trace.tool_names)}"
            )
        return Evaluation.from_failures(
            failures,
            **evaluation.metrics,
            expected_contextflow_tools=list(expected_tools),
            observed_contextflow_tools=list(trace.tool_names),
            contextflow_tool_sequence_verified=trace.found and trace.tool_names == expected_tools,
        )

    @staticmethod
    def _record(
        context: CaseContext,
        outcome: CaseOutcome,
        evaluation: Evaluation,
        file_delta: dict[str, Any],
        phase_deltas: dict[str, Any],
        phase_snapshots: dict[str, Any],
        trace: RouterTrace,
        internal_error: str | None,
    ) -> dict[str, Any]:
        response_calls = [
            call
            for turn in outcome.turns
            for call in turn.tool_calls
            if isinstance(call, dict)
        ]
        router_calls = list(trace.tool_names)
        response_usage = ABRunner._sum_usage(turn.usage for turn in outcome.turns)
        router_usage = dict(trace.usage)
        usage = ABRunner._sum_usage((response_usage, router_usage))
        preparation_latency = ABRunner._numeric_detail(outcome.details, "preparation_latency_ms", 0.0)
        question_latency = ABRunner._numeric_detail(outcome.details, "question_latency_ms", outcome.latency_ms)
        turn_errors = [turn.error for turn in outcome.turns if turn.error]
        fallback_count = sum(turn.fallback_count for turn in outcome.turns) + trace.fallback_count
        verified = evaluation.passed and outcome.success
        return {
            "case_id": context.case_id,
            "scene": context.scene,
            "attempt": context.attempt,
            "adapter": context.adapter_name,
            "order": context.order,
            "order_position": context.order_position,
            "session_key": context.session_key,
            "success": outcome.success,
            "outcome_verified": verified,
            "latency_ms": outcome.latency_ms,
            "latency_breakdown_ms": {
                "preparation": preparation_latency,
                "question": question_latency,
                "end_to_end": outcome.latency_ms,
            },
            "turn_count": len(outcome.turns),
            "response_tool_call_count": len(response_calls),
            "router_trace_found": trace.found,
            "router_trace_entries": trace.entry_count,
            "router_tool_calls": router_calls,
            "tool_calls": len(router_calls) if trace.found else len(response_calls),
            "tool_rounds": trace.tool_rounds,
            "llm_call_count": trace.llm_call_count,
            "tool_error_count": trace.tool_error_count,
            "fallback_count": fallback_count,
            "usage": usage,
            "response_usage": response_usage,
            "router_usage": router_usage,
            "artifacts": list(outcome.artifacts),
            "file_delta": file_delta,
            "phase_deltas": phase_deltas,
            "phase_snapshots": phase_snapshots,
            "metrics": evaluation.metrics,
            "failures": list(evaluation.failures),
            "answer": outcome.final_answer,
            "error": internal_error or ("; ".join(turn_errors) if turn_errors else trace.error),
        }

    @staticmethod
    def _sum_usage(items: Iterable[dict[str, Any]]) -> dict[str, int | float]:
        total: dict[str, int | float] = {}
        for item in items:
            for key, value in item.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total[key] = total.get(key, 0) + value
        return total

    @staticmethod
    def _numeric_detail(details: Mapping[str, Any], key: str, default: float) -> int | float:
        value = details.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    @staticmethod
    def _order(attempt: int) -> tuple[str, str]:
        return ("native_openclaw", "contextflow_openclaw") if attempt % 2 else ("contextflow_openclaw", "native_openclaw")

    def _report(self, records: list[dict[str, Any]], requested_runs: int) -> dict[str, Any]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault((record["case_id"], record["adapter"]), []).append(record)
        summaries = []
        for (case_id, adapter), items in grouped.items():
            latencies = sorted(float(item["latency_ms"]) for item in items)
            preparation_latencies = [float(item["latency_breakdown_ms"]["preparation"]) for item in items]
            question_latencies = [float(item["latency_breakdown_ms"]["question"]) for item in items]
            summaries.append({
                "case_id": case_id,
                "scene": items[0]["scene"],
                "adapter": adapter,
                "runs": len(items),
                "pass_rate": round(sum(bool(item["outcome_verified"]) for item in items) / len(items), 4),
                "latency_mean_ms": round(mean(latencies), 3),
                "latency_p50_ms": round(median(latencies), 3),
                "latency_p95_ms": round(self._percentile(latencies, 0.95), 3),
                "end_to_end_latency_mean_ms": round(mean(latencies), 3),
                "preparation_latency_mean_ms": round(mean(preparation_latencies), 3),
                "question_latency_mean_ms": round(mean(question_latencies), 3),
                "average_tool_calls": round(mean(float(item["tool_calls"]) for item in items), 3),
                "average_total_tokens": round(mean(float(self._total_tokens(item["usage"])) for item in items), 3),
                "trace_coverage": round(sum(bool(item["router_trace_found"]) for item in items) / len(items), 4) if adapter == "contextflow_openclaw" else None,
                "fallback_rate": round(sum(int(item["fallback_count"]) > 0 for item in items) / len(items), 4),
                "error_rate": round(sum(bool(item["error"]) for item in items) / len(items), 4),
                "metrics": self._average_metrics(items),
            })
        native_records = [record for record in records if record["adapter"] == "native_openclaw"]
        contextflow_records = [record for record in records if record["adapter"] == "contextflow_openclaw"]
        native_all_runs_passed = bool(native_records) and all(record["outcome_verified"] for record in native_records)
        contextflow_all_runs_passed = bool(contextflow_records) and all(record["outcome_verified"] for record in contextflow_records)
        return {
            "benchmark": "ContextFlowUnifiedAB",
            "version": "0.1",
            "generated_at": datetime.now(UTC).isoformat(),
            "requested_runs": requested_runs,
            "ordering": "AB on odd attempts; BA on even attempts",
            "metadata": self.metadata,
            "summary": {
                "cases": summaries,
                "native_all_runs_passed": native_all_runs_passed,
                "contextflow_all_runs_passed": contextflow_all_runs_passed,
                "all_runs_passed": native_all_runs_passed and contextflow_all_runs_passed,
            },
            "records": records,
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        rank = max(0, min(len(values) - 1, int((len(values) * percentile) + 0.999999) - 1))
        return values[rank]

    @staticmethod
    def _total_tokens(usage: Mapping[str, Any]) -> int | float:
        if isinstance(usage.get("total_tokens"), (int, float)):
            return usage["total_tokens"]
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        return (input_tokens if isinstance(input_tokens, (int, float)) else 0) + (output_tokens if isinstance(output_tokens, (int, float)) else 0)

    @staticmethod
    def _average_metrics(items: list[dict[str, Any]]) -> dict[str, float]:
        keys = sorted({
            key
            for item in items
            for key, value in item.get("metrics", {}).items()
            if isinstance(value, (int, float, bool))
        })
        return {
            key: round(mean(float(item.get("metrics", {}).get(key, 0)) for item in items), 4)
            for key in keys
        }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"unified-ab-{stamp}.json"
    markdown_path = output_dir / f"unified-ab-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# ContextFlow 统一 A/B Benchmark",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 总体判定",
        "",
        f"- ContextFlow 全部运行通过：{report['summary']['contextflow_all_runs_passed']}",
        f"- Native 全部运行通过：{report['summary']['native_all_runs_passed']}",
        f"- 完整 A/B 全部运行通过：{report['summary']['all_runs_passed']}",
        "",
        "| 场景 | 方案 | 通过率 | 端到端均值 | 知识准备均值 | 纯问答均值 | P50 | P95 | 平均工具调用 | 平均 Token | 轨迹覆盖 | 兜底率 | 错误率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["summary"]["cases"]:
        lines.append(
            f"| {item['case_id']} | {item['adapter']} | {item['pass_rate']:.0%} | "
            f"{item['end_to_end_latency_mean_ms']:.1f} ms | "
            f"{item['preparation_latency_mean_ms']:.1f} ms | {item['question_latency_mean_ms']:.1f} ms | "
            f"{item['latency_p50_ms']:.1f} ms | "
            f"{item['latency_p95_ms']:.1f} ms | {item['average_tool_calls']:.1f} | "
            f"{item['average_total_tokens']:.1f} | "
            f"{'—' if item['trace_coverage'] is None else format(item['trace_coverage'], '.0%')} | "
            f"{item['fallback_rate']:.0%} | {item['error_rate']:.0%} |"
        )
    lines += [
        "",
        "## 场景结果指标",
        "",
        "| 场景 | 方案 | 风险精确率 | 风险召回率 | 图表验证率 | 源文件完整率 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in report["summary"]["cases"]:
        metrics = item.get("metrics", {})
        if not any(key in metrics for key in ("risk_precision", "risk_recall", "chart_verified", "source_unchanged")):
            continue
        lines.append(
            f"| {item['case_id']} | {item['adapter']} | "
            f"{metrics.get('risk_precision', 0):.0%} | {metrics.get('risk_recall', 0):.0%} | "
            f"{metrics.get('chart_verified', 0):.0%} | {metrics.get('source_unchanged', 0):.0%} |"
        )
    lines += [
        "",
        "## 归档安全指标",
        "",
        "| 场景 | 方案 | 计划阶段无变更 | 拒绝阶段无变更 | 归档哈希保持 | 无关文件不变 | 撤销 SHA256 恢复 | 工具顺序正确 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["summary"]["cases"]:
        metrics = item.get("metrics", {})
        if not any(key in metrics for key in (
            "after_plan_change_count", "after_rejection_change_count",
            "archive_hashes_preserved", "undo_sha256_verified",
        )):
            continue
        lines.append(
            f"| {item['case_id']} | {item['adapter']} | "
            f"{'yes' if metrics.get('after_plan_change_count', 1) == 0 else 'no'} | "
            f"{('yes' if metrics['after_rejection_change_count'] == 0 else 'no') if 'after_rejection_change_count' in metrics else '—'} | "
            f"{format(metrics['archive_hashes_preserved'], '.0%') if 'archive_hashes_preserved' in metrics else '—'} | "
            f"{format(metrics['unrelated_files_unchanged'], '.0%') if 'unrelated_files_unchanged' in metrics else '—'} | "
            f"{format(metrics['undo_sha256_verified'], '.0%') if 'undo_sha256_verified' in metrics else '—'} | "
            f"{format(metrics['contextflow_tool_sequence_verified'], '.0%') if 'contextflow_tool_sequence_verified' in metrics else '—'} |"
        )
    lines += [
        "",
        "## 评测口径",
        "",
        "- 奇数轮按 Native→ContextFlow，偶数轮按 ContextFlow→Native 执行。",
        "- 每个方案、每轮使用独立 session key 与临时工作目录。",
        "- 工具调用优先采用 Router 的 session 轨迹；未采集到轨迹时才使用最终响应中的工具调用。",
        "- P95 使用最近秩法；少量冒烟样本的 P95 仅用于验证报表，不用于性能结论。",
        "- 文件变更通过相对路径、大小和 SHA256 快照判定。",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated AB/BA OpenClaw benchmark cases.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--case", choices=case_choices(), default="file_search")
    parser.add_argument("--question-id", default="rag_runtime_roles_v1", help="Question from fixtures/ground_truth/rag_questions.json")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark" / "reports")
    args = parser.parse_args()
    adapters, metadata = load_gateway_adapters(args.config.resolve())
    trace_url = metadata.get("router_trace_url")
    collector = RouterTraceCollector(trace_url) if isinstance(trace_url, str) and trace_url else None
    temporary_root = ROOT / "benchmark" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    report = ABRunner(
        adapters,
        trace_collector=collector,
        trace_session_resolver=OpenClawSessionResolver() if collector else None,
        metadata=metadata,
        temporary_root=temporary_root,
    ).run(select_cases(args.case, question_id=args.question_id), runs=args.runs)
    json_path, markdown_path = write_report(report, args.output_dir)
    print(f"contextflow_all_runs_passed={report['summary']['contextflow_all_runs_passed']}")
    print(f"native_all_runs_passed={report['summary']['native_all_runs_passed']}")
    print(f"all_runs_passed={report['summary']['all_runs_passed']}")
    print(f"json_report={json_path}")
    print(f"markdown_report={markdown_path}")


if __name__ == "__main__":
    main()

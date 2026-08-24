from __future__ import annotations

"""Run the first natural-language A/B smoke test through OpenClaw Gateway."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

try:  # Support both package imports and `python benchmark/gateway_smoke.py`.
    from .adapters import (
        BenchmarkRequest,
        ContextFlowOpenClawAdapter,
        GatewayConfig,
        NativeOpenClawAdapter,
        OpenClawGatewayAdapter,
        RunResult,
    )
except ImportError:  # pragma: no cover - direct script launcher.
    from adapters import (
        BenchmarkRequest,
        ContextFlowOpenClawAdapter,
        GatewayConfig,
        NativeOpenClawAdapter,
        OpenClawGatewayAdapter,
        RunResult,
    )

ROOT = Path(__file__).resolve().parents[1]


def _load_adapters(config_path: Path) -> tuple[NativeOpenClawAdapter, ContextFlowOpenClawAdapter]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    def make_config(name: str) -> GatewayConfig:
        item = raw.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"config must contain object {name}")
        return GatewayConfig(
            base_url=_required(item, "base_url"),
            model=_required(item, "model"),
            api_key_env=_optional(item, "api_key_env"),
            timeout_seconds=float(item.get("timeout_seconds", 90)),
        )

    return NativeOpenClawAdapter(make_config("native_openclaw")), ContextFlowOpenClawAdapter(make_config("contextflow_openclaw"))


def _required(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing config field {field}")
    return value.strip()


def _optional(item: dict[str, Any], field: str) -> str | None:
    value = item.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _run(adapter: OpenClawGatewayAdapter, prompt: str, attempt: int) -> RunResult:
    return adapter.run(BenchmarkRequest(
        case_id="ab_file_search_v1",
        prompt=prompt,
        session_key=f"benchmark-file-search-{adapter.name}-{attempt}-{uuid4().hex[:8]}",
        system_prompt="You are running a benchmark. Complete only the requested local file search. Do not modify files. Return the matching relative path and match count.",
    ))


def _result_row(result: RunResult, expected_name: str) -> dict[str, Any]:
    matched = expected_name.lower() in result.final_answer.lower()
    return {
        "adapter": result.adapter,
        "success": result.success,
        "outcome_verified": result.success and matched,
        "latency_ms": result.latency_ms,
        "first_response_ms": result.first_response_ms,
        "tool_calls": len(result.tool_calls),
        "fallback_count": result.fallback_count,
        "finish_reason": result.finish_reason,
        "model": result.model,
        "error": result.error,
        "answer": result.final_answer,
    }


def _write_report(rows: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = {"benchmark": "ContextFlow A/B File Search Smoke", "generated_at": datetime.now(UTC).isoformat(), "records": rows}
    json_path = output_dir / f"ab-file-search-{stamp}.json"
    md_path = output_dir / f"ab-file-search-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["adapter"], []).append(row)
    lines = ["# OpenClaw / ContextFlow A/B：文件搜索冒烟测试", "", "| 配置 | 验证通过率 | P50 总耗时 | 平均返回工具调用数 |", "|---|---:|---:|---:|"]
    for name, items in grouped.items():
        passed = sum(item["outcome_verified"] for item in items)
        lines.append(f"| {name} | {passed / len(items):.0%} | {median(item['latency_ms'] for item in items):.1f} ms | {sum(item['tool_calls'] for item in items) / len(items):.1f} |")
    lines += ["", "## 判定方式", "", "- 两端接收相同自然语言请求，但使用不同、隔离的 session key。", "- 只有成功返回且回答中出现目标文件名时，`outcome_verified` 才为真。", "- 这是连通性和统一指标冒烟测试；后续专项 Case 会以文件系统真实产物和工具轨迹做更严格验证。"]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a native OpenClaw vs ContextFlow file-search smoke test.")
    parser.add_argument("--config", type=Path, required=True, help="Private copy of gateway-benchmark.example.json")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--root", type=Path, default=ROOT / "demo_desktop")
    parser.add_argument("--query", default="notes")
    parser.add_argument("--expected-name", default="meeting-notes.txt")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark" / "reports")
    args = parser.parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be at least 1")
    root = args.root.resolve()
    if not root.is_dir():
        raise ValueError(f"--root is not a directory: {root}")
    native, contextflow = _load_adapters(args.config.resolve())
    prompt = f'请在目录 "{root}" 中搜索文件名包含 "{args.query}" 的文件。只读搜索，不要修改任何文件；请返回命中的相对路径和数量。'
    rows = [_result_row(adapter_result, args.expected_name) for attempt in range(1, args.runs + 1) for adapter_result in (_run(native, prompt, attempt), _run(contextflow, prompt, attempt))]
    json_path, md_path = _write_report(rows, args.output_dir)
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    for row in rows:
        print(f"{row['adapter']}: outcome_verified={row['outcome_verified']} latency_ms={row['latency_ms']} error={row['error']}")


if __name__ == "__main__":
    main()

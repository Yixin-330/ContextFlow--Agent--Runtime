from __future__ import annotations

"""Command-line entry point for the fixed ContextFlow Router evaluation."""

import argparse
from pathlib import Path

from .ab_runner import ROOT, load_gateway_adapters
from .router_accuracy import RouterAccuracyRunner, load_router_cases, write_router_accuracy_report
from .trace_collector import OpenClawSessionResolver, RouterTraceCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed 20-case ContextFlow Router accuracy benchmark.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cases", type=Path, default=None, help="Optional Router cases JSON; defaults to the fixed suite.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark" / "reports")
    args = parser.parse_args()

    adapters, metadata = load_gateway_adapters(args.config.resolve())
    history_url = metadata.get("router_trace_url")
    if not isinstance(history_url, str) or not history_url:
        raise ValueError("router_trace_url is required for Router accuracy evaluation")
    cases = load_router_cases(args.cases.resolve()) if args.cases else load_router_cases()
    temporary_root = ROOT / "benchmark" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    report = RouterAccuracyRunner(
        adapters["contextflow_openclaw"],
        RouterTraceCollector(history_url),
        trace_session_resolver=OpenClawSessionResolver(),
        temporary_root=temporary_root,
        metadata={
            "model": metadata.get("models", {}).get("contextflow_openclaw"),
            "router_trace_url": history_url,
        },
    ).run(cases, runs=args.runs)
    json_path, markdown_path, csv_path = write_router_accuracy_report(report, args.output_dir)
    overall = report["summary"]["overall"]
    print(f"all_runs_passed={report['summary']['all_runs_passed']}")
    print(f"attempts={overall['attempts']}")
    print(f"route_accuracy={overall['route_accuracy']}")
    print(f"first_tool_hit_rate={overall['first_tool_hit_rate']}")
    print(f"exact_tool_sequence_rate={overall['exact_tool_sequence_rate']}")
    print(f"acceptable_tool_sequence_rate={overall['acceptable_tool_sequence_rate']}")
    print(f"tool_family_hit_rate={overall['tool_family_hit_rate']}")
    print(f"upstream_route_hit_rate={overall['upstream_route_hit_rate']}")
    print(f"json_report={json_path}")
    print(f"markdown_report={markdown_path}")
    print(f"csv_report={csv_path}")


if __name__ == "__main__":
    main()

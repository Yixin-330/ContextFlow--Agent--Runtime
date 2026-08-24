from __future__ import annotations

"""ContextFlow scenario benchmark.

This runner invokes the local runtime directly. It measures state, drafts,
artifacts and execution traces without an online model adding noise. Router and
OpenClaw comparisons can use the same case fixtures through another adapter.
"""

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from runtime import ContextFlowRuntime  # noqa: E402
from storage import Storage  # noqa: E402


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    scene: str
    description: str
    execute: Callable[[ContextFlowRuntime, Path, str], dict[str, Any]]
    verify: Callable[[ContextFlowRuntime, str, dict[str, Any]], list[str]]


def _meeting_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    source = ROOT / "demo_data" / "contextflow_delivery_meeting.md"
    inbox = workspace / "inbox" / "meetings"
    inbox.mkdir(parents=True)
    notes = inbox / source.name
    shutil.copyfile(source, notes)
    return runtime.dispatch("context_import_meeting_notes", {
        "session_key": session_key,
        "path": str(notes),
        "title": "ContextFlow delivery review",
        "event_id": f"bench-meeting-{uuid4().hex}",
    })


def _meeting_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if result["context"]["state"] != "deadline_risk":
        failures.append("final state must be deadline_risk")
    if result["orchestration"]["delivery"]["status"] != "planned":
        failures.append("explicit 24-hour deadline must create a delivery-plan draft")
    types = [draft["workflow_type"] for draft in runtime.store.list_drafts(session_key)]
    if types.count("meeting_follow_up") != 1:
        failures.append("exactly one meeting follow-up draft is required")
    if types.count("delivery_plan") != 1:
        failures.append("exactly one delivery-plan draft is required")
    if len(runtime.store.list_sensed_events(session_key)) != 1:
        failures.append("meeting import must be recorded as one sensed event")
    return failures


def _meeting_no_deadline_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    notes = workspace / "inbox" / "meetings" / "weekly_sync.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# Weekly sync\n\nDecision: keep the demo scope.\nTODO: refine the presentation.\n", encoding="utf-8")
    return runtime.dispatch("context_import_meeting_notes", {"session_key": session_key, "path": str(notes)})


def _meeting_no_deadline_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if result["context"]["state"] != "meeting_ended":
        failures.append("meeting without numeric deadline must remain meeting_ended")
    if result["orchestration"]["delivery"]["status"] != "suggested":
        failures.append("meeting without numeric deadline must not create an automatic delivery plan")
    if any(draft["workflow_type"] == "delivery_plan" for draft in runtime.store.list_drafts(session_key)):
        failures.append("meeting without numeric deadline must not create a delivery draft")
    return failures


def _meeting_duplicate_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    notes = workspace / "inbox" / "meetings" / "idempotent_delivery.md"
    notes.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "demo_data" / "contextflow_delivery_meeting.md", notes)
    first = runtime.dispatch("context_import_meeting_notes", {"session_key": session_key, "path": str(notes)})
    repeated = runtime.dispatch("context_import_meeting_notes", {"session_key": session_key, "path": str(notes)})
    return {"first": first, "repeated": repeated}


def _meeting_duplicate_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not result["repeated"].get("duplicate_protected"):
        failures.append("same meeting file event must be duplicate protected")
    if result["repeated"].get("new_drafts_created") != 0:
        failures.append("duplicate meeting event must not create new drafts")
    if len(runtime.store.list_sensed_events(session_key)) != 1 or len(runtime.store.list_drafts(session_key)) != 2:
        failures.append("duplicate event must leave one sensed event and two original drafts")
    return failures


def _data_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    inbox = workspace / "inbox" / "data"
    inbox.mkdir(parents=True)
    # A deliberately diagnostic fixture: the final workstream has an extreme
    # risk score, so the verifier does not depend on incidental demo data.
    dataset_path = inbox / "project_progress_risk.csv"
    dataset_path.write_text(
        "workstream,progress,remaining_hours,risk_score\n"
        "LocalRAGAgent,100,0,0\n"
        "DataInsightAgent,80,2,0\n"
        "OfficeFlowAgent,70,4,0\n"
        "DeliveryPlannerAgent,60,6,0\n"
        "MTClaw_AIBOOK_deployment,20,24,100\n",
        encoding="utf-8",
    )
    profile = runtime.dispatch("data_profile", {
        "session_key": session_key, "dataset_id": "project-progress", "source_path": str(dataset_path),
    })
    analysis = runtime.dispatch("data_analyze", {"session_key": session_key, "dataset_id": "project-progress"})
    chart = runtime.dispatch("data_chart", {
        "session_key": session_key, "dataset_id": "project-progress",
        "x_column": "workstream", "y_column": "risk_score",
    })
    return {"profile": profile, "analysis": analysis, "chart": chart}


def _data_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if result["profile"]["dataset"]["row_count"] != 5:
        failures.append("CSV profile must contain five workstreams")
    risks = result["analysis"]["analysis"]["risks"]
    if not any(risk.get("type") == "numeric_outlier" for risk in risks):
        failures.append("risk analysis must detect the high-risk deployment outlier")
    if not Path(result["chart"]["chart"]["artifact_path"]).is_file():
        failures.append("chart artifact must be written to disk")
    return failures


def _data_arrival_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    dataset = workspace / "inbox" / "data" / "metrics.csv"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("metric,value\npass_rate,95\n", encoding="utf-8")
    return runtime.event_ingress.accept("data_file_ready", {"session_key": session_key, "path": str(dataset)})


def _data_arrival_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    if result["context"]["state"] != "data_ready_for_analysis":
        return ["stable CSV arrival must become data_ready_for_analysis"]
    if result["event"]["source"] != "local-data-inbox":
        return ["data readiness must retain the local-data-inbox source"]
    return []


def _data_isolation_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    dataset = workspace / "private.csv"
    dataset.write_text("team,value\nA,9\n", encoding="utf-8")
    runtime.dispatch("data_profile", {"session_key": session_key, "dataset_id": "private", "source_path": str(dataset)})
    try:
        runtime.dispatch("data_analyze", {"session_key": f"{session_key}:other", "dataset_id": "private"})
    except ValueError as error:
        return {"isolation_error": str(error)}
    return {"isolation_error": None}


def _data_isolation_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    return [] if result["isolation_error"] == "dataset not found; call data_profile first" else ["dataset must not cross session boundaries"]


def _desktop_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    desktop = workspace / "desktop"
    desktop.mkdir()
    note = desktop / "meeting-notes.txt"
    note.write_text("Archive after the review meeting.", encoding="utf-8")
    plan = runtime.dispatch("desktop_plan_archive", {
        "session_key": session_key, "root_path": str(desktop), "relative_paths": [note.name],
    })
    before_confirmation = note.is_file()
    runtime.dispatch("desktop_confirm_archive", {
        "session_key": session_key, "plan_id": plan["plan"]["plan_id"], "decision": "approved",
    })
    executed = runtime.dispatch("desktop_execute_archive", {
        "session_key": session_key, "plan_id": plan["plan"]["plan_id"],
    })
    undone = runtime.dispatch("desktop_undo_archive", {
        "session_key": session_key, "operation_id": executed["operation"]["operation_id"],
    })
    return {"plan": plan, "before_confirmation": before_confirmation, "executed": executed, "undone": undone, "restored": note.is_file()}


def _desktop_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not result["before_confirmation"]:
        failures.append("file must remain in place before user confirmation")
    if result["executed"]["operation"]["status"] != "completed":
        failures.append("approved archive operation must complete")
    if result["undone"]["operation"]["status"] != "undone" or not result["restored"]:
        failures.append("undo must restore the archived file")
    return failures


def _desktop_unapproved_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    desktop = workspace / "desktop"
    desktop.mkdir()
    note = desktop / "review.txt"
    note.write_text("must stay until approval", encoding="utf-8")
    plan = runtime.dispatch("desktop_plan_archive", {"session_key": session_key, "root_path": str(desktop), "relative_paths": [note.name]})["plan"]
    try:
        runtime.dispatch("desktop_execute_archive", {"session_key": session_key, "plan_id": plan["plan_id"]})
    except ValueError as error:
        return {"error": str(error), "file_still_present": note.is_file()}
    return {"error": None, "file_still_present": note.is_file()}


def _desktop_unapproved_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    if result["error"] is None or "must be approved" not in result["error"] or not result["file_still_present"]:
        return ["unapproved archive must be blocked without moving the file"]
    return []


def _desktop_path_escape_execute(runtime: ContextFlowRuntime, workspace: Path, session_key: str) -> dict[str, Any]:
    desktop = workspace / "desktop"
    desktop.mkdir()
    (workspace / "outside.txt").write_text("protected", encoding="utf-8")
    try:
        runtime.dispatch("desktop_plan_archive", {"session_key": session_key, "root_path": str(desktop), "relative_paths": ["../outside.txt"]})
    except ValueError as error:
        return {"error": str(error)}
    return {"error": None}


def _desktop_path_escape_verify(runtime: ContextFlowRuntime, session_key: str, result: dict[str, Any]) -> list[str]:
    return [] if result["error"] and "inside root_path" in result["error"] else ["archive planner must reject path escape"]


CASES = (
    BenchmarkCase("meeting_deadline_v1", "meeting_delivery", "Meeting material event → deadline risk → cross-agent plan", _meeting_execute, _meeting_verify),
    BenchmarkCase("meeting_no_deadline_v1", "meeting_delivery", "Meeting without deadline does not over-orchestrate", _meeting_no_deadline_execute, _meeting_no_deadline_verify),
    BenchmarkCase("meeting_duplicate_event_v1", "meeting_delivery", "Repeated meeting file event creates no duplicate work", _meeting_duplicate_execute, _meeting_duplicate_verify),
    BenchmarkCase("data_risk_v1", "data_insight", "CSV profile → deterministic risks → chart artifact", _data_execute, _data_verify),
    BenchmarkCase("data_arrival_state_v1", "data_insight", "Stable data file arrival becomes an explainable state", _data_arrival_execute, _data_arrival_verify),
    BenchmarkCase("data_session_isolation_v1", "data_insight", "Private dataset is not visible to another session", _data_isolation_execute, _data_isolation_verify),
    BenchmarkCase("desktop_archive_v1", "desktop_execution", "Plan → confirmation → archive → undo", _desktop_execute, _desktop_verify),
    BenchmarkCase("desktop_unapproved_block_v1", "desktop_execution", "Unapproved archive is blocked", _desktop_unapproved_execute, _desktop_unapproved_verify),
    BenchmarkCase("desktop_path_escape_v1", "desktop_execution", "Archive planner rejects paths outside its root", _desktop_path_escape_execute, _desktop_path_escape_verify),
)


def run_cases(case_id: str = "all", runs: int = 3) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    selected = [case for case in CASES if case_id == "all" or case.case_id == case_id]
    if not selected:
        raise ValueError(f"unknown case: {case_id}")
    records: list[dict[str, Any]] = []
    for case in selected:
        for attempt in range(1, runs + 1):
            with tempfile.TemporaryDirectory(prefix=f"contextflow-{case.case_id}-") as temporary:
                workspace = Path(temporary)
                runtime = ContextFlowRuntime(Storage(workspace / "state.db"))
                session_key = f"benchmark:{case.case_id}:{attempt}"
                started = perf_counter()
                try:
                    result = case.execute(runtime, workspace, session_key)
                    failures = case.verify(runtime, session_key, result)
                except Exception as error:
                    failures = [f"runtime error: {type(error).__name__}: {error}"]
                latency_ms = round((perf_counter() - started) * 1000, 3)
                evaluation = runtime.store.evaluation_summary(session_key)
                records.append({
                    "case_id": case.case_id, "scene": case.scene, "attempt": attempt,
                    "passed": not failures, "failures": failures, "latency_ms": latency_ms,
                    "tool_calls": evaluation["total_events"], "success_rate": evaluation["success_rate"],
                    "fallback_rate": evaluation["fallback_rate"],
                })
    return _report(records, runs)


def _report(records: list[dict[str, Any]], requested_runs: int) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case_id"], []).append(record)
    cases = []
    for case_id, items in by_case.items():
        passed = sum(item["passed"] for item in items)
        latencies = sorted(item["latency_ms"] for item in items)
        cases.append({
            "case_id": case_id, "scene": items[0]["scene"], "runs": len(items),
            "pass_rate": round(passed / len(items), 4), "pass_all_runs": passed == len(items),
            "latency_p50_ms": latencies[len(latencies) // 2],
            "average_tool_calls": round(sum(item["tool_calls"] for item in items) / len(items), 2),
            "fallback_rate": round(sum(item["fallback_rate"] for item in items) / len(items), 4),
        })
    return {"benchmark": "ContextFlowScenarioBench", "version": "0.1", "generated_at": datetime.now(UTC).isoformat(), "requested_runs": requested_runs, "summary": {"cases": cases, "all_runs_passed": all(item["passed"] for item in records)}, "records": records}


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"contextflow-benchmark-{stamp}.json"
    markdown_path = output_dir / f"contextflow-benchmark-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# ContextFlow 专项 Benchmark 报告", "", f"生成时间：{report['generated_at']}", "", "| 场景 | 通过率 | 全部重复通过 | P50 耗时 | 平均工具调用 | 上游兜底率 |", "|---|---:|---|---:|---:|---:|"]
    for item in report["summary"]["cases"]:
        lines.append(f"| {item['case_id']} | {item['pass_rate']:.0%} | {'是' if item['pass_all_runs'] else '否'} | {item['latency_p50_ms']:.1f} ms | {item['average_tool_calls']:.1f} | {item['fallback_rate']:.0%} |")
    lines += ["", "## 评测口径", "", "- 每轮使用独立 SQLite 与临时工作目录。", "- 会议场景验证事件感知、状态升级、跨 Agent 编排和草稿去重。", "- 数据场景验证风险发现和图表产物。", "- 桌面场景验证确认边界与可撤销执行。", "- 当前 fallback_rate 是 Runtime 内部记录；后续接入 MTClaw/OpenClaw A/B Adapter 后再测模型实际兜底。"]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ContextFlow deterministic scenario benchmarks.")
    parser.add_argument("--case", default="all", choices=["all", *(case.case_id for case in CASES)])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark" / "reports")
    args = parser.parse_args()
    report = run_cases(args.case, args.runs)
    json_path, markdown_path = write_report(report, args.output_dir)
    print(f"all_runs_passed={report['summary']['all_runs_passed']}")
    print(f"json_report={json_path}")
    print(f"markdown_report={markdown_path}")


if __name__ == "__main__":
    main()

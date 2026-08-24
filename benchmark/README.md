# ContextFlow 专项 Benchmark

该 Benchmark 用确定性验证 ContextFlow 的三条比赛主链路，不把在线模型的随机性混入 Runtime 回归测试。

```powershell
& ".\.venv\Scripts\python.exe" ".\benchmark\runner.py" --runs 3
```

报告会写入 `benchmark/reports/`，同时生成 JSON 原始记录与适合比赛材料引用的 Markdown 汇总。

当前九项任务：

- 会议交付：`meeting_deadline_v1`、`meeting_no_deadline_v1`、`meeting_duplicate_event_v1`；
- 数据洞察：`data_risk_v1`、`data_arrival_state_v1`、`data_session_isolation_v1`；
- 桌面执行：`desktop_archive_v1`、`desktop_unapproved_block_v1`、`desktop_path_escape_v1`。

它们分别覆盖任务完成、事件感知准确性、不过度编排、幂等去重、会话隔离、确认边界、可撤销执行与路径安全。

每一轮使用独立 SQLite 数据库与临时目录，`pass_all_runs` 只有在全部重复运行均通过时才为真。

后续会增加 MTClaw/OpenClaw A/B Adapter，并用 ClawBench/PinchBench 任务证明通用能力不退化。

## OpenClaw A/B Adapter

`adapters.py` defines one shared `RunResult` contract for both configurations:

- `NativeOpenClawAdapter`: Gateway → direct upstream model;
- `ContextFlowOpenClawAdapter`: Gateway → MTClaw Function Router → ContextFlow.

Copy `gateway-benchmark.example.json` to a private file and set the real direct
upstream model name plus the Gateway token environment-variable name. Do not
commit that private configuration or any token. Both sides must use the same
Gateway version, upstream model version, test files, timeout, and fresh session
keys. The ContextFlow side uses `function_router/function-router` through the
same OpenClaw Gateway, so its session-bridge can inject a stable session ID into
MTClaw.

The adapters are intentionally implemented before they are connected to the
case runner: the next change will add five natural-language, outcome-verifiable
A/B cases without changing the existing deterministic Runtime cases.

### First A/B smoke run: file search

After both Gateway routes are available, run the same read-only natural-language
search against both configurations. The script uses fresh session keys and
writes a side-by-side JSON and Markdown report.

```powershell
& ".\ContextFlow\.venv\Scripts\python.exe" ".\ContextFlow\benchmark\gateway_smoke.py" `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --runs 3
```

The file is verified only when the returned answer contains `meeting-notes.txt`.
The first run should use `--runs 1` to validate the Gateway connection, then
use `--runs 3` for a reportable result.

### Local RAG A/B question set

The shared reference document is `fixtures/rag/contextflow_reference.md`; the
ten fixed, automatically checked questions are in
`fixtures/ground_truth/rag_questions.json`. Both configurations receive the
same temporary copy of the document, fresh sessions, the same prompt, and the
same source-citation requirement.

For the ContextFlow route, a short `rag_ingest` preparation turn indexes the
copied document in its intentionally session-isolated local knowledge store;
the benchmark then asks the fixed question. The report records preparation,
question-only, and total end-to-end latency separately. Native OpenClaw reads
the same local file directly, so compare `question_latency_mean_ms` for warm
reading latency and `end_to_end_latency_mean_ms` for cold-start capability cost.

Run one question first, then repeat it three times:

```powershell
cd "C:\Users\Lenovo\Documents\摩尔线程个人工作智能体\ContextFlow"

& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case local_rag --question-id rag_runtime_roles_v1 --runs 3
```

This is a fair **local-document reading** comparison only if both configurations
have access to equivalent local-file tools. The separate ContextFlow RAG gain
test is the state-triggered, session-isolated retrieval used after a meeting.

## Unified online A/B framework

`ab_runner.py` is the shared seam for the competition-facing online cases. It
alternates AB/BA order, creates a fresh session and temporary workspace for
every adapter run, hashes the workspace before and after execution, collects a
sanitized Router trace by session key, and writes one JSON/Markdown report.
Case implementations only provide three lifecycle methods: `prepare`,
`execute`, and `verify`.

The JSON summary and terminal output expose three verdicts. The per-adapter
values distinguish whether every Native run and every ContextFlow run passed;
the strict global value is true only when both are true:

```text
contextflow_all_runs_passed=True
native_all_runs_passed=False
all_runs_passed=False
```

The private Gateway configuration may include the Router history endpoint:

```json
"router_trace_url": "http://127.0.0.1:18790/v1/tool_history"
```

Run the built-in read-only file-search compatibility case from the ContextFlow
directory:

```powershell
& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case file_search `
  --runs 3
```

The JSON records distinguish final-response tool calls from Router-internal
tool calls. Router traces retain only tool names, counts, status, and timestamps;
tool arguments, tool results, prompts, and credentials are not copied into the
trace section of the report. P95 uses nearest-rank and is meaningful only after
enough repetitions; three-run smoke reports are connectivity checks.

### Multi-turn traces and phase checkpoints

The trace collector aggregates every Router history entry with the same session
key in timestamp order. The report records the entry count, ordered tool names,
summed tool rounds, LLM calls, tool-result errors, and fallbacks across all
turns. Sensitive tool arguments and results remain excluded.

OpenClaw 2026.7 canonicalizes an inbound benchmark key and exposes its internal
session ID to provider plugins. The runner resolves that exact mapping from the
local OpenClaw session store before querying Router history; it does not use
prompt-prefix or fuzzy trace matching.

Router-native lexical ingestion and answer paths report zero Token usage because
they do not invoke an LLM. When Qwen routing is used, MTClaw now aggregates the
real usage returned by every routing and completion-check call instead of
emitting a hard-coded zero.

After `prepare` returns, the runner establishes the baseline file snapshot. A
multi-turn case can record meaningful states with one call:

```python
context.checkpoint("after_plan")
context.checkpoint("after_execution")
context.checkpoint("after_undo")
```

Each named phase reports both `from_start` and `from_previous` file deltas. The
runner always appends a reserved `final` checkpoint and keeps its `from_start`
delta in the legacy `file_delta` field for existing verifiers. The phase name
`final` is reserved and duplicate phase names fail the case rather than silently
overwriting evidence. Records also include `phase_snapshots`: relative path,
size, and SHA256 for `before`, every named phase, and `final`.

### Archive-safety A/B suite

Archive safety is intentionally split into three cases so a successful undo
cannot hide an unsafe plan or rejection phase:

- `archive_unconfirmed`: search and plan only; `after_plan` must equal `before`;
- `archive_rejected`: plan, then explicitly reject; both checkpoints must equal `before`;
- `archive_lifecycle`: plan, approve and execute, then undo. The execution phase
  must contain only the two expected moves, preserve source SHA256 values, leave
  unrelated and pre-existing archive files unchanged, and restore the exact
  initial path/SHA256 snapshot after undo.

The ContextFlow records additionally enforce the exact Router tool contract:
`desktop_search_files` → `desktop_plan_archive` → (when applicable)
`desktop_confirm_archive` → `desktop_execute_archive` → `desktop_undo_archive`.
These archive transitions use deterministic Router short paths: the plan reply
returns `PLAN_ID`, the benchmark includes it in the approval/rejection turn,
the execution reply returns `OPERATION_ID`, and the undo turn includes that
identifier. No routing or upstream LLM is needed on the ContextFlow side.

On Windows the runner grants the disposable benchmark workspace temporary
modify access for sibling processes after fixture preparation. This prevents a
long-running Router from seeing an empty sandbox directory while preserving
per-run isolation and automatic cleanup.

Each record separates `response_usage` (Gateway response tokens) from
`router_usage` (Router-internal LLM tokens). The legacy `usage` field is their
sum and is the value used by the summary's average Token metric.

Run one case or the complete suite:

```powershell
& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case archive_unconfirmed --runs 1

& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case archive_safety --runs 3
```

## Data-insight fixtures

The labeled risk dataset, clean control, invalid-schema case, machine-readable
ground truth, and human audit workbook live under `benchmark/fixtures/data/`.
Their README fixes the prompts and scoring contract. Regression tests verify
the CSV SHA256 values, exact deterministic risk identities, chart creation, and
safe failure when a requested chart column is absent.

Run one labeled-risk smoke attempt:

The Gateway timeout should be at least 180 seconds. Data-insight prompts require
the fixed English risk enums, `sum` aggregation, and—when available—the ordered
ContextFlow chain `data_profile -> data_analyze -> data_chart`.

```powershell
& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case data_risk `
  --runs 1
```

Run the complete data-insight suite (labeled risk, clean control, and invalid
schema) with AB/BA ordering:

```powershell
& ".\.venv\Scripts\python.exe" -m benchmark.ab_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --case data_insight `
  --runs 3
```

## Router accuracy

The Router benchmark is intentionally ContextFlow-only rather than A/B. It runs
20 fixed requests three times each, with a fresh session and disposable fixture
workspace for every attempt. It reports first-tool accuracy, exact tool-sequence
accuracy, direct-upstream accuracy, wrong and unnecessary tool calls, expected
multi-step and redundant calls, fallbacks, tool errors, invalid arguments,
latency, token usage, and trace coverage.

```powershell
& ".\.venv\Scripts\python.exe" -m benchmark.router_accuracy_runner `
  --config "$env:USERPROFILE\.config\contextflow\gateway-benchmark.json" `
  --runs 3
```

The command writes one JSON summary, one Markdown competition table, and one
attempt-level CSV under `benchmark/reports/`. Tool arguments and result bodies
are not copied into these reports.

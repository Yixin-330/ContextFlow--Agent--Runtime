# ContextFlow 与 MTClaw 集成说明

## 1. 集成定位

ContextFlow 不替换 MTClaw Function Router。它将用户工作状态与工作流草稿封装为 MTClaw 可路由工具，使高频、确定性、低风险场景优先走本地工具路径；未命中工具或需要复杂自然语言推理的请求仍由 MTClaw 透明转发至上游模型。

## 2. 真实代码映射

| MTClaw 文件 | MTClaw 能力 | ContextFlow 接入方式 |
|---|---|---|
| `function_router/server.py` | 加载工具、路由、执行、完成检查与工具历史 | 不修改核心；由工具 Schema 和 wrapper 接入 |
| `function_router/server.py: execute_tool()` | 以 `<tool_name>.sh` 执行外部工具 | ContextFlow 为每个工具提供同名 wrapper |
| `function_router/server.py: derive_session_key()` | 从 OpenClaw 请求头或 body 获得稳定会话键 | 将 `session_key` 作为 SQLite 状态和草稿的隔离键 |
| `plugins/session-bridge/index.ts` | 注入 OpenClaw session header | 复用，不修改；保证状态不会跨会话串扰 |
| `plugins/fr-tools/index.ts` | 在 OpenClaw 侧注册 Router 工具 | 读取 ContextFlow `functions.jsonl` 并委托 `/v1/execute_tool` |

## 3. 已实现工具

| Tool | 输入 | 输出 | 风险边界 |
|---|---|---|---|
| `context_update_state` | session、状态、证据、置信度 | 已存储 ContextState | 仅本地写 SQLite |
| `context_get_state` | session | 当前状态、证据、允许能力 | 只读 |
| `workflow_prepare_meeting` | session、会议标题 | 含 DAG 的会议工作流草稿 | 仅生成草稿 |
| `workflow_confirm_action` | session、草稿 ID、批准/拒绝 | 更新草稿状态 | 外部写入仍为 stub |

## 4. 运行链路

```text
OpenClaw session-bridge
  → x-openclaw-session-id / x-openclaw-session-key
  → MTClaw Function Router
  → functions.jsonl 匹配 ContextFlow 工具
  → scripts/<tool>.sh
  → service/contextflow_cli.py
  → SQLite 状态与工作流草稿
  → JSON stdout
  → MTClaw completion check / 上游 LLM
```

## 5. 下一步

1. 将 `ContextFlow/mtclaw/functions.jsonl` 配置为 MTClaw 的 `functions_file`。
2. 将 wrapper 复制到 MTClaw 的 `scripts_dir`，并在 Linux/MT AIOS 环境执行 `chmod +x`。
3. 配置 `tools_base_dir`，使 MTClaw 向 wrapper 注入 `FR_TOOLS_BASE_DIR`。
4. 接入真实会议事件和会议文本来源；当前版本仅验证状态与工作流契约。
5. 新增 `workflow_plan_delivery`，实现临期状态下的任务 DAG 规划。
6. 通过 MTClaw `/v1/tool_history` 与 ContextFlow SQLite 日志记录路由、耗时、成功率和回退证据。

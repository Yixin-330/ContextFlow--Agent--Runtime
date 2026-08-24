# MTClaw 部署步骤

## 当前开发环境限制

MTClaw 的 `execute_tool()` 使用 `bash <script>.sh` 执行 wrapper。当前 Windows 环境未安装 WSL/Linux Bash，因此不能在本机执行完整 Router 工具链。SQLite 状态机与工具 JSON 契约已在 Windows Python 下验证；端到端验证应在 MT AIOS / Ubuntu / WSL 环境进行。

## 1. 准备运行环境

在 MT AIOS 或 Ubuntu 环境中安装并运行 MTClaw，再将 `ContextFlow` 项目复制到本地目录，例如：

```bash
export CONTEXTFLOW_HOME=/opt/contextflow
cd "$CONTEXTFLOW_HOME/mtclaw"
cp config.contextflow.example.json config.contextflow.json
chmod +x scripts/*.sh run_contextflow_router.sh
```

安装 ContextFlow 的 Python 依赖仅使用标准库；MTClaw 本身按其 README 安装。

## 2. 配置模型端点

不要把 API Key 写入仓库。使用环境变量：

```bash
export CONTEXTFLOW_ROUTING_BASE_URL="https://your-routing-endpoint/v1"
export CONTEXTFLOW_ROUTING_MODEL="your-tool-calling-model"
export CONTEXTFLOW_ROUTING_API_KEY="..."
export CONTEXTFLOW_UPSTREAM_BASE_URL="https://your-upstream-endpoint/v1"
export CONTEXTFLOW_UPSTREAM_MODEL="your-upstream-model"
export CONTEXTFLOW_UPSTREAM_API_KEY="..."
```

路由模型必须支持 OpenAI-compatible tool calling。

## 3. 启动独立 ContextFlow Router

```bash
cd "$CONTEXTFLOW_HOME/mtclaw"
./run_contextflow_router.sh
curl -s http://127.0.0.1:18791/health
curl -s http://127.0.0.1:18791/v1/tools
```

独立端口 `18791` 避免影响已有 MTClaw 服务；确认稳定后再接入 OpenClaw。

## 4. 先做确定性工具验证

```bash
curl -s -X POST http://127.0.0.1:18791/v1/execute_tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"context_update_state","arguments":{"session_key":"demo-001","state":"meeting_ended","evidence":["calendar ended","recording ready"]}}'

curl -s -X POST http://127.0.0.1:18791/v1/execute_tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"workflow_prepare_meeting","arguments":{"session_key":"demo-001","meeting_title":"HICOOL 项目讨论"}}'
```

预期：第二个请求返回 `awaiting_review` 草稿，而不是执行邮件发送或任何外部写入。

## 5. 接入 OpenClaw

在 OpenClaw 中配置新的 Function Router provider，地址指向 `http://127.0.0.1:18791/v1`；启用 MTClaw `session-bridge` 与 `fr-tools` 插件。先验证 `x-openclaw-session-key` 或 `x-openclaw-session-id` 已进入 MTClaw 日志，再进行自然语言路由测试。

## 6. 评测日志

记录以下证据：

- Router `tools_loaded`；
- 每次工具调用的 session key、耗时、成功或失败；
- `/v1/tool_history`；
- ContextFlow SQLite 中的状态变化与草稿状态；
- 严格模式下的工具成功率和端到端延迟。

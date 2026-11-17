# OpenClaw + MTClaw + ContextFlow integration

## Runtime roles

`OpenClaw` is the Agent/Gateway runtime, not a base model. MTClaw Function Router is its OpenAI-compatible provider: it selects a matching ContextFlow tool for a short path, executes the corresponding Bash wrapper, then either returns the completed result or hands the turn to an upstream model. ContextFlow is the local state and workflow runtime behind those tools.

```text
OpenClaw session
  -> Session Bridge injects a stable session ID
  -> MTClaw Function Router (127.0.0.1:18791)
  -> ContextFlow functions.jsonl exposes the registered tool schemas
  -> scripts/<tool>.sh -> service/contextflow_cli.py
  -> local SQLite state, workflow, audit, and result JSON
  -> direct response or upstream OpenAI-compatible model
```

The single external seam is MTClaw's OpenAI-compatible provider endpoint. OpenClaw only needs the provider URL; ContextFlow implementation details stay behind MTClaw tool schemas and wrappers. Behind `contextflow_cli.py`, the Python Runtime owns Context State, Pipeline checkpoints, Tool Gateway operations, approvals and audit traces; Router fallback does not replace Runtime resume or idempotency.

## One-time installation on Ubuntu 22.04 / MTT AIBOOK

These steps should be completed on the final AIBOOK or an Ubuntu/WSL test environment, not on the current Windows-only Python test setup.

```bash
# MTClaw
git clone https://github.com/MooreThreads/MTClaw.git ~/MTClaw
cd ~/MTClaw
pip install .
./scripts/install.sh

# OpenClaw requires Node.js 24.
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install 24 && nvm use 24
curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash
openclaw onboard
```

Run the vendor installation interactively. It creates the baseline Function Router and OpenClaw configuration; do not place API keys in the ContextFlow repository.

## Deploy ContextFlow tools

Copy the full `ContextFlow` directory to the AIBOOK, for example `/opt/contextflow`. Then:

```bash
export CONTEXTFLOW_HOME=/opt/contextflow
cd "$CONTEXTFLOW_HOME/mtclaw"
cp config.contextflow.example.json config.contextflow.json
chmod +x scripts/*.sh run_contextflow_router.sh preflight_contextflow.sh

# Keep credentials private and outside the repository.
mkdir -p ~/.config/contextflow
cp contextflow.env.example ~/.config/contextflow/contextflow.env
chmod 600 ~/.config/contextflow/contextflow.env
# Edit the private file, then load it for this shell.
source ~/.config/contextflow/contextflow.env

./preflight_contextflow.sh
./run_contextflow_router.sh
```

`config.contextflow.json` intentionally listens on port `18791`, avoiding a conflict with a pre-existing default MTClaw Router on `18790`. If you change this port, change the OpenClaw `baseUrl` to the same value.

### Windows temporary Router environment

The final submission must still be validated on Ubuntu/MTT AIBOOK. For pre-final development on this Windows computer, Git Bash can execute the wrapper scripts, but it does not expose a `python3` command. Set `CONTEXTFLOW_PYTHON` to the Git-Bash form of the active Python executable before starting the Router, for example:

```bash
export CONTEXTFLOW_PYTHON="/c/Users/Lenovo/Documents/摩尔线程个人工作智能体/ContextFlow/.venv/Scripts/python.exe"
export CONTEXTFLOW_BASH_BIN="D:/Javaee/Git/bin/bash.exe"
```

All ContextFlow wrappers and `run_contextflow_router.sh` honour these optional variables; on Linux they are unnecessary and default to `python3` and `bash`.

The ContextFlow launcher also sets `FR_DISABLE_BUILTIN_TOOLS=1` by default. This prevents generic MTClaw tools such as `find` from competing with ContextFlow's audited `DesktopExecutorAgent` file search during natural-language routing. The Router should therefore report **18** ContextFlow tools after restart. Set `CONTEXTFLOW_DISABLE_BUILTIN_TOOLS=0` only if you intentionally need the generic builtins.

## Connect OpenClaw

1. Merge [openclaw.contextflow.example.json5](../mtclaw/openclaw.contextflow.example.json5) into `~/.openclaw/openclaw.json`. Preserve existing providers and agents.
2. Install the session bridge when OpenClaw supports it:

   ```bash
   openclaw plugins install clawhub:openclaw-session-bridge-plugin
   ```

3. Restart and inspect it:

   ```bash
   openclaw gateway restart
   openclaw gateway status
   ```

The Session Bridge is required for reliable multi-turn state isolation. Without it, set `fr_context_history.enabled` and `fr_context_preserve.enabled` to `false` in the MTClaw config and treat the result only as a single-session compatibility test.

## Acceptance checks

In a separate terminal after the ContextFlow Router starts:

```bash
curl -s http://127.0.0.1:18791/health
curl -s http://127.0.0.1:18791/v1/tools
curl -s -X POST http://127.0.0.1:18791/v1/execute_tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"context_get_state","arguments":{"session_key":"integration-check"}}'
```

The tools endpoint should include 22 ContextFlow functions. Then use one OpenClaw session for the final continuous dialogue: event sensing, meeting summary, local RAG, CSV/XLSX analysis, delivery plan, and desktop archive plan with explicit approval plus undo. Inspect both sources of evidence:

```bash
curl -s 'http://127.0.0.1:18791/v1/tool_history?limit=20'
tail -f ~/.function-router/logs/router.debug.log
```

ContextFlow records the complementary audit trail in `/opt/contextflow/data/contextflow.db`: selected Agent, tool, state, latency, success, fallback, and confirmation decision.

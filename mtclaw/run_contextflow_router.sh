#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export CONTEXTFLOW_HOME="${CONTEXTFLOW_HOME:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"

: "${CONTEXTFLOW_ROUTING_BASE_URL:?Set CONTEXTFLOW_ROUTING_BASE_URL}"
: "${CONTEXTFLOW_ROUTING_MODEL:?Set CONTEXTFLOW_ROUTING_MODEL}"
: "${CONTEXTFLOW_ROUTING_API_KEY:?Set CONTEXTFLOW_ROUTING_API_KEY}"
: "${CONTEXTFLOW_UPSTREAM_BASE_URL:?Set CONTEXTFLOW_UPSTREAM_BASE_URL}"
: "${CONTEXTFLOW_UPSTREAM_MODEL:?Set CONTEXTFLOW_UPSTREAM_MODEL}"
: "${CONTEXTFLOW_UPSTREAM_API_KEY:?Set CONTEXTFLOW_UPSTREAM_API_KEY}"

PYTHON_BIN="${CONTEXTFLOW_PYTHON:-python3}"
# On Linux/AIBOOK this remains `bash`.  Windows + Git Bash can set
# CONTEXTFLOW_BASH_BIN to an explicit bash.exe path.
export FR_SHELL_BIN="${CONTEXTFLOW_BASH_BIN:-bash}"
# ContextFlow exposes its own audited file tools. Avoid generic MTClaw builtins
# competing with the vertical Agent contracts during automatic routing.
export FR_DISABLE_BUILTIN_TOOLS="${CONTEXTFLOW_DISABLE_BUILTIN_TOOLS:-1}"
exec "$PYTHON_BIN" -m function_router.server --config "$SCRIPT_DIR/config.contextflow.json"

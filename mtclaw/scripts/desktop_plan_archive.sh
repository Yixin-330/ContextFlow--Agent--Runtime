#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${CONTEXTFLOW_PYTHON:-python3}"
exec "$PYTHON_BIN" "${FR_TOOLS_BASE_DIR:?FR_TOOLS_BASE_DIR is required}/service/contextflow_cli.py" desktop_plan_archive

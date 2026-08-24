#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${FR_PYTHON_BIN:-python3}"
exec "$PYTHON_BIN" "${FR_TOOLS_BASE_DIR:?FR_TOOLS_BASE_DIR is required}/service/contextflow_cli.py" context_import_meeting_notes

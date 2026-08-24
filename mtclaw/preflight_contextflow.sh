#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${CONTEXTFLOW_CONFIG:-$SCRIPT_DIR/config.contextflow.json}"

PYTHON_BIN="${CONTEXTFLOW_PYTHON:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python interpreter not found: $PYTHON_BIN"; exit 1; }
command -v bash >/dev/null || { echo 'ERROR: bash is required'; exit 1; }
[ -f "$CONFIG_PATH" ] || { echo "ERROR: missing config: $CONFIG_PATH"; exit 1; }

"$PYTHON_BIN" - "$SCRIPT_DIR/functions.jsonl" "$CONFIG_PATH" "$SCRIPT_DIR/scripts" <<'PY'
import json
import sys
from pathlib import Path

functions_path, config_path, scripts_dir = map(Path, sys.argv[1:])
functions = [json.loads(line)["name"] for line in functions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
config = json.loads(config_path.read_text(encoding="utf-8"))
delegated = config.get("delegate_tools_to_openclaw", {}).get("tools", [])
missing_scripts = [name for name in functions if not (scripts_dir / f"{name}.sh").is_file()]
missing_config = sorted(set(functions) - set(delegated))
extra_config = sorted(set(delegated) - set(functions))
if len(functions) != len(set(functions)) or missing_scripts or missing_config or extra_config:
    print(json.dumps({"functions": len(functions), "missing_scripts": missing_scripts,
                      "missing_config": missing_config, "extra_config": extra_config}, ensure_ascii=False))
    raise SystemExit(1)
print(json.dumps({"result": "ok", "contextflow_tools": len(functions), "config": str(config_path)}, ensure_ascii=False))
PY

if ! "$PYTHON_BIN" -c 'import function_router' 2>/dev/null; then
  echo 'WARNING: function_router is not importable yet; run this after MTClaw pip install .'
else
  echo 'OK: MTClaw function_router package is importable'
fi

echo "OK: ContextFlow root is $ROOT_DIR"

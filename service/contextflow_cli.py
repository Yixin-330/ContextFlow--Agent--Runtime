from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

try:  # Support both `python service/contextflow_cli.py` and `python -m service.contextflow_cli`.
    from .runtime import ContextFlowRuntime
    from .storage import Storage
except ImportError:  # pragma: no cover - used by MTClaw's shell-tool launcher.
    from runtime import ContextFlowRuntime
    from storage import Storage

ROOT = Path(__file__).resolve().parents[1]
STORE = Storage(ROOT / "data" / "contextflow.db")
RUNTIME = ContextFlowRuntime(STORE)


def handle(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return RUNTIME.dispatch(tool_name, payload)


def _read_payload(stream: BinaryIO) -> dict[str, Any]:
    """Read tool arguments using the Router's fixed UTF-8 wire encoding."""
    payload = json.loads(stream.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tool arguments must be a JSON object")
    return payload


def main() -> None:
    tool_name = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        payload = _read_payload(sys.stdin.buffer)
        if tool_name == "context_runtime_info":
            print(json.dumps(RUNTIME.runtime_info(payload.get("session_key")), ensure_ascii=False))
            return
        print(json.dumps(handle(tool_name, payload), ensure_ascii=False))
    except (ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

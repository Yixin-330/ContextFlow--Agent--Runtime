from __future__ import annotations

"""Read-only normalization of MTClaw Function Router execution history."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenClawSessionResolver:
    """Resolve an inbound Gateway session key to OpenClaw's provider session id.

    Recent OpenClaw releases intentionally canonicalize an inbound key such as
    ``benchmark:case:1`` to ``agent:main:benchmark:case:1`` and expose the
    associated internal session id to provider plugins.  Function Router sees
    that id, so benchmark traces must resolve the same exact mapping instead of
    falling back to prompt matching.
    """

    def __init__(self, store_path: Path | None = None, *, agent_id: str = "main") -> None:
        self.store_path = store_path or Path.home() / ".openclaw" / "agents" / agent_id / "sessions" / "sessions.json"
        self.agent_id = agent_id

    def resolve(self, session_key: str) -> str | None:
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        for key in (session_key, f"agent:{self.agent_id}:{session_key}"):
            entry = payload.get(key)
            if isinstance(entry, dict) and isinstance(entry.get("sessionId"), str) and entry["sessionId"].strip():
                return entry["sessionId"].strip()
        return None


@dataclass(frozen=True)
class RouterTrace:
    session_key: str
    found: bool = False
    entry_count: int = 0
    tool_names: tuple[str, ...] = ()
    tool_rounds: int = 0
    llm_call_count: int = 0
    tool_result_count: int = 0
    tool_error_count: int = 0
    invalid_argument_count: int = 0
    fallback_count: int = 0
    usage: dict[str, int | float] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "entry_count": self.entry_count,
            "tool_names": list(self.tool_names),
            "tool_rounds": self.tool_rounds,
            "llm_call_count": self.llm_call_count,
            "tool_result_count": self.tool_result_count,
            "tool_error_count": self.tool_error_count,
            "invalid_argument_count": self.invalid_argument_count,
            "fallback_count": self.fallback_count,
            "usage": dict(self.usage),
            "error": self.error,
            "metadata": self.metadata,
        }


class RouterTraceCollector:
    """Aggregate all sanitized Router turns for a session; omit arguments and answers."""

    def __init__(self, history_url: str, *, timeout_seconds: float = 3.0, attempts: int = 4) -> None:
        self.history_url = history_url
        self.timeout_seconds = timeout_seconds
        self.attempts = max(1, attempts)

    def collect(self, session_key: str) -> RouterTrace:
        last_error: str | None = None
        best_entries: list[dict[str, Any]] = []
        for attempt in range(self.attempts):
            try:
                payload = self._fetch()
                entries = self._find_entries(payload, session_key)
                if len(entries) >= len(best_entries):
                    best_entries = entries
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
                last_error = f"{type(error).__name__}: {error}"
            if attempt + 1 < self.attempts:
                sleep(0.1)
        if best_entries:
            return self._normalize(best_entries, session_key)
        return RouterTrace(session_key=session_key, error=last_error)

    def _fetch(self) -> Any:
        request = Request(self.history_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _find_entries(payload: Any, session_key: str) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            entries = payload.get("entries", [])
        elif isinstance(payload, list):
            entries = payload
        else:
            entries = []
        matching = [entry for entry in entries if isinstance(entry, dict) and entry.get("session_key") == session_key]
        indexed = list(enumerate(matching))
        indexed.sort(key=lambda item: (str(item[1].get("timestamp") or ""), item[0]))
        return [entry for _, entry in indexed]

    @classmethod
    def _normalize(cls, entries: list[dict[str, Any]], session_key: str) -> RouterTrace:
        tool_names: list[str] = []
        tool_rounds = 0
        llm_call_count = 0
        tool_result_count = 0
        tool_error_count = 0
        invalid_argument_count = 0
        fallback_count = 0
        usage: dict[str, int | float] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        timestamps: list[str] = []
        for entry in entries:
            calls = entry.get("tool_calls") if isinstance(entry.get("tool_calls"), list) else []
            results = entry.get("tool_results") if isinstance(entry.get("tool_results"), list) else []
            llm_calls = entry.get("llm_calls")
            tool_names.extend(name for name in (cls._tool_name(call) for call in calls) if name)
            tool_rounds += cls._integer(entry.get("tool_rounds"))
            llm_call_count += len(llm_calls) if isinstance(llm_calls, list) else cls._integer(llm_calls)
            tool_result_count += len(results)
            tool_error_count += sum(1 for result in results if isinstance(result, dict) and cls._result_failed(result))
            invalid_argument_count += sum(
                1 for result in results if isinstance(result, dict) and cls._invalid_arguments(result)
            )
            fallback_count += cls._fallback_count(entry)
            if isinstance(llm_calls, list):
                for call in llm_calls:
                    normalized = cls._usage(call.get("usage") if isinstance(call, dict) else None)
                    for key in usage:
                        usage[key] += normalized[key]
            if isinstance(entry.get("timestamp"), str):
                timestamps.append(entry["timestamp"])
        return RouterTrace(
            session_key=session_key,
            found=True,
            entry_count=len(entries),
            tool_names=tuple(tool_names),
            tool_rounds=tool_rounds,
            llm_call_count=llm_call_count,
            tool_result_count=tool_result_count,
            tool_error_count=tool_error_count,
            invalid_argument_count=invalid_argument_count,
            fallback_count=fallback_count,
            usage=usage,
            metadata={
                "first_timestamp": timestamps[0] if timestamps else None,
                "last_timestamp": timestamps[-1] if timestamps else None,
            },
        )

    @staticmethod
    def _tool_name(call: Any) -> str | None:
        if not isinstance(call, dict):
            return None
        function = call.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        for field in ("function_name", "tool_name", "name"):
            if isinstance(call.get(field), str):
                return call[field]
        return None

    @staticmethod
    def _result_failed(result: dict[str, Any]) -> bool:
        if result.get("success") is False or result.get("is_error") is True or result.get("error"):
            return True
        for field in ("result", "content"):
            value = result.get(field)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict) and (
                value.get("success") is False
                or value.get("is_error") is True
                or bool(value.get("error"))
            ):
                return True
        return False

    @classmethod
    def _invalid_arguments(cls, result: dict[str, Any]) -> bool:
        if not cls._result_failed(result):
            return False
        text = cls._result_error_text(result).casefold()
        markers = (
            "missing required argument",
            "required argument",
            "required field",
            "invalid argument",
            "invalid parameter",
            "unexpected argument",
            "must be a non-empty",
            "must be an integer",
            "must be a string",
            "must be an array",
            "validation error",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _result_error_text(result: dict[str, Any]) -> str:
        values: list[str] = []
        for field in ("error", "result", "content"):
            value = result.get(field)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    values.append(value)
                    continue
            if isinstance(value, dict):
                for key in ("error", "message", "detail"):
                    nested = value.get(key)
                    if isinstance(nested, str):
                        values.append(nested)
        return " ".join(values)

    @staticmethod
    def _fallback_count(entry: dict[str, Any]) -> int:
        if isinstance(entry.get("fallback_count"), (int, float)):
            return int(entry["fallback_count"])
        events = entry.get("ordered_events") if isinstance(entry.get("ordered_events"), list) else []
        return sum(1 for event in events if isinstance(event, dict) and "fallback" in str(event.get("event", event.get("type", ""))).casefold())

    @staticmethod
    def _integer(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    @staticmethod
    def _usage(value: Any) -> dict[str, int | float]:
        source = value if isinstance(value, dict) else {}
        prompt = source.get("prompt_tokens", source.get("input_tokens", 0))
        completion = source.get("completion_tokens", source.get("output_tokens", 0))
        prompt_value = prompt if isinstance(prompt, (int, float)) and not isinstance(prompt, bool) else 0
        completion_value = completion if isinstance(completion, (int, float)) and not isinstance(completion, bool) else 0
        total = source.get("total_tokens")
        total_value = total if isinstance(total, (int, float)) and not isinstance(total, bool) else prompt_value + completion_value
        return {
            "prompt_tokens": prompt_value,
            "completion_tokens": completion_value,
            "total_tokens": total_value,
        }

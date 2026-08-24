from __future__ import annotations

"""Transport adapters for fair OpenClaw / ContextFlow benchmark runs.

Both adapters speak the OpenAI-compatible chat-completions interface exposed by
the OpenClaw Gateway.  The only difference is the configured model route:
native OpenClaw calls the direct model, while ContextFlow calls the MTClaw
Function Router provider through the same Gateway.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class BenchmarkRequest:
    """One natural-language task sent to either benchmark configuration."""

    case_id: str
    prompt: str
    session_key: str
    system_prompt: str | None = None


@dataclass
class RunResult:
    """Configuration-neutral result used by verifiers and report generation."""

    adapter: str
    case_id: str
    session_key: str
    success: bool
    latency_ms: float
    first_response_ms: float | None = None
    final_answer: str = ""
    finish_reason: str | None = None
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    fallback_count: int = 0
    error: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatewayConfig:
    """All environment-specific values stay in configuration, never source code."""

    base_url: str
    model: str
    api_key_env: str | None = None
    timeout_seconds: float = 180.0
    session_header: str | None = "x-openclaw-session-key"
    extra_headers: Mapping[str, str] = field(default_factory=dict)


class OpenClawGatewayAdapter:
    """A small, deep interface over an OpenAI-compatible OpenClaw Gateway."""

    name = "openclaw_gateway"

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def run(self, request: BenchmarkRequest) -> RunResult:
        started = perf_counter()
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        body = {"model": "openclaw", "messages": messages, "stream": False}
        headers = {"Content-Type": "application/json","x-openclaw-model": self.config.model, **dict(self.config.extra_headers)}
        if self.config.session_header:
            headers[self.config.session_header] = request.session_key
        if self.config.api_key_env:
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                return self._failure(request, started, f"environment variable {self.config.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {api_key}"
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            wire_request = Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
            with urlopen(wire_request, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            return self._failure(request, started, f"{type(error).__name__}: {error}")
        latency_ms = round((perf_counter() - started) * 1000, 3)
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=True,
            latency_ms=latency_ms,
            first_response_ms=latency_ms,
            final_answer=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            finish_reason=choice.get("finish_reason"),
            model=payload.get("model", self.config.model),
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            tool_calls=calls if isinstance(calls, list) else [],
            raw_response=payload,
        )

    def _failure(self, request: BenchmarkRequest, started: float, error: str) -> RunResult:
        return RunResult(
            adapter=self.name,
            case_id=request.case_id,
            session_key=request.session_key,
            success=False,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            error=error,
        )


class NativeOpenClawAdapter(OpenClawGatewayAdapter):
    """Baseline: the Gateway directly invokes the configured upstream model."""

    name = "native_openclaw"


class ContextFlowOpenClawAdapter(OpenClawGatewayAdapter):
    """Treatment: the Gateway invokes MTClaw's ContextFlow Function Router model."""

    name = "contextflow_openclaw"

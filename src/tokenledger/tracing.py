"""Tracing layer: wrap any LLM-ish callable and emit GenAI-convention spans.

Field names follow the OpenTelemetry GenAI semantic conventions
(gen_ai.system, gen_ai.request.model, gen_ai.usage.input_tokens, ...) so this
schema is drop-in compatible with real OTel exporters — the JSONL sink here
stands in for an OTLP collector, and swapping it is one class.

Why wrap-at-the-client instead of decorating call sites: every call gets
traced or none do. Untraced spend is the FinOps failure mode — the analytics
can only govern what the instrumentation refuses to let escape.
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid
from dataclasses import dataclass, asdict, field


@dataclass
class GenAISpan:
    trace_id: str
    span_id: str
    start_ts: float
    end_ts: float
    # OTel GenAI semantic-convention attributes
    gen_ai_system: str            # "anthropic" | "mock" | ...
    gen_ai_request_model: str
    gen_ai_operation_name: str    # "chat"
    gen_ai_usage_input_tokens: int
    gen_ai_usage_output_tokens: int
    # app-level attributes (the analytics dimensions)
    app_route: str                # semantic | quantitative | synthesis | ...
    app_prompt_version: str
    app_success: bool             # request-level success (no exception/guard block)
    app_eval_score: float | None  # task-level quality when an eval exists
    app_error: str | None = None
    attributes: dict = field(default_factory=dict)

    @property
    def latency_ms(self) -> float:
        return (self.end_ts - self.start_ts) * 1000


class JsonlSink:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, span: GenAISpan) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(asdict(span) | {"latency_ms": span.latency_ms}) + "\n")


# pricing per 1M tokens (input, output) — update-as-needed lookup, kept as
# DATA not code so cost joins are reproducible for any pricing vintage
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "mock-model": (1.00, 5.00),   # priced AS haiku so mock traces produce
                                  # realistic cost shapes, flagged by system
}


def estimate_tokens(text: str) -> int:
    """chars/4 heuristic. Fine for cost ATTRIBUTION shape (relative shares);
    documented as ±20% for absolute dollars. Live providers return exact
    usage — the tracer prefers real counts when the response carries them."""
    return max(1, len(text) // 4)


class TracedLLM:
    """Wraps any object exposing complete(system, user) -> str."""

    def __init__(self, inner, sink: JsonlSink, model: str, system: str = "mock",
                 prompt_version: str = "v1", route_getter=None):
        self.inner = inner
        self.sink = sink
        self.model = model
        self.system = system
        self.prompt_version = prompt_version
        self.route_getter = route_getter or (lambda s, u: "unrouted")

    def complete(self, system: str, user: str) -> str:
        t0 = time.time()
        err, out = None, ""
        try:
            out = self.inner.complete(system, user)
            return out
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            self.sink.emit(GenAISpan(
                trace_id=uuid.uuid4().hex[:16],
                span_id=uuid.uuid4().hex[:8],
                start_ts=t0,
                end_ts=time.time(),
                gen_ai_system=self.system,
                gen_ai_request_model=self.model,
                gen_ai_operation_name="chat",
                gen_ai_usage_input_tokens=estimate_tokens(system + user),
                gen_ai_usage_output_tokens=estimate_tokens(out),
                app_route=self.route_getter(system, user),
                app_prompt_version=self.prompt_version,
                app_success=err is None,
                app_eval_score=None,
                app_error=err,
            ))


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout

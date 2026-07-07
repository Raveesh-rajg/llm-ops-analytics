"""Generate the trace corpus two ways, clearly labeled:

1. REAL traces: run the portfolio's own analytics-rag-agent eval suite with
   the tracer wrapped around its (mock) LLM — genuine call patterns from a
   genuine application: routing mix, retries, guard blocks.
2. SCALED traces: a seeded generator that extrapolates those shapes to
   30 days x thousands of calls, with PLANTED patterns the dashboard must
   surface (the testable-claims pattern used across this portfolio):
     * prompt v2 rollout on day 12 that RAISES eval scores but DOUBLES
       output tokens (the cost/quality trade the readout exists to catch)
     * a retry storm on day 20 (transient provider errors -> cost spike
       with zero extra successes)
     * one route (synthesis) drifting slower week over week
"""

from __future__ import annotations

import json
import pathlib
import random
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tokenledger.tracing import GenAISpan, JsonlSink, estimate_tokens

OUT = pathlib.Path(__file__).resolve().parents[2] / "data"

ROUTES = {"router": 0.25, "semantic_synthesis": 0.30, "text_to_sql": 0.30,
          "sql_interpret": 0.15}
DAY = 86_400


def capture_real_traces(agent_repo: pathlib.Path, sink_path: pathlib.Path) -> int:
    """Wrap the RAG agent's LLM with the tracer and drive it with its own
    eval questions. Returns span count. Uses MockLLM in-sandbox; with an
    API key the same wiring captures live Claude traces."""
    sys.path.insert(0, str(agent_repo / "src"))
    from rag_agent.agent import AnalyticsAgent
    from rag_agent.llm import MockLLM
    from rag_agent import warehouse
    from rag_agent.config import DEFAULT
    from tokenledger.tracing import TracedLLM

    if not DEFAULT.warehouse_path.exists():
        warehouse.build()
    sink = JsonlSink(sink_path)
    traced = TracedLLM(
        MockLLM(canned_sql={"delivered": "SELECT COUNT(*) AS n FROM fct_orders WHERE status = 'delivered'"}),
        sink, model="mock-model", system="mock", prompt_version="v1",
        route_getter=lambda s, u: (
            "router" if "classify" in s.lower()
            else "text_to_sql" if "Generate a single DuckDB SELECT" in s
            else "semantic_synthesis"),
    )
    agent = AnalyticsAgent(llm=traced)
    questions = [json.loads(l)["question"] for l in
                 (agent_repo / "eval" / "questions.jsonl").read_text().splitlines() if l.strip()]
    for q in questions:
        try:
            agent.ask(q)
        except Exception:
            pass
    return sum(1 for _ in open(sink_path))


def generate_scaled(sink_path: pathlib.Path, days: int = 30,
                    calls_per_day: int = 900, seed: int = 20260709) -> dict:
    rng = random.Random(seed)
    sink = JsonlSink(sink_path)
    t_start = time.time() - days * DAY
    planted = {"v2_rollout_day": 12, "retry_storm_day": 20}

    for d in range(days):
        day_t0 = t_start + d * DAY
        n = int(calls_per_day * rng.uniform(0.85, 1.15))
        for _ in range(n):
            route = rng.choices(list(ROUTES), weights=list(ROUTES.values()))[0]
            version = "v2" if (d >= planted["v2_rollout_day"] and rng.random() < 0.9) else "v1"

            in_tok = {"router": 220, "semantic_synthesis": 2600,
                      "text_to_sql": 1400, "sql_interpret": 900}[route]
            in_tok = int(in_tok * rng.uniform(0.7, 1.5))
            out_base = {"router": 4, "semantic_synthesis": 320,
                        "text_to_sql": 90, "sql_interpret": 120}[route]
            # PLANTED: v2 doubles output tokens on synthesis + sql routes
            mult = 2.0 if (version == "v2" and route in ("semantic_synthesis", "text_to_sql")) else 1.0
            out_tok = int(out_base * mult * rng.uniform(0.6, 1.6))

            # PLANTED: v2 raises eval scores (the trade's other side)
            base_q = {"router": 0.96, "semantic_synthesis": 0.78,
                      "text_to_sql": 0.82, "sql_interpret": 0.90}[route]
            q_lift = 0.07 if version == "v2" else 0.0
            eval_score = min(1.0, max(0.0, rng.gauss(base_q + q_lift, 0.08)))

            # PLANTED: synthesis latency drifts +12%/week; storm on day 20
            lat = rng.lognormvariate(6.4, 0.5)  # ~600ms median
            if route == "semantic_synthesis":
                lat *= 1.0 + 0.12 * (d / 7)
            error = None
            success = True
            if d == planted["retry_storm_day"] and rng.random() < 0.25:
                success, error, out_tok = False, "APITimeoutError: upstream", 0

            ts = day_t0 + rng.uniform(0, DAY)
            sink.emit(GenAISpan(
                trace_id=uuid.uuid4().hex[:16], span_id=uuid.uuid4().hex[:8],
                start_ts=ts, end_ts=ts + lat / 1000,
                gen_ai_system="anthropic", gen_ai_request_model="claude-haiku-4-5",
                gen_ai_operation_name="chat",
                gen_ai_usage_input_tokens=in_tok,
                gen_ai_usage_output_tokens=out_tok,
                app_route=route, app_prompt_version=version,
                app_success=success,
                app_eval_score=round(eval_score, 3) if success else None,
                app_error=error,
            ))
            # retry follows a failure (cost with no new success)
            if not success:
                ts2 = ts + rng.uniform(1, 5)
                sink.emit(GenAISpan(
                    trace_id=uuid.uuid4().hex[:16], span_id=uuid.uuid4().hex[:8],
                    start_ts=ts2, end_ts=ts2 + lat / 1000,
                    gen_ai_system="anthropic", gen_ai_request_model="claude-haiku-4-5",
                    gen_ai_operation_name="chat",
                    gen_ai_usage_input_tokens=in_tok,
                    gen_ai_usage_output_tokens=int(out_base * rng.uniform(0.6, 1.6)),
                    app_route=route, app_prompt_version=version,
                    app_success=True, app_eval_score=round(eval_score, 3),
                    app_error=None, attributes={"retry_of": "prior"},
                ))
    return planted


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    real = capture_real_traces(
        pathlib.Path.home() / "work" / "analytics-rag-agent",
        OUT / "traces_real_agent.jsonl")
    print(f"real agent traces: {real}")
    planted = generate_scaled(OUT / "traces_scaled.jsonl")
    print(f"scaled traces written; planted: {planted}")

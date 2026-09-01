# AI Spend & Reliability Control | Cost, quality, and failure analytics

FinOps + quality analytics ON an AI system: every LLM call traced to
OpenTelemetry GenAI semantic conventions, landed in a DuckDB warehouse, and
read out as the three questions engineering leadership actually asks —
**what does a successful answer cost, which prompt versions earn their spend,
and where is money burning without output?**

The 2026 observability market is full of vendor platforms for this; BI-style
analytical treatments are still rare — that gap is the project.

## The data is this portfolio's own telemetry

- **Real traces:** this repo's tracer wraps the sibling `analytics-rag-agent`
  and captures its eval-suite runs end-to-end (routing mix, guard blocks,
  retries) — a portfolio that instruments itself.
- **Scaled corpus:** a seeded generator extrapolates those shapes to 30 days /
  27,683 spans with three PLANTED operational patterns, so every dashboard
  claim is testable (the same planted-defect method as the claims-pipeline
  and A/B projects). In-sandbox traces use the mock provider and are priced
  as a small hosted model; the wiring captures live LLM traces unchanged.

## Verified findings (detected by code, asserted by tests — not narrated)

```
prompt v2 rollout : eval score +6.5pp, but cost per success x1.35 —
                    eval-per-dollar FELL on synthesis (169 -> 133) and
                    text-to-SQL (405 -> 352). The table is the decision.
retry storm       : 195 failed calls in one day, failure z = 5.3 while raw
                    cost z = 0.5 — waste that a cost chart cannot see, which
                    is the argument for failure-aware anomaly detection.
latency drift     : synthesis p50 grew 1.38x across the window; other routes flat.
headline          : 27,683 calls, ~$73 est. spend, $0.0027 per successful answer.
```

9 pytest tests: tracing (spans emitted even when calls THROW — untraced
failures are invisible spend), cost math, warehouse integrity, and one test
per planted finding.

## Design decisions worth interviewing on

- **Cost per SUCCESSFUL answer, not per call.** Retries, failures, and guard
  blocks spend tokens without producing answers. The warehouse asserts
  cost/success > cost/call whenever failures exist — the denominator argument
  as an executable test.
- **OTel GenAI field names** (`gen_ai.usage.input_tokens`, ...) so the schema
  is drop-in for real OTLP collectors; the JSONL sink stands in for one class.
- **Pricing as data, not code** — cost joins are reproducible for any pricing
  vintage.
- **Wrap the client, not the call sites** — every call gets traced or none do.
- **Token estimation honesty:** chars/4 (~±20%) for mock spans, real usage
  counts preferred when the provider returns them; relative cost shares (the
  decisions) are robust to this, absolute dollars are labeled estimates.

## Run

```bash
pip install duckdb streamlit pandas pytest
PYTHONPATH=src python src/tokenledger/generate_traces.py   # real + scaled traces
PYTHONPATH=src pytest tests/ -q                            # 9 tests
PYTHONPATH=src streamlit run dashboard/app.py
```

```
src/tokenledger/tracing.py     TracedLLM wrapper, GenAI-convention spans, pricing
src/tokenledger/generate_traces.py  real capture (RAG agent) + seeded scaler
src/tokenledger/warehouse.py   DuckDB marts: daily ops, version economics
src/tokenledger/findings.py    the three detectors the tests pin
dashboard/app.py               Streamlit: spend, findings, version table, latency
```

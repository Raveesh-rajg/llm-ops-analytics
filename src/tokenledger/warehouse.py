"""Trace warehouse: JSONL spans -> DuckDB marts.

The KPI worth defending in an interview: COST PER SUCCESSFUL ANSWER, not cost
per call. Retries, guard blocks, and failed calls all spend tokens without
producing an answer — dividing by calls hides exactly the waste this system
exists to find. Denominators are the analytics.
"""

from __future__ import annotations

import pathlib

import duckdb

from tokenledger.tracing import PRICING

SPANS_DDL = """
CREATE OR REPLACE TABLE spans AS
SELECT *,
       to_timestamp(start_ts)                    AS started_at,
       CAST(to_timestamp(start_ts) AS DATE)      AS span_date
FROM read_json_auto(?, format='newline_delimited');
"""

DDL = """
CREATE OR REPLACE TABLE pricing (model VARCHAR, usd_per_1m_in DOUBLE, usd_per_1m_out DOUBLE);

CREATE OR REPLACE VIEW v_spans_costed AS
SELECT s.*,
       s.gen_ai_usage_input_tokens  / 1e6 * p.usd_per_1m_in
     + s.gen_ai_usage_output_tokens / 1e6 * p.usd_per_1m_out AS cost_usd
FROM spans s
LEFT JOIN pricing p ON s.gen_ai_request_model = p.model;

-- daily x route x version: the mart the dashboard reads
CREATE OR REPLACE VIEW v_daily_ops AS
SELECT
    span_date,
    app_route,
    app_prompt_version,
    COUNT(*)                                        AS calls,
    SUM(CASE WHEN app_success THEN 1 ELSE 0 END)    AS successes,
    SUM(cost_usd)                                   AS cost_usd,
    SUM(cost_usd) / NULLIF(SUM(CASE WHEN app_success THEN 1 ELSE 0 END), 0)
                                                    AS cost_per_success,
    SUM(gen_ai_usage_output_tokens)                 AS output_tokens,
    AVG(app_eval_score)                             AS avg_eval_score,
    quantile_cont(latency_ms, 0.50)                 AS latency_p50_ms,
    quantile_cont(latency_ms, 0.95)                 AS latency_p95_ms
FROM v_spans_costed
GROUP BY 1, 2, 3;

-- prompt-version comparison at the grain decisions are made
CREATE OR REPLACE VIEW v_version_tradeoff AS
SELECT
    app_route,
    app_prompt_version,
    COUNT(*)                                         AS calls,
    AVG(app_eval_score)                              AS avg_eval_score,
    AVG(gen_ai_usage_output_tokens)                  AS avg_output_tokens,
    SUM(cost_usd) / NULLIF(SUM(CASE WHEN app_success THEN 1 ELSE 0 END), 0)
                                                     AS cost_per_success,
    AVG(app_eval_score) / NULLIF(SUM(cost_usd) / NULLIF(SUM(CASE WHEN app_success THEN 1 ELSE 0 END), 0), 0)
                                                     AS eval_score_per_dollar
FROM v_spans_costed
WHERE app_eval_score IS NOT NULL
GROUP BY 1, 2;
"""


def build(traces: list[pathlib.Path], db_path: pathlib.Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    # parameterized statement must run alone (DuckDB prepared-stmt rule)
    con.execute(SPANS_DDL, [[str(t) for t in traces]])
    con.execute(DDL)
    con.executemany("INSERT INTO pricing VALUES (?, ?, ?)",
                    [(m, i, o) for m, (i, o) in PRICING.items()])
    return con

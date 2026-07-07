"""Automated findings: the planted patterns, detected not narrated.

Each check returns (finding, evidence dict). The test suite asserts all
three planted patterns are caught — the dashboard's claims are executable.
"""

from __future__ import annotations

import duckdb


def v2_cost_quality_tradeoff(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute("""
        SELECT app_prompt_version,
               AVG(avg_eval_score)  AS eval_score,
               SUM(cost_usd) / SUM(successes) AS cost_per_success
        FROM v_daily_ops
        WHERE app_route IN ('semantic_synthesis', 'text_to_sql')
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    by_v = {r[0]: {"eval": r[1], "cps": r[2]} for r in rows}
    return {
        "finding": "v2_raises_quality_and_cost",
        "eval_lift": round(by_v["v2"]["eval"] - by_v["v1"]["eval"], 4),
        "cost_ratio": round(by_v["v2"]["cps"] / by_v["v1"]["cps"], 2),
        "detected": by_v["v2"]["eval"] > by_v["v1"]["eval"]
                    and by_v["v2"]["cps"] > by_v["v1"]["cps"] * 1.3,
    }


def cost_anomaly_days(con: duckdb.DuckDBPyConnection, z_threshold: float = 3.0) -> dict:
    """Waste anomalies: days whose FAILURE count is a >=z outlier.

    Why failures, not raw cost: a retry storm spends real money while its
    failed calls emit zero output tokens, so daily COST can look normal even
    when a quarter of traffic burned twice. 'Spend without successes' is the
    waste signal; raw-cost z-scores are reported alongside for context.
    Partial edge days (first/last, < half the median call volume) are
    excluded — they are calendar artifacts, not anomalies."""
    rows = con.execute("""
        WITH daily AS (
            SELECT span_date, SUM(cost_usd) AS cost,
                   SUM(calls) AS calls,
                   SUM(calls - successes) AS failures
            FROM v_daily_ops GROUP BY 1
        ),
        full_days AS (
            SELECT * FROM daily
            WHERE calls >= 0.5 * (SELECT MEDIAN(calls) FROM daily)
        ),
        stats AS (
            SELECT AVG(failures) AS f_mu, STDDEV(failures) AS f_sd,
                   AVG(cost) AS c_mu, STDDEV(cost) AS c_sd
            FROM full_days
        )
        SELECT d.span_date, d.cost, d.failures,
               (d.failures - s.f_mu) / NULLIF(s.f_sd, 0) AS failure_z,
               (d.cost - s.c_mu)     / NULLIF(s.c_sd, 0) AS cost_z
        FROM full_days d, stats s
        WHERE (d.failures - s.f_mu) / NULLIF(s.f_sd, 0) >= ?
        ORDER BY d.span_date
    """, [z_threshold]).fetchall()
    return {
        "finding": "waste_anomaly_days",
        "days": [{"date": str(r[0]), "cost": round(r[1], 2), "failures": r[2],
                  "failure_z": round(r[3], 1), "cost_z": round(r[4] or 0, 1)}
                 for r in rows],
        "detected": len(rows) >= 1 and all(r[2] > 0 for r in rows),
    }


def latency_drift(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute("""
        WITH weekly AS (
            SELECT app_route,
                   date_trunc('week', span_date) AS week,
                   AVG(latency_p50_ms)           AS p50
            FROM v_daily_ops GROUP BY 1, 2
        ),
        firstlast AS (
            SELECT app_route,
                   FIRST(p50 ORDER BY week)  AS first_p50,
                   LAST(p50 ORDER BY week)   AS last_p50
            FROM weekly GROUP BY 1
        )
        SELECT app_route, first_p50, last_p50, last_p50 / first_p50 AS ratio
        FROM firstlast ORDER BY ratio DESC
    """).fetchall()
    worst = rows[0]
    return {
        "finding": "latency_drift_by_route",
        "worst_route": worst[0],
        "p50_ratio_first_to_last_week": round(worst[3], 2),
        "detected": worst[0] == "semantic_synthesis" and worst[3] > 1.25,
    }

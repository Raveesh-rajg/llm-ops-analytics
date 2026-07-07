"""TokenLedger dashboard.

Run:  PYTHONPATH=src streamlit run dashboard/app.py
(builds the trace warehouse on first run if missing)
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb
import pandas as pd
import streamlit as st

from tokenledger import warehouse, findings

DATA = ROOT / "data"
DB = DATA / "ledger.duckdb"

st.set_page_config(page_title="TokenLedger", layout="wide")
st.title("TokenLedger — LLM cost, quality & latency")

if not DB.exists():
    traces = sorted(DATA.glob("traces_*.jsonl"))
    if not traces:
        from tokenledger.generate_traces import generate_scaled
        generate_scaled(DATA / "traces_scaled.jsonl")
        traces = [DATA / "traces_scaled.jsonl"]
    warehouse.build(traces, DB).close()

con = duckdb.connect(str(DB), read_only=True)

# ---- headline ----
n, cost, cps, fail = con.execute("""
    SELECT COUNT(*), SUM(cost_usd),
           SUM(cost_usd)/SUM(CASE WHEN app_success THEN 1 ELSE 0 END),
           SUM(CASE WHEN NOT app_success THEN 1 ELSE 0 END)
    FROM v_spans_costed""").fetchone()
c1, c2, c3, c4 = st.columns(4)
c1.metric("LLM calls (30d)", f"{n:,}")
c2.metric("Est. spend", f"${cost:,.2f}")
c3.metric("Cost per SUCCESSFUL answer", f"${cps:.4f}",
          help="Retries and failures spend tokens without producing answers — "
               "cost per call hides them; this denominator doesn't.")
c4.metric("Failed calls", f"{fail:,}")

# ---- daily spend by route ----
st.subheader("Daily spend by route")
daily = con.execute("""
    SELECT span_date, app_route, SUM(cost_usd) AS cost
    FROM v_daily_ops GROUP BY 1,2 ORDER BY 1""").df()
st.area_chart(daily.pivot_table(index="span_date", columns="app_route",
                                values="cost", aggfunc="sum"))

# ---- automated findings ----
st.subheader("Automated findings (detected, not narrated)")
f1 = findings.v2_cost_quality_tradeoff(con)
f2 = findings.cost_anomaly_days(con)
f3 = findings.latency_drift(con)
if f1["detected"]:
    st.warning(
        f"**Prompt v2 trade-off** — eval score +{f1['eval_lift']:.1%} but cost "
        f"per success x{f1['cost_ratio']}. Decision needed: is "
        f"{f1['eval_lift']:.1%} quality worth {f1['cost_ratio']}x unit cost? "
        "See the version table below before rolling v2 to 100%.")
if f2["detected"]:
    d = f2["days"][0]
    st.error(
        f"**Waste anomaly {d['date']}** — {d['failures']} failed calls "
        f"(failure z={d['failure_z']}) while raw cost looked normal "
        f"(cost z={d['cost_z']}). Retry storms hide in cost charts; they "
        "show in the failure denominator.")
if f3["detected"]:
    st.warning(
        f"**Latency drift** — {f3['worst_route']} p50 grew "
        f"x{f3['p50_ratio_first_to_last_week']} over the window. Check "
        "context growth: token counts on that route trend with latency.")

# ---- version tradeoff table ----
st.subheader("Prompt version economics")
vt = con.execute("""
    SELECT app_route, app_prompt_version, calls,
           ROUND(avg_eval_score, 3)        AS eval_score,
           ROUND(avg_output_tokens, 0)     AS avg_out_tokens,
           ROUND(cost_per_success, 5)      AS cost_per_success,
           ROUND(eval_score_per_dollar, 0) AS eval_per_dollar
    FROM v_version_tradeoff ORDER BY app_route, app_prompt_version""").df()
st.dataframe(vt, use_container_width=True)
st.caption(
    "eval_per_dollar falls under v2 on synthesis and text-to-SQL — quality "
    "went up, efficiency went down. This table is the rollout decision.")

# ---- latency ----
st.subheader("Latency percentiles by route")
lat = con.execute("""
    SELECT span_date, app_route, AVG(latency_p95_ms) AS p95
    FROM v_daily_ops GROUP BY 1,2 ORDER BY 1""").df()
st.line_chart(lat.pivot_table(index="span_date", columns="app_route", values="p95"))

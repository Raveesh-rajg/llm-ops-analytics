import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from tokenledger.tracing import TracedLLM, JsonlSink, cost_usd, estimate_tokens
from tokenledger.generate_traces import generate_scaled
from tokenledger import warehouse, findings

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"


class Dummy:
    def complete(self, system, user):
        return "answer " * 10


class Exploder:
    def complete(self, system, user):
        raise TimeoutError("upstream")


class TestTracing:
    def test_span_emitted_on_success(self, tmp_path):
        sink = JsonlSink(tmp_path / "t.jsonl")
        TracedLLM(Dummy(), sink, model="mock-model").complete("sys", "user q")
        lines = (tmp_path / "t.jsonl").read_text().splitlines()
        assert len(lines) == 1 and '"app_success": true' in lines[0]

    def test_span_emitted_even_on_failure(self, tmp_path):
        """The FinOps property: failed calls still spent tokens and MUST be
        traced — untraced failures are invisible spend."""
        sink = JsonlSink(tmp_path / "t.jsonl")
        with pytest.raises(TimeoutError):
            TracedLLM(Exploder(), sink, model="mock-model").complete("s", "u")
        line = (tmp_path / "t.jsonl").read_text()
        assert '"app_success": false' in line and "TimeoutError" in line

    def test_cost_math(self):
        # haiku pricing: $1/1M in, $5/1M out
        assert cost_usd("claude-haiku-4-5", 1_000_000, 0) == pytest.approx(1.0)
        assert cost_usd("claude-haiku-4-5", 0, 1_000_000) == pytest.approx(5.0)

    def test_token_estimate_floor(self):
        assert estimate_tokens("") == 1


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    d = tmp_path_factory.mktemp("traces")
    generate_scaled(d / "scaled.jsonl", days=30, calls_per_day=400, seed=7)
    return warehouse.build([d / "scaled.jsonl"], d / "ledger.duckdb")


class TestWarehouse:
    def test_all_spans_costed(self, con):
        uncosted = con.execute(
            "SELECT COUNT(*) FROM v_spans_costed WHERE cost_usd IS NULL").fetchone()[0]
        assert uncosted == 0

    def test_cost_per_success_exceeds_cost_per_call_when_failures_exist(self, con):
        row = con.execute("""
            SELECT SUM(cost_usd)/SUM(calls), SUM(cost_usd)/SUM(successes)
            FROM v_daily_ops""").fetchone()
        assert row[1] > row[0]  # the denominator argument, as an assertion


class TestPlantedFindings:
    def test_v2_tradeoff_detected(self, con):
        f = findings.v2_cost_quality_tradeoff(con)
        assert f["detected"], f
        assert f["eval_lift"] > 0.03 and f["cost_ratio"] > 1.3

    def test_retry_storm_day_flagged(self, con):
        f = findings.cost_anomaly_days(con)
        assert f["detected"], f

    def test_latency_drift_isolated_to_synthesis(self, con):
        f = findings.latency_drift(con)
        assert f["detected"], f

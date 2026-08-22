"""The lifecycle fixtures must carry the key sets real documents carry.

Key sets recorded 2026-08-22 from a read-only query against the production
RethinkDB (ids 138148 running, 102463 finished, 108477 stopped, 101666 error)
by scripts/dev_fetch_backtest_fixture.py.

Two corrections the live read forced on the key set the plan wrote down:

  * ``backtest_refusals`` is NOT universal. Only a run that reached the
    refusal writer carries it -- live, that is running/paused; the stopped,
    errored and finished samples have no such key at all. That is exactly the
    "absent until written" behaviour assemble() has to reproduce, so it is
    recorded here rather than papered over.
  * the stub has no ``strategy_schema`` either: the stub payload is written
    before a strategy is linked.
"""
import gzip
import json
import os

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fixtures")

STAGES = ("stub", "running", "paused", "stopped", "error", "finished")

_BASE = {
    "_last_active", "backtest_decisions", "backtest_id", "backtest_prices",
    "backtest_trades", "difficulty", "end_date",
    "granularity_sec", "id", "initial_cash", "instance_id", "logs", "pnl",
    "pnl_percent", "portfolio_value_history", "progress", "start_date",
    "status", "strategy_id", "strategy_schema", "tickers",
    "time_elapsed_seconds", "timestamp",
}
# Present only on runs that actually wrote a refusal.
_REFUSAL_STAGES = {"running", "paused"}
_STUB_ABSENT = {"backtest_decisions", "_last_active", "granularity_sec",
                "difficulty", "initial_cash", "strategy_schema"}
_FINISHED_EXTRA = {
    "cadence_mode", "code_version", "dividend_summary",
    "dual_cadence_backtest_simulation", "execution_cost_model",
    "execution_cost_model_version", "execution_promotion_eligible",
    "execution_promotion_error", "execution_provenance_complete", "fees",
    "fill_provenance", "pnl_per_stock", "pnl_percent_per_stock",
    "rejected_order_count", "slippage_cost", "spread_cost",
    "stock_price_change", "total_fees", "unfilled_order_count",
}
_STEP_ARRAYS = ("backtest_decisions", "backtest_refusals", "backtest_trades",
                "portfolio_value_history", "logs", "backtest_prices")


def load_fixture(stage: str) -> dict:
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("stage", STAGES)
def test_fixture_exists_and_is_an_object(stage):
    doc = load_fixture(stage)
    assert isinstance(doc, dict) and doc


@pytest.mark.parametrize("stage", STAGES)
def test_fixture_carries_the_base_key_set(stage):
    doc = load_fixture(stage)
    if stage == "stub":
        # The stub is _ensure_backtest_result_row's literal payload: no
        # decisions/refusals yet, and four empty arrays.
        assert set(doc) == _BASE - _STUB_ABSENT
        return
    assert _BASE <= set(doc), "missing: %r" % (_BASE - set(doc))
    assert ("backtest_refusals" in doc) == (stage in _REFUSAL_STAGES)


def test_finished_fixture_carries_the_terminal_only_keys():
    assert _FINISHED_EXTRA <= set(load_fixture("finished"))


def test_error_fixture_carries_an_error_string():
    assert isinstance(load_fixture("error")["error"], str)


def test_stopped_fixture_has_every_array_empty_except_logs():
    doc = load_fixture("stopped")
    for key in _STEP_ARRAYS:
        if key == "logs":
            assert doc[key], "stopped runs still carry their log tail"
        else:
            assert doc.get(key, []) == []


def test_stopped_fixture_carries_nexus_lookback():
    assert "nexus_lookback" in load_fixture("stopped")


def test_paused_fixture_carries_the_pause_metadata():
    doc = load_fixture("paused")
    assert doc["status"] == "paused_llm_critical"
    for key in ("pause_reason", "pause_call_site", "pause_attempts",
                "pause_bar_time", "paused_at"):
        assert key in doc


def test_progress_scalar_types_differ_across_stages():
    """The reason BacktestProgress stores a payload jsonb instead of typed
    columns: these types are load-bearing for byte-identity."""
    assert type(load_fixture("stopped")["progress"]) is int
    assert type(load_fixture("running")["progress"]) is float
    assert type(load_fixture("running")["time_elapsed_seconds"]) is int
    assert type(load_fixture("finished")["time_elapsed_seconds"]) is float


@pytest.mark.parametrize("stage", STAGES)
def test_fixtures_are_secret_free(stage):
    blob = json.dumps(load_fixture(stage)).lower()
    for marker in ("api_key", "apikey", "secret", "password", "token",
                   "pk_live", "sk_live", "bearer "):
        assert marker not in blob, "%s fixture leaks %r" % (stage, marker)


@pytest.mark.parametrize("stage", STAGES)
def test_fixtures_are_small_enough_to_commit(stage):
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    assert os.path.getsize(path) < 512 * 1024


@pytest.mark.parametrize("stage", STAGES)
def test_fixture_instance_id_is_scrubbed(stage):
    doc = load_fixture(stage)
    assert doc["instance_id"] in (None, "fixture-instance")

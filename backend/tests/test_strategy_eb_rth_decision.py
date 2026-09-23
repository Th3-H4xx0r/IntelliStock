"""Strategy EB must not DECIDE, live, outside the NYSE regular session.

2026-09-16/17, alpaca-main (REAL MONEY). The broker's session window opens at
01:00 PT (04:00 ET), so EB made its once-per-session plans pre-market, where a
fractional order cannot trade and IEX has no fresh quote. Its latches are
stamped on EMISSION and persisted right after run_once, so the plan's only
attempt was spent on orders that could never execute: the sweep sat $6,041 in
cash for five days, and the weekly rotation was missed for a week.

Re-arming on refusal/deferral (8dbe7de, 4270f68, 6f5a1f8) patched the symptom
but left the plan hanging on three things across ~9 pre-market ticks: an
in-memory re-arm that a restart undoes (the persisted cache still carries the
stamp), an order-book read that fails closed on any Alpaca outage, and a
working order anywhere on the account. One miss on any tick and the rebalance
waits a week.

EB's decision reads only COMPLETED sessions' daily closes, so it is the same
decision at 09:30 ET as at 04:40 ET — only the sizing prices are fresher. Live,
outside regular hours, the dispatcher now answers for EB with {} — exactly what
EB itself returns on any tick with nothing to do — without calling it, so no
latch is stamped and nothing is persisted until the plan can actually execute.
"""
import ast
import os
import sys
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Thursday 2026-09-24 — the first rebalance this ships for. EDT, so 13:30Z is
# the 09:30 ET open and 20:00Z the 16:00 ET close.
PRE_MARKET_FIRST_TICK = _utc(2026, 9, 24, 8, 40)       # 01:40 PT / 04:40 ET
MONITOR_TICK = _utc(2026, 9, 24, 12, 40)               # 05:40 PT / 08:40 ET
ONE_SECOND_BEFORE_OPEN = _utc(2026, 9, 24, 13, 29, 59)
THE_OPEN = _utc(2026, 9, 24, 13, 30, 0)
MID_SESSION = _utc(2026, 9, 24, 17, 0)
AFTER_THE_CLOSE = _utc(2026, 9, 24, 20, 10)            # 16:10 ET
SATURDAY_NOON = _utc(2026, 9, 26, 16, 0)
THANKSGIVING = _utc(2026, 11, 26, 16, 0)


def _extract(*names, run_mode="live", log=None):
    tree = ast.parse(open(_BROKER).read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in set(names)]
    found = {n.name for n in wanted}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"mode": run_mode, "MODE_BACKTEST": "backtest", "MODE_LIVE": "live",
          "_log": log or (lambda *a, **k: None)}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def outside(name, when, run_mode="live", log=None):
    ns = _extract("_eb_decision_outside_rth", run_mode=run_mode, log=log)
    return ns["_eb_decision_outside_rth"](name, when)


# --- the predicate ----------------------------------------------------------

@pytest.mark.parametrize("when", [
    PRE_MARKET_FIRST_TICK, MONITOR_TICK, ONE_SECOND_BEFORE_OPEN,
    AFTER_THE_CLOSE, SATURDAY_NOON, THANKSGIVING,
])
def test_live_eb_holds_its_decision_outside_regular_hours(when):
    assert outside("strategy_eb", when) is True


@pytest.mark.parametrize("when", [THE_OPEN, MID_SESSION])
def test_live_eb_decides_in_regular_hours_from_the_opening_minute(when):
    """13:30:00Z exactly is the open. The first FULL tick lands on it, and it
    must plan there, not one tick later."""
    assert outside("strategy_eb", when) is False


def test_every_eb_lane_spelling_is_held():
    for name in ("strategy_eb", "strategyeb", "StrategyEb", " Strategy_EB "):
        assert outside(name, PRE_MARKET_FIRST_TICK) is True, name


@pytest.mark.parametrize("name", ["graph_nexus_analysis", "strategy_x",
                                  "index_core_tilt", "", None])
def test_other_strategies_are_untouched(name):
    assert outside(name, PRE_MARKET_FIRST_TICK) is False


def test_backtests_are_untouched():
    """The engine's decisions are the measured strategy. This is a LIVE
    execution-timing rule; a backtest must never see it."""
    assert outside("strategy_eb", PRE_MARKET_FIRST_TICK,
                   run_mode="backtest") is False


def test_a_naive_tick_time_is_read_as_utc():
    assert outside("strategy_eb", datetime(2026, 9, 24, 8, 40)) is True
    assert outside("strategy_eb", datetime(2026, 9, 24, 13, 30)) is False


@pytest.mark.parametrize("when", [None, "2026-09-24T08:40:00Z", 1790152800])
def test_an_unreadable_tick_time_keeps_the_existing_behaviour(when):
    """Fail toward the path that already trades (pre-market plan + deferral
    re-arm), never toward an EB that silently stops deciding."""
    assert outside("strategy_eb", when) is False


def test_a_calendar_failure_keeps_the_existing_behaviour(monkeypatch):
    import live_calendar

    def boom(_now):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(live_calendar, "is_nyse_open", boom)
    lines = []
    assert outside("strategy_eb", PRE_MARKET_FIRST_TICK,
                   log=lambda m, c="white": lines.append((m, c))) is False
    assert any("calendar" in m for m, _c in lines), lines


# --- through the dispatcher -------------------------------------------------

#: Everything `run_run_once_strategies` reads at module scope — the same list
#: test_strategy_x_broker_coexistence.py keeps, for the same reason: the
#: per-strategy body is wrapped in a blanket `except Exception`, so a missing
#: name would read as "EB was not called" and pass the pre-market test for the
#: wrong reason. The in-RTH test is what proves the harness is complete.
_DISPATCHER_NAMES = {
    "run_run_once_strategies",
    "_residual_sleeve_config",
    "_eb_live_portfolio_view",
    "_eb_decision_outside_rth",
    "_merged_strategy_settings",
    "_llm_resolution_is_fatal",
    "_resolve_nexus_runtime_identity",
    "_run_graph_nexus_with_point_in_time",
}


class _RecordingEb:
    calls = []

    def run_once(self, *args, **kwargs):
        _RecordingEb.calls.append(args[2])
        kwargs["strategy_cache"]["_eb_last_rebalance_session"] = "2026-09-23"
        return {"GLD": -1, "_nexus_position_sizes": {
            "GLD": {"sell_fraction": 0.3}}}


def _dispatch(when, run_mode="live"):
    tree = ast.parse(open(_BROKER).read())
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in _DISPATCHER_NAMES]
    assert {n.name for n in nodes} == _DISPATCHER_NAMES
    ns = {
        "MODE_BACKTEST": "backtest", "MODE_LIVE": "live", "mode": run_mode,
        "os": os, "_strategy_cache": {},
        "_strategy_class_cache": {"strategy_eb": _RecordingEb},
        "_log": lambda *a, **k: None,
        "_load_strategy_class": lambda name: _RecordingEb,
        "_apply_regime_profile": lambda config, regime: dict(config),
        "_apply_live_overrides": lambda config: dict(config),
        "_instance_kind_and_crypto_config": lambda: ("stock", {}),
        "instance_id": "alpaca-main", "backtest_row_id": None,
        "telemetry_llm_call_context": lambda **kwargs: nullcontext(),
        "get_conn": lambda: pytest.fail("model resolution should not run"),
        "resolve_model_refs_in_config": lambda conn, config: config,
        "_partial_trim_syms": lambda sizes: set(),
        "_chop_ret20_cfg": lambda config: None,
    }
    for node in nodes:
        exec(compile(ast.Module([node], []), _BROKER, "exec"), ns)
    _RecordingEb.calls = []
    caches = {"strategy_eb": {"_strategy_eb_trend_state": "OFF"}}
    results = ns["run_run_once_strategies"](
        [{"strategy": "strategy_eb", "weight": 1.0,
          "config": {"strategy_eb_enabled": True}}],
        ["GLD"], {"GLD": 398.48}, when,
        data={}, portfolio_emulator=object(), strategy_caches=caches,
        mode="IDLE",
    )
    return results, caches, list(_RecordingEb.calls)


def test_pre_market_live_eb_is_not_called_and_stamps_nothing():
    results, caches, calls = _dispatch(PRE_MARKET_FIRST_TICK)
    assert calls == []
    assert caches["strategy_eb"] == {"_strategy_eb_trend_state": "OFF"}, (
        "a latch stamped pre-market is persisted right after run_once and a "
        "restart before the open restores it")
    assert len(results) == 1
    _spec, scores, _reasons, _meta = results[0]
    assert scores == {}


def test_at_the_open_live_eb_decides_and_its_plan_flows_through():
    results, caches, calls = _dispatch(THE_OPEN)
    assert calls == [THE_OPEN]
    assert caches["strategy_eb"]["_eb_last_rebalance_session"] == "2026-09-23"
    _spec, scores, _reasons, meta = results[0]
    assert scores == {"GLD": -1}
    assert meta["_nexus_position_sizes"]["GLD"]["sell_fraction"] == 0.3


def test_a_pre_market_backtest_tick_still_calls_eb():
    _results, _caches, calls = _dispatch(PRE_MARKET_FIRST_TICK,
                                         run_mode="backtest")
    assert calls == [PRE_MARKET_FIRST_TICK]


def test_the_run_once_call_site_consults_the_hold():
    """A source assertion: the call site is inline in a long function, and a
    helper defined but never called is a silent no-op live."""
    source = open(_BROKER).read()
    body = source.split("def run_run_once_strategies(", 1)[1].split("\ndef ", 1)[0]
    hold = body.find("_eb_decision_outside_rth(name, current_time)")
    call = body.find("raw = instance.run_once(")
    assert hold != -1 and call != -1 and hold < call

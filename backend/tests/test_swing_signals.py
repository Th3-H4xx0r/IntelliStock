"""Swing signal parity with ST paper_trader.py, fix 4's sector semantics, and
fix 11 (the bear counter advances once per session, not per run)."""
import importlib.util
import os
import sys
import types

import numpy as np

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import signals  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")
KEYS = ("close", "volume", "rsi", "rsi_prev", "macd_hist", "macd_hist_prev",
        "macd_hist_prev2", "sma200", "vol_avg20", "adx")


def _st():
    spec = importlib.util.spec_from_file_location(
        "_swing_st_paper", os.path.join(_ST_DIR, "paper_trader_pure.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _random_ind(rng):
    ind = {
        "close": float(rng.uniform(50, 150)), "volume": float(rng.uniform(1e5, 3e6)),
        "rsi": float(rng.uniform(20, 80)), "rsi_prev": float(rng.uniform(20, 80)),
        "macd_hist": float(rng.normal()), "macd_hist_prev": float(rng.normal()),
        "macd_hist_prev2": float(rng.normal()), "sma200": float(rng.uniform(50, 150)),
        "vol_avg20": float(rng.uniform(1e5, 3e6)), "adx": float(rng.uniform(5, 40)),
    }
    if rng.random() < 0.1:
        ind[KEYS[int(rng.integers(0, len(KEYS)))]] = None
    return ind


def test_entry_signal_matches_st():
    st, rng = _st(), np.random.default_rng(0)
    hits = 0
    for _ in range(3000):
        ind = _random_ind(rng)
        ours = signals.entry_signal(ind)
        assert ours == st.entry_signal(ind)
        hits += ours
    assert hits > 0, "fixture never produced an entry; widen the ranges"


def test_exit_signal_matches_st():
    st, rng = _st(), np.random.default_rng(1)
    for _ in range(3000):
        ind = _random_ind(rng)
        if ind["close"] is None:
            continue
        ind["rsi_prev"] = float(rng.uniform(60, 75)) if rng.random() < 0.5 else ind["rsi_prev"]
        entry = float(ind["close"] * rng.uniform(0.85, 1.15))
        assert signals.exit_signal(ind, entry) == st.exit_signal(ind, entry)


def test_config_thresholds_change_the_verdict():
    ind = {"close": 100.0, "volume": 2e6, "rsi": 55.0, "rsi_prev": 50.0,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.0,
           "sma200": 90.0, "vol_avg20": 1e6, "adx": 20.0}
    assert signals.entry_signal(ind) is False
    assert signals.entry_signal(ind, rsi_oversold=60) is True
    kw = signals.entry_kwargs({"rsi_entry_max": 60, "adx_min": 15,
                               "profit_target": 0.09, "stop_loss": 0.06})
    assert signals.entry_signal(ind, **kw) is True
    assert signals.exit_kwargs({"profit_target": 0.2, "stop_loss": 0.1,
                                "rsi_overbought": 80}) == {
        "profit_target": 0.2, "stop_loss": 0.1, "rsi_overbought": 80}


def test_sector_conflict_matches_st():
    st = _st()
    info = {"AAPL": "Technology", "MSFT": "Technology", "JPM": "Financial Services",
            "XOM": "Energy", "ZZZ": None}

    class _Ticker:
        def __init__(self, symbol):
            self.info = ({"sector": info[symbol]} if info.get(symbol) else {})

    st.yf = types.SimpleNamespace(Ticker=_Ticker)
    for sym in info:
        for active in ({"MSFT"}, {"JPM"}, {"XOM", "MSFT"}, set(), {"SPY"}):
            assert signals.sector_conflict(
                sym, active, sector_of=st.get_symbol_sector) == st.sector_conflict(sym, active)


def test_a_higher_sector_cap_admits_a_second_name():
    sector = {"AAPL": "technology", "MSFT": "technology", "NVDA": "technology"}.get
    assert signals.sector_conflict("NVDA", {"AAPL"}, sector_of=sector,
                                   max_per_sector=2) is None
    assert signals.sector_conflict("NVDA", {"AAPL", "MSFT"}, sector_of=sector,
                                   max_per_sector=2) in {"AAPL", "MSFT"}


def test_unknown_sector_is_never_a_conflict():
    assert signals.sector_conflict("X", {"Y"}, sector_of=lambda s: "unknown") is None


def test_bear_counter_matches_st_with_one_run_per_session(tmp_path):
    st = _st()
    st.REGIME_TRACKER_FILE = str(tmp_path / "regime_tracker.json")
    state = None
    for i, ok in enumerate([False, False, True, False, False, False]):
        theirs = st.update_regime_tracker(ok)
        state = signals.update_regime_tracker(state, ok, f"2026-06-0{i + 1}")
        assert state["blocked_days"] == theirs


def test_fix_11_a_second_tick_in_the_same_session_does_not_count(tmp_path):
    st = _st()
    st.REGIME_TRACKER_FILE = str(tmp_path / "regime_tracker.json")
    st.update_regime_tracker(False)
    assert st.update_regime_tracker(False) == 2      # ST counted runs
    state = signals.update_regime_tracker(None, False, "2026-06-01")
    state = signals.update_regime_tracker(state, False, "2026-06-01")
    assert (state["session"], state["blocked_days"]) == ("2026-06-01", 1)
    state = signals.update_regime_tracker(state, False, "2026-06-02")
    assert state["blocked_days"] == 2
    assert signals.update_regime_tracker(state, True, "2026-06-03")["blocked_days"] == 0


def test_the_bear_counter_follows_a_sessions_latest_verdict():
    # G1 minor 1: a transient VIX failure on the first evaluation of a session
    # must not freeze a false bear-mode count for the whole session.
    state = {"session": "2026-06-01", "blocked_days": 8}
    state = signals.update_regime_tracker(state, False, "2026-06-02")   # transient block
    assert state["blocked_days"] == 9
    state = signals.update_regime_tracker(state, True, "2026-06-02")    # recovered rerun
    assert state["blocked_days"] == 0
    state = signals.update_regime_tracker(state, False, "2026-06-02")   # blocked again
    assert state["blocked_days"] == 9                                   # still once a session
    assert signals.update_regime_tracker(state, False, "2026-06-03")["blocked_days"] == 10
    # The other way round: recovered first, then blocked, in one session.
    state = signals.update_regime_tracker({"session": "2026-06-01", "blocked_days": 8},
                                          True, "2026-06-02")
    assert signals.update_regime_tracker(state, False, "2026-06-02")["blocked_days"] == 9


def test_select_entry_universe_follows_paper_trader_600_626():
    live, defensive = ["AAA", "BBB"], ["XLP", "GLD"]
    kw = {"bear_regime_days": 10, "live_universe": live, "defensive_universe": defensive}
    assert signals.select_entry_universe(True, 0, **kw) == (live, "regime_ok")
    assert signals.select_entry_universe(False, 9, **kw) == (None, "blocked")
    assert signals.select_entry_universe(False, 10, **kw) == (defensive, "bear_mode")

"""Operator logging for the swing lane: decisions unchanged, the key lines
present, and the volume bounded.

The fixed backtest (swing_logging_fixture) replays 40 NYSE sessions through
the real indicator code; its payloads were recorded from the lane BEFORE the
logging rework (fixtures/swing_logging_golden.json), so any decision change
fails here.
"""
from __future__ import annotations

import os
import random
import re
import sys
from datetime import date, datetime, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import swing_logging_fixture as fx  # noqa: E402
from swing_trader import backtest_bars, signals  # noqa: E402
from swing_trader.constants import SWING_DEFAULTS  # noqa: E402


@pytest.fixture(scope="module")
def replay():
    from db.fake import FakeStore
    decisions, lines = fx.run_fixture(fx.load(), FakeStore())
    return decisions, lines


def test_every_decision_is_byte_identical_to_the_pre_logging_golden(replay):
    decisions, _lines = replay
    with open(fx.GOLDEN) as fh:
        assert fx.canonical(decisions) + "\n" == fh.read()


def test_the_config_banner_shows_exactly_once_per_run(replay):
    _decisions, lines = replay
    banners = [m for _c, m in lines if "| CONFIG |" in m]
    assert len(banners) == 1
    assert "granularity 86400s" in banners[0] and "AI gate SKIPPED in backtests" in banners[0]
    assert "stop -6%" in banners[0] and "target +9%" in banners[0]


def test_each_session_logs_its_regime_and_signal_funnel(replay):
    _decisions, lines = replay
    funnels = [m for _c, m in lines if "| funnel over" in m]
    regimes = [m for _c, m in lines if re.search(r"\| regime (ACTIVE|BLOCKED) \| SPY", m)]
    assert len(regimes) == fx.N_SESSIONS
    assert funnels and all(stage in funnels[0] for stage in (
        "universe 34", "above_sma", "rsi_signal", "macd_improving", "vol_above_avg",
        "trend_confirmed", "PASSED"))
    assert any("bear mode ON" in m for m in regimes)
    assert any("funnel over defensive ETFs" in m for m in funnels)
    assert any("no entry scan — regime blocked" in m for _c, m in lines)


def test_entries_exits_and_fills_are_green_with_their_numbers(replay):
    _decisions, lines = replay
    entries = [(c, m) for c, m in lines if "-> ENTER" in m]
    assert entries and all(c == "green" for c, _m in entries)
    assert re.search(r"RSI [\d.]+ \(prev [\d.]+\) \| MACD hist .* ADX .* vol [\d.]+x .* close "
                     r"[\d.]+ -> ENTER \d+ sh .* stop [\d.]+, target [\d.]+, cash \$", entries[0][1])
    exits = [m for c, m in lines if c == "green" and "| EXIT " in m]
    assert any(re.search(r"EXIT S29 — close at or above the target \(\+9%\) \| entry [\d.]+ -> "
                         r"close [\d.]+, P&L \+[\d.]+%", m) for m in exits)
    assert any("bracket stop leg (stop_loss) filled" in m and "P&L -" in m for m in exits)
    skips = [(c, m) for c, m in lines if "-> SKIP:" in m]
    assert skips and all(c == "yellow" for c, _m in skips)


def _ind(close):
    return {"close": close, "volume": 2e6, "rsi": 45.0, "rsi_prev": 40.0, "macd_hist": 0.2,
            "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05, "sma200": close * 0.9,
            "vol_avg20": 1e6, "adx": 20.0}


class Emu(fx.FillEmulator):
    pass


def test_one_session_over_a_500_symbol_universe_stays_under_40_lines(store, monkeypatch):
    """Every one of 500 members passes the signal, 8 names are held and 4 of
    them exit: 3 summary lines, 4 exits, 4 entries, 10 named skips, one
    overflow line and the decided line -- never a line per symbol."""
    mod = fx.load()
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(str(msg)))
    monkeypatch.setattr(mod, "store", store)
    names = [f"N{i:03d}" for i in range(500)]
    held = names[:8]
    store.insert("SwingMacroDaily", {"id": "VIX|2026-06-01", "series": "VIX",
                                     "date": "2026-06-01", "close": 15.0, "source": "cboe"},
                 conflict="replace")
    store.insert("SwingIndexMembership", {"id": "SPX|2026-01-02", "index": "SPX",
                                          "date": "2026-01-02", "members": names},
                 conflict="replace")
    ind = {"SPY": dict(_ind(450.0), rsi=60.0, sma200=400.0), **{s: _ind(50.0) for s in names}}
    for s in held[:4]:
        ind[s] = dict(_ind(60.0), rsi=72.0, rsi_prev=68.0)         # RSI-70 cross: exit
    monkeypatch.setattr(mod, "swing_indicators", lambda frames, c: ind)
    emu = Emu(cash=50_000.0)
    emu.positions = {s: 100 for s in held}
    emu.trades = [{"action": "buy", "ticker": s, "price": 50.0} for s in held]
    cache = {}
    mod.StrategySwing().run_once(sorted(ind), {s: v["close"] for s, v in ind.items()},
                                 datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
                                 dict(SWING_DEFAULTS, strategy_swing_enabled=True, max_per_sector=8),
                                 {}, data={s: [] for s in ind}, portfolio_emulator=emu,
                                 strategy_cache=cache)
    session = [m for m in lines if "| CONFIG |" not in m]
    assert len(session) <= 40, "\n".join(session)
    assert sum("-> ENTER" in m for m in session) == 4
    assert sum("| EXIT " in m for m in session) == 4
    assert sum("-> SKIP:" in m for m in session) == 10
    assert any("more candidate(s) skipped: no slot" in m for m in session)


def test_the_funnel_passes_exactly_the_names_entry_signal_passes():
    rng = random.Random(7)
    ind = {}
    for k in range(400):
        ind[f"S{k}"] = {"close": rng.uniform(10, 20), "sma200": rng.uniform(10, 20),
                        "rsi": rng.uniform(20, 80), "rsi_prev": rng.uniform(20, 80),
                        "macd_hist": rng.uniform(-1, 1), "macd_hist_prev": rng.uniform(-1, 1),
                        "volume": rng.uniform(0, 2), "vol_avg20": 1.0,
                        "adx": rng.uniform(5, 40), "macd_hist_prev2": 0.0}
    ind["S1"]["rsi"] = None
    kw = signals.entry_kwargs(SWING_DEFAULTS)
    held = {"S2", "S3"}
    universe = sorted(ind) + ["MISSING"]
    counts = signals.entry_funnel(universe, ind, exclude=held, **kw)
    passed = [s for s in universe if s not in held and s in ind and signals.entry_signal(ind[s], **kw)]
    assert counts["passed"] == len(passed) > 0
    assert counts["universe"] == 401 and counts["not_held"] == 399
    assert counts["has_indicators"] == 397
    stages = [counts[k] for k in signals.FUNNEL_STAGES]
    assert stages == sorted(stages, reverse=True)


class _Logs(list):
    def __call__(self, message, color="white"):
        self.append((color, str(message)))


class _Resp:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status_code, self.headers = body or {}, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.body


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.25
        return self.now


def test_bars_progress_every_10_symbols_with_rate_eta_and_cache_counts():
    logs, calls = _Logs(), []

    def http(url, *, headers, params, timeout):
        calls.append(url)
        if len(calls) == 3:
            return _Resp(status=429, headers={"Retry-After": "2"})
        if "/B07/" in url:
            return _Resp(status=500)
        return _Resp({"bars": [{"t": "2025-01-02T05:00:00Z", "c": 1.0}]})

    symbols = [f"B{i:02d}" for i in range(25)]
    out = backtest_bars.fetch_daily_bars(symbols, date(2025, 1, 2), date(2025, 1, 3), key="k",
                                         secret="s", feed="iex", http_get=http, cached=False,
                                         sleep=lambda s: None, log=logs, clock=_Clock())
    assert len(out) == 24
    text = [m for _c, m in logs]
    progress = [m for m in text if re.search(r"\d+/25 symbols, [\d.]+/s, ETA \d+", m)]
    assert [m.split()[1] for m in progress] == ["10/25", "20/25", "25/25"]
    assert "cache hits 0, network 10, 429 back-offs 1 (2s)" in progress[0]
    assert any("bars phase start: 25 symbols" in m for m in text)
    done = [m for m in text if "bars phase done" in m]
    assert len(done) == 1 and "24/25 symbols with bars" in done[0] and "1 with none (B07)" in done[0]
    assert any(c == "yellow" and "1 chunk(s) failed" in m and "B07" in m for c, m in logs)
    assert any(c == "yellow" and "429 back-off" in m for c, m in logs)


def test_the_eta_reads_minutes_and_seconds():
    assert backtest_bars._duration(160) == "2m40s"
    assert backtest_bars._duration(45) == "45s"
    assert backtest_bars._duration(3720) == "1h02m"

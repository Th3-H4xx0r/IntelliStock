"""A fixed, seeded backtest of the swing lane, for the logging tests.

run_fixture() drives StrategySwing session by session over synthetic daily
bars through the REAL indicator code, on a small emulator that fills each
decision at the next session's open and fires the -6%/+9% bracket legs on
the session's bar. It returns every session's emitted payload and every log
line. The payloads are pinned in fixtures/swing_logging_golden.json,
recorded from the lane before its logging was reworked: logging must leave
every decision byte-identical.

Regenerate (only when a decision change is intended):
    python3 backend/tests/swing_logging_fixture.py --write
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                      "swing_logging_golden.json")
PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
NAMES = [f"S{i:02d}" for i in range(32)]
SECTORS = ["technology", "energy", "healthcare", "utilities", "financial_services",
           "industrials"]
DEFENSIVE = ["XLP", "XLU", "XLV", "GLD", "SHY"]
FIRST_BAR, LAST_BAR = date(2025, 3, 3), date(2026, 6, 30)
FIRST_SESSION = date(2026, 4, 1)
N_SESSIONS = 40
#: VIX above the max for these sessions: a blocked stretch that reaches
#: bear mode (bear_regime_days 3 in the fixture config).
VIX_SPIKE = (date(2026, 5, 11), date(2026, 5, 18))


def load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def sessions(lo, hi):
    import live_calendar
    import pandas as pd
    return [d.date() for d in live_calendar._CAL.sessions_in_range(pd.Timestamp(lo),
                                                                    pd.Timestamp(hi))]


def make_bars():
    """{symbol: [bar, ...]}: seeded random walks with an upward drift, so
    the entry rule fires on some sessions and the brackets on others."""
    import numpy as np
    rng = np.random.default_rng(20260925)
    days = sessions(FIRST_BAR, LAST_BAR)
    out = {}
    for k, sym in enumerate(["SPY", "QQQ"] + NAMES + DEFENSIVE):
        drift = 0.0009 if sym in ("SPY", "QQQ") else 0.0004 + 0.00002 * (k % 9)
        vol = 0.008 if sym in ("SPY", "QQQ") else 0.024
        price, bars = 50.0 + 3 * k, []
        for d in days:
            o = price
            price = max(1.0, price * (1 + drift + vol * rng.standard_normal()))
            c = price
            h = max(o, c) * (1 + abs(0.006 * rng.standard_normal()))
            low = min(o, c) * (1 - abs(0.006 * rng.standard_normal()))
            v = float(int(1e6 * float(np.exp(0.35 * rng.standard_normal()))))
            bars.append({"t": f"{d.isoformat()}T04:00:00Z", "o": round(o, 4),
                         "h": round(h, 4), "l": round(low, 4), "c": round(c, 4), "v": v})
        out[sym] = bars
    return out


def seed_store(store):
    vix = []
    for d in sessions(date(2026, 1, 2), LAST_BAR):
        close = 31.0 if VIX_SPIKE[0] <= d <= VIX_SPIKE[1] else 16.0
        vix.append({"id": f"VIX|{d}", "series": "VIX", "date": d.isoformat(),
                    "close": close, "source": "cboe"})
    store.insert("SwingMacroDaily", vix, conflict="replace")
    store.insert("SwingIndexMembership", [
        {"id": "SPX|2025-01-02", "index": "SPX", "date": "2025-01-02",
         "members": list(NAMES)}], conflict="replace")
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": SECTORS[i % len(SECTORS)], "as_of": "2026-09-24",
         "source": "yfinance"} for i, s in enumerate(NAMES)], conflict="replace")


class FillEmulator:
    """Fills a decision at the next session's open; a held name's bracket
    legs fire on that session's bar (stop first, as the engine orders them)."""

    def __init__(self, cash=100_000.0):
        self.cash = cash
        self.positions = {}          # sym -> shares
        self.brackets = {}           # sym -> (stop, target)
        self.trades = []
        self.pending = {}            # sym -> (decision, hint)

    # -- what the lane reads ------------------------------------------------
    def get_cash(self):
        return self.cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self.cash

    def get_positions(self):
        return dict(self.positions)

    def get_portfolio_value(self, prices):
        return self.cash + sum(q * float((prices or {}).get(s, 0.0))
                               for s, q in self.positions.items())

    def get_trade_history(self):
        return list(self.trades)

    def pending_execution_symbols(self):
        return tuple(sorted(self.pending))

    # -- the simulated engine -------------------------------------------------
    def _trade(self, day, action, sym, shares, price, exit_reason=None):
        t = {"timestamp": datetime(day.year, day.month, day.day, 13, 30), "action": action,
             "ticker": sym, "shares": shares, "price": round(price, 4)}
        if exit_reason:
            t["exit_reason"] = exit_reason
        self.trades.append(t)

    def open_session(self, day, bar_of):
        """The session's open fills, then its bar's bracket legs."""
        for sym, (dec, hint) in sorted(self.pending.items()):
            bar = bar_of(sym, day)
            if bar is None:
                continue
            if dec == -1 and self.positions.get(sym):
                shares = self.positions.pop(sym)
                self.brackets.pop(sym, None)
                self.cash += shares * bar["o"]
                self._trade(day, "sell", sym, shares, bar["o"])
            elif dec == 1:
                shares = int(float(hint["buy_cash"]) / bar["o"])
                if shares >= 1 and shares * bar["o"] <= self.cash:
                    self.cash -= shares * bar["o"]
                    self.positions[sym] = self.positions.get(sym, 0) + shares
                    b = hint.get("bracket") or {}
                    self.brackets[sym] = (b.get("stop_loss_price"), b.get("take_profit_price"))
                    self._trade(day, "buy", sym, shares, bar["o"])
        self.pending = {}
        for sym in sorted(self.positions):
            stop, target = self.brackets.get(sym, (None, None))
            bar = bar_of(sym, day)
            if bar is None or stop is None:
                continue
            if bar["l"] <= stop:
                price, why = min(stop, bar["o"]), "stop_loss"
            elif bar["h"] >= target:
                price, why = max(target, bar["o"]), "take_profit"
            else:
                continue
            shares = self.positions.pop(sym)
            self.brackets.pop(sym, None)
            self.cash += shares * price
            self._trade(day, "sell", sym, shares, price, exit_reason=why)

    def take(self, payload):
        for sym, dec in payload.items():
            if sym.startswith("_"):
                continue
            self.pending[sym] = (dec, (payload.get("_nexus_position_sizes") or {}).get(sym) or {})


def fixture_cfg(defaults):
    return dict(defaults, strategy_swing_enabled=True, bear_regime_days=3,
                _telemetry_backtest_id="777")


def run_fixture(mod, store, *, n_sessions=N_SESSIONS):
    """[(session, payload)] and the log lines [(color, msg)]."""
    from swing_trader.constants import SWING_DEFAULTS
    lines = []
    mod.store = store
    mod._log = lambda msg, color="white": lines.append((color, str(msg)))
    seed_store(store)
    bars = make_bars()
    by_day = {s: {b["t"][:10]: b for b in v} for s, v in bars.items()}

    def bar_of(sym, day):
        return by_day.get(sym, {}).get(day.isoformat())

    emu, cache, out = FillEmulator(), {}, []
    config = fixture_cfg(SWING_DEFAULTS)
    days = [d for d in sessions(FIRST_SESSION, LAST_BAR)][:n_sessions]
    for day in days:
        emu.open_session(day, bar_of)
        now = datetime(day.year, day.month, day.day, 13, 0)      # 09:00 ET (EDT)
        prices = {s: by_day[s][max(k for k in by_day[s] if k < day.isoformat())]["c"]
                  for s in bars}
        payload = mod.StrategySwing().run_once(
            sorted(bars), prices, now, config, {}, data=bars, portfolio_emulator=emu,
            strategy_cache=cache, time_increment="86400")
        out.append((day.isoformat(), payload))
        emu.take(payload)
    return out, lines


def canonical(decisions) -> str:
    return json.dumps(decisions, sort_keys=True, indent=1, default=str)


if __name__ == "__main__":        # pragma: no cover - golden regeneration
    from db.fake import FakeStore
    got, _lines = run_fixture(load(), FakeStore())
    text = canonical(got)
    if "--write" in sys.argv:
        with open(GOLDEN, "w") as fh:
            fh.write(text + "\n")
    trades = sum(1 for _d, p in got if any(not k.startswith("_") for k in p))
    print(f"{len(got)} sessions, {trades} with decisions")
    for d, p in got:
        dec = {k: v for k, v in p.items() if not k.startswith("_")}
        if dec:
            print(d, dec)

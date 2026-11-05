"""Market tape and per-symbol trend stats for TR prompts (spec §7.2, plan Task 5).

Point in time: a decision on NY session ``S`` sees only daily bars dated
strictly before ``S`` (``OwnBars.before``) -- a daily bar labelled ``S``
carries the 16:00 close, hours after a pre-open decision. The loader is
asked only for days before ``S`` as well.

Bars come from ``bars_loader(symbols, start, end) -> {symbol: [bar, ...]}``
(dates inclusive; bars with ``"t"`` and ``"c"``). The default reads the
swing lane's ``SwingDailyBars`` cache (``swing_trader.backtest_bars``) and
never touches the network; a caller that needs missing years filled passes
a loader over ``backtest_bars.fetch_daily_bars``.

VIX is the latest ``SwingMacroDaily`` "VIX|" close strictly before ``S``
(``swing_trader.refdata.vix_before``), or None when there is none or it is
stale.

Every number is rounded to 6 decimals and a non-finite one is None, so the
output is canonical-JSON safe and stable across runs.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from swing_trader import backtest_bars, clock, refdata

__all__ = ["market_tape", "symbol_trend_stats", "cached_daily_bars", "TAPE_SYMBOLS",
           "SECTOR_SPDRS", "MIN_SESSIONS"]

INDEXES = ("SPY", "QQQ", "IWM")
SECTOR_SPDRS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
MACRO_ETFS = ("GLD", "SLV", "USO", "CPER", "TLT", "UUP")
TAPE_SYMBOLS = INDEXES + SECTOR_SPDRS + MACRO_ETFS

#: Calendar days of bars requested: 252 sessions (52 weeks) plus slack.
LOOKBACK_DAYS = 400
#: ``symbol_trend_stats`` returns None below this many sessions.
MIN_SESSIONS = 60
WEEK, MONTH, QUARTER, YEAR = 5, 21, 63, 252


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if math.isfinite(f) else None


def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def _session(decision_time: datetime) -> date:
    if not isinstance(decision_time, datetime) or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be a tz-aware datetime, got %r"
                         % (decision_time,))
    return date.fromisoformat(clock.ny_date(decision_time))


def cached_daily_bars(symbols, start: date, end: date, *, store=None) -> dict:
    """``{symbol: [bar, ...]}`` dated ``start..end`` from the SwingDailyBars
    cache (rows "SYM|YYYY"; split-adjusted rows only). No network: a year the
    cache lacks is simply absent."""
    st = _store(store)
    ids = ["%s|%d" % (s, y) for s in symbols for y in range(start.year, end.year + 1)]
    lo, hi = start.isoformat(), end.isoformat()
    out: dict = {}
    for k in range(0, len(ids), backtest_bars.READ_BATCH):
        for row in st.get_all(backtest_bars.CACHE_TABLE,
                              *ids[k:k + backtest_bars.READ_BATCH]) or []:
            if row.get("adjustment") != backtest_bars.ADJUSTMENT:
                continue
            sym = str(row.get("id") or "").partition("|")[0]
            for bar in row.get("bars") or []:
                ts = clock.as_utc(bar.get("t")) if isinstance(bar, dict) else None
                if ts is not None and lo <= ts.date().isoformat() <= hi:
                    out.setdefault(sym, []).append(bar)
    return out


def _closes(bars_by_symbol: dict, symbol: str, session: date) -> list:
    """``[(session_iso, close)]`` strictly before ``session``, oldest first,
    one per session (the last bar of a day wins); bad closes dropped."""
    own = backtest_bars.OwnBars({symbol: bars_by_symbol.get(symbol) or []})
    by_day: dict = {}
    for bar in own.before(symbol, session, LOOKBACK_DAYS):
        close = _num(bar.get("c", bar.get("close")))
        if close is None or close <= 0:
            continue
        day = clock.as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
        by_day[day.date().isoformat()] = close
    return sorted(by_day.items())


def _load(symbols, session: date, bars_loader, store) -> dict:
    loader = bars_loader or (lambda syms, a, b: cached_daily_bars(syms, a, b, store=store))
    start, end = session - timedelta(days=LOOKBACK_DAYS), session - timedelta(days=1)
    loaded = loader(list(symbols), start, end) or {}
    return {str(k).upper(): v for k, v in loaded.items()}


def _ret(closes, k):
    return _num(closes[-1] / closes[-1 - k] - 1) if len(closes) > k else None


def _sma_dist(closes, k):
    return _num(closes[-1] / (sum(closes[-k:]) / k) - 1) if len(closes) >= k else None


def _stats(series: list) -> dict | None:
    if not series:
        return None
    closes = [c for _, c in series]
    return {"last_session": series[-1][0], "last_close": closes[-1],
            "sessions": len(closes),
            "ret_1w": _ret(closes, WEEK), "ret_1m": _ret(closes, MONTH),
            "ret_3m": _ret(closes, QUARTER),
            "sma50_dist": _sma_dist(closes, 50), "sma200_dist": _sma_dist(closes, 200)}


def market_tape(decision_time: datetime, *, bars_loader=None, store=None) -> dict:
    """``{"as_of_session", "symbols": {sym: stats | None}, "vix"}`` for
    ``TAPE_SYMBOLS``; stats are 1w/1m/3m returns (5/21/63 sessions) and the
    distance of the last close from SMA50/SMA200 (None when too short)."""
    session = _session(decision_time)
    loaded = _load(TAPE_SYMBOLS, session, bars_loader, store)
    symbols = {s: _stats(_closes(loaded, s, session)) for s in TAPE_SYMBOLS}
    vix, _reason = refdata.vix_before(_store(store), session.isoformat())
    return {"as_of_session": session.isoformat(), "symbols": symbols, "vix": _num(vix)}


def symbol_trend_stats(symbol: str, decision_time: datetime, *, bars_loader=None,
                       store=None) -> dict | None:
    """The tape stats for ``symbol`` plus ``high_52w_dist`` (last close over
    the highest close of the last 252 sessions, minus 1) and
    ``max_drawdown_3m`` (worst close-to-running-peak fall over the last 63
    sessions, <= 0); None below ``MIN_SESSIONS`` sessions of bars."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    session = _session(decision_time)
    series = _closes(_load([sym], session, bars_loader, store), sym, session)
    if len(series) < MIN_SESSIONS:
        return None
    out = _stats(series)
    closes = [c for _, c in series]
    out["high_52w_dist"] = _num(closes[-1] / max(closes[-YEAR:]) - 1)
    peak, worst = 0.0, 0.0
    for c in closes[-(QUARTER + 1):]:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    out["max_drawdown_3m"] = _num(worst)
    return out

"""Market regime, ported from ST.

    paper_trader.py:478-480  spy_ok / vix_ok / regime_ok     (verbatim, config-driven)
    app.py:487-502           the blocked_reason strings of fetch_regime
    paper_trader.py:279-286  fetch_vix_close, with fetch_regime's NaN guard
                             (app.py:447-456) — ST's own later fix for the
                             same read.
Backtests do not call fetch_vix_close: they read SwingMacroDaily strictly
before the NY date (refdata.vix_before).
"""
from __future__ import annotations

import math

import yfinance as yf

from swing_trader.constants import SPY_BUFFER, VIX_FEAR_THRESHOLD


def _finite(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def regime_decision(spy_close, spy_sma200, vix_close, *, spy_buffer=SPY_BUFFER,
                    vix_max=VIX_FEAR_THRESHOLD) -> dict:
    spy_close, spy_sma200, vix_close = (_finite(spy_close), _finite(spy_sma200),
                                        _finite(vix_close))
    spy_ok     = spy_close is not None and spy_sma200 is not None and spy_close > spy_sma200 * spy_buffer
    vix_ok     = vix_close is not None and vix_close <= vix_max
    regime_ok  = spy_ok and vix_ok
    if spy_ok and vix_ok:
        blocked_reason = None
    elif not spy_ok and not vix_ok:
        blocked_reason = f"SPY below SMA200×{spy_buffer} and VIX > {vix_max:g}"
    elif not spy_ok:
        blocked_reason = f"SPY below SMA200×{spy_buffer}"
    else:
        blocked_reason = f"VIX > {vix_max}"
    return {"spy_close": spy_close, "spy_sma200": spy_sma200, "vix": vix_close,
            "spy_ok": bool(spy_ok), "vix_ok": bool(vix_ok),
            "regime_ok": bool(regime_ok), "entries_allowed": bool(regime_ok),
            "blocked_reason": blocked_reason}


def fetch_vix_close() -> float | None:
    try:
        raw = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if raw.empty:
            return None
        vix_close = raw["Close"].dropna()
        if vix_close.empty:
            return None
        val = float(vix_close.iloc[-1])
        return val if math.isfinite(val) else None
    except Exception:
        return None

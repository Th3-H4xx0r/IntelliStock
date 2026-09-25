"""Swing signals, ported from ST paper_trader.py.

    paper_trader.py:293-304  entry_signal     verbatim; thresholds lifted to kwargs
    paper_trader.py:307-326  exit_signal      verbatim; thresholds lifted to kwargs
    paper_trader.py:329-340  sector_conflict  the sector lookup is injected (a
                             stored map, spec §9 item 15) and a per-sector cap
                             other than ST's 1 is honoured
    paper_trader.py:135-149  update_regime_tracker, counted per NY SESSION
                             instead of per run (spec §9 fix 11)
    paper_trader.py:600-626  which universe the entry pass scans
"""
from __future__ import annotations

from swing_trader.constants import (
    ADX_TREND_MIN,
    MAX_PER_SECTOR,
    PROFIT_TARGET,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    STOP_LOSS,
)


def entry_signal(ind: dict, *, rsi_oversold=RSI_OVERSOLD, adx_trend_min=ADX_TREND_MIN,
                 profit_target=PROFIT_TARGET, stop_loss=STOP_LOSS) -> bool:
    if any(v is None for v in ind.values()):
        return False
    # RSI<50 + 1-bar MACD ("variant L") — only config positive in both halves of
    # the 2021-2026 S&P 500 sweep (experiments/combo_results.json, 2026-06-10).
    rsi_signal      = ind["rsi"] < rsi_oversold and ind["rsi"] > ind["rsi_prev"]
    macd_improving  = ind["macd_hist"] > ind["macd_hist_prev"]
    vol_above_avg   = ind["volume"] > ind["vol_avg20"]
    above_sma       = ind["close"] > ind["sma200"]
    trend_confirmed = ind["adx"] > adx_trend_min
    rr_ok           = (profit_target / stop_loss) >= 1.5
    return rsi_signal and macd_improving and vol_above_avg and above_sma and trend_confirmed and rr_ok


#: entry_signal's filters, by the names it gives them, in the order the
#: funnel reports them (the order an operator reads a chart in).
FUNNEL_STAGES = ("universe", "not_held", "has_indicators", "above_sma", "rsi_signal",
                 "macd_improving", "vol_above_avg", "trend_confirmed", "rr_ok", "passed")


def entry_funnel(universe, ind: dict, *, exclude=(), rsi_oversold=RSI_OVERSOLD,
                 adx_trend_min=ADX_TREND_MIN, profit_target=PROFIT_TARGET,
                 stop_loss=STOP_LOSS) -> dict:
    """LOGGING ONLY -- entry_signal decides. How many names of `universe`
    survive each of entry_signal's filters, applied cumulatively in
    FUNNEL_STAGES order: a name held or queued (`exclude`) or without
    indicators is not checked, exactly as the entry passes skip it. The
    filters AND together, so "passed" is the count entry_signal returns True
    for. A row the filters cannot evaluate counts under "errors" (the entry
    pass logs and skips it). Never raises."""
    counts = dict.fromkeys(FUNNEL_STAGES, 0)
    counts["errors"] = 0
    try:
        rr_ok = (float(profit_target) / float(stop_loss)) >= 1.5
    except (TypeError, ValueError, ZeroDivisionError):
        rr_ok = False
    exclude = set(exclude or ())
    for symbol in universe or []:
        counts["universe"] += 1
        if symbol in exclude:
            continue
        counts["not_held"] += 1
        i = (ind or {}).get(symbol)
        if not isinstance(i, dict) or not i or any(v is None for v in i.values()):
            continue
        counts["has_indicators"] += 1
        try:
            stages = (("above_sma", i["close"] > i["sma200"]),
                      ("rsi_signal", i["rsi"] < rsi_oversold and i["rsi"] > i["rsi_prev"]),
                      ("macd_improving", i["macd_hist"] > i["macd_hist_prev"]),
                      ("vol_above_avg", i["volume"] > i["vol_avg20"]),
                      ("trend_confirmed", i["adx"] > adx_trend_min),
                      ("rr_ok", rr_ok))
            stages = [(name, bool(ok)) for name, ok in stages]
        except Exception:
            counts["errors"] += 1
            continue
        for name, ok in stages:
            if not ok:
                break
            counts[name] += 1
        else:
            counts["passed"] += 1
    return counts


def exit_signal(ind: dict, entry_price: float, *, profit_target=PROFIT_TARGET,
                stop_loss=STOP_LOSS, rsi_overbought=RSI_OVERBOUGHT):
    """Returns (should_exit: bool, reason: str | None)."""
    price = ind["close"]
    pct = (price - entry_price) / entry_price

    if pct >= profit_target:
        return True, "profit_target"
    if pct <= -stop_loss:
        return True, "stop_loss"

    rsi_cross_ob = (
        ind["rsi_prev"] is not None
        and ind["rsi"] is not None
        and ind["rsi_prev"] < rsi_overbought
        and ind["rsi"] >= rsi_overbought
    )
    if rsi_cross_ob:
        return True, "rsi_overbought"

    return False, None


def sector_conflict(symbol: str, active_positions, *, sector_of,
                    max_per_sector: int = MAX_PER_SECTOR) -> str | None:
    """
    Returns the conflicting symbol if adding `symbol` would exceed MAX_PER_SECTOR
    for its sector, otherwise returns None.
    """
    candidate_sector = sector_of(symbol)
    if candidate_sector == "unknown":
        return None  # unknown sector — allow entry
    same = []
    for held in active_positions:
        if sector_of(held) == candidate_sector:
            same.append(held)
            if len(same) >= int(max_per_sector):
                return same[0]
    return None


def update_regime_tracker(state, regime_ok: bool, session: str) -> dict:
    """paper_trader.py:143-149 — `days = 0 if regime_ok else blocked + 1` — but
    keyed on the NY session: ST's cron re-ran on retries and restarts and each
    run advanced the counter (fix 11).

    fix (G1 minor 1): the state keeps the PRIOR session's count and every call
    within a session recomputes from it, so the count still moves once per
    session but the session's latest verdict wins — a transient VIX failure
    on the first evaluation no longer freezes a false bear-mode day."""
    state = dict(state or {})
    if state.get("session") == session:
        prior = state.get("prior_blocked_days")
        if prior is None:   # a state written before the fix: blocked_days = prior + 1
            prior = max(int(state.get("blocked_days") or 0) - 1, 0)
    else:
        prior = state.get("blocked_days")
    prior = int(prior or 0)
    days = 0 if regime_ok else prior + 1
    return {"session": session, "blocked_days": days, "prior_blocked_days": prior}


def select_entry_universe(regime_ok, blocked_days, *, bear_regime_days,
                          live_universe, defensive_universe):
    """paper_trader.py:602-626: the S&P list when the regime is on, the
    defensive ETFs once blocked for `bear_regime_days` sessions, else none."""
    if regime_ok:
        return list(live_universe), "regime_ok"
    if int(blocked_days) >= int(bear_regime_days):
        return list(defensive_universe), "bear_mode"
    return None, "blocked"


def entry_kwargs(cfg: dict) -> dict:
    return {"rsi_oversold": float(cfg.get("rsi_entry_max", RSI_OVERSOLD)),
            "adx_trend_min": float(cfg.get("adx_min", ADX_TREND_MIN)),
            "profit_target": float(cfg.get("profit_target", PROFIT_TARGET)),
            "stop_loss": float(cfg.get("stop_loss", STOP_LOSS))}


def exit_kwargs(cfg: dict) -> dict:
    return {"profit_target": float(cfg.get("profit_target", PROFIT_TARGET)),
            "stop_loss": float(cfg.get("stop_loss", STOP_LOSS)),
            "rsi_overbought": float(cfg.get("rsi_overbought", RSI_OVERBOUGHT))}

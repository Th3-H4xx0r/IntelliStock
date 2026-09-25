"""Wheel rules, ported from ST wheel_trader.py and app.py.

    wheel_trader.py:765-834    next_friday: Alpaca's calendar API became an
                               injected `calendar(start, end) -> set[date]`
    wheel_trader.py:839-953    get_candidates, split so the one network call
                               (earnings) runs only for names that passed every
                               technical filter; the candidate set is identical
                               (pinned by test_get_candidates_matches_st)
    wheel_trader.py:1307-1318  main()'s per-sector cap -> sector_filter
    wheel_trader.py:1220-1252  _scan_already_ran_this_week; the completion
                               marker lives in strategy_cache
Task 9 adds the order build (get_put_delta, place_put_order) and Task 10 the
position rules (check_open_wheel_positions, check_assigned_positions and
app.py's api_check_wheel_positions).

Operator-approved fixes (spec §9): 1 duplicate put, 3 the cap counts existing
puts, 6 the contract type is checked, 8 cash not margin, 9 the full chain,
10 contract fields instead of an OCC regex.
"""
from __future__ import annotations

import datetime as dt_module
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swing_trader.constants import (
    ATR_PERIOD,
    ETF_NO_EARNINGS,
    MIN_DTE,
    MIN_OPTION_PREMIUM_PCT,
    RSI_MAX,
    RSI_MIN,
    SMA50_PERIOD,
    STRIKE_ATR_MULT,
    WHEEL_MAX_PER_SECTOR,
    WHEEL_SECTOR_MAP,
    YFINANCE_FLAKY,
)
from swing_trader.indicators import _atr, _rsi, _sma

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


def next_friday(today: date, calendar, *, min_dte: int = MIN_DTE, log=None) -> str:
    """
    Return the date string (YYYY-MM-DD) of the next valid options expiry
    Friday that is at least MIN_DTE days away.

    1. Find the next Friday that is MIN_DTE+ days away
    2. Ask the calendar if that Friday is a trading day
    3. If yes — use it
    4. If no — check the Thursday of that week (exchange sometimes moves
       expiry to Thursday before a holiday Friday)
    5. If Thursday is also closed — skip to the following Friday and repeat
    Falls back to the pure date calculation if the calendar is unavailable.
    """
    log = log or _log
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    candidate = today + timedelta(days=days_ahead)

    # Enforce MIN_DTE
    if (candidate - today).days < int(min_dte):
        candidate += timedelta(days=7)

    try:
        # Fetch calendar for a 3-week window around candidate
        trading_days = calendar(candidate - timedelta(days=1),
                                candidate + timedelta(days=14))

        max_attempts = 8   # safety limit — never loop forever
        attempts     = 0
        while attempts < max_attempts:
            if candidate in trading_days:
                log(f"  [next_friday] Expiry: {candidate} (confirmed trading day via Alpaca calendar)")
                return str(candidate)

            # Friday is a holiday — check Thursday of same week
            thursday = candidate - timedelta(days=1)
            if thursday in trading_days:
                log(f"  [next_friday] {candidate} is a non-trading day — using Thursday {thursday} (holiday expiry)")
                return str(thursday)

            # Both closed — skip to next Friday
            log(f"  [next_friday] {candidate} is a non-trading day, Thursday also closed — skipping to following Friday")
            candidate += timedelta(days=7)
            attempts  += 1

        # Exhausted attempts — fall through to date-only result
        log(f"  [next_friday] WARNING: could not confirm trading day after {max_attempts} attempts — using {candidate}")
        return str(candidate)

    except Exception as exc:
        # Calendar unavailable — fall back to date calculation only
        log(f"  [next_friday] Calendar API unavailable ({exc}) — using date calculation fallback")
        return str(candidate)


def screen_technicals(raw: dict, symbols: list, *, cfg: dict | None = None,
                      log=None) -> list[dict]:
    """wheel_trader.py:839-953 without the earnings lookup and the expiry.

    Screening criteria (ALL must pass):
    1. RSI(14) between RSI_MIN and RSI_MAX — not overbought, not collapsing
    2. Close > SMA(50) — stock in uptrend (we want puts on rising stocks)
    3. ATR% > 1.5% — enough volatility to generate meaningful premium
    4. Estimated weekly premium > MIN_OPTION_PREMIUM_PCT of stock price
    6. Strike at least 1.5% OTM
    (5, no earnings within 7 days, is apply_earnings.)
    """
    cfg = cfg or {}
    log = log or _log
    rsi_min = cfg.get("rsi_min", RSI_MIN)
    rsi_max = cfg.get("rsi_max", RSI_MAX)
    sma_n = int(cfg.get("sma_trend", SMA50_PERIOD))
    atr_n = int(cfg.get("atr_period", ATR_PERIOD))
    strike_atr_mult = float(cfg.get("strike_atr_mult", STRIKE_ATR_MULT))
    min_premium_pct = float(cfg.get("min_premium_pct", MIN_OPTION_PREMIUM_PCT))
    out = []
    for symbol in symbols:
        try:
            # raw is {symbol: DataFrame} from market_data (R8-05)
            df = raw.get(symbol)
            if df is None:
                continue
            df = df.copy().dropna(subset=["Close"]).reset_index()

            if len(df) < sma_n + 5:
                log(f"  [wheel] {symbol}: insufficient data ({len(df)} bars), skipping")
                continue

            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]

            rsi_series = _rsi(close)
            sma50      = _sma(close, sma_n)
            atr_series = _atr(high, low, close, atr_n)

            last_close = float(close.iloc[-1])
            last_rsi   = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
            last_sma50 = float(sma50.iloc[-1])       if not pd.isna(sma50.iloc[-1])     else None
            last_atr   = float(atr_series.iloc[-1])  if not pd.isna(atr_series.iloc[-1]) else None

            if any(v is None for v in [last_rsi, last_sma50, last_atr]):
                log(f"  [wheel] {symbol}: indicator NaN, skipping")
                continue

            atr_pct = last_atr / last_close * 100

            # Strike selection: ATR-based OTM put
            strike_price = round(last_close - (last_atr * strike_atr_mult), 2)
            otm_pct      = (last_close - strike_price) / last_close * 100

            # Approximate weekly premium as 25% of ATR (rough Black-Scholes proxy)
            est_premium     = round(last_atr * 0.25, 2)
            est_premium_pct = est_premium / last_close * 100

            reasons_failed = []
            if not (rsi_min <= last_rsi <= rsi_max):
                reasons_failed.append(f"RSI {last_rsi:.1f} out of [{rsi_min},{rsi_max}]")
            if last_close < last_sma50:
                reasons_failed.append(f"below SMA50 (${last_close:.2f} < ${last_sma50:.2f})")
            if atr_pct < 1.5:
                reasons_failed.append(f"ATR% {atr_pct:.2f}% < 1.5%")
            if est_premium_pct < min_premium_pct * 100:
                reasons_failed.append(f"est. premium {est_premium_pct:.3f}% < {min_premium_pct*100:.2f}%")
            if otm_pct < 1.5:
                reasons_failed.append(f"strike only {otm_pct:.1f}% OTM (min 1.5%) — too close to ATM")

            if reasons_failed:
                log(f"  [wheel] {symbol}: FILTERED — {'; '.join(reasons_failed)}")
                continue

            out.append({
                "symbol":          symbol,
                "stock_price":     last_close,
                "strike_price":    strike_price,
                "otm_pct":         round(otm_pct, 2),
                "est_premium":     est_premium,
                "est_premium_pct": round(est_premium_pct, 3),
                "rsi":             round(last_rsi, 1),
                "sma50":           round(last_sma50, 2),
                "atr":             round(last_atr, 2),
                "atr_pct":         round(atr_pct, 2),
            })

        except Exception as exc:
            log(f"  [wheel] {symbol}: ERROR — {exc}")
            continue

    return out


def wheel_earnings_days(symbol: str):
    """wheel_trader.py:891-909. ETFs have no earnings calendar and three names
    were yfinance-flaky after hours in ST, so those are None without a call."""
    earnings_days = None
    try:
        if symbol.upper() in ETF_NO_EARNINGS or symbol.upper() in YFINANCE_FLAKY:
            earnings_days = None
        else:
            cal = yf.Ticker(symbol).calendar
            if cal:
                raw_dates = cal.get("Earnings Date")
                if raw_dates is not None:
                    today = dt_module.date.today()
                    items = raw_dates if hasattr(raw_dates, "__iter__") and not isinstance(raw_dates, str) else [raw_dates]
                    future = sorted(pd.Timestamp(d).date() for d in items if pd.Timestamp(d).date() >= today)
                    earnings_days = (future[0] - today).days if future else None
    except Exception:
        earnings_days = None
    return earnings_days


def apply_earnings(pre: dict, earnings_days, *, expiry: str,
                   earnings_block_days: int = 7):
    """wheel_trader.py:921-943: the earnings filter, then ST's candidate dict."""
    if earnings_days is not None and earnings_days <= int(earnings_block_days):
        return None
    return {
        "symbol":          pre["symbol"],
        "stock_price":     pre["stock_price"],
        "strike_price":    pre["strike_price"],
        "otm_pct":         pre["otm_pct"],
        "expiry":          expiry,
        "est_premium":     pre["est_premium"],
        "est_premium_pct": pre["est_premium_pct"],
        "rsi":             pre["rsi"],
        "sma50":           pre["sma50"],
        "atr":             pre["atr"],
        "atr_pct":         pre["atr_pct"],
        "earnings_days":   earnings_days,
    }


def get_candidates(raw: dict, symbols: list, *, expiry: str, earnings_days_for=None,
                   cfg: dict | None = None, log=None) -> list[dict]:
    log = log or _log
    earnings_days_for = earnings_days_for or wheel_earnings_days
    block = int((cfg or {}).get("earnings_block_days", 7))
    candidates = []
    for pre in screen_technicals(raw, symbols, cfg=cfg, log=log):
        earnings_days = earnings_days_for(pre["symbol"])
        c = apply_earnings(pre, earnings_days, expiry=expiry, earnings_block_days=block)
        if c is None:
            log(f"  [wheel] {pre['symbol']}: FILTERED — earnings in {earnings_days} days")
            continue
        candidates.append(c)
        log(f"  [wheel] {c['symbol']}: CANDIDATE  RSI {c['rsi']:.1f}  "
            f"strike ${c['strike_price']}  premium ~${c['est_premium']}/share  exp {expiry}")
    return candidates


def sector_filter(candidates: list, *, max_per_sector: int = WHEEL_MAX_PER_SECTOR,
                  sector_map: dict = WHEEL_SECTOR_MAP, log=None) -> list:
    """wheel_trader.py:1308-1318 — limit candidates per sector before AI scoring."""
    log = log or _log
    sector_counts: dict[str, int] = {}
    filtered_candidates = []
    for c in candidates:
        sector = sector_map.get(c["symbol"], "unknown")
        count  = sector_counts.get(sector, 0)
        if sector == "unknown" or count < int(max_per_sector):
            filtered_candidates.append(c)
            sector_counts[sector] = count + 1
        else:
            log(f"  [wheel] {c['symbol']}: sector cap reached ({sector}, max {max_per_sector})")
    return filtered_candidates


def scan_already_ran_this_week(marker, today: date) -> bool:
    """wheel_trader.py:1220-1252: True only if a scan COMPLETED this week, per
    the completion marker (a NY session date written after the whole scan
    finished), never because a partial scan left rows behind. Any error reading
    the marker returns False: an extra Tuesday scan is far cheaper than
    skipping the week's only successful scan."""
    if not marker:
        return False
    try:
        monday = today - timedelta(days=today.weekday())
        marker_date = datetime.strptime(str(marker).split()[0], "%Y-%m-%d").date()
        return marker_date >= monday
    except Exception:
        return False


def occ_parts(symbol):
    """(root, expiry, 'put'|'call', strike) from an OCC symbol's fixed-width
    suffix (YYMMDD + C/P + 8-digit strike ×1000), or None. Positions are read
    from Alpaca's contract fields (fix 10); this is only for WORKING ORDERS,
    whose OrderRef carries the symbol and nothing else."""
    s = str(symbol or "").strip().upper()
    if len(s) < 16:
        return None
    root, ymd, cp, strike8 = s[:-15], s[-15:-9], s[-9], s[-8:]
    if not root or not ymd.isdigit() or cp not in ("P", "C") or not strike8.isdigit():
        return None
    expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    try:
        date.fromisoformat(expiry)
    except ValueError:
        return None
    return (root, expiry, "put" if cp == "P" else "call", int(strike8) / 1000.0)

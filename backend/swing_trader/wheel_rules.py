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
    wheel_trader.py:137-227    get_put_delta -> nearby_contracts and
                               pick_put_by_delta, over the adapter's full
                               chain (fix 9) and its OptionSnapshotDTOs
    wheel_trader.py:531-702    place_put_order -> build_put_order (pure: the
                               contract, the limit ladder, the sizing) and
                               build_put_order_live (the adapter reads);
                               fixes 1, 3 and 8
    wheel_trader.py:230-321    check_open_wheel_positions -> two_x_exits and
                               btc_order (short PUTS only, fix 6)
    app.py:1259-1411           api_check_wheel_positions, the per-position
                               rules -> put_monitor_decision (contract
                               fields, fix 10; itm_pct signed, contract §9)
    wheel_trader.py:348-507    check_assigned_positions, the detection and
                               the strike pick -> covered_call_candidates,
                               dry_run_message and pick_covered_call

Operator-approved fixes (spec §9): 1 duplicate put, 3 the cap counts existing
puts, 6 the contract type is checked, 8 cash not margin, 9 the full chain,
10 contract fields instead of an OCC regex.
"""
from __future__ import annotations

import datetime as dt_module
import math
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swing_trader.constants import (
    ATR_PERIOD,
    AUTO_CLOSE_DTE2_PCT,
    AUTO_CLOSE_ITM_PCT,
    ETF_NO_EARNINGS,
    LIMIT_BID_MIN,
    LIMIT_BID_MULT,
    LIMIT_CLOSE_MULT,
    MAX_COLLATERAL_PCT,
    MIN_DTE,
    MIN_OPTION_PREMIUM_PCT,
    RSI_MAX,
    RSI_MIN,
    SMA50_PERIOD,
    STRIKE_ATR_MULT,
    TARGET_DELTA,
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
    Friday that is at least MIN_DTE days away — except on the holiday branch
    below: the Thursday before a holiday Friday is one day short, so called
    on a Friday before a holiday Friday this returns a 6-DTE expiry. ST
    behaves the same (verbatim, parity-pinned by
    test_next_friday_matches_st_across_a_good_friday); a wheel approval
    recomputes the expiry through here (approvals, fix 2).

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


# -- wheel_trader.py:137-227 get_put_delta, over the FULL chain (fix 9) ------

def nearby_contracts(contracts, target_strike: float) -> list:
    """wheel_trader.py:172-180: strikes within 15% of the target keep the
    snapshot request small; none nearby -> the whole chain."""
    nearby = [
        c for c in contracts
        if abs(float(c.strike) - target_strike) / target_strike <= 0.15
    ]
    if not nearby:
        nearby = list(contracts)
    return [c for c in nearby if getattr(c, "symbol", None)]


def pick_put_by_delta(nearby, snapshots, *, target_delta: float = TARGET_DELTA):
    """wheel_trader.py:193-223: the contract whose |delta| is closest to the
    target. Returns (strike, contract, bid, diff) or (None, None, None, None)
    for the ATR fallback. `snapshots` are OptionSnapshotDTOs (contract §3)."""
    best_symbol = None
    best_strike = None
    best_diff   = float("inf")
    best_bid    = 0.0

    for contract_sym, snap in (snapshots or {}).items():
        try:
            delta_raw = getattr(snap, "delta", None)
            if delta_raw is None:
                continue
            delta = abs(float(delta_raw))
            diff  = abs(delta - target_delta)
            matching = [c for c in nearby if c.symbol == contract_sym]
            # ST requested snapshots for `nearby` only, so every key matched;
            # skipping any other key keeps one contract from being paired
            # with the previous best's strike.
            if not matching:
                continue
            if diff < best_diff:
                best_diff   = diff
                best_symbol = contract_sym
                best_strike = float(matching[0].strike)
                try:
                    bid = getattr(snap, "bid", None)
                    best_bid = float(bid) if bid else 0.0
                except Exception:
                    best_bid = 0.0
        except Exception:
            continue

    if best_symbol and best_strike:
        return best_strike, best_symbol, best_bid, best_diff
    return None, None, None, None


# -- wheel_trader.py:531-702 place_put_order --------------------------------

def limit_price_ladder(*, delta_bid, contract_close, est_premium,
                       bid_mult: float = LIMIT_BID_MULT):
    """wheel_trader.py:587-606. Priority: live bid × 0.95 → stale close × 0.90
    → est_premium. Bid-side pricing gets fills; the haircut keeps premium."""
    if delta_bid and delta_bid > LIMIT_BID_MIN:
        return round(delta_bid * bid_mult, 2), "bid"
    close_price = float(contract_close) if contract_close else None
    if close_price and close_price > 0:
        return round(close_price * LIMIT_CLOSE_MULT, 2), "close"
    return round(float(est_premium), 2), "est_premium"


def short_put_collateral(option_positions):
    """Collateral of every open short PUT, total and by underlying, from the
    contract fields (fix 10)."""
    total, by = 0.0, {}
    for p in option_positions or []:
        try:
            qty = float(p.qty)
            if qty >= 0 or str(p.option_type).lower() != "put":  # fix 10
                continue
            c = float(p.strike) * 100 * abs(qty)
        except Exception:
            continue
        u = str(p.underlying).upper()
        total += c
        by[u] = by.get(u, 0.0) + c
    return total, by


def pending_sto_collateral(open_orders):
    """Collateral reserved by working sell-to-open PUT orders (the unfilled
    remainder). An OrderRef names its contract only by symbol."""
    total, by = 0.0, {}
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if parts is None:
            continue
        root, _expiry, kind, strike = parts
        if kind != "put" or str(getattr(o, "side", "")).lower() != "sell":
            continue
        if getattr(o, "position_intent", None) not in (None, "sell_to_open"):
            continue
        remaining = float(getattr(o, "qty", 0) or 0) - float(getattr(o, "filled_qty", 0) or 0)
        if remaining <= 0:
            continue
        c = strike * 100 * remaining
        total += c
        by[root] = by.get(root, 0.0) + c
    return total, by


def duplicate_put_reason(symbol, option_positions, open_orders, committed=()):
    """Fix 1: a rerun cannot sell a second put on an underlying that already
    has an open short put, a working sell order, or an order from this scan."""
    u = str(symbol).upper()
    for p in option_positions or []:
        try:
            if (float(p.qty) < 0 and str(p.option_type).lower() == "put"
                    and str(p.underlying).upper() == u):
                return f"duplicate: open short put {p.symbol} on {u}"
        except Exception:
            continue
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if (parts and parts[0] == u and parts[2] == "put"
                and str(getattr(o, "side", "")).lower() == "sell"):
            return f"duplicate: working sell order {o.symbol} on {u}"
    if u in {str(x).upper() for x in (committed or ())}:
        return f"duplicate: a put on {u} was already ordered in this scan"
    return None


def size_contracts(*, requested, strike, equity, cash,
                   max_collateral_pct: float = MAX_COLLATERAL_PCT,
                   existing_underlying_collateral: float = 0.0,
                   reserved_collateral: float = 0.0):
    """wheel_trader.py:611-670. Returns (contracts, error | None, notes).

    Step 3b, the per-name collateral cap (R3-08), now counts the collateral
    already open or working on this underlying (fix 3). Step 3c checks CASH
    net of every open and pending short put instead of margin buying power,
    which let a $79k put through on a $100k account (fix 8)."""
    notes = []
    n_contracts = int(requested)
    max_collateral = equity * max_collateral_pct
    headroom = max_collateral - float(existing_underlying_collateral or 0.0)  # fix 3
    allowed_contracts = int(headroom // (strike * 100)) if headroom > 0 else 0

    if allowed_contracts < 1:
        already = (f" (${existing_underlying_collateral:,.0f} already committed on this name)"
                   if existing_underlying_collateral else "")
        return 0, (f"Collateral cap: strike ${strike*100:,.0f} > {max_collateral_pct:.0%} "
                   f"of equity ${max_collateral:,.0f}{already}"), notes

    if n_contracts > allowed_contracts:
        notes.append(
            f"Collateral cap: reducing {n_contracts} → {allowed_contracts} contract(s) "
            f"(${strike * 100 * n_contracts:,.0f} would exceed {max_collateral_pct:.0%} "
            f"of equity ${max_collateral:,.0f})")
        n_contracts = allowed_contracts

    available = float(cash) - float(reserved_collateral or 0.0)  # fix 8
    required_capital = strike * 100 * n_contracts
    if available < required_capital:
        if available >= strike * 100:
            notes.append(f"insufficient cash for {n_contracts} contracts (need "
                         f"${required_capital:,.0f}, have ${available:,.0f}) — reducing to 1")
            n_contracts = 1
        else:
            return 0, (f"Insufficient cash: need ${strike*100:,.0f}, "
                       f"have ${available:,.0f}"), notes
    return n_contracts, None, notes


def build_put_order(candidate, *, chain, snapshots, cfg=None, equity, cash,
                    existing_underlying_collateral=0.0, reserved_collateral=0.0,
                    signal_id=None, session=None):
    """place_put_order (wheel_trader.py:531-702) as a pure function: pick the
    contract, price it, size it. Returns (order | None, error | None, meta)."""
    cfg = cfg or {}
    symbol        = candidate["symbol"]
    target_strike = float(candidate["strike_price"])
    expiry        = str(candidate["expiry"])[:10]
    n_contracts   = int(candidate.get("position_size_contracts", 1))

    puts = [c for c in (chain or [])
            if str(getattr(c, "option_type", "")).lower() == "put"
            and str(getattr(c, "expiration", ""))[:10] == expiry]
    if not puts:
        return None, f"No put contracts found for {symbol} exp {expiry}", {}

    nearby = nearby_contracts(puts, target_strike)
    delta_strike, delta_contract, delta_bid, delta_diff = pick_put_by_delta(
        nearby, snapshots, target_delta=float(cfg.get("target_delta", TARGET_DELTA)))

    if delta_strike and delta_contract:
        strike_used     = delta_strike
        contract_symbol = delta_contract
        matching        = [c for c in puts if c.symbol == delta_contract]
        best_for_price  = matching[0] if matching else None
    else:
        # Fallback: closest strike at or below ATR-based target
        eligible = [c for c in puts if float(c.strike) <= target_strike]
        if not eligible:
            eligible = puts
        best = max(eligible, key=lambda c: float(c.strike))
        strike_used     = float(best.strike)
        contract_symbol = best.symbol
        delta_bid       = 0.0
        best_for_price  = best

    limit_price, source = limit_price_ladder(
        delta_bid=delta_bid,
        contract_close=getattr(best_for_price, "close_price", None) if best_for_price else None,
        est_premium=candidate["est_premium"],
        bid_mult=float(cfg.get("limit_bid_mult", LIMIT_BID_MULT)))
    if limit_price < 0.01:
        return None, f"Limit price too low (${limit_price}) — skipping order", {}

    n, error, notes = size_contracts(
        requested=n_contracts, strike=strike_used, equity=float(equity), cash=float(cash),
        max_collateral_pct=float(cfg.get("max_collateral_pct", MAX_COLLATERAL_PCT)),
        existing_underlying_collateral=existing_underlying_collateral,
        reserved_collateral=reserved_collateral)
    if error:
        return None, error, {"notes": notes}

    # fix 1: the idempotency key is the contract plus the session. A-live keys
    # a sell-to-open on its own session, so "session" here is informational
    # (contract §11).
    order = {
        "signal_id": signal_id, "session": session, "underlying": symbol,
        "contract": contract_symbol, "option_type": "put", "strike": strike_used,
        "expiry": expiry, "position_intent": "sell_to_open", "qty": int(n),
        "order_type": "limit", "limit_price": float(limit_price), "tif": "day",
        "reason": "wheel_sto_put",
    }
    chosen = (snapshots or {}).get(contract_symbol)
    return order, None, {"notes": notes, "price_source": source,
                         "delta": getattr(chosen, "delta", None), "bid": delta_bid,
                         "delta_diff": delta_diff}


def build_put_order_live(candidate, *, adapter, cfg, equity, cash, option_positions,
                         open_orders, committed_collateral=None, signal_id=None,
                         session=None):
    """build_put_order over the broker adapter: the FULL chain for the expiry
    (plan A-live's get_option_contracts follows every next_page_token — ST
    read one page, fix 9) and snapshots for the nearby strikes."""
    symbol = str(candidate["symbol"]).upper()
    expiry = str(candidate["expiry"])[:10]
    chain = list(adapter.get_option_contracts(  # fix 9: every page
        symbol, option_type="put", expiration_gte=expiry, expiration_lte=expiry) or [])
    puts = [c for c in chain if str(getattr(c, "option_type", "")).lower() == "put"
            and str(getattr(c, "expiration", ""))[:10] == expiry]
    nearby = nearby_contracts(puts, float(candidate["strike_price"])) if puts else []
    try:
        snapshots = adapter.get_option_snapshots([c.symbol for c in nearby]) if nearby else {}
    except Exception as exc:
        _log(f"  [delta] Snapshot error for {symbol}: {exc} — falling back to ATR strike", "yellow")
        snapshots = {}
    held_total, held_by = short_put_collateral(option_positions)
    pend_total, pend_by = pending_sto_collateral(open_orders)
    # A put this scan already ordered counts only until the book shows it: once
    # it is a working order or a held position it is counted there, and a
    # resumed scan must not reserve its cash twice.
    committed = {str(k).upper(): float(v) for k, v in (committed_collateral or {}).items()
                 if str(k).upper() not in held_by and str(k).upper() not in pend_by}
    existing = held_by.get(symbol, 0.0) + pend_by.get(symbol, 0.0) + committed.get(symbol, 0.0)
    reserved = held_total + pend_total + sum(committed.values())
    return build_put_order(candidate, chain=chain, snapshots=snapshots, cfg=cfg,
                           equity=equity, cash=cash,
                           existing_underlying_collateral=existing,
                           reserved_collateral=reserved, signal_id=signal_id,
                           session=session)


# -- position rules ----------------------------------------------------------

def btc_order(pos, qty, reason, *, signal_id=None, session=None) -> dict:
    """A buy-to-close market order in the contract §1 shape."""
    return {"signal_id": signal_id, "session": session,
            "underlying": str(pos.underlying).upper(), "contract": pos.symbol,
            "option_type": str(pos.option_type).lower(), "strike": float(pos.strike),
            "expiry": str(pos.expiry)[:10], "position_intent": "buy_to_close",
            "qty": int(qty), "order_type": "market", "limit_price": None,
            "tif": "day", "reason": reason}


def two_x_exits(option_positions, *, open_orders=()):
    """wheel_trader.py:230-321 check_open_wheel_positions: a short PUT (fix 6:
    ST read any negative quantity) worth 2× the premium collected is bought
    back at market — a 100% loss on the premium caps the trade. A contract
    that already has a working buy is skipped."""
    working = {str(getattr(o, "symbol", "")).upper() for o in (open_orders or ())
               if str(getattr(o, "side", "")).lower() == "buy"}
    out = []
    for pos in option_positions or []:
        try:
            symbol = pos.symbol
            qty    = abs(int(float(pos.qty)))

            # Short puts have negative qty in Alpaca
            if float(pos.qty) >= 0:
                continue
            if str(pos.option_type).lower() != "put":  # fix 6
                continue

            # cost_basis is what we collected when selling
            # current market_value (negative for short) is what it costs to close
            cost_basis    = abs(float(pos.avg_entry_price)) * qty * 100
            current_value = abs(float(pos.market_value))

            if cost_basis <= 0:
                continue

            loss_ratio = current_value / cost_basis
            if loss_ratio >= 2.0 and str(symbol).upper() not in working:
                out.append((btc_order(pos, qty, "wheel_btc_2x"),
                            {"cost_basis": cost_basis, "current_value": current_value,
                             "loss_ratio": round(loss_ratio, 4)}))
        except Exception:
            continue
    return out


def put_monitor_decision(*, contract, underlying, strike, expiry, stock_price, today):
    """app.py:1309-1411, the per-position rules of api_check_wheel_positions,
    over Alpaca's contract fields (fix 10). Callers pass short PUTS only (fix 6).

    Tier 2 auto-close rules (no AI — deterministic):
      - ITM ≥ 10% at any DTE         → buy-to-close market order, priority 2
      - ITM ≥ 5% AND DTE ≤ 2         → buy-to-close market order, priority 2
      - DTE = 0 AND any ITM amount   → buy-to-close market order, priority 2
    Alert-only (human decides):
      - ITM < thresholds above       → priority 1, no auto action
      - DTE ≤ 1 AND OTM              → priority 0 informational
    """
    expiry_date = date.fromisoformat(str(expiry)[:10])
    dte         = (expiry_date - today).days

    # A price is a finite positive number or it is no price: ST's
    # `if itm and stock_price` kept a $0 print off the thresholds, and a
    # signed itm_pct would read it as 100% in the money.
    try:
        stock_price = float(stock_price) if stock_price is not None else None
    except (TypeError, ValueError):
        stock_price = None
    if stock_price is not None and not (math.isfinite(stock_price) and stock_price > 0):
        stock_price = None

    itm     = stock_price is not None and stock_price < strike
    # Signed (contract §9 item 3): > 0 in the money, < 0 out of it, None
    # without a price. ST reported 0.0 unless ITM; the thresholds below are
    # read only when itm, so they see the same values.
    itm_pct = ((strike - stock_price) / strike * 100) if stock_price is not None and strike else None
    deep_itm = bool(itm and itm_pct >= 3.0)

    out = {"contract": contract, "underlying": underlying, "strike": strike,
           "expiry": str(expiry_date), "stock_price": stock_price, "dte": dte,
           "itm": itm, "itm_pct": itm_pct, "deep_itm": deep_itm, "action": None,
           "intent": None, "reason": "", "title": None, "message": None,
           "priority": None}

    should_auto_close = False
    auto_close_reason = ""
    intent = None

    if stock_price is not None and itm:
        if itm_pct >= AUTO_CLOSE_ITM_PCT:
            should_auto_close = True
            auto_close_reason = f"{itm_pct:.1f}% ITM (≥{AUTO_CLOSE_ITM_PCT:.0f}% threshold) — bust regardless of DTE"
            intent = "wheel_btc_itm"
        elif dte <= 2 and itm_pct >= AUTO_CLOSE_DTE2_PCT:
            should_auto_close = True
            auto_close_reason = f"{itm_pct:.1f}% ITM with only {dte} day(s) to expiry — not recovering"
            intent = "wheel_btc_itm"
        elif dte == 0:
            should_auto_close = True
            auto_close_reason = f"Expiry TODAY and ITM ${stock_price:.2f} vs strike ${strike:.2f} — closing before assignment"
            intent = "wheel_btc_expiry"

    if should_auto_close:
        out.update(action="auto_close", intent=intent, reason=auto_close_reason,
                   priority=2, title=f"🚨 Auto-Closed: {underlying}",
                   message=(f"🚨 AUTO-CLOSE ORDERED\n"
                            f"{contract}\n"
                            f"Reason: {auto_close_reason}\n"
                            f"A buy-to-close market order was sent."))
        return out

    if stock_price is None:
        # ST formatted a None price into its alert and crashed out of the
        # position; say so instead of guessing.
        out["reason"] = "no underlying price"
        return out

    if itm:
        # ITM but below auto-close thresholds — monitor, may recover
        out.update(action="alert_itm", priority=1, title=f"⚠️ ITM PUT: {underlying}",
                   message=(f"{contract}\n"
                            f"Strike: ${strike:.2f} | Stock: ${stock_price:.2f}\n"
                            f"{itm_pct:.1f}% ITM | {dte} days to expiry\n"
                            f"Below auto-close threshold — monitor closely."))
    elif dte <= 1 and not itm:
        # Expiring soon but OTM — will expire worthless, informational only
        out.update(action="info_expiring", priority=0,
                   title=f"⏰ EXPIRING {'TODAY' if dte == 0 else 'TOMORROW'} OTM: {underlying}",
                   message=(f"{contract}\n"
                            f"Strike: ${strike:.2f} | Stock: ${stock_price:.2f}\n"
                            f"OTM — on track to expire worthless ✅\n"
                            f"Expiry: {expiry_date}"))
    return out


def covered_call_strike(cost_basis) -> float:
    """wheel_trader.py:432: cost basis × 1.05."""
    return round(float(cost_basis) * 1.05, 2)


def covered_call_candidates(equity_positions, option_positions, open_orders, *,
                            swing_owned=()):
    """wheel_trader.py:348-449 check_assigned_positions, the detection half.

    `equity_positions` is {symbol: {"qty", "avg_entry_price"}} (long stock).
    A name is covered when a held short option sits on it (contract fields,
    fix 10) or a working SELL option order does (its OCC root); ST counted a
    short put there too, and so does this. Open swing inventory is never an
    assignment (ST: _is_swing_position)."""
    covered_symbols = set()
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if parts and str(getattr(o, "side", "")).lower() == "sell":
            covered_symbols.add(parts[0])
    for p in option_positions or []:
        try:
            if float(p.qty) < 0:
                covered_symbols.add(str(p.underlying).upper())  # fix 10
        except Exception:
            continue
    owned = {str(s).upper() for s in (swing_owned or ())}
    out = []
    for symbol, pos in sorted((equity_positions or {}).items()):
        qty = int(float(pos.get("qty") or 0))
        if qty <= 0:
            continue
        if qty < 100:
            _log(f"  [covered-call] {symbol}: only {qty} shares — need 100 for covered call, skipping")
            continue
        if str(symbol).upper() in owned:
            _log(f"  [covered-call] {symbol}: open swing position — skipping")
            continue
        if str(symbol).upper() in covered_symbols:
            _log(f"  [covered-call] {symbol}: already has open short call — skipping")
            continue
        cost_basis = float(pos.get("avg_entry_price") or 0.0)
        out.append({"symbol": symbol, "qty": qty, "cost_basis": cost_basis,
                    "call_strike": covered_call_strike(cost_basis),
                    "n_contracts": qty // 100})
    return out


def dry_run_message(c, expiry):
    """wheel_trader.py:436-448, the AUTO_COVERED_CALL=False notification."""
    title = f"🔍 Assignment detected: {c['symbol']} (dry-run)"
    msg = (
        f"ASSIGNMENT DETECTED — dry-run, NO order placed\n"
        f"{c['symbol']}: {c['qty']} shares @ cost ${c['cost_basis']:.2f}\n"
        f"Would sell {c['n_contracts']} covered call(s):\n"
        f"Strike ≥ ${c['call_strike']:.2f} (cost basis × 1.05)  exp {expiry}\n"
        f"To enable: set auto_covered_call=true on the wheel lane"
    )
    return title, msg


def pick_covered_call(calls, call_strike, cost_basis):
    """wheel_trader.py:477-495: the closest strike AT OR ABOVE cost × 1.05
    (else the lowest available), priced at its close (else 1% of cost).
    Returns (contract, strike, limit_price) or None."""
    if not calls:
        return None
    eligible = [c for c in calls if float(c.strike) >= call_strike]
    if not eligible:
        eligible = list(calls)
    best = min(eligible, key=lambda c: float(c.strike))
    close_price = float(best.close_price) if getattr(best, "close_price", None) else None
    if close_price and close_price > 0:
        limit_price = round(close_price, 2)
    else:
        limit_price = round(cost_basis * 0.01, 2)
    if limit_price < 0.01:
        return None
    return best, float(best.strike), limit_price

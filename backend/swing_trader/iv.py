"""Daily implied-volatility snapshots, ported from ST iv_collector.py.

    iv_collector.py:67-103   snapshot_iv (Yahoo chain)            verbatim
    iv_collector.py:118-181  snapshot_iv_alpaca: the same 30-day ATM measure
                             from the broker adapter's option contracts and
                             indicative snapshots (plan A-live) instead of
                             ST's own clients
    iv_collector.py:232-257  run_iv_snapshot -> SwingIvSnapshots rows,
                             idempotent per ET day, bounded by the tick budget
    iv_collector.py:260-278  _load_iv_rank -> load_iv_rank; kept, and gating
                             nothing, as in ST
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swing_trader import clock
from swing_trader.constants import MIN_RANK_ROWS, RANK_WINDOW, TARGET_DTE, WHEEL_UNIVERSE
from swing_trader.refdata import IV_TABLE

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


def snapshot_iv(symbol: str, *, today: date | None = None) -> float | None:
    """30-day ATM implied volatility for one symbol, or None on any failure.

    Picks the listed expiry nearest TARGET_DTE days out, takes the strike
    closest to spot on both the call and put side, and averages their IVs.
    """
    try:
        t = yf.Ticker(symbol)
        expiries = t.options
        if not expiries:
            return None

        today = today or clock.ny_now(None).date()
        def dte(exp: str) -> int:
            return abs((datetime.strptime(exp, "%Y-%m-%d").date() - today).days - TARGET_DTE)
        expiry = min(expiries, key=dte)

        hist = t.history(period="1d")
        if hist.empty:
            return None
        spot = float(hist["Close"].iloc[-1])

        chain = t.option_chain(expiry)
        ivs = []
        for side in (chain.calls, chain.puts):
            if side is None or side.empty or "impliedVolatility" not in side.columns:
                continue
            atm = side.loc[(side["strike"] - spot).abs().idxmin()]
            iv = atm["impliedVolatility"]
            if iv is not None and not pd.isna(iv) and float(iv) > 0:
                ivs.append(float(iv))
        if not ivs:
            return None
        return round(sum(ivs) / len(ivs), 4)
    except Exception as exc:
        _log(f"  [iv] {symbol}: snapshot failed — {exc}")
        return None


def snapshot_iv_alpaca(symbol: str, *, adapter, spot, today: date) -> float | None:
    """The same measure from Alpaca option snapshots (feed=indicative)."""
    try:
        if spot is None:
            return None
        spot = float(spot)
        contracts = list(adapter.get_option_contracts(
            symbol,
            expiration_gte=(today + timedelta(days=TARGET_DTE - 12)).isoformat(),
            expiration_lte=(today + timedelta(days=TARGET_DTE + 15)).isoformat(),
            strike_gte=round(spot * 0.93, 2),
            strike_lte=round(spot * 1.07, 2)) or [])
        if not contracts:
            return None

        expiry = min(
            sorted({str(c.expiration)[:10] for c in contracts}),
            key=lambda d: abs((date.fromisoformat(d) - today).days - TARGET_DTE),
        )
        # ATM contract per side (call/put) at the chosen expiry
        atm: dict[str, tuple[float, str]] = {}
        for c in contracts:
            if str(c.expiration)[:10] != expiry:
                continue
            side = str(c.option_type).lower()
            dist = abs(float(c.strike) - spot)
            if side not in atm or dist < atm[side][0]:
                atm[side] = (dist, c.symbol)
        contract_symbols = [sym for _, sym in atm.values()]
        if not contract_symbols:
            return None

        snapshots = adapter.get_option_snapshots(contract_symbols) or {}
        ivs = []
        for cs in contract_symbols:
            snap = snapshots.get(cs)
            iv = getattr(snap, "iv", None) if snap else None
            if iv is not None and math.isfinite(float(iv)) and float(iv) > 0:
                ivs.append(float(iv))
        if not ivs:
            return None
        return round(sum(ivs) / len(ivs), 4)
    except Exception as exc:
        _log(f"  [iv] {symbol}: alpaca snapshot failed — {exc}")
        return None


def _spot(spot_for, symbol):
    """The Alpaca leg's spot, or None. ST read it inside snapshot_iv_alpaca's
    try (iv_collector.py:127-133), so a failed read cost that leg only."""
    try:
        return spot_for(symbol)
    except Exception as exc:
        _log(f"  [iv] {symbol}: spot read failed — {exc}")
        return None


def iv_row_id(symbol: str, day) -> str:
    return f"{str(symbol).upper()}|{str(day)[:10]}"


def run_iv_snapshot(store, *, adapter, spot_for, today: date, symbols=None,
                    deadline=None, now_fn=time.monotonic) -> dict:
    """Snapshot IV for every wheel-universe symbol. Idempotent per ET day —
    symbols already recorded today are skipped, so re-runs are safe. Dual-write
    (R8-07): either leg may fail; a row is written if at least one succeeded.
    With a `deadline` it stops starting new symbols 10 s before it."""
    day = today.isoformat()
    recorded, skipped, failed = [], [], []
    complete = True
    for symbol in list(symbols or WHEEL_UNIVERSE):
        if store.get(IV_TABLE, iv_row_id(symbol, day)) is not None:
            skipped.append(symbol)
            continue
        if deadline is not None and clock.time_left(deadline, clock=now_fn) < 10.0:
            complete = False
            break
        iv_yahoo = snapshot_iv(symbol, today=today)
        iv_alpaca = (snapshot_iv_alpaca(symbol, adapter=adapter, spot=_spot(spot_for, symbol),
                                        today=today) if adapter is not None else None)
        if iv_yahoo is None and iv_alpaca is None:
            failed.append(symbol)
            continue
        store.insert(IV_TABLE, {"id": iv_row_id(symbol, day), "symbol": symbol.upper(),
                                "date": day, "iv30": iv_yahoo, "iv30_alpaca": iv_alpaca},
                     conflict="replace")
        recorded.append(symbol)
    _log(f"  [iv] {day}: {len(recorded)} recorded, {len(skipped)} already done, "
         f"{len(failed)} failed{' (' + ', '.join(failed) + ')' if failed else ''}")
    return {"date": day, "recorded": recorded, "skipped": skipped, "failed": failed,
            "complete": complete}


def load_iv_rank(store, symbol: str) -> float | None:
    """IV Rank 0–100 from the trailing RANK_WINDOW snapshots:
    (current − low) / (high − low) × 100.
    Returns None when history is too short (< MIN_RANK_ROWS) or degenerate."""
    sym = str(symbol).upper()
    rows = store.run(store.order_by(store.between(IV_TABLE, f"{sym}|", f"{sym}|~"),
                                    index="id"))
    ivs = [float(r["iv30"]) for r in rows if r.get("iv30") not in (None, "")]
    ivs = ivs[-RANK_WINDOW:]
    if len(ivs) < MIN_RANK_ROWS:
        return None
    lo, hi, current = min(ivs), max(ivs), ivs[-1]
    if hi <= lo:
        return None
    return round((current - lo) / (hi - lo) * 100, 1)

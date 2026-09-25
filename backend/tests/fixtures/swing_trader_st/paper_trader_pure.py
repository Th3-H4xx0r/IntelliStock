"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, paper_trader.py.
Copied verbatim: lines 63-81, 85-89, 92-149, 156-201, 237-272, 293-340.
Not reproduced: the Alpaca client, dotenv, requests, tabulate and the print
rebinding of the original module. Two module attributes stand in for its I/O
so a test can drive it: `yf` (ST's yfinance module; None until a test injects
a stub) and REGIME_TRACKER_FILE (ST's /var/data path; a test points it at
tmp_path).
"""
import json
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

yf = None
REGIME_TRACKER_FILE = "regime_tracker.json"

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "SPY",
    "QQQ",  "AMD",  "NFLX", "JPM",  "DIS",  "BA",   "XLE",  "GLD",
]
POSITION_SIZE_PCT = 0.125
PROFIT_TARGET = 0.09
STOP_LOSS = 0.06
RSI_PERIOD = 14
RSI_OVERSOLD = 50   # raised from 40 (variant L sweep, 2026-06-10) — pullback entries, not knife-catches
RSI_OVERBOUGHT = 70
SMA200_PERIOD = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOL_AVG_PERIOD = 20
ADX_PERIOD    = 14
ADX_TREND_MIN = 15   # ADX must exceed this to confirm a real trend
VIX_FEAR_THRESHOLD = 25.0      # block new entries when VIX closes above this
SPY_BUFFER = 1.03              # SPY must be this multiple above SMA200 for regime to be active
WARMUP_DAYS   = 300  # ~200 trading days needed for SMA200 warmup
MAX_POSITIONS = 8    # max concurrent open positions
BEAR_REGIME_DAYS      = 10   # consecutive blocked days before switching to defensive mode
DEFENSIVE_UNIVERSE    = ["XLP", "XLU", "XLV", "GLD", "SHY"]
EARNINGS_HARD_BLOCK   = 5    # days — hard skip in entry loop before AI is called

# Sector map for correlation check — covers S&P 500 major sectors
# Symbol → sector string. Unknown symbols default to "unknown" (allowed through)
_SECTOR_OVERRIDES: dict[str, str] = {
    # ETFs and commodities not in S&P 500
    "SPY": "broad_market", "QQQ": "broad_market",
    "GLD": "commodity",    "XLE": "energy",
    "IWM": "broad_market", "DIA": "broad_market",
}

_SP500_SECTOR_CACHE: dict[str, str] = {}

def get_symbol_sector(symbol: str) -> str:
    """Return the sector for a symbol. Fetches from yfinance on first call, then caches."""
    if symbol in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[symbol]
    if symbol in _SP500_SECTOR_CACHE:
        return _SP500_SECTOR_CACHE[symbol]
    try:
        info   = yf.Ticker(symbol).info
        sector = info.get("sector", "unknown").lower().replace(" ", "_")
        _SP500_SECTOR_CACHE[symbol] = sector
        return sector
    except Exception:
        return "unknown"

MAX_PER_SECTOR = 1   # hard cap — only 1 position per sector at a time


def _atomic_write_json(path: str, data) -> None:
    dir_ = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_blocked_days() -> int:
    try:
        with open(REGIME_TRACKER_FILE) as f:
            return json.load(f).get("blocked_days", 0)
    except Exception:
        return 0


def update_regime_tracker(regime_ok: bool) -> int:
    days = 0 if regime_ok else get_blocked_days() + 1
    try:
        _atomic_write_json(REGIME_TRACKER_FILE, {"blocked_days": days, "updated": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
    return days


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (not direction)."""
    prev_close = close.shift(1)
    prev_low   = low.shift(1)
    prev_high  = high.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = np.where((high - prev_high) > (prev_low - low), (high - prev_high).clip(lower=0), 0.0)
    dm_minus = np.where((prev_low - low) > (high - prev_high), (prev_low - low).clip(lower=0), 0.0)

    dm_plus  = pd.Series(dm_plus,  index=close.index)
    dm_minus = pd.Series(dm_minus, index=close.index)

    atr      = tr.ewm(com=period - 1, min_periods=period).mean()
    di_plus  = 100 * dm_plus.ewm(com=period - 1,  min_periods=period).mean() / atr
    di_minus = 100 * dm_minus.ewm(com=period - 1, min_periods=period).mean() / atr

    dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
    return dx.ewm(com=period - 1, min_periods=period).mean()


def get_latest_indicators(df: pd.DataFrame) -> dict:
    """Return dict of symbol → latest indicator snapshot."""
    result = {}
    for symbol in df["symbol"].unique():
        sdf = df[df["symbol"] == symbol].copy().sort_values("date").reset_index(drop=True)
        # yfinance can return a NaN close for today's forming bar near the open.
        # float(nan) does not raise, so an unguarded close flows through as
        # now=$nan and (for SPY) SPY $nan in the regime line. Drop NaN-close rows
        # to fall back to the last completed bar; skip the symbol entirely if
        # nothing usable remains (handled downstream as "no indicator data").
        sdf = sdf[sdf["close"].notna()]
        if sdf.empty:
            continue
        sdf["rsi"] = _rsi(sdf["close"], RSI_PERIOD)
        macd_line, macd_sig = _macd(sdf["close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        sdf["macd_hist"]       = macd_line - macd_sig
        sdf["macd_hist_prev"]  = sdf["macd_hist"].shift(1)
        sdf["macd_hist_prev2"] = sdf["macd_hist"].shift(2)
        sdf["sma200"]    = _sma(sdf["close"], SMA200_PERIOD)
        sdf["vol_avg20"] = sdf["volume"].rolling(VOL_AVG_PERIOD).mean()
        sdf["rsi_prev"]  = sdf["rsi"].shift(1)
        sdf["adx"]       = _adx(sdf["high"], sdf["low"], sdf["close"], ADX_PERIOD)
        last = sdf.iloc[-1]
        result[symbol] = {
            "close":           float(last["close"]),
            "volume":          float(last["volume"]),
            "rsi":             float(last["rsi"])             if not pd.isna(last["rsi"])             else None,
            "rsi_prev":        float(last["rsi_prev"])        if not pd.isna(last["rsi_prev"])        else None,
            "macd_hist":       float(last["macd_hist"])       if not pd.isna(last["macd_hist"])       else None,
            "macd_hist_prev":  float(last["macd_hist_prev"])  if not pd.isna(last["macd_hist_prev"])  else None,
            "macd_hist_prev2": float(last["macd_hist_prev2"]) if not pd.isna(last["macd_hist_prev2"]) else None,
            "sma200":          float(last["sma200"])          if not pd.isna(last["sma200"])          else None,
            "vol_avg20":       float(last["vol_avg20"])       if not pd.isna(last["vol_avg20"])       else None,
            "adx":             float(last["adx"])             if not pd.isna(last["adx"])             else None,
        }
    return result


def entry_signal(ind: dict) -> bool:
    if any(v is None for v in ind.values()):
        return False
    # RSI<50 + 1-bar MACD ("variant L") — only config positive in both halves of
    # the 2021-2026 S&P 500 sweep (experiments/combo_results.json, 2026-06-10).
    rsi_signal      = ind["rsi"] < RSI_OVERSOLD and ind["rsi"] > ind["rsi_prev"]
    macd_improving  = ind["macd_hist"] > ind["macd_hist_prev"]
    vol_above_avg   = ind["volume"] > ind["vol_avg20"]
    above_sma       = ind["close"] > ind["sma200"]
    trend_confirmed = ind["adx"] > ADX_TREND_MIN
    rr_ok           = (PROFIT_TARGET / STOP_LOSS) >= 1.5
    return rsi_signal and macd_improving and vol_above_avg and above_sma and trend_confirmed and rr_ok


def exit_signal(ind: dict, entry_price: float):
    """Returns (should_exit: bool, reason: str | None)."""
    price = ind["close"]
    pct = (price - entry_price) / entry_price

    if pct >= PROFIT_TARGET:
        return True, "profit_target"
    if pct <= -STOP_LOSS:
        return True, "stop_loss"

    rsi_cross_ob = (
        ind["rsi_prev"] is not None
        and ind["rsi"] is not None
        and ind["rsi_prev"] < RSI_OVERBOUGHT
        and ind["rsi"] >= RSI_OVERBOUGHT
    )
    if rsi_cross_ob:
        return True, "rsi_overbought"

    return False, None


def sector_conflict(symbol: str, active_positions: set) -> str | None:
    """
    Returns the conflicting symbol if adding `symbol` would exceed MAX_PER_SECTOR
    for its sector, otherwise returns None.
    """
    candidate_sector = get_symbol_sector(symbol)
    if candidate_sector == "unknown":
        return None  # unknown sector — allow entry
    for held in active_positions:
        if get_symbol_sector(held) == candidate_sector:
            return held
    return None

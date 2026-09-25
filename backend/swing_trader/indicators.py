"""Indicators, ported from ST.

    paper_trader.py:156-201  _rsi, _macd, _sma, _adx            (verbatim)
    paper_trader.py:219-232  the long-frame reshape in fetch_bars -> bars_to_long_frame
    paper_trader.py:237-272  get_latest_indicators (periods lifted to keyword
                             arguments whose defaults are ST's constants)
    wheel_trader.py:734-741  _atr                                (verbatim)
    ai_analyst.py:111-119    _rsi returning the last value       -> rsi_last

wheel_trader.py:720-731 re-declares _rsi and _sma with identical bodies, so
the wheel reuses these.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from swing_trader.constants import (
    ADX_PERIOD,
    ATR_PERIOD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
    SMA200_PERIOD,
    VOL_AVG_PERIOD,
)


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


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def rsi_last(series: pd.Series, period: int = 14) -> float | None:
    """ai_analyst.py:111-119, where ST named it _rsi."""
    if len(series) < period + 2:
        return None
    d = series.diff()
    g = d.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    l = (-d).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs = g / l.replace(0, np.nan)
    val = (100.0 - 100.0 / (1.0 + rs)).iloc[-1]
    return float(val) if not np.isnan(val) else None


def bars_to_long_frame(bars: dict) -> pd.DataFrame:
    """paper_trader.py:219-232: {symbol: OHLCV frame} -> ST's long format."""
    all_parts = []
    for sym, sdf in bars.items():
        part = sdf.copy().dropna(how="all")
        part.columns = [c.lower() for c in part.columns]
        part["symbol"] = sym
        part["date"] = pd.to_datetime(part.index).normalize()
        part = part.reset_index(drop=True)
        all_parts.append(part)
    if not all_parts:
        return pd.DataFrame()
    return pd.concat(all_parts, ignore_index=True)


def get_latest_indicators(df: pd.DataFrame, *, rsi_period: int = RSI_PERIOD,
                          macd_fast: int = MACD_FAST, macd_slow: int = MACD_SLOW,
                          macd_signal: int = MACD_SIGNAL,
                          sma_period: int = SMA200_PERIOD,
                          vol_avg_period: int = VOL_AVG_PERIOD,
                          adx_period: int = ADX_PERIOD) -> dict:
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
        sdf["rsi"] = _rsi(sdf["close"], rsi_period)
        macd_line, macd_sig = _macd(sdf["close"], macd_fast, macd_slow, macd_signal)
        sdf["macd_hist"]       = macd_line - macd_sig
        sdf["macd_hist_prev"]  = sdf["macd_hist"].shift(1)
        sdf["macd_hist_prev2"] = sdf["macd_hist"].shift(2)
        sdf["sma200"]    = _sma(sdf["close"], sma_period)
        sdf["vol_avg20"] = sdf["volume"].rolling(vol_avg_period).mean()
        sdf["rsi_prev"]  = sdf["rsi"].shift(1)
        sdf["adx"]       = _adx(sdf["high"], sdf["low"], sdf["close"], adx_period)
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


def indicators_for_frames(frames: dict, cfg: dict | None = None) -> dict:
    """ST's fetch_bars reshape + get_latest_indicators, one symbol at a time.

    ST filters one long frame per symbol, which is O(symbols × rows) over a
    500-name universe; the snapshot per symbol is identical either way (pinned
    by test_indicators_for_frames_equals_the_long_frame_path).
    """
    cfg = cfg or {}
    kw = {
        "rsi_period": int(cfg.get("rsi_period", RSI_PERIOD)),
        "macd_fast": int(cfg.get("macd_fast", MACD_FAST)),
        "macd_slow": int(cfg.get("macd_slow", MACD_SLOW)),
        "macd_signal": int(cfg.get("macd_signal", MACD_SIGNAL)),
        "sma_period": int(cfg.get("sma_long", SMA200_PERIOD)),
        "vol_avg_period": int(cfg.get("vol_avg_period", VOL_AVG_PERIOD)),
        "adx_period": int(cfg.get("adx_period", ADX_PERIOD)),
    }
    out = {}
    for symbol, frame in (frames or {}).items():
        if frame is None or len(frame) == 0:
            continue
        long = bars_to_long_frame({symbol: frame})
        if long.empty or "close" not in long.columns:
            continue
        out.update(get_latest_indicators(long, **kw))
    return out

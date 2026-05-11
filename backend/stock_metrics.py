"""
Technical indicators computed from OHLCV DataFrame (DatetimeIndex, columns: Open, High, Low, Close, Adj Close, Volume).
All functions expect a pandas DataFrame with at least Close; many need High, Low, Volume.
Uses TA-Lib for indicator computation.
"""
import pandas as pd
import numpy as np
import talib


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average via TA-Lib."""
    result = talib.SMA(series.values.astype(np.float64), timeperiod=period)
    return pd.Series(result, index=series.index, name=series.name)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average via TA-Lib."""
    result = talib.EMA(series.values.astype(np.float64), timeperiod=period)
    return pd.Series(result, index=series.index, name=series.name)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI via TA-Lib (Wilder's smoothing)."""
    result = talib.RSI(close.values.astype(np.float64), timeperiod=period)
    return pd.Series(result, index=close.index, name="RSI")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD via TA-Lib. Returns (macd_line, signal_line, histogram) as Series."""
    m, s, h = talib.MACD(close.values.astype(np.float64), fastperiod=fast, slowperiod=slow, signalperiod=signal)
    return (
        pd.Series(m, index=close.index, name="MACD"),
        pd.Series(s, index=close.index, name="MACD_signal"),
        pd.Series(h, index=close.index, name="MACD_hist"),
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range via TA-Lib (Wilder's smoothing)."""
    result = talib.ATR(
        high.values.astype(np.float64),
        low.values.astype(np.float64),
        close.values.astype(np.float64),
        timeperiod=period,
    )
    return pd.Series(result, index=close.index, name="ATR")


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume via TA-Lib."""
    result = talib.OBV(close.values.astype(np.float64), volume.values.astype(np.float64))
    return pd.Series(result, index=close.index, name="OBV")


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index via TA-Lib."""
    result = talib.ADX(
        high.values.astype(np.float64),
        low.values.astype(np.float64),
        close.values.astype(np.float64),
        timeperiod=period,
    )
    return pd.Series(result, index=close.index, name="ADX")


def high_52w(high: pd.Series, min_bars: int = 200) -> float:
    """52-week (approx 260 trading days) high. Uses last 260 bars or available."""
    s = high.iloc[-min(260, len(high)):]
    return float(s.max()) if len(s) else np.nan


def low_52w(low: pd.Series, min_bars: int = 200) -> float:
    """52-week low."""
    s = low.iloc[-min(260, len(low)):]
    return float(s.min()) if len(s) else np.nan


def drawdown_pct(close: pd.Series, lookback: int = 60) -> float:
    """Max drawdown from recent high over lookback bars, as positive percentage (e.g. 15 = 15% down)."""
    if len(close) < 2 or lookback < 1:
        return 0.0
    window = close.iloc[-lookback:]
    peak = window.cummax()
    dd = (peak - close.iloc[-lookback:]) / peak.replace(0, np.nan)
    return float(100 * dd.max()) if len(dd) else 0.0


def add_all_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add technical indicator columns to OHLCV DataFrame. In-place style; returns df.
    Expects columns: Open, High, Low, Close, (Adj Close), Volume.
    """
    if df.empty or len(df) < 50:
        return df
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"] if "Volume" in df.columns else pd.Series(1, index=df.index)
    vol = vol.fillna(0).astype(float)

    df["SMA_20"] = sma(close, 20)
    df["SMA_50"] = sma(close, 50)
    df["SMA_200"] = sma(close, 200)
    df["RSI"] = rsi(close, 14)
    macd_line, signal_line, hist = macd(close, 12, 26, 9)
    df["MACD"] = macd_line
    df["MACD_signal"] = signal_line
    df["MACD_hist"] = hist
    df["ATR"] = atr(high, low, close, 14)
    df["OBV"] = obv(close, vol)
    df["ADX"] = adx(high, low, close, 14)
    return df


class StockMetrics:
    """
    Wraps an OHLCV DataFrame and exposes latest values + criteria checks.
    Call add_all_metrics(df) first, then instantiate.
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df
        if df.empty:
            self.n = 0
            return
        self.n = len(df)
        self.close = df["Close"].astype(float)

    def _last(self, s):
        if s is None or len(s) == 0:
            return None
        v = s.iloc[-1]
        if v is None or (hasattr(v, "__float__") and pd.isna(v)):
            return None
        return float(v)

    @property
    def price(self) -> float:
        return float(self.close.iloc[-1]) if self.n else 0.0

    @property
    def sma_20(self) -> float:
        return self._last(self.df.get("SMA_20"))

    @property
    def sma_50(self) -> float:
        return self._last(self.df.get("SMA_50"))

    @property
    def sma_200(self) -> float:
        return self._last(self.df.get("SMA_200"))

    @property
    def rsi(self) -> float:
        return self._last(self.df.get("RSI"))

    @property
    def macd_above_signal(self) -> bool:
        m = self.df.get("MACD")
        s = self.df.get("MACD_signal")
        if m is None or s is None or len(m) < 2:
            return False
        return float(m.iloc[-1]) > float(s.iloc[-1])

    @property
    def macd_hist_rising(self) -> bool:
        h = self.df.get("MACD_hist")
        if h is None or len(h) < 3:
            return False
        return float(h.iloc[-1]) > float(h.iloc[-2])

    @property
    def high_52w(self) -> float:
        return high_52w(self.df["High"]) if "High" in self.df.columns else 0.0

    @property
    def low_52w(self) -> float:
        return low_52w(self.df["Low"]) if "Low" in self.df.columns else 0.0

    @property
    def atr_pct(self) -> float:
        """ATR as % of price."""
        atr_val = self._last(self.df.get("ATR"))
        if atr_val is None or self.price <= 0:
            return 0.0
        return 100 * float(atr_val) / self.price

    @property
    def volume_latest(self) -> float:
        v = self.df.get("Volume")
        return float(v.iloc[-1]) if v is not None and len(v) else 0.0

    @property
    def volume_avg_20(self) -> float:
        v = self.df.get("Volume")
        if v is None or len(v) < 20:
            return 0.0
        return float(v.iloc[-20:].mean())

    @property
    def obv_trending_up_30(self) -> bool:
        obv_ = self.df.get("OBV")
        if obv_ is None or len(obv_) < 30:
            return False
        return float(obv_.iloc[-1]) > float(obv_.iloc[-30])

    @property
    def adx(self) -> float:
        return self._last(self.df.get("ADX")) or 0.0

    @property
    def drawdown_pct(self) -> float:
        return drawdown_pct(self.close, 60)

    def price_within_pct_of_52w_high(self, pct: float) -> bool:
        h = self.high_52w
        if h <= 0:
            return False
        return self.price >= (1 - pct / 100) * h

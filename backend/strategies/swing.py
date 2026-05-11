# INTELLISTOCK_SCHEMA: {"strategy": "Swing", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"rsi_period": 2, "rsi_long_threshold": 40, "rsi_short_threshold": 60, "volume_ratio_min": 0.8, "take_profit_1_pct": 5, "take_profit_2_pct": 10, "trailing_stop_pct": 3}}
# INTELLISTOCK_DESCRIPTION: RSI-2 mean reversion on daily bars. Bull: pullback/momentum/strong-trend entries; take-profit and trailing exits. Bear: short on RSI(2) > threshold.
# DIFFICULTY: 3
"""
Swing Strategy - Enhanced RSI-2 Mean Reversion with Volume & Trend Confirmation

TIMEFRAME: DAILY BARS (1Day) or INTRADAY BARS (converted to daily) - REQUIRED
- Uses daily aggregated data for indicators (200 SMA, 5 SMA, RSI-2, ATR, Volume)
- Entries/exits happen near market close (3:50 PM - 4:00 PM ET / 12:50 PM - 1:00 PM PT)
- Makes at most one entry per day (re-entry allowed after exits)
- NOTE: Short positions are tracked in cache but require broker support for execution

STRATEGY RULES:

1. BULL MARKET (Price > 200 SMA):
   - Entry: Enhanced multi-mode system with filters:
     * Pullback: Buy if RSI(2) < threshold (default 40, +5 bonus if rising)
       - Requires volume >= 0.8x 20-day average (configurable)
       - Prefers strong/stable trends (slope > -0.5%)
     * Momentum: If price >3% above SMA, allow entry on RSI(2) < 60
     * Strong Trend: If price >10% above SMA, allow entry on RSI(2) < 70
   - Exit: Multiple adaptive exit strategies:
     * Take Profit 1: Exit at 5% gain (ATR-adjusted, configurable via take_profit_1_pct)
     * Take Profit 2: Exit at 10% gain (ATR-adjusted, configurable via take_profit_2_pct)
     * Trailing Stop: Exit if price drops 3% from peak (ATR-adjusted, configurable via trailing_stop_pct)
     * SMA Exit: Exit if price closes >2% below 5-day SMA (ATR-adjusted, configurable via exit_threshold_pct)

2. BEAR/FLAT MARKET (Price < 200 SMA):
   - Entry: Short near market close if RSI(2) > threshold (default 60, configurable via rsi_short_threshold)
     Note: Re-entry allowed after exits.
   - Exit: Cover short near market close when price closes below 5-day SMA (re-entry allowed same day)

INDICATORS:
- 200-day SMA: Regime filter (bull vs bear/flat) + slope for trend strength
- 5-day SMA: Dynamic exit target
- 2-period RSI: Entry trigger with momentum detection (rising/falling)
- ATR(14): Volatility measurement for adaptive exits
- Volume: 20-day average for entry confirmation

IMPROVEMENTS:
- Volume confirmation: Filters out low-volume setups
- Trend strength: Uses SMA slope to prefer strong trends
- RSI momentum: Bonus threshold when RSI is rising from oversold
- ATR-adjusted exits: Scales profit targets and stops based on volatility
- Peak tracking: Trailing stop based on highest price since entry

CONFIGURABLE PARAMETERS:
- rsi_long_threshold: RSI(2) threshold for pullback entries (default 40)
- rsi_short_threshold: RSI(2) threshold for short entries (default 60)
- momentum_threshold_pct: Price % above SMA to activate momentum mode (default 3%)
- momentum_rsi_threshold: RSI(2) threshold for momentum entries (default 60)
- require_volume_confirmation: Require volume filter (default True)
- min_volume_ratio: Minimum volume vs 20-day average (default 0.8)
- prefer_strong_trend: Prefer entries in strong trends (default True)
- take_profit_1_pct: First profit target % (default 5%, ATR-adjusted)
- take_profit_2_pct: Second profit target % (default 10%, ATR-adjusted)
- trailing_stop_pct: Trailing stop % from peak (default 3%, ATR-adjusted)
- exit_threshold_pct: SMA exit threshold % (default 2%, ATR-adjusted)
- use_atr_adjusted_exits: Enable ATR-based exit scaling (default True)

Returns: 1 = buy (long entry or short cover), 0 = hold, -1 = sell (long exit or short entry).
"""

from __future__ import annotations

import datetime
import os
import numpy as np

try:
    import sys
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="SwingStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[SwingStrategy] {msg}")


def _get_bar_ohlcv(b, key_short, key_long, fallback_key="c"):
    """Extract OHLCV value from bar dict, supporting both short and long key names."""
    v = b.get(key_short) or b.get(key_long) or b.get(fallback_key) or 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _bar_datetime(b):
    """Bar dict -> naive UTC datetime from 't'."""
    t = b.get("t")
    if t is None:
        return None
    if isinstance(t, datetime.datetime):
        if getattr(t, "tzinfo", None) is not None:
            return t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return t
    s = str(t)
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            from dateutil import parser as dateutil_parser
            dt = dateutil_parser.parse(s)
        except Exception:
            return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def _to_pacific(dt):
    """Naive UTC datetime -> naive Los Angeles (Pacific) datetime."""
    if dt is None:
        return None
    try:
        import pytz
        utc = pytz.UTC
        pt = pytz.timezone("America/Los_Angeles")
        if getattr(dt, "tzinfo", None) is None:
            dt = utc.localize(dt)
        return dt.astimezone(pt).replace(tzinfo=None)
    except Exception:
        return dt


def _intraday_to_daily_bars(intraday_bars):
    """
    Convert intraday bars to daily aggregated bars.
    Returns list of daily bar dicts with keys: t, o, h, l, c, v
    """
    if not intraday_bars:
        return []
    
    # Group bars by date
    daily_dict = {}  # date -> {o, h, l, c, v, t}
    
    for bar in intraday_bars:
        dt_utc = _bar_datetime(bar)
        if dt_utc is None:
            continue
        date_key = dt_utc.date()
        
        o = _get_bar_ohlcv(bar, "o", "open")
        h = _get_bar_ohlcv(bar, "h", "high")
        l = _get_bar_ohlcv(bar, "l", "low")
        c = _get_bar_ohlcv(bar, "c", "close")
        v = _get_bar_ohlcv(bar, "v", "volume")
        
        if date_key not in daily_dict:
            daily_dict[date_key] = {
                "t": dt_utc,  # Use first bar's timestamp for the day
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
            }
        else:
            daily = daily_dict[date_key]
            daily["h"] = max(daily["h"], h)
            daily["l"] = min(daily["l"], l)
            daily["c"] = c  # Last close of the day
            daily["v"] += v  # Sum volume
    
    # Convert to list sorted by date
    daily_bars = []
    for date_key in sorted(daily_dict.keys()):
        daily = daily_dict[date_key]
        daily_bars.append({
            "t": daily["t"],
            "o": daily["o"],
            "h": daily["h"],
            "l": daily["l"],
            "c": daily["c"],
            "v": daily["v"],
        })
    
    return daily_bars


def _sma(values, period):
    """Simple Moving Average (last value). Uses TA-Lib."""
    if len(values) < period:
        return np.nan
    import talib
    arr = np.asarray(values, dtype=np.float64)
    result = talib.SMA(arr, timeperiod=period)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _rsi_2(closes):
    """2-period RSI via TA-Lib. Returns scalar or np.nan."""
    if len(closes) < 3:
        return np.nan
    import talib
    arr = np.asarray(closes, dtype=np.float64)
    result = talib.RSI(arr, timeperiod=2)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _atr(bars, period=14):
    """ATR via TA-Lib (Wilder's smoothing). Accepts bar dicts, returns scalar."""
    if len(bars) < period + 1:
        return np.nan
    import talib
    high = np.array([_get_bar_ohlcv(b, "h", "high") for b in bars], dtype=np.float64)
    low = np.array([_get_bar_ohlcv(b, "l", "low") for b in bars], dtype=np.float64)
    close = np.array([_get_bar_ohlcv(b, "c", "close") for b in bars], dtype=np.float64)
    result = talib.ATR(high, low, close, timeperiod=period)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _sma_slope(values, period, lookback=5):
    """Calculate SMA slope to measure trend strength."""
    if len(values) < period + lookback:
        return 0.0
    
    sma_current = _sma(values, period)
    sma_past_values = values[-(period + lookback):-lookback]
    sma_past = np.mean(sma_past_values) if len(sma_past_values) >= period else sma_current
    
    if sma_past == 0:
        return 0.0
    
    slope_pct = ((sma_current - sma_past) / sma_past * 100.0)
    return slope_pct


def _average_volume(bars, period=20):
    """Calculate average volume over period."""
    if len(bars) < period:
        return np.nan
    
    volumes = [_get_bar_ohlcv(b, "v", "volume") for b in bars[-period:]]
    return np.mean(volumes)


class Swing:
    """
    Swing Strategy - RSI-2 Mean Reversion
    
    Uses daily bars (or intraday bars converted to daily).
    Entries/exits near market close (3:50 PM - 4:00 PM ET / 12:50 PM - 1:00 PM PT).
    """
    
    def __init__(self):
        self.min_daily_bars = 40  # Minimum daily bars (reduced from 200 for flexibility)
        self.min_intraday_bars = 50  # Minimum intraday bars if using intraday
        
        # Market close window for entries/exits (Pacific time)
        # 3:50 PM - 4:00 PM ET = 12:50 PM - 1:00 PM PT
        self.ENTRY_START_PT = datetime.time(12, 50, 0)  # 12:50 PM PT = 3:50 PM ET
        self.ENTRY_END_PT = datetime.time(13, 0, 0)     # 1:00 PM PT = 4:00 PM ET
    
    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Run swing strategy.
        
        Returns: 1 = buy (long entry or short cover), 0 = hold, -1 = sell (long exit or short entry).
        """
        if data is None or symbol not in data:
            _log(f"{symbol}: No data available", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if len(bars) < self.min_intraday_bars:
            _log(f"{symbol}: Insufficient bars ({len(bars)} < {self.min_intraday_bars})", "yellow")
            return (0, None, None, "No clear signal")
        cache = strategy_cache if strategy_cache is not None else {}
        cache_key = f"swing_{symbol}"
        
        # Detect if we're using daily bars based on time_increment or bar spacing
        # Check time_increment first (most reliable)
        is_daily_bars = False
        if time_increment:
            time_increment_str = str(time_increment).lower()
            if "d" in time_increment_str or time_increment_str in ("1day", "1d", "day", "daily"):
                is_daily_bars = True
        
        # Convert to daily bars if needed
        if not is_daily_bars and len(bars) > 1:
            # Check bar spacing: if bars span many days with few bars, likely daily
            first_dt = _bar_datetime(bars[0])
            last_dt = _bar_datetime(bars[-1])
            if first_dt and last_dt:
                days_span = (last_dt.date() - first_dt.date()).days
                # If we have many bars but span few days, likely intraday
                if days_span < len(bars) / 2:
                    daily_bars = _intraday_to_daily_bars(bars)
                    is_daily_bars = False  # Was intraday, converted to daily
                else:
                    daily_bars = bars  # Already daily bars
                    is_daily_bars = True
            else:
                daily_bars = _intraday_to_daily_bars(bars)  # Safe default
                is_daily_bars = False
        elif is_daily_bars:
            # Already daily bars, use as-is
            daily_bars = bars
        else:
            # Single bar or couldn't determine, assume daily
            daily_bars = bars
            is_daily_bars = True
        
        if len(daily_bars) < self.min_daily_bars:
            _log(f"{symbol}: Insufficient daily bars ({len(daily_bars)} < {self.min_daily_bars})", "yellow")
            return (0, None, None, "No clear signal")
        # Get current bar and time
        current_bar = bars[-1]  # Use original bars for time check
        dt_utc = _bar_datetime(current_bar)
        if dt_utc is None:
            _log(f"{symbol}: Failed to parse bar datetime", "yellow")
            return (0, None, None, "No clear signal")
        dt_pt = _to_pacific(dt_utc)
        if dt_pt is None:
            _log(f"{symbol}: Failed to convert to Pacific time", "yellow")
            return (0, None, None, "No clear signal")
        current_time_pt = dt_pt.time()
        date_str = dt_pt.date().isoformat()
        day_key = f"{cache_key}_{date_str}"
        
        # Get current day's daily bar (need this before logging)
        current_daily_bar = daily_bars[-1]
        current_close = _get_bar_ohlcv(current_daily_bar, "c", "close")
        current_open = _get_bar_ohlcv(current_daily_bar, "o", "open")
        
        if current_close <= 0:
            _log(f"{symbol}: Invalid close price: {current_close}", "yellow")
            return (0, None, None, "No clear signal")
        # Only process near market close (entry/exit window) for intraday bars
        # For daily bars, process whenever the bar is available (represents end of day)
        if not is_daily_bars:
            # Intraday bars: only process in entry/exit window (12:50 PM - 1:00 PM PT)
            if current_time_pt < self.ENTRY_START_PT or current_time_pt >= self.ENTRY_END_PT:
                # Log once per day when outside window
                if not cache.get(day_key + "_outside_window_logged"):
                    _log(f"{symbol}: Outside entry/exit window ({current_time_pt} PT not in {self.ENTRY_START_PT}-{self.ENTRY_END_PT} PT)", "cyan")
                    cache[day_key + "_outside_window_logged"] = True
                return (0, None, None, "No clear signal")
        # Daily bars: process whenever available (bar represents end of day close)
        
        # Log that we're processing
        bar_type = "daily" if is_daily_bars else "intraday"
        _log(f"{symbol}: Processing {bar_type} bars at {current_time_pt} PT | Daily bars: {len(daily_bars)}, Current price: {current_close:.2f}", "cyan")
        
        if current_close <= 0:
            return (0, None, None, "No clear signal")
        # Extract closes and volumes for indicators
        closes = np.array([_get_bar_ohlcv(b, "c", "close") for b in daily_bars], dtype=float)
        
        # Adaptive SMA periods based on available data
        # Use smaller periods if we don't have enough data for full 200 SMA
        sma_period_200 = min(200, max(20, len(daily_bars) - 5))  # At least 20, but leave 5 bars buffer
        sma_period_5 = min(5, max(3, len(daily_bars) - 2))  # At least 3, but leave 2 bars buffer
        
        # Calculate indicators
        sma_200 = _sma(closes, sma_period_200)
        sma_5 = _sma(closes, sma_period_5)
        rsi_2 = _rsi_2(closes)
        
        # Calculate RSI momentum (previous RSI value)
        rsi_2_prev = np.nan
        if len(closes) >= 4:  # Need at least 4 closes for previous RSI(2)
            closes_prev = closes[:-1]
            rsi_2_prev = _rsi_2(closes_prev)
        
        # Calculate trend strength (SMA slope)
        sma_200_slope = _sma_slope(closes, sma_period_200, lookback=5) if len(closes) >= sma_period_200 + 5 else 0.0
        
        # Calculate ATR for volatility-adjusted entries/exits
        atr_14 = _atr(daily_bars, period=14) if len(daily_bars) >= 15 else np.nan
        
        # Calculate volume metrics
        avg_volume_20 = _average_volume(daily_bars, period=20) if len(daily_bars) >= 20 else np.nan
        current_volume = _get_bar_ohlcv(daily_bars[-1], "v", "volume") if daily_bars else 0.0
        volume_ratio = (current_volume / avg_volume_20) if avg_volume_20 > 0 else 1.0
        
        if np.isnan(sma_200) or np.isnan(sma_5) or np.isnan(rsi_2):
            _log(f"{symbol}: Indicator calculation failed - SMA{sma_period_200}: {sma_200}, SMA{sma_period_5}: {sma_5}, RSI(2): {rsi_2}", "yellow")
            return (0, None, None, "No clear signal")
        # Determine market regime with trend strength
        is_bull_market = current_close > sma_200
        trend_strength = "STRONG" if abs(sma_200_slope) > 0.5 else "WEAK" if abs(sma_200_slope) > 0.1 else "FLAT"
        regime = "BULL" if is_bull_market else "BEAR/FLAT"
        
        # RSI momentum (rising/falling)
        rsi_rising = not np.isnan(rsi_2_prev) and rsi_2 > rsi_2_prev
        rsi_momentum = "RISING" if rsi_rising else "FALLING" if not np.isnan(rsi_2_prev) else "UNKNOWN"
        
        _log(f"{symbol}: Indicators | SMA{sma_period_200}: {sma_200:.2f} (slope: {sma_200_slope:.2f}%), SMA{sma_period_5}: {sma_5:.2f}, RSI(2): {rsi_2:.1f} ({rsi_momentum}), Volume: {volume_ratio:.2f}x avg, Regime: {regime} ({trend_strength})", "cyan")
        
        # Check if we're in a position (check persistent cache key, not day-specific)
        position_key = cache_key + "_position_type"
        entry_price_key = cache_key + "_entry_price"
        entry_date_key = cache_key + "_entry_date"
        
        position_type = cache.get(position_key)  # "long" or "short" or None
        
        # Also check portfolio_emulator to see if we have actual shares (for long positions)
        has_long_shares = False
        if portfolio_emulator is not None:
            positions = portfolio_emulator.get_positions() or {}
            shares = positions.get(symbol, 0.0) or 0.0
            has_long_shares = shares > 0
        
        # If we have shares but no cache entry, assume it's a long position from this strategy
        if has_long_shares and position_type is None:
            position_type = "long"
            cache[position_key] = "long"
            # Try to get entry price from cache
            entry_price = cache.get(entry_price_key)
            if entry_price is None:
                # Look for entry price from previous days
                for i in range(1, 10):
                    prev_date = dt_pt.date() - datetime.timedelta(days=i)
                    prev_day_key = f"{cache_key}_{prev_date.isoformat()}"
                    entry_price = cache.get(prev_day_key + "_entry_price")
                    if entry_price:
                        cache[entry_price_key] = entry_price
                        cache[entry_date_key] = prev_date.isoformat()
                        break
        
        # Get entry details (use persistent keys)
        entry_price = cache.get(entry_price_key)
        entry_date = cache.get(entry_date_key)
        
        # EXIT LOGIC
        if position_type == "long":
            entry_str = f"{entry_price:.2f}" if entry_price is not None else "N/A"
            _log(f"{symbol}: Checking LONG exit | Entry: {entry_str}, Current: {current_close:.2f}", "cyan")
            
            if entry_price is None or entry_price <= 0:
                _log(f"{symbol}: Invalid entry price, skipping exit check", "yellow")
                return (0, None, None, "No clear signal")
            # Calculate current profit
            profit_pct = ((current_close - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
            
            # Track highest price since entry (for trailing stop)
            peak_price_key = cache_key + "_peak_price"
            peak_price = cache.get(peak_price_key, entry_price)
            if current_close > peak_price:
                peak_price = current_close
                cache[peak_price_key] = peak_price
            
            # Configurable exit parameters (ATR-adjusted if available)
            base_exit_threshold = float(conditions.get("exit_threshold_pct", 2.0)) if conditions else 2.0
            base_take_profit_1 = float(conditions.get("take_profit_1_pct", 5.0)) if conditions else 5.0
            base_take_profit_2 = float(conditions.get("take_profit_2_pct", 10.0)) if conditions else 10.0
            base_trailing_stop = float(conditions.get("trailing_stop_pct", 3.0)) if conditions else 3.0
            
            # ATR-adjusted exits: Scale thresholds based on volatility
            use_atr_adjusted_exits = conditions.get("use_atr_adjusted_exits", True) if conditions else True
            if use_atr_adjusted_exits and not np.isnan(atr_14) and atr_14 > 0 and entry_price > 0:
                # Scale thresholds by ATR (higher volatility = wider stops/targets)
                atr_pct_of_price = (atr_14 / entry_price * 100.0)
                volatility_multiplier = max(0.5, min(2.0, atr_pct_of_price / 2.0))  # Normalize around 2% ATR
                exit_threshold_pct = base_exit_threshold * volatility_multiplier
                take_profit_1_pct = base_take_profit_1 * volatility_multiplier
                take_profit_2_pct = base_take_profit_2 * volatility_multiplier
                trailing_stop_pct = base_trailing_stop * volatility_multiplier
            else:
                exit_threshold_pct = base_exit_threshold
                take_profit_1_pct = base_take_profit_1
                take_profit_2_pct = base_take_profit_2
                trailing_stop_pct = base_trailing_stop
            
            should_exit = False
            exit_reason = ""
            
            # 1. Profit targets (take profits at key levels)
            if profit_pct >= take_profit_2_pct:
                should_exit = True
                exit_reason = f"Take Profit 2 ({take_profit_2_pct:.1f}% gain)"
            elif profit_pct >= take_profit_1_pct:
                should_exit = True
                exit_reason = f"Take Profit 1 ({take_profit_1_pct:.1f}% gain)"
            
            # 2. Trailing stop (exit if price drops X% from peak)
            if not should_exit and peak_price > entry_price:
                drop_from_peak_pct = ((peak_price - current_close) / peak_price * 100.0) if peak_price > 0 else 0.0
                if drop_from_peak_pct > trailing_stop_pct:
                    should_exit = True
                    exit_reason = f"Trailing Stop ({drop_from_peak_pct:.1f}% drop from peak ${peak_price:.2f})"
            
            # 3. SMA-based exit (original logic - exit if price closes significantly below 5-day SMA)
            if not should_exit:
                price_below_sma_pct = ((sma_5 - current_close) / sma_5 * 100.0) if sma_5 > 0 else 0.0
                if current_close < sma_5 and price_below_sma_pct > exit_threshold_pct:
                    should_exit = True
                    exit_reason = f"SMA Exit (Price {price_below_sma_pct:.1f}% below {sma_period_5}-day SMA)"
            
            if not should_exit:
                drop_from_peak_pct = ((peak_price - current_close) / peak_price * 100.0) if peak_price > entry_price else 0.0
                _log(f"{symbol}: LONG exit condition not met | Current: {current_close:.2f}, Profit: {profit_pct:.2f}%, Peak: {peak_price:.2f}, Drop from peak: {drop_from_peak_pct:.2f}%", "cyan")
            
            if should_exit:
                cache.pop(position_key, None)
                cache.pop(entry_price_key, None)
                cache.pop(entry_date_key, None)
                cache.pop(peak_price_key, None)  # Clear peak price tracking
                cache.pop(day_key + "_entered_today", None)  # Allow re-entry after exit
                profit_pct = ((current_close - entry_price) / entry_price * 100.0) if entry_price and entry_price > 0 else 0.0
                entry_str = f"{entry_price:.2f}" if entry_price is not None else "N/A"
                _log(
                    f"{symbol}: LONG EXIT - {exit_reason} | "
                    f"Exit: {current_close:.2f}, Entry: {entry_str}, Profit: {profit_pct:.2f}% | Re-entry allowed",
                    "green"
                )
                return (-1, None, None, "Sell signal generated")
        
        elif position_type == "short":
            entry_str = f"{entry_price:.2f}" if entry_price is not None else "N/A"
            _log(f"{symbol}: Checking SHORT exit | Entry: {entry_str}, Current: {current_close:.2f}", "cyan")
            # Short exit: Price closes below SMA
            # Note: Broker may not support shorts natively; this is tracked in cache
            if current_close < sma_5:
                cache.pop(position_key, None)
                cache.pop(entry_price_key, None)
                cache.pop(entry_date_key, None)
                # Allow re-entry after exit
                cache.pop(day_key + "_entered_today", None)  # Allow re-entry after exit
                profit_pct = ((entry_price - current_close) / entry_price * 100.0) if entry_price and entry_price > 0 else 0.0
                entry_str = f"{entry_price:.2f}" if entry_price is not None else "N/A"
                _log(
                    f"{symbol}: SHORT EXIT - Price closed below {sma_period_5}-day SMA | "
                    f"Exit: {current_close:.2f}, Entry: {entry_str}, Profit: {profit_pct:.2f}% | Re-entry allowed "
                    f"(Note: Short positions require broker support)",
                    "green"
                )
                return (1, None, None, "Buy signal generated")
            else:
                _log(f"{symbol}: SHORT exit condition not met | Current: {current_close:.2f} >= SMA{sma_period_5}: {sma_5:.2f}", "cyan")
        
        # ENTRY LOGIC (only if not in position)
        if position_type is None:
            _log(f"{symbol}: No position, checking entry conditions | Regime: {regime}", "cyan")
            # ONE ENTRY PER DAY: Check if we've already entered today (but allow re-entry after exits)
            # Only block if we've entered today, not if we've exited today
            if cache.get(day_key + "_entered_today"):
                _log(f"{symbol}: Already entered today, skipping entry", "cyan")
                return (0, None, None, "No clear signal")
            # Configurable RSI thresholds (defaults: 40 for long, 60 for short - more aggressive for strong trends)
            # Further relaxed to catch strong trends where pullbacks are shallow
            rsi_long_threshold = float(conditions.get("rsi_long_threshold", 40.0)) if conditions else 40.0
            rsi_short_threshold = float(conditions.get("rsi_short_threshold", 60.0)) if conditions else 60.0
            
            # Momentum entry: For very strong trends, allow more aggressive entries
            use_momentum_entry = conditions.get("use_momentum_entry", True) if conditions else True
            momentum_threshold_pct = float(conditions.get("momentum_threshold_pct", 3.0)) if conditions else 3.0
            momentum_rsi_threshold = float(conditions.get("momentum_rsi_threshold", 60.0)) if conditions else 60.0
            
            # Volume filter: require above-average volume for entries (configurable)
            # Made less restrictive - default False to allow more entries
            require_volume_confirmation = conditions.get("require_volume_confirmation", False) if conditions else False
            min_volume_ratio = float(conditions.get("min_volume_ratio", 0.5)) if conditions else 0.5  # Lowered from 0.8 to 0.5 for more entries
            
            # Trend strength filter: prefer entries when trend is strengthening
            # Made less restrictive - default False to allow more entries
            prefer_strong_trend = conditions.get("prefer_strong_trend", False) if conditions else False
            
            if is_bull_market:
                # Calculate how far above SMA we are
                price_above_sma_pct = ((current_close - sma_200) / sma_200 * 100.0) if sma_200 > 0 else 0.0
                
                # BULL MARKET: Enhanced entry modes with filters
                # 1. Pullback entry: RSI(2) < threshold (default 40) + volume confirmation
                # 2. Momentum entry: If price is above SMA (>3%), allow entry on RSI(2) < 60
                # 3. Strong trend entry: If price is >10% above SMA, allow entry on RSI(2) < 70
                # 4. RSI momentum bonus: Prefer entries when RSI is rising from oversold
                entry_condition_met = False
                entry_reason = ""
                
                # Volume check
                volume_ok = not require_volume_confirmation or volume_ratio >= min_volume_ratio
                
                # Trend strength check (prefer strong trends, but don't block weak ones)
                # Made more lenient - only block if trend is strongly declining (>-1%)
                trend_ok = not prefer_strong_trend or sma_200_slope > -1.0  # Allow if trend isn't strongly declining
                
                # RSI momentum bonus: Lower threshold if RSI is rising
                rsi_bonus = 5.0 if rsi_rising and not np.isnan(rsi_2_prev) else 0.0
                effective_rsi_threshold = rsi_long_threshold + rsi_bonus
                
                if rsi_2 < effective_rsi_threshold and volume_ok and trend_ok:
                    entry_condition_met = True
                    rsi_note = f" (rising)" if rsi_rising else ""
                    entry_reason = f"Pullback (RSI(2) < {effective_rsi_threshold:.1f}{rsi_note}, Vol: {volume_ratio:.2f}x)"
                elif use_momentum_entry and price_above_sma_pct > momentum_threshold_pct and volume_ok and trend_ok:
                    if price_above_sma_pct > 10.0 and rsi_2 < 70:
                        # Very strong trend: allow entry on RSI(2) < 70
                        entry_condition_met = True
                        entry_reason = f"Strong Trend (Price {price_above_sma_pct:.1f}% above SMA, RSI(2) < 70, Vol: {volume_ratio:.2f}x)"
                    elif rsi_2 < momentum_rsi_threshold:
                        # Moderate strong trend: allow entry on RSI(2) < 60
                        entry_condition_met = True
                        entry_reason = f"Momentum (Price {price_above_sma_pct:.1f}% above SMA, RSI(2) < {momentum_rsi_threshold:.1f}, Vol: {volume_ratio:.2f}x)"
                
                if entry_condition_met:
                    cache[position_key] = "long"
                    cache[entry_price_key] = current_close
                    cache[entry_date_key] = date_str
                    cache[day_key + "_entered_today"] = True  # Track entry, not just any trade
                    _log(
                        f"{symbol}: LONG ENTRY - {entry_reason} | "
                        f"Entry: {current_close:.2f}, RSI(2): {rsi_2:.1f}, SMA{sma_period_200}: {sma_200:.2f}, SMA{sma_period_5}: {sma_5:.2f}",
                        "green"
                    )
                    return (1, None, None, "Buy signal generated")
                else:
                    # Log why entry was rejected
                    rejection_reasons = []
                    if not volume_ok:
                        rejection_reasons.append(f"Volume too low ({volume_ratio:.2f}x < {min_volume_ratio:.2f}x)")
                    if not trend_ok:
                        rejection_reasons.append(f"Weak trend (slope: {sma_200_slope:.2f}%)")
                    if rsi_2 >= effective_rsi_threshold:
                        if not (use_momentum_entry and price_above_sma_pct > momentum_threshold_pct):
                            rejection_reasons.append(f"RSI too high ({rsi_2:.1f} >= {effective_rsi_threshold:.1f})")
                    if use_momentum_entry and price_above_sma_pct > momentum_threshold_pct:
                        if price_above_sma_pct <= 10.0 and rsi_2 >= momentum_rsi_threshold:
                            rejection_reasons.append(f"Momentum RSI too high ({rsi_2:.1f} >= {momentum_rsi_threshold:.1f})")
                        elif price_above_sma_pct > 10.0 and rsi_2 >= 70:
                            rejection_reasons.append(f"Strong trend RSI too high ({rsi_2:.1f} >= 70)")
                    elif use_momentum_entry and price_above_sma_pct <= momentum_threshold_pct:
                        rejection_reasons.append(f"Price not enough above SMA ({price_above_sma_pct:.1f}% <= {momentum_threshold_pct:.1f}%)")
                    
                    rejection_note = f" | Rejected: {', '.join(rejection_reasons)}" if rejection_reasons else ""
                    _log(f"{symbol}: BULL market entry condition not met | RSI(2): {rsi_2:.1f} ({rsi_momentum}), Price above SMA: {price_above_sma_pct:.1f}%, Vol: {volume_ratio:.2f}x, Trend slope: {sma_200_slope:.2f}%{rejection_note}", "cyan")
            
            else:
                # BEAR/FLAT MARKET: Short if RSI(2) > threshold (default 70, relaxed for stronger trends)
                # Note: Broker may not support shorts natively; this is tracked in cache
                _log(f"{symbol}: BEAR/FLAT market entry check | RSI(2): {rsi_2:.1f} (need > {rsi_short_threshold:.1f})", "cyan")
                if rsi_2 > rsi_short_threshold:
                    cache[position_key] = "short"
                    cache[entry_price_key] = current_close
                    cache[entry_date_key] = date_str
                    cache[day_key + "_entered_today"] = True  # Track entry, not just any trade
                    _log(
                        f"{symbol}: SHORT ENTRY - Bear market bounce | "
                        f"Entry: {current_close:.2f}, RSI(2): {rsi_2:.1f}, SMA{sma_period_200}: {sma_200:.2f}, SMA{sma_period_5}: {sma_5:.2f} "
                        f"(Note: Short positions require broker support)",
                        "yellow"
                    )
                    return (-1, None, None, "Sell signal generated")
                else:
                    _log(f"{symbol}: BEAR/FLAT market entry condition not met | RSI(2): {rsi_2:.1f} <= {rsi_short_threshold:.1f} (need > {rsi_short_threshold:.1f})", "cyan")
            
            # No position and no entry taken - log summary
            if is_bull_market:
                price_above_sma_pct = ((current_close - sma_200) / sma_200 * 100.0) if sma_200 > 0 else 0.0
                if use_momentum_entry and price_above_sma_pct > momentum_threshold_pct:
                    if price_above_sma_pct > 10.0:
                        _log(f"{symbol}: No LONG entry | RSI(2): {rsi_2:.1f} (need < {rsi_long_threshold:.1f} or < {momentum_rsi_threshold:.1f} or < 70 for strong trend) - Price {price_above_sma_pct:.1f}% above SMA", "cyan")
                    else:
                        _log(f"{symbol}: No LONG entry | RSI(2): {rsi_2:.1f} (need < {rsi_long_threshold:.1f} or < {momentum_rsi_threshold:.1f} for momentum) - Price {price_above_sma_pct:.1f}% above SMA", "cyan")
                else:
                    _log(f"{symbol}: No LONG entry | RSI(2): {rsi_2:.1f} (threshold: < {rsi_long_threshold:.1f}) - RSI too high for entry", "cyan")
            else:
                _log(f"{symbol}: No SHORT entry | RSI(2): {rsi_2:.1f} (threshold: > {rsi_short_threshold:.1f}) - RSI too low for entry", "cyan")
        else:
            entry_str = f"{entry_price:.2f}" if entry_price is not None else "N/A"
            _log(f"{symbol}: In {position_type.upper()} position, no new entry | Entry: {entry_str}, Current: {current_close:.2f}", "cyan")
        
        return (0, None, None, "No clear signal")

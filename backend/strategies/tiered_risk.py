# INTELLISTOCK_SCHEMA: {"strategy": "TieredRisk", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"atr_period": 14, "atr_multiplier": 2.0, "daily_loss_pct": 6, "time_stop_days": 10, "trail_sma_days": 20}}
# INTELLISTOCK_DESCRIPTION: Tiered risk management. Hard stop, daily loss limit, time-based stop, target 1 (2R, sell 50%), trailing stop (SMA), climax exit. Uses ATR for initial stop.
# DIFFICULTY: 3
"""
Tiered Risk Management Strategy - Detailed Risk Management Algorithm

RISK MANAGEMENT ALGORITHM - DETAILED SPECIFICATION

Initialization: When stock is purchased, capture entry price, entry date, initial shares, account size.
Calculate initial stop loss using ATR (14-period, 2.0x multiplier) or 8% below entry (whichever is tighter).

Daily Checks (in order):
1. Update tracking variables
2. Hard Stop Loss (highest priority)
3. Daily Loss Limit (6% of account)
4. Time-Based Stop (10 days, <3% profit)
5. Early Trend Break (50-day SMA, only before Target 1)
6. Target 1 - First Profit Take (2R, sell 50%)
7. Trailing Stop (20-day SMA, 2 consecutive closes below)
8. Climax Exit (RSI > 80, >70% from 200 SMA, declining volume)

Returns: 1 = buy (only when not blacklisted), 0 = hold, -1 = sell.
Can return (score, weight_override, size_hint). When blacklisted:
returns (0, 1.0) to always hold and not buy.
"""

from __future__ import annotations

import datetime
import numpy as np

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="TieredRisk")
except Exception:
    def _log(msg, color="white"):
        print(f"[TieredRisk] {msg}")


def _get_bar_ohlcv(b, key_short, key_long, fallback_key="c"):
    """Extract OHLCV value from bar dict."""
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


def _intraday_to_daily_bars(intraday_bars):
    """Convert intraday bars to daily aggregated bars."""
    if not intraday_bars:
        return []
    daily_dict = {}
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
            daily_dict[date_key] = {"t": dt_utc, "o": o, "h": h, "l": l, "c": c, "v": v}
        else:
            daily = daily_dict[date_key]
            daily["h"] = max(daily["h"], h)
            daily["l"] = min(daily["l"], l)
            daily["c"] = c
            daily["v"] += v
    return [daily_dict[k] for k in sorted(daily_dict.keys())]


def _sma(closes, period):
    """Simple Moving Average (last value) via TA-Lib."""
    if len(closes) < period:
        return np.nan
    import talib
    arr = np.asarray(closes, dtype=np.float64)
    result = talib.SMA(arr, timeperiod=period)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _atr_ema(bars, period=14):
    """ATR via TA-Lib (Wilder's smoothing). Accepts bar dicts, returns scalar."""
    if len(bars) < period + 1:
        return np.nan
    import talib
    high = np.array([_get_bar_ohlcv(b, "h", "high") for b in bars], dtype=np.float64)
    low = np.array([_get_bar_ohlcv(b, "l", "low") for b in bars], dtype=np.float64)
    close = np.array([_get_bar_ohlcv(b, "c", "close") for b in bars], dtype=np.float64)
    result = talib.ATR(high, low, close, timeperiod=period)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _rsi(closes, period=14):
    """RSI via TA-Lib (Wilder's smoothing). Returns scalar."""
    if len(closes) < period + 1:
        return np.nan
    import talib
    arr = np.asarray(closes, dtype=np.float64)
    result = talib.RSI(arr, timeperiod=period)
    return float(result[-1]) if not np.isnan(result[-1]) else np.nan


def _calculate_average_entry_price(trades, symbol):
    """Average entry price and current shares for symbol (FIFO cost basis)."""
    if not trades:
        return None, 0.0
    symbol_trades = [t for t in trades if t.get("ticker") == symbol]
    if not symbol_trades:
        return None, 0.0
    buy_queue = []
    current_shares = 0.0
    total_cost_basis = 0.0
    for trade in symbol_trades:
        action = trade.get("action")
        shares = float(trade.get("shares", 0))
        price = float(trade.get("price", 0))
        if action == "buy":
            buy_queue.append((shares, price))
            current_shares += shares
            total_cost_basis += shares * price
        elif action == "sell":
            remaining_to_sell = shares
            while remaining_to_sell > 0 and buy_queue:
                buy_shares, buy_price = buy_queue[0]
                if buy_shares <= remaining_to_sell:
                    total_cost_basis -= buy_shares * buy_price
                    current_shares -= buy_shares
                    remaining_to_sell -= buy_shares
                    buy_queue.pop(0)
                else:
                    total_cost_basis -= remaining_to_sell * buy_price
                    current_shares -= remaining_to_sell
                    buy_queue[0] = (buy_shares - remaining_to_sell, buy_price)
                    remaining_to_sell = 0
    if current_shares <= 0:
        return None, 0.0
    return total_cost_basis / current_shares, current_shares


def _get_entry_trade(trades, symbol):
    """Get the first buy trade for this symbol (entry date and price)."""
    if not trades:
        return None, None, None
    symbol_trades = [t for t in trades if t.get("ticker") == symbol and t.get("action") == "buy"]
    if not symbol_trades:
        return None, None, None
    # Get first buy (entry)
    entry_trade = symbol_trades[0]
    entry_price = float(entry_trade.get("price", 0))
    entry_timestamp = entry_trade.get("timestamp")
    if isinstance(entry_timestamp, datetime.datetime):
        entry_date = entry_timestamp.date()
    elif isinstance(entry_timestamp, str):
        try:
            entry_date = datetime.datetime.fromisoformat(entry_timestamp.replace("Z", "+00:00")).date()
        except:
            entry_date = None
    else:
        entry_date = None
    return entry_price, entry_date, entry_trade


def _get_initial_shares(trades, symbol):
    """Get initial shares purchased (sum of all buys before any sells)."""
    if not trades:
        return 0.0
    symbol_trades = sorted([t for t in trades if t.get("ticker") == symbol], 
                          key=lambda x: x.get("timestamp") or datetime.datetime.min)
    initial_shares = 0.0
    for trade in symbol_trades:
        if trade.get("action") == "buy":
            initial_shares += float(trade.get("shares", 0))
        elif trade.get("action") == "sell":
            initial_shares -= float(trade.get("shares", 0))
            if initial_shares <= 0:
                return 0.0
    return initial_shares


def _calculate_daily_loss(portfolio_emulator, current_time):
    """Calculate total realized losses from closed trades today (account-wide)."""
    if portfolio_emulator is None:
        return 0.0
    trades = portfolio_emulator.get_trade_history() or []
    if not trades:
        return 0.0
    today_date = current_time.date() if hasattr(current_time, "date") else current_time
    daily_loss = 0.0
    for trade in trades:
        trade_date = None
        ts = trade.get("timestamp")
        if isinstance(ts, datetime.datetime):
            trade_date = ts.date()
        elif isinstance(ts, str):
            try:
                trade_date = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
            except:
                pass
        if trade_date != today_date:
            continue
        if trade.get("action") == "sell":
            # Calculate P&L for this sell
            ticker = trade.get("ticker")
            sell_price = float(trade.get("price", 0))
            sell_shares = float(trade.get("shares", 0))
            # Find corresponding buy(s) using FIFO
            buy_trades = [t for t in trades if t.get("ticker") == ticker and t.get("action") == "buy"]
            buy_trades = sorted(buy_trades, key=lambda x: x.get("timestamp") or datetime.datetime.min)
            remaining_sell = sell_shares
            for buy_trade in buy_trades:
                if remaining_sell <= 0:
                    break
                buy_price = float(buy_trade.get("price", 0))
                buy_shares = float(buy_trade.get("shares", 0))
                if buy_shares <= remaining_sell:
                    pnl = (sell_price - buy_price) * buy_shares
                    daily_loss += min(0, pnl)  # Only losses
                    remaining_sell -= buy_shares
                else:
                    pnl = (sell_price - buy_price) * remaining_sell
                    daily_loss += min(0, pnl)
                    remaining_sell = 0
    return abs(daily_loss)  # Return positive loss amount


class TieredRisk:
    """
    Tiered Risk Management: Detailed risk management algorithm with ATR-based stops,
    profit targets, trailing stops, and multiple exit conditions.
    """

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        if strategy_cache is None:
            strategy_cache = {}
        cfg = config or {}
        
        # Configuration values (with defaults)
        atr_multiplier = float(cfg.get("atr_multiplier", 2.0))
        hard_stop_pct = float(cfg.get("hard_stop_pct", 8.0))  # 8% below entry
        target1_multiplier = float(cfg.get("target1_multiplier", 2.0))  # 2R
        target1_sell_pct = float(cfg.get("target1_sell_pct", 50.0)) / 100.0  # 50%
        stop_lock_pct = float(cfg.get("stop_lock_pct", 5.0))  # 5% above entry
        max_days_no_profit = int(cfg.get("max_days_no_profit", 10))
        min_profit_threshold_pct = float(cfg.get("min_profit_threshold_pct", 3.0))
        trailing_ma_period = int(cfg.get("trailing_ma_period", 20))
        consecutive_closes = int(cfg.get("consecutive_closes", 2))
        rsi_threshold = float(cfg.get("rsi_threshold", 80))
        distance_from_200sma_pct = float(cfg.get("distance_from_200sma_pct", 70.0))
        volume_decline_ratio = float(cfg.get("volume_decline_ratio", 80.0)) / 100.0  # 0.8
        daily_loss_limit_pct = float(cfg.get("daily_loss_limit_pct", 6.0)) / 100.0  # 6%
        blacklist_profit_threshold_pct = float(cfg.get("blacklist_profit_threshold_pct", 40.0))
        blacklist_days = int(cfg.get("blacklist_days", 30))

        cache = strategy_cache.setdefault(symbol, {})
        now = current_time
        if not hasattr(now, "date"):
            if isinstance(now, str):
                try:
                    now = datetime.datetime.fromisoformat(now.replace("Z", "+00:00"))
                except:
                    pass

        # Check blacklist
        blacklisted_until = cache.get("blacklisted_until")
        if blacklisted_until is not None:
            if isinstance(blacklisted_until, str):
                try:
                    blacklisted_until = datetime.datetime.fromisoformat(blacklisted_until.replace("Z", "+00:00"))
                except:
                    pass
            if isinstance(blacklisted_until, datetime.datetime) and blacklisted_until > now:
                _log(f"[TIERED] {symbol}: blacklisted until {blacklisted_until}; hold (weight 1.0)", "cyan")
                return (0, 1.0, None, 'Holding position (blacklisted)')
            cache["blacklisted_until"] = None

        if portfolio_emulator is None:
            return (0, None, None, 'Holding position')

        positions = portfolio_emulator.get_positions() or {}
        shares = positions.get(symbol, 0.0) or 0.0
        trades = portfolio_emulator.get_trade_history() or []

        # No position: allow buy (default size), no override
        if shares <= 0:
            return (1, None, None, 'No position, buy allowed')

        if price is None or float(price) <= 0:
            return (0, None, None, 'Holding position')
        current_price = float(price)

        # Get entry information
        entry_price, entry_date, entry_trade = _get_entry_trade(trades, symbol)
        if entry_price is None or entry_date is None:
            # Try to get from cache
            entry_price = cache.get("entry_price")
            entry_date = cache.get("entry_date")
            if entry_date and isinstance(entry_date, str):
                try:
                    entry_date = datetime.datetime.fromisoformat(entry_date.replace("Z", "+00:00")).date()
                except:
                    entry_date = None
            if entry_price is None or entry_date is None:
                # Initialize from current position
                avg_entry, _ = _calculate_average_entry_price(trades, symbol)
                if avg_entry:
                    entry_price = avg_entry
                    entry_date = now.date() if hasattr(now, "date") else now
                else:
                    return (0, None, None, 'Holding position')

        # Detect new position: if we have shares but cache doesn't have initial_shares, initialize
        if cache.get("initial_shares") is None and shares > 0:
            # This is a new position - reinitialize
            initial_shares = _get_initial_shares(trades, symbol)
            if initial_shares <= 0:
                initial_shares = shares
            cache["initial_shares"] = initial_shares
            cache["entry_price"] = entry_price
            cache["entry_date"] = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
            cache["shares_remaining"] = shares
            cache["target1_hit"] = False
            cache["trailing_stop_enabled"] = False
            cache["highest_price_since_entry"] = entry_price
            cache["days_in_trade"] = 0
            # Calculate initial stop loss using ATR
            initial_stop_loss = None
            if data and symbol in data:
                bars = data[symbol]
                if bars and len(bars) > 0:
                    daily_bars = bars
                    if len(bars) > 0 and _bar_datetime(bars[0]):
                        first_date = _bar_datetime(bars[0]).date()
                        last_date = _bar_datetime(bars[-1]).date()
                        if first_date == last_date and len(bars) > 1:
                            daily_bars = _intraday_to_daily_bars(bars)
                    if len(daily_bars) >= 15:
                        atr_value = _atr_ema(daily_bars, period=14)
                        if not np.isnan(atr_value):
                            atr_stop = entry_price - (atr_multiplier * atr_value)
                            pct_stop = entry_price * (1.0 - hard_stop_pct / 100.0)
                            initial_stop_loss = max(atr_stop, pct_stop)
            if initial_stop_loss is None:
                initial_stop_loss = entry_price * (1.0 - hard_stop_pct / 100.0)
            cache["initial_stop_loss"] = initial_stop_loss
            cache["current_stop_loss"] = initial_stop_loss
            account_size = portfolio_emulator.get_portfolio_value({symbol: current_price}) if portfolio_emulator else 100000.0
            cache["account_size"] = account_size

        # Restore from cache
        initial_shares = cache.get("initial_shares", shares)
        # Sync shares_remaining with actual shares (after our sells or external sells)
        cached_shares_remaining = cache.get("shares_remaining", shares)
        shares_remaining = min(cached_shares_remaining, shares)
        # If actual shares is less than cached, update cache (sell was executed)
        if shares < cached_shares_remaining:
            shares_remaining = shares
        cache["shares_remaining"] = shares_remaining
        target1_hit = cache.get("target1_hit", False)
        trailing_stop_enabled = cache.get("trailing_stop_enabled", False)
        highest_price = cache.get("highest_price_since_entry", entry_price)
        initial_stop_loss = cache.get("initial_stop_loss", entry_price * (1.0 - hard_stop_pct / 100.0))
        current_stop_loss = cache.get("current_stop_loss", initial_stop_loss)
        account_size = cache.get("account_size", 100000.0)

        # CHECK 1: Update tracking variables
        if hasattr(entry_date, "date"):
            entry_date_val = entry_date.date()
        else:
            entry_date_val = entry_date
        if hasattr(now, "date"):
            current_date = now.date()
        else:
            current_date = now
        days_in_trade = (current_date - entry_date_val).days if entry_date_val else 0
        cache["days_in_trade"] = days_in_trade
        cache["highest_price_since_entry"] = max(highest_price, current_price)
        highest_price = cache["highest_price_since_entry"]
        current_profit_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
        current_profit_dollars = (current_price - entry_price) * shares_remaining

        # Get daily bars for indicators
        daily_bars = None
        closes = None
        if data and symbol in data:
            bars = data[symbol]
            if bars and len(bars) > 0:
                daily_bars = bars
                if len(bars) > 0 and _bar_datetime(bars[0]):
                    first_date = _bar_datetime(bars[0]).date()
                    last_date = _bar_datetime(bars[-1]).date()
                    if first_date == last_date and len(bars) > 1:
                        daily_bars = _intraday_to_daily_bars(bars)
                closes = [_get_bar_ohlcv(b, "c", "close") for b in daily_bars] if daily_bars else None

        # CHECK 2: Hard Stop Loss (HIGHEST PRIORITY)
        if current_price < current_stop_loss:
            _log(f"[TIERED] {symbol}: Hard stop loss hit (price {current_price:.2f} < stop {current_stop_loss:.2f}) -> sell all", "red")
            cache["shares_remaining"] = 0
            cache["entry_price"] = None
            cache["target1_hit"] = None
            cache["trailing_stop_enabled"] = None
            return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # CHECK 3: Daily Loss Limit (account-wide)
        daily_loss = _calculate_daily_loss(portfolio_emulator, now)
        if daily_loss > 0:
            max_daily_loss = account_size * daily_loss_limit_pct
            if daily_loss >= max_daily_loss:
                _log(f"[TIERED] {symbol}: Daily loss limit reached ({daily_loss:.2f} >= {max_daily_loss:.2f}) -> sell all", "red")
                cache["shares_remaining"] = 0
                return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # CHECK 4: Time-Based Stop
        if days_in_trade > max_days_no_profit and current_profit_pct < min_profit_threshold_pct:
            _log(f"[TIERED] {symbol}: Time stop (>{max_days_no_profit} days, <{min_profit_threshold_pct}% profit) -> sell all", "yellow")
            cache["shares_remaining"] = 0
            return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # CHECK 5: Early Trend Break (only before Target 1)
        if not target1_hit and daily_bars and closes and len(closes) >= 50:
            sma_50 = _sma(closes, 50)
            if not np.isnan(sma_50) and current_price < sma_50:
                _log(f"[TIERED] {symbol}: Early trend break (price {current_price:.2f} < 50 SMA {sma_50:.2f}) -> sell all", "yellow")
                cache["shares_remaining"] = 0
                return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # CHECK 6: Target 1 - First Profit Take (2R)
        if not target1_hit:
            risk_per_share = entry_price - initial_stop_loss
            target1_price = entry_price + (target1_multiplier * risk_per_share)
            if current_price >= target1_price:
                sell_shares_pct = target1_sell_pct
                _log(f"[TIERED] {symbol}: Target 1 hit (2R at {target1_price:.2f}) -> sell {sell_shares_pct*100:.0f}% of {shares_remaining:.2f} shares", "green")
                cache["target1_hit"] = True
                cache["trailing_stop_enabled"] = True
                cache["current_stop_loss"] = entry_price * (1.0 + stop_lock_pct / 100.0)
                # shares_remaining will be updated after broker executes the sell
                return (-1, 1.0, {"sell_fraction": sell_shares_pct}, 'Target 1 hit (partial sell)')  # Always override on sell signals

        # CHECK 7: Trailing Stop (only after Target 1)
        if trailing_stop_enabled and daily_bars and closes and len(closes) >= trailing_ma_period + consecutive_closes:
            # Check if price closed below SMA for consecutive days
            # Yesterday's close vs yesterday's SMA, today's close vs today's SMA
            closes_below_count = 0
            for i in range(consecutive_closes):
                idx = -(consecutive_closes - i)  # -2, -1 for 2 consecutive
                if idx < 0 and abs(idx) <= len(closes):
                    price_at_idx = closes[idx]
                    # Calculate SMA up to that index
                    closes_up_to_idx = closes[:len(closes)+idx+1]  # +1 because idx is negative
                    if len(closes_up_to_idx) >= trailing_ma_period:
                        sma_at_idx = _sma(closes_up_to_idx, trailing_ma_period)
                        if not np.isnan(sma_at_idx) and price_at_idx < sma_at_idx:
                            closes_below_count += 1
            if closes_below_count >= consecutive_closes:
                total_profit_pct = current_profit_pct
                do_blacklist = total_profit_pct > blacklist_profit_threshold_pct
                _log(f"[TIERED] {symbol}: Trailing stop ({consecutive_closes} closes below {trailing_ma_period}-day SMA)" + (f", blacklist {blacklist_days}d" if do_blacklist else ""), "green")
                if do_blacklist:
                    cache["blacklisted_until"] = (now + datetime.timedelta(days=blacklist_days)).isoformat()
                cache["shares_remaining"] = 0
                return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # CHECK 8: Climax Exit (only after Target 1)
        if trailing_stop_enabled and daily_bars and closes:
            if len(closes) >= 200:
                rsi_14 = _rsi(closes, 14)
                sma_200 = _sma(closes, 200)
                if not np.isnan(rsi_14) and not np.isnan(sma_200):
                    distance_from_200sma = ((current_price - sma_200) / sma_200 * 100.0) if sma_200 > 0 else 0.0
                    # Check volume decline
                    volumes = [_get_bar_ohlcv(b, "v", "volume") for b in daily_bars]
                    if len(volumes) >= 5:
                        avg_volume_5 = np.mean(volumes[-5:])
                        current_volume = volumes[-1] if volumes else 0.0
                        volume_declining = current_volume < (avg_volume_5 * volume_decline_ratio)
                        if rsi_14 > rsi_threshold and distance_from_200sma > distance_from_200sma_pct and volume_declining:
                            _log(f"[TIERED] {symbol}: Climax exit (RSI {rsi_14:.1f} > {rsi_threshold}, {distance_from_200sma:.1f}% from 200 SMA, volume declining) -> sell all, blacklist", "green")
                            cache["blacklisted_until"] = (now + datetime.timedelta(days=blacklist_days)).isoformat()
                            cache["shares_remaining"] = 0
                            return (-1, 1.0, {"sell_fraction": 1.0}, 'Stop/Exit triggered (sell 100%)')

        # Update shares_remaining in cache
        cache["shares_remaining"] = shares_remaining

        # Hold
        return (0, None, None, 'Holding position')

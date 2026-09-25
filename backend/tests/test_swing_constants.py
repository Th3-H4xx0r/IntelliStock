"""Every ported constant equals ST's, and the header defaults are ST's values
under the spec's key names (spec §5)."""
import importlib.util
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import constants as C  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st(name):
    """Load a vendored ST module by path, never as a package import."""
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_swing_constants_equal_st():
    st = _st("paper_trader_pure")
    for name in ("UNIVERSE", "POSITION_SIZE_PCT", "PROFIT_TARGET", "STOP_LOSS",
                 "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SMA200_PERIOD",
                 "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL", "VOL_AVG_PERIOD",
                 "ADX_PERIOD", "ADX_TREND_MIN", "VIX_FEAR_THRESHOLD",
                 "SPY_BUFFER", "WARMUP_DAYS", "MAX_POSITIONS",
                 "BEAR_REGIME_DAYS", "DEFENSIVE_UNIVERSE",
                 "EARNINGS_HARD_BLOCK", "MAX_PER_SECTOR"):
        assert getattr(C, name) == getattr(st, name), name


def test_wheel_constants_equal_st():
    st = _st("wheel_trader_pure")
    pairs = {
        "WHEEL_UNIVERSE": "WHEEL_UNIVERSE", "WHEEL_RSI_PERIOD": "RSI_PERIOD",
        "RSI_MAX": "RSI_MAX", "RSI_MIN": "RSI_MIN",
        "IV_PERCENTILE_MIN": "IV_PERCENTILE_MIN",
        "MIN_OPTION_PREMIUM_PCT": "MIN_OPTION_PREMIUM_PCT",
        "MAX_DELTA": "MAX_DELTA", "TARGET_DELTA": "TARGET_DELTA",
        "DAYS_TO_EXPIRY": "DAYS_TO_EXPIRY", "WHEEL_WARMUP_DAYS": "WARMUP_DAYS",
        "SMA50_PERIOD": "SMA50_PERIOD", "ATR_PERIOD": "ATR_PERIOD",
        "STRIKE_ATR_MULT": "STRIKE_ATR_MULT",
        "MAX_COLLATERAL_PCT": "MAX_COLLATERAL_PCT",
        "AUTO_COVERED_CALL": "AUTO_COVERED_CALL",
        "WHEEL_APPROVE_THRESHOLD": "APPROVE_THRESHOLD",
        "WHEEL_REVIEW_THRESHOLD": "REVIEW_THRESHOLD",
        "WHEEL_SECTOR_MAP": "WHEEL_SECTOR_MAP",
        "WHEEL_MAX_PER_SECTOR": "WHEEL_MAX_PER_SECTOR",
    }
    for ours, theirs in pairs.items():
        assert getattr(C, ours) == getattr(st, theirs), ours


def test_the_remaining_st_constants_carry_st_values():
    # ai_analyst.py:41-43, iv_collector.py:50-52, calibration.py:22-25,
    # app.py:1341-1342, wheel_trader.py:785 and :590-602.
    assert (C.AI_RSI_PERIOD, C.AI_APPROVE_THRESHOLD, C.AI_REVIEW_THRESHOLD) == (14, 75, 50)
    assert (C.TARGET_DTE, C.RANK_WINDOW, C.MIN_RANK_ROWS) == (30, 252, 60)
    assert (C.GATE_TRADES, C.MIN_BUCKET_N) == (20, 5)
    assert C.BUCKETS == [(50, 64), (65, 74), (75, 84), (85, 100)]
    assert (C.AUTO_CLOSE_ITM_PCT, C.AUTO_CLOSE_DTE2_PCT) == (10.0, 5.0)
    assert C.MIN_DTE == 7
    assert (C.LIMIT_BID_MIN, C.LIMIT_BID_MULT, C.LIMIT_CLOSE_MULT) == (0.05, 0.95, 0.90)
    assert C.ETF_NO_EARNINGS == {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
    assert C.YFINANCE_FLAKY == {"NVDA", "TSLA", "AMD"}


SWING_KEYS = {
    "strategy_swing_enabled", "rsi_period", "rsi_entry_max", "rsi_overbought",
    "sma_long", "macd_fast", "macd_slow", "macd_signal", "vol_avg_period",
    "adx_period", "adx_min", "spy_buffer", "vix_max", "position_size_pct",
    "max_positions", "max_per_sector", "profit_target", "stop_loss",
    "bear_regime_days", "defensive_universe", "earnings_hard_block_days",
    "ai_gate_enabled", "ai_approve_threshold", "ai_review_threshold",
    "conviction_llm_model_id", "scan_time_et", "live_max_order_fraction",
    "live_max_symbol_fraction", "live_max_leveraged_fraction",
    "live_soft_drawdown", "live_hard_drawdown", "live_kill_drawdown",
    "honour_single_position_cap", "broker_max_single_position_pct",
}
WHEEL_KEYS = {
    "strategy_wheel_enabled", "rsi_min", "rsi_max", "sma_trend", "atr_period",
    "strike_atr_mult", "min_premium_pct", "target_delta", "days_to_expiry",
    "max_collateral_pct", "max_per_sector", "auto_covered_call",
    "approve_threshold", "review_threshold", "earnings_block_days",
    "limit_bid_mult", "scan_weekday", "scan_time_et", "monitor_time_et",
    "conviction_llm_model_id",
}


def test_swing_defaults_are_st_values_under_the_spec_keys():
    d = C.SWING_DEFAULTS
    assert set(d) == SWING_KEYS
    assert d["strategy_swing_enabled"] is False
    assert (d["rsi_period"], d["rsi_entry_max"], d["rsi_overbought"]) == (
        C.RSI_PERIOD, C.RSI_OVERSOLD, C.RSI_OVERBOUGHT)
    assert d["sma_long"] == C.SMA200_PERIOD
    assert (d["macd_fast"], d["macd_slow"], d["macd_signal"]) == (
        C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    assert (d["vol_avg_period"], d["adx_period"], d["adx_min"]) == (
        C.VOL_AVG_PERIOD, C.ADX_PERIOD, C.ADX_TREND_MIN)
    assert (d["spy_buffer"], d["vix_max"]) == (C.SPY_BUFFER, C.VIX_FEAR_THRESHOLD)
    assert (d["position_size_pct"], d["max_positions"], d["max_per_sector"]) == (
        C.POSITION_SIZE_PCT, C.MAX_POSITIONS, C.MAX_PER_SECTOR)
    assert (d["profit_target"], d["stop_loss"]) == (C.PROFIT_TARGET, C.STOP_LOSS)
    assert d["bear_regime_days"] == C.BEAR_REGIME_DAYS
    assert d["defensive_universe"] == C.DEFENSIVE_UNIVERSE
    assert d["earnings_hard_block_days"] == C.EARNINGS_HARD_BLOCK
    assert d["ai_gate_enabled"] is True
    assert (d["ai_approve_threshold"], d["ai_review_threshold"]) == (75, 50)
    assert d["conviction_llm_model_id"] == "" and d["scan_time_et"] == "09:15"
    assert (d["live_max_order_fraction"], d["live_max_symbol_fraction"]) == (0.2, 0.2)
    assert d["honour_single_position_cap"] is True
    assert d["broker_max_single_position_pct"] == 0.2
    assert 0 < d["live_soft_drawdown"] < d["live_hard_drawdown"] < d["live_kill_drawdown"] < 1


def test_wheel_defaults_are_st_values_under_the_spec_keys():
    d = C.WHEEL_DEFAULTS
    assert set(d) == WHEEL_KEYS
    assert d["strategy_wheel_enabled"] is False
    assert (d["rsi_min"], d["rsi_max"], d["sma_trend"], d["atr_period"]) == (
        C.RSI_MIN, C.RSI_MAX, C.SMA50_PERIOD, C.ATR_PERIOD)
    assert (d["strike_atr_mult"], d["min_premium_pct"], d["target_delta"]) == (
        C.STRIKE_ATR_MULT, C.MIN_OPTION_PREMIUM_PCT, C.TARGET_DELTA)
    assert (d["days_to_expiry"], d["max_collateral_pct"], d["max_per_sector"]) == (
        C.DAYS_TO_EXPIRY, C.MAX_COLLATERAL_PCT, C.WHEEL_MAX_PER_SECTOR)
    assert d["auto_covered_call"] is C.AUTO_COVERED_CALL is False
    assert (d["approve_threshold"], d["review_threshold"]) == (
        C.WHEEL_APPROVE_THRESHOLD, C.WHEEL_REVIEW_THRESHOLD)
    assert (d["earnings_block_days"], d["limit_bid_mult"]) == (7, C.LIMIT_BID_MULT)
    assert (d["scan_weekday"], d["scan_time_et"], d["monitor_time_et"]) == (0, "10:30", "15:45")
    assert d["conviction_llm_model_id"] == ""

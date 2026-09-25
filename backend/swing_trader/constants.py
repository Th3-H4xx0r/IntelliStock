"""Every ST constant, with ST's value, and the two strategy header defaults.

Ported from github.com/tmasters2876/swing-trader @ c2afa71 ("ST"):
    paper_trader.py:63-81, 85-89, 117   swing constants (verbatim)
    wheel_trader.py:76-80, 83-120       wheel constants
    ai_analyst.py:41-43                 AI thresholds
    iv_collector.py:50-52               IV snapshot constants (verbatim)
    calibration.py:22-25                calibration gate (verbatim)
    wheel_trader.py:892-893             get_candidates' earnings exemptions (hoisted)
    wheel_trader.py:785, 590-602        next_friday's MIN_DTE, the limit ladder (hoisted)
    app.py:1341-1342                    wheel monitor auto-close thresholds (hoisted)
Names that collide across ST files carry a WHEEL_ or AI_ prefix here; the
values do not change.
"""

# -- paper_trader.py:63-81 -------------------------------------------------
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
# -- paper_trader.py:85-89 -------------------------------------------------
WARMUP_DAYS   = 300  # ~200 trading days needed for SMA200 warmup
MAX_POSITIONS = 8    # max concurrent open positions
BEAR_REGIME_DAYS      = 10   # consecutive blocked days before switching to defensive mode
DEFENSIVE_UNIVERSE    = ["XLP", "XLU", "XLV", "GLD", "SHY"]
EARNINGS_HARD_BLOCK   = 5    # days — hard skip in entry loop before AI is called
# -- paper_trader.py:117 ---------------------------------------------------
MAX_PER_SECTOR = 1   # hard cap — only 1 position per sector at a time

# -- wheel_trader.py:76-80 -------------------------------------------------
WHEEL_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "JPM",  "V",     "JNJ",  "WMT",
    "SPY",  "QQQ",  "GLD",   "XLE",  "AMD",
]

# -- wheel_trader.py:83-100 (RSI_PERIOD and WARMUP_DAYS renamed) ------------
WHEEL_RSI_PERIOD       = 14
RSI_MAX                = 60        # only sell puts when stock is NOT overbought
RSI_MIN                = 30        # avoid if deeply oversold (could keep falling)
IV_PERCENTILE_MIN      = 20        # want elevated IV for premium (approximated via ATR%)
MIN_OPTION_PREMIUM_PCT = 0.005     # minimum 0.5% of stock price as weekly premium (approx)
MAX_DELTA              = 0.35      # target 0.20–0.35 delta puts (approximated by strike selection)
TARGET_DELTA           = 0.25      # target put delta — 0.25 = ~75% probability of expiring worthless
DAYS_TO_EXPIRY         = 7         # target weekly options (next Friday)
WHEEL_WARMUP_DAYS      = 60        # days of history needed for indicators
SMA50_PERIOD           = 50        # trend filter — only sell puts above 50-day SMA
ATR_PERIOD             = 14        # for IV approximation and strike selection
STRIKE_ATR_MULT        = 0.5       # put strike = current price − (ATR × this multiple)
MAX_COLLATERAL_PCT     = 0.25      # max collateral per name as fraction of account equity
                                   # (strike × 100 × contracts must fit — caps assignment risk)
AUTO_COVERED_CALL      = False     # R4-02: dry-run mode. When False, assignment detection
                                   # still runs and notifies what WOULD be placed, but no
                                   # order is submitted. Flip to True after the first real
                                   # assignment's dry-run notification has been reviewed.

# -- wheel_trader.py:102-105 (renamed: they collide with ai_analyst's) ------
# AI thresholds — same pattern as ai_analyst.py
WHEEL_APPROVE_THRESHOLD = 75       # score ≥ 75 → log as recommended
WHEEL_REVIEW_THRESHOLD  = 50       # score 50–74 → log as pending review
                                   # score < 50 → reject

# -- wheel_trader.py:107-120 -----------------------------------------------
WHEEL_SECTOR_MAP: dict[str, str] = {
    "AAPL": "tech",  "MSFT": "tech",  "NVDA": "tech",  "AMD": "tech",
    "GOOGL": "tech", "META": "tech",  "AMZN": "tech",  "TSLA": "tech",
    "NFLX": "tech",  "ADBE": "tech",  "CRM": "tech",   "ORCL": "tech",
    "INTC": "tech",  "QCOM": "tech",  "TXN": "tech",   "AVGO": "tech",
    "JPM": "finance","BAC": "finance","GS": "finance",  "MS": "finance",
    "WFC": "finance","C": "finance",  "BLK": "finance", "BX": "finance",
    "XOM": "energy", "CVX": "energy", "COP": "energy",  "SLB": "energy",
    "XLE": "energy", "OXY": "energy",
    "JNJ": "health", "UNH": "health", "PFE": "health",  "MRK": "health",
    "ABT": "health", "TMO": "health", "MDT": "health",  "LLY": "health",
    "SPY": "broad",  "QQQ": "broad",  "GLD": "commodity",
}
WHEEL_MAX_PER_SECTOR = 2   # allow up to 2 per sector for wheel (more diversified than swing)

# -- ai_analyst.py:41-43 (renamed) -----------------------------------------
AI_RSI_PERIOD       = 14
AI_APPROVE_THRESHOLD = 75
AI_REVIEW_THRESHOLD  = 50

# -- iv_collector.py:50-52 -------------------------------------------------
TARGET_DTE       = 30    # sample the expiry nearest 30 days out
RANK_WINDOW      = 252   # trailing rows (~52 weeks) for IV Rank
MIN_RANK_ROWS    = 60    # below this, _load_iv_rank returns None (insufficient history)

# -- calibration.py:22-25 --------------------------------------------------
GATE_TRADES      = 20    # closed scored trades needed for the go-live calibration call
MIN_BUCKET_N     = 5     # below this a bucket is statistically meaningless

BUCKETS = [(50, 64), (65, 74), (75, 84), (85, 100)]

# -- hoisted locals ----------------------------------------------------------
# wheel_trader.py:892-893 (get_candidates)
ETF_NO_EARNINGS = {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
YFINANCE_FLAKY  = {"NVDA", "TSLA", "AMD"}  # intermittent after-hours data issues
# wheel_trader.py:785 (next_friday)
MIN_DTE = 7
# wheel_trader.py:590-602 (place_put_order's limit-price ladder)
LIMIT_BID_MIN = 0.05       # a live bid above this prices the order
LIMIT_BID_MULT = 0.95      # live bid × 0.95
LIMIT_CLOSE_MULT = 0.90    # else stale close × 0.90, else est_premium
# app.py:1341-1342 (api_check_wheel_positions)
AUTO_CLOSE_ITM_PCT  = 10.0   # ≥10% ITM at any DTE → close immediately
AUTO_CLOSE_DTE2_PCT = 5.0    # ≥5% ITM with ≤2 DTE → not recovering in time
# spec §5.2: the IV snapshot runs once per session at or after 09:15 ET
IV_SNAPSHOT_TIME_ET = "09:15"

# -- strategy headers (spec §5.1 / §5.2) ------------------------------------
#: StrategySwing's DEFAULTS. The INTELLISTOCK_SCHEMA header of
#: backend/strategies/strategy_swing.py is generated from this dict by
#: scripts/strategy_swing_sync_schema.py and a test pins them equal.
SWING_DEFAULTS = {
    "strategy_swing_enabled": False,
    "rsi_period": RSI_PERIOD,
    "rsi_entry_max": RSI_OVERSOLD,
    "rsi_overbought": RSI_OVERBOUGHT,
    "sma_long": SMA200_PERIOD,
    "macd_fast": MACD_FAST,
    "macd_slow": MACD_SLOW,
    "macd_signal": MACD_SIGNAL,
    "vol_avg_period": VOL_AVG_PERIOD,
    "adx_period": ADX_PERIOD,
    "adx_min": ADX_TREND_MIN,
    "spy_buffer": SPY_BUFFER,
    "vix_max": VIX_FEAR_THRESHOLD,
    "position_size_pct": POSITION_SIZE_PCT,
    "max_positions": MAX_POSITIONS,
    "max_per_sector": MAX_PER_SECTOR,
    "profit_target": PROFIT_TARGET,
    "stop_loss": STOP_LOSS,
    "bear_regime_days": BEAR_REGIME_DAYS,
    "defensive_universe": list(DEFENSIVE_UNIVERSE),
    "earnings_hard_block_days": EARNINGS_HARD_BLOCK,
    "ai_gate_enabled": True,
    "ai_approve_threshold": AI_APPROVE_THRESHOLD,
    "ai_review_threshold": AI_REVIEW_THRESHOLD,
    # Opt-in, for a short experiment: the AI gate in a BACKTEST, over
    # point-in-time inputs only (no news, web search or earnings lookup),
    # stopping after ai_backtest_max_calls model calls a run.
    "ai_gate_in_backtest": False,
    "ai_backtest_max_calls": 300,
    "conviction_llm_model_id": "",
    "scan_time_et": "09:15",
    # The live envelope, in EB's form (spec §5.1). The lane is sized at 12.5%
    # per name, so 20% per order and per symbol only ever refuses a mistake;
    # the drawdown rungs are EB's. The swing universe holds no leveraged ETF;
    # 0.2 matches plan A-live's `_SW_DEFAULTS` row in defaults_by_lane.
    "live_max_order_fraction": 0.2,
    "live_max_symbol_fraction": 0.2,
    "live_max_leveraged_fraction": 0.2,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
    # BROKER-side keys (backtest_engine / broker read them off the lane).
    "honour_single_position_cap": True,
    "broker_max_single_position_pct": 0.2,
}

#: StrategyWheel's DEFAULTS; same generation and pin as SWING_DEFAULTS.
WHEEL_DEFAULTS = {
    "strategy_wheel_enabled": False,
    "rsi_min": RSI_MIN,
    "rsi_max": RSI_MAX,
    "sma_trend": SMA50_PERIOD,
    "atr_period": ATR_PERIOD,
    "strike_atr_mult": STRIKE_ATR_MULT,
    "min_premium_pct": MIN_OPTION_PREMIUM_PCT,
    "target_delta": TARGET_DELTA,
    "days_to_expiry": DAYS_TO_EXPIRY,
    "max_collateral_pct": MAX_COLLATERAL_PCT,
    "max_per_sector": WHEEL_MAX_PER_SECTOR,
    "auto_covered_call": AUTO_COVERED_CALL,
    "approve_threshold": WHEEL_APPROVE_THRESHOLD,
    "review_threshold": WHEEL_REVIEW_THRESHOLD,
    "earnings_block_days": 7,
    "limit_bid_mult": LIMIT_BID_MULT,
    "scan_weekday": 0,
    "scan_time_et": "10:30",
    "monitor_time_et": "15:45",
    "conviction_llm_model_id": "",
}

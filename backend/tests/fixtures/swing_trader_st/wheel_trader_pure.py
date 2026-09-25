"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, wheel_trader.py.
Copied verbatim: lines 76-80, 83-120, 704, 720-741, 765-834, 839-953,
958-1072, 1220-1252. Not reproduced: the Alpaca and Anthropic clients,
dotenv, tabulate, notify and the print rebinding. Module attributes a test
drives: yf, datetime (next_friday reads datetime.now(ET)),
_get_trading_client (the calendar), _get_client and _fetch_news (the scorer),
WHEEL_COMPLETE_MARKER.
"""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from alpaca.trading.requests import GetCalendarRequest

yf = None
MODEL = "claude-sonnet-4-6"
WHEEL_COMPLETE_MARKER = "last_wheel_scan_complete.txt"


def _get_trading_client():
    raise RuntimeError("a test must inject _get_trading_client")


def _get_client():
    raise RuntimeError("a test must inject _get_client")


def _fetch_news(symbol):
    raise RuntimeError("a test must inject _fetch_news")


WHEEL_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "JPM",  "V",     "JNJ",  "WMT",
    "SPY",  "QQQ",  "GLD",   "XLE",  "AMD",
]

RSI_PERIOD             = 14
RSI_MAX                = 60        # only sell puts when stock is NOT overbought
RSI_MIN                = 30        # avoid if deeply oversold (could keep falling)
IV_PERCENTILE_MIN      = 20        # want elevated IV for premium (approximated via ATR%)
MIN_OPTION_PREMIUM_PCT = 0.005     # minimum 0.5% of stock price as weekly premium (approx)
MAX_DELTA              = 0.35      # target 0.20–0.35 delta puts (approximated by strike selection)
TARGET_DELTA           = 0.25      # target put delta — 0.25 = ~75% probability of expiring worthless
DAYS_TO_EXPIRY         = 7         # target weekly options (next Friday)
WARMUP_DAYS            = 60        # days of history needed for indicators
SMA50_PERIOD           = 50        # trend filter — only sell puts above 50-day SMA
ATR_PERIOD             = 14        # for IV approximation and strike selection
STRIKE_ATR_MULT        = 0.5       # put strike = current price − (ATR × this multiple)
MAX_COLLATERAL_PCT     = 0.25      # max collateral per name as fraction of account equity
                                   # (strike × 100 × contracts must fit — caps assignment risk)
AUTO_COVERED_CALL      = False     # R4-02: dry-run mode. When False, assignment detection
                                   # still runs and notifies what WOULD be placed, but no
                                   # order is submitted. Flip to True after the first real
                                   # assignment's dry-run notification has been reviewed.

# AI thresholds — same pattern as ai_analyst.py
APPROVE_THRESHOLD   = 75           # score ≥ 75 → log as recommended
REVIEW_THRESHOLD    = 50           # score 50–74 → log as pending review
                                   # score < 50 → reject

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

ET = ZoneInfo("America/New_York")


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def next_friday() -> str:
    """
    Return the date string (YYYY-MM-DD) of the next valid options expiry
    Friday that is at least MIN_DTE days away.

    Uses Alpaca's market calendar API to determine valid trading days —
    this handles all US market holidays, observed holidays, and unexpected
    closures automatically without a static holiday list.

    Logic:
    1. Find the next Friday that is MIN_DTE+ days away
    2. Ask Alpaca if that Friday is a trading day
    3. If yes — use it
    4. If no — check the Thursday of that week (exchange sometimes moves
       expiry to Thursday before a holiday Friday)
    5. If Thursday is also closed — skip to the following Friday and repeat
    Falls back to the pure date calculation if Alpaca calendar is unavailable.
    """
    from datetime import date as _date

    MIN_DTE = 7

    today = datetime.now(ET).date()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    candidate = today + timedelta(days=days_ahead)

    # Enforce MIN_DTE
    if (candidate - today).days < MIN_DTE:
        candidate += timedelta(days=7)

    # Ask Alpaca's calendar — handles all holidays automatically
    try:
        client     = _get_trading_client()
        # Fetch calendar for a 3-week window around candidate
        cal_req    = GetCalendarRequest(
            start=str(candidate - timedelta(days=1)),
            end=str(candidate + timedelta(days=14)),
        )
        calendar   = client.get_calendar(cal_req)
        # Build a set of valid trading dates in the window
        trading_days = {_date.fromisoformat(str(c.date)) for c in calendar}

        max_attempts = 8   # safety limit — never loop forever
        attempts     = 0
        while attempts < max_attempts:
            if candidate in trading_days:
                print(f"  [next_friday] Expiry: {candidate} (confirmed trading day via Alpaca calendar)")
                return str(candidate)

            # Friday is a holiday — check Thursday of same week
            thursday = candidate - timedelta(days=1)
            if thursday in trading_days:
                print(f"  [next_friday] {candidate} is a non-trading day — using Thursday {thursday} (holiday expiry)")
                return str(thursday)

            # Both closed — skip to next Friday
            print(f"  [next_friday] {candidate} is a non-trading day, Thursday also closed — skipping to following Friday")
            candidate += timedelta(days=7)
            attempts  += 1

        # Exhausted attempts — fall through to date-only result
        print(f"  [next_friday] WARNING: could not confirm trading day after {max_attempts} attempts — using {candidate}")
        return str(candidate)

    except Exception as exc:
        # Alpaca calendar unavailable — fall back to date calculation only
        print(f"  [next_friday] Calendar API unavailable ({exc}) — using date calculation fallback")
        return str(candidate)


def get_candidates(raw: dict, symbols: list) -> list[dict]:
    """
    Screen each symbol and return a list of candidate dicts for put selling.

    Screening criteria (ALL must pass):
    1. RSI(14) between RSI_MIN and RSI_MAX — not overbought, not collapsing
    2. Close > SMA(50) — stock in uptrend (we want puts on rising stocks)
    3. ATR% > 1.5% — enough volatility to generate meaningful premium
    4. Estimated weekly premium > MIN_OPTION_PREMIUM_PCT of stock price
    5. No earnings within 7 days — avoid binary event risk
    """
    candidates = []

    for symbol in symbols:
        try:
            # raw is {symbol: DataFrame} from market_data (R8-05)
            df = raw.get(symbol)
            if df is None:
                continue
            df = df.copy().dropna(subset=["Close"]).reset_index()

            if len(df) < SMA50_PERIOD + 5:
                print(f"  [wheel] {symbol}: insufficient data ({len(df)} bars), skipping")
                continue

            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]

            rsi_series = _rsi(close)
            sma50      = _sma(close, SMA50_PERIOD)
            atr_series = _atr(high, low, close)

            last_close = float(close.iloc[-1])
            last_rsi   = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
            last_sma50 = float(sma50.iloc[-1])       if not pd.isna(sma50.iloc[-1])     else None
            last_atr   = float(atr_series.iloc[-1])  if not pd.isna(atr_series.iloc[-1]) else None

            if any(v is None for v in [last_rsi, last_sma50, last_atr]):
                print(f"  [wheel] {symbol}: indicator NaN, skipping")
                continue

            atr_pct = last_atr / last_close * 100

            # Strike selection: ATR-based OTM put
            strike_price = round(last_close - (last_atr * STRIKE_ATR_MULT), 2)
            otm_pct      = (last_close - strike_price) / last_close * 100

            # Approximate weekly premium as 25% of ATR (rough Black-Scholes proxy)
            est_premium     = round(last_atr * 0.25, 2)
            est_premium_pct = est_premium / last_close * 100

            # Earnings check — ETFs (SPY, QQQ, GLD, XLE etc.) have no earnings calendar
            ETF_NO_EARNINGS = {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
            YFINANCE_FLAKY  = {"NVDA", "TSLA", "AMD"}  # intermittent after-hours data issues
            earnings_days = None
            try:
                import datetime as dt_module
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

            # Apply all filters
            reasons_failed = []
            if not (RSI_MIN <= last_rsi <= RSI_MAX):
                reasons_failed.append(f"RSI {last_rsi:.1f} out of [{RSI_MIN},{RSI_MAX}]")
            if last_close < last_sma50:
                reasons_failed.append(f"below SMA50 (${last_close:.2f} < ${last_sma50:.2f})")
            if atr_pct < 1.5:
                reasons_failed.append(f"ATR% {atr_pct:.2f}% < 1.5%")
            if est_premium_pct < MIN_OPTION_PREMIUM_PCT * 100:
                reasons_failed.append(f"est. premium {est_premium_pct:.3f}% < {MIN_OPTION_PREMIUM_PCT*100:.2f}%")
            if earnings_days is not None and earnings_days <= 7:
                reasons_failed.append(f"earnings in {earnings_days} days")
            if otm_pct < 1.5:
                reasons_failed.append(f"strike only {otm_pct:.1f}% OTM (min 1.5%) — too close to ATM")

            if reasons_failed:
                print(f"  [wheel] {symbol}: FILTERED — {'; '.join(reasons_failed)}")
                continue

            candidates.append({
                "symbol":          symbol,
                "stock_price":     last_close,
                "strike_price":    strike_price,
                "otm_pct":         round(otm_pct, 2),
                "expiry":          next_friday(),
                "est_premium":     est_premium,
                "est_premium_pct": round(est_premium_pct, 3),
                "rsi":             round(last_rsi, 1),
                "sma50":           round(last_sma50, 2),
                "atr":             round(last_atr, 2),
                "atr_pct":         round(atr_pct, 2),
                "earnings_days":   earnings_days,
            })
            print(
                f"  [wheel] {symbol}: CANDIDATE  RSI {last_rsi:.1f}  "
                f"strike ${strike_price}  premium ~${est_premium}/share  exp {next_friday()}"
            )

        except Exception as exc:
            print(f"  [wheel] {symbol}: ERROR — {exc}")
            continue

    return candidates


def score_candidate(candidate: dict) -> dict:
    """
    Ask Claude to score a put-selling candidate 0–100.
    Returns the candidate dict enriched with AI fields.
    """
    import json, re

    symbol      = candidate["symbol"]
    stock_price = candidate["stock_price"]
    strike      = candidate["strike_price"]
    otm_pct     = candidate["otm_pct"]
    expiry      = candidate["expiry"]
    est_premium = candidate["est_premium"]
    rsi         = candidate["rsi"]
    atr_pct     = candidate["atr_pct"]
    earnings    = candidate["earnings_days"]

    print(f"  [AI-wheel] Fetching news for {symbol}…")
    news = _fetch_news(symbol)

    prompt = f"""You are an options income analyst evaluating a cash-secured put opportunity.
Score this trade 0–100 and return ONLY valid JSON — no text outside it.

PROPOSED TRADE
  Strategy:        Sell cash-secured put (Wheel income)
  Symbol:          {symbol}
  Stock price:     ${stock_price:.2f}
  Put strike:      ${strike:.2f}  ({otm_pct:.1f}% OTM)
  Expiry:          {expiry}  (weekly, ~{DAYS_TO_EXPIRY} days)
  Est. premium:    ${est_premium:.2f}/share  (~${est_premium*100:.0f}/contract)
  Est. premium%:   {candidate['est_premium_pct']:.3f}% of stock price

TECHNICALS
  RSI(14):         {rsi:.1f}  (screening range: {RSI_MIN}–{RSI_MAX})
  ATR%:            {atr_pct:.2f}%  (weekly volatility proxy)
  Above SMA(50):   Yes ✓

CONTEXT
  Days to earnings:{f' {earnings}' if earnings is not None else ' unknown'}
  Recent news:     {news}

MARKET CONTEXT — apply penalties as directed above:
  If recent news or your knowledge indicates the broad market (SPY) has declined
  > 2% over the past 3 trading days, apply the SPY weakness penalty.
  If recent news or your knowledge indicates the stock's sector ETF has declined
  > 3% over the past 5 trading days, apply the sector weakness penalty.
  If the stock price is within 5% of the put strike, apply the low margin of
  safety penalty. Current margin: {otm_pct:.1f}% OTM.

SCORING GUIDE
  75–100 → strong put-sell candidate
  50–74  → marginal — flag for human review
  0–49   → reject (too risky or poor premium)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 7 days (gap-down risk) — reduce score by 20 points
  • Stock in clear downtrend or recent sharp drop — reduce score by 15 points
  • Very low premium (< 0.5% weekly) — reduce score by 10 points
  • Negative news catalyst (downgrade, miss, scandal) — reduce score by 15 points
  • Sector ETF down > 3% over past 5 days (sector weakness) — reduce score by 15 points
  • SPY down > 2% over past 3 days (broad market weakness) — reduce score by 10 points
  • Stock price within 5% of strike (low margin of safety) — reduce score by 10 points

POSITIVE FACTORS THAT SHOULD RAISE SCORE
  • Stock near support level (put strike near 52-week support)
  • Bullish news or analyst upgrades
  • Premium > 1% weekly (excellent income yield)
  • RSI recovering from oversold (stock likely to stay above strike)

Return exactly this JSON:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences explaining the score>",
  "position_size_contracts": <1 | 2 | 3>,
  "key_risks": ["<risk 1>", "<risk 2>"]
}}"""

    print(f"  [AI-wheel] Calling Claude for {symbol} conviction score…")
    try:
        client = _get_client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = resp.content[0].text.strip()
        fence    = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        json_str = fence.group(1) if fence else raw_text
        result   = json.loads(json_str)

        score = int(result.get("conviction_score", 0))
        if score >= APPROVE_THRESHOLD:
            result["recommendation"] = "approve"
        elif score >= REVIEW_THRESHOLD:
            result["recommendation"] = "review"
        else:
            result["recommendation"] = "reject"

        contracts = int(result.get("position_size_contracts", 1))
        result["position_size_contracts"] = max(1, min(contracts, 3))

    except Exception as exc:
        print(f"  [AI-wheel] ERROR scoring {symbol}: {exc}")
        result = {
            "conviction_score":      0,
            "recommendation":        "reject",
            "reasoning":             f"AI scoring failed: {exc}",
            "position_size_contracts": 1,
            "key_risks":             ["AI scoring error"],
        }

    candidate.update(result)
    candidate["timestamp"] = datetime.now(timezone.utc).isoformat()
    return candidate


def _scan_already_ran_this_week() -> bool:
    """
    Return True only if a scan COMPLETED this week, per the completion marker
    (last_wheel_scan_complete.txt), not merely if wheel_trades.csv has rows.

    Why the marker and not CSV rows: a Monday scan that started but was killed
    mid-run (e.g. a gunicorn worker restart) still writes partial CSV rows. The
    old row-presence check then reported the week as "already covered" and the
    Tuesday fallback — whose entire purpose is to catch a failed Monday — skipped
    itself. This happened in production on 2026-06-30. The marker is written ONLY
    after main() finishes successfully, so its presence proves completion.

    Fail-safe: any error reading/parsing the marker logs and returns False
    (proceed with the scan). A false negative (an extra Tuesday scan when Monday
    actually succeeded) is far cheaper than a false positive (skipping the only
    successful scan opportunity of the week).
    """
    if not os.path.exists(WHEEL_COMPLETE_MARKER):
        return False
    try:
        today  = datetime.now(ET).date()
        monday = today - timedelta(days=today.weekday())
        with open(WHEEL_COMPLETE_MARKER) as f:
            raw = f.read().strip()
        # Marker holds run_time, formatted "%Y-%m-%d %H:%M ET" — take the date.
        marker_date = datetime.strptime(raw.split()[0], "%Y-%m-%d").date()
        ran = marker_date >= monday
        if ran:
            print(f"  [wheel] Scan already completed this week ({marker_date}) — skipping Tuesday fallback")
        return ran
    except Exception as exc:
        print(f"  [wheel] Could not read completion marker ({exc}) — proceeding")
        return False

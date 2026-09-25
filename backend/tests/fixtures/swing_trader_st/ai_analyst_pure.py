"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, ai_analyst.py.
Copied verbatim: lines 48-77, 80-92 (line 89, `from paper_trader import
get_symbol_sector`, removed: the module attribute below stands in), 111-119,
203-338, 343-391 (line 390, `from app import append_to_pending`, removed:
the module attribute below stands in). Module attributes a test drives:
_get_client, days_until_earnings, sector_etf_rsi, fetch_news_summary,
get_symbol_sector, and `pending` (what append_to_pending received).
"""
import json
import re
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

MODEL            = "claude-sonnet-4-6"
PENDING_FILE     = "pending_trades.json"
RSI_PERIOD       = 14
APPROVE_THRESHOLD = 75
REVIEW_THRESHOLD  = 50

pending = []


def append_to_pending(record):
    pending.append(record)


def get_symbol_sector(symbol):
    return "unknown"


def _get_client():
    raise RuntimeError("a test must inject _get_client")


def days_until_earnings(symbol):
    raise RuntimeError("a test must inject days_until_earnings")


def sector_etf_rsi(symbol):
    raise RuntimeError("a test must inject sector_etf_rsi")


def fetch_news_summary(symbol):
    raise RuntimeError("a test must inject fetch_news_summary")


SECTOR_ETF: dict[str, str] = {
    "XLE":   "XLE",   # Energy (maps to itself)
    "GLD":   "GLD",   # Gold (maps to itself)
    "SPY":   "SPY",   # Broad market (maps to itself)
    "QQQ":   "XLK",   # Nasdaq proxy → tech
    # Defensive universe — used in bear mode (BEAR_REGIME_DAYS reached)
    "XLP":   "XLP",   # Consumer Staples
    "XLU":   "XLU",   # Utilities
    "XLV":   "XLV",   # Health Care
    "SHY":   "SHY",   # Short-term Treasuries
}

# GICS sector (normalized as paper_trader.get_symbol_sector returns:
# lowercase, spaces→underscores) → Select Sector SPDR ETF. This gives sector
# context for ANY symbol in the universe, not just hand-listed ones (R5-01).
_SECTOR_TO_ETF: dict[str, str] = {
    "technology":             "XLK",
    "financial_services":     "XLF",
    "healthcare":             "XLV",
    "consumer_cyclical":      "XLY",
    "consumer_defensive":     "XLP",
    "energy":                 "XLE",
    "industrials":            "XLI",
    "basic_materials":        "XLB",
    "utilities":              "XLU",
    "real_estate":            "XLRE",
    "communication_services": "XLC",
    "broad_market":           "SPY",
    "commodity":              "GLD",
}


def _resolve_sector_etf(symbol: str) -> str | None:
    """Sector ETF for a symbol: explicit override first, else by GICS sector.
    Late import of get_symbol_sector avoids the circular dependency
    (paper_trader imports ai_analyst at module level). Returns None only when
    the sector is unknown/unmapped — caller treats that as 'unavailable'."""
    explicit = SECTOR_ETF.get(symbol.upper())
    if explicit:
        return explicit
    try:
        return _SECTOR_TO_ETF.get(get_symbol_sector(symbol))
    except Exception:
        return None


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> float | None:
    if len(series) < period + 2:
        return None
    d = series.diff()
    g = d.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    l = (-d).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs = g / l.replace(0, np.nan)
    val = (100.0 - 100.0 / (1.0 + rs)).iloc[-1]
    return float(val) if not np.isnan(val) else None


def analyse(signal: dict) -> dict:
    """
    Run the full AI analysis pipeline for a trade signal.

    Required signal keys:
      symbol, rsi, rsi_prev, macd_hist, macd_hist_prev,
      entry_price, shares, stop_price, target_price

    Returns a result dict and handles file I/O (pending_trades.json).
    """
    symbol         = str(signal["symbol"]).upper()
    rsi            = float(signal["rsi"])
    rsi_prev       = float(signal["rsi_prev"])
    macd_hist      = float(signal["macd_hist"])
    macd_hist_prev = float(signal["macd_hist_prev"])
    entry_price    = float(signal["entry_price"])
    shares         = int(signal["shares"])
    stop_price     = float(signal["stop_price"])
    target_price   = float(signal["target_price"])

    risk_pct   = (entry_price - stop_price) / entry_price * 100
    reward_pct = (target_price - entry_price) / entry_price * 100
    rr_ratio   = reward_pct / risk_pct if risk_pct else 0.0

    # ── Enrichment (runs in serial to avoid rate-limiting) ────────────────
    print(f"  [AI] Fetching earnings proximity for {symbol}…")
    earnings_days = days_until_earnings(symbol)

    print(f"  [AI] Fetching sector ETF RSI for {symbol}…")
    etf_sym  = _resolve_sector_etf(symbol) or "unknown"
    etf_rsi  = sector_etf_rsi(symbol)

    print(f"  [AI] Fetching news for {symbol}…")
    news = fetch_news_summary(symbol)

    # ── Build Claude prompt ───────────────────────────────────────────────
    prompt = f"""You are an AI risk analyst for a swing trading system.
Evaluate this proposed trade and return ONLY a valid JSON object — no text outside it.

TRADE SIGNAL
  Symbol:          {symbol}
  Entry price:     ${entry_price:.2f}
  Shares:          {shares}
  Stop loss:       ${stop_price:.2f}  (risk: −{risk_pct:.1f}%)
  Profit target:   ${target_price:.2f}  (reward: +{reward_pct:.1f}%)
  Risk/reward:     1:{rr_ratio:.2f}

TECHNICAL INDICATORS
  RSI(14):         {rsi:.1f}  (prev: {rsi_prev:.1f}) — {'rising ✓' if rsi > rsi_prev else 'falling ✗'}
  MACD histogram:  {macd_hist:.4f}  (prev: {macd_hist_prev:.4f}) — {'improving ✓' if macd_hist > macd_hist_prev else 'weakening ✗'}

CONTEXT
  Sector ETF:      {etf_sym}  |  Sector RSI(14): {f'{etf_rsi:.1f}' if etf_rsi is not None else 'unavailable'}
  Days to earnings:{f' {earnings_days}' if earnings_days is not None else ' unknown'}
  Recent news:     {news}

UPSTREAM FILTERS ALREADY PASSED
  RSI < 50 and rising (pullback in uptrend), MACD histogram improving, price > SMA(200),
  volume > 20-day avg, SPY regime (>SMA200×1.03), VIX ≤ 25.

SCORING GUIDE
  75–100 → approve   (strong setup, proceed)
  50–74  → review    (flag for human review)
  0–49   → reject    (too risky)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 5 days (gap risk)
  • Sector ETF RSI > 65 (overbought sector) or < 35 (sector breakdown) — ONLY when a value is shown above; if sector RSI is unavailable, do NOT treat its absence as a risk
  • Negative news catalyst (downgrade, miss, scandal)

key_risks RULES (critical):
  • Every entry MUST be specific to THIS trade and MUST inform the approve/reject decision.
  • Do NOT list structural constants that are identical on every trade — the
    risk/reward is always 1:1.50 and the stop/target are always −6%/+9% by
    system design, so NEVER cite the R/R ratio or the fixed bracket as a risk.
  • Do NOT cite "sector data unavailable" as a risk — it is a data-coverage note, not a trade weakness.
  • If there are no genuine trade-specific risks, return an empty list rather than filler.

Return exactly this JSON structure:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences>",
  "position_size_adjustment": <1.0 | 0.5 | 0.25>,
  "key_risks": ["<trade-specific risk>", ...]
}}"""

    # ── Call Claude ───────────────────────────────────────────────────────
    print(f"  [AI] Calling Claude for conviction score…")
    client = _get_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # Strip markdown fences if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = fence_match.group(1) if fence_match else raw

    result = json.loads(json_str)

    # Enforce thresholds regardless of what Claude said in the text
    score = int(result.get("conviction_score", 0))
    if score >= APPROVE_THRESHOLD:
        result["recommendation"] = "approve"
    elif score >= REVIEW_THRESHOLD:
        result["recommendation"] = "review"
    else:
        result["recommendation"] = "reject"

    # Validate position_size_adjustment
    valid_adj = {1.0, 0.5, 0.25}
    adj = float(result.get("position_size_adjustment", 1.0))
    result["position_size_adjustment"] = adj if adj in valid_adj else 1.0

    # Attach enrichment and identity fields
    result.update({
        "symbol":         symbol,
        "entry_price":    entry_price,
        "shares":         shares,
        "stop_price":     stop_price,
        "target_price":   target_price,
        "risk_pct":       round(risk_pct, 2),
        "reward_pct":     round(reward_pct, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "earnings_days":  earnings_days,
        "sector_etf":     etf_sym,
        "sector_rsi":     etf_rsi,
        "news_summary":   news,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })

    _handle_result(result)
    return result


def _handle_result(result: dict) -> None:
    sym   = result["symbol"]
    score = result["conviction_score"]
    rec   = result["recommendation"]
    adj   = result["position_size_adjustment"]

    if rec == "approve":
        print(
            f"  [AI] AUTO-APPROVED  {sym}  "
            f"score={score}  size_adj={adj}x\n"
            f"       {result['reasoning'][:100]}"
        )
        return

    if rec == "reject":
        print(
            f"  [AI] REJECTED       {sym}  "
            f"score={score}\n"
            f"       {result['reasoning'][:100]}"
        )
        return

    # review — validate prices before writing to pending_trades.json
    entry  = float(result.get("entry_price", 0))
    stop   = float(result.get("stop_price",  0))
    target = float(result.get("target_price", 0))
    if stop >= entry * 0.99:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"stop_price ${stop:.4f} must be < entry_price ${entry:.4f} × 0.99 "
            f"(got stop/entry = {stop/entry:.4f})"
        )
    if target <= entry * 1.01:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"target_price ${target:.4f} must be > entry_price ${entry:.4f} × 1.01 "
            f"(got target/entry = {target/entry:.4f})"
        )

    print(
        f"  [AI] REVIEW NEEDED  {sym}  "
        f"score={score}  size_adj={adj}x  → {PENDING_FILE}"
    )
    # Late import avoids circular: app → paper_trader → ai_analyst.
    # By call time app is fully initialised. append_to_pending holds
    # _pending_lock for the full read-append-write so concurrent
    # approve/reject routes cannot race with this write.
    append_to_pending(result)

"""
Fetch fundamentals and sentiment from yfinance for criteria checks.
Returns dict with keys like marketCap, trailingPE, forwardPE, pegRatio, returnOnEquity, etc.
Missing or invalid values are None or 0 as appropriate.
ETFs/funds (e.g. SPY) often return 404 from Yahoo quoteSummary; we catch that and return empty-style dict.
"""
import yfinance as yf
from datetime import datetime, timedelta

try:
    from urllib.error import HTTPError
except ImportError:
    HTTPError = Exception  # noqa: A001


def _num(v, default=None):
    if v is None:
        return default
    try:
        x = float(v)
        return x if x == x else default  # NaN check
    except (TypeError, ValueError):
        return default


def get_fundamentals(symbol: str) -> dict:
    """
    Fetch fundamentals and sentiment for a symbol via yfinance.
    Returns dict: marketCap, trailingPE, forwardPE, pegRatio, returnOnEquity, debtToEquity,
    revenueGrowth, earningsGrowth, freeCashflow, currentRatio, targetMeanPrice, recommendationKey,
    nextEarningsDate, sector (for P/E vs sector median - we don't have sector median, so optional).
    """
    out = {
        "marketCap": None,
        "trailingPE": None,
        "forwardPE": None,
        "pegRatio": None,
        "returnOnEquity": None,
        "debtToEquity": None,
        "revenueGrowth": None,
        "earningsGrowth": None,
        "freeCashflow": None,
        "currentRatio": None,
        "targetMeanPrice": None,
        "recommendationKey": None,
        "nextEarningsDate": None,
        "sector": None,
    }
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        out["sector"] = (info.get("sector") or "").strip() or None
        out["marketCap"] = _num(info.get("marketCap"), 0)
        out["trailingPE"] = _num(info.get("trailingPE"))
        out["forwardPE"] = _num(info.get("forwardPE"))
        out["pegRatio"] = _num(info.get("pegRatio"))
        out["returnOnEquity"] = _num(info.get("returnOnEquity"))
        out["debtToEquity"] = _num(info.get("debtToEquity"))
        out["revenueGrowth"] = _num(info.get("revenueGrowth"))  # already decimal e.g. 0.15 = 15%
        out["earningsGrowth"] = _num(info.get("earningsGrowth"))
        out["freeCashflow"] = _num(info.get("freeCashflow"))
        out["currentRatio"] = _num(info.get("currentRatio"))
        out["targetMeanPrice"] = _num(info.get("targetMeanPrice"))
        out["recommendationKey"] = (info.get("recommendationKey") or "").strip().lower()
        # Earnings date: calendar or next earnings (ETFs often 404 on calendar)
        try:
            cal = getattr(t, "calendar", None)
            if cal is not None and hasattr(cal, "get"):
                dates = (cal.get("Earnings") or {}).get("Earnings Date") or []
                if dates:
                    out["nextEarningsDate"] = dates[0] if isinstance(dates[0], (datetime, type(None))) else None
        except HTTPError:
            pass
    except HTTPError:
        # Yahoo returns 404 for many ETFs/funds (no fundamentals); return empty-style dict without re-raising
        pass
    except Exception:
        pass
    return out


def earnings_within_days(next_earnings_date, within_days: int = 7) -> bool:
    """True if next earnings is within the next N days (exclude such stocks)."""
    if next_earnings_date is None:
        return False
    try:
        if hasattr(next_earnings_date, "timestamp"):
            d = next_earnings_date
        else:
            d = datetime.fromtimestamp(next_earnings_date) if isinstance(next_earnings_date, (int, float)) else None
        if d is None:
            return False
        return 0 <= (d - datetime.now()).days <= within_days
    except Exception:
        return False

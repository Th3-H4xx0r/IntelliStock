"""SEC EDGAR XBRL fundamentals — the TRUE point-in-time source.

WHY THIS EXISTS
---------------
`factor_profitability.yfinance_fetcher` is the only fetcher this repo had, and
its own module docstring says why that is a problem: yfinance serves statements
as they read TODAY, with no filing date, so restatements are invisible and the
only defence is a blunt `DEFAULT_REPORTING_LAG_DAYS = 120` heuristic. That lag
is a PROXY, and `factor_profitability` says so explicitly at line ~116:

    THIS IS A PROXY AND A STRICTLY WORSE ONE THAN THE TRUTH. The correct input
    is the actual EDGAR filing timestamp ... Swapping the availability source is
    a one-function change: see `FiscalPeriod.available_at`.

This module is that one function. Every EDGAR XBRL fact carries a `filed` date,
which populates `FiscalPeriod.filed_at`, which WINS over the lag heuristic. Two
things follow:

  * no lookahead — a fact is invisible until the date it was actually filed,
    including for the delinquent filers that a 120-day rule silently admits;
  * ~30-60 days of signal back for large accelerated filers, who file well
    inside 120 days and whose figures the heuristic was needlessly withholding.

RESTATEMENTS ARE KEPT, NOT COLLAPSED
------------------------------------
EDGAR returns MULTIPLE vintages of the same fiscal period — the original filing
and every later restatement — each with its own `filed` date and possibly a
different value. Collapsing them to "the current value" would reintroduce
exactly the lookahead this module exists to remove.

So every vintage becomes its own `FiscalPeriod`, emitted in ASCENDING `filed`
order. `select_period` keeps only periods with `available_at() <= as_of` and
then sorts by `(period_end, frequency)` taking the last; that sort is stable, so
among vintages of the SAME period_end the LATEST-FILED one that was public by
`as_of` wins. That is the correct answer: it is what an investor reading EDGAR
on `as_of` would have seen.

USE AS A VETO, NOT A SELECTOR
-----------------------------
Bessembinder: 58% of US stocks underperform T-bills over their lifetime, so
EXCLUDING likely losers is far more tractable than picking winners. It also
sidesteps Grinold — concentrating the graph's measured-NEGATIVE selection alpha
would amplify losses, whereas a veto only ever removes names.

DEFAULT OFF and NO CALL SITES. This module is network + parsing only; nothing
imports it into a decision path yet. Wiring it is a separate, measured change.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime

from factor_profitability import FiscalPeriod, FundamentalsRecord

__all__ = [
    "EdgarError",
    "edgar_fetcher",
    "periods_from_company_facts",
    "resolve_cik",
    "user_agent",
]

#: SEC requires a descriptive User-Agent with a contact address and returns 403
#: without one. Configurable so a deployment declares its own contact.
_DEFAULT_UA = "IntelliStock research (contact: krishnatechpranav@gmail.com)"

#: SEC's published fair-access ceiling is 10 requests/second. Stay well under —
#: a backtest sweeping a 200-name universe is not latency-sensitive and a 403
#: block costs far more than the delay.
_MIN_REQUEST_INTERVAL_S = 0.15

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

#: XBRL tags, in preference order. Companies tag the same economics differently;
#: `GrossProfit` is often absent and must be reconstructed as revenue - COGS,
#: which is Novy-Marx's actual definition anyway.
_TAG_GROSS_PROFIT = ("GrossProfit",)
_TAG_REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
)
_TAG_COGS = (
    "CostOfGoodsAndServicesSold",
    "CostOfRevenue",
    "CostOfGoodsSold",
)
_TAG_ASSETS = ("Assets",)

#: A flow fact spanning at least this many days is treated as annual. 10-K
#: periods are ~365 days; 52/53-week fiscal years land as low as 358.
_ANNUAL_MIN_DAYS = 350

_rate_lock = threading.Lock()
_last_request_at = [0.0]
_cik_cache: dict[str, int] = {}
_cik_lock = threading.Lock()


class EdgarError(RuntimeError):
    """Network or payload problem talking to EDGAR."""


def user_agent() -> str:
    return os.environ.get("SEC_EDGAR_USER_AGENT") or _DEFAULT_UA


def _throttle() -> None:
    with _rate_lock:
        wait = _MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_at[0] = time.monotonic()


def _get_json(url: str, *, timeout: float = 20.0):
    _throttle()
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EdgarError(f"{url} -> HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise EdgarError(f"{url} -> {type(exc).__name__}: {exc}") from exc


def resolve_cik(symbol: str, *, _fetch=None) -> int | None:
    """Ticker -> CIK, using SEC's own ticker map (cached for the process)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    with _cik_lock:
        if not _cik_cache:
            fetch = _fetch or (lambda: _get_json(_TICKERS_URL))
            try:
                payload = fetch()
            except EdgarError:
                return None
            rows = payload.values() if isinstance(payload, dict) else (payload or [])
            for row in rows:
                try:
                    _cik_cache[str(row["ticker"]).strip().upper()] = int(row["cik_str"])
                except (KeyError, TypeError, ValueError):
                    continue
        return _cik_cache.get(sym)


def _as_date(value) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _usd_points(facts: dict, tags) -> list[dict]:
    """All USD-denominated facts for the first tag present, in filing order."""
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    for tag in tags:
        node = gaap.get(tag)
        if not isinstance(node, dict):
            continue
        for unit_key, points in (node.get("units") or {}).items():
            if str(unit_key).upper().startswith("USD") and points:
                return list(points)
    return []


def _annual_flow(points: list[dict]) -> dict[tuple, dict]:
    """Annual flow facts keyed by (period_end, filed)."""
    out: dict[tuple, dict] = {}
    for p in points:
        start, end, filed = _as_date(p.get("start")), _as_date(p.get("end")), _as_date(p.get("filed"))
        if not (start and end and filed):
            continue
        if (end - start).days < _ANNUAL_MIN_DAYS:
            continue            # quarterly/9-month slice, not an annual figure
        try:
            out[(end, filed)] = {"val": float(p["val"]), "form": str(p.get("form") or "")}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _instant(points: list[dict]) -> dict[tuple, float]:
    """Balance-sheet (instantaneous) facts keyed by (period_end, filed)."""
    out: dict[tuple, float] = {}
    for p in points:
        if p.get("start"):
            continue            # a flow fact, not a balance
        end, filed = _as_date(p.get("end")), _as_date(p.get("filed"))
        if not (end and filed):
            continue
        try:
            out[(end, filed)] = float(p["val"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def periods_from_company_facts(facts: dict) -> tuple[FiscalPeriod, ...]:
    """Build dated `FiscalPeriod`s from a companyfacts payload. PURE — no network.

    One `FiscalPeriod` per (period_end, filed) vintage, ascending by `filed` so
    the stable sort in `select_period` resolves same-period restatements to the
    latest one that was public at `as_of`.
    """
    gp = _annual_flow(_usd_points(facts, _TAG_GROSS_PROFIT))
    rev = _annual_flow(_usd_points(facts, _TAG_REVENUE))
    cogs = _annual_flow(_usd_points(facts, _TAG_COGS))
    assets = _instant(_usd_points(facts, _TAG_ASSETS))

    # Assets are filed alongside the income statement in the same report, but a
    # balance sheet is stamped at the period END while the income statement
    # spans TO it. Index assets by period_end and take the vintage filed on or
    # before the income statement's filing, so the pair comes from one report.
    assets_by_end: dict[date, list[tuple[date, float]]] = {}
    for (end, filed), val in assets.items():
        assets_by_end.setdefault(end, []).append((filed, val))
    for vintages in assets_by_end.values():
        vintages.sort()

    out: list[FiscalPeriod] = []
    for (end, filed) in sorted(set(gp) | set(rev) | set(cogs)):
        candidates = [(f, v) for f, v in assets_by_end.get(end, []) if f <= filed]
        if not candidates:
            continue            # no balance sheet public by then -> no GP/A
        total_assets = candidates[-1][1]
        out.append(FiscalPeriod(
            period_end=end,
            gross_profit=(gp.get((end, filed)) or {}).get("val"),
            total_revenue=(rev.get((end, filed)) or {}).get("val"),
            cost_of_revenue=(cogs.get((end, filed)) or {}).get("val"),
            total_assets=total_assets,
            frequency="annual",
            filed_at=filed,      # THE POINT OF THIS MODULE
        ))
    out.sort(key=lambda p: (p.filed_at or date.min, p.period_end))
    return tuple(out)


def edgar_fetcher(symbol: str) -> FundamentalsRecord | None:
    """`factor_profitability`-compatible fetcher backed by SEC EDGAR.

    Drop-in for `yfinance_fetcher`: same signature, same return type, but every
    period carries a real `filed_at`, so `available_at()` stops guessing.
    Returns None (never raises) so a lookup failure degrades to "no opinion"
    rather than taking down the caller — same contract as the yfinance path.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    try:
        cik = resolve_cik(sym)
        if cik is None:
            return None
        facts = _get_json(_FACTS_URL.format(cik=cik))
        periods = periods_from_company_facts(facts)
        if not periods:
            return None
        return FundamentalsRecord(
            symbol=sym,
            quote_type="EQUITY",
            sector="",
            periods=periods,
            source="edgar",
            fetched_at=time.time(),
        )
    except EdgarError:
        return None
    except Exception:
        return None

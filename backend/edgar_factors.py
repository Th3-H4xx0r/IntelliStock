"""Point-in-time fundamental factors from SEC EDGAR — a stock-research layer.

WHY THIS EXISTS
---------------
The graph's own conviction cannot rank: **89% of its buys carry the maximum raw
score**, and the resulting satellite is worth **-$7.80 per position** (33.7% hit
rate, average winner +$30 vs average loser -$27 — no right tail). A signal that
cannot discriminate cannot be filtered, only replaced. This supplies an EXTERNAL
discriminator computed from filings, not from the model.

WHICH FACTORS, AND WHY THESE
----------------------------
Only factors that survive replication. Hou, Xue & Zhang (2020) could not
replicate a large fraction of the published anomaly zoo; this project has already
been burned by one of the casualties (Piotroski F-score: 0.29%/mo, t=1.06 —
Novy-Marx & Velikov net it to 0.09%/mo, t=0.45). The four here are among the
survivors, and every one is computable from EDGAR alone:

  gp_a          Gross profit / assets. Novy-Marx (2013). The profitability leg
                that replicates where earnings-based quality does not.
  asset_growth  -(A_t/A_{t-1} - 1). Cooper, Gulen & Schill (2008). Companies
                that expand the balance sheet fast underperform. Sign flipped so
                HIGHER IS BETTER, like every other factor here.
  accruals      -(NI - CFO)/A. Sloan (1996). Earnings not backed by cash reverse.
                Sign flipped: low accruals score high.
  net_issuance  -(shares_t/shares_{t-1} - 1). Daniel & Titman (2006),
                Pontiff & Woodgate (2008). Buybacks beat dilution. Sign flipped.

USE AS A VETO / RANK, NEVER AS A CONCENTRATOR
--------------------------------------------
Bessembinder: 58% of US stocks underperform T-bills over their lifetime, so
EXCLUDING likely losers is the tractable half of the problem. Grinold: your top-3
need ~2.2x the information coefficient of your average pick merely to break even,
so concentrating a weak signal destroys value. Expect this to raise the hit rate,
not to produce multi-baggers.

POINT-IN-TIME BY CONSTRUCTION
-----------------------------
Every fact is admitted only if its EDGAR `filed` date is on or before `as_of`,
and restatement vintages are kept separate, so a name is judged on what was
public THAT DAY. See `edgar_fundamentals`.

DEFAULT OFF. No call sites beyond the veto that opts in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from edgar_fundamentals import _annual_flow, _get_json, _instant, _usd_points, resolve_cik


def _unit_points(facts: dict, tags, unit_prefix: str) -> list:
    """Facts for the first present tag whose UNIT matches `unit_prefix`.

    Share counts are reported in `shares`, not `USD`. `_usd_points` accepts only
    USD*, so reusing it for share counts silently returned nothing and
    `net_issuance` was permanently None — invisible in tests because the fixture
    had wrongly filed shares under USD. Verified against live EDGAR.
    """
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    want = str(unit_prefix or "").upper()
    for tag in tags:
        node = gaap.get(tag)
        if not isinstance(node, dict):
            continue
        for unit_key, points in (node.get("units") or {}).items():
            if str(unit_key).upper().startswith(want) and points:
                return list(points)
    return []

__all__ = ["FactorSet", "factor_set", "composite_score"]

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_TAG_ASSETS = ("Assets",)
_TAG_NET_INCOME = ("NetIncomeLoss", "ProfitLoss")
_TAG_CFO = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_TAG_SHARES = (
    "CommonStockSharesOutstanding",
    "CommonStockSharesIssued",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
)
_TAG_GROSS_PROFIT = ("GrossProfit",)
_TAG_REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
_TAG_COGS = ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")


@dataclass(frozen=True)
class FactorSet:
    """Factors visible at `as_of`. Every field is None when unknowable — never a
    guess, because a fabricated zero ranks like a real one."""

    symbol: str
    as_of: date
    period_end: date | None = None
    filed_at: date | None = None
    gp_a: float | None = None
    asset_growth: float | None = None       # sign-flipped: higher is better
    accruals: float | None = None           # sign-flipped: higher is better
    net_issuance: float | None = None       # sign-flipped: higher is better

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(f for f in ("gp_a", "asset_growth", "accruals", "net_issuance")
                     if getattr(self, f) is not None)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _visible_annual(points_by_key, as_of):
    """{period_end: value} for facts FILED on or before as_of.

    Later filings win for the same period end — that is what an investor reading
    EDGAR on `as_of` would see, restatements included.
    """
    out: dict[date, tuple[date, float]] = {}
    for (end, filed), payload in points_by_key.items():
        if filed > as_of:
            continue                       # not public yet: the lookahead guard
        val = payload["val"] if isinstance(payload, dict) else payload
        prev = out.get(end)
        if prev is None or filed >= prev[0]:
            out[end] = (filed, float(val))
    return {end: v for end, (_f, v) in out.items()}


def _two_latest(series):
    """(current, prior, period_end) for the two most recent annual points."""
    if len(series) < 2:
        return None, None, (max(series) if series else None)
    ends = sorted(series)
    return series[ends[-1]], series[ends[-2]], ends[-1]


def factor_set(symbol: str, as_of, *, facts: dict | None = None) -> FactorSet:
    """Compute every available factor for `symbol` as of `as_of`.

    `facts` accepts a pre-fetched companyfacts payload so callers can batch, and
    so tests never touch the network.
    """
    as_of_d = _as_date(as_of) or date.today()
    sym = str(symbol or "").strip().upper()
    empty = FactorSet(symbol=sym, as_of=as_of_d)
    if not sym:
        return empty
    try:
        if facts is None:
            cik = resolve_cik(sym)
            if cik is None:
                return empty
            facts = _get_json(_FACTS_URL.format(cik=cik))

        assets = _visible_annual(_instant(_usd_points(facts, _TAG_ASSETS)), as_of_d)
        gp = _visible_annual(_annual_flow(_usd_points(facts, _TAG_GROSS_PROFIT)), as_of_d)
        rev = _visible_annual(_annual_flow(_usd_points(facts, _TAG_REVENUE)), as_of_d)
        cogs = _visible_annual(_annual_flow(_usd_points(facts, _TAG_COGS)), as_of_d)
        ni = _visible_annual(_annual_flow(_usd_points(facts, _TAG_NET_INCOME)), as_of_d)
        cfo = _visible_annual(_annual_flow(_usd_points(facts, _TAG_CFO)), as_of_d)
        # shares are denominated in `shares`, never USD — see _unit_points
        shares = _visible_annual(_instant(_unit_points(facts, _TAG_SHARES, "SHARES")),
                                 as_of_d)

        # Anchor on ANNUAL period ends, not on the latest balance-sheet date.
        # `_instant` picks up 10-Q balances too, so taking the newest Assets date
        # paired a quarterly balance (e.g. AAPL 2025-12-27) with an annual gross
        # profit that does not exist at that end — gp_a came back None for every
        # off-calendar filer. Verified against live EDGAR.
        annual_ends = sorted(set(gp) | set(rev) | set(ni) | set(cfo))
        annual_ends = [e for e in annual_ends if e in assets]
        if not annual_ends:
            return empty
        end = annual_ends[-1]
        prev_end = annual_ends[-2] if len(annual_ends) > 1 else None
        a_now = assets.get(end)
        a_prev = assets.get(prev_end) if prev_end else None

        # --- gross profitability -------------------------------------------
        gp_a = None
        total_assets = assets.get(end)
        gross = gp.get(end)
        if gross is None and rev.get(end) is not None and cogs.get(end) is not None:
            gross = rev[end] - cogs[end]            # Novy-Marx's own definition
        if gross is not None and total_assets:
            v = gross / total_assets
            gp_a = v if -1.0 <= v <= 10.0 else None  # units sanity, as upstream

        # --- asset growth (sign flipped) ------------------------------------
        asset_growth = None
        if a_now is not None and a_prev:
            asset_growth = -((a_now / a_prev) - 1.0)

        # --- accruals (sign flipped) ----------------------------------------
        accruals = None
        if ni.get(end) is not None and cfo.get(end) is not None and total_assets:
            accruals = -((ni[end] - cfo[end]) / total_assets)

        # --- net issuance (sign flipped) ------------------------------------
        net_issuance = None
        s_now = shares.get(end)
        s_prev = shares.get(prev_end) if prev_end else None
        if s_now is None or s_prev is None:
            # fall back to the two most recent share counts of any date
            s_now, s_prev, _ = _two_latest(shares)
        if s_now is not None and s_prev:
            net_issuance = -((s_now / s_prev) - 1.0)

        return FactorSet(
            symbol=sym, as_of=as_of_d, period_end=end,
            gp_a=gp_a, asset_growth=asset_growth,
            accruals=accruals, net_issuance=net_issuance,
        )
    except Exception:
        return empty


def composite_score(fs: FactorSet, weights: dict | None = None) -> float | None:
    """Mean of the AVAILABLE factors, each weighted. None when nothing is known.

    Averaging only what exists is deliberate: substituting 0.0 for a missing
    factor is a silent opinion, and a name with three unknowns would then rank
    beside a name that genuinely scored zero on all four.

    This is a RANK, not a return forecast. Its job is ordering candidates so the
    weakest can be dropped.
    """
    w = {"gp_a": 1.0, "asset_growth": 1.0, "accruals": 1.0, "net_issuance": 1.0}
    if weights:
        w.update({k: float(v) for k, v in weights.items()})
    num = den = 0.0
    for name in ("gp_a", "asset_growth", "accruals", "net_issuance"):
        val = getattr(fs, name, None)
        if val is None:
            continue
        num += w.get(name, 1.0) * float(val)
        den += abs(w.get(name, 1.0))
    if den <= 0:
        return None
    return num / den

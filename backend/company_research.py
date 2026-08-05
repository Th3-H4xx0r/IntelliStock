"""Per-company research dossier, computed point-in-time before a buy.

WHAT PROBLEM THIS SOLVES
------------------------
Not a shortage of data — this system already ingests news, LLM sentiment,
Benzinga ratings/insider/government trades, and a 2.5M-node graph. The problem is
that none of it produces a RANKING: **89% of buys carry the maximum raw
conviction score**, so every candidate looks equally good and the resulting
satellite is worth **-$7.80 per position** (33.7% hit rate, no right tail).

The measured losers are the tell. The worst positions across 11 runs — FTH -$227,
SMX -$149, BSP -$96, plus ABSI, HAPN, REPL — are small, frequently pre-profit
names. Nothing in the pipeline ever asked whether the business was viable. This
does, from filings, before the buy.

WHAT IT IS NOT
--------------
Not a return forecast, and not a stock picker. Bessembinder: 58% of US stocks
underperform T-bills over their lifetime, so the tractable job is EXCLUDING the
obviously fragile, not divining winners. Grinold: concentrating a weak signal
destroys value, so a good grade is permission to buy, never a reason to size up.

POINT-IN-TIME
-------------
Every input is an EDGAR fact whose `filed` date is on or before `as_of`. A name
is judged on what was public that day. See `edgar_fundamentals`.

FAILS OPEN. Unknown is never a fail — a company with no filings (an ETF, a
foreign issuer, a fresh IPO) returns grade None and the caller must treat that as
"no opinion", not "reject".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from edgar_factors import (
    _TAG_ASSETS,
    _TAG_CFO,
    _TAG_NET_INCOME,
    _TAG_REVENUE,
    _as_date,
    _unit_points,
    _visible_annual,
    composite_score,
    factor_set,
)
from edgar_fundamentals import _annual_flow, _get_json, _instant, _usd_points, resolve_cik

__all__ = ["Dossier", "research_company"]

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_TAG_LIABILITIES = ("Liabilities",)
_TAG_EQUITY = ("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
_TAG_CUR_ASSETS = ("AssetsCurrent",)
_TAG_CUR_LIABS = ("LiabilitiesCurrent",)


@dataclass
class Dossier:
    """Everything the filings say, plus a grade and the reasons behind it."""

    symbol: str
    as_of: date
    period_end: date | None = None
    grade: str | None = None            # A/B/C/D/F, or None for "no opinion"
    score: float | None = None
    factors: object | None = None
    red_flags: list = field(default_factory=list)
    green_flags: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def viable(self) -> bool | None:
        """False only when the filings show a fragile business. None = unknown."""
        if self.grade is None:
            return None
        return self.grade != "F"

    def summary(self) -> str:
        if self.grade is None:
            return f"{self.symbol}: no filings visible as of {self.as_of}"
        bits = [f"{self.symbol} grade {self.grade}"]
        if self.score is not None:
            bits.append(f"score {self.score:+.3f}")
        if self.red_flags:
            bits.append("RED: " + "; ".join(self.red_flags))
        if self.green_flags:
            bits.append("green: " + "; ".join(self.green_flags))
        return " | ".join(bits)


def _latest(series, end):
    return series.get(end) if end in series else None


def research_company(symbol: str, as_of, *, facts: dict | None = None) -> Dossier:
    """Build the dossier for `symbol` as it stood on `as_of`."""
    as_of_d = _as_date(as_of) or date.today()
    sym = str(symbol or "").strip().upper()
    dossier = Dossier(symbol=sym, as_of=as_of_d)
    if not sym:
        return dossier
    try:
        if facts is None:
            cik = resolve_cik(sym)
            if cik is None:
                return dossier
            facts = _get_json(_FACTS_URL.format(cik=cik))

        fs = factor_set(sym, as_of_d, facts=facts)
        dossier.factors = fs
        dossier.period_end = fs.period_end
        if fs.period_end is None:
            return dossier                      # nothing public yet: no opinion

        end = fs.period_end
        assets = _visible_annual(_instant(_usd_points(facts, _TAG_ASSETS)), as_of_d)
        liabs = _visible_annual(_instant(_usd_points(facts, _TAG_LIABILITIES)), as_of_d)
        equity = _visible_annual(_instant(_usd_points(facts, _TAG_EQUITY)), as_of_d)
        cur_a = _visible_annual(_instant(_usd_points(facts, _TAG_CUR_ASSETS)), as_of_d)
        cur_l = _visible_annual(_instant(_usd_points(facts, _TAG_CUR_LIABS)), as_of_d)
        rev = _visible_annual(_annual_flow(_usd_points(facts, _TAG_REVENUE)), as_of_d)
        ni = _visible_annual(_annual_flow(_usd_points(facts, _TAG_NET_INCOME)), as_of_d)
        cfo = _visible_annual(_annual_flow(_usd_points(facts, _TAG_CFO)), as_of_d)

        A = _latest(assets, end)
        m = dossier.metrics

        # --- solvency ------------------------------------------------------
        L = _latest(liabs, end)
        if A and L is not None:
            m["liabilities_to_assets"] = L / A
            if L / A > 0.90:
                dossier.red_flags.append(f"liabilities {L/A:.0%} of assets")
            elif L / A < 0.50:
                dossier.green_flags.append(f"low leverage ({L/A:.0%})")
        E = _latest(equity, end)
        if E is not None and E < 0:
            dossier.red_flags.append("negative shareholder equity")
        m["equity"] = E

        # --- liquidity -----------------------------------------------------
        ca, cl = _latest(cur_a, end), _latest(cur_l, end)
        if ca is not None and cl:
            m["current_ratio"] = ca / cl
            if ca / cl < 1.0:
                dossier.red_flags.append(f"current ratio {ca/cl:.2f} < 1")
            elif ca / cl > 2.0:
                dossier.green_flags.append(f"current ratio {ca/cl:.1f}")

        # --- cash generation: the screen the measured losers would fail -----
        C = _latest(cfo, end)
        m["cfo"] = C
        if C is not None:
            if C < 0:
                dossier.red_flags.append("burns cash from operations")
                if A and abs(C) / A > 0.20:
                    dossier.red_flags.append(f"burn {abs(C)/A:.0%} of assets/yr")
            else:
                dossier.green_flags.append("cash-generative")

        # --- profitability -------------------------------------------------
        N = _latest(ni, end)
        m["net_income"] = N
        if N is not None and N < 0:
            dossier.red_flags.append("unprofitable")

        # --- revenue: scale and trend --------------------------------------
        R = _latest(rev, end)
        m["revenue"] = R
        if R is not None and R <= 0:
            dossier.red_flags.append("no revenue")
        ends = sorted(rev)
        if len(ends) > 1 and rev.get(ends[-2]):
            g = (rev[ends[-1]] / rev[ends[-2]]) - 1.0
            m["revenue_growth"] = g
            if g > 0.15:
                dossier.green_flags.append(f"revenue +{g:.0%}")
            elif g < -0.15:
                dossier.red_flags.append(f"revenue {g:.0%}")

        dossier.score = composite_score(fs)

        # --- grade ----------------------------------------------------------
        # F is reserved for businesses the filings say are fragile, because F is
        # the only grade that blocks a trade. Everything else is a ranking.
        hard = [f for f in dossier.red_flags
                if f.startswith(("burns cash", "unprofitable", "no revenue",
                                 "negative shareholder"))]
        if len(hard) >= 2:
            dossier.grade = "F"
        elif len(dossier.red_flags) >= 3:
            dossier.grade = "D"
        elif dossier.red_flags:
            dossier.grade = "C"
        elif len(dossier.green_flags) >= 3:
            dossier.grade = "A"
        else:
            dossier.grade = "B"
        return dossier
    except Exception:
        return Dossier(symbol=sym, as_of=as_of_d)

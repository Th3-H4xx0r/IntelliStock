"""EDGAR XBRL fundamentals: real filing dates, and no lookahead.

The property under test is the one the 120-day heuristic could only approximate:
a fact is invisible until the date it was ACTUALLY filed, and a restatement is
invisible until IT was filed — while the original remains visible before that.

All offline: `periods_from_company_facts` is pure, so these use a synthetic
companyfacts payload rather than the network.
"""
import os
import sys
from datetime import date

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from edgar_fundamentals import (  # noqa: E402
    edgar_fetcher,
    periods_from_company_facts,
    resolve_cik,
)
from factor_profitability import (  # noqa: E402
    DEFAULT_REPORTING_LAG_DAYS,
    FundamentalsRecord,
    gp_a_from_period,
    select_period,
)


def _flow(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}


#: FY2024 (filed 2025-02-10) and FY2025 (filed 2026-02-09), plus a RESTATEMENT
#: of FY2024 filed 2025-11-01 that cuts gross profit from 400 to 300.
FACTS = {"facts": {"us-gaap": {
    "GrossProfit": {"units": {"USD": [
        _flow("2024-01-01", "2024-12-31", 400, "2025-02-10"),
        _flow("2024-01-01", "2024-12-31", 300, "2025-11-01"),   # restated
        _flow("2025-01-01", "2025-12-31", 500, "2026-02-09"),
        # a quarterly slice, which must never be treated as an annual figure
        _flow("2025-01-01", "2025-03-31", 120, "2025-05-01", form="10-Q"),
    ]}},
    "Revenues": {"units": {"USD": [
        _flow("2024-01-01", "2024-12-31", 1000, "2025-02-10"),
        _flow("2024-01-01", "2024-12-31", 1000, "2025-11-01"),
        _flow("2025-01-01", "2025-12-31", 1200, "2026-02-09"),
    ]}},
    "Assets": {"units": {"USD": [
        _inst("2024-12-31", 2000, "2025-02-10"),
        _inst("2024-12-31", 2000, "2025-11-01"),
        _inst("2025-12-31", 2500, "2026-02-09"),
    ]}},
}}}


def _record():
    return FundamentalsRecord(symbol="TEST", quote_type="EQUITY",
                              periods=periods_from_company_facts(FACTS),
                              source="edgar")


def test_filing_dates_are_populated_not_guessed():
    periods = periods_from_company_facts(FACTS)
    assert periods, "expected parsed periods"
    for p in periods:
        assert p.filed_at is not None, "filed_at is the whole point of this module"
        # available_at must equal the FILING date, not period_end + 120 days
        assert p.available_at() == p.filed_at
        assert p.available_at() != p.period_end + __import__(
            "datetime").timedelta(days=DEFAULT_REPORTING_LAG_DAYS)


def test_quarterly_slices_are_not_treated_as_annual():
    ends = {(p.period_end, p.gross_profit) for p in periods_from_company_facts(FACTS)}
    assert (date(2025, 3, 31), 120) not in ends


def test_no_lookahead_before_the_filing_date():
    """The day before FY2025 was filed, the newest visible period is FY2024."""
    rec = _record()
    period, reason = select_period(rec, date(2026, 2, 8))
    assert period is not None, reason
    assert period.period_end == date(2024, 12, 31)


def test_fact_becomes_visible_on_its_filing_date():
    rec = _record()
    period, _ = select_period(rec, date(2026, 2, 9))
    assert period.period_end == date(2025, 12, 31)
    assert period.gross_profit == 500


def test_nothing_is_visible_before_the_first_filing():
    rec = _record()
    period, reason = select_period(rec, date(2025, 2, 9))
    assert period is None, f"got {period} — that is lookahead"


def test_restatement_is_invisible_until_it_is_filed():
    """Before 2025-11-01 an investor saw GP=400; after, they saw 300. Both are
    correct answers for their own as_of, and collapsing them would be lookahead."""
    rec = _record()
    before, _ = select_period(rec, date(2025, 10, 31))
    assert before.gross_profit == 400, "restated value leaked backwards"
    after, _ = select_period(rec, date(2025, 11, 2))
    assert after.gross_profit == 300, "latest vintage public by as_of must win"


def test_gp_a_computes_off_the_selected_vintage():
    rec = _record()
    period, _ = select_period(rec, date(2025, 10, 31))
    gp_a, reason = gp_a_from_period(period)
    assert gp_a == 400 / 2000, reason
    period, _ = select_period(rec, date(2025, 11, 2))
    gp_a, reason = gp_a_from_period(period)
    assert gp_a == 300 / 2000, reason


def test_assets_are_paired_from_the_same_or_earlier_filing():
    """A balance sheet filed LATER than the income statement must not be paired
    with it — that would be a lookahead through the denominator."""
    facts = {"facts": {"us-gaap": {
        "GrossProfit": {"units": {"USD": [
            _flow("2024-01-01", "2024-12-31", 400, "2025-02-10")]}},
        "Assets": {"units": {"USD": [
            _inst("2024-12-31", 2000, "2025-06-01")]}},   # filed AFTER
    }}}
    assert periods_from_company_facts(facts) == ()


def test_missing_or_empty_payloads_degrade_to_no_opinion():
    assert periods_from_company_facts({}) == ()
    assert periods_from_company_facts({"facts": {}}) == ()
    assert edgar_fetcher("") is None
    assert edgar_fetcher(None) is None


def test_resolve_cik_reads_the_sec_ticker_map():
    payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    import edgar_fundamentals as ef
    ef._cik_cache.clear()
    try:
        assert resolve_cik("aapl", _fetch=lambda: payload) == 320193
        assert resolve_cik("NOPE", _fetch=lambda: payload) is None
    finally:
        ef._cik_cache.clear()

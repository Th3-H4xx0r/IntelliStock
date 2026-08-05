"""Point-in-time fundamental factors: no lookahead, no fabricated opinions.

The property that matters most is the same one `edgar_fundamentals` guards: a
fact is invisible until its EDGAR `filed` date, so a factor computed for
2026-04-01 can never contain a figure filed in 2026-08. The second property is
that "unknown" stays unknown — substituting 0.0 for a missing factor would let a
name with no data rank beside one that genuinely scored zero.
"""
import os
import sys
from datetime import date

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from edgar_factors import FactorSet, composite_score, factor_set  # noqa: E402


def _flow(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-K"}


def _inst(end, val, filed):
    return {"end": end, "val": val, "filed": filed, "form": "10-K"}


#: FY2024 filed 2025-02-10, FY2025 filed 2026-02-09.
#: assets 1000 -> 1200 (+20% growth), shares 100 -> 95 (5% buyback),
#: NI 150 vs CFO 200 -> accruals negative (good), GP 400/1200.
FACTS = {"facts": {"us-gaap": {
    "Assets": {"units": {"USD": [
        _inst("2024-12-31", 1000, "2025-02-10"),
        _inst("2025-12-31", 1200, "2026-02-09")]}},
    "GrossProfit": {"units": {"USD": [
        _flow("2024-01-01", "2024-12-31", 300, "2025-02-10"),
        _flow("2025-01-01", "2025-12-31", 400, "2026-02-09")]}},
    "NetIncomeLoss": {"units": {"USD": [
        _flow("2024-01-01", "2024-12-31", 100, "2025-02-10"),
        _flow("2025-01-01", "2025-12-31", 150, "2026-02-09")]}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        _flow("2024-01-01", "2024-12-31", 120, "2025-02-10"),
        _flow("2025-01-01", "2025-12-31", 200, "2026-02-09")]}},
    # EDGAR reports share counts under a "shares" unit, NOT USD. The first
    # version of this fixture used USD, which made net_issuance permanently
    # None on real data while the test passed — verified against live EDGAR.
    "CommonStockSharesOutstanding": {"units": {"shares": [
        _inst("2024-12-31", 100, "2025-02-10"),
        _inst("2025-12-31", 95, "2026-02-09")]}},
}}}


def test_all_four_factors_compute_after_the_fy2025_filing():
    fs = factor_set("TEST", date(2026, 3, 1), facts=FACTS)
    assert fs.period_end == date(2025, 12, 31)
    assert fs.gp_a == 400 / 1200
    assert fs.asset_growth == -((1200 / 1000) - 1.0)      # -0.20, growth is BAD
    assert fs.accruals == -((150 - 200) / 1200)           # +0.041, cash-backed is GOOD
    assert fs.net_issuance == -((95 / 100) - 1.0)         # +0.05, buyback is GOOD
    assert set(fs.available) == {"gp_a", "asset_growth", "accruals", "net_issuance"}


def test_no_lookahead_before_the_fy2025_filing_date():
    """The day before FY2025 was filed, the factors must be FY2024's."""
    fs = factor_set("TEST", date(2026, 2, 8), facts=FACTS)
    assert fs.period_end == date(2024, 12, 31)
    assert fs.gp_a == 300 / 1000
    # only one prior annual point exists, so year-over-year factors are unknown
    assert fs.asset_growth is None
    assert fs.net_issuance is None


def test_nothing_is_visible_before_the_first_filing():
    fs = factor_set("TEST", date(2025, 1, 1), facts=FACTS)
    assert fs.period_end is None
    assert fs.available == ()


def test_signs_point_the_same_way_higher_is_better():
    """A balance sheet that doubled and a share count that ballooned must both
    score NEGATIVE, or the composite silently rewards the wrong behaviour."""
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            _flow("2024-01-01", "2024-12-31", 10, "2025-02-10"),
            _flow("2025-01-01", "2025-12-31", 10, "2026-02-09")]}},
        "Assets": {"units": {"USD": [
            _inst("2024-12-31", 1000, "2025-02-10"),
            _inst("2025-12-31", 2000, "2026-02-09")]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            _inst("2024-12-31", 100, "2025-02-10"),
            _inst("2025-12-31", 150, "2026-02-09")]}},
    }}}
    fs = factor_set("TEST", date(2026, 3, 1), facts=facts)
    assert fs.asset_growth == -1.0        # doubled assets
    assert fs.net_issuance == -0.5        # 50% dilution


def test_composite_averages_only_what_is_known():
    fs = FactorSet("X", date(2026, 1, 1), gp_a=0.5, asset_growth=None,
                   accruals=None, net_issuance=0.1)
    assert composite_score(fs) == (0.5 + 0.1) / 2


def test_composite_is_none_when_nothing_is_known():
    assert composite_score(FactorSet("X", date(2026, 1, 1))) is None


def test_a_missing_factor_is_not_treated_as_zero():
    """Two names, one with a genuine 0.0 and one with no data, must NOT rank the
    same — that conflation is how an unresearched name looks 'average'."""
    known_zero = FactorSet("A", date(2026, 1, 1), gp_a=0.4, asset_growth=0.0)
    unknown = FactorSet("B", date(2026, 1, 1), gp_a=0.4)
    assert composite_score(known_zero) == 0.2
    assert composite_score(unknown) == 0.4
    assert composite_score(known_zero) != composite_score(unknown)


def test_weights_are_honoured():
    fs = FactorSet("X", date(2026, 1, 1), gp_a=1.0, accruals=0.0)
    assert composite_score(fs, {"gp_a": 3.0, "accruals": 1.0}) == 0.75


def test_bad_input_never_raises():
    assert factor_set("", date(2026, 1, 1)).available == ()
    assert factor_set("TEST", date(2026, 1, 1), facts={}).available == ()
    assert factor_set("TEST", date(2026, 1, 1), facts={"facts": {}}).available == ()

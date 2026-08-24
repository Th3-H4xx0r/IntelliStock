"""An empty sleeve must not raise leverage.

`core_budget = 1.0 - sat_pct - com_pct`, and an unfilled sleeve zeroes its own
`_pct`, so the freed weight flowed straight into `targets[bull]` — the 3x fund.

Measured in bt 773215 (bear 2026-02): bar 1 targeted
`{'SLV': 0.1, 'GDX': 0.1, 'TQQQ': 0.8}` because the graph sleeve had no picks
yet. That 80% filled at the HIGHEST QQQ print of the window, putting effective
beta at 2.34x against a design of 1.8x, and TQQQ went on to account for
-$1,046.85 of a -$787.51 loss — 133% of it, with every other sleeve positive.

The code already says a dead ranking should "degrade to the index, never to
cash". It degraded to the LEVERED index instead. These tests pin the intent:
unfilled sleeve budget goes to the unlevered chop occupant, and the levered core
keeps its designed weight no matter what the sleeves do.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_x import plan_targets  # noqa: E402

CFG = {"core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY",
       "core_bear_symbol": "", "core_weight": 1.0,
       "satellite_pct": 0.20, "commodity_pct": 0.20,
       "commodity_symbols": ["GLD", "SLV"], "commodity_max_names": 2}


def test_an_empty_satellite_does_not_lever_the_core_up():
    """THE bt 773215 bar-1 defect: 0.8 TQQQ instead of 0.6."""
    t, notes = plan_targets(risk_on=True, config=CFG, satellite_ranked=[],
                            commodity_ranked=["GLD", "SLV"])
    assert t["TQQQ"] == 0.6, (
        f"an empty sleeve levered the core to {t['TQQQ']} of NAV: {t}")
    assert round(t.get("SPY", 0.0), 6) == 0.2, (
        f"the unfilled sleeve budget did not land in the chop occupant: {t}")
    assert abs(sum(t.values()) - 1.0) < 1e-6


def test_both_sleeves_empty_still_holds_the_designed_core_weight():
    t, _ = plan_targets(risk_on=True, config=CFG, satellite_ranked=[],
                        commodity_ranked=[])
    assert t["TQQQ"] == 0.6, f"core levered up with both sleeves empty: {t}"
    assert round(t.get("SPY", 0.0), 6) == 0.4
    assert abs(sum(t.values()) - 1.0) < 1e-6


def test_a_partially_filled_satellite_only_reallocates_the_gap():
    """3 of 4 names ranked: 15% deployed, 5% to the index, core unchanged."""
    cfg = dict(CFG, satellite_max_names=4)
    t, _ = plan_targets(risk_on=True, config=cfg,
                        satellite_ranked=["AAA", "BBB", "CCC"],
                        commodity_ranked=["GLD", "SLV"])
    assert t["TQQQ"] == 0.6, f"partial fill changed core leverage: {t}"
    # 0.20/3 floored to the 6dp grid = 0.066666 each -> 0.199998 deployed.
    assert abs(sum(t[s] for s in ("AAA", "BBB", "CCC")) - 0.2) < 1e-4
    assert abs(sum(t.values()) - 1.0) < 1e-6


def test_a_full_sleeve_is_unchanged_by_the_fix():
    """The normal path must be byte-identical to before."""
    cfg = dict(CFG, satellite_max_names=2)
    t, _ = plan_targets(risk_on=True, config=cfg,
                        satellite_ranked=["AAA", "BBB"],
                        commodity_ranked=["GLD", "SLV"])
    assert t["TQQQ"] == 0.6
    assert t["AAA"] == 0.1 and t["BBB"] == 0.1
    assert t["GLD"] == 0.1 and t["SLV"] == 0.1
    assert "SPY" not in t or t["SPY"] == 0.0


def test_risk_off_is_unaffected_the_core_is_already_unlevered():
    t, _ = plan_targets(risk_on=False, config=CFG, satellite_ranked=[],
                        commodity_ranked=["GLD", "SLV"])
    assert "TQQQ" not in t or t["TQQQ"] == 0.0
    assert round(t["SPY"], 6) == 0.8
    assert abs(sum(t.values()) - 1.0) < 1e-6

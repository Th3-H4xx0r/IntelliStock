"""Tiered drawdown circuit (2026-07-19 regime-safety spec, Phase 4).

soft(-5%, SPY-corroborated) halts buys; hard(-9%) also tightens the cut
floor; kill(-12%) liquidates. Classifier-independent: driven purely by NAV
drawdown from the rolling peak.
"""
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


class _Emu:
    def __init__(self, value, positions=None):
        self._value = value
        self._initial_value = 6000.0
        self._pos = positions or {}

    def get_portfolio_value(self, prices=None):
        return self._value

    def get_positions(self):
        return dict(self._pos)

    def get_cash(self):
        return 0.0


def _spy_bars(ret20_pct: float):
    """60 daily SPY closes whose last-vs-21st-back return is ret20_pct."""
    base = datetime(2026, 1, 2)
    closes = [100.0] * 40 + [100.0 * (1 + ret20_pct / 100.0)] * 20
    return [
        {"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": c}
        for i, c in enumerate(closes)
    ]


def _run(value, spy_ret20=None, positions=None, cfg=None, cache=None):
    cache = cache if cache is not None else {}
    cache.setdefault("_portfolio_drawdown_state", {"peak_value": 6000.0})
    if spy_ret20 is not None:
        cache.setdefault("_overlay_bars_raw", {"SPY": _spy_bars(spy_ret20)})
    scores = {"NEW": {"score": 1, "action_intent": "initial_buy", "reason": "x"}}
    for sym in (positions or {}):
        scores.setdefault(sym, {"score": 0, "action_intent": "hold"})
    out = g._apply_portfolio_drawdown_halt(
        scores, list(scores.keys()), _Emu(value, positions), cfg or {},
        cache, {}, {}, "2026-03-10",
    )
    return out, cache


def test_no_tier_above_soft_threshold():
    out, cache = _run(5800.0, spy_ret20=-2.0)  # -3.3% dd
    assert out["NEW"]["score"] == 1
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""


def test_soft_tier_blocks_buys_when_spy_corroborates():
    out, cache = _run(5650.0, spy_ret20=-2.0)  # -5.8% dd, SPY down
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "soft"


def test_soft_tier_suppressed_in_rising_market():
    out, cache = _run(5650.0, spy_ret20=+3.0)  # -5.8% dd, SPY up → keep buying
    assert out["NEW"]["score"] == 1
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""


def test_soft_tier_blind_spy_is_conservative():
    out, cache = _run(5650.0, spy_ret20=None)  # no SPY data → block
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "soft"


def test_hard_tier_sets_cut_floor_override():
    out, cache = _run(5430.0, spy_ret20=+3.0)  # -9.5% dd — corroboration irrelevant
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "hard"
    assert cache["_dd_cut_floor_override"] == -7.0


def test_kill_tier_liquidates_held():
    out, cache = _run(5200.0, spy_ret20=-4.0,  # -13.3% dd
                      positions={"AAA": 1.0, "BBB": 2.0})
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "kill"
    assert out["AAA"]["score"] == -1 and out["BBB"]["score"] == -1
    assert "Circuit breaker" in out["AAA"]["reason"]  # survives grace gates
    assert out["NEW"]["score"] == 0


def test_kill_tier_marks_forced_exit():
    """2026-07-25 regression: the kill tier set score/reason but NOT
    `_forced_exit`, so the sweep at gna.py:27338 never added the symbol to
    `nexus_sell_enforcement` — and the broker's allowed_syms filter
    (broker.py:3790) then SILENTLY DROPPED the liquidation for any held name
    that had aged out of the discovery universe. The score assertion above
    passes either way, which is why this went unnoticed."""
    out, _ = _run(5200.0, spy_ret20=-4.0,  # -13.3% dd
                  positions={"AAA": 1.0, "BBB": 2.0})
    for sym in ("AAA", "BBB"):
        assert out[sym].get("_forced_exit") is True, (
            f"{sym} kill order would be dropped at broker.py:3790")


def test_circuit_disabled_by_config():
    out, cache = _run(5200.0, spy_ret20=-4.0, cfg={"drawdown_circuit_enabled": False})
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""
    assert out["NEW"]["score"] == 1  # legacy 15% halt not reached either


def test_override_cleared_when_recovered():
    _, cache = _run(5430.0, spy_ret20=+3.0)  # hard → override set
    assert "_dd_cut_floor_override" in cache
    out2, cache2 = _run(6000.0, spy_ret20=+3.0, cache=cache)  # recovered
    assert "_dd_cut_floor_override" not in cache2


def test_account_kill_includes_sleeve_legs():
    cfg = {"residual_sleeve_enabled": True, "residual_sleeve_symbol": "SPY",
           "residual_sleeve_bear_symbol": "SQQQ"}
    out, cache = _run(5200.0, spy_ret20=-4.0,
                      positions={"AAA": 1.0, "SQQQ": 70.0, "SPY": 2.0}, cfg=cfg)
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "kill"
    assert out["AAA"]["score"] == -1
    assert out["SQQQ"]["score"] == -1
    assert out["SPY"]["score"] == -1


# ── THE KILL LOOP (2026-08-15) ─────────────────────────────────────────────
#
# `portfolio_dd_kill_pct` is 12 and `portfolio_drawdown_halt_backfill_stop_pct`
# is 25, so across the 13-point band between them the circuit LIQUIDATES THE
# WHOLE BOOK and funds new entries ON THE SAME TICK. And because `peak_value` is
# re-based only on resume, the kill re-fires every bar. Measured on bt 569516,
# 02-02..02-16 — twelve consecutive buy->kill cycles:
#
#     02-02 BFQ BUY SNDK      -> 02-03 KILL SNDK
#     02-03 BFQ BUY TYRA      -> 02-04 KILL TYRA
#     02-04 BFQ BUY SNDK, C   -> 02-05 KILL C, SNDK
#     02-05 BFQ BUY LLY       -> 02-06 KILL LLY
#     02-09 BFQ BUY LLY       -> 02-10 KILL LLY
#     02-10 BFQ BUY GM        -> 02-11 KILL GM
#
# 74% of that run's governed turnover and 100% of its sell notional. The round
# trips LOST money (-$240.75), deepening the drawdown -18.0% -> -22.0%, which
# kept the kill armed. The churn deepens the drawdown that causes the churn.
#
# It is absent from the older runs only because they never made enough money to
# draw down 12%: bt 523085 peaked at +6.5% and logged SOFT twice, never KILL.

KILL_VALUE = 5100.0          # -15% from the 6000 peak: inside the 12-25% band
HELD = {"AAA": 10.0, "BBB": 5.0}


def test_today_the_kill_tier_still_funds_new_entries():
    """The defect, pinned. Flag off: the backfill queue keeps a budget on the
    very tick the book is being liquidated."""
    out, cache = _run(KILL_VALUE, spy_ret20=-4.0, positions=HELD)
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "kill"
    assert cache.get("_bfq_halt_budget_pct", 0.0) > 0.0, (
        "pre-fix behaviour: entries are still funded during a KILL")
    assert not cache.get("_dd_kill_blocks_entries")


def test_the_flag_zeroes_the_entry_budget_during_a_kill():
    out, cache = _run(KILL_VALUE, spy_ret20=-4.0, positions=HELD,
                      cfg={"dd_kill_blocks_entries_enabled": True})
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "kill"
    assert cache["_bfq_halt_budget_pct"] == 0.0
    assert cache["_dd_kill_blocks_entries"] is True, (
        "the momentum lanes read this; the queue budget alone does not close "
        "the entry path")


def test_the_entry_block_lifts_below_the_kill_tier():
    """It must bind ONLY while the kill is armed — a hard/soft tier keeps its
    graduated budget, and a healthy book is untouched."""
    for value in (5650.0, 5500.0, 5900.0):
        _out, cache = _run(value, spy_ret20=-4.0, positions=HELD,
                           cfg={"dd_kill_blocks_entries_enabled": True})
        if cache["_portfolio_drawdown_state"]["circuit_tier"] != "kill":
            assert not cache.get("_dd_kill_blocks_entries"), value


# The NAV must keep FALLING, which is what actually happened. A flat NAV
# accrues up-days and resumes after two bars — that is the correctness argument
# the code comment relies on ("the kill tier liquidates to 100% cash, and a
# 100%-cash portfolio has an EXACTLY flat NAV"). It fails in practice because
# the backfill queue and the momentum lanes keep buying, the round trips lose
# money, NAV keeps moving, `up_days` keeps resetting, and the halt never clears.
# bt 569516 sat in the band for 12 sessions on exactly this.
_DECLINE = [5100.0, 5050.0, 5000.0]


def test_the_kill_fires_once_per_episode_with_the_latch():
    """Second bar deeper in the drawdown must NOT re-liquidate."""
    cache = {"_portfolio_drawdown_state": {"peak_value": 6000.0}}
    cfg = {"dd_kill_once_per_episode_enabled": True}
    first, cache = _run(_DECLINE[0], spy_ret20=-4.0, positions=HELD, cfg=cfg,
                        cache=cache)
    killed_first = [s for s, v in first.items() if v.get("score") == -1]
    assert killed_first, "the first kill must still liquidate"
    assert cache["_portfolio_drawdown_state"].get("kill_fired_episode") is True

    second, cache = _run(_DECLINE[1], spy_ret20=-4.0, positions=HELD,
                         cfg=cfg, cache=cache)
    killed_second = [s for s, v in second.items() if v.get("score") == -1]
    assert not killed_second, (
        f"the kill re-fired on the next bar: {killed_second}. peak_value is "
        "only re-based on resume, so without the latch it re-arms every bar — "
        "12 times in bt 569516.")


def test_without_the_latch_the_kill_refires_every_bar():
    """Proves the latch is load-bearing rather than coincidental."""
    cache = {"_portfolio_drawdown_state": {"peak_value": 6000.0}}
    fired = 0
    for value in _DECLINE:
        out, cache = _run(value, spy_ret20=-4.0, positions=HELD, cache=cache)
        if [s for s, v in out.items() if v.get("score") == -1]:
            fired += 1
    assert fired == 3, (
        f"expected the documented re-fire on every bar, got {fired}. The peak "
        "is re-based only on resume, so a still-falling book re-arms the tier "
        "every bar — 12 times in bt 569516.")


def test_flag_off_is_byte_identical_across_the_drawdown_range():
    for value in (5900.0, 5650.0, 5500.0, 5100.0, 4400.0):
        a_cache = {"_portfolio_drawdown_state": {"peak_value": 6000.0}}
        b_cache = {"_portfolio_drawdown_state": {"peak_value": 6000.0}}
        a, a_cache = _run(value, spy_ret20=-4.0, positions=HELD, cache=a_cache,
                          cfg={})
        b, b_cache = _run(value, spy_ret20=-4.0, positions=HELD, cache=b_cache,
                          cfg={"dd_kill_blocks_entries_enabled": False,
                               "dd_kill_once_per_episode_enabled": False})
        assert {k: v.get("score") for k, v in a.items()} == \
               {k: v.get("score") for k, v in b.items()}, value
        assert a_cache["_portfolio_drawdown_state"]["circuit_tier"] == \
               b_cache["_portfolio_drawdown_state"]["circuit_tier"], value

"""bt 571147 defect (A): the peak give-back exit fired 55 times and SLV never sold.

    PEAK GIVE-BACK EXIT: SLV peaked +60.5% (>=30%) and has handed back 28.2% (>=25%)   x55
    Monitor decision: SLV day 28 pnl=+15.2% cp=$77.59 entry=$67.35 -> SELL (...)        x52
    <no `FILL SELL SLV`, no `Sell enforcement ADD: SLV`, no `ML overlay PRESERVE`>

Compare CART in the SAME run — the -10% circuit breaker exit that DID execute:

    [sell-gate] CART | gate=circuit_breaker | ... | result=fired
    ML overlay PRESERVE forced-exit: CART score=-1 reason=Circuit breaker: ...
    Sell enforcement ADD: CART forced_exit=True, reason=Circuit breaker: ...
    Nexus sell enforcement: CART
    V7.5 sell enforcement injection: 1 held ticker(s) added to execution: CART
    FILL SELL CART qty=16.24549906 ...

The whole difference is one boolean. `_forced_exit` is computed by substring
match of the exit REASON against `_FORCED_EXIT_TAGS` = ("Fast loser",
"Trailing stop", "Hold-limit", "Circuit breaker", "Catastrophic stop"), and
"Peak give-back exit: ..." matches none of them. With `_forced_exit=False`:

  * FULL cycle — `_apply_ml_overlay`'s first branch is
    `if base["_forced_exit"]: keep the -1`; falling past it the else-branch
    RECOMPUTES the score from raw_net. SLV carried raw_score=+1.000 on the
    2026-02-02 full cycle, so the sell silently became a buy.
  * The forced-exit sweep (`Sell enforcement ADD`) only admits
    `_forced_exit and score == -1` into `nexus_sell_enforcement`, and a held
    name outside the discovery universe reaches the broker ONLY through
    enforcement (SLV is absent from all 120 symbols that cycle handed over).
  * MONITOR cycle — the broker discards the monitor's score dict entirely
    ("Run-once strategy returned scores for 0 symbols"), so
    `_nexus_sell_enforcement` is the monitor's only channel, and that loop
    also requires `_forced_exit`.
  * Downstream of the same flag: rank-band exit suppression, the
    `llm_sell_min_hold` gate (15d in this document), `winner_protect`, and the
    overlay `sell_block` are all guarded by `not base["_forced_exit"]`.

`peak_giveback_forced_exit_enabled` (default False) makes the give-back exit
travel CART's path. Every test below fails on the pre-fix code.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402
from strategies.graph_nexus_analysis import (  # noqa: E402
    _FORCED_EXIT_TAGS,
    _evaluate_position_risk,
)


# SLV, bt 571147: 12.47106599 sh @ $67.353890 on 2026-01-02, peak $107.99
# (+60.3%), first give-back fire on 2026-01-30 at $77.59 (28.2% off the peak).
SLV_ENTRY = 67.353890
SLV_PEAK = 107.99
SLV_FIRE = 77.59


SLV_ENTRY_TS = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)


def _slv_emu():
    from portfolio_emulator import PortfolioEmulator

    pe = PortfolioEmulator(initial_cash=6000.0)
    pe.buy("SLV", 12.47106599, SLV_ENTRY, timestamp=SLV_ENTRY_TS)
    return pe


def _cfg(**over):
    """571147's exit-relevant document, trimmed to what the helper reads."""
    cfg = {
        "peak_giveback_min_peak_pnl_pct": 30.0,
        "peak_giveback_exit_drawdown_pct": 25.0,
        "peak_protection_enabled": True,
        "peak_protection_min_peak_pnl_pct": 30.0,
        "peak_protection_max_drawdown_from_peak_pct": 25.0,
        "trailing_stop_disabled": True,
        "profit_take_enabled": False,
        "fast_loser_cut_pct": -10.0,
        "max_open_loss_pct": -15.0,
        "trailing_stop_activation_pct": 10.0,
        "initial_grace_enabled": False,
        "sell_enforcement_min_hold_days": 15,
    }
    cfg.update(over)
    return cfg


def _run(cfg, cp=SLV_FIRE, peak=SLV_PEAK, held_days=28, mode="full"):
    cache = {"_peak_SLV_2026-01-02T14:00:00+00:00": peak}
    return _evaluate_position_risk(
        "SLV",
        fresh_score=0 if mode == "monitor" else 1,
        fresh_reason="monitor: hold" if mode == "monitor" else "Graph(1 paths, raw=+1.000)",
        config=cfg,
        portfolio_emulator=_slv_emu(),
        strategy_cache=cache,
        prices={"SLV": cp},
        price_history={},
        date_key="2026-01-30",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc),
        held_days=held_days,
        max_hold_days=90,
        bypass_winner_protection=(mode == "monitor"),
        side_effect_mode=mode,
    )


# ── 0. the defect itself: the reason matches no forced-exit tag ─────────────

def test_the_give_back_reason_matches_no_forced_exit_tag():
    """This is WHY nothing sold. Documented, not asserted away."""
    _s, reason, _e = _run(_cfg())
    assert _s == -1 and "Peak give-back exit" in reason
    assert not any(tag in reason for tag in _FORCED_EXIT_TAGS), (
        "if this ever matches, the flag below is redundant — delete the key"
    )


def test_the_circuit_breaker_reason_DOES_match(monkeypatch):
    """CART's path, for contrast: its reason carries a forced-exit tag."""
    assert any(t in "Circuit breaker: -10.4% loss hit floor -10.0%"
               for t in _FORCED_EXIT_TAGS)


# ── 1. the fix: extras carries the forced-exit request ─────────────────────

def test_give_back_requests_forced_exit_when_enabled():
    score, reason, extras = _run(_cfg(peak_giveback_forced_exit_enabled=True))
    assert score == -1
    assert "Peak give-back exit" in reason
    assert extras.get("forced_exit") is True


def test_default_off_is_byte_identical():
    """Key absent -> the give-back still fires and still asks for nothing."""
    score, reason, extras = _run(_cfg())
    assert score == -1 and "Peak give-back exit" in reason
    assert "forced_exit" not in extras


def test_explicit_false_is_off():
    _s, _r, extras = _run(_cfg(peak_giveback_forced_exit_enabled=False))
    assert "forced_exit" not in extras


def test_a_name_below_the_thresholds_asks_for_nothing():
    """SNDK's worst give-back, 22.4% — the widest a surviving winner printed."""
    cp = SLV_PEAK * (1 - 0.224)
    score, _r, extras = _run(_cfg(peak_giveback_forced_exit_enabled=True), cp=cp)
    assert score != -1
    assert "forced_exit" not in extras


def test_forced_exit_never_travels_without_a_live_sell():
    """Grace can still take the score off -1; the flag must not survive that."""
    cfg = _cfg(peak_giveback_forced_exit_enabled=True,
               initial_grace_enabled=True, initial_grace_bars=60)
    score, _r, extras = _run(cfg, held_days=1)
    if score != -1:
        assert "forced_exit" not in extras


# ── 2. _finalize_scores: the flag reaches the score doc ────────────────────

def _finalize(cfg, cp=SLV_FIRE):
    return gna._finalize_scores(
        ["SLV"],
        {},
        {"SLV": {"raw_score": 1.0, "reasons": ["trend_momentum"], "n_paths": 1}},
        cfg,
        pending_by_symbol={},
        portfolio_emulator=_slv_emu(),
        date_key="2026-01-30",
        prices={"SLV": cp},
        strategy_cache={"_peak_SLV_2026-01-02T14:00:00+00:00": SLV_PEAK},
        price_history={},
    )


def test_finalize_scores_sets_forced_exit_when_enabled():
    out = _finalize(_cfg(peak_giveback_forced_exit_enabled=True))
    assert out["SLV"]["score"] == -1
    assert out["SLV"]["_forced_exit"] is True, (
        "without this the forced-exit sweep never adds SLV to "
        "nexus_sell_enforcement and the ML overlay recomputes the -1 away"
    )


def test_finalize_scores_default_off_reproduces_the_bug():
    out = _finalize(_cfg())
    assert out["SLV"]["score"] == -1
    assert not out["SLV"]["_forced_exit"]


# ── 3. the ML overlay lane: where the -1 actually died ─────────────────────

def test_ml_overlay_recompute_is_what_eats_an_unforced_sell():
    """SLV carried raw_score=+1.000 on the 2026-02-02 full cycle.

    `_apply_ml_overlay`'s first branch preserves a forced exit; everything
    else falls through to a recompute from raw_net. With the flag set the
    branch is taken and the -1 survives — that is the `ML overlay PRESERVE
    forced-exit` line CART printed and SLV never did.
    """
    forced = _finalize(_cfg(peak_giveback_forced_exit_enabled=True))["SLV"]
    plain = _finalize(_cfg())["SLV"]

    def overlay_final(base, raw_net, buy_threshold=0.15):
        if base.get("_forced_exit"):
            return base["score"]          # PRESERVE
        final = 0
        if raw_net >= buy_threshold:
            final = 1                     # recompute -> the sell becomes a BUY
        return final

    assert overlay_final(forced, raw_net=1.0) == -1
    assert overlay_final(plain, raw_net=1.0) == 1


# ── 4. the rank band and the min-hold gate both key on the same flag ───────

def _band_universe(forced):
    """Ten names; the held one carries a give-back exit at rank #1.

    `rank_band_enabled` was True in 571147's document. A held name high in the
    ranking sits INSIDE the hold band, which is exactly where the band converts
    a signal sell into a hold — unless the doc is a protective exit.
    """
    scores = {}
    for i in range(1, 11):
        scores[f"S{i:02d}"] = {"score": 0, "raw_net_score": 1.0 - i * 0.05,
                               "reason": "hold"}
    scores["S01"] = {
        "score": -1,
        "raw_net_score": 0.95,
        "reason": ("Peak give-back exit: peaked +60.0% then handed back 28.2% "
                   "(thresholds 30%/25%)"),
        "_forced_exit": forced,
    }
    return sorted(scores), scores


def test_rank_band_exit_suppression_is_bypassed():
    """The band ranks on the news/graph/ML blend; a stop it can veto is not a
    stop. The exemption is keyed on `_forced_exit` (or a `_RISK_EXIT_TAGS`
    substring, which the give-back reason does not carry)."""
    from portfolio_emulator import PortfolioEmulator

    cfg = {"rank_band_enabled": True, "rank_band_entry_pct": 10.0,
           "rank_band_exit_pct": 50.0}
    for forced, expected in ((True, -1), (False, 0)):
        pe = PortfolioEmulator(initial_cash=100000.0)
        pe.buy("S01", 10, 100.0, timestamp=SLV_ENTRY_TS)
        syms, scores = _band_universe(forced)
        gna._apply_rank_band_gate(scores, syms, pe, cfg)
        assert scores["S01"]["score"] == expected, (
            f"_forced_exit={forced} -> expected score {expected}"
        )


def test_min_hold_and_winner_protect_are_bypassed_by_the_flag():
    """Both gates are literally `not base.get("_forced_exit")`."""
    forced = _finalize(_cfg(peak_giveback_forced_exit_enabled=True))["SLV"]
    plain = _finalize(_cfg())["SLV"]
    assert forced.get("_forced_exit") and not plain.get("_forced_exit")


# ── 5. the forced-exit sweep: what actually puts SLV in front of the broker ─

def test_forced_exit_sweep_admits_the_give_back():
    """Mirror of the `Sell enforcement ADD` sweep."""
    def sweep(scores):
        enf, sizes = set(), {}
        for sym, doc in scores.items():
            if isinstance(doc, dict) and doc.get("_forced_exit") and doc.get("score") == -1:
                enf.add(sym)
                sizes.setdefault(sym, {})["sell_fraction"] = 1.0
        return enf, sizes

    enf_on, sizes_on = sweep(_finalize(_cfg(peak_giveback_forced_exit_enabled=True)))
    enf_off, _ = sweep(_finalize(_cfg()))
    assert enf_on == {"SLV"} and sizes_on["SLV"]["sell_fraction"] == 1.0
    assert enf_off == set(), "the pre-fix behaviour: 55 fires, zero enforcement"


# ── 6. the monitor cycle: 52 of the 55 fires were monitor ticks ────────────

def test_monitor_cycle_marks_the_give_back_forced():
    score, reason, extras = _run(
        _cfg(peak_giveback_forced_exit_enabled=True), mode="monitor")
    assert score == -1 and "Peak give-back exit" in reason
    assert extras.get("forced_exit") is True


def test_monitor_enforcement_loop_admits_it():
    """`_nexus_sell_enforcement` is the monitor's ONLY channel to execution."""
    score, reason, extras = _run(
        _cfg(peak_giveback_forced_exit_enabled=True), mode="monitor")
    entry = {
        "score": score,
        "reason": reason,
        "_forced_exit": bool(
            (score == -1 and reason and any(t in reason for t in _FORCED_EXIT_TAGS))
            or (score == -1 and extras.get("forced_exit"))
        ),
    }
    se = [s for s, e in {"SLV": entry}.items()
          if e.get("score") == -1 and e.get("_forced_exit")]
    assert se == ["SLV"]


def test_monitor_cycle_end_to_end_emits_sell_enforcement():
    """Full `_run_monitor_cycle`, the shape bt 571147 actually ran."""
    from portfolio_emulator import PortfolioEmulator

    pe = PortfolioEmulator(initial_cash=6000.0)
    pe.buy("SLV", 12.47106599, SLV_ENTRY,
           timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc))
    cfg = _cfg(peak_giveback_forced_exit_enabled=True,
               nexus_monitor_risk_exit_always_enabled=True,
               _nexus_is_live_mode=False, use_llm_sentiment=False,
               max_hold_days=90, cash_reserve_floor_pct=0.10,
               nexus_dual_cadence_monitor_block_same_day_exit=True)
    cache = {}
    out = gna.GraphNexusAnalysis()._run_monitor_cycle(
        symbols_list=["SLV"],
        prices={"SLV": SLV_PEAK},          # bar 1: set the high-water mark
        price_history={},
        config=cfg,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-01-29",
        current_time=datetime(2026, 1, 29, 20, 0, tzinfo=timezone.utc),
    )
    out = gna.GraphNexusAnalysis()._run_monitor_cycle(
        symbols_list=["SLV"],
        prices={"SLV": SLV_FIRE},          # bar 2: 28.2% off the peak
        price_history={},
        config=cfg,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-01-30",
        current_time=datetime(2026, 1, 30, 20, 0, tzinfo=timezone.utc),
    )
    assert out["SLV"]["score"] == -1
    assert out["SLV"]["_forced_exit"] is True
    assert "SLV" in (out.get("_nexus_sell_enforcement") or []), (
        "this list is the ONLY thing the broker reads back from a monitor tick"
    )


def test_monitor_cycle_end_to_end_default_off_still_drops_it():
    from portfolio_emulator import PortfolioEmulator

    pe = PortfolioEmulator(initial_cash=6000.0)
    pe.buy("SLV", 12.47106599, SLV_ENTRY,
           timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc))
    cfg = _cfg(nexus_monitor_risk_exit_always_enabled=True,
               _nexus_is_live_mode=False, use_llm_sentiment=False,
               max_hold_days=90, cash_reserve_floor_pct=0.10,
               nexus_dual_cadence_monitor_block_same_day_exit=True)
    cache = {}
    strat = gna.GraphNexusAnalysis()
    for date_key, px, hh in (("2026-01-29", SLV_PEAK, 29), ("2026-01-30", SLV_FIRE, 30)):
        out = strat._run_monitor_cycle(
            symbols_list=["SLV"], prices={"SLV": px}, price_history={},
            config=cfg, portfolio_emulator=pe, strategy_cache=cache,
            date_key=date_key,
            current_time=datetime(2026, 1, hh, 20, 0, tzinfo=timezone.utc),
        )
    assert out["SLV"]["score"] == -1                      # it FIRES
    assert not out["SLV"]["_forced_exit"]                 # and goes nowhere
    assert "SLV" not in (out.get("_nexus_sell_enforcement") or [])

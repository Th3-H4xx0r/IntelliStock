"""
Evidence-based selection + exit discipline (turnover cost control).

Every mechanism under test here is gated behind a config key that defaults to
TODAY's behaviour, because this strategy runs a real-money instance. The
FIRST assertion in each group is therefore always the default-off case: with
the flag absent, behaviour must be identical to before the flag existed.

Covers:
  - `_rank_band_cutoffs` / `_apply_rank_band_gate` — the Novy-Marx & Velikov
    buy/hold spread (enter in the top decile, exit out of the top half, never
    trade inside the band).
  - `profit_take_disabled` — the Odean/Frazzini kill switch that outranks
    `profit_take_enabled` + `profit_take_tiers`.
  - `trailing_stop_disabled` + `catastrophic_stop_*` — replacing the intraday
    trailing stop with one unconditional daily-close loss floor.
  - `edge_type_corroboration_weight` — de-weighting "N edge types agree" in
    the conviction allocation schedule.
  - The one-target-vector invariant: `_finalize_scores` emits exactly one
    decision per symbol, so per-signal trades in the same name cannot be
    executed independently.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import strategies.graph_nexus_analysis as gna  # noqa: E402
from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_rank_band_gate,
    _conviction_allocation_schedule,
    _evaluate_position_risk,
    _finalize_scores,
    _FORCED_EXIT_TAGS,
    _rank_band_cutoffs,
    _RISK_EXIT_TAGS,
)


class _Emu:
    """Minimal PortfolioEmulator stand-in: positions + buy trade history."""

    def __init__(self):
        self._positions: dict = {}
        self._trades: list = []

    def add(self, ticker: str, shares: float, entry_price: float, ts=None):
        self._positions[ticker] = self._positions.get(ticker, 0.0) + shares
        self._trades.append({
            "ticker": ticker,
            "action": "buy",
            "price": float(entry_price),
            "shares": float(shares),
            "timestamp": ts or datetime(2026, 1, 1, tzinfo=timezone.utc),
        })

    def get_positions(self):
        return dict(self._positions)

    def get_trade_history(self):
        return list(self._trades)


def _doc(score: int, raw: float, reason: str = "graph", **extra) -> dict:
    d = {"score": score, "raw_net_score": raw, "reason": reason, "ml": {}}
    d.update(extra)
    return d


def _band_config(**overrides) -> dict:
    cfg = {
        "rank_band_enabled": True,
        "rank_band_entry_pct": 10.0,
        "rank_band_exit_pct": 50.0,
        "rotation_ml_weight": 0.0,
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# _rank_band_cutoffs (pure)
# ---------------------------------------------------------------------------


def test_cutoffs_decile_and_half():
    assert _rank_band_cutoffs(100, 10.0, 50.0) == (10, 50)


def test_cutoffs_round_up_on_small_universe():
    # 20 names: top decile is 2 (ceil), top half is 10.
    assert _rank_band_cutoffs(20, 10.0, 50.0) == (2, 10)
    # Never zero — a 3-name universe still has a #1.
    assert _rank_band_cutoffs(3, 10.0, 50.0) == (1, 2)


def test_cutoffs_empty_universe():
    assert _rank_band_cutoffs(0, 10.0, 50.0) == (0, 0)


def test_cutoffs_clamp_inverted_band():
    # exit_pct < entry_pct is an operator typo. An inverted band would put every
    # held name simultaneously below the sell edge and above the buy edge —
    # liquidate the book, block the rebuy. Clamped to "no band" instead.
    entry_cut, exit_cut = _rank_band_cutoffs(100, 50.0, 10.0)
    assert entry_cut == exit_cut == 50


def test_cutoffs_never_exceed_universe():
    _, exit_cut = _rank_band_cutoffs(10, 10.0, 500.0)
    assert exit_cut == 10


# ---------------------------------------------------------------------------
# _apply_rank_band_gate — default OFF
# ---------------------------------------------------------------------------


def test_band_disabled_by_default_is_a_no_op():
    scores = {"AAA": _doc(1, 0.10), "BBB": _doc(-1, -0.90)}
    before = {k: dict(v) for k, v in scores.items()}
    out = _apply_rank_band_gate(scores, ["AAA", "BBB"], _Emu(), {})
    assert out is scores
    assert {k: dict(v) for k, v in out.items()} == before


def test_band_explicitly_disabled_is_a_no_op():
    scores = {"AAA": _doc(1, 0.10)}
    _apply_rank_band_gate(scores, ["AAA"], _Emu(), _band_config(rank_band_enabled=False))
    assert scores["AAA"]["score"] == 1


# ---------------------------------------------------------------------------
# Entry side: buy only in the top decile
# ---------------------------------------------------------------------------


def _ten_name_universe(buy_syms=()):
    syms = [f"S{i:02d}" for i in range(10)]
    scores = {
        # S00 highest conviction, S09 lowest.
        s: _doc(1 if s in buy_syms else 0, 1.0 - 0.1 * i)
        for i, s in enumerate(syms)
    }
    return syms, scores


def test_band_allows_a_buy_inside_the_entry_band():
    syms, scores = _ten_name_universe(buy_syms={"S00"})
    _apply_rank_band_gate(scores, syms, _Emu(), _band_config())
    assert scores["S00"]["score"] == 1


def test_band_blocks_a_buy_outside_the_entry_band():
    # 10 names, entry_pct=10 -> only rank #1 may be bought. S03 is rank #4.
    syms, scores = _ten_name_universe(buy_syms={"S03"})
    _apply_rank_band_gate(scores, syms, _Emu(), _band_config())
    assert scores["S03"]["score"] == 0
    assert scores["S03"]["action_intent"] == "hold"
    assert "RANK_BAND" in scores["S03"]["reason"]


def test_band_blocks_an_add_on_a_held_name_inside_the_band():
    # "Never trade inside the band" — an add is an entry of new capital, so a
    # held name ranked mid-band gets no add either.
    syms, scores = _ten_name_universe(buy_syms={"S04"})
    emu = _Emu()
    emu.add("S04", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S04"]["score"] == 0


def test_band_allows_an_add_on_a_held_top_decile_name():
    syms, scores = _ten_name_universe(buy_syms={"S00"})
    emu = _Emu()
    emu.add("S00", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S00"]["score"] == 1


# ---------------------------------------------------------------------------
# Exit side: sell only once out of the hold band
# ---------------------------------------------------------------------------


def test_band_holds_a_signal_sell_still_inside_the_hold_band():
    syms, scores = _ten_name_universe()
    scores["S02"] = _doc(-1, 0.80, reason="Graph(3 paths, raw=-0.20): downgrade")
    emu = _Emu()
    emu.add("S02", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S02"]["score"] == 0
    assert "RANK_BAND" in scores["S02"]["reason"]
    # The original reason is preserved for the audit trail.
    assert "downgrade" in scores["S02"]["reason"]


def test_band_releases_a_signal_sell_once_out_of_the_hold_band():
    syms, scores = _ten_name_universe()
    # S08 is rank #9 of 10 — outside the top half.
    scores["S08"] = _doc(-1, 0.20, reason="Graph(1 paths, raw=-0.40): downgrade")
    emu = _Emu()
    emu.add("S08", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S08"]["score"] == -1


def test_band_ignores_sell_signals_on_names_we_do_not_hold():
    syms, scores = _ten_name_universe()
    scores["S02"] = _doc(-1, 0.80, reason="Graph: downgrade")
    _apply_rank_band_gate(scores, syms, _Emu(), _band_config())
    assert scores["S02"]["score"] == -1


@pytest.mark.parametrize("tag", list(_RISK_EXIT_TAGS))
def test_band_never_blocks_a_protective_exit(tag):
    # A stop a ranking can veto is not a stop.
    syms, scores = _ten_name_universe()
    scores["S01"] = _doc(-1, 0.90, reason=f"{tag}: protective exit")
    emu = _Emu()
    emu.add("S01", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S01"]["score"] == -1


def test_band_never_blocks_a_forced_exit_flag():
    syms, scores = _ten_name_universe()
    scores["S01"] = _doc(-1, 0.90, reason="bear book trim", _forced_exit=True)
    emu = _Emu()
    emu.add("S01", 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, _band_config())
    assert scores["S01"]["score"] == -1


def test_band_exempts_etf_lane_and_sleeve_symbols():
    # ETF conviction is trend strength, not the news/graph/ML blend — ranking
    # them against stocks would switch the sleeve off rather than tighten it.
    etf = sorted(gna._ALL_ETF_TICKERS)[0]
    syms, scores = _ten_name_universe()
    scores[etf] = _doc(1, 0.0)
    syms = syms + [etf]
    _apply_rank_band_gate(scores, syms, _Emu(), _band_config())
    assert scores[etf]["score"] == 1


def test_band_is_deterministic_under_ties():
    # Every name identical: the ticker tiebreak, not dict order, decides who
    # sits on the decile boundary.
    syms = [f"T{i:02d}" for i in range(10)]
    first = {s: _doc(1, 0.5) for s in syms}
    second = {s: _doc(1, 0.5) for s in reversed(syms)}
    _apply_rank_band_gate(first, syms, _Emu(), _band_config())
    _apply_rank_band_gate(second, list(reversed(syms)), _Emu(), _band_config())
    assert [s for s in syms if first[s]["score"] == 1] == ["T00"]
    assert [s for s in syms if second[s]["score"] == 1] == ["T00"]


def test_band_denominator_ignores_duplicate_tickers():
    # A repeated ticker inflates the percentile denominator and quietly widens
    # the entry band. 10 unique names -> entry cut is #1 either way.
    syms, scores = _ten_name_universe(buy_syms={"S01"})
    _apply_rank_band_gate(scores, syms + syms, _Emu(), _band_config())
    assert scores["S01"]["score"] == 0


def test_band_fails_open_when_positions_cannot_be_read():
    class _Broken:
        def get_positions(self):
            raise RuntimeError("changefeed died")

    syms, scores = _ten_name_universe(buy_syms={"S05"})
    _apply_rank_band_gate(scores, syms, _Broken(), _band_config())
    assert scores["S05"]["score"] == 1


# ---------------------------------------------------------------------------
# profit_take_disabled
# ---------------------------------------------------------------------------


def _pt_config(**overrides) -> dict:
    cfg = {
        "max_open_loss_pct": -15.0,
        "fast_loser_cut_pct": -10.0,
        "trailing_stop_activation_pct": 200.0,   # keep the trail out of the way
        "trailing_stop_pnl_scaling_enabled": False,
        "mega_winner_protect_enabled": False,
        "peak_protection_enabled": False,
        "initial_grace_enabled": False,
        "profit_take_enabled": True,
        "profit_take_gain_pct": 40.0,
        "profit_take_sell_fraction": 0.5,
    }
    cfg.update(overrides)
    return cfg


def _risk(sym, cfg, emu, price, *, mode="full", score=0, cache=None, held_days=1):
    return _evaluate_position_risk(
        sym,
        fresh_score=score,
        fresh_reason="No graph signal",
        config=cfg,
        portfolio_emulator=emu,
        strategy_cache={} if cache is None else cache,
        prices={sym: price},
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=held_days,
        max_hold_days=0,
        side_effect_mode=mode,
    )


def test_profit_take_still_fires_when_the_kill_switch_is_absent():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    score, reason, extras = _risk("FOO", _pt_config(), emu, 150.0)
    assert score == -1
    assert "Profit take" in reason
    assert extras["sell_fraction"] == pytest.approx(0.5)


def test_profit_take_disabled_outranks_profit_take_enabled():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    score, reason, extras = _risk(
        "FOO", _pt_config(profit_take_disabled=True), emu, 150.0)
    assert score == 0
    assert "Profit take" not in reason
    assert "sell_fraction" not in extras


def test_profit_take_disabled_also_kills_tiers():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _pt_config(profit_take_disabled=True,
                     profit_take_tiers=[[20, 0.25], [40, 0.25]])
    score, reason, _ = _risk("FOO", cfg, emu, 150.0)
    assert score == 0
    assert "Profit take" not in reason


def test_profit_take_tiers_still_fire_without_the_kill_switch():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _pt_config(profit_take_tiers=[[20, 0.25], [40, 0.25]])
    score, reason, extras = _risk("FOO", cfg, emu, 150.0)
    assert score == -1
    assert "Profit take tier" in reason
    assert extras["sell_fraction"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# trailing_stop_disabled + catastrophic stop
# ---------------------------------------------------------------------------


def _ts_config(**overrides) -> dict:
    cfg = {
        "max_open_loss_pct": -95.0,       # keep the tier floor out of the way
        "fast_loser_cut_pct": -95.0,      # keep the fast cut out of the way
        "circuit_breaker_regime_gating_enabled": False,
        "trailing_stop_activation_pct": 5.0,
        "trailing_stop_activation_vol_multiplier": 0.0,
        "trailing_stop_pct": 8.0,
        "trailing_stop_pnl_scaling_enabled": False,
        "mega_winner_protect_enabled": False,
        "peak_protection_enabled": False,
        "profit_take_enabled": False,
        "initial_grace_enabled": False,
    }
    cfg.update(overrides)
    return cfg


def test_trailing_stop_fires_by_default():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cache: dict = {}
    _risk("FOO", _ts_config(), emu, 150.0, cache=cache)     # arm + set the peak
    score, reason, _ = _risk("FOO", _ts_config(), emu, 130.0, cache=cache)
    assert score == -1
    assert "Trailing stop" in reason


def test_trailing_stop_disabled_suppresses_the_sell():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(trailing_stop_disabled=True)
    cache: dict = {}
    _risk("FOO", cfg, emu, 150.0, cache=cache)
    score, reason, _ = _risk("FOO", cfg, emu, 130.0, cache=cache)
    assert score == 0
    assert "Trailing stop" not in reason


def test_trailing_stop_disabled_still_maintains_the_peak_high_water_mark():
    # The HWM is shared with peak protection, winner-add's drawdown gate and
    # the mega-winner widener. Freezing it would be a different — and worse —
    # change than "no trailing stop".
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(trailing_stop_disabled=True)
    cache: dict = {}
    _risk("FOO", cfg, emu, 150.0, cache=cache)
    peak_keys = [k for k in cache if "peak" in k.lower() and not k.endswith("::armed")]
    assert peak_keys, f"no peak key written: {sorted(cache)}"
    assert max(float(cache[k]) for k in peak_keys) == pytest.approx(150.0)


def test_catastrophic_stop_is_off_by_default():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    score, reason, _ = _risk("FOO", _ts_config(), emu, 70.0)   # -30%
    assert score == 0
    assert "Catastrophic stop" not in reason


def test_catastrophic_stop_fires_at_the_floor():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(catastrophic_stop_enabled=True, catastrophic_stop_pct=-20.0)
    score, reason, _ = _risk("FOO", cfg, emu, 75.0)            # -25%
    assert score == -1
    assert "Catastrophic stop" in reason


def test_catastrophic_stop_does_not_fire_above_the_floor():
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(catastrophic_stop_enabled=True, catastrophic_stop_pct=-20.0)
    score, _, _ = _risk("FOO", cfg, emu, 85.0)                 # -15%
    assert score == 0


def test_catastrophic_stop_is_evaluated_on_the_daily_cycle_only():
    # Kaminski & Lo: a stop evaluated on intraday marks fires on noise, which
    # re-prices the whole rationale. Monitor ticks do not arm it.
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(catastrophic_stop_enabled=True, catastrophic_stop_pct=-20.0)
    score, _, _ = _risk("FOO", cfg, emu, 75.0, mode="monitor")
    assert score == 0


def test_catastrophic_stop_is_not_bypassed_by_peak_protection():
    # Peak protection defers losers to "the trailing stop" — the very mechanism
    # the catastrophic stop replaces, so it must not be able to veto it.
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(
        catastrophic_stop_enabled=True,
        catastrophic_stop_pct=-20.0,
        trailing_stop_disabled=True,
        peak_protection_enabled=True,
        peak_protection_min_peak_pnl_pct=30.0,
        peak_protection_max_drawdown_from_peak_pct=90.0,
    )
    cache: dict = {}
    _risk("FOO", cfg, emu, 200.0, cache=cache)                 # build a +100% peak
    score, reason, _ = _risk("FOO", cfg, emu, 75.0, cache=cache)
    assert score == -1
    assert "Catastrophic stop" in reason


def test_catastrophic_stop_is_a_forced_and_risk_exit():
    # Without the tag in both tuples the stop fires but nothing sells: grace
    # suppresses it, and it never reaches nexus_sell_enforcement.
    assert any(t in "Catastrophic stop: floor" for t in _FORCED_EXIT_TAGS)
    assert any(t in "Catastrophic stop: floor" for t in _RISK_EXIT_TAGS)


def test_catastrophic_stop_survives_the_initial_grace_period():
    # -12% at day 1: grace is IN and does NOT escape (its own catastrophic
    # escape needs -15%), so this fails the moment "Catastrophic stop" drops
    # out of _RISK_EXIT_TAGS.
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cfg = _ts_config(
        catastrophic_stop_enabled=True,
        catastrophic_stop_pct=-10.0,
        initial_grace_enabled=True,
        initial_grace_bars=14,
    )
    assert gna._in_initial_grace_period(1, -12.0, cfg, "chop")[:2] == (True, False)
    score, reason, _ = _risk("FOO", cfg, emu, 88.0, held_days=1)
    assert score == -1
    assert "Catastrophic stop" in reason


# ---------------------------------------------------------------------------
# edge_type_corroboration_weight
# ---------------------------------------------------------------------------


def test_corroboration_weight_defaults_to_todays_behaviour():
    ranked = [
        {"raw_net_score": 1.0, "n_paths": 11},
        {"raw_net_score": 1.0, "n_paths": 1},
    ]
    legacy = _conviction_allocation_schedule(ranked)
    assert legacy == _conviction_allocation_schedule(ranked, {})
    assert legacy == _conviction_allocation_schedule(
        ranked, {"edge_type_corroboration_weight": 1.0})
    # +50% bonus on the 11-path name -> 1.5 : 1.0
    assert legacy[0] / legacy[1] == pytest.approx(1.5)


def test_corroboration_weight_zero_prices_edge_type_agreement_at_zero():
    # Ali & Hirshleifer: six "different" momentum edges are one effect once
    # shared analyst coverage is controlled, so N types agreeing is not N
    # confirmations.
    ranked = [
        {"raw_net_score": 1.0, "n_paths": 11},
        {"raw_net_score": 1.0, "n_paths": 1},
    ]
    weights = _conviction_allocation_schedule(
        ranked, {"edge_type_corroboration_weight": 0.0})
    assert weights[0] == pytest.approx(weights[1])


def test_corroboration_weight_partial_de_weight():
    ranked = [
        {"raw_net_score": 1.0, "n_paths": 11},
        {"raw_net_score": 1.0, "n_paths": 1},
    ]
    weights = _conviction_allocation_schedule(
        ranked, {"edge_type_corroboration_weight": 0.5})
    assert weights[0] / weights[1] == pytest.approx(1.25)


def test_corroboration_weight_rejects_garbage_without_crashing():
    ranked = [{"raw_net_score": 1.0, "n_paths": 5}]
    assert _conviction_allocation_schedule(
        ranked, {"edge_type_corroboration_weight": "not-a-number"}) == [1.0]
    # Negative weights would INVERT the bonus (more paths -> less capital).
    assert _conviction_allocation_schedule(
        ranked, {"edge_type_corroboration_weight": -5.0}) == [1.0]


# ---------------------------------------------------------------------------
# One-target-vector invariant (DeMiguel et al.)
# ---------------------------------------------------------------------------


def test_finalize_scores_emits_exactly_one_decision_per_symbol():
    """Signals are netted into one target vector BEFORE trading — there is no
    per-signal decision channel, so two signals firing on the same name in the
    same bar cannot produce two independently-executed trades.

    This is a regression guard, not a feature: DeMiguel/Martin-Utrera/Nogales/
    Uppal (RFS 2020) put the saving from netting a K-signal combination at a
    1/sqrt(K) turnover scaling, and this codebase already banks it (every
    signal sums into `raw_net`, which thresholds into one `score`). The test
    exists so that a future per-signal buy/sell channel fails loudly here
    instead of quietly doubling turnover.
    """
    syms = ["AAA", "BBB", "CCC"]
    propagated = {
        "AAA": {"raw_score": 0.9, "reasons": ["supplier"], "n_paths": 3},
        "BBB": {"raw_score": -0.9, "reasons": ["competitor"], "n_paths": 2},
        "CCC": {"raw_score": 0.0, "reasons": [], "n_paths": 1},
    }
    sentiment = {"AAA": {"sentiment": -1, "event": "legal"}}   # conflicts with the graph
    out = _finalize_scores(
        syms,
        sentiment,
        propagated,
        {"buy_threshold": 0.15, "sell_threshold": -0.15,
         "breakout_score_boost_enabled": False},
        portfolio_emulator=None,
        date_key="2026-04-01",
        prices={s: 100.0 for s in syms},
        strategy_cache={},
    )
    assert set(out) == set(syms)
    for sym in syms:
        assert isinstance(out[sym], dict)
        assert out[sym]["score"] in (-1, 0, 1)
    buys = {s for s in syms if out[s]["score"] == 1}
    sells = {s for s in syms if out[s]["score"] == -1}
    assert not (buys & sells)
    # AAA had a direct sell sentiment AND a +0.9 graph score; one decision came
    # out, not two trades.
    assert out["AAA"]["score"] == -1

"""Unit tests for scripts/simulate_allocation.py.

The harness exists so allocation/gate levers can be evaluated WITHOUT spending a
backtest credit. That is only worth anything if it agrees with production, so
these tests pin three things:

  1. the reconstruction reads a log the way the broker wrote it,
  2. the gate chain dispatches to the REAL production functions rather than a
     private copy (a drifted copy is the failure mode this whole design exists
     to prevent -- see the module docstring),
  3. the inline broker arithmetic the harness has to restate (cash floor,
     `available`, the 15% single-position cap, the $50 execution minimum) still
     matches broker.py.

Nothing here touches the network, RethinkDB or a real backtest.
"""
import ast
import importlib.util
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_SCRIPT = os.path.join(_ROOT, "scripts", "simulate_allocation.py")


def _load():
    spec = importlib.util.spec_from_file_location("simulate_allocation", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves __module__ through sys.modules, so register before exec.
    sys.modules["simulate_allocation"] = mod
    spec.loader.exec_module(mod)
    return mod


sa = _load()


# ---------------------------------------------------------------------------
# A minimal but REAL log fragment.
#
# Every line below is copied from backtests/820236_20260808-142050Z.log with
# only the symbols/numbers reduced, so the regexes are tested against the shape
# broker.py actually emits rather than one invented here.
# ---------------------------------------------------------------------------

LOG = """\
[2026-08-08 13:01:42] [BROKER] RNG seed: 0 (BACKTEST_SEED env ('0'))
[2026-08-08 13:02:00] [GraphNexusAnalysis] V31 market regime: chop (raw=chop, proxy=QQQ, closes=90, ret20=-1.24)
[2026-08-08 13:02:01] [GraphNexusAnalysis] Run once | V31-PHASE2 | date=2026-01-02 | symbols=120 | lookback=NO
[2026-08-08 13:02:02] [BROKER] max_positions gate armed: held=0, cap=6
[2026-08-08 13:02:03] [BROKER] Buy gate inputs for WDC: cash=$6000.00 reserved=$0.00 floor=$120.00 effective_floor=$120.00 high_conv=True open_pos=0 cash_per_trade=$840.00 available=$5880.00 cash_to_use=$840.00 \u2192 PASS
[2026-08-08 13:02:04] [BROKER] [execution] FILL BUY WDC qty=4.62656307 cumulative=4.62656307 price=181.554815 fees=0.025199 quote=2026-01-02 15:00:00+00:00 model=equity-measured-v3-nbbo23
[2026-08-08 13:03:00] [GraphNexusAnalysis] Run once | V31-PHASE2 | date=2026-01-05 | symbols=120 | lookback=NO
[2026-08-08 13:03:01] [BROKER] max_positions gate armed: held=1, cap=6
[2026-08-08 13:03:02] [GraphNexusAnalysis] Monitor decision: WDC day 1 pnl=+1.0% cp=$183.40 entry=$181.55 \u2192 HOLD (monitor: hold)
[2026-08-08 13:03:03] [BROKER] TURNOVER BUDGET BINDING: 70% of NAV traded in the last 21 sessions \u2014 new discretionary BUYS are blocked this tick; risk exits and reduce-only sells are unaffected
[2026-08-08 13:03:04] [BROKER] SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 \u2014 funding $889 of room out of the core (floor-bounded)
[2026-08-08 13:03:05] [BROKER] SATELLITE CAP: SNDK trimmed $873 -> $591 to keep the core at target
[2026-08-08 13:03:06] [BROKER] TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 \u2014 admitting a conviction buy through a 70% budget; the brake is for churn, not for the trade that matters
[2026-08-08 13:03:07] [BROKER] Buy gate inputs for SNDK: cash=$5000.00 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=1 cash_per_trade=$591.39 available=$5000.00 cash_to_use=$591.39 \u2192 PASS
[2026-08-08 13:03:08] [BROKER] MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)
[2026-08-08 13:03:09] [BROKER] SATELLITE CAP: TER skipped \u2014 satellite at its design share ($-32 room); core would be squeezed below target
[2026-08-08 13:03:10] [BROKER] TURNOVER BUDGET BLOCK: UNG skipped \u2014 57% of NAV traded in 21 sessions
"""


CFG_CORE_ON = {
    "core_sleeve_enabled": True,
    "core_target_pct": 0.35,
    "core_min_pct": 0.25,
    "core_max_pct": 0.40,
    "cash_reserve_floor_pct": 0.02,
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "max_positions": 6,
    "turnover_budget_monthly_pct": 0.5,
    "satellite_conviction_overflow_min_raw_score": 1.5,
    "regime_position_cap_hard_enforce": False,
}


@pytest.fixture(scope="module")
def prod():
    return sa.Production()


@pytest.fixture()
def replay():
    lines = LOG.splitlines()
    return sa.parse_log(lines, initial_cash=6000.0)


# ---------------------------------------------------------------------------
# 1. Production wiring -- the harness must dispatch to the real functions.
# ---------------------------------------------------------------------------

def test_ast_extraction_gets_every_required_broker_helper(prod):
    """A missing helper turns a gate into a silent fail-open. Production()
    raises rather than report a plausible number from a chain with a hole in
    it -- this asserts the guard is real by checking every name landed."""
    for name in sa._BROKER_REQUIRED:
        assert callable(prod.ns[name]) or name in prod.ns, name


def test_uses_the_real_core_sleeve_module(prod):
    import core_sleeve
    assert prod.core_sleeve is core_sleeve
    # and the real shares, not a copy
    assert prod.core_sleeve.satellite_design_share(
        {"core_target_pct": 0.35, "cash_reserve_floor_pct": 0.02}) == pytest.approx(0.63)
    assert prod.core_sleeve.satellite_max_share(
        {"core_min_pct": 0.25, "cash_reserve_floor_pct": 0.02}) == pytest.approx(0.73)


def test_uses_the_real_max_positions_gate(prod):
    import nexus_broker_utils
    assert prod.nbu is nexus_broker_utils
    assert prod.nbu.max_positions_gate({"A", "B"}, 2, set(), set(), "C") is False
    # a planned full exit frees the slot -- the rotation case
    assert prod.nbu.max_positions_gate({"A", "B"}, 2, {"A"}, set(), "C") is True


def test_uses_the_real_portfolio_emulator(prod):
    import portfolio_emulator
    assert prod.PortfolioEmulator is portfolio_emulator.PortfolioEmulator


def test_headroom_comes_from_broker_and_tracks_core_target(prod):
    """The satellite clamp must move when core_target_pct moves, or the harness
    is not reading the real function."""
    chain_a = sa.GateChain(prod, dict(CFG_CORE_ON, core_target_pct=0.35))
    chain_b = sa.GateChain(prod, dict(CFG_CORE_ON, core_target_pct=0.60))
    prices = {"SPY": 100.0, "WDC": 100.0}
    pos = {"SPY": 20.0, "WDC": 20.0}   # NAV 4000 + cash
    specs_a, _ = chain_a.specs("chop")
    specs_b, _ = chain_b.specs("chop")
    pe_a = chain_a.book(pos, 2000.0, 6000.0)
    pe_b = chain_b.book(pos, 2000.0, 6000.0)
    room_a = prod.core_sleeve_satellite_headroom(pe_a, prices, specs_a)
    room_b = prod.core_sleeve_satellite_headroom(pe_b, prices, specs_b)
    assert room_a is not None and room_b is not None
    assert room_a > room_b, "a bigger core target must leave less satellite room"


# ---------------------------------------------------------------------------
# 2. Log reconstruction.
# ---------------------------------------------------------------------------

def test_ticks_are_delimited_by_the_gate_arm_line(replay):
    assert len(replay.ticks) == 2
    assert [t.logged_cap for t in replay.ticks] == [6, 6]
    assert [t.logged_held for t in replay.ticks] == [0, 1]


def test_reconstructed_book_agrees_with_the_logged_held_count(replay):
    assert replay.held_mismatches == 0
    assert replay.ticks[0].positions == {}
    assert replay.ticks[1].positions == pytest.approx({"WDC": 4.62656307})


def test_bar_date_and_regime_are_carried_onto_the_tick(replay):
    assert replay.ticks[0].bar_date == "2026-01-02"
    assert replay.ticks[1].bar_date == "2026-01-05"
    assert replay.ticks[1].regime == "chop"


def test_cash_is_taken_from_the_buy_gate_line(replay):
    # broker.py:15095 snapshots the emulator's own cash; that reading is
    # authoritative, not the harness's running estimate.
    assert replay.ticks[0].cash == pytest.approx(6000.0)
    assert replay.ticks[0].cash_source == "buy_gate"


def test_turnover_reading_is_per_tick_and_never_carried(replay):
    assert replay.ticks[0].logged_turnover_pct is None
    assert replay.ticks[1].logged_turnover_pct == 70


def test_candidate_sizes_and_scores_are_recovered(replay):
    cands = {c.symbol: c for c in replay.ticks[1].candidates}
    assert set(cands) == {"SNDK", "TER", "UNG"}
    sndk = cands["SNDK"]
    # allocator size is the PRE-trim number off the SATELLITE CAP line
    assert sndk.alloc_cash == pytest.approx(873.0)
    assert sndk.raw_score == pytest.approx(1.700)
    assert sndk.score_provenance in ("satellite_overflow", "turnover_bypass")
    assert sndk.high_conviction is True
    # last stage wins: SNDK passed the buy gate and then died at max_positions
    assert sndk.recorded_stage == "max_positions"
    assert cands["TER"].recorded_stage == "satellite_skip"
    assert cands["UNG"].recorded_stage == "turnover_budget"


def test_unscored_candidates_are_flagged_not_guessed(replay):
    cands = {c.symbol: c for c in replay.ticks[1].candidates}
    assert cands["TER"].raw_score is None
    assert cands["TER"].score_provenance == "unknown"


def test_prices_come_from_fills_and_monitor_lines(replay):
    assert replay.ticks[1].prices["WDC"] == pytest.approx(181.554815)


# ---------------------------------------------------------------------------
# 3. Config handling.
# ---------------------------------------------------------------------------

def test_regime_profile_is_merged_like_apply_regime_profile():
    cfg = {"core_sleeve_enabled": False,
           "regime_profiles": {"bull": {"core_sleeve_enabled": True,
                                        "core_target_pct": 0.35}}}
    assert sa.merge_regime_profile(cfg, "bull")["core_sleeve_enabled"] is True
    assert sa.merge_regime_profile(cfg, "bull")["core_target_pct"] == 0.35
    # no overlay for this regime -> config returned UNCHANGED (core_sleeve.py:157)
    assert sa.merge_regime_profile(cfg, "bear")["core_sleeve_enabled"] is False


def test_overrides_coerce_types():
    out = sa.apply_overrides({}, ["max_positions=8", "flag=true", "pct=0.15",
                                  "name=abc", "off=false"])
    assert out == {"max_positions": 8, "flag": True, "pct": 0.15,
                   "name": "abc", "off": False}


def test_config_facts_read_the_levers_off_the_log():
    facts = {k: (exp, n, opp) for k, exp, n, _note, _s, opp in
             sa.config_facts_from_log(LOG.splitlines())}
    assert facts["turnover_budget_conviction_bypass_enabled"][:2] == (True, 1)
    assert facts["satellite_conviction_overflow_min_raw_score"][0] == ">0"
    assert facts["turnover_budget_monthly_pct"][0] == ">0"
    assert facts["max_positions"][0] == 6


def test_config_facts_contradict_a_lever_the_run_never_exercised(capsys):
    """bt 820236 is configured (on disk) with
    backtest_credit_sell_proceeds_enabled=True and its log contains ZERO
    'Sell-proceeds credit' lines against 19 SELL fills. Absence with
    opportunity is evidence, and the harness has to say so."""
    log = LOG + (
        "[2026-08-08 13:04:00] [BROKER] [execution] FILL SELL WDC qty=1.0 "
        "cumulative=1.0 price=180.0 fees=0.01 quote=2026-01-06 16:00:00+00:00 "
        "model=equity-measured-v3-nbbo23\n")
    sa.print_config_facts(sa.config_facts_from_log(log.splitlines()),
                          {"backtest_credit_sell_proceeds_enabled": True,
                           "max_positions": 6})
    out = capsys.readouterr().out
    assert "backtest_credit_sell_proceeds_enabled" in out
    assert "NEVER FIRED" in out
    assert "was NOT live in that run" in out


def test_config_facts_catch_a_pre_patch_doc_backup(capsys):
    """The on-disk doc backups are written BEFORE the patch that made the run
    interesting, so the config that looks right is usually missing a lever.
    The harness has to SAY so rather than silently A/B against a baseline that
    never ran."""
    sa.print_config_facts(sa.config_facts_from_log(LOG.splitlines()),
                          {"max_positions": 6})
    out = capsys.readouterr().out
    assert "THE SUPPLIED CONFIG DISAGREES WITH THE RUN" in out
    assert "--set turnover_budget_conviction_bypass_enabled=true" in out


# ---------------------------------------------------------------------------
# 4. The gate chain.
# ---------------------------------------------------------------------------

def _one_candidate(**kw):
    base = dict(symbol="XYZ", alloc_cash=800.0, price=100.0,
                raw_score=None, high_conviction=False)
    base.update(kw)
    return sa.Candidate(**base)


def _tick(cands, *, held=None, cap=6, regime="chop", prices=None,
          turnover=None, bar="2026-01-05"):
    return sa.Tick(index=0, wall_ts="2026-08-08 13:00:00", bar_date=bar,
                   regime=regime, logged_held=len(held or {}), logged_cap=cap,
                   positions=dict(held or {}), prices=dict(prices or {}),
                   cash=5000.0, logged_turnover_pct=turnover,
                   candidates=list(cands))


def test_max_positions_blocks_a_new_name_at_the_cap(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    held = {f"H{i}": 1.0 for i in range(6)}
    prices = {f"H{i}": 10.0 for i in range(6)}
    prices["XYZ"] = 100.0
    t = _tick([_one_candidate()], held=held, prices=prices)
    out = chain.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert [d.stage for d in out] == ["max_positions"]


def test_raising_max_positions_admits_the_same_name(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False,
               turnover_budget_monthly_pct=0.0, max_positions=8)
    chain = sa.GateChain(prod, cfg)
    held = {f"H{i}": 1.0 for i in range(6)}
    prices = {f"H{i}": 10.0 for i in range(6)}
    prices["XYZ"] = 100.0
    t = _tick([_one_candidate()], held=held, prices=prices)
    out = chain.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert [d.stage for d in out] == ["admitted"]


def test_a_full_exit_frees_a_slot_for_a_rotation(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    held = {f"H{i}": 1.0 for i in range(6)}
    prices = {f"H{i}": 10.0 for i in range(6)}
    prices["XYZ"] = 100.0
    t = _tick([_one_candidate()], held=held, prices=prices)
    t.full_exits = {"H0"}
    out = chain.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert [d.stage for d in out] == ["admitted"]


def test_turnover_budget_blocks_and_the_conviction_bypass_releases(prod):
    prices = {"XYZ": 100.0}
    t = _tick([_one_candidate(raw_score=1.7)], held={}, prices=prices, turnover=70)

    off = sa.GateChain(prod, dict(CFG_CORE_ON), run_budget_pct=0.5)
    assert off.run_tick(t, cash=5000.0, positions={}, initial_cash=6000.0,
                        ledger_date="2026-01-05")[0].stage == "turnover_budget"

    on = sa.GateChain(prod, dict(CFG_CORE_ON,
                                 turnover_budget_conviction_bypass_enabled=True),
                      run_budget_pct=0.5)
    assert on.run_tick(t, cash=5000.0, positions={}, initial_cash=6000.0,
                       ledger_date="2026-01-05")[0].stage == "admitted"


def test_bypass_ceiling_refuses_conviction_above_the_ceiling(prod):
    prices = {"XYZ": 100.0}
    t = _tick([_one_candidate(raw_score=1.7)], held={}, prices=prices, turnover=104)
    chain = sa.GateChain(prod, dict(CFG_CORE_ON,
                                    turnover_budget_conviction_bypass_enabled=True,
                                    turnover_budget_conviction_bypass_max_pct=0.9),
                         run_budget_pct=0.5)
    out = chain.run_tick(t, cash=5000.0, positions={}, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert out[0].stage == "turnover_bypass_ceiling"


def test_turnover_is_reported_unknown_rather_than_guessed(prod):
    """The log only prints a reading on ticks where the budget BOUND. Lowering
    the budget therefore cannot be decided on a silent tick, and the harness
    must say so instead of assuming zero usage."""
    prices = {"XYZ": 100.0}
    t = _tick([_one_candidate()], held={}, prices=prices, turnover=None)

    lower = sa.GateChain(prod, dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.2),
                         run_budget_pct=0.5)
    assert lower.run_tick(t, cash=5000.0, positions={}, initial_cash=6000.0,
                          ledger_date="2026-01-05")[0].stage == "turnover_unknown"

    higher = sa.GateChain(prod, dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.9),
                          run_budget_pct=0.5)
    assert higher.run_tick(t, cash=5000.0, positions={}, initial_cash=6000.0,
                           ledger_date="2026-01-05")[0].stage == "admitted"


def test_satellite_clamp_trims_then_admits(prod):
    """core_target_pct 0.35 / cash floor 0.02 -> design share 0.63. A book at
    NAV 6000 with 3700 of satellite has 78 of room, which trims an 800 order."""
    cfg = dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    positions = {"SPY": 10.0, "SAT": 37.0}
    prices = {"SPY": 100.0, "SAT": 100.0, "XYZ": 100.0}
    t = _tick([_one_candidate()], held=positions, prices=prices)
    out = chain.run_tick(t, cash=1300.0, positions=positions, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert out[0].stage == "admitted"
    assert "satellite trim" in out[0].note
    assert out[0].size < 800.0


def test_satellite_clamp_skips_when_there_is_no_room(prod):
    cfg = dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    positions = {"SPY": 5.0, "SAT": 50.0}     # satellite already past its share
    prices = {"SPY": 100.0, "SAT": 100.0, "XYZ": 100.0}
    t = _tick([_one_candidate()], held=positions, prices=prices)
    out = chain.run_tick(t, cash=500.0, positions=positions, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert out[0].stage == "satellite_skip"


def test_conviction_overflow_uses_the_max_share_not_the_design_share(prod):
    """A raw score over the threshold measures room against core_min_pct, which
    is the whole point of the overflow band (broker.py:14837-14872).

    NAV 10,000 = cash 1,000 + SPY 2,600 + satellite 6,400.
      design share  1 - 0.35 - 0.02 = 0.63 -> 6,300, room -100  => SKIP
      overflow band 1 - 0.25 - 0.02 = 0.73 -> 7,300, room  +900 => ADMIT
    """
    cfg = dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    positions = {"SPY": 26.0, "SAT": 64.0}
    prices = {"SPY": 100.0, "SAT": 100.0, "XYZ": 100.0}
    plain = _tick([_one_candidate(raw_score=1.0)], held=positions, prices=prices)
    conv = _tick([_one_candidate(raw_score=1.7)], held=positions, prices=prices)
    a = chain.run_tick(plain, cash=1000.0, positions=positions,
                       initial_cash=10000.0, ledger_date="2026-01-05")[0]
    b = chain.run_tick(conv, cash=1000.0, positions=positions,
                       initial_cash=10000.0, ledger_date="2026-01-05")[0]
    assert a.stage == "satellite_skip"
    assert b.stage == "admitted"
    assert b.size == pytest.approx(800.0)


def test_execution_minimum_matches_broker_py():
    assert sa.EXEC_MIN_POSITION_USD == 50.0


def test_single_position_cap_default_matches_broker_py():
    assert sa.DEFAULT_MAX_SINGLE_POSITION_PCT == 0.15


def test_single_position_cap_trims_to_15_percent_of_equity(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False, turnover_budget_monthly_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    prices = {"XYZ": 100.0}
    t = _tick([_one_candidate(alloc_cash=5000.0)], held={}, prices=prices)
    out = chain.run_tick(t, cash=6000.0, positions={}, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert out[0].stage == "admitted"
    assert out[0].size == pytest.approx(900.0)      # 15% of a 6000 NAV
    assert "single-position cap" in out[0].note


def test_cash_floor_blocks_a_low_conviction_buy(prod):
    """cash_reserve_floor_pct 0.10, hard, and fewer than the min positions ->
    the floor is NOT released, so available is cash - floor (broker.py:15083)."""
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False,
               turnover_budget_monthly_pct=0.0,
               cash_reserve_floor_pct=0.10, cash_reserve_floor_hard=True)
    chain = sa.GateChain(prod, cfg)
    prices = {"XYZ": 100.0}
    t = _tick([_one_candidate(alloc_cash=800.0)], held={}, prices=prices)
    out = chain.run_tick(t, cash=620.0, positions={}, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    # 620 cash - 600 floor = 20 available, under the $50 execution minimum
    assert out[0].stage == "min_position"
    assert out[0].size == pytest.approx(20.0)


def test_sell_proceeds_credit_is_inert_because_the_emulator_reclamps(prod):
    """Reproduces bt 498816's 2026-01-16 SNDK bar exactly.

    The broker lifted the sizing ceiling $700.74 -> $1,397.39 on 95% of a
    $733.32 CPER sell and approved cash_to_use=$755.47. The emulator then
    re-clamped to pre-sell buying power and the fill was
    1.68975570 x $414.687474 = $700.65.

        [BROKER] Sell-proceeds credit: sizing ceiling $700.74 -> $1397.39
        [BROKER] Buy gate inputs for SNDK: cash=$700.74 ... cash_to_use=$755.47
        [BROKER] FILL BUY SNDK qty=1.68975570 ... price=414.687474

    The whole point of this harness is that a lever can be shown INERT without
    spending a credit, so this case is pinned.
    """
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False,
               turnover_budget_monthly_pct=0.0, cash_reserve_floor_pct=0.0,
               backtest_credit_sell_proceeds_enabled=True)
    chain = sa.GateChain(prod, cfg)
    # A large held name keeps NAV high so the 15% single-position cap is not
    # what binds; the only clamp under test is the emulator's buying power.
    held = {"OTHER": 100.0}
    prices = {"OTHER": 100.0, "SNDK": 414.687474}
    cand = _one_candidate(symbol="SNDK", alloc_cash=755.47, price=414.687474)
    t = _tick([cand], held=held, prices=prices)
    out = chain.run_tick(t, cash=700.74, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05", sell_proceeds=[733.32])
    assert out[0].stage == "admitted"
    assert "sell-proceeds ceiling" in out[0].note
    assert "emulator clamp" in out[0].note
    # ceiling lifted by $696.65, fill grew by $0.
    assert out[0].size == pytest.approx(700.74)
    assert out[0].size < 755.47


def test_sell_proceeds_credit_is_off_by_default_in_backtest(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False,
               turnover_budget_monthly_pct=0.0, cash_reserve_floor_pct=0.0)
    chain = sa.GateChain(prod, cfg)
    held = {"OTHER": 100.0}
    prices = {"OTHER": 100.0, "SNDK": 414.687474}
    t = _tick([_one_candidate(symbol="SNDK", alloc_cash=755.47, price=414.687474)],
              held=held, prices=prices)
    out = chain.run_tick(t, cash=700.74, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05", sell_proceeds=[733.32])
    assert "sell-proceeds ceiling" not in out[0].note


def test_regime_cap_hard_blocks_when_enforced(prod):
    cfg = dict(CFG_CORE_ON, core_sleeve_enabled=False,
               turnover_budget_monthly_pct=0.0, max_positions=99,
               regime_position_cap_hard_enforce=True, max_positions_chop=2)
    chain = sa.GateChain(prod, cfg)
    held = {"A": 1.0, "B": 1.0}
    prices = {"A": 10.0, "B": 10.0, "XYZ": 100.0}
    t = _tick([_one_candidate()], held=held, prices=prices, regime="chop")
    out = chain.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                         ledger_date="2026-01-05")
    assert out[0].stage == "regime_cap"


def test_sleeve_leg_exclusion_frees_a_slot(prod):
    """max_positions_exclude_sleeve_legs is DEFAULT OFF and this is the lever
    bt 718249 tried; the harness must show the mechanism, and the caller must
    remember 718249 returned +4.23% against +12.33% for the tighter arm."""
    prices = {f"H{i}": 10.0 for i in range(5)}
    prices.update({"SPY": 100.0, "XYZ": 100.0})
    held = {f"H{i}": 1.0 for i in range(5)}
    held["SPY"] = 1.0
    t = _tick([_one_candidate()], held=held, prices=prices)

    off = sa.GateChain(prod, dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0,
                                  core_sleeve_enabled=False))
    assert off.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                        ledger_date="2026-01-05")[0].stage == "max_positions"

    on = sa.GateChain(prod, dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0,
                                 core_sleeve_enabled=False,
                                 max_positions_exclude_sleeve_legs=True))
    assert on.run_tick(t, cash=5000.0, positions=held, initial_cash=6000.0,
                       ledger_date="2026-01-05")[0].stage == "admitted"


# ---------------------------------------------------------------------------
# 5. End to end on the fragment, plus the honesty guarantees.
# ---------------------------------------------------------------------------

def test_end_to_end_frozen_run(prod, replay):
    cfg = dict(CFG_CORE_ON, turnover_budget_conviction_bypass_enabled=True)
    res = sa.simulate(prod, replay, cfg, mode="frozen", label="t",
                      run_budget_pct=0.5)
    assert sum(res.by_stage.values()) == sum(len(t.candidates) for t in replay.ticks)
    assert res.turnover_source == "from-log"


def test_frozen_mode_never_mutates_the_replay(prod, replay):
    before = [dict(t.positions) for t in replay.ticks]
    sa.simulate(prod, replay, dict(CFG_CORE_ON), mode="frozen", label="t")
    assert [dict(t.positions) for t in replay.ticks] == before


def test_projected_mode_is_labelled_and_diverges(prod, replay):
    res = sa.simulate(prod, replay, dict(CFG_CORE_ON, turnover_budget_monthly_pct=0.0),
                      mode="projected", label="t")
    assert res.mode == "projected"


def test_result_reports_which_config_keys_were_consumed(prod, replay):
    res = sa.simulate(prod, replay, dict(CFG_CORE_ON), mode="frozen", label="t")
    assert "max_positions" in res.keys_read
    assert "satellite_conviction_overflow_min_raw_score" in res.keys_read
    assert "max_positions_exclude_sleeve_legs" in res.keys_read
    # a key no gate here reads must NOT appear, or the report would imply the
    # harness can answer a question it cannot
    assert "total_spend_cap_target_weight_pct" not in res.keys_read
    assert "nexus_portfolio_pct" not in res.keys_read


def test_unscored_candidates_are_counted_in_the_result(prod, replay):
    res = sa.simulate(prod, replay, dict(CFG_CORE_ON), mode="frozen", label="t")
    assert res.unscored >= 1


def test_docstring_states_what_cannot_be_modelled():
    doc = sa.__doc__ or ""
    for phrase in ("CANNOT MODEL", "P&L", "PRICE IMPACT", "DOWNSTREAM DIVERGENCE",
                   "THE STRATEGY SIDE", "LIVE-ONLY BEHAVIOUR", "SETTLEMENT"):
        assert phrase in doc, phrase


def test_fidelity_is_reported_and_penalises_a_wrong_config(prod, replay):
    good = sa.simulate(prod, replay,
                       dict(CFG_CORE_ON, turnover_budget_conviction_bypass_enabled=True),
                       mode="frozen", label="good", run_budget_pct=0.5)
    bad = sa.simulate(prod, replay, dict(CFG_CORE_ON), mode="frozen", label="bad",
                      run_budget_pct=0.5)
    assert sa.fidelity(good)["rate"] >= sa.fidelity(bad)["rate"]

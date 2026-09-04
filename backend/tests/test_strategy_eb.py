"""Pure sizing tests for Strategy EB.

Every series here is an EXACT alternating-return construction, so the realised
volatility, and therefore the target weight, is arithmetic rather than a
regression fixture: closes[i+1] = closes[i] * (1 +/- pct) alternating gives a
sample stdev over any EVEN window of exactly pct-ish, and the numbers below were
computed from that construction, not read off an implementation.
"""
import decimal
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_eb import (  # noqa: E402
    DEFAULTS,
    LAST_REBALANCE_KEY,
    LAST_STATE_KEY,
    _i,
    eb_core_weight,
    eb_remainder_targets,
    eb_should_trade,
    eb_state_book,
    eb_targets,
    eb_trend_state,
    rebalance_weekdays,
    session_ordinal,
    session_weekday,
    strategy_eb_universe,
)


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    value.update(overrides)
    return value


def alternating(pct, n=101, start=100.0):
    """n closes whose returns alternate exactly +pct, -pct, +pct, ..."""
    out = [start]
    for i in range(n - 1):
        out.append(out[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return out


# ── eb_core_weight: the vol transform ───────────────────────────────────────

def test_a_one_percent_tape_targets_forty_percent_of_a_3x_fund():
    """rv = 0.16287 annualised; 0.20 / (3.0 * 0.16287) = 0.40933, floored to
    the 0.05 grid = 0.40."""
    assert eb_core_weight(alternating(0.01), cfg()) == 0.40


def test_the_same_tape_targets_sixty_percent_of_a_2x_fund():
    """Same rv, leverage 2.0: 0.20 / (2.0 * 0.16287) = 0.61399 -> 0.60."""
    got = eb_core_weight(alternating(0.01), cfg(core_leverage=2.0))
    assert got == 0.60


def test_a_calm_tape_is_clamped_at_core_max_weight():
    """w_raw = 4.09; the clamp, not the grid, is what stops it."""
    assert eb_core_weight(alternating(0.001), cfg()) == 0.65


def test_a_violent_tape_floors_to_zero_rather_than_a_dust_position():
    """w_raw = 0.0409, below one grid step. Flooring means quantization only
    ever holds LESS."""
    assert eb_core_weight(alternating(0.10), cfg()) == 0.0


def test_quantization_always_floors_never_rounds():
    """0.0819 is nearer 0.10 than 0.05; it must still land on 0.05."""
    assert eb_core_weight(alternating(0.05), cfg()) == 0.05


def test_the_slow_window_governs_when_it_is_the_more_dangerous_one():
    """max(stdev20, stdev60), not the fast window alone: a calm last month
    inside a violent quarter must not re-lever. The tail CONTINUES from the
    violent series' last close — concatenating two series that both start at
    100 would inject one enormous seam return into both windows."""
    violent = alternating(0.05, n=81)
    closes = violent + alternating(0.001, n=21, start=violent[-1])[1:]
    calm_only = eb_core_weight(alternating(0.001), cfg())
    assert eb_core_weight(closes, cfg()) < calm_only


def test_too_little_history_fails_closed():
    """A cold start must never silently lever up."""
    assert eb_core_weight(alternating(0.01, n=69), cfg()) is None


def test_exactly_min_history_bars_is_enough():
    assert eb_core_weight(alternating(0.01, n=70), cfg()) is not None


def test_a_nonfinite_close_fails_closed():
    closes = alternating(0.01)
    closes[-3] = float("nan")
    assert eb_core_weight(closes, cfg()) is None


def test_a_flat_tape_has_no_measurable_risk_and_fails_closed():
    """rv == 0 would divide by zero and ask for infinite leverage."""
    assert eb_core_weight([100.0] * 101, cfg()) is None


def test_an_infinite_target_vol_falls_back_to_the_default_rather_than_levering():
    """`_f` returns the default for a non-finite value rather than letting inf
    reach the divide, which is the only safe direction for a levered position.
    An infinite target vol asks for infinite exposure."""
    assert eb_core_weight(alternating(0.01), cfg(target_vol=float("inf"))) == 0.40


def test_a_vol_window_longer_than_the_history_floor_fails_closed():
    """The floor must cover the WINDOW, not just `min_history_bars`. With
    vol_slow_bars=250 and only 100 closes the slow window would silently
    truncate to 99 returns; a shorter window measures LESS risk, and less
    measured risk sizes a 3x position LARGER."""
    assert eb_core_weight(alternating(0.01, n=100),
                          cfg(vol_slow_bars=250)) is None


def test_a_vol_window_exactly_covered_by_the_history_is_enough():
    """The boundary of the same rule: 100 closes = 99 returns, so a 99-bar
    slow window is the longest one this history can honestly measure."""
    assert eb_core_weight(alternating(0.01, n=100),
                          cfg(vol_slow_bars=99)) is not None
    assert eb_core_weight(alternating(0.01, n=100),
                          cfg(vol_slow_bars=100)) is None


def test_an_absurd_vol_window_fails_closed_rather_than_measuring_nothing():
    """A config value large enough to be nonsense must refuse, never re-lever."""
    assert eb_core_weight(alternating(0.01), cfg(vol_slow_bars=10 ** 400)) is None


# ── the integer parser fails CLOSED ─────────────────────────────────────────

def test_the_int_parser_returns_the_default_for_a_non_finite_float():
    """strategy_x's `_i` raises OverflowError here: `int(inf)` is not caught by
    its (TypeError, ValueError, AttributeError). EB's owns the branch."""
    for junk in (float("inf"), float("-inf"), float("nan")):
        assert _i(cfg(vol_slow_bars=junk), "vol_slow_bars") == 60, junk


def test_the_int_parser_returns_the_default_when_the_conversion_overflows():
    """A Decimal infinity reaches `int()` and raises OverflowError there —
    the branch a plain Python int can never trigger, since ints are unbounded."""
    value = decimal.Decimal("Infinity")
    assert _i(cfg(vol_slow_bars=value), "vol_slow_bars") == 60


def test_the_int_parser_returns_the_default_for_unusable_values():
    for junk in (None, "", "sixty", object()):
        assert _i(cfg(vol_slow_bars=junk), "vol_slow_bars") == 60, junk


def test_the_int_parser_resolves_a_missing_default_against_eb_defaults():
    """strategy_x's `_i` resolves against ITS DEFAULTS, so an EB-only key with
    no explicit default raises TypeError there."""
    assert _i({}, "min_history_bars") == 70


def test_a_zero_leverage_config_fails_closed_instead_of_dividing_by_zero():
    assert eb_core_weight(alternating(0.01), cfg(core_leverage=0)) is None


# ── eb_targets: the remainder dial ──────────────────────────────────────────

def test_the_default_remainder_is_all_spy():
    assert eb_targets(0.40, cfg()) == {"TQQQ": 0.40, "SPY": 0.60}


def test_the_dial_at_one_puts_the_whole_remainder_in_bills():
    assert eb_targets(0.40, cfg(remainder_bil_fraction=1.0)) == {
        "TQQQ": 0.40, "BIL": 0.60}


def test_the_dial_at_a_half_splits_the_remainder():
    got = eb_targets(0.40, cfg(remainder_bil_fraction=0.5))
    assert got == {"TQQQ": 0.40, "SPY": 0.30, "BIL": 0.30}


def test_every_target_set_sums_to_exactly_one():
    for dial in (0.0, 0.25, 0.5, 1.0):
        for w in (0.0, 0.05, 0.4, 0.65):
            got = eb_targets(w, cfg(remainder_bil_fraction=dial))
            assert round(sum(got.values()), 6) == 1.0, (w, dial)


def test_a_zero_core_weight_emits_no_core_leg_at_all():
    got = eb_targets(0.0, cfg())
    assert "TQQQ" not in got
    assert got == {"SPY": 1.0}


def test_colliding_symbols_accumulate_rather_than_overwrite():
    """off_symbol == cash_symbol must not silently drop half the book."""
    got = eb_targets(0.40, cfg(off_symbol="BIL", remainder_bil_fraction=0.5))
    assert got == {"TQQQ": 0.40, "BIL": 0.60}


# ── eb_remainder_targets: the cash sweep's plan ─────────────────────────────
#
# The remainder legs around the core the book ALREADY holds. `targets_to_orders`
# sizes buys off SETTLED cash, so the remainder leg of a rebalance that also
# sold the core is dropped on the trade tick — and on every later session the
# band sees no breach, so the freed cash is never deployed and the book drifts
# to cash. These targets are what the wrapper re-offers that cash to.

def test_the_remainder_plan_excludes_the_core_it_is_built_around():
    got = eb_remainder_targets(0.40, cfg())
    assert "TQQQ" not in got
    assert got == {"SPY": 0.60}


def test_the_remainder_plan_follows_the_dial():
    assert eb_remainder_targets(0.40, cfg(remainder_bil_fraction=1.0)) == {
        "BIL": 0.60}
    assert eb_remainder_targets(0.40, cfg(remainder_bil_fraction=0.5)) == {
        "SPY": 0.30, "BIL": 0.30}


def test_the_remainder_plan_of_a_flat_book_is_the_whole_book():
    """After a full exit `w_held` is 0, and the entire account is remainder."""
    assert eb_remainder_targets(0.0, cfg()) == {"SPY": 1.0}


def test_the_remainder_plan_of_a_fully_invested_core_asks_for_nothing():
    assert eb_remainder_targets(1.0, cfg()) == {}


def test_the_remainder_plan_never_sums_past_the_uninvested_share():
    """Asking for more than 1 - w_held would fund the buy by selling the core,
    which is exactly what the sweep must never do."""
    for dial in (0.0, 0.25, 0.5, 1.0):
        for held in (0.0, 0.05, 0.4, 0.65):
            got = eb_remainder_targets(held, cfg(remainder_bil_fraction=dial))
            assert round(sum(got.values()), 6) == round(1.0 - held, 6), (
                held, dial)


# ── strategy_eb_universe ────────────────────────────────────────────────────

def test_the_universe_is_reference_core_off_and_cash_in_order():
    assert strategy_eb_universe(cfg()) == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_the_universe_deduplicates():
    assert strategy_eb_universe(cfg(off_symbol="QQQ")) == ["QQQ", "TQQQ", "BIL"]


def test_the_universe_is_freshly_allocated_so_callers_cannot_mutate_defaults():
    first = strategy_eb_universe(cfg())
    first.append("JUNK")
    assert "JUNK" not in strategy_eb_universe(cfg())


# ── session clock ───────────────────────────────────────────────────────────

def test_the_session_ordinal_is_days_since_1970_not_the_proleptic_ordinal():
    """A raw ordinal is ~739,000 and reads as corruption to anything that
    bounds a parsed counter at 100,000."""
    assert session_ordinal("1970-01-01") == 0
    assert session_ordinal("2026-06-03") == 20607


def test_an_unusable_session_label_is_zero_not_an_exception():
    for junk in (None, "", "not-a-date", 20607, "2026-13-45"):
        assert session_ordinal(junk) == 0, junk


def test_the_weekday_comes_from_the_session_date():
    assert session_weekday("2026-06-01") == 0   # Monday
    assert session_weekday("2026-06-03") == 2   # Wednesday
    assert session_weekday("2026-06-05") == 4   # Friday
    assert session_weekday("junk") == -1


def test_rebalance_weekdays_parses_and_bounds():
    assert rebalance_weekdays(cfg()) == (2,)
    assert rebalance_weekdays(cfg(rebalance_weekdays=[1, 3])) == (1, 3)
    assert rebalance_weekdays(cfg(rebalance_weekdays=[3, 1, 3])) == (1, 3)
    for junk in (None, [], "wed", [9], [-1], [None]):
        assert rebalance_weekdays(cfg(rebalance_weekdays=junk)) == (2,), junk


# ── eb_should_trade: cadence, band, tranches, unconditional exit ────────────

WED = "2026-06-03"
THU = "2026-06-04"
MON = "2026-06-01"


def test_it_trades_on_the_configured_weekday_when_the_band_is_breached():
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), {}) == (True, 0.40)


def test_it_does_not_trade_on_any_other_weekday():
    assert eb_should_trade(THU, 0.40, 0.00, cfg(), {}) == (False, 0.00)
    assert eb_should_trade(MON, 0.40, 0.00, cfg(), {}) == (False, 0.00)


def test_a_drift_inside_the_band_does_not_trade_even_on_the_decision_day():
    assert eb_should_trade(WED, 0.40, 0.35, cfg(), {}) == (False, 0.35)


def test_a_drift_exactly_at_the_band_does_trade():
    """`>=`, per the spec: |w - w_held| >= core_rebalance_band."""
    traded, target = eb_should_trade(WED, 0.45, 0.35, cfg(), {})
    assert traded is True and target == 0.45


def test_an_exit_to_zero_is_unconditional_and_ignores_the_weekday():
    """The band is meaningless around a target of zero, and waiting until
    Wednesday to leave a 3x fund is the failure this exists to prevent."""
    assert eb_should_trade(THU, 0.0, 0.30, cfg(), {}) == (True, 0.0)


def test_an_exit_to_zero_from_flat_is_not_an_order():
    assert eb_should_trade(THU, 0.0, 0.0, cfg(), {}) == (False, 0.0)


def test_an_exit_to_zero_ignores_the_same_session_guard_too():
    """"Unconditional" includes the once-per-session rule. A weight that has
    already traded today and then reads zero must still leave the 3x fund;
    it re-arms harmlessly, because once the exit fills `w_held` is 0."""
    cache = {LAST_REBALANCE_KEY: WED}
    assert eb_should_trade(WED, 0.0, 0.30, cfg(), cache) == (True, 0.0)
    assert eb_should_trade(WED, 0.0, 0.0, cfg(), cache) == (False, 0.0)


def test_it_refuses_a_second_trade_in_the_same_session():
    """The engine calls run_once on every tick; at 15m granularity that is ~26
    evaluations per session."""
    cache = {LAST_REBALANCE_KEY: WED}
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), cache) == (False, 0.00)


def test_the_same_session_guard_does_not_leak_into_the_next_session():
    cache = {LAST_REBALANCE_KEY: "2026-05-27"}
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), cache) == (True, 0.40)


def test_two_tranches_move_half_the_way_on_each_listed_weekday():
    """1/N tranching removes rebalance-timing luck, which is >100 bp/yr."""
    two = cfg(rebalance_weekdays=[1, 3])
    assert eb_should_trade("2026-06-02", 0.60, 0.20, two, {}) == (True, 0.40)
    assert eb_should_trade("2026-06-04", 0.60, 0.40, two, {}) == (True, 0.50)


def test_a_tranche_still_exits_the_whole_position_at_a_zero_target():
    two = cfg(rebalance_weekdays=[1, 3])
    assert eb_should_trade("2026-06-02", 0.0, 0.40, two, {}) == (True, 0.0)


def test_an_unusable_session_label_never_trades():
    assert eb_should_trade("junk", 0.40, 0.00, cfg(), {}) == (False, 0.00)


# ── eb_trend_state: the two-state trend machine ─────────────────────────────
#
# N=4 everywhere below so the SMA is arithmetic you can check by eye:
# [100, 100, 100, c] has mean (300 + c) / 4.

TREND = {"trend_filter_bars": 4}


def test_the_trend_filter_is_off_by_default_and_every_state_read_is_on():
    """`trend_filter_bars = 0` IS the feature switch. With it off the machine
    must not merely start ON — it must be incapable of leaving, or a stale
    persisted OFF from an earlier config would rotate a default book to
    cash."""
    assert eb_trend_state([100.0, 100.0, 100.0, 1.0], "OFF", cfg()) == "ON"


def test_an_on_state_survives_a_close_inside_the_enter_band():
    """sma 99.75, threshold 99.75 * 0.99 = 98.7525. A close of 99 is BELOW the
    average and still risk-on: the band is what stops a weekly whipsaw."""
    assert eb_trend_state([100.0, 100.0, 100.0, 99.0], "ON",
                          cfg(**TREND)) == "ON"


def test_an_on_state_flips_off_below_the_enter_threshold():
    """sma 99.5, threshold 98.505; 98 clears it downward."""
    assert eb_trend_state([100.0, 100.0, 100.0, 98.0], "ON",
                          cfg(**TREND)) == "OFF"


def test_an_off_state_survives_a_close_back_above_the_average():
    """The hysteresis is ASYMMETRIC on purpose: sma 100.5, threshold 102.51.
    A close of 102 is above the average, above the OFF trigger, and still
    OFF."""
    assert eb_trend_state([100.0, 100.0, 100.0, 102.0], "OFF",
                          cfg(**TREND)) == "OFF"


def test_an_off_state_flips_on_above_the_exit_threshold():
    """sma 100.75, threshold 102.765; 103 clears it."""
    assert eb_trend_state([100.0, 100.0, 100.0, 103.0], "OFF",
                          cfg(**TREND)) == "ON"


def test_the_two_thresholds_leave_a_dead_zone_neither_edge_can_cross():
    """Between sma*(1-enter) and sma*(1+exit) NOTHING moves, from either side.
    That gap is the whole point: 2011 whipsawed 10 times through a 1%/2% band
    and the replay warns a narrower one is worse."""
    closes = [100.0, 100.0, 100.0, 100.0]
    assert eb_trend_state(closes, "ON", cfg(**TREND)) == "ON"
    assert eb_trend_state(closes, "OFF", cfg(**TREND)) == "OFF"


def test_a_short_history_holds_the_previous_state_rather_than_guessing():
    """Fail CLOSED means fail UNCHANGED here: inventing a state on a cold start
    is what makes a restart trade, and this machine rotates the whole
    remainder."""
    for prev in ("ON", "OFF"):
        assert eb_trend_state([100.0, 100.0, 100.0], prev,
                              cfg(**TREND)) == prev, prev


def test_a_non_finite_close_holds_the_previous_state():
    assert eb_trend_state([100.0, float("nan"), 100.0, 98.0], "ON",
                          cfg(**TREND)) == "ON"
    assert eb_trend_state([100.0, 0.0, 100.0, 103.0], "OFF",
                          cfg(**TREND)) == "OFF"


def test_the_initial_state_is_on():
    """No persisted state yet. The book starts risk-ON and the first decision
    day is the first chance to leave."""
    assert eb_trend_state([100.0, 100.0, 100.0, 100.0], None,
                          cfg(**TREND)) == "ON"
    assert eb_trend_state([100.0, 100.0, 100.0, 98.0], None,
                          cfg(**TREND)) == "OFF"


def test_an_unusable_persisted_state_reads_as_on():
    """A corrupted cache value must resolve to the SAME state the initial one
    does, not to a third behaviour."""
    assert eb_trend_state([100.0, 100.0, 100.0, 98.0], "banana",
                          cfg(**TREND)) == "OFF"
    assert eb_trend_state([100.0, 100.0, 100.0, 102.0], "banana",
                          cfg(**TREND)) == "ON"


def test_the_average_uses_the_last_n_closes_and_no_more():
    """A longer history must not drag the average: only the trailing window is
    the trend."""
    old = [1.0] * 50
    assert eb_trend_state(old + [100.0, 100.0, 100.0, 98.0], "ON",
                          cfg(**TREND)) == "OFF"


def test_an_absurd_window_holds_the_previous_state():
    assert eb_trend_state([100.0] * 200, "OFF",
                          cfg(trend_filter_bars=10 ** 400)) == "OFF"


def test_a_zero_enter_threshold_flips_on_the_average_itself():
    """x = 0 is a grid point in the replay: the trigger is then a bare
    close < sma."""
    zero = cfg(**TREND, trend_off_enter_pct=0.0)
    assert eb_trend_state([100.0, 100.0, 100.0, 99.9], "ON", zero) == "OFF"
    assert eb_trend_state([100.0, 100.0, 100.0, 100.0], "ON", zero) == "ON"


# ── eb_targets: the trend-conditioned occupant ──────────────────────────────

def test_the_default_state_keeps_the_signature_change_invisible():
    assert eb_targets(0.40, cfg()) == eb_targets(0.40, cfg(), "ON")


def test_the_off_state_moves_the_whole_remainder_to_the_risk_off_symbol():
    got = eb_targets(0.40, cfg(**TREND, risk_off_symbol="GLD"), "OFF")
    assert got == {"TQQQ": 0.40, "GLD": 0.60}


def test_the_on_state_keeps_the_spy_remainder():
    got = eb_targets(0.40, cfg(**TREND, risk_off_symbol="GLD"), "ON")
    assert got == {"TQQQ": 0.40, "SPY": 0.60}


def test_an_unset_risk_off_symbol_falls_back_to_the_cash_leg():
    """`risk_off_symbol = ""` with the filter on is the BIL variant of the
    replay grid, not a misconfiguration."""
    assert eb_targets(0.40, cfg(**TREND), "OFF") == {"TQQQ": 0.40, "BIL": 0.60}


def test_the_risk_off_book_still_sums_to_exactly_one():
    for w in (0.0, 0.05, 0.4, 0.65):
        got = eb_targets(w, cfg(**TREND, risk_off_symbol="GLD"), "OFF")
        assert round(sum(got.values()), 6) == 1.0, w


def test_a_risk_off_book_with_a_zero_core_is_all_gold():
    got = eb_targets(0.0, cfg(**TREND, risk_off_symbol="GLD"), "OFF")
    assert got == {"GLD": 1.0}


def test_the_bil_dial_is_untouched_while_the_state_is_on():
    """The dial and the occupant switch are independent levers; the replay
    turns the dial off, but enabling the filter must not silently disable
    it."""
    got = eb_targets(0.40, cfg(**TREND, risk_off_symbol="GLD",
                               remainder_bil_fraction=0.5), "ON")
    assert got == {"TQQQ": 0.40, "SPY": 0.30, "BIL": 0.30}


def test_the_remainder_plan_follows_the_trend_state_too():
    """Otherwise the cash sweep buys back the very leg the flip just sold."""
    got = eb_remainder_targets(0.40, cfg(**TREND, risk_off_symbol="GLD"),
                               "OFF")
    assert got == {"GLD": 0.60}
    assert eb_remainder_targets(0.40, cfg(**TREND, risk_off_symbol="GLD"),
                                "ON") == {"SPY": 0.60}


# ── core_off_damp: less core, not just a different remainder ────────────────

def test_the_off_damp_is_applied_before_quantisation():
    """0.40933 * 0.9 = 0.36840, floored onto the 0.05 grid = 0.35. Damping the
    QUANTISED 0.40 instead gives 0.36 — off the grid entirely, which is the
    turnover control this strategy is built on."""
    assert eb_core_weight(alternating(0.01),
                          cfg(**TREND, core_off_damp=0.9), "OFF") == 0.35


def test_the_off_damp_is_applied_before_the_clamp_as_the_replay_does():
    """eb2.py: w = clip(tv / (k*rv) * damp, 0, cap). On a tape calm enough for
    the clamp to bind, w_raw is 4.09 and half of it still clamps to 0.65.
    Damping a clamped 0.65 would hold 0.30 — a different strategy from the one
    whose numbers were measured."""
    assert eb_core_weight(alternating(0.001),
                          cfg(**TREND, core_off_damp=0.5), "OFF") == 0.65


def test_a_half_damp_halves_an_unclamped_core():
    assert eb_core_weight(alternating(0.01),
                          cfg(**TREND, core_off_damp=0.5), "OFF") == 0.20


def test_a_zero_damp_leaves_the_core_entirely():
    assert eb_core_weight(alternating(0.01),
                          cfg(**TREND, core_off_damp=0.0), "OFF") == 0.0


def test_the_damp_never_applies_while_the_state_is_on():
    assert eb_core_weight(alternating(0.01),
                          cfg(**TREND, core_off_damp=0.0), "ON") == 0.40
    assert eb_core_weight(alternating(0.01), cfg(core_off_damp=0.0)) == 0.40


def test_a_damp_above_one_cannot_lever_up_the_state_it_de_risks():
    """The one direction this key must never move: a config error that sized
    the core LARGER while risk-off would invert the whole feature."""
    assert eb_core_weight(alternating(0.01),
                          cfg(**TREND, core_off_damp=3.0), "OFF") == 0.40


def test_an_unusable_damp_falls_back_to_no_damping():
    for junk in (None, "", "half", float("nan")):
        assert eb_core_weight(alternating(0.01),
                              cfg(**TREND, core_off_damp=junk),
                              "OFF") == 0.40, junk


# ── the universe carries the risk-off leg ───────────────────────────────────

def test_the_universe_includes_the_risk_off_leg_when_the_filter_is_on():
    """`broker._strategy_eb_universe_symbols` reads this to decide what bars to
    fetch and what to price. A leg missing from it has no price, and
    `targets_to_orders` skips it silently."""
    assert strategy_eb_universe(cfg(**TREND, risk_off_symbol="GLD")) == [
        "QQQ", "TQQQ", "SPY", "BIL", "GLD"]


def test_the_universe_omits_the_risk_off_leg_while_the_filter_is_off():
    """Declaring GLD with the feature off makes the broker fetch bars for a leg
    that can never be bought."""
    assert strategy_eb_universe(cfg(risk_off_symbol="GLD")) == [
        "QQQ", "TQQQ", "SPY", "BIL"]


def test_an_unset_risk_off_symbol_adds_nothing_to_the_universe():
    assert strategy_eb_universe(cfg(**TREND)) == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_the_universe_still_deduplicates_the_risk_off_leg():
    assert strategy_eb_universe(cfg(**TREND, risk_off_symbol="BIL")) == [
        "QQQ", "TQQQ", "SPY", "BIL"]


# ── the flip forces a rebalance the band cannot see ─────────────────────────

def test_a_state_flip_trades_even_when_the_band_is_not_breached():
    """THE clause that makes the feature work. The band is on the CORE weight;
    a flip changes the REMAINDER, which the band cannot see. Without this the
    occupant rotation is skipped on every decision day whose core drift is
    inside 0.10 — which is most of them, by design."""
    cache = {LAST_STATE_KEY: "ON"}
    assert eb_should_trade(WED, 0.40, 0.35, cfg(**TREND), cache,
                           "OFF") == (True, 0.40)


def test_no_flip_leaves_the_band_in_charge():
    cache = {LAST_STATE_KEY: "ON"}
    assert eb_should_trade(WED, 0.40, 0.35, cfg(**TREND), cache,
                           "ON") == (False, 0.35)


def test_a_flip_with_nothing_recorded_to_rotate_away_from_does_not_trade():
    """First decision ever: `_strategy_eb_last_state` is absent, and there is
    no executed book to rotate."""
    assert eb_should_trade(WED, 0.40, 0.35, cfg(**TREND), {},
                           "OFF") == (False, 0.35)


def test_a_rotation_with_no_core_drift_at_all_still_trades():
    assert eb_should_trade(WED, 0.40, 0.40, cfg(**TREND),
                           {LAST_STATE_KEY: "ON"}, "OFF") == (True, 0.40)


def test_a_rotation_still_obeys_the_weekday_cadence():
    """The replay evaluates the machine on decision days ONLY."""
    assert eb_should_trade(THU, 0.40, 0.35, cfg(**TREND),
                           {LAST_STATE_KEY: "ON"}, "OFF") == (False, 0.35)


def test_a_rotation_still_obeys_the_one_decision_per_session_rule():
    cache = {LAST_REBALANCE_KEY: WED, LAST_STATE_KEY: "ON"}
    assert eb_should_trade(WED, 0.40, 0.35, cfg(**TREND), cache,
                           "OFF") == (False, 0.35)


def test_the_default_state_argument_never_rotates():
    """Every existing caller passes four arguments; with the feature off the
    wrapper never writes the key, so the clause is inert."""
    assert eb_should_trade(WED, 0.40, 0.35, cfg(), {}) == (False, 0.35)
    assert eb_should_trade(WED, 0.40, 0.35, cfg(),
                           {LAST_STATE_KEY: "ON"}) == (False, 0.35)


# ── the remainder BOOKS: many occupants instead of one ──────────────────────
#
# A book is {SYMBOL: weight}. The remainder (1 - core) is split across it by
# weight; whatever the weights leave unspent goes to the SINGLE occupant the
# state would have used on its own, which is what makes an empty book — the
# default — the code path that existed before this feature.

ON_BOOK = {"trend_on_book": {"SMH": 0.5, "GLD": 0.5}}
OFF_BOOK = {"trend_off_book": {"GDX": 0.5, "XLE": 0.5}}


def test_both_books_are_empty_by_default():
    assert eb_state_book(cfg(), "ON") == {}
    assert eb_state_book(cfg(), "OFF") == {}
    assert DEFAULTS["trend_on_book"] == {}
    assert DEFAULTS["trend_off_book"] == {}


def test_a_book_uppercases_its_symbols_and_accumulates_duplicates():
    got = eb_state_book(cfg(trend_on_book={"smh": 0.2, "SMH": 0.1}), "ON")
    assert got == {"SMH": 0.3}


def test_a_book_drops_empty_symbols_and_non_positive_weights():
    """A zero or negative weight is not a short leg — this book is long-only
    and the weights are shares of a remainder that is already >= 0."""
    got = eb_state_book(cfg(trend_on_book={"SMH": 0.5, "": 0.2, "GLD": 0.0,
                                           "XLE": -0.3, "IWM": "junk"}), "ON")
    assert got == {"SMH": 0.5}


def test_a_book_summing_past_one_is_renormalised_not_clipped():
    """Clipping would silently drop whichever leg came last. Renormalising
    keeps every leg's SHARE of the remainder, which is what the operator
    wrote."""
    got = eb_state_book(cfg(trend_on_book={"A": 1.0, "B": 3.0}), "ON")
    assert got == {"A": 0.25, "B": 0.75}


def test_a_book_that_is_not_a_dict_is_no_book_at_all():
    for junk in (None, "", [], "SMH", 3):
        assert eb_state_book(cfg(trend_on_book=junk), "ON") == {}, junk


def test_each_state_reads_its_own_book():
    both = cfg(**ON_BOOK, **OFF_BOOK)
    assert eb_state_book(both, "ON") == {"SMH": 0.5, "GLD": 0.5}
    assert eb_state_book(both, "OFF") == {"GDX": 0.5, "XLE": 0.5}


def test_the_on_book_splits_the_whole_remainder_by_weight():
    got = eb_targets(0.40, cfg(**ON_BOOK))
    assert got == {"TQQQ": 0.40, "SMH": 0.30, "GLD": 0.30}


def test_a_book_that_does_not_spend_the_remainder_leaves_the_rest_in_spy():
    """The shortfall is not cash by accident: it goes to the SAME occupant the
    state would have used with no book at all."""
    got = eb_targets(0.40, cfg(trend_on_book={"SMH": 0.5}))
    assert got == {"TQQQ": 0.40, "SMH": 0.30, "SPY": 0.30}


def test_the_shortfall_still_follows_the_bil_dial():
    """The dial and the book are independent levers. A book that spends half
    the remainder must not silently disable the dial on the other half."""
    got = eb_targets(0.40, cfg(trend_on_book={"SMH": 0.5},
                               remainder_bil_fraction=1.0))
    assert got == {"TQQQ": 0.40, "SMH": 0.30, "BIL": 0.30}


def test_a_renormalised_book_leaves_no_shortfall_at_all():
    got = eb_targets(0.40, cfg(trend_on_book={"A": 1.0, "B": 3.0}))
    assert got == {"TQQQ": 0.40, "A": 0.15, "B": 0.45}
    assert "SPY" not in got


def test_the_off_book_splits_the_remainder_while_risk_off():
    got = eb_targets(0.40, cfg(**TREND, risk_off_symbol="GLD", **OFF_BOOK),
                     "OFF")
    assert got == {"TQQQ": 0.40, "GDX": 0.30, "XLE": 0.30}


def test_an_off_book_shortfall_falls_back_to_the_risk_off_leg():
    got = eb_targets(0.40, cfg(**TREND, risk_off_symbol="GLD",
                               trend_off_book={"GDX": 0.5}), "OFF")
    assert got == {"TQQQ": 0.40, "GDX": 0.30, "GLD": 0.30}


def test_an_off_book_shortfall_with_no_risk_off_leg_falls_back_to_cash():
    got = eb_targets(0.40, cfg(**TREND, trend_off_book={"GDX": 0.5}), "OFF")
    assert got == {"TQQQ": 0.40, "GDX": 0.30, "BIL": 0.30}


def test_each_state_uses_only_its_own_book():
    both = cfg(**TREND, **ON_BOOK, **OFF_BOOK, risk_off_symbol="GLD")
    assert eb_targets(0.40, both, "ON") == {"TQQQ": 0.40, "SMH": 0.30,
                                            "GLD": 0.30}
    assert eb_targets(0.40, both, "OFF") == {"TQQQ": 0.40, "GDX": 0.30,
                                             "XLE": 0.30}


def test_the_on_book_applies_with_the_trend_filter_switched_off():
    """The static blend: no state machine at all, the book IS the remainder.
    `trend_filter_bars = 0` pins the state ON, so the ON book is the only one
    that can ever be read."""
    got = eb_targets(0.40, cfg(trend_filter_bars=0, **ON_BOOK, **OFF_BOOK))
    assert got == {"TQQQ": 0.40, "SMH": 0.30, "GLD": 0.30}


def test_every_book_target_set_sums_to_exactly_one():
    books = ({"SMH": 0.5, "GLD": 0.5}, {"SMH": 0.33, "GLD": 0.33, "A": 0.33},
             {"A": 1.0}, {"A": 0.1}, {"A": 2.0, "B": 1.0}, {"A": 1 / 3})
    for book in books:
        for w in (0.0, 0.05, 0.35, 0.40, 0.65, 1.0):
            got = eb_targets(w, cfg(trend_on_book=book))
            assert round(sum(got.values()), 6) == 1.0, (book, w)


def test_the_remainder_plan_carries_the_book_of_the_current_state():
    """Otherwise the sweep deploys idle cash into the single occupant the book
    was configured to replace."""
    got = eb_remainder_targets(0.40, cfg(**TREND, **OFF_BOOK), "OFF")
    assert got == {"GDX": 0.30, "XLE": 0.30}
    assert eb_remainder_targets(0.40, cfg(**TREND, **ON_BOOK, **OFF_BOOK),
                                "ON") == {"SMH": 0.30, "GLD": 0.30}


def test_a_book_leg_colliding_with_a_fallback_leg_accumulates():
    """SPY in the book AND as the shortfall occupant is one position, not a
    duplicated key that overwrites half the weight."""
    got = eb_targets(0.40, cfg(trend_on_book={"SPY": 0.5}))
    assert got == {"TQQQ": 0.40, "SPY": 0.60}


# ── the universe carries the book legs ──────────────────────────────────────

def test_the_universe_appends_the_book_legs_after_the_named_ones():
    got = strategy_eb_universe(cfg(**TREND, risk_off_symbol="GLD",
                                   trend_on_book={"SMH": 0.6, "IWM": 0.4},
                                   **OFF_BOOK))
    assert got == ["QQQ", "TQQQ", "SPY", "BIL", "GLD", "SMH", "IWM",
                   "GDX", "XLE"]


def test_the_universe_omits_the_off_book_while_the_filter_is_off():
    """Same reason the risk-off leg is omitted: with the filter off the state
    is always ON, so an OFF-book leg can never be bought and the broker would
    fetch bars and carry a price for nothing."""
    assert strategy_eb_universe(cfg(**ON_BOOK, **OFF_BOOK)) == [
        "QQQ", "TQQQ", "SPY", "BIL", "SMH", "GLD"]


def test_the_universe_deduplicates_book_legs():
    assert strategy_eb_universe(cfg(trend_on_book={"SPY": 0.5,
                                                   "BIL": 0.5})) == [
        "QQQ", "TQQQ", "SPY", "BIL"]


def test_the_universe_is_unchanged_by_empty_books():
    assert strategy_eb_universe(cfg()) == ["QQQ", "TQQQ", "SPY", "BIL"]


# ── a PURE BOOK: core weight 0, the books carry the whole NAV ───────────────
#
# Two configs express it, and BOTH resolve to a legitimate zero rather than to
# a refusal: `target_vol = 0` makes w_raw itself 0, and `core_max_weight = 0`
# clamps whatever w_raw is to 0. Neither is the None that means "cannot
# measure my own risk" — that is reserved for a short history, a NaN close and
# a flat tape, which still refuse with a pure book.

def test_a_zero_vol_target_sizes_the_core_at_zero_rather_than_refusing():
    assert eb_core_weight(alternating(0.01), cfg(target_vol=0.0)) == 0.0


def test_a_zero_core_cap_sizes_the_core_at_zero_rather_than_refusing():
    assert eb_core_weight(alternating(0.001), cfg(core_max_weight=0.0)) == 0.0


def test_a_pure_book_still_refuses_when_it_cannot_measure_the_tape():
    """The zero core is a CONFIGURED zero. An unmeasurable tape must still
    return None, or a pure-book run would trade straight through an outage."""
    pure = cfg(target_vol=0.0, **ON_BOOK)
    assert eb_core_weight([100.0] * 5, pure) is None
    assert eb_core_weight([100.0] * 101, pure) is None


def test_a_pure_book_target_is_the_book_itself():
    got = eb_targets(0.0, cfg(target_vol=0.0,
                              trend_on_book={"SMH": 0.3, "GLD": 0.7}))
    assert got == {"SMH": 0.30, "GLD": 0.70}


def test_a_pure_book_trades_on_its_decision_weekday_from_a_flat_account():
    """With no core weight there is nothing for the band to measure, so the
    weekday alone decides and `targets_to_orders`'s own per-leg band is what
    suppresses churn. Without this clause the exit-to-zero rule answers
    (False, 0.0) forever and a pure book never opens a position."""
    pure = cfg(target_vol=0.0, **ON_BOOK)
    assert eb_should_trade(WED, 0.0, 0.0, pure, {}) == (True, 0.0)


def test_a_pure_book_still_obeys_the_weekday_cadence():
    pure = cfg(target_vol=0.0, **ON_BOOK)
    assert eb_should_trade(THU, 0.0, 0.0, pure, {}) == (False, 0.0)
    assert eb_should_trade(MON, 0.0, 0.0, pure, {}) == (False, 0.0)


def test_a_pure_book_still_decides_once_per_session():
    pure = cfg(target_vol=0.0, **ON_BOOK)
    assert eb_should_trade(WED, 0.0, 0.0, pure,
                           {LAST_REBALANCE_KEY: WED}) == (False, 0.0)


def test_a_pure_book_holding_a_core_exits_it_unconditionally():
    """Rule 2 is untouched: a core to leave is left on any session, book or
    no book."""
    pure = cfg(target_vol=0.0, **ON_BOOK)
    assert eb_should_trade(THU, 0.0, 0.40, pure, {}) == (True, 0.0)


def test_a_flat_account_with_no_book_still_never_trades_at_a_zero_target():
    """The byte-identity clause: without a book a zero target from flat is
    exactly what it has always been — nothing to do, on every weekday."""
    for session in (WED, THU, MON):
        assert eb_should_trade(session, 0.0, 0.0, cfg(), {}) == (False, 0.0)


def test_a_pure_book_rotates_when_the_state_flips():
    pure = cfg(**TREND, target_vol=0.0, **ON_BOOK, **OFF_BOOK)
    assert eb_should_trade(WED, 0.0, 0.0, pure,
                           {LAST_STATE_KEY: "ON"}, "OFF") == (True, 0.0)


# ── the differential: with no books configured, NOTHING moved ───────────────

def test_the_book_free_book_is_the_pre_feature_formula_exactly():
    """Recomputed from the docstring formula rather than from the
    implementation: core = w, bil = (1-w)*dial, spy = 1-core-bil, and the whole
    remainder to the occupant while OFF."""
    for w in (0.0, 0.05, 0.2, 0.35, 0.4, 0.65, 1.0):
        for dial in (0.0, 0.25, 0.5, 1.0):
            base = cfg(remainder_bil_fraction=dial)
            expected = {}
            bil = round((1.0 - w) * dial, 6)
            spy = round(1.0 - w - bil, 6)
            for symbol, weight in (("TQQQ", w), ("BIL", bil), ("SPY", spy)):
                if weight > 0:
                    expected[symbol] = round(expected.get(symbol, 0.0)
                                             + weight, 6)
            assert eb_targets(w, base) == expected, (w, dial)
            assert eb_targets(w, base, "ON") == expected, (w, dial)

            off = cfg(**TREND, risk_off_symbol="GLD",
                      remainder_bil_fraction=dial)
            expected_off = {}
            if w > 0:
                expected_off["TQQQ"] = round(w, 6)
            if round(1.0 - w, 6) > 0:
                expected_off["GLD"] = round(1.0 - w, 6)
            assert eb_targets(w, off, "OFF") == expected_off, (w, dial)


def test_a_pure_book_named_on_one_state_only_still_rotates_off_it():
    """A book on the ON state alone must still be SELLABLE when the state
    flips: the rotation is decided in the OFF state, where that book is empty,
    and the whole NAV then belongs to the risk-off occupant."""
    pure = cfg(**TREND, target_vol=0.0, risk_off_symbol="GLD", **ON_BOOK)
    assert eb_should_trade(WED, 0.0, 0.0, pure, {LAST_STATE_KEY: "ON"},
                           "OFF") == (True, 0.0)
    assert eb_targets(0.0, pure, "OFF") == {"GLD": 1.0}


def test_an_off_book_the_filter_can_never_reach_is_not_a_book_at_all():
    """With the filter off the OFF book is unreachable, so a config that sets
    only that one must behave exactly as it did before these keys existed —
    including at a zero core, where the pre-feature answer is "nothing to
    do"."""
    unreachable = cfg(target_vol=0.0, trend_off_book={"GDX": 1.0})
    assert eb_should_trade(WED, 0.0, 0.0, unreachable, {}) == (False, 0.0)
    assert strategy_eb_universe(unreachable) == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_a_parsed_book_is_freshly_allocated_so_defaults_cannot_be_mutated():
    """`{**DEFAULTS, **config}` shares the default dict by reference, so a
    caller that mutated a returned book would edit every future run's
    default."""
    book = eb_state_book(cfg(**ON_BOOK), "ON")
    book["JUNK"] = 9.0
    assert eb_state_book(cfg(**ON_BOOK), "ON") == {"SMH": 0.5, "GLD": 0.5}
    empty = eb_state_book(cfg(), "ON")
    empty["JUNK"] = 9.0
    assert DEFAULTS["trend_on_book"] == {}
    assert DEFAULTS["trend_off_book"] == {}


# ── VIX term-structure re-entry (2026-09-04, pre-registered) ────────────────

def test_vts_off_leaves_the_price_rule_bit_for_bit():
    from strategy_eb import eb_trend_state, vts_enabled
    c = cfg(trend_filter_bars=4)
    falling = [100.0, 100.0, 100.0, 100.0, 90.0]
    assert vts_enabled(c) is False
    assert eb_trend_state(falling, "ON", c) == "OFF"
    assert eb_trend_state(falling, "ON", c, vts_ratio=0.5) == "OFF"   # ignored when off


def test_vts_keeps_the_book_on_unless_the_vol_curve_is_inverted_too():
    from strategy_eb import eb_trend_state
    c = cfg(trend_filter_bars=4, vts_enabled=True, vts_threshold=1.0)
    falling = [100.0, 100.0, 100.0, 100.0, 90.0]
    assert eb_trend_state(falling, "ON", c, vts_ratio=0.95) == "ON"     # price OFF, curve normal
    assert eb_trend_state(falling, "ON", c, vts_ratio=1.20) == "OFF"    # price OFF, curve inverted
    assert eb_trend_state(falling, "ON", c, vts_ratio=None) == "OFF"    # unmeasurable -> price rule
    rising = [100.0, 100.0, 100.0, 100.0, 110.0]
    assert eb_trend_state(rising, "OFF", c, vts_ratio=1.50) == "ON"     # price ON wins regardless


def test_vts_ratio_norm_is_the_ratio_over_its_trailing_median():
    from strategy_eb import vts_ratio_norm
    c = cfg(vts_median_bars=5)
    short = [10.0] * 9 + [15.0]          # last day the short leg spikes 50%
    mid = [20.0] * 10
    assert abs(vts_ratio_norm(short, mid, c) - 1.5) < 1e-9
    assert vts_ratio_norm([10.0], [20.0], c) is None                    # fewer than 2 sessions
    assert vts_ratio_norm([10.0, float("nan")], [20.0, 20.0], c) is None


def test_vts_data_symbols_join_the_universe_only_when_on():
    from strategy_eb import strategy_eb_universe
    base = strategy_eb_universe(cfg(trend_filter_bars=25))
    assert "VIXY" not in base and "VIXM" not in base
    on = strategy_eb_universe(cfg(trend_filter_bars=25, vts_enabled=True))
    assert on[:len(base)] == base and on[len(base):] == ["VIXY", "VIXM"]


def test_a_vts_flip_may_trade_off_cadence_but_a_plain_off_day_may_not():
    from strategy_eb import eb_should_trade, LAST_STATE_KEY
    c = cfg(trend_filter_bars=25, vts_enabled=True, rebalance_weekdays=[2])
    thursday = "2026-06-04"      # weekday 3, not a decision day
    cache = {LAST_STATE_KEY: "OFF"}
    assert eb_should_trade(thursday, 0.40, 0.0, c, cache, "ON")[0] is True   # flip OFF->ON
    assert eb_should_trade(thursday, 0.40, 0.0, c, {LAST_STATE_KEY: "ON"}, "ON")[0] is False
    plain = cfg(trend_filter_bars=25, rebalance_weekdays=[2])
    assert eb_should_trade(thursday, 0.40, 0.0, plain, cache, "ON")[0] is False  # VTS off: cadence rules

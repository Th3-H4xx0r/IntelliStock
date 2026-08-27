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
    _i,
    eb_core_weight,
    eb_should_trade,
    eb_targets,
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

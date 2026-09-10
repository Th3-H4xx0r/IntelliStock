"""Pure tests for Strategy HX: the state machine, the books, the universe."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402


def cfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def test_the_default_universe_is_the_four_declared_legs():
    assert strategy_hx_universe(DEFAULTS) == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_every_bear_book_leg_is_declared():
    """A leg the broker never fetches has no bars and no price, and
    `targets_to_orders` silently skips it — the bear book would simply not
    exist. V5 is the widest preregistered book."""
    syms = strategy_hx_universe(
        cfg(bear_book={"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40}))
    assert syms == ["BIL", "GLD", "PSQ", "QQQ", "TQQQ"]


def test_a_malformed_bear_book_still_declares_the_fixed_legs():
    for junk in (None, [], "PSQ", 7):
        assert strategy_hx_universe(cfg(bear_book=junk)) == [
            "BIL", "QQQ", "TQQQ"], junk


def test_the_defaults_carry_the_working_single_position_cap():
    """`max_single_position_pct` is in broker.py's `_DEAD_STRATEGY_CONFIG_KEYS`
    — nothing reads it. The key the backtest engine actually reads is
    `broker_max_single_position_pct`, gated on `honour_single_position_cap`.
    Without it every 65% TQQQ buy is trimmed to $0.00 under the 15% failsafe
    (BT102936) and the whole battery measures an inert strategy."""
    assert DEFAULTS["broker_max_single_position_pct"] == 0.95
    assert DEFAULTS["honour_single_position_cap"] is True


def test_the_parsers_fail_toward_less_exposure():
    from strategy_hx import _f, _i, _s
    for bad in (None, "", "wide", float("nan"), float("inf")):
        assert _f(cfg(core_max_weight=bad), "core_max_weight") == 0.65, bad
        assert _i(cfg(sma_bars=bad), "sma_bars") == 50, bad
    assert _s({"core_symbol": " tqqq "}, "core_symbol") == "TQQQ"
    assert _s({"core_symbol": None}, "core_symbol") == "TQQQ"


def test_the_parsers_pass_a_configured_value_through():
    """The fallbacks above are all any test would see if `_f`/`_i` simply
    returned `DEFAULTS[key]`. A configured value has to survive the parser, or
    every knob on the strategy is decorative."""
    from strategy_hx import _f, _i
    assert _f(cfg(core_max_weight=0.30), "core_max_weight") == 0.30
    assert _i(cfg(sma_bars=20), "sma_bars") == 20


from strategy_hx import hx_state  # noqa: E402


def rising(n, start=100.0, rate=0.001):
    """A clean 0.1%/day uptrend. Its realised volatility is exactly zero, so
    it is a STATE fixture only — `eb_core_weight` refuses on it by design."""
    return [start * ((1.0 + rate) ** i) for i in range(n)]


def crash(rise=110, days=10, pct=0.09):
    """110 rising sessions, then a 9% fall over 10 sessions."""
    closes = rising(rise)
    top = closes[-1]
    for i in range(1, days + 1):
        closes.append(top * (1.0 - pct * i / days))
    return closes


def test_a_clean_uptrend_is_bull():
    assert hx_state(rising(120), "BULL", 0, cfg()) == ("BULL", 0)


def test_a_nine_percent_ten_day_fall_fires_bear():
    """The fast leg. A 7% fall from a 20-session high, reached within 10
    sessions of that high, is what no weekly filter can react to in time."""
    assert hx_state(crash(), "BULL", 0, cfg()) == ("BEAR", 0)


def test_a_flat_tape_is_chop_not_bull():
    """A flat series never closes BELOW its own average, so the slope clause
    is what has to catch it. Without that clause a dead tape would hold the
    full 3x core."""
    assert hx_state([100.0] * 120, "BULL", 0, cfg()) == ("CHOP", 0)


def test_short_history_is_unknown_and_never_guesses():
    assert hx_state(rising(30), "BULL", 0, cfg()) == ("UNKNOWN", 0)
    assert hx_state(rising(69), "BULL", 0, cfg()) == ("UNKNOWN", 0)


def test_a_nan_close_reads_as_unknown_not_as_no_signal():
    series = rising(120)
    series[-4] = float("nan")
    assert hx_state(series, "BULL", 0, cfg()) == ("UNKNOWN", 0)


def test_four_sessions_above_the_average_do_not_leave_bear_and_five_do():
    """The slow leg, and the whole reason HX is not just a fast filter: a
    four-session bounce inside a bear must not put a 3x fund back on."""
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for _ in range(4):
        closes.append(top * 1.10)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert (state, confirm) == ("BEAR", 4)
    closes.append(top * 1.10)
    assert hx_state(closes, state, confirm, cfg()) == ("BULL", 0)


def test_one_close_below_the_average_resets_the_exit_counter():
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for px in [top * 1.10] * 4 + [top * 0.5]:
        closes.append(px)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert (state, confirm) == ("BEAR", 0)


def test_the_drawdown_trigger_does_not_refire_on_stale_history():
    """After an exit the last 10 sessions still contain the crash lows. A
    trigger that scanned them would slam straight back into BEAR one session
    after confirming its way out, and the 5-session hysteresis would buy
    nothing at all."""
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for _ in range(6):
        closes.append(top * 1.10)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert state == "BULL"


def test_a_corrupted_previous_state_reads_as_unknown_not_as_bull():
    """A damaged cache row must not be a third behaviour. Anything
    unrecognised takes the entry path, which can only ever DE-risk."""
    for junk in (None, "", "ON", 7, "bearish"):
        state, _ = hx_state(crash(), junk, 0, cfg())
        assert state == "BEAR", junk


def test_a_non_positive_confirm_length_does_not_shorten_the_bear_exit():
    """The parsers deliberately do not clamp, so a corrupted
    `exit_confirm_sessions` of 0 arrives intact. Floored with `max(1, n)` it
    reads as a ONE-session exit — malformed config putting a 3x fund back on
    four sessions early, the one direction this module may never fail in. A
    non-positive count is not a shorter wait, it is a MISSING one, and it
    reads as the documented default."""
    for bad in (0, -1, -5):
        closes = crash()
        top = max(closes)
        state, confirm = "BEAR", 0
        for _ in range(4):
            closes.append(top * 1.10)
            state, confirm = hx_state(
                closes, state, confirm, cfg(exit_confirm_sessions=bad))
        assert (state, confirm) == ("BEAR", 4), bad


def test_a_non_positive_bar_count_does_not_shrink_the_unknown_floor():
    """Same trap on the window lengths. Floored with `max(2, n)` a corrupted
    `sma_bars` drags the history floor it feeds down with it, and a shorter
    window on the same tape always measures LESS risk — here 69 sessions
    would clear a floor that is meant to be 70."""
    for bad in (0, -5):
        assert hx_state(rising(69), "BULL", 0,
                        cfg(sma_bars=bad, min_history_bars=0)) == (
                            "UNKNOWN", 0), bad

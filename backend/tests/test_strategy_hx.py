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

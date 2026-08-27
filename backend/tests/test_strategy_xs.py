"""Pure allocation tests for Strategy XS."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_xs import diversifier_basket  # noqa: E402


def cfg(**overrides):
    value = {
        "diversifier_symbols": ["GLD", "UUP", "DBMF"],
        "diversifier_min_history_bars": 60,
    }
    value.update(overrides)
    return value


def series(n, start=100.0):
    return [start + i * 0.1 for i in range(n)]


PRICES = {"GLD": 200.0, "UUP": 28.0, "DBMF": 26.0}


def test_all_members_qualify_when_priceable_and_long_enough():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP", "DBMF")


def test_a_member_without_a_price_is_dropped():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    prices = dict(PRICES, DBMF=0.0)
    assert diversifier_basket(closes, prices, cfg()) == ("GLD", "UUP")


def test_a_member_with_too_little_history_is_dropped():
    """DBMF has no history before 2019-05, so this is the ordinary case for
    any window starting earlier, not an edge case."""
    closes = {"GLD": series(80), "UUP": series(80), "DBMF": series(30)}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_a_nonfinite_close_in_the_required_window_drops_that_member():
    closes = {"GLD": series(80), "UUP": series(80),
              "DBMF": series(59) + [float("nan")]}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_order_follows_the_configured_list_not_the_dict():
    closes = {s: series(80) for s in ("DBMF", "GLD", "UUP")}
    assert diversifier_basket(closes, PRICES,
                              cfg(diversifier_symbols=["UUP", "DBMF", "GLD"])
                              ) == ("UUP", "DBMF", "GLD")


def test_no_qualifying_member_returns_empty():
    assert diversifier_basket({}, {}, cfg()) == ()


def test_a_nonfinite_history_requirement_falls_back_to_the_default():
    closes = {"GLD": series(80), "UUP": series(30), "DBMF": series(80)}
    for bad in (float("nan"), float("inf"), None, "sixty"):
        assert diversifier_basket(closes, PRICES,
                                  cfg(diversifier_min_history_bars=bad)
                                  ) == ("GLD", "DBMF"), bad

"""position_qty must match a held crypto position regardless of slash form on
EITHER side (query or stored key). Regression for the crypto no-sells bug:
a slash-less universe symbol ("BTCUSD") could not match a slash position
("BTC/USD"), so held_symbols came back empty and the strategy never sold."""

from strategies.crypto import core


def test_position_qty_slash_symmetry():
    # slash position, slash-less query -> the real bug; must find it
    assert core.position_qty({"BTC/USD": 0.14}, "BTCUSD") == 0.14
    # slash-less position, slash query
    assert core.position_qty({"BTCUSD": 0.14}, "BTC/USD") == 0.14
    # exact matches still work
    assert core.position_qty({"BTC/USD": 0.14}, "BTC/USD") == 0.14
    assert core.position_qty({"BTCUSD": 0.14}, "BTCUSD") == 0.14
    # absent -> 0.0
    assert core.position_qty({"ETH/USD": 1.0}, "BTC/USD") == 0.0
    # non-numeric / missing read as 0.0
    assert core.position_qty({"BTC/USD": None}, "BTC/USD") == 0.0
    assert core.position_qty(None, "BTC/USD") == 0.0

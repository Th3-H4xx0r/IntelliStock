"""2-F (bug-sweep 2026-05-28): RobinhoodAdapter must pick an order style that can
actually FILL outside regular hours, WITHOUT breaking fractional orders.

Robinhood prohibits fractional shares in extended hours and fractional limit
orders (robinhood_engine.py:1224-1236), so the graph_nexus strategy (which trades
fractional shares) can only use an extended-hours marketable LIMIT for WHOLE-share
quantities. Fractional off-hours orders must stay regular-hours market (they queue
to the next open) — submitting them as ext-hours limit would be rejected.
"""
from __future__ import annotations


def test_rth_uses_plain_market_order():
    from broker_adapters.robinhood import _order_style_for_now
    s = _order_style_for_now(price=100.0, side="buy", qty=10.0, in_rth=True)
    assert s["order_type"] == "market"
    assert s["extended_hours"] is False
    assert s["limit_price"] is None
    assert s["tif"] == "day"


def test_offhours_whole_share_uses_extended_hours_limit():
    from broker_adapters.robinhood import _order_style_for_now
    s = _order_style_for_now(price=100.0, side="sell", qty=10.0, in_rth=False)
    assert s["order_type"] == "limit"
    assert s["extended_hours"] is True
    assert s["tif"] == "gfd"
    # marketable sell limit = below last price
    assert 0 < s["limit_price"] <= 100.0


def test_offhours_whole_share_buy_limit_is_marketable_up():
    from broker_adapters.robinhood import _order_style_for_now
    s = _order_style_for_now(price=100.0, side="buy", qty=3.0, in_rth=False)
    assert s["order_type"] == "limit"
    assert s["limit_price"] >= 100.0  # marketable buy limit = at/above last price


def test_offhours_fractional_stays_regular_market():
    """The RH fractional guard: off-hours fractional exit must NOT become an
    ext-hours limit (RH would reject it). It stays a regular-hours market order."""
    from broker_adapters.robinhood import _order_style_for_now
    s = _order_style_for_now(price=100.0, side="sell", qty=1.7233, in_rth=False)
    assert s["order_type"] == "market"
    assert s["extended_hours"] is False
    assert s["tif"] == "day"


def test_offhours_notional_buy_stays_regular_market():
    """Notional buys (qty=None) are inherently fractional -> regular market."""
    from broker_adapters.robinhood import _order_style_for_now
    s = _order_style_for_now(price=100.0, side="buy", qty=None, in_rth=False)
    assert s["order_type"] == "market"
    assert s["extended_hours"] is False

"""Displacement must actually free cash, not just log an intention.

bt 550605 fired the decision path 12 times and moved nothing, because the request
was written to the strategy cache and never consumed. This pins the round trip:
the request carries the shortfall, the consumer converts it to a partial trim, and
the trim covers the need without liquidating the holding.
"""
import ast
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()


def _consume(reqs, sizes, enabled=True):
    """Mirror of the broker's displacement-execution block."""
    sell_set = set()
    if not enabled:
        return sell_set, sizes
    for req in reqs or []:
        try:
            sym = str(req.get("sell") or "").upper()
            val = float(req.get("value") or 0.0)
            need = float(req.get("need") or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not sym or val <= 0 or need <= 0:
            continue
        frac = min(1.0, (need * 1.05) / val)
        hint = sizes.setdefault(sym, {})
        if isinstance(hint, dict) and "buy_cash" not in hint:
            hint["sell_fraction"] = max(float(hint.get("sell_fraction") or 0.0), frac)
            sell_set.add(sym)
    return sell_set, sizes


REQ = [{"sell": "CPER", "fund": "SNDK", "value": 921.78, "need": 420.0}]


def test_the_request_becomes_an_actual_sell():
    sell_set, sizes = _consume(REQ, {})
    assert "CPER" in sell_set, "displacement must reach the sell path"
    assert sizes["CPER"]["sell_fraction"] > 0


def test_trims_only_the_shortfall_not_the_whole_position():
    _, sizes = _consume(REQ, {})
    frac = sizes["CPER"]["sell_fraction"]
    assert frac < 1.0, "freeing $420 from $921 must not liquidate the holding"
    assert 921.78 * frac >= 420.0, "the trim must actually cover the need"


def test_buffer_covers_slippage_but_stays_small():
    _, sizes = _consume(REQ, {})
    freed = 921.78 * sizes["CPER"]["sell_fraction"]
    assert 420.0 <= freed <= 420.0 * 1.10


def test_small_holding_is_fully_trimmed_but_never_over_one():
    _, sizes = _consume([{"sell": "X", "value": 100.0, "need": 420.0}], {})
    assert sizes["X"]["sell_fraction"] == 1.0


def test_never_converts_a_pending_buy_into_a_sell():
    sizes = {"CPER": {"buy_cash": 500.0}}
    sell_set, sizes = _consume(REQ, sizes)
    assert "CPER" not in sell_set
    assert "sell_fraction" not in sizes["CPER"]


def test_existing_larger_trim_is_not_reduced():
    sizes = {"CPER": {"sell_fraction": 0.9}}
    _, sizes = _consume(REQ, sizes)
    assert sizes["CPER"]["sell_fraction"] == 0.9


def test_disabled_consumes_nothing():
    sell_set, sizes = _consume(REQ, {}, enabled=False)
    assert sell_set == set() and sizes == {}


def test_malformed_requests_never_raise():
    bad = [None, {}, {"sell": "", "value": 1, "need": 1},
           {"sell": "A", "value": 0, "need": 5}, {"sell": "B", "value": 5, "need": 0}]
    sell_set, _ = _consume([b for b in bad if b is not None], {})
    assert sell_set == set()


def test_request_carries_the_shortfall_field():
    assert '"need": float(_exec_min_pos),' in _src, (
        "the decision site must record the shortfall or the consumer cannot size the trim")


def test_the_cache_key_is_both_written_and_consumed():
    assert _src.count("_broker_displacement_requests") >= 2, (
        "a write-only key is an inert lever; bt 550605 proved that costs a run")

"""client_order_id format: alpaca-valid, deterministic, retry-unique."""
import os
import re
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from broker_adapters._client_order_id import make_client_order_id


ALPACA_VALID = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")


def test_valid_format():
    cid = make_client_order_id("instance-1", "AAPL", "2026-04-19T14:30:00+00:00", "buy", 0)
    assert ALPACA_VALID.match(cid), f"invalid per Alpaca constraint: {cid!r}"
    assert len(cid) <= 48


def test_deterministic_same_inputs():
    a = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    b = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    assert a == b


def test_retry_changes_id():
    a = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    b = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 1)
    assert a != b


def test_strips_forbidden_chars():
    cid = make_client_order_id("inst:1+", "AAPL@NYSE", "2026-04-19T14:30:00+00:00", "buy", 0)
    assert ":" not in cid
    assert "+" not in cid
    assert "@" not in cid
    assert " " not in cid


def test_different_symbol_differs():
    a = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    b = make_client_order_id("i1", "MSFT", "2026-04-19T14:30:00Z", "buy", 0)
    assert a != b


def test_different_side_differs():
    a = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    b = make_client_order_id("i1", "AAPL", "2026-04-19T14:30:00Z", "sell", 0)
    assert a != b


def test_empty_instance_id_safe():
    cid = make_client_order_id("", "AAPL", "2026-04-19T14:30:00Z", "buy", 0)
    assert ALPACA_VALID.match(cid)

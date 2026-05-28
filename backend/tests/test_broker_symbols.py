"""2-D: canonical broker-symbol normalization (dot->dash for RH share classes)."""
from __future__ import annotations


def test_normalize_share_class_dot_to_dash():
    from broker_adapters._symbols import normalize_broker_symbol
    assert normalize_broker_symbol("BRK.B") == "BRK-B"
    assert normalize_broker_symbol("bf.b") == "BF-B"


def test_normalize_plain_ticker_unchanged():
    from broker_adapters._symbols import normalize_broker_symbol
    assert normalize_broker_symbol("AAPL") == "AAPL"
    assert normalize_broker_symbol("  tsla  ") == "TSLA"


def test_normalize_handles_empty_and_none():
    from broker_adapters._symbols import normalize_broker_symbol
    assert normalize_broker_symbol("") == ""
    assert normalize_broker_symbol(None) == ""

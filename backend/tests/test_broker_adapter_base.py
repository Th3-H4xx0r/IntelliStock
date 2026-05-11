"""Contract tests for BrokerAdapter ABC + typed errors."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest

from broker_adapters.base import BrokerAdapter
from broker_adapters.errors import (
    BrokerError, BrokerPreflightBlocked, InsufficientBuyingPower,
    PDTRestricted, AssetHalted, AssetNotTradable, WashSale,
    FractionalNotAllowed, BrokerRateLimited, BrokerMFARequired,
    NON_RETRYABLE,
)


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()  # type: ignore[abstract]


def test_abstract_methods_cover_required_surface():
    abstract = BrokerAdapter.__abstractmethods__
    required = {
        "submit_order", "cancel_order", "get_order", "get_order_by_client_id",
        "list_open_orders", "refresh_positions", "refresh_cash",
        "refresh_account", "is_market_open", "health_check",
        "buy", "sell", "execute_signal", "get_positions",
        "get_positions_value", "get_portfolio_value", "get_trade_history",
        "get_portfolio_history", "get_cash", "get_available_cash",
        "get_initial_value", "save_portfolio_snapshot", "print_portfolio",
    }
    missing = required - abstract
    assert not missing, f"ABC missing methods: {missing}"


def test_error_taxonomy_hierarchy():
    assert issubclass(InsufficientBuyingPower, BrokerError)
    assert issubclass(PDTRestricted, BrokerError)
    assert issubclass(AssetHalted, BrokerError)
    assert issubclass(AssetNotTradable, BrokerError)
    assert issubclass(WashSale, BrokerError)
    assert issubclass(FractionalNotAllowed, BrokerError)
    assert issubclass(BrokerRateLimited, BrokerError)
    assert issubclass(BrokerMFARequired, BrokerError)
    assert issubclass(BrokerPreflightBlocked, BrokerError)


def test_non_retryable_set():
    for cls in NON_RETRYABLE:
        assert issubclass(cls, BrokerError)
    assert AssetHalted in NON_RETRYABLE
    assert PDTRestricted in NON_RETRYABLE

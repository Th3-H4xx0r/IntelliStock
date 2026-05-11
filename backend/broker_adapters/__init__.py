"""Broker adapters: unified interface over Alpaca and Robinhood.

Alpaca is the production path. Robinhood uses an unofficial reverse-engineered
API; the UI surfaces an explicit account-ban warning when the user picks it,
and the adapter defaults to RH_DRY_RUN=true at first boot so accidental live
orders are blocked until an operator flips the env.

This package mirrors the PortfolioEmulator interface (including private attrs
_positions / _trades / _initial_value / _cash / _last_prices) so strategies that
access those attributes directly continue to work transparently in live mode.

Import convention: matches the rest of the backend/ codebase - no `backend.`
prefix, relies on `_backend_dir` being on sys.path (set by broker.py startup
and by backend/tests/ test files).
"""
from broker_adapters.base import (
    BrokerAdapter,
    OrderRef,
    PositionDTO,
    CashDTO,
    AccountDTO,
    HealthStatus,
)
from broker_adapters.errors import (
    BrokerError,
    BrokerPreflightBlocked,
    InsufficientBuyingPower,
    PDTRestricted,
    AssetHalted,
    AssetNotTradable,
    WashSale,
    FractionalNotAllowed,
    BrokerRateLimited,
    BrokerMFARequired,
    NON_RETRYABLE,
)

__all__ = [
    "BrokerAdapter",
    "OrderRef",
    "PositionDTO",
    "CashDTO",
    "AccountDTO",
    "HealthStatus",
    "BrokerError",
    "BrokerPreflightBlocked",
    "InsufficientBuyingPower",
    "PDTRestricted",
    "AssetHalted",
    "AssetNotTradable",
    "WashSale",
    "FractionalNotAllowed",
    "BrokerRateLimited",
    "BrokerMFARequired",
    "NON_RETRYABLE",
]

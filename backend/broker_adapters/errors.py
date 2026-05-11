"""Typed broker errors for rejection taxonomy.

Strategies and loop code use isinstance() against these classes, never string
matching. The NON_RETRYABLE tuple lists rejection types where a retry is
guaranteed to fail again and must be reported up as a dropped signal.
"""


class BrokerError(Exception):
    """Base broker error."""


class BrokerPreflightBlocked(BrokerError):
    """Preflight (PDT / cash / market-hours / tradability) check failed before submission."""


class InsufficientBuyingPower(BrokerError):
    pass


class PDTRestricted(BrokerError):
    """Pattern day trader restriction - account < $25k + 4th day trade in rolling 5 days."""


class AssetHalted(BrokerError):
    pass


class AssetNotTradable(BrokerError):
    pass


class WashSale(BrokerError):
    pass


class FractionalNotAllowed(BrokerError):
    pass


class BrokerRateLimited(BrokerError):
    def __init__(self, msg: str, retry_after_sec: float | None = None):
        super().__init__(msg)
        self.retry_after_sec = retry_after_sec


class BrokerMFARequired(BrokerError):
    pass


# Never retry these - will fail the same way.
NON_RETRYABLE = (
    AssetHalted,
    PDTRestricted,
    WashSale,
    AssetNotTradable,
    FractionalNotAllowed,
)

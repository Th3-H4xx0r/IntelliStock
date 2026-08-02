"""Typed broker errors for rejection taxonomy.

Strategies and loop code use isinstance() against these classes, never string
matching. The NON_RETRYABLE tuple lists rejection types where a retry is
guaranteed to fail again and must be reported up as a dropped signal.
"""


class BrokerError(Exception):
    """Base broker error.

    ``broker_definitive_rejection`` answers one question for the live order
    lifecycle: did the broker definitely NOT take this order? False means the
    outcome is unknown, and LiveOrderService must keep the order open and go
    ask the broker. True means nothing was submitted, so the lifecycle can go
    straight to a terminal REJECTED and free the identity for a retry.

    It defaults to False because guessing "rejected" on an ambiguous failure
    is how you place the same order twice. Subclasses that can only be raised
    when the broker refused - and ``submit_order``, which additionally proves
    absence by querying the client order id - set it True.
    """

    broker_definitive_rejection = False


class BrokerPreflightBlocked(BrokerError):
    """Preflight (PDT / cash / market-hours / tradability) check failed before submission."""

    broker_definitive_rejection = True


class InsufficientBuyingPower(BrokerError):
    broker_definitive_rejection = True


class PDTRestricted(BrokerError):
    """Pattern day trader restriction - account < $25k + 4th day trade in rolling 5 days."""

    broker_definitive_rejection = True


class AssetHalted(BrokerError):
    broker_definitive_rejection = True


class AssetNotTradable(BrokerError):
    broker_definitive_rejection = True


class WashSale(BrokerError):
    broker_definitive_rejection = True


class FractionalNotAllowed(BrokerError):
    broker_definitive_rejection = True


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

"""Broker adapter factory.

Dispatches to AlpacaAdapter (production) or RobinhoodAdapter. Robinhood
uses an unofficial reverse-engineered API; the UI surfaces an explicit
account-ban warning when the user picks it. Adapter still defaults to
RH_DRY_RUN=true at first boot so accidental live orders are blocked.
"""

from __future__ import annotations

from typing import Any

from broker_adapters.base import BrokerAdapter
from broker_adapters._wal import LiveOrderWAL
from broker_adapters.errors import BrokerError


def build_adapter(
    *,
    broker_type: str,
    api_key: str,
    api_secret: str,
    paper: bool,
    instance_id: str,
    wal_store: Any,
    initial_value: float | None = None,
    account_number: str | None = None,
    device_token: str | None = None,
    # 2026-04-30 — RH session-state extras + brokerage row id so the
    # adapter's in-process refresh path can compute a real TTL gate AND
    # persist new tokens back to the same DB row credential_service reads.
    rh_obtained_at_epoch: int | None = None,
    rh_expires_in: int | None = None,
    rh_account_url: str | None = None,
    rh_brokerage_id: str | None = None,
    # 2026-05-28 — clean-room mode threading. When clean_room_mode=True the
    # adapter reconciles broker state against this-instance's LiveOrderWAL
    # at boot (strategy-owned vs external split). Defaults False -> existing
    # legacy behavior unchanged for back-compat.
    clean_room_mode: bool = False,
    cid_prefix: str | None = None,
    clean_room_retention_days: int = 180,
    seed_trades_from_broker: bool = True,
) -> BrokerAdapter:
    """Build a live BrokerAdapter for the given broker_type.

    broker_type: 'alpaca' (default) or 'robinhood'.
    wal_store: must implement the _wal.Store protocol (insert/update/get/list_open).
    account_number, device_token: Robinhood-only. Ignored for Alpaca. Required
        for RH so the adapter trades from the user-selected sub-account.
    rh_obtained_at_epoch / rh_expires_in / rh_account_url / rh_brokerage_id:
        Robinhood-only. Required for proactive token refresh + DB persistence.
        Ignored for Alpaca.
    """
    t = (broker_type or "alpaca").strip().lower()
    wal = LiveOrderWAL(wal_store)
    if t in ("alpaca", ""):
        from broker_adapters.alpaca import AlpacaAdapter
        return AlpacaAdapter(
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            instance_id=instance_id,
            wal=wal,
            initial_value=initial_value,
            seed_trades_from_broker=seed_trades_from_broker,
            clean_room_mode=clean_room_mode,
            cid_prefix=cid_prefix,
            clean_room_retention_days=clean_room_retention_days,
        )
    if t == "robinhood":
        from broker_adapters.robinhood import RobinhoodAdapter
        return RobinhoodAdapter(
            api_key=api_key,
            api_secret=api_secret,
            instance_id=instance_id,
            wal=wal,
            account_number=account_number,
            device_token=device_token,
            initial_value=initial_value,
            obtained_at_epoch=rh_obtained_at_epoch,
            expires_in=rh_expires_in,
            account_url=rh_account_url,
            brokerage_id=rh_brokerage_id,
            seed_trades_from_broker=seed_trades_from_broker,
            clean_room_mode=clean_room_mode,
            cid_prefix=cid_prefix,
            clean_room_retention_days=clean_room_retention_days,
        )
    if t in ("binanceus", "binance", "binance_us", "binance.us"):
        from broker_adapters.binanceus import BinanceUSAdapter
        return BinanceUSAdapter(
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            instance_id=instance_id,
            wal=wal,
            initial_value=initial_value,
            cid_prefix=cid_prefix,
        )
    raise BrokerError(f"unknown broker_type: {broker_type!r}")

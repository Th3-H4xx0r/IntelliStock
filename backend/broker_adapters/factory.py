"""Broker adapter factory for supported broker integrations."""

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
    # 2026-05-28 — clean-room mode threading. When clean_room_mode=True the
    # adapter reconciles broker state against this-instance's LiveOrderWAL
    # at boot (strategy-owned vs external split). Defaults False -> existing
    # legacy behavior unchanged for back-compat.
    clean_room_mode: bool = False,
    cid_prefix: str | None = None,
    clean_room_retention_days: int = 180,
    seed_trades_from_broker: bool = True,
    defer_ownership_reconciliation: bool = False,
) -> BrokerAdapter:
    """Build a live BrokerAdapter for the given broker_type.

    broker_type: ``alpaca`` for equities or ``binanceus`` for crypto.
    wal_store: must implement the _wal.Store protocol (insert/update/get/list_open).
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
            defer_ownership_reconciliation=defer_ownership_reconciliation,
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

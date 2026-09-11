"""Broker adapter factory for supported broker integrations."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from broker_adapters.base import BrokerAdapter
from broker_adapters._wal import LiveOrderWAL
from broker_adapters.errors import BrokerError


# Environment override for the brokerage-account identity stamped on every
# WAL row. Set it to something stable for the account (the BrokerageAccounts
# row id, or the Alpaca account number) when API keys are rotated -- see
# derive_account_identity.
ACCOUNT_ID_ENV = "LIVE_WAL_ACCOUNT_ID"


def derive_account_identity(
    *,
    broker_type: str,
    paper: bool,
    api_key: str,
    account_id: str | None = None,
) -> str:
    """Return the account identity stamped on this instance's WAL rows.

    Precedence: an explicit ``account_id`` (the caller knows the brokerage row
    id), then the ``LIVE_WAL_ACCOUNT_ID`` environment override, then a
    one-way fingerprint of the credentials.

    The fingerprint is a truncated SHA-256 over broker type, paper flag and
    API key. It is never reversible and never logged, but it is only as stable
    as the key: ROTATING THE API KEY CHANGES THE FINGERPRINT, and every WAL row
    written under the old key stops matching, so a clean-room boot quarantines
    the positions instead of adopting them. That is the safe direction (the
    strategy will not sell what it cannot prove it bought), but an operator who
    rotates keys should set ``LIVE_WAL_ACCOUNT_ID`` -- or pass ``account_id``
    -- to a value that survives the rotation.
    """
    explicit = str(account_id or "").strip()
    if explicit:
        return explicit
    env = str(os.environ.get(ACCOUNT_ID_ENV, "") or "").strip()
    if env:
        return env
    material = "|".join((
        str(broker_type or "").strip().lower(),
        "paper" if paper else "live",
        str(api_key or ""),
    ))
    return "fp:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


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
    account_id: str | None = None,
) -> BrokerAdapter:
    """Build a live BrokerAdapter for the given broker_type.

    broker_type: ``alpaca`` for equities or ``binanceus`` for crypto.
    wal_store: must implement the _wal.Store protocol (insert/update/get/list_open).
    """
    t = (broker_type or "alpaca").strip().lower()
    # Every WAL row this adapter writes is stamped with the instance AND the
    # brokerage account that owns it, so a sibling instance sharing the first
    # 8 characters of the id can never reconstruct these fills as its own.
    wal = LiveOrderWAL(
        wal_store,
        instance_id=instance_id,
        account_id=derive_account_identity(
            broker_type=t, paper=paper, api_key=api_key, account_id=account_id,
        ),
    )
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

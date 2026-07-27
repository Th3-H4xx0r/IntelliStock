"""Strict credential selection for Alpaca equity instances.

The helpers are pure: callers perform database lookups for the exact linked
row, then pass both documents here.  No global account scan or plaintext
fallback is permitted for equities.
"""
from __future__ import annotations

from dataclasses import dataclass

from secret_store import decrypt_required


NON_EQUITY_KINDS = frozenset({"crypto", "kalshi"})


class StockCredentialError(RuntimeError):
    """Raised when an equity instance cannot prove an exact encrypted link."""


@dataclass(frozen=True)
class StockAlpacaCredentials:
    brokerage_id: str
    key: str
    secret: str
    paper: bool
    data_feed: str


def is_equity_stock_instance(instance_doc: dict | None) -> bool:
    kind = str((instance_doc or {}).get("kind") or "").strip().lower()
    return kind not in NON_EQUITY_KINDS


def linked_alpaca_brokerage_id(
    instance_doc: dict | None,
    *,
    data: bool,
) -> str:
    """Return the exact account link to use, or fail without exposing its ID."""
    if not isinstance(instance_doc, dict):
        raise StockCredentialError("stock instance record is missing")
    value = None
    if data:
        value = instance_doc.get("alpaca_data_brokerage_id")
    value = value or instance_doc.get("brokerage_id")
    brokerage_id = str(value or "").strip()
    if not brokerage_id:
        purpose = "data/trading" if data else "trading"
        raise StockCredentialError(
            f"stock instance has no exact linked Alpaca {purpose} brokerage"
        )
    return brokerage_id


def resolve_linked_alpaca_credentials(
    instance_doc: dict | None,
    brokerage_doc: dict | None,
    *,
    data: bool,
) -> StockAlpacaCredentials:
    """Validate identity/type/mode and strictly decrypt one linked Alpaca row."""
    brokerage_id = linked_alpaca_brokerage_id(instance_doc, data=data)
    return resolve_alpaca_brokerage_credentials(
        brokerage_doc,
        expected_brokerage_id=brokerage_id,
    )


def resolve_alpaca_brokerage_credentials(
    brokerage_doc: dict | None,
    *,
    expected_brokerage_id: str | None = None,
) -> StockAlpacaCredentials:
    """Strictly resolve one explicitly selected Alpaca brokerage row."""
    if not isinstance(brokerage_doc, dict):
        raise StockCredentialError("linked Alpaca brokerage record is missing")
    brokerage_id = str(brokerage_doc.get("id") or "").strip()
    if not brokerage_id:
        raise StockCredentialError("linked Alpaca brokerage identity is missing")
    if (
        expected_brokerage_id is not None
        and brokerage_id != str(expected_brokerage_id).strip()
    ):
        raise StockCredentialError("brokerage record does not match the exact instance link")
    if str(brokerage_doc.get("brokerage_type") or "").strip().lower() != "alpaca":
        raise StockCredentialError("linked stock brokerage is not Alpaca")

    paper_field = brokerage_doc.get("alpaca_paper")
    if not isinstance(paper_field, bool):
        raise StockCredentialError("linked Alpaca brokerage must declare paper mode explicitly")

    try:
        key = decrypt_required(brokerage_doc.get("alpaca_key"), field="alpaca_key")
        secret = decrypt_required(
            brokerage_doc.get("alpaca_secret"),
            field="alpaca_secret",
        )
    except Exception as exc:
        raise StockCredentialError(
            "linked Alpaca brokerage credentials failed strict decryption"
        ) from exc

    feed = str(brokerage_doc.get("alpaca_data_feed") or "iex").strip().lower()
    if feed not in {"iex", "sip"}:
        feed = "iex"
    return StockAlpacaCredentials(
        brokerage_id=brokerage_id,
        key=key,
        secret=secret,
        paper=paper_field,
        data_feed=feed,
    )

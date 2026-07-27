"""Read-only inventory of credential fields stored in database rows.

The inventory intentionally reports field names and a one-way row identifier
hash only.  It never returns, logs, or interpolates a credential value.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from secret_store import is_encrypted
from strategy_secret_boundary import iter_inline_strategy_secrets


SECRET_FIELDS_BY_TABLE: Mapping[str, tuple[str, ...]] = {
    "BrokerageAccounts": (
        "alpaca_key",
        "alpaca_secret",
        "robinhood_access_token",
        "robinhood_refresh_token",
        "robinhood_device_token",
    ),
    "Models": ("api_key",),
    "Instances": ("key", "secret"),
    "BacktestInstances": ("key", "secret"),
}

AUDITED_TABLES = tuple(SECRET_FIELDS_BY_TABLE) + ("Strategies",)


@dataclass(frozen=True)
class SecretFinding:
    table: str
    row_id_hash: str
    field: str
    encrypted: bool


def _hash_row_id(row: Mapping[str, object]) -> str:
    """Return a stable identifier without retaining the original database ID."""
    return hashlib.sha256(str(row.get("id", "")).encode("utf-8")).hexdigest()


def scan_secret_fields(rows_by_table: Mapping[str, Iterable[dict]]) -> tuple[SecretFinding, ...]:
    """Return metadata for non-empty allowlisted credential fields only."""
    findings: list[SecretFinding] = []
    for table, fields in SECRET_FIELDS_BY_TABLE.items():
        for row in rows_by_table.get(table, ()):  # Unknown tables are ignored.
            row_id_hash = _hash_row_id(row)
            for field in fields:
                stored = row.get(field)
                if stored in (None, ""):
                    continue
                findings.append(
                    SecretFinding(
                        table=table,
                        row_id_hash=row_id_hash,
                        field=field,
                        encrypted=is_encrypted(stored),
                    )
                )
    for row in rows_by_table.get("Strategies", ()):
        row_id_hash = _hash_row_id(row)
        strategies = row.get("strategies")
        for field, stored in iter_inline_strategy_secrets(
            strategies,
            path="strategies",
        ):
            findings.append(
                SecretFinding(
                    table="Strategies",
                    row_id_hash=row_id_hash,
                    field=field,
                    encrypted=is_encrypted(stored),
                )
            )
    return tuple(findings)

"""Idempotent client_order_id generator.

Alpaca constraint: <=48 chars, alphanumeric + hyphen/underscore only.
Format: {instance[:8]}-{md5(symbol|YYYY-MM-DD|side)[:16]}-{retry_n}

Deterministic: same (instance_id, symbol, date, side, retry_n) -> same id.

2026-04-22 (Fix A): cid is now keyed on DATE (YYYY-MM-DD) rather than
the full bar_iso timestamp. Previously, restarting mid-session caused
Nexus to re-emit a buy decision on a DIFFERENT bar_iso — the resulting
cid hashed differently, Alpaca's get_order_by_client_id(new_cid) did
NOT find the morning's order, and a DUPLICATE buy could land.
Keying by date collapses all same-symbol-same-day-same-side decisions
to one cid; Alpaca rejects the second submit via its duplicate-cid
check, and the adapter's own get_order_by_client_id() short-circuit at
submit_order() line ~214 returns the existing OrderRef instead of
re-submitting. retry_n remains so explicit retries (after a broker
rejection for a recoverable reason) can still get a fresh cid.
"""

from __future__ import annotations

import hashlib
import re


_SAFE = re.compile(r"[^A-Za-z0-9]")


def _safe(s: str, limit: int) -> str:
    return _SAFE.sub("", s or "")[:limit]


def _date_key(bar_iso: str) -> str:
    """Extract YYYY-MM-DD from an ISO timestamp (or a date string, or
    any string). Falls back to the full input so the hash never collapses
    to the same value for genuinely-different non-ISO inputs."""
    s = (bar_iso or "").strip()
    # ISO-8601: "YYYY-MM-DDTHH:MM:SS…" — slice the date portion.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def make_client_order_id(
    instance_id: str,
    symbol: str,
    bar_iso: str,
    side: str,
    retry_n: int,
) -> str:
    inst = _safe(instance_id, 8) or "x"
    date_key = _date_key(bar_iso)
    digest = hashlib.md5(
        f"{symbol}|{date_key}|{side}".encode("utf-8")
    ).hexdigest()[:16]
    cid = f"{inst}-{digest}-{int(retry_n)}"
    return cid[:48]

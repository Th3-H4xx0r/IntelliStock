"""Timestamp normalisation shared by the observer and the retention policy.

One function, because getting it wrong twice in two modules is how the first
version of this subsystem shipped a join that could never match.

Three real formats have to survive:

* `"2026-04-01T13:30:00"`         — naive. `broker.py:17343` stamps
  `current_time`, and `_parse_date` (`broker.py:9470`) returns a NAIVE datetime.
* `"2026-04-01T13:45:00+00:00"`   — aware UTC. Fill rows carry
  `fill.executed_at` = `quote.timestamp` (`simulated_execution.py:657`).
* `"2026-08-15T22:12:26.180893+00:00Z"` — DOUBLE suffix, produced right now by
  `bot_decision_log.build_decision_doc:89` (`isoformat()` on an aware datetime,
  then `+ "Z"`). A global `.replace("Z", "+00:00")` turns this into
  `+00:00+00:00` and raises, so only a TRAILING `Z` may be rewritten.

An offset is CONVERTED to UTC, never dropped. Dropping it reinterprets local
wall time as UTC, which shifts a row by the offset and both deletes rows that
should live and keeps rows that should expire.
"""
from __future__ import annotations

from datetime import datetime, timezone


def to_naive_utc(value):
    """Parse anything the codebase writes into a naive-UTC datetime, or None."""
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        # Trailing-anchored only — a global replace corrupts a double suffix.
        if raw.endswith("Z"):
            raw = raw[:-1]
            if not raw.endswith("+00:00"):
                raw = raw + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

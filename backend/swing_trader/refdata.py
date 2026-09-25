"""Point-in-time reference data for backtests (spec §7).

Rows are written only by scripts/build_swing_reference_data.py. Every reader
takes the store as an argument (db.store in production, the FakeStore fixture
in tests) and reads STRICTLY BEFORE the NY trading date: a daily row dated the
session carries a close from the future. `between` is [lo, hi), so the open
upper bound "VIX|<ny date>" is that rule.
"""
from __future__ import annotations

import math
from datetime import date

from swing_trader.universe import norm_symbol

SIGNALS_TABLE = "SwingSignals"
SCANS_TABLE = "SwingWheelScans"
IV_TABLE = "SwingIvSnapshots"
MACRO_TABLE = "SwingMacroDaily"
MEMBERSHIP_TABLE = "SwingIndexMembership"
SECTOR_TABLE = "SwingSectorMap"
SWING_TABLES = (SIGNALS_TABLE, SCANS_TABLE, IV_TABLE, MACRO_TABLE,
                MEMBERSHIP_TABLE, SECTOR_TABLE)

#: A VIX row older than this, counted back from the session, is a data gap and
#: not a weekend or holiday: the longest regular gap is Friday to the Tuesday
#: after a Monday holiday, 4 days. A gap blocks the regime, as a missing VIX
#: did in ST (paper_trader.py:479).
VIX_MAX_STALE_DAYS = 5


def macro_id(series, d) -> str:
    return f"{series}|{str(d)[:10]}"


def membership_id(index, d) -> str:
    return f"{index}|{str(d)[:10]}"


def _latest_before(store, table, prefix, ny_date):
    rows = store.run(store.limit(store.order_by(
        store.between(table, f"{prefix}|", f"{prefix}|{str(ny_date)[:10]}"),
        index="id", desc=True), 1))
    return rows[0] if rows else None


def vix_before(store, ny_date, *, max_stale_days: int = VIX_MAX_STALE_DAYS):
    """(close, None) for the latest VIX row strictly before `ny_date`, or
    (None, reason) when there is none, it is too old, or its close is bad."""
    row = _latest_before(store, MACRO_TABLE, "VIX", ny_date)
    if row is None:
        return None, f"no VIX row before {ny_date}"
    d = str(row.get("date") or str(row.get("id", "")).split("|", 1)[-1])[:10]
    age = (date.fromisoformat(str(ny_date)[:10]) - date.fromisoformat(d)).days
    if age > int(max_stale_days):
        return None, f"latest VIX row is {d}, {age} days before {ny_date}"
    try:
        close = float(row.get("close"))
    except (TypeError, ValueError):
        return None, f"VIX row {d} has no numeric close"
    if not math.isfinite(close) or close <= 0:
        return None, f"VIX row {d} close is {row.get('close')!r}"
    return close, None


def members_before(store, ny_date, index: str = "SPX"):
    """The full member list as of the latest change dated strictly before
    `ny_date` (dot-form symbols), or None when no row precedes it."""
    row = _latest_before(store, MEMBERSHIP_TABLE, index, ny_date)
    if row is None:
        return None
    return [norm_symbol(s) for s in (row.get("members") or []) if str(s).strip()]


def sector_map(store, symbols) -> dict:
    keys = sorted({norm_symbol(s) for s in (symbols or []) if str(s).strip()})
    if not keys:
        return {}
    rows = store.get_all(SECTOR_TABLE, *keys)
    return {str(r.get("symbol") or r.get("id")).upper(): str(r.get("sector") or "unknown")
            for r in rows}

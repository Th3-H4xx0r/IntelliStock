"""Canonical broker-symbol normalization.

2-D (bug-sweep 2026-05-28): Robinhood's instruments endpoint uses DASH-form
share-class tickers ("BRK-B", "BF-B") and rejects the dot-form ("BRK.B").
RobinhoodAdapter normalizes dot->dash at every request site, and
refresh_positions keys self._positions by the dash-form symbol the instrument
returns. Any code that WRITES a symbol the classifier later matches against a
broker position (e.g. scripts/migrate_external_position.py writing a synthetic
WAL row) must use the SAME normalization, or a share-class adoption silently
fails to match and the position stays quarantined as external. Centralizing the
rule here keeps the write path and the read path from diverging.
"""
from __future__ import annotations


def normalize_broker_symbol(symbol: str) -> str:
    """Return the canonical broker symbol: stripped, upper-cased, dot->dash."""
    return (symbol or "").strip().upper().replace(".", "-")

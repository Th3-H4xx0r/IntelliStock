"""Canonical broker-symbol normalization.

Share-class tickers may be represented in dot form ("BRK.B") or dash form
("BRK-B"). Any code that writes a symbol the classifier later matches against
a broker position must use the same normalization. Centralizing the rule keeps
the write and read paths aligned.
"""
from __future__ import annotations


def normalize_broker_symbol(symbol: str) -> str:
    """Return the canonical broker symbol: stripped, upper-cased, dot->dash."""
    return (symbol or "").strip().upper().replace(".", "-")

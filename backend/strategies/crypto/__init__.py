"""Self-contained crypto trading strategy layer for IntelliStock.

This package is deliberately DECOUPLED from ``alpaca-py``: it talks to the
Alpaca crypto data REST API over plain ``requests`` and takes injectable
provider callables for anything external, so the whole package (and its unit
tests) run with no network and no ``alpaca`` import.

Public surface:
    - ``core``      — fee model, scheduler config, bars fetch, sizing, order builder.
    - ``discovery`` — auto-coin-discovery (live + point-in-time, pure ranking).
    - Strategy classes ``Reference`` / ``Fast`` / ``Momentum`` / ``Allocator``
      live in their own modules and are loaded by the broker by name.
"""

from __future__ import annotations

from . import core, discovery

__all__ = ["core", "discovery"]

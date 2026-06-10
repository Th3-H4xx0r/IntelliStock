"""Policy gate: may the strategy fall back to Robinhood for MARKET DATA?

Robinhood prohibits unofficial API access. The live data path used to fall back
to Robinhood's public /quotes/historicals/ endpoint for any symbol Alpaca's free
IEX feed couldn't serve — and it did so from the server IP on EVERY live cycle,
regardless of which broker was trading. That re-flagged the operator's Robinhood
account even after they stopped trading on it.

This gate ensures the Robinhood data fallback only fires when Robinhood is the
TRADING broker (so a non-Robinhood instance — e.g. Alpaca — never touches
Robinhood), with an explicit kill switch via the ROBINHOOD_DATA_FALLBACK env var
or the per-instance `robinhood_data_fallback_enabled` config flag. The yfinance
fallback is independent and unaffected.

Kept in a small importable module (broker.py runs top-level code + sys.exit on
import, so it can't host a unit-testable helper).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

_OFF = ("0", "false", "no", "off")


def robinhood_data_fallback_allowed(
    broker_type: Optional[str],
    config: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True only if the strategy may call Robinhood for market-data fallback.

    Rules (all must hold):
      1. not killed via env ROBINHOOD_DATA_FALLBACK in {0,false,no,off};
      2. Robinhood is the trading broker (`broker_type == "robinhood"`), so a
         non-RH instance never calls Robinhood from the server IP;
      3. not disabled via config `robinhood_data_fallback_enabled=false`
         (default True).
    """
    if (os.environ.get("ROBINHOOD_DATA_FALLBACK", "") or "").strip().lower() in _OFF:
        return False
    if str(broker_type or "").strip().lower() != "robinhood":
        return False
    return bool((config or {}).get("robinhood_data_fallback_enabled", True))

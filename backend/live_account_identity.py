"""Boot-time LIVE account identity assertion (2026-07-27).

THE GAP THIS CLOSES: `BrokerageAccounts.alpaca_account_number` is captured when
the brokerage is linked and then never read at runtime — nothing compares it to
the account the live TradingClient actually connects to. Alpaca's paper/live
endpoint split means a wrong ENDPOINT fails closed with a 401, but a valid key
pair for a DIFFERENT LIVE ACCOUNT boots cleanly and trades that account's real
money. The only existing control is a logged reminder in broker.py:
"operator confirmed via launch checklist (no programmatic check)".

DESIGN — fail closed on a definite mismatch, fail OPEN on unknowns. Refusing to
trade because a field is merely absent would be its own outage, and the stored
value is optional on older rows. So:

    both present and different -> BLOCK   (the case that loses real money)
    either missing/blank       -> WARN    (cannot assert what we do not have)
    equal                      -> OK

Comparison is normalised: Alpaca has returned account numbers with surrounding
whitespace and inconsistent case across endpoints, and operators paste them by
hand. Pure and side-effect free so it is unit-testable without a broker.
"""
from __future__ import annotations

from typing import NamedTuple


class IdentityVerdict(NamedTuple):
    ok: bool           # False only on a DEFINITE mismatch -> caller must refuse to trade
    status: str        # "match" | "mismatch" | "unknown_stored" | "unknown_live"
    message: str


def _norm(v) -> str:
    return str(v or "").strip().upper()


def check_account_identity(stored_account_number, live_account_number,
                           *, instance_id: str = "?") -> IdentityVerdict:
    """Compare the linked account number against the connected one.

    `ok=False` means STOP: the credentials point at a different real account
    than the one this instance was configured for.
    """
    stored, live = _norm(stored_account_number), _norm(live_account_number)

    if not stored:
        return IdentityVerdict(
            True, "unknown_stored",
            f"[{instance_id}] account identity NOT VERIFIED — no "
            f"alpaca_account_number stored on the linked brokerage. Re-link the "
            f"brokerage to record it; trading continues unverified.")
    if not live:
        return IdentityVerdict(
            True, "unknown_live",
            f"[{instance_id}] account identity NOT VERIFIED — the broker did not "
            f"report an account number. Trading continues unverified.")
    if stored != live:
        return IdentityVerdict(
            False, "mismatch",
            f"[{instance_id}] LIVE ACCOUNT MISMATCH — credentials connect to "
            f"account ending {live[-4:]} but this instance is linked to one "
            f"ending {stored[-4:]}. REFUSING TO TRADE: these keys belong to a "
            f"different real account. Fix the linked brokerage or the keys.")
    return IdentityVerdict(
        True, "match",
        f"[{instance_id}] account identity verified (…{live[-4:]}).")

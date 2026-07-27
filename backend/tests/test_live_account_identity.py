"""Boot-time LIVE account identity assertion (2026-07-27).

Closes an audit finding: `BrokerageAccounts.alpaca_account_number` is recorded
at link time and never read at runtime, so a valid key pair for a DIFFERENT
LIVE ACCOUNT boots cleanly and trades that account's real money. Alpaca's
paper/live endpoint split only catches a wrong ENDPOINT (401), never a wrong
live account.

Fail closed on a definite mismatch; fail OPEN on unknowns — refusing to trade
over a merely-absent field would be its own outage.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_account_identity import check_account_identity


def test_match_allows_trading():
    v = check_account_identity("123456789", "123456789", instance_id="alpaca-main")
    assert v.ok and v.status == "match"
    assert "6789" in v.message


def test_mismatch_blocks_trading():
    """THE case this exists for: valid keys, wrong real account."""
    v = check_account_identity("123456789", "987654321", instance_id="alpaca-main")
    assert v.ok is False
    assert v.status == "mismatch"
    assert "REFUSING TO TRADE" in v.message
    # both tails surfaced so the operator can tell which is which
    assert "4321" in v.message and "6789" in v.message


def test_normalisation_does_not_cause_false_mismatch():
    """Alpaca has returned padded/mixed-case values; operators paste by hand.
    A cosmetic difference must NOT halt live trading."""
    for stored, live in (("  123456789 ", "123456789"),
                         ("abc123", "ABC123"),
                         ("ABC123", " abc123\t")):
        v = check_account_identity(stored, live)
        assert v.ok and v.status == "match", (stored, live)


def test_missing_stored_fails_open_with_warning():
    for stored in (None, "", "   "):
        v = check_account_identity(stored, "123456789")
        assert v.ok, "must not block on an unrecorded account number"
        assert v.status == "unknown_stored"
        assert "NOT VERIFIED" in v.message


def test_missing_live_fails_open_with_warning():
    for live in (None, "", "  "):
        v = check_account_identity("123456789", live)
        assert v.ok
        assert v.status == "unknown_live"
        assert "NOT VERIFIED" in v.message


def test_both_missing_fails_open():
    v = check_account_identity(None, None)
    assert v.ok and v.status == "unknown_stored"


def test_verdict_is_pure_and_repeatable():
    a = check_account_identity("111", "222")
    b = check_account_identity("111", "222")
    assert a == b


def test_non_string_inputs_do_not_raise():
    """Row values can arrive as ints from the DB."""
    assert check_account_identity(123456789, "123456789").ok
    assert check_account_identity(123456789, 987654321).ok is False

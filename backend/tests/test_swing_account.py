"""swing_trader.account: the read-only book views both lanes share."""
import os
import sys
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import account  # noqa: E402


class Health:
    """An adapter with the public option_positions_health() accessor."""

    def __init__(self, answer, rows=()):
        self.answer, self.rows = answer, list(rows)

    def option_positions_health(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer) if isinstance(self.answer, dict) else self.answer

    def list_option_positions(self):
        return list(self.rows)


# -- G8b minor 1: an answer without stale_since fails closed -------------------------

@pytest.mark.parametrize("answer", [{"complete": True}, {"complete": True, "stale": None}])
def test_a_health_answer_without_stale_since_is_unreadable(answer):
    complete, stale_since, error = account.positions_health(Health(answer))
    assert error is not None and "stale_since" in error
    assert complete is False and stale_since is None
    rows, reason = account.option_book(Health(answer))
    assert reason is not None and "could not be read" in reason
    assert account.option_positions(Health(answer)) is None


def test_a_complete_current_answer_still_reads_healthy():
    assert account.positions_health(Health({"complete": True, "stale_since": None})) == (
        True, None, None)
    assert account.option_book(Health({"complete": True, "stale_since": None})) == ([], None)


def test_an_adapter_without_the_accessor_keeps_the_private_flag_fallback():
    legacy = NS(_option_positions_complete=True, _positions_stale_since=None)
    assert account.positions_health(legacy) == (True, None, None)


# -- seams m2 / m3: swing_live_budget and put_collateral on the REAL adapter ----------

PUT_400 = ("XYZ261016P00400000", "XYZ", 400.0)


def alpaca(*, cash, bp="20000", puts=(), contracts=None):
    """A REAL AlpacaAdapter over the network-free alpaca-py doubles: its
    refresh_cash and option map are what the swing budget reads. `puts` are
    (contract, underlying, strike) shorts of one contract; `contracts`
    overrides the contract rows its lookups answer."""
    from swing_alpaca_fakes import FakeOptionsTradingClient
    from swing_alpaca_fakes import account as account_row
    from swing_alpaca_fakes import contract_row, make_adapter, option_position

    if contracts is None:
        contracts = {occ: contract_row(occ, underlying=u, strike=k, expiration="2026-10-16")
                     for occ, u, k in puts}
    client = FakeOptionsTradingClient(
        account_row=account_row(cash=cash, buying_power=bp),
        positions=[option_position(occ, qty="-1") for occ, _u, _k in puts],
        contracts_by_symbol=contracts)
    return make_adapter(client)


def test_m2_negative_cash_is_no_budget_with_no_puts_open():
    """After an assignment debit the account's cash is negative while margin
    buying power stays positive. FW1's equity snapshot refuses every swing
    BUY on negative cash, so a budget of buying power let the lane plan and
    count entries the gate was certain to refuse."""
    assert account.swing_live_budget(alpaca(cash="-500"), []) == (0.0, 0.0, None)


def test_m2_negative_cash_is_no_budget_with_puts_open():
    assert account.swing_live_budget(alpaca(cash="-500", puts=[PUT_400]), []) == (
        0.0, 40_000.0, None)


def test_m2_positive_cash_with_no_puts_is_still_st_s_buying_power():
    assert account.swing_live_budget(alpaca(cash="500"), []) == (20_000.0, 0.0, None)


# Seams m3: FW1's option snapshot treats a short whose type, underlying or strike
# is unknown as unknown collateral even when the map reads complete
# (broker.py meta_known). Alpaca can answer a contract with no type or
# underlying_symbol, which _option_contract_dto maps to "". The lane counted
# that short as 0 collateral.

def _contract(**changes):
    from swing_alpaca_fakes import contract_row

    values = {"underlying": "XYZ", "strike": 400.0, "expiration": "2026-10-16"}
    values.update(changes)
    return {PUT_400[0]: contract_row(PUT_400[0], **values)}


@pytest.mark.parametrize("contract,missing", [
    (dict(kind=None), "type"),
    (dict(underlying=""), "underlying"),
    (dict(strike=0.0), "strike"),
])
def test_m3_a_short_with_unknown_type_underlying_or_strike_is_unknown_collateral(
        contract, missing):
    adapter = alpaca(cash="45000", bp="90000", puts=[PUT_400], contracts=_contract(**contract))
    rows, reason = account.option_book(adapter)
    assert reason is None and [r.symbol for r in rows] == [PUT_400[0]]   # the map reads whole
    collateral, why = account.put_collateral(adapter, [])
    assert collateral is None and PUT_400[0] in why and "unknown" in why
    assert account.swing_live_budget(adapter, [])[0] is None


def test_m3_a_fully_known_short_call_carries_no_put_collateral():
    adapter = alpaca(cash="45000", bp="90000", puts=[PUT_400], contracts=_contract(kind="call"))
    assert account.put_collateral(adapter, []) == (0.0, None)

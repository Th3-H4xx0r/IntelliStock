"""Same-session funding: a margin account's CONFIRMED sell proceeds fund the
plan's buys in the session that sold, never borrowing.

Live (alpaca-main, real money) every EB rotation sold at the open and bought a
session later: 09-24 -> 09-25, 10-08 -> 10-09, 10-22 -> 10-23. The plan tick
sizes its buys off the cash the account holds BEFORE its own sells fill, so the
rotation's buys are clipped to nothing, and the sweep that would deploy the
proceeds refused to run in the session that planned. The backtest credits the
funding sell (`backtest_credit_pending_sell_proceeds` on doc 200), so it has
no lag; the repo measured that lag at about -1.1pp CAGR.

The fixtures come from the real producers: the AlpacaAdapter itself, driven by
a client that returns alpaca-py's own `TradeAccount` model, read through the
same `LivePendingOrderView` broker.py wraps EB's adapter in.
"""
import os
import sys
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from alpaca.trading.models import TradeAccount  # noqa: E402

from broker_adapters._wal import InMemoryStore, LiveOrderWAL  # noqa: E402
from broker_adapters.alpaca import AlpacaAdapter  # noqa: E402
from live_pending_orders import LivePendingOrderView  # noqa: E402
from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS, LAST_REBALANCE_KEY, LAST_STATE_KEY  # noqa: E402
from test_strategy_eb_run_once import (  # noqa: E402
    DECIDES, DECISION_SESSION, PRICES, FakeEmulator, alternating, cfg,
    data_for,
)

#: 10:00 ET plan tick and the next live tick, both in the 2026-06-04 session,
#: whose last visible daily close is the Wednesday decision session.
PLAN_TICK = DECIDES.replace(hour=14)
NEXT_TICK = PLAN_TICK + timedelta(minutes=20)


def account(*, cash, multiplier, buying_power=None):
    """An account exactly as alpaca-py parses Alpaca's /v2/account JSON."""
    return TradeAccount(
        id=uuid.uuid4(), account_number="PA0000TEST", status="ACTIVE",
        currency="USD", cash=str(cash),
        buying_power=str(buying_power if buying_power is not None else cash),
        regt_buying_power=str(cash), daytrading_buying_power="0",
        non_marginable_buying_power=str(cash), equity="10000",
        last_equity="10000", multiplier=multiplier,
        shorting_enabled=multiplier not in (None, "1"),
        pattern_day_trader=False, daytrade_count=0, account_blocked=False,
        trading_blocked=False)


def order(symbol, status, side="sell"):
    return SimpleNamespace(id="o1", client_order_id="c1", symbol=symbol,
                           side=side, qty="8", status=status, filled_qty="8",
                           filled_avg_price=None, submitted_at=None,
                           notional=None)


class Client:
    """The slice of alpaca-py's TradingClient the adapter touches."""

    def __init__(self, acct, orders=()):
        self.acct = acct
        self.orders = (orders if isinstance(orders, BaseException)
                       else list(orders))
        self.account_reads = 0

    def get_account(self):
        self.account_reads += 1
        if isinstance(self.acct, BaseException):
            raise self.acct
        return self.acct

    def get_all_positions(self):
        return []

    def get_orders(self, filter=None):
        if isinstance(self.orders, BaseException):
            raise self.orders
        return self.orders


def live(client, *, positions, cash):
    """The object live EB receives: the adapter inside the pending-order view.

    `_positions` and `_cash` are set the way the trade-update handler leaves
    them after the fills (alpaca.py `_apply_legacy_trade_update`).
    """
    boot = client.acct
    client.acct = account(cash=cash, multiplier="2")
    adapter = AlpacaAdapter(api_key="k", api_secret="s", paper=True,
                            instance_id="eb-test",
                            wal=LiveOrderWAL(InMemoryStore()),
                            initial_value=10000,
                            seed_trades_from_broker=False,
                            _test_client=client)
    client.acct = boot
    client.account_reads = 0  # the boot read is not the strategy's
    adapter._positions = dict(positions)
    adapter._cash = float(cash)
    return LivePendingOrderView(adapter)


def tick(emulator, when, cache, prices=PRICES, end_day=None, **overrides):
    return StrategyEb().run_once([], prices, when, cfg(**overrides), {},
                                 data=data_for(alternating(.01,
                                                           end_day=end_day)),
                                 portfolio_emulator=emulator,
                                 strategy_cache=cache)


def planned_rotation(multiplier="2"):
    """The plan tick: fully in SPY, the plan wants 40% TQQQ / 60% SPY. The
    SPY trim goes out; the TQQQ buy is clipped to the $0 the account holds
    before its own sell fills — the defect, exactly as live."""
    cache = {}
    client = Client(account(cash=0, multiplier=multiplier))
    out = tick(live(client, positions={"SPY": 20.0}, cash=0.0), PLAN_TICK,
               cache)
    assert out.get("SPY") == -1 and "TQQQ" not in out
    assert cache[LAST_REBALANCE_KEY] == DECISION_SESSION
    return cache


def after_the_sell_filled(cache, *, cash=4000.0, multiplier="2", orders=(),
                          buying_power=None, prices=PRICES, **overrides):
    """The next tick of the SAME session: the SPY trim has filled (8 shares at
    $500), so Alpaca's cash holds the unsettled proceeds."""
    client = Client(account(cash=cash, multiplier=multiplier,
                            buying_power=buying_power), orders)
    emulator = live(client, positions={"SPY": 12.0}, cash=cash)
    return tick(emulator, NEXT_TICK, cache, prices=prices, **overrides), client


def buy_cash(out, symbol):
    return out["_nexus_position_sizes"][symbol]["buy_cash"]


# --- the fix -----------------------------------------------------------------


def test_a_margin_account_funds_the_rotation_buy_in_the_session_that_sold():
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, orders=[order("SPY", "filled")])
    assert out.get("TQQQ") == 1
    assert "TQQQ" in out["_nexus_executable_buys"]
    assert 0 < buy_cash(out, "TQQQ") <= 4000.0
    assert not out["_nexus_sell_enforcement"]


def test_it_happens_once_per_session():
    cache = planned_rotation()
    first, _ = after_the_sell_filled(cache)
    assert first.get("TQQQ") == 1
    again, _ = after_the_sell_filled(cache)
    assert again == {}


def test_a_cash_account_keeps_waiting_for_the_next_session():
    cache = planned_rotation(multiplier="1")
    out, _ = after_the_sell_filled(cache, multiplier="1")
    assert out == {}


def test_an_unreported_multiplier_is_not_a_margin_account():
    cache = planned_rotation(multiplier=None)
    out, _ = after_the_sell_filled(cache, multiplier=None)
    assert out == {}


def test_spend_never_exceeds_cash_however_much_buying_power_there_is():
    """The proceeds are 'cash including unsettled'; margin buying power is
    borrowing, and EB never borrows."""
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, cash=1000.0, buying_power=20000.0)
    assert out.get("TQQQ") == 1
    assert buy_cash(out, "TQQQ") <= 1000.0


def test_a_borrowed_balance_funds_nothing():
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, cash=-50.0, buying_power=20000.0)
    assert out == {}


@pytest.mark.parametrize("status", ["new", "accepted", "partially_filled",
                                    "pending_new", "held"])
def test_an_unconfirmed_or_partial_sell_funds_nothing(status):
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, orders=[order("SPY", status)])
    assert out == {}
    # Not consumed: once the sell resolves, a later tick still funds the buy.
    out, _ = after_the_sell_filled(cache, orders=[order("SPY", "filled")])
    assert out.get("TQQQ") == 1


def test_any_working_order_on_the_account_funds_nothing():
    """Cash a working BUY will consume is not the sweep's to spend."""
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, orders=[order("GLD", "new", "buy")])
    assert out == {}


def test_an_unreadable_order_book_funds_nothing():
    cache = planned_rotation()
    out, _ = after_the_sell_filled(cache, orders=RuntimeError("503"))
    assert out == {}


def test_an_unreadable_account_funds_nothing():
    cache = planned_rotation()
    client = Client(RuntimeError("account 503"))
    out = tick(live(client, positions={"SPY": 12.0}, cash=4000.0), NEXT_TICK,
               cache)
    assert out == {}


def test_a_symbol_the_plan_sold_is_not_bought_back_the_same_session():
    """SPY drops 20% after its trim filled, so it now sits below its band.
    Buying it back is a round trip on the plan's own sell; the funded buy
    goes to TQQQ alone."""
    cache = planned_rotation()
    out, _ = after_the_sell_filled(
        cache, prices={**PRICES, "SPY": 400.0})
    assert out.get("TQQQ") == 1
    assert "SPY" not in out
    assert "SPY" not in out["_nexus_position_sizes"]


def test_a_plan_without_a_recorded_sell_list_waits_for_the_next_session():
    """A plan made before this code shipped carries no record of what it
    sold; fail closed to the old behaviour rather than guess."""
    cache = planned_rotation()
    cache.pop("_strategy_eb_plan_sells")
    out, _ = after_the_sell_filled(cache)
    assert out == {}


def test_the_kill_switch_restores_the_old_behaviour():
    cache = planned_rotation()
    out, client = after_the_sell_filled(
        cache, live_same_session_funding_enabled=False)
    assert out == {}
    assert client.account_reads == 0


def test_the_backtest_emulator_is_unchanged():
    """No margin facts to read: the same-session sweep stays refused, exactly
    as before (the backtest funds the plan tick through its own credit)."""
    cache = {LAST_REBALANCE_KEY: DECISION_SESSION,
             "_strategy_eb_pending_targets": {"TQQQ": .4, "SPY": .6},
             "_strategy_eb_plan_sells": {"session": DECISION_SESSION,
                                         "symbols": ["SPY"]}}
    out = tick(FakeEmulator(cash=4000.0, positions={"SPY": 12.0}), NEXT_TICK,
               cache)
    assert out == {}


def test_the_next_session_sweep_is_unchanged_for_a_cash_account():
    cache = planned_rotation(multiplier="1")
    client = Client(account(cash=4000, multiplier="1"))
    emulator = live(client, positions={"SPY": 12.0}, cash=4000.0)
    out = tick(emulator, NEXT_TICK + timedelta(days=1), cache,
               end_day=NEXT_TICK.replace(hour=0))
    assert out.get("TQQQ") == 1


# --- the adapter's account facts ---------------------------------------------


def test_the_adapter_reports_the_multiplier_and_cash_alpaca_returns():
    client = Client(account(cash=4321.5, multiplier="2"))
    adapter = live(client, positions={}, cash=0.0)
    assert adapter.margin_funding_facts() == {"multiplier": 2.0,
                                              "cash": 4321.5}


def test_the_adapter_raises_rather_than_guess_a_missing_multiplier():
    client = Client(account(cash=100, multiplier=None))
    adapter = live(client, positions={}, cash=0.0)
    with pytest.raises(Exception):
        adapter.margin_funding_facts()


# --- the shipped bil25 book: the 10-22 trend-OFF exit -----------------------


def falling(n=120, drop=.01):
    """Alternating history whose last 30 sessions fall 1% a day: QQQ closes
    well under SMA25 x 0.99 on the decision Wednesday, so the trend is OFF."""
    bars = alternating(.01, n=n)
    close = bars[n - 31]["c"]
    for i in range(n - 30, n):
        close *= 1 - drop
        bars[i] = {**bars[i], "c": close}
    return bars


BOOK_PRICES = {**PRICES, "GLD": 370.0, "GDX": 90.0, "XLE": 68.0}
BOOK_LEGS = ("TQQQ", "SPY", "BIL", "GLD", "GDX", "XLE")


def shipped_tick(emulator, when, cache):
    config = {**DEFAULTS, "strategy_eb_enabled": True}
    return StrategyEb().run_once([], BOOK_PRICES, when, config, {},
                                 data=data_for(falling(), legs=BOOK_LEGS),
                                 portfolio_emulator=emulator,
                                 strategy_cache=cache)


def test_the_trend_off_exit_funds_the_risk_off_book_the_same_session():
    """10-22: TQQQ sold at the open on trend OFF; BIL/GLD/XLE were bought on
    10-23. With the exit filled on a margin account they go out on 10-22."""
    cache = {"_strategy_eb_trend_state": "ON", LAST_STATE_KEY: "ON"}
    held = {"TQQQ": 30.0, "GLD": 9.0, "GDX": 15.0, "XLE": 30.0}
    client = Client(account(cash=0, multiplier="2"))
    plan = shipped_tick(live(client, positions=held, cash=0.0), PLAN_TICK,
                        cache)
    assert plan.get("TQQQ") == -1
    assert not plan["_nexus_executable_buys"]
    assert cache["_strategy_eb_trend_state"] == "OFF"

    proceeds = 30.0 * BOOK_PRICES["TQQQ"]
    filled = {s: q for s, q in held.items() if s != "TQQQ"}
    client = Client(account(cash=proceeds, multiplier="2"),
                    [order("TQQQ", "filled")])
    out = shipped_tick(live(client, positions=filled, cash=proceeds),
                       NEXT_TICK, cache)
    assert out.get("BIL") == 1
    assert "TQQQ" not in out
    spent = sum(size["buy_cash"]
                for size in out["_nexus_position_sizes"].values()
                if isinstance(size, dict) and "buy_cash" in size)
    assert 0 < spent <= proceeds
    # Whatever the plan trimmed this session is not bought back today.
    for symbol in cache["_strategy_eb_plan_sells"]["symbols"]:
        assert symbol not in out

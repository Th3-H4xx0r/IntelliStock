"""End-to-end behavior tests for clean_room_mode covering the four
scenarios from the spec
(docs/superpowers/specs/2026-05-28-broker-state-reconciliation-design.md):

  1. First-ever live boot, broker contains prior bad-test positions
     (the operator's actual current concern with `main`).
  2. Strategy bought TSLA yesterday; daemon restart 5 seconds later.
  3. Strategy holds TSLA in WAL; broker shows 0 (operator manually sold).
  4. Partial-external: broker shows 50sh; WAL implies 30sh.

Tests are full-stack at the classifier + adapter __init__ boundary:
real classifier code, real LiveOrderWAL + InMemoryStore, a stub Alpaca
TradingClient injected via the adapter's ``_test_client`` parameter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


_NOW = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)


def _alpaca_stub(positions, cash=10000.0, equity=10000.0):
    c = MagicMock()
    c.get_account.return_value = MagicMock(
        cash=str(cash), buying_power=str(cash), daytrading_buying_power=str(cash),
        equity=str(equity), last_equity=str(equity), pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    c.get_all_positions.return_value = positions
    c.get_orders.return_value = []
    return c


def _pos(symbol, qty, mv):
    return MagicMock(symbol=symbol, qty=str(qty), market_value=str(mv),
                     avg_entry_price="0.0", unrealized_pl="0.0",
                     current_price=str(mv / qty if qty else 0))


def _wal_with_rows(rows: list[dict]):
    """Build a LiveOrderWAL backed by an InMemoryStore preseeded with rows."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore
    store = InMemoryStore()
    for row in rows:
        store.insert(row)
    return LiveOrderWAL(store)


def _build_alpaca(*, positions, wal, initial_value=10000.0, cash=10000.0, equity=12000.0):
    from broker_adapters.alpaca import AlpacaAdapter
    return AlpacaAdapter(
        api_key="k", api_secret="s", paper=True,
        instance_id="main", wal=wal,
        seed_trades_from_broker=False,
        initial_value=initial_value,
        clean_room_mode=True,
        cid_prefix="main-",
        _test_client=_alpaca_stub(positions=positions, cash=cash, equity=equity),
    )


# ----------------------- Scenario 1 ---------------------------------------

def test_scenario_1_first_boot_with_contaminated_broker():
    """Bad-test orders exist in the broker; WAL is empty.

    The operator just relinked Robinhood; the account already has TSLA, NVDA,
    AAPL from prior testing. WAL has no rows because this is the strategy's
    first live boot. ALL 3 broker positions must be quarantined; _positions
    must be empty; _initial_value must be the operator-supplied baseline.
    """
    wal = _wal_with_rows([])
    adapter = _build_alpaca(
        positions=[
            _pos("TSLA", 12.0, 2640.0),
            _pos("NVDA", 8.0, 1920.0),
            _pos("AAPL", 50.0, 9200.0),
        ],
        wal=wal,
    )
    assert adapter._positions == {}, \
        "no broker positions should be adopted when WAL is empty"
    assert set(adapter._external_positions.keys()) == {"TSLA", "NVDA", "AAPL"}
    assert adapter._initial_value == 10000.0
    assert adapter._trades == []


# ----------------------- Scenario 2 ---------------------------------------

def test_scenario_2_restart_continuity_5sec_later():
    """Strategy bought TSLA yesterday; restart 5 seconds later.

    Broker still shows TSLA. The WAL has a filled BUY row this instance wrote
    yesterday. The strategy MUST recognize TSLA as its own — _positions
    contains TSLA, _trades has the WAL fill with the correct entry price.
    """
    wal = _wal_with_rows([
        {
            "client_order_id": "main-tsla-buy-0",
            "broker_order_id": "bx-1",
            "state": "filled",
            "symbol": "TSLA",
            "side": "BUY",
            "qty": 10.0,
            "notional": 2200.0,
            "filled_qty": 10.0,
            "filled_avg_price": 220.0,
            "created_at_utc": (_NOW - timedelta(days=1, hours=3)).isoformat(),
            "updated_at_utc": (_NOW - timedelta(days=1, hours=2)).isoformat(),
        },
    ])
    adapter = _build_alpaca(positions=[_pos("TSLA", 10.0, 2200.0)], wal=wal)
    assert adapter._positions == {"TSLA": 10.0}
    assert adapter._external_positions == {}
    assert len(adapter._trades) == 1
    assert adapter._trades[0]["ticker"] == "TSLA"
    assert adapter._trades[0]["price"] == 220.0, \
        "V32 trailing stop reads this as entry price; must be 220.0 from WAL"
    assert adapter._trades[0]["source"] == "wal"


# ----------------------- Scenario 3 ---------------------------------------

def test_scenario_3_manual_sell_during_downtime():
    """WAL says TSLA was bought; broker shows ZERO TSLA (operator manually sold).

    Strategy must reflect broker reality — TSLA not in _positions and not in
    _external_positions. _trades still includes the historical buy so
    strategy code that walks history can see the past, but no position is
    managed.
    """
    wal = _wal_with_rows([
        {
            "client_order_id": "main-tsla-old-0",
            "broker_order_id": "bx-old",
            "state": "filled",
            "symbol": "TSLA",
            "side": "BUY",
            "qty": 10.0,
            "notional": 2000.0,
            "filled_qty": 10.0,
            "filled_avg_price": 200.0,
            "created_at_utc": (_NOW - timedelta(days=8)).isoformat(),
            "updated_at_utc": (_NOW - timedelta(days=8)).isoformat(),
        },
    ])
    # Broker shows EMPTY positions (manual sell happened during downtime)
    adapter = _build_alpaca(positions=[], wal=wal)
    assert adapter._positions == {}, \
        "TSLA was manually sold; strategy must not think it's held"
    assert adapter._external_positions == {}
    # _trades reflects the historical fill — strategy can see the past
    assert len(adapter._trades) == 1


# ----------------------- Scenario 4 ---------------------------------------

def test_scenario_4_partial_external_split():
    """Broker shows 50 NVDA; WAL implies only 30 NVDA. Strategy owns 30,
    quarantines 20."""
    wal = _wal_with_rows([
        {
            "client_order_id": "main-nvda-a", "broker_order_id": "bx1",
            "state": "filled", "symbol": "NVDA", "side": "BUY",
            "qty": 10.0, "notional": 1800.0,
            "filled_qty": 10.0, "filled_avg_price": 180.0,
            "created_at_utc": (_NOW - timedelta(days=27)).isoformat(),
            "updated_at_utc": (_NOW - timedelta(days=27)).isoformat(),
        },
        {
            "client_order_id": "main-nvda-b", "broker_order_id": "bx2",
            "state": "filled", "symbol": "NVDA", "side": "BUY",
            "qty": 20.0, "notional": 4000.0,
            "filled_qty": 20.0, "filled_avg_price": 200.0,
            "created_at_utc": (_NOW - timedelta(days=18)).isoformat(),
            "updated_at_utc": (_NOW - timedelta(days=18)).isoformat(),
        },
    ])
    adapter = _build_alpaca(positions=[_pos("NVDA", 50.0, 10000.0)], wal=wal)
    assert adapter._positions == {"NVDA": 30.0}
    assert "NVDA" in adapter._external_positions
    assert adapter._external_positions["NVDA"]["qty"] == 20.0
    assert "partial external" in adapter._external_positions["NVDA"]["note"]


# ----------------------- Bonus: migration cycle ---------------------------

def test_migrated_position_via_synthetic_wal_row_is_strategy_owned():
    """Operator runs migrate_external_position.py for AAPL — it writes a
    synthetic WAL row tagged source=migrated. Next adapter boot should
    classify AAPL as strategy-owned."""
    wal = _wal_with_rows([
        {
            "client_order_id": "main-MIGRATED-abcd1234",
            "broker_order_id": "MIGRATED-deadbeef0001",
            "state": "filled",
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 50.0,
            "notional": 9200.0,
            "filled_qty": 50.0,
            "filled_avg_price": 184.0,
            "created_at_utc": _NOW.isoformat(),
            "updated_at_utc": _NOW.isoformat(),
            "source": "migrated",  # advisory
        },
    ])
    adapter = _build_alpaca(positions=[_pos("AAPL", 50.0, 9200.0)], wal=wal)
    assert adapter._positions == {"AAPL": 50.0}
    assert adapter._external_positions == {}

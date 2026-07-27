"""Adapter __init__ behavior under clean_room_mode.

We mock the broker clients so no live API call is made; the focus is on
how the adapter mediates between the broker (positions/cash/equity) and
the WAL (provenance) during initialization.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


# ---------- Test scaffolding ----------

def _alpaca_client_with(positions=None, cash=10000.0, equity=10000.0):
    """Build a stub alpaca-py TradingClient that satisfies AlpacaAdapter.__init__."""
    c = MagicMock()
    c.get_account.return_value = MagicMock(
        cash=str(cash), buying_power=str(cash), daytrading_buying_power=str(cash),
        equity=str(equity), last_equity=str(equity),
        pattern_day_trader=False, daytrade_count=0, account_blocked=False,
        trading_blocked=False,
    )
    c.get_all_positions.return_value = positions or []
    c.get_orders.return_value = []
    # The adapter probes session attribute when patching timeouts; satisfy it.
    c._session = None
    return c


def _mock_alpaca_position(symbol: str, qty: float, mv: float):
    p = MagicMock()
    p.symbol = symbol
    p.qty = str(qty)
    p.market_value = str(mv)
    p.avg_entry_price = "0.0"
    p.unrealized_pl = "0.0"
    p.current_price = str(mv / qty if qty else 0)
    return p


# ---------- Parameter acceptance ----------

def test_alpaca_clean_room_mode_param_accepted():
    from broker_adapters.alpaca import AlpacaAdapter

    sig = inspect.signature(AlpacaAdapter.__init__)
    for name in ("clean_room_mode", "cid_prefix", "clean_room_retention_days"):
        assert name in sig.parameters, f"AlpacaAdapter.__init__ missing {name}"
    assert sig.parameters["clean_room_mode"].default is False, \
        "clean_room_mode must default to False for backward compatibility"


def test_factory_threads_clean_room_params():
    """build_adapter accepts the new params and threads them through."""
    from broker_adapters import factory
    sig = inspect.signature(factory.build_adapter)
    for required in ("clean_room_mode", "cid_prefix", "clean_room_retention_days"):
        assert required in sig.parameters, f"build_adapter missing {required}"


# ---------- AlpacaAdapter behavior ----------

def _build_alpaca_clean_room(*, positions, wal, initial_value, cash=10000.0, equity=12000.0):
    """Helper: build an AlpacaAdapter in clean_room_mode with mocks. Returns the adapter."""
    from broker_adapters.alpaca import AlpacaAdapter
    fake_client = _alpaca_client_with(positions=positions, cash=cash, equity=equity)
    adapter = AlpacaAdapter(
        api_key="k", api_secret="s", paper=True,
        instance_id="main", wal=wal,
        seed_trades_from_broker=False,
        initial_value=initial_value,
        clean_room_mode=True,
        cid_prefix="main-",
        _test_client=fake_client,
    )
    return adapter


def test_alpaca_clean_room_classifies_strategy_owned_and_external():
    """Broker shows TSLA + AAPL; WAL has matching BUY only for TSLA.
    TSLA -> _positions; AAPL -> _external_positions; _trades rebuilt from WAL."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-tsla-0",
        "symbol": "TSLA", "side": "BUY", "state": "filled",
        "filled_qty": 10.0, "filled_avg_price": 200.0,
        "updated_at_utc": (now - timedelta(days=8)).isoformat(),
        "broker_order_id": "bx-1",
    })
    wal = LiveOrderWAL(store)

    adapter = _build_alpaca_clean_room(
        positions=[
            _mock_alpaca_position("TSLA", 10.0, 2200.0),
            _mock_alpaca_position("AAPL", 5.0, 920.0),
        ],
        wal=wal,
        initial_value=10000.0,
    )

    assert adapter._positions == {"TSLA": 10.0}, \
        f"only TSLA should be strategy-owned, got {adapter._positions}"
    assert "AAPL" in adapter._external_positions
    assert adapter._external_positions["AAPL"]["qty"] == 5.0
    assert adapter._initial_value == 10000.0, \
        "initial_value must come from the explicit param, not broker equity"
    assert len(adapter._trades) == 1
    assert adapter._trades[0]["ticker"] == "TSLA"
    assert adapter._trades[0]["source"] == "wal"


def test_alpaca_clean_room_refresh_does_not_readopt_external():
    """2-A (bug-sweep 2026-05-28): a forced refresh_positions must NOT re-merge
    a quarantined external position back into strategy-owned."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-tsla-0", "symbol": "TSLA", "side": "BUY",
        "state": "filled", "filled_qty": 10.0, "filled_avg_price": 200.0,
        "updated_at_utc": (now - timedelta(days=8)).isoformat(), "broker_order_id": "bx-1",
    })
    wal = LiveOrderWAL(store)
    adapter = _build_alpaca_clean_room(
        positions=[
            _mock_alpaca_position("TSLA", 10.0, 2200.0),
            _mock_alpaca_position("AAPL", 5.0, 920.0),
        ],
        wal=wal, initial_value=10000.0,
    )
    assert adapter._positions == {"TSLA": 10.0}
    assert "AAPL" in adapter._external_positions

    # Re-run refresh (broker still reports BOTH). AAPL must STAY quarantined.
    adapter.refresh_positions()
    assert adapter._positions == {"TSLA": 10.0}, \
        f"external AAPL re-adopted on refresh: {adapter._positions}"
    assert "AAPL" in adapter._external_positions


def test_alpaca_clean_room_refresh_preserves_partial_external_split():
    """2-A partial: broker 50sh, WAL implies 30 owned -> 30 owned / 20 external.
    A refresh must keep owned == 30 (not re-merge the 20 external shares)."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-nvda-0", "symbol": "NVDA", "side": "BUY",
        "state": "filled", "filled_qty": 30.0, "filled_avg_price": 180.0,
        "updated_at_utc": (now - timedelta(days=10)).isoformat(), "broker_order_id": "bx-2",
    })
    wal = LiveOrderWAL(store)
    adapter = _build_alpaca_clean_room(
        positions=[_mock_alpaca_position("NVDA", 50.0, 10000.0)],
        wal=wal, initial_value=10000.0,
    )
    assert adapter._positions == {"NVDA": 30.0}
    assert adapter._external_positions["NVDA"]["qty"] == 20.0

    adapter.refresh_positions()
    assert adapter._positions == {"NVDA": 30.0}, \
        f"partial-external re-merged on refresh: {adapter._positions}"


def test_alpaca_clean_room_requires_initial_value():
    """clean_room_mode=True without initial_value raises BrokerError."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore
    from broker_adapters.errors import BrokerError
    import pytest

    wal = LiveOrderWAL(InMemoryStore())
    with pytest.raises(BrokerError, match="initial_value"):
        _build_alpaca_clean_room(
            positions=[],
            wal=wal,
            initial_value=None,  # the offending omission
        )


def test_alpaca_legacy_mode_unchanged():
    """clean_room_mode=False (default) -> existing behavior: _positions reflects
    broker reality directly, _initial_value falls back to broker equity."""
    from broker_adapters.alpaca import AlpacaAdapter
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore

    wal = LiveOrderWAL(InMemoryStore())
    fake_client = _alpaca_client_with(
        positions=[_mock_alpaca_position("TSLA", 10.0, 2200.0)],
        cash=5000.0, equity=7200.0,
    )
    adapter = AlpacaAdapter(
        api_key="k", api_secret="s", paper=True,
        instance_id="main", wal=wal,
        seed_trades_from_broker=False,
        _test_client=fake_client,
        # No clean_room_mode, no initial_value -> legacy path
    )

    # Legacy path: broker positions adopted directly (TSLA is in _positions)
    assert "TSLA" in adapter._positions
    # Legacy path: _initial_value comes from broker equity ($7200)
    assert adapter._initial_value == 7200.0
    # Legacy: no external positions populated
    assert adapter.get_external_positions() == {}

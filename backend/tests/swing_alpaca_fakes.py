"""Network-free alpaca-py doubles for the swing-port adapter tests.

Nothing here opens a socket: the adapter is built with ``_test_client`` and
its market-data clients are replaced before any call reaches them.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from broker_adapters._wal import InMemoryStore, LiveOrderWAL
from broker_adapters.alpaca import AlpacaAdapter

#: Monday 2026-10-05 09:15 ET.
T0 = datetime(2026, 10, 5, 13, 15, tzinfo=timezone.utc)


def enum(value):
    return SimpleNamespace(value=value)


def account(**changes):
    values = {
        "cash": "10000", "buying_power": "10000",
        "daytrading_buying_power": "10000", "equity": "10000",
        "last_equity": "10000", "pattern_day_trader": False,
        "daytrade_count": 0, "account_blocked": False,
        "trading_blocked": False, "non_marginable_buying_power": "9000",
        "options_buying_power": "8000", "options_approved_level": 1,
        "options_trading_level": 1,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def order_row(**changes):
    values = {
        "id": "broker-1", "client_order_id": "cid-1", "symbol": "AAPL",
        "side": enum("buy"), "qty": "5", "status": enum("accepted"),
        "filled_qty": "0", "filled_avg_price": None, "filled_fees": "0",
        "order_class": enum("simple"), "type": enum("market"),
        "limit_price": None, "stop_price": None, "position_intent": None,
        "asset_class": enum("us_equity"), "legs": None,
        "submitted_at": T0, "updated_at": T0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def request_json(request) -> str:
    """One alpaca-py request as sorted JSON with enums reduced to values."""
    return json.dumps(request.to_request_fields(), sort_keys=True,
                      default=lambda value: getattr(value, "value", str(value)))


class FakeTradingClient:
    """Records every call; answers from the rows it was given."""

    def __init__(self, *, account_row=None, positions=(), orders=(),
                 contracts_by_symbol=None, submit_error=None):
        self._session = None
        self._account = account_row or account()
        self.positions = list(positions)
        self.orders = {str(row.id): row for row in orders}
        self.contracts_by_symbol = dict(contracts_by_symbol or {})
        self.contract_lookups = []
        self.submit_error = submit_error
        self.submitted = []
        self.cancelled = []
        self.order_requests = []
        self.by_id_requests = []
        #: broker id -> [(status, filled_qty), ...] answered in turn by
        #: get_order_by_id; the last entry repeats.
        self.status_script = {}

    def get_account(self):
        return self._account

    def get_all_positions(self):
        return list(self.positions)

    def get_order_by_client_id(self, client_order_id):
        raise Exception("404 not found")

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        if self.submit_error is not None:
            raise self.submit_error
        return order_row(
            id=f"broker-{len(self.submitted)}",
            client_order_id=order_data.client_order_id,
            symbol=order_data.symbol, side=order_data.side,
            qty=order_data.qty, status=enum("accepted"),
            order_class=order_data.order_class or enum("simple"),
            position_intent=order_data.position_intent)

    def get_order_by_id(self, order_id, filter=None):
        self.by_id_requests.append((str(order_id), filter))
        row = self.orders[str(order_id)]
        script = self.status_script.get(str(order_id))
        if script:
            status, filled = script.pop(0) if len(script) > 1 else script[0]
            row = SimpleNamespace(**{**vars(row), "status": enum(status),
                                     "filled_qty": filled})
        return row

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(str(order_id))

    def get_orders(self, filter=None):
        self.order_requests.append(filter)
        return list(self.orders.values())

    def get_option_contract(self, symbol):
        self.contract_lookups.append(symbol)
        if symbol not in self.contracts_by_symbol:
            raise Exception("404 not found")
        return self.contracts_by_symbol[symbol]


def make_adapter(client, *, instance_id="swing-paper", clean_room=False):
    adapter = AlpacaAdapter(
        api_key="k", api_secret="s", paper=True, instance_id=instance_id,
        wal=LiveOrderWAL(InMemoryStore()), initial_value=10000,
        seed_trades_from_broker=False, clean_room_mode=clean_room,
        defer_ownership_reconciliation=clean_room, _test_client=client)
    # Alerts reach the Discord outbox (a DB write); tests never page anyone.
    adapter._alert_submit = adapter._alert_fill = None
    adapter._alert_reject = adapter._alert_retry = None
    return adapter

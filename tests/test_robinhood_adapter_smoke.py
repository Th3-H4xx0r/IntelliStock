"""Smoke tests for RobinhoodAdapter (Phase B/C live-trading adapter).

Validates the DRY-RUN path, idempotency contract, and untradable-symbol
handling without ever touching the real Robinhood HTTP surface.

The full RobinhoodAdapter constructor exercises a lot of moving pieces
(client init, select_default_account, refresh_cash, refresh_positions,
seed_trades_from_broker). We patch each of those to keep this test purely
in-process.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Make backend importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Force DRY-RUN before any module-level reads.
os.environ["RH_DRY_RUN"] = "true"
os.environ["ALLOW_UNOFFICIAL_BROKERS"] = "true"

from broker_adapters._wal import LiveOrderWAL, InMemoryStore  # noqa: E402
from broker_adapters.errors import AssetNotTradable, BrokerError  # noqa: E402
from broker_adapters.base import OrderRef  # noqa: E402


def _build_adapter(*, instrument_url: str = "https://api.robinhood.com/instruments/abc/",
                   find_order_by_ref_id_returns=None) -> "RobinhoodAdapter":
    """Construct a RobinhoodAdapter with a fully-mocked RobinhoodClient.

    We stub robinhood_engine.RobinhoodClient AND get_live_prices to keep
    the constructor path fully synthetic.
    """
    from broker_adapters import robinhood as rh_mod  # noqa: F401
    from broker_adapters.robinhood import RobinhoodAdapter

    # Build a MagicMock client that satisfies every constructor call.
    mock_client = MagicMock(name="RobinhoodClient")
    mock_client.select_default_account.return_value = {
        "account_number": "ACC123456",
        "url": "https://api.robinhood.com/accounts/ACC123456/",
    }
    mock_client.get_account_summary.return_value = {
        "cash": 10_000.0,
        "buying_power": 10_000.0,
        "day_trade_buying_power": 0.0,
        "unsettled_funds": 0.0,
        "equity": 10_000.0,
        "pattern_day_trader": False,
        "day_trade_count": 0,
        "account_blocked": False,
        "trading_blocked": False,
    }
    mock_client.get_positions.return_value = []
    mock_client.list_orders.return_value = []
    mock_client.find_instrument_url_by_symbol.return_value = instrument_url
    mock_client.find_order_by_ref_id.return_value = find_order_by_ref_id_returns
    mock_client.get_latest_quote.return_value = {
        "ask_price": "150.10", "bid_price": "150.00", "last_trade_price": "150.05",
    }
    # _price_from_quote_side is called as an instance method on the client
    mock_client._price_from_quote_side.return_value = 150.05
    # state attribute object for token freshness checks
    mock_client.state = types.SimpleNamespace(
        access_token="t", refresh_token="r",
        obtained_at_epoch=0, expires_in=86400,
    )

    # Patch the RobinhoodClient symbol the adapter imports inside __init__.
    # The adapter does `from robinhood_engine import RobinhoodClient,
    # RobinhoodSessionState, get_live_prices as _rh_get_live_prices`.
    fake_state_cls = MagicMock(name="RobinhoodSessionState")
    fake_get_live_prices = MagicMock(name="get_live_prices", return_value={})
    fake_client_cls = MagicMock(name="RobinhoodClientCls", return_value=mock_client)

    import robinhood_engine as eng  # noqa: F401

    with patch.object(eng, "RobinhoodClient", fake_client_cls), \
         patch.object(eng, "RobinhoodSessionState", fake_state_cls), \
         patch.object(eng, "get_live_prices", fake_get_live_prices):
        store = InMemoryStore()
        wal = LiveOrderWAL(store)
        adapter = RobinhoodAdapter(
            api_key="access_tok",
            api_secret="refresh_tok",
            instance_id="test-inst-1",
            wal=wal,
            account_number="ACC123456",
            initial_value=10_000.0,        # skip equity-check fallback
            seed_trades_from_broker=False,  # avoid touching list_orders for trades
        )
    # Save the mock for assertions
    adapter._mock_client = mock_client
    adapter._wal_store = store
    return adapter


class RobinhoodAdapterSmokeTests(unittest.TestCase):

    def test_dry_run_buy_increments_position_decrements_cash(self):
        adapter = _build_adapter()
        # Pre-state
        self.assertEqual(adapter._positions, {})
        cash_before = adapter._cash

        # Stub Discord alert hooks so we can assert they fire.
        adapter._alert_submit = MagicMock()
        adapter._alert_fill = MagicMock()
        adapter._alert_reject = MagicMock()

        ref = adapter.submit_order(
            symbol="AAPL", side="buy", qty=10.0, notional=None,
            order_type="market", limit_price=None, tif="day",
            extended_hours=False, client_order_id="cid-buy-1",
        )

        # Assertions: position credited, cash debited, _trades appended.
        self.assertIsInstance(ref, OrderRef)
        self.assertEqual(ref.status, "filled")
        self.assertEqual(adapter._positions.get("AAPL"), 10.0)
        self.assertAlmostEqual(adapter._cash, cash_before - 10.0 * 150.05, places=4)
        self.assertEqual(len(adapter._trades), 1)
        self.assertEqual(adapter._trades[0]["action"], "buy")
        self.assertEqual(adapter._trades[0]["ticker"], "AAPL")
        self.assertTrue(adapter._trades[0].get("_dry_run"))

        # Discord parity: submit + fill alerts both fired in DRY-RUN.
        self.assertEqual(adapter._alert_submit.call_count, 1)
        self.assertEqual(adapter._alert_fill.call_count, 1)
        self.assertEqual(adapter._alert_reject.call_count, 0)
        # Submit alert carries broker=robinhood + dry_run=True in extras.
        kwargs = adapter._alert_submit.call_args.kwargs
        self.assertEqual(kwargs.get("symbol"), "AAPL")
        self.assertEqual(kwargs.get("side"), "buy")
        self.assertTrue(kwargs.get("extra", {}).get("dry_run"))
        self.assertEqual(kwargs.get("extra", {}).get("broker"), "robinhood")

        # No real HTTP submit path was touched.
        adapter._mock_client.place_order_equity.assert_not_called()

    def test_submit_raises_asset_not_tradable_when_instrument_missing(self):
        adapter = _build_adapter(instrument_url=None)
        adapter._alert_reject = MagicMock()
        # Make find_instrument_url_by_symbol return None (already configured).
        adapter._mock_client.find_instrument_url_by_symbol.return_value = None

        with self.assertRaises(AssetNotTradable):
            adapter.submit_order(
                symbol="ZZZZ", side="buy", qty=1.0, notional=None,
                order_type="market", limit_price=None, tif="day",
                extended_hours=False, client_order_id="cid-bad-1",
            )

        # WAL should reflect the rejection.
        rec = adapter._wal_store.get("cid-bad-1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.get("state"), "rejected")

    def test_get_order_by_client_id_returns_none_when_not_found(self):
        adapter = _build_adapter(find_order_by_ref_id_returns=None)
        # Ensure the mock returns falsy for find_order_by_ref_id.
        adapter._mock_client.find_order_by_ref_id.return_value = None
        result = adapter.get_order_by_client_id("never-submitted-cid")
        self.assertIsNone(result)
        # And empty cid short-circuits to None too.
        self.assertIsNone(adapter.get_order_by_client_id(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

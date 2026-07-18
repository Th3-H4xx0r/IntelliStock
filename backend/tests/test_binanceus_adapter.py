"""Binance.US client + paper adapter unit tests (offline — no network).

Covers HMAC signing correctness, symbol mapping, and the paper-mode fill
accounting with the 0.00%/0.02% Binance.US fee model.
"""

import hashlib
import hmac

from broker_adapters.binanceus_client import (
    BinanceUSClient, to_binance, from_binance, BINANCE_US_FEES,
)
from broker_adapters.binanceus import BinanceUSAdapter


def test_signing_is_standard_hmac_sha256():
    c = BinanceUSClient(api_secret="topsecret")
    q = "symbol=BTCUSD&side=BUY&type=MARKET&timestamp=1&recvWindow=5000"
    assert c._sign(q) == hmac.new(b"topsecret", q.encode(), hashlib.sha256).hexdigest()


def test_signed_query_appends_timestamp_and_signature():
    c = BinanceUSClient(api_secret="s", recv_window=5000)
    q = c._signed_query({"symbol": "BTCUSD"}, now_ms=1000)
    assert "symbol=BTCUSD" in q and "timestamp=1000" in q and "recvWindow=5000" in q
    assert "&signature=" in q


def test_symbol_mapping_roundtrip():
    assert to_binance("BTC/USD") == "BTCUSD"
    assert from_binance("BTCUSD") == "BTC/USD"
    assert from_binance("BTCUSDT") == "BTC/USDT"     # USDT matched before USD
    assert to_binance(from_binance("ETHUSDT")) == "ETHUSDT"
    assert from_binance("BTC/USD") == "BTC/USD"       # already slashed


def test_fees_are_binance_us():
    assert BINANCE_US_FEES == {"maker": 0.0, "taker": 0.0002}


def test_paper_buy_sell_accounting_with_002pct_taker():
    a = BinanceUSAdapter(paper=True, wal=None, initial_value=10_000.0)
    px = 50_000.0
    assert a.execute_signal("BTC/USD", 1, px, cash_per_trade=5_000.0) is True
    assert abs(a.get_cash() - 5_000.0) < 1e-6
    # 0.02% taker on $5,000 = $1.00; coins = 5000/50000 * (1-0.0002)
    assert abs(a.get_fee_summary()["total_fees"] - 1.0) < 1e-6
    assert abs(a.get_positions()["BTC/USD"] - 0.1 * (1 - 0.0002)) < 1e-9
    # sell all
    assert a.execute_signal("BTC/USD", -1, px) is True
    assert a.get_positions() == {}
    assert 9_997.0 < a.get_cash() < 9_999.5          # ~$2 total fees, both legs
    assert a.get_fee_summary()["taker_rate"] == 0.0002


def test_paper_submit_order_generates_cid_and_fills(monkeypatch):
    a = BinanceUSAdapter(paper=True, wal=None, instance_id="test", initial_value=1_000.0)
    monkeypatch.setattr(a, "_price", lambda t: 50_000.0)   # avoid network
    ref = a.submit_order("BTC/USD", "buy", None, 500.0, "market", None, "gtc", False, None)
    assert ref.status == "filled"
    assert ref.client_order_id and ref.filled_avg_price == 50_000.0
    assert a.is_market_open(None) is True
    assert a.get_portfolio_value({"BTC/USD": 50_000.0}) > 0


def test_execute_signal_guards(monkeypatch):
    a = BinanceUSAdapter(paper=True, wal=None, initial_value=100.0)
    monkeypatch.setattr(a, "_price", lambda t: None)             # no price available
    assert a.execute_signal("BTC/USD", -1, 50_000.0) is False   # nothing held
    assert a.execute_signal("BTC/USD", 0, 50_000.0) is False    # hold
    assert a.execute_signal("BTC/USD", 1, 0.0) is False          # no price -> can't size


def test_backtest_taker_fee_is_venue_configurable():
    """The backtest PortfolioEmulator applies the venue's crypto taker fee:
    Alpaca 0.25% by default, Binance.US 0.02% when passed."""
    from portfolio_emulator import PortfolioEmulator
    from broker_adapters.fees import crypto_taker_fee
    assert crypto_taker_fee("binanceus") == 0.0002
    assert crypto_taker_fee("alpaca") == 0.0025
    assert crypto_taker_fee(None) == 0.0025
    default = PortfolioEmulator(initial_cash=10_000.0)          # Alpaca 0.25%
    default.buy("BTC/USD", 5_000 / 50_000, 50_000.0)
    assert abs(default.get_fee_summary()["total_fees"] - 12.5) < 1e-6
    binance = PortfolioEmulator(initial_cash=10_000.0, taker_fee=crypto_taker_fee("binanceus"))
    binance.buy("BTC/USD", 5_000 / 50_000, 50_000.0)
    assert abs(binance.get_fee_summary()["total_fees"] - 1.0) < 1e-6


def test_resolve_taker_fee_falls_back_to_linked_brokerage():
    """The Instances row stores brokerage_id but NOT broker_type, so the fee model
    must resolve the venue from the LINKED brokerage — else a Binance.US-linked
    crypto instance backtests at Alpaca 0.25% instead of 0.02%."""
    from broker_adapters.fees import resolve_broker_type, resolve_crypto_taker_fee
    # Instance row as written by action_create_instance: no broker_type, just a link.
    inst = {"id": "c1", "kind": "crypto", "brokerage_id": "b1"}
    binance_brok = {"id": "b1", "brokerage_type": "binanceus"}
    alpaca_brok = {"id": "b2", "brokerage_type": "alpaca"}
    assert resolve_broker_type(inst, binance_brok) == "binanceus"
    assert resolve_crypto_taker_fee(inst, binance_brok) == 0.0002   # 0.02%, not 0.25%
    assert resolve_crypto_taker_fee(inst, alpaca_brok) == 0.0025
    # Instance's own broker_type wins when present (authoritative).
    assert resolve_broker_type({"broker_type": "binanceus"}, alpaca_brok) == "binanceus"
    # Neither set -> None -> default Alpaca fee.
    assert resolve_broker_type({"id": "x"}, None) is None
    assert resolve_crypto_taker_fee({"id": "x"}, None) == 0.0025


def test_list_endpoint_masks_binanceus_credentials():
    """GET /brokerages must never ship the Binance.US key/secret to the browser
    — not even the Fernet ciphertext. _mask_brokerage_doc masks both like it
    already does for Alpaca."""
    from interactive_utils import _mask_brokerage_doc
    doc = {
        "brokerage_type": "binanceus",
        "account_name": "BUS",
        "binanceus_key": "ABCDEFGH12345678",     # plaintext or ciphertext — must be masked
        "binanceus_secret": "supersecretvalue",
        "alpaca_paper": True,
    }
    m = _mask_brokerage_doc(doc)
    assert "****" in m["binanceus_key"] and "ABCDEFGH" not in m["binanceus_key"]
    assert m["binanceus_secret"] == "****"
    assert "supersecretvalue" not in str(m)
    assert m["account_name"] == "BUS"            # non-sensitive fields untouched

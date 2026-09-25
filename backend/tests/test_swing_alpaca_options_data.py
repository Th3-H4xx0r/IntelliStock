"""swing-port Task 4: options and reference data through alpaca-py 0.43.5.
Spec section 9 fix 9: the option chain is read in full, all pages."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionContractDTO
from broker_adapters.errors import BrokerError
from swing_alpaca_fakes import (
    T0,
    FakeOptionDataClient,
    FakeOptionsTradingClient,
    FakeStockDataClient,
    account,
    bar_row,
    contract_row,
    make_adapter,
    option_snapshot_row,
)

OCC = "APH261002P00130000"


def _adapter(**kwargs):
    client = FakeOptionsTradingClient(**kwargs)
    return client, make_adapter(client)


def test_option_contracts_read_every_page_with_string_strikes():
    c1 = contract_row("APH261002P00125000", strike=125.0)
    c2 = contract_row(OCC, strike=130.0)
    c3 = contract_row("APH261002P00135000", strike=135.0, open_interest=None)
    client, adapter = _adapter(contract_pages=[([c1, c2], "t1"), ([c3], None)])
    out = adapter.get_option_contracts(
        "aph", option_type="put", expiration_gte="2026-10-01",
        expiration_lte="2026-10-09", strike_gte=120, strike_lte=140.5)
    assert [c.symbol for c in out] == [
        "APH261002P00125000", OCC, "APH261002P00135000"]
    assert out[1] == OptionContractDTO(OCC, "APH", "put", 130.0,
                                       "2026-10-02", 120, 1.1)
    assert out[2].open_interest is None
    first, second = client.contract_requests
    assert first.underlying_symbols == ["APH"]
    assert first.strike_price_gte == "120.00"
    assert first.strike_price_lte == "140.50"
    assert first.type.value == "put"
    assert first.limit == 1000
    assert first.page_token is None and second.page_token == "t1"


def test_a_repeated_page_token_fails_closed():
    c1 = contract_row()
    client, adapter = _adapter(contract_pages=[([c1], "t1"), ([c1], "t1")])
    with pytest.raises(BrokerError, match="repeated"):
        adapter.get_option_contracts("APH")


def test_the_page_cap_refuses_a_partial_chain():
    c1 = contract_row()
    client, adapter = _adapter(contract_pages=[([c1], "t1"), ([c1], "t2")])
    with pytest.raises(BrokerError, match="exceeded"):
        adapter.get_option_contracts("APH", max_pages=2)


def test_a_real_alpaca_py_option_contract_reads_through_the_dto():
    """The fakes use SimpleNamespace; this runs alpaca-py 0.43.5's own
    OptionContract model (no network) through _option_contract_dto."""
    from uuid import uuid4

    from alpaca.trading.models import OptionContract

    raw = OptionContract(
        id=str(uuid4()), symbol=OCC, name="APH Oct 02 2026 130 Put",
        status="active", tradable=True, expiration_date="2026-10-02",
        root_symbol="APH", underlying_symbol="APH",
        underlying_asset_id=uuid4(), type="put", style="american",
        strike_price=130.0, size="100", open_interest="120",
        close_price="1.10")
    client, adapter = _adapter(contract_pages=[([raw], None)])
    assert adapter.get_option_contracts("APH") == [OptionContractDTO(
        OCC, "APH", "put", 130.0, "2026-10-02", 120, 1.1)]


def test_option_snapshots_use_the_indicative_feed_in_batches_of_100():
    symbols = [f"APH261002P{str(i).zfill(8)}" for i in range(150)]
    data = FakeOptionDataClient({s: option_snapshot_row() for s in symbols})
    client, adapter = _adapter()
    adapter._option_rest_client = data
    out = adapter.get_option_snapshots(symbols + [symbols[0].lower()])
    assert len(data.requests) == 2
    assert all(r.feed.value == "indicative" for r in data.requests)
    assert max(len(r.symbol_or_symbols) for r in data.requests) == 100
    snap = out[symbols[0]]
    assert (snap.bid, snap.ask, snap.last, snap.iv, snap.delta) == (
        1.0, 1.2, 1.1, 0.31, -0.24)
    assert snap.quote_ts == T0.isoformat()


def test_the_option_chain_joins_every_contract_to_its_snapshot():
    client, adapter = _adapter(contract_pages=[([contract_row()], None)])
    adapter._option_rest_client = FakeOptionDataClient(
        {OCC: option_snapshot_row(bid=0.9, ask=1.1)})
    chain = adapter.get_option_chain("APH", option_type="put")
    assert list(chain) == [OCC] and chain[OCC].bid == 0.9


def test_the_option_data_client_is_separate_from_ebs_stock_quote_client():
    """EB's REST quote rescue owns _rest_quote_client; the options client is
    its own cached object and never replaces it."""
    client, adapter = _adapter()
    stock = FakeStockDataClient()
    adapter._rest_quote_client = stock
    options = adapter._option_data_client()
    assert type(options).__name__ == "OptionHistoricalDataClient"
    assert adapter._option_data_client() is options
    assert adapter._rest_quote_data_client() is stock


def test_account_options_are_read_from_trade_account_fields():
    client, adapter = _adapter(account_row=account(
        options_trading_level=1, options_approved_level=2,
        options_buying_power="7500.5", non_marginable_buying_power="7000",
        cash="8000", equity="10000"))
    assert adapter.get_account_options() == {
        "options_trading_level": 1, "options_approved_level": 2,
        "options_buying_power": 7500.5, "non_marginable_buying_power": 7000.0,
        "cash": 8000.0, "equity": 10000.0}


def test_missing_options_fields_read_as_none():
    client, adapter = _adapter(account_row=SimpleNamespace(
        cash="1", buying_power="1", daytrading_buying_power="1", equity="1",
        last_equity="1", pattern_day_trader=False, daytrade_count=0,
        account_blocked=False, trading_blocked=False))
    options = adapter.get_account_options()
    assert options["options_trading_level"] is None
    assert options["options_buying_power"] is None


def test_option_activities_page_through_each_type_and_sort():
    opasn = [{"id": f"a{i}", "activity_type": "OPASN", "symbol": OCC,
              "qty": "-1", "date": "2026-10-02"} for i in range(3)]
    opexp = [{"id": "e1", "activity_type": "OPEXP", "symbol": OCC,
              "qty": "-1", "date": "2026-10-01", "price": "0"}]
    client, adapter = _adapter(activities={"OPASN": opasn, "OPEXP": opexp})
    out = adapter.get_option_activities(after="2026-09-25", page_size=2)
    assert [a.id for a in out] == ["e1", "a0", "a1", "a2"]
    paths = [path for path, _ in client.get_calls]
    assert paths == ["/account/activities/OPASN", "/account/activities/OPASN",
                     "/account/activities/OPEXP", "/account/activities/OPEXC"]
    assert client.get_calls[1][1]["page_token"] == "a1"
    assert all(params["after"] == "2026-09-25" for _, params in client.get_calls)
    assert out[1].qty == -1.0 and out[0].price == 0.0


def test_an_unknown_activity_type_is_refused():
    client, adapter = _adapter()
    with pytest.raises(ValueError):
        adapter.get_option_activities(types=("FILL",))


def test_daily_bars_batch_sip_all_adjusted_oldest_first():
    symbols = [f"S{i:03d}" for i in range(150)]
    stock = FakeStockDataClient(bars={"S000": [bar_row(3, 101.0),
                                               bar_row(2, 100.0)]})
    client, adapter = _adapter()
    adapter._rest_quote_client = stock
    before = datetime.now(timezone.utc)
    bars = adapter.get_daily_bars(symbols, 400)
    assert len(stock.bar_requests) == 2
    request = stock.bar_requests[0]
    assert len(request.symbol_or_symbols) == 100
    assert request.timeframe.unit.value == "Day"
    assert request.adjustment.value == "all" and request.feed.value == "sip"
    # alpaca-py stores request datetimes as naive UTC.
    end = request.end.replace(tzinfo=request.end.tzinfo or timezone.utc)
    start = request.start.replace(tzinfo=request.start.tzinfo or timezone.utc)
    assert end <= before - timedelta(minutes=15)
    assert start <= before - timedelta(days=399)
    assert [b["c"] for b in bars["S000"]] == [100.0, 101.0]
    assert set(bars["S000"][0]) == {"t", "o", "h", "l", "c", "v"}
    assert bars["S149"] == []


def test_daily_bars_without_a_data_client_raise_instead_of_returning_empty():
    client, adapter = _adapter()
    adapter._rest_quote_data_client = lambda: None
    with pytest.raises(BrokerError):
        adapter.get_daily_bars(["AAPL"], 30)


def test_latest_trades_read_the_iex_feed():
    stock = FakeStockDataClient(trades={"AAPL": SimpleNamespace(price=201.5,
                                                                timestamp=T0)})
    client, adapter = _adapter()
    adapter._rest_quote_client = stock
    assert adapter.get_latest_trades(["aapl", "MSFT"]) == {
        "AAPL": (201.5, T0.isoformat())}
    assert stock.trade_requests[0].feed.value == "iex"

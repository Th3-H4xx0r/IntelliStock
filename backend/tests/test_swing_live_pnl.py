"""swing-port Task 14: live-state rows carry option fields and Alpaca's own
P&L (interfaces doc section 9 item 4); close_position refuses contracts."""
import datetime as datetime_module
import hashlib
import itertools
import json
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import live_broker_fetch as fetch
from broker_adapters.base import PositionDTO
from live_orders import GateDecision
from live_state import OrderSubmission
from swing_broker_harness import extract, function_source

OCC = "APH261009P00130000"

# --- EB pins (written first; every digest was computed on the pre-change code
# --- at ced98fe and must not move) ---------------------------------------------


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def _snapshot_fn():
    # The pre-change digests were computed with only the snapshot extracted;
    # Task 14 adds the one helper it calls for option rows.
    return extract(("_compute_live_state_snapshot", "_option_position_payload"),
                   namespace={
        "datetime": datetime_module, "time": __import__("time"),
        "_bounded_adapter_call": lambda _name, fn, _timeout: fn(),
        "_live_trading_log_path": None, "_live_trading_started_at": None,
        "_live_trading_stop_event": threading.Event(),
        "_log": lambda *_a, **_k: None, "_strategy_tick_state": {},
        "get_conn": _no_db, "r": None, "shutdown_requested": False,
    })["_compute_live_state_snapshot"]


def _no_db():
    raise RuntimeError("no database in this test")


#: EB-shaped broker rows as refresh_positions returns them (asset_class None),
#: plus the edge rows the equity branch has always handled.
_EB_ROWS = {
    "tqqq": PositionDTO("TQQQ", 10.0, 80.0, 880.0),
    "gld_fractional": PositionDTO("GLD", 3.5, 190.0, 700.0),
    "gdx_zero_qty": PositionDTO("GDX", 0.0, 35.0, 0.0),
    "xle_no_value": PositionDTO("XLE", 4.0, 90.0, 0.0),
    "no_entry": PositionDTO("BIL", 2.0, 0.0, 183.2),
    "short_equity": PositionDTO("SQQQ", -2.0, 10.0, -21.0),
    "tagged_equity": PositionDTO("QQQ", 1.0, 480.0, 490.0,
                                 asset_class="us_equity", side="long"),
}
_ROW_SETS = {
    "empty": [], "tqqq": ["tqqq"],
    "book": ["tqqq", "gld_fractional", "gdx_zero_qty"],
    "edges": list(_EB_ROWS),
}


class _EbAdapter:
    def __init__(self, *, account, rows, option_map):
        self._account = account
        self._rows = rows
        self._positions = {"TQQQ": 10.0, "GLD": 3.5}
        self._last_prices = {"TQQQ": 88.0}
        self._trades = [
            {"timestamp": datetime_module.datetime(2026, 9, 24, 14, 0,
                                                   tzinfo=datetime_module.timezone.utc),
             "action": "buy", "ticker": "TQQQ", "shares": 10, "price": 80.0},
            {"ts": "2026-09-23T15:00:00+00:00", "side": "sell",
             "symbol": "GLD", "qty": 1, "price": 191.0, "order_id": "o-9"},
        ]
        self._cash = 1234.5
        self._initial_value = 6041.43
        self._portfolio_snapshots = [
            {"timestamp": "2026-09-20T20:00:00+00:00", "value": 6000.0}]
        self._account_id = "PA-EB"
        self._paper = False
        if option_map is not None:
            self._option_positions = option_map

    def refresh_account(self):
        if self._account is None:
            raise ConnectionError("account endpoint down")
        return self._account

    def refresh_positions(self):
        if self._rows == "raise":
            raise ConnectionError("positions endpoint down")
        if self._rows == "none":
            return None
        return [_EB_ROWS[name] for name in _ROW_SETS[self._rows]]

    def health_check(self):
        return SimpleNamespace(auth_fresh=True, trade_updates_connected=True,
                               last_heartbeat_utc=None, errors=[])


_ACCOUNTS = {
    "alpaca": SimpleNamespace(cash=1500.0, buying_power=3000.0,
                              equity=6100.0, last_equity=6041.43),
    "down": None,
}
_VOLATILE = ("uptime_sec", "portfolio_history", "mark_health",
             "strategy_tick", "lookback", "log_file_path")


def _eb_snapshots():
    snapshot = _snapshot_fn()
    out = []
    for account, rows, option_map in itertools.product(
            _ACCOUNTS, list(_ROW_SETS) + ["none", "raise"], (None, {})):
        adapter = _EbAdapter(account=_ACCOUNTS[account], rows=rows,
                             option_map=option_map)
        payload = snapshot("alpaca-main", adapter)
        out.append({"case": [account, rows, option_map is not None],
                    **{k: v for k, v in payload.items() if k not in _VOLATILE}})
    return out


EB_SNAPSHOT_DIGEST = (
    "7ea2159a2fdceb48cdec7812f9f992a37e394a839024bc4a5c3f101baa160025")


def test_eb_live_state_snapshots_are_byte_identical():
    snapshots = _eb_snapshots()
    assert len(snapshots) == 24
    assert _digest(snapshots) == EB_SNAPSHOT_DIGEST


def test_every_eb_position_row_keeps_exactly_the_old_seven_keys():
    old = {"symbol", "qty", "avg_entry_price", "last_price", "market_value",
           "unrealized_pnl", "unrealized_pnl_pct"}
    for snap in _eb_snapshots():
        for row in snap["positions"]:
            assert set(row) == old, (snap["case"], row)


_FIXED = datetime_module.datetime(2026, 9, 25, 14, 31, 7,
                                  tzinfo=datetime_module.timezone.utc)


class _FrozenDateTime(datetime_module.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FIXED if tz is not None else _FIXED.replace(tzinfo=None)


_FROZEN = SimpleNamespace(datetime=_FrozenDateTime,
                          timezone=datetime_module.timezone,
                          timedelta=datetime_module.timedelta,
                          date=datetime_module.date)


def _command_fn():
    return extract(("_execute_live_command",), check=(), namespace={
        "datetime": _FROZEN, "instance_id": "alpaca-main",
        "get_conn_retry": lambda **kw: None})["_execute_live_command"]


class _EbService:
    account_id = "brk-alpaca-main"
    instance_id = "alpaca-main"
    risk_snapshot_id = ""

    def __init__(self, mode):
        self.mode = mode
        self.intents = []

    def enqueue(self, intent):
        self.intents.append(intent)
        if self.mode == "raise":
            raise RuntimeError("lifecycle store down")
        allowed = self.mode == "allow"
        decision = GateDecision(
            allowed=allowed,
            approved_quantity=intent.quantity if allowed else Decimal("0"),
            reason_codes=() if allowed else ("dependency.quote.stale",),
            idempotency_key=intent.idempotency_key)
        ref = SimpleNamespace(broker_order_id="b-1") if allowed else None
        return OrderSubmission(decision=decision, reference=ref)


class _Marks:
    def __init__(self, mode):
        self.mode = mode

    def get(self, symbol):
        if self.mode == "raise":
            raise RuntimeError("mark book locked")
        if self.mode == "fresh" and symbol == "TQQQ":
            return SimpleNamespace(
                observed_at=_FIXED - datetime_module.timedelta(seconds=4),
                price=88.25)
        return None


def _eb_command_rows():
    execute = _command_fn()
    rows = []
    for symbol, qty, marks, prices, service_mode in itertools.product(
            ("TQQQ", " tqqq ", "GLD", "XLE", ""),
            (None, 5, "2.5", "abc", 0, -1, 40),
            (None, "fresh", "raise"),
            ({"TQQQ": 88.0}, {}),
            (None, "allow", "block", "raise")):
        adapter = SimpleNamespace(
            _positions={"TQQQ": 10.0, "GLD": 3.5}, _last_prices=dict(prices),
            _market_marks=_Marks(marks) if marks else None)
        payload = {"symbol": symbol}
        if qty is not None:
            payload["qty"] = qty
        service = _EbService(service_mode) if service_mode else None
        ok, error, result = execute(
            adapter, {"type": "close_position", "payload": payload}, service)
        intents = [
            {"key": i.idempotency_key, "identity": i.identity_payload,
             "symbol": i.symbol, "side": i.side.value, "qty": str(i.quantity),
             "reduce_only": i.reduce_only, "source": i.source.value,
             "reason": i.reason, "risk": i.risk_snapshot_id,
             "reference": str(i.reference_price), "quote_at": i.quote_at}
            for i in (service.intents if service else [])]
        rows.append([symbol, qty, marks, prices, service_mode, ok, error,
                     result, intents])
    return rows


EB_CLOSE_DIGEST = (
    "4aafc8737dadd4168af0ffd553222d8331902f301cf8ee7b25c6da91344cb4f5")


def test_eb_close_position_commands_are_byte_identical():
    rows = _eb_command_rows()
    assert len(rows) == 840
    assert _digest(rows) == EB_CLOSE_DIGEST


def _enum(value):
    return SimpleNamespace(value=value)


class _FetchClient:
    def __init__(self, account, positions, orders):
        self._account = account
        self._positions = positions
        self._orders = orders

    def get_account(self):
        return self._account

    def get_all_positions(self):
        return list(self._positions)

    def get_orders(self, filter=None):
        return list(self._orders)


def _alpaca_row(symbol, qty, avg, value):
    """An EB equity row as Alpaca serves it when it omits the P&L fields."""
    return SimpleNamespace(symbol=symbol, qty=str(qty),
                           avg_entry_price=str(avg), market_value=str(value),
                           asset_class=_enum("us_equity"), side=_enum("long"))


_EB_ALPACA_ROWS = [
    _alpaca_row("TQQQ", 10, 80, 880), _alpaca_row("gld", 3.5, 190, 700),
    _alpaca_row("GDX", 12, 35.25, 441.0), _alpaca_row("XLE", 0.25, 90, 22.8),
]
_EB_ORDERS = [
    SimpleNamespace(status=_enum("filled"), side=_enum("buy"), symbol="tqqq",
                    filled_at=datetime_module.datetime(
                        2026, 9, 24, 13, 30, tzinfo=datetime_module.timezone.utc),
                    submitted_at=None, filled_qty="10", filled_avg_price="80",
                    id="o-1"),
    SimpleNamespace(status="filled", side="sell", symbol="GLD", filled_at=None,
                    submitted_at="2026-09-23T15:00:00Z", filled_qty="1",
                    filled_avg_price="191", id="o-2"),
    SimpleNamespace(status=_enum("canceled"), side=_enum("buy"), symbol="XLE",
                    filled_at=None, submitted_at=None, filled_qty="0",
                    filled_avg_price=None, id="o-3"),
]
_OLD_ROW_KEYS = ("symbol", "qty", "avg_entry_price", "last_price",
                 "market_value", "unrealized_pnl", "unrealized_pnl_pct")
_OLD_TRADE_KEYS = ("ts", "symbol", "side", "qty", "price", "order_id")


def _eb_fetches(monkeypatch):
    out = []
    accounts = [
        SimpleNamespace(cash="1500", equity="6100", buying_power="3000",
                        last_equity="6041.43", account_number="PA-EB", id="x"),
        SimpleNamespace(cash=None, equity="0", buying_power=None,
                        last_equity=None, account_number=None, id="acct-2"),
    ]
    histories = [[], [{"ts": "2026-09-01T00:00:00+00:00", "value": 6041.43}]]
    for account, history, rows in itertools.product(
            accounts, histories, ([], _EB_ALPACA_ROWS)):
        client = _FetchClient(account, rows, _EB_ORDERS)
        monkeypatch.setattr(fetch, "_get_alpaca_client", lambda *_a: client)
        monkeypatch.setattr(fetch, "_portfolio_history_cached",
                            lambda *_a, _h=history: list(_h))
        state = fetch._fetch_alpaca("alpaca-main", {"paper": False})
        out.append({
            **{k: v for k, v in state.items()
               if k not in ("positions", "recent_trades")},
            "positions": [{k: row[k] for k in _OLD_ROW_KEYS}
                          for row in state["positions"]],
            "recent_trades": [{k: row[k] for k in _OLD_TRADE_KEYS}
                              for row in state["recent_trades"]],
        })
    return out


EB_FETCH_DIGEST = (
    "eb461f6c3a584f790ff17d6f3547719cc5654218b5a02aa605cf48382501fa7b")


def test_eb_api_fetch_keeps_every_old_value(monkeypatch):
    """Everything the API served for alpaca-main before, for rows whose P&L
    Alpaca omits: account figures, the seven old position keys and the six
    old trade keys. (Rows carrying Alpaca's own P&L now show Alpaca's numbers:
    see the consistency test below.)"""
    assert _digest(_eb_fetches(monkeypatch)) == EB_FETCH_DIGEST


def test_eb_rows_with_consistent_alpaca_pnl_show_the_same_numbers(monkeypatch):
    """alpaca-main's real rows carry Alpaca's own current_price, unrealized_pl
    and unrealized_plpc; the API now serves those. Where Alpaca's figures
    agree with the old market_value / qty derivation, the values do not move
    (to float noise); only a row where Alpaca disagrees shows a new number."""
    rows = []
    for symbol, qty, avg, value in (("TQQQ", 10, 80, 880), ("GLD", 3.5, 190, 700),
                                    ("GDX", 12, 35.25, 441.0),
                                    ("XLE", 0.25, 90, 22.8)):
        price = value / qty
        rows.append(SimpleNamespace(
            symbol=symbol, qty=str(qty), avg_entry_price=str(avg),
            market_value=str(value), current_price=str(price),
            unrealized_pl=str((price - avg) * qty),
            unrealized_plpc=str(price / avg - 1.0),
            asset_class=_enum("us_equity"), side=_enum("long")))
    client = _FetchClient(SimpleNamespace(cash="1", equity="2", buying_power="3",
                                          last_equity="2", account_number="A"),
                          rows, [])
    monkeypatch.setattr(fetch, "_get_alpaca_client", lambda *_a: client)
    monkeypatch.setattr(fetch, "_portfolio_history_cached", lambda *_a: [])
    served = fetch._fetch_alpaca("alpaca-main", {"paper": False})["positions"]
    for row, alpaca in zip(served, rows):
        qty, avg, value = float(alpaca.qty), float(alpaca.avg_entry_price), float(
            alpaca.market_value)
        old_price = value / qty
        assert row["last_price"] == pytest.approx(old_price, abs=1e-9)
        assert row["unrealized_pnl"] == pytest.approx((old_price - avg) * qty,
                                                      abs=1e-9)
        assert row["unrealized_pnl_pct"] == pytest.approx(
            ((old_price / avg) - 1.0) * 100.0, abs=1e-9)
        assert (row["asset_class"], row["side"], row["multiplier"]) == (
            "us_equity", "long", 1)


# --- the brief's tests (Ruling F4 adds option_type to the option row) ----------

class _Client:
    def __init__(self):
        self.lookups = []

    def get_option_contract(self, symbol):
        self.lookups.append(symbol)
        return SimpleNamespace(underlying_symbol="APH", strike_price=130.0,
                               expiration_date=date(2026, 10, 9),
                               type=_enum("put"))


def test_an_alpaca_option_row_carries_contract_fields_and_alpaca_pnl():
    fetch._OPTION_META_CACHE.clear()
    client = _Client()
    row = fetch._alpaca_position_payload(client, SimpleNamespace(
        symbol=OCC, qty="-1", asset_class=_enum("us_option"),
        side=_enum("short"), avg_entry_price="1.23", market_value="-110",
        current_price="1.10", unrealized_pl="13", unrealized_plpc="0.1057"))
    assert row == {
        "symbol": OCC, "qty": -1.0, "avg_entry_price": 1.23,
        "last_price": 1.10, "market_value": -110.0, "unrealized_pnl": 13.0,
        "unrealized_pnl_pct": 10.57, "asset_class": "us_option",
        "side": "short", "multiplier": 100, "underlying": "APH",
        "option_type": "put", "strike": 130.0, "expiry": "2026-10-09"}
    fetch._alpaca_position_payload(client, SimpleNamespace(
        symbol=OCC, qty="-1", asset_class="us_option"))
    assert client.lookups == [OCC]


def test_an_option_row_without_alpaca_prices_is_null_not_zero():
    fetch._OPTION_META_CACHE.clear()
    row = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol=OCC, qty="-1", asset_class="us_option", avg_entry_price="1.23"))
    assert (row["last_price"], row["market_value"], row["unrealized_pnl"],
            row["unrealized_pnl_pct"]) == (None, None, None, None)


def test_an_equity_row_prefers_alpaca_and_falls_back_to_the_old_derivation():
    alpaca = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol="tqqq", qty="10", avg_entry_price="80", market_value="880",
        current_price="88.5", unrealized_pl="85", unrealized_plpc="0.10625"))
    assert (alpaca["last_price"], alpaca["unrealized_pnl"],
            alpaca["asset_class"], alpaca["side"], alpaca["multiplier"]) == (
        88.5, 85.0, "us_equity", "long", 1)
    assert abs(alpaca["unrealized_pnl_pct"] - 10.625) < 1e-9
    legacy = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol="TQQQ", qty="10", avg_entry_price="80", market_value="880"))
    assert legacy["last_price"] == 88.0 and legacy["unrealized_pnl"] == 80.0
    assert abs(legacy["unrealized_pnl_pct"] - 10.0) < 1e-9


def test_recent_trades_carry_their_asset_class():
    filled = SimpleNamespace(
        status=_enum("filled"), side=_enum("sell"), symbol=OCC,
        filled_at=None, submitted_at=None, filled_qty="1",
        filled_avg_price="1.23", id="o-1", asset_class=_enum("us_option"))
    legacy = SimpleNamespace(
        status="filled", side="buy", symbol="TQQQ", filled_at=None,
        submitted_at=None, filled_qty="2", filled_avg_price="88", id="o-2")
    client = SimpleNamespace(get_orders=lambda filter=None: [filled, legacy])
    rows = fetch._alpaca_recent_trades(client)
    assert [r["asset_class"] for r in rows] == ["us_option", "us_equity"]


def test_the_broker_snapshot_builds_option_rows_from_alpaca_fields():
    payload = extract(("_option_position_payload",))["_option_position_payload"]
    row = payload(SimpleNamespace(
        symbol=OCC, qty=-1.0, avg_entry_price=1.23, market_value=-110.0,
        current_price=1.10, unrealized_pl=13.0, unrealized_plpc=0.1057,
        asset_class="us_option", side="short", multiplier=100,
        underlying="APH", option_type="put", strike=130.0, expiry="2026-10-09"))
    assert row["unrealized_pnl"] == 13.0 and row["multiplier"] == 100
    assert abs(row["unrealized_pnl_pct"] - 10.57) < 1e-9
    assert (row["side"], row["underlying"], row["option_type"], row["strike"],
            row["expiry"]) == ("short", "APH", "put", 130.0, "2026-10-09")
    blank = payload(SimpleNamespace(symbol=OCC, qty=-1.0, avg_entry_price=1.23,
                                    market_value=0.0, current_price=None,
                                    unrealized_pl=None, unrealized_plpc=None,
                                    asset_class="us_option", side="short",
                                    multiplier=100, underlying=None,
                                    option_type=None, strike=None, expiry=None))
    assert (blank["last_price"], blank["market_value"],
            blank["unrealized_pnl"], blank["unrealized_pnl_pct"]) == (
        None, None, None, None)


def test_the_snapshot_loop_diverts_only_option_rows():
    body = function_source("_compute_live_state_snapshot")
    divert = body.index('if getattr(p, "asset_class", None) == "us_option":')
    equity = body.index('sym = getattr(p, "symbol", "") or ""')
    assert divert < equity
    assert "_option_position_payload(p)" in body


def test_close_position_refuses_an_option_contract():
    ns = extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "swing-paper",
        "get_conn_retry": lambda **kw: None})

    class _Service:
        account_id = "acct-1"
        instance_id = "swing-paper"

        def enqueue(self, intent):
            raise AssertionError("an option contract reached the order service")

    adapter = SimpleNamespace(_positions={}, _option_positions={OCC: object()},
                              _last_prices={}, _market_marks=None)
    ok, error, _result = ns["_execute_live_command"](
        adapter, {"type": "close_position", "payload": {"symbol": OCC}},
        _Service())
    assert ok is False and "buy-to-close" in error


# --- controller ruling 3 (plan B G8a re-review) and ruling 4 (contract) -------

def _patch_fetch(monkeypatch, client, *, history=()):
    monkeypatch.setattr(fetch, "_load_credentials", lambda _iid: {
        "error": None, "broker_type": "alpaca", "paper": True,
        "key": "k", "secret": "s"})
    monkeypatch.setattr(fetch, "_get_alpaca_client", lambda *_a: client)
    monkeypatch.setattr(fetch, "_portfolio_history_cached",
                        lambda *_a: list(history))


class _WheelClient(_FetchClient):
    def __init__(self, positions, orders=(), *, positions_error=None):
        super().__init__(SimpleNamespace(cash="25000", equity="38000",
                                         buying_power="25000",
                                         last_equity="37900",
                                         account_number="PA-WHEEL"),
                         positions, orders)
        self.positions_error = positions_error
        self.lookups = []

    def get_all_positions(self):
        if self.positions_error is not None:
            raise self.positions_error
        return list(self._positions)

    def get_option_contract(self, symbol):
        self.lookups.append(symbol)
        kind = "call" if symbol[-9] == "C" else "put"
        return SimpleNamespace(underlying_symbol="APH", strike_price=130.0,
                               expiration_date=date(2026, 10, 9),
                               type=_enum(kind))


def _short_put(**changes):
    values = dict(symbol=OCC, qty="-1", asset_class=_enum("us_option"),
                  side=_enum("short"), avg_entry_price="1.23",
                  market_value="-110", current_price="1.10",
                  unrealized_pl="13", unrealized_plpc="0.1057")
    values.update(changes)
    return SimpleNamespace(**values)


_OPTION_ROW_KEYS = {"asset_class", "side", "multiplier", "underlying", "strike",
                    "expiry", "option_type", "qty"}


def test_every_api_option_row_carries_the_contract_shape(monkeypatch):
    fetch._OPTION_META_CACHE.clear()
    covered_call = _short_put(symbol="APH261009C00140000")
    _patch_fetch(monkeypatch, _WheelClient(
        [_short_put(), covered_call, _alpaca_row("APH", 100, 130, 13100)]))
    state = fetch.fetch_broker_live_state(None, "swing-paper")
    assert state["broker_fetch_error"] is None
    put, call, shares = state["positions"]
    for row in (put, call):
        assert _OPTION_ROW_KEYS <= set(row)
        assert (row["asset_class"], row["side"], row["multiplier"],
                row["underlying"], row["strike"], row["expiry"], row["qty"]) == (
            "us_option", "short", 100, "APH", 130.0, "2026-10-09", -1.0)
    assert (put["option_type"], call["option_type"]) == ("put", "call")
    assert (shares["asset_class"], shares["side"], shares["multiplier"],
            shares["option_type"], shares["qty"]) == (
        "us_equity", "long", 1, None, 100.0)


def test_option_pnl_is_alpacas_per_share_not_the_per_contract_mismatch(monkeypatch):
    """Before: last_price = market_value / qty = 110 (a contract's dollars)
    against a per-share entry of 1.23, so unrealized = (110 - 1.23) x -1 =
    -108.77 on a put that is 13 dollars in profit."""
    fetch._OPTION_META_CACHE.clear()
    _patch_fetch(monkeypatch, _WheelClient([_short_put()]))
    (row,) = fetch.fetch_broker_live_state(None, "swing-paper")["positions"]
    assert (row["last_price"], row["market_value"], row["unrealized_pnl"],
            row["avg_entry_price"]) == (1.10, -110.0, 13.0, 1.23)
    assert row["unrealized_pnl_pct"] == pytest.approx(10.57)


@pytest.mark.parametrize("absent", ["current_price", "market_value",
                                    "unrealized_pl", "unrealized_plpc"])
def test_an_absent_alpaca_figure_is_null_never_zero(monkeypatch, absent):
    fetch._OPTION_META_CACHE.clear()
    _patch_fetch(monkeypatch, _WheelClient([_short_put(**{absent: None})]))
    (row,) = fetch.fetch_broker_live_state(None, "swing-paper")["positions"]
    field = {"current_price": "last_price", "market_value": "market_value",
             "unrealized_pl": "unrealized_pnl",
             "unrealized_plpc": "unrealized_pnl_pct"}[absent]
    assert row[field] is None


def test_api_recent_trades_carry_their_asset_class(monkeypatch):
    fetch._OPTION_META_CACHE.clear()
    orders = [SimpleNamespace(
        status=_enum("filled"), side=_enum("sell"), symbol=OCC, filled_at=None,
        submitted_at=None, filled_qty="1", filled_avg_price="1.23", id="o-1",
        asset_class=_enum("us_option"))] + list(_EB_ORDERS)
    _patch_fetch(monkeypatch, _WheelClient([], orders))
    trades = fetch.fetch_broker_live_state(None, "swing-paper")["recent_trades"]
    assert [(t["symbol"], t["asset_class"]) for t in trades] == [
        (OCC, "us_option"), ("TQQQ", "us_equity"), ("GLD", "us_equity")]


@pytest.mark.parametrize("failure", [
    ConnectionError("positions endpoint down"), "not-a-list", None])
def test_an_unreadable_positions_answer_is_a_fetch_error(monkeypatch, failure):
    """Plan B G8a re-review: the wheel page must never read an outage as "no
    open puts". A raised read, or an answer that is not a list, fails the
    whole fetch; the API then serves the container's row, marked stale."""
    client = _WheelClient([_short_put()])
    if isinstance(failure, Exception):
        client.positions_error = failure
    else:
        client.get_all_positions = lambda: failure
    _patch_fetch(monkeypatch, client)
    state = fetch.fetch_broker_live_state(None, "swing-paper")
    assert str(state["broker_fetch_error"]).startswith("positions_unavailable: ")
    assert "positions" not in state


def test_an_unreadable_option_row_is_a_fetch_error_not_a_missing_put(monkeypatch):
    fetch._OPTION_META_CACHE.clear()
    _patch_fetch(monkeypatch, _WheelClient([_short_put(qty="garbage")]))
    state = fetch.fetch_broker_live_state(None, "swing-paper")
    assert str(state["broker_fetch_error"]).startswith("positions_unavailable: ")


def test_an_unreadable_equity_row_is_still_skipped_as_before(monkeypatch):
    _patch_fetch(monkeypatch, _WheelClient(
        [_alpaca_row("TQQQ", "garbage", 80, 880), _alpaca_row("GLD", 1, 190, 200)]))
    state = fetch.fetch_broker_live_state(None, "alpaca-main")
    assert state["broker_fetch_error"] is None
    assert [row["symbol"] for row in state["positions"]] == ["GLD"]


def test_the_api_serves_the_container_row_as_stale_on_a_positions_outage(
        monkeypatch):
    import interactive_utils as iu
    import live_state

    container_put = {"symbol": OCC, "qty": -1.0, "asset_class": "us_option",
                     "side": "short", "multiplier": 100, "last_price": None}
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda _conn, _iid: {"id": "swing-paper"})
    monkeypatch.setattr(live_state, "get_live_state", lambda *_a: {
        "id": "swing-paper", "status": "active", "trading_active": True,
        "positions": [container_put], "cash": 25000.0})
    _patch_fetch(monkeypatch, _WheelClient(
        [], positions_error=ConnectionError("positions endpoint down")))
    served = iu.action_get_live_state(None, "swing-paper")
    assert served["stale"] is True and served["trading_active"] is False
    assert served["broker_fetch_error"].startswith("positions_unavailable: ")
    assert served["positions"] == [container_put]


def _outage_adapter(rows):
    from broker_adapters.base import OptionPositionDTO

    adapter = _EbAdapter(account=_ACCOUNTS["alpaca"], rows=rows,
                         option_map={OCC: OptionPositionDTO(
                             OCC, "APH", "put", 130.0, "2026-10-09", -1, 1.23,
                             1.10, -110.0, 13.0)})
    return adapter


@pytest.mark.parametrize("rows", ["tqqq", "none", "raise"])
def test_a_positions_outage_keeps_the_last_known_put_with_prices_unknown(rows):
    """The adapter's preserve path (and the cached fallback) rebuild only the
    equity mirror, so the container row would lose the short put exactly when
    the API falls back to it. The last-good contract is carried, unpriced."""
    payload = _snapshot_fn()("swing-paper", _outage_adapter(rows))
    (put,) = [row for row in payload["positions"] if row["symbol"] == OCC]
    assert (put["asset_class"], put["side"], put["multiplier"], put["qty"],
            put["underlying"], put["option_type"], put["strike"],
            put["expiry"]) == ("us_option", "short", 100, -1.0, "APH", "put",
                               130.0, "2026-10-09")
    assert (put["last_price"], put["market_value"], put["unrealized_pnl"],
            put["unrealized_pnl_pct"]) == (None, None, None, None)


def test_a_fresh_option_row_is_never_carried_twice():
    live = PositionDTO(OCC, -1.0, 1.23, -110.0, asset_class="us_option",
                       side="short", unrealized_pl=13.0, unrealized_plpc=0.1057,
                       current_price=1.10, multiplier=100, underlying="APH",
                       option_type="put", strike=130.0, expiry="2026-10-09")
    adapter = _outage_adapter("tqqq")
    adapter.refresh_positions = lambda: [_EB_ROWS["tqqq"], live]
    payload = _snapshot_fn()("swing-paper", adapter)
    (put,) = [row for row in payload["positions"] if row["symbol"] == OCC]
    assert put["unrealized_pnl"] == 13.0 and put["last_price"] == 1.10
    assert [row["symbol"] for row in payload["positions"]] == ["TQQQ", OCC]


@pytest.mark.parametrize("symbol", [OCC, "aph261009p00130000",
                                    "SPY261009C00580000", "BRKB261009P00400000"])
def test_close_position_refuses_any_contract_even_one_the_book_lost(symbol):
    """A contract missing from the option map (an incomplete refresh) still
    must never be sold like a stock: with an explicit qty it would otherwise
    reach the order service as an equity SELL of an OCC symbol."""
    execute = _command_fn()
    service = _EbService("allow")
    adapter = SimpleNamespace(_positions={}, _option_positions={},
                              _last_prices={}, _market_marks=None)
    ok, error, result = execute(adapter, {
        "type": "close_position", "payload": {"symbol": symbol, "qty": 1}},
        service)
    assert ok is False and service.intents == [] and result == {}
    assert error == (f"{symbol.strip().upper()} is an option contract; option "
                     "contracts are closed by buy-to-close, not close_position")


def test_the_contract_doc_matches_the_emitted_row_shape():
    """Ruling 4: interfaces section 10 names every key an option row carries."""
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "..", "docs",
                        "superpowers", "plans",
                        "2026-09-24-swing-port-interfaces.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    section = text[text.index("## 10. Additions from plan A-live"):]
    item = section[section.index("16. Live-state position rows"):]
    for key in ("asset_class", "side", "multiplier", "underlying", "strike",
                "expiry", "option_type", "broker_fetch_error",
                "positions_unavailable"):
        assert f"`{key}`" in item or key in item, key

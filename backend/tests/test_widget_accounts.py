"""One-call iOS widget payload (/widget/accounts) so the widget self-refreshes.

Curve/value come from the broker's portfolio history (dense + persistent across
restarts); positions from live-state.
"""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_brokerage_id_resolution():
    from api.main import _widget_brokerage_id
    assert _widget_brokerage_id({"brokerage": {"brokerage_id": "b1"}}) == "b1"
    assert _widget_brokerage_id({"brokerage_id": "b2"}) == "b2"
    assert _widget_brokerage_id({"brokerage": {"id": "b3"}}) == "b3"
    assert _widget_brokerage_id({}) is None


def test_account_maps_history_to_chart():
    from api.main import _widget_account
    inst = {"id": "alpaca-main", "name": "Alpaca Live Main"}
    hist = {
        "timestamps": [1781136000000, 1781136900000],  # ms
        "values": [5937.90, 6102.22],
        "current_value": 6102.22,
        "change_abs": 162.91,
        "change_pct": 2.74,
    }
    positions = [{"symbol": "CAVA", "unrealizedPnlPct": 10.5, "marketValue": 542.0}]
    acct = _widget_account(inst, hist, positions)
    assert acct["id"] == "alpaca-main" and acct["label"] == "Alpaca Live Main"
    assert acct["accountValue"] == 6102.22
    assert acct["dayPnlAbs"] == 162.91 and acct["dayPnlPct"] == 2.74
    assert len(acct["intradayPoints"]) == 2
    assert acct["intradayPoints"][0] == {"t": 1781136000, "v": 5937.90}  # ms -> epoch s
    assert acct["intradayPoints"][1]["v"] == 6102.22
    assert acct["positions"][0]["symbol"] == "CAVA"


def test_account_current_value_falls_back_to_last_point():
    from api.main import _widget_account
    hist = {"timestamps": [1000, 2000], "values": [10.0, 12.0]}  # no current_value
    acct = _widget_account({"id": "x"}, hist, [])
    assert acct["accountValue"] == 12.0


def test_widget_accounts_endpoint(monkeypatch):
    from api import main
    monkeypatch.setattr(main, "action_instances",
                        lambda conn: {"instances": [{"id": "a", "name": "A"}]})
    monkeypatch.setattr(main, "action_get_instance",
                        lambda conn, iid: {"id": iid, "name": "A", "brokerage": {"brokerage_id": "brk1"}})
    monkeypatch.setattr(main, "action_get_portfolio_history",
                        lambda conn, bid, rng: {"timestamps": [1781136000000, 1781136900000],
                                                "values": [100.0, 110.0], "current_value": 110.0,
                                                "change_abs": 10.0, "change_pct": 10.0})
    monkeypatch.setattr(main, "action_get_live_state",
                        lambda conn, iid: {"positions": [{"symbol": "AAPL", "market_value": 50.0,
                                                          "unrealized_pnl_pct": 1.0}]})
    res = main.api_widget_accounts(conn=None, current_user={"id": "u1"})
    assert len(res["accounts"]) == 1
    acct = res["accounts"][0]
    assert acct["accountValue"] == 110.0
    assert len(acct["intradayPoints"]) == 2  # dense curve preserved
    assert acct["positions"][0]["symbol"] == "AAPL"
    assert "synced_at" in res


def test_widget_accounts_skips_instance_without_history(monkeypatch):
    from api import main
    monkeypatch.setattr(main, "action_instances",
                        lambda conn: {"instances": [{"id": "a", "name": "A"}]})
    monkeypatch.setattr(main, "action_get_instance",
                        lambda conn, iid: {"id": iid, "brokerage": {"brokerage_id": "brk1"}})

    def _boom(conn, bid, rng):
        raise RuntimeError("no creds")

    monkeypatch.setattr(main, "action_get_portfolio_history", _boom)
    monkeypatch.setattr(main, "action_get_live_state", lambda conn, iid: None)
    res = main.api_widget_accounts(conn=None, current_user={"id": "u1"})
    assert res["accounts"] == []

"""One-call iOS widget payload (/widget/accounts) so the widget self-refreshes."""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_iso_to_epoch():
    from api.main import _widget_iso_to_epoch
    assert _widget_iso_to_epoch("2026-06-11T00:00:00+00:00") == 1781136000
    assert _widget_iso_to_epoch("2026-06-11T00:00:00Z") == 1781136000
    assert _widget_iso_to_epoch(1781136000) == 1781136000
    assert _widget_iso_to_epoch(None) is None
    assert _widget_iso_to_epoch("garbage") is None


def test_account_from_live_state_maps_fields():
    from api.main import _widget_account_from_live_state
    inst = {"id": "alpaca-main", "name": "Alpaca Live Main"}
    ls = {
        "equity": 5994.38,
        "day_pnl": -62.10,
        "day_pnl_pct": -1.04,
        "portfolio_history": [
            {"ts": "2026-06-11T20:00:00+00:00", "value": 5937.90},
            {"ts": "2026-06-11T08:00:00+00:00", "value": 5994.38},
        ],
        "positions": [
            {"symbol": "AAPL", "market_value": 422.64, "unrealized_pnl_pct": 1.2},
        ],
    }
    acct = _widget_account_from_live_state(inst, ls)
    assert acct["id"] == "alpaca-main"
    assert acct["label"] == "Alpaca Live Main"
    assert acct["accountValue"] == 5994.38  # live equity, not the stale RTH point
    assert acct["dayPnlAbs"] == -62.10
    assert len(acct["intradayPoints"]) == 2
    assert acct["intradayPoints"][0]["v"] == 5937.90
    assert acct["positions"][0] == {"symbol": "AAPL", "unrealizedPnlPct": 1.2, "marketValue": 422.64}


def test_widget_accounts_endpoint_skips_dead_instances(monkeypatch):
    from api import main
    monkeypatch.setattr(main, "action_instances",
                        lambda conn: {"instances": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})

    def _ls(conn, iid):
        if iid == "a":
            return {"equity": 1000.0, "day_pnl": 5.0, "day_pnl_pct": 0.5,
                    "portfolio_history": [{"ts": "2026-06-11T20:00:00+00:00", "value": 1000.0}],
                    "positions": []}
        return None  # b not running

    monkeypatch.setattr(main, "action_get_live_state", _ls)
    res = main.api_widget_accounts(conn=None, current_user={"id": "u1"})
    assert len(res["accounts"]) == 1
    assert res["accounts"][0]["id"] == "a"
    assert res["accounts"][0]["accountValue"] == 1000.0
    assert "synced_at" in res

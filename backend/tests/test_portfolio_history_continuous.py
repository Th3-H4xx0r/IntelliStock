"""Bug fix (2026-06-11): the iOS widget + dashboard portfolio history froze at
market close because the Alpaca portfolio/history request used
intraday_reporting=market_hours. It now requests `continuous` so the series +
current_value include the overnight session.
"""
from __future__ import annotations


class _Resp:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"timestamp": [1718000000, 1718000900], "equity": [5937.90, 5994.38]}


def test_alpaca_portfolio_history_uses_continuous_reporting(monkeypatch):
    import requests
    captured = {}

    def _fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(requests, "get", _fake_get)
    from interactive_utils import _fetch_alpaca_portfolio_history
    out = _fetch_alpaca_portfolio_history("k", "s", "https://paper-api.alpaca.markets", "1D")

    assert captured["params"]["intraday_reporting"] == "continuous"
    assert captured["params"]["extended_hours"] == "true"
    # current value = last point = the overnight/live equity, not the RTH close
    assert out["values"][-1] == 5994.38

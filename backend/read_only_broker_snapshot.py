"""Concrete GET-only Alpaca account snapshot used by inactive verification."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from secret_store import decrypt_required


def _get_json(url, headers):
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def read_authoritative_snapshot(instance, brokerage):
    """Read positions plus open/recent order and fill history; never writes."""
    if type(brokerage) is not dict or brokerage.get("brokerage_type") != "alpaca":
        raise RuntimeError("unsupported live brokerage for read-only verification")
    if brokerage.get("alpaca_paper") is not False:
        raise RuntimeError("brokerage is not a funded Alpaca account")
    key = decrypt_required(brokerage.get("alpaca_key"), field="alpaca_key")
    secret = decrypt_required(brokerage.get("alpaca_secret"), field="alpaca_secret")
    base = brokerage.get("alpaca_base_url")
    if type(base) is not str or not base.startswith("https://"):
        raise RuntimeError("Alpaca endpoint is unavailable")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    root = base.rstrip("/") + "/v2"
    account = _get_json(root + "/account", headers)
    account_id = account.get("account_number") if type(account) is dict else None
    linked = brokerage.get("alpaca_account_number")
    if not isinstance(account_id, str) or not account_id or account_id != linked:
        raise RuntimeError("Alpaca account does not match linked brokerage")
    return {
        "account_id": account_id,
        "positions": _get_json(root + "/positions", headers),
        "open_orders": _get_json(root + "/orders?status=open&limit=500", headers),
        "recent_orders": _get_json(root + "/orders?status=all&limit=500", headers),
        "recent_trades": _get_json(root + "/account/activities/FILL?direction=desc&page_size=100", headers),
    }

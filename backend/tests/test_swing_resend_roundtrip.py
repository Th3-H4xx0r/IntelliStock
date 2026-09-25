"""FW item 3: a re-sent approval, end to end. The re-send route queues the
same submit_order payload the decision route queued, and plan A-live's
approval handler claims approved -> submitted before it sends anything. So
however many copies reach the broker (a re-send racing a late original, a
double re-send from two devices), one approval places one order.

The route runs over real HTTP (TestClient) with the REAL live_state queue
on the FakeStore; every queued payload is then fed to the REAL handler from
broker.py (test_swing_approval_roundtrip's harness)."""
import datetime
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import interactive_utils  # noqa: E402
import live_state  # noqa: E402
from swing_trader import signals_store  # noqa: E402
from test_swing_approval_roundtrip import (  # noqa: E402
    IID, Adapter, Service, command, operator, pending,
)


@pytest.fixture
def api(store, monkeypatch, notices):
    from fastapi.testclient import TestClient

    from api import main

    monkeypatch.setattr(interactive_utils, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(live_state, "store", store)
    monkeypatch.setattr(live_state, "ensure_tables", lambda r=None, conn=None: None)
    store.insert("Instances", [{"id": IID, "name": IID, "runCommand": True}])
    # Follow-up 3: a re-send needs the signal's session (the harness's
    # 2026-09-28) to be today in New York.
    monkeypatch.setattr(interactive_utils, "_ny_today",
                        lambda now=None: datetime.date(2026, 9, 28))
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "u1",
                                                                    "username": "pranav"}
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        main.app.dependency_overrides.pop(main.get_current_user, None)


@pytest.fixture
def notices(monkeypatch):
    from swing_trader import notify

    sent = []
    monkeypatch.setattr(notify, "notify_swing_approval_failed",
                        lambda instance_id, **fields: sent.append((instance_id, fields)))
    return sent


def queued_payloads(store):
    rows = store.run(store.filter(live_state.LIVE_COMMANDS_TABLE,
                                  store.P.field("instance_id").eq(IID)))
    return [r["payload"] for r in rows]


def test_a_resend_racing_the_original_places_one_order(api, store):
    sid = pending()
    operator(sid, "approve")
    # The original approval's command is queued but has not run yet; a
    # finished copy of it (the broker restarted) would not block the re-send.
    original = live_state.submit_command(None, None, instance_id=IID, type="submit_order",
                                         payload={"source": "swing_approval",
                                                  "signal_id": sid})
    store.update(live_state.LIVE_COMMANDS_TABLE, original,
                 {"status": "reconciliation_required"})
    assert api.post(f"/instances/{IID}/swing/signals/{sid}/resend").status_code == 200
    payloads = queued_payloads(store)
    assert payloads == [{"source": "swing_approval", "signal_id": sid}] * 2

    service = Service()
    results = [command(service, Adapter(), p["signal_id"]) for p in payloads]
    assert [ok for ok, _e, _r in results] == [True, False]
    assert "not approved" in results[1][1]
    assert len(service.intents) == 1
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_a_double_resend_from_two_devices_places_one_order(api, store):
    sid = pending()
    operator(sid, "approve")
    url = f"/instances/{IID}/swing/signals/{sid}/resend"
    assert api.post(url).status_code == 200
    # The second device's click lands while the first copy is queued: refused.
    assert api.post(url).status_code == 409
    # Even if both had been queued, the claim lets only one through.
    live_state.submit_command(None, None, instance_id=IID, type="submit_order",
                              payload={"source": "swing_approval", "signal_id": sid})
    service = Service()
    oks = [command(service, Adapter(), p["signal_id"])[0] for p in queued_payloads(store)]
    assert oks == [True, False] and len(service.intents) == 1
    # Once submitted, there is nothing left to re-send.
    again = api.post(url)
    assert again.status_code == 409 and "submitted" in again.json()["detail"]

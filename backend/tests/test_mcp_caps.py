"""Caps on MCP-principal calls (plan Task 15, contract §7, spec §6).

On the MCP listener a call can be refused with a 429 {"detail", "retry_after_s"}
and the same integer in Retry-After, audited "capped": past the per-minute call
window, past the per-minute write window (non-read calls), while a backtest
already holds the one backtest slot (create_backtest), past the daily cost-route
cap (routes marked cost), or, for a call about to be held, past the daily
approval-request cap. The daily counts are global and survive a restart: they
re-seed from McpAudit and McpApprovals at the first check of each UTC day.
Checks fail closed. Idempotent replays, approved replays, service-level routes
and the public listener never touch a cap.

Each test gets a fresh McpCaps on an injected clock, installed as mcp_caps.caps.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import Body, HTTPException

import apns_sender
import mcp_approvals
import mcp_audit
import mcp_caps
import notifications
from mcp_caps import BACKTEST_START_OPERATIONS, CapRefusal, McpCaps, running_backtest_count
from mcp_helpers import (
    IDEM, human_headers, make_probe_app, mcp_client, mcp_env, p256_keypair, public_client,
    seed_money_fixture, seed_operator, sign_decision, use_store,
)
from mcp_policy import McpConfig, iso_utc, mcp_policy

requires_pg = pytest.mark.skipif(not os.environ.get("PG_TEST_DSN"),
                                 reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

LIVE_ACT = "/probe/instances/alpaca-main/act"
# One hour before UTC midnight.
LATE = datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)
CALL_RATE = "mcp: call rate cap reached"
WRITE_RATE = "mcp: write rate cap reached"
COST = "mcp: daily cost-route cap reached"
APPROVALS = "mcp: daily approval-request cap reached"
ONE_BACKTEST = "mcp: a backtest is already running (one at a time)"


class Clock:
    """The caps' clock: epoch seconds, moved by hand."""

    def __init__(self, start: datetime) -> None:
        self.now = start.timestamp()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.now, timezone.utc)


@pytest.fixture(autouse=True)
def _mcp(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    seed_money_fixture(store)
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())              # never reach APNs
    monkeypatch.setattr(notifications, "notify", Mock())                  # never reach Discord
    monkeypatch.setattr(mcp_approvals, "notify_approval_async", Mock())   # no push thread
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 0)   # FakeStore has no SQL


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """A fresh McpCaps on a clock at LATE, as the gate's singleton. Audit and
    approval stamps follow the same clock, so the day a row counts for is the
    caps' day whatever date the suite runs on."""
    clk = Clock(LATE)
    monkeypatch.setattr(mcp_caps, "caps", McpCaps(clock=clk))
    monkeypatch.setattr(mcp_audit, "utc_now", lambda: clk.dt)
    monkeypatch.setattr(mcp_approvals, "utc_now", lambda: clk.dt)
    return clk


@pytest.fixture
def probe(clock):
    app, calls = make_probe_app()
    return app, mcp_client(app), calls


def _cfg(**env: str) -> McpConfig:
    return McpConfig.from_env(env)


def _outcomes(store) -> list[tuple]:
    return [(r["outcome"], r["status"]) for r in store.run("McpAudit")]


def _capped(res, detail: str, retry_after_s: int | None = None) -> int:
    """Assert a cap's 429 (body, Retry-After); its retry_after_s."""
    assert res.status_code == 429, res.text
    body = res.json()
    assert set(body) == {"detail", "retry_after_s"} and body["detail"] == detail
    assert res.headers["Retry-After"] == str(body["retry_after_s"])
    if retry_after_s is not None:
        assert body["retry_after_s"] == retry_after_s
    return body["retry_after_s"]


# -- per-minute windows -------------------------------------------------------------

def test_call_rate_cap(monkeypatch, probe, clock, store):
    mcp_env(monkeypatch, MCP_CAP_CALLS_PER_MIN="2")
    _, c, calls = probe
    assert c.get("/probe/read").status_code == 200
    clock.advance(10)
    assert c.get("/probe/read").status_code == 200
    clock.advance(5)
    res = c.get("/probe/read")
    assert res.status_code == 429 and res.headers["Retry-After"] == str(res.json()["retry_after_s"])
    assert 1 <= res.json()["retry_after_s"] <= 60
    _capped(res, CALL_RATE, 45)                  # the oldest call leaves the window 45 s from now
    assert calls["count"]["probe_read"] == 2
    assert _outcomes(store) == [("ok", 200), ("ok", 200), ("capped", 429)]
    assert store.run("McpAudit")[-1]["operation_id"] == "probe_read"
    clock.advance(45)
    assert c.get("/probe/read").status_code == 200


def test_write_rate_cap(monkeypatch, probe, clock, store):
    mcp_env(monkeypatch, MCP_CAP_WRITES_PER_MIN="1")
    _, c, calls = probe
    assert c.post("/probe/write", json={"n": 1}, headers=IDEM()).status_code == 200
    clock.advance(20)
    retry = {"Idempotency-Key": "k-retry"}
    _capped(c.post("/probe/write", json={"n": 2}, headers=retry), WRITE_RATE, 40)
    assert c.get("/probe/read").status_code == 200 and c.get("/probe/read").status_code == 200
    assert calls["count"]["probe_write"] == 1
    # The refused write claimed nothing: the same key runs once the window frees.
    assert store.run("McpIdempotency") and all(r["key"] != "k-retry" for r in store.run("McpIdempotency"))
    clock.advance(40)
    res = c.post("/probe/write", json={"n": 2}, headers=retry)
    assert res.status_code == 200 and "X-MCP-Idempotent-Replay" not in res.headers
    assert calls["count"]["probe_write"] == 2


def test_money_and_irreversible_calls_count_as_writes(monkeypatch, probe):
    mcp_env(monkeypatch, MCP_CAP_WRITES_PER_MIN="2")
    _, c, calls = probe
    assert c.post("/probe/instances/paper-1/act", json={"q": 1}, headers=IDEM()).status_code == 200
    assert c.delete("/probe/things/1", headers=IDEM()).status_code == 202
    _capped(c.post("/probe/write", json={}, headers=IDEM()), WRITE_RATE)
    assert calls["count"].get("probe_write", 0) == 0


def test_rate_window_unit():
    clk = Clock(LATE)
    caps = McpCaps(clock=clk)
    cfg = _cfg(MCP_CAP_CALLS_PER_MIN="3", MCP_CAP_WRITES_PER_MIN="1")
    assert caps.check_rate(non_read=True, cfg=cfg) is None
    clk.advance(0.5)
    # The write window is full; a refusal reserves nothing, so the call slot stays free.
    assert caps.check_rate(non_read=True, cfg=cfg) == CapRefusal(WRITE_RATE, 60)
    assert caps.check_rate(non_read=False, cfg=cfg) is None
    assert caps.check_rate(non_read=False, cfg=cfg) is None
    assert caps.check_rate(non_read=False, cfg=cfg) == CapRefusal(CALL_RATE, 60)   # ceil(59.5)
    clk.advance(59.5)                                        # exactly 60 s after the first call
    assert caps.check_rate(non_read=True, cfg=cfg) is None
    # A cap of 0 refuses everything; with nothing in the window the wait is the window.
    fresh = McpCaps(clock=clk)
    assert fresh.check_rate(non_read=False, cfg=_cfg(MCP_CAP_CALLS_PER_MIN="0")) == CapRefusal(CALL_RATE, 60)


def test_rate_cap_holds_under_concurrency():
    caps = McpCaps(clock=Clock(LATE))
    cfg = _cfg(MCP_CAP_CALLS_PER_MIN="10")
    results: list = []
    barrier = threading.Barrier(25)

    def call() -> None:
        barrier.wait()
        results.append(caps.check_rate(non_read=False, cfg=cfg))

    threads = [threading.Thread(target=call) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(None) == 10 and len(results) == 25


# -- the daily cost-route cap ----------------------------------------------------

def test_cost_cap_resets_at_utc_midnight(monkeypatch, probe, clock, store):
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="1")
    _, c, calls = probe
    first = c.post("/probe/cost", json={}, headers=IDEM())
    assert first.status_code == 200
    second = c.post("/probe/cost", json={}, headers=IDEM())
    assert second.json()["retry_after_s"] == 3600
    _capped(second, COST, 3600)
    assert c.post("/probe/write", json={}, headers=IDEM()).status_code == 200   # not a cost route
    assert calls["count"]["probe_cost"] == 1
    clock.advance(3600)                                      # 2026-11-20T00:00:00Z
    assert c.post("/probe/cost", json={}, headers=IDEM()).status_code == 200
    assert calls["count"]["probe_cost"] == 2
    rows = store.run("McpAudit")
    assert [(r["operation_id"], r["outcome"], r.get("cap_bucket")) for r in rows] == [
        ("probe_cost", "ok", "cost"), ("probe_cost", "capped", None),
        ("probe_write", "ok", None), ("probe_cost", "ok", "cost")]


def test_cost_cap_survives_restart(store, monkeypatch, probe, clock):
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="1")
    _, c, calls = probe
    store.insert("McpAudit", {"id": "a-earlier", "ts": iso_utc(clock.dt - timedelta(hours=2)),
                              "outcome": "ok", "status": 200, "cap_bucket": "cost"})
    _capped(c.post("/probe/cost", json={}, headers=IDEM()), COST, 3600)
    assert calls["count"].get("probe_cost", 0) == 0


def test_cost_seed_counts_only_todays_cost_rows(store, monkeypatch, probe, clock):
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="2")
    _, c, calls = probe
    midnight = datetime(2026, 11, 19, tzinfo=timezone.utc)
    store.insert("McpAudit", [
        {"id": "a-today", "ts": iso_utc(midnight), "outcome": "ok", "cap_bucket": "cost"},
        {"id": "a-yesterday", "ts": iso_utc(midnight - timedelta(microseconds=1)), "outcome": "ok",
         "cap_bucket": "cost"},
        {"id": "a-plain", "ts": iso_utc(clock.dt), "outcome": "ok"},
    ])
    assert c.post("/probe/cost", json={}, headers=IDEM()).status_code == 200
    _capped(c.post("/probe/cost", json={}, headers=IDEM()), COST)
    assert calls["count"]["probe_cost"] == 1


def test_daily_count_is_seeded_once_per_utc_day(monkeypatch, store):
    clk = Clock(LATE)
    caps = McpCaps(clock=clk)
    cfg = _cfg(MCP_CAP_COST_PER_DAY="5")
    seen: list = []
    real_count = store.count
    monkeypatch.setattr(store, "count", lambda sel: seen.append(sel) or real_count(sel), raising=False)
    for _ in range(3):
        assert caps.check_cost(cfg) is None
    assert len(seen) == 1
    clk.advance(3600)
    assert caps.check_cost(cfg) is None and len(seen) == 2


def test_daily_seed_failure_fails_closed_then_retries(monkeypatch, store):
    caps = McpCaps(clock=Clock(LATE))
    cfg = _cfg(MCP_CAP_COST_PER_DAY="5", MCP_CAP_APPROVALS_PER_DAY="5")
    real_count = store.count
    broken = {"on": True}

    def count(sel):
        if broken["on"]:
            raise RuntimeError("store down")
        return real_count(sel)

    monkeypatch.setattr(store, "count", count, raising=False)
    assert caps.check_cost(cfg) == CapRefusal("mcp: daily cost-route count unavailable", 60)
    assert caps.check_approvals(cfg) == CapRefusal("mcp: daily approval-request count unavailable", 60)
    broken["on"] = False
    assert caps.check_cost(cfg) is None and caps.check_approvals(cfg) is None


def test_daily_cap_holds_under_concurrency():
    caps = McpCaps(clock=Clock(LATE))
    cfg = _cfg(MCP_CAP_COST_PER_DAY="5")
    results: list = []
    barrier = threading.Barrier(20)

    def call() -> None:
        barrier.wait()
        results.append(caps.check_cost(cfg))

    threads = [threading.Thread(target=call) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(None) == 5 and len(results) == 20


# -- the daily approval-request cap ------------------------------------------------

def test_approvals_cap(monkeypatch, probe, clock, store):
    mcp_env(monkeypatch, MCP_CAP_APPROVALS_PER_DAY="1")
    _, c, calls = probe
    assert c.post(LIVE_ACT, json={"qty": 1}, headers=IDEM()).status_code == 202
    _capped(c.post(LIVE_ACT, json={"qty": 2}, headers=IDEM()), APPROVALS, 3600)
    assert len(store.run("McpApprovals")) == 1 and calls["count"].get("probe_act", 0) == 0
    assert mcp_approvals.notify_approval_async.call_count == 1
    capped = store.run("McpAudit")[-1]
    assert (capped["outcome"], capped["status"], capped["money_state"]) == ("capped", 429, "live")
    # Calls that are not held never meet this cap.
    assert c.post("/probe/instances/paper-1/act", json={"qty": 1}, headers=IDEM()).status_code == 200


def test_approvals_cap_refusal_is_not_stored_as_the_keys_response(monkeypatch, probe, clock, store):
    """The cap is checked after the key's claim, so a refusal releases the key:
    a retry with the same key is checked again, never replayed as a 429."""
    mcp_env(monkeypatch, MCP_CAP_APPROVALS_PER_DAY="1")
    _, c, _ = probe
    assert c.post(LIVE_ACT, json={"qty": 1}, headers=IDEM()).status_code == 202
    key = {"Idempotency-Key": "k-held"}
    _capped(c.post(LIVE_ACT, json={"qty": 2}, headers=key), APPROVALS)
    assert all(r["key"] != "k-held" for r in store.run("McpIdempotency"))
    again = c.post(LIVE_ACT, json={"qty": 2}, headers=key)
    _capped(again, APPROVALS)
    assert "X-MCP-Idempotent-Replay" not in again.headers
    clock.advance(3600)
    held = c.post(LIVE_ACT, json={"qty": 2}, headers=key)
    assert held.status_code == 202 and "X-MCP-Idempotent-Replay" not in held.headers
    assert len(store.run("McpApprovals")) == 2
    replay = c.post(LIVE_ACT, json={"qty": 2}, headers=key)        # now the 202 is the key's response
    assert replay.json() == held.json() and replay.headers["X-MCP-Idempotent-Replay"] == "true"


def test_a_failed_approvals_check_releases_the_key(monkeypatch, probe, store):
    """A check that raises (it should not) still leaves one audit row and frees
    the claimed key, so the retry is not a 409 for a day."""
    app, _, calls = probe

    def broken(cfg):
        raise RuntimeError("bug")

    monkeypatch.setattr(mcp_caps.caps, "check_approvals", broken)
    res = mcp_client(app, raise_server_exceptions=False).post(LIVE_ACT, json={"qty": 1},
                                                              headers={"Idempotency-Key": "k-bug"})
    assert res.status_code == 500 and store.run("McpApprovals") == []
    assert store.run("McpIdempotency") == [] and _outcomes(store) == [("error", 500)]


def test_approvals_cap_survives_restart(monkeypatch, probe, clock, store):
    mcp_env(monkeypatch, MCP_CAP_APPROVALS_PER_DAY="1")
    _, c, _ = probe
    store.insert("McpApprovals", {"id": "apr_" + "0" * 32, "status": "denied",
                                  "created_at": iso_utc(clock.dt - timedelta(hours=1))})
    _capped(c.delete("/probe/things/1", headers=IDEM()), APPROVALS, 3600)
    assert len(store.run("McpApprovals")) == 1


# -- the backtest slot -----------------------------------------------------------

def test_one_backtest_at_a_time(monkeypatch, probe, store):
    _, c, calls = probe
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 1)
    res = c.post("/probe/backtests", json={}, headers=IDEM())
    assert res.status_code == 429 and "one at a time" in res.json()["detail"]
    _capped(res, ONE_BACKTEST, 60)
    assert calls["count"].get("create_backtest", 0) == 0
    assert _outcomes(store) == [("capped", 429)]
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 0)
    assert c.post("/probe/backtests", json={}, headers=IDEM()).status_code == 200
    assert BACKTEST_START_OPERATIONS == frozenset({"create_backtest"})


def test_backtest_count_failure_fails_closed(monkeypatch, probe):
    _, c, calls = probe

    def broken() -> int:
        raise RuntimeError("store down")

    monkeypatch.setattr(mcp_caps, "running_backtest_count", broken)
    _capped(c.post("/probe/backtests", json={}, headers=IDEM()), ONE_BACKTEST, 60)
    assert calls["count"].get("create_backtest", 0) == 0


def test_the_backtest_slot_only_gates_create_backtest(monkeypatch, probe):
    _, c, calls = probe
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 5)
    assert c.post("/probe/cost", json={}, headers=IDEM()).status_code == 200
    assert c.post("/probe/write", json={}, headers=IDEM()).status_code == 200


def test_a_refused_backtest_spends_no_daily_cost(monkeypatch, probe, store):
    """The slot is checked before the cost cap: an agent retrying while a long
    backtest runs must not burn the day's cost-route budget."""
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="1")
    _, c, calls = probe
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 1)
    for _ in range(3):
        _capped(c.post("/probe/backtests", json={}, headers=IDEM()), ONE_BACKTEST)
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 0)
    assert c.post("/probe/backtests", json={}, headers=IDEM()).status_code == 200
    assert calls["count"]["create_backtest"] == 1
    assert [r.get("cap_bucket") for r in store.run("McpAudit")] == [None, None, None, "cost"]


def test_backtest_slot_unit(monkeypatch):
    clk = Clock(LATE)
    caps = McpCaps(clock=clk)
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 1)
    assert caps.check_backtest_slot(_cfg()) == CapRefusal(ONE_BACKTEST, 60)        # one already runs
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="2")) is None  # a refusal held nothing
    # A granted slot is held: the next start waits for release or the backstop.
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="2")) == CapRefusal(ONE_BACKTEST, 60)
    caps.release_backtest_slot()
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="2")) is None
    clk.advance(mcp_caps.BACKTEST_HOLD_S - 1)
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="2")) == CapRefusal(ONE_BACKTEST, 60)
    clk.advance(1)
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="2")) is None
    caps.release_backtest_slot()
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 0)
    assert caps.check_backtest_slot(_cfg(MCP_CAP_CONCURRENT_BACKTESTS="0")) == CapRefusal(ONE_BACKTEST, 60)
    assert mcp_caps.BACKTEST_HOLD_S == 300


def test_two_concurrent_backtest_starts_one_passes(probe):
    """Neither start has a queue row yet (the count reads 0 throughout): the
    slot is held from the grant until the first route returns."""
    app, c, _ = probe
    entered, finish = threading.Event(), threading.Event()
    ran: list = []

    @app.post("/probe/backtests/slow", operation_id="create_backtest",
              openapi_extra=mcp_policy("write", cost=True))
    def slow_backtest(body: dict = Body(default=None)):
        ran.append(1)
        entered.set()
        finish.wait(10)
        return {"id": 1}

    first: dict = {}
    worker = threading.Thread(target=lambda: first.setdefault(
        "res", mcp_client(app).post("/probe/backtests/slow", json={}, headers=IDEM())))
    worker.start()
    try:
        assert entered.wait(10)
        second = c.post("/probe/backtests/slow", json={}, headers=IDEM())
    finally:
        finish.set()
        worker.join(10)
    _capped(second, ONE_BACKTEST, 60)
    assert first["res"].status_code == 200 and len(ran) == 1


def test_a_start_arriving_during_the_count_is_refused(monkeypatch):
    caps = McpCaps(clock=Clock(LATE))
    counting, finish = threading.Event(), threading.Event()

    def slow_count() -> int:
        counting.set()
        finish.wait(10)
        return 0

    monkeypatch.setattr(mcp_caps, "running_backtest_count", slow_count)
    out: dict = {}
    worker = threading.Thread(target=lambda: out.setdefault("first", caps.check_backtest_slot(_cfg())))
    worker.start()
    try:
        assert counting.wait(10)
        second = caps.check_backtest_slot(_cfg())
    finally:
        finish.set()
        worker.join(10)
    assert second == CapRefusal(ONE_BACKTEST, 60) and out["first"] is None


def test_the_slot_is_released_when_the_route_returns(monkeypatch, probe):
    app, c, calls = probe

    @app.post("/probe/backtests/bad", operation_id="create_backtest",
              openapi_extra=mcp_policy("write", cost=True))
    def bad_backtest(body: dict = Body(default=None)):
        raise HTTPException(status_code=400, detail="bad dates")

    @app.post("/probe/backtests/boom", operation_id="create_backtest",
              openapi_extra=mcp_policy("write", cost=True))
    def boom_backtest(body: dict = Body(default=None)):
        raise RuntimeError("boom")

    assert c.post("/probe/backtests", json={}, headers=IDEM()).status_code == 200
    assert c.post("/probe/backtests/bad", json={}, headers=IDEM()).status_code == 400
    assert mcp_client(app, raise_server_exceptions=False).post(
        "/probe/backtests/boom", json={}, headers=IDEM()).status_code == 500
    assert c.post("/probe/backtests", json={}, headers=IDEM()).status_code == 200
    assert calls["count"]["create_backtest"] == 2
    # Now the queue row of a created backtest is what refuses the next start.
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 1)
    _capped(c.post("/probe/backtests", json={}, headers=IDEM()), ONE_BACKTEST)


def test_a_later_refusal_releases_the_slot(monkeypatch, probe):
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="0")
    _, c, calls = probe
    _capped(c.post("/probe/backtests", json={}, headers=IDEM()), COST)
    mcp_env(monkeypatch, MCP_CAP_COST_PER_DAY="1")
    assert c.post("/probe/backtests", json={}, headers=IDEM()).status_code == 200
    assert calls["count"]["create_backtest"] == 1


def test_running_backtest_count_counts_queue_rows_once(store, monkeypatch):
    """A backtest is in BacktestInstances (the queue) from create until the
    engine deletes the row, and in BacktestProgress once it runs."""
    if os.environ.get("PG_TEST_DSN"):
        pytest.skip("FakeStore only: the PG variant runs the real SQL")
    monkeypatch.setattr(store, "sql", lambda query, params=(): [{"id": "901"}, {"id": "950"}])
    store.insert("BacktestInstances", [
        {"id": 901, "status": "running"}, {"id": 902, "status": "pending"},
        {"id": 903, "status": "starting"}, {"id": 904, "status": "done"}, {"id": 905},
    ])
    assert running_backtest_count() == 4             # 901, 902, 903, 950


def test_running_backtest_count_without_sql_raises():
    """On the FakeStore (no SQL engine) the real count raises, so the slot refuses."""
    if os.environ.get("PG_TEST_DSN"):
        pytest.skip("FakeStore only")
    with pytest.raises(NotImplementedError):
        running_backtest_count()            # the real one; the autouse fixture patched the module's


@requires_pg
def test_running_backtest_count_on_postgres(store):
    statuses = ["running", "queued", "pending", "paused", "paused_by_user", "completed", "error",
                "stopped", "Running"]
    for n, status in enumerate(statuses):
        store.sql('INSERT INTO "BacktestProgress" (id, payload) VALUES (%s, %s::jsonb)',
                  (str(900 + n), '{"status": "%s"}' % status))
    store.sql('INSERT INTO "BacktestProgress" (id, payload) VALUES (%s, %s::jsonb)', ("999", "{}"))
    assert running_backtest_count() == 5
    store.insert("BacktestInstances", [
        {"id": 900, "status": "running"},           # also in BacktestProgress: counted once
        {"id": 1001, "status": "pending"}, {"id": 1002, "status": "starting"},
        {"id": 1003, "status": "done"}, {"id": 1004},
    ])
    assert running_backtest_count() == 7


# -- what never touches a cap ----------------------------------------------------

def test_idempotent_replay_does_not_consume_caps(monkeypatch, probe):
    mcp_env(monkeypatch, MCP_CAP_CALLS_PER_MIN="2", MCP_CAP_WRITES_PER_MIN="1", MCP_CAP_COST_PER_DAY="1")
    _, c, calls = probe
    key = {"Idempotency-Key": "k-cost"}
    assert c.post("/probe/cost", json={"n": 1}, headers=key).status_code == 200
    for _ in range(3):
        res = c.post("/probe/cost", json={"n": 1}, headers=key)
        assert res.status_code == 200 and res.headers["X-MCP-Idempotent-Replay"] == "true"
    assert calls["count"]["probe_cost"] == 1
    assert c.get("/probe/read").status_code == 200                 # the second call of the window
    _capped(c.get("/probe/read"), CALL_RATE)


def test_public_listener_is_never_capped(monkeypatch, probe):
    mcp_env(monkeypatch, MCP_CAP_CALLS_PER_MIN="1", MCP_CAP_WRITES_PER_MIN="1", MCP_CAP_COST_PER_DAY="1")
    app, _, calls = probe
    human = public_client(app)
    headers = human_headers(monkeypatch)
    for _ in range(3):
        assert human.get("/whoami", headers=headers).status_code == 200
        assert human.post("/probe/cost", json={}, headers=headers).status_code == 200
    assert calls["count"] == {"whoami": 3, "probe_cost": 3}


def test_service_level_routes_are_never_capped(monkeypatch, probe):
    mcp_env(monkeypatch, MCP_CAP_CALLS_PER_MIN="0")
    app, c, _ = probe
    app.add_api_route("/health", lambda: {"status": "ok"}, methods=["GET"])
    for _ in range(3):
        assert c.get("/health").status_code == 200
    _capped(c.get("/probe/read"), CALL_RATE, 60)


def test_approved_replay_is_never_capped(monkeypatch, probe, clock, store):
    """An approved call was counted when it was held; its in-process replay
    passes even with every window and daily cap spent."""
    mcp_env(monkeypatch, MCP_CAP_CALLS_PER_MIN="1", MCP_CAP_WRITES_PER_MIN="1",
            MCP_CAP_APPROVALS_PER_DAY="1")
    app, c, calls = probe
    held = c.post(LIVE_ACT, json={"qty": 1}, headers=IDEM())
    assert held.status_code == 202
    _capped(c.get("/probe/read"), CALL_RATE)
    key, pub = p256_keypair()
    device = mcp_approvals.register_device(user={"id": "u-op", "username": "operator"}, name="iPhone",
                                           public_key_b64=pub, now=clock.dt - timedelta(seconds=601))
    row = store.get("McpApprovals", held.json()["pending_approval"]["id"])
    ts = int(clock.now)
    payload = {"decision": "approve", "body_sha256": row["body_sha256"], "ts": ts, "device_id": device["id"],
               "signature": sign_decision(key, row["id"], row["body_sha256"], "approve", ts)}
    res = public_client(app).post(f"/mcp/approvals/{row['id']}/decision", json=payload,
                                  headers=human_headers(monkeypatch))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "executed" and calls["count"]["probe_act"] == 1
    assert store.run("McpAudit")[-1]["outcome"] == "executed_approved"

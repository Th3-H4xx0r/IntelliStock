"""The operator can waive the live-readiness gate, loudly and on the record.

``instance.py:_assert_live_broker_start_allowed`` will not spawn a funded
broker without ``Instances.<id>.live_readiness_report`` -- a fingerprinted,
artifact-bound report with all six checks passed and state LIVE_ELIGIBLE.
Nothing in the API could write one, by design: the report is supposed to be
earned. When the repo owner decides to accept the risk anyway, the choice
between "hand-edit the JSON blob a real-money launcher reads" and "an audited
route that records who waived what against which image" is not a close one.

The route is open to any *signed-in* user (operator decision 2026-09-11): the
phrase and the reason are the gate, and the audit record names whoever typed
them. Anonymous callers are still refused.

So the waiver is a route, and these tests pin the properties that make it
safer than the database write it replaces:

* the confirm phrase names the instance, so a waiver cannot be aimed at the
  wrong one by a copy-pasted body;
* the artifact hash is computed server-side, never accepted from the caller --
  a client-supplied hash would let a waiver written today authorize an image
  built tomorrow;
* the report it writes is exactly what the launcher parses, and the launcher
  still refuses it against any *other* image.
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

ADMIN = {"id": "admin-1", "username": "root", "role": "admin"}
BOB = {"id": "bob-1", "username": "bob", "role": "user"}

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
INSTANCE = "alpaca-main"
CONFIRM = f"WAIVE LIVE GATE {INSTANCE}"
REASON = "EB is engine-proven and I accept the live risk on a $6k sleeve."


@pytest.fixture
def waiver(store, monkeypatch):
    """TestClient + fake store + fake digest lookup. No Docker, no network."""
    from fastapi.testclient import TestClient

    from api import main
    import live_alerts

    store.insert("Instances", {"id": INSTANCE, "name": "EB live"})

    alerts: list = []
    monkeypatch.setattr(main, "db_store", store)
    monkeypatch.setattr(main, "deployed_artifact_digest", lambda: DIGEST)
    monkeypatch.setattr(
        live_alerts, "alert_strategy_error",
        lambda **kw: alerts.append(kw))

    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        yield TestClient(main.app), store, alerts
    finally:
        main.app.dependency_overrides.clear()


def _post(client, **body):
    payload = {"confirm": CONFIRM, "reason": REASON}
    payload.update(body)
    return client.post(f"/instances/{INSTANCE}/readiness-waiver", json=payload)


# --- refusals -------------------------------------------------------------


@pytest.mark.parametrize("confirm", [
    "",
    "WAIVE LIVE GATE",
    "WAIVE LIVE GATE alpaca-paper-pit",   # the wrong instance
    "waive live gate alpaca-main",        # not exact
    "WAIVE LIVE GATE alpaca-main ",       # not exact
])
def test_a_confirm_phrase_that_is_not_exact_is_refused(waiver, confirm):
    client, store, _alerts = waiver
    res = _post(client, confirm=confirm)
    assert res.status_code == 400, res.text
    assert store.get("Instances", INSTANCE).get("live_readiness_report") is None


def test_a_short_reason_is_refused(waiver):
    """The reason is the whole audit record. Twenty characters is not much of
    a bar, but "ok" is not a justification for starting a real-money broker."""
    client, store, _alerts = waiver
    res = _post(client, reason="looks fine to me")  # 16 chars
    assert res.status_code == 400, res.text
    assert store.get("Instances", INSTANCE).get("live_readiness_report") is None


def test_an_unknown_instance_is_a_404(waiver):
    client, _store, _alerts = waiver
    res = client.post(
        "/instances/not-a-real-instance/readiness-waiver",
        json={"confirm": "WAIVE LIVE GATE not-a-real-instance",
              "reason": REASON})
    assert res.status_code == 404, res.text


def test_a_signed_in_non_admin_may_waive_and_is_named_on_the_row(store, monkeypatch):
    """Operator decision 2026-09-11: the role is not the gate -- the typed
    phrase and the reason are, and the audit record has to name whoever typed
    them, not the role they happened to hold."""
    from fastapi.testclient import TestClient

    from api import main
    import live_alerts

    store.insert("Instances", {"id": INSTANCE, "name": "EB live"})
    monkeypatch.setattr(main, "db_store", store)
    monkeypatch.setattr(main, "deployed_artifact_digest", lambda: DIGEST)
    monkeypatch.setattr(live_alerts, "alert_strategy_error", lambda **kw: None)

    main.app.dependency_overrides[main.get_current_user] = lambda: BOB
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = TestClient(main.app).post(
            f"/instances/{INSTANCE}/readiness-waiver",
            json={"confirm": CONFIRM, "reason": REASON})
    finally:
        main.app.dependency_overrides.clear()

    assert res.status_code == 200, res.text
    assert res.json()["waived_by"] == "bob"
    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_waived_by"] == "bob"
    for check in row["live_readiness_report"]["checks"]:
        assert " by bob: " in check["reason"]


def test_an_unauthenticated_caller_still_cannot_waive(store, monkeypatch):
    """Open to any signed-in user is not open to anyone: without a usable
    token ``get_current_user`` refuses before the route body runs."""
    from fastapi.testclient import TestClient

    from api import main

    store.insert("Instances", {"id": INSTANCE})
    monkeypatch.setattr(main, "db_store", store)
    monkeypatch.setattr(main, "deployed_artifact_digest", lambda: DIGEST)

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = TestClient(main.app).post(
            f"/instances/{INSTANCE}/readiness-waiver",
            json={"confirm": CONFIRM, "reason": REASON},
            headers={"Authorization": "Bearer not-a-real-token"})
    finally:
        main.app.dependency_overrides.clear()
    assert res.status_code == 401, res.text
    assert store.get("Instances", INSTANCE).get("live_readiness_report") is None


def test_the_route_is_wired_to_the_authenticated_user_dependency():
    """test_api_authorization.py classifies this route in its exemption table;
    this pins the wiring even if the entry is dropped from it -- in either
    direction, since re-gating it on admin would also fail here."""
    import inspect

    from api import main

    endpoint = next(
        route.endpoint for route in main.app.routes
        if getattr(route, "path", None)
        == "/instances/{instance_id}/readiness-waiver"
        and "POST" in getattr(route, "methods", ()))
    guard = inspect.signature(endpoint).parameters["current_user"].default
    assert guard.dependency is main.get_current_user


# --- the happy path -------------------------------------------------------


def test_the_waiver_writes_a_report_the_launcher_accepts(waiver):
    from live_readiness import (assert_live_start_allowed, report_from_mapping,
                                required_live_checks)

    client, store, _alerts = waiver
    res = _post(client)
    assert res.status_code == 200, res.text

    row = store.get("Instances", INSTANCE)
    report = report_from_mapping(
        row["live_readiness_report"], instance_id=INSTANCE,
        verify_fingerprint=True)
    # Exactly the call instance.py makes immediately before it spawns a
    # real-money broker.
    assert_live_start_allowed(report, deployed_artifact_hash=DIGEST)
    assert {check.name for check in report.checks} == set(required_live_checks())


def test_the_report_is_bound_to_the_image_it_was_written_against(waiver):
    """The binding is the point: a waiver is for the artifact on the host
    today, not for whatever gets built next."""
    from live_readiness import (LiveReadinessError, assert_live_start_allowed,
                                report_from_mapping)

    client, store, _alerts = waiver
    assert _post(client).status_code == 200

    report = report_from_mapping(
        store.get("Instances", INSTANCE)["live_readiness_report"],
        instance_id=INSTANCE)
    with pytest.raises(LiveReadinessError):
        assert_live_start_allowed(report, deployed_artifact_hash=OTHER_DIGEST)


def test_the_artifact_hash_is_never_taken_from_the_caller(waiver):
    client, store, _alerts = waiver
    res = _post(client, artifact_hash=OTHER_DIGEST)
    assert res.status_code == 200, res.text
    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_report"]["artifact_hash"] == DIGEST
    assert res.json()["artifact_hash"] == DIGEST


def test_every_reason_says_it_was_waived_and_by_whom(waiver):
    client, store, _alerts = waiver
    assert _post(client).status_code == 200

    row = store.get("Instances", INSTANCE)
    checks = row["live_readiness_report"]["checks"]
    assert len(checks) == 6
    today = datetime.now(timezone.utc).date().isoformat()
    for check in checks:
        assert check["passed"] is True
        assert check["reason"].startswith(f"OPERATOR WAIVED {today} by root: ")
        assert REASON in check["reason"]
        assert check["evidence_hash"] == hashlib.sha256(
            check["reason"].encode("utf-8")).hexdigest()


def test_who_waived_and_when_are_recorded_on_the_row(waiver):
    client, store, _alerts = waiver
    assert _post(client).status_code == 200

    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_waived_by"] == "root"
    assert row["live_readiness_waived_at"].startswith(
        datetime.now(timezone.utc).date().isoformat())


def test_the_waiver_pages_the_operator(waiver):
    client, _store, alerts = waiver
    assert _post(client).status_code == 200
    assert len(alerts) == 1
    assert alerts[0]["instance_id"] == INSTANCE
    assert "WAIVED" in alerts[0]["message"].upper()


def test_the_response_returns_the_fingerprint_and_nothing_else_of_substance(waiver):
    client, store, _alerts = waiver
    res = _post(client)
    body = res.json()
    stored = store.get("Instances", INSTANCE)["live_readiness_report"]
    assert body["fingerprint"] == stored["fingerprint"]
    assert body["artifact_hash"] == DIGEST
    # The evidence itself never leaves the row.
    assert "checks" not in body


def test_a_docker_lookup_failure_does_not_write_a_half_waiver(store, monkeypatch):
    """No digest, no waiver: a report whose artifact_hash is a guess would be
    rejected by the launcher anyway, and an unusable report on the row reads
    as an authorization that is not one."""
    from fastapi.testclient import TestClient

    from api import main
    from live_readiness import LiveReadinessError

    store.insert("Instances", {"id": INSTANCE})
    monkeypatch.setattr(main, "db_store", store)

    def _boom():
        raise LiveReadinessError("Docker client is unavailable")

    monkeypatch.setattr(main, "deployed_artifact_digest", _boom)
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = TestClient(main.app).post(
            f"/instances/{INSTANCE}/readiness-waiver",
            json={"confirm": CONFIRM, "reason": REASON})
    finally:
        main.app.dependency_overrides.clear()
    assert res.status_code == 503, res.text
    assert store.get("Instances", INSTANCE).get("live_readiness_report") is None


# --- the digest helper ----------------------------------------------------


def test_the_digest_helper_reads_the_instance_image_without_launching_anything():
    from deployed_artifact import deployed_artifact_digest

    class _Image:
        id = "sha256:" + DIGEST

    class _Images:
        def __init__(self):
            self.asked = []

        def get(self, name):
            self.asked.append(name)
            return _Image()

    class _Client:
        def __init__(self):
            self.images = _Images()

    client = _Client()
    assert deployed_artifact_digest(client=client) == DIGEST
    assert client.images.asked == [
        os.environ.get("DOCKER_INSTANCE_IMAGE", "intellistock-backend")]


def test_the_digest_helper_refuses_a_malformed_image_identity():
    from deployed_artifact import deployed_artifact_digest
    from live_readiness import LiveReadinessError

    class _Client:
        images = type("I", (), {"get": staticmethod(
            lambda name: type("Image", (), {"id": "latest"})())})()

    with pytest.raises(LiveReadinessError):
        deployed_artifact_digest(client=_Client())

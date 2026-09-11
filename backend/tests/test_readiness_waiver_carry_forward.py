"""A standing operator waiver survives a deploy; earned evidence does not.

The readiness report binds to the Docker image it was written against, so
until now every deploy silently invalidated the operator's waiver and the
funded broker refused to start until somebody pressed the button again --
before the instance restarted, which is a race nobody can reliably win.

The operator's decision (2026-09-11) is that a waiver is a *standing* one:
"this instance may start live on my say-so", not "on my say-so against
image ab12cd34". So the launcher carries it forward. What it must never
carry forward is an *earned* report: that one is evidence gathered about a
specific artifact and says nothing whatsoever about the next one, which is
exactly why the binding exists.

These tests pin the difference, and the audit trail that makes the
carry-forward readable after the fact: who waived it, when, which image it
came from, a RED log line and a page every single time.
"""
from __future__ import annotations

import hashlib
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# server.py is the supervisor; importing it pulls in the socket and WSGI
# stacks it serves with, neither of which this file exercises.
sys.modules.setdefault("socketio", MagicMock())
_waitress = types.ModuleType("waitress")
_waitress.serve = lambda *args, **kwargs: None
sys.modules.setdefault("waitress", _waitress)

OLD = "a" * 64
NEW = "b" * 64
INSTANCE = "alpaca-main"
WAIVED_AT = "2026-09-11T12:00:00+00:00"
WAIVER_REASON = ("OPERATOR WAIVED 2026-09-11 by root: EB is engine-proven "
                 "and I accept the live risk.")


def _mapping(reason, artifact_hash, *, instance_id=INSTANCE, state=None):
    """A persisted report with a real fingerprint over the given reason."""
    from live_readiness import (ReadinessCheck, ReadinessReport,
                                ReadinessState, report_to_mapping,
                                required_live_checks)

    evidence = hashlib.sha256(reason.encode("utf-8")).hexdigest()
    return report_to_mapping(ReadinessReport(
        instance_id=instance_id,
        state=state or ReadinessState.LIVE_ELIGIBLE,
        checks=tuple(ReadinessCheck(name, True, reason, evidence)
                     for name in required_live_checks()),
        artifact_hash=artifact_hash,
    ))


def waiver(artifact_hash=OLD):
    return _mapping(WAIVER_REASON, artifact_hash)


def earned(artifact_hash=OLD):
    return _mapping("paper observation completed over 21 sessions",
                    artifact_hash)


# --- the pure re-bind ------------------------------------------------------


def test_a_waiver_is_recognised_by_every_reason_saying_so():
    from live_readiness import is_operator_waiver

    assert is_operator_waiver(waiver()) is True
    assert is_operator_waiver(earned()) is False
    assert is_operator_waiver(None) is False
    assert is_operator_waiver({}) is False
    assert is_operator_waiver({"checks": []}) is False


def test_one_earned_check_among_five_waived_ones_is_not_a_waiver():
    """Mixed provenance is not a standing waiver. Carrying it forward would
    re-bind somebody's evidence to an image it was never gathered on."""
    from live_readiness import is_operator_waiver

    mixed = waiver()
    mixed["checks"][2]["reason"] = "paper observation completed"
    assert is_operator_waiver(mixed) is False


def test_the_rebind_returns_the_same_waiver_bound_to_the_new_image():
    from live_readiness import (assert_live_start_allowed, rebind_operator_waiver,
                                report_from_mapping)

    before = waiver(OLD)
    after = rebind_operator_waiver(before, instance_id=INSTANCE,
                                   artifact_hash=NEW)

    assert after["artifact_hash"] == NEW
    # Everything except the binding and its fingerprint is byte-identical: the
    # operator's words, their date, their name, the evidence hashes.
    assert after["checks"] == before["checks"]
    assert after["state"] == before["state"]
    assert after["fingerprint"] != before["fingerprint"]

    report = report_from_mapping(after, instance_id=INSTANCE,
                                 verify_fingerprint=True)
    assert_live_start_allowed(report, deployed_artifact_hash=NEW)


def test_the_rebind_declines_an_earned_report_and_one_already_bound():
    from live_readiness import rebind_operator_waiver

    assert rebind_operator_waiver(earned(OLD), instance_id=INSTANCE,
                                  artifact_hash=NEW) is None
    assert rebind_operator_waiver(waiver(NEW), instance_id=INSTANCE,
                                  artifact_hash=NEW) is None
    assert rebind_operator_waiver(None, instance_id=INSTANCE,
                                  artifact_hash=NEW) is None


def test_a_tampered_waiver_is_not_laundered_into_a_valid_one():
    """The re-bind recomputes a fingerprint, so it must refuse to recompute
    one over a payload whose current fingerprint does not verify -- otherwise
    a hand-edited blob gets a valid signature for free."""
    from live_readiness import LiveReadinessError, rebind_operator_waiver

    tampered = waiver(OLD)
    tampered["state"] = "LIVE_RUNNING"          # fingerprint no longer matches
    with pytest.raises(LiveReadinessError):
        rebind_operator_waiver(tampered, instance_id=INSTANCE,
                               artifact_hash=NEW)


def test_the_rebind_refuses_a_digest_that_is_not_an_artifact_identity():
    from live_readiness import LiveReadinessError, rebind_operator_waiver

    for bad in ("latest", "", "B" * 64, "b" * 12, None):
        with pytest.raises(LiveReadinessError):
            rebind_operator_waiver(waiver(OLD), instance_id=INSTANCE,
                                   artifact_hash=bad)


def test_the_rebind_refuses_a_report_for_another_instance():
    from live_readiness import LiveReadinessError, rebind_operator_waiver

    with pytest.raises(LiveReadinessError):
        rebind_operator_waiver(waiver(OLD), instance_id="some-other-instance",
                               artifact_hash=NEW)


# --- the launcher ----------------------------------------------------------


@pytest.fixture
def launch(store, monkeypatch):
    """server._preflight_instance_launch over a fake store and fake Docker.

    Nothing is launched: the preflight only resolves the image identity and
    reads the rows. The client hands back ``NEW`` as the deployed digest,
    which is the whole point -- it is the image a deploy just built.
    """
    import server
    import live_alerts

    logged: list = []
    alerts: list = []
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server.intellistock_logger, "log",
                        lambda msg, colour=None, **kw: logged.append((msg, colour)))
    monkeypatch.setattr(live_alerts, "alert_strategy_error",
                        lambda **kw: alerts.append(kw))

    def _client(digest=NEW):
        return type("C", (), {"images": type("I", (), {
            "get": lambda self, _name: type(
                "Image", (), {"id": "sha256:" + digest})()})()})()

    def _run(row, *, digest=NEW):
        store.insert("Instances", row, conflict="replace")
        store.insert("BrokerageAccounts",
                     {"id": "k1", "kalshi_environment": "live"},
                     conflict="replace")
        return server._preflight_instance_launch(
            INSTANCE, client=_client(digest))

    return _run, store, logged, alerts


def _row(report, **extra):
    row = {"id": INSTANCE, "kind": "equities", "live_readiness_report": report,
           "live_readiness_waived_by": "root",
           "live_readiness_waived_at": WAIVED_AT}
    row.update(extra)
    return row


def test_a_deploy_no_longer_invalidates_the_operators_waiver(launch):
    """The regression this whole change exists for: the waiver was written
    against the previous image, a deploy built a new one, and the funded
    broker refused to start until somebody pressed the button again."""
    from live_readiness import (assert_live_start_allowed, report_from_mapping)

    run, store, _logged, _alerts = launch
    run(_row(waiver(OLD)))

    persisted = store.get("Instances", INSTANCE)["live_readiness_report"]
    assert persisted["artifact_hash"] == NEW
    report = report_from_mapping(persisted, instance_id=INSTANCE,
                                 verify_fingerprint=True)
    # The exact call instance.py makes immediately before spawning a
    # real-money broker, against the digest server.py puts in its environment.
    assert_live_start_allowed(report, deployed_artifact_hash=NEW)


def test_the_preflight_hands_the_rebound_report_to_the_kalshi_gate(launch):
    """A funded Kalshi instance is gated inside the preflight itself, so the
    carry-forward has to happen before that assert and the in-memory row it
    checks has to be the rebound one -- not the stale copy read a line
    earlier."""
    run, _store, _logged, _alerts = launch
    preflight = run(_row(
        waiver(OLD), kind="kalshi",
        kalshi_config={"live_enabled": True, "paper_mode": False},
        brokerage_id="k1"))
    assert preflight.image_digest == NEW
    assert preflight.instance["live_readiness_report"]["artifact_hash"] == NEW


def test_an_earned_report_still_refuses_the_new_image_and_is_untouched(launch):
    """Evidence is about one artifact. A deploy invalidating it is the
    feature, and nothing here may quietly re-sign it."""
    run, store, _logged, _alerts = launch
    before = earned(OLD)

    with pytest.raises(Exception):
        run(_row(before, kind="kalshi",
                 kalshi_config={"live_enabled": True, "paper_mode": False},
                 brokerage_id="k1"))

    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_report"] == before
    assert row.get("live_readiness_rebound_at") is None


def test_a_waiver_already_bound_to_this_image_is_not_rewritten(launch):
    """A restart is not a deploy. Re-stamping the row on every container
    start would turn the audit trail into noise."""
    run, store, logged, alerts = launch
    run(_row(waiver(NEW)))

    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_report"] == waiver(NEW)
    assert row.get("live_readiness_rebound_at") is None
    assert alerts == []
    assert not [m for m, _c in logged if "carried forward" in m]


def test_the_carry_forward_names_who_waived_it_and_when(launch):
    run, store, logged, alerts = launch
    run(_row(waiver(OLD)))

    row = store.get("Instances", INSTANCE)
    assert row["live_readiness_rebound_from"] == OLD[:12]
    assert row["live_readiness_rebound_at"].startswith("20")
    # The stamp of the original decision is never overwritten: the waiver was
    # made once, by a person, on a date.
    assert row["live_readiness_waived_by"] == "root"
    assert row["live_readiness_waived_at"] == WAIVED_AT

    message = next(m for m, _c in logged if "carried forward" in m)
    assert message == (
        f"live-readiness waiver carried forward to image {NEW[:12]} "
        f"for {INSTANCE} (waived by root at {WAIVED_AT})")
    assert [c for m, c in logged if "carried forward" in m] == ["red"]
    assert len(alerts) == 1
    assert alerts[0]["instance_id"] == INSTANCE
    assert "carried forward" in alerts[0]["message"]


def test_a_dead_alert_channel_does_not_block_the_launch(launch, monkeypatch):
    """The waiver is already on the row by then; a broken Discord webhook
    must not be the reason a real-money instance fails to start."""
    import live_alerts

    run, store, _logged, _alerts = launch
    monkeypatch.setattr(live_alerts, "alert_strategy_error",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("nope")))
    run(_row(waiver(OLD)))
    assert store.get("Instances", INSTANCE)[
        "live_readiness_report"]["artifact_hash"] == NEW


def test_a_row_with_no_report_is_left_alone(launch):
    run, store, logged, _alerts = launch
    run({"id": INSTANCE, "kind": "equities"})
    row = store.get("Instances", INSTANCE)
    assert row.get("live_readiness_report") is None
    assert not [m for m, _c in logged if "carried forward" in m]


def test_a_malformed_report_does_not_take_the_launch_path_down(launch):
    """Refusing to start is the launcher's job, not the carry-forward's. A
    report it cannot parse is left exactly where it is, for
    ``assert_live_start_allowed`` to refuse a moment later."""
    run, store, logged, _alerts = launch
    junk = {"instance_id": INSTANCE, "checks": "not a list"}
    run(_row(junk))
    assert store.get("Instances", INSTANCE)["live_readiness_report"] == junk


def test_a_tampered_waiver_leaves_the_row_alone_and_says_so(launch):
    """It reads as a waiver but its fingerprint does not verify. The
    carry-forward refuses to re-sign it, keeps the launch path walking, and
    leaves a RED line saying why."""
    run, store, logged, alerts = launch
    tampered = waiver(OLD)
    tampered["state"] = "LIVE_RUNNING"

    run(_row(tampered))

    assert store.get("Instances", INSTANCE)["live_readiness_report"] == tampered
    assert alerts == []
    assert [c for m, c in logged if "waiver carry-forward" in m] == ["red"]

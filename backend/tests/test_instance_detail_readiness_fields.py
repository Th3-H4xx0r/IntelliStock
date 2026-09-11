"""GET /instances/{id} returns the fields the live-readiness UI reads.

Two routes now write live-start state onto ``Instances.<id>``: PATCH writes
``initial_value`` / ``clean_room_mode``, and POST .../readiness-waiver writes
``live_readiness_report`` plus who waived it and when. Nothing could read any
of them back: ``action_get_instance`` builds its response from an explicit
whitelist, so the operator's only way to see what they had just set was a
direct database query -- which is exactly the hand-editing the waiver route
exists to replace.

The keys are pinned as *always present*, null when unset. A client that gets
no key at all is talking to an API too old to know about readiness, and must
not render that as "this instance has no readiness report".
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

READINESS_KEYS = (
    "initial_value",
    "clean_room_mode",
    "live_readiness_report",
    "live_readiness_waived_at",
    "live_readiness_waived_by",
)

REPORT = {
    "instance_id": "alpaca-main",
    "state": "LIVE_ELIGIBLE",
    "checks": [{"name": "clean_room_baseline", "passed": True,
                "reason": "OPERATOR WAIVED 2026-09-11 by root: ...",
                "evidence_hash": "c" * 64}],
    "artifact_hash": "a" * 64,
    "fingerprint": "f" * 64,
}


@pytest.fixture
def detail(monkeypatch):
    """action_get_instance over a stubbed row -- no table, no backtests."""
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)

    def _get(doc):
        monkeypatch.setattr(iu, "_resolve_instance_doc", lambda conn, iid: doc)
        return iu.action_get_instance(None, "alpaca-main")

    return _get


def test_a_waived_instance_reports_the_report_and_who_waived_it(detail):
    out = detail({
        "id": "alpaca-main",
        "initial_value": 6000.0,
        "clean_room_mode": True,
        "live_readiness_report": REPORT,
        "live_readiness_waived_at": "2026-09-11T12:00:00+00:00",
        "live_readiness_waived_by": "root",
    })
    assert out["initial_value"] == 6000.0
    assert out["clean_room_mode"] is True
    assert out["live_readiness_report"]["artifact_hash"] == "a" * 64
    assert out["live_readiness_report"]["state"] == "LIVE_ELIGIBLE"
    assert out["live_readiness_waived_at"] == "2026-09-11T12:00:00+00:00"
    assert out["live_readiness_waived_by"] == "root"


def test_an_ungated_instance_reports_nulls_not_missing_keys(detail):
    """"No report" and "this API cannot tell you" must not look alike."""
    out = detail({"id": "alpaca-main"})
    for key in READINESS_KEYS:
        assert key in out, f"{key} dropped from the instance detail projection"
    assert out["live_readiness_report"] is None
    assert out["live_readiness_waived_at"] is None
    assert out["live_readiness_waived_by"] is None
    assert out["initial_value"] is None
    # Absent reads as off, matching instance.py's doc.get(..., False).
    assert out["clean_room_mode"] is False


def test_the_artifact_hash_survives_the_round_trip_whole(detail):
    """The UI shows a short prefix, but truncating server-side would make the
    value it displays unverifiable against `docker images --no-trunc`."""
    out = detail({"id": "alpaca-main", "live_readiness_report": REPORT})
    assert len(out["live_readiness_report"]["artifact_hash"]) == 64

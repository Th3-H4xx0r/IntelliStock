from datetime import datetime, timedelta, timezone

from live_state import _command_claim_transition


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_pending_command_gets_a_bounded_lease_and_attempt():
    updated, executable = _command_claim_transition(
        {"id": "c1", "status": "pending", "attempt_count": 0},
        now=NOW,
        worker_id="worker-1",
        lease_seconds=30,
    )
    assert executable is True
    assert updated["status"] == "running"
    assert updated["lease_owner"] == "worker-1"
    assert updated["lease_expires_at_iso"] == (
        NOW + timedelta(seconds=30)
    ).isoformat()
    assert updated["attempt_count"] == 1


def test_active_running_command_cannot_be_double_claimed():
    updated, executable = _command_claim_transition(
        {
            "id": "c1",
            "status": "running",
            "lease_owner": "worker-1",
            "lease_expires_at_iso": (NOW + timedelta(seconds=10)).isoformat(),
            "attempt_count": 1,
        },
        now=NOW,
        worker_id="worker-2",
        lease_seconds=30,
    )
    assert updated is None
    assert executable is False


def test_expired_money_moving_command_requires_reconciliation_not_retry():
    updated, executable = _command_claim_transition(
        {
            "id": "c1",
            "type": "submit_order",
            "status": "running",
            "lease_owner": "dead-worker",
            "lease_expires_at_iso": (NOW - timedelta(seconds=1)).isoformat(),
            "attempt_count": 1,
        },
        now=NOW,
        worker_id="worker-2",
        lease_seconds=30,
    )
    assert executable is False
    assert updated["status"] == "reconciliation_required"
    assert updated["attempt_count"] == 1
    assert updated["lease_owner"] is None


def test_expired_halt_is_safe_to_reclaim_once():
    updated, executable = _command_claim_transition(
        {
            "id": "c1",
            "type": "halt",
            "status": "running",
            "lease_expires_at_iso": (NOW - timedelta(seconds=1)).isoformat(),
            "attempt_count": 1,
        },
        now=NOW,
        worker_id="worker-2",
        lease_seconds=30,
    )
    assert executable is True
    assert updated["status"] == "running"
    assert updated["attempt_count"] == 2

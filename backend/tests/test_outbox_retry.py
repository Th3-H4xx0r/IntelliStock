"""Hardening (2026-06-10): the Discord outbox poller must RETRY transient send
failures with backoff instead of marking a message 'failed' forever (the old
behavior silently dropped any message whose first send raised — a second way
fill notifs could go missing).

We unit-test the pure retry-decision helper; the RethinkDB filter + requeue
action are thin wrappers around it.
"""
from __future__ import annotations


def test_retry_decision_backoff_progression():
    from interactive_utils import discord_retry_decision
    now = 1000.0
    status, next_at, attempts = discord_retry_decision(0, now)
    assert status == "pending" and next_at == now + 5 and attempts == 1
    status, next_at, attempts = discord_retry_decision(1, now)
    assert status == "pending" and next_at == now + 10 and attempts == 2
    status, next_at, attempts = discord_retry_decision(3, now)
    assert status == "pending" and next_at == now + 40 and attempts == 4


def test_retry_decision_caps_backoff():
    from interactive_utils import discord_retry_decision
    # Even if attempts were large but below MAX, backoff is capped at 300s.
    # (With MAX=5 this guards future tuning.)
    from interactive_utils import RETRY_MAX_ATTEMPTS
    if RETRY_MAX_ATTEMPTS > 6:
        status, next_at, _ = discord_retry_decision(6, 0.0)
        assert next_at == min(300, 5 * (2 ** 6))


def test_retry_decision_gives_up_at_max():
    from interactive_utils import discord_retry_decision, RETRY_MAX_ATTEMPTS
    status, next_at, attempts = discord_retry_decision(RETRY_MAX_ATTEMPTS, 0.0)
    assert status == "failed" and next_at is None
    status, next_at, _ = discord_retry_decision(RETRY_MAX_ATTEMPTS + 3, 0.0)
    assert status == "failed" and next_at is None

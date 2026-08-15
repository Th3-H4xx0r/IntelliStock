"""Self-learning subsystem: observe, measure, hypothesise, act.

Phase 1 is OBSERVE only — nothing in this package writes strategy config or
takes an autonomous action. Every module except `store` is pure and DB-free so
it unit-tests without RethinkDB, matching the idiom of `scheduler.py`,
`nexus_telemetry.py` and `bot_decision_log.py`.
"""
from self_learning.types import (  # noqa: F401
    Finding, Lever, Observation, VarianceReport, content_id,
)

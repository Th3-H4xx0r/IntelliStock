"""Caps on MCP-principal calls (contract §7, spec §6, plan Decisions 12 and 18).

The gate asks these before a call runs, after its Idempotency-Key lookup and
before the claim: the per-minute windows (every call; non-read calls also on
the write window), the one backtest slot (create_backtest only), then the
daily cost-route cap (routes marked cost). The slot goes before the cost cap,
so an agent retrying while a long backtest runs does not burn the day's
cost-route budget. The daily approval-request cap is asked just before a call
is held. A check that passes reserves its slot at once, under the one lock, so
two concurrent calls cannot both take the last one; a refused check reserves
nothing (the backtest slot never reserves: it only reads the running count).

The per-minute windows live in memory: the api is one process. The daily
counts are global across MCP clients (Decision 18). Each is seeded at its first
check of a UTC day from what the store already holds (McpAudit rows marked
cap_bucket "cost"; McpApprovals created that day), then counted in memory, so a
restart does not reset them. Every check fails closed: a count the store cannot
give is a refusal, never a pass.

A refusal is a CapRefusal. The gate answers it with a 429 {"detail",
"retry_after_s"}, the same integer in Retry-After, audited "capped".
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from db import P, store
from mcp_approvals import APPROVALS_TABLE
from mcp_audit import AUDIT_TABLE
from mcp_policy import McpConfig, iso_utc

_log = logging.getLogger("uvicorn.error")

BACKTEST_START_OPERATIONS = frozenset({"create_backtest"})

WINDOW_S = 60
# How soon to retry when a backtest holds the slot, or a daily count could not be read.
BACKTEST_RETRY_AFTER_S = 60
SEED_RETRY_AFTER_S = 60

CALL_RATE_DETAIL = "mcp: call rate cap reached"
WRITE_RATE_DETAIL = "mcp: write rate cap reached"
BACKTEST_DETAIL = "mcp: a backtest is already running (one at a time)"
COST_LABEL = "cost-route"
APPROVALS_LABEL = "approval-request"


@dataclass(frozen=True)
class CapRefusal:
    detail: str
    retry_after_s: int


def running_backtest_count() -> int:
    """BacktestProgress rows holding the backtest slot: running, queued, pending or paused*."""
    rows = store.sql(
        'SELECT count(*) AS n FROM "BacktestProgress" WHERE status IN (%s, %s, %s) OR status LIKE %s',
        ("running", "queued", "pending", "paused%"))
    return int(rows[0]["n"])


def _cost_calls_since(midnight_iso: str) -> int:
    return store.count(store.filter(
        AUDIT_TABLE, P.field("ts").ge(midnight_iso) & P.field("cap_bucket").eq("cost")))


def _approvals_since(midnight_iso: str) -> int:
    return store.count(store.filter(APPROVALS_TABLE, P.field("created_at").ge(midnight_iso)))


def _utc_midnight(now: float) -> datetime:
    return datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _window_refusal(window: deque, cap: int, now: float, detail: str) -> CapRefusal | None:
    """None while the window holds fewer than cap calls; else a refusal that
    waits until its oldest call leaves (the whole window when it is empty)."""
    if len(window) < cap:
        return None
    oldest = window[0] if window else now
    return CapRefusal(detail, max(1, math.ceil(oldest + WINDOW_S - now)))


class McpCaps:
    """The caps' state. One lock guards all of it; store reads happen outside it."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()
        self._writes: deque[float] = deque()
        self._daily: dict[str, tuple[datetime, int]] = {}

    def check_rate(self, *, non_read: bool, cfg: McpConfig) -> CapRefusal | None:
        """The 60-second sliding windows: every call on the call window, a
        non-read call also on the write window. Reserves on both, or on neither."""
        now = self._clock()
        with self._lock:
            for window in (self._calls, self._writes):
                while window and window[0] <= now - WINDOW_S:
                    window.popleft()
            refusal = _window_refusal(self._calls, cfg.cap_calls_per_min, now, CALL_RATE_DETAIL)
            if refusal is None and non_read:
                refusal = _window_refusal(self._writes, cfg.cap_writes_per_min, now, WRITE_RATE_DETAIL)
            if refusal is not None:
                return refusal
            self._calls.append(now)
            if non_read:
                self._writes.append(now)
            return None

    def check_cost(self, cfg: McpConfig) -> CapRefusal | None:
        """The daily cost-route cap, global across clients; resets at UTC midnight."""
        return self._check_daily("cost", COST_LABEL, cfg.cap_cost_per_day, _cost_calls_since)

    def check_approvals(self, cfg: McpConfig) -> CapRefusal | None:
        """The daily approval-request cap, global across clients; resets at UTC midnight."""
        return self._check_daily("approvals", APPROVALS_LABEL, cfg.cap_approvals_per_day, _approvals_since)

    def check_backtest_slot(self, cfg: McpConfig) -> CapRefusal | None:
        """A refusal while running_backtest_count() reaches the cap, or when it
        cannot be read. Reserves nothing."""
        try:
            running = running_backtest_count()
        except Exception as exc:
            # The type only: a store error's message can quote the query.
            _log.warning("mcp: running-backtest count failed (%s); refusing", type(exc).__name__)
            running = None
        if running is None or running >= cfg.cap_concurrent_backtests:
            return CapRefusal(BACKTEST_DETAIL, BACKTEST_RETRY_AFTER_S)
        return None

    def _check_daily(self, bucket: str, label: str, cap: int,
                     count_since: Callable[[str], int]) -> CapRefusal | None:
        """Check and reserve one of today's cap slots in the bucket.

        The day's count is read from the store once, outside the lock, so a slow
        store never stalls the rate checks; if another call seeded the day
        meanwhile, its count wins. A failed read is a refusal, and the next call
        reads again.
        """
        now = self._clock()
        day = _utc_midnight(now)
        seeded: int | None = None
        while True:
            with self._lock:
                held = self._daily.get(bucket)
                if (held is None or held[0] != day) and seeded is not None:
                    held = self._daily[bucket] = (day, seeded)
                if held is not None and held[0] == day:
                    if held[1] >= cap:
                        next_midnight = (day + timedelta(days=1)).timestamp()
                        return CapRefusal(f"mcp: daily {label} cap reached",
                                          max(1, math.ceil(next_midnight - now)))
                    self._daily[bucket] = (day, held[1] + 1)
                    return None
            try:
                seeded = int(count_since(iso_utc(day)))
            except Exception as exc:
                # The type only: a store error's message can quote the query.
                _log.warning("mcp: daily %s count failed (%s); refusing", bucket, type(exc).__name__)
                return CapRefusal(f"mcp: daily {label} count unavailable", SEED_RETRY_AFTER_S)


caps = McpCaps()

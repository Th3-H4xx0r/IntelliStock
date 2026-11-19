"""Auth audit events: recorded in the auth store, then delivered to the API (contract section 4).

record() is synchronous and does no network I/O, so a login or token request
never waits on the API: it filters the detail, writes one auth_events row, and
logs the alert events at WARNING. flush() delivers pending rows to
POST /internal/mcp/audit-event, alert events first, then in the order they were
recorded. The API relays refresh_reuse_revoked, lockout and disabled to Discord;
this container has no alert path of its own.

Delivery is at least once. A row is marked delivered only after the API
accepts it, and the server and the admin CLI each flush the same file, so a
row can reach the API twice; it is never lost while the store survives.

Callers pass the detail the API needs to word its alerts: refresh_reuse_revoked
takes session_id, user_id, username and client_id; lockout takes scope ("user"
or "global"), username (None for global) and seconds; disabled and enabled take
source ("admin_cli").
"""
import asyncio
import json
import logging
from typing import Any, Callable

from mcp_server.api_client import ApiClient
from mcp_server.authdb import AuthDB

AUTH_EVENTS = frozenset({"login_ok", "login_failed", "passkey_failed", "totp_failed", "lockout",
                         "refresh_reuse_revoked", "session_revoked", "enrolled", "disabled", "enabled"})  # contract §4
LOCAL_ONLY_EVENTS = frozenset({"signing_key_rotated"})  # never sent to the API (not in the contract enum)
ALERT_EVENTS = frozenset({"refresh_reuse_revoked", "lockout", "disabled"})  # the API relays these to Discord
FORBIDDEN_DETAIL_SUBSTRINGS = ("password", "token", "secret", "code", "credential", "assertion", "cookie", "otp")

RETENTION_S = 90 * 86_400

logger = logging.getLogger(__name__)

_SENDABLE = sorted(AUTH_EVENTS)
_ALERTS = sorted(ALERT_EVENTS)
_PENDING_SQL = (
    "SELECT id, event, detail FROM auth_events "
    f"WHERE delivered_at IS NULL AND event IN ({', '.join('?' * len(_SENDABLE))}) "
    f"ORDER BY event IN ({', '.join('?' * len(_ALERTS))}) DESC, id "
    "LIMIT ?"
)


def _forbidden(key: Any) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in FORBIDDEN_DETAIL_SUBSTRINGS)


def _scrub(value: Any) -> Any:
    """value with every key that names a secret removed, in nested objects and arrays too."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if not _forbidden(key)}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


class AuditSink:
    def __init__(self, db: AuthDB, api: ApiClient, *, clock: Callable[[], float]) -> None:
        self._db = db
        self._api = api
        self._clock = clock

    def record(self, event: str, **detail: Any) -> None:
        """Store one event; ValueError for an event outside AUTH_EVENTS and LOCAL_ONLY_EVENTS.

        Keys containing a FORBIDDEN_DETAIL_SUBSTRINGS entry, in any case, are dropped.
        The detail must be JSON: anything else raises here, not later in flush().
        """
        if event not in AUTH_EVENTS and event not in LOCAL_ONLY_EVENTS:
            raise ValueError(f"unknown audit event {event!r}")
        text = json.dumps(_scrub(detail), sort_keys=True, allow_nan=False)
        self._db.query("INSERT INTO auth_events (at, event, detail) VALUES (?, ?, ?)",
                       (self._clock(), event, text))
        if event in ALERT_EVENTS:
            logger.warning("mcp auth alert: %s %s", event, text)

    async def flush(self, limit: int = 100) -> int:
        """Deliver up to limit pending rows; returns how many the API accepted.

        The first failure ends the pass: it nearly always means the API is down,
        and one timeout per pass is enough to learn that. The failed row keeps its
        place and is retried on the next flush.
        """
        rows = self._db.query(_PENDING_SQL, (*_SENDABLE, *_ALERTS, limit))
        delivered = 0
        for row in rows:
            try:
                ok = await self._api.post_audit_event(row["event"], json.loads(row["detail"]))
            except Exception as exc:  # never let one bad delivery take down the caller
                logger.warning("audit event delivery failed (%s)", type(exc).__name__)
                ok = False
            self._db.query(
                "UPDATE auth_events SET attempts = attempts + 1, delivered_at = ? WHERE id = ?",
                (self._clock() if ok else None, row["id"]),
            )
            if not ok:
                break
            delivered += 1
        return delivered

    async def flush_loop(self, interval_s: float = 5.0) -> None:
        """flush() every interval_s seconds until cancelled."""
        while True:
            try:
                await self.flush()
            except Exception as exc:  # the store can fail too; the loop must outlive it
                logger.warning("audit flush failed (%s)", type(exc).__name__)
            await asyncio.sleep(interval_s)

    def purge(self) -> None:
        """Delete delivered rows recorded more than 90 days ago (the API keeps its own copy)."""
        self._db.query("DELETE FROM auth_events WHERE delivered_at IS NOT NULL AND at < ?",
                       (self._clock() - RETENTION_S,))

"""Login throttling: per-username lockouts with doubling backoff, and a global lockout on bursts.

Every failed sign-in step (password, passkey or TOTP) is one login_failures row
under the normalised username, strip().lower()[:128], so " Operator" and
"operator" share one count. Five failures for one username inside 15 minutes lock
that username; twenty failures across all usernames inside 10 minutes lock
everyone, which catches a guesser who spreads attempts over many names.

A user lockout's level is the previous level + 1 when that username's previous
lockout is still running or ended no more than 24 h ago, else 1, and it lasts
min(base * 2 ** (level - 1), max): 900 s, 1800 s, 3600 s ... capped at 86,400 s.
The 24 h memory runs from the end of the previous lockout, not its start, so a
guesser who waits out a capped lockout and resumes stays capped; only a full quiet
day after a lockout ends brings the next one back to 900 s. The global lockout is
a fixed 600 s and never
escalates: it locks the operator out too, so a longer one would only hand a
guesser a longer denial of service.

A lockout spends the failures that set it: only failures after a scope's last
lockout count toward its next one, so the first attempt after a lockout ends
never re-locks at once. record_success deletes that username's failures and
keeps its lockout row, so a success does not reset the backoff level.

record_failure is one BEGIN IMMEDIATE transaction, so two processes on the file
never both set the same lockout. on_lockout(scope, username, seconds) runs once
per lockout set, after the transaction commits and outside it: scope "user" with
the normalised username, or "global" with None.
"""
import math
import sqlite3
from typing import Callable, Optional

from mcp_server.authdb import AuthDB

USERNAME_MAX_CHARS = 128
LEVEL_MEMORY_S = 86_400
FAILURE_RETENTION_S = 86_400
GLOBAL_SCOPE = "global"
_USER_SCOPE_PREFIX = "user:"  # keeps a username "global" off the global row


def normalize_username(username: str) -> str:
    """The form failures and lockouts are keyed by: stripped, lower-cased, at most 128 characters."""
    return username.strip().lower()[:USERNAME_MAX_CHARS]


def _user_scope(name: str) -> str:
    return _USER_SCOPE_PREFIX + name


class LoginThrottle:
    """Failure counts and lockouts on the auth store; see the module docstring for the rules."""

    def __init__(self, db: AuthDB, *, clock, on_lockout: Callable[[str, Optional[str], int], None],
                 max_failures: int = 5, window_s: int = 900, base_lockout_s: int = 900, max_lockout_s: int = 86_400,
                 global_max_failures: int = 20, global_window_s: int = 600, global_lockout_s: int = 600) -> None:
        self._db = db
        self._clock = clock
        self._on_lockout = on_lockout
        self._max_failures = max_failures
        self._window_s = window_s
        self._base_lockout_s = base_lockout_s
        self._max_lockout_s = max_lockout_s
        self._global_max_failures = global_max_failures
        self._global_window_s = global_window_s
        self._global_lockout_s = global_lockout_s

    def check(self, username: str) -> Optional[int]:
        """Whole seconds left on the username's lockout or the global one, whichever ends later; None if neither."""
        now = self._clock()
        rows = self._db.query("SELECT until FROM lockouts WHERE scope IN (?, ?)",
                              (_user_scope(normalize_username(username)), GLOBAL_SCOPE))
        left = max((row["until"] - now for row in rows), default=0)
        return math.ceil(left) if left > 0 else None  # rounded up: never 0 while still locked

    def record_failure(self, username: str, kind: str) -> Optional[int]:
        """Count one failed step; the lockout seconds if it set a lockout (the longer, if it set both)."""
        name = normalize_username(username)
        triggered = []
        with self._db.tx() as conn:
            now = self._clock()
            conn.execute("INSERT INTO login_failures (username, kind, at) VALUES (?, ?, ?)", (name, kind, now))
            seconds = self._maybe_lock(conn, now, _user_scope(name), " AND username = ?", (name,),
                                       self._window_s, self._max_failures, self._user_lockout_s)
            if seconds is not None:
                triggered.append(("user", name, seconds))
            seconds = self._maybe_lock(conn, now, GLOBAL_SCOPE, "", (), self._global_window_s,
                                       self._global_max_failures, lambda level: self._global_lockout_s)
            if seconds is not None:
                triggered.append(("global", None, seconds))
        for scope, who, seconds in triggered:
            self._on_lockout(scope, who, seconds)
        return max((seconds for _, _, seconds in triggered), default=None)

    def record_success(self, username: str) -> None:
        """Delete the username's failures; its lockout row, and so its backoff level, stays."""
        with self._db.tx() as conn:
            conn.execute("DELETE FROM login_failures WHERE username = ?", (normalize_username(username),))

    def purge(self) -> None:
        """Delete failures older than a day, and lockouts that ended over a day ago (they no longer raise a level)."""
        with self._db.tx() as conn:
            now = self._clock()
            conn.execute("DELETE FROM login_failures WHERE at < ?", (now - FAILURE_RETENTION_S,))
            conn.execute("DELETE FROM lockouts WHERE until < ?", (now - LEVEL_MEMORY_S,))

    def _user_lockout_s(self, level: int) -> int:
        return min(self._base_lockout_s * 2 ** (level - 1), self._max_lockout_s)

    def _maybe_lock(self, conn: sqlite3.Connection, now: float, scope: str, where: str, params: tuple,
                    window_s: int, limit: int, duration: Callable[[int], int]) -> Optional[int]:
        """Lock scope if limit failures matching where (a fixed clause, never caller text) fall in its window."""
        prev = conn.execute("SELECT level, set_at, until FROM lockouts WHERE scope = ?", (scope,)).fetchone()
        since = now - window_s if prev is None else max(now - window_s, prev["set_at"])
        count = conn.execute(f"SELECT COUNT(*) FROM login_failures WHERE at > ?{where}",
                             (since,) + params).fetchone()[0]
        if count < limit:
            return None
        # Remembered from the previous lockout's end (negative while it still runs), so the cap holds.
        level = prev["level"] + 1 if prev is not None and now - prev["until"] <= LEVEL_MEMORY_S else 1
        seconds = duration(level)
        conn.execute(
            "INSERT INTO lockouts (scope, until, level, set_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (scope) DO UPDATE SET "
            "until = excluded.until, level = excluded.level, set_at = excluded.set_at",
            (scope, now + seconds, level, now),
        )
        return seconds

"""The operator's admin CLI, run inside the container:

    docker exec -it intellistock-mcp python -m mcp_server.admin <command>

Commands:
    enroll <username>               print a single-use enrollment link (valid ENROLL_TTL_S seconds)
    list-sessions [--all]           sessions (refresh families), newest first; --all adds revoked and expired
    revoke <session-id-prefix>      revoke the one session whose id starts with the prefix (8+ characters)
    revoke-all [--user <username>]  revoke every active session, or every active session of one user
    disable / enable                set or clear the kill switch (every /mcp, sign-in and token request gets 503)
    rotate-signing-key              replace the signing key: every access token stops verifying, refresh still works
    status                          the kill switch, enrolled users, active sessions and the current key id

Exit codes: 0 done; 1 a usage error or a session error (an unknown, ambiguous or
inactive session, an unknown user); 2 invalid settings, one line per problem.

The CLI opens the server's own auth store (AuthDB allows a second process on the
file), so a revocation, the kill switch and a key rotation take effect on the
server's next request. A mutating command ends by delivering pending audit events
to the API. The change is already committed by then, so an unreachable API does
not fail the command: it exits 0, prints "(audit delivery pending)", and the
server's own flush loop delivers the events later.
"""
import argparse
import asyncio
import os
import sys
import time
from typing import Callable, Mapping, Optional, TextIO

import httpx

from mcp_server.api_client import ApiClient
from mcp_server.audit import AUTH_EVENTS, AuditSink
from mcp_server.authdb import AuthDB
from mcp_server.config import ENROLL_TTL_S, Settings, SettingsError, load_settings
from mcp_server.crypto import hash_token, new_token
from mcp_server.sessions import SessionInfo, SessionService
from mcp_server.tokens import AccessTokenIssuer, SigningKeyring

PROG = "python -m mcp_server.admin"
ENROLL_TOKEN_PREFIX = "mcpen_"
ENROLL_FIRST_STAGE = "password"
SESSION_PREFIX_MIN_CHARS = 8
SESSION_ID_DISPLAY_CHARS = 12
ADMIN_REVOKE_REASON = "admin"
AUDIT_SOURCE = "admin_cli"
DISABLED_FLAG = "disabled"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SETTINGS = 2

MUTATING_COMMANDS = frozenset({"enroll", "revoke", "revoke-all", "disable", "enable", "rotate-signing-key"})

# Same order as tokens.SigningKeyring's current key, read without unsealing or creating one.
_CURRENT_KID_SQL = ("SELECT kid FROM signing_keys WHERE retired_at IS NULL "
                    "ORDER BY created_at DESC, kid DESC LIMIT 1")
_SENDABLE = sorted(AUTH_EVENTS)
_UNDELIVERED_SINCE_SQL = (
    "SELECT COUNT(*) AS n FROM auth_events WHERE id > ? AND delivered_at IS NULL AND dead_at IS NULL "
    f"AND event IN ({', '.join('?' * len(_SENDABLE))})"
)


def create_enrollment(db: AuthDB, settings: Settings, username: str, *, clock,
                      ttl_s: int = ENROLL_TTL_S) -> tuple[str, float]:
    """(link, expires_at) for a new single-use enrollment of username.

    The token is new_token("mcpen_") and only its hash is stored. The row starts at
    stage "password" with opened_at NULL. Every earlier enrollment of the same
    username that has not completed is deleted in the same transaction, so its
    link, and a browser already part-way through it, stop working.
    """
    if not username.strip():
        raise ValueError("username must not be empty")
    token = new_token(ENROLL_TOKEN_PREFIX)
    with db.tx() as conn:
        now = clock()
        expires_at = now + ttl_s
        conn.execute("DELETE FROM enrollments WHERE username = ? AND completed_at IS NULL", (username,))
        conn.execute(
            "INSERT INTO enrollments (token_hash, username, created_at, expires_at, stage) VALUES (?, ?, ?, ?, ?)",
            (hash_token(token), username, now, expires_at, ENROLL_FIRST_STAGE),
        )
    return f"{settings.public_base}/enroll?t={token}", expires_at


def _iso(ts: Optional[float]) -> str:
    """ISO-8601 UTC to the second, or "-" for no time."""
    return "-" if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _session_status(info: SessionInfo, now: float) -> str:
    if info.revoked_at is not None:
        return "revoked:" + (info.revoked_reason or "unknown")
    return "expired" if now >= info.abs_expires_at else "active"


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]


class _Stop(Exception):
    """argparse finished early (help, or a usage error) with this exit status."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that writes to the CLI's output and exits 1, not 2, on a usage error."""

    def __init__(self, *args, out: TextIO, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._out = out

    def print_usage(self, file=None) -> None:
        super().print_usage(self._out if file is None else file)

    def print_help(self, file=None) -> None:
        super().print_help(self._out if file is None else file)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if message:
            self._out.write(message)
        raise _Stop(status)

    def error(self, message: str) -> None:
        self.print_usage()
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def _parser(out: TextIO) -> _Parser:
    parser = _Parser(prog=PROG, description="Administer the IntelliStock MCP server.", out=out)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    enroll = commands.add_parser("enroll", help="print a single-use enrollment link for a user", out=out)
    enroll.add_argument("username")
    listing = commands.add_parser("list-sessions", help="list active sessions", out=out)
    listing.add_argument("--all", action="store_true", help="include revoked and expired sessions")
    revoke = commands.add_parser("revoke", help="revoke one session by id prefix", out=out)
    revoke.add_argument("prefix", metavar="session-id-prefix",
                        help=f"at least {SESSION_PREFIX_MIN_CHARS} characters of the session id")
    revoke_all = commands.add_parser("revoke-all", help="revoke every active session", out=out)
    revoke_all.add_argument("--user", metavar="username", help="only this user's sessions")
    commands.add_parser("disable", help="turn the MCP server off (503 everywhere)", out=out)
    commands.add_parser("enable", help="turn the MCP server back on", out=out)
    commands.add_parser("rotate-signing-key", help="invalidate every access token (refresh still works)", out=out)
    commands.add_parser("status", help="kill switch, users, sessions and signing key", out=out)
    return parser


class _Admin:
    """The CLI's view of the auth store; the API client is made only when audit events are recorded or sent."""

    def __init__(self, settings: Settings, db: AuthDB, *, out: TextIO, clock: Callable[[], float],
                 api_transport: Optional[httpx.AsyncBaseTransport]) -> None:
        self.settings = settings
        self.db = db
        self.out = out
        self.clock = clock
        self.keyring = SigningKeyring(db, settings.master_key, clock=clock)
        # The CLI never presents a refresh token (no lookup, no rotate), so it can never detect reuse.
        self.sessions = SessionService(db, AccessTokenIssuer(self.keyring, settings, clock=clock),
                                       settings.master_key, clock=clock, on_reuse=lambda event: None)
        self._api_transport = api_transport
        self._api: Optional[ApiClient] = None
        self._audit: Optional[AuditSink] = None

    def say(self, line: str) -> None:
        self.out.write(line + "\n")

    @property
    def audit(self) -> AuditSink:
        if self._audit is None:
            self._api = ApiClient(self.settings.api_base, self.settings.service_token,
                                  timeout_s=self.settings.api_timeout_s, transport=self._api_transport)
            self._audit = AuditSink(self.db, self._api, clock=self.clock)
        return self._audit

    def last_event_id(self) -> int:
        return self.db.query_one("SELECT COALESCE(MAX(id), 0) AS n FROM auth_events")["n"]

    def deliver(self, after_id: int) -> None:
        """Flush pending audit events; say so when an event recorded after after_id is still undelivered."""
        audit, api = self.audit, self._api

        async def flush_and_close() -> None:
            try:
                await audit.flush()
            finally:
                await api.aclose()

        asyncio.run(flush_and_close())
        if self.db.query_one(_UNDELIVERED_SINCE_SQL, (after_id, *_SENDABLE))["n"]:
            self.say("(audit delivery pending)")

    # --- commands: each returns an exit code ---

    def enroll(self, args: argparse.Namespace) -> int:
        try:
            link, expires_at = create_enrollment(self.db, self.settings, args.username, clock=self.clock)
        except ValueError as exc:
            self.say(f"Cannot enroll: {exc}")
            return EXIT_ERROR
        self.say(f"Enrollment link (single use, expires {_iso(expires_at)}):")
        self.say(link)
        return EXIT_OK

    def list_sessions(self, args: argparse.Namespace) -> int:
        found = self.sessions.list(include_inactive=args.all)
        if not found:
            self.say("No sessions." if args.all else "No active sessions.")
            return EXIT_OK
        now = self.clock()
        rows = [("ID", "USER", "CLIENT", "CREATED", "LAST USED", "EXPIRES", "STATUS")]
        rows += [(info.family_id[:SESSION_ID_DISPLAY_CHARS], info.username or "-", info.client_id,
                  _iso(info.created_at), _iso(info.last_used_at), _iso(info.abs_expires_at),
                  _session_status(info, now)) for info in found]
        for line in _table(rows):
            self.say(line)
        return EXIT_OK

    def revoke(self, args: argparse.Namespace) -> int:
        prefix = args.prefix
        if len(prefix) < SESSION_PREFIX_MIN_CHARS:
            self.say(f"Session id prefix must be at least {SESSION_PREFIX_MIN_CHARS} characters")
            return EXIT_ERROR
        matches = [info for info in self.sessions.list(include_inactive=True) if info.family_id.startswith(prefix)]
        if not matches:
            self.say(f"No session id starts with {prefix!r}")
            return EXIT_ERROR
        if len(matches) > 1:
            self.say(f"Session id prefix {prefix!r} matches {len(matches)} sessions; give more characters")
            return EXIT_ERROR
        info = matches[0]
        short = info.family_id[:SESSION_ID_DISPLAY_CHARS]
        status = _session_status(info, self.clock())
        if status != "active" or not self.sessions.revoke(info.family_id, ADMIN_REVOKE_REASON):
            self.say(f"Session {short} is not active ({status})")
            return EXIT_ERROR
        self.audit.record("session_revoked", session_id=info.family_id, user_id=info.user_id,
                          username=info.username, client_id=info.client_id, source=AUDIT_SOURCE)
        self.say(f"Revoked {short}")
        return EXIT_OK

    def revoke_all(self, args: argparse.Namespace) -> int:
        detail = {}
        user_id = None
        if args.user is not None:
            row = self.db.query_one("SELECT user_id FROM users WHERE username = ?", (args.user,))
            if row is None:
                self.say(f"No enrolled user named {args.user!r}")
                return EXIT_ERROR
            user_id = row["user_id"]
            detail = {"user_id": user_id, "username": args.user}
        count = self.sessions.revoke_all(ADMIN_REVOKE_REASON, user_id=user_id)
        if count:
            self.audit.record("session_revoked", count=count, source=AUDIT_SOURCE, **detail)
        self.say(f"Revoked {count} session(s)")
        return EXIT_OK

    def disable(self, args: argparse.Namespace) -> int:
        self.db.set_flag(DISABLED_FLAG, True)
        self.audit.record("disabled", source=AUDIT_SOURCE)
        self.say("MCP server disabled")
        return EXIT_OK

    def enable(self, args: argparse.Namespace) -> int:
        self.db.set_flag(DISABLED_FLAG, False)
        self.audit.record("enabled", source=AUDIT_SOURCE)
        self.say("MCP server enabled")
        return EXIT_OK

    def rotate_signing_key(self, args: argparse.Namespace) -> int:
        kid = self.keyring.rotate()
        self.audit.record("signing_key_rotated", kid=kid, source=AUDIT_SOURCE)
        self.say(f"New signing key {kid}; all access tokens are now invalid (refresh still works).")
        return EXIT_OK

    def status(self, args: argparse.Namespace) -> int:
        users = self.db.query_one("SELECT COUNT(*) AS n FROM users")["n"]
        kid = self.db.query_one(_CURRENT_KID_SQL)
        self.say("Disabled: " + ("yes" if self.db.get_flag(DISABLED_FLAG) else "no"))
        self.say(f"Enrolled users: {users}")
        self.say(f"Active sessions: {len(self.sessions.list())}")
        self.say("Signing key: " + (kid["kid"] if kid is not None else "none"))
        return EXIT_OK


def main(argv: Optional[list[str]] = None, *, env: Optional[Mapping[str, str]] = None,
         out: Optional[TextIO] = None, api_transport: Optional[httpx.AsyncBaseTransport] = None,
         clock: Callable[[], float] = time.time) -> int:
    """Run one admin command; returns the exit code (see the module docstring)."""
    out = sys.stdout if out is None else out
    try:
        args = _parser(out).parse_args(argv)
    except _Stop as stop:
        return stop.status
    try:
        settings = load_settings(os.environ if env is None else env)
    except SettingsError as exc:
        for line in exc.errors:
            out.write(line + "\n")
        return EXIT_SETTINGS
    db = AuthDB(settings.db_path)
    try:
        admin = _Admin(settings, db, out=out, clock=clock, api_transport=api_transport)
        commands = {
            "enroll": admin.enroll,
            "list-sessions": admin.list_sessions,
            "revoke": admin.revoke,
            "revoke-all": admin.revoke_all,
            "disable": admin.disable,
            "enable": admin.enable,
            "rotate-signing-key": admin.rotate_signing_key,
            "status": admin.status,
        }
        after_id = admin.last_event_id()
        code = commands[args.command](args)
        if code == EXIT_OK and args.command in MUTATING_COMMANDS:
            admin.deliver(after_id)
        return code
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

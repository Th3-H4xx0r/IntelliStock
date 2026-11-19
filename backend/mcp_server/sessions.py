"""Refresh-token families: rotation on every use, a 120 s reuse grace, reuse revocation.

A family is one sign-in: the user, the client and an absolute expiry 30 days out
that rotation never extends. Each refresh token is new_token("mcprt_"), stored only
as crypto.hash_token output, and is spent by its first rotation. The successor pair
that rotation mints is kept on the spent row for grace_s seconds, sealed under the
"refresh-grace" subkey with aad = the spent row's hash. Jarvis runs several
processes over one token file, so the same spent token presented again inside the
window gets that same pair back. Presented after the window it is reuse: the whole
family is revoked (reason "refresh_reuse") and on_reuse fires once.

Rotation is a compare-and-swap on rotated_at inside BEGIN IMMEDIATE, so two
rotations of one token, from two threads or two processes, never both mint: the
loser reads the winner's sealed pair and returns it. on_reuse runs after the
revocation has committed, outside any transaction.
"""
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional, Sequence

from mcp_server.authdb import AuthDB
from mcp_server.config import ACCESS_TOKEN_TTL_S, REFRESH_ABS_LIFETIME_S, REFRESH_REUSE_GRACE_S, SCOPE
from mcp_server.crypto import derive_key, hash_token, new_token, seal, unseal
from mcp_server.tokens import AccessTokenIssuer

REFRESH_TOKEN_PREFIX = "mcprt_"
REUSE_REASON = "refresh_reuse"
PURGE_AFTER_EXPIRY_S = 86_400

_TOKEN_SQL = (
    "SELECT t.token_hash, t.rotated_at, t.grace_blob, f.family_id, f.user_id, f.client_id, "
    "f.abs_expires_at, f.revoked_at "
    "FROM refresh_tokens t JOIN refresh_families f ON f.family_id = t.family_id "
    "WHERE t.token_hash = ?"
)


@dataclass(frozen=True)
class IssuedPair:
    # Kept out of repr: a repr ends up in logs.
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    scope: str


@dataclass(frozen=True)
class RefreshLookup:
    token_hash: str
    family_id: str
    user_id: str
    client_id: str
    abs_expires_at: float
    kind: Literal["active", "grace"]


@dataclass(frozen=True)
class SessionInfo:
    family_id: str
    user_id: str
    username: Optional[str]
    client_id: str
    created_at: float
    last_used_at: Optional[float]
    abs_expires_at: float
    revoked_at: Optional[float]
    revoked_reason: Optional[str]


@dataclass(frozen=True)
class ReuseEvent:
    family_id: str
    user_id: str
    client_id: str


class SessionService:
    """Refresh-token families on the auth store; see the module docstring for the rules."""

    def __init__(self, db: AuthDB, issuer: AccessTokenIssuer, master_key: bytes, *, clock,
                 on_reuse: Callable[[ReuseEvent], None], abs_lifetime_s: int = REFRESH_ABS_LIFETIME_S,
                 grace_s: int = REFRESH_REUSE_GRACE_S) -> None:
        self._db = db
        self._issuer = issuer
        self._grace_key = derive_key(master_key, "refresh-grace")
        self._clock = clock
        self._on_reuse = on_reuse
        self._abs_lifetime_s = abs_lifetime_s
        self._grace_s = grace_s

    def start(self, *, user_id: str, client_id: str) -> tuple[str, IssuedPair]:
        """A new family and its first pair: (family_id, pair)."""
        family_id = uuid.uuid4().hex
        with self._db.tx() as conn:
            now = self._clock()
            conn.execute(
                "INSERT INTO refresh_families (family_id, user_id, client_id, created_at, abs_expires_at, "
                "last_used_at, revoked_at, revoked_reason) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (family_id, user_id, client_id, now, now + self._abs_lifetime_s),
            )
            pair, _ = self._mint(conn, family_id, user_id, now)
        return family_id, pair

    def lookup(self, refresh_token: str) -> Optional[RefreshLookup]:
        """The token's family and whether it is unspent ("active") or a spent token inside its grace window.

        None for an unknown token or a revoked or expired family. A spent token past
        its window revokes the family and reports the reuse, then returns None.
        """
        if not isinstance(refresh_token, str) or not refresh_token.startswith(REFRESH_TOKEN_PREFIX):
            return None
        row = self._db.query_one(_TOKEN_SQL, (hash_token(refresh_token),))
        now = self._clock()
        if row is None or not self._family_live(row, now):
            return None
        if row["rotated_at"] is None:
            kind = "active"
        elif self._in_grace(row, now):
            kind = "grace"
        else:
            if self.revoke(row["family_id"], REUSE_REASON):
                self._on_reuse(_reuse_event(row))
            return None
        return RefreshLookup(token_hash=row["token_hash"], family_id=row["family_id"], user_id=row["user_id"],
                             client_id=row["client_id"], abs_expires_at=row["abs_expires_at"], kind=kind)

    def rotate(self, found: RefreshLookup) -> Optional[IssuedPair]:
        """The next pair for a lookup: a new one for an active token, the kept one inside the grace window.

        The family is re-read first, so a lookup taken before a revocation or the
        absolute expiry gets None. A grace lookup whose window has closed since also
        gets None; presenting the token again is then reuse.
        """
        if found.kind == "grace":
            row = self._db.query_one(_TOKEN_SQL, (found.token_hash,))
            now = self._clock()
            if row is None or not self._family_live(row, now) or not self._in_grace(row, now):
                return None
            return self._kept_pair(row, now)
        reused = None
        with self._db.tx() as conn:
            now = self._clock()
            # Read under the write lock: this row is current, including a concurrent winner's commit.
            row = conn.execute(_TOKEN_SQL, (found.token_hash,)).fetchone()
            if row is None or not self._family_live(row, now):
                return None
            spent = conn.execute(
                "UPDATE refresh_tokens SET rotated_at = ? WHERE token_hash = ? AND rotated_at IS NULL",
                (now, found.token_hash),
            )
            if spent.rowcount == 0:  # lost the race to another rotation of this token
                if self._in_grace(row, now):
                    return self._kept_pair(row, now)
                if self._revoke_where(conn, "family_id = ?", (row["family_id"],), REUSE_REASON, now):
                    reused = _reuse_event(row)
            else:
                pair, exp = self._mint(conn, row["family_id"], row["user_id"], now)
                kept = json.dumps({"access_token": pair.access_token, "refresh_token": pair.refresh_token,
                                   "exp": exp})
                conn.execute(
                    "UPDATE refresh_tokens SET grace_blob = ?, grace_until = ? WHERE token_hash = ?",
                    (seal(self._grace_key, kept, found.token_hash), now + self._grace_s, found.token_hash),
                )
                conn.execute("UPDATE refresh_families SET last_used_at = ? WHERE family_id = ?",
                             (now, row["family_id"]))
                return pair
        if reused is not None:
            self._on_reuse(reused)
        return None

    def is_active(self, family_id: str) -> bool:
        """True while the family exists, is not revoked and is short of its absolute expiry."""
        row = self._db.query_one("SELECT abs_expires_at, revoked_at FROM refresh_families WHERE family_id = ?",
                                 (family_id,))
        return row is not None and self._family_live(row, self._clock())

    def revoke(self, family_id: str, reason: str) -> bool:
        """Revoke one family; False if it is unknown or already revoked."""
        with self._db.tx() as conn:
            return self._revoke_where(conn, "family_id = ?", (family_id,), reason, self._clock()) > 0

    def revoke_all(self, reason: str, *, user_id: Optional[str] = None) -> int:
        """Revoke every active family, or every active family of one user; the number revoked."""
        with self._db.tx() as conn:
            now = self._clock()
            if user_id is None:
                return self._revoke_where(conn, "abs_expires_at > ?", (now,), reason, now)
            return self._revoke_where(conn, "abs_expires_at > ? AND user_id = ?", (now, user_id), reason, now)

    def list(self, *, include_inactive: bool = False) -> List[SessionInfo]:
        """Active families, newest first; with include_inactive, revoked and expired ones too."""
        sql = (
            "SELECT f.family_id, f.user_id, u.username, f.client_id, f.created_at, f.last_used_at, "
            "f.abs_expires_at, f.revoked_at, f.revoked_reason "
            "FROM refresh_families f LEFT JOIN users u ON u.user_id = f.user_id"
        )
        params: Sequence = ()
        if not include_inactive:
            sql += " WHERE f.revoked_at IS NULL AND f.abs_expires_at > ?"
            params = (self._clock(),)
        sql += " ORDER BY f.created_at DESC, f.family_id"
        return [SessionInfo(**dict(row)) for row in self._db.query(sql, params)]

    def purge(self) -> None:
        """Drop sealed pairs whose window has closed; delete families a day past their absolute expiry."""
        with self._db.tx() as conn:
            now = self._clock()
            conn.execute("UPDATE refresh_tokens SET grace_blob = NULL WHERE grace_blob IS NOT NULL AND grace_until < ?",
                         (now,))
            # refresh_tokens rows go with their family (ON DELETE CASCADE).
            conn.execute("DELETE FROM refresh_families WHERE abs_expires_at < ?", (now - PURGE_AFTER_EXPIRY_S,))

    def _mint(self, conn: sqlite3.Connection, family_id: str, user_id: str, now: float) -> tuple[IssuedPair, int]:
        refresh = new_token(REFRESH_TOKEN_PREFIX)
        conn.execute("INSERT INTO refresh_tokens (token_hash, family_id, created_at) VALUES (?, ?, ?)",
                     (hash_token(refresh), family_id, now))
        access, exp = self._issuer.issue(user_id=user_id, family_id=family_id)
        return IssuedPair(access_token=access, refresh_token=refresh, expires_in=ACCESS_TOKEN_TTL_S, scope=SCOPE), exp

    def _kept_pair(self, row: sqlite3.Row, now: float) -> IssuedPair:
        kept = json.loads(unseal(self._grace_key, row["grace_blob"], row["token_hash"]))
        return IssuedPair(access_token=kept["access_token"], refresh_token=kept["refresh_token"],
                          expires_in=max(0, int(kept["exp"] - now)), scope=SCOPE)

    def _family_live(self, row: sqlite3.Row, now: float) -> bool:
        return row["revoked_at"] is None and now < row["abs_expires_at"]

    def _in_grace(self, row: sqlite3.Row, now: float) -> bool:
        return (row["rotated_at"] is not None and now - row["rotated_at"] <= self._grace_s
                and row["grace_blob"] is not None)

    def _revoke_where(self, conn: sqlite3.Connection, where: str, params: Sequence, reason: str,
                      now: float) -> int:
        """Revoke the unrevoked families matching where (a fixed clause, never caller text); the count."""
        # A revoked family's kept pairs can never be served again, so none stays at rest.
        conn.execute(
            "UPDATE refresh_tokens SET grace_blob = NULL WHERE grace_blob IS NOT NULL AND family_id IN "
            f"(SELECT family_id FROM refresh_families WHERE revoked_at IS NULL AND {where})",
            tuple(params),
        )
        cur = conn.execute(
            f"UPDATE refresh_families SET revoked_at = ?, revoked_reason = ? WHERE revoked_at IS NULL AND {where}",
            (now, reason) + tuple(params),
        )
        return cur.rowcount


def _reuse_event(row: sqlite3.Row) -> ReuseEvent:
    return ReuseEvent(family_id=row["family_id"], user_id=row["user_id"], client_id=row["client_id"])

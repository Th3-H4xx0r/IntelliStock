"""The MCP container's own auth state: one SQLite file, all of its DDL, transactions and flags.

The file is $MCP_DATA_DIR/mcp_auth.sqlite3 (spec section 5.2), created owner-only
(directory 0700, file 0600) because it holds sealed secrets and token hashes. The
server and the admin CLI each open their own AuthDB on the same file; WAL mode and
a 5 s busy timeout let them share it.

One AuthDB is one connection shared by the server's threads. Every statement runs
under one re-entrant lock, so a read on another thread waits for an open
transaction to finish instead of reading through it. tx() is BEGIN IMMEDIATE ...
COMMIT, rolled back if the block raises; a tx() opened inside another is a
savepoint, so the inner block rolls back alone.

Times are REAL unix seconds; every *_hash column holds crypto.hash_token output.
No other module writes DDL.
"""
import contextlib
import os
import sqlite3
import threading
from typing import Iterator, List, Optional, Sequence

SCHEMA_VERSION = 1

_DDL = (
    """CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        enrolled_at REAL,
        totp_secret_enc BLOB NOT NULL,
        totp_last_step INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS passkeys (
        credential_id BLOB PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
        public_key BLOB,
        sign_count INTEGER,
        transports TEXT,
        created_at REAL,
        last_used_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS enrollments (
        token_hash TEXT PRIMARY KEY,
        username TEXT,
        created_at REAL,
        expires_at REAL,
        opened_at REAL,
        cookie_hash TEXT,
        stage TEXT,
        user_id TEXT,
        webauthn_challenge BLOB,
        pending_passkey TEXT,
        pending_totp_enc BLOB,
        totp_shown_at REAL,
        completed_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_txns (
        txn_hash TEXT PRIMARY KEY,
        client_id TEXT,
        redirect_uri TEXT,
        code_challenge TEXT,
        state TEXT,
        scopes TEXT,
        resource TEXT,
        created_at REAL,
        expires_at REAL,
        cookie_hash TEXT,
        stage TEXT CHECK (stage IN ('password', 'second_factor')),
        user_id TEXT,
        username TEXT,
        webauthn_challenge BLOB,
        attempts INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS auth_codes (
        code_hash TEXT PRIMARY KEY,
        client_id TEXT,
        redirect_uri TEXT,
        code_challenge TEXT,
        resource TEXT,
        scopes TEXT,
        user_id TEXT,
        expires_at REAL,
        used_at REAL,
        family_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS refresh_families (
        family_id TEXT PRIMARY KEY,
        user_id TEXT,
        client_id TEXT,
        created_at REAL,
        abs_expires_at REAL,
        last_used_at REAL,
        revoked_at REAL,
        revoked_reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS refresh_tokens (
        token_hash TEXT PRIMARY KEY,
        family_id TEXT NOT NULL REFERENCES refresh_families (family_id) ON DELETE CASCADE,
        created_at REAL,
        rotated_at REAL,
        grace_until REAL,
        grace_blob BLOB
    )""",
    """CREATE TABLE IF NOT EXISTS signing_keys (
        kid TEXT PRIMARY KEY,
        private_key_enc BLOB NOT NULL,
        created_at REAL,
        retired_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS login_failures (
        id INTEGER PRIMARY KEY,
        username TEXT,
        kind TEXT,
        at REAL
    )""",
    "CREATE INDEX IF NOT EXISTS login_failures_username_at ON login_failures (username, at)",
    "CREATE INDEX IF NOT EXISTS login_failures_at ON login_failures (at)",
    """CREATE TABLE IF NOT EXISTS lockouts (
        scope TEXT PRIMARY KEY,
        until REAL,
        level INTEGER,
        set_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_events (
        id INTEGER PRIMARY KEY,
        at REAL,
        event TEXT,
        detail TEXT,
        delivered_at REAL,
        dead_at REAL,
        attempts INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS server_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
)


class AuthDB:
    """The auth store on one SQLite file; see the module docstring for the locking rules."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._savepoints = 0
        parent = os.path.dirname(os.path.abspath(path))
        old_umask = os.umask(0o077)
        try:
            os.makedirs(parent, mode=0o700, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            try:
                os.chmod(path, 0o600)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = FULL")
                conn.execute("PRAGMA foreign_keys = ON")
                self._migrate(conn)
            except BaseException:
                conn.close()
                raise
        finally:
            os.umask(old_umask)
        self._conn = conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            found = conn.execute("PRAGMA user_version").fetchone()[0]
            if found > SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path}: auth store schema version {found} is newer than this build's "
                    f"{SCHEMA_VERSION}; refusing to open it"
                )
            for statement in _DDL:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction; commits when the block ends, rolls back if it raises."""
        with self._lock:
            conn = self._conn
            if conn.in_transaction:
                yield from self._savepoint(conn)
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _savepoint(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        self._savepoints += 1
        name = f"nested_{self._savepoints}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        else:
            conn.execute(f"RELEASE {name}")
        finally:
            self._savepoints -= 1

    def query(self, sql: str, params: Sequence = ()) -> List[sqlite3.Row]:
        """Every row of one statement; outside tx() a write commits on its own."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence = ()) -> Optional[sqlite3.Row]:
        """The first row of one statement, or None."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def get_flag(self, key: str) -> bool:
        """A server_state flag; a key never set reads as False."""
        row = self.query_one("SELECT value FROM server_state WHERE key = ?", (key,))
        return row is not None and row["value"] == "1"

    def set_flag(self, key: str, value: bool) -> None:
        self.query(
            "INSERT INTO server_state (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, "1" if value else "0"),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

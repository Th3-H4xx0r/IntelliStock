"""The SQLite auth store: file and connection settings, schema, transactions, flags."""
import os
import sqlite3
import stat
import threading

import pytest

from mcp_server.authdb import SCHEMA_VERSION, AuthDB

TABLES = {
    "users", "passkeys", "enrollments", "auth_txns", "auth_codes", "refresh_families",
    "refresh_tokens", "signing_keys", "login_failures", "lockouts", "auth_events", "server_state",
}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data" / "mcp_auth.sqlite3")


@pytest.fixture
def db(db_path):
    handle = AuthDB(db_path)
    yield handle
    handle.close()


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_creates_file_0600_and_wal(db, db_path):
    assert _mode(os.path.dirname(db_path)) == 0o700
    assert _mode(db_path) == 0o600
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    db.set_flag("disabled", True)  # a write, so the WAL file exists while the handle is open
    assert _mode(db_path + "-wal") == 0o600
    assert db.query_one("PRAGMA synchronous")[0] == 2  # FULL
    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    assert db.query_one("PRAGMA busy_timeout")[0] == 5000
    assert db.query_one("PRAGMA user_version")[0] == SCHEMA_VERSION == 1


def test_file_is_owner_only_from_creation(db_path, monkeypatch):
    # chmod after the fact leaves a window; the umask closes it, whatever the caller's umask is
    real_connect, modes = sqlite3.connect, []

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        modes.append(_mode(db_path))
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    previous = os.umask(0o022)
    try:
        AuthDB(db_path).close()
    finally:
        os.umask(previous)
    assert modes == [0o600]


def test_tightens_an_existing_loose_file(db_path):
    os.makedirs(os.path.dirname(db_path))
    sqlite3.connect(db_path).close()
    os.chmod(db_path, 0o644)
    handle = AuthDB(db_path)
    try:
        assert _mode(db_path) == 0o600
    finally:
        handle.close()


def test_all_tables_exist(db):
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    assert {row["name"] for row in rows} == TABLES
    indexes = db.query("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'login_failures' "
                       "AND sql IS NOT NULL")
    columns = {tuple(col["name"] for col in db.query(f"PRAGMA index_info({row['name']})")) for row in indexes}
    assert columns == {("username", "at"), ("at",)}


def test_reopen_keeps_rows_and_schema(db_path):
    first = AuthDB(db_path)
    first.query("INSERT INTO lockouts (scope, until, level, set_at) VALUES ('global', 1.5, 1, 1.0)")
    first.close()
    second = AuthDB(db_path)
    try:
        assert second.query_one("SELECT until FROM lockouts WHERE scope = 'global'")["until"] == 1.5
    finally:
        second.close()


def test_foreign_keys_cascade(db):
    with db.tx() as conn:
        conn.execute("INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc) VALUES ('u1', 'op', 1.0, ?)",
                     (b"sealed",))
        conn.execute("INSERT INTO passkeys (credential_id, user_id, public_key, sign_count, created_at) "
                     "VALUES (?, 'u1', ?, 0, 1.0)", (b"cred", b"pk"))
        conn.execute("INSERT INTO refresh_families (family_id, user_id, client_id, created_at, abs_expires_at) "
                     "VALUES ('f1', 'u1', 'jarvis', 1.0, 2.0)")
        conn.execute("INSERT INTO refresh_tokens (token_hash, family_id, created_at) VALUES ('h1', 'f1', 1.0)")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as conn:
            conn.execute("INSERT INTO passkeys (credential_id, user_id, public_key, created_at) "
                         "VALUES (?, 'nobody', ?, 1.0)", (b"cred2", b"pk"))
    db.query("DELETE FROM users WHERE user_id = 'u1'")
    db.query("DELETE FROM refresh_families WHERE family_id = 'f1'")
    assert db.query_one("SELECT count(*) FROM passkeys")[0] == 0
    assert db.query_one("SELECT count(*) FROM refresh_tokens")[0] == 0


def test_auth_txn_stage_is_checked(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.query("INSERT INTO auth_txns (txn_hash, stage) VALUES ('t1', 'done')")
    db.query("INSERT INTO auth_txns (txn_hash, stage) VALUES ('t2', 'second_factor')")
    assert db.query_one("SELECT attempts FROM auth_txns WHERE txn_hash = 't2'")["attempts"] == 0


def test_tx_commits(db, db_path):
    with db.tx() as conn:
        conn.execute("INSERT INTO server_state (key, value) VALUES ('a', 'x')")
        assert conn.in_transaction
    other = AuthDB(db_path)
    try:
        assert other.query_one("SELECT value FROM server_state WHERE key = 'a'")["value"] == "x"
    finally:
        other.close()


def test_tx_rolls_back_on_error(db):
    with pytest.raises(ValueError):
        with db.tx() as conn:
            conn.execute("INSERT INTO server_state (key, value) VALUES ('a', 'x')")
            raise ValueError("boom")
    assert db.query("SELECT * FROM server_state") == []
    assert db.query_one("SELECT * FROM server_state WHERE key = 'a'") is None
    with db.tx() as conn:  # the handle is usable again
        conn.execute("INSERT INTO server_state (key, value) VALUES ('b', 'y')")
    assert [tuple(row) for row in db.query("SELECT key, value FROM server_state")] == [("b", "y")]


def test_nested_tx_rolls_back_only_the_inner_block(db):
    with db.tx() as conn:
        conn.execute("INSERT INTO server_state (key, value) VALUES ('outer', '1')")
        with pytest.raises(ValueError):
            with db.tx() as inner:
                inner.execute("INSERT INTO server_state (key, value) VALUES ('inner', '1')")
                raise ValueError("boom")
        with db.tx() as inner:
            inner.execute("INSERT INTO server_state (key, value) VALUES ('kept', '1')")
    assert {row["key"] for row in db.query("SELECT key FROM server_state")} == {"outer", "kept"}


def test_tx_excludes_other_threads(db):
    inside, release, seen = threading.Event(), threading.Event(), []

    def writer():
        with db.tx() as conn:
            conn.execute("INSERT INTO server_state (key, value) VALUES ('k', 'uncommitted')")
            inside.set()
            release.wait(5)
            conn.execute("UPDATE server_state SET value = 'committed' WHERE key = 'k'")

    def reader():
        seen.append(db.query_one("SELECT value FROM server_state WHERE key = 'k'")["value"])

    write_thread = threading.Thread(target=writer)
    write_thread.start()
    assert inside.wait(5)
    read_thread = threading.Thread(target=reader)
    read_thread.start()
    read_thread.join(0.2)
    blocked = read_thread.is_alive()
    release.set()
    write_thread.join(5)
    read_thread.join(5)
    assert blocked, "a read on another thread ran inside the open transaction"
    assert seen == ["committed"]


def test_flags_default_false_and_persist_across_reopen(db_path):
    first = AuthDB(db_path)
    assert first.get_flag("disabled") is False
    first.set_flag("disabled", True)
    assert first.get_flag("disabled") is True
    first.close()
    second = AuthDB(db_path)
    try:
        assert second.get_flag("disabled") is True
        assert second.get_flag("other") is False
        second.set_flag("disabled", False)
        assert second.get_flag("disabled") is False
    finally:
        second.close()


def test_refuses_newer_schema(db_path):
    AuthDB(db_path).close()
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA user_version = 2")
    raw.close()
    with pytest.raises(RuntimeError, match="schema version 2"):
        AuthDB(db_path)
    raw = sqlite3.connect(db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        raw.close()


def test_two_handles_share_state(db_path):
    server = AuthDB(db_path)
    cli = AuthDB(db_path)
    try:
        cli.set_flag("disabled", True)
        assert server.get_flag("disabled") is True
        with server.tx() as conn:
            conn.execute("INSERT INTO auth_events (at, event, detail) VALUES (1.0, 'login_ok', '{}')")
        row = cli.query_one("SELECT event, attempts, delivered_at FROM auth_events")
        assert (row["event"], row["attempts"], row["delivered_at"]) == ("login_ok", 0, None)
    finally:
        cli.close()
        server.close()

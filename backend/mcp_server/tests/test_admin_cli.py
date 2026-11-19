"""The admin CLI: enrollment links, session listing and revocation, the kill switch, key rotation."""
import io
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import mcp_server
from conftest import FakeClock
from fake_api import BASE_URL, SERVICE_TOKEN, FakeApi
from mcp_server.admin import create_enrollment, main
from mcp_server.authdb import AuthDB
from mcp_server.crypto import hash_token
from mcp_server.sessions import SessionService
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring

START = 1_800_000_000.0  # 2027-01-15T08:00:00Z
DAY = 86_400


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def env(env):
    # The CLI's ApiClient must present what the fake API checks.
    return {**env, "MCP_API_BASE": BASE_URL, "MCP_SERVICE_TOKEN": SERVICE_TOKEN}


@pytest.fixture
def fake():
    return FakeApi()


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def keyring(db, settings, clock):
    return SigningKeyring(db, settings.master_key, clock=clock)


@pytest.fixture
def sessions(db, settings, clock, keyring):
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    return SessionService(db, issuer, settings.master_key, clock=clock, on_reuse=lambda event: None)


@pytest.fixture
def cli(env, fake, clock):
    """Run one command: (exit code, output lines)."""
    def run(*argv, transport=None):
        out = io.StringIO()
        code = main(list(argv), env=env, out=out, api_transport=transport or fake.transport(), clock=clock)
        return code, out.getvalue().splitlines()

    return run


def _add_user(db, user_id, username):
    db.query("INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc) VALUES (?, ?, ?, ?)",
             (user_id, username, START, b"sealed"))


def _add_family(db, family_id, user_id, *, created_at=START, abs_expires_at=START + 30 * DAY):
    db.query("INSERT INTO refresh_families (family_id, user_id, client_id, created_at, abs_expires_at) "
             "VALUES (?, ?, 'jarvis', ?, ?)", (family_id, user_id, created_at, abs_expires_at))


def _stored_bytes(path):
    """The database file plus its WAL: a fresh row sits in the WAL until a checkpoint."""
    return b"".join(Path(p).read_bytes() for p in (path, path + "-wal") if os.path.exists(p))


def _events(fake):
    return [body["event"] for body in fake.audit_events]


def _pending_rows(db):
    return db.query("SELECT event FROM auth_events WHERE delivered_at IS NULL AND dead_at IS NULL ORDER BY id")


def test_enroll_prints_single_use_link(cli, db, settings, clock):
    other_link, _ = create_enrollment(db, settings, "someone-else", clock=clock)

    code, lines = cli("enroll", "operator")

    assert code == 0
    assert lines[0] == "Enrollment link (single use, expires 2027-01-15T08:10:00Z):"
    assert len(lines) == 2
    link = lines[1]
    assert link.startswith(settings.public_base + "/enroll?t=mcpen_")
    token = link.split("?t=", 1)[1]
    row = db.query_one("SELECT * FROM enrollments WHERE token_hash = ?", (hash_token(token),))
    assert row["username"] == "operator"
    assert row["created_at"] == START
    assert row["expires_at"] == START + 600
    assert row["opened_at"] is None and row["completed_at"] is None
    assert row["stage"] == "password"
    stored = _stored_bytes(settings.db_path)
    assert token.encode("ascii") not in stored
    assert hash_token(token).encode("ascii") in stored  # positive control: the bytes read hold the row

    code, lines = cli("enroll", "operator")

    assert code == 0
    second = lines[1].split("?t=", 1)[1]
    assert second != token
    assert db.query_one("SELECT 1 FROM enrollments WHERE token_hash = ?", (hash_token(token),)) is None
    pending = db.query("SELECT token_hash FROM enrollments WHERE username = 'operator'")
    assert [r["token_hash"] for r in pending] == [hash_token(second)]
    # Another user's pending enrollment is not touched.
    other = other_link.split("?t=", 1)[1]
    assert db.query_one("SELECT 1 FROM enrollments WHERE token_hash = ?", (hash_token(other),)) is not None


def test_enroll_keeps_completed_enrollments_and_honours_ttl(db, settings, clock):
    link, _ = create_enrollment(db, settings, "operator", clock=clock)
    first = hash_token(link.split("?t=", 1)[1])
    db.query("UPDATE enrollments SET completed_at = ? WHERE token_hash = ?", (START, first))

    link, expires_at = create_enrollment(db, settings, "operator", clock=clock, ttl_s=30)

    assert expires_at == START + 30
    assert db.query_one("SELECT completed_at FROM enrollments WHERE token_hash = ?", (first,))["completed_at"] == START
    with pytest.raises(ValueError):
        create_enrollment(db, settings, "  ", clock=clock)


def test_list_and_revoke_by_prefix(cli, db, sessions, clock, fake):
    _add_user(db, "u-123", "operator")
    keep_id, _ = sessions.start(user_id="u-123", client_id="jarvis")
    clock.advance(60)
    gone_id, gone_pair = sessions.start(user_id="u-123", client_id="jarvis")
    _add_family(db, "e" * 32, "u-123", created_at=START - 31 * DAY, abs_expires_at=START - DAY)

    code, lines = cli("list-sessions")

    assert code == 0
    assert lines[0].split() == ["ID", "USER", "CLIENT", "CREATED", "LAST", "USED", "EXPIRES", "STATUS"]
    rows = [line.split() for line in lines[1:]]
    assert [r[0] for r in rows] == [gone_id[:12], keep_id[:12]]  # newest first; the expired one is hidden
    assert rows[0] == [gone_id[:12], "operator", "jarvis", "2027-01-15T08:01:00Z", "-",
                       "2027-02-14T08:01:00Z", "active"]

    code, lines = cli("revoke", gone_id[:8])

    assert (code, lines) == (0, ["Revoked " + gone_id[:12]])
    assert not sessions.is_active(gone_id) and sessions.is_active(keep_id)
    assert sessions.lookup(gone_pair.refresh_token) is None
    assert _events(fake) == ["session_revoked"]
    assert fake.audit_events[0]["detail"] == {"session_id": gone_id, "user_id": "u-123", "username": "operator",
                                              "client_id": "jarvis", "source": "admin_cli"}

    code, lines = cli("list-sessions")
    assert [line.split()[0] for line in lines[1:]] == [keep_id[:12]]

    code, lines = cli("list-sessions", "--all")
    statuses = {line.split()[0]: line.split()[-1] for line in lines[1:]}
    assert statuses == {gone_id[:12]: "revoked:admin", keep_id[:12]: "active", "e" * 12: "expired"}

    # Revoking it again, or revoking an expired session, is a session error.
    assert cli("revoke", gone_id)[0] == 1
    assert cli("revoke", "e" * 8)[0] == 1
    assert _events(fake) == ["session_revoked"]


def test_revoke_ambiguous_prefix_exit_1(cli, db, sessions, fake):
    _add_user(db, "u-123", "operator")
    _add_family(db, "abcdef01" + "1" * 24, "u-123")
    _add_family(db, "abcdef01" + "2" * 24, "u-123")
    _add_family(db, "fedcba98" + "3" * 24, "u-123")

    code, lines = cli("revoke", "abcdef01")
    assert code == 1
    assert "2 sessions" in lines[0]

    assert cli("revoke", "0123456789")[0] == 1   # unknown
    assert cli("revoke", "fedcba9")[0] == 1      # unique, but shorter than 8 characters
    assert all(sessions.is_active(fid) for fid in ("abcdef01" + "1" * 24, "abcdef01" + "2" * 24, "fedcba98" + "3" * 24))
    assert fake.requests == [] and _pending_rows(db) == []

    assert cli("revoke", "abcdef011")[1] == ["Revoked abcdef011111"]


def test_revoke_all_user_filter(cli, db, sessions, fake):
    _add_user(db, "u-123", "operator")
    _add_user(db, "u-456", "other")
    mine = [sessions.start(user_id="u-123", client_id="jarvis")[0] for _ in range(2)]
    theirs, _ = sessions.start(user_id="u-456", client_id="jarvis")

    assert cli("revoke-all", "--user", "operator") == (0, ["Revoked 2 session(s)"])
    assert not any(sessions.is_active(fid) for fid in mine)
    assert sessions.is_active(theirs)

    assert cli("revoke-all", "--user", "nobody")[0] == 1
    assert sessions.is_active(theirs)

    assert cli("revoke-all") == (0, ["Revoked 1 session(s)"])
    assert not sessions.is_active(theirs)
    assert cli("revoke-all") == (0, ["Revoked 0 session(s)"])

    details = [body["detail"] for body in fake.audit_events]
    assert [body["event"] for body in fake.audit_events] == ["session_revoked", "session_revoked"]
    assert details == [{"count": 2, "user_id": "u-123", "username": "operator", "source": "admin_cli"},
                       {"count": 1, "source": "admin_cli"}]


def test_disable_enable_flag_and_audit(cli, db, fake):
    assert cli("disable") == (0, ["MCP server disabled"])
    assert db.get_flag("disabled") is True
    assert _events(fake) == ["disabled"]

    assert cli("enable") == (0, ["MCP server enabled"])
    assert db.get_flag("disabled") is False
    assert _events(fake) == ["disabled", "enabled"]
    assert [body["detail"] for body in fake.audit_events] == [{"source": "admin_cli"}] * 2


def test_api_down_still_exits_0_and_delivers_later(cli, db, fake):
    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    down = httpx.MockTransport(unreachable)

    assert cli("disable", transport=down) == (0, ["MCP server disabled", "(audit delivery pending)"])
    assert db.get_flag("disabled") is True
    assert [r["event"] for r in _pending_rows(db)] == ["disabled"]

    # The next command with the API back delivers the backlog with its own event.
    assert cli("enable") == (0, ["MCP server enabled"])
    assert _events(fake) == ["disabled", "enabled"]
    assert _pending_rows(db) == []


def test_rotate_signing_key_invalidates_access_tokens(cli, db, settings, sessions, keyring, clock, fake):
    verifier = McpTokenVerifier(keyring, settings, clock=clock, session_active=sessions.is_active,
                                disabled=lambda: False)
    family_id, pair = sessions.start(user_id="u-123", client_id="jarvis")
    old_kid = keyring.current()[0]
    assert verifier.verify(pair.access_token) is not None

    code, lines = cli("rotate-signing-key")

    new_kid = db.query_one("SELECT kid FROM signing_keys WHERE retired_at IS NULL")["kid"]
    assert new_kid != old_kid
    assert (code, lines) == (0, [f"New signing key {new_kid}; all access tokens are now invalid "
                                 "(refresh still works)."])
    assert verifier.verify(pair.access_token) is None
    renewed = sessions.rotate(sessions.lookup(pair.refresh_token))
    assert verifier.verify(renewed.access_token).family_id == family_id
    # The rotation is recorded locally only: it is not in the contract's event list.
    assert [r["event"] for r in db.query("SELECT event FROM auth_events")] == ["signing_key_rotated"]
    assert fake.requests == []


def test_status_reports_flag_users_sessions_and_kid(cli, db, sessions, keyring, settings):
    assert cli("status") == (0, ["Disabled: no", "Enrolled users: 0", "Active sessions: 0", "Signing key: none"])
    assert db.query("SELECT kid FROM signing_keys") == []  # status never creates a key

    _add_user(db, "u-123", "operator")
    family_id, _ = sessions.start(user_id="u-123", client_id="jarvis")
    sessions.start(user_id="u-123", client_id="jarvis")
    sessions.revoke(family_id, "admin")
    db.set_flag("disabled", True)

    assert cli("status") == (0, ["Disabled: yes", "Enrolled users: 1", "Active sessions: 1",
                                 "Signing key: " + keyring.current()[0]])


def test_settings_error_exit_2(cli, env, settings):
    del env["MCP_PUBLIC_BASE"]
    env["MCP_TOTP_ENC_KEY"] = "not base64 !"

    code, lines = cli("status")

    assert code == 2
    assert "MCP_PUBLIC_BASE is required" in lines
    assert "MCP_TOTP_ENC_KEY must be base64 (standard or URL-safe)" in lines
    assert not any(SERVICE_TOKEN in line for line in lines)
    assert not os.path.exists(settings.db_path)


@pytest.mark.parametrize("argv", [[], ["frobnicate"], ["enroll"], ["revoke-all", "--user"], ["status", "extra"]])
def test_usage_errors_exit_1(cli, argv, settings):
    code, lines = cli(*argv)
    assert code == 1
    assert any(line.startswith("usage:") for line in lines)
    assert not os.path.exists(settings.db_path)


def test_help_exits_0(cli):
    code, lines = cli("--help")
    assert code == 0
    assert "rotate-signing-key" in "\n".join(lines)


def test_runs_as_module(env, tmp_path):
    """python -m mcp_server.admin reads os.environ and exits with main()'s code."""
    isolated = str(Path(mcp_server.__file__).parent.parent)  # conftest's directory holding only mcp_server
    child_env = {**env, "PYTHONPATH": isolated, "PATH": os.environ.get("PATH", "")}
    done = subprocess.run([sys.executable, "-m", "mcp_server.admin", "status"], env=child_env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "Disabled: no"
    done = subprocess.run([sys.executable, "-m", "mcp_server.admin", "status"],
                          env={**child_env, "MCP_RP_ID": ""}, cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 2
    assert "MCP_RP_ID is required" in done.stdout

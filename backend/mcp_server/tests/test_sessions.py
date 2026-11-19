"""Refresh-token families: rotation, the 120 s reuse grace, reuse revocation, lifetime, purge."""
import json
import os
import threading
from pathlib import Path

import pytest

from conftest import FakeClock
from mcp_server.authdb import AuthDB
from mcp_server.config import ACCESS_TOKEN_TTL_S, REFRESH_ABS_LIFETIME_S, REFRESH_REUSE_GRACE_S, SCOPE
from mcp_server.crypto import CryptoError, derive_key, hash_token, unseal
from mcp_server.sessions import IssuedPair, RefreshLookup, ReuseEvent, SessionInfo, SessionService
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring

USER = "u-1"
CLIENT = "jarvis"
START = 1_800_000_000.0  # a whole second, so expires_in arithmetic is exact
DAY = 86_400


@pytest.fixture
def clock():
    # Nothing in this module compares against the real clock (unlike the SDK's bearer backend).
    return FakeClock(START)


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def reuses():
    """Every ReuseEvent the service reported, in order."""
    return []


def _service(db, settings, clock, reuses, **kwargs):
    keyring = SigningKeyring(db, settings.master_key, clock=clock)
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    return SessionService(db, issuer, settings.master_key, clock=clock, on_reuse=reuses.append, **kwargs)


@pytest.fixture
def service(db, settings, clock, reuses):
    return _service(db, settings, clock, reuses)


def _stored_bytes(path):
    """The database file plus its WAL: a fresh row sits in the WAL until a checkpoint."""
    data = b""
    for candidate in (path, path + "-wal"):
        if os.path.exists(candidate):
            data += Path(candidate).read_bytes()
    return data


def _grace_blob(db, refresh_token):
    row = db.query_one("SELECT grace_blob FROM refresh_tokens WHERE token_hash = ?", (hash_token(refresh_token),))
    return row["grace_blob"]


def _refresh(service, refresh_token):
    """What the token endpoint does: lookup, then rotate."""
    found = service.lookup(refresh_token)
    return None if found is None else service.rotate(found)


def test_start_stores_only_hashes(service, db, settings):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    assert first.refresh_token.startswith("mcprt_")
    second = _refresh(service, first.refresh_token)
    stored = _stored_bytes(settings.db_path)
    for raw in (first.refresh_token, second.refresh_token, first.access_token, second.access_token):
        assert raw.encode("ascii") not in stored
    # Positive control: the hashes are in the bytes read, so the check above looked in the right place.
    for raw in (first.refresh_token, second.refresh_token):
        assert hash_token(raw).encode("ascii") in stored
    rows = db.query("SELECT token_hash FROM refresh_tokens WHERE family_id = ?", (family_id,))
    assert {row["token_hash"] for row in rows} == {hash_token(first.refresh_token), hash_token(second.refresh_token)}


def test_rotate_issues_new_pair_and_old_enters_grace(service, db, settings, clock, reuses):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    assert (first.expires_in, first.scope) == (ACCESS_TOKEN_TTL_S, SCOPE)
    found = service.lookup(first.refresh_token)
    assert found == RefreshLookup(token_hash=hash_token(first.refresh_token), family_id=family_id, user_id=USER,
                                  client_id=CLIENT, abs_expires_at=START + REFRESH_ABS_LIFETIME_S, kind="active")
    clock.advance(10)
    second = service.rotate(found)
    assert isinstance(second, IssuedPair)
    assert second.refresh_token.startswith("mcprt_")
    assert second.refresh_token != first.refresh_token and second.access_token != first.access_token
    assert (second.expires_in, second.scope) == (ACCESS_TOKEN_TTL_S, SCOPE)

    old = service.lookup(first.refresh_token)
    assert (old.kind, old.family_id) == ("grace", family_id)
    new = service.lookup(second.refresh_token)
    assert new == RefreshLookup(token_hash=hash_token(second.refresh_token), family_id=family_id, user_id=USER,
                                client_id=CLIENT, abs_expires_at=START + REFRESH_ABS_LIFETIME_S, kind="active")

    old_hash = hash_token(first.refresh_token)
    row = db.query_one("SELECT rotated_at, grace_until, grace_blob FROM refresh_tokens WHERE token_hash = ?",
                       (old_hash,))
    assert (row["rotated_at"], row["grace_until"]) == (START + 10, START + 10 + REFRESH_REUSE_GRACE_S)
    # The successor pair is kept sealed under the refresh-grace subkey and bound to the spent row.
    grace_key = derive_key(settings.master_key, "refresh-grace")
    kept = json.loads(unseal(grace_key, row["grace_blob"], old_hash))
    assert (kept["access_token"], kept["refresh_token"]) == (second.access_token, second.refresh_token)
    with pytest.raises(CryptoError):
        unseal(grace_key, row["grace_blob"], hash_token(second.refresh_token))

    # The new access token is a real one for this family.
    keyring = SigningKeyring(db, settings.master_key, clock=clock)
    verifier = McpTokenVerifier(keyring, settings, clock=clock, session_active=service.is_active,
                                disabled=lambda: False)
    verified = verifier.verify(second.access_token)
    assert (verified.user_id, verified.family_id) == (USER, family_id)
    assert service.list()[0].last_used_at == START + 10
    assert reuses == []


def test_reuse_within_grace_returns_same_pair(service, clock, reuses):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    second = _refresh(service, first.refresh_token)
    clock.advance(REFRESH_REUSE_GRACE_S)  # the window is inclusive at 120 s
    again = service.lookup(first.refresh_token)
    assert again.kind == "grace"
    replay = service.rotate(again)
    assert replay.access_token == second.access_token
    assert replay.refresh_token == second.refresh_token
    assert service.is_active(family_id)
    assert service.lookup(second.refresh_token).kind == "active"
    assert reuses == []


def test_reuse_after_grace_revokes_family_and_fires_on_reuse(service, db, clock, reuses):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    second = _refresh(service, first.refresh_token)
    clock.advance(121)
    assert service.lookup(first.refresh_token) is None
    assert service.is_active(family_id) is False
    assert service.lookup(second.refresh_token) is None
    assert reuses == [ReuseEvent(family_id=family_id, user_id=USER, client_id=CLIENT)]
    # Presenting the spent token again does not alert again: the family is already revoked.
    assert service.lookup(first.refresh_token) is None
    assert len(reuses) == 1
    [info] = service.list(include_inactive=True)
    assert (info.revoked_at, info.revoked_reason) == (START + 121, "refresh_reuse")
    assert service.list() == []
    # A revoked family keeps no sealed pair at rest.
    assert _grace_blob(db, first.refresh_token) is None


def test_stale_active_lookup_rotated_after_grace_is_reuse(service, clock, reuses):
    """A lookup that saw the token unspent, rotated after someone else spent it 121 s earlier."""
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    stale = service.lookup(first.refresh_token)
    assert _refresh(service, first.refresh_token) is not None
    clock.advance(121)
    assert service.rotate(stale) is None
    assert service.is_active(family_id) is False
    assert reuses == [ReuseEvent(family_id=family_id, user_id=USER, client_id=CLIENT)]


def test_grace_lookup_whose_window_closes_before_rotate_gets_nothing(service, clock, reuses):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    _refresh(service, first.refresh_token)
    clock.advance(REFRESH_REUSE_GRACE_S)
    found = service.lookup(first.refresh_token)
    assert found.kind == "grace"
    clock.advance(1)
    assert service.rotate(found) is None
    # Not revoked here: the presentation fell inside the window. The next one is reuse.
    assert service.is_active(family_id) and reuses == []
    assert service.lookup(first.refresh_token) is None
    assert service.is_active(family_id) is False and len(reuses) == 1


def test_lookup_ignores_unknown_and_malformed_tokens(service, reuses):
    service.start(user_id=USER, client_id=CLIENT)
    for token in ("mcprt_" + "A" * 43, "", "garbage", "mcpac_" + "A" * 43, None, 7):
        assert service.lookup(token) is None
    assert reuses == []


def test_absolute_lifetime_30_days(service, clock, reuses):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    clock.advance(REFRESH_ABS_LIFETIME_S - 100)
    second = _refresh(service, first.refresh_token)
    found = service.lookup(second.refresh_token)
    assert found.abs_expires_at == START + REFRESH_ABS_LIFETIME_S  # rotation never extends the family
    clock.advance(99)
    assert service.is_active(family_id)
    clock.advance(1)  # now == abs_expires_at
    assert service.is_active(family_id) is False
    assert service.lookup(second.refresh_token) is None
    assert service.rotate(found) is None  # a lookup taken before expiry does not rotate after it
    clock.advance(1)
    assert clock() - START == 2_592_001
    assert service.lookup(second.refresh_token) is None
    assert service.lookup(first.refresh_token) is None  # spent and expired: not reported as reuse
    assert reuses == []
    assert service.list() == []
    assert [info.family_id for info in service.list(include_inactive=True)] == [family_id]


@pytest.mark.parametrize("processes", [1, 2], ids=["two-threads-one-process", "two-processes"])
def test_concurrent_rotation_same_token_both_succeed(db, settings, clock, reuses, processes):
    first_service = _service(db, settings, clock, reuses)
    others = []
    if processes == 2:
        # A second process is a second AuthDB (its own connection and lock) on the same file.
        others.append(AuthDB(settings.db_path))
        services = [first_service, _service(others[0], settings, clock, reuses)]
    else:
        services = [first_service, first_service]
    try:
        family_id, pair = first_service.start(user_id=USER, client_id=CLIENT)
        barrier = threading.Barrier(2, timeout=10)
        kinds, results, errors = [None, None], [None, None], []

        def refresh(i):
            try:
                found = services[i].lookup(pair.refresh_token)
                kinds[i] = found.kind
                barrier.wait()  # both have seen the token unspent before either rotates
                results[i] = services[i].rotate(found)
            except BaseException as exc:  # surfaced by the assertion below
                errors.append(exc)
                barrier.abort()

        threads = [threading.Thread(target=refresh, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        for other in others:
            other.close()

    assert errors == []
    assert kinds == ["active", "active"]  # so one rotate lost the compare-and-swap
    assert results[0] is not None
    assert results[0] == results[1]
    assert first_service.is_active(family_id)
    assert reuses == []
    assert first_service.lookup(results[0].refresh_token).kind == "active"
    count = db.query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE family_id = ?", (family_id,))["n"]
    assert count == 2  # exactly one successor was minted


def test_revoke_and_revoke_all_by_user(service, db, settings, clock, reuses):
    db.query("INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc) VALUES (?, ?, ?, ?)",
             ("u-1", "op", START, b"sealed"))
    short = _service(db, settings, clock, reuses, abs_lifetime_s=1)
    expired, _ = short.start(user_id="u-1", client_id=CLIENT)
    f1, p1 = service.start(user_id="u-1", client_id=CLIENT)
    clock.advance(1)
    f2, p2 = service.start(user_id="u-1", client_id=CLIENT)
    clock.advance(1)
    f3, p3 = service.start(user_id="u-2", client_id=CLIENT)
    assert [info.family_id for info in service.list()] == [f3, f2, f1]  # newest first, active only

    stale = service.lookup(p1.refresh_token)
    assert service.revoke(f1, "admin") is True
    assert service.revoke(f1, "admin") is False  # already revoked
    assert service.revoke("no-such-family", "admin") is False
    assert service.is_active(f1) is False and service.is_active(f2)
    assert service.lookup(p1.refresh_token) is None
    assert service.rotate(stale) is None  # a lookup taken before the revocation no longer rotates

    p2b = _refresh(service, p2.refresh_token)
    assert _grace_blob(db, p2.refresh_token) is not None
    assert service.revoke_all("re-enrolled", user_id="u-1") == 1  # f1 already revoked, the expired one skipped
    assert service.is_active(f2) is False and service.is_active(f3)
    assert _grace_blob(db, p2.refresh_token) is None  # revocation drops the sealed pair
    assert service.lookup(p2.refresh_token) is None and service.lookup(p2b.refresh_token) is None
    assert service.revoke_all("operator") == 1
    assert service.is_active(f3) is False
    assert service.revoke_all("operator") == 0
    assert reuses == []

    assert service.list() == []
    infos = {info.family_id: info for info in service.list(include_inactive=True)}
    assert set(infos) == {expired, f1, f2, f3}
    assert infos[f1] == SessionInfo(family_id=f1, user_id="u-1", username="op", client_id=CLIENT,
                                    created_at=START, last_used_at=None,
                                    abs_expires_at=START + REFRESH_ABS_LIFETIME_S, revoked_at=START + 2,
                                    revoked_reason="admin")
    assert (infos[f2].revoked_reason, infos[f2].last_used_at) == ("re-enrolled", START + 2)
    assert (infos[f3].revoked_reason, infos[f3].username) == ("operator", None)  # no users row for u-2
    assert (infos[expired].revoked_at, infos[expired].revoked_reason) == (None, None)


def test_purge_drops_grace_blobs(service, db, clock):
    family_id, first = service.start(user_id=USER, client_id=CLIENT)
    second = _refresh(service, first.refresh_token)
    clock.advance(REFRESH_REUSE_GRACE_S)
    service.purge()
    assert _grace_blob(db, first.refresh_token) is not None  # still inside the window
    assert service.lookup(first.refresh_token).kind == "grace"
    clock.advance(1)
    service.purge()
    assert _grace_blob(db, first.refresh_token) is None
    assert service.is_active(family_id)  # purge revokes nothing
    assert service.lookup(second.refresh_token).kind == "active"

    # A family and its tokens are deleted one day after its absolute expiry, not before.
    clock.advance(REFRESH_ABS_LIFETIME_S - REFRESH_REUSE_GRACE_S - 1 + DAY)  # now == abs_expires_at + 1 day
    service.purge()
    assert [info.family_id for info in service.list(include_inactive=True)] == [family_id]
    clock.advance(1)
    live, live_pair = service.start(user_id=USER, client_id=CLIENT)
    service.purge()
    assert [row["family_id"] for row in db.query("SELECT family_id FROM refresh_families")] == [live]
    assert [row["token_hash"] for row in db.query("SELECT token_hash FROM refresh_tokens")] == [
        hash_token(live_pair.refresh_token)]


def test_grace_pair_expires_in_recomputed(service, db, settings, clock, reuses):
    _, first = service.start(user_id=USER, client_id=CLIENT)
    second = _refresh(service, first.refresh_token)
    assert second.expires_in == ACCESS_TOKEN_TTL_S
    clock.advance(30)
    replay = _refresh(service, first.refresh_token)
    assert (replay.access_token, replay.refresh_token) == (second.access_token, second.refresh_token)
    assert replay.expires_in == 870
    clock.advance(0.5)
    assert _refresh(service, first.refresh_token).expires_in == 869  # rounded down: never over-promised

    # Past the access token's own expiry (a long grace keeps the pair served): never negative.
    long_grace = _service(db, settings, clock, reuses, grace_s=1000)
    _, a = long_grace.start(user_id=USER, client_id=CLIENT)
    b = _refresh(long_grace, a.refresh_token)
    clock.advance(950)
    late = _refresh(long_grace, a.refresh_token)
    assert (late.access_token, late.expires_in) == (b.access_token, 0)
    assert reuses == []

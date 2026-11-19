"""The OAuth 2.1 authorization server: metadata, /authorize, /token, and the login-transaction helpers.

Every request goes through the SDK's own handlers over httpx.ASGITransport. A code
comes from provider.complete_login after a real /authorize; the sign-in pages that
call it in production arrive in Task 11.
"""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.server.auth.provider import TokenError
from starlette.applications import Starlette

from mcp_server.authdb import AuthDB
from mcp_server.config import ACCESS_TOKEN_TTL_S, AUTH_CODE_TTL_S, LOGIN_TXN_TTL_S, SCOPE
from mcp_server.crypto import hash_token
from mcp_server.oauth_provider import (
    IntelliStockOAuthProvider,
    LoginTxn,
    build_as_metadata,
    build_auth_routes,
    jarvis_client,
)
from mcp_server.sessions import ReuseEvent, SessionService
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring

USER = "u-123"
USERNAME = "operator"
STATE = "st-Jarvis.42"


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def reuses():
    """Every ReuseEvent the session service reported, in order."""
    return []


@pytest.fixture
def stack(db, settings, clock, reuses):
    keyring = SigningKeyring(db, settings.master_key, clock=clock)
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    sessions = SessionService(db, issuer, settings.master_key, clock=clock, on_reuse=reuses.append)
    verifier = McpTokenVerifier(keyring, settings, clock=clock, session_active=sessions.is_active,
                                disabled=lambda: db.get_flag("disabled"))
    provider = IntelliStockOAuthProvider(settings, db, sessions, verifier, clock=clock)
    return SimpleNamespace(keyring=keyring, sessions=sessions, verifier=verifier, provider=provider)


@pytest.fixture
def provider(stack):
    return stack.provider


@pytest.fixture
async def client(provider, settings):
    app = Starlette(routes=build_auth_routes(provider, settings))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_base) as http:
        yield http


def _pkce():
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def _authorize_params(settings, challenge, **overrides):
    params = {
        "response_type": "code",
        "client_id": settings.jarvis_client_id,
        "redirect_uri": settings.jarvis_redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": STATE,
        "scope": SCOPE,
        "resource": settings.resource_url,
    }
    params.update(overrides)
    return {key: value for key, value in params.items() if value is not None}


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def _authorize_error(response, settings):
    """The OAuth error code of a rejected /authorize, checking where it was delivered.

    With a known client and the registered redirect URI the SDK redirects the error back
    to Jarvis (RFC 6749 section 4.1.2.1) with the request's state; otherwise it answers 400 itself.
    """
    if response.status_code == 302:
        location = response.headers["location"]
        assert location.startswith(settings.jarvis_redirect_uri + "?")
        query = _query(location)
        assert query["state"] == STATE
        return query["error"]
    assert response.status_code == 400
    assert "location" not in response.headers
    return response.json()["error"]


def _txn_count(db):
    return db.query_one("SELECT COUNT(*) AS n FROM auth_txns")["n"]


async def _start_login(client, settings, **overrides):
    """A real /authorize: (txn_id, code_verifier)."""
    verifier, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge, **overrides))
    assert response.status_code == 302, response.text
    txn_id = _query(response.headers["location"])["txn"]
    return txn_id, verifier


def _pass_password(provider, txn_id):
    """What the password step of the sign-in pages does on success."""
    provider.update_txn(txn_id, stage="second_factor", user_id=USER, username=USERNAME)


async def _obtain_code(client, provider, settings):
    """(code, code_verifier) after /authorize and a completed sign-in."""
    txn_id, verifier = await _start_login(client, settings)
    _pass_password(provider, txn_id)
    redirect = provider.complete_login(txn_id, USER)
    assert redirect.startswith(settings.jarvis_redirect_uri + "?")
    query = _query(redirect)
    assert query["state"] == STATE
    assert query["code"].startswith("mcpac_")
    return query["code"], verifier


async def _exchange(client, settings, code, verifier):
    return await client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.jarvis_redirect_uri,
        "client_id": settings.jarvis_client_id,
        "code_verifier": verifier,
        "resource": settings.resource_url,
    })


async def _refresh(client, settings, refresh_token):
    return await client.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.jarvis_client_id,
        "resource": settings.resource_url,
    })


async def _signed_in(client, provider, settings):
    """The token response body of a full sign-in."""
    code, verifier = await _obtain_code(client, provider, settings)
    response = await _exchange(client, settings, code, verifier)
    assert response.status_code == 200, response.text
    return response.json()


def _stored_bytes(path):
    """The database file plus its WAL: a fresh row sits in the WAL until a checkpoint."""
    data = b""
    for candidate in (path, path + "-wal"):
        if os.path.exists(candidate):
            data += Path(candidate).read_bytes()
    return data


async def test_metadata_exact(client, settings):
    base = settings.public_base
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    assert response.json() == {
        "issuer": base,
        "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token",
        "scopes_supported": ["mcp"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }
    assert "registration_endpoint" not in response.json()
    assert "revocation_endpoint" not in response.json()
    # The SDK client compares the issuer byte for byte: no trailing slash anywhere.
    assert json.loads(build_as_metadata(settings).model_dump_json(exclude_none=True))["issuer"] == base


async def test_register_endpoint_absent(client, settings):
    response = await client.post("/register", json={"redirect_uris": [settings.jarvis_redirect_uri]})
    assert response.status_code == 404


async def test_revoke_endpoint_absent_and_no_cors(client, settings):
    response = await client.post("/revoke", data={"token": "x", "client_id": settings.jarvis_client_id})
    assert response.status_code == 404
    preflight = await client.options("/token", headers={"Origin": "https://evil.test",
                                                         "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 405
    metadata = await client.get("/.well-known/oauth-authorization-server", headers={"Origin": "https://evil.test"})
    for response in (preflight, metadata):
        assert "access-control-allow-origin" not in response.headers


async def test_authorize_redirects_to_login(client, provider, settings, db, clock):
    verifier, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge))
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(settings.public_base + "/login?txn=mcptx_")
    assert response.headers["cache-control"] == "no-store"
    txn_id = _query(location)["txn"]
    assert provider.get_txn(txn_id) == LoginTxn(
        txn_hash=hash_token(txn_id), client_id=settings.jarvis_client_id, redirect_uri=settings.jarvis_redirect_uri,
        code_challenge=challenge, state=STATE, scopes=["mcp"], resource=settings.resource_url,
        expires_at=clock() + LOGIN_TXN_TTL_S, cookie_hash=None, stage="password", user_id=None, username=None,
        webauthn_challenge=None, attempts=0,
    )
    assert txn_id.encode("ascii") not in _stored_bytes(settings.db_path)

    # No scope parameter: the txn gets the one scope there is. Neither request carries a client secret.
    txn_id, _ = await _start_login(client, settings, scope=None, state=None)
    txn = provider.get_txn(txn_id)
    assert (txn.scopes, txn.state) == (["mcp"], None)


async def test_authorize_unknown_client_400_no_redirect(client, settings, db):
    _, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge, client_id="evil"))
    assert response.status_code == 400
    assert "location" not in response.headers
    assert response.json()["error"] == "invalid_request"
    assert _txn_count(db) == 0


@pytest.mark.parametrize("redirect_uri", [
    "http://127.0.0.1:33419/callback",       # same host, other port
    "http://localhost:33418/callback",       # same port, other host
    "http://127.0.0.1:33418/callback/",      # trailing slash
    "http://127.0.0.1:33418/callback?x=1",   # added query
])
async def test_authorize_wrong_redirect_uri_400(client, settings, db, redirect_uri):
    _, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge, redirect_uri=redirect_uri))
    assert response.status_code == 400
    assert "location" not in response.headers
    assert response.json()["error"] == "invalid_request"
    assert _txn_count(db) == 0


async def test_authorize_missing_redirect_uri_rejected(client, settings, db):
    # OAuth 2.1 makes redirect_uri mandatory; the SDK would otherwise fill in the one registered URI.
    _, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge, redirect_uri=None))
    assert _authorize_error(response, settings) == "invalid_request"
    assert _txn_count(db) == 0


async def test_authorize_requires_pkce(client, settings, db):
    _, challenge = _pkce()
    for overrides in (
        {"code_challenge": None, "code_challenge_method": None},   # no PKCE at all
        {"code_challenge_method": "plain"},
        {"code_challenge": challenge[:-1]},                         # 42 characters
        {"code_challenge": challenge[:-1] + "="},                   # padded
        {"code_challenge": "+" + challenge[1:]},                    # not base64url
    ):
        response = await client.get("/authorize", params=_authorize_params(settings, challenge, **overrides))
        assert _authorize_error(response, settings) == "invalid_request", overrides
    assert _txn_count(db) == 0


async def test_authorize_requires_resource(client, settings, db):
    _, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge, resource=None))
    assert _authorize_error(response, settings) == "invalid_request"
    assert _query(response.headers["location"])["error_description"] == "resource is required"
    for other in (settings.public_base, settings.resource_url + "/", "https://evil.test/mcp"):
        response = await client.get("/authorize", params=_authorize_params(settings, challenge, resource=other))
        assert _authorize_error(response, settings) == "invalid_target", other
    assert _txn_count(db) == 0


async def test_authorize_bad_scope(client, settings, db):
    _, challenge = _pkce()
    for scope in ("admin", "mcp admin"):
        response = await client.get("/authorize", params=_authorize_params(settings, challenge, scope=scope))
        assert _authorize_error(response, settings) == "invalid_scope", scope
    assert _txn_count(db) == 0


async def test_token_exchange_issues_pair(client, provider, stack, settings):
    code, verifier = await _obtain_code(client, provider, settings)
    response = await _exchange(client, settings, code, verifier)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "scope", "refresh_token"}
    assert (body["token_type"], body["expires_in"], body["scope"]) == ("Bearer", ACCESS_TOKEN_TTL_S, "mcp")
    assert body["refresh_token"].startswith("mcprt_")
    verified = stack.verifier.verify(body["access_token"])
    assert verified is not None
    assert (verified.user_id, verified.client_id) == (USER, settings.jarvis_client_id)
    assert stack.sessions.is_active(verified.family_id)
    assert code.encode("ascii") not in _stored_bytes(settings.db_path)


async def test_token_wrong_verifier_invalid_grant(client, provider, settings):
    code, _ = await _obtain_code(client, provider, settings)
    other_verifier, _ = _pkce()
    response = await _exchange(client, settings, code, other_verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert "access_token" not in response.json()


async def test_code_replay_revokes_family(client, provider, stack, settings, db):
    code, verifier = await _obtain_code(client, provider, settings)
    first = await _exchange(client, settings, code, verifier)
    assert first.status_code == 200
    family_id = stack.verifier.verify(first.json()["access_token"]).family_id

    replay = await _exchange(client, settings, code, verifier)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert not stack.sessions.is_active(family_id)
    assert stack.verifier.verify(first.json()["access_token"]) is None
    refreshed = await _refresh(client, settings, first.json()["refresh_token"])
    assert refreshed.status_code == 400
    assert refreshed.json()["error"] == "invalid_grant"
    row = db.query_one("SELECT revoked_reason FROM refresh_families WHERE family_id = ?", (family_id,))
    assert row["revoked_reason"] == "code_replay"


async def test_concurrent_exchange_one_wins_and_family_revoked(client, provider, stack, settings, reuses):
    # Two exchanges of one code race (two threads, each with its own event loop): exactly one mints,
    # and the loser revokes the family the winner started, so the winner's pair is dead too.
    code, _ = await _obtain_code(client, provider, settings)
    jarvis = await provider.get_client(settings.jarvis_client_id)
    loaded = await provider.load_authorization_code(jarvis, code)
    barrier = threading.Barrier(2)
    outcomes = []

    def exchange():
        barrier.wait()
        try:
            outcomes.append(asyncio.run(provider.exchange_authorization_code(jarvis, loaded)))
        except TokenError as error:
            outcomes.append(error)

    threads = [threading.Thread(target=exchange) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    errors = [outcome for outcome in outcomes if isinstance(outcome, TokenError)]
    tokens = [outcome for outcome in outcomes if not isinstance(outcome, TokenError)]
    assert (len(errors), len(tokens)) == (1, 1)
    assert errors[0].error == "invalid_grant"
    assert stack.verifier.verify(tokens[0].access_token) is None
    assert (await _refresh(client, settings, tokens[0].refresh_token)).status_code == 400
    assert reuses == []  # a code replay is not a refresh-token reuse


async def test_code_expires_after_120s(client, provider, settings, clock):
    # Positive control: a code just short of its lifetime still works.
    code, verifier = await _obtain_code(client, provider, settings)
    clock.advance(AUTH_CODE_TTL_S - 1)
    assert (await _exchange(client, settings, code, verifier)).status_code == 200

    code, verifier = await _obtain_code(client, provider, settings)
    clock.advance(AUTH_CODE_TTL_S + 1)
    response = await _exchange(client, settings, code, verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_refresh_rotation_via_http(client, provider, stack, settings):
    first = await _signed_in(client, provider, settings)
    response = await _refresh(client, settings, first["refresh_token"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    second = response.json()
    assert (second["token_type"], second["expires_in"], second["scope"]) == ("Bearer", ACCESS_TOKEN_TTL_S, "mcp")
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    assert stack.verifier.verify(second["access_token"]).user_id == USER

    # Another Jarvis process presenting the spent token inside the grace window gets the same pair.
    again = await _refresh(client, settings, first["refresh_token"])
    assert again.status_code == 200
    assert (again.json()["access_token"], again.json()["refresh_token"]) == (
        second["access_token"], second["refresh_token"])

    third = await _refresh(client, settings, second["refresh_token"])
    assert third.status_code == 200
    assert third.json()["refresh_token"] not in (first["refresh_token"], second["refresh_token"])

    unknown = await _refresh(client, settings, "mcprt_" + secrets.token_urlsafe(32))
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "invalid_grant"


async def test_refresh_reuse_after_grace_revokes_and_records(client, provider, stack, settings, clock, reuses):
    first = await _signed_in(client, provider, settings)
    family_id = stack.verifier.verify(first["access_token"]).family_id
    second = (await _refresh(client, settings, first["refresh_token"])).json()

    clock.advance(121)
    reused = await _refresh(client, settings, first["refresh_token"])
    assert reused.status_code == 400
    assert reused.json()["error"] == "invalid_grant"
    assert reuses == [ReuseEvent(family_id=family_id, user_id=USER, client_id=settings.jarvis_client_id)]
    assert not stack.sessions.is_active(family_id)
    successor = await _refresh(client, settings, second["refresh_token"])
    assert successor.status_code == 400
    assert len(reuses) == 1


async def test_disabled_blocks_authorize_and_token(client, provider, stack, settings, db):
    signed_in = await _signed_in(client, provider, settings)
    code, verifier = await _obtain_code(client, provider, settings)
    db.set_flag("disabled", True)

    _, challenge = _pkce()
    response = await client.get("/authorize", params=_authorize_params(settings, challenge))
    assert _authorize_error(response, settings) == "temporarily_unavailable"

    exchanged = await _exchange(client, settings, code, verifier)
    assert exchanged.status_code == 400
    assert exchanged.json()["error"] == "invalid_grant"
    refreshed = await _refresh(client, settings, signed_in["refresh_token"])
    assert refreshed.status_code == 400
    assert refreshed.json() == {"error": "invalid_grant", "error_description": "server disabled"}

    # Refused, not spent: once re-enabled, the same code and refresh token work.
    db.set_flag("disabled", False)
    assert (await _exchange(client, settings, code, verifier)).status_code == 200
    assert (await _refresh(client, settings, signed_in["refresh_token"])).status_code == 200


async def test_revoke_token_revokes_family(client, provider, stack, settings):
    by_refresh = await _signed_in(client, provider, settings)
    jarvis = await provider.get_client(settings.jarvis_client_id)
    await provider.revoke_token(await provider.load_refresh_token(jarvis, by_refresh["refresh_token"]))
    assert stack.verifier.verify(by_refresh["access_token"]) is None

    by_access = await _signed_in(client, provider, settings)
    access = await provider.load_access_token(by_access["access_token"])
    assert access is not None and access.subject == USER
    await provider.revoke_token(access)
    assert await provider.load_access_token(by_access["access_token"]) is None
    assert (await _refresh(client, settings, by_access["refresh_token"])).status_code == 400


async def test_jarvis_is_the_only_client(provider, settings):
    client_info = await provider.get_client(settings.jarvis_client_id)
    assert client_info == jarvis_client(settings)
    assert [str(uri) for uri in client_info.redirect_uris] == [settings.jarvis_redirect_uri]
    assert (client_info.token_endpoint_auth_method, client_info.client_secret) == ("none", None)
    assert client_info.grant_types == ["authorization_code", "refresh_token"]
    assert (client_info.scope, client_info.client_name) == ("mcp", "Jarvis")
    assert await provider.get_client("other") is None
    with pytest.raises(NotImplementedError):
        await provider.register_client(client_info)


async def test_txn_cookie_binds_once(client, provider, settings):
    txn_id, _ = await _start_login(client, settings)
    cookie = provider.bind_txn_cookie(txn_id)
    assert cookie
    assert provider.get_txn(txn_id).cookie_hash == hash_token(cookie)
    assert provider.bind_txn_cookie(txn_id) is None
    assert provider.get_txn(txn_id).cookie_hash == hash_token(cookie)
    assert provider.bind_txn_cookie("mcptx_" + secrets.token_urlsafe(32)) is None


async def test_txn_expires_after_600s(client, provider, settings, clock):
    txn_id, _ = await _start_login(client, settings)
    clock.advance(LOGIN_TXN_TTL_S - 1)
    assert provider.get_txn(txn_id) is not None
    clock.advance(1)
    assert provider.get_txn(txn_id) is None
    assert provider.bind_txn_cookie(txn_id) is None
    with pytest.raises(LookupError):
        provider.complete_login(txn_id, USER)
    assert provider.get_txn("not-a-txn") is None


async def test_update_txn_sets_only_login_fields(client, provider, settings):
    txn_id, _ = await _start_login(client, settings)
    provider.update_txn(txn_id, stage="second_factor", user_id=USER, username=USERNAME,
                        webauthn_challenge=b"\x00challenge")
    txn = provider.get_txn(txn_id)
    assert (txn.stage, txn.user_id, txn.username, txn.webauthn_challenge) == (
        "second_factor", USER, USERNAME, b"\x00challenge")
    provider.update_txn(txn_id, webauthn_challenge=None)
    assert provider.get_txn(txn_id).webauthn_challenge is None
    for field in ("redirect_uri", "code_challenge", "client_id", "resource", "attempts", "cookie_hash"):
        with pytest.raises(ValueError):
            provider.update_txn(txn_id, **{field: "x"})
    assert provider.get_txn(txn_id).redirect_uri == settings.jarvis_redirect_uri


async def test_fail_txn_deletes_at_max(client, provider, settings):
    txn_id, _ = await _start_login(client, settings)
    for attempt in range(1, 5):
        assert provider.fail_txn(txn_id) is True
        assert provider.get_txn(txn_id).attempts == attempt
    assert provider.fail_txn(txn_id) is False
    assert provider.get_txn(txn_id) is None
    assert provider.fail_txn(txn_id) is False


async def test_complete_login_needs_password_stage_and_is_single_use(client, provider, settings, db):
    txn_id, _ = await _start_login(client, settings)
    with pytest.raises(LookupError):   # the password step has not passed
        provider.complete_login(txn_id, USER)
    _pass_password(provider, txn_id)
    with pytest.raises(LookupError):   # not the user the password step named
        provider.complete_login(txn_id, "u-other")
    redirect = provider.complete_login(txn_id, USER)
    assert _query(redirect)["state"] == STATE
    assert provider.get_txn(txn_id) is None
    with pytest.raises(LookupError):
        provider.complete_login(txn_id, USER)
    code = _query(redirect)["code"]
    row = db.query_one("SELECT * FROM auth_codes WHERE code_hash = ?", (hash_token(code),))
    assert (row["user_id"], row["client_id"], row["redirect_uri"], row["resource"]) == (
        USER, settings.jarvis_client_id, settings.jarvis_redirect_uri, settings.resource_url)
    assert (row["used_at"], row["family_id"]) == (None, None)


async def test_purge_drops_expired_txns_and_codes(client, provider, settings, db, clock):
    stale_txn, _ = await _start_login(client, settings)
    stale_code, _ = await _obtain_code(client, provider, settings)
    clock.advance(LOGIN_TXN_TTL_S)
    live_txn, _ = await _start_login(client, settings)
    live_code, _ = await _obtain_code(client, provider, settings)
    provider.purge()
    assert {row["txn_hash"] for row in db.query("SELECT txn_hash FROM auth_txns")} == {hash_token(live_txn)}
    assert {row["code_hash"] for row in db.query("SELECT code_hash FROM auth_codes")} == {hash_token(live_code)}
    assert stale_txn and stale_code

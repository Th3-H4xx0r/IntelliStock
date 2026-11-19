"""The sign-in pages: password, then a passkey or a TOTP code, then an authorization code for Jarvis.

Every request goes through Starlette over httpx.ASGITransport with the
authorization server's own routes in front, so each sign-in starts at a real
/authorize and ends, where it succeeds, with a real /token exchange. The API is
FakeApi; the throttle, the audit sink, TOTP, passkeys (against SoftAuthenticator)
and the login transactions all run for real on one AuthDB. Each httpx client is
one browser, with its own cookie jar.
"""
import asyncio
import base64
import hashlib
import html
import json
import logging
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp
import pytest
from starlette.applications import Starlette

from conftest import seed_enrolled_user
from fake_api import VERIFY_PASSWORD_PATH, FakeApi
from mcp_server.audit import AuditSink
from mcp_server.authdb import AuthDB
from mcp_server.config import LOGIN_TXN_TTL_S, SCOPE
from mcp_server.login import _UserLocks, login_routes
from mcp_server.oauth_provider import IntelliStockOAuthProvider, build_auth_routes
from mcp_server.pages import WebDeps, security_headers, static_routes
from mcp_server.passkeys import PasskeyService
from mcp_server.sessions import SessionService
from mcp_server.throttle import LoginThrottle
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring
from mcp_server.totp import TotpService
from webauthn_fake import b64url, b64url_decode

USER = "u-123"
PASSWORD = "correct horse"
STATE = "st-Jarvis.42"
EXPIRED = "This sign-in request expired. Start again from Jarvis."
OTHER_BROWSER = "This sign-in link was opened in another browser. Start again from Jarvis."
FAILED = "Sign-in failed."
UNAVAILABLE = "IntelliStock is unavailable. Try again shortly."
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "form-action 'self' http://127.0.0.1:33418; frame-ancestors 'none'; base-uri 'none'")
PAGE_HEADERS = {
    "content-security-policy": CSP,
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def fake():
    # newbie's password is right, but newbie never enrolled with the MCP server.
    return FakeApi(users={"operator": (PASSWORD, USER), "newbie": (PASSWORD, "u-456")})


@pytest.fixture
async def deps(settings, db, clock, fake):
    """The objects build_deps wires in production, the throttle's lockouts going to the audit sink."""
    api = fake.client()
    audit = AuditSink(db, api, clock=clock)
    keyring = SigningKeyring(db, settings.master_key, clock=clock)
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    sessions = SessionService(db, issuer, settings.master_key, clock=clock, on_reuse=lambda event: None)
    verifier = McpTokenVerifier(keyring, settings, clock=clock, session_active=sessions.is_active,
                                disabled=lambda: db.get_flag("disabled"))
    throttle = LoginThrottle(db, clock=clock, on_lockout=lambda scope, username, seconds: audit.record(
        "lockout", scope=scope, username=username, seconds=seconds))
    yield WebDeps(
        settings=settings,
        db=db,
        provider=IntelliStockOAuthProvider(settings, db, sessions, verifier, clock=clock),
        api=api,
        throttle=throttle,
        totp=TotpService(db, settings.master_key, clock=clock),
        passkeys=PasskeyService(db, rp_id=settings.rp_id, origin=settings.public_origin, clock=clock),
        audit=audit,
        sessions=sessions,
        clock=clock,
    )
    await api.aclose()


@pytest.fixture
def enrolled(deps):
    """operator (u-123) enrolled: (the authenticator holding the passkey, the TOTP secret)."""
    return seed_enrolled_user(deps.db, deps.passkeys, deps.totp)


@pytest.fixture
def app(deps):
    """One server: every browser in a test talks to this app."""
    return Starlette(routes=build_auth_routes(deps.provider, deps.settings) + login_routes(deps) + static_routes())


def _browser(app, settings):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_base)


@pytest.fixture
async def browser(app, settings):
    async with _browser(app, settings) as http:
        yield http


class _SlowPasswords:
    """verify-password answered after a pause, as a busy API answers; records calls and the most in flight at once."""

    def __init__(self, users):
        self.users = users
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body["username"])
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            self.in_flight -= 1
        known = self.users.get(body["username"])
        if known is None or known[0] != body["password"]:
            return httpx.Response(401, json={"detail": "invalid credentials"})
        return httpx.Response(200, json={"user_id": known[1], "username": body["username"]})


def _origin(settings):
    return {"Origin": settings.public_origin}


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def _authorize(http, settings):
    """A real /authorize: (txn_id, code_verifier)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    response = await http.get("/authorize", params={
        "response_type": "code", "client_id": settings.jarvis_client_id, "redirect_uri": settings.jarvis_redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": STATE, "scope": SCOPE,
        "resource": settings.resource_url,
    })
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(settings.public_base + "/login?txn=")
    return _query(location)["txn"], verifier


async def _open(http, settings):
    """/authorize, then the sign-in page in this browser: (txn_id, code_verifier)."""
    txn_id, verifier = await _authorize(http, settings)
    response = await http.get("/login", params={"txn": txn_id})
    assert response.status_code == 200, response.text
    return txn_id, verifier


async def _password(http, settings, txn_id, username="operator", password=PASSWORD, headers=None):
    return await http.post("/login", data={"txn": txn_id, "username": username, "password": password},
                           headers=_origin(settings) if headers is None else headers)


async def _past_password(http, settings):
    """A sign-in through its password step: (txn_id, code_verifier)."""
    txn_id, verifier = await _open(http, settings)
    response = await _password(http, settings, txn_id)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/login/second?txn=" + txn_id
    return txn_id, verifier


async def _options(http, settings, txn_id, headers=None):
    return await http.post("/login/passkey/options", json={"txn": txn_id},
                           headers=_origin(settings) if headers is None else headers)


async def _send_assertion(http, settings, txn_id, credential, headers=None):
    return await http.post("/login/passkey", json={"txn": txn_id, "credential": credential},
                           headers=_origin(settings) if headers is None else headers)


async def _passkey(http, settings, txn_id, authenticator, extra_headers=None):
    """Options, an assertion from the authenticator, and its submission: the submission's response."""
    options = await _options(http, settings, txn_id)
    assert options.status_code == 200, options.text
    return await _send_assertion(http, settings, txn_id, authenticator.get(options.text),
                                 headers={**_origin(settings), **(extra_headers or {})})


async def _totp(http, settings, txn_id, code, headers=None):
    return await http.post("/login/totp", data={"txn": txn_id, "code": code},
                           headers=_origin(settings) if headers is None else headers)


def _code(secret, clock):
    return pyotp.TOTP(secret).at(clock())


def _wrong_code(secret, clock):
    """Six digits that are no step's code inside the +-1 window."""
    valid = {pyotp.TOTP(secret).at(clock() + offset) for offset in (-30, 0, 30)}
    return next(code for code in ("%06d" % n for n in range(1_000_000)) if code not in valid)


async def _exchange(http, settings, code, verifier):
    return await http.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": settings.jarvis_redirect_uri,
        "client_id": settings.jarvis_client_id, "code_verifier": verifier, "resource": settings.resource_url,
    })


def _events(db):
    """Every recorded audit event, oldest first: [(event, detail)]."""
    rows = db.query("SELECT event, detail FROM auth_events ORDER BY id")
    return [(row["event"], json.loads(row["detail"])) for row in rows]


def _failures(db):
    return [(row["username"], row["kind"]) for row in db.query("SELECT username, kind FROM login_failures ORDER BY id")]


def _verify_calls(fake):
    return sum(1 for request in fake.requests if request.url.path == VERIFY_PASSWORD_PATH)


def _last_step(db, user_id=USER):
    return db.query_one("SELECT totp_last_step FROM users WHERE user_id = ?", (user_id,))["totp_last_step"]


def _assert_untouched(deps, txn_id, stage="password"):
    """Nothing was spent or counted: the transaction is live at stage, with no attempts and no challenge."""
    txn = deps.provider.get_txn(txn_id)
    assert (txn.stage, txn.attempts, txn.webauthn_challenge) == (stage, 0, None)
    assert _failures(deps.db) == []


async def test_password_then_passkey_returns_code_redirect(browser, deps, settings, enrolled, db):
    authenticator, _ = enrolled
    txn_id, verifier = await _open(browser, settings)
    assert (await _password(browser, settings, txn_id, password="wrong horse")).status_code == 401
    response = await _password(browser, settings, txn_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/login/second?txn=" + txn_id
    txn = deps.provider.get_txn(txn_id)
    assert (txn.stage, txn.user_id, txn.username) == ("second_factor", USER, "operator")

    second = await browser.get("/login/second", params={"txn": txn_id})
    assert second.status_code == 200
    assert "Use passkey" in second.text and 'name="code"' in second.text

    response = await _passkey(browser, settings, txn_id, authenticator,
                              extra_headers={"Tailscale-User-Login": "operator@github"})
    assert response.status_code == 200, response.text
    redirect = response.json()["redirect"]
    assert redirect.startswith(settings.jarvis_redirect_uri + "?")
    query = _query(redirect)
    assert query["state"] == STATE
    assert query["code"].startswith("mcpac_")
    assert deps.provider.get_txn(txn_id) is None

    token = await _exchange(browser, settings, query["code"], verifier)
    assert token.status_code == 200, token.text
    assert token.json()["refresh_token"].startswith("mcprt_")
    assert _events(db)[-1] == ("login_ok", {"method": "passkey", "tailnet_user": "operator@github", "user_id": USER,
                                            "username": "operator"})
    assert _failures(db) == []  # a completed sign-in clears the failed password before it


async def test_password_then_totp(browser, deps, settings, enrolled, clock, db):
    _, secret = enrolled
    txn_id, verifier = await _past_password(browser, settings)
    code = _code(secret, clock)
    response = await _totp(browser, settings, txn_id, code[:3] + " " + code[3:])  # as an authenticator app shows it
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(settings.jarvis_redirect_uri + "?")
    query = _query(location)
    assert query["state"] == STATE
    assert query["code"].startswith("mcpac_")
    assert (await _exchange(browser, settings, query["code"], verifier)).status_code == 200
    assert _events(db)[-1] == ("login_ok", {"method": "totp", "tailnet_user": None, "user_id": USER,
                                            "username": "operator"})

    # The code's step is spent: the same code does not sign in again.
    txn_id, _ = await _past_password(browser, settings)
    again = await _totp(browser, settings, txn_id, code)
    assert again.status_code == 401
    assert FAILED in again.text
    assert 'name="code"' in again.text  # the second-factor page again, for another try
    assert _events(db)[-1] == ("totp_failed", {"user_id": USER, "username": "operator"})
    assert _failures(db) == [("operator", "totp")]
    assert deps.provider.get_txn(txn_id).attempts == 1


async def test_wrong_password_generic_message_and_audit(browser, deps, settings, enrolled, db):
    txn_id, _ = await _open(browser, settings)
    response = await _password(browser, settings, txn_id, password="wrong horse")
    assert response.status_code == 401
    assert FAILED in response.text
    assert "wrong horse" not in response.text
    assert 'value="operator"' in response.text and 'name="password"' in response.text  # the form again
    txn = deps.provider.get_txn(txn_id)
    assert (txn.stage, txn.attempts, txn.user_id) == ("password", 1, None)
    assert _events(db) == [("login_failed", {"reason": "invalid", "username": "operator"})]
    assert _failures(db) == [("operator", "password")]


async def test_unenrolled_user_same_generic_message(browser, deps, settings, enrolled, db):
    first_txn, _ = await _open(browser, settings)
    wrong = await _password(browser, settings, first_txn, "operator", "wrong horse")
    second_txn, _ = await _open(browser, settings)
    unenrolled = await _password(browser, settings, second_txn, "newbie", PASSWORD)  # right password, not enrolled
    assert (wrong.status_code, unenrolled.status_code) == (401, 401)
    # Byte for byte the same page once the transaction id and the typed username are set aside.
    assert (wrong.text.replace(first_txn, "TXN").replace("operator", "NAME")
            == unenrolled.text.replace(second_txn, "TXN").replace("newbie", "NAME"))
    assert FAILED in unenrolled.text
    assert deps.provider.get_txn(second_txn).stage == "password"
    assert _events(db) == [("login_failed", {"reason": "invalid", "username": "operator"}),
                           ("login_failed", {"reason": "not_enrolled", "username": "newbie"})]
    assert _failures(db) == [("operator", "password"), ("newbie", "password")]


async def test_cookie_from_other_browser_rejected(app, browser, deps, settings, enrolled, fake, clock):
    _, secret = enrolled
    txn_id, _ = await _open(browser, settings)
    forged = {**_origin(settings), "Cookie": "mcp_login=mcplc_" + secrets.token_urlsafe(32)}
    async with _browser(app, settings) as other:
        opened = await other.get("/login", params={"txn": txn_id})
        assert opened.status_code == 400
        assert OTHER_BROWSER in opened.text
        assert "set-cookie" not in opened.headers
        for headers in (None, forged):
            response = await _password(other, settings, txn_id, headers=headers)
            assert response.status_code == 400
            assert OTHER_BROWSER in response.text
    assert _verify_calls(fake) == 0
    _assert_untouched(deps, txn_id)

    # The browser that opened the link signs in; the second factor is bound to it as well.
    await _password(browser, settings, txn_id)
    code = _code(secret, clock)
    async with _browser(app, settings) as other:
        assert (await other.get("/login/second", params={"txn": txn_id})).status_code == 400
        assert (await _options(other, settings, txn_id)).status_code == 400
        assert (await _send_assertion(other, settings, txn_id, {})).status_code == 400
        rejected = await _totp(other, settings, txn_id, code, headers=forged)
        assert rejected.status_code == 400
        assert OTHER_BROWSER in rejected.text
    _assert_untouched(deps, txn_id, stage="second_factor")
    assert _last_step(deps.db) == 0  # the code was not spent
    assert (await _totp(browser, settings, txn_id, code)).status_code == 303


async def test_foreign_or_missing_origin_rejected(browser, deps, settings, enrolled, fake, clock):
    authenticator, secret = enrolled
    txn_id, _ = await _open(browser, settings)
    foreign = [{"Origin": "https://evil.test"}, {}, {"Origin": "null"}, {"Origin": settings.public_origin + "/"},
               {"Origin": "http://s7.tail.ts.net:8443"}, {"Origin": "https://s7.tail.ts.net"}]
    for headers in foreign:
        assert (await _password(browser, settings, txn_id, headers=headers)).status_code == 403, headers
    assert _verify_calls(fake) == 0
    _assert_untouched(deps, txn_id)

    await _password(browser, settings, txn_id)
    code = _code(secret, clock)
    options, _ = deps.passkeys.authentication_options(USER)
    assertion = authenticator.get(options)
    for headers in foreign:
        assert (await _options(browser, settings, txn_id, headers=headers)).status_code == 403, headers
        assert (await _send_assertion(browser, settings, txn_id, assertion, headers=headers)).status_code == 403
        assert (await _totp(browser, settings, txn_id, code, headers=headers)).status_code == 403, headers
    _assert_untouched(deps, txn_id, stage="second_factor")
    assert _last_step(deps.db) == 0
    assert (await _totp(browser, settings, txn_id, code)).status_code == 303


async def test_txn_dies_after_5_failures(browser, deps, settings, enrolled, clock, db):
    authenticator, secret = enrolled
    txn_id, _ = await _open(browser, settings)
    # Different usernames, so no per-user lockout steps in before the transaction's own limit.
    for n in (1, 2):
        assert (await _password(browser, settings, txn_id, f"nobody-{n}", "guess")).status_code == 401
        assert deps.provider.get_txn(txn_id).attempts == n
    assert (await _password(browser, settings, txn_id)).status_code == 303  # attempts carry over
    for n in (3, 4):
        assert (await _totp(browser, settings, txn_id, _wrong_code(secret, clock))).status_code == 401
        assert deps.provider.get_txn(txn_id).attempts == n
    authenticator.user_verified = False  # an assertion without the device's PIN or biometric
    fifth = await _passkey(browser, settings, txn_id, authenticator)
    assert fifth.status_code == 401
    assert fifth.json() == {"error": FAILED}
    assert deps.provider.get_txn(txn_id) is None
    assert [event for event, _ in _events(db)] == ["login_failed", "login_failed", "totp_failed", "totp_failed",
                                                   "passkey_failed"]

    response = await _totp(browser, settings, txn_id, _code(secret, clock))
    assert response.status_code == 400
    assert EXPIRED in response.text
    assert _last_step(db) == 0


async def test_lockout_after_5_password_failures(browser, deps, settings, enrolled, fake, db, caplog):
    caplog.set_level(logging.WARNING, logger="mcp_server.audit")
    txn_id, _ = await _open(browser, settings)
    for _ in range(5):
        assert (await _password(browser, settings, txn_id, password="guess")).status_code == 401
    lockouts = [detail for event, detail in _events(db) if event == "lockout"]
    assert lockouts == [{"scope": "user", "seconds": 900, "username": "operator"}]
    alerts = [record.getMessage() for record in caplog.records
              if record.name == "mcp_server.audit" and record.levelno == logging.WARNING]
    assert alerts == ["mcp auth alert: lockout " + json.dumps(lockouts[0], sort_keys=True)]

    # The fifth failure also ended that transaction; on a new one, the right password is refused unchecked.
    txn_id, _ = await _open(browser, settings)
    response = await _password(browser, settings, txn_id)
    assert response.status_code == 429
    assert "Too many attempts. Try again in 15 min." in response.text
    assert _verify_calls(fake) == 5
    txn = deps.provider.get_txn(txn_id)
    assert (txn.stage, txn.attempts) == ("password", 0)


async def test_right_password_does_not_reset_failures(browser, deps, settings, enrolled, clock, db):
    _, secret = enrolled
    first_txn, _ = await _open(browser, settings)
    for _ in range(4):
        assert (await _password(browser, settings, first_txn, password="guess")).status_code == 401
    # A guesser who has the password, on a fresh transaction, meets the lockout at the second factor.
    txn_id, _ = await _past_password(browser, settings)
    assert (await _totp(browser, settings, txn_id, _wrong_code(secret, clock))).status_code == 401
    assert [detail for event, detail in _events(db) if event == "lockout"] == [
        {"scope": "user", "seconds": 900, "username": "operator"}]
    assert (await _totp(browser, settings, txn_id, _code(secret, clock))).status_code == 429
    assert _last_step(db) == 0


async def test_throttle_checked_before_second_factor(browser, deps, settings, enrolled, clock, db):
    _, secret = enrolled
    txn_id, _ = await _past_password(browser, settings)
    for _ in range(5):  # guesses at operator's password from elsewhere lock the user
        deps.throttle.record_failure("operator", "password")
    response = await _totp(browser, settings, txn_id, _code(secret, clock))
    assert response.status_code == 429
    assert "Too many attempts. Try again in 15 min." in response.text
    options = await _options(browser, settings, txn_id)
    assert options.status_code == 429
    assert options.json() == {"error": "Too many attempts. Try again in 15 min."}
    assert _last_step(db) == 0
    txn = deps.provider.get_txn(txn_id)
    assert (txn.attempts, txn.webauthn_challenge) == (0, None)


async def test_second_factor_requires_password_stage(browser, deps, settings, enrolled, clock, db):
    authenticator, secret = enrolled
    txn_id, _ = await _open(browser, settings)
    code = _code(secret, clock)
    options, _ = deps.passkeys.authentication_options(USER)
    assertion = authenticator.get(options)
    assert (await browser.get("/login/second", params={"txn": txn_id})).status_code == 400
    assert (await _options(browser, settings, txn_id)).status_code == 400
    assert (await _send_assertion(browser, settings, txn_id, assertion)).status_code == 400
    assert (await _totp(browser, settings, txn_id, code)).status_code == 400
    _assert_untouched(deps, txn_id)
    assert _last_step(db) == 0
    assert _events(db) == []

    # Positive control: the same code works once the password step has passed.
    await _password(browser, settings, txn_id)
    assert (await _totp(browser, settings, txn_id, code)).status_code == 303


async def test_password_step_runs_once(browser, deps, settings, enrolled, fake):
    txn_id, _ = await _past_password(browser, settings)
    reopened = await browser.get("/login", params={"txn": txn_id})
    assert reopened.status_code == 303
    assert reopened.headers["location"] == "/login/second?txn=" + txn_id
    again = await _password(browser, settings, txn_id, "newbie", PASSWORD)
    assert again.status_code == 400
    assert _verify_calls(fake) == 1
    txn = deps.provider.get_txn(txn_id)
    assert (txn.stage, txn.user_id, txn.attempts) == ("second_factor", USER, 0)


async def test_passkey_challenge_is_spent_by_any_attempt(browser, deps, settings, enrolled):
    authenticator, _ = enrolled
    txn_id, _ = await _past_password(browser, settings)
    options = await _options(browser, settings, txn_id)
    assert deps.provider.get_txn(txn_id).webauthn_challenge is not None
    good = authenticator.get(options.text)
    signature = bytearray(b64url_decode(good["response"]["signature"]))
    signature[-1] ^= 1
    broken = {**good, "response": {**good["response"], "signature": b64url(bytes(signature))}}
    assert (await _send_assertion(browser, settings, txn_id, broken)).status_code == 401
    assert deps.provider.get_txn(txn_id).webauthn_challenge is None
    # The untouched assertion answered that same challenge, so it is refused too.
    assert (await _send_assertion(browser, settings, txn_id, good)).status_code == 401
    assert deps.provider.get_txn(txn_id).attempts == 2
    assert (await _passkey(browser, settings, txn_id, authenticator)).status_code == 200


async def test_security_headers_on_pages(browser, deps, settings, enrolled, clock):
    _, secret = enrolled
    assert {name.lower(): value for name, value in security_headers(settings).items()} == PAGE_HEADERS
    assert "form-action 'self' http://127.0.0.1:33418" in security_headers(settings)["Content-Security-Policy"]

    txn_id, _ = await _authorize(browser, settings)
    login = await browser.get("/login", params={"txn": txn_id})
    assert re.fullmatch(r"mcp_login=mcplc_[A-Za-z0-9_-]{43}; HttpOnly; Secure; SameSite=Strict; Path=/login; "
                        r"Max-Age=600", login.headers["set-cookie"])
    responses = {
        "login": login,
        "expired": await browser.get("/login", params={"txn": "mcptx_unknown"}),
        "failed": await _password(browser, settings, txn_id, password="guess"),
        "foreign": await _password(browser, settings, txn_id, headers={"Origin": "https://evil.test"}),
        "passed": await _password(browser, settings, txn_id),
        "second": await browser.get("/login/second", params={"txn": txn_id}),
        "options": await _options(browser, settings, txn_id),
        "totp_failed": await _totp(browser, settings, txn_id, _wrong_code(secret, clock)),
        "redirect": await _totp(browser, settings, txn_id, _code(secret, clock)),
    }
    assert {name: response.status_code for name, response in responses.items()} == {
        "login": 200, "expired": 400, "failed": 401, "foreign": 403, "passed": 303, "second": 200, "options": 200,
        "totp_failed": 401, "redirect": 303}
    for name, response in responses.items():
        assert {header: response.headers.get(header) for header in PAGE_HEADERS} == PAGE_HEADERS, name

    for name in ("login", "expired", "failed", "foreign", "second", "totp_failed"):
        page = responses[name].text
        assert responses[name].headers["content-type"] == "text/html; charset=utf-8", name
        # The one script and the one stylesheet, both from /static; no inline code, no other resource.
        assert re.findall(r"<script\b[^>]*>", page) == ['<script src="/static/mcp-auth.js" defer>'], name
        assert re.findall(r'\b(?:src|href)="([^"]*)"', page) == ["/static/mcp-auth.css", "/static/mcp-auth.js"], name
        assert "<style" not in page and " style=" not in page, name
        assert re.search(r"\son[a-z]+=", page) is None, name


async def test_static_assets(browser):
    script = await browser.get("/static/mcp-auth.js")
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    for needle in ("navigator.credentials.get", "navigator.credentials.create", "atob", "btoa"):
        assert needle in script.text, needle
    style = await browser.get("/static/mcp-auth.css")
    assert style.headers["content-type"] == "text/css; charset=utf-8"
    for response in (script, style):
        assert (response.status_code, response.headers["cache-control"]) == (200, "no-store")
        assert response.headers["x-content-type-options"] == "nosniff"
    assert (await browser.post("/static/mcp-auth.js")).status_code == 405


async def test_api_unavailable_shows_503(browser, deps, settings, enrolled, fake, db):
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    txn_id, _ = await _open(browser, settings)
    for status, body in ((0, refused), (500, {"detail": "boom"}), (422, {"detail": "unexpected"})):
        fake.set_route("POST", VERIFY_PASSWORD_PATH, status, body)
        response = await _password(browser, settings, txn_id)
        assert response.status_code == 503, status
        assert UNAVAILABLE in response.text
    _assert_untouched(deps, txn_id)  # an outage is nobody's failed attempt
    assert _events(db) == []


async def test_api_throttled_shows_429(browser, deps, settings, enrolled, fake, db):
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 429, {"detail": "slow down", "retry_after_s": 90})
    txn_id, _ = await _open(browser, settings)
    response = await _password(browser, settings, txn_id)
    assert response.status_code == 429
    assert "Too many attempts. Try again in 2 min." in response.text
    _assert_untouched(deps, txn_id)


async def test_txn_expires_after_600s(browser, deps, settings, enrolled, clock):
    _, secret = enrolled
    txn_id, _ = await _authorize(browser, settings)
    clock.advance(LOGIN_TXN_TTL_S)
    response = await browser.get("/login", params={"txn": txn_id})
    assert response.status_code == 400
    assert EXPIRED in response.text
    assert "set-cookie" not in response.headers

    # Opened in time, the sign-in still ends 600 s after /authorize: the password step passes at 599 s ...
    txn_id, _ = await _open(browser, settings)
    clock.advance(LOGIN_TXN_TTL_S - 1)
    assert (await _password(browser, settings, txn_id)).status_code == 303
    clock.advance(1)
    # ... and one second later the second factor finds it gone.
    for response in (await browser.get("/login/second", params={"txn": txn_id}),
                     await _totp(browser, settings, txn_id, _code(secret, clock))):
        assert response.status_code == 400
        assert EXPIRED in response.text
    for params in ({}, {"txn": ""}, {"txn": "not-a-txn"}):
        response = await browser.get("/login", params=params)
        assert (response.status_code, EXPIRED in response.text) == (400, True), params


async def test_username_is_escaped(browser, settings, enrolled):
    txn_id, _ = await _open(browser, settings)
    name = '"><script>alert(1)</script>'
    response = await _password(browser, settings, txn_id, name, "guess")
    assert response.status_code == 401
    assert "<script>alert(1)</script>" not in response.text
    assert html.escape(name) in response.text


async def test_malformed_requests_rejected(browser, deps, settings, enrolled):
    txn_id, _ = await _past_password(browser, settings)
    for content in (b"not json", b"[]", b'"txn"'):
        response = await browser.post("/login/passkey/options", content=content,
                                      headers={**_origin(settings), "Content-Type": "application/json"})
        assert response.status_code == 400, content
        assert "error" in response.json()
    for body in ({}, {"txn": 7}, {"txn": None}):
        assert (await browser.post("/login/passkey", json=body, headers=_origin(settings))).status_code == 400, body
    # A credential that is not an object is a failed attempt, like any assertion that does not verify.
    await _options(browser, settings, txn_id)
    assert (await _send_assertion(browser, settings, txn_id, "not a credential")).status_code == 401
    # Bodies stop at 16 KiB, as on the authorization server's own routes.
    big = await browser.post("/login/totp", data={"txn": txn_id, "code": "1" * 20_000}, headers=_origin(settings))
    assert big.status_code == 413
    assert deps.provider.get_txn(txn_id).attempts == 1


async def test_concurrent_password_guesses_cannot_outrun_the_lockout(app, browser, deps, settings, enrolled, fake,
                                                                     db):
    slow = _SlowPasswords(fake.users)
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 0, slow)
    clients = [_browser(app, settings) for _ in range(30)]
    try:
        txns = [(await _open(client, settings))[0] for client in clients]
        # Thirty browsers guess at once, under spellings of one username that the throttle counts as one.
        names = ["operator", "Operator", " OPERATOR "]
        responses = await asyncio.gather(*(
            _password(client, settings, txn_id, names[n % 3], "guess")
            for n, (client, txn_id) in enumerate(zip(clients, txns))))
    finally:
        for client in clients:
            await client.aclose()
    assert sorted(response.status_code for response in responses) == [401] * 5 + [429] * 25
    assert len(slow.calls) == 5
    assert slow.max_in_flight == 1  # one username's attempts run one at a time
    assert _failures(db) == [("operator", "password")] * 5
    assert [detail for event, detail in _events(db) if event == "lockout"] == [
        {"scope": "user", "seconds": 900, "username": "operator"}]

    # The right password after the lockout gets the lockout answer, and the API never sees it.
    txn_id, _ = await _open(browser, settings)
    response = await _password(browser, settings, txn_id)
    assert response.status_code == 429
    assert "Too many attempts. Try again in 15 min." in response.text
    assert len(slow.calls) == 5


async def test_concurrent_guesses_on_one_transaction_stop_at_five(browser, deps, settings, enrolled, fake, db):
    slow = _SlowPasswords(fake.users)
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 0, slow)
    txn_id, _ = await _open(browser, settings)
    # Distinct usernames, so the per-user throttle never steps in: only the transaction's own limit applies.
    responses = await asyncio.gather(*(_password(browser, settings, txn_id, f"nobody-{n}", "guess")
                                       for n in range(30)))
    assert sorted(response.status_code for response in responses) == [400] * 25 + [401] * 5
    assert len(slow.calls) == 5
    assert deps.provider.get_txn(txn_id) is None
    response = await _password(browser, settings, txn_id)
    assert response.status_code == 400
    assert EXPIRED in response.text
    assert len(slow.calls) == 5


async def test_concurrent_second_factor_guesses_stop_at_five(browser, deps, settings, enrolled, clock, db):
    _, secret = enrolled
    txn_id, _ = await _past_password(browser, settings)
    assert (await _options(browser, settings, txn_id)).status_code == 200
    wrong = _wrong_code(secret, clock)
    responses = await asyncio.gather(
        *[_totp(browser, settings, txn_id, wrong) for _ in range(15)],
        *[_send_assertion(browser, settings, txn_id, "not a credential") for _ in range(15)])
    statuses = [response.status_code for response in responses]
    assert statuses.count(401) == 5
    assert set(statuses) <= {400, 401, 429}
    assert len(_failures(db)) == 5
    assert len([event for event, _ in _events(db) if event in ("totp_failed", "passkey_failed")]) == 5
    assert deps.provider.get_txn(txn_id) is None
    assert _last_step(db) == 0


async def test_user_locks_serialise_one_name_and_forget_it():
    locks = _UserLocks()
    order = []

    async def hold(name, tag):
        async with locks.hold(name):
            order.append(("in", tag))
            await asyncio.sleep(0.01)
            order.append(("out", tag))

    await asyncio.gather(hold("operator", 1), hold("operator", 2), hold("other", 3))
    same_name = [step for step in order if step[1] != 3]
    assert same_name in ([("in", 1), ("out", 1), ("in", 2), ("out", 2)], [("in", 2), ("out", 2), ("in", 1), ("out", 1)])
    assert order.index(("in", 3)) < order.index(("out", 1))  # another name does not wait
    assert len(locks) == 0  # names are kept only while held or awaited

    # A waiter cancelled in the queue leaves nothing behind either.
    release = asyncio.Event()

    async def holder():
        async with locks.hold("operator"):
            await release.wait()

    first = asyncio.ensure_future(holder())
    await asyncio.sleep(0)
    waiter = asyncio.ensure_future(hold("operator", 4))
    await asyncio.sleep(0)
    assert len(locks) == 1
    waiter.cancel()
    release.set()
    await first
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert len(locks) == 0

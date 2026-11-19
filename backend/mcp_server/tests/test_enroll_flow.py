"""The enrollment pages: one single-use link, then the password, a passkey and a TOTP authenticator.

Every request goes through Starlette over httpx.ASGITransport, with the
authorization server and the sign-in pages mounted beside the enrollment pages,
so an enrollment is followed by a real Task 11 sign-in that ends in a real
/token exchange. Links come from admin.create_enrollment, as the CLI makes them.
The API is FakeApi; the throttle, the audit sink, sessions, TOTP and passkeys
(against SoftAuthenticator) all run for real on one AuthDB. Each httpx client is
one browser, with its own cookie jar.
"""
import asyncio
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp
import pytest
from starlette.applications import Starlette

from fake_api import VERIFY_PASSWORD_PATH, FakeApi
from mcp_server.admin import create_enrollment
from mcp_server.audit import AuditSink
from mcp_server.authdb import AuthDB
from mcp_server.config import ENROLL_TTL_S, SCOPE
from mcp_server.crypto import hash_token
from mcp_server.enroll import (
    EXPIRED,
    FOREIGN_ORIGIN,
    NOT_OPEN,
    PASSKEY_FAILED,
    WRONG_CODE,
    WRONG_PASSWORD,
    WRONG_STEP,
    enroll_routes,
)
from mcp_server.login import login_routes
from mcp_server.oauth_provider import IntelliStockOAuthProvider, build_auth_routes
from mcp_server.pages import WebDeps, static_routes
from mcp_server.passkeys import PasskeyService
from mcp_server.sessions import SessionService
from mcp_server.throttle import LoginThrottle
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring
from mcp_server.totp import TotpService
from webauthn_fake import SoftAuthenticator, b64url

USER = "u-123"
PASSWORD = "correct horse"
LINK_USED = "This enrollment link was already used or has expired."
ALREADY_SHOWN = "The authenticator code was already shown. Start a new enrollment with the admin CLI."
DONE = "Enrollment complete. You can close this page."
QR = "data:image/svg+xml"
PAGE_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "form-action 'self' http://127.0.0.1:33418; frame-ancestors 'none'; base-uri 'none'"),
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}
COOKIE = re.compile(r"mcp_enroll=mcpec_[A-Za-z0-9_-]{43}; HttpOnly; Secure; SameSite=Strict; Path=/enroll; Max-Age=600")
CLEARED_COOKIE = "mcp_enroll=; HttpOnly; Secure; SameSite=Strict; Path=/enroll; Max-Age=0"
SECRET = re.compile(r'<code id="totp-secret">([A-Z2-7]{32})</code>')


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def fake():
    return FakeApi(users={"operator": (PASSWORD, USER), "other": (PASSWORD, "u-456")})


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
def app(deps):
    """One server with the sign-in pages too: every browser in a test talks to this app."""
    return Starlette(routes=build_auth_routes(deps.provider, deps.settings) + login_routes(deps)
                     + enroll_routes(deps) + static_routes())


def _browser(app, settings):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_base)


@pytest.fixture
async def browser(app, settings):
    async with _browser(app, settings) as http:
        yield http


@pytest.fixture
def device(settings):
    """The operator's authenticator, playing this server's RP ID and origin."""
    return SoftAuthenticator(settings.rp_id, settings.public_origin)


def _origin(settings):
    return {"Origin": settings.public_origin}


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


def _link(deps, username="operator"):
    """A new enrollment link, as python -m mcp_server.admin enroll prints it: its path on this server."""
    link, _ = create_enrollment(deps.db, deps.settings, username, clock=deps.clock)
    assert link.startswith(deps.settings.public_base + "/enroll?t=mcpen_")
    return link[len(deps.settings.public_base):]


def _row(db, path):
    """The enrollments row behind a link path."""
    token = _query(path)["t"]
    return db.query_one("SELECT * FROM enrollments WHERE token_hash = ?", (hash_token(token),))


async def _open(http, settings, path):
    """The link's Start page, then its button: the POST /enroll/open response, a 303 to /enroll."""
    page = await http.get(path)
    assert page.status_code == 200, page.text
    assert "set-cookie" not in page.headers
    assert '<form method="post" action="/enroll/open">' in page.text and "Start enrollment" in page.text
    token = re.search(r'<input type="hidden" name="t" value="([^"]+)">', page.text).group(1)
    assert token == _query(path)["t"]
    response = await http.post("/enroll/open", data={"t": token}, headers=_origin(settings))
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/enroll"
    return response


async def _password(http, settings, password=PASSWORD, headers=None):
    return await http.post("/enroll/password", data={"password": password},
                           headers=_origin(settings) if headers is None else headers)


async def _options(http, settings, headers=None):
    return await http.post("/enroll/passkey/options", json={},
                           headers=_origin(settings) if headers is None else headers)


async def _send_registration(http, settings, credential, headers=None):
    return await http.post("/enroll/passkey", json={"credential": credential},
                           headers=_origin(settings) if headers is None else headers)


async def _register(http, settings, authenticator):
    """Options, a registration from the authenticator, and its submission: the submission's response."""
    options = await _options(http, settings)
    assert options.status_code == 200, options.text
    return await _send_registration(http, settings, authenticator.create(options.text))


async def _send_code(http, settings, code, headers=None):
    return await http.post("/enroll/totp", data={"code": code},
                           headers=_origin(settings) if headers is None else headers)


async def _show_secret(http):
    """The TOTP page's first view: the secret it shows."""
    page = await http.get("/enroll")
    assert page.status_code == 200, page.text
    assert QR in page.text
    return SECRET.search(page.text).group(1)


async def _to_passkey(http, deps, path=None):
    """A link opened in this browser and its password step passed: the link's path."""
    path = path or _link(deps)
    await _open(http, deps.settings, path)
    response = await _password(http, deps.settings)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/enroll"
    return path


async def _to_totp(http, deps, authenticator, path=None):
    """Through the passkey step: (the link's path, the TOTP secret the page showed)."""
    path = await _to_passkey(http, deps, path)
    response = await _register(http, deps.settings, authenticator)
    assert response.status_code == 200, response.text
    assert response.json() == {"redirect": "/enroll"}
    return path, await _show_secret(http)


async def _enroll(http, deps, authenticator):
    """A whole enrollment of operator: the TOTP secret."""
    _, secret = await _to_totp(http, deps, authenticator)
    response = await _send_code(http, deps.settings, _code(secret, deps.clock))
    assert response.status_code == 200, response.text
    assert DONE in response.text
    return secret


def _code(secret, clock):
    return pyotp.TOTP(secret).at(clock())


def _wrong_code(secret, clock):
    """Six digits that are no step's code inside the +-1 window."""
    valid = {pyotp.TOTP(secret).at(clock() + offset) for offset in (-30, 0, 30)}
    return next(code for code in ("%06d" % n for n in range(1_000_000)) if code not in valid)


async def _past_login_password(http, settings):
    """A Task 11 sign-in of operator through its password step: (txn_id, code_verifier)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    response = await http.get("/authorize", params={
        "response_type": "code", "client_id": settings.jarvis_client_id, "redirect_uri": settings.jarvis_redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st", "scope": SCOPE,
        "resource": settings.resource_url,
    })
    assert response.status_code == 302, response.text
    txn_id = _query(response.headers["location"])["txn"]
    assert (await http.get("/login", params={"txn": txn_id})).status_code == 200
    response = await http.post("/login", data={"txn": txn_id, "username": "operator", "password": PASSWORD},
                               headers=_origin(settings))
    assert response.status_code == 303, response.text
    return txn_id, verifier


async def _exchange(http, settings, redirect, verifier):
    return await http.post("/token", data={
        "grant_type": "authorization_code", "code": _query(redirect)["code"],
        "redirect_uri": settings.jarvis_redirect_uri, "client_id": settings.jarvis_client_id,
        "code_verifier": verifier, "resource": settings.resource_url,
    })


async def _sign_in(http, settings, *, authenticator=None, code=None):
    """A whole Task 11 sign-in, finished with the passkey or the TOTP code: the /token response, else the failure."""
    txn_id, verifier = await _past_login_password(http, settings)
    if authenticator is not None:
        options = await http.post("/login/passkey/options", json={"txn": txn_id}, headers=_origin(settings))
        assert options.status_code == 200, options.text
        assertion = authenticator.get(options.text)
        response = await http.post("/login/passkey", json={"txn": txn_id, "credential": assertion},
                                   headers=_origin(settings))
        if response.status_code != 200:
            return response
        redirect = response.json()["redirect"]
    else:
        response = await http.post("/login/totp", data={"txn": txn_id, "code": code}, headers=_origin(settings))
        if response.status_code != 303:
            return response
        redirect = response.headers["location"]
    return await _exchange(http, settings, redirect, verifier)


def _events(db):
    """Every recorded audit event, oldest first: [(event, detail)]."""
    rows = db.query("SELECT event, detail FROM auth_events ORDER BY id")
    return [(row["event"], json.loads(row["detail"])) for row in rows]


def _failures(db):
    return [(row["username"], row["kind"]) for row in db.query("SELECT username, kind FROM login_failures ORDER BY id")]


def _verify_calls(fake):
    return sum(1 for request in fake.requests if request.url.path == VERIFY_PASSWORD_PATH)


def _nothing_enrolled(db):
    assert db.query("SELECT * FROM users") == []
    assert db.query("SELECT * FROM passkeys") == []


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


async def test_full_enrollment_then_login_works(browser, deps, settings, device, clock, db):
    path = _link(deps)
    opened = await _open(browser, settings, path)
    assert COOKIE.fullmatch(opened.headers["set-cookie"])
    row = _row(db, path)
    assert (row["opened_at"], row["stage"]) == (clock(), "password")

    page = await browser.get("/enroll")  # the token has left the address bar; the cookie finds the enrollment
    assert page.status_code == 200
    assert 'value="operator"' in page.text and 'name="password"' in page.text
    response = await _password(browser, settings)
    assert (response.status_code, response.headers["location"]) == (303, "/enroll")
    row = _row(db, path)
    assert (row["stage"], row["user_id"], row["username"]) == ("passkey", USER, "operator")

    page = await browser.get("/enroll")
    assert page.status_code == 200
    assert 'data-webauthn="create"' in page.text and 'id="passkey-status"' in page.text
    options = await _options(browser, settings)
    assert options.status_code == 200
    assert options.headers["content-type"] == "application/json"
    parsed = json.loads(options.text)
    assert parsed["excludeCredentials"] == []
    assert parsed["authenticatorSelection"]["userVerification"] == "required"
    assert parsed["user"]["name"] == "operator"
    assert _row(db, path)["webauthn_challenge"] is not None
    response = await _send_registration(browser, settings, device.create(options.text))
    assert response.status_code == 200, response.text
    assert response.json() == {"redirect": "/enroll"}
    row = _row(db, path)
    assert (row["stage"], row["webauthn_challenge"]) == ("totp", None)
    pending = json.loads(row["pending_passkey"])
    assert pending["credential_id"] == b64url(device.credential_id)
    assert pending["transports"] == ["internal"]
    deps.totp.unseal(row["pending_totp_enc"], row["token_hash"])  # sealed to this row
    _nothing_enrolled(db)  # nothing is written to users or passkeys before the last step

    secret = await _show_secret(browser)
    code = _code(secret, clock)
    response = await _send_code(browser, settings, code[:3] + " " + code[3:])  # as an authenticator app shows it
    assert response.status_code == 200, response.text
    assert DONE in response.text
    assert response.headers["set-cookie"] == CLEARED_COOKIE

    user = db.query_one("SELECT * FROM users WHERE user_id = ?", (USER,))
    assert (user["username"], user["enrolled_at"]) == ("operator", clock())
    assert user["totp_last_step"] == int(clock() // 30)  # the confirming code's step is spent
    assert deps.totp.unseal(user["totp_secret_enc"], USER) == secret
    passkeys = db.query("SELECT * FROM passkeys")
    assert [(bytes(p["credential_id"]), p["user_id"], p["transports"]) for p in passkeys] == [
        (device.credential_id, USER, '["internal"]')]
    row = _row(db, path)
    assert row["completed_at"] == clock()
    assert (row["pending_passkey"], row["pending_totp_enc"], row["webauthn_challenge"]) == (None, None, None)
    assert _events(db) == [("enrolled", {"reenrolled": False, "sessions_revoked": 0, "user_id": USER,
                                         "username": "operator"})]

    # A Task 11 sign-in with the new passkey reaches /token.
    token = await _sign_in(browser, settings, authenticator=device)
    assert token.status_code == 200, token.text
    assert token.json()["refresh_token"].startswith("mcprt_")
    # The code that confirmed enrollment cannot sign in; the next step's code can.
    assert (await _sign_in(browser, settings, code=code)).status_code == 401
    clock.advance(30)
    assert (await _sign_in(browser, settings, code=_code(secret, clock))).status_code == 200


async def _press_start(http, settings, path):
    """The Start button's POST for a link, without first loading its page."""
    return await http.post("/enroll/open", data={"t": _query(path)["t"]}, headers=_origin(settings))


async def test_link_single_use(app, browser, deps, settings, device):
    path = _link(deps)
    assert (await browser.head(path)).status_code == 405
    await _open(browser, settings, path)
    async with _browser(app, settings) as other:
        for again in (await other.get(path), await _press_start(other, settings, path)):
            assert again.status_code == 400
            assert LINK_USED in again.text
            assert "set-cookie" not in again.headers
        assert (await other.get("/enroll")).status_code == 400
    # The browser that opened it, following the link or pressing Start again, is sent back and not rebound.
    for back in (await browser.get(path), await _press_start(browser, settings, path)):
        assert (back.status_code, back.headers["location"]) == (303, "/enroll")
        assert "set-cookie" not in back.headers

    await _password(browser, settings)
    await _register(browser, settings, device)
    response = await _send_code(browser, settings, _code(await _show_secret(browser), deps.clock))
    assert DONE in response.text
    for done in (await browser.get(path), await _press_start(browser, settings, path)):
        assert (done.status_code, LINK_USED in done.text) == (400, True)
    for bad in ("/enroll?t=", "/enroll?t=mcpen_unknown", "/enroll?t=not-a-token"):
        for response in (await browser.get(bad), await _press_start(browser, settings, bad)):
            assert (response.status_code, LINK_USED in response.text) == (400, True), bad
            assert "set-cookie" not in response.headers
    no_token = await browser.post("/enroll/open", data={}, headers=_origin(settings))
    assert (no_token.status_code, LINK_USED in no_token.text) == (400, True)


async def test_link_preview_leaves_it_unused(app, browser, deps, settings, db):
    path = _link(deps)
    async with _browser(app, settings) as preview:  # a link preview, say iMessage on a tailnet Mac
        for _ in range(2):
            page = await preview.get(path)
            assert page.status_code == 200
            assert "Start enrollment" in page.text and "operator" in page.text
            assert "set-cookie" not in page.headers
    row = _row(db, path)
    assert (row["opened_at"], row["cookie_hash"]) == (None, None)
    await _open(browser, settings, path)  # the operator's browser still opens it
    assert _row(db, path)["opened_at"] is not None


async def test_link_expires_after_600s(browser, deps, settings, clock, db):
    path = _link(deps)
    clock.advance(ENROLL_TTL_S - 1)
    assert (await browser.get(path)).status_code == 200  # the Start page at 599 s ...
    clock.advance(1)
    for response in (await browser.get(path), await _press_start(browser, settings, path)):  # ... gone at 600 s
        assert response.status_code == 400
        assert LINK_USED in response.text
        assert "set-cookie" not in response.headers
    assert _row(db, path)["opened_at"] is None

    # Opened in time, the enrollment still ends 600 s after the link was made: the password passes at 599 s ...
    path = _link(deps)
    await _open(browser, settings, path)
    clock.advance(ENROLL_TTL_S - 1)
    assert (await _password(browser, settings)).status_code == 303
    clock.advance(1)
    # ... and one second later every step finds it gone.
    page = await browser.get("/enroll")
    assert (page.status_code, EXPIRED in page.text) == (400, True)
    options = await _options(browser, settings)
    assert (options.status_code, options.json()) == (400, {"error": EXPIRED})
    assert _row(db, path)["webauthn_challenge"] is None


async def test_other_browser_cookie_rejected(app, browser, deps, settings, device, fake, db):
    path = _link(deps)
    await _open(browser, settings, path)
    forged = {**_origin(settings), "Cookie": "mcp_enroll=mcpec_" + secrets.token_urlsafe(32)}
    async with _browser(app, settings) as other:
        page = await other.get("/enroll")
        assert (page.status_code, NOT_OPEN in page.text) == (400, True)
        assert "Paste your enrollment link into the address bar." in page.text
        for headers in (None, forged):
            response = await _password(other, settings, headers=headers)
            assert response.status_code == 400
            assert NOT_OPEN in response.text
    assert _verify_calls(fake) == 0
    assert (_row(db, path)["stage"], _failures(db)) == ("password", [])

    # The browser that opened the link passes the password; the later steps are bound to it as well.
    await _password(browser, settings)
    async with _browser(app, settings) as other:
        for headers in (None, forged):
            options = await _options(other, settings, headers=headers)
            assert (options.status_code, options.json()) == (400, {"error": NOT_OPEN})
            response = await _send_registration(other, settings, {}, headers=headers)
            assert response.status_code == 400
            assert (await _send_code(other, settings, "123456", headers=headers)).status_code == 400
    row = _row(db, path)
    assert (row["stage"], row["webauthn_challenge"]) == ("passkey", None)
    assert (await _register(browser, settings, device)).status_code == 200


async def test_foreign_or_missing_origin_rejected(browser, deps, settings, device, fake, clock, db):
    foreign = [{"Origin": "https://evil.test"}, {}, {"Origin": "null"}, {"Origin": settings.public_origin + "/"},
               {"Origin": "http://s7.tail.ts.net:8443"}]
    path = _link(deps)
    for headers in foreign:
        response = await browser.post("/enroll/open", data={"t": _query(path)["t"]}, headers=headers)
        assert (response.status_code, FOREIGN_ORIGIN in response.text) == (403, True), headers
        assert "set-cookie" not in response.headers
    assert _row(db, path)["opened_at"] is None
    await _open(browser, settings, path)
    for headers in foreign:
        response = await _password(browser, settings, headers=headers)
        assert (response.status_code, FOREIGN_ORIGIN in response.text) == (403, True), headers
    assert (_verify_calls(fake), _row(db, path)["stage"]) == (0, "password")

    await _password(browser, settings)
    options = await _options(browser, settings)
    credential = device.create(options.text)
    for headers in foreign:
        refused = await _options(browser, settings, headers=headers)
        assert (refused.status_code, refused.json()) == (403, {"error": FOREIGN_ORIGIN}), headers
        assert (await _send_registration(browser, settings, credential, headers=headers)).status_code == 403
    assert _row(db, path)["stage"] == "passkey"
    assert (await _send_registration(browser, settings, credential)).status_code == 200  # its challenge was kept

    secret = await _show_secret(browser)
    for headers in foreign:
        assert (await _send_code(browser, settings, _code(secret, clock), headers=headers)).status_code == 403
    _nothing_enrolled(db)
    assert DONE in (await _send_code(browser, settings, _code(secret, clock))).text


async def test_wrong_password_does_not_advance(browser, deps, settings, fake, db):
    path = _link(deps)
    await _open(browser, settings, path)
    response = await _password(browser, settings, password="wrong horse")
    assert response.status_code == 401
    assert WRONG_PASSWORD in response.text
    assert 'name="password"' in response.text and "wrong horse" not in response.text
    row = _row(db, path)
    assert (row["stage"], row["user_id"]) == ("password", None)
    assert _failures(db) == [("operator", "enroll_password")]
    assert _events(db) == [("login_failed", {"reason": "invalid", "username": "operator", "via": "enroll"})]
    assert (await _password(browser, settings, password="")).status_code == 401  # blank: counted, never sent
    assert _verify_calls(fake) == 1

    assert (await _options(browser, settings)).status_code == 400  # still at the password step
    assert (await _password(browser, settings)).status_code == 303
    assert _row(db, path)["stage"] == "passkey"


async def test_concurrent_password_guesses_stop_at_lockout(browser, deps, settings, fake, db):
    slow = _SlowPasswords(fake.users)
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 0, slow)
    path = _link(deps)
    await _open(browser, settings, path)
    responses = await asyncio.gather(*(_password(browser, settings, password="guess") for _ in range(12)))
    assert sorted(response.status_code for response in responses) == [401] * 5 + [429] * 7
    assert (len(slow.calls), slow.max_in_flight) == (5, 1)
    assert _failures(db) == [("operator", "enroll_password")] * 5
    assert [detail for event, detail in _events(db) if event == "lockout"] == [
        {"scope": "user", "seconds": 900, "username": "operator"}]

    # The right password is refused unchecked while operator is locked out, here and on the sign-in page alike.
    response = await _password(browser, settings)
    assert response.status_code == 429
    assert "Too many attempts. Try again in 15 min." in response.text
    assert (len(slow.calls), _row(db, path)["stage"]) == (5, "password")


async def test_api_outage_or_throttle_counts_nothing(browser, deps, settings, fake, db):
    path = _link(deps)
    await _open(browser, settings, path)
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 500, {"detail": "boom"})
    response = await _password(browser, settings)
    assert response.status_code == 503
    assert "IntelliStock is unavailable. Try again shortly." in response.text
    fake.set_route("POST", VERIFY_PASSWORD_PATH, 429, {"detail": "slow down", "retry_after_s": 90})
    response = await _password(browser, settings)
    assert response.status_code == 429
    assert "Too many attempts. Try again in 2 min." in response.text
    assert (_row(db, path)["stage"], _failures(db), _events(db)) == ("password", [], [])


async def test_api_refusing_this_server_counts_nothing(browser, deps, settings, fake, db):
    # The gate's own 401 (MCP_ENABLED unset, a service token mismatch, a peer outside MCP_ALLOWED_CIDR) and any
    # 403 say nothing about the password: more refusals than a lockout takes, and nothing is counted.
    path = _link(deps)
    await _open(browser, settings, path)
    fake.service_enabled = False
    for attempt in range(6):
        if attempt == 3:
            fake.service_enabled = True
            fake.set_route("POST", VERIFY_PASSWORD_PATH, 403, {"detail": "mcp: operation not available"})
        response = await _password(browser, settings)
        assert response.status_code == 503, attempt
        assert ("Sign-in is unavailable because this server is misconfigured: IntelliStock refused its "
                "credentials. Your password was not checked.") in response.text
        assert "That password was not accepted." not in response.text
    assert (_row(db, path)["stage"], _failures(db), _events(db)) == ("password", [], [])
    assert deps.throttle.check("operator") is None

    fake.clear_route("POST", VERIFY_PASSWORD_PATH)
    response = await _password(browser, settings)
    assert response.status_code == 303, response.text
    assert _row(db, path)["stage"] == "passkey"


async def test_totp_qr_shown_once(browser, deps, settings, device, clock, db):
    path = await _to_passkey(browser, deps)
    assert (await _register(browser, settings, device)).status_code == 200
    first = await browser.get("/enroll")
    assert first.status_code == 200
    assert QR in first.text
    secret = SECRET.search(first.text).group(1)
    assert re.search(r'<img class="qr" src="data:image/svg\+xml;[^"]+" alt="[^"]+">', first.text)
    assert 'name="code"' in first.text
    assert _row(db, path)["totp_shown_at"] == clock()

    for _ in range(2):
        later = await browser.get("/enroll")
        assert later.status_code == 200
        assert QR not in later.text and secret not in later.text
        assert ALREADY_SHOWN in later.text
        assert 'name="code"' not in later.text
    # The page that showed the code can still finish.
    assert DONE in (await _send_code(browser, settings, _code(secret, clock))).text


async def test_wrong_totp_code_keeps_stage(browser, deps, settings, device, clock, db):
    path, secret = await _to_totp(browser, deps, device)
    for code in (_wrong_code(secret, clock), "", "12345", "abcdef", "١٢٣٤٥٦"):
        response = await _send_code(browser, settings, code)
        assert response.status_code == 401, code
        assert WRONG_CODE in response.text
        assert 'name="code"' in response.text  # the form again, for another try ...
        assert QR not in response.text and secret not in response.text  # ... but never the secret again
    row = _row(db, path)
    assert (row["stage"], row["completed_at"]) == ("totp", None)
    _nothing_enrolled(db)
    assert _events(db) == []
    assert DONE in (await _send_code(browser, settings, _code(secret, clock))).text


async def test_reenroll_replaces_factors_and_revokes_sessions(browser, deps, settings, device, clock, db):
    old_secret = await _enroll(browser, deps, device)
    old_credential = device.credential_id
    token = await _sign_in(browser, settings, authenticator=device)
    assert token.status_code == 200, token.text
    old_refresh = token.json()["refresh_token"]
    family_id = deps.sessions.lookup(old_refresh).family_id
    _, bystander = deps.sessions.start(user_id="u-456", client_id=settings.jarvis_client_id)

    # Re-enrolled on the same device: nothing is excluded, so the device holding the old passkey registers again.
    clock.advance(30)
    new_secret = await _enroll(browser, deps, device)
    assert device.credential_id != old_credential and new_secret != old_secret
    rows = db.query("SELECT credential_id FROM passkeys WHERE user_id = ?", (USER,))
    assert [bytes(row["credential_id"]) for row in rows] == [device.credential_id]
    assert deps.sessions.lookup(old_refresh) is None
    assert db.query_one("SELECT revoked_reason FROM refresh_families WHERE family_id = ?",
                        (family_id,))["revoked_reason"] == "re-enrolled"
    assert deps.sessions.lookup(bystander.refresh_token) is not None  # another user's session is untouched
    assert [detail for event, detail in _events(db) if event == "enrolled"] == [
        {"reenrolled": False, "sessions_revoked": 0, "user_id": USER, "username": "operator"},
        {"reenrolled": True, "sessions_revoked": 1, "user_id": USER, "username": "operator"}]

    # The old passkey no longer signs in, even offered by the device that still holds it.
    txn_id, _ = await _past_login_password(browser, settings)
    options = await browser.post("/login/passkey/options", json={"txn": txn_id}, headers=_origin(settings))
    offered = json.loads(options.text)
    offered["allowCredentials"] = [{"id": b64url(old_credential), "type": "public-key"}]
    old_assertion = device.get(json.dumps(offered))
    assert old_assertion["rawId"] == b64url(old_credential)
    response = await browser.post("/login/passkey", json={"txn": txn_id, "credential": old_assertion},
                                  headers=_origin(settings))
    assert response.status_code == 401
    # Nor does the old authenticator app; the new passkey and the new app do.
    clock.advance(30)
    assert (await _sign_in(browser, settings, code=_code(old_secret, clock))).status_code == 401
    assert (await _sign_in(browser, settings, code=_code(new_secret, clock))).status_code == 200
    assert (await _sign_in(browser, settings, authenticator=device)).status_code == 200


async def test_uv_false_registration_rejected(browser, deps, settings, device, db):
    path = await _to_passkey(browser, deps)
    options = await _options(browser, settings)
    touched_only = SoftAuthenticator(settings.rp_id, settings.public_origin, user_verified=False)
    refused = await _send_registration(browser, settings, touched_only.create(options.text))
    assert refused.status_code == 400
    assert refused.json() == {"error": PASSKEY_FAILED}
    row = _row(db, path)
    assert (row["stage"], row["pending_passkey"], row["pending_totp_enc"]) == ("passkey", None, None)
    assert row["webauthn_challenge"] is None  # spent by the refused registration
    # A good registration answering that spent challenge is refused too; one with fresh options passes.
    late = await _send_registration(browser, settings, device.create(options.text))
    assert (late.status_code, late.json()) == (400, {"error": PASSKEY_FAILED})
    for credential in ("not a credential", {"id": "x"}):
        await _options(browser, settings)
        assert (await _send_registration(browser, settings, credential)).status_code == 400
    assert (await _register(browser, settings, device)).status_code == 200
    assert _row(db, path)["stage"] == "totp"


async def test_steps_out_of_order_rejected(browser, deps, settings, device, fake, db):
    path = _link(deps)
    await _open(browser, settings, path)
    options = await _options(browser, settings)
    assert (options.status_code, options.json()) == (400, {"error": WRONG_STEP})
    assert (await _send_registration(browser, settings, {})).status_code == 400
    response = await _send_code(browser, settings, "123456")
    assert (response.status_code, WRONG_STEP in response.text) == (400, True)

    await _password(browser, settings)
    again = await _password(browser, settings)  # the password step runs once
    assert (again.status_code, WRONG_STEP in again.text) == (400, True)
    assert (await _send_code(browser, settings, "123456")).status_code == 400
    assert _verify_calls(fake) == 1

    await _register(browser, settings, device)
    # At the totp step, before the page has shown the secret, no code is taken.
    assert (await _send_code(browser, settings, "123456")).status_code == 400
    assert (await _options(browser, settings)).status_code == 400
    assert (await _password(browser, settings)).status_code == 400
    row = _row(db, path)
    assert (row["stage"], row["totp_shown_at"], row["completed_at"]) == ("totp", None, None)


async def test_newer_enrollment_replaces_older(browser, deps, settings, device, clock, db, monkeypatch):
    # A newer enroll for the same user deletes this unfinished row: its cookie finds nothing.
    _, secret = await _to_totp(browser, deps, device)
    _link(deps)
    response = await _send_code(browser, settings, _code(secret, clock))
    assert (response.status_code, NOT_OPEN in response.text) == (400, True)
    _nothing_enrolled(db)

    # The CLI, another process on the file, can do it between the code check and the final transaction.
    _, secret = await _to_totp(browser, deps, device, path=_link(deps))
    real_match = deps.totp.match_step
    cli = AuthDB(settings.db_path)

    def match_then_reenroll(*args):
        step = real_match(*args)
        create_enrollment(cli, settings, "operator", clock=clock)
        return step

    monkeypatch.setattr(deps.totp, "match_step", match_then_reenroll)
    try:
        response = await _send_code(browser, settings, _code(secret, clock))
    finally:
        cli.close()
    assert (response.status_code, NOT_OPEN in response.text) == (400, True)
    _nothing_enrolled(db)
    assert _events(db) == []


async def test_security_headers_on_enroll_pages(browser, deps, settings, device, clock):
    path = _link(deps)
    responses = {"link_page": await browser.get(path)}
    responses["open"] = await _press_start(browser, settings, path)
    assert COOKIE.fullmatch(responses["open"].headers["set-cookie"])
    responses["password_page"] = await browser.get("/enroll")
    responses["wrong_password"] = await _password(browser, settings, password="guess")
    responses["foreign"] = await _password(browser, settings, headers={"Origin": "https://evil.test"})
    responses["password"] = await _password(browser, settings)
    responses["passkey_page"] = await browser.get("/enroll")
    responses["options"] = await _options(browser, settings)
    responses["registered"] = await _send_registration(browser, settings, device.create(responses["options"].text))
    responses["totp_page"] = await browser.get("/enroll")
    secret = SECRET.search(responses["totp_page"].text).group(1)
    responses["shown_again"] = await browser.get("/enroll")
    responses["wrong_code"] = await _send_code(browser, settings, _wrong_code(secret, clock))
    responses["done"] = await _send_code(browser, settings, _code(secret, clock))
    responses["used_link"] = await browser.get(path)
    assert {name: response.status_code for name, response in responses.items()} == {
        "link_page": 200, "open": 303, "password_page": 200, "wrong_password": 401, "foreign": 403, "password": 303,
        "passkey_page": 200, "options": 200, "registered": 200, "totp_page": 200, "shown_again": 200,
        "wrong_code": 401, "done": 200, "used_link": 400}
    for name, response in responses.items():
        assert {header: response.headers.get(header) for header in PAGE_HEADERS} == PAGE_HEADERS, name

    for name in ("link_page", "password_page", "wrong_password", "foreign", "passkey_page", "totp_page", "shown_again",
                 "wrong_code", "done", "used_link"):
        page = responses[name].text
        assert responses[name].headers["content-type"] == "text/html; charset=utf-8", name
        # The one script and the one stylesheet, both from /static, and on the TOTP page the QR as a data: image.
        assert re.findall(r"<script\b[^>]*>", page) == ['<script src="/static/mcp-auth.js" defer>'], name
        resources = re.findall(r'\b(?:src|href)="([^"]*)"', page)
        expected = ["/static/mcp-auth.css", "/static/mcp-auth.js"] + (["data:"] if name == "totp_page" else [])
        assert [url[:5] if url.startswith("data:") else url for url in resources] == expected, name
        assert "<style" not in page and " style=" not in page, name
        assert re.search(r"\son[a-z]+=", page) is None, name


async def test_username_is_escaped(browser, deps, settings):
    name = '"><script>alert(1)</script>'
    path = _link(deps, name)
    start = await browser.get(path)
    assert "<script>alert(1)</script>" not in start.text
    assert "<strong>&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;</strong>" in start.text
    await _open(browser, settings, path)
    page = await browser.get("/enroll")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert 'value="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in page.text

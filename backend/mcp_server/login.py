"""Sign-in: the IntelliStock password, then a passkey or a TOTP code, then an authorization code for Jarvis.

/authorize stores a login transaction and sends the browser to GET /login?txn=.
That first visit binds the transaction to the browser with the mcp_login
cookie. Every later step must come from the same browser (the cookie's hash
matches the one stored on the transaction), and every POST from this server's
own pages (Origin is exactly the public origin, otherwise 403). The steps:

1. POST /login checks the username's lockout, then the password with the API.
   A right password for an enrolled user moves the transaction to stage
   second_factor and names the user. A wrong password and a user who never
   enrolled get the same "Sign-in failed." page, so the page never says which
   usernames exist; only the audit row's reason tells them apart.
2. GET /login/second offers a passkey (mcp-auth.js calls POST
   /login/passkey/options, then POST /login/passkey) and a TOTP form (POST
   /login/totp). These endpoints answer 400 before the password step.
3. A verified passkey or code completes the transaction: complete_login mints
   the code, and the browser goes to Jarvis's callback with it and the state.

Every failed step (password, passkey or TOTP) counts against the username in
LoginThrottle and against the transaction, which its fifth failure deletes. A
right password does not clear the username's count; only a completed sign-in
does, so a guesser who has the password still meets the lockout at the second
factor. The lockout is checked before every attempt. An API outage, or the
API's own rate limit, is nobody's failure and counts against nothing.

A passkey challenge is spent by the first assertion presented against it,
whatever its outcome, with a compare-and-swap on the transaction row. Synced
passkeys always report a signature counter of 0, so for them the challenge is
the only replay guard.

The audit records login_ok, login_failed, passkey_failed and totp_failed; the
throttle records lockout through its callback. The Tailscale-User-Login
header goes into login_ok as information only. It authenticates nothing.
"""
import math
from typing import Any, Awaitable, Callable, List, Mapping, Optional
from urllib.parse import quote

from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, request_response
from starlette.types import ASGIApp

from mcp_server.api_client import PasswordCheck
from mcp_server.config import LOGIN_TXN_TTL_S
from mcp_server.oauth_provider import MAX_BODY_BYTES, LoginTxn
from mcp_server.pages import (
    WebDeps,
    cookie_header,
    cookie_matches,
    login_password_html,
    login_second_factor_html,
    message_html,
    origin_allowed,
    render_page,
    security_headers,
)

LOGIN_COOKIE = "mcp_login"
LOGIN_PATH = "/login"
SECOND_FACTOR_PATH = "/login/second"
TAILNET_USER_HEADER = "Tailscale-User-Login"

SIGN_IN_TITLE = "Sign in"
SECOND_FACTOR_TITLE = "Confirm it's you"

EXPIRED = "This sign-in request expired. Start again from Jarvis."
OTHER_BROWSER = "This sign-in link was opened in another browser. Start again from Jarvis."
FAILED = "Sign-in failed."
LOCKED = "Too many attempts. Try again in {minutes} min."
UNAVAILABLE = "IntelliStock is unavailable. Try again shortly."
WRONG_STEP = "This sign-in step is not available now. Start again from Jarvis."
FOREIGN_ORIGIN = "This request did not come from the sign-in page."
BAD_REQUEST = "The request was not understood."


class _Refused(Exception):
    """A request turned away before any attempt: nothing is counted or spent."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def locked_message(seconds: Optional[int]) -> str:
    """The lockout copy, in whole minutes rounded up (at least 1)."""
    return LOCKED.format(minutes=max(1, math.ceil((seconds or 0) / 60)))


def _second_factor_url(txn_id: str) -> str:
    return SECOND_FACTOR_PATH + "?txn=" + quote(txn_id, safe="")


def _field(form: Mapping[str, Any], name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


class _LoginPages:
    """The /login endpoints over one WebDeps; see the module docstring for the flow."""

    def __init__(self, deps: WebDeps) -> None:
        self._deps = deps
        self._settings = deps.settings

    # --- Endpoints -----------------------------------------------------------------------------

    async def password_page(self, request: Request) -> Response:
        """GET /login?txn=: bind the transaction to this browser, then show the password form."""
        txn_id = request.query_params.get("txn", "")
        txn = self._deps.provider.get_txn(txn_id)
        if txn is None:
            return self._error_page(400, EXPIRED)
        cookie = self._deps.provider.bind_txn_cookie(txn_id)
        if cookie is None and not cookie_matches(request, LOGIN_COOKIE, txn.cookie_hash):
            return self._error_page(400, OTHER_BROWSER)
        if txn.stage == "second_factor":
            response = self._redirect(_second_factor_url(txn_id))
        else:
            response = self._page(SIGN_IN_TITLE, login_password_html(txn_id))
        if cookie is not None:
            response.headers.append("set-cookie", cookie_header(LOGIN_COOKIE, cookie, path=LOGIN_PATH,
                                                                max_age=LOGIN_TXN_TTL_S))
        return response

    async def password(self, request: Request) -> Response:
        """POST /login (txn, username, password)."""
        if not origin_allowed(request, self._settings):
            return self._error_page(403, FOREIGN_ORIGIN)
        form = await request.form()
        txn_id, username, password = (_field(form, name) for name in ("txn", "username", "password"))
        try:
            self._txn(request, txn_id, "password")
            self._check_lockout(username)
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        if username and password:
            check = await self._deps.api.verify_password(username, password)
        else:
            check = PasswordCheck("invalid")
        if check.outcome == "throttled":
            return self._error_page(429, locked_message(check.retry_after_s))
        if check.outcome == "unavailable":
            return self._error_page(503, UNAVAILABLE)
        if check.outcome == "ok" and self._enrolled(check.user_id):
            self._deps.provider.update_txn(txn_id, stage="second_factor", user_id=check.user_id,
                                           username=check.username)
            return self._redirect(_second_factor_url(txn_id))
        self._deps.throttle.record_failure(username, "password")
        alive = self._deps.provider.fail_txn(txn_id)
        self._deps.audit.record("login_failed", username=username,
                                reason="not_enrolled" if check.outcome == "ok" else "invalid")
        if alive:
            return self._page(SIGN_IN_TITLE, login_password_html(txn_id, username=username, error=FAILED), 401)
        return self._page(SIGN_IN_TITLE, message_html(FAILED, error=True), 401)

    async def second_factor_page(self, request: Request) -> Response:
        """GET /login/second?txn=: the passkey button and the TOTP form."""
        txn_id = request.query_params.get("txn", "")
        try:
            txn = self._txn(request, txn_id, "second_factor")
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        return self._page(SECOND_FACTOR_TITLE, login_second_factor_html(txn_id, txn.username))

    async def passkey_options(self, request: Request) -> Response:
        """POST /login/passkey/options {txn}: assertion options; their challenge is kept on the transaction."""
        try:
            body = await self._json_body(request)
            txn_id = body.get("txn")
            txn = self._txn(request, txn_id, "second_factor")
            self._check_lockout(txn.username)
        except _Refused as refused:
            return self._json({"error": refused.message}, refused.status)
        options, challenge = self._deps.passkeys.authentication_options(txn.user_id)
        self._deps.provider.update_txn(txn_id, webauthn_challenge=challenge)
        return Response(options, media_type="application/json", headers=security_headers(self._settings))

    async def passkey(self, request: Request) -> Response:
        """POST /login/passkey {txn, credential}: {"redirect": Jarvis's callback with the code} on success."""
        try:
            body = await self._json_body(request)
            txn_id = body.get("txn")
            txn = self._txn(request, txn_id, "second_factor")
            self._check_lockout(txn.username)
        except _Refused as refused:
            return self._json({"error": refused.message}, refused.status)
        credential = body.get("credential")
        challenge = self._spend_challenge(txn)
        verified = (challenge is not None and isinstance(credential, dict)
                    and self._deps.passkeys.verify_authentication(txn.user_id, credential, challenge))
        if not verified:
            self._second_factor_failed(txn_id, txn, "passkey")
            return self._json({"error": FAILED}, 401)
        try:
            redirect = self._complete(request, txn_id, txn, "passkey")
        except _Refused as refused:
            return self._json({"error": refused.message}, refused.status)
        return self._json({"redirect": redirect})

    async def totp(self, request: Request) -> Response:
        """POST /login/totp (txn, code): a 303 to Jarvis's callback with the code on success."""
        if not origin_allowed(request, self._settings):
            return self._error_page(403, FOREIGN_ORIGIN)
        form = await request.form()
        txn_id = _field(form, "txn")
        try:
            txn = self._txn(request, txn_id, "second_factor")
            self._check_lockout(txn.username)
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        code = "".join(_field(form, "code").split())  # authenticator apps show "123 456"
        if not self._deps.totp.verify_and_consume(txn.user_id, code):
            if self._second_factor_failed(txn_id, txn, "totp"):
                body = login_second_factor_html(txn_id, txn.username, error=FAILED)
            else:
                body = message_html(FAILED, error=True)
            return self._page(SECOND_FACTOR_TITLE, body, 401)
        try:
            redirect = self._complete(request, txn_id, txn, "totp")
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        return self._redirect(redirect)

    # --- Steps -----------------------------------------------------------------------------------

    def _txn(self, request: Request, txn_id: Any, stage: str) -> LoginTxn:
        """The live transaction, bound to this browser and at stage; otherwise _Refused (400)."""
        txn = self._deps.provider.get_txn(txn_id)
        if txn is None:
            raise _Refused(400, EXPIRED)
        if not cookie_matches(request, LOGIN_COOKIE, txn.cookie_hash):
            raise _Refused(400, OTHER_BROWSER)
        if txn.stage != stage:
            raise _Refused(400, WRONG_STEP)
        return txn

    def _check_lockout(self, username: str) -> None:
        seconds = self._deps.throttle.check(username)
        if seconds is not None:
            raise _Refused(429, locked_message(seconds))

    def _enrolled(self, user_id: Optional[str]) -> bool:
        return self._deps.db.query_one("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) is not None

    def _spend_challenge(self, txn: LoginTxn) -> Optional[bytes]:
        """The transaction's passkey challenge, cleared so no second assertion can answer it; None if gone."""
        challenge = txn.webauthn_challenge
        if challenge is None:
            return None
        with self._deps.db.tx() as conn:
            spent = conn.execute(
                "UPDATE auth_txns SET webauthn_challenge = NULL "
                "WHERE txn_hash = ? AND webauthn_challenge = ? AND expires_at > ?",
                (txn.txn_hash, challenge, self._deps.clock()),
            )
            return challenge if spent.rowcount == 1 else None

    def _second_factor_failed(self, txn_id: str, txn: LoginTxn, kind: str) -> bool:
        """Count a failed passkey or TOTP attempt; False once it has ended the transaction."""
        self._deps.throttle.record_failure(txn.username, kind)
        self._deps.audit.record(kind + "_failed", username=txn.username, user_id=txn.user_id)
        return self._deps.provider.fail_txn(txn_id)

    def _complete(self, request: Request, txn_id: str, txn: LoginTxn, method: str) -> str:
        """Trade the transaction for a code: Jarvis's callback URL. _Refused (400) if it ran out meanwhile."""
        try:
            redirect = self._deps.provider.complete_login(txn_id, txn.user_id)
        except LookupError:
            raise _Refused(400, EXPIRED) from None
        self._deps.throttle.record_success(txn.username)
        self._deps.audit.record("login_ok", username=txn.username, user_id=txn.user_id, method=method,
                                tailnet_user=request.headers.get(TAILNET_USER_HEADER))
        return redirect

    # --- Responses -------------------------------------------------------------------------------

    async def _json_body(self, request: Request) -> dict:
        """The JSON object a same-origin POST carries; _Refused (403 or 400) otherwise."""
        if not origin_allowed(request, self._settings):
            raise _Refused(403, FOREIGN_ORIGIN)
        try:
            body = await request.json()
        except ValueError:
            raise _Refused(400, BAD_REQUEST) from None
        if not isinstance(body, dict):
            raise _Refused(400, BAD_REQUEST)
        return body

    def _page(self, title: str, body_html: str, status: int = 200) -> Response:
        return render_page(self._settings, title, body_html, status=status)

    def _error_page(self, status: int, message: str) -> Response:
        return self._page(SIGN_IN_TITLE, message_html(message, error=True), status)

    def _json(self, body: dict, status: int = 200) -> Response:
        return JSONResponse(body, status_code=status, headers=security_headers(self._settings))

    def _redirect(self, url: str) -> Response:
        return RedirectResponse(url, status_code=303, headers=security_headers(self._settings))


def _limited(endpoint: Callable[[Request], Awaitable[Response]]) -> ASGIApp:
    """The endpoint behind the same 16 KiB body limit as the authorization server's routes."""
    return RequestBodyLimitMiddleware(request_response(endpoint), MAX_BODY_BYTES)


def login_routes(deps: WebDeps) -> List[Route]:
    """GET and POST /login, GET /login/second, POST /login/passkey/options, /login/passkey and /login/totp."""
    pages = _LoginPages(deps)
    return [
        Route(LOGIN_PATH, endpoint=_limited(pages.password_page), methods=["GET"]),
        Route(LOGIN_PATH, endpoint=_limited(pages.password), methods=["POST"]),
        Route(SECOND_FACTOR_PATH, endpoint=_limited(pages.second_factor_page), methods=["GET"]),
        Route("/login/passkey/options", endpoint=_limited(pages.passkey_options), methods=["POST"]),
        Route("/login/passkey", endpoint=_limited(pages.passkey), methods=["POST"]),
        Route("/login/totp", endpoint=_limited(pages.totp), methods=["POST"]),
    ]

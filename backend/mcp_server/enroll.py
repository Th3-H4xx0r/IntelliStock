"""Enrollment: an admin-issued single-use link, then the password, a passkey and a TOTP authenticator app.

`python -m mcp_server.admin enroll <username>` stores an enrollments row at stage
password, keeping only the link token's hash. These pages move that row through
a stage machine, and nothing reaches users or passkeys before the last step:

1. GET /enroll?t= only reads. An unexpired link nobody has opened gets a page
   whose "Start enrollment" button POSTs the token to /enroll/open; that POST
   (this server's Origin required) sets opened_at, binds the row to this
   browser with the mcp_enroll cookie, and answers 303 to /enroll, so the token
   leaves the address bar. A used, expired or unknown link gets 400, except in
   the browser the row is already bound to, which is sent back to /enroll (the
   operator clicked the link twice). Because a GET changes nothing, a link
   preview (iMessage, Slack) cannot spend the link. Because the cookie is set
   by a same-origin POST, the redirect after it is same-site and carries the
   SameSite=Strict cookie, even when the link itself was clicked on another
   site.
2. password: POST /enroll/password checks the row's username and the typed
   password with the API. The username's lockout is checked first, as on the
   sign-in page, and a wrong password counts against it in LoginThrottle (kind
   enroll_password). The right password names the user: stage passkey.
3. passkey: POST /enroll/passkey/options keeps a registration challenge on the
   row. POST /enroll/passkey spends it, whatever the outcome, then verifies the
   registration (user verification required). The passkey is kept as
   pending_passkey and a fresh TOTP secret is sealed into pending_totp_enc with
   aad = the row's token hash: stage totp.
4. totp: the first GET /enroll shows the secret as a QR code and as text and
   sets totp_shown_at; every later GET shows neither. Reloading means a new
   enrollment from the CLI.
5. POST /enroll/totp checks the code against the pending secret. One
   transaction then writes the user (the secret re-sealed with aad = user_id,
   totp_last_step = the code's step, so that code cannot also sign in), deletes
   all of the user's passkeys, inserts the new one and completes the row. The
   row is read again inside that transaction, because a newer enroll, from the
   CLI's own process, deletes unfinished rows. After it every session of the
   user is revoked ("re-enrolled") and enrolled is audited.

After the redirect the cookie is the only handle on the enrollment: the row is
found by the cookie's hash, so a browser without it gets 400 everywhere. Every
POST must also carry this server's Origin, otherwise 403. The enrollment ends
when its link does, ENROLL_TTL_S after enroll, wherever it has got to.

Password attempts take one asyncio.Lock and check the lockout again once they
hold it, so concurrent guesses cannot outrun the lockout across the API await
(the container is one process with one event loop). The other steps do not
await between reading the row and writing it, and each write is a
compare-and-swap on the row's stage.
"""
import asyncio
import base64
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Mapping, Optional

import segno
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, request_response
from starlette.types import ASGIApp

from mcp_server.admin import ENROLL_TOKEN_PREFIX
from mcp_server.api_client import PasswordCheck
from mcp_server.config import ENROLL_TTL_S
from mcp_server.crypto import hash_token, new_token
from mcp_server.login import BAD_REQUEST, MISCONFIGURED, UNAVAILABLE, locked_message
from mcp_server.oauth_provider import MAX_BODY_BYTES
from mcp_server.pages import (
    WebDeps,
    cookie_header,
    cookie_matches,
    enroll_passkey_html,
    enroll_password_html,
    enroll_start_html,
    enroll_totp_html,
    message_html,
    origin_allowed,
    render_page,
    security_headers,
)
from mcp_server.passkeys import NewPasskey, PasskeyError

ENROLL_COOKIE = "mcp_enroll"
ENROLL_COOKIE_PREFIX = "mcpec_"
ENROLL_PATH = "/enroll"
REENROLL_REASON = "re-enrolled"
THROTTLE_KIND = "enroll_password"

ENROLL_TITLE = "Set up sign-in"
PASSKEY_TITLE = "Add a passkey"
TOTP_TITLE = "Add an authenticator app"

LINK_USED = "This enrollment link was already used or has expired."
NOT_OPEN = ("No enrollment is open in this browser. Paste your enrollment link into the address bar. "
            "If the link was already used, start a new enrollment with the admin CLI.")
EXPIRED = "This enrollment has expired. Start a new enrollment with the admin CLI."
WRONG_STEP = "This enrollment step is not available now. Go back and reload the page."
FOREIGN_ORIGIN = "This request did not come from the enrollment page."
WRONG_PASSWORD = "That password was not accepted."
PASSKEY_FAILED = "The passkey could not be registered. Try again."
ALREADY_SHOWN = "The authenticator code was already shown. Start a new enrollment with the admin CLI."
WRONG_CODE = "That code did not match. Enter the current code from your authenticator app."
DONE = "Enrollment complete. You can close this page."

_COLUMNS = ("token_hash, username, expires_at, opened_at, cookie_hash, stage, user_id, webauthn_challenge, "
            "pending_passkey, pending_totp_enc, totp_shown_at, completed_at")
_LIVE = "completed_at IS NULL AND expires_at > ?"  # every write to a row also requires these


class _Refused(Exception):
    """A request turned away: nothing on the row changes."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class _Enrolled:
    user_id: str
    username: str
    reenrolled: bool


def _field(form: Mapping[str, Any], name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pending_passkey(passkey: NewPasskey) -> str:
    """The verified passkey as kept on the row until the last step: JSON, bytes as base64url."""
    return json.dumps({"credential_id": _b64url(passkey.credential_id), "public_key": _b64url(passkey.public_key),
                       "sign_count": passkey.sign_count, "transports": list(passkey.transports)}, sort_keys=True)


def _require_live(row: Optional[sqlite3.Row], now: float) -> None:
    """_Refused unless the row exists, is unfinished and has not run out."""
    if row is None or row["completed_at"] is not None:
        raise _Refused(400, NOT_OPEN)
    if now >= row["expires_at"]:
        raise _Refused(400, EXPIRED)


class _EnrollPages:
    """The /enroll endpoints over one WebDeps; see the module docstring for the flow."""

    def __init__(self, deps: WebDeps) -> None:
        self._deps = deps
        self._settings = deps.settings
        self._password_lock = asyncio.Lock()

    # --- Endpoints -----------------------------------------------------------------------------

    async def page(self, request: Request) -> Response:
        """GET /enroll?t= shows a link's Start page; GET /enroll shows the current step."""
        if request.method != "GET":  # a HEAD must not spend the QR code's one showing
            return Response(status_code=405, headers={"Allow": "GET", **security_headers(self._settings)})
        if "t" in request.query_params:
            return self._link_page(request, request.query_params["t"])
        try:
            row = self._enrollment(request)
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        if row["stage"] == "password":
            return self._page(ENROLL_TITLE, enroll_password_html(row["username"]))
        if row["stage"] == "passkey":
            return self._page(PASSKEY_TITLE, enroll_passkey_html())
        return self._totp_page(row)

    async def open_link(self, request: Request) -> Response:
        """POST /enroll/open (t): bind the link's row to this browser and send it to /enroll."""
        if not origin_allowed(request, self._settings):
            return self._error_page(403, FOREIGN_ORIGIN)
        form = await request.form()
        token = _field(form, "t")
        if not token.startswith(ENROLL_TOKEN_PREFIX):
            return self._error_page(400, LINK_USED)
        cookie = new_token(ENROLL_COOKIE_PREFIX)
        with self._deps.db.tx() as conn:
            now = self._deps.clock()
            opened = conn.execute(
                f"UPDATE enrollments SET opened_at = ?, cookie_hash = ? WHERE token_hash = ? AND opened_at IS NULL "
                f"AND {_LIVE}",
                (now, hash_token(cookie), hash_token(token), now),
            ).rowcount
        if opened != 1:
            return self._used_link(request, self._link_row(token))
        response = self._redirect(ENROLL_PATH)
        response.headers.append("set-cookie", cookie_header(ENROLL_COOKIE, cookie, path=ENROLL_PATH,
                                                            max_age=ENROLL_TTL_S))
        return response

    async def password(self, request: Request) -> Response:
        """POST /enroll/password (password)."""
        if not origin_allowed(request, self._settings):
            return self._error_page(403, FOREIGN_ORIGIN)
        form = await request.form()
        password = _field(form, "password")
        try:
            row = self._at(request, "password")
            self._check_lockout(row["username"])
            async with self._password_lock:
                row = self._at(request, "password")
                self._check_lockout(row["username"])
                return await self._check_password(row, password)
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)

    async def passkey_options(self, request: Request) -> Response:
        """POST /enroll/passkey/options {}: registration options; their challenge is kept on the row."""
        try:
            await self._json_body(request)
            row = self._at(request, "passkey")
        except _Refused as refused:
            return self._json({"error": refused.message}, refused.status)
        options, challenge = self._deps.passkeys.registration_options(user_id=row["user_id"],
                                                                     username=row["username"])
        self._deps.db.query(
            f"UPDATE enrollments SET webauthn_challenge = ? WHERE token_hash = ? AND stage = 'passkey' AND {_LIVE}",
            (challenge, row["token_hash"], self._deps.clock()),
        )
        return Response(options, media_type="application/json", headers=security_headers(self._settings))

    async def passkey(self, request: Request) -> Response:
        """POST /enroll/passkey {credential}: {"redirect": "/enroll"} once the passkey is registered."""
        try:
            body = await self._json_body(request)
            row = self._at(request, "passkey")
        except _Refused as refused:
            return self._json({"error": refused.message}, refused.status)
        credential = body.get("credential")
        challenge = self._spend_challenge(row)
        if challenge is None or not isinstance(credential, dict):
            return self._json({"error": PASSKEY_FAILED}, 400)
        try:
            passkey = self._deps.passkeys.verify_registration(credential, challenge)
        except PasskeyError:
            return self._json({"error": PASSKEY_FAILED}, 400)
        sealed = self._deps.totp.seal(self._deps.totp.new_secret(), row["token_hash"])
        with self._deps.db.tx() as conn:
            moved = conn.execute(
                "UPDATE enrollments SET pending_passkey = ?, pending_totp_enc = ?, stage = 'totp' "
                f"WHERE token_hash = ? AND stage = 'passkey' AND {_LIVE}",
                (_pending_passkey(passkey), sealed, row["token_hash"], self._deps.clock()),
            ).rowcount
        if moved != 1:
            return self._json({"error": EXPIRED}, 400)
        return self._json({"redirect": ENROLL_PATH})

    async def totp(self, request: Request) -> Response:
        """POST /enroll/totp (code): the done page once the user, the passkey and the secret are written."""
        if not origin_allowed(request, self._settings):
            return self._error_page(403, FOREIGN_ORIGIN)
        form = await request.form()
        code = "".join(_field(form, "code").split())  # authenticator apps show "123 456"
        try:
            row = self._at(request, "totp")
            if row["totp_shown_at"] is None:
                raise _Refused(400, WRONG_STEP)
            secret = self._deps.totp.unseal(row["pending_totp_enc"], row["token_hash"])
            step = self._deps.totp.match_step(secret, code)
            if step is None:
                return self._page(TOTP_TITLE, enroll_totp_html(error=WRONG_CODE), 401)
            enrolled = self._complete(row, secret, step)
        except _Refused as refused:
            return self._error_page(refused.status, refused.message)
        revoked = self._deps.sessions.revoke_all(REENROLL_REASON, user_id=enrolled.user_id)
        self._deps.audit.record("enrolled", user_id=enrolled.user_id, username=enrolled.username,
                                reenrolled=enrolled.reenrolled, sessions_revoked=revoked)
        response = self._page(ENROLL_TITLE, message_html(DONE))
        response.headers.append("set-cookie", cookie_header(ENROLL_COOKIE, "", path=ENROLL_PATH, max_age=0))
        return response

    # --- Steps -----------------------------------------------------------------------------------

    def _link_row(self, token: str) -> Optional[sqlite3.Row]:
        """The row behind a link token, live or not; None for an unknown or malformed token."""
        if not token.startswith(ENROLL_TOKEN_PREFIX):
            return None
        return self._deps.db.query_one(f"SELECT {_COLUMNS} FROM enrollments WHERE token_hash = ?",
                                       (hash_token(token),))

    def _link_page(self, request: Request, token: str) -> Response:
        """GET /enroll?t=, which changes nothing: the Start page for a live link nobody has opened."""
        row = self._link_row(token)
        try:
            _require_live(row, self._deps.clock())
        except _Refused:
            return self._error_page(400, LINK_USED)
        if row["opened_at"] is None:
            return self._page(ENROLL_TITLE, enroll_start_html(token, row["username"]))
        return self._used_link(request, row)

    def _used_link(self, request: Request, row: Optional[sqlite3.Row]) -> Response:
        """A link that is not open for the taking: back to /enroll in its bound browser, else 400."""
        try:
            _require_live(row, self._deps.clock())
        except _Refused:
            return self._error_page(400, LINK_USED)
        if row["opened_at"] is not None and cookie_matches(request, ENROLL_COOKIE, row["cookie_hash"]):
            return self._redirect(ENROLL_PATH)
        return self._error_page(400, LINK_USED)

    def _enrollment(self, request: Request) -> sqlite3.Row:
        """The live row this browser's cookie is bound to; otherwise _Refused (400)."""
        cookie = request.cookies.get(ENROLL_COOKIE)
        if not cookie:
            raise _Refused(400, NOT_OPEN)
        row = self._deps.db.query_one(f"SELECT {_COLUMNS} FROM enrollments WHERE cookie_hash = ?",
                                      (hash_token(cookie),))
        _require_live(row, self._deps.clock())
        return row

    def _at(self, request: Request, stage: str) -> sqlite3.Row:
        """The browser's live row, at stage; otherwise _Refused (400)."""
        row = self._enrollment(request)
        if row["stage"] != stage:
            raise _Refused(400, WRONG_STEP)
        return row

    def _check_lockout(self, username: str) -> None:
        seconds = self._deps.throttle.check(username)
        if seconds is not None:
            raise _Refused(429, locked_message(seconds))

    async def _check_password(self, row: sqlite3.Row, password: str) -> Response:
        """The password step, holding the password lock."""
        username = row["username"]
        if password:
            check = await self._deps.api.verify_password(username, password)
        else:
            check = PasswordCheck("invalid")
        if check.outcome == "throttled":
            return self._error_page(429, locked_message(check.retry_after_s))
        if check.outcome == "unavailable":
            return self._error_page(503, MISCONFIGURED if check.misconfigured else UNAVAILABLE)
        if check.outcome == "ok":
            # The API's spelling of the username is the one the users row keeps.
            with self._deps.db.tx() as conn:
                moved = conn.execute(
                    "UPDATE enrollments SET user_id = ?, username = ?, stage = 'passkey' "
                    f"WHERE token_hash = ? AND stage = 'password' AND {_LIVE}",
                    (check.user_id, check.username, row["token_hash"], self._deps.clock()),
                ).rowcount
            if moved != 1:
                raise _Refused(400, EXPIRED)
            return self._redirect(ENROLL_PATH)
        self._deps.throttle.record_failure(username, THROTTLE_KIND)
        self._deps.audit.record("login_failed", username=username, reason="invalid", via="enroll")
        return self._page(ENROLL_TITLE, enroll_password_html(username, error=WRONG_PASSWORD), 401)

    def _totp_page(self, row: sqlite3.Row) -> Response:
        """The QR code and the secret on the first view only; totp_shown_at marks that view."""
        secret = self._deps.totp.unseal(row["pending_totp_enc"], row["token_hash"])
        with self._deps.db.tx() as conn:
            now = self._deps.clock()
            shown = conn.execute(
                "UPDATE enrollments SET totp_shown_at = ? "
                f"WHERE token_hash = ? AND stage = 'totp' AND totp_shown_at IS NULL AND {_LIVE}",
                (now, row["token_hash"], now),
            ).rowcount
        if shown != 1:
            return self._page(TOTP_TITLE, message_html(ALREADY_SHOWN, error=True))
        uri = self._deps.totp.provisioning_uri(secret, row["username"])
        qr = segno.make(uri, error="m").svg_data_uri(scale=6)
        return self._page(TOTP_TITLE, enroll_totp_html(qr_data_uri=qr, secret=secret))

    def _spend_challenge(self, row: sqlite3.Row) -> Optional[bytes]:
        """The row's registration challenge, cleared so nothing else can answer it; None if there is none."""
        challenge = row["webauthn_challenge"]
        if challenge is None:
            return None
        with self._deps.db.tx() as conn:
            spent = conn.execute(
                "UPDATE enrollments SET webauthn_challenge = NULL "
                f"WHERE token_hash = ? AND webauthn_challenge = ? AND stage = 'passkey' AND {_LIVE}",
                (row["token_hash"], challenge, self._deps.clock()),
            ).rowcount
        return bytes(challenge) if spent == 1 else None

    def _complete(self, row: sqlite3.Row, secret: str, step: int) -> _Enrolled:
        """Write the user, replace their passkeys and complete the row, in one transaction; _Refused if it went."""
        with self._deps.db.tx() as conn:
            now = self._deps.clock()
            # Read again under the write lock: a newer enroll may have deleted this row since the code was checked.
            current = conn.execute(f"SELECT {_COLUMNS} FROM enrollments WHERE token_hash = ?",
                                   (row["token_hash"],)).fetchone()
            _require_live(current, now)
            if current["stage"] != "totp" or current["pending_totp_enc"] != row["pending_totp_enc"]:
                raise _Refused(400, WRONG_STEP)
            user_id, username = current["user_id"], current["username"]
            passkey = json.loads(current["pending_passkey"])
            reenrolled = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None
            conn.execute(
                "INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc, totp_last_step) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET username = excluded.username, "
                "enrolled_at = excluded.enrolled_at, totp_secret_enc = excluded.totp_secret_enc, "
                "totp_last_step = excluded.totp_last_step",
                (user_id, username, now, self._deps.totp.seal(secret, user_id), step),
            )
            conn.execute("DELETE FROM passkeys WHERE user_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO passkeys (credential_id, user_id, public_key, sign_count, transports, created_at, "
                "last_used_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (_unb64url(passkey["credential_id"]), user_id, _unb64url(passkey["public_key"]),
                 passkey["sign_count"], json.dumps(passkey["transports"]), now),
            )
            conn.execute(
                "UPDATE enrollments SET completed_at = ?, webauthn_challenge = NULL, pending_passkey = NULL, "
                "pending_totp_enc = NULL WHERE token_hash = ?",
                (now, current["token_hash"]),
            )
        return _Enrolled(user_id=user_id, username=username, reenrolled=reenrolled)

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
        return self._page(ENROLL_TITLE, message_html(message, error=True), status)

    def _json(self, body: dict, status: int = 200) -> Response:
        return JSONResponse(body, status_code=status, headers=security_headers(self._settings))

    def _redirect(self, url: str) -> Response:
        return RedirectResponse(url, status_code=303, headers=security_headers(self._settings))


def _limited(endpoint: Callable[[Request], Awaitable[Response]]) -> ASGIApp:
    """The endpoint behind the same 16 KiB body limit as the authorization server's routes."""
    return RequestBodyLimitMiddleware(request_response(endpoint), MAX_BODY_BYTES)


def enroll_routes(deps: WebDeps) -> List[Route]:
    """GET /enroll; POST /enroll/open, /enroll/password, /enroll/passkey/options, /enroll/passkey, /enroll/totp."""
    pages = _EnrollPages(deps)
    return [
        Route(ENROLL_PATH, endpoint=_limited(pages.page), methods=["GET"]),
        Route("/enroll/open", endpoint=_limited(pages.open_link), methods=["POST"]),
        Route("/enroll/password", endpoint=_limited(pages.password), methods=["POST"]),
        Route("/enroll/passkey/options", endpoint=_limited(pages.passkey_options), methods=["POST"]),
        Route("/enroll/passkey", endpoint=_limited(pages.passkey), methods=["POST"]),
        Route("/enroll/totp", endpoint=_limited(pages.totp), methods=["POST"]),
    ]

"""The web layer the sign-in and enrollment pages share: their dependencies, headers, page shell, cookies and assets.

Pages are rendered on the server with no inline script or style and no external
resource. The one script, /static/mcp-auth.js, runs the WebAuthn ceremonies for
both sign-in and enrollment (base64url to ArrayBuffer and back, and
navigator.credentials.get/create); the one stylesheet is /static/mcp-auth.css.
Every value placed into HTML goes through html.escape.

Every page response carries security_headers(). The CSP lets a page load only
its own script, stylesheet and data: images (the enrollment QR code), fetch only
from this origin, and submit forms to this origin or to Jarvis's loopback
callback: Chrome applies form-action to the redirect that follows a form POST,
and POST /login/totp answers with a 303 to that callback.

Referrer-Policy is same-origin, not no-referrer. Under no-referrer a browser
sends "Origin: null" with a form POST (Fetch, "append a request Origin header"),
and every POST here must carry this server's origin. Under same-origin a page's
URL, which holds its txn, is still never sent to another origin, Jarvis's
callback included.

The login and enrollment cookies are host-only, HttpOnly, Secure and
SameSite=Strict, scoped to their own path, and last as long as the transaction
they bind. Only their hash is stored, so cookie_matches compares hashes.
"""
import hmac
from dataclasses import dataclass
from html import escape
from typing import Callable, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from mcp_server.api_client import ApiClient
from mcp_server.audit import AuditSink
from mcp_server.authdb import AuthDB
from mcp_server.config import Settings
from mcp_server.crypto import hash_token
from mcp_server.oauth_provider import IntelliStockOAuthProvider
from mcp_server.passkeys import PasskeyService
from mcp_server.sessions import SessionService
from mcp_server.throttle import LoginThrottle
from mcp_server.totp import TotpService

SCRIPT_PATH = "/static/mcp-auth.js"
STYLE_PATH = "/static/mcp-auth.css"
REFERRER_POLICY = "same-origin"  # not no-referrer: see the module docstring
_STATIC_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass
class WebDeps:
    """What the sign-in and enrollment routes use. The provider and sessions must share db (oauth_provider)."""

    settings: Settings
    db: AuthDB
    provider: IntelliStockOAuthProvider
    api: ApiClient
    throttle: LoginThrottle
    totp: TotpService
    passkeys: PasskeyService
    audit: AuditSink
    sessions: SessionService
    clock: Callable[[], float]


def security_headers(settings: Settings) -> Dict[str, str]:
    """The headers on every page, redirect and JSON answer of the sign-in and enrollment routes."""
    return {
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            f"form-action 'self' {settings.redirect_origin}; frame-ancestors 'none'; base-uri 'none'"
        ),
        "X-Frame-Options": "DENY",
        "Referrer-Policy": REFERRER_POLICY,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def render_page(settings: Settings, title: str, body_html: str, *, status: int = 200) -> HTMLResponse:
    """A whole page around body_html, which the caller has built with every value escaped."""
    page = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + escape(title) + " - IntelliStock</title>\n"
        '<link rel="stylesheet" href="' + STYLE_PATH + '">\n'
        '<script src="' + SCRIPT_PATH + '" defer></script>\n'
        "</head>\n"
        "<body>\n"
        "<main>\n"
        '<p class="brand">IntelliStock</p>\n'
        "<h1>" + escape(title) + "</h1>\n"
        + body_html + "\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    return HTMLResponse(page, status_code=status, headers=security_headers(settings))


def message_html(text: str, *, error: bool = False) -> str:
    """One paragraph of text; an error is announced to screen readers."""
    if error:
        return '<p class="error" role="alert">' + escape(text) + "</p>"
    return "<p>" + escape(text) + "</p>"


def origin_allowed(request: Request, settings: Settings) -> bool:
    """True when the request's Origin is exactly this server's public origin."""
    return request.headers.get("origin") == settings.public_origin


def cookie_header(name: str, value: str, *, path: str, max_age: int) -> str:
    """A Set-Cookie value binding a transaction to this browser."""
    return f"{name}={value}; HttpOnly; Secure; SameSite=Strict; Path={path}; Max-Age={max_age}"


def cookie_matches(request: Request, name: str, cookie_hash: Optional[str]) -> bool:
    """True when the request carries the cookie whose hash the transaction stored."""
    value = request.cookies.get(name)
    if not value or cookie_hash is None:
        return False
    return hmac.compare_digest(hash_token(value), cookie_hash)


def login_password_html(txn_id: str, *, username: str = "", error: Optional[str] = None) -> str:
    """The password form, with the username typed so far and an error above it."""
    return (
        (message_html(error, error=True) + "\n" if error else "")
        + '<form method="post" action="/login">\n'
        + '<input type="hidden" name="txn" value="' + escape(txn_id) + '">\n'
        + '<label for="username">Username</label>\n'
        + '<input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" '
        + 'required value="' + escape(username) + '">\n'
        + '<label for="password">Password</label>\n'
        + '<input id="password" name="password" type="password" autocomplete="current-password" required>\n'
        + '<button type="submit">Continue</button>\n'
        + "</form>"
    )


def login_second_factor_html(txn_id: str, username: str, *, error: Optional[str] = None) -> str:
    """The passkey button (mcp-auth.js drives it) and the TOTP form."""
    txn = escape(txn_id)
    return (
        (message_html(error, error=True) + "\n" if error else "")
        + '<p class="muted">Password accepted for <strong>' + escape(username) + "</strong>. "
        + "Finish with your passkey, or with a code from your authenticator app.</p>\n"
        + '<button type="button" data-webauthn="get" data-options-url="/login/passkey/options" '
        + 'data-submit-url="/login/passkey" data-txn="' + txn + '" data-status="passkey-status">Use passkey</button>\n'
        + '<p id="passkey-status" class="error" role="alert" hidden></p>\n'
        + '<p class="divider">or</p>\n'
        + '<form method="post" action="/login/totp">\n'
        + '<input type="hidden" name="txn" value="' + txn + '">\n'
        + '<label for="code">Authenticator code</label>\n'
        + '<input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required>\n'
        + '<button type="submit" class="secondary">Sign in with code</button>\n'
        + "</form>"
    )


def enroll_password_html(username: str, *, error: Optional[str] = None) -> str:
    """The enrollment's password form; the username is the one the admin CLI enrolled, shown read-only."""
    return (
        (message_html(error, error=True) + "\n" if error else "")
        + '<p class="muted">Confirm your IntelliStock password. Then you add a passkey and an authenticator app.</p>\n'
        + '<form method="post" action="/enroll/password">\n'
        + '<label for="username">Username</label>\n'
        + '<input id="username" name="username" autocomplete="username" readonly value="' + escape(username) + '">\n'
        + '<label for="password">Password</label>\n'
        + '<input id="password" name="password" type="password" autocomplete="current-password" required>\n'
        + '<button type="submit">Continue</button>\n'
        + "</form>"
    )


def enroll_passkey_html() -> str:
    """The passkey registration button (mcp-auth.js drives it)."""
    return (
        '<p class="muted">Create a passkey on this device. Your browser asks for your device PIN, fingerprint or '
        + "face.</p>\n"
        + '<button type="button" data-webauthn="create" data-options-url="/enroll/passkey/options" '
        + 'data-submit-url="/enroll/passkey" data-status="passkey-status">Create passkey</button>\n'
        + '<p id="passkey-status" class="error" role="alert" hidden></p>'
    )


def enroll_totp_html(*, qr_data_uri: Optional[str] = None, secret: Optional[str] = None,
                     error: Optional[str] = None) -> str:
    """The code form, below the QR code and the secret on the page's one showing of them."""
    shown = ""
    if qr_data_uri is not None and secret is not None:
        shown = (
            '<p class="muted">Scan this QR code with your authenticator app. It is shown only once.</p>\n'
            + '<img class="qr" src="' + escape(qr_data_uri) + '" alt="QR code for your authenticator app">\n'
            + '<p class="muted">Or enter this key in the app:</p>\n'
            + '<p><code id="totp-secret">' + escape(secret) + "</code></p>\n"
        )
    return (
        (message_html(error, error=True) + "\n" if error else "")
        + shown
        + '<form method="post" action="/enroll/totp">\n'
        + '<label for="code">Code from the app</label>\n'
        + '<input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required>\n'
        + '<button type="submit">Finish</button>\n'
        + "</form>"
    )


AUTH_JS = r"""'use strict';
// WebAuthn for the IntelliStock MCP sign-in and enrollment pages.
// A button with data-webauthn="get" (sign-in) or "create" (enrollment) runs one ceremony: it POSTs
// {txn} (when data-txn is set, else {}) to data-options-url, passes the options to navigator.credentials,
// and POSTs {txn, credential} to data-submit-url. A {redirect} answer is followed; an {error} answer
// is shown in the element whose id is data-status.
(() => {
  const FALLBACK = 'Something went wrong. Try again.';

  const toBuffer = (text) => {
    const base64 = text.replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4));
    return Uint8Array.from(binary, (char) => char.charCodeAt(0)).buffer;
  };

  const fromBuffer = (buffer) => {
    let binary = '';
    new Uint8Array(buffer).forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };

  const withBufferIds = (list) => (list || []).map((item) => ({ ...item, id: toBuffer(item.id) }));

  const requestOptions = (options) => ({
    ...options,
    challenge: toBuffer(options.challenge),
    allowCredentials: withBufferIds(options.allowCredentials),
  });

  const creationOptions = (options) => ({
    ...options,
    challenge: toBuffer(options.challenge),
    user: { ...options.user, id: toBuffer(options.user.id) },
    excludeCredentials: withBufferIds(options.excludeCredentials),
  });

  // The JSON shape of PublicKeyCredential.toJSON(), which py_webauthn parses.
  const credentialJson = (credential) => {
    const response = credential.response;
    const json = {
      id: credential.id,
      rawId: fromBuffer(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment || null,
      clientExtensionResults: credential.getClientExtensionResults(),
      response: { clientDataJSON: fromBuffer(response.clientDataJSON) },
    };
    if (response.attestationObject) {
      json.response.attestationObject = fromBuffer(response.attestationObject);
      json.response.transports = typeof response.getTransports === 'function' ? response.getTransports() : [];
    } else {
      json.response.authenticatorData = fromBuffer(response.authenticatorData);
      json.response.signature = fromBuffer(response.signature);
      json.response.userHandle = response.userHandle ? fromBuffer(response.userHandle) : null;
    }
    return json;
  };

  const postJson = async (url, body) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || FALLBACK);
    }
    return data;
  };

  const run = async (button, show) => {
    const base = button.dataset.txn ? { txn: button.dataset.txn } : {};
    button.disabled = true;
    show('');
    try {
      const options = await postJson(button.dataset.optionsUrl, base);
      const credential = button.dataset.webauthn === 'create'
        ? await navigator.credentials.create({ publicKey: creationOptions(options) })
        : await navigator.credentials.get({ publicKey: requestOptions(options) });
      const answer = await postJson(button.dataset.submitUrl, { ...base, credential: credentialJson(credential) });
      if (answer.redirect) {
        window.location.assign(answer.redirect);
        return;
      }
      show(answer.error || FALLBACK);
    } catch (error) {
      if (error && error.name === 'NotAllowedError') {
        show('The passkey request was cancelled or timed out.');
      } else {
        show((error && error.message) || FALLBACK);
      }
    }
    button.disabled = false;
  };

  const init = () => {
    document.querySelectorAll('[data-webauthn]').forEach((button) => {
      const status = document.getElementById(button.dataset.status);
      const show = (text) => {
        status.textContent = text;
        status.hidden = !text;
      };
      if (!window.PublicKeyCredential || !navigator.credentials) {
        button.disabled = true;
        show('This browser cannot use passkeys.');
        return;
      }
      button.addEventListener('click', () => run(button, show));
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""

AUTH_CSS = """:root {
  color-scheme: light dark;
  --text: #1d1d1f;
  --muted: #6e6e73;
  --page: #f5f5f7;
  --card: #ffffff;
  --line: #d2d2d7;
  --accent: #0a5cc2;
  --on-accent: #ffffff;
  --error: #b3261e;
}

@media (prefers-color-scheme: dark) {
  :root {
    --text: #f5f5f7;
    --muted: #a1a1a6;
    --page: #000000;
    --card: #1c1c1e;
    --line: #3a3a3c;
    --accent: #4c9aff;
    --on-accent: #000000;
    --error: #ff7b72;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--page);
  color: var(--text);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
}

main {
  width: 100%;
  max-width: 400px;
  padding: 32px 28px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 14px;
}

.brand { margin: 0 0 4px; color: var(--muted); font-size: 0.875rem; font-weight: 600; }
h1 { margin: 0 0 20px; font-size: 1.5rem; line-height: 1.25; }
p { margin: 0 0 16px; }
.muted { color: var(--muted); }
.error { color: var(--error); font-weight: 600; }
.divider { margin: 20px 0 0; color: var(--muted); text-align: center; }
label { display: block; margin: 16px 0 6px; font-weight: 600; }

input {
  width: 100%;
  padding: 10px 12px;
  font: inherit;
  color: inherit;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 8px;
}

button {
  width: 100%;
  margin-top: 20px;
  padding: 11px 16px;
  font: inherit;
  font-weight: 600;
  color: var(--on-accent);
  background: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 8px;
  cursor: pointer;
}

button.secondary { color: var(--accent); background: transparent; }
button:disabled { opacity: 0.6; cursor: default; }
input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
img { display: block; max-width: 100%; height: auto; margin: 16px auto; }
img.qr { background: #ffffff; } /* the QR code's SVG is transparent: keep it scannable in dark mode */
code { word-break: break-all; }
[hidden] { display: none !important; }
"""


def static_routes() -> List[Route]:
    """GET /static/mcp-auth.js and /static/mcp-auth.css, never cached."""
    async def script(request: Request) -> Response:
        return Response(AUTH_JS, media_type="text/javascript", headers=_STATIC_HEADERS)

    async def style(request: Request) -> Response:
        return Response(AUTH_CSS, media_type="text/css", headers=_STATIC_HEADERS)

    return [Route(SCRIPT_PATH, script, methods=["GET"]), Route(STYLE_PATH, style, methods=["GET"])]

"""The OAuth 2.1 authorization server on the SDK's own handlers: Jarvis, login transactions, codes, grants.

Jarvis is the one client: pre-registered, public (no secret, PKCE S256 instead),
with one exact loopback redirect URI. There is no dynamic registration, no
revocation endpoint and no CORS; the routes are the SDK's MetadataHandler,
AuthorizationHandler and TokenHandler, each behind a 16 KiB body limit.

/authorize checks the request, stores a login transaction (new_token("mcptx_"),
kept only as its hash, LOGIN_TXN_TTL_S) and sends the browser to
/login?txn=<id>. The sign-in pages drive the transaction through the helpers
below (get_txn, bind_txn_cookie, update_txn, fail_txn) and finish with
complete_login, which deletes it and mints an authorization code
(new_token("mcpac_"), hashed, AUTH_CODE_TTL_S) bound to the user the password
step named.

A code is spent by a compare-and-swap on used_at in the same transaction that
starts its refresh family and records the family on the code, so a replay, or
the loser of a race between two exchanges, always finds the family the winner
started and revokes it. sessions.start runs inside that transaction as a
savepoint, which is why the provider and its SessionService must share one
AuthDB. Refresh grants are SessionService.lookup and rotate.

While the "disabled" flag is set, /authorize answers temporarily_unavailable and
both grants answer invalid_grant without spending the code or refresh token.
"""
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, List, Literal, Optional, Union

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken
from pydantic import AnyUrl
from starlette.routing import Route, request_response
from starlette.types import ASGIApp

from mcp_server.authdb import AuthDB
from mcp_server.config import AUTH_CODE_TTL_S, LOGIN_TXN_TTL_S, SCOPE, Settings
from mcp_server.crypto import hash_token, new_token
from mcp_server.sessions import IssuedPair, RefreshLookup, SessionService
from mcp_server.tokens import McpTokenVerifier

TXN_PREFIX = "mcptx_"
CODE_PREFIX = "mcpac_"
LOGIN_COOKIE_PREFIX = "mcplc_"
MAX_BODY_BYTES = 16_384
DISABLED_FLAG = "disabled"
CODE_REPLAY_REASON = "code_replay"
TOKEN_REVOKED_REASON = "token_revoked"
GRANT_TYPES = ("authorization_code", "refresh_token")
TXN_STAGES = ("password", "second_factor")

# An S256 challenge is the unpadded base64url of a SHA-256 digest: exactly 43 characters.
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# What the sign-in pages may change on a transaction; the request it carries is fixed at /authorize.
_UPDATABLE_TXN_FIELDS = frozenset({"stage", "user_id", "username", "webauthn_challenge"})
_TXN_COLUMNS = (
    "txn_hash, client_id, redirect_uri, code_challenge, state, scopes, resource, expires_at, cookie_hash, "
    "stage, user_id, username, webauthn_challenge, attempts"
)


@dataclass(frozen=True)
class LoginTxn:
    txn_hash: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    state: Optional[str]
    scopes: List[str]
    resource: str
    expires_at: float
    cookie_hash: Optional[str]
    stage: Literal["password", "second_factor"]
    user_id: Optional[str]
    username: Optional[str]
    webauthn_challenge: Optional[bytes]
    attempts: int


class McpAuthCode(AuthorizationCode):
    code_hash: str


class McpRefreshToken(RefreshToken):
    token_hash: str
    family_id: str
    kind: str


def jarvis_client(settings: Settings) -> OAuthClientInformationFull:
    """The one registered client: public, PKCE only, one exact redirect URI, scope mcp."""
    return OAuthClientInformationFull(
        client_id=settings.jarvis_client_id,
        redirect_uris=[AnyUrl(settings.jarvis_redirect_uri)],
        token_endpoint_auth_method="none",
        grant_types=list(GRANT_TYPES),
        response_types=["code"],
        scope=SCOPE,
        client_name="Jarvis",
    )


def _is_token(value: object, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix)


def _login_txn(row: sqlite3.Row) -> LoginTxn:
    fields = dict(row)
    fields["scopes"] = json.loads(fields["scopes"])
    if fields["webauthn_challenge"] is not None:
        fields["webauthn_challenge"] = bytes(fields["webauthn_challenge"])
    return LoginTxn(**fields)


def _oauth_token(pair: IssuedPair) -> OAuthToken:
    return OAuthToken(access_token=pair.access_token, token_type="Bearer", expires_in=pair.expires_in,
                      scope=pair.scope, refresh_token=pair.refresh_token)


class IntelliStockOAuthProvider:
    """OAuthAuthorizationServerProvider[McpAuthCode, McpRefreshToken, AccessToken]; see the module docstring."""

    def __init__(self, settings: Settings, db: AuthDB, sessions: SessionService, verifier: McpTokenVerifier, *,
                 clock: Callable[[], float]) -> None:
        self._settings = settings
        self._db = db
        self._sessions = sessions
        self._verifier = verifier
        self._clock = clock
        self._client = jarvis_client(settings)

    def _disabled(self) -> bool:
        return self._db.get_flag(DISABLED_FLAG)

    # --- SDK protocol -------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        return self._client if client_id == self._settings.jarvis_client_id else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("dynamic client registration is disabled")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Store a login transaction for a valid request; the URL of its sign-in page."""
        if self._disabled():
            raise AuthorizeError("temporarily_unavailable", "the MCP server is disabled")
        if (not params.redirect_uri_provided_explicitly
                or str(params.redirect_uri) != self._settings.jarvis_redirect_uri):
            raise AuthorizeError("invalid_request", "redirect_uri is required and must be the registered URI")
        if not _CODE_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "code_challenge must be an S256 challenge")
        if not params.resource:
            raise AuthorizeError("invalid_request", "resource is required")
        if params.resource != self._settings.resource_url:
            raise AuthorizeError("invalid_target", "resource is not this server's /mcp URL")
        scopes = params.scopes or [SCOPE]
        txn_id = new_token(TXN_PREFIX)
        now = self._clock()
        self._db.query(
            "INSERT INTO auth_txns (txn_hash, client_id, redirect_uri, code_challenge, state, scopes, resource, "
            "created_at, expires_at, cookie_hash, stage, user_id, username, webauthn_challenge, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'password', NULL, NULL, NULL, 0)",
            (hash_token(txn_id), client.client_id, self._settings.jarvis_redirect_uri, params.code_challenge,
             params.state, json.dumps(scopes), params.resource, now, now + LOGIN_TXN_TTL_S),
        )
        return f"{self._settings.public_base}/login?txn={txn_id}"

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> Optional[McpAuthCode]:
        """The code if it is this client's and unexpired. A spent code still loads, so its replay is seen."""
        if not _is_token(authorization_code, CODE_PREFIX):
            return None
        code_hash = hash_token(authorization_code)
        row = self._db.query_one(
            "SELECT client_id, redirect_uri, code_challenge, resource, scopes, user_id, expires_at "
            "FROM auth_codes WHERE code_hash = ?",
            (code_hash,),
        )
        if row is None or row["client_id"] != client.client_id or self._clock() >= row["expires_at"]:
            return None
        return McpAuthCode(
            code=authorization_code,
            code_hash=code_hash,
            scopes=json.loads(row["scopes"]),
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=True,
            resource=row["resource"],
            subject=row["user_id"],
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: McpAuthCode) -> OAuthToken:
        """Spend the code and start its session; a second use revokes that session."""
        if self._disabled():
            raise TokenError("invalid_grant", "server disabled")
        code_hash = authorization_code.code_hash
        with self._db.tx() as conn:
            spent = conn.execute("UPDATE auth_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                                 (self._clock(), code_hash))
            if spent.rowcount == 1:
                family_id, pair = self._sessions.start(user_id=authorization_code.subject,
                                                       client_id=client.client_id)
                conn.execute("UPDATE auth_codes SET family_id = ? WHERE code_hash = ?", (family_id, code_hash))
                return _oauth_token(pair)
            # Replayed, or lost the race: the winner's family is committed and visible under this lock.
            row = conn.execute("SELECT family_id FROM auth_codes WHERE code_hash = ?", (code_hash,)).fetchone()
        if row is not None and row["family_id"] is not None:
            self._sessions.revoke(row["family_id"], CODE_REPLAY_REASON)
        raise TokenError("invalid_grant", "authorization code was already used")

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> Optional[McpRefreshToken]:
        """The token if it is live and this client's (a spent token past its grace revokes its family)."""
        found = self._sessions.lookup(refresh_token)
        if found is None or found.client_id != client.client_id:
            return None
        return McpRefreshToken(
            token=refresh_token,
            client_id=found.client_id,
            scopes=[SCOPE],
            expires_at=int(found.abs_expires_at),
            subject=found.user_id,
            token_hash=found.token_hash,
            family_id=found.family_id,
            kind=found.kind,
        )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: McpRefreshToken,
                                     scopes: List[str]) -> OAuthToken:
        if self._disabled():
            raise TokenError("invalid_grant", "server disabled")
        pair = self._sessions.rotate(RefreshLookup(
            token_hash=refresh_token.token_hash,
            family_id=refresh_token.family_id,
            user_id=refresh_token.subject,
            client_id=refresh_token.client_id,
            abs_expires_at=float(refresh_token.expires_at),
            kind=refresh_token.kind,
        ))
        if pair is None:
            raise TokenError("invalid_grant", "refresh token is no longer valid")
        return _oauth_token(pair)

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        return await self._verifier.verify_token(token)

    async def revoke_token(self, token: Union[AccessToken, McpRefreshToken]) -> None:
        """Revoke the session behind either token; nothing to do for one already revoked."""
        if isinstance(token, McpRefreshToken):
            family_id = token.family_id
        else:
            family_id = (token.claims or {}).get("sid")
        if isinstance(family_id, str):
            self._sessions.revoke(family_id, TOKEN_REVOKED_REASON)

    # --- Login transactions (the sign-in pages) -----------------------------------------------

    def get_txn(self, txn_id: str) -> Optional[LoginTxn]:
        """The transaction, or None when it is unknown or expired."""
        if not _is_token(txn_id, TXN_PREFIX):
            return None
        row = self._db.query_one(f"SELECT {_TXN_COLUMNS} FROM auth_txns WHERE txn_hash = ?", (hash_token(txn_id),))
        if row is None or self._clock() >= row["expires_at"]:
            return None
        return _login_txn(row)

    def bind_txn_cookie(self, txn_id: str) -> Optional[str]:
        """A new login-cookie value bound to the transaction (stored hashed); None if already bound or gone."""
        if not _is_token(txn_id, TXN_PREFIX):
            return None
        cookie = new_token(LOGIN_COOKIE_PREFIX)
        with self._db.tx() as conn:
            bound = conn.execute(
                "UPDATE auth_txns SET cookie_hash = ? WHERE txn_hash = ? AND cookie_hash IS NULL AND expires_at > ?",
                (hash_token(cookie), hash_token(txn_id), self._clock()),
            ).rowcount
        return cookie if bound == 1 else None

    def update_txn(self, txn_id: str, **fields: Any) -> None:
        """Set stage, user_id, username or webauthn_challenge on a live transaction."""
        unknown = set(fields) - _UPDATABLE_TXN_FIELDS
        if unknown:
            raise ValueError(f"update_txn cannot set {sorted(unknown)}")
        if "stage" in fields and fields["stage"] not in TXN_STAGES:
            raise ValueError(f"stage must be one of {TXN_STAGES}")
        if not fields or not _is_token(txn_id, TXN_PREFIX):
            return
        names = sorted(fields)  # every name is from the allow-list above, never caller text
        assignments = ", ".join(f"{name} = ?" for name in names)
        self._db.query(
            f"UPDATE auth_txns SET {assignments} WHERE txn_hash = ? AND expires_at > ?",
            tuple(fields[name] for name in names) + (hash_token(txn_id), self._clock()),
        )

    def fail_txn(self, txn_id: str, max_attempts: int = 5) -> bool:
        """Count a failed attempt; the max_attempts-th deletes the transaction. False once it is gone."""
        if not _is_token(txn_id, TXN_PREFIX):
            return False
        txn_hash = hash_token(txn_id)
        with self._db.tx() as conn:
            row = conn.execute("SELECT attempts, expires_at FROM auth_txns WHERE txn_hash = ?", (txn_hash,)).fetchone()
            if row is None:
                return False
            attempts = row["attempts"] + 1
            if attempts >= max_attempts or self._clock() >= row["expires_at"]:
                conn.execute("DELETE FROM auth_txns WHERE txn_hash = ?", (txn_hash,))
                return False
            conn.execute("UPDATE auth_txns SET attempts = ? WHERE txn_hash = ?", (attempts, txn_hash))
            return True

    def complete_login(self, txn_id: str, user_id: str) -> str:
        """Trade a signed-in transaction for an authorization code: the redirect URL with code and state.

        The transaction must be live, at stage second_factor, and name user_id (the
        password step sets both); anything else raises LookupError. The transaction
        is deleted, so a second call raises too.
        """
        if not _is_token(txn_id, TXN_PREFIX):
            raise LookupError("no login transaction ready to complete")
        txn_hash = hash_token(txn_id)
        code = new_token(CODE_PREFIX)
        with self._db.tx() as conn:
            now = self._clock()
            row = conn.execute(f"SELECT {_TXN_COLUMNS} FROM auth_txns WHERE txn_hash = ?", (txn_hash,)).fetchone()
            if (row is None or now >= row["expires_at"] or row["stage"] != "second_factor"
                    or row["user_id"] != user_id):
                raise LookupError("no login transaction ready to complete")
            conn.execute("DELETE FROM auth_txns WHERE txn_hash = ?", (txn_hash,))
            conn.execute(
                "INSERT INTO auth_codes (code_hash, client_id, redirect_uri, code_challenge, resource, scopes, "
                "user_id, expires_at, used_at, family_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (hash_token(code), row["client_id"], row["redirect_uri"], row["code_challenge"], row["resource"],
                 row["scopes"], user_id, now + AUTH_CODE_TTL_S),
            )
        return construct_redirect_uri(row["redirect_uri"], code=code, state=row["state"])

    def purge(self) -> None:
        """Delete expired login transactions and authorization codes."""
        with self._db.tx() as conn:
            now = self._clock()
            conn.execute("DELETE FROM auth_txns WHERE expires_at <= ?", (now,))
            conn.execute("DELETE FROM auth_codes WHERE expires_at <= ?", (now,))


def build_as_metadata(settings: Settings) -> OAuthMetadata:
    """RFC 8414 metadata from plain strings: AnyHttpUrl(...) would append "/" to the bare issuer."""
    base = settings.public_base
    return OAuthMetadata.model_validate({
        "issuer": base,
        "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": list(GRANT_TYPES),
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    })


def _body_limited(handler: Callable[..., Any]) -> ASGIApp:
    return RequestBodyLimitMiddleware(request_response(handler), MAX_BODY_BYTES)


def build_auth_routes(provider: IntelliStockOAuthProvider, settings: Settings) -> List[Route]:
    """Metadata, /authorize and /token on the SDK handlers. No /register, no /revoke, no CORS."""
    return [
        Route("/.well-known/oauth-authorization-server",
              endpoint=_body_limited(MetadataHandler(build_as_metadata(settings)).handle), methods=["GET"]),
        Route("/authorize", endpoint=_body_limited(AuthorizationHandler(provider).handle), methods=["GET", "POST"]),
        Route("/token", endpoint=_body_limited(TokenHandler(provider, ClientAuthenticator(provider)).handle),
              methods=["POST"]),
    ]

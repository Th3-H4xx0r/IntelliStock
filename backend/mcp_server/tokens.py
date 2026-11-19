"""Ed25519 signing keys, access-token minting, and the resource server's token check.

Access tokens are EdDSA JWTs (RFC 9068 shape, header typ "at+jwt") that live
ACCESS_TOKEN_TTL_S seconds. Exactly one signing key is current at a time; its
private half is stored as PKCS8 DER sealed under the "signing-key" subkey with
aad = kid, so a blob copied onto another row does not open. A token verifies only
under the current kid, so rotate() invalidates every outstanding access token at
once while refresh tokens keep working.

The keyring reads the current key from the database on every call: the admin CLI
rotates from its own process, and the server must see that at once.
"""
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_der_private_key,
)
from mcp.server.auth.provider import AccessToken

from mcp_server.authdb import AuthDB
from mcp_server.config import ACCESS_TOKEN_TTL_S, SCOPE, Settings
from mcp_server.crypto import derive_key, seal, unseal

TOKEN_TYPE = "at+jwt"
ALGORITHM = "EdDSA"
CLOCK_SKEW_S = 60
REQUIRED_CLAIMS = ["iss", "aud", "sub", "exp", "iat", "jti", "client_id", "sid"]

_log = logging.getLogger(__name__)

_CURRENT_KEY_SQL = (
    "SELECT kid, private_key_enc FROM signing_keys WHERE retired_at IS NULL "
    "ORDER BY created_at DESC, kid DESC LIMIT 1"
)


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class SigningKeyring:
    """The current Ed25519 signing key, created on first use and replaced by rotate()."""

    def __init__(self, db: AuthDB, master_key: bytes, *, clock) -> None:
        self._db = db
        self._key = derive_key(master_key, "signing-key")
        self._clock = clock
        self._cached: Optional[tuple[str, Ed25519PrivateKey]] = None

    def _open(self, kid: str, blob: bytes) -> Ed25519PrivateKey:
        cached = self._cached
        if cached is not None and cached[0] == kid:
            return cached[1]
        private = load_der_private_key(unseal(self._key, blob, kid), password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise TypeError("stored signing key is not an Ed25519 key")
        self._cached = (kid, private)
        return private

    def _insert_new(self, conn: sqlite3.Connection) -> str:
        kid = uuid.uuid4().hex
        der = Ed25519PrivateKey.generate().private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        conn.execute(
            "INSERT INTO signing_keys (kid, private_key_enc, created_at, retired_at) VALUES (?, ?, ?, NULL)",
            (kid, seal(self._key, der, kid), self._clock()),
        )
        return kid

    def current(self) -> tuple[str, Ed25519PrivateKey]:
        """The current (kid, private key); the first call on an empty store creates it."""
        row = self._db.query_one(_CURRENT_KEY_SQL)
        if row is None:
            with self._db.tx() as conn:  # BEGIN IMMEDIATE: a concurrent first call waits, then finds this key
                row = conn.execute(_CURRENT_KEY_SQL).fetchone()
                if row is None:
                    self._insert_new(conn)
                    row = conn.execute(_CURRENT_KEY_SQL).fetchone()
        return row["kid"], self._open(row["kid"], row["private_key_enc"])

    def public_key(self, kid: str) -> Optional[Ed25519PublicKey]:
        """The public key for kid if kid is the current key; never creates one."""
        row = self._db.query_one(_CURRENT_KEY_SQL)
        if row is None or row["kid"] != kid:
            return None
        return self._open(row["kid"], row["private_key_enc"]).public_key()

    def rotate(self) -> str:
        """Retire every key and make a new one current; returns the new kid."""
        with self._db.tx() as conn:
            conn.execute("UPDATE signing_keys SET retired_at = ? WHERE retired_at IS NULL", (self._clock(),))
            return self._insert_new(conn)


class AccessTokenIssuer:
    """Mints access tokens for a refresh family under the current signing key."""

    def __init__(self, keyring: SigningKeyring, settings: Settings, *, clock) -> None:
        self._keyring = keyring
        self._settings = settings
        self._clock = clock

    def issue(self, *, user_id: str, family_id: str) -> tuple[str, int]:
        """(jwt, exp): a token for user_id bound to the refresh family family_id."""
        kid, private = self._keyring.current()
        iat = int(self._clock())
        exp = iat + ACCESS_TOKEN_TTL_S
        claims = {
            "iss": self._settings.issuer,
            "aud": self._settings.resource_url,
            "sub": user_id,
            "client_id": self._settings.jarvis_client_id,
            "sid": family_id,
            "scope": SCOPE,
            "iat": iat,
            "exp": exp,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(claims, private, algorithm=ALGORITHM, headers={"typ": TOKEN_TYPE, "kid": kid})
        return token, exp


@dataclass(frozen=True)
class VerifiedToken:
    user_id: str
    family_id: str
    client_id: str
    exp: int
    jti: str


class McpTokenVerifier:
    """The resource server's check on every /mcp bearer token (mcp.server.auth.provider.TokenVerifier)."""

    def __init__(self, keyring: SigningKeyring, settings: Settings, *, clock,
                 session_active: Callable[[str], bool], disabled: Callable[[], bool]) -> None:
        self._keyring = keyring
        self._settings = settings
        self._clock = clock
        self._session_active = session_active
        self._disabled = disabled

    def verify(self, token: str) -> Optional[VerifiedToken]:
        """The token's identity if every check passes; None for any failure, whatever it was."""
        try:
            return self._verify(token)
        except jwt.InvalidTokenError:
            return None
        except Exception as exc:  # a server-side fault (the keyring, the store): the class only, never the token
            _log.warning("access token check failed on a server-side error (%s)", type(exc).__name__)
            return None

    def _verify(self, token: str) -> Optional[VerifiedToken]:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str):
            return None
        public = self._keyring.public_key(kid)
        if public is None:
            return None
        claims = jwt.decode(
            token,
            public,
            algorithms=[ALGORITHM],
            audience=self._settings.resource_url,
            issuer=self._settings.issuer,
            options={"require": REQUIRED_CLAIMS, "verify_exp": False, "verify_iat": False,
                     "verify_nbf": False, "strict_aud": True},
        )
        # The signature covers the header, so the header read above is the one that was signed.
        now = self._clock()
        iat, exp, sid = claims["iat"], claims["exp"], claims["sid"]
        if not (header.get("typ") == TOKEN_TYPE
                and claims["client_id"] == self._settings.jarvis_client_id
                and claims.get("scope") == SCOPE
                and _is_whole_number(iat) and _is_whole_number(exp)
                and iat <= now + CLOCK_SKEW_S
                and now < exp
                and exp - iat <= ACCESS_TOKEN_TTL_S
                and isinstance(sid, str)):
            return None
        if not self._session_active(sid) or self._disabled():
            return None
        return VerifiedToken(user_id=claims["sub"], family_id=sid, client_id=claims["client_id"], exp=exp,
                             jti=claims["jti"])

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        verified = self.verify(token)
        if verified is None:
            return None
        return AccessToken(
            token=token,
            client_id=verified.client_id,
            scopes=[SCOPE],
            expires_at=verified.exp,
            resource=self._settings.resource_url,
            subject=verified.user_id,
            claims={"iss": self._settings.issuer, "sid": verified.family_id, "jti": verified.jti},
        )

"""Signing keys, access tokens and the resource-server verifier."""
import base64
import json
import os
import re
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from mcp.server.auth.provider import AccessToken

from conftest import FakeClock
from mcp_server.authdb import AuthDB
from mcp_server.crypto import CryptoError, derive_key, unseal
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring, VerifiedToken

USER = "u-123"
FAMILY = "fam-1"
HEX32 = re.compile(r"[0-9a-f]{32}")
_CURRENT = object()  # forge(): sign with the keyring's current key
CLAIMS = {"iss", "aud", "sub", "client_id", "sid", "scope", "iat", "exp", "jti"}


class Sessions:
    """The verifier's two callbacks: which refresh families are active, and the disabled flag."""

    def __init__(self):
        self.active = {FAMILY}
        self.disabled = False
        self.asked = []

    def session_active(self, sid):
        self.asked.append(sid)
        return sid in self.active

    def is_disabled(self):
        return self.disabled


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def keyring(db, settings, clock):
    return SigningKeyring(db, settings.master_key, clock=clock)


@pytest.fixture
def issuer(keyring, settings, clock):
    return AccessTokenIssuer(keyring, settings, clock=clock)


@pytest.fixture
def sessions():
    return Sessions()


def _verifier(keyring, settings, clock, sessions):
    return McpTokenVerifier(keyring, settings, clock=clock, session_active=sessions.session_active,
                            disabled=sessions.is_disabled)


@pytest.fixture
def verifier(keyring, settings, clock, sessions):
    return _verifier(keyring, settings, clock, sessions)


@pytest.fixture
def forge(keyring, settings, clock):
    """Sign a token by hand: the issuer's exact claims and header unless overridden; None drops a claim."""

    def make(headers=None, key=_CURRENT, algorithm="EdDSA", **overrides):
        kid, private = keyring.current()
        iat = int(clock())
        claims = {
            "iss": settings.issuer, "aud": settings.resource_url, "sub": USER,
            "client_id": settings.jarvis_client_id, "sid": FAMILY, "scope": "mcp",
            "iat": iat, "exp": iat + 900, "jti": uuid.uuid4().hex,
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        header = {"typ": "at+jwt", "kid": kid}
        header.update(headers or {})
        return jwt.encode(claims, private if key is _CURRENT else key, algorithm=algorithm, headers=header)

    return make


def _b64(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=")


def _signed_raw(header, claims, private):
    """An EdDSA token assembled by hand, for headers PyJWT refuses to encode (a missing or non-string kid)."""
    signing_input = _b64(header) + b"." + _b64(claims)
    signature = base64.urlsafe_b64encode(private.sign(signing_input)).rstrip(b"=")
    return (signing_input + b"." + signature).decode("ascii")


def _unverified(token):
    return jwt.decode(token, options={"verify_signature": False})


def _db_bytes(path):
    """The main file and its WAL: a fresh row lives in the WAL until a checkpoint."""
    data = b""
    for name in (path, path + "-wal"):
        if os.path.exists(name):
            with open(name, "rb") as handle:
                data += handle.read()
    return data


def test_issue_verify_roundtrip(issuer, keyring, verifier, settings, clock, sessions):
    token, exp = issuer.issue(user_id=USER, family_id=FAMILY)
    kid = keyring.current()[0]
    assert HEX32.fullmatch(kid) and uuid.UUID(kid).version == 4
    assert jwt.get_unverified_header(token) == {"alg": "EdDSA", "typ": "at+jwt", "kid": kid}

    claims = _unverified(token)
    assert set(claims) == CLAIMS
    iat = int(clock())
    assert claims["iss"] == settings.issuer == "https://s7.tail.ts.net:8443"
    assert claims["aud"] == settings.resource_url == "https://s7.tail.ts.net:8443/mcp"
    assert claims["sub"] == USER
    assert claims["client_id"] == "jarvis"
    assert claims["sid"] == FAMILY
    assert claims["scope"] == "mcp"
    assert claims["iat"] == iat
    assert claims["exp"] == exp == iat + 900
    assert HEX32.fullmatch(claims["jti"]) and uuid.UUID(claims["jti"]).version == 4
    # the signature is the current key's
    jwt.decode(token, keyring.public_key(kid), algorithms=["EdDSA"], audience=settings.resource_url)

    assert verifier.verify(token) == VerifiedToken(
        user_id=USER, family_id=FAMILY, client_id="jarvis", exp=exp, jti=claims["jti"])
    assert sessions.asked == [FAMILY]
    assert _unverified(issuer.issue(user_id=USER, family_id=FAMILY)[0])["jti"] != claims["jti"]


def test_hand_signed_baseline_is_accepted(forge, verifier):
    # the control for every rejection below: forge() alone yields a token the verifier accepts
    assert verifier.verify(forge()) is not None


def test_wrong_audience_rejected(forge, verifier, settings):
    assert verifier.verify(forge(aud=settings.public_base)) is None
    assert verifier.verify(forge(aud=settings.resource_url + "/")) is None


def test_wrong_issuer_rejected(forge, verifier, settings):
    assert verifier.verify(forge(iss="https://other.tail.ts.net:8443")) is None
    assert verifier.verify(forge(iss=settings.public_base + "/")) is None


def test_expired_rejected(issuer, verifier, clock):
    token, _ = issuer.issue(user_id=USER, family_id=FAMILY)
    clock.advance(899)
    assert verifier.verify(token) is not None
    clock.advance(2)
    assert verifier.verify(token) is None


def test_expiry_is_exclusive_and_read_from_the_injected_clock(keyring, settings, sessions):
    clock = FakeClock(1_000_000_000)  # far from the real time, so only the injected clock can decide
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    verifier = _verifier(keyring, settings, clock, sessions)
    token, exp = issuer.issue(user_id=USER, family_id=FAMILY)
    assert exp == 1_000_000_900
    clock.advance(899)
    assert verifier.verify(token) is not None
    clock.advance(1)  # now == exp
    assert verifier.verify(token) is None


def test_unknown_client_rejected(forge, verifier):
    assert verifier.verify(forge(client_id="other-client")) is None


def test_alg_none_and_hs256_rejected(forge, keyring, verifier):
    assert verifier.verify(forge(key=None, algorithm="none")) is None
    public = keyring.current()[1].public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert verifier.verify(forge(key=public, algorithm="HS256")) is None


def test_unknown_kid_rejected(forge, keyring, verifier):
    assert verifier.verify(forge(headers={"kid": uuid.uuid4().hex})) is None
    kid, private = keyring.current()
    claims = _unverified(forge())
    assert verifier.verify(_signed_raw({"alg": "EdDSA", "typ": "at+jwt", "kid": kid}, claims, private)) is not None
    assert verifier.verify(_signed_raw({"alg": "EdDSA", "typ": "at+jwt"}, claims, private)) is None
    assert verifier.verify(_signed_raw({"alg": "EdDSA", "typ": "at+jwt", "kid": 7}, claims, private)) is None
    # the current kid on a token signed by some other key
    assert verifier.verify(forge(key=Ed25519PrivateKey.generate())) is None


def test_malformed_tokens_rejected(verifier, keyring):
    keyring.current()
    for token in ("", "garbage", "a.b.c", "a.b", "...."):
        assert verifier.verify(token) is None


def test_typ_must_be_at_jwt(forge, verifier):
    assert verifier.verify(forge(headers={"typ": "JWT"})) is None
    assert verifier.verify(forge(headers={"typ": None})) is None


@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "exp", "iat", "jti", "client_id", "sid", "scope"])
def test_missing_claim_rejected(forge, verifier, claim):
    assert verifier.verify(forge(**{claim: None})) is None


def test_scope_must_be_exactly_mcp(forge, verifier):
    for scope in ("mcp admin", "admin", "", ["mcp"]):
        assert verifier.verify(forge(scope=scope)) is None


def test_iat_and_lifetime_bounds(forge, verifier, clock):
    iat = int(clock())
    assert verifier.verify(forge(iat=iat + 60, exp=iat + 960)) is not None  # 60 s of clock skew
    assert verifier.verify(forge(iat=iat + 61, exp=iat + 961)) is None
    assert verifier.verify(forge(iat=iat, exp=iat + 901)) is None  # longer than 900 s
    assert verifier.verify(forge(iat=float(iat), exp=float(iat + 900))) is None  # NumericDate must be whole
    assert verifier.verify(forge(iat=True, exp=iat + 900)) is None


def test_rotate_invalidates_old_tokens(issuer, keyring, verifier, db, clock):
    old, _ = issuer.issue(user_id=USER, family_id=FAMILY)
    old_kid = keyring.current()[0]
    assert verifier.verify(old) is not None

    clock.advance(5)
    new_kid = keyring.rotate()
    assert HEX32.fullmatch(new_kid) and new_kid != old_kid
    assert keyring.current()[0] == new_kid
    assert keyring.public_key(old_kid) is None
    assert keyring.public_key(new_kid) is not None
    assert verifier.verify(old) is None
    assert verifier.verify(issuer.issue(user_id=USER, family_id=FAMILY)[0]) is not None

    rows = {row["kid"]: row["retired_at"] for row in db.query("SELECT kid, retired_at FROM signing_keys")}
    assert rows == {old_kid: clock(), new_kid: None}


def test_rotation_by_another_process_is_seen(issuer, verifier, settings, clock):
    # the admin CLI rotates from its own process, on its own connection to the same file
    token, _ = issuer.issue(user_id=USER, family_id=FAMILY)
    assert verifier.verify(token) is not None
    other = AuthDB(settings.db_path)
    try:
        SigningKeyring(other, settings.master_key, clock=clock).rotate()
    finally:
        other.close()
    assert verifier.verify(token) is None
    assert verifier.verify(issuer.issue(user_id=USER, family_id=FAMILY)[0]) is not None


def test_first_key_created_lazily_and_persisted(db, keyring, settings, clock, sessions):
    assert keyring.public_key(uuid.uuid4().hex) is None
    assert db.query_one("SELECT COUNT(*) FROM signing_keys")[0] == 0  # a lookup never creates a key

    kid, private = keyring.current()
    assert keyring.current()[0] == kid
    assert db.query_one("SELECT COUNT(*) FROM signing_keys")[0] == 1
    token, _ = AccessTokenIssuer(keyring, settings, clock=clock).issue(user_id=USER, family_id=FAMILY)

    # a restarted server opens the same file and keeps both the key and its tokens
    other = AuthDB(settings.db_path)
    try:
        reopened = SigningKeyring(other, settings.master_key, clock=clock)
        assert reopened.current()[0] == kid
        assert reopened.public_key(kid).public_bytes(Encoding.Raw, PublicFormat.Raw) == \
            private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert _verifier(reopened, settings, clock, sessions).verify(token) is not None
    finally:
        other.close()


def test_revoked_session_rejected(issuer, verifier, sessions):
    token, _ = issuer.issue(user_id=USER, family_id=FAMILY)
    assert verifier.verify(token) is not None
    sessions.active.discard(FAMILY)
    assert verifier.verify(token) is None
    assert sessions.asked == [FAMILY, FAMILY]


def test_disabled_rejects(issuer, verifier, sessions):
    token, _ = issuer.issue(user_id=USER, family_id=FAMILY)
    sessions.disabled = True
    assert verifier.verify(token) is None
    sessions.disabled = False
    assert verifier.verify(token) is not None


def test_private_key_encrypted_at_rest(keyring, db, settings):
    kid, private = keyring.current()
    der = private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    blob = bytes(db.query_one("SELECT private_key_enc FROM signing_keys WHERE kid = ?", (kid,))[0])

    stored = _db_bytes(settings.db_path)
    assert blob in stored  # the bytes read are where the row lives
    assert der not in stored
    assert raw not in stored

    subkey = derive_key(settings.master_key, "signing-key")
    assert unseal(subkey, blob, kid) == der
    with pytest.raises(CryptoError):
        unseal(subkey, blob, uuid.uuid4().hex)  # bound to its own kid


async def test_verify_token_returns_sdk_access_token(issuer, verifier, settings):
    token, exp = issuer.issue(user_id=USER, family_id=FAMILY)
    jti = _unverified(token)["jti"]
    result = await verifier.verify_token(token)
    assert isinstance(result, AccessToken)
    assert result == AccessToken(
        token=token, client_id="jarvis", scopes=["mcp"], expires_at=exp, resource=settings.resource_url,
        subject=USER, claims={"iss": settings.issuer, "sid": FAMILY, "jti": jti})
    assert await verifier.verify_token(token + "x") is None

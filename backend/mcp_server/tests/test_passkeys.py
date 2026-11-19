"""Passkeys: WebAuthn with user verification required, RP ID and origin from settings, counters only moving forward."""
import base64
import json
import sqlite3

import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import mcp_server.passkeys as passkeys_module
from conftest import FakeClock
from mcp_server.authdb import AuthDB
from mcp_server.passkeys import NewPasskey, PasskeyError, PasskeyService
from webauthn_fake import SoftAuthenticator

START = 1_800_000_000.0
USER = "u-123"
OTHER = "u-456"


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def users(db):
    for user_id, username in ((USER, "operator"), (OTHER, "other")):
        db.query("INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc) VALUES (?, ?, ?, ?)",
                 (user_id, username, START, b"sealed"))


@pytest.fixture
def passkeys(db, settings, clock):
    return PasskeyService(db, rp_id=settings.rp_id, origin=settings.public_origin, clock=clock)


@pytest.fixture
def authenticator(settings):
    return SoftAuthenticator(settings.rp_id, settings.public_origin)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _store(db, user_id, passkey):
    """The passkeys row enrollment writes for a verified registration."""
    db.query(
        "INSERT INTO passkeys (credential_id, user_id, public_key, sign_count, transports, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (passkey.credential_id, user_id, passkey.public_key, passkey.sign_count, json.dumps(passkey.transports), START),
    )


def _register(passkeys, db, authenticator, user_id=USER, username="operator"):
    options, challenge = passkeys.registration_options(user_id=user_id, username=username)
    passkey = passkeys.verify_registration(authenticator.create(options), challenge)
    _store(db, user_id, passkey)
    return passkey


def _sign_in(passkeys, authenticator, user_id=USER):
    options, challenge = passkeys.authentication_options(user_id)
    return passkeys.verify_authentication(user_id, authenticator.get(options), challenge)


def _row(db, passkey):
    return db.query_one("SELECT sign_count, last_used_at FROM passkeys WHERE credential_id = ?",
                        (passkey.credential_id,))


def _unused(db, passkey):
    row = _row(db, passkey)
    return (row["sign_count"], row["last_used_at"]) == (passkey.sign_count, None)


def test_register_then_authenticate(passkeys, db, users, authenticator, clock):
    passkey = _register(passkeys, db, authenticator)
    assert isinstance(passkey, NewPasskey)
    assert passkey.credential_id == authenticator.credential_id
    assert passkey.sign_count == 0
    assert passkey.transports == ["internal"]
    cose_key = cbor2.loads(passkey.public_key)
    assert (cose_key[1], cose_key[3], cose_key[-1]) == (2, -7, 1)  # EC2, ES256, P-256
    assert _unused(db, passkey)
    for n in (1, 2, 3):
        clock.advance(60)
        assert _sign_in(passkeys, authenticator) is True
        row = _row(db, passkey)
        assert (row["sign_count"], row["last_used_at"]) == (n, START + 60 * n)


def test_user_verification_required(passkeys, db, users, authenticator, settings):
    unverified = SoftAuthenticator(settings.rp_id, settings.public_origin, user_verified=False)
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    with pytest.raises(PasskeyError):
        passkeys.verify_registration(unverified.create(options), challenge)
    # Registered with user verification, the same authenticator signing in without it is refused.
    passkey = _register(passkeys, db, authenticator)
    authenticator.user_verified = False
    assert _sign_in(passkeys, authenticator) is False
    assert _unused(db, passkey)
    authenticator.user_verified = True
    assert _sign_in(passkeys, authenticator) is True


def test_wrong_origin_rejected(passkeys, db, users, authenticator, settings):
    near_misses = ["https://evil.example", "https://s7.tail.ts.net", "http://s7.tail.ts.net:8443",
                   "https://s7.tail.ts.net:8444", "https://tail.ts.net:8443", settings.public_origin + "/"]
    for origin in near_misses:
        phishing = SoftAuthenticator(settings.rp_id, origin)
        options, challenge = passkeys.registration_options(user_id=USER, username="operator")
        with pytest.raises(PasskeyError):
            passkeys.verify_registration(phishing.create(options), challenge)
    passkey = _register(passkeys, db, authenticator)
    for origin in near_misses:
        authenticator.origin = origin
        assert _sign_in(passkeys, authenticator) is False, origin
    assert _unused(db, passkey)
    authenticator.origin = settings.public_origin
    assert _sign_in(passkeys, authenticator) is True


def test_wrong_rp_id_rejected(passkeys, db, users, authenticator, settings):
    # tail.ts.net is a registrable parent a browser would accept as an RP ID; the server pins s7.tail.ts.net.
    wrong = ["evil.example", "tail.ts.net", "s7.tail.ts.net.evil.example", "S7.TAIL.TS.NET"]
    for rp_id in wrong:
        foreign = SoftAuthenticator(rp_id, settings.public_origin)
        options, challenge = passkeys.registration_options(user_id=USER, username="operator")
        with pytest.raises(PasskeyError):
            passkeys.verify_registration(foreign.create(options), challenge)
    passkey = _register(passkeys, db, authenticator)
    for rp_id in wrong:
        authenticator.rp_id = rp_id
        assert _sign_in(passkeys, authenticator) is False, rp_id
    assert _unused(db, passkey)
    authenticator.rp_id = settings.rp_id
    assert _sign_in(passkeys, authenticator) is True


def test_replayed_assertion_rejected_by_sign_count(passkeys, db, users, authenticator):
    passkey = _register(passkeys, db, authenticator)
    options, challenge = passkeys.authentication_options(USER)
    assertion = authenticator.get(options)
    assert passkeys.verify_authentication(USER, assertion, challenge) is True
    assert passkeys.verify_authentication(USER, assertion, challenge) is False  # replayed as is
    # An assertion made earlier but presented after a later one: the counter has already moved past it.
    early_options, early_challenge = passkeys.authentication_options(USER)
    early = authenticator.get(early_options)
    late_options, late_challenge = passkeys.authentication_options(USER)
    late = authenticator.get(late_options)
    assert passkeys.verify_authentication(USER, late, late_challenge) is True
    assert passkeys.verify_authentication(USER, early, early_challenge) is False
    assert _row(db, passkey)["sign_count"] == 3
    # A cloned authenticator whose counter runs behind the stored one.
    authenticator.sign_count = 1
    assert _sign_in(passkeys, authenticator) is False
    assert _row(db, passkey)["sign_count"] == 3


def test_counterless_authenticator_signs_in_repeatedly(passkeys, db, users, settings, clock):
    # Synced passkeys (iCloud Keychain, Google Password Manager) report a counter of 0 on every assertion.
    synced = SoftAuthenticator(settings.rp_id, settings.public_origin, counter=False)
    passkey = _register(passkeys, db, synced)
    for n in (1, 2, 3):
        clock.advance(60)
        assert _sign_in(passkeys, synced) is True
        row = _row(db, passkey)
        assert (row["sign_count"], row["last_used_at"]) == (0, START + 60 * n)


def test_credential_of_other_user_rejected(passkeys, db, users, authenticator, settings):
    mine = _register(passkeys, db, authenticator, USER, "operator")
    other_authenticator = SoftAuthenticator(settings.rp_id, settings.public_origin)
    theirs = _register(passkeys, db, other_authenticator, OTHER, "other")
    options, _ = passkeys.authentication_options(USER)
    assert [c["id"] for c in json.loads(options)["allowCredentials"]] == [_b64url(mine.credential_id)]
    # A valid assertion of the other user's passkey, presented as this user, on the other user's challenge.
    their_options, their_challenge = passkeys.authentication_options(OTHER)
    assertion = other_authenticator.get(their_options)
    assert passkeys.verify_authentication(USER, assertion, their_challenge) is False
    assert passkeys.verify_authentication("u-nobody", assertion, their_challenge) is False
    assert _unused(db, theirs) and _unused(db, mine)
    # The same assertion is good for its own user, so the refusals above were about ownership alone.
    assert passkeys.verify_authentication(OTHER, assertion, their_challenge) is True


def test_wrong_challenge_rejected(passkeys, db, users, authenticator):
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    _, other_challenge = passkeys.registration_options(user_id=USER, username="operator")
    credential = authenticator.create(options)
    with pytest.raises(PasskeyError):
        passkeys.verify_registration(credential, other_challenge)
    passkey = passkeys.verify_registration(credential, challenge)
    _store(db, USER, passkey)
    options, challenge = passkeys.authentication_options(USER)
    _, other_challenge = passkeys.authentication_options(USER)
    assertion = authenticator.get(options)
    assert passkeys.verify_authentication(USER, assertion, other_challenge) is False
    assert _unused(db, passkey)
    assert passkeys.verify_authentication(USER, assertion, challenge) is True


def test_malformed_or_tampered_credentials_rejected(passkeys, db, users, authenticator):
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    good = authenticator.create(options)
    bad_registrations = [
        {}, [], "not a credential", None,
        {**good, "response": {}},
        {**good, "response": "x"},
        {**good, "rawId": "!!!"},
        {**good, "type": "password"},
        {**good, "response": {**good["response"], "attestationObject": _b64url(b"not cbor")}},
    ]
    for credential in bad_registrations:
        with pytest.raises(PasskeyError):
            passkeys.verify_registration(credential, challenge)
    passkey = passkeys.verify_registration(good, challenge)  # the untouched response
    _store(db, USER, passkey)

    options, challenge = passkeys.authentication_options(USER)
    good = authenticator.get(options)
    response = good["response"]
    signature = base64.urlsafe_b64decode(response["signature"] + "==")
    auth_data = bytearray(base64.urlsafe_b64decode(response["authenticatorData"] + "=="))
    auth_data[36] += 1  # the counter's low byte, after rpIdHash (32) and flags (1)
    bad_assertions = [
        {}, [], "not a credential", None,
        {**good, "response": {}},
        {**good, "id": _b64url(b"another id")},
        {**good, "rawId": "!!!"},
        {**good, "response": {**response, "signature": _b64url(signature[:-1] + bytes([signature[-1] ^ 1]))}},
        {**good, "response": {**response, "authenticatorData": _b64url(bytes(auth_data))}},
    ]
    for credential in bad_assertions:
        assert passkeys.verify_authentication(USER, credential, challenge) is False
    assert _unused(db, passkey)
    assert passkeys.verify_authentication(USER, good, challenge) is True


def _with_cose_key(credential, cose_key):
    """The registration response with its attested public key swapped; "none" attestation signs nothing."""
    response = credential["response"]
    attestation = cbor2.loads(base64.urlsafe_b64decode(response["attestationObject"] + "=="))
    auth_data = attestation["authData"]
    id_length = int.from_bytes(auth_data[53:55], "big")  # after rpIdHash, flags, counter (37) and AAGUID (16)
    attestation["authData"] = auth_data[:55 + id_length] + cose_key
    return {**credential, "response": {**response, "attestationObject": _b64url(cbor2.dumps(attestation))}}


def test_registration_of_an_unoffered_key_type_rejected(passkeys, authenticator):
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    credential = authenticator.create(options)
    public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_numbers()
    rs256 = cbor2.dumps({1: 3, 3: -257, -1: public.n.to_bytes(256, "big"), -2: public.e.to_bytes(3, "big")})
    with pytest.raises(PasskeyError):
        passkeys.verify_registration(_with_cose_key(credential, rs256), challenge)
    # The same splice with the authenticator's own ES256 key verifies, so only the key type was refused.
    es256 = passkeys.verify_registration(credential, challenge).public_key
    assert passkeys.verify_registration(_with_cose_key(credential, es256), challenge).public_key == es256


def test_registration_of_a_stored_credential_rejected(passkeys, db, users, authenticator):
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    credential = authenticator.create(options)
    # Verifying writes nothing: the same response verifies until enrollment stores it.
    first = passkeys.verify_registration(credential, challenge)
    assert passkeys.verify_registration(credential, challenge) == first
    _store(db, USER, first)
    with pytest.raises(PasskeyError):
        passkeys.verify_registration(credential, challenge)


def test_sign_count_update_is_a_compare_and_swap(passkeys, db, users, authenticator, monkeypatch):
    passkey = _register(passkeys, db, authenticator)
    real_verify = passkeys_module.verify_authentication_response

    def racing_verify(**kwargs):
        # Another worker spends the same assertion between this call's read of the row and its write.
        verified = real_verify(**kwargs)
        db.query("UPDATE passkeys SET sign_count = ? WHERE credential_id = ?",
                 (verified.new_sign_count, passkey.credential_id))
        return verified

    monkeypatch.setattr(passkeys_module, "verify_authentication_response", racing_verify)
    assert _sign_in(passkeys, authenticator) is False
    row = _row(db, passkey)
    assert (row["sign_count"], row["last_used_at"]) == (1, None)


def test_store_error_raises_rather_than_failing(passkeys, db, users, authenticator):
    # A broken store is a server fault: it must not read as a failed passkey and count towards a lockout.
    _register(passkeys, db, authenticator)
    options, challenge = passkeys.authentication_options(USER)
    assertion = authenticator.get(options)
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):
        passkeys.verify_authentication(USER, assertion, challenge)


def test_options_require_uv_and_exclude_existing(passkeys, db, users, authenticator, settings):
    passkey = _register(passkeys, db, authenticator)
    options, challenge = passkeys.registration_options(user_id=USER, username="operator")
    parsed = json.loads(options)
    assert parsed["authenticatorSelection"]["userVerification"] == "required"
    assert parsed["authenticatorSelection"]["residentKey"] == "preferred"
    assert parsed["excludeCredentials"] == [
        {"id": _b64url(passkey.credential_id), "type": "public-key", "transports": ["internal"]}]
    assert parsed["rp"] == {"id": settings.rp_id, "name": "IntelliStock MCP"}
    assert parsed["user"] == {"id": _b64url(USER.encode()), "name": "operator", "displayName": "operator"}
    assert parsed["challenge"] == _b64url(challenge) and len(challenge) >= 32
    assert [p["alg"] for p in parsed["pubKeyCredParams"]] == [-7, -8]  # ES256, EdDSA
    assert (parsed["timeout"], parsed["attestation"]) == (120000, "none")
    assert passkeys.registration_options(user_id=USER, username="operator")[1] != challenge
    # Only the user's own passkeys are excluded.
    assert json.loads(passkeys.registration_options(user_id=OTHER, username="other")[0])["excludeCredentials"] == []

    options, challenge = passkeys.authentication_options(USER)
    parsed = json.loads(options)
    assert parsed["userVerification"] == "required"
    assert (parsed["rpId"], parsed["timeout"]) == (settings.rp_id, 120000)
    assert parsed["allowCredentials"] == [
        {"id": _b64url(passkey.credential_id), "type": "public-key", "transports": ["internal"]}]
    assert parsed["challenge"] == _b64url(challenge) and len(challenge) >= 32
    assert passkeys.authentication_options(USER)[1] != challenge
    named = PasskeyService(db, rp_id=settings.rp_id, origin=settings.public_origin, clock=FakeClock(START),
                           rp_name="IntelliStock Lab")
    named_options = json.loads(named.registration_options(user_id=USER, username="operator")[0])
    assert named_options["rp"]["name"] == "IntelliStock Lab"

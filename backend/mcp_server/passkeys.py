"""Passkeys, the second factor with user verification: WebAuthn on py_webauthn.

The RP ID is MCP_RP_ID and the one accepted origin is MCP_PUBLIC_BASE's origin,
both from settings, so a passkey is bound to that hostname (spec section 5.2).
Attestation is "none", resident keys are preferred, and ES256 and EdDSA are the
only key types offered or accepted. Every ceremony requires user verification:
a passkey that was only touched, without the device's PIN or biometric, is
refused.

A passkeys row is (credential_id, user_id, public_key = the COSE key,
sign_count, transports = a JSON array of transport names, created_at,
last_used_at). Enrollment writes it from a NewPasskey; this module reads it and,
after each sign-in, moves sign_count and last_used_at. A registration whose
credential id is already stored, for any user, is refused (WebAuthn section 7.1).

An assertion counts only for its own user: the row is looked up by credential
id AND user_id. A counter that does not move forward is refused as a replay or
a cloned authenticator, and the write is a compare-and-swap on the count the
assertion was checked against, so two workers holding one assertion get one
success. Synced passkeys (iCloud Keychain, Google Password Manager) report 0
forever; for them the challenge, which the caller stores and spends once, is
the only replay defence.

A credential that does not parse or verify is a failed attempt: PasskeyError
from verify_registration, False from verify_authentication. A store error
raises: that is a broken server, not a bad passkey.
"""
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_authentication_credential_json, parse_registration_credential_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from mcp_server.authdb import AuthDB

RP_NAME = "IntelliStock MCP"
CEREMONY_TIMEOUT_MS = 120_000
PUB_KEY_ALGS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.EDDSA]
_TRANSPORTS = {transport.value for transport in AuthenticatorTransport}


class PasskeyError(Exception):
    """A registration response that did not verify, or one for a credential already stored."""


@dataclass(frozen=True)
class NewPasskey:
    """A verified registration, as enrollment stores it in a passkeys row."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]


class PasskeyService:
    """WebAuthn registration and sign-in for the store's users; see the module docstring for the rules."""

    def __init__(self, db: AuthDB, *, rp_id: str, origin: str, clock, rp_name: str = RP_NAME) -> None:
        self._db = db
        self._rp_id = rp_id
        self._origin = origin
        self._clock = clock
        self._rp_name = rp_name

    def registration_options(self, *, user_id: str, username: str) -> tuple[str, bytes]:
        """(options JSON for navigator.credentials.create(), the challenge to keep for verify_registration)."""
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_name=username,
            user_id=user_id.encode("utf-8"),
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=self._descriptors(user_id),
            supported_pub_key_algs=PUB_KEY_ALGS,
            timeout=CEREMONY_TIMEOUT_MS,
        )
        return options_to_json(options), options.challenge

    def verify_registration(self, credential: Mapping[str, Any], challenge: bytes) -> NewPasskey:
        """The verified passkey; raises PasskeyError. Writes nothing: enrollment stores the result."""
        try:
            parsed = parse_registration_credential_json(dict(credential))
            verified = verify_registration_response(
                credential=parsed,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                require_user_verification=True,
                supported_pub_key_algs=PUB_KEY_ALGS,
            )
        except Exception as exc:
            raise PasskeyError("passkey registration did not verify") from exc
        if self._db.query_one("SELECT 1 FROM passkeys WHERE credential_id = ?", (verified.credential_id,)):
            raise PasskeyError("passkey is already registered")
        return NewPasskey(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=[transport.value for transport in parsed.response.transports or []],
        )

    def authentication_options(self, user_id: str) -> tuple[str, bytes]:
        """(options JSON for navigator.credentials.get(), the challenge to keep for verify_authentication)."""
        options = generate_authentication_options(
            rp_id=self._rp_id,
            allow_credentials=self._descriptors(user_id),
            user_verification=UserVerificationRequirement.REQUIRED,
            timeout=CEREMONY_TIMEOUT_MS,
        )
        return options_to_json(options), options.challenge

    def verify_authentication(self, user_id: str, credential: Mapping[str, Any], challenge: bytes) -> bool:
        """True for a verified assertion of one of user_id's passkeys whose counter moved forward."""
        try:
            parsed = parse_authentication_credential_json(dict(credential))
        except Exception:
            return False
        row = self._db.query_one(
            "SELECT public_key, sign_count FROM passkeys WHERE credential_id = ? AND user_id = ?",
            (parsed.raw_id, user_id),
        )
        if row is None:
            return False
        try:
            verified = verify_authentication_response(
                credential=parsed,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=row["public_key"],
                credential_current_sign_count=row["sign_count"] or 0,
                require_user_verification=True,
            )
        except Exception:
            return False
        with self._db.tx() as conn:
            # Another worker may have spent this assertion since the read: the count it was checked against decides.
            moved = conn.execute(
                "UPDATE passkeys SET sign_count = ?, last_used_at = ? "
                "WHERE credential_id = ? AND user_id = ? AND sign_count IS ?",
                (verified.new_sign_count, self._clock(), parsed.raw_id, user_id, row["sign_count"]),
            )
            return moved.rowcount == 1

    def _descriptors(self, user_id: str) -> list[PublicKeyCredentialDescriptor]:
        """The user's passkeys, oldest first, as options list them."""
        rows = self._db.query(
            "SELECT credential_id, transports FROM passkeys WHERE user_id = ? ORDER BY created_at, credential_id",
            (user_id,),
        )
        return [PublicKeyCredentialDescriptor(id=bytes(row["credential_id"]), transports=_transports(row["transports"]))
                for row in rows]


def _transports(stored: Optional[str]) -> Optional[list[AuthenticatorTransport]]:
    """The stored JSON array's known transport names; None, no hint at all, when there are none."""
    try:
        names = json.loads(stored) if stored else []
    except ValueError:
        return None
    if not isinstance(names, list):
        return None
    known = [AuthenticatorTransport(name) for name in names if isinstance(name, str) and name in _TRANSPORTS]
    return known or None

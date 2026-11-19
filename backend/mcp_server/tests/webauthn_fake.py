"""A software WebAuthn authenticator for tests: ES256 keys, "none" attestation, a signature counter.

It plays the browser and the authenticator together: create() answers
navigator.credentials.create() options with a RegistrationCredential JSON and
get() answers navigator.credentials.get() options with an
AuthenticationCredential JSON, both in the shape a browser's toJSON() gives.
It is written from the WebAuthn spec with cbor2 and cryptography, not with
py_webauthn, so the server's checks run against an independent implementation.

rp_id, origin, user_verified and sign_count are plain attributes. A test
changes them to play a foreign RP ID, a phishing origin, an authenticator that
skipped user verification, or a clone whose counter runs behind. The options'
own RP ID is not checked: authData always hashes self.rp_id.
"""
import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40
ES256 = -7
TRANSPORTS = ["internal"]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    """One authenticator, any number of credentials, one counter shared by them."""

    def __init__(self, rp_id: str, origin: str, *, user_verified: bool = True, counter: bool = True) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.user_verified = user_verified
        # counter=False reports 0 on every assertion, as synced passkeys do.
        self.counter = counter
        self.sign_count = 0
        self.credential_id = None  # the newest credential's id
        self._credentials = {}  # credential id -> (private key, user handle as base64url)

    def create(self, options_json: str) -> dict:
        """A new ES256 credential for these creation options, as RegistrationCredential JSON."""
        options = json.loads(options_json)
        if ES256 not in [param.get("alg") for param in options["pubKeyCredParams"]]:
            raise ValueError("NotSupportedError: the options do not offer ES256")
        key = ec.generate_private_key(ec.SECP256R1())
        credential_id = os.urandom(32)
        public = key.public_key().public_numbers()
        # COSE EC2 key: kty 2, alg ES256, crv P-256, x, y.
        cose_key = cbor2.dumps({1: 2, 3: ES256, -1: 1,
                                -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
        attested = bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose_key
        auth_data = self._auth_data(FLAG_AT, self.sign_count if self.counter else 0) + attested
        client_data = self._client_data("webauthn.create", options["challenge"])
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        self._credentials[credential_id] = (key, options["user"]["id"])
        self.credential_id = credential_id
        return {
            "id": b64url(credential_id),
            "rawId": b64url(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": list(TRANSPORTS),
            },
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    def get(self, options_json: str) -> dict:
        """An assertion by the newest held credential the options allow, as AuthenticationCredential JSON."""
        options = json.loads(options_json)
        allowed = [b64url_decode(entry["id"]) for entry in options.get("allowCredentials") or []]
        held = [credential_id for credential_id in self._credentials if not allowed or credential_id in allowed]
        if not held:
            raise LookupError("NotAllowedError: no credential here matches the options")
        credential_id = held[-1]
        key, user_handle = self._credentials[credential_id]
        if self.counter:
            self.sign_count += 1
        auth_data = self._auth_data(0, self.sign_count if self.counter else 0)
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(credential_id),
            "rawId": b64url(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": user_handle,
            },
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    def _auth_data(self, extra_flags: int, count: int) -> bytes:
        flags = FLAG_UP | (FLAG_UV if self.user_verified else 0) | extra_flags
        return hashlib.sha256(self.rp_id.encode("utf-8")).digest() + bytes([flags]) + count.to_bytes(4, "big")

    def _client_data(self, ceremony: str, challenge: str) -> bytes:
        data = {"type": ceremony, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

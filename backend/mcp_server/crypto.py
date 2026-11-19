"""Key derivation, sealed (encrypted and authenticated) blobs, and opaque tokens.

One master key (MCP_TOTP_ENC_KEY) yields an independent subkey per purpose via
HKDF-SHA256. A sealed blob is a version byte, a random 96-bit nonce and the
AES-256-GCM ciphertext with its tag; the associated data binds a blob to the row
it belongs to, so a blob copied onto another row fails to open. Opaque tokens are
stored only as their SHA-256 hex digest.
"""
import hashlib
import secrets
from typing import Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PURPOSES = frozenset({"totp", "refresh-grace", "signing-key"})
KEY_BYTES = 32
_VERSION = b"\x01"
_NONCE_BYTES = 12
_TAG_BYTES = 16

Data = Union[bytes, bytearray, memoryview, str]


class CryptoError(Exception):
    """A sealed blob did not open: wrong key or aad, tampered, truncated, or an unknown version."""


def _as_bytes(value: Data) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def derive_key(master: bytes, purpose: str) -> bytes:
    """The 32-byte subkey for one purpose; the same master and purpose always give the same key."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown key purpose {purpose!r}; expected one of {sorted(PURPOSES)}")
    if len(master) != KEY_BYTES:
        raise ValueError(f"master key must be {KEY_BYTES} bytes")
    info = b"intellistock-mcp/" + purpose.encode("ascii") + b"/v1"
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=info).derive(bytes(master))


def seal(key: bytes, plaintext: Data, aad: Data) -> bytes:
    """Encrypt and authenticate plaintext, bound to aad. A str is taken as UTF-8."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, _as_bytes(plaintext), _as_bytes(aad))


def unseal(key: bytes, blob: Union[bytes, bytearray, memoryview], aad: Data) -> bytes:
    """The plaintext of a blob from seal(); raises CryptoError if it does not authenticate."""
    blob = bytes(blob)
    if len(blob) < 1 + _NONCE_BYTES + _TAG_BYTES or blob[:1] != _VERSION:
        raise CryptoError("sealed blob is truncated or has an unknown version")
    nonce, ciphertext = blob[1:1 + _NONCE_BYTES], blob[1 + _NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _as_bytes(aad))
    except InvalidTag:
        raise CryptoError("sealed blob failed authentication") from None


def hash_token(token: str) -> str:
    """The SHA-256 hex digest under which an opaque token is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix: str) -> str:
    """A fresh opaque token: prefix + 32 random bytes, URL-safe base64 (43 characters)."""
    return prefix + secrets.token_urlsafe(32)

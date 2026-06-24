"""Kalshi v2 request signing: RSA-PSS over SHA256.

The signed message is `f"{timestamp_ms}{METHOD}{path}"` where `path` EXCLUDES
the query string — signing the path with its query is the single most common
cause of 401s. The signature is base64-encoded into the KALSHI-ACCESS-SIGNATURE
header alongside the key id and timestamp.
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


def load_private_key(pem: str | bytes) -> RSAPrivateKey:
    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("Kalshi API key must be an RSA private key")
    return key


def sign_text(private_key: RSAPrivateKey, text: str) -> str:
    """RSA-PSS(SHA256, MGF1(SHA256), salt=digest length) over `text`,
    base64-encoded."""
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def signed_message(method: str, path: str, ts_ms: int) -> str:
    """The exact string Kalshi expects to be signed. Path is stripped of any
    query string."""
    bare_path = path.split("?", 1)[0]
    return f"{ts_ms}{method.upper()}{bare_path}"


def sign_request(private_key_pem: str | bytes, method: str, path: str, ts_ms: int) -> str:
    key = load_private_key(private_key_pem)
    return sign_text(key, signed_message(method, path, ts_ms))


def access_headers(
    *, key_id: str, method: str, path: str, ts_ms: int, private_key_pem: str | bytes
) -> dict:
    """The three KALSHI-ACCESS-* headers for an authenticated request."""
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key_pem, method, path, ts_ms),
        "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
    }

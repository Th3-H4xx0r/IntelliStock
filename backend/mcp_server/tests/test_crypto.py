"""Crypto primitives: HKDF subkeys, versioned AES-GCM blobs, token minting and hashing."""
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mcp_server.crypto import CryptoError, derive_key, hash_token, new_token, seal, unseal

MASTER = bytes(range(32))


def _key(purpose="totp"):
    return derive_key(MASTER, purpose)


def test_seal_roundtrip():
    key = _key()
    blob = seal(key, b"JBSWY3DPEHPK3PXP", b"user-1")
    assert blob[:1] == b"\x01"
    assert len(blob) == 1 + 12 + len(b"JBSWY3DPEHPK3PXP") + 16
    assert b"JBSWY3DPEHPK3PXP" not in blob
    assert unseal(key, blob, b"user-1") == b"JBSWY3DPEHPK3PXP"
    # a fresh random nonce every time
    assert seal(key, b"JBSWY3DPEHPK3PXP", b"user-1")[1:13] != blob[1:13]


def test_seal_accepts_str_as_utf8():
    key = _key()
    blob = seal(key, "s\u00e9cret", "kid-1")
    assert unseal(key, blob, b"kid-1") == "s\u00e9cret".encode("utf-8")
    assert unseal(key, seal(key, b"x", b"kid-1"), "kid-1") == b"x"


def test_seal_empty_plaintext_roundtrip():
    key = _key()
    assert unseal(key, seal(key, b"", b"a"), b"a") == b""


def test_unseal_wrong_aad_raises():
    key = _key()
    blob = seal(key, b"secret", b"user-1")
    with pytest.raises(CryptoError):
        unseal(key, blob, b"user-2")


def test_unseal_wrong_key_raises():
    blob = seal(_key("totp"), b"secret", b"a")
    with pytest.raises(CryptoError):
        unseal(_key("refresh-grace"), blob, b"a")


def test_unseal_tampered_raises():
    key = _key()
    blob = seal(key, b"secret", b"a")
    for index in (0, 1, 12, 13, len(blob) - 1):
        tampered = bytearray(blob)
        tampered[index] ^= 0x01
        with pytest.raises(CryptoError):
            unseal(key, bytes(tampered), b"a")
    for short in (b"", blob[:1], blob[:13], blob[:28]):
        with pytest.raises(CryptoError):
            unseal(key, short, b"a")
    with pytest.raises(CryptoError):
        unseal(key, b"\x02" + blob[1:], b"a")


def test_derive_key_purposes_differ():
    keys = {purpose: derive_key(MASTER, purpose) for purpose in ("totp", "refresh-grace", "signing-key")}
    assert len(set(keys.values())) == 3
    assert all(len(key) == 32 for key in keys.values())
    assert derive_key(MASTER, "totp") == keys["totp"]
    assert derive_key(bytes(32), "totp") != keys["totp"]


def test_derive_key_is_hkdf_sha256_with_versioned_info():
    # pins the derivation: changing it orphans every secret already sealed at rest
    for purpose in ("totp", "refresh-grace", "signing-key"):
        expected = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                        info=b"intellistock-mcp/" + purpose.encode() + b"/v1").derive(MASTER)
        assert derive_key(MASTER, purpose) == expected


def test_derive_key_rejects_unknown_purpose_and_bad_master():
    with pytest.raises(ValueError):
        derive_key(MASTER, "totp ")
    with pytest.raises(ValueError):
        derive_key(MASTER[:31], "totp")


def test_hash_token_is_sha256_hex():
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    token = new_token("mcprt_")
    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_new_token_prefix_and_entropy():
    tokens = [new_token("mcprt_") for _ in range(100)]
    assert len(set(tokens)) == 100
    for token in tokens:
        assert token.startswith("mcprt_")
        assert len(token) - len("mcprt_") >= 43
        body = token[len("mcprt_"):]
        assert set(body) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

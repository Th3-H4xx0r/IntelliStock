"""secret_store: Fernet round-trip, legacy passthrough, key-missing error."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest

# Skip entire module if cryptography isn't installed; these are integration-style
# tests of Fernet behaviour. secret_store itself still loads fine.
pytest.importorskip("cryptography")


@pytest.fixture
def with_key(monkeypatch):
    from cryptography.fernet import Fernet
    k = Fernet.generate_key().decode()
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", k)
    return k


def test_encrypt_decrypt_roundtrip(with_key):
    from secret_store import encrypt, decrypt
    ct = encrypt("super_secret_key_123")
    assert ct is not None
    assert ct != "super_secret_key_123"
    assert ct.startswith("fernet:")
    assert decrypt(ct) == "super_secret_key_123"


def test_none_passthrough(with_key):
    from secret_store import encrypt, decrypt
    assert encrypt(None) is None
    assert decrypt(None) is None


def test_legacy_plaintext_decrypts_as_is(with_key):
    """Legacy plaintext rows should decrypt unchanged for gradual migration."""
    from secret_store import decrypt
    assert decrypt("APCA_LIVE_KEY_NOT_ENCRYPTED") == "APCA_LIVE_KEY_NOT_ENCRYPTED"


def test_encrypt_without_key_errors(monkeypatch):
    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    from secret_store import encrypt
    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        encrypt("x")


def test_is_encrypted_detects_tag(with_key):
    from secret_store import encrypt, is_encrypted
    ct = encrypt("x")
    assert is_encrypted(ct)
    assert not is_encrypted("plain")
    assert not is_encrypted(None)

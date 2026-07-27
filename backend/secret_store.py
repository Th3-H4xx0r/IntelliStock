"""Fernet-based credential store.

Secrets (Alpaca API key/secret, Robinhood tokens) are Fernet-encrypted before
write to the BrokerageAccounts RethinkDB rows. The key is loaded from the
INTELLISTOCK_CRED_KEY env var; the repo never contains it.

Legacy plaintext values are passed through decrypt() unchanged - this enables
gradual migration from plaintext to encrypted rows.

HARD RULE: encrypt() refuses to run without INTELLISTOCK_CRED_KEY set.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except ImportError:
    Fernet = None  # type: ignore[misc,assignment]
    InvalidToken = Exception  # type: ignore[misc,assignment]
    _HAS_FERNET = False


ENV_KEY = "INTELLISTOCK_CRED_KEY"
_TAG = "fernet:"  # stored-value marker


def _fernet():
    if not _HAS_FERNET:
        raise RuntimeError("cryptography package is required for secret_store")
    k = os.environ.get(ENV_KEY)
    if not k:
        raise RuntimeError(
            f"{ENV_KEY} env var is required to encrypt/decrypt secrets. "
            "Generate one via: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
            "and load it from your OS keyring or systemd LoadCredential, NOT from the repo."
        )
    # R18 (2026-04-25): defensive normalization. Dockploy's env-var editor
    # (and some shell launchers) treat the entire token after `=` as the
    # value, INCLUDING any surrounding quotes the user typed. So a stored
    # value of `INTELLISTOCK_CRED_KEY="abc..="` shows up here as the
    # literal 47-char string `"abc..="`, which Fernet rejects as malformed
    # base64. Strip leading/trailing whitespace and matched ASCII single
    # or double quotes before constructing the Fernet object so the same
    # key works whether the operator quoted it or not.
    if isinstance(k, str):
        k = k.strip()
        if len(k) >= 2 and k[0] == k[-1] and k[0] in ('"', "'"):
            k = k[1:-1].strip()
    return Fernet(k.encode() if isinstance(k, str) else k)


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Return Fernet-encrypted ciphertext with TAG prefix, or None passthrough."""
    if plaintext is None:
        return None
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _TAG + token


def decrypt(stored: Optional[str]) -> Optional[str]:
    """Return plaintext. Legacy (no TAG) values pass through unchanged for migration."""
    if stored is None:
        return None
    if not isinstance(stored, str):
        return stored
    if not stored.startswith(_TAG):
        return stored  # legacy plaintext - schedule for re-encryption
    try:
        return _fernet().decrypt(stored[len(_TAG):].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise


def decrypt_required(stored: str | None, *, field: str) -> str:
    """Decrypt a required credential without accepting legacy plaintext.

    ``decrypt`` remains deliberately backward-compatible for callers that
    participate in the staged data migration.  Credential-consuming paths use
    this stricter boundary once their table has been migrated.
    """
    if not is_encrypted(stored):
        raise RuntimeError(f"{field}: plaintext secret is forbidden")
    value = decrypt(stored)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field}: decrypted secret is empty")
    return value


def is_encrypted(stored: Optional[str]) -> bool:
    return isinstance(stored, str) and stored.startswith(_TAG)


def generate_key() -> str:
    """Return a fresh Fernet key (base64-encoded). Store this outside the repo."""
    if not _HAS_FERNET:
        raise RuntimeError("cryptography package required")
    return Fernet.generate_key().decode()

"""TOTP, the backup second factor: six digits, 30 s steps, +-1 step, and no step accepted twice.

Secrets are pyotp.random_base32(length=32), 160 bits. Each lives in
users.totp_secret_enc, sealed under the "totp" subkey with aad = the user_id, so
a blob copied onto another user's row does not open. The current step is
T = int(now // 30); a code is accepted for T - 1, T or T + 1, compared in
constant time with pyotp's code for that step.

A step is spent once. verify_and_consume moves users.totp_last_step forward
with a compare-and-swap, UPDATE ... WHERE totp_last_step < step, that must
change exactly one row: the same code twice, an older code after a newer one,
and two workers racing with one code each get one success at most. Enrollment
stores the step its confirming code matched, so that code cannot then sign in.
"""
import hmac
import re
from typing import Optional

import pyotp

from mcp_server import crypto
from mcp_server.authdb import AuthDB

TOTP_DIGITS = 6
TOTP_STEP_S = 30
TOTP_SKEW_STEPS = 1
SECRET_LENGTH = 32
ISSUER_NAME = "IntelliStock MCP"
# ASCII: a str \d also matches other scripts' digits, which hmac.compare_digest refuses with a TypeError.
_CODE = re.compile(r"\d{6}", re.ASCII)


class TotpService:
    """TOTP secrets, codes and step replay protection; see the module docstring for the rules."""

    def __init__(self, db: AuthDB, master_key: bytes, *, clock, issuer_name: str = ISSUER_NAME) -> None:
        self._db = db
        self._key = crypto.derive_key(master_key, "totp")
        self._clock = clock
        self._issuer_name = issuer_name

    def new_secret(self) -> str:
        return pyotp.random_base32(length=SECRET_LENGTH)

    def provisioning_uri(self, secret: str, username: str) -> str:
        """The otpauth:// URI an authenticator app scans; 6 digits, 30 s and SHA-1 are its defaults."""
        return self._totp(secret).provisioning_uri(name=username, issuer_name=self._issuer_name)

    def seal(self, secret: str, aad: str) -> bytes:
        return crypto.seal(self._key, secret, aad)

    def unseal(self, blob: bytes, aad: str) -> str:
        """The secret in blob; raises CryptoError for a wrong aad or key."""
        return crypto.unseal(self._key, blob, aad).decode("utf-8")

    def match_step(self, secret: str, code: str) -> Optional[int]:
        """The step, T - 1 to T + 1, whose code this is; None for no match or anything but six ASCII digits."""
        if not isinstance(code, str) or _CODE.fullmatch(code) is None:
            return None
        totp = self._totp(secret)
        current = int(self._clock() // TOTP_STEP_S)
        # Newest first: on a code shared by two steps, the later one is spent, which shuts out more.
        for step in range(current + TOTP_SKEW_STEPS, current - TOTP_SKEW_STEPS - 1, -1):
            if hmac.compare_digest(totp.generate_otp(step), code):
                return step
        return None

    def verify_and_consume(self, user_id: str, code: str) -> bool:
        """True once per step for the user's code; False for an unknown user, a wrong code or a spent step.

        A secret that does not open (a changed master key, a row copied from another
        user) raises CryptoError: that is a broken store, not a wrong code.
        """
        row = self._db.query_one("SELECT totp_secret_enc, totp_last_step FROM users WHERE user_id = ?", (user_id,))
        if row is None:
            return False
        step = self.match_step(self.unseal(row["totp_secret_enc"], user_id), code)
        if step is None or step <= row["totp_last_step"]:
            return False
        with self._db.tx() as conn:
            # The read above may be stale: this UPDATE is what decides, under the write lock.
            spent = conn.execute("UPDATE users SET totp_last_step = ? WHERE user_id = ? AND totp_last_step < ?",
                                 (step, user_id, step))
            return spent.rowcount == 1

    def _totp(self, secret: str) -> pyotp.TOTP:
        return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_STEP_S)

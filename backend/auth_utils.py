"""
Auth utilities: user storage in Postgres, password hashing, JWT.
Used by api/main.py (auth endpoints + protection) and server.py (table setup).

There is one class of user (operator decision 2026-09-11). Nothing here reads
a role to decide anything, nothing writes one, and no account is provisioned
from the environment: the first account is created through the bootstrap path
on ``POST /auth/users``, which is open exactly while ``count_users`` is zero.

Every function keeps its leading ``conn`` parameter (R26): ~20 call sites and
half a dozen test doubles pass one. It is accepted and ignored -- the store
takes its own pooled connection per operation.
"""

import calendar
import hmac
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from db import schema as db_schema
from db import store

DB_NAME = os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")
USERS_TABLE = "Users"
USERNAME_INDEX = "username"

# Lazy imports for optional deps (bcrypt, jwt)
_bcrypt_available = None
_jwt_available = None


def _check_bcrypt():
    global _bcrypt_available
    if _bcrypt_available is None:
        try:
            import bcrypt
            _bcrypt_available = True
        except ImportError:
            _bcrypt_available = False
    return _bcrypt_available


def _check_jwt():
    global _jwt_available
    if _jwt_available is None:
        try:
            import jwt
            _jwt_available = True
        except ImportError:
            _jwt_available = False
    return _jwt_available


def ensure_users_table(conn=None) -> None:
    """Create the Users table + its username index if they do not exist.

    The index set comes from db/schema.py's registry, where Users already
    declares indexed_fields=("username",) -- so this is R25's one-liner and
    index_wait is gone (CREATE INDEX is synchronous).
    """
    db_schema.ensure_table(USERS_TABLE)


# Bcrypt only uses the first 72 bytes. We always truncate so bcrypt never raises.
BCRYPT_MAX_BYTES = 72

# One rule, enforced twice: the API body rejects a short password with a 422
# before the route runs, and this floor catches every other caller.
MIN_PASSWORD_LENGTH = 8


def _password_bytes(s: str) -> bytes:
    """Encode password to bytes, truncate to 72 bytes for bcrypt."""
    if not s:
        return b""
    b = s.encode("utf-8")
    if len(b) > BCRYPT_MAX_BYTES:
        b = b[:BCRYPT_MAX_BYTES]
    return b


def hash_password(password: str) -> str:
    if not _check_bcrypt():
        raise RuntimeError("bcrypt is required for auth. Install with: pip install bcrypt")
    import bcrypt
    pw_bytes = _password_bytes(password)
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not _check_bcrypt():
        return False
    import bcrypt
    pw_bytes = _password_bytes(plain)
    try:
        hashed_bytes = hashed.encode("utf-8") if isinstance(hashed, str) else hashed
        return bcrypt.checkpw(pw_bytes, hashed_bytes)
    except Exception:
        return False


def user_doc_to_public(doc: Optional[Dict]) -> Optional[Dict]:
    """Return user without password_hash for API responses."""
    if doc is None:
        return None
    out = dict(doc)
    out.pop("password_hash", None)
    # Backfill onboarding flag for legacy user docs created before the field existed.
    out.setdefault("has_completed_onboarding", False)
    return out


def set_onboarding_completed(conn, user_id: str, completed: bool) -> Dict[str, Any]:
    """Set has_completed_onboarding on a user. Returns the updated public doc."""
    doc = get_user_by_id(conn, user_id)
    if doc is None:
        raise ValueError("User not found")
    now = datetime.utcnow().isoformat() + "Z"
    store.update(USERS_TABLE, user_id, {
        "has_completed_onboarding": bool(completed),
        "updated_at": now,
    })
    return user_doc_to_public(get_user_by_id(conn, user_id))


def create_user(
    conn,
    username: str,
    password: str,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new user. Raises ValueError if username exists.

    No role is written. Rows created before the tier was removed still carry
    one; it is reported back like any other field and read by nothing.
    """
    username = username.strip().lower()
    if not username:
        raise ValueError("Username required")
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    ensure_users_table(conn)
    existing = store.get_all(USERS_TABLE, username, index=USERNAME_INDEX)
    if existing:
        raise ValueError("Username already exists")
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    doc = {
        "id": user_id,
        "username": username,
        "password_hash": hash_password(password),
        "token_version": 0,
        "has_completed_onboarding": False,
        "created_at": now,
        "updated_at": now,
    }
    if email is not None:
        doc["email"] = email.strip()
    res = store.insert(USERS_TABLE, doc)
    if res["errors"]:
        raise ValueError(res["first_error"] or "Could not create user")
    return user_doc_to_public(doc)


def get_user_by_username(conn, username: str) -> Optional[Dict]:
    username = username.strip().lower()
    rows = store.get_all(USERS_TABLE, username, index=USERNAME_INDEX)
    return rows[0] if rows else None


def get_user_by_id(conn, user_id: str) -> Optional[Dict]:
    return store.get(USERS_TABLE, user_id)


def list_users(conn) -> List[Dict]:
    ensure_users_table(conn)
    rows = store.run(USERS_TABLE)
    return [user_doc_to_public(row) for row in rows if isinstance(row, dict)]


def count_users(conn) -> int:
    """How many accounts exist.

    Two rules read this and both are about lockout, in opposite directions:
    zero opens the first-run bootstrap on ``POST /auth/users``, and one
    refuses the delete that would empty the table.
    """
    ensure_users_table(conn)
    return int(store.count(USERS_TABLE))


def update_user(
    conn,
    user_id: str,
    password: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Update user. Returns public user dict. Raises ValueError if user not found."""
    doc = get_user_by_id(conn, user_id)
    if doc is None:
        raise ValueError("User not found")
    now = datetime.utcnow().isoformat() + "Z"
    update = {"updated_at": now}
    if password is not None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        update["password_hash"] = hash_password(password)
        # Revoke every token minted against the old password. A password is
        # changed precisely when the operator suspects it leaked; leaving the
        # sessions it authorised alive defeats the point.
        update["token_version"] = token_version_of(doc) + 1
    if email is not None:
        update["email"] = email.strip() if email else None
    store.update(USERS_TABLE, user_id, update)
    updated = get_user_by_id(conn, user_id)
    return user_doc_to_public(updated)


def delete_user(conn, user_id: str) -> None:
    result = store.delete(USERS_TABLE, user_id)
    if result.get("deleted", 0) == 0:
        raise ValueError("User not found")


def verify_secret_auth_key(provided: Optional[str]) -> bool:
    """Return True if provided key matches SECRET_AUTH_KEY from env.

    Uses constant-time comparison so a network attacker can't leak the key
    one character at a time via response-time side channel.
    """
    secret = os.environ.get("SECRET_AUTH_KEY", "").strip()
    if not secret:
        return False
    if provided is None:
        return False
    return hmac.compare_digest(provided.strip(), secret)


# ---------------------------------------------------------------------------
# Login and signup rate limiting
#
# In-process and per-worker by design: it needs no new dependency and no shared
# store, and the thing it defends against -- an unlimited online guessing run
# against POST /auth/login or POST /auth/signup -- is slowed by any one worker
# refusing. A multi-worker deployment therefore allows up to max_attempts PER
# WORKER per window; put a real limiter at the proxy if you need a hard global
# number. State is lost on restart, which is the safe direction: a restart
# never locks a legitimate operator out.
#
# Three buckets: failed logins per (username, client host); failed logins per
# client host across usernames, which the caller can switch off (api.main
# does when the host is a reverse proxy every client shares); and signup
# attempts per client host, every attempt counted.
# ---------------------------------------------------------------------------

LOGIN_RATE_LIMIT_MAX_ATTEMPTS_DEFAULT = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS_DEFAULT = 900   # 15 minutes
LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS_DEFAULT = 30
SIGNUP_RATE_LIMIT_MAX_ATTEMPTS_DEFAULT = 5
SIGNUP_RATE_LIMIT_WINDOW_SECONDS_DEFAULT = 3600  # 1 hour

# Once a bucket holds this many keys, recording a new key first sweeps out
# every key that has aged out of the window, so a spray across usernames or
# spoofed hosts does not keep growing the dict after its attempts expire.
_ATTEMPT_BUCKETS_MAX_KEYS = 10_000


class _AttemptBuckets:
    """Timestamps of recent attempts per key, behind one lock."""

    def __init__(self, max_keys: int = _ATTEMPT_BUCKETS_MAX_KEYS) -> None:
        self._attempts: Dict[Any, List[float]] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def count(self, key: Any, ts: float, window: int) -> int:
        """Attempts for key inside the window; an aged-out key is dropped."""
        with self._lock:
            recent = [t for t in self._attempts.get(key, ()) if t > ts - window]
            if recent:
                self._attempts[key] = recent
            else:
                self._attempts.pop(key, None)
            return len(recent)

    def record(self, key: Any, ts: float, window: int) -> int:
        """Record one attempt for key. Returns the count now in the window."""
        with self._lock:
            if len(self._attempts) >= self._max_keys and key not in self._attempts:
                # Drop every key that has fully aged out before giving up room.
                for k in [k for k, v in self._attempts.items()
                          if not any(t > ts - window for t in v)]:
                    self._attempts.pop(k, None)
            recent = [t for t in self._attempts.get(key, ()) if t > ts - window]
            recent.append(ts)
            self._attempts[key] = recent
            return len(recent)

    def clear(self, key: Any) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._attempts.clear()


_LOGIN_ATTEMPTS = _AttemptBuckets()      # (username, host)
_LOGIN_IP_ATTEMPTS = _AttemptBuckets()   # host, across usernames
_SIGNUP_ATTEMPTS = _AttemptBuckets()     # host


def _env_int(name: str, default: int) -> int:
    try:
        val = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def login_rate_limit_config() -> tuple:
    """(max_attempts, window_seconds), read from the environment each call."""
    return (
        _env_int("LOGIN_RATE_LIMIT_MAX_ATTEMPTS",
                 LOGIN_RATE_LIMIT_MAX_ATTEMPTS_DEFAULT),
        _env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                 LOGIN_RATE_LIMIT_WINDOW_SECONDS_DEFAULT),
    )


def login_ip_rate_limit_max() -> int:
    """Failed logins one client host may make across usernames per window."""
    return _env_int("LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS",
                    LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS_DEFAULT)


def signup_rate_limit_config() -> tuple[int, int]:
    """(max_attempts, window_seconds) for POST /auth/signup per client host."""
    return (
        _env_int("SIGNUP_RATE_LIMIT_MAX_ATTEMPTS",
                 SIGNUP_RATE_LIMIT_MAX_ATTEMPTS_DEFAULT),
        _env_int("SIGNUP_RATE_LIMIT_WINDOW_SECONDS",
                 SIGNUP_RATE_LIMIT_WINDOW_SECONDS_DEFAULT),
    )


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _host_key(client_host: str) -> str:
    return str(client_host or "unknown").strip()


def _login_bucket_key(username: str, client_host: str) -> tuple:
    return (str(username or "").strip().lower(), _host_key(client_host))


def login_is_rate_limited(username: str, client_host: str, now: Optional[float] = None,
                          *, per_ip: bool = True) -> bool:
    """True when this username+host has used up its attempts in the window,
    or, with ``per_ip``, when this host has used up its attempts across
    usernames."""
    max_attempts, window = login_rate_limit_config()
    ts = _now(now)
    if _LOGIN_ATTEMPTS.count(_login_bucket_key(username, client_host), ts, window) >= max_attempts:
        return True
    if not per_ip:
        return False
    return _LOGIN_IP_ATTEMPTS.count(_host_key(client_host), ts, window) >= login_ip_rate_limit_max()


def record_failed_login(username: str, client_host: str, now: Optional[float] = None,
                        *, per_ip: bool = True) -> int:
    """Record one failed attempt, in the host bucket too with ``per_ip``.
    Returns the username+host count now in the window."""
    _max_attempts, window = login_rate_limit_config()
    ts = _now(now)
    if per_ip:
        _LOGIN_IP_ATTEMPTS.record(_host_key(client_host), ts, window)
    return _LOGIN_ATTEMPTS.record(_login_bucket_key(username, client_host), ts, window)


def clear_login_attempts(username: str, client_host: str) -> None:
    """Forget this username+host bucket -- called on a successful login. The
    host's count across usernames stays, so one good password does not
    reset a spray."""
    _LOGIN_ATTEMPTS.clear(_login_bucket_key(username, client_host))


def signup_is_rate_limited(client_host: str, now: Optional[float] = None) -> bool:
    """True when this host has used up its signup attempts in the window."""
    max_attempts, window = signup_rate_limit_config()
    return _SIGNUP_ATTEMPTS.count(_host_key(client_host), _now(now), window) >= max_attempts


def record_signup_attempt(client_host: str, now: Optional[float] = None) -> int:
    """Record one signup attempt, good or bad. Returns the count now in the window."""
    _max_attempts, window = signup_rate_limit_config()
    return _SIGNUP_ATTEMPTS.record(_host_key(client_host), _now(now), window)


def reset_login_rate_limit() -> None:
    """Drop every login and signup bucket (tests, and a deliberate operator unlock)."""
    _LOGIN_ATTEMPTS.clear_all()
    _LOGIN_IP_ATTEMPTS.clear_all()
    _SIGNUP_ATTEMPTS.clear_all()


def token_version_of(user: Optional[Dict]) -> int:
    """The user row's token generation. Absent reads as 0, so rows and tokens
    that predate revocation still match each other."""
    if not user:
        return 0
    try:
        return int(user.get("token_version") or 0)
    except (TypeError, ValueError):
        return 0


def create_access_token(
    user_id: str,
    username: str,
    token_version: int = 0,
) -> str:
    """Mint an access token.

    Lifetime defaults to 24 hours (it was 720 — a stolen token was good for a
    month), still overridable with JWT_EXPIRE_HOURS, and sliding renewal keeps
    an ACTIVE session alive past it. ``token_version`` pins the token to a
    generation of the user row: bumping the row's version on a password change
    kills every token minted before it.
    """
    if not _check_jwt():
        raise RuntimeError("PyJWT is required for auth. Install with: pip install pyjwt")
    import jwt
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is required for auth")
    now = datetime.utcnow()
    hours = _env_int("JWT_EXPIRE_HOURS", 24)
    payload = {
        "sub": user_id,
        "username": username,
        "token_version": int(token_version or 0),
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not _check_jwt():
        return None
    import jwt
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except Exception:
        return None


def token_needs_refresh(payload: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when a token is past the halfway point of its lifetime.

    Requires both ``iat`` and ``exp`` (unix seconds, as PyJWT returns them).
    Tokens minted before sliding renewal shipped have no ``iat`` and never
    refresh — they expire once, then the user gets a fresh sliding token.
    """
    iat = payload.get("iat")
    exp = payload.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        return False
    lifetime = exp - iat
    if lifetime <= 0:
        return False
    now_dt = now or datetime.utcnow()
    # Treat the naive datetime as UTC (matches utcnow + PyJWT's UTC encoding).
    now_ts = calendar.timegm(now_dt.utctimetuple())
    return (exp - now_ts) < lifetime / 2


def renewed_token_if_stale(payload: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """Return a freshly-minted token when ``payload`` is past half-life, else None."""
    if not token_needs_refresh(payload, now):
        return None
    sub = payload.get("sub")
    username = payload.get("username")
    if not sub or not username:
        return None
    try:
        version = int(payload.get("token_version") or 0)
    except (TypeError, ValueError):
        version = 0
    return create_access_token(str(sub), str(username), version)

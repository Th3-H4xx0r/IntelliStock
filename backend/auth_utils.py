"""
Auth utilities: user storage in RethinkDB, password hashing, JWT.
Used by server.py (default admin) and api/main.py (auth endpoints + protection).
"""

import calendar
import hmac
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")
USERS_TABLE = "Users"

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


def get_conn():
    from interactive_utils import get_conn as _get_conn
    return _get_conn()


def ensure_users_table(conn) -> None:
    """Create Users table if it does not exist."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if USERS_TABLE not in tables:
        r.db(DB_NAME).table_create(USERS_TABLE).run(conn)
        # Unique index on username
        r.db(DB_NAME).table(USERS_TABLE).index_create("username").run(conn)
        r.db(DB_NAME).table(USERS_TABLE).index_wait("username").run(conn)


# Bcrypt only uses the first 72 bytes. We always truncate so bcrypt never raises.
BCRYPT_MAX_BYTES = 72


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
    r.db(DB_NAME).table(USERS_TABLE).get(user_id).update({
        "has_completed_onboarding": bool(completed),
        "updated_at": now,
    }).run(conn)
    return user_doc_to_public(get_user_by_id(conn, user_id))


def create_user(
    conn,
    username: str,
    password: str,
    role: str = "user",
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new user. Raises ValueError if username exists."""
    username = username.strip().lower()
    if not username:
        raise ValueError("Username required")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if role not in ("admin", "user"):
        raise ValueError("Role must be admin or user")
    ensure_users_table(conn)
    existing = r.db(DB_NAME).table(USERS_TABLE).get_all(username, index="username").run(conn)
    existing = list(existing)
    if existing:
        raise ValueError("Username already exists")
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    doc = {
        "id": user_id,
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "has_completed_onboarding": False,
        "created_at": now,
        "updated_at": now,
    }
    if email is not None:
        doc["email"] = email.strip()
    r.db(DB_NAME).table(USERS_TABLE).insert(doc).run(conn)
    return user_doc_to_public(doc)


def get_user_by_username(conn, username: str) -> Optional[Dict]:
    username = username.strip().lower()
    cursor = r.db(DB_NAME).table(USERS_TABLE).get_all(username, index="username").run(conn)
    rows = list(cursor)
    return rows[0] if rows else None


def get_user_by_id(conn, user_id: str) -> Optional[Dict]:
    doc = r.db(DB_NAME).table(USERS_TABLE).get(user_id).run(conn)
    return doc


def list_users(conn) -> List[Dict]:
    ensure_users_table(conn)
    cursor = r.db(DB_NAME).table(USERS_TABLE).run(conn)
    rows = list(cursor)
    return [user_doc_to_public(row) for row in rows if isinstance(row, dict)]


def update_user(
    conn,
    user_id: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Update user. Returns public user dict. Raises ValueError if user not found."""
    doc = get_user_by_id(conn, user_id)
    if doc is None:
        raise ValueError("User not found")
    now = datetime.utcnow().isoformat() + "Z"
    update = {"updated_at": now}
    if password is not None:
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters")
        update["password_hash"] = hash_password(password)
    if role is not None:
        if role not in ("admin", "user"):
            raise ValueError("Role must be admin or user")
        update["role"] = role
    if email is not None:
        update["email"] = email.strip() if email else None
    r.db(DB_NAME).table(USERS_TABLE).get(user_id).update(update).run(conn)
    updated = get_user_by_id(conn, user_id)
    return user_doc_to_public(updated)


def delete_user(conn, user_id: str) -> None:
    result = r.db(DB_NAME).table(USERS_TABLE).get(user_id).delete().run(conn)
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


def create_access_token(user_id: str, username: str, role: str) -> str:
    if not _check_jwt():
        raise RuntimeError("PyJWT is required for auth. Install with: pip install pyjwt")
    import jwt
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is required for auth")
    now = datetime.utcnow()
    hours = int(os.environ.get("JWT_EXPIRE_HOURS", "720"))  # default ~30 days
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
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
    role = payload.get("role", "user")
    if not sub or not username:
        return None
    return create_access_token(str(sub), str(username), str(role))


def _strip_env_quotes(s: str) -> str:
    """Strip optional surrounding quotes from env value (load_dotenv can leave them)."""
    if not s:
        return s
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1].strip()
    return s


def ensure_default_admin(conn) -> None:
    """
    Create default admin user from env (DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
    if that user does not exist.

    SECURITY: We deliberately refuse to start with the legacy "changeme"
    fallback when no admin exists yet. The install scripts auto-generate
    a strong DEFAULT_ADMIN_PASSWORD and write it to .env; running without
    one is a deployment mistake we want to catch loudly, not paper over.
    """
    ensure_users_table(conn)
    username = _strip_env_quotes(os.environ.get("DEFAULT_ADMIN_USERNAME", "") or "").strip().lower()
    password = _strip_env_quotes(os.environ.get("DEFAULT_ADMIN_PASSWORD", "") or "")
    if not username:
        username = "admin"
    existing = get_user_by_username(conn, username)
    if existing is not None:
        # Admin already exists — leave them alone. The password env var is
        # only consulted on first-boot provisioning; rotating it requires
        # a real password-change flow, not a silent re-provision.
        return
    if not password or len(password) < 12:
        # Bail loudly. install.sh / install.ps1 generate a 16-char random
        # password and surface it to the operator at the end of the install
        # run; if we got here without one, something is wrong.
        raise RuntimeError(
            "DEFAULT_ADMIN_PASSWORD must be set to a value >= 12 characters before first boot. "
            "Re-run ./install.sh (or install.ps1) to auto-generate one, "
            "or set it manually in .env and restart the backend."
        )
    create_user(conn, username, password, role="admin")

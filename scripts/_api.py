#!/usr/bin/env python3
"""Thin authenticated client for the IntelliStock API.

    python3 scripts/_api.py GET /strategies
    python3 scripts/_api.py POST /backtests '{"instance_id": "...", ...}'

Auth, in order: INTELLISTOCK_API_TOKEN if set, otherwise a login with
INTELLISTOCK_API_USERNAME / INTELLISTOCK_API_PASSWORD from the primary .env.
Those name an ordinary account created in the Users tab -- there is no
environment-provisioned account any more.

The API is the serving-truth read path post-Postgres-cutover; direct RethinkDB
reads are a stale mirror.
"""
import json
import os
import sys
from pathlib import Path

_PRIMARY = Path("/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PRIMARY / "scripts"))
sys.path.insert(0, str(_REPO / "scripts"))

from pull_backtest_logs import _http, _load_dotenv, _login  # noqa: E402

_load_dotenv(_PRIMARY)
_load_dotenv(_REPO)

API = (os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
_TOKEN = None


def _credentials():
    """(username, password), newest key names first.

    ``DEFAULT_ADMIN_*`` is the deprecated pair. It survives here, and only
    here, so that a checkout running against an .env written before the
    Users tab shipped keeps working through the deploy; see
    docs/runbooks/users-and-login.md. Nothing about it is special any more --
    it names an ordinary account.
    """
    username = os.environ.get("INTELLISTOCK_API_USERNAME")
    password = os.environ.get("INTELLISTOCK_API_PASSWORD")
    if username or password:
        return (username or "").strip(), password or ""
    legacy_user = os.environ.get("DEFAULT_ADMIN_USERNAME")
    legacy_pass = os.environ.get("DEFAULT_ADMIN_PASSWORD")
    if legacy_user or legacy_pass:
        print("[_api] DEFAULT_ADMIN_USERNAME/PASSWORD are deprecated — rename them "
              "to INTELLISTOCK_API_USERNAME/INTELLISTOCK_API_PASSWORD in .env.",
              file=sys.stderr)
        return (legacy_user or "").strip(), legacy_pass or ""
    return "", ""


def auth():
    global _TOKEN
    if _TOKEN is None:
        token = os.environ.get("INTELLISTOCK_API_TOKEN")
        if token:
            _TOKEN = token
        else:
            username, password = _credentials()
            if not username or not password:
                raise SystemExit(
                    "No API credentials. Set INTELLISTOCK_API_TOKEN, or "
                    "INTELLISTOCK_API_USERNAME and INTELLISTOCK_API_PASSWORD, "
                    "in .env — they name an account created in the Users tab.")
            _TOKEN = _login(API, username, password)
    return _TOKEN


def call(method: str, path: str, body=None, *, retries: int = 4):
    """One API call. Retries 5xx — a deploy rebuild returns transient 502/503
    and every poll loop in this repo has been bitten by not retrying."""
    import time as _t

    last = None
    for attempt in range(retries):
        try:
            # _http encodes str -> bytes but passes anything else through, so a
            # dict reaches urllib as chunked data and raises. Serialise here.
            headers = {"Authorization": "Bearer " + auth()}
            payload = None
            if body is not None:
                payload = json.dumps(body)
                headers["Content-Type"] = "application/json"
            return _http(method, API + path, headers=headers, body=payload)
        except BaseException as exc:  # noqa: BLE001
            # BaseException, not Exception: `_http` raises SystemExit on every
            # HTTPError, and SystemExit does not derive from Exception. Caught
            # as Exception this clause never fired, the documented 5xx retry
            # was dead code, and a deploy rebuild's transient 502 killed the
            # run on the spot. A non-5xx — and a KeyboardInterrupt — still
            # surfaces immediately, and so does the last attempt.
            last = exc
            msg = str(exc)
            if (not any(c in msg for c in ("500", "502", "503", "504"))
                    or attempt >= retries - 1):
                raise
            _t.sleep(3 * (attempt + 1))
    raise last


def main():
    method = sys.argv[1].upper()
    path = sys.argv[2]
    body = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    out = call(method, path, body)
    text = json.dumps(out, indent=2, default=str)
    limit = int(os.environ.get("API_PRINT_LIMIT", "0") or 0)
    # Truncating by DEFAULT is how a present item reads as absent: a `grep` over
    # a clipped listing returns 0 and looks like "not deployed". Print it all
    # unless the caller asks for a cap.
    print(text if limit <= 0 else text[:limit])


if __name__ == "__main__":
    main()

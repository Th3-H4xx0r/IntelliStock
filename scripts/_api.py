#!/usr/bin/env python3
"""Thin authenticated client for the IntelliStock API.

    python3 scripts/_api.py GET /strategies
    python3 scripts/_api.py POST /backtests '{"instance_id": "...", ...}'

Auth follows scripts/run_paired_experiment.py: INTELLISTOCK_API_TOKEN if set,
otherwise a login with DEFAULT_ADMIN_USERNAME/PASSWORD from the primary .env.
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


def auth():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
            API,
            os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
            os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
        )
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
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc)
            if not any(c in msg for c in ("500", "502", "503", "504")):
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

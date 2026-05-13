#!/usr/bin/env python
"""Pull backtest logs from the IntelliStock API and save them locally.

Usage:
    # Default: latest backtest, save to backtests/<id>_<ts>.log + .json
    python scripts/pull_backtest_logs.py

    # Specific id
    python scripts/pull_backtest_logs.py 832260

    # Override output path
    python scripts/pull_backtest_logs.py 832260 --out my-debug-log.log

    # Just print summary stats (counts of errors / warnings / 401s)
    python scripts/pull_backtest_logs.py 832260 --summary

    # Filter to lines matching a regex
    python scripts/pull_backtest_logs.py 832260 --filter '401|Unauthor'

Env vars (read from .env at repo root or process env):
    INTELLISTOCK_API_URL     base API URL (default API_URL env or
                             http://localhost:<API_PORT>)
    INTELLISTOCK_API_TOKEN   long-lived bearer token (skips login)
    INTELLISTOCK_USERNAME    admin user (default DEFAULT_ADMIN_USERNAME or 'admin')
    INTELLISTOCK_PASSWORD    admin pass (default DEFAULT_ADMIN_PASSWORD)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


def _load_dotenv(repo_root: Path) -> None:
    """Minimal .env loader — sets os.environ from KEY=VAL lines without
    overwriting anything already in the environment."""
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_DEFAULT_UA = "intellistock-pull-backtest-logs/1.0"


def _http(method: str, url: str, *, headers=None, body=None, timeout=60):
    merged = {"User-Agent": _DEFAULT_UA}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, method=method, headers=merged)
    data = body.encode("utf-8") if isinstance(body, str) else body
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type") or ""
            return resp.status, json.loads(raw) if "json" in ct else raw
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} on {method} {url}\n{body_text}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error contacting {url}: {e}") from e


def _login(api_url: str, username: str, password: str) -> str:
    status, body = _http(
        "POST",
        f"{api_url}/auth/login",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"username": username, "password": password}),
    )
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        raise SystemExit(f"Login failed (status={status}): {body!r}")
    return body["access_token"]


def _fetch(api_url: str, token: str, path: str, *, allow_404: bool = False):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"{api_url}{path}", headers={**headers, "User-Agent": _DEFAULT_UA}),
            timeout=60,
        ) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type") or ""
            return resp.status, json.loads(raw) if "json" in ct else raw
    except urllib.error.HTTPError as e:
        if allow_404 and e.code == 404:
            return 404, None
        body_text = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GET {path} failed: {e.code} — {body_text[:500]}") from e


def _resolve_latest_backtest_id(api_url: str, token: str) -> int:
    """List backtests and pick the highest id. The /backtests endpoint
    returns rows from BacktestInstances ordered by recency on the server,
    but the id is a monotonically-increasing int so max(id) is the
    canonical 'most recent' regardless of pagination ordering."""
    status, body = _fetch(api_url, token, "/backtests?page=1&per_page=20")
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"Couldn't list backtests: status={status} body_type={type(body).__name__}")
    rows = body.get("results") or body.get("backtests") or []
    if not rows:
        raise SystemExit("No backtests found on this server.")
    def _key(row: Any) -> int:
        try:
            return int(row.get("id"))
        except (TypeError, ValueError, AttributeError):
            return -1
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=_key, reverse=True)
    return _key(rows[0])


def _summary_stats(logs: list[str]) -> dict:
    rx_401 = re.compile(r"\b401\b|Unauthorized", re.I)
    rx_err = re.compile(r"\bERROR\b|\bexception\b|traceback", re.I)
    rx_warn = re.compile(r"\bWARN(ING)?\b", re.I)
    rx_404 = re.compile(r"\b404\b|NOT_FOUND|model_not_found|is not found for", re.I)
    counts = {"total": len(logs), "401": 0, "404": 0, "error": 0, "warning": 0}
    for line in logs:
        if rx_401.search(line):
            counts["401"] += 1
        if rx_404.search(line):
            counts["404"] += 1
        if rx_err.search(line):
            counts["error"] += 1
        if rx_warn.search(line):
            counts["warning"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backtest_id", nargs="?", type=int, default=None,
                        help="Backtest row ID (omit to auto-fetch the latest).")
    parser.add_argument("--out", help="Write logs to this file. Default: backtests/<id>_<ts>.log")
    parser.add_argument("--filter", help="Print only lines matching this regex")
    parser.add_argument("--summary", action="store_true", help="Print summary stats and exit")
    parser.add_argument("--api-url", help="Override API URL (else env / default)")
    parser.add_argument("--no-meta", action="store_true",
                        help="Skip writing the .json sidecar with status/summary metadata.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print logs to stdout instead of writing a file.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    _load_dotenv(repo_root)

    default_api = (
        os.environ.get("API_URL")
        or f"http://localhost:{os.environ.get('API_PORT', '8011')}"
    )
    api_url = (
        args.api_url
        or os.environ.get("INTELLISTOCK_API_URL")
        or default_api
    ).rstrip("/")

    # Prefer a long-lived bearer token if set; otherwise login with user/pass.
    token = (os.environ.get("INTELLISTOCK_API_TOKEN") or "").strip()
    if not token:
        username = (
            os.environ.get("INTELLISTOCK_USERNAME")
            or os.environ.get("DEFAULT_ADMIN_USERNAME")
            or "admin"
        ).strip()
        password = (
            os.environ.get("INTELLISTOCK_PASSWORD")
            or os.environ.get("DEFAULT_ADMIN_PASSWORD")
            or ""
        ).strip()
        if not password:
            raise SystemExit(
                "No INTELLISTOCK_API_TOKEN, and no INTELLISTOCK_PASSWORD / "
                "DEFAULT_ADMIN_PASSWORD to login with. Set one in .env."
            )
        token = _login(api_url, username, password)

    bt_id = args.backtest_id
    if bt_id is None:
        bt_id = _resolve_latest_backtest_id(api_url, token)
        print(f"[pull_backtest_logs] No id given — picked latest: {bt_id}", file=sys.stderr)

    # Fetch status, summary (best-effort — may 404 if BacktestResults not yet
    # written), and logs.
    status_code, status_body = _fetch(api_url, token, f"/backtests/{bt_id}/status", allow_404=True)
    summary_code, summary_body = _fetch(api_url, token, f"/backtests/{bt_id}/summary", allow_404=True)
    logs_code, logs_payload = _fetch(api_url, token, f"/backtests/{bt_id}/logs")
    if logs_code != 200 or not isinstance(logs_payload, dict):
        raise SystemExit(f"Unexpected /logs response (status={logs_code}, type={type(logs_payload).__name__})")
    logs = logs_payload.get("logs")
    if not isinstance(logs, list):
        raise SystemExit(f"Unexpected logs shape: {type(logs).__name__}")

    if args.summary:
        stats = _summary_stats(logs)
        print(
            f"Backtest #{bt_id} ({logs_payload.get('status')!r}) — "
            f"{stats['total']} lines, {stats['401']}×401, {stats['404']}×404, "
            f"{stats['error']} errors, {stats['warning']} warnings"
        )
        return 0

    if args.filter:
        rx = re.compile(args.filter, re.I)
        lines = [l for l in logs if rx.search(l)]
    else:
        lines = list(logs)

    out_text = "\n".join(lines)

    if args.stdout and not args.out:
        try:
            print(out_text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(out_text.encode("utf-8", errors="replace") + b"\n")
        return 0

    # Default: save to backtests/<id>_<UTC-timestamp>.log
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    default_out = repo_root / "backtests" / f"{bt_id}_{ts}.log"
    out_path = Path(args.out) if args.out else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_text, encoding="utf-8")
    print(
        f"[pull_backtest_logs] Wrote {len(lines)} lines to {out_path} "
        f"(status={logs_payload.get('status')!r})",
        file=sys.stderr,
    )

    if not args.no_meta:
        meta = {
            "backtest_id": bt_id,
            "api_url": api_url,
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "log_source": logs_payload.get("source"),
            "status_endpoint": status_body if status_code == 200 else None,
            "summary_endpoint": summary_body if summary_code == 200 else None,
            "logs_status_field": logs_payload.get("status"),
            "logs_error_field": logs_payload.get("error"),
            "line_count": len(lines),
            "summary_stats": _summary_stats(logs),
        }
        meta_path = out_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        print(f"[pull_backtest_logs] Metadata sidecar: {meta_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

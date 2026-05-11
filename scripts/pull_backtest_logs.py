#!/usr/bin/env python
"""Pull backtest logs by ID via the IntelliStock API.

Usage:
    python scripts/pull_backtest_logs.py <backtest_id> [--out PATH] [--filter REGEX] [--summary]

Examples:
    # Dump full logs to stdout
    python scripts/pull_backtest_logs.py 322286

    # Save to file
    python scripts/pull_backtest_logs.py 322286 --out logs/322286.log

    # Filter only 401 errors
    python scripts/pull_backtest_logs.py 322286 --filter '401|Unauthor'

    # Print just the summary stats
    python scripts/pull_backtest_logs.py 322286 --summary

Env vars (read from .env at repo root or process env):
    INTELLISTOCK_API_URL     base API URL (default http://localhost:8011)
    INTELLISTOCK_USERNAME    admin user (default DEFAULT_ADMIN_USERNAME or "admin")
    INTELLISTOCK_PASSWORD    admin pass (default DEFAULT_ADMIN_PASSWORD)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


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


def _fetch(api_url: str, token: str, path: str):
    status, body = _http(
        "GET",
        f"{api_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if status != 200:
        raise SystemExit(f"GET {path} failed: status={status}")
    return body


def _summary_stats(logs: list[str]) -> dict:
    rx_401 = re.compile(r"\b401\b|Unauthorized", re.I)
    rx_err = re.compile(r"\bERROR\b|\bexception\b|traceback", re.I)
    rx_warn = re.compile(r"\bWARN(ING)?\b", re.I)
    counts = {"total": len(logs), "401": 0, "error": 0, "warning": 0}
    for line in logs:
        if rx_401.search(line):
            counts["401"] += 1
        if rx_err.search(line):
            counts["error"] += 1
        if rx_warn.search(line):
            counts["warning"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backtest_id", type=int, help="Backtest row ID")
    parser.add_argument("--out", help="Write logs to this file instead of stdout")
    parser.add_argument("--filter", help="Print only lines matching this regex")
    parser.add_argument("--summary", action="store_true", help="Print summary stats and exit")
    parser.add_argument("--api-url", help="Override API URL (else env / default)")
    parser.add_argument("--include-summary-doc", action="store_true",
                        help="Also fetch /backtests/{id}/summary and print it")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    _load_dotenv(repo_root)

    # Default to the in-container API URL or localhost; operators on a public
    # deploy should set INTELLISTOCK_API_URL in .env to their own domain.
    default_api = (
        os.environ.get("API_URL")
        or f"http://localhost:{os.environ.get('API_PORT', '8011')}"
    )
    api_url = (
        args.api_url
        or os.environ.get("INTELLISTOCK_API_URL")
        or default_api
    ).rstrip("/")
    username = os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME") or "admin"
    password = os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD") or ""
    if not password:
        raise SystemExit("No password — set INTELLISTOCK_PASSWORD or DEFAULT_ADMIN_PASSWORD in .env")

    token = _login(api_url, username, password)

    if args.include_summary_doc:
        summary = _fetch(api_url, token, f"/backtests/{args.backtest_id}/summary")
        print("=== SUMMARY ===", file=sys.stderr)
        print(json.dumps(summary, indent=2)[:4000])
        print("=== LOGS ===", file=sys.stderr)

    payload = _fetch(api_url, token, f"/backtests/{args.backtest_id}/logs")
    logs = payload.get("logs") if isinstance(payload, dict) else None
    if not isinstance(logs, list):
        raise SystemExit(f"Unexpected log response shape: {type(payload).__name__}")

    if args.summary:
        stats = _summary_stats(logs)
        print(f"Backtest #{args.backtest_id} ({payload.get('status')!r}) — "
              f"{stats['total']} lines, {stats['401']} x 401, "
              f"{stats['error']} errors, {stats['warning']} warnings")
        return 0

    if args.filter:
        rx = re.compile(args.filter, re.I)
        lines = [l for l in logs if rx.search(l)]
    else:
        lines = logs

    out_text = "\n".join(lines)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text, encoding="utf-8")
        print(f"Wrote {len(lines)} lines to {out_path} (status={payload.get('status')!r})", file=sys.stderr)
    else:
        try:
            print(out_text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(out_text.encode("utf-8", errors="replace") + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

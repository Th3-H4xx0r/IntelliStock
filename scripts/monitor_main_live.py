"""Monitor the 'main' live-trading instance: tail logs, detect issues,
and flag when today's first full cycle after lookback has completed.

Usage:
    python scripts/monitor_main_live.py                # one-shot check
    python scripts/monitor_main_live.py --since 0      # replay from line 0
    python scripts/monitor_main_live.py --reset        # forget cursor + re-tail

Behaviour:
- Reads API_URL + auth creds from .env (or env vars).
- Logs into /auth/login, persists JWT to .monitor_state/token.txt.
- Tails /instances/main/live-logs since last cursor (stored in
  .monitor_state/cursor.txt). Prints ALL new lines verbatim.
- Categorises new lines into: ERRORS, WARNINGS, LOOKBACK_PROGRESS,
  LOOKBACK_DONE, CYCLE_COMPLETE_TODAY, RH_FALLBACK_OK, RH_FALLBACK_FAIL.
- Exits with special code 42 once TODAY's first "Run once complete |
  date=<YYYY-MM-DD>" appears AFTER the "Live lookback warmup check"
  marker, meaning the full cycle is done. Outer /loop uses exit-code
  42 to stop re-invoking.

All state lives under .monitor_state/ (gitignored). Log files not
duplicated — we only read the API.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import sys
from typing import Optional
import urllib.request
import urllib.error
import urllib.parse


STATE_DIR = pathlib.Path(".monitor_state")
CURSOR_FILE = STATE_DIR / "cursor.txt"
TOKEN_FILE = STATE_DIR / "token.txt"
INSTANCE_ID = "main"


def _read_env_file(path: str = ".env") -> dict[str, str]:
    """Parse a .env file into a dict. Handles quoted values + # comments."""
    env: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                env[k] = v
    except FileNotFoundError:
        pass
    return env


def _load_config() -> dict[str, str]:
    """Merge .env + os.environ (env wins) and return what we need."""
    env = _read_env_file()
    for k, v in os.environ.items():
        env[k] = v
    # Prefer a LAN/Tailscale origin to bypass Cloudflare's browser-signature
    # WAF (error 1010 on the public domain). Fall back to the public API URL.
    cfg = {
        "api_url": (
            env.get("TAILSCALE_API_URL")
            or env.get("MONITOR_API_URL")
            or env.get("API_URL")
            or env.get("VITE_API_URL")
            or "http://localhost:8011"
        ).rstrip("/"),
        "username": (env.get("DEFAULT_ADMIN_USERNAME") or env.get("MONITOR_USERNAME") or "").strip().lower(),
        "password": env.get("DEFAULT_ADMIN_PASSWORD") or env.get("MONITOR_PASSWORD") or "",
    }
    if not cfg["username"] or not cfg["password"]:
        print("ERROR: need DEFAULT_ADMIN_USERNAME + DEFAULT_ADMIN_PASSWORD "
              "(or MONITOR_USERNAME/MONITOR_PASSWORD) in .env or env.",
              file=sys.stderr)
        sys.exit(2)
    return cfg


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
       "IntelliStockMonitor/1.0")


def _http(method: str, url: str, token: Optional[str] = None, body: Optional[dict] = None, timeout: int = 30):
    # User-Agent: urllib's default "Python-urllib/3.x" is banned by Cloudflare
    # on this deployment (error 1010). Send a normal browser UA. For fully
    # Cloudflare-free access, set TAILSCALE_API_URL in .env pointing at a
    # private-network origin (e.g. http://api-host.internal:8011).
    headers = {
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return -1, str(e)


def _login(cfg: dict[str, str]) -> str:
    status, resp = _http("POST", f"{cfg['api_url']}/auth/login",
                         body={"username": cfg["username"], "password": cfg["password"]})
    if status != 200 or not isinstance(resp, dict) or "access_token" not in resp:
        print(f"ERROR: login failed status={status} resp={resp}", file=sys.stderr)
        sys.exit(3)
    return str(resp["access_token"])


def _ensure_token(cfg: dict[str, str]) -> str:
    STATE_DIR.mkdir(exist_ok=True)
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        # Test it.
        status, _ = _http("GET", f"{cfg['api_url']}/auth/me", token=tok)
        if status == 200:
            return tok
    tok = _login(cfg)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


def _read_cursor() -> int:
    try:
        return int(CURSOR_FILE.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0


def _write_cursor(n: int) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    CURSOR_FILE.write_text(str(n), encoding="utf-8")


# --- Pattern classifiers ---

# NOTE: order matters — WARN_PATTERNS are tested BEFORE ERR_PATTERNS below,
# so expected-but-noisy 401s are demoted rather than miscategorised as errors.
WARN_PATTERNS = [
    # Alpaca IEX free-tier 401s are EXPECTED for restricted symbols and are
    # handled by the RH fallback. Only noteworthy if no RH_OK follows them.
    re.compile(r"Alpaca bars chunk error.*401 Client Error", re.I),
    re.compile(r"Robinhood fallback: EMPTY DataFrame"),
    re.compile(r"Robinhood fallback: get_price_history RAISED"),
    re.compile(r"No Alpaca prices for .* Nexus will use JIT"),
    re.compile(r"Skipping Robinhood fallback"),
    re.compile(r"Live lookback .* FAILED:"),   # per-day failures, loop continues
    re.compile(r"PDT.*rule|day trade"),
    # 2026-04-22 Round 4 Fix 9: surface new broker / strategy guard rails
    # so the monitor's summary view reflects what really happened.
    re.compile(r"SKIP SELL\b"),
    re.compile(r"Backfill queue SKIP\b"),
    re.compile(r"Overlay LLM ticker filter: rejected"),
    re.compile(r"DiscoverStocks read filter: dropped"),
    re.compile(r"Alpaca SUBMIT"),
    re.compile(r"Alpaca ACCEPTED"),
    re.compile(r"Alpaca REJECTED"),
    re.compile(r"Alpaca FILLED"),
    re.compile(r"refresh_orders_today:"),
    re.compile(r"Pre-cycle cash sync"),
    re.compile(r"refresh_positions REST failure"),
    re.compile(r"LiveState snapshot failure"),
    re.compile(r"Portfolio drawdown halt: activated"),
    # 2026-04-23 Round 5: strategy_cache persistence + ramp bypass + trade
    # pagination + orders-today rollover telemetry.
    re.compile(r"strategy_cache restored at boot for"),
    re.compile(r"strategy_cache: no persisted row for"),
    re.compile(r"F1b ramp bypass"),
    re.compile(r"F1b warm-boot positions probe failed"),
    re.compile(r"_seed_trades: paginated"),
    re.compile(r"refresh_orders_today: NY date rollover"),
]
ERR_PATTERNS = [
    re.compile(r"\bTraceback\b"),
    re.compile(r"UnboundLocalError|AttributeError|TypeError|KeyError|NameError|ValueError|ImportError"),
    re.compile(r"RuntimeError(?!\s+from)"),
    re.compile(r"CANNOT IMPORT robinhood_engine"),
    re.compile(r"RH fallback cache write failed"),
    # Hard-fail broker markers.
    re.compile(r"Broker .* crashed|Broker shutdown due to|broker loop exited"),
    # 2026-04-22 Round 4 Fix 9: Discord outage + strategy-loop crash are
    # on-call pages on their own.
    re.compile(r"Discord live-alerts DISABLED"),
    re.compile(r"run_run_once_strategies crashed"),
    # 2026-04-23 Round 5: persistence layer hard-failures worth paging on.
    re.compile(r"strategy_cache load failed"),
    re.compile(r"F1/F1b boot sequence failed"),
]
LOOKBACK_PROG = re.compile(r"Live lookback progress (\d+)/(\d+)")
LOOKBACK_DONE = re.compile(r"Live lookback warmup check: (\d+) outcomes")
RUN_ONCE_COMPLETE = re.compile(r"Run once complete \| date=(\d{4}-\d{2}-\d{2})")
RH_OK = re.compile(r"Robinhood fallback: fetched (\d+) bars for (\S+)")
RH_CACHED = re.compile(r"Robinhood fallback: CACHED (\d+) bars for (\S+)")


def _classify(line: str) -> str:
    # Check WARN before ERROR so expected 401/PDT/lookback-day-fail lines
    # aren't miscategorised as hard errors.
    if any(p.search(line) for p in WARN_PATTERNS):
        return "WARN"
    if any(p.search(line) for p in ERR_PATTERNS):
        return "ERROR"
    if LOOKBACK_DONE.search(line):
        return "LOOKBACK_DONE"
    if LOOKBACK_PROG.search(line):
        return "LOOKBACK_PROGRESS"
    if RUN_ONCE_COMPLETE.search(line):
        return "RUN_ONCE_COMPLETE"
    if RH_OK.search(line) or RH_CACHED.search(line):
        return "RH_OK"
    return "INFO"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=None,
                    help="Override cursor (start from this line; 0 = full replay).")
    ap.add_argument("--reset", action="store_true", help="Reset cursor to 0 + invalidate token.")
    ap.add_argument("--max-print", type=int, default=1200,
                    help="Cap verbatim lines per invocation (default 1200).")
    ap.add_argument("--quiet-info", action="store_true",
                    help="Suppress INFO-level lines in verbatim echo (still counted).")
    args = ap.parse_args(argv)

    if args.reset:
        for p in (CURSOR_FILE, TOKEN_FILE):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        print("Reset done.")

    cfg = _load_config()
    tok = _ensure_token(cfg)

    since = args.since if args.since is not None else _read_cursor()
    url = f"{cfg['api_url']}/instances/{INSTANCE_ID}/live-logs?since_line={since}"
    status, resp = _http("GET", url, token=tok)
    if status == 401:
        # Token expired — relogin + retry.
        try:
            TOKEN_FILE.unlink()
        except FileNotFoundError:
            pass
        tok = _ensure_token(cfg)
        status, resp = _http("GET", url, token=tok)
    if status != 200 or not isinstance(resp, dict):
        print(f"ERROR: live-logs fetch status={status} resp={resp}", file=sys.stderr)
        return 4

    # The live-logs endpoint returns `logs` (list[str]), `next_line` (int),
    # `total_lines` (int), `final_status` ("running"|"halted"), `truncated` (bool).
    lines = resp.get("logs") or resp.get("lines") or []
    next_line = int(resp.get("next_line") or (since + len(lines)))
    total_lines = int(resp.get("total_lines") or 0)
    truncated = bool(resp.get("truncated"))
    instance_status = resp.get("final_status") or resp.get("status") or "?"

    # Classify + tally.
    counts: dict[str, int] = {}
    errors: list[str] = []
    warns: list[str] = []
    lookback_progress: Optional[tuple[int, int]] = None
    lookback_done = False
    run_once_dates: list[str] = []
    rh_ok_lines: list[str] = []

    # Also fetch live-state for status / lookback dict.
    _s2, resp2 = _http("GET", f"{cfg['api_url']}/instances/{INSTANCE_ID}/live-state", token=tok)
    lb_snap = None
    state_status = None
    if isinstance(resp2, dict):
        lb_snap = resp2.get("lookback")
        state_status = resp2.get("status")

    print(f"=== monitor_main_live @ {_dt.datetime.now().isoformat(timespec='seconds')} ===")
    print(f"final_status={instance_status} live_state_status={state_status} "
          f"since_line={since} new_lines={len(lines)} next_line={next_line} "
          f"total_lines={total_lines} truncated={truncated}")
    if lb_snap:
        print(f"lookback: {lb_snap.get('current')}/{lb_snap.get('total')} ({lb_snap.get('current_date')}) spec={lb_snap.get('spec_name')}")
    else:
        print("lookback: <none>")

    printed = 0
    for item in lines:
        # live-logs sends list[str]; tolerate dict wrapping for forward-compat.
        if isinstance(item, dict):
            text = item.get("text") or item.get("line") or ""
        else:
            text = str(item)
        if not text:
            continue
        cat = _classify(text)
        counts[cat] = counts.get(cat, 0) + 1
        if cat == "ERROR":
            errors.append(text.rstrip())
        elif cat == "WARN":
            warns.append(text.rstrip())
        elif cat == "LOOKBACK_PROGRESS":
            m = LOOKBACK_PROG.search(text)
            if m:
                lookback_progress = (int(m.group(1)), int(m.group(2)))
        elif cat == "LOOKBACK_DONE":
            lookback_done = True
        elif cat == "RUN_ONCE_COMPLETE":
            m = RUN_ONCE_COMPLETE.search(text)
            if m:
                run_once_dates.append(m.group(1))
        elif cat == "RH_OK":
            rh_ok_lines.append(text.rstrip())
        if printed < args.max_print and (cat != "INFO" or not args.quiet_info):
            print(text.rstrip())
            printed += 1

    print(f"--- summary | counts={counts} ---")
    if lookback_progress:
        print(f"last lookback progress in batch: {lookback_progress[0]}/{lookback_progress[1]}")
    if lookback_done:
        print("LOOKBACK_DONE observed in this batch.")
    if rh_ok_lines:
        print(f"RH fallback succeeded {len(rh_ok_lines)} time(s) in this batch.")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors[:10]:
            print(f"  ! {e}")
    if warns:
        print(f"WARNINGS ({len(warns)}):")
        for w in warns[:5]:
            print(f"  ? {w}")

    # Determine if "today's full cycle" is done. We consider it done if:
    #   - lookback_done has been observed in history (via live-state has lb_snap=None AND
    #     at least one RUN_ONCE_COMPLETE for today's date)
    today_str = _dt.date.today().isoformat()
    cycle_done_today = today_str in run_once_dates

    # Persist cursor.
    _write_cursor(next_line)

    # Emit machine-readable tail for orchestrator loop.
    result = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "instance_status": state_status,
        "cursor_before": since,
        "cursor_after": next_line,
        "counts": counts,
        "lookback_done": lookback_done,
        "lookback_snapshot": lb_snap,
        "errors_head": errors[:3],
        "warns_head": warns[:3],
        "run_once_dates_new": run_once_dates,
        "rh_fallback_hits": len(rh_ok_lines),
        "today": today_str,
        "cycle_done_today": cycle_done_today,
    }
    print("RESULT_JSON " + json.dumps(result))

    if cycle_done_today and lb_snap is None:
        print("EXIT: full cycle for today complete — monitor should stop.")
        return 42
    return 0


if __name__ == "__main__":
    sys.exit(main())

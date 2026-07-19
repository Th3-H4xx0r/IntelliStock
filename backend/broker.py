#  Round-7 deploy trigger: 2026-04-23 (feed selector + no-silent-discard fallback)
import sys
import os
import socket as _socket_default
# 2026-05-05 live-hang investigation: cap socket.getaddrinfo() at process
# level. Python's socket.timeout / requests.timeout / urllib3.timeout do
# NOT bound name resolution — `getaddrinfo` is a C-level blocking call
# that can hang for 5-15 minutes when Docker NAT DNS flakes. This is the
# ONLY mechanism in CPython that bounds it. 45s is comfortably above all
# our per-request timeouts (15-30s) so legitimate slow-but-working calls
# still complete; anything beyond is a hang.
_socket_default.setdefaulttimeout(45)
import time
import threading
import argparse
import time
import datetime
import hashlib
import json
import re
import dotenv
from contextlib import contextmanager
from typing import Any, Optional
from portfolio_emulator import PortfolioEmulator
from llm_utils import llm_model_reference, normalize_reasoning_effort
from model_resolver import resolve_model_refs_in_config
from nexus_broker_utils import build_nexus_buy_guard, buy_ceiling, get_nexus_buy_block_details, get_nexus_buy_block_reason, max_positions_gate, max_positions_projected_count, resolve_max_positions_cap, max_positions_arm_warning
from robinhood_data_policy import robinhood_data_fallback_allowed
from persistence_safety import SecretMaterialError, assert_secret_free, sanitize_snapshot

try:
    from llm_telemetry import llm_call_context as telemetry_llm_call_context
except Exception:
    @contextmanager
    def telemetry_llm_call_context(**_kwargs):
        yield

# Live-mode modules (imported lazily inside functions where sensible to keep
# backtest cold-start light, but we expose the names at module scope for clarity).
try:
    from live_mode_overrides import apply_live_overrides as _apply_live_overrides
except Exception:
    def _apply_live_overrides(cfg):
        return dict(cfg or {})

# Prevent double-execution: when broker.py runs as __main__, register it as the 'broker'
# module too.  Otherwise `from broker import ...` inside strategies re-executes all
# module-level code (backtest init, DB writes, bar fetching) a second time.
if __name__ == '__main__' and 'broker' not in sys.modules:
    sys.modules['broker'] = sys.modules['__main__']

### GLOBAL VARIABLES
MODE_LIVE = "live"
MODE_BACKTEST = "backtest"

# Live-readiness Q1 (2026-04-29): hoist RethinkDB import for the kill-switch
# tick poll so we don't pay an import cost every tick. None when the package
# isn't available (e.g. unit tests with stubbed env).
try:
    from rethinkdb import RethinkDB as _KS_RDB  # type: ignore
except Exception:
    _KS_RDB = None  # type: ignore

# Live trading state persistence + command queue + per-instance log file.
# See backend/live_state.py for the full contract. Globals below are populated
# at boot by `_init_live_trading_state(instance_id, adapter)` and torn down at
# shutdown by `_shutdown_live_trading_state(reason)`. The snapshot thread
# re-queries the adapter every ~3s and upserts a LiveState row for the UI to
# poll. The command thread watches LiveCommands via changefeed and executes
# halt / close_position / submit_order on the adapter.
_live_trading_log_file = None
_live_trading_log_path = None
_live_trading_snapshot_thread = None
_live_trading_command_thread = None
_live_trading_stop_event = threading.Event()
_live_trading_started_at = None
# 2026-05-03 live-hang investigation: per-call ThreadPoolExecutor with `with`
# blocks indefinitely on __exit__ (shutdown(wait=True)) when the worker thread
# is wedged on a TCP read. Use module-level long-lived pools with bounded
# max_workers so the leak is bounded — at most max_workers threads can be
# zombie-stuck at once. Caller submits + result(timeout=N), and on TimeoutError
# we just walk away — the zombie thread finishes whenever the OS unblocks it.
import concurrent.futures as _live_cf
_PRICE_FETCH_EXECUTOR = _live_cf.ThreadPoolExecutor(
    # 2026-05-06: bumped from 4 → 12. Executor.submit() never blocks
    # (unbounded queue) but result(timeout=N) only times the running
    # phase, NOT the queue wait. With 4 workers and zombie threads from
    # wedged HTTP (60-180s OS TCP timeout), pool can saturate and new
    # submits queue indefinitely. 12 workers + bounded zombie tolerance
    # gives strategy/snapshot/diagnostic enough headroom even under
    # worst-case 4-zombie wedge.
    max_workers=12, thread_name_prefix="price-fetch-watchdog"
)
_SCP_PERSIST_EXECUTOR = _live_cf.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="scp-persist-watchdog"
)
# 2026-05-05 live-hang investigation: snapshot worker hung 71s after full
# cycle completion (LiveState frozen 5+h). Robinhood adapter calls in
# _compute_live_state_snapshot share a requests.Session that 11cec87 did
# not patch, and r.connect() had no timeout — both wedge silently. Wrap
# the whole tick (compute + upsert) in a watchdog with bounded zombie
# leak (max_workers=2).
_SNAPSHOT_EXECUTOR = _live_cf.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="snapshot-watchdog"
)

# 2026-05-07 scheduler refactor (commit 4): broker now drives the strategy
# schedule via backend.scheduler.get_next_wake, replacing the dual-cadence
# gate that lived in graph_nexus_analysis.run_once.
try:
    from scheduler import get_next_wake as _scheduler_get_next_wake
except Exception as _sched_exc:
    _scheduler_get_next_wake = None  # type: ignore

# Strategy-tick monotonic counter (live mode only).
_strategy_tick_n = 0
# Consecutive SKIP count for 3-strike `os._exit(1)` escalation. Reset on a
# successful tick; incremented when the back-to-back zombie guard fires.
_strategy_consecutive_skips = 0
# Watchdog timeout per mode. FULL keeps the existing 30-min budget; MONITOR
# is a 2-min cap (price refresh + risk evaluation, no LLM/discovery); IDLE
# never invokes run_once.
_WATCHDOG_FULL_SEC = 1800
_WATCHDOG_MONITOR_SEC = 120

# 2026-05-07 strategy-tick diagnostic state. Lives in its own module so
# tests can import it without dragging in broker.py's argparse + DB
# bootstrap. broker.py just wraps the helpers for backwards compat with
# existing call sites in this file.
import strategy_tick_state as _strategy_tick_state_mod
_strategy_tick_state = _strategy_tick_state_mod.STATE
_set_strategy_tick_phase = _strategy_tick_state_mod.set_phase


def _bounded_eppi_call(portfolio_emulator, prices, current_time, *, data, symbols, key, secret, label, timeout=30.0):
    """Wrap _ensure_prices_include_positions in the proven submit+result(timeout)
    pattern used at the pre-tick site. Returns the new prices dict on success,
    or the input prices unchanged on TimeoutError (with a yellow warning log).
    """
    try:
        _fut = _PRICE_FETCH_EXECUTOR.submit(
            _ensure_prices_include_positions,
            portfolio_emulator, prices, current_time,
            data=data, symbols=symbols, key=key, secret=secret,
        )
        return _fut.result(timeout=timeout)
    except _live_cf.TimeoutError:
        try:
            _log(f"{label}: EPPI exceeded {timeout:.0f}s; using cached prices for this tick", "yellow")
        except Exception:
            pass
        return prices
    except Exception as _eppi_exc:
        try:
            _log(f"{label}: EPPI failed ({type(_eppi_exc).__name__}: {_eppi_exc}); using cached prices", "yellow")
        except Exception:
            pass
        return prices


def _send_monitor_discord_notification(_inst_id: str, _date_key: str, _now_pt_str: str, _meta: dict | None):
    """Fire-and-forget Discord ping for every MONITOR cycle (per user request,
    2026-05-07). Uses live_alerts._safe_enqueue via a daemon thread so a slow
    Discord webhook can't extend the strategy tick.

    `_meta` is the monitor cycle's return dict — we extract held/sells/holds
    counts when available.
    """
    def _send():
        try:
            from live_alerts import _safe_enqueue
        except Exception as _imp_exc:
            try:
                _log(f"Discord monitor-ping: live_alerts import failed: {type(_imp_exc).__name__}: {_imp_exc}", "yellow")
            except Exception:
                pass
            return
        held = sells = holds = 0
        try:
            if isinstance(_meta, dict):
                # Held = number of position symbols the monitor scored.
                held = sum(1 for k, v in _meta.items()
                           if not (isinstance(k, str) and k.startswith("_")))
                sells = len(_meta.get("_nexus_sell_enforcement", []) or [])
                holds = max(0, held - sells)
        except Exception:
            pass
        content = (
            f"[{_inst_id}] Monitor cycle | {_date_key} {_now_pt_str} | "
            f"held={held} | sells={sells} | holds={holds}"
        )
        try:
            _safe_enqueue("notifications", content, embed=None)
            try:
                _log(f"Discord monitor-ping enqueued: {content}", "cyan")
            except Exception:
                pass
        except Exception as _enq_exc:
            try:
                _log(f"Discord monitor-ping enqueue FAILED: {type(_enq_exc).__name__}: {_enq_exc}", "yellow")
            except Exception:
                pass
    try:
        threading.Thread(target=_send, daemon=True).start()
    except Exception as _th_exc:
        try:
            _log(f"Discord monitor-ping thread spawn FAILED: {type(_th_exc).__name__}: {_th_exc}", "yellow")
        except Exception:
            pass
# 2026-05-05 live-hang continuation: even with snapshot-level watchdog AND
# RH client timeout=20s, urllib3's SSL recv() doesn't reliably propagate
# socket.timeout when TLS is stuck or a NAT path drops mid-stream. The
# 20s requests timeout and 20s watchdog then race at the same threshold;
# when watchdog wins, the call wedges 60-180s waiting for OS TCP to fire.
# Fix: hard-bound each RH adapter call (refresh_account, refresh_positions)
# with a 12s executor timeout — well inside the watchdog — and on timeout
# rebuild the RH requests.Session so the wedged urllib3 conn pool is
# flushed before the next snapshot tick.
_snap_session_reset_pending: bool = False
# 2026-05-05 third pass: snapshot is now strictly cache-only —
# refresh_account/refresh_positions(force=False) return cache without HTTP.
# So the snapshot's _bounded_adapter_call should never wedge anymore.
# The "consecutive timeout backoff" + "session reset on snapshot timeout"
# logic is dead code in the new design (only the hourly pre-cycle hook
# can hit RH HTTP, and IT has its own 5-retry + give-up path).
# We keep the network diagnostic runner — it's still useful when the
# pre-cycle hook fires it on its own retry path. Diagnostic is rate-limited
# to one run per 60s.
_snap_last_diagnostic_epoch: float = 0.0
_RH_DIAGNOSTIC_INTERVAL_SEC: float = 60.0


def _diagnose_rh_network() -> None:
    """One-shot network probe to localize a wedge in RH calls. Each step is
    submitted to _PRICE_FETCH_EXECUTOR and bounded so the diagnostic itself
    can't wedge the snapshot. Logs:

      - DNS time + IP   (or "DNS HANG/FAIL" → resolver issue)
      - TCP+TLS time    (or "TCP+TLS HANG/FAIL" → network/firewall issue)

    If both probes succeed but the actual API call still wedges, the issue is
    in the HTTP layer (rate-limit, RH backend, TLS fingerprint reset).
    """
    global _snap_last_diagnostic_epoch
    _snap_last_diagnostic_epoch = time.time()
    import socket as _sock
    import ssl as _ssl
    host = "api.robinhood.com"
    port = 443

    def _do_dns():
        return _sock.gethostbyname(host)

    fut = _PRICE_FETCH_EXECUTOR.submit(_do_dns)
    ip = None
    try:
        _t0 = time.time()
        ip = fut.result(timeout=5.0)
        _ms = (time.time() - _t0) * 1000
        try:
            _log(f"rh-diag: DNS {host}={ip} in {_ms:.0f}ms", "yellow")
        except Exception:
            pass
    except _live_cf.TimeoutError:
        try:
            _log("rh-diag: DNS HANG (>5s) — resolver issue. "
                 "Container DNS may be misconfigured; check /etc/resolv.conf "
                 "or move to a static resolver (1.1.1.1).", "red")
        except Exception:
            pass
        return
    except Exception as e:
        try:
            _log(f"rh-diag: DNS FAIL: {type(e).__name__}: {e}", "red")
        except Exception:
            pass
        return

    def _do_tcp_tls():
        s = _sock.create_connection((ip, port), timeout=5.0)
        try:
            ctx = _ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname=host, do_handshake_on_connect=False)
            ss.settimeout(5.0)
            ss.do_handshake()
            ss.close()
            return True
        finally:
            try:
                s.close()
            except Exception:
                pass

    fut = _PRICE_FETCH_EXECUTOR.submit(_do_tcp_tls)
    try:
        _t0 = time.time()
        fut.result(timeout=10.0)
        _ms = (time.time() - _t0) * 1000
        try:
            _log(
                f"rh-diag: TCP+TLS to {ip}:{port} in {_ms:.0f}ms — "
                f"network OK; wedge is in HTTP layer (likely RH rate-limit / "
                f"fingerprint reset / backend slowness).",
                "yellow",
            )
        except Exception:
            pass
    except _live_cf.TimeoutError:
        try:
            _log(
                f"rh-diag: TCP+TLS HANG (>10s) to {ip}:{port} — "
                f"network/firewall issue. Container egress may be blocked or "
                f"the path is dropping packets.",
                "red",
            )
        except Exception:
            pass
    except Exception as e:
        try:
            _log(f"rh-diag: TCP+TLS FAIL to {ip}:{port}: {type(e).__name__}: {e}", "red")
        except Exception:
            pass


def _bounded_adapter_call(name: str, fn, timeout: float = 12.0):
    """Run an adapter call with a hard timeout. After 2026-05-05 third pass,
    snapshot's refresh_account/refresh_positions are cache-only — they
    return immediately or raise BrokerError on cold cache. So this bound
    really only catches Python-level pathology (GIL contention, executor
    saturation, etc.). Kept as defense-in-depth — the snapshot watchdog
    still depends on each adapter call returning under the per-stage budget.
    """
    fut = _PRICE_FETCH_EXECUTOR.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except _live_cf.TimeoutError:
        try:
            _log(
                f"adapter.{name}() hard-timeout (>{timeout:.0f}s) — "
                f"unexpected since snapshot is cache-only. "
                f"Possible executor saturation or GIL stall.",
                "yellow",
            )
        except Exception:
            pass
        global _snap_last_diagnostic_epoch
        _now = time.time()
        if (_now - _snap_last_diagnostic_epoch > _RH_DIAGNOSTIC_INTERVAL_SEC
                and robinhood_data_fallback_allowed(live_broker_type)):
            try:
                _diagnose_rh_network()
            except Exception:
                pass
        raise
# Detach all watchdog executors at process exit so zombie threads on wedged
# TCP reads can't block clean shutdown for the OS-level FIN_WAIT timeout
# (60-180s on Linux without keepalive tuning). wait=False fire-and-forgets.
import atexit as _live_atexit
def _live_shutdown_executors():
    for _ex in (_PRICE_FETCH_EXECUTOR, _SCP_PERSIST_EXECUTOR, _SNAPSHOT_EXECUTOR):
        try:
            _ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
_live_atexit.register(_live_shutdown_executors)
# Live lookback progress — populated by _run_live_historic_lookback and read
# by the snapshot worker so the UI can show a "Historic lookback N/M" banner
# while the 120-day warmup runs.
_live_lookback_progress: dict | None = None

# Loop-log dedupe: suppress identical "Running" / "Outside session"
# lines when they repeat tick after tick. We still emit one line on state
# transitions and a coarse heartbeat every LOOP_LOG_HEARTBEAT_SEC while
# the state is unchanged, so operators can tell the loop is alive.
LOOP_LOG_HEARTBEAT_SEC = int(os.environ.get("BROKER_LOOP_LOG_HEARTBEAT_SEC", "300"))
_loop_log_last_running_key = None
_loop_log_last_outside = None
_loop_log_last_market_closed = None
_loop_log_last_heartbeat_at = 0.0
_backtest_alpaca_timeframe = None  # Set during backtest setup (e.g., "1Day", "1Hour").
_backtest_fetch_start_dt = None
_backtest_fetch_end_dt = None
_backtest_no_history_symbols = set()

dotenv.load_dotenv()

def _null_or_value(s):
    """Return None if s is None or uppercase 'NULL', else return s."""
    if s is None or (isinstance(s, str) and s.upper() == "NULL"):
        return None
    return s


class _SafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that scrubs potentially-secret positional args from error echoes.

    Historical call form included key/secret as positionals 6 and 7. Argparse errors
    echoed the full argv, leaking secrets into logs. We scrub them before echoing.
    """

    _POTENTIAL_SECRET_POSITIONS = (6, 7)

    def error(self, message):
        scrubbed_argv = list(sys.argv)
        for i in self._POTENTIAL_SECRET_POSITIONS:
            if 0 < i < len(scrubbed_argv):
                scrubbed_argv[i] = "***REDACTED***"
        # Write scrubbed error without the argv echo.
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def parse_args():
    """Parse broker CLI arguments. Secrets are read from DB, not argv."""
    parser = _SafeArgumentParser(
        prog="broker.py",
        description="IntelliStock broker: run in live or backtest mode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Live (from instance.py):
    broker.py my-instance live NULL NULL NULL AAPL MSFT
    (key/secret are read from the Instances RethinkDB row; NOT passed as argv)
  Backtest (legacy - key/secret may still be passed for data fetch):
    broker.py my-instance backtest 2020-01-01 2021-12-31 1d [KEY SECRET] AAPL
        """.strip(),
    )
    parser.add_argument("instance_id", help="Instance identifier")
    parser.add_argument(
        "mode",
        choices=[MODE_LIVE, MODE_BACKTEST],
        help="'live' or 'backtest'",
    )
    parser.add_argument(
        "start_date",
        help="Start date (e.g. 2020-01-01). Use NULL for live mode.",
    )
    parser.add_argument(
        "end_date",
        help="End date (e.g. 2021-12-31). Use NULL for live mode.",
    )
    parser.add_argument(
        "time_increment",
        help="Time increment (e.g. 1d, 1h). Use NULL for live mode.",
    )
    # key/secret are OPTIONAL positionals. In live mode, broker.py reads them
    # from the Instances table (decrypted via secret_store). In backtest mode,
    # they may still be passed for historic-data fetch; if omitted, we fall back
    # to env vars. Legacy call sites (scripts/_v32_*) still pass them.
    parser.add_argument("key", nargs="?", default=None, help="(optional) broker API key; live reads from DB")
    parser.add_argument("secret", nargs="?", default=None, help="(optional) broker API secret; live reads from DB")
    parser.add_argument("symbols", nargs="*", help="Stock symbols to run (e.g. AAPL MSFT)")
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100000.0,
        metavar="AMOUNT",
        help="Initial cash for backtest portfolio (default: 100000). Ignored in live mode.",
    )
    parser.add_argument(
        "--backtest-id",
        default=None,
        metavar="ID",
        help="Backtest row ID (backtest mode only; supplied by backtest engine).",
    )
    parser.add_argument(
        "--taker-fee",
        type=float,
        default=None,
        metavar="RATE",
        help="Crypto taker fee to EMULATE in a backtest (fraction, e.g. 0.0002). "
             "Overrides the instance venue's fee. Backtest mode only.",
    )
    args = parser.parse_args()

    start_date = _null_or_value(args.start_date)
    end_date = _null_or_value(args.end_date)
    time_increment = _null_or_value(args.time_increment)
    if args.mode == MODE_LIVE:
        start_date = end_date = None
        # time_increment from instance (e.g. 60, 1m); keep parsed value, use default later if None
    elif args.mode == MODE_BACKTEST:
        if start_date is None or end_date is None:
            parser.error("backtest mode requires non-NULL start_date and end_date")
    key_val = _null_or_value(args.key)
    secret_val = _null_or_value(args.secret)
    return argparse.Namespace(
        instance_id=args.instance_id,
        mode=args.mode,
        start_date=start_date,
        end_date=end_date,
        time_increment=time_increment,
        key=key_val,
        secret=secret_val,
        symbols=args.symbols,
        initial_cash=args.initial_cash,
        backtest_row_id=args.backtest_id,
        emulated_taker_fee=args.taker_fee,
    )


def _load_robinhood_extras_from_db(brokerage_id):
    """Phase C (2026-04-29) — fetch RH-specific fields the factory needs to
    build a working RobinhoodAdapter.

    2026-04-30 — extended to also return ``obtained_at_epoch``, ``expires_in``
    and ``account_url`` so the in-process token-refresh helper can compute
    a real TTL gate. Without these the adapter's _maybe_refresh_token was a
    no-op for the entire session (CRITICAL agent finding #1).

    Returns a dict with keys:
      ``account_number``  (str | None)  — RH sub-account to trade from
      ``device_token``    (str | None)  — RH per-device fingerprint
      ``obtained_at_epoch`` (int | None) — when current access_token was minted
      ``expires_in``      (int | None)  — TTL of access_token in seconds
      ``account_url``     (str | None)  — RH /accounts/<num>/ resource URL
    All keys present even on failure (values may be None).
    Called only when broker_type == 'robinhood'.
    """
    out = {
        "account_number": None,
        "device_token": None,
        "obtained_at_epoch": None,
        "expires_in": None,
        "account_url": None,
    }
    if not brokerage_id:
        return out
    try:
        from rethinkdb import RethinkDB
        from secret_store import decrypt
        _r = RethinkDB()
        host = os.environ.get("RETHINKDB_HOST", "localhost")
        port = int(os.environ.get("RETHINKDB_PORT", "28015"))
        conn = _r.connect(host=host, port=port, timeout=10)
        try:
            b = _r.db("IntelliStock").table("BrokerageAccounts").get(brokerage_id).run(conn)
            if not b:
                return out
            out["account_number"] = (b.get("robinhood_account_number") or "").strip() or None
            try:
                out["device_token"] = decrypt(b.get("robinhood_device_token")) or None
            except Exception:
                out["device_token"] = b.get("robinhood_device_token") or None
            try:
                _oa = b.get("robinhood_obtained_at_epoch")
                out["obtained_at_epoch"] = int(_oa) if _oa is not None else None
            except Exception:
                out["obtained_at_epoch"] = None
            try:
                _ei = b.get("robinhood_expires_in")
                out["expires_in"] = int(_ei) if _ei is not None else None
            except Exception:
                out["expires_in"] = None
            out["account_url"] = (b.get("robinhood_account_url") or "").strip() or None
            return out
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        return out


def _load_live_credentials_from_db(instance_id):
    """In live mode, read key/secret from Instances row and decrypt.

    Returns (key, secret, broker_type, paper, brokerage_id).

    Fail-loud contract: if the linked brokerage row has Fernet-encrypted creds
    and decrypt raises (INTELLISTOCK_CRED_KEY missing on this host), we log a
    RED error and return (None, None, ...). We do NOT silently fall back to
    legacy instance-level plaintext keys because those may be paper/live-
    mismatched with the linked brokerage and would cause confusing 401s.
    """
    # Local logger shim: this function runs at module-init time, BEFORE the
    # module-level _log() is defined (it lives further down). Use the
    # intellistock_logger singleton directly.
    def _early_log(msg, color="white"):
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(msg, color, service="BROKER")
        except Exception:
            print(f"[BROKER] {msg}")

    try:
        from rethinkdb import RethinkDB
        from secret_store import decrypt
        _r = RethinkDB()
        host = os.environ.get("RETHINKDB_HOST", "localhost")
        port = int(os.environ.get("RETHINKDB_PORT", "28015"))
        conn = _r.connect(host=host, port=port, timeout=10)
        try:
            inst_doc = _r.db("IntelliStock").table("Instances").get(str(instance_id)).run(conn)
            if not inst_doc:
                return None, None, "alpaca", True, None
            brokerage_id = inst_doc.get("brokerage_id") or None
            broker_type = (inst_doc.get("broker_type") or "alpaca").strip().lower()
            # Live-readiness P0 #2: require explicit `alpaca_paper` field — no
            # silent default. A typo on a live brokerage row that omits the field
            # used to default to paper, which silently routes real-money intent
            # to a paper account. Now: missing field logs RED and refuses the
            # decision (treat as paper for safety, but operator sees the warning).
            _paper_field = inst_doc.get("alpaca_paper")
            if _paper_field is None:
                # Phase C (2026-04-29): only fire the RED log for Alpaca where
                # the paper/live distinction is meaningful. Robinhood has no
                # paper account; RH_DRY_RUN env flag handles dry-run instead.
                if broker_type != "robinhood":
                    _early_log(
                        f"Instance {instance_id} has NO `alpaca_paper` field. Defaulting to "
                        f"PAPER for safety. Set alpaca_paper=true/false explicitly on the row.",
                        "red",
                    )
                paper = True
            else:
                paper = bool(_paper_field)
            if brokerage_id:
                try:
                    b_doc = _r.db("IntelliStock").table("BrokerageAccounts").get(brokerage_id).run(conn)
                except Exception as _e:
                    _early_log(f"BrokerageAccounts lookup for {brokerage_id} failed: {_e}", "red")
                    return None, None, broker_type, paper, brokerage_id
                if b_doc:
                    broker_type = (b_doc.get("brokerage_type") or broker_type or "alpaca").strip().lower()
                    _b_paper_field = b_doc.get("alpaca_paper")
                    if _b_paper_field is None:
                        # Phase C (2026-04-29): only RED-log for Alpaca. RH
                        # has no paper-account concept; RH_DRY_RUN handles dry-run.
                        if broker_type != "robinhood":
                            _early_log(
                                f"BrokerageAccount {brokerage_id} has NO `alpaca_paper` field. "
                                f"Defaulting to PAPER for safety. Set the field explicitly.",
                                "red",
                            )
                        paper = True
                    else:
                        paper = bool(_b_paper_field)
                    if broker_type == "alpaca":
                        try:
                            k = decrypt(b_doc.get("alpaca_key")) or None
                            s = decrypt(b_doc.get("alpaca_secret")) or None
                        except Exception as _e:
                            _early_log(
                                f"Decrypt failed for BrokerageAccount {brokerage_id} "
                                f"(is INTELLISTOCK_CRED_KEY set in this container?): "
                                f"{type(_e).__name__}: {_e}",
                                "red",
                            )
                            # Fail closed.
                            return None, None, broker_type, paper, brokerage_id
                        return k, s, broker_type, paper, brokerage_id
                    if broker_type == "robinhood":
                        try:
                            k = decrypt(b_doc.get("robinhood_access_token")) or None
                            s = decrypt(b_doc.get("robinhood_refresh_token")) or None
                        except Exception as _e:
                            _early_log(
                                f"Decrypt failed for BrokerageAccount {brokerage_id} "
                                f"(is INTELLISTOCK_CRED_KEY set?): {type(_e).__name__}: {_e}",
                                "red",
                            )
                            return None, None, broker_type, paper, brokerage_id
                        return k, s, broker_type, paper, brokerage_id
                    if broker_type in ("binanceus", "binance", "binance_us", "binance.us"):
                        try:
                            k = decrypt(b_doc.get("binanceus_key")) or None
                            s = decrypt(b_doc.get("binanceus_secret")) or None
                        except Exception as _e:
                            _early_log(
                                f"Decrypt failed for BrokerageAccount {brokerage_id} "
                                f"(is INTELLISTOCK_CRED_KEY set?): {type(_e).__name__}: {_e}",
                                "red",
                            )
                            return None, None, broker_type, paper, brokerage_id
                        return k, s, broker_type, paper, brokerage_id
            # No brokerage_id - instance-level legacy fields (decrypt passes
            # plaintext through unchanged).
            try:
                k = decrypt(inst_doc.get("key")) or None
                s = decrypt(inst_doc.get("secret")) or None
            except Exception as _e:
                _early_log(f"Decrypt of instance-level legacy creds failed: {type(_e).__name__}: {_e}", "red")
                return None, None, broker_type, paper, None
            return k, s, broker_type, paper, None
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as _e:
        _early_log(f"_load_live_credentials_from_db error: {type(_e).__name__}: {_e}", "red")
        return None, None, "alpaca", True, None


def _load_live_data_credentials_from_db(instance_id):
    """Load market-data Alpaca creds from the optional alpaca_data_brokerage_id
    on the Instance row. Used when the trading brokerage is PAPER (no data
    subscription) and a separate LIVE data account is linked.

    2026-04-23: also returns ``data_feed`` ("iex" or "sip") persisted on
    the BrokerageAccount row so bars calls use the user-chosen feed.
    Falls back to legacy env var ALPACA_DATA_FEED (and finally "iex") when
    the row doesn't carry the field yet.

    Returns (data_key, data_secret, data_brokerage_id, data_feed)
    or (None, None, None, None) if no data-source brokerage is linked
    or decrypt fails.
    """
    def _early_log(msg, color="white"):
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(msg, color, service="BROKER")
        except Exception:
            print(f"[BROKER] {msg}")

    try:
        from rethinkdb import RethinkDB
        from secret_store import decrypt
        _r = RethinkDB()
        host = os.environ.get("RETHINKDB_HOST", "localhost")
        port = int(os.environ.get("RETHINKDB_PORT", "28015"))
        conn = _r.connect(host=host, port=port, timeout=10)
        try:
            inst_doc = _r.db("IntelliStock").table("Instances").get(str(instance_id)).run(conn)
            if not inst_doc:
                return None, None, None, None
            data_bid = inst_doc.get("alpaca_data_brokerage_id") or None
            if not data_bid:
                return None, None, None, None
            try:
                b_doc = _r.db("IntelliStock").table("BrokerageAccounts").get(data_bid).run(conn)
            except Exception as _e:
                _early_log(f"Data BrokerageAccounts lookup for {data_bid} failed: {_e}", "red")
                return None, None, data_bid, None
            if not b_doc or (b_doc.get("brokerage_type") or "").strip().lower() != "alpaca":
                _early_log(f"alpaca_data_brokerage_id {data_bid} is not an Alpaca account; ignoring.", "yellow")
                return None, None, data_bid, None
            try:
                k = decrypt(b_doc.get("alpaca_key")) or None
                s = decrypt(b_doc.get("alpaca_secret")) or None
            except Exception as _e:
                _early_log(
                    f"Decrypt failed for data BrokerageAccount {data_bid} "
                    f"(is INTELLISTOCK_CRED_KEY set?): {type(_e).__name__}: {_e}",
                    "red",
                )
                return None, None, data_bid, None
            # 2026-04-23: persisted feed wins over legacy env var. UI save flow
            # validated this feed against data.alpaca.markets before storing,
            # so whatever's here is known to be authorized for this account.
            _feed_raw = str(b_doc.get("alpaca_data_feed") or "").strip().lower()
            if _feed_raw not in ("iex", "sip"):
                _feed_raw = os.environ.get("ALPACA_DATA_FEED", "iex").strip().lower() or "iex"
                if _feed_raw not in ("iex", "sip"):
                    _feed_raw = "iex"
            _early_log(
                f"Using separate data-source brokerage {data_bid} for market-data API calls "
                f"(paper={bool(b_doc.get('alpaca_paper', False))}, feed={_feed_raw}).",
                "cyan",
            )
            return k, s, data_bid, _feed_raw
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as _e:
        _early_log(f"_load_live_data_credentials_from_db error: {type(_e).__name__}: {_e}", "red")
        return None, None, None, None


# Defaults so variables exist if parse_args exits (e.g. --help)
instance_id = ""
mode = MODE_LIVE
start_date = end_date = time_increment = None
key = secret = ""
symbols = []
initial_cash = 100000.0
backtest_row_id = None
emulated_taker_fee = None  # --taker-fee: crypto fee to emulate in this backtest (else None = instance venue)
backtest_difficulty = None  # Set from BACKTEST_DIFFICULTY env by backtest engine (1-10)
backtest_high_usage = False  # Set from BACKTEST_HIGH_USAGE env when backtest has high-difficulty substrategy
try:
    parsed = parse_args()
    instance_id = parsed.instance_id
    mode = parsed.mode
    start_date = parsed.start_date
    end_date = parsed.end_date
    time_increment = parsed.time_increment
    key = parsed.key
    secret = parsed.secret
    symbols = parsed.symbols
    initial_cash = parsed.initial_cash
    backtest_row_id = parsed.backtest_row_id
    emulated_taker_fee = parsed.emulated_taker_fee
except SystemExit:
    sys.exit(2)

# 2026-07-11: Crypto instances run through THIS SAME broker, marked
# kind="crypto" on the Instances row (they are NOT a forked module). A small
# set of kind-gated branches (24/7 scheduler, market-hours bypass, crypto bars
# endpoint, gtc/no-extended orders) switch behavior; equity instances see
# kind=None and byte-identical behavior. The kind + crypto_config are read once
# and cached here so the hot loop never re-hits the DB.
_INSTANCE_KIND_CACHE = {"loaded": False, "kind": None, "crypto_config": {}, "broker_type": None}


def _instance_kind_and_crypto_config():
    """Return (kind, crypto_config) for this instance, read once and cached.
    kind is 'crypto' for crypto instances, else None/other. Fails closed to
    (None, {}) on any DB error so equities are never affected."""
    if _INSTANCE_KIND_CACHE["loaded"]:
        return _INSTANCE_KIND_CACHE["kind"], _INSTANCE_KIND_CACHE["crypto_config"]
    try:
        _c = get_conn()
        try:
            _doc = r.db(DB_NAME).table("Instances").get(str(instance_id)).run(_c) or {}
            # broker_type for the fee model: the Instances row usually lacks it
            # (action_create_instance only stores brokerage_id), so resolve the
            # venue from the LINKED brokerage. Without this a Binance.US-linked
            # crypto instance would backtest at Alpaca's 0.25% instead of 0.02%.
            bt = (_doc.get("broker_type") or "").strip().lower() or None
            if not bt:
                _bid = _doc.get("brokerage_id")
                if _bid:
                    try:
                        _bdoc = r.db(DB_NAME).table("BrokerageAccounts").get(str(_bid)).run(_c) or {}
                        bt = (_bdoc.get("brokerage_type") or "").strip().lower() or None
                    except Exception:
                        pass
        finally:
            try:
                _c.close()
            except Exception:
                pass
        kind = _doc.get("kind")
        cc = _doc.get("crypto_config") or {}
        # Cache ONLY on a SUCCESSFUL read. RethinkDB is memory-starved/flaky here;
        # caching a transient-error result would permanently mis-classify a real
        # crypto instance as equity for the whole process lifetime.
        _INSTANCE_KIND_CACHE.update({"loaded": True, "kind": kind, "crypto_config": cc, "broker_type": bt})
        return kind, cc
    except Exception:
        # Transient failure — do NOT cache; retry on the next tick.
        return None, {}


def _is_crypto_instance_runtime():
    """True iff this broker process is running a kind='crypto' instance."""
    return _instance_kind_and_crypto_config()[0] == "crypto"


def _instance_crypto_taker_fee():
    """Crypto taker fee for THIS instance's venue, for the backtest fee model:
    Binance.US = 0.02%, else Alpaca 0.25%. Returns None (PortfolioEmulator's
    default 0.25%) on any error so equity + Alpaca crypto backtests are unchanged."""
    try:
        _instance_kind_and_crypto_config()  # ensure the cache is loaded
        from broker_adapters.fees import crypto_taker_fee
        return crypto_taker_fee(_INSTANCE_KIND_CACHE.get("broker_type"))
    except Exception:
        return None

# Live-mode broker configuration: resolved from DB, not argv.
# data_key/data_secret are for market-data API calls (bars, news); they can be
# sourced from a SEPARATE brokerage row so operators can pair a PAPER trading
# account (cheap, no data subscription) with a LIVE data-subscription account.
# Falls back to trading creds if no separate data brokerage is linked.
live_broker_type = "alpaca"
live_broker_paper = True
live_brokerage_id = None
live_data_brokerage_id = None
data_key = None
data_secret = None
data_feed = "iex"  # user-selectable via BrokerageAccounts.alpaca_data_feed; falls back to "iex"
if mode == MODE_LIVE:
    # Always prefer DB creds in live mode (argv creds, if any, are scrubbed).
    _db_key, _db_secret, live_broker_type, live_broker_paper, live_brokerage_id = _load_live_credentials_from_db(instance_id)
    if _db_key:
        key = _db_key
    if _db_secret:
        secret = _db_secret
    # Final fallback to env vars is OFF by default. Set ALLOW_LEGACY_ENV_CREDS=1
    # during initial migration only; remove once all accounts are linked in DB.
    _allow_env_fallback = os.environ.get("ALLOW_LEGACY_ENV_CREDS", "").strip().lower() in ("1", "true", "yes")
    if _allow_env_fallback:
        if not key:
            env_key = (os.environ.get("APCA_API_KEY_ID") or os.environ.get("KEY") or "").strip()
            if env_key:
                try:
                    _log("ALLOW_LEGACY_ENV_CREDS=1: pulling broker key from environment (migration only)", "yellow")
                except Exception:
                    pass
                key = env_key
        if not secret:
            env_secret = (os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("SECRET") or "").strip()
            if env_secret:
                secret = env_secret
    # Resolve separate data-source creds if the instance has alpaca_data_brokerage_id set.
    data_key, data_secret, live_data_brokerage_id, _data_feed_from_db = _load_live_data_credentials_from_db(instance_id)
    if not data_key or not data_secret:
        # Fall back to trading creds (works if the trading brokerage also has data).
        data_key = key
        data_secret = secret
    # 2026-04-23: prefer persisted feed from data brokerage row; fall back to
    # trading brokerage's feed if data brokerage didn't supply one; final
    # fallback is env var / "iex".
    if _data_feed_from_db:
        data_feed = _data_feed_from_db
    else:
        try:
            _trading_feed = None
            if live_brokerage_id:
                from rethinkdb import RethinkDB as _Rcheck
                _rc = _Rcheck()
                _cc = _rc.connect(
                    host=os.environ.get("RETHINKDB_HOST", "localhost"),
                    port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                )
                try:
                    _tdoc = _rc.db("IntelliStock").table("BrokerageAccounts").get(live_brokerage_id).run(_cc)
                    _trading_feed = str((_tdoc or {}).get("alpaca_data_feed") or "").strip().lower()
                finally:
                    try:
                        _cc.close()
                    except Exception:
                        pass
            if _trading_feed in ("iex", "sip"):
                data_feed = _trading_feed
            else:
                data_feed = os.environ.get("ALPACA_DATA_FEED", "iex").strip().lower() or "iex"
                if data_feed not in ("iex", "sip"):
                    data_feed = "iex"
        except Exception:
            data_feed = os.environ.get("ALPACA_DATA_FEED", "iex").strip().lower() or "iex"
            if data_feed not in ("iex", "sip"):
                data_feed = "iex"
try:
    _bd = os.environ.get("BACKTEST_DIFFICULTY", "").strip()
    if _bd:
        backtest_difficulty = float(_bd)
except (TypeError, ValueError):
    pass
_backtest_high = os.environ.get("BACKTEST_HIGH_USAGE", "").strip().lower()
backtest_high_usage = _backtest_high in ("1", "true", "yes")


def _backtest_difficulty_discord_str():
    """Difficulty string for Discord embeds, with (HIGH USAGE) when applicable."""
    if backtest_difficulty is None:
        return "—"
    s = "%.1f" % backtest_difficulty
    if backtest_high_usage:
        s += " (HIGH USAGE)"
    return s


# ---------------------------------------------------------------------------
# Alpaca historical API: fetch 5-minute bars for backtest
# ---------------------------------------------------------------------------
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"
# ALPACA_TIMEFRAME is now determined dynamically from time_increment


def _alpaca_chunk_days_for_timeframe(timeframe):
    """
    Return max calendar days per chunk so each request stays under ~10k bars (market hours ~6.5h/day).
    For 1-hour and smaller timeframes, use much smaller chunks to avoid API limits and empty responses.
    """
    bars_per_day = {
        "1Min": 390,
        "5Min": 78,
        "15Min": 26,
        "30Min": 13,
        "1Hour": 7,
        "1Day": 1,
    }
    n = bars_per_day.get(timeframe, 7)
    
    # For 1-hour and smaller timeframes, use much smaller chunks to avoid API limits
    # and empty responses from data feeds (especially IEX) with limited history
    if timeframe == "1Hour":
        # 1-hour: ~7 bars per trading day, target ~100-150 bars per chunk (~15-20 trading days = ~20-30 calendar days)
        # Reduced from 30 to 20 calendar days to avoid empty chunks and improve reliability
        chunk_days = 20
    elif timeframe == "30Min":
        # 30-min: ~13 bars per trading day, target ~300-400 bars per chunk (~20-30 calendar days)
        chunk_days = 25
    elif timeframe == "15Min":
        # 15-min: ~26 bars per trading day, target ~400-500 bars per chunk (~15-20 calendar days)
        chunk_days = 20
    elif timeframe == "5Min":
        # 5-min: ~78 bars per trading day, target ~500-700 bars per chunk (~7-10 calendar days)
        chunk_days = 10
    elif timeframe == "1Min":
        # 1-min: ~390 bars per trading day, target ~1000-2000 bars per chunk (~3-5 calendar days)
        chunk_days = 5
    else:
        # For daily bars, can use larger chunks
        chunk_days = max(1, min(365, 10000 // n))
    
    return chunk_days


def fetch_alpaca_historical_bars(
    symbols,
    start_date,
    end_date,
    key=None,
    secret=None,
    timeframe="1Min",  # Default, will be overridden by caller
    db_conn=None,
    feed=None,  # 2026-04-23: user-selectable via BrokerageAccounts.alpaca_data_feed
    allow_backtest_rh_fallback=False,  # R13.1 (2026-04-24): strategy-config opt-in, see below
):
    """
    Fetch historical OHLCV bars from Alpaca Data API v2 by requesting smaller date-range
    chunks and stitching results. Avoids empty/large-range issues and stays under API limits.

    Args:
        symbols: Single symbol (str) or list of symbols (e.g. "AAPL" or ["AAPL", "MSFT"]).
        start_date: Start of range (str "YYYY-MM-DD" or datetime, or ISO string).
        end_date: End of range (str "YYYY-MM-DD" or datetime, or ISO string).
        key: Alpaca API key (default: os.environ["KEY"] or broker key).
        secret: Alpaca API secret (default: os.environ["SECRET"] or broker secret).
        timeframe: Bar size (default "5Min"). Use "1Min", "5Min", "15Min", "1Hour", "1Day".

    Returns:
        dict mapping symbol -> list of bar dicts. Each bar has: t (timestamp), o, h, l, c, v.
        Empty dict or empty lists on error or no data.
    """
    import requests
    from datetime import datetime, timedelta

    if not symbols:
        return {}
    if isinstance(symbols, str):
        symbols = [symbols]
    key = key or os.environ.get("KEY", "")
    secret = secret or os.environ.get("SECRET", "")
    if not key or not secret:
        try:
            _log(
                "Alpaca key or secret missing; cannot fetch historical bars. "
                "In backtest, set key/secret when creating the backtest (or on the instance). "
                "KEY and SECRET env vars are used as fallback.",
                "yellow",
            )
        except NameError:
            pass
        return {}

    def to_dt(d):
        if d is None:
            return None
        if isinstance(d, datetime):
            return d
        if isinstance(d, str):
            if "T" in d:
                s = d.replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    return datetime.strptime(d[:19], "%Y-%m-%dT%H:%M:%S")
            try:
                return datetime.strptime(d[:10], "%Y-%m-%d")
            except ValueError:
                try:
                    return datetime.strptime(d[:10], "%m/%d/%Y")
                except ValueError:
                    return None
        return None

    def to_iso(d):
        if d is None:
            return None
        if isinstance(d, datetime):
            return d.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(d, str):
            if "T" in d:
                return d
            try:
                dt = datetime.strptime(d[:10], "%Y-%m-%d")
                return dt.strftime("%Y-%m-%dT00:00:00Z")
            except ValueError:
                try:
                    dt = datetime.strptime(d[:10], "%m/%d/%Y")
                    return dt.strftime("%Y-%m-%dT00:00:00Z")
                except ValueError:
                    return d
        return str(d)

    start_dt = to_dt(start_date)
    end_dt = to_dt(end_date)
    if not start_dt or not end_dt or start_dt >= end_dt:
        return {}

    chunk_days = _alpaca_chunk_days_for_timeframe(timeframe)
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "accept": "application/json",
    }
    # 2026-04-23: prefer caller-supplied feed (from user's
    # BrokerageAccounts.alpaca_data_feed). Fall back to env var then "iex"
    # so test scripts and legacy call sites still work.
    if feed is None:
        feed = os.environ.get("ALPACA_DATA_FEED", "iex")
    feed = (feed or "iex").strip().lower() or "iex"
    if feed not in ("iex", "sip"):
        feed = "iex"
    symbols_norm = [s.strip().upper() for s in symbols]
    out = {s: [] for s in symbols_norm}

    try:
        from price_utils import get_bars_chunk_cached
    except ImportError:
        get_bars_chunk_cached = None

    def _robinhood_bars_fallback(sym, chunk_start, chunk_end, tf):
        """Fallback to Robinhood's public historicals endpoint when Alpaca
        rejects a symbol (401 on IEX free-tier without SIP subscription, or
        unavailable ticker). Returns bars in Alpaca's wire format
        ({t, o, h, l, c, v}) so the caller can merge into `out[sym]`
        without caring which provider served them.

        Robinhood's /quotes/historicals/ is public (no auth required) and
        covers most US-listed tickers that Alpaca IEX skips.

        Logs every step so operators can see when/why fallback fires or
        why it returns empty (import failure, empty DF, window filter).
        """
        try:
            from robinhood_engine import get_price_history
        except Exception as _imp_e:
            try:
                _log(
                    f"Robinhood fallback: CANNOT IMPORT robinhood_engine for {sym} "
                    f"({type(_imp_e).__name__}: {_imp_e}) — install robin-stocks + its deps",
                    "red",
                )
            except NameError:
                pass
            return []
        _tf = (tf or "").strip()
        # Map Alpaca timeframe to Robinhood interval + span + bounds.
        # CRITICAL: Robinhood's API rejects combinations of span and bounds:
        #   - `bounds=trading` only works with intraday spans (day, week).
        #   - `bounds=regular` is required for span=year / 5year.
        # Previously left at the default `bounds=trading`, so every call
        # with span=year 400'd with:
        #   "span 'year' is not valid with bounds 'trading'."
        # and returned an empty DataFrame silently → fallback appeared
        # to do nothing. Fixed 2026-04-21.
        if _tf.startswith("1Min") or _tf.startswith("5Min"):
            interval, span, bounds = "5minute", "week", "trading"
        elif _tf.startswith("1Hour") or _tf.startswith("4Hour"):
            interval, span, bounds = "hour", "week", "trading"
        else:
            interval = "day"
            try:
                _days = (chunk_end - chunk_start).days
            except Exception:
                _days = 365
            if _days <= 7:
                span, bounds = "week", "trading"
            elif _days <= 365:
                span, bounds = "year", "regular"
            else:
                span, bounds = "5year", "regular"
        try:
            _log(
                f"Robinhood fallback: CALLING get_price_history "
                f"sym={sym} tf={_tf} interval={interval} span={span} bounds={bounds} "
                f"window={chunk_start.strftime('%Y-%m-%d')}..{chunk_end.strftime('%Y-%m-%d')}",
                "cyan",
            )
        except Exception:
            pass
        try:
            df = get_price_history(sym, interval=interval, span=span, bounds=bounds)
        except Exception as _rh_e:
            try:
                _log(
                    f"Robinhood fallback: get_price_history RAISED for {sym}: "
                    f"{type(_rh_e).__name__}: {_rh_e}",
                    "yellow",
                )
            except NameError:
                pass
            return []
        if df is None or getattr(df, "empty", True):
            try:
                _log(
                    f"Robinhood fallback: EMPTY DataFrame for {sym} "
                    f"(interval={interval}, span={span}) — symbol may not exist on RH "
                    f"or endpoint returned no rows",
                    "yellow",
                )
            except NameError:
                pass
            return []
        try:
            _log(
                f"Robinhood fallback: raw DF has {len(df)} row(s) for {sym} "
                f"before window filter",
                "cyan",
            )
        except Exception:
            pass

        # Bug-swept 2026-04-21:
        # C1 - NaN contamination: `row.get("Open") or 0` returns NaN because
        #      NaN is truthy. `float(nan)` poisons downstream indicator math.
        #      Use pd.notna + explicit NaN check.
        # M2 - NaT handling: `pd.to_datetime` can yield NaT for malformed
        #      begins_at; skip those.
        def _safe_float(v):
            try:
                if v is None:
                    return 0.0
                f = float(v)
                return 0.0 if f != f else f
            except (TypeError, ValueError):
                return 0.0

        def _safe_int(v):
            try:
                if v is None:
                    return 0
                f = float(v)
                return 0 if f != f else int(f)
            except (TypeError, ValueError):
                return 0

        # Filter to the requested window + convert each row to Alpaca-shape.
        bars = []
        try:
            _cs = chunk_start.replace(tzinfo=None) if getattr(chunk_start, "tzinfo", None) else chunk_start
            _ce = chunk_end.replace(tzinfo=None) if getattr(chunk_end, "tzinfo", None) else chunk_end
        except Exception:
            _cs, _ce = chunk_start, chunk_end
        for dt, row in df.iterrows():
            try:
                _dt_naive = dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt
                # Skip NaT (Not-a-Time from pandas on malformed RH timestamps).
                try:
                    import pandas as _pd_check
                    if _pd_check.isna(_dt_naive):
                        continue
                except Exception:
                    pass
                if getattr(_dt_naive, "tzinfo", None) is not None:
                    _dt_naive = _dt_naive.replace(tzinfo=None)
                if _dt_naive < _cs or _dt_naive >= _ce:
                    continue
                _c = _safe_float(row.get("Close"))
                if _c <= 0:
                    # Skip bars with invalid/zero close — they break price logic.
                    continue
                bars.append({
                    "t": _dt_naive.isoformat() + "Z",
                    "o": _safe_float(row.get("Open")),
                    "h": _safe_float(row.get("High")),
                    "l": _safe_float(row.get("Low")),
                    "c": _c,
                    "v": _safe_int(row.get("Volume")),
                })
            except Exception:
                continue
        try:
            _log(
                f"Robinhood fallback: window-filtered {len(bars)}/{len(df)} row(s) "
                f"for {sym} ({chunk_start.strftime('%Y-%m-%d')}..{chunk_end.strftime('%Y-%m-%d')})",
                "cyan",
            )
        except Exception:
            pass
        return bars

    def _do_fetch_one_chunk(sym, chunk_start, chunk_end, log_empty_once=None, retry_smaller=False):
        """Perform the actual Alpaca HTTP request for one chunk. Returns list of bars."""
        # Crypto (kind="crypto") uses the v1beta3 crypto bars endpoint: symbols
        # as a query param, NO feed, and a symbol-keyed response dict. Equities
        # keep the /v2/stocks/{sym}/bars path unchanged.
        _is_crypto = _is_crypto_instance_runtime()
        start_iso = to_iso(chunk_start)
        end_iso = to_iso(chunk_end)
        if not start_iso or not end_iso:
            return []
        if _is_crypto:
            url = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
            params = {
                "symbols": sym,
                "start": start_iso,
                "end": end_iso,
                "timeframe": timeframe,
                "limit": 10000,
                "sort": "asc",
            }
        else:
            url = f"{ALPACA_DATA_BASE}/stocks/{sym}/bars"
            params = {
                "start": start_iso,
                "end": end_iso,
                "timeframe": timeframe,
                "limit": 10000,
                "feed": feed,
                "sort": "asc",
            }
        collected = []
        # Live-readiness P0 #4: 429 retry/backoff on Alpaca data API. Under live
        # load (every 5 min × hundreds of tickers via discovery) IEX free tier
        # 200 req/min is reachable. Old code raised on 429 and the outer chunk
        # loop just skipped, silently dropping bars. Now: honor Retry-After up
        # to 3 attempts, then give up loudly.
        _429_attempts = 0
        _429_max_attempts = 3
        # 2026-05-02 hardening: cap pagination at 500 pages per chunk so a
        # buggy/adversarial Alpaca response (next_page_token returned forever)
        # cannot freeze the fetch thread indefinitely. 500 pages × 10000 bars
        # = 5M bars per chunk — vastly more than any legitimate hourly window.
        _page_count = 0
        _max_pages = 500
        while True:
            _page_count += 1
            if _page_count > _max_pages:
                try:
                    _log(
                        f"Alpaca pagination cap hit for {sym} ({_max_pages} pages); "
                        f"breaking to avoid infinite loop. Fetched {len(collected)} bars so far.",
                        "red",
                    )
                except NameError:
                    pass
                break
            # Alpaca's v1beta3 crypto market-data endpoint is PUBLIC (no auth). If
            # we send the instance's trading-account auth headers and they aren't
            # authorized for the data API (e.g. paper keys), Alpaca rejects the
            # request with 401 — even though an unauthenticated request succeeds.
            # So for crypto, omit auth entirely; equities keep their required auth.
            _req_headers = {"accept": "application/json"} if _is_crypto else headers
            r = requests.get(url, headers=_req_headers, params=params, timeout=60)
            if r.status_code == 429 and _429_attempts < _429_max_attempts:
                _429_attempts += 1
                _retry_after = r.headers.get("Retry-After", "2")
                try:
                    _sleep_s = max(1.0, min(30.0, float(_retry_after)))
                except (ValueError, TypeError):
                    _sleep_s = 2.0 * _429_attempts  # exponential-ish fallback
                try:
                    _log(f"Alpaca data 429 for {sym}: backing off {_sleep_s:.1f}s (attempt {_429_attempts}/{_429_max_attempts})", "yellow")
                except NameError:
                    pass
                time.sleep(_sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if _is_crypto:
                # v1beta3 crypto response is symbol-keyed: {"bars": {"BTC/USD": [...]}}
                bars = (data.get("bars") or {}).get(sym, []) or []
            else:
                bars = data.get("bars") or []
            collected.extend(bars)
            if log_empty_once is not None and len(bars) == 0 and not log_empty_once[0]:
                log_empty_once[0] = True
                try:
                    _log(
                        "Alpaca returned 0 bars for %s (request: start=%s end=%s feed=%s timeframe=%s). "
                        "IEX free tier has limited history; try ALPACA_DATA_FEED=sip if subscribed, or a more recent date range."
                        % (sym, start_iso, end_iso, feed, timeframe),
                        "yellow",
                    )
                except NameError:
                    pass
            next_token = data.get("next_page_token")
            if not next_token:
                break
            params = dict(params)
            params["page_token"] = next_token
            if not _is_crypto:
                # Equities (v2 /stocks/{sym}/bars): preserve the pre-existing
                # pagination behavior exactly — drop start/end and resume from
                # page_token. Equity chunks fit under `limit` in a single page,
                # so this branch is effectively unreachable for equities and
                # the real-money path stays byte-identical.
                params.pop("start", None)
                params.pop("end", None)
            # Crypto (v1beta3 /crypto/us/bars): KEEP start/end on every page.
            # That endpoint caps each page at ~1000 bars (far below our
            # limit=10000) and returns a next_page_token for ANY multi-day
            # window, so crypto always paginates. Dropping `end` (as the
            # equity path does) leaves the follow-up pages unbounded, so a
            # single 20-day chunk over-fetches from its start to the end of
            # ALL available data (observed: ~17.3k bars / 26 pages for a
            # 20-day 15Min window that should be ~1.9k / 3 pages). Keeping
            # start+end bounds each chunk to its requested window; the
            # page_token still resumes correctly and the response drops the
            # token once `end` is reached.
        # If chunk returned 0 bars and retry_smaller is enabled, try splitting into smaller chunks
        if len(collected) == 0 and retry_smaller and (chunk_end - chunk_start).days > 7:
            mid_point = chunk_start + (chunk_end - chunk_start) / 2
            try:
                left_bars = fetch_one_chunk(sym, chunk_start, mid_point, log_empty_once=None, retry_smaller=False)
                right_bars = fetch_one_chunk(sym, mid_point, chunk_end, log_empty_once=None, retry_smaller=False)
                collected = left_bars + right_bars
            except Exception:
                pass
        return collected

    def fetch_one_chunk(sym, chunk_start, chunk_end, log_empty_once=None, retry_smaller=False):
        """Fetch bars for one symbol in [chunk_start, chunk_end); from cache if available, else Alpaca."""
        if db_conn and get_bars_chunk_cached:
            bars, from_cache = get_bars_chunk_cached(
                db_conn, sym, chunk_start, chunk_end, timeframe, feed,
                lambda: _do_fetch_one_chunk(sym, chunk_start, chunk_end, log_empty_once, retry_smaller),
            )
            if from_cache and bars:
                try:
                    _log(f"Bars chunk from cache for {sym} ({chunk_start.date()} to {chunk_end.date()})", "cyan")
                except NameError:
                    pass
            return bars
        return _do_fetch_one_chunk(sym, chunk_start, chunk_end, log_empty_once, retry_smaller)

    try:
        for sym in symbols_norm:
            chunk_start = start_dt
            log_empty_once = [False]
            total_chunks = max(1, int((end_dt - start_dt).days / chunk_days) + 1)
            chunk_num = 0
            # Enable retry with smaller chunks for 1-hour bars to handle empty responses better
            enable_retry = (timeframe == "1Hour")
            while chunk_start < end_dt:
                chunk_end = min(chunk_start + timedelta(days=chunk_days), end_dt)
                chunk_num += 1
                try:
                    bars = fetch_one_chunk(sym, chunk_start, chunk_end, log_empty_once=log_empty_once, retry_smaller=enable_retry)
                    out[sym].extend(bars)
                    if chunk_num == 1 or chunk_num % 5 == 0 or chunk_end >= end_dt:
                        try:
                            _log(f"Fetched chunk {chunk_num}/{total_chunks} for {sym}: {len(bars)} bars ({chunk_start.date()} to {chunk_end.date()})", "cyan")
                        except NameError:
                            pass
                except Exception as e:
                    _err_str = str(e)
                    try:
                        _log(f"Alpaca bars chunk error for {sym} ({chunk_start.date()}–{chunk_end.date()}): {e}", "yellow")
                    except NameError:
                        pass
                    # Bug-swept 2026-04-21:
                    # Agent-3 C1: RH fallback writing to AlpacaBarsCache during
                    #   backtests would silently swap data providers mid-run
                    #   and break V32 Phase 3 reproducibility. Skip RH entirely
                    #   when mode==MODE_BACKTEST so backtests stay Alpaca-pure.
                    # Agent-1 H1: Only fall back on 401/403/404 (permanent
                    #   auth/access failures). For 429/5xx, let the outer
                    #   loop keep the symbol's partial bars and move on —
                    #   don't spam RH on transient Alpaca issues.
                    # R13.1: strategy-config opt-in for backtest RH fallback
                    # via allow_backtest_rh_fallback kwarg. When the strategy
                    # doesn't pass it (default False), backtest stays pure.
                    _should_fallback = (mode != MODE_BACKTEST and robinhood_data_fallback_allowed(live_broker_type)) or bool(locals().get("allow_backtest_rh_fallback", False))
                    if _should_fallback:
                        _status_code = None
                        try:
                            _resp = getattr(e, "response", None)
                            if _resp is not None:
                                _status_code = getattr(_resp, "status_code", None)
                        except Exception:
                            _status_code = None
                        if _status_code is not None and _status_code not in (401, 403, 404, 422):
                            _should_fallback = False
                            try:
                                _log(
                                    f"Skipping Robinhood fallback for {sym}: transient Alpaca "
                                    f"HTTP {_status_code} (will retry next cycle).",
                                    "yellow",
                                )
                            except NameError:
                                pass
                    # Robinhood public-historicals fallback — covers symbols
                    # that Alpaca IEX rejects with 401 Unauthorized (MRK,
                    # TSLA, NVO, GOOGL, EA, CSCO etc. on the free data feed).
                    # No auth required; public /quotes/historicals/ endpoint.
                    # Successful RH bars are written into the same
                    # AlpacaBarsCache table under the same chunk key so the
                    # NEXT run hits cache and skips both Alpaca 401 AND the
                    # Robinhood call — matching the user's "reduce calls" ask.
                    # Cache writes are LIVE-ONLY so backtests can't read RH
                    # bars that were originally fetched for live mode (they
                    # will cache-miss, hit Alpaca, and either succeed or fail
                    # fast with no silent provider swap).
                    try:
                        _rh_bars = _robinhood_bars_fallback(sym, chunk_start, chunk_end, timeframe) if _should_fallback else []
                        if _rh_bars:
                            out[sym].extend(_rh_bars)
                            try:
                                _log(
                                    f"Robinhood fallback: fetched {len(_rh_bars)} bars for {sym} "
                                    f"({chunk_start.date()}–{chunk_end.date()})",
                                    "green",
                                )
                            except NameError:
                                pass
                            # Persist to the same cache so future runs reuse.
                            # Bug-swept 2026-04-21: previously referenced
                            # undefined `r` (shadowed by local requests.Response)
                            # and `DB_NAME` (defined only in other functions) and
                            # `datetime.datetime.utcnow()` (here `datetime` IS
                            # the class via `from datetime import datetime`).
                            # Fix: use `_rethink` already initialized at
                            # price_utils module-import time, hardcode db name,
                            # call `datetime.utcnow()`.
                            if db_conn is not None:
                                try:
                                    import base64 as _b64
                                    import gzip as _gz
                                    import json as _json
                                    from price_utils import (
                                        alpaca_bars_cache_key as _key_fn,
                                        ALPACA_BARS_CACHE_TABLE as _bars_tbl,
                                        _BARS_COMPRESS_THRESHOLD as _bars_thresh,
                                        _ensure_bars_cache_table as _ensure_bars_tbl,
                                        _rethink as _rdb,
                                    )
                                    if _rdb is not None:
                                        _cache_db = "IntelliStock"
                                        _ensure_bars_tbl(db_conn, _cache_db, _bars_tbl)
                                        _cache_id = _key_fn(sym, chunk_start, chunk_end, timeframe, feed)
                                        _bars_json = _json.dumps(_rh_bars)
                                        _payload = _bars_json
                                        _compressed = False
                                        if len(_bars_json) > _bars_thresh:
                                            _payload = _b64.b64encode(_gz.compress(_bars_json.encode("utf-8"))).decode("ascii")
                                            _compressed = True
                                        _rdb.db(_cache_db).table(_bars_tbl).insert({
                                            "id": _cache_id,
                                            "symbol": sym.upper(),
                                            "start_date": chunk_start.strftime("%Y-%m-%d"),
                                            "end_date": chunk_end.strftime("%Y-%m-%d"),
                                            "timeframe": timeframe,
                                            "feed": feed,
                                            "bars": _payload,
                                            "compressed": _compressed,
                                            "cached_at": datetime.utcnow().isoformat() + "Z",
                                            "source": "robinhood_fallback",
                                        }, conflict="replace").run(db_conn)
                                        try:
                                            _log(
                                                f"Robinhood fallback: CACHED {len(_rh_bars)} bars "
                                                f"for {sym} to AlpacaBarsCache "
                                                f"(id={_cache_id[:12]}..., compressed={_compressed})",
                                                "green",
                                            )
                                        except NameError:
                                            pass
                                except Exception as _cache_e:
                                    # Agent-3 M1: surface cache write failures
                                    # so operators can see when "reduce calls"
                                    # path is broken (RethinkDB down, schema
                                    # drift, etc.) instead of silently
                                    # re-fetching from RH every run.
                                    try:
                                        _log(
                                            f"RH fallback cache write failed for {sym}: "
                                            f"{type(_cache_e).__name__}: {_cache_e}",
                                            "yellow",
                                        )
                                    except NameError:
                                        pass
                    except Exception as _rh_e:
                        try:
                            _log(f"Robinhood fallback failed for {sym}: {_rh_e}", "yellow")
                        except NameError:
                            pass
                chunk_start = chunk_end
            # Sort by time and dedupe by 't'
            seen_t = set()
            unique = []
            for b in sorted(out[sym], key=lambda x: x.get("t") or ""):
                t = b.get("t")
                if t and t not in seen_t:
                    seen_t.add(t)
                    unique.append(b)
            out[sym] = unique
        return out
    except Exception as e:
        try:
            _log(f"Alpaca historical bars fetch failed: {e}", "yellow")
        except NameError:
            pass
        return out


def _resolve_data_brokerage_creds_now():
    """R17 (2026-04-25): re-resolve Alpaca data-brokerage creds DIRECTLY from
    RethinkDB at the moment of need, bypassing the module-level
    ``data_key``/``data_secret`` globals.

    The globals are populated at broker boot from
    ``_load_live_data_credentials_from_db(instance_id)``. That call can
    return ``None,None,None,None`` for a number of silent reasons:
    decrypt failure (``INTELLISTOCK_CRED_KEY`` missing on the container,
    e.g. backtest container has a different env), Instance row missing
    the ``alpaca_data_brokerage_id`` linkage, or transient RethinkDB
    connectivity issues. When that happens, the boot-time fallback at
    lines 404-407 silently sets ``data_key = key`` (the TRADING / paper
    creds), which then 401s every bars-endpoint request because paper
    trading creds don't carry the data-API entitlement bound to the
    live data brokerage.

    This helper short-circuits that whole path: it queries
    BrokerageAccounts directly, prefers the instance's linked data
    brokerage if available, otherwise scans for any Alpaca account with
    ``alpaca_paper=False`` (the typical signature of a live account
    holding the data subscription). Decrypts on-demand. Returns
    ``(key, secret, feed)`` or ``(None, None, None)`` on hard failure.
    Logs each resolution outcome so operators can see why a backtest's
    bars fetches succeeded or failed.
    """
    try:
        from rethinkdb import RethinkDB as _Rcheck
        from secret_store import decrypt as _decrypt
    except Exception as _import_exc:
        try:
            _log(f"R17 cred resolve: import failed — {type(_import_exc).__name__}: {_import_exc}", "red")
        except NameError:
            pass
        return None, None, None
    try:
        _r17 = _Rcheck()
        _conn = _r17.connect(
            host=os.environ.get("RETHINKDB_HOST", "localhost"),
            port=int(os.environ.get("RETHINKDB_PORT", "28015")),
        )
    except Exception as _conn_exc:
        try:
            _log(f"R17 cred resolve: RethinkDB connect failed — {_conn_exc}", "yellow")
        except NameError:
            pass
        return None, None, None
    try:
        _data_bid = None
        # Step 1: prefer the explicitly-linked data brokerage on the instance
        if instance_id:
            try:
                _inst = _r17.db("IntelliStock").table("Instances").get(str(instance_id)).run(_conn)
                if _inst and _inst.get("alpaca_data_brokerage_id"):
                    _data_bid = str(_inst["alpaca_data_brokerage_id"])
            except Exception:
                _data_bid = None
        # Step 2: fallback — scan BrokerageAccounts for an Alpaca live account
        if not _data_bid:
            try:
                _rows = list(
                    _r17.db("IntelliStock").table("BrokerageAccounts")
                    .filter(lambda doc: doc["brokerage_type"] == "alpaca")
                    .run(_conn)
                )
                # Prefer paper=False (live, holds data subscription)
                for _row in _rows:
                    if not bool(_row.get("alpaca_paper", False)) and _row.get("alpaca_data_feed"):
                        _data_bid = str(_row.get("id"))
                        break
                # If no live account, accept any Alpaca account
                if not _data_bid and _rows:
                    _data_bid = str(_rows[0].get("id"))
            except Exception:
                _data_bid = None
        if not _data_bid:
            try:
                _log("R17 cred resolve: no Alpaca brokerage found in BrokerageAccounts table", "yellow")
            except NameError:
                pass
            return None, None, None
        try:
            _row = _r17.db("IntelliStock").table("BrokerageAccounts").get(_data_bid).run(_conn)
        except Exception as _e:
            try:
                _log(f"R17 cred resolve: BrokerageAccounts.get({_data_bid[:8]}...) failed — {_e}", "yellow")
            except NameError:
                pass
            return None, None, None
        if not _row:
            return None, None, None
        try:
            _k = _decrypt(_row.get("alpaca_key")) or None
            _s = _decrypt(_row.get("alpaca_secret")) or None
        except Exception as _e:
            try:
                _log(
                    f"R17 cred resolve: decrypt failed for {_data_bid[:8]}... — "
                    f"{type(_e).__name__}: {_e} (is INTELLISTOCK_CRED_KEY set on this container?)",
                    "red",
                )
            except NameError:
                pass
            return None, None, None
        if not _k or not _s:
            return None, None, None
        _feed = str(_row.get("alpaca_data_feed") or "iex").strip().lower()
        if _feed not in ("iex", "sip"):
            _feed = "iex"
        try:
            _log(
                f"R17 cred resolve: using brokerage {_data_bid[:8]}... "
                f"(paper={_row.get('alpaca_paper', False)}, feed={_feed}, key=...{_k[-4:]})",
                "cyan",
            )
        except NameError:
            pass
        return _k, _s, _feed
    finally:
        try:
            _conn.close()
        except Exception:
            pass


def _ensure_backtest_history_for_symbols(data, symbols, key=None, secret=None):
    """Load historical bars for newly discovered symbols during backtests."""
    global _backtest_no_history_symbols
    if mode != MODE_BACKTEST or not isinstance(data, dict):
        return []
    symbols_norm = sorted({
        str(sym or "").strip().upper()
        for sym in (symbols or [])
        if str(sym or "").strip()
    })
    missing = [sym for sym in symbols_norm if sym not in data and sym not in _backtest_no_history_symbols]
    if not missing:
        return []
    if _backtest_fetch_start_dt is None or _backtest_fetch_end_dt is None:
        _log("Backtest symbol expansion: fetch window is not initialized; cannot load discovered symbol bars", "yellow")
        return []
    timeframe = _backtest_alpaca_timeframe or "1Day"
    _log(
        f"Backtest symbol expansion: fetching {timeframe} history for {len(missing)} discovered symbol(s): {', '.join(missing[:10])}",
        "cyan",
    )
    db_conn = None
    try:
        db_conn = get_conn_retry(max_attempts=3, delay=1)
    except Exception:
        db_conn = None
    # R17 (2026-04-25): re-resolve data-brokerage creds at the call site.
    # Module-level data_key/data_secret can be empty/wrong if boot-time
    # decrypt failed (e.g. INTELLISTOCK_CRED_KEY mismatch between container
    # env files), causing every bars fetch to 401 with paper trading creds.
    # Direct re-resolution from RethinkDB is the source-of-truth path.
    _r17_k, _r17_s, _r17_feed = _resolve_data_brokerage_creds_now()
    if _r17_k and _r17_s:
        _final_k, _final_s = _r17_k, _r17_s
        _final_feed = _r17_feed or data_feed or "iex"
    else:
        _final_k, _final_s = key, secret
        _final_feed = data_feed
    try:
        # Note: fetch_alpaca_historical_bars does NOT accept `feed=` on this
        # branch — the R7/a3cf155 commit that added that param is not in the
        # keys-on-baseline cherry-pick chain. _final_feed is computed and
        # logged via R17's diagnostic but the actual feed selection happens
        # inside the helper using the data brokerage's stored value.
        fetched = fetch_alpaca_historical_bars(
            missing,
            _backtest_fetch_start_dt,
            _backtest_fetch_end_dt,
            key=_final_k,
            secret=_final_s,
            timeframe=timeframe,
            db_conn=db_conn,
            feed=data_feed,  # 2026-04-23 bug-sweep: match primary backtest prep at :3706
        )
    finally:
        try:
            if db_conn is not None:
                db_conn.close()
        except Exception:
            pass
    loaded = []
    for sym in missing:
        bars = list((fetched or {}).get(sym) or [])
        if bars:
            data[sym] = bars
            loaded.append(sym)
            _log(f"Backtest symbol expansion: loaded {len(bars)} {timeframe} bars for {sym}", "cyan")
        else:
            data.setdefault(sym, [])
            _backtest_no_history_symbols.add(sym)
            _log(f"Backtest symbol expansion: no historical bars found for {sym}", "yellow")
    return loaded

# ---------------------------------------------------------------------------
# RethinkDB: check if instance should keep broker alive (runCommand=True)
# ---------------------------------------------------------------------------
try:
    from rethinkdb import RethinkDB
    r = RethinkDB()
    DB_NAME = 'IntelliStock'
    RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
    RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

    def get_conn():
        # 2026-05-05 live-hang investigation: bound r.connect() with an
        # explicit timeout so a half-open TCP socket on the rdb side
        # cannot wedge the snapshot/SCP-flush threads forever.
        return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT, timeout=10)

    def get_conn_retry(max_attempts=5, delay=2):
        """Try get_conn() up to max_attempts with delay between attempts. Returns conn or None."""
        for attempt in range(1, max_attempts + 1):
            try:
                return get_conn()
            except Exception as e:
                if attempt == max_attempts:
                    return None
                try:
                    import time as _t
                    _t.sleep(delay)
                except Exception:
                    pass
        return None

    def should_keep_alive():
        """Return True if Instances[instance_id].runCommand is True (keep broker alive)."""
        if not instance_id:
            return False
        try:
            conn = get_conn()
            try:
                doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
                return doc is not None and doc.get('runCommand', False) is True
            finally:
                conn.close()
        except Exception:
            return True  # On error, assume keep alive and let reconnect retry
except Exception:
    r = None
    DB_NAME = 'IntelliStock'
    RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
    RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

    def get_conn():
        raise RuntimeError("RethinkDB is not available (import or init failed)")

    def get_conn_retry(max_attempts=5, delay=2):
        return None

    def should_keep_alive():
        return True


def _init_llm_telemetry() -> None:
    """Best-effort telemetry setup for broker/backtest worker processes."""
    if r is None:
        return
    try:
        import llm_telemetry

        _override_pm_cache: dict = {}  # (provider, model) -> (ts, override|None)

        def _models_override_lookup(model_id, provider=None, model=None):
            keys = (
                "input_cost_per_1m",
                "output_cost_per_1m",
                "cache_creation_cost_per_1m",
                "cache_read_cost_per_1m",
            )

            def _extract(row):
                if not row:
                    return None
                out = {key: row.get(key) for key in keys if row.get(key) is not None}
                return out or None

            conn = None
            try:
                conn = get_conn()
                if model_id:
                    res = _extract(r.db(DB_NAME).table("Models").get(model_id).run(conn))
                    if res is not None:
                        return res
                # Fall back to a (provider, model) match so per-model price
                # overrides apply even when the call site didn't thread the
                # Models-row id (model_id is None on the plain / raw-json
                # structured paths). Cached briefly — Models is tiny but this
                # runs per recorded call.
                if provider and model:
                    ck = (str(provider), str(model))
                    hit = _override_pm_cache.get(ck)
                    if hit and (time.time() - hit[0]) < 60.0:
                        return hit[1]
                    matches = list(
                        r.db(DB_NAME).table("Models")
                        .filter({"provider": provider, "model": model}).run(conn)
                    )
                    chosen = next((m for m in matches if _extract(m) is not None), None)
                    res = _extract(chosen)
                    _override_pm_cache[ck] = (time.time(), res)
                    return res
                return None
            except Exception:
                return None
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        pricing_path = os.path.join(os.path.dirname(__file__), "llm_pricing.yaml")
        llm_telemetry.configure(
            db_conn_factory=get_conn,
            enabled=True,
            flush_interval_s=2.0,
            max_buffer=50,
            pricing_yaml_path=pricing_path,
            r_module=r,
            db_name=DB_NAME,
            models_override_lookup=_models_override_lookup,
        )
        try:
            from llm_telemetry import ensure_llm_usage_tables
            setup_conn = get_conn()
            try:
                ensure_llm_usage_tables(conn=setup_conn, r=r, db_name=DB_NAME)
            finally:
                try:
                    setup_conn.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            _live_atexit.register(llm_telemetry.flush)
        except Exception:
            pass
    except Exception as exc:
        try:
            print(f"[BROKER] llm telemetry init failed: {exc}", flush=True)
        except Exception:
            pass


_init_llm_telemetry()

# ---------------------------------------------------------------------------
# Strategies: load from DB, run from backend/strategies/<name>.py by execution_position
# ---------------------------------------------------------------------------
# Strategy row from table Strategies: { "id": int, "instance": int, "strategies": [{"weight": float, "execution_position": int, "strategy": str, "conditions": dict}] }
# Each strategy file lives in backend/strategies/<module_name>.py with a class named like the strategy (PascalCase).
# The class has a run(self, symbol, price, current_time, config, conditions, data, portfolio_emulator) method.
# The run() method can return:
#   - An integer (1, 0, -1) for backward compatibility
#   - A tuple (score, weight_override) where score is 1|0|-1 and weight_override is float (0-1) or None
#     If weight_override is provided, other strategies' weights are scaled proportionally to share remaining weight.

import importlib.util

def _strategy_name_to_module_and_class(name):
    """Strategy string from DB -> (module_name for file, class_name). e.g. 'Example' -> ('example', 'Example')."""
    name = (name or '').strip()
    if not name:
        return None, None
    # Support both canonical snake_case ids and legacy PascalCase names from older UI/API paths.
    module_name = name.replace(" ", "_")
    module_name = re.sub(r"(?<!^)(?=[A-Z])", "_", module_name)
    module_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in module_name).strip()
    module_name = re.sub(r"_+", "_", module_name).strip("_").lower()
    if not module_name:
        return None, None
    # Class: PascalCase (e.g. example -> Example, macd_signal -> MacdSignal)
    class_name = ''.join(w.capitalize() for w in module_name.split('_'))
    return module_name, class_name

def _load_strategy_class(strategy_name):
    """Load the strategy class from strategies/<module_name>.py. Returns the class or None."""
    module_name, class_name = _strategy_name_to_module_and_class(strategy_name)
    if not module_name or not class_name:
        _log(f"Invalid strategy name '{strategy_name}': module={module_name}, class={class_name}", "yellow")
        return None
    broker_dir = os.path.dirname(os.path.abspath(__file__))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    try:
        # Resolve the flat equity path first (strategies.<module>); fall back to
        # the crypto subpackage (strategies.crypto.<module>) so crypto strategies
        # load through this same loader. Flat-first keeps equity behavior intact.
        spec = None
        _resolved_mod_path = None
        for _mod_path in ("strategies." + module_name, "strategies.crypto." + module_name):
            try:
                _cand = importlib.util.find_spec(_mod_path)
            except (ImportError, AttributeError, ValueError):
                _cand = None
            if _cand is not None and _cand.origin is not None:
                spec = _cand
                _resolved_mod_path = _mod_path
                break
        if spec is None:
            _log(f"Module 'strategies.{module_name}' (nor strategies.crypto.{module_name}) found for strategy '{strategy_name}'", "yellow")
            return None
        module = importlib.util.module_from_spec(spec)
        # Register module in sys.modules before execution (required for dataclass decorators)
        sys.modules[_resolved_mod_path] = module
        spec.loader.exec_module(module)
        cls = getattr(module, class_name, None)
        if cls is None:
            _log(f"Class '{class_name}' not found in module 'strategies.{module_name}' for strategy '{strategy_name}'. Available: {[x for x in dir(module) if not x.startswith('_')]}", "yellow")
        elif not isinstance(cls, type):
            _log(f"'{class_name}' in module 'strategies.{module_name}' is not a class (type: {type(cls)})", "yellow")
            return None
        return cls
    except Exception as e:
        _log(f"Error loading strategy '{strategy_name}' (module='strategies.{module_name}', class='{class_name}'): {e}", "red")
        import traceback
        _log(traceback.format_exc(), "red")
        return None

# Cache loaded strategy classes by strategy name
_strategy_class_cache = {}

# Pre-decision strategies run first (by execution_position) and vote for buy/hold/sell.
# Post-decision strategies run after the final decision (by execution_position) for order size, pricing, etc.
# Run-once strategies (execution_scope "run_once") run once per loop and return symbol -> score; merged into vote for symbols in the trading list.
_pre_decision_specs = []
_post_decision_specs = []
_run_once_specs = []
_per_symbol_specs = []

# Strategy cache: per-strategy dict for strategies to persist data across runs (e.g. trained models).
# Key: strategy name (e.g. "Candles"), value: dict that the strategy can read/write.
_strategy_cache = {}


def _get_reserved_capital_pct(spec):
    """Return 0-1 fraction of portfolio that this strategy reserves (e.g. Earnings capital_pct). Other strategies cannot use this."""
    if not spec or not isinstance(spec, dict):
        return 0.0
    pct = spec.get("reserved_capital_pct")
    if pct is not None:
        try:
            return max(0.0, min(1.0, float(pct)))
        except (TypeError, ValueError):
            pass
    pct = (spec.get("config") or {}).get("capital_pct")
    if pct is not None:
        try:
            return max(0.0, min(1.0, float(pct)))
        except (TypeError, ValueError):
            pass
    return 0.0


def _fetch_price_for_symbol(symbol: str, current_time, key=None, secret=None, feed=None,
                              allow_non_alpaca_fallback: bool = False) -> float | None:
    """Fetch a symbol price at/before current_time via Alpaca Data API (no future-in-day leakage).

    2026-04-23: ``feed`` kwarg threaded from BrokerageAccounts.alpaca_data_feed.

    2026-04-23: ``allow_non_alpaca_fallback`` — when True and all 5 Alpaca
    tiers return no price (401 on restricted symbols, empty response, or
    network failure), try yfinance then Robinhood public API before
    returning None. LIVE callers pass True so 401'd symbols don't get
    silently discarded. BACKTEST callers leave False (default) so
    historical price reproducibility stays Alpaca-pure.
    """
    if not symbol or not isinstance(symbol, str):
        return None
    key = key or os.environ.get("KEY", "")
    secret = secret or os.environ.get("SECRET", "")
    if not key or not secret:
        return None
    sym = symbol.upper()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json"}
    try:
        import datetime as _dt
        import requests

        ct_utc = _current_time_to_utc(current_time)
        if ct_utc is None:
            return None

        if feed is None:
            feed = os.environ.get("ALPACA_DATA_FEED", "iex")
        feed = (feed or "iex").strip().lower() or "iex"
        if feed not in ("iex", "sip"):
            feed = "iex"
        day_start = _dt.datetime(ct_utc.year, ct_utc.month, ct_utc.day)
        cutoff_end = ct_utc + _dt.timedelta(seconds=1)  # end is exclusive
        if cutoff_end <= day_start:
            cutoff_end = day_start + _dt.timedelta(seconds=1)
        start_iso = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff_iso = cutoff_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Live-readiness Task F: track resolved bar age across all fall-through
        # tiers so operators see a yellow STALE TIER warning when the resolver
        # ultimately returns a value older than LIVE_MAX_BAR_STALE_SECONDS.
        # Tier 1 (1Min bars) already drops stale and falls through; tiers 2-5
        # previously returned silently regardless of age, so 16/60 symbols on
        # 2026-04-30 traded on 1.6-3.4h-old prices unnoticed. We do NOT drop
        # the symbol (drop risks div-by-zero in allocation normalization); the
        # existing single-position / price-sanity guards downstream catch
        # genuinely bad prices.
        _resolved_age_s = None
        _resolved_tier_name = ""
        try:
            _live_max_stale_f = float(os.environ.get("LIVE_MAX_BAR_STALE_SECONDS", "600") or "600")
        except (TypeError, ValueError):
            _live_max_stale_f = 600.0
        _now_utc_resolver = _dt.datetime.now(_dt.timezone.utc)

        def _parse_alpaca_ts(_ts):
            """Parse an Alpaca ISO-8601 timestamp ('...Z') into a UTC datetime."""
            try:
                if _ts is None:
                    return None
                _s = str(_ts).rstrip("Z")
                _d = _dt.datetime.fromisoformat(_s)
                if _d.tzinfo is None:
                    _d = _d.replace(tzinfo=_dt.timezone.utc)
                return _d
            except (TypeError, ValueError):
                return None

        def _set_resolved_age_from_ts(_ts, _tier_name):
            """Compute and stash bar age (seconds) and tier label for the cap log."""
            nonlocal _resolved_age_s, _resolved_tier_name
            _bar_dt = _parse_alpaca_ts(_ts)
            if _bar_dt is None:
                return
            try:
                _resolved_age_s = (_now_utc_resolver - _bar_dt).total_seconds()
                _resolved_tier_name = _tier_name
            except Exception:
                _resolved_age_s = None

        def _maybe_log_stale_tier():
            """Yellow warning when the resolved value's age exceeds the cap."""
            if _resolved_age_s is None:
                return
            if _live_max_stale_f <= 0:
                return
            if _resolved_age_s <= _live_max_stale_f:
                return
            if mode != MODE_LIVE:
                return
            try:
                _log(
                    f"STALE TIER {sym}: tier={_resolved_tier_name} "
                    f"age={_resolved_age_s:.0f}s > max_stale {_live_max_stale_f:.0f}s "
                    f"— proceeding but flagged",
                    "yellow",
                )
            except NameError:
                pass

        def _req_json(url: str, params: dict, timeout: int = 15):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=timeout)
                if resp.status_code != 200:
                    return None
                return resp.json() or {}
            except Exception:
                return None

        # 1) Preferred: intraday bars up to current_time (backtest-safe).
        bars_data = _req_json(
            f"{ALPACA_DATA_BASE}/stocks/{sym}/bars",
            {"start": start_iso, "end": cutoff_iso, "timeframe": "1Min", "limit": 10000, "feed": feed, "sort": "asc"},
            timeout=20,
        )
        bars = (bars_data or {}).get("bars") or []
        if bars:
            last = bars[-1]
            if isinstance(last, dict) and last.get("c") is not None:
                # Live-readiness P0 #3: stale-bar age check. In LIVE mode reject
                # bars older than `max_stale_seconds` (default 600s = 10min). A
                # frozen feed (IEX gap, halted ticker, network partition serving
                # cached value) used to silently trade on stale price. Now we
                # fall through to next tier and ultimately fail-loud.
                _is_stale = False
                if mode == MODE_LIVE:
                    try:
                        _max_stale = int(os.environ.get("LIVE_MAX_BAR_STALE_SECONDS", "600") or "600")
                    except (ValueError, TypeError):
                        _max_stale = 600
                    _bar_t = last.get("t") or ""
                    if _bar_t and _max_stale > 0:
                        try:
                            # Alpaca bar timestamps are ISO-8601 UTC. Strip 'Z' if present.
                            _bts = str(_bar_t).rstrip("Z")
                            _bar_dt = _dt.datetime.fromisoformat(_bts)
                            if _bar_dt.tzinfo is None:
                                _bar_dt = _bar_dt.replace(tzinfo=_dt.timezone.utc)
                            _wall_utc = _dt.datetime.now(_dt.timezone.utc)
                            _age_s = (_wall_utc - _bar_dt).total_seconds()
                            if _age_s > _max_stale:
                                _is_stale = True
                                try:
                                    _log(f"STALE BAR: {sym} bar age {_age_s:.0f}s > {_max_stale}s — falling through to next tier", "yellow")
                                except NameError:
                                    pass
                        except (ValueError, TypeError):
                            pass
                if not _is_stale:
                    try:
                        p = float(last.get("c"))
                        if p > 0:
                            _set_resolved_age_from_ts(last.get("t"), "intraday-1min")
                            _maybe_log_stale_tier()
                            return p
                    except (TypeError, ValueError):
                        pass

        # 2) Fallback: previous day's daily bar (works before market open).
        #    Use start_iso (midnight today) as end to exclude today's bar — its close would be forward-looking.
        prev_day = (day_start - _dt.timedelta(days=3))  # 3 days back to handle weekends
        prev_day_iso = prev_day.strftime("%Y-%m-%dT%H:%M:%SZ")
        daily_data = _req_json(
            f"{ALPACA_DATA_BASE}/stocks/{sym}/bars",
            {"start": prev_day_iso, "end": start_iso, "timeframe": "1Day", "limit": 5, "feed": feed, "sort": "desc"},
            timeout=15,
        )
        daily_bars = (daily_data or {}).get("bars") or []
        if daily_bars:
            last = daily_bars[0]
            if isinstance(last, dict) and last.get("c") is not None:
                try:
                    p = float(last.get("c"))
                    if p > 0:
                        _set_resolved_age_from_ts(last.get("t"), "daily-prev")
                        _maybe_log_stale_tier()
                        return p
                except (TypeError, ValueError):
                    pass

        # 3) Fallback: most recent historical trade up to current_time.
        trades_data = _req_json(
            f"{ALPACA_DATA_BASE}/stocks/trades",
            {"symbols": sym, "start": start_iso, "end": cutoff_iso, "limit": 1, "sort": "desc", "feed": feed},
            timeout=20,
        )
        trades_by_sym = (trades_data or {}).get("trades") or {}
        sym_trades = trades_by_sym.get(sym) if isinstance(trades_by_sym, dict) else None
        if isinstance(sym_trades, list) and sym_trades:
            t0 = sym_trades[0]
            if isinstance(t0, dict) and t0.get("p") is not None:
                try:
                    p = float(t0.get("p"))
                    if p > 0:
                        _set_resolved_age_from_ts(t0.get("t"), "historical-trade")
                        _maybe_log_stale_tier()
                        return p
                except (TypeError, ValueError):
                    pass

        # 4) Fallback: most recent historical quote up to current_time.
        quotes_data = _req_json(
            f"{ALPACA_DATA_BASE}/stocks/quotes",
            {"symbols": sym, "start": start_iso, "end": cutoff_iso, "limit": 1, "sort": "desc", "feed": feed},
            timeout=20,
        )
        quotes_by_sym = (quotes_data or {}).get("quotes") or {}
        q = quotes_by_sym.get(sym) if isinstance(quotes_by_sym, dict) else None
        if isinstance(q, list) and q:
            q = q[0]
        if isinstance(q, dict):
            ap, bp = q.get("ap"), q.get("bp")
            try:
                if ap is not None and float(ap) > 0 and bp is not None and float(bp) > 0:
                    _set_resolved_age_from_ts(q.get("t"), "historical-quote")
                    _maybe_log_stale_tier()
                    return float((float(ap) + float(bp)) / 2.0)
                if ap is not None and float(ap) > 0:
                    _set_resolved_age_from_ts(q.get("t"), "historical-quote")
                    _maybe_log_stale_tier()
                    return float(ap)
                if bp is not None and float(bp) > 0:
                    _set_resolved_age_from_ts(q.get("t"), "historical-quote")
                    _maybe_log_stale_tier()
                    return float(bp)
            except (TypeError, ValueError):
                pass

        # 5) Near-real-time safety valve for "now" only.
        now_utc = _dt.datetime.utcnow()
        if ct_utc.date() == now_utc.date() and abs((now_utc - ct_utc).total_seconds()) <= 600:
            latest_q = _req_json(f"{ALPACA_DATA_BASE}/stocks/quotes/latest", {"symbols": sym}, timeout=10)
            q_latest = (latest_q or {}).get("quotes", {}).get(sym) if isinstance((latest_q or {}).get("quotes"), dict) else None
            if isinstance(q_latest, dict):
                ap, bp = q_latest.get("ap"), q_latest.get("bp")
                try:
                    if ap is not None and float(ap) > 0 and bp is not None and float(bp) > 0:
                        _set_resolved_age_from_ts(q_latest.get("t"), "latest-quote")
                        _maybe_log_stale_tier()
                        return float((float(ap) + float(bp)) / 2.0)
                    if ap is not None and float(ap) > 0:
                        _set_resolved_age_from_ts(q_latest.get("t"), "latest-quote")
                        _maybe_log_stale_tier()
                        return float(ap)
                    if bp is not None and float(bp) > 0:
                        _set_resolved_age_from_ts(q_latest.get("t"), "latest-quote")
                        _maybe_log_stale_tier()
                        return float(bp)
                except (TypeError, ValueError):
                    pass
            latest_t = _req_json(f"{ALPACA_DATA_BASE}/stocks/trades/latest", {"symbols": sym}, timeout=10)
            t_latest = (latest_t or {}).get("trades", {}).get(sym) if isinstance((latest_t or {}).get("trades"), dict) else None
            if isinstance(t_latest, dict) and t_latest.get("p") is not None:
                try:
                    p = float(t_latest.get("p"))
                    if p > 0:
                        _set_resolved_age_from_ts(t_latest.get("t"), "latest-trade")
                        _maybe_log_stale_tier()
                        return p
                except (TypeError, ValueError):
                    pass
        # 2026-04-23: explicit non-Alpaca fallback for live callers. Alpaca
        # 401s on restricted symbols (e.g. SIP-only, delisted-IEX, free-tier
        # edge cases) were silently discarding tickers from Nexus expansion
        # buys / discovery / sell-enforcement. Callers that pass
        # ``allow_non_alpaca_fallback=True`` get yfinance → Robinhood fallback
        # so no ticker is silently dropped.
        if allow_non_alpaca_fallback:
            # 2026-05-03 live-hang investigation: yfinance (curl_cffi gzip
            # hangs on rate-limit) and Robinhood public get_price_history
            # have no enforceable client-side timeout. Submit to a long-lived
            # module-level executor (_PRICE_FETCH_EXECUTOR) and wait at most
            # 15s for the result. A wedged HTTP read leaks one thread (bounded
            # by the executor's max_workers=4) but never blocks the live tick.
            try:
                _yp_fut = _PRICE_FETCH_EXECUTOR.submit(
                    _fetch_price_yfinance, symbol, current_time
                )
                try:
                    _yp = _yp_fut.result(timeout=15)
                except _live_cf.TimeoutError:
                    try:
                        _log(f"_fetch_price_yfinance({symbol}) watchdog timeout (>15s); leaving worker to finish in background", "yellow")
                    except Exception:
                        pass
                    _yp = None
                if _yp and _yp > 0:
                    return _yp
            except Exception:
                pass
            # Robinhood data fallback is gated: only when Robinhood is the trading
            # broker (a non-RH instance, e.g. Alpaca, must NEVER call Robinhood from
            # the server IP). yfinance above still runs for every caller.
            if not robinhood_data_fallback_allowed(live_broker_type):
                return None
            try:
                from robinhood_engine import get_price_history as _rh_ph
                import datetime as _dt2
                import math as _math2
                ct_utc2 = _current_time_to_utc(current_time)
                if ct_utc2 is not None:
                    _target_date = ct_utc2.date()
                    _rh_fut = _PRICE_FETCH_EXECUTOR.submit(_rh_ph, symbol, "day", "year")
                    try:
                        df = _rh_fut.result(timeout=15)
                    except _live_cf.TimeoutError:
                        try:
                            _log(f"robinhood get_price_history({symbol}) watchdog timeout (>15s); leaving worker to finish in background", "yellow")
                        except Exception:
                            pass
                        df = None
                    if df is not None and not df.empty:
                        for _i in reversed(range(len(df))):
                            _row_date = df.index[_i]
                            if hasattr(_row_date, "date"):
                                _row_date = _row_date.date()
                            if _row_date <= _target_date:
                                try:
                                    _c = float(df["close_price"].iloc[_i]) if "close_price" in df.columns else float(df["close"].iloc[_i])
                                    if _c > 0 and not _math2.isnan(_c):
                                        return _c
                                except (TypeError, ValueError, KeyError):
                                    pass
                                break
            except Exception:
                pass
        return None
    except Exception:
        return None


def _fetch_price_yfinance(symbol: str, current_time) -> float | None:
    """Fallback price fetch via yfinance for the date of current_time (most recent close ≤ target date)."""
    try:
        import yfinance as yf
        import datetime as _dt
        import math as _math
        ct_utc = _current_time_to_utc(current_time)
        if ct_utc is None:
            return None
        target_date = ct_utc.date()
        start_date = (target_date - _dt.timedelta(days=7)).isoformat()
        end_date = (target_date + _dt.timedelta(days=1)).isoformat()
        hist = yf.download(symbol, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=True)
        if hist is None or hist.empty:
            return None
        for i in reversed(range(len(hist))):
            row_date = hist.index[i]
            if hasattr(row_date, "date"):
                row_date = row_date.date()
            if row_date < target_date:  # strict < to avoid same-day close (forward-looking in intraday backtests)
                try:
                    close = float(hist["Close"].iloc[i])
                    if close > 0 and not _math.isnan(close):
                        return close
                except (TypeError, ValueError):
                    pass
        return None
    except Exception:
        return None


def _fetch_price_with_fallback(symbol: str, current_time, key: str = "", secret: str = "", log_fn=None, feed: str | None = None) -> float | None:
    """Try Alpaca 3 times (with 1 s backoff), then fall back to yfinance. Returns price or None.

    2026-04-23 bug-sweep: accept ``feed`` so callers can route through the
    user-selected bars feed instead of always using env-var default.
    """
    import time as _time
    price = None
    for attempt in range(3):
        price = _fetch_price_for_symbol(symbol, current_time, key=key, secret=secret, feed=feed)
        if price and price > 0:
            return price
        if attempt < 2:
            _time.sleep(1)
    # Alpaca failed all 3 attempts — try yfinance
    price = _fetch_price_yfinance(symbol, current_time)
    if price and price > 0:
        if log_fn:
            log_fn("[Pending] Fetched price %s=%.2f via yfinance fallback" % (symbol, price), "cyan")
        return price
    # yfinance also failed — try Robinhood public API (no auth needed) — but only
    # when Robinhood is the trading broker (a non-RH instance must not call
    # Robinhood from the server IP).
    if not robinhood_data_fallback_allowed(live_broker_type):
        return None
    try:
        import datetime as _dt
        import math as _math
        from robinhood_engine import get_price_history as _rh_price_history
        ct_utc = _current_time_to_utc(current_time)
        if ct_utc is not None:
            target_date = ct_utc.date()
            df = _rh_price_history(symbol, interval="day", span="week")
            if df is not None and not df.empty:
                for i in reversed(range(len(df))):
                    row_date = df.index[i]
                    if hasattr(row_date, "date"):
                        row_date = row_date.date()
                    if row_date < target_date:  # strict < to avoid same-day close (forward-looking in intraday backtests)
                        close = float(df["Close"].iloc[i])
                        if close > 0 and not _math.isnan(close):
                            if log_fn:
                                log_fn("[Pending] Fetched price %s=%.2f via Robinhood fallback" % (symbol, close), "cyan")
                            return close
    except Exception:
        pass
    return None


def _ensure_prices_include_positions(portfolio_emulator, prices, current_time, data=None, symbols=None, key=None, secret=None):
    """
    Ensure prices dict includes every symbol we have a position in (so portfolio value is correct).
    Missing positions are valued at 0 by get_portfolio_value, which would understate P&L.

    Resolution order per missing ticker:
      1. Pre-existing ``prices[sym]`` if > 0
      2. Backtest ``data`` bar lookup (backtest only)
      3. Adapter's ``_last_prices`` cache (populated on fills + WAL reconcile)
      4. Alpaca via ``_fetch_price_for_symbol``
      5. yfinance fallback via ``_fetch_price_with_fallback`` (last-close, ~15min delay)

    Previously only steps 1/2/4 were tried, so a live instance that had
    positions hydrated from Alpaca's REST (via ``_seed_trades_from_broker``)
    but hadn't yet received a WebSocket trade_update would see cp=$0 on
    every held ticker and the risk pipeline would bypass all checks.
    Modifies prices in place; returns prices.
    """
    if not portfolio_emulator:
        return prices
    positions = portfolio_emulator.get_positions() or {}
    if not positions:
        return prices
    prices = prices if isinstance(prices, dict) else {}
    key = key or os.environ.get("KEY", "")
    secret = secret or os.environ.get("SECRET", "")
    _last_prices_cache = getattr(portfolio_emulator, "_last_prices", {}) or {}
    # Task 4 (July 10 incident): in LIVE mode typed timestamped marks are the
    # first authority. The untimestamped ``_last_prices`` scalar cache is
    # demoted to a valuation-only fallback AFTER an outbound fresh fetch —
    # it must never be treated as fresh live data (it can be a fill price).
    _live_marks = {}
    if mode == MODE_LIVE:
        try:
            _get_marks = getattr(portfolio_emulator, "get_market_marks", None)
            _live_marks = _get_marks() if callable(_get_marks) else {}
        except Exception:
            _live_marks = {}
    for sym in positions:
        if sym in prices and prices.get(sym) is not None and float(prices.get(sym) or 0) > 0:
            continue
        p = None
        if data and (data.get(sym) or []):
            one = _get_prices_at_time(data, [sym], current_time)
            p = one.get(sym)
        # LIVE: fresh typed mark (quote/trade/broker-position within the
        # 120s broker-fallback SLA; execution-only fill marks excluded).
        if (p is None or p <= 0) and _live_marks:
            try:
                _mk = _live_marks.get(sym)
                if _mk is not None and getattr(_mk.quality, "value", "") != "execution_only" \
                        and _mk.age_seconds(datetime.datetime.now(datetime.timezone.utc)) <= 120:
                    p = float(_mk.price)
            except Exception:
                p = None
        # BACKTEST only: adapter cache before outbound HTTP (bar-derived and
        # deterministic there). LIVE skips this — see valuation fallback below.
        if (p is None or p <= 0) and _last_prices_cache and mode != MODE_LIVE:
            try:
                p = float(_last_prices_cache.get(sym) or 0) or None
            except Exception:
                p = None
        if p is None or p <= 0:
            p = _fetch_price_for_symbol(
                sym, current_time, key=key, secret=secret, feed=data_feed,
                allow_non_alpaca_fallback=(mode == MODE_LIVE),
            )
        # LIVE valuation-only fallback: stale scalar beats a $0 mark for
        # portfolio valuation, but it can never authorize an exposure
        # increase (decision_price() fails closed independently of this).
        if (p is None or p <= 0) and _last_prices_cache and mode == MODE_LIVE:
            try:
                p = float(_last_prices_cache.get(sym) or 0) or None
            except Exception:
                p = None
        # yfinance fallback for free-tier / rate-limited / delisted-feed cases.
        # ~15-minute delay vs Alpaca but better than $0 and a bypassed risk gate.
        if p is None or p <= 0:
            try:
                p = _fetch_price_with_fallback(sym, current_time, key=key, secret=secret, log_fn=None, feed=data_feed)
            except Exception:
                p = None
        if p is not None and p > 0:
            prices[sym] = p
            # Mirror into the adapter cache so subsequent calls resolve
            # without re-hitting yfinance.
            try:
                if hasattr(portfolio_emulator, "_last_prices") and portfolio_emulator._last_prices is not None:
                    portfolio_emulator._last_prices[sym] = float(p)
            except Exception:
                pass
    return prices


def _residual_sleeve_config(cached_strategies):
    """P&L sweep 2026-07-19: config for the residual SPY sleeve (idle cash
    parks in a broad ETF instead of dragging at 0% — the 3-regime forensics
    measured 28% avg idle cash costing −3.8pp in a +13.6% SPY window).
    Config-gated on the nexus strategy spec; DEFAULT OFF (legacy behavior
    byte-identical when disabled)."""
    for spec in (cached_strategies or []):
        cfg = (spec or {}).get("config") or {}
        if str((spec or {}).get("strategy") or "") == "graph_nexus_analysis" \
                or "residual_sleeve_enabled" in cfg:
            return {
                "enabled": bool(cfg.get("residual_sleeve_enabled", False)),
                "symbol": str(cfg.get("residual_sleeve_symbol", "SPY")).upper(),
                "buffer_pct": float(cfg.get("residual_sleeve_buffer_pct", 0.02) or 0.02),
                "min_deploy_pct": float(cfg.get("residual_sleeve_min_deploy_pct", 0.05) or 0.05),
                "release_cash_pct": float(cfg.get("residual_sleeve_release_cash_pct", 0.15) or 0.15),
                "min_park_hours": float(cfg.get("residual_sleeve_min_park_hours", 24.0) or 24.0),
                # 2026-07-19 bear leg: park into an inverse ETF during
                # CONFIRMED bear/crash so the sleeve earns the downtrend
                # instead of hiding in cash. "" = disabled (default).
                "bear_symbol": str(cfg.get("residual_sleeve_bear_symbol", "") or "").upper(),
                "bear_alloc_pct": float(cfg.get("residual_sleeve_bear_alloc_pct", 0.35) or 0.35),
                # Scenario-sim E (V-bottom): the regime exit lags the bottom
                # by up to 9 trading days; a leg-level stop (default -10% ≈ a
                # 3.3% market rally on a 3x inverse) exits on rally day ~2.
                "bear_stop_loss_pct": float(cfg.get("residual_sleeve_bear_stop_loss_pct", 10.0) or 10.0),
            }
    return {"enabled": False}


# 2026-07-19 sleeve-hysteresis fix: sim-clock timestamp of the last park so
# release can enforce a min-park duration (per-process; resets per backtest).
# bear_entry_px = weighted avg entry of the current bear-leg episode (for the
# leg stop-loss); last_bear_exit_ts throttles re-entry after any bear-leg exit.
_RESIDUAL_SLEEVE_STATE: dict = {"last_park_ts": None, "bear_entry_px": None,
                                "last_bear_exit_ts": None}


def _sleeve_circuit_tier():
    """Current drawdown-circuit tier from the nexus strategy cache ('' when
    none). Sleeve deploy must not add exposure under hard/kill."""
    try:
        cache = (globals().get("_strategy_cache") or {}).get(
            "graph_nexus_analysis") or {}
        return str(((cache.get("_portfolio_drawdown_state") or {}).get("circuit_tier")) or "")
    except Exception:
        return ""


def _regime_position_cap_hard(cached_strategies):
    """(regime, cap) for the airtight execution-time regime position cap, or
    None when disabled, no nexus spec, or no regime stamped yet.

    BEAR_F6 (2026-07-19) proved the strategy-side Z4.1 capacity gate is
    porous: the backfill-queue drain, momentum-watchlist and direct-reserved
    lanes kept buying with the book at 9-11 names against a bear cap of 2.
    Every lane's buys pass through the broker execution gate, so the cap is
    re-enforced there via this helper. Mirrors Z4.1's cap table defaults.
    """
    try:
        for spec in (cached_strategies or []):
            cfg = (spec or {}).get("config") or {}
            if str((spec or {}).get("strategy") or "") == "graph_nexus_analysis":
                if not bool(cfg.get("regime_position_cap_hard_enforce", True)):
                    return None
                regime = _sleeve_market_regime()
                if regime not in ("bull", "chop", "bear", "crash"):
                    return None  # no regime stamped yet — legacy behavior
                mx = int(cfg.get("max_positions", 15) or 15)
                caps = {
                    "bull": int(cfg.get("max_positions_bull", mx) or mx),
                    "chop": int(cfg.get("max_positions_chop", min(mx, 12)) or 12),
                    "bear": int(cfg.get("max_positions_bear", min(mx, 8)) or 8),
                    "crash": int(cfg.get("max_positions_crash", 0) or 0),
                }
                return regime, caps[regime]
        return None
    except Exception:
        return None


def _sleeve_market_regime():
    """Current V31 regime from the nexus strategy cache (bull/chop/bear/crash;
    '' when unavailable — treated as NOT bull, conservative)."""
    try:
        cache = (globals().get("_strategy_cache") or {}).get(
            "graph_nexus_analysis") or {}
        return str(cache.get("_market_regime") or "").strip().lower()
    except Exception:
        return ""


def _residual_sleeve_prepare(data, prices, current_time, cached_strategies,
                             key=None, secret=None):
    """Make the sleeve symbol priceable this bar. Pure-discovery backtests
    skip the bulk symbol fetch, so the sleeve symbol must ride the SAME
    incremental loader discovered symbols use; live mode falls back to a
    direct price fetch. Without this the sleeve silently no-ops (BEAR_F2:
    '[sleeve] ENABLED but no SPY price')."""
    try:
        cfg = _residual_sleeve_config(cached_strategies)
        if not cfg["enabled"]:
            return
        _sleeve_syms = [s for s in (cfg["symbol"], cfg.get("bear_symbol") or "")
                        if s and not (prices or {}).get(s)]
        for sym in _sleeve_syms:
            if mode == MODE_BACKTEST and isinstance(data, dict):
                if sym not in data:
                    _ensure_backtest_history_for_symbols(
                        data, [sym], key=key, secret=secret)
                if data.get(sym):
                    p = (_get_prices_at_time(data, [sym], current_time) or {}).get(sym)
                    if p and float(p) > 0 and isinstance(prices, dict):
                        prices[sym] = float(p)
            elif mode == MODE_LIVE and isinstance(prices, dict):
                p = _fetch_price_for_symbol(
                    sym, current_time, key=key, secret=secret, feed=data_feed,
                    allow_non_alpaca_fallback=True)
                if p and float(p) > 0:
                    prices[sym] = float(p)
    except Exception as _sp_exc:
        try:
            _log(f"[sleeve] prepare skipped: {_sp_exc}", "yellow")
        except Exception:
            pass


def _residual_sleeve_release(portfolio_emulator, prices, current_time, cached_strategies):
    """Cycle start: free the sleeve for active picks when cash is low, and
    ALWAYS liquidate it when the regime turns bear/crash (BEAR_F guardrail
    failure 2026-07-19: an exit-less sleeve rode SPY down −8%, flipping the
    bear window from +0.87% to −7.77%). The trend-conditioned regime gate —
    not a drawdown gate — is what the measured counterfactuals prescribe."""
    try:
        cfg = _residual_sleeve_config(cached_strategies)
        if not cfg["enabled"] or portfolio_emulator is None:
            return
        regime = _sleeve_market_regime()
        # ── Bear leg (inverse ETF): auto-sell the moment the bear is over.
        # Runs FIRST — before any SPY-leg early return — and is an
        # unconditional protective exit on upgrade to chop/bull, ignoring
        # the min-park duration and cash thresholds by design.
        bsym = cfg.get("bear_symbol") or ""
        if bsym:
            bqty = float((portfolio_emulator.get_positions() or {}).get(bsym, 0.0) or 0.0)
            bpx = float((prices or {}).get(bsym) or 0.0)
            _bear_exit_why = None
            if bqty > 0 and regime not in ("bear", "crash"):
                _bear_exit_why = f"bear over: regime={regime or 'unknown'} protective exit"
            elif bqty > 0 and bpx > 0:
                # Leg stop-loss (scenario-sim E): the regime exit lags a
                # V-bottom by up to 9 trading days; a -10% leg stop (~3.3%
                # market rally on 3x) exits on rally day ~2 instead.
                _bentry = _RESIDUAL_SLEEVE_STATE.get("bear_entry_px")
                _bstop = float(cfg.get("bear_stop_loss_pct", 10.0) or 10.0)
                if _bentry and _bstop > 0 and bpx <= float(_bentry) * (1.0 - _bstop / 100.0):
                    _bear_exit_why = (f"leg stop-loss: {bpx:.2f} <= "
                                      f"{float(_bentry):.2f} -{_bstop:.0f}%")
                else:
                    # Demand refill (adversarial review MED): in bear the
                    # inverse leg is the only sleeve — when cash cannot fund
                    # the (few) allowed bear slots, partially release it.
                    _bnav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
                    _bcash = float(portfolio_emulator.get_cash() or 0.0)
                    if _bnav > 0 and _bcash < cfg["release_cash_pct"] * _bnav:
                        _bneeded = max(0.0, cfg["release_cash_pct"] * _bnav - _bcash)
                        _bsell_qty = min(bqty, _bneeded / bpx)
                        if _bsell_qty > 0:
                            _bfrac = min(1.0, _bsell_qty / bqty)
                            _bok = portfolio_emulator.execute_signal(
                                bsym, -1, bpx, timestamp=current_time,
                                sell_fraction=_bfrac)
                            _log(f"[sleeve] released {_bsell_qty:.4f} {bsym} @ {bpx:.2f} "
                                 f"(bear-leg refill: cash {_bcash / _bnav * 100.0:.1f}% of NAV, "
                                 f"ok={_bok})", "cyan")
            if _bear_exit_why and bqty > 0:
                if bpx > 0:
                    bok = portfolio_emulator.execute_signal(
                        bsym, -1, bpx, timestamp=current_time, sell_fraction=1.0)
                    if bok:
                        _RESIDUAL_SLEEVE_STATE["bear_entry_px"] = None
                        _RESIDUAL_SLEEVE_STATE["last_bear_exit_ts"] = current_time
                    _log(f"[sleeve] released {bqty:.4f} {bsym} @ {bpx:.2f} "
                         f"({_bear_exit_why}, ok={bok})", "cyan")
                else:
                    _log(f"[sleeve] bear leg {bsym} needs exit ({_bear_exit_why}) "
                         "but no price this bar — retrying next bar", "yellow")
        sym = cfg["symbol"]
        qty = float((portfolio_emulator.get_positions() or {}).get(sym, 0.0) or 0.0)
        if qty <= 0:
            return
        px = float((prices or {}).get(sym) or 0.0)
        if px <= 0:
            _log(f"[sleeve] no {sym} price this bar — holding sleeve", "yellow")
            return
        nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
        cash = float(portfolio_emulator.get_cash() or 0.0)
        protective = regime in ("bear", "crash")
        if not protective and (nav <= 0 or cash >= cfg["release_cash_pct"] * nav):
            return  # enough cash for active buys; keep the sleeve invested
        if not protective:
            # 2026-07-19 sleeve-hysteresis fix: (a) honor the min-park
            # duration so a fresh park cannot round-trip on the next cycle;
            # (b) release only enough to refill the dry-powder target, not
            # the whole sleeve (BEAR_F3 logged ~53 full park/release
            # round-trips per month — free in the fee model, a real
            # slippage bleed live).
            _last_park = _RESIDUAL_SLEEVE_STATE.get("last_park_ts")
            if _last_park is not None and current_time is not None:
                try:
                    _held_hours = (current_time - _last_park).total_seconds() / 3600.0
                    if _held_hours < cfg["min_park_hours"]:
                        return
                except (TypeError, AttributeError):
                    pass
            needed = max(0.0, cfg["release_cash_pct"] * nav - cash)
            sell_qty = min(qty, needed / px) if px > 0 else qty
            if sell_qty <= 0:
                return
            frac = min(1.0, sell_qty / qty) if qty > 0 else 1.0
        else:
            sell_qty = qty
            frac = 1.0
        ok = portfolio_emulator.execute_signal(
            sym, -1, px, timestamp=current_time, sell_fraction=frac)
        why = f"regime={regime} protective exit" if protective else (
            f"cash was {cash / nav * 100.0:.1f}% of NAV, refill "
            f"{sell_qty:.4f}/{qty:.4f}")
        _log(f"[sleeve] released {sell_qty:.4f} {sym} @ {px:.2f} ({why}, ok={ok})",
             "cyan")
    except Exception as _sleeve_exc:
        try:
            _log(f"[sleeve] release skipped: {_sleeve_exc}", "yellow")
        except Exception:
            pass


def _residual_sleeve_deploy(portfolio_emulator, prices, current_time, cached_strategies):
    """Cycle end: park idle cash above the operational buffer into the sleeve
    symbol. Delta-buy only; deploys only when idle exceeds the hysteresis
    threshold so a post-release remainder does not immediately round-trip."""
    try:
        cfg = _residual_sleeve_config(cached_strategies)
        if not cfg["enabled"] or portfolio_emulator is None:
            return
        # BEAR_F guardrail fix (2026-07-19): deploy the SPY leg only in a
        # confirmed bull. 2026-07-19 bear leg: in confirmed bear/crash park
        # into the inverse ETF instead (capped at bear_alloc_pct of NAV) so
        # the sleeve earns the downtrend. Chop and cold starts stay in cash.
        regime = _sleeve_market_regime()
        # Adversarial review CRITICAL follow-up: never ADD sleeve exposure
        # while the drawdown circuit is at hard/kill.
        if _sleeve_circuit_tier() in ("hard", "kill"):
            return
        if regime in ("bear", "crash") and cfg.get("bear_symbol"):
            bsym = cfg["bear_symbol"]
            # Re-entry dwell after any bear-leg exit (stop-loss/protective):
            # prevents same-day sell/rebuy churn (PDT-class live risk).
            _lbx = _RESIDUAL_SLEEVE_STATE.get("last_bear_exit_ts")
            if _lbx is not None and current_time is not None:
                try:
                    if (current_time - _lbx).total_seconds() / 3600.0 < cfg["min_park_hours"]:
                        return
                except (TypeError, AttributeError):
                    pass
            bpx = float((prices or {}).get(bsym) or 0.0)
            if bpx <= 0:
                if not globals().get("_sleeve_bear_no_price_logged"):
                    globals()["_sleeve_bear_no_price_logged"] = True
                    _log(f"[sleeve] bear leg ENABLED but no {bsym} price available — "
                         "bear leg is inert (is the symbol in the bar universe?)", "red")
                return
            nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
            if nav <= 0:
                return
            cash = float(portfolio_emulator.get_cash() or 0.0)
            park_floor_pct = max(cfg["buffer_pct"],
                                 cfg["release_cash_pct"] + cfg["buffer_pct"])
            idle = cash - park_floor_pct * nav
            cur_val = float((portfolio_emulator.get_positions() or {}).get(bsym, 0.0) or 0.0) * bpx
            room = max(0.0, cfg["bear_alloc_pct"] * nav - cur_val)
            deploy = min(idle, room)
            if deploy < max(50.0, cfg["min_deploy_pct"] * nav):
                return
            bok = portfolio_emulator.execute_signal(
                bsym, 1, bpx, timestamp=current_time, cash_per_trade=deploy)
            if bok:
                _RESIDUAL_SLEEVE_STATE["last_park_ts"] = current_time
                # Weighted avg entry for the leg stop-loss.
                _prev_entry = float(_RESIDUAL_SLEEVE_STATE.get("bear_entry_px") or 0.0)
                _prev_qty = cur_val / bpx if bpx > 0 else 0.0
                _new_qty = deploy / bpx if bpx > 0 else 0.0
                if _prev_entry > 0 and _prev_qty > 0 and (_prev_qty + _new_qty) > 0:
                    _RESIDUAL_SLEEVE_STATE["bear_entry_px"] = (
                        (_prev_qty * _prev_entry + _new_qty * bpx) / (_prev_qty + _new_qty))
                else:
                    _RESIDUAL_SLEEVE_STATE["bear_entry_px"] = bpx
            _log(f"[sleeve] parked ${deploy:.2f} in BEAR leg {bsym} @ {bpx:.2f} "
                 f"(regime={regime}, leg={cur_val + deploy:.0f}/"
                 f"{cfg['bear_alloc_pct'] * nav:.0f} cap, ok={bok})", "cyan")
            return
        if regime != "bull":
            return
        sym = cfg["symbol"]
        px = float((prices or {}).get(sym) or 0.0)
        if px <= 0:
            # Loud, throttled: a priceless sleeve symbol means the sleeve is
            # inert — that silence hid the BULL_F/BEAR_F no-op entirely.
            if not globals().get("_sleeve_no_price_logged"):
                globals()["_sleeve_no_price_logged"] = True
                _log(f"[sleeve] ENABLED but no {sym} price available — sleeve "
                     "is inert (is the symbol in the bar universe?)", "red")
            return
        nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
        if nav <= 0:
            return
        cash = float(portfolio_emulator.get_cash() or 0.0)
        # 2026-07-19 sleeve-hysteresis fix: park only cash ABOVE the release
        # threshold + buffer. The old floor (buffer alone, 2%) left post-park
        # cash below the 15% release trigger, guaranteeing a full release the
        # very next cycle — a per-bar park/release oscillation.
        park_floor_pct = max(cfg["buffer_pct"],
                             cfg["release_cash_pct"] + cfg["buffer_pct"])
        idle = cash - park_floor_pct * nav
        if idle < max(50.0, cfg["min_deploy_pct"] * nav):
            return
        ok = portfolio_emulator.execute_signal(
            sym, 1, px, timestamp=current_time, cash_per_trade=idle)
        if ok:
            _RESIDUAL_SLEEVE_STATE["last_park_ts"] = current_time
        _log(f"[sleeve] parked ${idle:.2f} idle cash in {sym} @ {px:.2f} "
             f"(cash was {cash / nav * 100.0:.1f}% of NAV, ok={ok})", "cyan")
    except Exception as _sleeve_exc:
        try:
            _log(f"[sleeve] deploy skipped: {_sleeve_exc}", "yellow")
        except Exception:
            pass


def watch_strategies_changefeed():
    """Watch Instances table for strategy_id changes and Strategies table for strategy config changes. Exit broker when changes detected."""
    global shutdown_requested, instance_id
    if not instance_id:
        return
    import threading
    try:
        # instance_id can be int or string (e.g. "ai-temp-xxx" or numeric); use as-is for DB .get()
        conn = get_conn()
        try:
            instance_doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
            strategy_id = instance_doc.get('strategy_id') if instance_doc else None
        finally:
            conn.close()
        _log(f"Watching Instances[{instance_id}] and Strategies[{strategy_id}] for changes...", "white")
        # Watch instance for strategy_id changes
        def watch_instance():
            # Self-heal: a RethinkDB blip must not kill this watcher — dying
            # would silently stop reacting to strategy_id changes, and returning
            # on error used to spuriously unblock the join (triggering a reload).
            # Reconnect with backoff on any error; only the detected change exits.
            while True:
                conn_inst = None
                try:
                    conn_inst = get_conn()
                    for change in r.db(DB_NAME).table('Instances').get(instance_id).changes().run(conn_inst):
                        if change.get('old_val') and change.get('new_val'):
                            old_sid = change['old_val'].get('strategy_id')
                            new_sid = change['new_val'].get('strategy_id')
                            if old_sid != new_sid:
                                _log(f"Instance strategy_id changed ({old_sid} -> {new_sid}). Exiting broker to reload...", "yellow")
                                shutdown_requested = True
                                return
                            # 2026-07-18: crypto_config edits must reach the RUNNING
                            # loop. _instance_kind_and_crypto_config caches read-once,
                            # so without invalidation a PATCH (allocations, band,
                            # bear_gate_ma, rebalance_drift, ...) silently did NOTHING
                            # until restart — caught by the end-to-end paper-trade
                            # test (5% BTC allocation never bought). The injected
                            # config is re-read from this cache every tick, so
                            # invalidation alone suffices — EXCEPT a strategy switch,
                            # whose run_once spec was synthesized at boot: that needs
                            # the same exit-to-reload as strategy_id.
                            _old_cc = change['old_val'].get('crypto_config') or {}
                            _new_cc = change['new_val'].get('crypto_config') or {}
                            if _old_cc != _new_cc:
                                if _old_cc.get('strategy') != _new_cc.get('strategy'):
                                    _log(
                                        f"crypto_config.strategy changed ({_old_cc.get('strategy')} -> "
                                        f"{_new_cc.get('strategy')}). Exiting broker to reload...",
                                        "yellow",
                                    )
                                    shutdown_requested = True
                                    return
                                try:
                                    _INSTANCE_KIND_CACHE["loaded"] = False
                                except Exception:
                                    pass
                                _log(
                                    "crypto_config changed — cache invalidated; next tick reads the new config.",
                                    "yellow",
                                )
                except Exception as e:
                    _log(f"Instance changefeed error ({e}); reconnecting...", "yellow")
                finally:
                    if conn_inst is not None:
                        try:
                            conn_inst.close()
                        except Exception:
                            pass
                time.sleep(2)
        # Watch strategy for config changes (if strategy_id exists)
        def watch_strategy():
            if strategy_id is None:
                return
            # Self-heal: reconnect on a RethinkDB blip instead of dying (or
            # spuriously triggering a reload); only a real config change exits.
            while True:
                conn_strat = None
                try:
                    conn_strat = get_conn()
                    for change in r.db(DB_NAME).table('Strategies').get(strategy_id).changes().run(conn_strat):
                        _log("Strategy config changed. Exiting broker to reload...", "yellow")
                        shutdown_requested = True
                        return
                except Exception as e:
                    _log(f"Strategy changefeed error ({e}); reconnecting...", "yellow")
                finally:
                    if conn_strat is not None:
                        try:
                            conn_strat.close()
                        except Exception:
                            pass
                time.sleep(2)
        # Start both watchers in separate threads
        t1 = threading.Thread(target=watch_instance, daemon=True)
        t1.start()
        if strategy_id is not None:
            t2 = threading.Thread(target=watch_strategy, daemon=True)
            t2.start()
            t2.join()  # Wait for either thread to finish (both are daemon)
        t1.join()
    except Exception as e:
        _log(f"Could not start strategies changefeed: {e}", "yellow")


_backtest_paused = False


def watch_backtest_run_command():
    """Separate thread: watch this backtest's row in BacktestInstances.
    When run=false (stop requested): set BacktestResults status to stopped, remove row from
    BacktestInstances, and exit immediately via os._exit(0) so the process does not wait for
    the main loop. When paused=true/false only update _backtest_paused.

    2026-05-22: On `paused: True -> False` transition (operator resumed), reset
    llm_critical_guard + backtest_critical_abort module state and flip
    BacktestResults.status back to 'running' if it was 'paused_llm_critical'.
    This complements the broker outer-except pause flow."""
    global _backtest_result_id, _backtest_paused
    _prev_paused = False
    if not backtest_row_id or r is None:
        return
    try:
        row_id = int(backtest_row_id)
    except (TypeError, ValueError):
        return
    conn = None
    try:
        conn = get_conn()
        for change in r.db(DB_NAME).table('BacktestInstances').get(row_id).changes().run(conn):
            new_val = change.get('new_val')
            # Row deleted (e.g. stop-all) or run=false: stop this backtest and exit
            if new_val is None:
                _log("Backtest row deleted (e.g. stop-all); setting status stopped and exiting.", "yellow")
                try:
                    from datetime import datetime
                    if _backtest_result_id is not None:
                        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update({
                            'status': 'stopped',
                            'timestamp': datetime.utcnow().isoformat() + 'Z',
                        }).run(conn)
                    msg_key = str(backtest_row_id) if backtest_row_id is not None else str(_backtest_result_id)
                    diff_str = _backtest_difficulty_discord_str()
                    try:
                        from interactive_utils import action_enqueue_discord_edit
                        action_enqueue_discord_edit(conn, "backtests", msg_key, content=None, embed={
                            "title": "Backtest Stopped",
                            "description": "Backtest was stopped (row removed).",
                            "color": 0x95A5A6,
                            "fields": [
                                {"name": "ID", "value": str(_backtest_result_id), "inline": True},
                                {"name": "Status", "value": "stopped", "inline": True},
                                {"name": "Difficulty", "value": diff_str, "inline": True},
                            ],
                        })
                    except Exception:
                        pass
                except Exception as e:
                    _log(f"Error updating DB on row delete: {e}", "red")
                os._exit(0)
            new_paused = bool(new_val.get('paused', False))
            # 2026-05-22 — Resume transition (paused: True -> False).
            # Operator clicked Resume after an LLM-critical pause: reset the
            # critical-guard module state on both modules so the next failure
            # can re-fire cleanly, and flip BacktestResults.status back to
            # 'running' (gated on it being 'paused_llm_critical' so manual
            # operator pauses don't get stomped). Defensive try/except on every
            # step — the changefeed thread must never die.
            if _prev_paused and not new_paused:
                try:
                    from llm_critical_guard import reset_state as _cg_reset
                    _cg_reset()
                except Exception:
                    pass
                try:
                    from backtest_critical_abort import reset_state as _bca_reset
                    _bca_reset()
                except Exception:
                    pass
                try:
                    if _backtest_result_id is not None:
                        # Clear the stale pause_* metadata handle() wrote, so a run
                        # that finishes after resuming doesn't carry misleading
                        # pause fields. Only when transitioning out of the critical
                        # pause (gated on status), so manual pauses aren't stomped.
                        from backtest_critical_abort import cleared_pause_fields as _bca_cleared_pause
                        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(
                            lambda row: r.branch(
                                row["status"].default("").eq("paused_llm_critical"),
                                {"status": "running", "resumed_at": r.now(), **_bca_cleared_pause()},
                                {}
                            )
                        ).run(conn)
                except Exception:
                    pass
                try:
                    _log("Resume detected; critical-guard state reset", "cyan")
                except Exception:
                    pass
            _prev_paused = new_paused
            _backtest_paused = new_paused
            if new_val.get('run') is False:
                _log("Backtest stop requested (run=false); setting status, removing from queue, exiting.", "yellow")
                try:
                    from datetime import datetime
                    if _backtest_result_id is not None:
                        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update({
                            'status': 'stopped',
                            'timestamp': datetime.utcnow().isoformat() + 'Z',
                        }).run(conn)
                        _log("BacktestResults status set to stopped", "yellow")
                    # Edit #backtests Discord message to show Stopped (same message_key as Queued/Running)
                    msg_key = str(backtest_row_id) if backtest_row_id is not None else str(_backtest_result_id)
                    diff_str = _backtest_difficulty_discord_str()
                    try:
                        from interactive_utils import action_enqueue_discord_edit
                        action_enqueue_discord_edit(conn, "backtests", msg_key, content=None, embed={
                            "title": "Backtest Stopped",
                            "description": "Backtest was stopped (run=false).",
                            "color": 0x95A5A6,
                            "fields": [
                                {"name": "ID", "value": str(_backtest_result_id), "inline": True},
                                {"name": "Status", "value": "stopped", "inline": True},
                                {"name": "Difficulty", "value": diff_str, "inline": True},
                            ],
                        })
                    except Exception:
                        pass
                    r.db(DB_NAME).table('BacktestInstances').get(row_id).delete().run(conn)
                    _log("Removed backtest from BacktestInstances queue", "yellow")
                except Exception as e:
                    _log(f"Error updating DB on stop: {e}", "red")
                os._exit(0)
    except Exception as e:
        _log(f"BacktestInstances changefeed error: {e}", "red")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _ensure_strategies_table(conn):
    """Ensure DB and Strategies table exist on startup. No-op if already present."""
    if r is None:
        return
    try:
        dbs = list(r.db_list().run(conn))
        if DB_NAME not in dbs:
            r.db_create(DB_NAME).run(conn)
        tables = list(r.db(DB_NAME).table_list().run(conn))
        if 'Strategies' not in tables:
            r.db(DB_NAME).table_create('Strategies').run(conn)
            _log("Created Strategies table", "green")
    except Exception as e:
        _log(f"Could not ensure Strategies table: {e}", "yellow")


_CRYPTO_STRATEGY_NAMES = ("momentum", "allocator", "fast", "reference", "meanrev", "connors", "adaptive")

# crypto_config tuning knobs forwarded into the synthesized strategy config so a
# user can tune a crypto strategy from crypto_config without a Strategies row.
_CRYPTO_STRATEGY_TUNABLES = (
    "rsi_period", "rsi_buy", "rsi_exit", "regime_ma", "top_k", "exit_ma",  # meanrev/connors
    "sizing", "atr_period", "bear_gate_ma",                            # meanrev sizing + crash-bear gate
    "switch_ma", "confirm_ma", "rebalance_drift",                      # adaptive regime switcher
    "fast_ema", "slow_ema", "momentum_lookback", "adx_period", "adx_min",  # momentum
    "entry_window", "exit_window", "trend_ma",                        # fast
)


def _crypto_synthetic_specs(instance_doc):
    """For a crypto instance, synthesize a run_once strategy spec from
    ``crypto_config.strategy`` (momentum/allocator/fast/reference) so it runs
    without needing a Strategies-table row. Returns the ``(specs, id, schema)``
    tuple, or ``None`` for non-crypto instances (fall through to the DB loader)."""
    try:
        if (instance_doc or {}).get("kind") != "crypto":
            return None
        cc = instance_doc.get("crypto_config") or {}
        name = str(cc.get("strategy") or "momentum").strip().lower()
        if name not in _CRYPTO_STRATEGY_NAMES:
            name = "momentum"
        cfg = {"band": cc.get("band", "medium")}
        for _k in _CRYPTO_STRATEGY_TUNABLES:
            if _k in cc:
                cfg[_k] = cc[_k]
        spec = {
            "strategy": name,
            "weight": 1.0,
            "execution_position": 0,
            "decision_phase": "pre",
            "execution_scope": "run_once",
            "config": cfg,
            "conditions": {},
        }
        return [spec], None, {"name": "crypto:%s" % name, "strategies": [spec]}
    except Exception:
        return None


def load_strategies_from_db():
    """Load strategy list from DB table Strategies via instance.strategy_id. Returns tuple: (list of strategy specs, strategy_row_id), or ([], None) on error/missing."""
    try:
        conn = get_conn()
        try:
            _ensure_strategies_table(conn)
            tables = list(r.db(DB_NAME).table_list().run(conn))
            if 'Strategies' not in tables:
                return [], None, None
            # Get instance doc to find strategy_id (instance_id can be int or string, e.g. "ai-temp-xxx")
            if not instance_id:
                return [], None, None
            instance_doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
            if not instance_doc:
                return [], None, None
            # Crypto instances run the strategy named in crypto_config.strategy
            # via a synthesized run_once spec — no Strategies row required.
            _crypto_specs = _crypto_synthetic_specs(instance_doc)
            if _crypto_specs is not None:
                _c_specs, _c_sid, _c_schema = _crypto_specs
                return _c_specs, _c_sid, sanitize_snapshot(_c_schema)
            strategy_id = instance_doc.get('strategy_id')
            if strategy_id is None:
                return [], None, None
            # Get strategy by id
            strategy_doc = r.db(DB_NAME).table('Strategies').get(strategy_id).run(conn)
            if not strategy_doc:
                return [], None, None
            strategies_array = strategy_doc.get('strategies', [])
            if not isinstance(strategies_array, list):
                return [], None, None
            # Snapshot of strategy schema for storing in BacktestResults (name +
            # strategies at load time). Sanitized: credential values must never
            # reach BacktestResults again (2026-07 plaintext-secret incident).
            strategy_schema = sanitize_snapshot({
                "name": strategy_doc.get("name"),
                "strategies": list(strategies_array),
            })
            specs = [s for s in strategies_array if isinstance(s, dict) and s.get('strategy') is not None and 'weight' in s]
            return specs, strategy_id, strategy_schema
        finally:
            conn.close()
    except (NameError, Exception) as e:
        try:
            _log("Could not load strategies from DB (instance may have no strategy_id, or RethinkDB unreachable): %s" % (e,), "yellow")
        except Exception:
            pass
        return [], None, None

def run_run_once_strategies(specs, symbols, prices, current_time, data=None, portfolio_emulator=None, time_increment=None, alpaca_key=None, alpaca_secret=None, strategy_caches=None, alpaca_data_feed=None, mode=None):
    """
    Run strategies that have execution_scope "run_once". Each returns a dict mapping symbol -> score (and optional reason).
    Return value can be dict[symbol -> int] or dict[symbol -> dict with "score" and optional "reason"].
    Returns: list of (spec, scores_dict, reasons_dict, metadata_dict).
        reasons_dict[sym] is str or None.
        metadata_dict contains strategy-specific metadata (e.g. _nexus_discovered, _nexus_sell_enforcement).
    alpaca_key/alpaca_secret: broker-level Alpaca credentials injected into conditions for any strategy that needs them.
    mode: 2026-05-07 scheduler refactor — "FULL" | "MONITOR" | "IDLE" | None.
        Forwarded to strategy.run_once(..., mode=mode). When the broker drives
        scheduling via backend.scheduler.get_next_wake, mode is concrete and
        bypasses the strategy's legacy dual-cadence gate. Backtest path passes
        mode=None and the strategy's gate (or the FULL pipeline) decides.
    """
    if not specs:  # V7.3: removed `or not symbols` -- Nexus can discover from scratch
        return []
    # Re-resolve *_llm_model_id references on every invocation so changes
    # made via the Models UI (PUT /models/{id} or a fresh POST + strategy
    # re-pointing) propagate to the running broker without a restart.
    # Without this, credentials baked into spec["config"] at startup stay
    # stale forever — the symptom was repeated NVIDIA 401 errors after a
    # successful "Test & Save" in the UI. The model_resolver keeps a 5-min
    # TTL doc cache so the steady-state cost is one dict update per spec.
    # invalidate_model_cache() is called from action_edit_model, so the
    # next call after a UI update fetches the fresh row from the DB.
    try:
        _needs_resolve = [
            s for s in specs
            if isinstance(s.get("config"), dict)
            and any(k.endswith("llm_model_id") for k in s["config"])
        ]
        if _needs_resolve:
            _resolve_conn = get_conn()
            try:
                for _s in _needs_resolve:
                    _s["config"] = resolve_model_refs_in_config(_resolve_conn, _s["config"])
            finally:
                try:
                    _resolve_conn.close()
                except Exception:
                    pass
    except Exception as _resolve_e:
        # Resolution failure (DB connectivity, malformed model row) is
        # non-fatal — fall through to the baked-in credentials from the
        # last successful resolution. Log so operators can spot it.
        _log(f"Model re-resolution warning: {_resolve_e}", "yellow")
    results = []
    cache_store = strategy_caches if isinstance(strategy_caches, dict) else _strategy_cache
    for spec in specs:
        name = (spec.get("strategy") or "").strip()
        if not name or float(spec.get("weight", 0)) <= 0:
            continue
        if name not in _strategy_class_cache:
            _strategy_class_cache[name] = _load_strategy_class(name)
        cls = _strategy_class_cache[name]
        if cls is None or not hasattr(cls, "run_once"):
            _log(f"Run-once strategy '{name}' has no run_once method; skipping.", "yellow")
            continue
        config = dict(spec.get("config") or {})
        conditions = dict(spec.get("conditions") or {})
        merged_settings = {}
        merged_settings.update(conditions)
        merged_settings.update(config)
        config = dict(merged_settings)
        conditions = dict(merged_settings)
        if name.strip().lower() == "graph_nexus_analysis":
            base_runtime_instance_id = str(config.get("base_instance_id") or config.get("instance_id") or instance_id or "").strip() or "default"
            base_runtime_instance_id, history_scope_id, scoped_runtime_instance_id, history_model_stamp = _resolve_nexus_runtime_identity(
                base_runtime_instance_id,
                merged_settings,
            )
            config["base_instance_id"] = base_runtime_instance_id
            conditions["base_instance_id"] = base_runtime_instance_id
            config["history_scope_id"] = history_scope_id
            conditions["history_scope_id"] = history_scope_id
            config["history_model_stamp"] = dict(history_model_stamp)
            conditions["history_model_stamp"] = dict(history_model_stamp)
            config["runtime_instance_id"] = scoped_runtime_instance_id
            conditions["runtime_instance_id"] = scoped_runtime_instance_id
        # Inject broker-level Alpaca credentials into config.
        # IMPORTANT: broker-provided creds OVERRIDE whatever is baked into the
        # Strategies row. The broker resolves creds from the linked brokerage
        # (trading brokerage, or the separate data-source brokerage when set
        # for live mode). Strategies rows are frozen per user directive, so
        # stale/legacy alpaca_key values in config would otherwise win and
        # cause 401s against data.alpaca.markets for paper-linked instances.
        # Only override when broker actually supplied non-empty creds.
        if alpaca_key:
            config["alpaca_key"] = alpaca_key
            conditions["alpaca_key"] = alpaca_key
        if alpaca_secret:
            config["alpaca_secret"] = alpaca_secret
            conditions["alpaca_secret"] = alpaca_secret
        # 2026-04-23: user-selected bars feed (iex/sip) from
        # BrokerageAccounts.alpaca_data_feed. Strategies pass this through
        # when they call `fetch_alpaca_historical_bars(..., feed=...)`.
        _feed_for_strategies = (alpaca_data_feed or "iex").strip().lower() if alpaca_data_feed else "iex"
        if _feed_for_strategies not in ("iex", "sip"):
            _feed_for_strategies = "iex"
        config["alpaca_data_feed"] = _feed_for_strategies
        conditions["alpaca_data_feed"] = _feed_for_strategies
        if instance_id and not config.get("instance_id"):
            config["instance_id"] = instance_id
            conditions["instance_id"] = instance_id
        # Crypto instances: pass the instance-level crypto_config (band +
        # per-coin allocations) into the strategy so it can honor fixed
        # weights + a dynamic remainder. Uses the already-cached blob (no
        # per-tick DB hit); equity instances are unaffected.
        try:
            _ck_inj, _ccfg_inj = _instance_kind_and_crypto_config()
            if _ck_inj == "crypto" and _ccfg_inj and not config.get("crypto_config"):
                config["crypto_config"] = dict(_ccfg_inj)
                conditions["crypto_config"] = dict(_ccfg_inj)
        except Exception:
            pass
        # 2026-04-22: inject a live-mode marker so the strategy's destructive
        # session-cleanup paths (designed for backtest reproducibility) can
        # detect they are running against a live instance and skip the
        # row-wiping step. The strategy's DECISION LOGIC does not read this
        # flag — only the side-effect cleanup does. Without this gate, every
        # live-broker restart erases today's trade-context + outcome rows,
        # which Fix B's "orders today" cache depends on for restart safety.
        config["_nexus_is_live_mode"] = (mode == MODE_LIVE)
        conditions["_nexus_is_live_mode"] = (mode == MODE_LIVE)
        strategy_cache = cache_store.setdefault(name, {})
        try:
            instance = cls()
            broker_backtest_id = str(backtest_row_id).strip() if backtest_row_id is not None else None
            broker_instance_id = str(instance_id).strip() if instance_id is not None else None
            # 2026-05-21: telemetry context uses threading.local(), so the broker's
            # outer llm_call_context frame does NOT reach worker threads spawned by
            # strategies (e.g., active_event_maintenance's ThreadPoolExecutor).
            # Propagate the IDs via the config dict — strategies push them into
            # their per-call inner llm_call_context, which works on any thread.
            config["_telemetry_backtest_id"] = broker_backtest_id or None
            config["_telemetry_instance_id"] = broker_instance_id or None
            conditions["_telemetry_backtest_id"] = broker_backtest_id or None
            conditions["_telemetry_instance_id"] = broker_instance_id or None
            with telemetry_llm_call_context(
                backtest_id=broker_backtest_id or None,
                instance_id=broker_instance_id or None,
            ):
                raw = instance.run_once(
                    list(symbols), prices, current_time, config, conditions,
                    data=data, portfolio_emulator=portfolio_emulator,
                    strategy_cache=strategy_cache, time_increment=time_increment,
                    mode=mode,
                )
            if isinstance(raw, dict):
                out_scores = {}
                out_reasons = {}
                metadata = {}
                # Extract nexus metadata keys before processing scores
                nexus_discovered = raw.pop("_nexus_discovered", [])
                nexus_sell_enforcement = raw.pop("_nexus_sell_enforcement", [])
                nexus_position_sizes = raw.pop("_nexus_position_sizes", {})
                nexus_action_intents = raw.pop("_nexus_action_intents", {})
                nexus_active_events = raw.pop("_nexus_active_events", [])
                # Fix 15: Extract propagation-expansion BUY tickers
                nexus_expansion_buys = raw.pop("_nexus_expansion_buys", [])
                nexus_executable_buys = raw.pop("_nexus_executable_buys", [])
                if nexus_discovered:
                    metadata["_nexus_discovered"] = list(nexus_discovered)
                if nexus_sell_enforcement:
                    metadata["_nexus_sell_enforcement"] = list(nexus_sell_enforcement)
                if nexus_position_sizes:
                    metadata["_nexus_position_sizes"] = dict(nexus_position_sizes)
                if nexus_action_intents:
                    metadata["_nexus_action_intents"] = dict(nexus_action_intents)
                if nexus_active_events:
                    metadata["_nexus_active_events"] = list(nexus_active_events)
                if nexus_expansion_buys:
                    metadata["_nexus_expansion_buys"] = list(nexus_expansion_buys)
                if nexus_executable_buys:
                    metadata["_nexus_executable_buys"] = list(nexus_executable_buys)
                # Allow original symbols, discovered symbols, expansion buys, and executable buys (V20b: includes backfill rotation buys)
                allowed_syms = set(symbols) | set(nexus_discovered) | set(nexus_expansion_buys) | set(nexus_executable_buys)
                for sym, val in raw.items():
                    if sym not in allowed_syms:
                        continue
                    if isinstance(val, dict):
                        sc = val.get("score", val.get("signal", 0))
                        reason = val.get("reason")
                        action_intent = val.get("action_intent")
                        if reason is not None:
                            reason_text = str(reason)[:500] if reason else None
                            if action_intent and reason_text:
                                reason_text = f"[intent={action_intent}] {reason_text}"[:500]
                            out_reasons[sym] = reason_text
                    else:
                        sc = val
                    if sc in (1, 0, -1):
                        out_scores[sym] = int(sc)
                results.append((spec, out_scores, out_reasons, metadata))
                n_discovered = len([s for s in out_scores if s in set(nexus_discovered)])
                extra = f" (+{n_discovered} discovered)" if n_discovered else ""
                _log(f"Run-once strategy '{name}' returned scores for {len(out_scores)} symbols{extra}", "cyan")
        except Exception as e:
            _log(f"Run-once strategy '{name}' error: {e}", "red")
    return results


def _merged_strategy_settings(spec):
    merged = {}
    if isinstance(spec, dict):
        merged.update(dict(spec.get("conditions") or {}))
        merged.update(dict(spec.get("config") or {}))
    return merged


# The history-scope identity now lives in the shared, broker-free
# `nexus_config_identity` module so the "preserve history" re-stamp feature and
# this boot path compute byte-identical hashes. These thin aliases keep the
# existing in-module call sites (`_resolve_nexus_runtime_identity`, etc.) working.
from nexus_config_identity import (
    history_scope_doc as _nexus_history_scope_doc,
    history_scope_id as _nexus_history_scope_id,
    live_config_hash as _nexus_live_config_hash,
)


def _resolve_nexus_runtime_identity(base_instance_id, settings):
    base = str(base_instance_id or "").strip() or "default"
    scope_id = _nexus_history_scope_id(settings)
    scoped_instance_id = f"{base}|{scope_id}" if scope_id else base
    return base, scope_id, scoped_instance_id, _nexus_history_scope_doc(settings)


def _iter_backtest_trading_session_opens(start_dt, end_dt):
    if start_dt is None or end_dt is None or start_dt > end_dt:
        return []
    opens = []
    day_cursor = start_dt.date()
    end_date_only = end_dt.date()
    while day_cursor <= end_date_only:
        if day_cursor.weekday() < 5:
            open_dt = _next_market_open_utc(datetime.datetime.combine(day_cursor, datetime.time(0, 0, 0)))
            if open_dt is not None:
                opens.append(open_dt)
        day_cursor += datetime.timedelta(days=1)
    return opens


def _nexus_lookback_update_db(current: int, total: int, current_date: str, start_date: str, end_date: str) -> None:
    """Write Nexus lookback training progress to BacktestResults (best-effort, never raises)."""
    if _backtest_result_id is None or _backtest_db_conn is None or r is None:
        return
    try:
        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update({
            "nexus_lookback": {
                "current": current,
                "total": total,
                "current_date": current_date,
                "start_date": start_date,
                "end_date": end_date,
            },
            "_last_active": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        }).run(_backtest_db_conn)
    except Exception:
        pass


def _nexus_lookback_clear_db() -> None:
    """Clear the nexus_lookback field from BacktestResults once training is done."""
    if _backtest_result_id is None or _backtest_db_conn is None or r is None:
        return
    try:
        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(
            {"nexus_lookback": None}
        ).run(_backtest_db_conn)
    except Exception:
        pass


def _log_historic_lookback_banner(*, start=True, spec_name="", start_date=None, end_date=None, trading_days=0):
    line = "=" * 24
    _log(line, "cyan")
    _log("Historic Lookback Start" if start else "Historic Lookback End", "cyan")
    detail_parts = []
    if spec_name:
        detail_parts.append(f"strategy={spec_name}")
    if start_date is not None and end_date is not None:
        detail_parts.append(f"range={start_date} -> {end_date}")
    if trading_days:
        detail_parts.append(f"trading_days={trading_days}")
    if detail_parts:
        _log(" | ".join(detail_parts), "cyan")
    _log(line, "cyan")


def _load_nexus_processed_trade_context_dates(instance_id_value, date_keys):
    """Thin shim — delegates to ``nexus_lookback_db`` (extracted so the
    helpers are importable from tests; broker.py argparses at module
    load so it can't be imported standalone)."""
    from nexus_lookback_db import load_nexus_processed_trade_context_dates
    return load_nexus_processed_trade_context_dates(instance_id_value, date_keys)


def _historic_lookback_resume_dates(instance_id_value, lookback_opens):
    """Thin shim — delegates to ``nexus_lookback_db`` so the resume-date
    logic has a single home (the legacy duplicate here was diverging
    from the extracted module after the index refactor)."""
    from nexus_lookback_db import historic_lookback_resume_dates
    return historic_lookback_resume_dates(instance_id_value, lookback_opens)


def _run_backtest_historic_lookback(run_once_specs, symbols, data, start_dt, portfolio_emulator, time_increment, alpaca_key, alpaca_secret):
    # 2026-05-22 — extend critical-guard pause/retry coverage to the lookback
    # prepass. The main while-loop's outer except catches LLMCriticalFailure
    # and pauses (commit d92c435), but the lookback runs BEFORE that loop and
    # was OUTSIDE the catch — an Azure 403 storm on lookback day 1 (bt437583)
    # caused container exit 1 instead of pause. The per-iteration wrap below
    # routes lookback critical failures through backtest_critical_abort.handle
    # and idles on _backtest_paused until the operator resumes via the UI.
    global _backtest_paused
    if mode != MODE_BACKTEST or not run_once_specs or start_dt is None:
        return
    # Diagnostic: this prep block was running silently for 60-90s
    # before the first "Historic Lookback Start" log appeared. Surface
    # each major step so operators can see where time is being spent.
    _lb_prep_t0 = time.time()
    _log("Historic lookback prep: scanning run-once specs for graph_nexus_analysis...", "cyan")
    eligible_specs = []
    for spec in (run_once_specs or []):
        name = str((spec or {}).get("strategy") or "").strip()
        if name.lower() != "graph_nexus_analysis":
            continue
        settings = _merged_strategy_settings(spec)
        if settings.get("historical_lookback_enabled", True) is False:
            continue
        lookback_days = int(settings.get("lookback_learning_days", settings.get("learning_stage_days", 30)) or 0)
        if lookback_days <= 0:
            continue
        eligible_specs.append((spec, lookback_days))
    if not eligible_specs:
        _log("Historic lookback prep: no eligible strategies — skipping.", "yellow")
        return
    _log(
        f"Historic lookback prep: {len(eligible_specs)} eligible strategy(ies) found in {time.time() - _lb_prep_t0:.2f}s.",
        "cyan",
    )

    base_symbols = list(symbols or [])
    for spec, lookback_days in eligible_specs:
        spec_name = str((spec or {}).get("strategy") or "").strip() or "run_once"
        spec_settings = _merged_strategy_settings(spec)
        base_runtime_instance_id = str(spec_settings.get("base_instance_id") or spec_settings.get("instance_id") or instance_id or "").strip() or "default"
        _id_t0 = time.time()
        _log(
            f"Historic lookback prep: resolving runtime identity for {spec_name} "
            f"(base={base_runtime_instance_id}, lookback_days={lookback_days})...",
            "cyan",
        )
        _base_instance_id, history_scope_id, scoped_runtime_instance_id, _history_model_stamp = _resolve_nexus_runtime_identity(
            base_runtime_instance_id,
            spec_settings,
        )
        _log(
            f"Historic lookback prep: runtime identity resolved in {time.time() - _id_t0:.2f}s "
            f"| scope={history_scope_id[:12]}...",
            "cyan",
        )
        lookback_start_dt = start_dt - datetime.timedelta(days=lookback_days)
        lookback_end_dt = start_dt - datetime.timedelta(days=1)
        _cal_t0 = time.time()
        _log(
            f"Historic lookback prep: enumerating trading sessions "
            f"{lookback_start_dt.strftime('%Y-%m-%d')} → {lookback_end_dt.strftime('%Y-%m-%d')} "
            "(exchange-calendars NYSE)...",
            "cyan",
        )
        lookback_opens = _iter_backtest_trading_session_opens(lookback_start_dt, lookback_end_dt)
        _log(
            f"Historic lookback prep: enumerated {len(lookback_opens)} trading session(s) "
            f"in {time.time() - _cal_t0:.2f}s.",
            "cyan",
        )
        if not lookback_opens:
            _log(f"Historic lookback skipped for {spec_name}: no prior trading sessions in window.", "yellow")
            continue
        _resume_t0 = time.time()
        _log(
            f"Historic lookback prep: querying GraphNexusTradeContexts for resume dates "
            f"(scope={history_scope_id[:12]}..., {len(lookback_opens)} candidate days)...",
            "cyan",
        )
        resume_opens = _historic_lookback_resume_dates(scoped_runtime_instance_id, lookback_opens)
        _log(
            f"Historic lookback prep: resume-date query done in {time.time() - _resume_t0:.2f}s "
            f"({len(resume_opens)}/{len(lookback_opens)} days still need processing).",
            "cyan",
        )
        existing_days = max(0, len(lookback_opens) - len(resume_opens))
        if not resume_opens:
            _log(
                f"Historic lookback reuse: using existing Nexus history for {spec_name} "
                f"({len(lookback_opens)}/{len(lookback_opens)} trading days already built, scope={history_scope_id[:12]}...).",
                "cyan",
            )
            continue

        prepass_spec = dict(spec or {})
        prepass_config = dict(prepass_spec.get("config") or {})
        prepass_conditions = dict(prepass_spec.get("conditions") or {})
        prepass_config["historical_lookback_mode"] = True
        prepass_config["historical_lookback_source"] = "broker_prepass"
        prepass_config["learning_stage_enabled"] = False
        prepass_conditions["historical_lookback_mode"] = True
        prepass_conditions["historical_lookback_source"] = "broker_prepass"
        prepass_conditions["learning_stage_enabled"] = False
        prepass_spec["config"] = prepass_config
        prepass_spec["conditions"] = prepass_conditions

        temp_strategy_caches = {}
        if existing_days > 0:
            _log(
                f"Historic lookback reuse: {existing_days}/{len(lookback_opens)} trading days already built; "
                f"resuming from {resume_opens[0].strftime('%Y-%m-%d')} (scope={history_scope_id[:12]}...).",
                "cyan",
            )
        _log_historic_lookback_banner(
            start=True,
            spec_name=spec_name,
            start_date=resume_opens[0].strftime("%Y-%m-%d"),
            end_date=lookback_end_dt.strftime("%Y-%m-%d"),
            trading_days=len(resume_opens),
        )
        _lb_start_str = resume_opens[0].strftime('%Y-%m-%d')
        _lb_end_str = lookback_end_dt.strftime('%Y-%m-%d')
        _lb_total = len(resume_opens)
        _log(f"Lookback: {_lb_total} bars to process (overlay={'SKIP' if prepass_config.get('historical_lookback_mode') else 'RUN'})", "cyan")
        for idx, lookback_time in enumerate(resume_opens, start=1):
            if idx == 1 or idx == len(resume_opens) or idx % 5 == 0:
                _log(
                    f"Historic lookback progress {idx}/{_lb_total} | date={lookback_time.strftime('%Y-%m-%d')}",
                    "cyan",
                )
            lookback_prices = _get_prices_at_time(data, base_symbols, lookback_time) if isinstance(data, dict) else {}
            lookback_history = get_price_history_up_to_current(data, base_symbols, lookback_time) if isinstance(data, dict) else {}

            # 2026-05-22 — per-iteration snapshot capture so an LLMCriticalFailure
            # during lookback can restore the last-good-bar state before retry.
            # Mirrors the main-loop snapshot capture at ~line 7397.
            try:
                from backtest_bar_snapshot import capture as _bs_capture
                _bs_capture(
                    strategy_caches=(_strategy_cache if isinstance(_strategy_cache, dict) else {}),
                    portfolio_emulator=portfolio_emulator,
                    current_time=lookback_time,
                )
            except Exception as _capture_err:
                try:
                    _log(f"lookback bar snapshot capture failed (non-fatal): {_capture_err}", "yellow")
                except Exception:
                    pass

            # Retry-on-critical loop wrapping the strategy invocation. Same pattern
            # as the main loop's outer-except at ~line 9253: route LLMCriticalFailure
            # through backtest_critical_abort.handle (which restores snapshot, marks
            # paused in the DB, pages Discord), then idle until the operator resumes
            # via the UI. On resume, reset the guards and retry the SAME idx.
            while not shutdown_requested:
                try:
                    run_run_once_strategies(
                        [prepass_spec],
                        base_symbols,
                        lookback_prices,
                        lookback_time,
                        data=lookback_history,
                        portfolio_emulator=portfolio_emulator,
                        time_increment=time_increment,
                        alpaca_key=alpaca_key,
                        alpaca_secret=alpaca_secret,
                        strategy_caches=temp_strategy_caches,
                    )
                    break  # success → advance to next idx
                except BaseException as _lb_err:
                    # except BaseException because LLMCriticalFailure inherits
                    # BaseException (see llm_critical_guard.py:146, commit 468d4ca)
                    # and would otherwise escape any plain `except Exception`.
                    try:
                        from llm_critical_guard import LLMCriticalFailure
                        _is_llm_critical = isinstance(_lb_err, LLMCriticalFailure)
                    except Exception:
                        _is_llm_critical = False

                    if not _is_llm_critical:
                        raise  # not our concern — let the top-level error path take over

                    # Critical LLM failure during lookback. Route through the same
                    # pause flow as the main loop.
                    try:
                        if _backtest_result_id is not None:
                            from backtest_critical_abort import handle as _bt_handle
                            _bt_handle(
                                backtest_id=str(_backtest_result_id),
                                instance_id=str(instance_id),
                                failure=_lb_err,
                            )
                    except Exception as _bt_handle_err:
                        try:
                            _log(f"backtest_critical_abort handler raised during lookback: {_bt_handle_err}", "red")
                        except Exception:
                            pass

                    # Synchronously set local pause flag — same fix as commit d92c435
                    # for the main loop. `global _backtest_paused` is declared at the
                    # top of this function so this assignment hits the module global.
                    _backtest_paused = True

                    # Wait for operator to resume via UI.
                    try:
                        _log(f"Lookback paused at idx={idx}/{_lb_total} (LLM critical); awaiting resume...", "yellow")
                    except Exception:
                        pass
                    while _backtest_paused and not shutdown_requested:
                        time.sleep(1)

                    if shutdown_requested:
                        import sys as _sys
                        _sys.exit(0)

                    # Resume: reset critical-guard state and retry the same idx.
                    try:
                        from llm_critical_guard import reset_state as _cg_reset
                        _cg_reset()
                    except Exception:
                        pass
                    try:
                        from backtest_critical_abort import reset_state as _bca_reset
                        _bca_reset()
                    except Exception:
                        pass
                    try:
                        _log(f"Resumed lookback at idx={idx}/{_lb_total}; retrying...", "cyan")
                    except Exception:
                        pass
                    # Loop back to retry this same idx (while not shutdown_requested).

            _nexus_lookback_update_db(idx, _lb_total, lookback_time.strftime('%Y-%m-%d'), _lb_start_str, _lb_end_str)
        _nexus_lookback_clear_db()
        _log_historic_lookback_banner(
            start=False,
            spec_name=spec_name,
            start_date=resume_opens[0].strftime("%Y-%m-%d"),
            end_date=lookback_end_dt.strftime("%Y-%m-%d"),
            trading_days=len(resume_opens),
        )


def _fetch_daily_close_prices(symbols, start_date, end_date, alpaca_key, alpaca_secret, feed=None):
    """Fetch daily close prices for symbols between start_date and end_date (inclusive).
    Returns dict[date_str -> dict[symbol -> close_price]].

    2026-04-23: ``feed`` kwarg threaded from BrokerageAccounts.alpaca_data_feed.
    """
    import requests
    headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret, "accept": "application/json"}
    if feed is None:
        feed = os.environ.get("ALPACA_DATA_FEED", "iex")
    feed = (feed or "iex").strip().lower() or "iex"
    if feed not in ("iex", "sip"):
        feed = "iex"
    start_iso = start_date.strftime("%Y-%m-%dT00:00:00Z")
    end_iso = (end_date + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    prices_by_date = {}
    for sym in symbols:
        collected = []
        _alpaca_auth_failed = False
        try:
            params = {"start": start_iso, "end": end_iso, "timeframe": "1Day", "limit": 10000, "feed": feed, "sort": "asc"}
            url = f"{ALPACA_DATA_BASE}/stocks/{sym}/bars"
            while True:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code in (401, 403, 404, 422):
                    # 2026-04-23: mark Alpaca-auth failure so we try RH fallback below
                    _alpaca_auth_failed = True
                    break
                if resp.status_code != 200:
                    break
                body = resp.json()
                bars = body.get("bars") or []
                collected.extend(bars)
                npt = body.get("next_page_token")
                if not npt:
                    break
                params = {"page_token": npt, "timeframe": "1Day", "limit": 10000, "feed": feed, "sort": "asc"}
        except Exception as e:
            _log(f"  Lookback price fetch failed for {sym}: {e}", "yellow")
        # 2026-04-23: RH fallback on Alpaca 401/403/404/422 — no silent discards.
        # Mirrors the fetch_alpaca_historical_bars RH fallback policy so live
        # lookback's daily-close prefetch covers SIP-only / delisted / IEX-
        # restricted tickers without operator intervention.
        if _alpaca_auth_failed and not collected and robinhood_data_fallback_allowed(live_broker_type):
            try:
                from robinhood_engine import get_price_history as _rh_ph
                df = _rh_ph(sym, interval="day", span="year")
                if df is not None and not df.empty:
                    _start_d = start_date
                    _end_d = end_date
                    for _i in range(len(df)):
                        _row_date = df.index[_i]
                        if hasattr(_row_date, "date"):
                            _row_date = _row_date.date()
                        if _row_date < _start_d or _row_date > _end_d:
                            continue
                        try:
                            _c = float(df["close_price"].iloc[_i]) if "close_price" in df.columns else float(df["close"].iloc[_i])
                            if _c > 0:
                                prices_by_date.setdefault(_row_date.strftime("%Y-%m-%d"), {})[sym] = _c
                        except (TypeError, ValueError, KeyError):
                            continue
                    _log(f"  Lookback price fetch: RH fallback succeeded for {sym}", "cyan")
                    continue  # RH filled in; skip the Alpaca collected loop below
            except Exception as _rh_e:
                _log(f"  Lookback price fetch: RH fallback failed for {sym}: {_rh_e}", "yellow")
        for b in collected:
            t = b.get("t")
            c = b.get("c")
            if t and c is not None:
                date_str = str(t)[:10]
                prices_by_date.setdefault(date_str, {})[sym] = float(c)
    return prices_by_date


def _run_live_historic_lookback(
    run_once_specs,
    symbols,
    alpaca_key,
    alpaca_secret,
    *,
    restrict_to_dates=None,
):
    """Run Nexus historic lookback on live startup.

    Contract (Phase B-full, 2026-04-21 revamp):
      * Default lookback is 120 trading days for a first-launch instance so
        the Learning stage has ≥5 outcomes on day 1. Override via strategy
        config ``lookback_learning_days`` or env ``NEXUS_LIVE_LOOKBACK_MAX_DAYS``.
      * IDEMPOTENT. ``_historic_lookback_resume_dates`` skips dates already
        recorded in ``nexus_processed_trade_contexts`` for this scope so a
        same-day restart or next-day boot only processes the incremental gap,
        not the full 120 days again.
      * Self-discover friendly: when ``symbols=[]`` (discovery instance), we
        still run the prepass by passing ``base_symbols=[]`` through to
        ``run_run_once_strategies`` with ``historical_lookback_mode=True``.
        Nexus picks its own universe per day, matching backtest day-1
        semantics (see V7.3 contract at the ``watch_backtest_run_command``
        comment in this module).
      * LLM cap during lookback is raised from the live default (4) to
        ``lookback_mode_max_llm_calls_per_cycle`` (default 32) so the 120-day
        prepass parallelises more than a single live bar.
      * Pre-market timing gate: if market opens in <4 hours, run whatever fits
        and let the rest resume tomorrow via ``_historic_lookback_resume_dates``.

    Returns the merged ``temp_strategy_caches`` dict so the caller can seed
    the module-level ``_strategy_cache`` for the main loop (preserves V32
    peak state, fast-loser blacklist, V32 convert cooldowns, momentum
    watchlist — without the merge, everything the prepass built is thrown
    away the moment the function returns).
    """
    if not run_once_specs:
        return {}

    # Hard cap from env: operators can dial back the 120-day default if the
    # first boot eats their LLM budget. Config in the strategy spec still wins.
    try:
        _env_max_days = int(os.environ.get("NEXUS_LIVE_LOOKBACK_MAX_DAYS", "120"))
    except Exception:
        _env_max_days = 120

    eligible_specs = []
    for spec in (run_once_specs or []):
        name = str((spec or {}).get("strategy") or "").strip()
        if name.lower() != "graph_nexus_analysis":
            continue
        settings = _merged_strategy_settings(spec)
        if settings.get("historical_lookback_enabled", True) is False:
            continue
        # Default 120 days for first-launch context (was 30 via
        # learning_stage_days). Still overridable via config.
        _cfg_days = settings.get("lookback_learning_days", settings.get("learning_stage_days", 120))
        try:
            lookback_days = int(_cfg_days or 0)
        except Exception:
            lookback_days = 120
        lookback_days = min(lookback_days, _env_max_days) if _env_max_days > 0 else lookback_days
        if lookback_days <= 0:
            continue
        eligible_specs.append((spec, lookback_days))
    if not eligible_specs:
        return {}

    # Pre-market timing gate: if market open is imminent (<4h), the full
    # 120-day prepass would collide with open. Still start — B9's resume
    # idempotency means whatever we don't finish gets picked up tomorrow.
    try:
        _pre_market_window_sec = int(os.environ.get("NEXUS_LIVE_LOOKBACK_MIN_PREMARKET_SEC", "14400"))  # 4 hrs
        # _next_market_open_utc returns a NAIVE datetime per its implementation,
        # so strip tzinfo from _now_utc before subtracting or Python raises
        # TypeError("can't subtract offset-naive and offset-aware datetimes")
        # and the try/except swallows it — the gate never fires.
        _now_naive = datetime.datetime.utcnow()
        _next_open = _next_market_open_utc(_now_naive)
        if _next_open is not None:
            # Some branches of _next_market_open_utc return aware datetimes;
            # normalise to naive for the subtraction.
            if getattr(_next_open, "tzinfo", None) is not None:
                _next_open = _next_open.replace(tzinfo=None)
            _gap_sec = int((_next_open - _now_naive).total_seconds())
        else:
            _gap_sec = None
        if _gap_sec is not None and _gap_sec < _pre_market_window_sec:
            _log(
                f"Live lookback: market opens in {_gap_sec // 60}m (<{_pre_market_window_sec // 60}m) — "
                "starting prepass but expect resume across following sessions.",
                "yellow",
            )
    except Exception as _e_gate:
        _log(f"Live lookback pre-market gate check failed (non-fatal): {_e_gate}", "yellow")

    base_symbols = list(symbols or [])
    merged_caches: dict = {}
    for spec, lookback_days in eligible_specs:
        spec_name = str((spec or {}).get("strategy") or "").strip() or "run_once"
        spec_settings = _merged_strategy_settings(spec)
        base_runtime_instance_id = str(spec_settings.get("base_instance_id") or spec_settings.get("instance_id") or instance_id or "").strip() or "default"
        _base_instance_id, history_scope_id, scoped_runtime_instance_id, _history_model_stamp = _resolve_nexus_runtime_identity(
            base_runtime_instance_id,
            spec_settings,
        )
        now = datetime.datetime.utcnow()
        lookback_start_dt = now - datetime.timedelta(days=lookback_days)
        lookback_end_dt = now - datetime.timedelta(days=1)
        lookback_opens = _iter_backtest_trading_session_opens(lookback_start_dt, lookback_end_dt)
        if not lookback_opens:
            _log(f"Live lookback skipped for {spec_name}: no prior trading sessions in window.", "yellow")
            continue
        resume_opens = _historic_lookback_resume_dates(scoped_runtime_instance_id, lookback_opens)
        existing_days = max(0, len(lookback_opens) - len(resume_opens))
        if not resume_opens:
            _log(
                f"Live lookback reuse: using existing Nexus history for {spec_name} "
                f"({len(lookback_opens)}/{len(lookback_opens)} trading days already built, scope={history_scope_id[:12]}...).",
                "cyan",
            )
            continue

        # Phase 1 (2026-05-20): if the caller passed a snapshot gap-day list,
        # AFTER the resume-marker skip applies, FURTHER restrict to just those
        # explicit dates. The snapshot already covers everything up through
        # its end_date; we only need to fill in the gap from snapshot+1 to
        # today. The resume-marker filter above guards against re-running any
        # gap day that nexus_processed_trade_contexts already recorded.
        if restrict_to_dates is not None:
            _restrict_set = set(restrict_to_dates)
            _before = len(resume_opens)
            resume_opens = [d for d in resume_opens if d.strftime("%Y-%m-%d") in _restrict_set]
            _log(
                f"[lookback] restricted to {len(resume_opens)} gap day(s) (from {_before}): "
                f"{[d.strftime('%Y-%m-%d') for d in resume_opens]}",
                "cyan",
            )
            if not resume_opens:
                _log(
                    f"Live lookback: snapshot gap empty for {spec_name}; skipping prepass.",
                    "cyan",
                )
                continue

        # Only pre-fetch Alpaca daily closes when the caller provided seed
        # symbols. For discovery instances (base_symbols=[]) Nexus will
        # fetch its own per-day prices just-in-time — same as backtest day-1
        # when `data` was empty. Skipping the prefetch avoids the historic
        # "no price data fetched — skipping" bailout that made the entire
        # live prepass a no-op.
        daily_prices: dict = {}
        if base_symbols:
            _log(
                f"Live lookback: fetching daily prices for {len(base_symbols)} symbols "
                f"({resume_opens[0].strftime('%Y-%m-%d')} to {lookback_end_dt.strftime('%Y-%m-%d')})...",
                "cyan",
            )
            daily_prices = _fetch_daily_close_prices(
                base_symbols,
                resume_opens[0].date(),
                lookback_end_dt.date(),
                alpaca_key,
                alpaca_secret,
                feed=data_feed,  # 2026-04-23 bug-sweep: honor user-selected feed
            ) or {}
            if not daily_prices:
                _log(
                    "Live lookback: no Alpaca daily prices fetched; Nexus will attempt per-day JIT fetches.",
                    "yellow",
                )
        else:
            _log(
                f"Live lookback: no seed symbols — Nexus will self-discover each of the "
                f"{len(resume_opens)} day(s) (base_instance_id={base_runtime_instance_id}).",
                "cyan",
            )

        prepass_spec = dict(spec or {})
        prepass_config = dict(prepass_spec.get("config") or {})
        prepass_conditions = dict(prepass_spec.get("conditions") or {})
        prepass_config["historical_lookback_mode"] = True
        prepass_config["historical_lookback_source"] = "broker_prepass"
        prepass_config["learning_stage_enabled"] = False
        prepass_conditions["historical_lookback_mode"] = True
        prepass_conditions["historical_lookback_source"] = "broker_prepass"
        prepass_conditions["learning_stage_enabled"] = False
        # Raise the LLM cap ONLY for the prepass spec so a 120-day run can
        # parallelise more than a live bar. Live cycles still honour the
        # LIVE_OVERRIDES cap of 4.
        try:
            _lb_llm_cap = int(
                spec_settings.get("lookback_mode_max_llm_calls_per_cycle")
                or os.environ.get("NEXUS_LOOKBACK_MAX_LLM_CALLS_PER_CYCLE", "32")
            )
        except Exception:
            _lb_llm_cap = 32
        prepass_config["nexus_live_max_llm_calls_per_cycle"] = _lb_llm_cap
        prepass_conditions["nexus_live_max_llm_calls_per_cycle"] = _lb_llm_cap
        prepass_spec["config"] = prepass_config
        prepass_spec["conditions"] = prepass_conditions

        temp_strategy_caches: dict = {}
        if existing_days > 0:
            _log(
                f"Live lookback reuse: {existing_days}/{len(lookback_opens)} trading days already built; "
                f"resuming from {resume_opens[0].strftime('%Y-%m-%d')} (scope={history_scope_id[:12]}...).",
                "cyan",
            )
        _lb_start_str = resume_opens[0].strftime("%Y-%m-%d")
        _lb_end_str = lookback_end_dt.strftime("%Y-%m-%d")
        _lb_total = len(resume_opens)
        _log_historic_lookback_banner(
            start=True,
            spec_name=f"{spec_name} (live)",
            start_date=_lb_start_str,
            end_date=_lb_end_str,
            trading_days=_lb_total,
        )
        # Bug-swept 2026-04-21 (Agent-2 H1): wrap the lookback loop in
        # try/finally so if ANY iteration raises uncaught, the UI banner
        # still clears (otherwise snapshot worker keeps publishing a frozen
        # "42/86" dict until process exit).
        try:
            for idx, lookback_time in enumerate(resume_opens, start=1):
                date_str = lookback_time.strftime("%Y-%m-%d")
                if idx == 1 or idx == _lb_total or idx % 5 == 0:
                    _log(
                        f"Live lookback progress {idx}/{_lb_total} | date={date_str}",
                        "cyan",
                    )
                # Publish to the shared module global so the snapshot worker can
                # include it in the LiveState row → UI renders a "Historic
                # lookback 12/86" banner, matching what backtest does via
                # BacktestResults.nexus_lookback.
                globals()["_live_lookback_progress"] = {
                    "current": idx,
                    "total": _lb_total,
                    "current_date": date_str,
                    "start_date": _lb_start_str,
                    "end_date": _lb_end_str,
                    "spec_name": spec_name,
                    "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
                }
                lookback_prices = daily_prices.get(date_str, {}) if daily_prices else {}
                # Soft-warn on empty-price day. Previously this `continue`d out of
                # the whole spec; now we run the strategy anyway and let it do JIT
                # fetches / self-discover, matching backtest tolerance.
                if base_symbols and not lookback_prices:
                    _log(f"  No Alpaca prices for {date_str}; Nexus will use JIT/graph fallback.", "yellow")
                try:
                    # time_increment is passed to `int(...)` downstream so use the
                    # seconds-per-day string, matching the backtest lookback which
                    # forwards the broker's numeric time_increment ("60", "3600",
                    # "86400"). Previously passed "1d" which triggered:
                    #   invalid literal for int() with base 10: '1d'
                    # on every lookback day.
                    run_run_once_strategies(
                        [prepass_spec],
                        base_symbols,
                        lookback_prices,
                        lookback_time,
                        data=None,
                        portfolio_emulator=None,
                        time_increment="86400",
                        alpaca_key=alpaca_key,
                        alpaca_secret=alpaca_secret,
                        strategy_caches=temp_strategy_caches,
                    )
                except BaseException as _e:
                    # 2026-05-22 — was `except Exception`, but LLMCriticalFailure
                    # inherits BaseException (see llm_critical_guard.py:146,
                    # commit 468d4ca) so an Azure-403/5xx storm during live
                    # lookback was escaping uncaught → container exit 1, no
                    # operator alert. Route critical failures through the
                    # dedicated live abort handler (mirrors main-loop live
                    # path at ~line 9321); preserve previous log-and-continue
                    # behaviour for everything else.
                    try:
                        from llm_critical_guard import LLMCriticalFailure
                        _is_llm_critical = isinstance(_e, LLMCriticalFailure)
                    except Exception:
                        _is_llm_critical = False

                    if _is_llm_critical:
                        try:
                            _log(
                                f"  Live lookback {date_str} LLM-CRITICAL: {type(_e).__name__}: {_e}",
                                "red",
                            )
                        except Exception:
                            pass
                        try:
                            from live_critical_abort import handle as _lv_handle
                            _lv_handle(instance_id=str(instance_id), failure=_e)
                        except Exception as _lv_handle_err:
                            try:
                                _log(
                                    f"live_critical_abort handler raised during lookback: {_lv_handle_err}",
                                    "red",
                                )
                            except Exception:
                                pass
                        # Live can't pause (no snapshot model). Exit code 7
                        # matches main-loop live path so the supervisor knows
                        # this was an operator-actionable LLM failure, not a
                        # generic crash. The outer try/finally still runs and
                        # clears the UI lookback banner.
                        import sys as _sys
                        _sys.exit(7)

                    # Not LLM-critical. Re-raise non-Exception BaseException
                    # cases (KeyboardInterrupt, SystemExit) so they propagate
                    # to Python's default handler / outer try/finally; log
                    # and continue for ordinary Exceptions (preserves prior
                    # log-and-skip-day behaviour).
                    if not isinstance(_e, Exception):
                        raise
                    _log(f"  Live lookback {date_str} FAILED: {_e}", "yellow")
                # Mirror the backtest path by writing per-day progress to the DB
                # so the UI progress strip populates during a 120-day warmup.
                try:
                    _nexus_lookback_update_db(idx, _lb_total, date_str, _lb_start_str, _lb_end_str)
                except Exception:
                    pass
        finally:
            try:
                _nexus_lookback_clear_db()
            except Exception:
                pass
            # Clear the live-mode progress marker so the UI banner disappears,
            # even if the loop crashed mid-way.
            globals()["_live_lookback_progress"] = None
        # Post-warmup verification: count outcomes under this scope so
        # operators can see whether the Learning stage will have enough.
        try:
            _conn_oc = get_conn_retry(max_attempts=2, delay=1)
            if _conn_oc is not None:
                try:
                    # Table name is GraphNexusOutcomes (see
                    # graph_nexus_analysis.py OUTCOMES_TABLE constant). Each
                    # row is keyed by instance_id+date and carries the
                    # history_scope_id field that lookback/live share.
                    _oc_count = int(
                        r.db(DB_NAME)
                        .table("GraphNexusOutcomes")
                        .filter({"history_scope_id": history_scope_id})
                        .count()
                        .default(0)
                        .run(_conn_oc)
                    )
                    _log(
                        f"Live lookback warmup check: {_oc_count} outcomes recorded for scope="
                        f"{history_scope_id[:12]} (Learning threshold: 5).",
                        "green" if _oc_count >= 5 else "yellow",
                    )
                finally:
                    try:
                        _conn_oc.close()
                    except Exception:
                        pass
        except Exception:
            pass
        _log_historic_lookback_banner(
            start=False,
            spec_name=f"{spec_name} (live)",
            start_date=_lb_start_str,
            end_date=_lb_end_str,
            trading_days=_lb_total,
        )
        # Merge this spec's accumulated cache into the return dict so the
        # caller can seed the main-loop `_strategy_cache` (preserves peak
        # state, blacklists, momentum watchlist etc. that would otherwise
        # evaporate when `temp_strategy_caches` goes out of scope).
        for _k, _v in (temp_strategy_caches or {}).items():
            if isinstance(_v, dict):
                merged_caches.setdefault(_k, {}).update(_v)
            else:
                merged_caches[_k] = _v
    return merged_caches


def run_strategy(spec, symbol, prices, current_time, data=None, portfolio_emulator=None, time_increment=None):
    """
    Run a single strategy from backend/strategies/<name>.py and return (score, weight_override).
    - score: 1 (buy), 0 (hold), -1 (sell)
    - weight_override: float or None (None means use original weight from spec)
    - time_increment: optional (e.g. "1h", "1d", 3600) for instance/backtest granularity; strategies may use it (e.g. News).
    
    The strategy file must define a class with the same name as the strategy string (PascalCase)
    and a run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None) method.
    
    strategy_cache: per-strategy dict (stored globally in broker) for the strategy to persist data across runs.
    
    The run() method can return:
    - An integer (1, 0, -1) for backward compatibility (weight_override=None, default size, no reason)
    - A tuple (score, weight_override [, size_hint [, reason]]) where weight_override is optional float or None,
      size_hint is optional dict, and reason is optional str explaining the decision.
    Returns: (score, weight_override, size_hint, reason).
    """
    name = (spec.get('strategy') or '').strip()
    config = dict(spec.get('config') or {})
    conditions = dict(spec.get('conditions') or {})
    merged_settings = {}
    merged_settings.update(conditions)
    merged_settings.update(config)
    config = dict(merged_settings)
    conditions = dict(merged_settings)
    if instance_id and not config.get("instance_id"):
        config["instance_id"] = instance_id
        conditions["instance_id"] = instance_id
    price = (prices or {}).get(symbol)
    if not name:
        return (0, None, None, None)
    if name not in _strategy_class_cache:
        _strategy_class_cache[name] = _load_strategy_class(name)
    cls = _strategy_class_cache[name]
    if cls is None:
        _log(f"Strategy class not found for '{name}'. Check that class name matches conversion from strategy name.", "red")
        return (0, None, None, None)
    # Per-strategy cache: strategies can store/load data here (e.g. trained model for Candles).
    strategy_cache = _strategy_cache.setdefault(name, {})
    try:
        instance = cls()
        result = instance.run(symbol, price, current_time, config, conditions, data, portfolio_emulator=portfolio_emulator, strategy_cache=strategy_cache, time_increment=time_increment)
        
        # Handle return value: int or tuple (score [, weight_override [, size_hint [, reason]]])
        reason = None
        if isinstance(result, tuple) and len(result) >= 1:
            score = result[0]
            weight_override = result[1] if len(result) > 1 else None
            size_hint = result[2] if len(result) > 2 else None
            reason = result[3] if len(result) > 3 else None
            if reason is not None and not isinstance(reason, str):
                reason = str(reason)[:500] if reason else None
            # Validate weight_override
            if weight_override is not None:
                try:
                    weight_override = float(weight_override)
                    if weight_override < 0 or weight_override > 1:
                        _log(f"Strategy '{name}' returned invalid weight_override {weight_override} (must be 0-1). Using original weight.", "yellow")
                        weight_override = None
                except (TypeError, ValueError):
                    _log(f"Strategy '{name}' returned invalid weight_override type. Using original weight.", "yellow")
                    weight_override = None
            # Validate size_hint: must be dict with sell_fraction (0-1) and/or buy_cash (positive)
            if size_hint is not None and not isinstance(size_hint, dict):
                size_hint = None
            if size_hint is not None:
                clean_hint = {}
                if "sell_fraction" in size_hint:
                    try:
                        sf = float(size_hint["sell_fraction"])
                        if 0 < sf <= 1:
                            clean_hint["sell_fraction"] = sf
                    except (TypeError, ValueError):
                        pass
                if "buy_cash" in size_hint:
                    try:
                        bc = float(size_hint["buy_cash"])
                        if bc > 0:
                            clean_hint["buy_cash"] = bc
                    except (TypeError, ValueError):
                        pass
                size_hint = clean_hint if clean_hint else None
            if weight_override is not None:
                _log(f"Strategy '{name}' returned score: {score}, weight_override: {weight_override}", "cyan")
            else:
                _log(f"Strategy '{name}' returned score: {score}", "white")
            return (score, weight_override, size_hint, reason)
        else:
            # Backward compatibility: integer result
            score = int(result) if result is not None else 0
            _log(f"Strategy '{name}' returned score: {score}", "white")
            return (score, None, None, None)
    except Exception as e:
        _log(f"Strategy '{name}' error: {e}", "red")
        return (0, None, None, None)

def aggregate_weighted_scores(weighted_scores, threshold=0.1):
    """
    Combine weighted scores into one decision.
    weighted_scores: list of (weight, score) with score in {1, 0, -1}.
    Returns: 1 (buy), 0 (hold), or -1 (sell).
    """
    if not weighted_scores:
        return 0
    total_weight = sum(w for w, _ in weighted_scores)
    if total_weight <= 0:
        return 0
    weighted_sum = sum(w * s for w, s in weighted_scores)
    normalized = weighted_sum / total_weight
    if normalized > threshold:
        return 1
    if normalized < -threshold:
        return -1
    return 0


def get_position_sizing_trade_size(spec, symbol, decision, price, portfolio_emulator, prices=None):
    """
    Call the position_sizing strategy (if present) to get trade size. Used only after a buy/sell decision.
    spec: strategy spec dict (config, etc.)
    decision: 1 (buy) or -1 (sell)
    Returns: dict with 'buy_cash' and/or 'sell_fraction', or None to use broker defaults.
    """
    if spec is None or decision == 0:
        return None
    side = "buy" if decision == 1 else "sell"
    config = spec.get("config") or {}
    # Use initial account value for risk % so position size is based on starting capital, not current (e.g. after other trades this bar).
    account_size = 0.0
    if portfolio_emulator is not None:
        account_size = portfolio_emulator.get_initial_value() or 0.0
    if account_size <= 0:
        account_size = 100000.0
    try:
        name = (spec.get("strategy") or "").strip()
        if name.lower() != "position_sizing":
            return None
        cls = _load_strategy_class(name)
        if cls is None:
            return None
        instance = cls()
        if not hasattr(instance, "get_trade_size"):
            return None
        result = instance.get_trade_size(
            symbol, side, price, account_size, config, portfolio_emulator=portfolio_emulator
        )
        return result
    except Exception as e:
        _log(f"Position sizing error: {e}", "red")
        return None


def run_post_decision_strategies(specs, symbol, decision, price, portfolio_emulator, prices=None, strategy_summary=None, price_history_symbol=None):
    """
    Run all post-decision strategy specs in execution order. Merge hints (buy_cash, sell_fraction, etc.)
    and allow strategies with get_final_decision to override the trading decision (e.g. ai-trading-decision).
    strategy_summary: list of {strategy, weight, decision, reason} from all pre-decision strategies that voted.
    price_history_symbol: list of last 30 bars (or days) for this symbol for get_final_decision.
    Returns: (hints_dict, final_decision_override, post_decision_trace).
    post_decision_trace contains structured records like
    {strategy, decision, reason} for strategies that explicitly produced a
    final-decision vote.
    """
    if not specs:
        return {}, None, []
    side = "buy" if decision == 1 else "sell"
    account_size = 0.0
    if portfolio_emulator is not None:
        account_size = portfolio_emulator.get_initial_value() or 0.0
    if account_size <= 0:
        account_size = 100000.0
    hints = {}
    final_decision_override = None
    post_decision_trace = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("strategy"):
            continue
        name = (spec.get("strategy") or "").strip()
        config = spec.get("config") or {}
        try:
            cls = _load_strategy_class(name)
            if cls is None:
                continue
            instance = cls()
            if hasattr(instance, "get_trade_size") and decision != 0:
                result = instance.get_trade_size(
                    symbol, side, price, account_size, config, portfolio_emulator=portfolio_emulator
                )
                if isinstance(result, dict):
                    for k, v in result.items():
                        if v is not None:
                            hints[k] = v
            if hasattr(instance, "get_post_decision_hints"):
                extra = instance.get_post_decision_hints(
                    symbol, decision, price, config, portfolio_emulator=portfolio_emulator, prices=prices
                )
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if v is not None:
                            hints[k] = v
            # Strategies like ai-trading-decision can override the final decision (run after size hints)
            if hasattr(instance, "get_final_decision"):
                raw_override = instance.get_final_decision(
                    symbol, decision, strategy_summary or [], price_history_symbol or [],
                    config, portfolio_emulator=portfolio_emulator, prices=prices,
                )
                parsed_override = None
                parsed_reason = ""
                if raw_override in (1, 0, -1):
                    parsed_override = int(raw_override)
                elif isinstance(raw_override, (tuple, list)) and raw_override:
                    head = raw_override[0]
                    if head in (1, 0, -1):
                        parsed_override = int(head)
                        if len(raw_override) >= 4 and raw_override[3] is not None:
                            parsed_reason = str(raw_override[3]).strip()
                        elif len(raw_override) >= 2 and raw_override[1] is not None:
                            parsed_reason = str(raw_override[1]).strip()
                if parsed_override is not None:
                    final_decision_override = parsed_override
                    post_decision_trace.append({
                        "strategy": name,
                        "decision": parsed_override,
                        "reason": parsed_reason[:1500],
                    })
        except Exception as e:
            _log(f"Post-decision strategy '{name}' error: {e}", "red")
    return hints, final_decision_override, post_decision_trace


# ---------------------------------------------------------------------------
# Socket.IO: connect to server (communicate with instance via server)
# ---------------------------------------------------------------------------
shutdown_requested = False
strategies_reload_requested = False
_cached_strategies = None
_strategy_row_id = None
_backtest_strategy_schema = None  # Snapshot of strategy (name + strategies) at backtest start; stored in BacktestResults.strategy_schema

def _log(msg, color="white"):
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service="BROKER")
    except Exception:
        print(f"[BROKER] {msg}")


def _convert_datetimes_to_iso(obj):
    """Recursively convert datetime-like objects to ISO strings for RethinkDB."""
    if isinstance(obj, dict):
        return {k: _convert_datetimes_to_iso(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_datetimes_to_iso(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_convert_datetimes_to_iso(item) for item in obj)
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            return obj
    return obj

def _open_live_trading_log(instance_id_val: str) -> None:
    """Open the per-instance live-trading log file and attach it to the logger
    as a secondary sink. Call once at live-mode startup, BEFORE the adapter
    is built so startup errors are captured. Safe no-op on failure.

    2026-04-22: idempotent — if called again (e.g. re-entry on exception
    retry, SIGHUP reopen), close the prior file handle first to avoid fd
    leaks.
    """
    global _live_trading_log_file, _live_trading_log_path, _live_trading_started_at
    # 2026-04-22 Round 4 Fix 8: DETACH the logger's context log BEFORE closing
    # the prior file handle. Previously we closed `_prior` first; if a
    # concurrent writer (snapshot worker, stream callback, strategy thread)
    # tried to log during the brief window between close() and the new
    # `set_context_log_file(..., f)` below, it would hit a closed file
    # descriptor and raise. Detaching first makes the logger fall back to
    # its root sink for that window — silent but correct.
    _prior = _live_trading_log_file
    if _prior is not None:
        try:
            from intellistock_logger import intellistock_logger as _logger
            _logger.set_context_log_file("live_trading", None)
        except Exception:
            pass
        try:
            _prior.flush()
        except Exception:
            pass
        try:
            _prior.close()
        except Exception:
            pass
        _live_trading_log_file = None
    try:
        import live_state as _ls_mod
    except Exception:
        return
    try:
        f, path = _ls_mod.open_live_log(instance_id_val)
        _live_trading_log_file = f
        _live_trading_log_path = path
        _live_trading_started_at = time.time()
    except Exception:
        return
    try:
        from intellistock_logger import intellistock_logger as _logger
        _logger.set_context_log_file("live_trading", f)
    except Exception:
        pass


def _close_live_trading_log(reason: str = "shutdown") -> None:
    global _live_trading_log_file, _live_trading_log_path
    try:
        from intellistock_logger import intellistock_logger as _logger
        _logger.close_context_log_file("live_trading")
    except Exception:
        pass
    try:
        import live_state as _ls_mod
        _ls_mod.close_live_log(_live_trading_log_file, reason=reason)
    except Exception:
        pass
    _live_trading_log_file = None
    _live_trading_log_path = None


def _load_containment_state(instance_id_val):
    """Containment state for this instance from AlphaState (Tasks 0/6).

    Missing row or missing table => not contained (``{}``). A READ FAILURE
    returns ``None`` so ``legacy_live_order_block`` fails closed — a live
    instance whose containment state cannot be read must not trade.
    Retried (audit 2026-07-18) so one transient RethinkDB blip at boot does
    not latch the gate for the whole process lifetime."""
    try:
        conn = get_conn_retry(max_attempts=3, delay=2)
        if conn is None:
            conn = get_conn()
        try:
            tables = list(r.db(DB_NAME).table_list().run(conn))
            if 'AlphaState' not in tables:
                return {}
            row = r.db(DB_NAME).table('AlphaState').get(
                f"containment:{instance_id_val}").run(conn)
            return (row or {}).get("payload") or {}
        finally:
            conn.close()
    except Exception:
        return None


_containment_gate_logged: set = set()


def _write_containment_gate_event(instance_id_val, sym, side, reason):
    """Best-effort hard-durability GATE event; storage failure never
    weakens the block itself."""
    try:
        conn = get_conn()
        try:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            eid = "gate-" + hashlib.sha256(
                f"{instance_id_val}|{sym}|{side}|{ts}".encode()).hexdigest()[:24]
            r.db(DB_NAME).table('AlphaEvents').insert({
                "id": eid, "kind": "GATE",
                "payload": {"instance_id": instance_id_val, "symbol": sym,
                            "side": side, "reason": reason},
                "created_at": ts,
            }, durability="hard").run(conn)
        finally:
            conn.close()
    except Exception:
        pass


def _install_legacy_containment_gate(adapter, instance_id_val):
    """Task 6: when RethinkDB containment disables legacy order authority for
    this instance, wrap the adapter's ``execute_signal`` so EVERY legacy live
    submission (buys AND sells) is blocked at the single choke point. The
    typed reduce-only emergency path submits through ``submit_order``
    directly and is unaffected. No OFF-mode setting or healthy mark-health
    result clears containment — only an authenticated Task 18 promotion."""
    containment = _load_containment_state(instance_id_val)
    if isinstance(containment, dict) and not containment.get(
            "legacy_order_authority_disabled"):
        return adapter
    from benchmark_alpha.risk import legacy_live_order_block
    orig_execute = adapter.execute_signal

    def _gated_execute_signal(sym, sig, *args, **kwargs):
        side = "buy" if (sig or 0) > 0 else "sell"
        reason = legacy_live_order_block(instance_id_val, side, containment)
        if reason:
            gate_key = (str(sym).upper(), side)
            if gate_key not in _containment_gate_logged:
                _containment_gate_logged.add(gate_key)
                try:
                    _log(f"[containment] {reason} ({sym})", "red")
                except Exception:
                    pass
            _write_containment_gate_event(instance_id_val, str(sym), side, reason)
            return False
        return orig_execute(sym, sig, *args, **kwargs)

    adapter.execute_signal = _gated_execute_signal
    try:
        _log(
            f"[containment] legacy order authority DISABLED for {instance_id_val} "
            "— all legacy live submissions will be blocked",
            "red",
        )
    except Exception:
        pass
    return adapter


def _compute_live_state_snapshot(instance_id_val: str, adapter) -> dict:
    """Build the dict to upsert into LiveState. Read-only over the adapter.

    2026-05-05: instrumented with per-stage timing. Records the LAST
    checkpoint entered into a module global so the watchdog handler
    in _live_state_snapshot_worker can name the wedged stage when the
    20s watchdog fires.
    """
    import copy
    _stage_start = time.time()
    def _enter(name):
        # Module-global checkpoint: watchdog reads this when timeout fires.
        globals()["_snap_last_checkpoint"] = (name, time.time())
    def _stage_log(name):
        nonlocal _stage_start
        _now = time.time()
        _elapsed = _now - _stage_start
        if _elapsed >= 8.0:
            try:
                _log(f"snap:{name} took {_elapsed:.1f}s (slow path)", "yellow")
            except Exception:
                pass
        _stage_start = _now
        _enter(name + "_done")
    _enter("start")
    # 2026-05-05 third pass: snapshot is cache-only, so no session-rebuild
    # check needed. _rebuild_session() is now triggered by the pre-cycle
    # hook on its own retry path (see broker.py:6430-ish). Snapshot stays
    # purely in-memory.
    now_ts = time.time()
    uptime = 0
    try:
        if _live_trading_started_at:
            uptime = int(max(0, now_ts - _live_trading_started_at))
    except Exception:
        uptime = 0

    # Prices: prefer adapter's _last_prices cache; fall back to nothing.
    last_prices = {}
    try:
        last_prices = dict(getattr(adapter, "_last_prices", {}) or {})
    except Exception:
        last_prices = {}

    # Pull Alpaca's authoritative view of the account in ONE REST call so
    # Live Trading UI mirrors what the user sees in the Alpaca app: cash,
    # equity (= cash + positions at Alpaca's pricing), buying_power, and
    # last_equity (prev-day close equity, used for Daily Change).
    #
    # Previously this function read stale `_cash` + recomputed equity from
    # local `_positions` × `_last_prices` — which diverged from Alpaca any
    # time the user manually traded, deposited cash, or the local price
    # cache went stale. See screenshot: Alpaca shows cash=$6,884.54 while
    # broker reported cash=$0 because `_cash` hadn't been refreshed.
    cash = 0.0
    buying_power = 0.0
    alpaca_equity = 0.0
    last_equity = 0.0
    try:
        _enter("refresh_account_in")
        # 2026-05-05 third pass: snapshot is cache-only; refresh_account
        # returns either the cached DTO (instant) or raises (cold cache).
        # 12s bound is plenty AND keeps account_bound + positions_bound
        # (12+12=24s) safely under the 35s watchdog.
        _acct = _bounded_adapter_call("refresh_account", adapter.refresh_account, 12.0)
        _stage_log("refresh_account")
        cash = float(getattr(_acct, "cash", 0.0) or 0.0)
        buying_power = float(getattr(_acct, "buying_power", 0.0) or 0.0)
        alpaca_equity = float(getattr(_acct, "equity", 0.0) or 0.0)
        last_equity = float(getattr(_acct, "last_equity", 0.0) or 0.0)
    except Exception as _acct_e:
        # Fall back to cached values on transient Alpaca outage so the UI
        # shows something rather than $0/$0/$0.
        try:
            _log(f"refresh_account failed in snapshot: {type(_acct_e).__name__}: {_acct_e}; "
                 f"falling back to cached _cash/_initial_value", "yellow")
        except NameError:
            pass
        try:
            cash = float(getattr(adapter, "_cash", 0.0) or 0.0)
        except Exception:
            cash = 0.0

    initial_value = 0.0
    try:
        initial_value = float(getattr(adapter, "_initial_value", 0.0) or 0.0)
    except Exception:
        initial_value = 0.0

    # Equity = Alpaca's authoritative equity when we got it; otherwise
    # reconstruct cash + positions*last_prices.
    equity = alpaca_equity if alpaca_equity > 0 else cash
    positions_payload = []
    # Prefer live Alpaca positions (DTOs with authoritative avg_entry + market_value)
    # so the UI table matches the Alpaca app. Falls back to cached `_positions`
    # on transient REST errors.
    live_position_dtos = None
    try:
        _enter("refresh_positions_in")
        live_position_dtos = _bounded_adapter_call("refresh_positions", adapter.refresh_positions, 12.0)
        _stage_log("refresh_positions")
    except Exception as _pos_e:
        try:
            _log(f"refresh_positions failed in snapshot: "
                 f"{type(_pos_e).__name__}: {_pos_e}; using cached _positions",
                 "yellow")
        except NameError:
            pass
        live_position_dtos = None

    try:
        if live_position_dtos is not None:
            for p in live_position_dtos:
                sym = getattr(p, "symbol", "") or ""
                qty_f = float(getattr(p, "qty", 0.0) or 0.0)
                avg_entry = float(getattr(p, "avg_entry_price", 0.0) or 0.0)
                market_value = float(getattr(p, "market_value", 0.0) or 0.0)
                # Derive last price from Alpaca's own market_value/qty if
                # available (already split/dividend-adjusted); else cache.
                price = market_value / qty_f if qty_f else float(last_prices.get(sym, 0.0) or 0.0)
                if alpaca_equity <= 0:
                    equity += market_value
                unrealized = (price - avg_entry) * qty_f if avg_entry and price else 0.0
                unrealized_pct = ((price / avg_entry) - 1.0) * 100.0 if avg_entry else 0.0
                positions_payload.append({
                    "symbol": sym,
                    "qty": qty_f,
                    "avg_entry_price": avg_entry if avg_entry else None,
                    "last_price": price,
                    "market_value": market_value,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_pct": unrealized_pct,
                })
            # Done — skip the cached-fallback branch below.
            raw_positions = {}
        else:
            raw_positions = dict(getattr(adapter, "_positions", {}) or {})
        for sym, qty in raw_positions.items():
            try:
                qty_f = float(qty or 0.0)
            except Exception:
                qty_f = 0.0
            price = float(last_prices.get(sym, 0.0) or 0.0)
            market_value = qty_f * price
            # Only re-sum positions into equity if we had to fall back
            # (Alpaca's equity already includes positions at their pricing).
            if alpaca_equity <= 0:
                equity += market_value
            # Best-effort avg entry: scan _trades in reverse for latest BUY.
            avg_entry = None
            try:
                for tr in reversed(getattr(adapter, "_trades", []) or []):
                    if tr.get("symbol") == sym and str(tr.get("side", tr.get("type", ""))).lower() == "buy":
                        avg_entry = float(tr.get("price") or 0.0)
                        break
            except Exception:
                avg_entry = None
            unrealized = (price - avg_entry) * qty_f if avg_entry and price else 0.0
            unrealized_pct = ((price / avg_entry) - 1.0) * 100.0 if avg_entry else 0.0
            positions_payload.append({
                "symbol": sym,
                "qty": qty_f,
                "avg_entry_price": avg_entry,
                "last_price": price,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
            })
    except Exception:
        positions_payload = []

    # Recent trades (cap to MAX_RECENT_TRADES, newest first).
    recent_trades = []
    try:
        from live_state import MAX_RECENT_TRADES as _MAX_TRADES
    except Exception:
        _MAX_TRADES = 100
    try:
        raw_trades = list(getattr(adapter, "_trades", []) or [])
        for tr in raw_trades[-_MAX_TRADES:]:
            ts = tr.get("timestamp") or tr.get("time") or tr.get("ts")
            try:
                if hasattr(ts, "isoformat"):
                    ts = ts.isoformat()
            except Exception:
                pass
            # Fix 2026-04-22: AlpacaAdapter writes fills to _trades using
            # the PortfolioEmulator schema (`ticker` / `action` / `shares`)
            # not the broker schema (`symbol` / `side` / `qty`). Previously
            # this snapshot read only broker keys, so the Live Trading UI's
            # Recent Executions panel rendered `symbol=null, side=""` for
            # every entry. Fall back to PortfolioEmulator keys to keep
            # adapter + PortfolioEmulator consumers source-compatible.
            recent_trades.append({
                "ts": ts,
                "symbol": tr.get("symbol") or tr.get("ticker"),
                "side": str(tr.get("side") or tr.get("type") or tr.get("action") or "").lower(),
                "qty": float(tr.get("shares", tr.get("qty", 0.0)) or 0.0),
                "price": float(tr.get("price", 0.0) or 0.0),
                "order_id": tr.get("order_id"),
            })
        recent_trades.reverse()  # newest first
    except Exception:
        recent_trades = []

    # Portfolio history (cap).
    portfolio_history = []
    try:
        from live_state import MAX_PORTFOLIO_HISTORY as _MAX_PH
    except Exception:
        _MAX_PH = 500
    try:
        raw_snaps = list(getattr(adapter, "_portfolio_snapshots", []) or [])
        for s in raw_snaps[-_MAX_PH:]:
            ts = s.get("timestamp")
            try:
                if hasattr(ts, "isoformat"):
                    ts = ts.isoformat()
            except Exception:
                pass
            portfolio_history.append({"ts": ts, "value": float(s.get("value") or 0.0)})
    except Exception:
        portfolio_history = []

    # 2026-06-11 fix: the snapshot list only grows on live (non-IDLE) ticks, so
    # it freezes at RTH close while Alpaca equity keeps moving overnight (24/5).
    # Append the fresh account equity as the latest served point so the chart
    # never goes stale. Ephemeral — recomputed from fresh equity each 3s tick.
    try:
        from live_state import append_current_equity_point as _append_eq
        _now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        portfolio_history = _append_eq(portfolio_history, equity, _now_iso, _MAX_PH)
    except Exception:
        pass

    total_pnl = equity - initial_value
    total_pnl_pct = ((equity / initial_value) - 1.0) * 100.0 if initial_value > 0 else 0.0
    # Day PnL mirrors Alpaca's "Daily Change" exactly: equity − last_equity
    # (last_equity = yesterday's close equity from the Alpaca account).
    # Previously reconstructed from portfolio_history which diverged from
    # Alpaca whenever the broker hadn't snapshotted at market open, or
    # when reporting weekends/holidays with no history row.
    day_pnl = 0.0
    day_pnl_pct = 0.0
    if last_equity > 0 and alpaca_equity > 0:
        day_pnl = alpaca_equity - last_equity
        day_pnl_pct = ((alpaca_equity / last_equity) - 1.0) * 100.0
    else:
        # Fallback: recover from portfolio_history (pre-Alpaca-fetch path).
        try:
            today_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            for s in portfolio_history:
                ts = s.get("ts") or ""
                if isinstance(ts, str) and ts.startswith(today_iso):
                    base = float(s.get("value") or 0.0)
                    if base > 0:
                        day_pnl = equity - base
                        day_pnl_pct = ((equity / base) - 1.0) * 100.0
                    break
        except Exception:
            pass

    health = {}
    try:
        _enter("health_check_in")
        h = adapter.health_check()
        _stage_log("health_check")
        health = {
            "auth_fresh": bool(getattr(h, "auth_fresh", False)),
            "trade_stream": bool(getattr(h, "trade_updates_connected", False)),
            "last_heartbeat": getattr(h, "last_heartbeat_utc", None).isoformat() if getattr(h, "last_heartbeat_utc", None) else None,
            "errors": list(getattr(h, "errors", []) or []),
        }
    except Exception:
        health = {}

    # Determine status
    if shutdown_requested:
        status_str = "halted"
    elif _live_trading_stop_event.is_set():
        status_str = "halted"
    else:
        status_str = "active"

    broker_name = type(adapter).__name__
    # Pull lookback-progress snapshot (module global) so the UI can render
    # a "Historic lookback N/M" banner on /instances/{id}/live during the
    # 120-day warmup. Mirrors BacktestResults.nexus_lookback for backtests.
    _lb_snap = globals().get("_live_lookback_progress")
    # 2026-05-07 strategy-tick diagnostic state. Snapshot daemon copies the
    # strategy worker thread's last phase/wake info into the live-state
    # payload so the UI / API can show "tick #4 wedged at phase=rh_refresh
    # since 22:00:01" instead of just falling silent.
    try:
        _strat_tick = dict(_strategy_tick_state)
    except Exception:
        _strat_tick = {}
    # Task 6: typed mark health over the adapter's current marks, and the
    # persisted alpha risk state (telemetry; held count comes from snapshot
    # positions, never per-cycle action counters).
    _mark_health_payload = None
    try:
        from benchmark_alpha.risk import evaluate_mark_health as _emh
        _mh_marks = adapter.get_market_marks() if hasattr(adapter, "get_market_marks") else {}
        _mh_syms = [p.get("symbol") for p in (positions_payload or [])
                    if isinstance(p, dict) and p.get("symbol")]
        _mh = _emh(_mh_syms, _mh_marks,
                   datetime.datetime.now(datetime.timezone.utc))
        _mark_health_payload = {"ok": _mh.ok, "entries": list(_mh.entries)}
    except Exception:
        _mark_health_payload = None
    _risk_payload = None
    try:
        _risk_conn = get_conn()
        try:
            _risk_row = r.db(DB_NAME).table('AlphaState').get(
                f"risk:{instance_id_val}").run(_risk_conn)
            if _risk_row:
                _risk_payload = dict(_risk_row.get("payload") or {})
            # Audit 2026-07-18: the independent watchdog compares direct
            # broker truth against live_snapshot:<id>, which nothing wrote —
            # detection was permanently fail-open. Persist the broker
            # process's view here (soft durability: monitoring telemetry on
            # a 3s cadence, replaced each tick).
            try:
                _snap_marks = {
                    str(_sym): float(_mk.price)
                    for _sym, _mk in (adapter.get_market_marks() or {}).items()
                } if hasattr(adapter, "get_market_marks") else {}
                r.db(DB_NAME).table('AlphaState').insert({
                    "id": f"live_snapshot:{instance_id_val}",
                    "version": 0,
                    "payload": {"equity": float(equity or 0.0),
                                "marks": _snap_marks},
                    "updated_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                }, conflict="replace", durability="soft").run(_risk_conn)
            except Exception:
                pass
        finally:
            _risk_conn.close()
    except Exception:
        _risk_payload = None
    return {
        "trading_active": status_str == "active",
        "status": status_str,
        "uptime_sec": uptime,
        "cash": cash,
        "equity": equity,
        "buying_power": buying_power,
        "last_equity": last_equity,
        "initial_value": initial_value,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "positions": positions_payload,
        "held_positions_count": len(positions_payload or []),
        "mark_health": _mark_health_payload,
        "risk": _risk_payload,
        "recent_trades": recent_trades,
        "portfolio_history": portfolio_history,
        "broker": {
            "name": broker_name,
            "account_id": getattr(adapter, "_account_id", None),
            "paper": bool(getattr(adapter, "_paper", True)),
            "health": health,
        },
        "log_file_path": _live_trading_log_path,
        "lookback": dict(_lb_snap) if isinstance(_lb_snap, dict) else None,
        "strategy_tick": _strat_tick,
    }


def _live_state_snapshot_worker(instance_id_val: str, adapter) -> None:
    """Upsert LiveState every 3s until stop_event fires.

    2026-04-22 Round 4 Fix 4: surface silent crashes. Previous version
    caught all exceptions and reconnected without logging, so a persistent
    snapshot failure (e.g. RethinkDB down, live_state.upsert schema drift)
    would stop the UI from updating without any operator signal. Now we
    log the exception, count consecutive failures, and emit a Discord
    strategy-error alert on the 5th failure so the pager fires before a
    trader wonders why the live page hasn't moved in 15 seconds.
    """
    interval = float(os.environ.get("LIVE_STATE_SNAPSHOT_INTERVAL_SEC", "3"))
    # 2026-05-05 live-hang investigation: bound the entire tick (compute +
    # RethinkDB upsert) under a watchdog. Either Robinhood session reads or
    # rdb writes can wedge silently; on TimeoutError, walk away and let the
    # zombie thread finish whenever the OS unblocks it.
    # 2026-05-05 third pass: live_broker_fetch (which serves the working
    # live trading UI) uses RobinhoodClient with default timeout_sec=20.
    # Our prior 20s watchdog + 12s adapter bound were both TIGHTER than
    # RH's typical response window, causing premature aborts. Raise to
    # 35s watchdog + 25s adapter bound — RH's 20s requests-level timeout
    # then has room to fire FIRST and surface a real exception, while
    # adapter calls slow enough to complete within their natural window
    # actually return data instead of being prematurely abandoned.
    snapshot_watchdog_sec = float(
        os.environ.get("LIVE_STATE_SNAPSHOT_WATCHDOG_SEC", "35")
    )
    consecutive_watchdog_timeouts = 0
    conn_local = None
    consecutive_failures = 0
    alert_emitted = False  # one-shot until a success resets it
    # 2026-05-05 bug-sweep finding (CRITICAL): with max_workers=2 and an
    # unbounded queue, two consecutive wedges saturate the pool and every
    # subsequent submit() queues forever. Track the previous future and SKIP
    # submission while it's still running — semantically only one snapshot at
    # a time matters anyway. Bounds zombie thread count to <=1, eliminates
    # the queue-growth memory leak.
    prev_fut = None
    _tick_started_at = 0.0
    try:
        import live_state as _ls_mod
    except Exception:
        return

    def _snapshot_tick_blocking(_conn):
        _payload = _compute_live_state_snapshot(instance_id_val, adapter)
        _ls_mod.upsert_live_state(r, _conn, instance_id_val, _payload)

    while not _live_trading_stop_event.is_set() and not shutdown_requested:
        try:
            # Skip submission if the prior tick's worker hasn't returned yet
            # (it's wedged on a TCP read). Log YELLOW once per stretch so
            # operators see the issue, but DON'T pile up queued tasks.
            if prev_fut is not None and not prev_fut.done():
                consecutive_watchdog_timeouts += 1
                if consecutive_watchdog_timeouts == 1 or consecutive_watchdog_timeouts % 20 == 0:
                    try:
                        _log(
                            f"LiveState snapshot prior-tick STILL RUNNING (zombie wedge "
                            f"#{consecutive_watchdog_timeouts}); skipping this tick. "
                            f"Will recover automatically when OS TCP timeout fires (~60-180s).",
                            "yellow",
                        )
                    except Exception:
                        pass
                _live_trading_stop_event.wait(interval)
                continue
            if conn_local is None:
                conn_local = get_conn_retry(max_attempts=3, delay=2)
            if conn_local is None:
                _live_trading_stop_event.wait(interval)
                continue
            _fut = _SNAPSHOT_EXECUTOR.submit(_snapshot_tick_blocking, conn_local)
            prev_fut = _fut
            _tick_started_at = time.time()
            try:
                _fut.result(timeout=snapshot_watchdog_sec)
            except _live_cf.TimeoutError:
                consecutive_watchdog_timeouts += 1
                # Log on first, every 5th, and 50th to avoid spam while
                # still surfacing persistent wedges.
                if (
                    consecutive_watchdog_timeouts == 1
                    or consecutive_watchdog_timeouts % 5 == 0
                ):
                    # Pull the last-entered checkpoint set by
                    # _compute_live_state_snapshot's _enter() helper. Names
                    # the wedged stage so we can pinpoint the exact call.
                    _ckpt = globals().get("_snap_last_checkpoint")
                    _ckpt_str = "?"
                    if isinstance(_ckpt, tuple) and len(_ckpt) == 2:
                        _name, _ts = _ckpt
                        try:
                            _age = time.time() - float(_ts)
                            _ckpt_str = f"{_name} (entered {_age:.0f}s ago)"
                        except Exception:
                            _ckpt_str = str(_name)
                    # 2026-05-05 third pass: include tick-age (time since
                    # the bounded watchdog future started) so operators can
                    # tell whether a "5s ago" checkpoint means a brand-new
                    # stage or a fall-through-after-prior-timeout sequence.
                    _tick_age_str = "?"
                    try:
                        _tick_age = time.time() - float(_tick_started_at) if _tick_started_at else 0.0
                        _tick_age_str = f"{_tick_age:.0f}s"
                    except Exception:
                        _tick_age_str = "?"
                    try:
                        _log(
                            f"LiveState snapshot watchdog timeout (>{snapshot_watchdog_sec:.0f}s) "
                            f"#{consecutive_watchdog_timeouts} wedged at: {_ckpt_str}, "
                            f"tick age {_tick_age_str}; leaving worker to finish in background",
                            "yellow",
                        )
                    except Exception:
                        pass
                # Drop the (likely wedged) rdb conn so next tick reconnects.
                # noreply_wait=False is critical: the default rdb close()
                # sends a NOREPLY_WAIT query through the socket and waits
                # for response — if the socket is half-dead (which is why
                # we hit the watchdog), close() itself blocks the parent
                # snapshot thread.
                try:
                    if conn_local:
                        conn_local.close(noreply_wait=False)
                except Exception:
                    pass
                conn_local = None
                _live_trading_stop_event.wait(interval)
                continue
            consecutive_watchdog_timeouts = 0
            if consecutive_failures:
                _log(
                    f"LiveState snapshot recovered after {consecutive_failures} failure(s)",
                    "green",
                )
            consecutive_failures = 0
            alert_emitted = False
            # 2026-05-06 — broker liveness heartbeat. Snapshot worker runs
            # every 3s; emit a "Loop alive" log every LOOP_LOG_HEARTBEAT_SEC
            # (300s) so a quiet strategy loop is distinguishable from a
            # wedged broker. Includes a tick counter so successive
            # heartbeats prove forward progress.
            try:
                _hb_count = int(globals().get("_snap_hb_tick_count") or 0) + 1
                globals()["_snap_hb_tick_count"] = _hb_count
                _hb_now = time.time()
                _hb_last = float(globals().get("_snap_hb_last_at") or 0.0)
                if (_hb_now - _hb_last) >= float(LOOP_LOG_HEARTBEAT_SEC):
                    _log(
                        f"Broker alive | snap-tick #{_hb_count} | "
                        f"interval={LOOP_LOG_HEARTBEAT_SEC}s heartbeat",
                        "white",
                    )
                    globals()["_snap_hb_last_at"] = _hb_now
            except Exception:
                pass
        except Exception as _e:
            consecutive_failures += 1
            try:
                _log(
                    f"LiveState snapshot failure #{consecutive_failures}: {type(_e).__name__}: {_e}",
                    "yellow",
                )
            except Exception:
                pass
            if consecutive_failures >= 5 and not alert_emitted:
                alert_emitted = True
                try:
                    from live_alerts import alert_strategy_error as _ase
                    _ase(
                        instance_id=str(instance_id_val),
                        tag="live_state_snapshot_worker",
                        message=(
                            f"{consecutive_failures} consecutive snapshot upserts failed. "
                            f"Live UI will appear frozen. Last error: "
                            f"{type(_e).__name__}: {_e}"
                        ),
                    )
                except Exception:
                    pass
            try:
                if conn_local:
                    conn_local.close(noreply_wait=False)
            except Exception:
                pass
            conn_local = None
        _live_trading_stop_event.wait(interval)
    try:
        if conn_local:
            conn_local.close(noreply_wait=False)
    except Exception:
        pass


def _execute_live_command(adapter, cmd: dict) -> tuple[bool, str, dict]:
    """Run one command against the live adapter. Returns (success, error, result)."""
    ctype = str(cmd.get("type") or "").lower()
    payload = dict(cmd.get("payload") or {})
    try:
        if ctype == "halt":
            # Halt = set runCommand=False on the instance so the supervisor
            # kills the broker subprocess. Does NOT liquidate. Also cancel
            # any open orders so the halt is clean.
            #
            # Order is important: flip runCommand=False FIRST so the main
            # strategy loop stops submitting new orders on its next tick.
            # Then cancel open orders, THEN do a best-effort second pass to
            # catch anything submitted between the flip and the first cancel
            # (the trading loop's iteration can race the DB update).
            halt_conn = get_conn_retry(max_attempts=3, delay=2)
            _halt_reason_text = str(payload.get("reason") or "manual halt via UI")
            try:
                if halt_conn is not None:
                    try:
                        r.db(DB_NAME).table("Instances").get(str(instance_id)).update({
                            "runCommand": False,
                            "halt_reason": _halt_reason_text,
                            "halted_at": r.now(),
                        }).run(halt_conn)
                    except Exception as _he:
                        return (False, f"instances update failed: {_he}", {})
                # 2026-04-22 Round 4 Fix 7: page on manual halt. UI users and
                # the supervisor both route through this branch; a Discord
                # notification closes the observability loop so everyone on
                # the pager knows the instance is down and why.
                try:
                    from live_alerts import alert_halt as _a_halt
                    _a_halt(instance_id=str(instance_id), reason=_halt_reason_text)
                except Exception:
                    pass
                orders_canceled = 0
                for _pass in range(2):
                    try:
                        for od in adapter.list_open_orders(limit=500) or []:
                            try:
                                if adapter.cancel_order(od.broker_order_id):
                                    orders_canceled += 1
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # Brief yield between passes so any in-flight submits can
                    # land at the broker before we re-scan the open list.
                    try:
                        time.sleep(0.5)
                    except Exception:
                        pass
            finally:
                try:
                    if halt_conn is not None:
                        halt_conn.close()
                except Exception:
                    pass
            return (True, "", {"orders_canceled": orders_canceled, "instance_halted": True})

        if ctype == "close_position":
            symbol = str(payload.get("symbol") or "").strip().upper()
            if not symbol:
                return (False, "close_position requires payload.symbol", {})
            positions = dict(getattr(adapter, "_positions", {}) or {})
            qty = payload.get("qty")
            try:
                qty = float(qty) if qty is not None else float(positions.get(symbol, 0.0) or 0.0)
            except Exception:
                return (False, "close_position qty must be a number", {})
            if qty <= 0:
                return (False, f"no open long position for {symbol}", {})
            last_prices = dict(getattr(adapter, "_last_prices", {}) or {})
            price = float(last_prices.get(symbol, 0.0) or 0.0)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            import live_state as _ls_mod
            # Canonical signature: (instance_id, symbol, bar_iso, side, retry_n)
            coid = _ls_mod.make_client_order_id(str(instance_id), symbol, now_iso, "sell", 0)
            try:
                ref = adapter.submit_order(
                    symbol=symbol, side="sell", qty=qty, notional=None,
                    order_type="market", limit_price=None, tif="day",
                    extended_hours=False, client_order_id=coid,
                )
            except Exception as _se:
                return (False, f"submit_order failed: {_se}", {})
            return (True, "", {
                "symbol": symbol,
                "qty": qty,
                "order_id": getattr(ref, "broker_order_id", None),
                "client_order_id": coid,
                "last_price": price,
            })

        if ctype == "submit_order":
            symbol = str(payload.get("symbol") or "").strip().upper()
            side = str(payload.get("side") or "").strip().lower()
            if side not in ("buy", "sell"):
                return (False, "submit_order: side must be buy or sell", {})
            if not symbol:
                return (False, "submit_order requires payload.symbol", {})
            order_type = str(payload.get("order_type") or "market").strip().lower()
            if order_type not in ("market", "limit"):
                return (False, "submit_order: order_type must be market or limit", {})
            qty = payload.get("qty")
            notional = payload.get("notional")
            if qty is None and notional is None:
                return (False, "submit_order requires qty or notional", {})
            try:
                qty = float(qty) if qty is not None else None
            except Exception:
                return (False, "submit_order: qty must be numeric", {})
            try:
                notional = float(notional) if notional is not None else None
            except Exception:
                return (False, "submit_order: notional must be numeric", {})
            limit_price = payload.get("limit_price")
            if order_type == "limit":
                try:
                    limit_price = float(limit_price)
                    if limit_price <= 0:
                        return (False, "submit_order: limit_price must be > 0", {})
                except Exception:
                    return (False, "submit_order: limit order requires numeric limit_price", {})
            else:
                limit_price = None
            tif = str(payload.get("tif") or "day").strip().lower()
            # Restrict to TIFs the adapter's tif_map actually supports. opg/cls
            # silently downgraded to DAY in AlpacaAdapter, so disallow until
            # the adapter is extended.
            if tif not in ("day", "gtc", "ioc", "fok"):
                return (False, f"submit_order: unsupported tif {tif!r}", {})
            extended_hours = bool(payload.get("extended_hours", False))
            # Mirror Alpaca's constraint to fail fast instead of at the adapter:
            # extended_hours is only valid for LIMIT + DAY.
            if extended_hours and (order_type != "limit" or tif != "day"):
                return (False, "submit_order: extended_hours requires order_type=limit + tif=day", {})
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            import live_state as _ls_mod
            # Canonical signature: (instance_id, symbol, bar_iso, side, retry_n)
            coid = _ls_mod.make_client_order_id(str(instance_id), symbol, now_iso, side, 0)
            try:
                ref = adapter.submit_order(
                    symbol=symbol, side=side, qty=qty, notional=notional,
                    order_type=order_type, limit_price=limit_price, tif=tif,
                    extended_hours=extended_hours, client_order_id=coid,
                )
            except Exception as _se:
                return (False, f"submit_order failed: {_se}", {})
            return (True, "", {
                "symbol": symbol, "side": side, "qty": qty, "notional": notional,
                "order_type": order_type, "limit_price": limit_price, "tif": tif,
                "extended_hours": extended_hours,
                "order_id": getattr(ref, "broker_order_id", None),
                "client_order_id": coid,
            })

        return (False, f"unknown command type: {ctype!r}", {})
    except Exception as _e:
        return (False, f"{type(_e).__name__}: {_e}", {})


def _live_state_command_worker(instance_id_val: str, adapter) -> None:
    """Poll LiveCommands every ~1s for pending commands for this instance and
    execute them sequentially. We intentionally use polling not changefeed
    here: commands are infrequent (operator actions), and polling is simpler
    to reason about than a long-lived changefeed that can die silently under
    network blips."""
    interval = float(os.environ.get("LIVE_STATE_COMMAND_POLL_INTERVAL_SEC", "1"))
    conn_local = None
    try:
        import live_state as _ls_mod
    except Exception:
        return
    while not _live_trading_stop_event.is_set() and not shutdown_requested:
        try:
            if conn_local is None:
                conn_local = get_conn_retry(max_attempts=3, delay=2)
            if conn_local is None:
                _live_trading_stop_event.wait(interval)
                continue
            cmd = _ls_mod.claim_next_pending(r, conn_local, instance_id_val)
            if cmd is None:
                _live_trading_stop_event.wait(interval)
                continue
            cmd_id = cmd.get("id")
            _log(f"Live command received: type={cmd.get('type')} id={cmd_id} payload={cmd.get('payload')}", "cyan")
            ok, err, result = _execute_live_command(adapter, cmd)
            if ok:
                _ls_mod.complete_command(r, conn_local, cmd_id, result=result)
                _log(f"Live command {cmd_id} completed: {result}", "green")
            else:
                _ls_mod.fail_command(r, conn_local, cmd_id, error=err)
                _log(f"Live command {cmd_id} failed: {err}", "yellow")
        except Exception as _e:
            _log(f"Live command worker warning: {_e}", "yellow")
            try:
                if conn_local:
                    conn_local.close()
            except Exception:
                pass
            conn_local = None
            _live_trading_stop_event.wait(interval)
    try:
        if conn_local:
            conn_local.close()
    except Exception:
        pass


def _start_live_trading_threads(adapter) -> None:
    """Spawn snapshot + command processor threads once the adapter is ready."""
    global _live_trading_snapshot_thread, _live_trading_command_thread
    try:
        import live_state as _ls_mod
        init_conn = get_conn_retry(max_attempts=3, delay=2)
        if init_conn is not None:
            try:
                _ls_mod.ensure_tables(r, init_conn)
            finally:
                try:
                    init_conn.close()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        _live_trading_snapshot_thread = threading.Thread(
            target=_live_state_snapshot_worker,
            args=(str(instance_id), adapter),
            daemon=True,
            name="live-state-snapshot",
        )
        _live_trading_snapshot_thread.start()
    except Exception as _e:
        _log(f"Could not start live snapshot thread: {_e}", "yellow")
    try:
        _live_trading_command_thread = threading.Thread(
            target=_live_state_command_worker,
            args=(str(instance_id), adapter),
            daemon=True,
            name="live-state-commands",
        )
        _live_trading_command_thread.start()
    except Exception as _e:
        _log(f"Could not start live command thread: {_e}", "yellow")
    _log("Live state snapshot + command threads started (3s cadence)", "green")


def _shutdown_live_trading_state(reason: str = "shutdown") -> None:
    """Signal threads to stop, wait for them to exit, clear the LiveState row,
    close the log file. Join order matters: if we clear the row BEFORE the
    snapshot worker has finished its current tick, that worker can upsert a
    zombie row right after the delete and the UI polls "halted" forever."""
    try:
        _live_trading_stop_event.set()
    except Exception:
        pass
    # Wait for the snapshot + command threads to exit so a mid-tick upsert
    # can't resurrect the row we're about to delete.
    _snap_interval = float(os.environ.get("LIVE_STATE_SNAPSHOT_INTERVAL_SEC", "3"))
    _cmd_interval = float(os.environ.get("LIVE_STATE_COMMAND_POLL_INTERVAL_SEC", "1"))
    _join_timeout = max(_snap_interval, _cmd_interval) + 2.0
    for _t in (_live_trading_snapshot_thread, _live_trading_command_thread):
        try:
            if _t is not None and _t.is_alive():
                _t.join(timeout=_join_timeout)
        except Exception:
            pass
    # Clear LiveState so the UI knows the broker isn't running anymore.
    try:
        import live_state as _ls_mod
        clear_conn = get_conn_retry(max_attempts=2, delay=1)
        if clear_conn is not None:
            try:
                _ls_mod.clear_live_state(r, clear_conn, str(instance_id))
            finally:
                try:
                    clear_conn.close()
                except Exception:
                    pass
    except Exception:
        pass
    _close_live_trading_log(reason=reason)


def run_socket_loop():
    """Connect to server; on disconnect, check DB and reconnect if keep_alive."""
    global shutdown_requested
    import socketio
    # Prefer SERVER_URL; fall back to INSTANCE_SERVER_URL (used when server spawns instance containers)
    server_url = os.environ.get('SERVER_URL') or os.environ.get('INSTANCE_SERVER_URL', 'http://localhost:5000')
    reconnect_delay = 2
    max_reconnect_delay = 60
    reconnect_attempt = 0
    while not shutdown_requested:
        if not should_keep_alive():
            _log("DB says do not keep alive; exiting.", "yellow")
            shutdown_requested = True
            os._exit(0)
        sio = socketio.Client()
        try:
            sio.connect(server_url, transports=['polling'])
            reconnect_attempt = 0  # reset after successful connect
            # Register as broker for this instance (server links broker to instance).
            # UUID MUST NOT collide with instance.py's UUID (which equals
            # instance_id). When broker has no symbol (self-discovering Nexus
            # strategy), we still use a distinct suffix so the server doesn't
            # see broker-connect as instance-reconnect and terminate instance.
            symbol = symbols[0] if symbols else None
            if symbol:
                uuid_val = f"{instance_id}_{symbol}"
            else:
                uuid_val = f"{instance_id}_broker"
            sio.emit('clientType', {'UUID': uuid_val, 'instance': instance_id, 'symbol': symbol})
            _log("Connected to server (instance " + str(instance_id) + ")", "green")
            sio.wait()  # blocks until disconnected
        except Exception as e:
            _log("Socket error: " + str(e), "yellow")
        if shutdown_requested:
            break
        # Disconnected: check DB before reconnecting
        if not should_keep_alive():
            _log("DB says do not keep alive after disconnect; exiting.", "yellow")
            shutdown_requested = True
            os._exit(0)
        reconnect_attempt += 1
        # Log full message only for first few attempts or every 10th to avoid log spam when server is down
        if reconnect_attempt <= 3 or reconnect_attempt % 10 == 0:
            _log("Disconnected from server; reconnecting in " + str(int(reconnect_delay)) + "s (attempt " + str(reconnect_attempt) + ", DB says keep alive)...", "yellow")
        time.sleep(min(reconnect_delay, max_reconnect_delay))
        reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

# Socket connection only in live mode; backtest runs without server
if mode == MODE_LIVE:
    try:
        socket_thread = threading.Thread(target=run_socket_loop, daemon=True)
        socket_thread.start()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Main loop: run work; exit when DB says do not keep alive
# ---------------------------------------------------------------------------

# Live mode uses tz-aware UTC; backtest uses naive local (legacy) because
# existing bar-comparison logic assumes naive. The live loop refreshes
# current_time as tz-aware UTC at the top of each iteration.
if mode == MODE_LIVE:
    current_time = datetime.datetime.now(datetime.timezone.utc)
else:
    current_time = datetime.datetime.now()
backtest_increment_td = datetime.timedelta(seconds=60)
_backtest_start_dt_input = None
_backtest_end_dt_input = None

def _parse_date(s):
    """Parse date string (YYYY-MM-DD, DD/MM/YYYY, or MM/DD/YYYY) to naive datetime at midnight."""
    s = str(s)[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.datetime.strptime(s, "%Y-%m-%d")


def _time_increment_to_timedelta(time_increment):
    """
    Convert time_increment string to timedelta for warmup calculation.
    Examples: "60" -> 60 seconds, "1d" -> 1 day, "1h" -> 1 hour, "15m" -> 15 minutes.
    """
    s = (time_increment or "60").strip().lower()
    if not s:
        return datetime.timedelta(seconds=60)
    if s.isdigit():
        return datetime.timedelta(seconds=int(s))
    num_str = ""
    for c in s:
        if c in "0123456789.":
            num_str += c
        else:
            break
    try:
        num = float(num_str) if num_str else 1.0
    except ValueError:
        return datetime.timedelta(seconds=60)
    unit = s[len(num_str):].strip() or "s"
    if unit.startswith("d"):
        return datetime.timedelta(days=num)
    if unit.startswith("h"):
        return datetime.timedelta(hours=num)
    if unit.startswith("m"):
        return datetime.timedelta(minutes=num)
    return datetime.timedelta(seconds=num)


def _time_increment_to_alpaca_timeframe(time_increment):
    """
    Convert time_increment string to Alpaca API timeframe format.
    Examples: "60" -> "1Min", "300" -> "5Min", "15m" -> "15Min", "1h" -> "1Hour", "1d" -> "1Day".
    Returns: Alpaca timeframe string ("1Min", "5Min", "15Min", "30Min", "1Hour", "1Day")
    """
    if not time_increment:
        return "1Min"  # Default to 1 minute
    
    # First convert to timedelta to get total seconds
    td = _time_increment_to_timedelta(time_increment)
    total_seconds = int(td.total_seconds())
    
    # Convert to Alpaca timeframe format
    if total_seconds >= 86400:  # 1 day or more
        days = total_seconds // 86400
        if days == 1:
            return "1Day"
        else:
            # Alpaca only supports 1Day, so use 1Day for any day increment
            return "1Day"
    elif total_seconds >= 3600:  # 1 hour or more
        hours = total_seconds // 3600
        if hours == 1:
            return "1Hour"
        else:
            # Alpaca only supports 1Hour, so use 1Hour for any hour increment
            return "1Hour"
    elif total_seconds >= 1800:  # 30 minutes or more
        return "30Min"
    elif total_seconds >= 900:  # 15 minutes or more
        return "15Min"
    elif total_seconds >= 300:  # 5 minutes or more
        return "5Min"
    else:  # Less than 5 minutes
        return "1Min"


# Backtest warmup: fetch this many cycles of data before start_date so strategies have history
# Increased to ensure strategies like volatility have enough bars (200+ needed)
WARMUP_CYCLES = 700

if mode == MODE_BACKTEST:
    _log("Running in backtest mode", "green")
    # CRITICAL-GUARD: reset per-process module state at the top of every backtest
    # entry so a fresh run isn't contaminated by leftover state from a prior run
    # inside the same Python process (defense-in-depth — backtests usually run as
    # separate docker containers, but tests and dev runs may reuse the process).
    try:
        from llm_critical_guard import reset_state as _cg_reset
        from backtest_critical_abort import reset_state as _bca_reset
        _cg_reset()
        _bca_reset()
    except Exception:
        pass
    _log("Start date: " + str(start_date), "green")
    _log("End date: " + str(end_date), "green")
    _log("Time increment: " + str(time_increment), "green")
    _log("Symbols: " + ", ".join(symbols), "green")
    start_dt = _parse_date(start_date)
    end_dt_input = _parse_date(end_date)
    _backtest_fetch_start_dt = None
    _backtest_fetch_end_dt = None
    _backtest_no_history_symbols = set()
    _backtest_start_dt_input = start_dt
    _backtest_end_dt_input = end_dt_input
    # Treat end date as inclusive for backtests: run through the full calendar day.
    end_dt = end_dt_input + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)
    current_time = start_dt
    end_date = end_dt
    # Fetch from (start - WARMUP_CYCLES) so strategies have history when backtest starts
    # Also ensure we fetch at least 30 days of history to get enough bars (accounting for market hours)
    increment_td = _time_increment_to_timedelta(time_increment)
    backtest_increment_td = increment_td
    warmup_td = WARMUP_CYCLES * increment_td
    # Ensure at least 90 days of history (to account for weekends/holidays, we need more calendar days)
    # This ensures we get enough daily bars for ML strategies that need training data
    from datetime import timedelta
    min_warmup_days = timedelta(days=90)  # 90 calendar days to ensure ~60+ trading days (enough for daily aggregation)
    if warmup_td < min_warmup_days:
        warmup_td = min_warmup_days
        _log(f"Warmup period extended to {warmup_td.days} calendar days to ensure sufficient historical bars", "cyan")
    extended_start = start_dt - warmup_td
    _backtest_fetch_start_dt = extended_start
    _log("Warmup: fetching from %s (%s cycles / ~%s days before start) to end." % (extended_start, WARMUP_CYCLES, warmup_td.days), "green")

from robinhood_engine import get_live_prices, get_price_history

# Symbols list for price_history: same as symbols unless Breakout strategy needs SPY for market filter
symbols_for_data = list(symbols or [])

# print(get_live_prices(symbols))
if mode == MODE_BACKTEST:
    # Load strategies early so we can add SPY to the fetch when Breakout strategy is used
    _cached_strategies, _strategy_row_id, _backtest_strategy_schema = load_strategies_from_db()
    if _cached_strategies:
        _log(f"Loaded {len(_cached_strategies)} strategy(ies) from DB", "green")
        # Resolve all model_id references at startup. force_refresh drops
        # the model_resolver's 5-min TTL cache so a freshly-edited Models
        # row (via UI Test & Save) is picked up immediately — every
        # backtest spawn reads the latest credentials from the Models
        # table, no broker restart required.
        try:
            _resolve_conn = get_conn()
            for _spec in _cached_strategies:
                cfg = _spec.get("config") or {}
                _spec["config"] = resolve_model_refs_in_config(_resolve_conn, cfg, force_refresh=True)
            _resolve_conn.close()
            _log("Resolved model_id references in strategy configs (force_refresh=True)", "green")
        except Exception as _e:
            _log(f"Model resolution warning: {_e}", "yellow")
        _cached_strategies = sorted(_cached_strategies, key=lambda s: int(s.get('execution_position', 0)))
    # Split into pre-decision (voting) and post-decision (order size, pricing). Post = decision_phase "post" or name position_sizing (backward compat).
    _post_decision_specs = [
        s for s in (_cached_strategies or [])
        if (
            (str(s.get("decision_phase") or "pre").strip().lower() == "post")
            or (str(s.get("strategy") or "").strip().lower() == "position_sizing")
        )
    ]
    _pre_decision_specs = [s for s in (_cached_strategies or []) if s not in _post_decision_specs]
    _run_once_specs = [s for s in _pre_decision_specs if (str(s.get("execution_scope") or "per_symbol").strip().lower() == "run_once")]
    _per_symbol_specs = [s for s in _pre_decision_specs if s not in _run_once_specs]
    if _post_decision_specs:
        _log(f"Pre-decision (voting): {len(_pre_decision_specs)}; post-decision (size/pricing): {len(_post_decision_specs)}", "cyan")
    if _run_once_specs:
        _log(f"Run-once (pre-strategy-execution): {len(_run_once_specs)}; per-symbol: {len(_per_symbol_specs)}", "cyan")
    symbols_for_fetch = list(symbols or [])
    for s in (_cached_strategies or []):
        name = (s.get('strategy') or '').strip().lower()
        if name == 'breakout' or name == 'momentum_pullback' or name == 'momentumpullback':
            if 'SPY' not in symbols_for_fetch:
                symbols_for_fetch.append('SPY')
                _log(f"Adding SPY to bar data for {name} strategy market filter", "cyan")
            # Don't break - check all strategies
        # P&L sweep 2026-07-19: the residual sleeve trades its own symbol —
        # without bars it has no price and silently no-ops (BULL_F/BEAR_F ran
        # with a completely inert sleeve because SPY was never fetched).
        _sleeve_cfg = (s.get('config') or {})
        if bool(_sleeve_cfg.get('residual_sleeve_enabled', False)):
            _sleeve_sym = str(_sleeve_cfg.get('residual_sleeve_symbol', 'SPY')).upper()
            if _sleeve_sym and _sleeve_sym not in symbols_for_fetch:
                symbols_for_fetch.append(_sleeve_sym)
                _log(f"Adding {_sleeve_sym} to bar data for the residual sleeve", "cyan")
    symbols_for_data = symbols_for_fetch
    # Convert time_increment to Alpaca timeframe format
    alpaca_timeframe = _time_increment_to_alpaca_timeframe(time_increment)
    _backtest_alpaca_timeframe = alpaca_timeframe
    _backtest_fetch_end_dt = end_date
    _log(f"Using Alpaca timeframe: {alpaca_timeframe} (from time_increment: {time_increment})", "green")
    _log("Fetching Alpaca bars in date-range chunks and stitching (avoids large-range / limit issues)", "cyan")
    try:
        _bars_db_conn = get_conn()
    except Exception:
        _bars_db_conn = None
    data = fetch_alpaca_historical_bars(
        symbols_for_fetch, extended_start, end_date, key, secret, alpaca_timeframe,
        db_conn=_bars_db_conn, feed=data_feed,
    )
    # Approximate expected bars for requested range (trading hours ~6.5/day, ~252 trading days/year)
    req_days = (end_date - extended_start).days if (end_date and extended_start) else 0
    try:
        bars_per_trading_day = {"1Min": 390, "5Min": 78, "15Min": 26, "30Min": 13, "1Hour": 7, "1Day": 1}
        bpd = bars_per_trading_day.get(alpaca_timeframe, 7)
        expected_bars = max(1, int(req_days / 365.0 * 252 * bpd))
    except Exception:
        expected_bars = None
    for sym in (symbols or []):
        n = len(data.get(sym) or [])
        if n == 0:
            _log(
                f"No Alpaca bars returned for {sym}. Check: (1) date range is within your Alpaca data plan, "
                "(2) IEX free tier has limited history—try recent dates or set ALPACA_DATA_FEED=sip if you have SIP, "
                "(3) market hours for intraday (e.g. 9:30 AM–4 PM ET).",
                "yellow",
            )
        else:
            _log(f"Loaded {n} {alpaca_timeframe} bars for {sym} (from {extended_start} to {end_date}).", "green")
            if expected_bars is not None and req_days > 60 and n < expected_bars * 0.5:
                _log(
                    f"Only {n} bars for ~{req_days} days is low (expected ~{expected_bars}). "
                    "Alpaca IEX feed often has limited intraday history; use SIP or check data plan for more.",
                    "yellow",
                )
    # Dual-cadence backtest harness — opt-in via run_once strategy config.
    # Spec: docs/superpowers/specs/2026-05-02-nexus-dual-cadence-backtest-harness-design.md
    # NOT a P&L A/B vs the daily-cadence baseline (bar-density-coupled state
    # behaves differently at hourly cadence). Operator-visible RED warning is
    # emitted on every harness run; cadence_mode is recorded on BacktestResults.
    _dc_bt_sim = False
    try:
        for _spec in (_run_once_specs or []):
            _cfg = _spec.get("config") or {}
            if bool(_cfg.get("nexus_dual_cadence_backtest_simulation", False)):
                _dc_bt_sim = True
                break
    except Exception:
        _dc_bt_sim = False
    # The dual-cadence harness only makes sense at sub-hourly cadence —
    # the gate it's simulating fires per intraday tick and is decoupled
    # from daily-bar persistence. At daily granularity (e.g. 86400s) the
    # gate would never activate, so the harness adds no signal and the
    # preflight unconditionally rejects. Gracefully degrade to a plain
    # backtest with a one-line warning rather than failing the whole run.
    if _dc_bt_sim:
        try:
            _ti_check = int(time_increment)
        except (TypeError, ValueError):
            _ti_check = -1
        if _ti_check < 30 or _ti_check > 3600:
            _log(
                f"nexus_dual_cadence_backtest_simulation=True but granularity_sec={_ti_check} "
                f"is outside the harness's supported [30, 3600] range — running as a plain "
                f"single-cadence backtest. Use 30s-1h granularity to actually exercise the "
                f"dual-cadence gate.",
                "yellow",
            )
            _dc_bt_sim = False
    if _dc_bt_sim:
        from dual_cadence_preflight import (
            dual_cadence_backtest_preflight as _dc_preflight,
            validate_coupled_flags as _dc_validate_coupled,
        )
        # Validate coupled flags FIRST so the misconfiguration fails before
        # we burn CPU on the bar-coverage scan. Raises at backtest startup
        # rather than per-tick (where the exception handler would swallow it).
        _dc_validate_coupled(_run_once_specs)
        _dc_preflight(data, time_increment, symbols)
        _log(
            "WARNING: nexus_dual_cadence_backtest_simulation=True.\n"
            "This is a BEHAVIOR VALIDATION harness, not a P&L A/B test.\n"
            "Headline returns are NOT comparable to the daily-cadence +49% baseline because:\n"
            "  - _fast_loser_blacklist bars_remaining decays at 7x the daily-cadence rate\n"
            "  - _deployment_bar_index increments at 7x the daily-cadence rate\n"
            "  - momentum amplifier triggers and rotation gates respond to bar count, not date\n"
            "For paper-trade A/B comparison see /brokerages UI; this harness is for wiring tests only.",
            "red",
        )
else:
    data = {}
    _dc_bt_sim = False

# Portfolio emulator: backtest uses PortfolioEmulator; live uses AlpacaAdapter
# which exposes the SAME private-attribute surface (_positions / _trades /
# _initial_value / _cash / _last_prices) so 34 strategy call-sites work unchanged.
portfolio_emulator = None
live_adapter = None
live_wal = None
if mode == MODE_BACKTEST:
    # Emulated-fee override (--taker-fee) wins; else resolve from the instance venue.
    _bt_taker_fee = emulated_taker_fee if emulated_taker_fee is not None else _instance_crypto_taker_fee()
    portfolio_emulator = PortfolioEmulator(initial_cash=initial_cash, taker_fee=_bt_taker_fee)
    _log("PortfolioEmulator initialized for backtest (initial_cash=%s, taker_fee=%s%s)."
         % (initial_cash, _bt_taker_fee, " [emulated]" if emulated_taker_fee is not None else ""), "green")
elif mode == MODE_LIVE:
    try:
        from nexus_runtime_state import ensure_tables as _ensure_live_tables, WALStore as _WALStore
        from broker_adapters.factory import build_adapter as _build_adapter
        from broker_adapters._preflight import alpaca_quota_budget as _quota_budget
        from broker_adapters.errors import BrokerError as _BrokerError
        from live_alerts import alert_halt as _alert_halt, alert_strategy_error as _alert_strategy_error

        if not key or not secret:
            _log("Live mode requires broker credentials; none found in DB or env. Exiting.", "red")
            sys.exit(3)

        _ensure_live_tables()
        live_wal = _WALStore()
        # Paper-vs-live assertion: if the instance says live but the stored
        # creds are the default paper URL, we refuse to start. (The adapter
        # calls get_account() during __init__ which will fail loudly on mismatch.)
        _budget = _quota_budget(
            n_symbols=len(symbols or []),
            calls_per_cycle=3,  # quote + account + positions baseline
            cycles_per_min=max(1.0, 60.0 / max(1, int(time_increment or "60"))) if (time_increment or "").isdigit() else 1.0,
        )
        if not _budget.ok:
            _log(
                f"Alpaca quota budget exhausted: est {_budget.est_rpm} rpm vs limit {_budget.rpm_limit} "
                f"(headroom {_budget.headroom_pct:.1%}). Reduce symbols or lengthen cadence.",
                "red",
            )
            try:
                _alert_halt(instance_id=str(instance_id), reason=f"quota budget refused at boot: est {_budget.est_rpm}/{_budget.rpm_limit}")
            except Exception:
                pass
            sys.exit(4)
        # 2026-04-22 Fix 6: bind the per-instance log file sink BEFORE we
        # build the adapter + run WAL reconcile + refresh_orders_today. The
        # docstring at _open_live_trading_log (broker.py:~2700) already states
        # this ordering intent but the implementation previously deferred it.
        # Net effect: boot-time log lines (reconcile_wal_with_broker,
        # refresh_orders_today, "adapter ready") now land in the per-instance
        # file the UI monitor tails, instead of global stderr where nothing
        # reads them.
        try:
            _open_live_trading_log(instance_id)
        except Exception as _log_e_pre:
            _log(f"Could not open live-trading log file (pre-adapter): {_log_e_pre}", "yellow")
        # Phase C (2026-04-29) — resolve RH-only extras (account_number +
        # device_token) so RobinhoodAdapter trades from the sub-account the
        # user picked at link time. No-op for Alpaca.
        _rh_account_number = None
        _rh_device_token = None
        _rh_obtained_at = None
        _rh_expires_in = None
        _rh_account_url = None
        if (live_broker_type or "").strip().lower() == "robinhood":
            _rh_extras = _load_robinhood_extras_from_db(live_brokerage_id)
            _rh_account_number = _rh_extras.get("account_number")
            _rh_device_token = _rh_extras.get("device_token")
            _rh_obtained_at = _rh_extras.get("obtained_at_epoch")
            _rh_expires_in = _rh_extras.get("expires_in")
            _rh_account_url = _rh_extras.get("account_url")
            if not _rh_account_number:
                _log(
                    f"Robinhood brokerage {live_brokerage_id} has NO `robinhood_account_number`. "
                    f"Re-link the account from the UI so the adapter trades from the right sub-account.",
                    "yellow",
                )
            # Surface dry-run state on boot so operators can't miss it.
            _rh_dry = (os.environ.get("RH_DRY_RUN", "true") or "true").strip().lower() not in ("0", "false", "no", "off")
            if _rh_dry:
                _log(
                    "RH_DRY_RUN=true (default) — Robinhood orders will NOT be submitted. "
                    "Set RH_DRY_RUN=false to enable real-money trading.",
                    "yellow",
                )

        # ----- Clean-room mode resolution (2026-05-28) ---------------------
        # Sources of truth, in precedence order:
        #   1. env LIVE_CLEAN_ROOM_MODE (per-host force)
        #   2. Instances.<id>.clean_room_mode (per-instance DB field)
        #   3. default False (backward-compatible legacy behavior)
        _instance_row_for_clean_room = {}
        try:
            _cr_conn = get_conn_retry(max_attempts=3, delay=1)
            if _cr_conn is not None:
                try:
                    _instance_row_for_clean_room = (
                        r.db("IntelliStock").table("Instances").get(str(instance_id)).run(_cr_conn)
                        or {}
                    )
                finally:
                    try:
                        _cr_conn.close()
                    except Exception:
                        pass
        except Exception as _e_inst_cr:
            _log(f"[live_boot] could not read Instances row for clean_room resolution: {_e_inst_cr}", "yellow")

        _env_cr = (os.environ.get("LIVE_CLEAN_ROOM_MODE", "") or "").strip().lower()
        if _env_cr in ("1", "true", "yes", "on"):
            _clean_room_mode = True
        elif _env_cr in ("0", "false", "no", "off"):
            _clean_room_mode = False
        else:
            _clean_room_mode = bool(_instance_row_for_clean_room.get("clean_room_mode", False))

        # initial_value resolution: env > Instances row > None (let the
        # adapter raise BrokerError under clean_room_mode if absent).
        # Track which source set the value for the LiveBootAudit row.
        _env_iv = (os.environ.get("LIVE_INITIAL_VALUE", "") or "").strip()
        _initial_value = None
        _initial_value_source = "none"
        if _env_iv:
            try:
                _initial_value = float(_env_iv)
                _initial_value_source = "env"
            except ValueError:
                _initial_value = None
        if _initial_value is None:
            _iv_field = _instance_row_for_clean_room.get("initial_value")
            if _iv_field is not None:
                try:
                    _initial_value = float(_iv_field)
                    _initial_value_source = "instance_row"
                except (TypeError, ValueError):
                    _initial_value = None
        # Bug-sweep 2026-05-28: reject NaN/inf which would slip past the
        # adapter's <= 0 guard and propagate through portfolio math.
        if _initial_value is not None and (
            _initial_value != _initial_value  # NaN check
            or _initial_value == float("inf") or _initial_value == float("-inf")
        ):
            _log(f"[live_boot] LIVE_INITIAL_VALUE / instance_row.initial_value is "
                 f"non-finite ({_initial_value!r}); ignoring.", "yellow")
            _initial_value = None
            _initial_value_source = "none"

        # WAL retention window (days) used by the classifier.
        try:
            _clean_room_retention_days = int(
                os.environ.get("LIVE_CLEAN_ROOM_WAL_RETENTION_DAYS", "180") or 180
            )
        except ValueError:
            _clean_room_retention_days = 180

        # cid prefix per broker_adapters/_client_order_id.py::_safe(instance, 8)
        try:
            from broker_adapters._classifier import derive_cid_prefix as _derive_cid_prefix
            _cid_prefix = _derive_cid_prefix(str(instance_id))
        except Exception:
            _cid_prefix = None

        # Scope D (A1/A2/A3): whether THIS boot is the genuine first clean-room
        # boot. Captured in the cleanup block below and reused to gate the 2-B
        # drawdown re-baseline + 2-E momentum strip so they never reset
        # live-accumulated state on a restart. Default False = conservative.
        _clean_room_first_boot = False
        if _clean_room_mode:
            _log(
                f"[live_boot] CLEAN_ROOM_MODE=true instance={instance_id} "
                f"initial_value={_initial_value} retention_days={_clean_room_retention_days} "
                f"cid_prefix={_cid_prefix!r}",
                "yellow",
            )
            # ----- First-clean-room-boot auto-cleanup (2026-05-28) -----
            # When the operator opts into clean_room_mode via the Instances row,
            # we want the daemon to do the same per-instance state wipe the
            # operator would otherwise run via
            # scripts/clear_main_instance_lookback_state.py --apply, but only
            # once: detect "first clean-room boot" by checking LiveBootAudit
            # row count for this instance. Idempotent — subsequent boots see
            # >=1 audit row and skip the cleanup so legitimate live-accumulated
            # operational state is preserved across normal restarts.
            try:
                from live_boot_setup import (
                    is_first_clean_room_boot as _is_first_cr_boot,
                    run_first_clean_room_boot_cleanup as _run_cr_cleanup,
                    write_clean_room_cleanup_marker as _write_cr_marker,
                )
                _setup_conn = get_conn_retry(max_attempts=3, delay=2)
                if _setup_conn is not None:
                    try:
                        _clean_room_first_boot = bool(
                            _is_first_cr_boot(r, _setup_conn, str(instance_id))
                        )
                        if _clean_room_first_boot:
                            _log(
                                f"[live_boot] FIRST clean_room boot for {instance_id} "
                                "— running auto-cleanup of per-instance operational state "
                                "(preserves backtest snapshots)",
                                "yellow",
                            )
                            _cleanup_result = _run_cr_cleanup(r, _setup_conn, str(instance_id))
                            if _cleanup_result.get("error"):
                                _log(
                                    f"[live_boot] auto-cleanup error (non-fatal, continuing): "
                                    f"{_cleanup_result.get('error')}",
                                    "red",
                                )
                            else:
                                _log(
                                    f"[live_boot] auto-cleanup complete: "
                                    f"{_cleanup_result.get('total_deleted', 0)} rows deleted across "
                                    f"{len(_cleanup_result.get('tables') or [])} tables",
                                    "green",
                                )
                            # Scope D A3: write the cleanup-done sentinel NOW, on
                            # this same connection, BEFORE _build_adapter — but ONLY
                            # when the cleanup actually SUCCEEDED. Scope C relied on
                            # the forensic LiveBootAudit row (written after
                            # _build_adapter), so an adapter-build sys.exit left no
                            # marker and the next boot re-ran the wipe. The sentinel
                            # makes cleanup idempotent independent of the rest of
                            # boot succeeding. Writing it on a FAILED/partial cleanup
                            # would mark cleanup "done" and permanently suppress the
                            # retry (is_first_clean_room_boot counts any clean_room
                            # row) — leaving backtest-era state live. On error, leave
                            # no marker so the next boot re-runs the idempotent wipe.
                            if not _cleanup_result.get("error"):
                                if _write_cr_marker(r, _setup_conn, str(instance_id)):
                                    _log(
                                        "[live_boot] cleanup-done sentinel written "
                                        "(first-boot cleanup will not re-run).",
                                        "green",
                                    )
                                else:
                                    _log(
                                        "[live_boot] WARNING: cleanup-done sentinel write "
                                        "FAILED — if adapter build now fails, the next boot "
                                        "could re-run the destructive cleanup.",
                                        "red",
                                    )
                            else:
                                _log(
                                    "[live_boot] cleanup-done sentinel NOT written "
                                    "(cleanup errored) — next boot will retry the wipe.",
                                    "yellow",
                                )
                    finally:
                        try:
                            _setup_conn.close()
                        except Exception:
                            pass
            except Exception as _e_cr_setup:
                _log(
                    f"[live_boot] clean_room auto-setup module load failed "
                    f"(non-fatal): {_e_cr_setup}",
                    "yellow",
                )

        try:
            live_adapter = _build_adapter(
                broker_type=live_broker_type,
                api_key=key,
                api_secret=secret,
                paper=live_broker_paper,
                instance_id=str(instance_id),
                wal_store=live_wal,
                account_number=_rh_account_number,
                device_token=_rh_device_token,
                # 2026-04-30 — auth chain CRITICAL #1/#2: thread session-state
                # extras into the adapter so the in-process refresh path is
                # actually live. brokerage_id is also threaded so the adapter
                # can persist refreshed tokens back to the same DB row.
                rh_obtained_at_epoch=_rh_obtained_at,
                rh_expires_in=_rh_expires_in,
                rh_account_url=_rh_account_url,
                rh_brokerage_id=live_brokerage_id,
                # 2026-05-28 — clean-room mode threading. Backward-compatible:
                # clean_room_mode defaults False; when False the adapter
                # uses the legacy "adopt broker state at boot" behavior.
                initial_value=_initial_value,
                clean_room_mode=_clean_room_mode,
                cid_prefix=_cid_prefix,
                clean_room_retention_days=_clean_room_retention_days,
                seed_trades_from_broker=(not _clean_room_mode),
            )
            live_adapter.start_trade_updates()
            # Task 6 containment: block every legacy live submission when
            # this instance's RethinkDB containment state disables legacy
            # order authority (fail closed if the state is unreadable).
            live_adapter = _install_legacy_containment_gate(
                live_adapter, str(instance_id))
        except _BrokerError as _be:
            _log(f"Failed to build live broker adapter: {_be}", "red")
            # Phase C (2026-04-29): Discord alert on adapter build failure.
            # Was silently exiting — operators only saw the red log line in
            # the per-instance log file, not in the live-alerts channel.
            try:
                _alert_strategy_error(
                    instance_id=str(instance_id),
                    tag=f"adapter-init:{live_broker_type}",
                    message=f"Failed to build adapter: {type(_be).__name__}: {_be}",
                )
            except Exception:
                pass
            sys.exit(5)
        # Restart reconciliation: query broker for any open WAL rows.
        try:
            resolutions = live_adapter.reconcile_wal_with_broker()
            if resolutions:
                _log(f"WAL restart reconcile: {resolutions}", "cyan")
        except Exception as _e:
            _log(f"WAL reconcile warning: {_e}", "yellow")
        # 2026-04-22 Fix B: cache today's orders so the buy-loop can skip
        # symbols we already ordered this morning even if Nexus's in-memory
        # state was wiped by the restart. Fix A's date-keyed cid gives
        # Alpaca-side idempotency; this gate skips the round-trip entirely.
        try:
            if hasattr(live_adapter, "refresh_orders_today"):
                live_adapter.refresh_orders_today()
        except Exception as _e:
            _log(f"refresh_orders_today warning: {_e}", "yellow")

        # ----- LiveBootAudit row (2026-05-28) ------------------------------
        # One row per broker boot: forensic record of what the adapter
        # adopted as strategy-owned vs quarantined as external. Non-fatal
        # on failure (the live broker keeps running).
        try:
            from live_boot_audit import build_audit_row as _build_audit, persist_audit_row as _persist_audit
            _ext_at_boot = dict(getattr(live_adapter, "_external_positions", {}) or {})
            _audit_row = _build_audit(
                instance_id=str(instance_id),
                broker_type=str(live_broker_type or "unknown"),
                mode=("clean_room" if _clean_room_mode else "legacy"),
                broker_cash_at_boot=float(getattr(live_adapter, "_cash", 0.0) or 0.0),
                broker_positions_total=(
                    len(getattr(live_adapter, "_positions", {}) or {}) + len(_ext_at_boot)
                ),
                strategy_owned=dict(getattr(live_adapter, "_positions", {}) or {}),
                external=_ext_at_boot,
                initial_value=float(getattr(live_adapter, "_initial_value", 0.0) or 0.0),
                initial_value_source=(
                    _initial_value_source if _initial_value is not None
                    else "broker_equity"
                ),
                snapshot_loaded=False,  # set true once F1 snapshot hydrate completes (see below)
                snapshot_keys=0,
                trades_seeded=len(getattr(live_adapter, "_trades", []) or []),
                trades_seeded_source=("wal" if _clean_room_mode else "broker_history"),
                notes=[],
            )
            _audit_conn = get_conn_retry(max_attempts=3, delay=2)
            if _audit_conn is not None:
                try:
                    _persist_audit(r=r, conn=_audit_conn, row=_audit_row)
                except Exception as _e:
                    _log(f"[live_boot] audit write failed (non-fatal): {_e}", "yellow")
                finally:
                    try:
                        _audit_conn.close()
                    except Exception:
                        pass
        except Exception as _e:
            _log(f"[live_boot] audit module load failed (non-fatal): {_e}", "yellow")

        # ----- External-positions alert (2026-05-28) -----------------------
        _ext_now = dict(getattr(live_adapter, "_external_positions", {}) or {})
        if _ext_now:
            _ext_summary = ", ".join(
                f"{t}={(v or {}).get('qty', 0):.4f}sh" for t, v in sorted(_ext_now.items())
            )
            _log(
                f"[live_boot] EXTERNAL positions quarantined (NOT managed by strategy): {_ext_summary}",
                "yellow",
            )
            try:
                _alert_strategy_error(
                    instance_id=str(instance_id),
                    tag="external_positions_detected",
                    message=(
                        f"External (non-strategy) positions found at boot: {_ext_summary}. "
                        "Strategy will NOT sell these. Run "
                        "scripts/inspect_broker_state.py for details, or "
                        "scripts/migrate_external_position.py to adopt one."
                    ),
                )
            except Exception:
                pass

        # 2026-04-23 F1 + F1b: strategy_cache boot sequence.
        #   F1 loads the persisted `_deployment_bar_index` /
        #       `_portfolio_drawdown_state` / `_v28_*` / `_sold_cooldown` /
        #       `_overlay_no_data_tickers` keys from RethinkDB so the live
        #       broker survives a container restart without resetting state.
        #   F1b fast-forwards `_deployment_bar_index` to the end of the ramp
        #       when a warm portfolio (≥1 position) is detected and no
        #       persisted bar_index exists (or persisted is stuck at 1).
        #       Without F1b, a first-deploy live boot caps the ramp at 50%
        #       of starting equity, leaving `ramp_room = 0` once the
        #       portfolio MTM grows past 50% → `buys=0` pathology.
        # Both run ONCE at boot; the tick-loop's load-gate prevents re-run.
        try:
            # Phase 1 (2026-05-20): snapshot-aware boot load. Try the new
            # 5-segment (config_hash|origin|end_date) snapshot row first
            # via load_with_fallback. If that returns no row, fall back to
            # the legacy 2-segment per-instance row so existing live
            # deployments keep working.
            from broker_snapshot_helpers import (
                _invoke_load_snapshot_with_gap,
                _collect_prompt_versions,
                _collect_llm_stages,
                _collect_history_scope_inputs,
                _resolve_nexus_module_path,
            )
            from strategy_cache_persistence import (
                load_strategy_cache_from_db as _scp_load_boot,
                merge_loaded_cache_into as _scp_merge_boot,
                _compute_config_hash,
                _compute_module_hash,
            )
            _boot_conn = get_conn_retry(max_attempts=3, delay=2)
            # Seed F1 for the known live strategy name `graph_nexus_analysis`.
            # Other strategies load on their first tick via the existing
            # load path (gated on `_strategy_cache_loaded_from_db`).
            _nexus_name = "graph_nexus_analysis"
            _nexus_cache = _strategy_cache.setdefault(_nexus_name, {})
            # Scope D (A1/A2): origin of the hydrated snapshot ("backtest" /
            # "live" / ""), used to gate the 2-B re-baseline + 2-E strip.
            _snap_origin = None
            if _boot_conn is not None:
                try:
                    # Build config_hash + module_hash for the snapshot lookup
                    # (must match what backtest writes in
                    # _invoke_persist_backtest_snapshot — same canonical fields).
                    _nexus_spec_for_load = next(
                        (s for s in (_run_once_specs or [])
                         if str((s or {}).get("strategy") or "").strip() == _nexus_name),
                        None,
                    )
                    # Scope E (2026-05-29): on a LIVE boot this snapshot-load block
                    # runs BEFORE the live strategy specs are loaded (~line 6034),
                    # so `_run_once_specs` is still the empty default here and the
                    # config_hash would be computed from {} — which never matches a
                    # stored snapshot (reason=no_match), forcing a full re-lookback
                    # on EVERY boot. Load the nexus config inline via the SAME
                    # pipeline the live save-writer uses (DB load -> resolve model
                    # refs -> apply live overrides) so the config_hash matches and
                    # the snapshot/gap-fill path can hit. module_hash stays strict,
                    # so a snapshot built on different code is still rejected.
                    if _nexus_spec_for_load is None and mode != MODE_BACKTEST:
                        try:
                            _ehp_strats, _, _ = load_strategies_from_db()
                            for _ehp_s in (_ehp_strats or []):
                                if str(_ehp_s.get("strategy") or "").strip().lower() == _nexus_name:
                                    _ehp_cfg = _ehp_s.get("config") or {}
                                    try:
                                        _ehp_conn = get_conn()
                                        _ehp_cfg = resolve_model_refs_in_config(_ehp_conn, _ehp_cfg, force_refresh=True)
                                        _ehp_conn.close()
                                    except Exception:
                                        pass
                                    if mode == MODE_LIVE:
                                        _ehp_cfg = _apply_live_overrides(_ehp_cfg)
                                    _nexus_spec_for_load = {"strategy": _nexus_name, "config": _ehp_cfg}
                                    break
                        except Exception as _ehp_e:
                            _log(f"[snapshot] config-hash spec preload failed (non-fatal): {_ehp_e}", "yellow")
                    _bt_cfg_load = (_nexus_spec_for_load or {}).get("config") or {}
                    # Shared identity (matches the re-stamp feature byte-for-byte).
                    _current_config_hash = _nexus_live_config_hash(_bt_cfg_load, strategy_name=_nexus_name)
                    _nexus_module_path = _resolve_nexus_module_path() or ""
                    _current_module_hash = _compute_module_hash(_nexus_module_path) if _nexus_module_path else "missing"
                    _snap_cache, _snap_reason, _gap_dates, _snap_origin = _invoke_load_snapshot_with_gap(
                        conn=_boot_conn,
                        r=r,
                        instance_id=str(instance_id),
                        strategy_name=_nexus_name,
                        current_config_hash=_current_config_hash,
                        current_module_hash=_current_module_hash,
                    )
                    _log(
                        f"[snapshot] decision: reason={_snap_reason} "
                        f"gap_days={None if _gap_dates is None else len(_gap_dates)}",
                        "cyan",
                    )
                    if _snap_cache is not None:
                        _scp_merge_boot(_nexus_cache, _snap_cache)
                        _log(
                            f"[snapshot] hydrated {len(_snap_cache)} keys into _strategy_cache[{_nexus_name!r}] "
                            f"(bar_index={_nexus_cache.get('_deployment_bar_index')}, "
                            f"halt_active={(_nexus_cache.get('_portfolio_drawdown_state') or {}).get('halt_active')})",
                            "green",
                        )
                        # Store gap_dates so the lookback caller (line ~6019) can
                        # narrow the prepass to only the missing days.
                        globals()["_NEXUS_SNAPSHOT_GAP_DATES"] = _gap_dates
                        # Mark legacy F1 load as already-satisfied so the
                        # tick-loop fallback at ~6815 doesn't re-run the load.
                        globals()["_strategy_cache_loaded_from_db"] = True
                    else:
                        # Snapshot not usable -> fall back to legacy per-instance
                        # row load (preserves current behavior for ongoing
                        # deployments that haven't yet produced a 5-segment row).
                        _loaded_boot = _scp_load_boot(
                            _boot_conn, r, str(instance_id), _nexus_name,
                        )
                        if _loaded_boot:
                            _scp_merge_boot(_nexus_cache, _loaded_boot)
                            _log(
                                f"[snapshot] legacy row loaded: {len(_loaded_boot)} key(s) "
                                f"(bar_index={_nexus_cache.get('_deployment_bar_index')}, "
                                f"halt_active={(_nexus_cache.get('_portfolio_drawdown_state') or {}).get('halt_active')})",
                                "cyan",
                            )
                        else:
                            _log(
                                f"strategy_cache: no persisted row for {instance_id}|{_nexus_name} "
                                "(first deploy or cleared); starting fresh",
                                "cyan",
                            )
                        # None signals "run full lookback" to the caller below.
                        globals()["_NEXUS_SNAPSHOT_GAP_DATES"] = None
                    # 2-E (Scope D A2): strip stale backtest first_seen_price from
                    # the hydrated momentum watchlist so the runup baseline
                    # re-establishes from live bars (the consumer re-seeds it on the
                    # next score). Runs after EITHER load path. Gated like 2-B: ONLY
                    # on a backtest-origin / first-boot hydrate — NEVER strip a
                    # live-origin snapshot (Scope C re-stripped live baselines on
                    # every restart, permanently disabling the runup ceiling).
                    try:
                        from live_boot_setup import (
                            strip_stale_momentum_baseline as _strip_mom,
                            should_reset_backtest_state_on_boot as _should_reset_bt2,
                        )
                        if _should_reset_bt2(_snap_origin, _clean_room_first_boot):
                            _stripped = _strip_mom(_nexus_cache)
                            if _stripped:
                                _log(
                                    f"[snapshot] stripped stale first_seen_price from "
                                    f"{_stripped} momentum entries "
                                    f"(origin={_snap_origin!r} first_boot={_clean_room_first_boot})",
                                    "cyan",
                                )
                    except Exception as _mom_e:
                        _log(f"[snapshot] momentum-baseline strip failed (non-fatal): {_mom_e}", "yellow")
                finally:
                    try:
                        _boot_conn.close()
                    except Exception:
                        pass
            else:
                # No DB connection at all -> conservative: run full lookback.
                globals()["_NEXUS_SNAPSHOT_GAP_DATES"] = None
            # F1b: ramp bypass on warm cold boot.
            try:
                _bar_now = int(_nexus_cache.get("_deployment_bar_index", 0) or 0)
            except Exception:
                _bar_now = 0
            # Phase C (2026-04-29) — adapter-agnostic warm-boot positions
            # probe. Was: `live_adapter._client.get_all_positions()` which
            # only worked for Alpaca's TradingClient; for RobinhoodAdapter
            # the underlying client is `RobinhoodClient` and the method is
            # named differently. Use the BrokerAdapter ABC method so any
            # adapter implementation works. refresh_positions() returns a
            # list[PositionDTO] with PositionDTO.qty as float.
            _warm_position_count = 0
            try:
                # 2026-05-05 third pass: force=True so the warm-boot probe
                # actually fetches AND populates the cache. Without force,
                # the new cache-only refresh_positions returns empty on
                # cold cache, so the snapshot would have nothing to show
                # until the first hourly pre-cycle ran (~2-3 min after
                # boot in current configs).
                try:
                    _adapter_positions = live_adapter.refresh_positions(force=True)
                except TypeError:
                    # AlpacaAdapter doesn't accept force= — fall back.
                    _adapter_positions = live_adapter.refresh_positions()
                for _p in _adapter_positions or []:
                    try:
                        if float(getattr(_p, "qty", 0.0) or 0.0) > 0:
                            _warm_position_count += 1
                    except Exception:
                        continue
            except Exception as _fb_exc:
                _log(
                    f"F1b warm-boot positions probe failed: {type(_fb_exc).__name__}: {_fb_exc}",
                    "yellow",
                )
            # 2026-05-05 third pass: also seed the AccountDTO cache at
            # warm-boot so the first snapshot tick has something to read.
            # Without this, refresh_account(force=False) raises BrokerError
            # ("cache cold") on the very first tick. Best-effort — if this
            # fails, the snapshot's existing exception handler falls back
            # to cached _cash/_initial_value (which __init__ also seeded).
            try:
                live_adapter.refresh_account(force=True)
            except TypeError:
                try:
                    live_adapter.refresh_account()
                except Exception as _acc_seed_e:
                    _log(
                        f"F1b warm-boot account-DTO seed failed: "
                        f"{type(_acc_seed_e).__name__}: {_acc_seed_e}",
                        "yellow",
                    )
            except Exception as _acc_seed_e:
                _log(
                    f"F1b warm-boot account-DTO seed failed: "
                    f"{type(_acc_seed_e).__name__}: {_acc_seed_e}",
                    "yellow",
                )
            # bug-sweep: only fast-forward on a TRUE cold boot (no persisted
            # `_deployment_last_bar_key`). If a prior session made at least
            # one `run_once` call, `_deployment_last_bar_key` was set at
            # graph_nexus_analysis.py:6512 — in that case we trust the
            # persisted bar_index to be intentional, not a "stuck at 1"
            # artifact. This prevents F1b from over-riding a legitimate
            # day-1 ramp on a fresh deploy's very first tick.
            _has_prior_tick = bool(_nexus_cache.get("_deployment_last_bar_key"))
            if _warm_position_count > 0 and _bar_now <= 1 and not _has_prior_tick:
                # Strategy's baked default ramp is length 3 ([0.5, 0.7, 0.9]);
                # bug-sweep corrected from 5. If the config overrides this
                # to a longer schedule, `_get_deployment_ramp_bar_index`
                # at graph_nexus_analysis.py:6561 harmlessly stays at
                # `ramp_cap_pct=1.0` when bar_index > len — the ramp gate
                # disables, which is exactly what we want.
                _ramp_caps_len = 3
                _nexus_cache["_deployment_bar_index"] = int(_ramp_caps_len)
                # Seed `_deployment_last_bar_key` to a sentinel so the next
                # call inside `_get_deployment_ramp_bar_index` DOESN'T
                # increment (different bar_key would bump it past len → ramp
                # disabled branch, which is what we want).
                _nexus_cache["_deployment_last_bar_key"] = ""
                _log(
                    f"F1b ramp bypass: warm portfolio ({_warm_position_count} "
                    f"position(s)) detected at cold boot → _deployment_bar_index "
                    f"fast-forwarded to {_ramp_caps_len} (skip ramp)",
                    "green",
                )
            # 2026-05-01 — account-migration detector. After cache + warm-
            # positions probe, compare the cached drawdown peak against the
            # broker's CURRENT equity. A peak materially above current equity
            # (>40% gap) AND empty broker positions strongly suggests the
            # operator switched brokerages (e.g. Alpaca → Robinhood) leaving
            # state from the prior account. Without resetting, the persisted
            # peak immediately trips drawdown_halt and demotes every buy.
            #
            # Behavior:
            #   - Always log RED + Discord alert when detected.
            #   - If LIVE_AUTO_RESET_ON_MIGRATION=true, also clear migration-
            #     sensitive cache keys (peak, halt state, sold cooldowns,
            #     deployment ramp index) so the new account starts clean.
            #   - Otherwise: log the manual reset query and continue (operator
            #     must clear cache themselves).
            try:
                _cache_dd_state = _nexus_cache.get("_portfolio_drawdown_state") or {}
                _cache_peak = float(_cache_dd_state.get("peak_value") or 0.0)
                _cur_equity = float(getattr(live_adapter, "_initial_value", 0.0) or 0.0)
                _migration_detected = (
                    _cache_peak > 0
                    and _cur_equity > 0
                    and _cache_peak > _cur_equity * 1.40
                    and _warm_position_count == 0
                )
                if _migration_detected:
                    _drop_pct = ((_cache_peak - _cur_equity) / _cache_peak) * 100.0
                    _msg = (
                        f"ACCOUNT MIGRATION DETECTED: cached peak=${_cache_peak:,.0f} "
                        f"vs current equity=${_cur_equity:,.0f} (-{_drop_pct:.1f}%) "
                        f"AND broker reports 0 positions. This usually means the "
                        f"instance was repointed to a different brokerage account. "
                        f"Stale state will trip drawdown_halt and demote all buys."
                    )
                    _log(_msg, "red")
                    try:
                        _alert_strategy_error(
                            instance_id=str(instance_id),
                            tag="account-migration",
                            message=_msg,
                        )
                    except Exception:
                        pass
                    # 2026-05-28: clean_room_mode implies operator intent
                    # to start fresh, so the migration detector auto-resets
                    # by default. Explicit LIVE_AUTO_RESET_ON_MIGRATION=false
                    # still overrides (operator override always wins).
                    try:
                        from live_boot_setup import should_auto_reset_on_migration as _should_auto_reset
                        _auto_reset = _should_auto_reset(
                            os.environ.get("LIVE_AUTO_RESET_ON_MIGRATION"),
                            _clean_room_mode,
                        )
                    except Exception:
                        _auto_reset = (
                            os.environ.get("LIVE_AUTO_RESET_ON_MIGRATION", "")
                            or ""
                        ).strip().lower() in ("1", "true", "yes")
                    # Keys to reset on migration. Conservative — peak/halt
                    # are the must-clear; the rest are belt-and-suspenders so
                    # cooldowns / queues from the prior account don't bleed
                    # through. _deployment_bar_index is reset so F1b's
                    # warm-boot ramp-bypass doesn't lock the ramp at a stale
                    # value relative to the new (smaller) account.
                    _migration_reset_keys = (
                        "_portfolio_drawdown_state",
                        "_sold_cooldown",
                        "_v28_hold_trim_cooldown",
                        "_v32_position_history",
                        "_v32_entry_prices",
                        "_v32_trailing_stops",
                        "_overlay_no_data_tickers",
                        "_deployment_bar_index",
                        "_deployment_last_bar_key",
                        "_backfill_queue",
                        "_strategy_start_alert_date",
                        # Tier-3 Phase 3 (2026-05-17): observation-only telemetry
                        # buffer; safe to clear on account migration so the new
                        # account starts with a clean rolling window.
                        "_nexus_conviction_telemetry",
                        "_nexus_conviction_telemetry_capped_logged",
                        # Tier-3 Phase 2b (2026-05-17): momentum post-sell tracker.
                        # Stale entries reference symbols held in the prior
                        # account; clear so the new account doesn't lift cooldowns
                        # on prior tickers' breakouts.
                        "_post_sell_breakout_history",
                        "_post_sell_breakout_reentry_cooldown",
                        # BT136708 P1.7 (2026-05-18): A4 in-memory post_sell_watch
                        # mirror for backtest exercise + the mcap pre-seed flag.
                        # Both reference state from the prior account; clear so
                        # the new account starts fresh.
                        # Phase γ.1 (2026-05-18, BT232179 follow-up): the bool flag
                        # `_yf_market_cap_cache_preseeded` is replaced by a set[str]
                        # `_yf_market_cap_cache_preseeded_tickers`. Reset both
                        # forms during migration so stale seeded-state from the
                        # prior account doesn't carry across.
                        "_post_sell_watch_inmem",
                        "_yf_market_cap_cache_preseeded",
                        "_yf_market_cap_cache_preseeded_tickers",
                        # Phase α.2 (BT109429 follow-up, 2026-05-18): Neo4j
                        # query snapshot cache + its hit/miss telemetry.
                        # Per-backtest-run constructs; carrying them across
                        # an account migration would surface stale results.
                        "_neo4j_snapshot",
                        "_neo4j_snapshot_stats",
                    )
                    if _auto_reset:
                        for _k in _migration_reset_keys:
                            _nexus_cache.pop(_k, None)
                        _log(
                            f"Account migration auto-reset complete: cleared "
                            f"{len(_migration_reset_keys)} cache key(s). Peak will "
                            f"re-baseline to ${_cur_equity:,.0f} on next strategy run.",
                            "green",
                        )
                    else:
                        _log(
                            "LIVE_AUTO_RESET_ON_MIGRATION not set — strategy will run "
                            "with stale state. To reset: set "
                            "LIVE_AUTO_RESET_ON_MIGRATION=true and restart, OR run "
                            "RethinkDB query: r.db('IntelliStock').table("
                            "'NexusStrategyCache').get('"
                            f"{instance_id}|graph_nexus_analysis').delete()",
                            "yellow",
                        )
            except Exception as _mig_e:
                _log(
                    f"Account migration check failed (non-fatal): "
                    f"{type(_mig_e).__name__}: {_mig_e}",
                    "yellow",
                )

            # 2-B (bug-sweep 2026-05-28): in clean_room_mode the hydrated snapshot
            # is the origin="backtest" row, so its _portfolio_drawdown_state carries
            # the BACKTEST peak/halt. The migration detector above only re-baselines
            # when cached peak > 1.40x equity AND 0 positions; in the 1.0-1.40x band
            # (or with any position) a stale peak silently demotes or halts day-1
            # buys. Re-baseline the drawdown state to live equity unconditionally on
            # a clean-room boot so the new account starts measuring drawdown from its
            # own equity, not a historical backtest high-water mark.
            if _clean_room_mode:
                try:
                    from live_boot_setup import (
                        rebaseline_clean_room_drawdown as _rebaseline_dd,
                        should_reset_backtest_state_on_boot as _should_reset_bt,
                    )
                    # Scope D A1: only re-baseline when the hydrated snapshot is
                    # backtest-origin (or the genuine first clean-room boot).
                    # NEVER reset a live-origin snapshot — Scope C fired this on
                    # EVERY restart, silently clearing the live drawdown halt and
                    # high-water mark on a real-money account.
                    if _should_reset_bt(_snap_origin, _clean_room_first_boot):
                        _cur_eq = float(getattr(live_adapter, "_initial_value", 0.0) or 0.0)
                        if _rebaseline_dd(_nexus_cache, _cur_eq) is not None:
                            _log(
                                f"[live_boot] clean-room drawdown re-baselined to live "
                                f"equity ${_cur_eq:,.2f} (cleared backtest-origin peak/halt; "
                                f"origin={_snap_origin!r} first_boot={_clean_room_first_boot}).",
                                "cyan",
                            )
                    else:
                        _log(
                            f"[live_boot] drawdown re-baseline SKIPPED — preserving "
                            f"live-accumulated peak/halt (origin={_snap_origin!r} "
                            f"first_boot={_clean_room_first_boot}).",
                            "cyan",
                        )
                except Exception as _dd_e:
                    _log(f"[live_boot] drawdown re-baseline failed (non-fatal): {_dd_e}", "yellow")
            globals()["_strategy_cache_loaded_from_db"] = True
        except Exception as _scp_be:
            try:
                _log(
                    f"F1/F1b boot sequence failed (continuing with fresh cache): "
                    f"{type(_scp_be).__name__}: {_scp_be}",
                    "yellow",
                )
            except Exception:
                pass
            globals()["_strategy_cache_loaded_from_db"] = True
            # Boot exception path -> conservative: run full lookback.
            globals()["_NEXUS_SNAPSHOT_GAP_DATES"] = None

        # portfolio_emulator points at the adapter so strategies read through transparently.
        portfolio_emulator = live_adapter
        _log(
            f"Live broker adapter ready: broker={live_broker_type} paper={live_broker_paper} "
            f"equity=${getattr(live_adapter, '_initial_value', 0.0):.2f}",
            "green",
        )
        # 2026-05-07 scheduler refactor: fire a Discord boot ping so the
        # operator can confirm the Discord pipeline is wired BEFORE the
        # first MONITOR cycle. If this ping doesn't arrive, the issue is
        # the channel/webhook config, not the per-MONITOR call site.
        try:
            def _boot_discord_ping():
                try:
                    from live_alerts import _safe_enqueue
                    _equity = getattr(live_adapter, '_initial_value', 0.0)
                    content = (
                        f"[{instance_id}] Broker boot | broker={live_broker_type} "
                        f"paper={live_broker_paper} equity=${_equity:.2f}"
                    )
                    _safe_enqueue("notifications", content, embed=None)
                    try:
                        _log(f"Discord boot-ping enqueued (channel=notifications)", "cyan")
                    except Exception:
                        pass
                except Exception as _be:
                    try:
                        _log(f"Discord boot-ping FAILED: {type(_be).__name__}: {_be}", "yellow")
                    except Exception:
                        pass
            threading.Thread(target=_boot_discord_ping, daemon=True).start()
        except Exception:
            pass
        # Defence-in-depth: once the adapter's TradingClient holds the creds,
        # wipe the module-level strings so a stack-dump or traceback.print_exc
        # mid-session cannot surface them. The adapter retains its own copy
        # internally for the WebSocket stream re-init path.
        try:
            key = ""
            secret = ""
        except Exception:
            pass
        # Live-state wiring: spawn snapshot + command worker threads. The
        # per-instance log file was already bound BEFORE the adapter build
        # (Fix 6) so boot-time reconcile / refresh_orders_today lines are
        # already in the file.
        try:
            _start_live_trading_threads(live_adapter)
        except Exception as _t_e:
            _log(f"Could not start live-trading state threads: {_t_e}", "yellow")
    except Exception as _e_live:
        _log(f"Live adapter init failed: {type(_e_live).__name__}: {_e_live}", "red")
        # Phase C (2026-04-29): Discord alert on outer live-init failure.
        try:
            from live_alerts import alert_strategy_error as _ase_outer
            _ase_outer(
                instance_id=str(instance_id),
                tag=f"live-init:{live_broker_type}",
                message=f"Live adapter init failed: {type(_e_live).__name__}: {_e_live}",
            )
        except Exception:
            pass
        sys.exit(6)

# Load strategies once at startup (backtest loads earlier for SPY; live loads here)
if mode != MODE_BACKTEST:
    _cached_strategies, _strategy_row_id, _ = load_strategies_from_db()
    if _cached_strategies:
        _log(f"Loaded {len(_cached_strategies)} strategy(ies) from DB", "green")
        # Resolve all model_id references at startup. force_refresh drops
        # the resolver's 5-min TTL cache so the live broker always boots
        # with the latest credentials from the Models table — a key
        # edit via the UI propagates on the next live-broker (re)start
        # AND on every bar via run_run_once_strategies' per-call refresh.
        try:
            _resolve_conn = get_conn()
            for _spec in _cached_strategies:
                cfg = _spec.get("config") or {}
                _spec["config"] = resolve_model_refs_in_config(_resolve_conn, cfg, force_refresh=True)
            _resolve_conn.close()
            _log("Resolved model_id references in strategy configs (force_refresh=True)", "green")
        except Exception as _e:
            _log(f"Model resolution warning: {_e}", "yellow")
        # LIVE MODE: apply in-memory overrides on top of the user's DB-tuned configs.
        # The DB rows are NEVER mutated by this step - apply_live_overrides returns
        # a new dict. Strategies table stays frozen per user directive.
        if mode == MODE_LIVE:
            for _spec in _cached_strategies:
                _spec_cfg = _spec.get("config") or {}
                _spec["config"] = _apply_live_overrides(_spec_cfg)
            _log(
                "Applied LIVE_OVERRIDES in-memory to strategy configs "
                "(analyst_panel off, mcap-fail-closed, drawdown halt, LLM bounded). "
                "DB Strategies rows unchanged.",
                "cyan",
            )
        _cached_strategies = sorted(_cached_strategies, key=lambda s: int(s.get('execution_position', 0)))
        _post_decision_specs = [
            s for s in _cached_strategies
            if (
                (str(s.get("decision_phase") or "pre").strip().lower() == "post")
                or (str(s.get("strategy") or "").strip().lower() == "position_sizing")
            )
        ]
        _pre_decision_specs = [s for s in _cached_strategies if s not in _post_decision_specs]
        _run_once_specs = [s for s in _pre_decision_specs if (str(s.get("execution_scope") or "per_symbol").strip().lower() == "run_once")]
        _per_symbol_specs = [s for s in _pre_decision_specs if s not in _run_once_specs]
        if _post_decision_specs:
            _log(f"Pre-decision (voting): {len(_pre_decision_specs)}; post-decision (size/pricing): {len(_post_decision_specs)}", "cyan")
        if _run_once_specs:
            _log(f"Run-once (pre-strategy-execution): {len(_run_once_specs)}; per-symbol: {len(_per_symbol_specs)}", "cyan")
    else:
        _log("No strategies found for this instance", "yellow")
        _pre_decision_specs = []
        _post_decision_specs = []
        _run_once_specs = []
        _per_symbol_specs = []
elif not _cached_strategies:
    _log("No strategies found for this instance", "yellow")
else:
    # backtest path already set _cached_strategies; ensure pre/post split is set
    _post_decision_specs = [
        s for s in (_cached_strategies or [])
        if (
            (str(s.get("decision_phase") or "pre").strip().lower() == "post")
            or (str(s.get("strategy") or "").strip().lower() == "position_sizing")
        )
    ]
    _pre_decision_specs = [s for s in (_cached_strategies or []) if s not in _post_decision_specs]
    _run_once_specs = [s for s in _pre_decision_specs if (str(s.get("execution_scope") or "per_symbol").strip().lower() == "run_once")]
    _per_symbol_specs = [s for s in _pre_decision_specs if s not in _run_once_specs]

# Start thread to watch for strategy changes (only in live mode, not backtest)
if mode == MODE_LIVE:
    try:
        import threading
        strategies_watch_thread = threading.Thread(target=watch_strategies_changefeed, daemon=True)
        strategies_watch_thread.start()
        _log("Started strategies changefeed watcher", "green")
    except Exception as e:
        _log(f"Could not start strategies watcher thread: {e}", "yellow")

# 2026-07-12: parse extracted to bar_time.py (import-safe, fromisoformat-first
# ~50x faster than dateutil on Alpaca ISO timestamps; output identical). Kept
# the private name so all call sites and the injected-helper usage still work.
from bar_time import bar_time_to_datetime as _bar_time_to_datetime

def _current_time_to_utc(current_time):
    """Convert current_time to naive UTC for comparison with Alpaca bar times (which are in UTC)."""
    if current_time is None:
        return None
    if isinstance(current_time, datetime.datetime):
        if getattr(current_time, "tzinfo", None) is not None:
            return current_time.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        # Naive timestamps in this broker are UTC by convention.
        return current_time
    try:
        if hasattr(current_time, "year") and hasattr(current_time, "month") and hasattr(current_time, "day"):
            return datetime.datetime(current_time.year, current_time.month, current_time.day)
    except Exception:
        pass
    try:
        parsed = _bar_time_to_datetime(str(current_time))
        return parsed
    except Exception:
        return None


# Mode-aware daily session windows (Pacific time):
#   - LIVE: 1 AM – 5 PM PT (= 4 AM – 8 PM ET, full Alpaca extended-hours window).
#   - BACKTEST: 5 AM – 8 PM PT — preserves legacy backtest reproducibility.
_TRADING_DAY_START_HOUR_LIVE = 1
_TRADING_DAY_END_HOUR_LIVE = 17
_TRADING_DAY_START_HOUR_BACKTEST = 5
_TRADING_DAY_END_HOUR_BACKTEST = 20
_TRADING_DAY_START_HOUR = _TRADING_DAY_START_HOUR_LIVE
_TRADING_DAY_END_HOUR = _TRADING_DAY_END_HOUR_LIVE


def _session_hours_for_mode() -> tuple[int, int]:
    if mode == MODE_BACKTEST:
        return (_TRADING_DAY_START_HOUR_BACKTEST, _TRADING_DAY_END_HOUR_BACKTEST)
    return (_TRADING_DAY_START_HOUR_LIVE, _TRADING_DAY_END_HOUR_LIVE)


# 2026-04-30 v2 Task A: session helpers extracted to broker_session.py
# so tests can import without broker.py's module-level side effects
# (argparse, DB connections, adapter build). Live mode now uses the
# NYSE-aware gate (is_within_live_session) which honors holidays and
# early closes via exchange_calendars; backtest keeps the legacy PT
# window for determinism. Thin mode-aware wrappers below preserve the
# existing public names so callers (lines 2281, 2629, 5617, 6925,
# 6929, 6951) keep working.
from broker_session import (
    is_within_legacy_pt_window as _is_within_legacy_pt_window,
    is_within_live_session as _is_within_live_session,
    next_legacy_pt_open_utc as _next_legacy_pt_open_utc,
    next_market_open_utc as _next_live_market_open_utc,
    advance_backtest_time as _advance_backtest_time,
)


def _is_within_trading_session_pt(current_time):
    """Mode-aware PT session gate.

    LIVE: 1 AM-5 PM PT (covers Alpaca extended hours; replaced by the
    NYSE-aware ``_is_within_live_session`` at the live tick gate).
    BACKTEST: 5 AM-8 PM PT (legacy window; preserves backtest determinism).
    """
    start_h, end_h = _session_hours_for_mode()
    return _is_within_legacy_pt_window(current_time, start_h, end_h)


def _next_market_open_utc(current_time):
    """Mode-aware "next market open" returning naive UTC.

    BACKTEST uses the legacy PT-window logic (5 AM PT next trading day)
    so daily-bar replay timestamps stay stable across runs. LIVE uses
    the NYSE-aware calendar but still returns naive UTC (callers at
    line 2629 already strip tzinfo before subtracting).
    """
    if mode == MODE_BACKTEST:
        start_h, end_h = _session_hours_for_mode()
        return _next_legacy_pt_open_utc(current_time, start_h, end_h)
    # LIVE: NYSE-aware. Returns aware UTC or None; callers normalise.
    aware = _next_live_market_open_utc(current_time)
    if aware is None:
        return None
    if getattr(aware, "tzinfo", None) is not None:
        return aware.replace(tzinfo=None)
    return aware


def _is_daily_backtest_timeframe() -> bool:
    """True when backtest bars are daily; daily close is only available after day completion."""
    try:
        return mode == MODE_BACKTEST and str(_backtest_alpaca_timeframe or "").strip().lower() == "1day"
    except Exception:
        return False


def _can_use_same_day_daily_bar(current_utc: datetime.datetime) -> bool:
    """
    Daily bars represent completed-day OHLC. In normal loop iterations we must not use
    same-day daily bars; allow only explicit end-of-day snapshots (e.g. final summary).
    """
    try:
        return current_utc.time() >= datetime.time(23, 59, 0)
    except Exception:
        return False


def _get_prices_at_time(data, symbols, current_time, use_cursor=False):
    """Get close price ('c') for each symbol at the bar that corresponds to current_time (latest bar at or before current_time).

    ``use_cursor=True`` (passed only from the monotonic main backtest loop, which
    always uses the master ``data``) takes the O(new_bars)/call cursor path for
    the non-daily case; every other call site keeps the O(n) full scan so the
    cursor never has to reason about a different ``data`` object or the daily
    same-day-bar rule."""
    prices = {}
    current_utc = _current_time_to_utc(current_time)
    if current_utc is None:
        return prices
    daily_mode = _is_daily_backtest_timeframe()
    allow_same_day_daily = _can_use_same_day_daily_bar(current_utc)
    if use_cursor and mode == MODE_BACKTEST and not daily_mode:
        return _bprices_cursor.latest_price_at(
            data, symbols, current_utc, bar_time_to_datetime=_bar_time_to_datetime,
        )
    for sym in symbols:
        bars = data.get(sym) or []
        bar_at_time = None
        for b in bars:
            bt = _bar_time_to_datetime(b.get("t"))
            if bt is None:
                continue
            if daily_mode:
                if bt.date() < current_utc.date() or (allow_same_day_daily and bt.date() == current_utc.date()):
                    bar_at_time = b
                else:
                    break
            else:
                if bt <= current_utc:
                    bar_at_time = b
                else:
                    break
        if bar_at_time is not None and "c" in bar_at_time:
            try:
                prices[sym] = float(bar_at_time["c"])
            except (TypeError, ValueError):
                pass
    return prices


def _backtest_symbol_price_lookup_block_reason(data, symbol, current_time):
    symbol = str(symbol or "").strip().upper()
    if mode != MODE_BACKTEST or not isinstance(data, dict) or not symbol:
        return ""
    if symbol in _backtest_no_history_symbols:
        return "no_history"
    bars = data.get(symbol) or []
    if not bars:
        return ""
    current_utc = _current_time_to_utc(current_time)
    first_bar_dt = _bar_time_to_datetime((bars[0] or {}).get("t"))
    if current_utc is None or first_bar_dt is None:
        return ""
    if _is_daily_backtest_timeframe():
        return "prelisting" if first_bar_dt.date() > current_utc.date() else ""
    return "prelisting" if first_bar_dt > current_utc else ""


# 2026-05-08 backtest perf: monotonic cursor cache for
# get_price_history_up_to_current. Since backtest time advances
# strictly forward, we track per-symbol cursors and advance them only
# as far as the current_time requires. The cache state + helper live
# in `backtest_price_history` so they can be tested without importing
# broker.py (not import-safe — runs argparse + main path at load).
import backtest_price_history as _bph
import backtest_prices_cursor as _bprices_cursor


def _invalidate_price_history_cursor() -> None:
    """Module-level shim that delegates to the cache module."""
    _bph.invalidate_cursor()


def get_price_history_up_to_current(data, symbols, current_time):
    """
    (Backtesting only.) Return price history from the start of the fetched range (including warmup)
    up to and including current emulated time. So strategies receive only past bars, no lookahead.
    Returns: dict symbol -> list of bar dicts (t, o, h, l, c, v) with bar['t'] <= current_time, sorted by t.
    """
    return _bph.get_price_history_up_to_current(
        data, symbols, current_time,
        daily_mode=_is_daily_backtest_timeframe(),
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
    )

print("Time Increment:", time_increment)

# Record backtest start time for elapsed time calculation
backtest_start_time = None
# Backtest progress tracking: document id for DB updates, last progress % updated, last loop time
_backtest_result_id = None
_last_progress_updated = -2.0  # so first update at 0%; then every 2% so progress is visible in DB
# Throttle "Could not fetch price" to once per symbol per run (avoid log spam)
_fetch_price_fail_logged = set()
_backtest_last_loop_time = None
_backtest_loop_is_slow = None  # None until first loop completes; then True if loop >= 0.3s
# Backtest log capture (last 500 lines saved to BacktestResults.logs)
_backtest_log_buffer = None
# Consecutive progress-update failures; we only exit after this many in a row (transient RethinkDB blips)
_backtest_progress_fail_count = 0
_BACKTEST_PROGRESS_FAIL_MAX = 5  # Exit only after 5 consecutive failed progress updates
# Long-lived RethinkDB connection for backtest (reused for all progress updates to avoid repeated connect/disconnect)
_backtest_db_conn = None
# Per-bar, per-symbol sub-strategy decision log for playback UI
_backtest_decisions = []

if mode == MODE_BACKTEST:
    import time
    import random
    import numpy as _np_backtest
    backtest_start_time = time.time()
    # Engine creates BacktestResults row with status running; we update it with full stub
    backtest_id_raw = backtest_row_id
    _backtest_result_id = int(backtest_id_raw) if backtest_id_raw and str(backtest_id_raw).isdigit() else backtest_id_raw
    # Start capturing logs to buffer (last 500 lines) for live DB progress writes
    _backtest_log_buffer = []
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.set_backtest_log_buffer(_backtest_log_buffer, max_lines=500)
        # Also open persistent log file on the shared Docker volume (unlimited full log)
        import os as _os
        _log_dir = _os.environ.get('BACKTEST_LOG_DIR', '/app/backtest_logs')
        try:
            _os.makedirs(_log_dir, exist_ok=True)
            _log_file_path = _os.path.join(_log_dir, f"{_backtest_result_id}.log")
            _log_file_obj = open(_log_file_path, 'w', buffering=1, encoding='utf-8')
            intellistock_logger.set_backtest_log_file(_log_file_obj)
        except Exception:
            pass
    except Exception:
        _backtest_log_buffer = []
    # Phase γ.2 (2026-05-18, BT232179 follow-up): the α.3 seed-log block
    # below MUST emit AFTER the log buffer + file sink are wired so the
    # `RNG seed: ...` and PYTHONHASHSEED confirmation lines land in the
    # operator-pulled audit log (not just stdout). Buffer and file are
    # independent — `intellistock_logger.log` fans out to whichever
    # contexts are currently attached, so the seed block writes to
    # whatever wiring succeeded above (buffer + file, just file, or
    # neither, in which case the lines still hit stdout).
    # Phase α.3 (2026-05-18, BT109429 follow-up): always seed the process-
    # global RNGs in backtest mode for paired re-run determinism. The
    # variance/robustness agent attributed ~5% of the 4.8x same-code spread
    # to RNG drift across paired runs (LLM-jitter timing, set ordering when
    # combined with dict insertion variance). Explicit BACKTEST_SEED env
    # var still wins for back-compat. Derivation extracted to
    # _phase_alpha_helpers.derive_backtest_seed for unit testability. Bare
    # import (not `backend.`) because prod Docker layout is flat at /app/.
    from _phase_alpha_helpers import derive_backtest_seed as _derive_backtest_seed
    _seed_int, _seed_source = _derive_backtest_seed(
        backtest_row_id,
        symbols,
        env_seed=os.environ.get("BACKTEST_SEED"),
    )
    _log(f"RNG seed: {_seed_int} ({_seed_source})", "cyan")
    try:
        random.seed(_seed_int)
        # numpy seed must fit uint32; safe to mask. 31-bit derived seeds
        # are already within range, so this is a no-op for them and a
        # narrowing for raw BACKTEST_SEED env values >= 2**32.
        _np_backtest.random.seed(_seed_int & 0xFFFFFFFF)
    except Exception as _seed_apply_exc:
        _log(f"RNG seed apply failed: {_seed_apply_exc!r}", "yellow")
    # Set-iteration variance reminder: Python's set ordering depends on
    # PYTHONHASHSEED, which must be exported BEFORE the interpreter starts —
    # setting it inside this process is too late (Python has already cached
    # the hash randomization for the duration of the run).
    _ph_seed = os.environ.get("PYTHONHASHSEED")
    if _ph_seed in (None, "", "random"):
        _log(
            "RNG seed: PYTHONHASHSEED is unset or 'random' — set "
            "PYTHONHASHSEED=0 in the BACKTEST ENGINE's launch env "
            "(Docker `environment:` block or .env, BEFORE the broker "
            "subprocess starts) for full set-ordering determinism "
            "across paired re-runs (Phase alpha.3).",
            "yellow",
        )
    else:
        _log(f"RNG seed: PYTHONHASHSEED={_ph_seed} (confirmed)", "cyan")
    try:
        conn = get_conn_retry(max_attempts=5, delay=2)
        if conn is None:
            _log("Backtest cannot connect to RethinkDB after 5 attempts (exiting).", "red")
            _log("  Current RETHINKDB_HOST=%s (set by backtest engine from INSTANCE_RETHINKDB_HOST in .env)." % os.environ.get('RETHINKDB_HOST', 'localhost'), "red")
            _log("  If RethinkDB runs on the host machine: set INSTANCE_RETHINKDB_HOST=host.docker.internal in .env so the container can reach it.", "red")
            _log("  If RethinkDB is remote: ensure the container can reach that host (network/firewall).", "red")
            sys.stdout.flush()
            sys.stderr.flush()
            sys.exit(1)
        try:
            tables = list(r.db(DB_NAME).table_list().run(conn))
            if 'BacktestResults' not in tables:
                r.db(DB_NAME).table_create('BacktestResults').run(conn)
            # instance_id can be numeric (8) or string (e.g. "ai-temp-xxx"); store as-is for BacktestResults
            instance_id_for_db = int(instance_id) if (instance_id and str(instance_id).isdigit()) else instance_id
            try:
                backtest_start_date = _backtest_start_dt_input.isoformat() if _backtest_start_dt_input else (start_dt.isoformat() if start_dt else None)
                backtest_end_date = _backtest_end_dt_input.isoformat() if _backtest_end_dt_input else (end_dt.isoformat() if end_dt else None)
            except NameError:
                backtest_start_date = backtest_end_date = None
            backtest_id_int = _backtest_result_id if isinstance(_backtest_result_id, int) else (int(_backtest_result_id) if _backtest_result_id else None)
            existing_result = r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).run(conn)
            # Duplicate-run guard: if another container is actively running this backtest, exit.
            # Uses _last_active heartbeat (updated on every progress write) to detect live runs.
            if existing_result is not None and existing_result.get('status') == 'running':
                _last_active = existing_result.get('_last_active', '')
                if _last_active:
                    try:
                        _la_dt = __import__('datetime').datetime.fromisoformat(_last_active.replace('Z', '+00:00'))
                        _now_utc = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                        _active_age = (_now_utc - _la_dt).total_seconds()
                    except Exception:
                        _active_age = 9999
                    if _active_age < 120:
                        _log(f"Backtest id={_backtest_result_id} is already running (heartbeat {int(_active_age)}s ago). "
                             "Exiting duplicate to avoid double LLM costs and race conditions.", "yellow")
                        try:
                            conn.close()
                        except Exception:
                            pass
                        sys.stdout.flush()
                        sys.stderr.flush()
                        sys.exit(0)
                    else:
                        _log(f"Backtest id={_backtest_result_id} has stale 'running' status (heartbeat {int(_active_age)}s ago, likely crashed). Overwriting.", "yellow")
            _now_iso = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
            stub = {
                'id': _backtest_result_id,
                'backtest_id': backtest_id_int,
                'instance_id': instance_id_for_db,
                'timestamp': _now_iso,
                'strategy_id': _strategy_row_id,
                'strategy_schema': _backtest_strategy_schema,
                'status': 'running',
                'progress': 0,
                'pnl': None,
                'pnl_percent': None,
                'start_date': backtest_start_date,
                'end_date': backtest_end_date,
                'tickers': symbols if symbols else [],
                'time_elapsed_seconds': None,
                'portfolio_value_history': [],
                'backtest_trades': [],
                'backtest_prices': [],
                'backtest_decisions': [],
                'logs': [],
                'granularity_sec': int(time_increment) if str(time_increment or '').strip().isdigit() else None,
                'initial_cash': initial_cash,
                '_last_active': _now_iso,
            }
            if existing_result is not None and existing_result.get('difficulty') is not None:
                stub['difficulty'] = existing_result['difficulty']
            # Validate required fields; if missing, write error row (progress=100, status=error) and exit
            validation_errors = []
            if not instance_id or not str(instance_id).strip():
                validation_errors.append("instance_id is missing or empty")
            # Crypto instances have NO Strategies-table row — their strategy is a
            # synthesized run_once spec built from crypto_config.strategy, so
            # strategy_id is legitimately null while the specs + schema ARE loaded
            # (schema name "crypto:<name>", see _crypto_synthetic_specs). Only flag a
            # genuinely missing strategy. The equity path (strategy_id present, or
            # truly absent) stays byte-identical.
            _crypto_synth_loaded = bool(
                _backtest_strategy_schema
                and str((_backtest_strategy_schema or {}).get("name", "")).startswith("crypto:")
            )
            if _strategy_row_id is None and not _crypto_synth_loaded:
                validation_errors.append("no strategy linked to instance (strategy_id is null)")
            # V7.3: Allow empty symbols for Nexus pure discovery mode
            # if not symbols or not [s for s in symbols if s and str(s).strip()]:
            #     validation_errors.append("no symbols/tickers provided")
            if validation_errors:
                err_msg = "; ".join(validation_errors)
                stub['status'] = 'error'
                stub['progress'] = 100.0
                stub['error'] = err_msg[:2000]
                assert_secret_free(stub)
                r.db(DB_NAME).table('BacktestResults').insert(stub, conflict='replace').run(conn)
                _log("Backtest validation failed: %s" % err_msg, "red")
                _log("BacktestResults row written with status=error, progress=100.", "yellow")
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                sys.stdout.flush()
                sys.stderr.flush()
                sys.exit(1)
            # Ensure row exists: insert with conflict='replace' so it works when broker runs standalone (no engine)
            assert_secret_free(stub)
            r.db(DB_NAME).table('BacktestResults').insert(stub, conflict='replace').run(conn)
            _log(f"Backtest result row (id={_backtest_result_id}) ensured in DB, status=running", "green")
            # Keep connection open for all progress updates (avoids repeated connect/disconnect that can cause "lost connection")
            _backtest_db_conn = conn
            conn = None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception as e:
        _log(f"Backtest cannot connect to RethinkDB or update result stub (exiting): {e}", "red")
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)
    # Watch BacktestInstances row for run=false (stop) and paused (pause/resume)
    if backtest_row_id:
        try:
            row_id = int(backtest_row_id)
            c = get_conn()
            row = r.db(DB_NAME).table('BacktestInstances').get(row_id).run(c)
            _backtest_paused = bool(row.get('paused', False)) if row else False
            c.close()
        except Exception:
            pass
        try:
            _backtest_run_watch_thread = threading.Thread(target=watch_backtest_run_command, daemon=True)
            _backtest_run_watch_thread.start()
            _log("Watching BacktestInstances for stop (run=false) and pause/resume (paused)", "green")
        except Exception as e:
            _log(f"Could not start backtest run watcher: {e}", "yellow")
    # ── Heartbeat thread ─────────────────────────────────────────────────
    # Heavy strategies (e.g. Nexus with LLM) can block the main loop for
    # minutes.  The progress-update block only runs at the *end* of each
    # loop iteration, so time_elapsed_seconds and _last_active go stale
    # while the strategy call blocks.  This heartbeat thread independently
    # updates those two fields every 15 seconds so the UI timer keeps
    # ticking between full progress writes.
    _heartbeat_stop = threading.Event()
    def _backtest_heartbeat():
        import datetime as _dt_hb
        hb_conn = None
        while not _heartbeat_stop.is_set() and not shutdown_requested:
            _heartbeat_stop.wait(15)
            if _heartbeat_stop.is_set() or shutdown_requested:
                break
            try:
                if hb_conn is None:
                    hb_conn = get_conn_retry(max_attempts=3, delay=2)
                if hb_conn is None:
                    continue
                now_hb = _dt_hb.datetime.now(_dt_hb.timezone.utc).isoformat()
                elapsed_hb = max(0, int(time.time() - backtest_start_time)) if backtest_start_time else None
                hb_payload = {'_last_active': now_hb}
                if elapsed_hb is not None:
                    hb_payload['time_elapsed_seconds'] = elapsed_hb
                if _backtest_log_buffer is not None:
                    hb_payload['logs'] = list(_backtest_log_buffer)[-500:]
                r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(hb_payload).run(hb_conn)
            except Exception:
                # Connection lost — reset so next iteration reconnects
                try:
                    if hb_conn:
                        hb_conn.close()
                except Exception:
                    pass
                hb_conn = None
        # Cleanup
        try:
            if hb_conn:
                hb_conn.close()
        except Exception:
            pass
    try:
        _heartbeat_thread = threading.Thread(target=_backtest_heartbeat, daemon=True)
        _heartbeat_thread.start()
        _log("Backtest heartbeat thread started (updates _last_active every 15s)", "green")
    except Exception as e:
        _log(f"Could not start heartbeat thread: {e}", "yellow")

def _backtest_save_error_and_exit(error_msg, progress_pct=None):
    """On backtest crash: write status=error, error message, and current logs to BacktestResults then exit."""
    global _backtest_db_conn
    # Stop heartbeat to prevent it from overwriting the error status
    try:
        _heartbeat_stop.set()
    except NameError:
        pass
    import traceback
    _log(f"Backtest error: {error_msg}", "red")
    tb = traceback.format_exc()
    if tb and tb.strip():
        _log(tb.strip(), "red")
    sys.stdout.flush()
    sys.stderr.flush()
    if _backtest_db_conn is not None:
        try:
            _backtest_db_conn.close()
        except Exception:
            pass
        _backtest_db_conn = None
    if _backtest_result_id is not None:
        try:
            conn = get_conn_retry(max_attempts=5, delay=3)
            if conn is None:
                _log("Could not connect to RethinkDB to save error status.", "red")
            else:
                try:
                    update_payload = {
                        'status': 'error',
                        'progress': round(progress_pct, 2) if progress_pct is not None else 100.0,
                        'timestamp': __import__('datetime').datetime.now().isoformat(),
                        'error': str(error_msg)[:2000],
                    }
                    if _backtest_log_buffer is not None:
                        update_payload['logs'] = list(_backtest_log_buffer)[-500:]
                    r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(update_payload).run(conn)
                    _log("Saved error status and logs to BacktestResults.", "yellow")
                    # Edit #backtests Discord message to Failed (same message that was Queued/Running)
                    try:
                        from interactive_utils import action_enqueue_discord_edit
                        err_short = (str(error_msg)[:300] + "...") if len(str(error_msg)) > 300 else str(error_msg)
                        msg_key = str(backtest_row_id) if backtest_row_id is not None else str(_backtest_result_id)
                        _res = r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).run(conn)
                        _d = _res.get('difficulty') if _res else None
                        diff_str = ("%.1f" % float(_d) + (" (HIGH USAGE)" if backtest_high_usage else "")) if _d is not None else _backtest_difficulty_discord_str()
                        action_enqueue_discord_edit(conn, "backtests", msg_key, content=None, embed={
                            "title": "Backtest Failed",
                            "description": "A backtest run ended with an error.",
                            "color": 0xE74C3C,
                            "fields": [
                                {"name": "ID", "value": str(_backtest_result_id), "inline": True},
                                {"name": "Status", "value": "error", "inline": True},
                                {"name": "Difficulty", "value": diff_str, "inline": True},
                                {"name": "Progress", "value": "%.1f%%" % (progress_pct or 0), "inline": True},
                                {"name": "Error", "value": err_short or "—", "inline": False},
                            ],
                        })
                    except Exception:
                        pass
                finally:
                    conn.close()
        except Exception as e2:
            _log(f"Could not save error to DB: {e2}", "red")
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.clear_backtest_log_buffer()
        intellistock_logger.close_backtest_log_file()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(1)


if mode == MODE_BACKTEST:
    try:
        _run_backtest_historic_lookback(
            _run_once_specs,
            symbols,
            data,
            start_dt,
            portfolio_emulator,
            time_increment,
            key,
            secret,
        )
    except Exception as e:
        _backtest_save_error_and_exit(f"Historic lookback prepass failed: {e}", progress_pct=0.0)

if mode == MODE_LIVE:
    try:
        # Phase 1 (2026-05-20): snapshot-aware lookback narrowing.
        # The F1 boot block above set _NEXUS_SNAPSHOT_GAP_DATES based on
        # whether a usable backtest snapshot was found:
        #   None  -> no snapshot or boot failed; run the FULL 120-day lookback.
        #   []    -> snapshot end_date covers today; SKIP lookback entirely.
        #   [...] -> snapshot is partial; restrict to the missing trading days.
        _gap_for_lookback = globals().get("_NEXUS_SNAPSHOT_GAP_DATES")
        if _gap_for_lookback == []:
            _log("[snapshot] gap_dates empty; skipping lookback", "green")
            _lb_caches = {}
        else:
            _lb_caches = _run_live_historic_lookback(
                _run_once_specs,
                symbols,
                # Scope E (2026-05-29): the LIVE historic lookback must fetch
                # bars/news with the DATA-source brokerage creds (data_key/
                # data_secret), NOT the trading creds (key/secret) — which for a
                # Robinhood-trading instance are not valid Alpaca data creds AND
                # are wiped to "" after the adapter build (~line 6007). Passing
                # empty/trading creds let the stale doc-179 alpaca_key win and
                # 401'd every data.alpaca.markets bars call (then fell back to RH).
                # Mirrors the per-tick path's _strat_data_key (data_key or key).
                data_key or key or os.environ.get("KEY", ""),
                data_secret or secret or os.environ.get("SECRET", ""),
                restrict_to_dates=_gap_for_lookback if _gap_for_lookback is not None else None,
            ) or {}
        # Seed the module-level _strategy_cache with whatever the prepass
        # accumulated (peak watermarks, fast-loser blacklist, V32 convert
        # cooldowns, momentum watchlist). Without this merge the state
        # evaporates the moment the function returns, and the main live
        # loop starts blind — exactly the backtest↔live parity bug that
        # motivated this revamp.
        for _cache_k, _cache_v in (_lb_caches or {}).items():
            if isinstance(_cache_v, dict):
                _strategy_cache.setdefault(_cache_k, {}).update(_cache_v)
            else:
                _strategy_cache[_cache_k] = _cache_v
        if _lb_caches:
            _log(
                f"Live lookback: merged {len(_lb_caches)} strategy cache(s) into main-loop state.",
                "green",
            )
    except Exception as e:
        _log(f"Live historic lookback failed (non-fatal): {e}", "yellow")

    # Phase 1 BLOCKER boot-time log lines (2026-05-20).
    # Emit AFTER the lookback completes and AFTER the cache merge, so the
    # warm-position count + bar_index + drawdown halt state reflect both
    # the snapshot hydrate and any lookback fills. Each block is wrapped
    # so an attribute miss in one doesn't suppress the others.
    _nexus_name_for_blocker_logs = "graph_nexus_analysis"
    # BLOCKER #2 — F1b ramp bypass with 0 warm positions
    try:
        _warm_pos_count = 0
        try:
            for _p in (getattr(live_adapter, "_positions", {}) or {}).values():
                try:
                    if float(getattr(_p, "qty", 0.0) or 0.0) > 0:
                        _warm_pos_count += 1
                except Exception:
                    continue
        except Exception:
            _warm_pos_count = len(getattr(live_adapter, "_positions", {}) or {})
        _bar_idx = _strategy_cache.get(_nexus_name_for_blocker_logs, {}).get("_deployment_bar_index", 0)
        _log(
            f"[live_boot] warm_positions={_warm_pos_count}, "
            f"F1b_bypass={'enabled' if _warm_pos_count > 0 else 'disabled'}, "
            f"ramp_starting_bar_index={_bar_idx}",
            "cyan",
        )
    except Exception:
        pass

    # BLOCKER #3 — _nexus_full_cycle_completed_date communication
    try:
        _fcd = _strategy_cache.get(_nexus_name_for_blocker_logs, {}).get(
            "_nexus_full_cycle_completed_date", ""
        )
        _log(
            f"[live_boot] _nexus_full_cycle_completed_date={_fcd or '<unset>'}; "
            f"next FULL cycle expected ~06:30 AM PT",
            "cyan",
        )
    except Exception:
        pass

    # Phase 1 BLOCKER #1 — settlement reminder (manual operator verification)
    # A robust adapter-agnostic settlement check is deferred; for now we just log
    # a reminder. Operator verifies via the launch checklist before starting.
    _log("[live_boot] BLOCKER #1 settlement: operator confirmed via launch checklist (no programmatic check)", "cyan")


# ---------------------------------------------------------------------------
# Bot trade-decision logging (for the mobile "Bot activity" view)
#
# On each CONFIRMED live buy/sell we record the decision + the reasoning the
# strategies produced into the BotTradeDecisions table, keyed by this instance's
# brokerage. This is purely additive telemetry: it runs OFF the trade path on a
# daemon thread and is wrapped so it can never delay or break trading. The
# record-building + primary-driver logic lives in the pure, unit-tested
# bot_decision_log module.
# ---------------------------------------------------------------------------

def _log_live_trade_decision(symbol, decision, price, ts, strategy_summary,
                             post_decision_trace, pre_override_decision, normalized):
    """Best-effort record of a confirmed live buy/sell + its reasoning. Never
    raises; the DB write happens on a daemon thread so it stays off the trade
    path."""
    try:
        import bot_decision_log as _bdl
        iid = instance_id  # module-level global

        def _persist():
            conn = None
            try:
                from rethinkdb import RethinkDB
                _rb = RethinkDB()
                conn = _rb.connect(
                    host=os.environ.get("RETHINKDB_HOST", "localhost"),
                    port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                    timeout=10,
                )
                db = "IntelliStock"
                if _bdl.TABLE not in list(_rb.db(db).table_list().run(conn)):
                    try:
                        _rb.db(db).table_create(_bdl.TABLE).run(conn)
                    except Exception:
                        pass
                bid = ""
                try:
                    inst = _rb.db(db).table("Instances").get(str(iid)).run(conn)
                    if inst:
                        bid = str(inst.get("brokerage_id") or "")
                except Exception:
                    pass
                doc = _bdl.build_decision_doc(
                    iid, bid, symbol, decision, price, ts,
                    strategy_summary, post_decision_trace,
                    pre_override_decision, normalized,
                )
                _rb.db(db).table(_bdl.TABLE).insert(doc).run(conn)
            except Exception:
                pass
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        threading.Thread(target=_persist, name="bot-trade-log", daemon=True).start()
    except Exception:
        pass


def _credit_guard_or_raise(*, call_site: str) -> str:
    """R2 Task 4: preflight OpenRouter credit guard.

    Best-effort and FAIL-OPEN: returns "ok"/"warn"/"skip" normally and NEVER
    lets its own failure block trading (a flaky credits endpoint degrades to
    "ok" — the reactive 402 de-cliff still protects us). On "halt" (balance
    at/under the halt threshold) it raises LLMCriticalFailure(insufficient_
    credits) so the existing outer-except routes it exactly like a mid-run
    402: backtest → clean-stop paused_credits; live → live_critical_abort +
    exit(7). Testable logic lives in openrouter_credits.py; this wrapper is
    the thin lazy-import wiring (broker.py is not import-safe)."""
    try:
        from openrouter_credits import run_credit_guard as _run_credit_guard
    except Exception:
        return "skip"

    def _notify(msg):
        try:
            _log(f"[credit-guard] {msg}", "yellow")
        except Exception:
            pass
        try:
            from live_alerts import alert_strategy_error as _alert
            _alert(instance_id=str(instance_id), tag="openrouter_low_credit",
                   message=str(msg))
        except Exception:
            pass

    try:
        result = _run_credit_guard(_run_once_specs, notify_fn=_notify)
    except Exception as _g_err:
        try:
            _log(f"[credit-guard] guard raised, ignoring: {_g_err}", "yellow")
        except Exception:
            pass
        return "skip"

    if result == "halt":
        try:
            _log(f"[credit-guard] OpenRouter balance at/below halt threshold at "
                 f"{call_site}; raising insufficient_credits critical.", "red")
        except Exception:
            pass
        from llm_critical_guard import LLMCriticalFailure
        raise LLMCriticalFailure(
            class_tag="insufficient_credits",
            provider="openrouter",
            model="(preflight)",
            attribution={"call_site": f"credit_preflight:{call_site}",
                         "instance_id": str(instance_id)},
            attempts=[{"attempt": 1, "class_tag": "insufficient_credits",
                       "http_status": 402,
                       "body_sample": ("OpenRouter preflight credit guard: balance "
                                       "at/below halt threshold; top up credits and "
                                       "re-queue.")}],
        )
    return result


# R2 Task 4: sim-day counter driving the every-5-days backtest credit check.
_bt_credit_guard_tick = 0

while not shutdown_requested:
    try:
        ###################################
        ## Backtesting: record loop start for duration check
        ###################################
        if mode == MODE_BACKTEST:
            import time as _time
            _backtest_loop_start = _time.time()
            # R2 Task 4: preflight OpenRouter credit guard at backtest START
            # (tick 0) and every 5 sim days thereafter. A "halt" raises
            # LLMCriticalFailure(insufficient_credits); the outer-except then
            # clean-stops with status=paused_credits instead of simulating an
            # entire month LLM-blind (incident 586767).
            if _bt_credit_guard_tick % 5 == 0:
                _credit_guard_or_raise(call_site="backtest_sim_day")
            _bt_credit_guard_tick += 1
        ###################################
        ## Backtesting/Live Price Fetching
        ###################################
        # Emit the "Running" line ONLY when the content changes (symbols list
        # added/removed). The strategy outer loop only ticks once per
        # `time_increment` (3600s in production), so a heartbeat here would
        # ALSO only fire once per hour — useless for "is the broker alive?"
        # observability. The 5-min heartbeat is emitted by the snapshot
        # worker (which runs every 3s) instead — see _live_state_snapshot_worker.
        _running_line = "Running" + (f" ({len(symbols)} tickers: {', '.join(symbols)})" if symbols else "")
        if _loop_log_last_running_key != _running_line:
            _log(_running_line, "white")
            _loop_log_last_running_key = _running_line
        prices = {}
        # 2026-05-07 scheduler refactor (commit 4): in LIVE mode, compute
        # the current tick's mode + next_wake from backend.scheduler.
        # mode flows through to strategy.run_once via run_run_once_strategies;
        # next_wake replaces the legacy drift-free boundary calc at end of
        # the loop. _tick_mode is None in BACKTEST (legacy behavior).
        _tick_mode = None
        _next_wake_utc = None
        # Track whether scheduler call succeeded — used to gate Discord
        # MONITOR notifications. On scheduler error we fall back to MONITOR
        # for safety, but we DON'T want to spam Discord with every fallback
        # tick (a misconfigured scheduler would page operators every 60s).
        _scheduler_call_ok = False
        if mode == MODE_LIVE and _scheduler_get_next_wake is not None:
            try:
                _now_utc_for_sched = current_time if hasattr(current_time, "astimezone") else datetime.datetime.now(datetime.timezone.utc)
                if _now_utc_for_sched.tzinfo is None:
                    _now_utc_for_sched = _now_utc_for_sched.replace(tzinfo=datetime.timezone.utc)
                _tick_marker = None
                try:
                    _gn_cache = (_strategy_cache.get("graph_nexus_analysis", {}) or {})
                    _tick_marker = _gn_cache.get("_nexus_full_cycle_completed_date")
                except Exception:
                    pass
                # Crypto instances get a 24/7 scheduler config (no NYSE hours,
                # band-paced monitor cadence); equities pass config=None → the
                # existing equity DEFAULT_CONFIG. Fail-closed to None on any error.
                _sched_cfg = None
                try:
                    _ck, _ccfg = _instance_kind_and_crypto_config()
                    if _ck == "crypto":
                        from strategies.crypto import core as _crypto_core
                        _sched_cfg = _crypto_core.crypto_scheduler_config(
                            (_ccfg or {}).get("band", "medium")
                        )
                except Exception:
                    _sched_cfg = None
                _next_wake_utc, _tick_mode = _scheduler_get_next_wake(
                    _now_utc_for_sched, _tick_marker, config=_sched_cfg,
                )
                _scheduler_call_ok = True
                _strategy_tick_n += 1
                try:
                    from zoneinfo import ZoneInfo as _ZI_pt
                    _now_pt_str = _now_utc_for_sched.astimezone(_ZI_pt("America/Los_Angeles")).strftime("%H:%M PT")
                    _next_wake_pt_str = _next_wake_utc.astimezone(_ZI_pt("America/Los_Angeles")).strftime("%H:%M PT")
                except Exception:
                    _now_pt_str = _now_utc_for_sched.strftime("%H:%M UTC")
                    _next_wake_pt_str = _next_wake_utc.strftime("%H:%M UTC")
                _delta_to_wake = max(0.0, (_next_wake_utc - _now_utc_for_sched).total_seconds())
                _log(
                    f"strategy-tick #{_strategy_tick_n} STARTED | "
                    f"mode={_tick_mode} | now={_now_pt_str} | "
                    f"marker={_tick_marker or 'none'} | "
                    f"next_wake={_next_wake_pt_str} (in {_delta_to_wake:.0f}s)",
                    "cyan",
                )
                # Phase-stamp so the live-state UI / debugger can show
                # exactly where a wedged tick stalled.
                try:
                    _set_strategy_tick_phase(
                        "wake",
                        tick_index=_strategy_tick_n,
                        mode=_tick_mode,
                        next_wake_ts=_next_wake_utc.astimezone(datetime.timezone.utc).isoformat(),
                        next_wake_mode=_tick_mode,
                        started=True,
                    )
                except Exception:
                    pass
            except Exception as _sched_exc:
                _log(f"scheduler-error (60s fallback): {type(_sched_exc).__name__}: {_sched_exc}", "yellow")
                _tick_mode = "MONITOR"  # safe default — won't write marker, won't run heavy pipeline
                _next_wake_utc = (datetime.datetime.now(datetime.timezone.utc)
                                  + datetime.timedelta(seconds=60))
        if mode == MODE_BACKTEST:
            if current_time > end_date:
                _log("Backtest end date reached; exiting.", "yellow")
                # Stop the heartbeat thread before writing final status to
                # prevent it from overwriting status=finished with stale data.
                try:
                    _heartbeat_stop.set()
                except NameError:
                    pass
                if portfolio_emulator is not None:
                    positions = portfolio_emulator.get_positions() if portfolio_emulator else {}
                    trades = portfolio_emulator.get_trade_history()
                    traded_symbols = set(t.get("ticker") for t in trades if t.get("ticker"))
                    all_traded = sorted(set(symbols or []) | set(positions.keys()) | traded_symbols)
                    # Get final prices for accurate portfolio valuation (watchlist from bars; other symbols fetched)
                    final_prices = _get_prices_at_time(data, symbols, end_date) or {}
                    for sym in all_traded:
                        if sym not in (symbols or []) or final_prices.get(sym) is None:
                            p = _fetch_price_for_symbol(sym, end_date, key=key, secret=secret, feed=data_feed)
                            if p is not None and p > 0:
                                final_prices[sym] = p
                    if not final_prices:
                        snapshots = portfolio_emulator.get_portfolio_history()
                        if snapshots:
                            final_prices = snapshots[-1].get("prices", {}) or {}
                    portfolio_emulator.print_portfolio(final_prices, logger=_log)
                    snapshots = portfolio_emulator.get_portfolio_history()
                    start_prices = _get_prices_at_time(data, symbols, start_dt) if start_dt else {}
                    if not isinstance(start_prices, dict):
                        start_prices = {}
                    for sym in all_traded:
                        if sym not in (symbols or []) or start_prices.get(sym) is None:
                            p = _fetch_price_for_symbol(sym, start_dt, key=key, secret=secret, feed=data_feed) if start_dt else None
                            if p is not None and p > 0:
                                start_prices[sym] = p
                    # P&L per stock and P&L % per stock for all traded symbols
                    # (watchlist + earnings etc.). Open positions are marked at
                    # the LAST snapshot's own prices (the marks the sim ran on)
                    # so per-stock P&L and end_price reconcile with the headline
                    # pnl; the resolver's final_prices fill only symbols the
                    # last snapshot lacks (incident 586767 — a duplicate
                    # end-date bar made the two bases disagree by ~$2,756/pos).
                    from backtest_summary import (
                        resolve_end_prices,
                        compute_per_stock_pnl,
                        compute_stock_price_change,
                    )
                    end_prices = resolve_end_prices(final_prices, snapshots)
                    pnl_per_stock, pnl_percent_per_stock = compute_per_stock_pnl(
                        trades, positions, end_prices, all_traded, log=_log,
                    )
                    stock_price_change = compute_stock_price_change(all_traded, start_prices, end_prices)
                    _log("---------- Backtest summary: P&L per stock ----------", "cyan")
                    for sym in all_traded:
                        pnl = pnl_per_stock.get(sym)
                        pnl_pct_s = pnl_percent_per_stock.get(sym)
                        pnl_str = f"${pnl:,.2f}" if pnl is not None else "N/A"
                        pct_str = f" ({pnl_pct_s:+.2f}%)" if pnl_pct_s is not None else ""
                        _log(f"  {sym}: P&L = {pnl_str}{pct_str}", "white")
                    _log("---------- Stock movement (start -> end) ----------", "cyan")
                    for sym in all_traded:
                        sc = stock_price_change.get(sym) or {}
                        sp = sc.get("start_price")
                        ep = sc.get("end_price")
                        pct = sc.get("change_percent")
                        if sp is not None and ep is not None:
                            pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
                            _log(f"  {sym}: ${sp:.2f} -> ${ep:.2f}  ({pct_str})", "white")
                        else:
                            _log(f"  {sym}: start={sp}, end={ep}", "white")
                    _log("---------------------------------------------------", "cyan")
                    
                    # Save to database (reuse long-lived conn if still open)
                    try:
                        conn = _backtest_db_conn if _backtest_db_conn is not None else get_conn_retry(max_attempts=5, delay=2)
                        if conn is None:
                            conn = get_conn()
                        try:
                            # Ensure BacktestResults table exists
                            tables = list(r.db(DB_NAME).table_list().run(conn))
                            if 'BacktestResults' not in tables:
                                r.db(DB_NAME).table_create('BacktestResults').run(conn)
                                _log("Created BacktestResults table", "green")
                            
                            # Get instance_id and strategy_row_id (instance_id can be int or string)
                            instance_id_for_db = int(instance_id) if (instance_id and str(instance_id).isdigit()) else instance_id
                            strategy_row_id = _strategy_row_id
                            
                            # Calculate elapsed time for backtest
                            time_elapsed_seconds = None
                            if backtest_start_time is not None:
                                import time
                                time_elapsed_seconds = time.time() - backtest_start_time
                            
                            # Get start_date and end_date (start_dt and end_dt are datetime objects from backtest setup)
                            try:
                                backtest_start_date = _backtest_start_dt_input.isoformat() if _backtest_start_dt_input else (start_dt.isoformat() if start_dt else None)
                                backtest_end_date = _backtest_end_dt_input.isoformat() if _backtest_end_dt_input else (end_dt.isoformat() if end_dt else None)
                            except NameError:
                                backtest_start_date = None
                                backtest_end_date = None
                            
                            # Build price series for DB and CSV (same as below for backtest_prices.csv)
                            start_date_only = start_dt.date() if start_dt else None
                            end_date_only = end_dt.date() if end_dt else None
                            if start_date_only is None and snapshots:
                                try:
                                    first_ts = snapshots[0].get("timestamp")
                                    if hasattr(first_ts, "date"):
                                        start_date_only = first_ts.date()
                                    elif first_ts:
                                        start_date_only = _bar_time_to_datetime(first_ts).date() if _bar_time_to_datetime(first_ts) else None
                                except Exception:
                                    pass
                            if end_date_only is None and snapshots:
                                try:
                                    last_ts = snapshots[-1].get("timestamp")
                                    if hasattr(last_ts, "date"):
                                        end_date_only = last_ts.date()
                                    elif last_ts:
                                        end_date_only = _bar_time_to_datetime(last_ts).date() if _bar_time_to_datetime(last_ts) else None
                                except Exception:
                                    pass
                            # Build the DB/UI price series. Snapshot-derived
                            # closes fill only (date, symbol) the bars didn't
                            # cover, so no second same-date close is seeded for
                            # a symbol that already has an end-date bar (the
                            # duplicate-bar half of incident 586767).
                            from backtest_summary import (
                                build_backtest_price_series,
                                compute_backtest_summary,
                            )
                            price_symbols = all_traded if all_traded else (symbols or [])
                            backtest_prices_list = build_backtest_price_series(
                                data, snapshots, price_symbols,
                                start_date_only, end_date_only,
                                bar_time_to_datetime=_bar_time_to_datetime,
                            )

                            # Final P&L (for DB). Derive from the equity
                            # curve's own end mark (last snapshot value) so
                            # pnl == snapshots[-1]["value"] - initial_cash by
                            # construction — never from a separately-resolved
                            # end-date bar that can disagree (the +$437 vs
                            # -$2,318 half of incident 586767).
                            _bt_summary = compute_backtest_summary(
                                portfolio_emulator, snapshots, initial_cash,
                            )
                            final_value = _bt_summary["final_value"]
                            final_pnl = _bt_summary["pnl"]
                            final_pnl_percent = _bt_summary["pnl_percent"]
                            
                            # Create backtest result document (full update)
                            from datetime import datetime as _dt
                            backtest_id_raw = backtest_row_id
                            backtest_id_int = int(backtest_id_raw) if backtest_id_raw and str(backtest_id_raw).isdigit() else None
                            backtest_result = {
                                'backtest_id': backtest_id_int,
                                'instance_id': instance_id_for_db,
                                'timestamp': _dt.now().isoformat(),
                                'strategy_id': strategy_row_id,
                                'strategy_schema': _backtest_strategy_schema,
                                'status': 'finished',
                                'progress': 100.0,
                                'pnl': final_pnl,
                                'pnl_percent': round(final_pnl_percent, 4) if final_pnl_percent is not None else None,
                                # Crypto fee accounting (None for equity runs).
                                'fees': _bt_summary.get("fees"),
                                'pnl_per_stock': pnl_per_stock,
                                'pnl_percent_per_stock': pnl_percent_per_stock,
                                'stock_price_change': stock_price_change,
                                'time_elapsed_seconds': time_elapsed_seconds,
                                'start_date': backtest_start_date,
                                'end_date': backtest_end_date,
                                'tickers': all_traded if all_traded else (symbols if symbols else []),
                                'portfolio_value_history': _convert_datetimes_to_iso(snapshots),  # List of dicts with ISO timestamps
                                'backtest_trades': _convert_datetimes_to_iso(trades),  # List of dicts with ISO timestamps
                                'backtest_prices': backtest_prices_list,  # List of {timestamp, symbol, close} for graph-backtest
                                'backtest_decisions': list(_backtest_decisions) if _backtest_decisions else [],
                                'logs': list(_backtest_log_buffer)[-500:] if _backtest_log_buffer else [],
                                'cadence_mode': "dual_cadence_backtest_sim" if _dc_bt_sim else "full_every_tick",
                                'dual_cadence_backtest_simulation': bool(_dc_bt_sim),
                            }
                            
                            # Update existing row if we have id, else insert.
                            # Fail the write closed if any secret material slipped
                            # into the payload (schema, logs, decisions, ...).
                            assert_secret_free(backtest_result)
                            if _backtest_result_id is not None:
                                r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(backtest_result).run(conn)
                                _log(f"Updated backtest results in database (id={_backtest_result_id}, status=finished, P&L={final_pnl})", "green")
                            else:
                                r.db(DB_NAME).table('BacktestResults').insert(backtest_result).run(conn)
                                _log(f"Saved backtest results to database (instance_id={instance_id_for_db}, strategy_id={strategy_row_id})", "green")
                            # Phase 1 snapshot: persist final _strategy_cache for live-boot reuse.
                            # Helpers live in broker_snapshot_helpers.py so they're testable without
                            # broker.py's module-level argparse + DB bootstrap (extraction pattern
                            # mirrors broker_session.py / strategy_tick_state.py).
                            try:
                                from broker_snapshot_helpers import (
                                    _invoke_persist_backtest_snapshot as _scp_invoke,
                                    _collect_prompt_versions as _scp_collect_prompts,
                                    _collect_llm_stages as _scp_collect_stages,
                                    _collect_history_scope_inputs as _scp_collect_history,
                                )
                                _nexus_spec_for_snapshot = next(
                                    (s for s in (_run_once_specs or [])
                                     if str((s or {}).get("strategy") or "").strip() == "graph_nexus_analysis"),
                                    None,
                                )
                                if _nexus_spec_for_snapshot is not None:
                                    _bt_cfg = _nexus_spec_for_snapshot.get("config") or {}
                                    # Phase 1 bug-sweep (2026-05-21): the snapshot row's instance_id field
                                    # is the LIVE-mode namespace key — live boot reads
                                    # `r.row["instance_id"] == "main"`. Backtests use a numeric
                                    # `instance_id_for_db` distinct from that namespace, so writing the
                                    # snapshot with the backtest id meant live boot never found it.
                                    # Prefer the explicit `base_instance_id` from the spec config
                                    # (populated by run_run_once_strategies for graph_nexus_analysis).
                                    _bt_base_id = str(
                                        _bt_cfg.get("base_instance_id")
                                        or instance_id_for_db
                                        or instance_id
                                        or ""
                                    )
                                    if not _bt_cfg.get("base_instance_id"):
                                        try:
                                            _log(
                                                f"[snapshot] WARN: base_instance_id missing from spec config; "
                                                f"falling back to instance_id_for_db={instance_id_for_db!r}",
                                                "yellow",
                                            )
                                        except NameError:
                                            pass
                                    _scp_invoke(
                                        conn=conn,
                                        r=r,
                                        base_instance_id=_bt_base_id,
                                        strategy_name="graph_nexus_analysis",
                                        strategy_cache=_strategy_cache.get("graph_nexus_analysis", {}) or {},
                                        config_dict={
                                            "strategy_name": "graph_nexus_analysis",
                                            "prompt_versions": _scp_collect_prompts(_bt_cfg),
                                            "llm_stages": _scp_collect_stages(_bt_cfg),
                                            "history_scope_id_inputs": _scp_collect_history(_bt_cfg),
                                            "lookback_learning_days": int(_bt_cfg.get("lookback_learning_days", 120) or 120),
                                        },
                                        start_date=str(backtest_start_date) if backtest_start_date else "",
                                        end_date=str(backtest_end_date) if backtest_end_date else "",
                                    )
                            except Exception as _snap_e:
                                try:
                                    _log(f"[snapshot] wrap-up failed (suppressed): {_snap_e}", "yellow")
                                except NameError:
                                    pass
                            # Edit #backtests Discord message to Finished (same message that was Queued/Running)
                            try:
                                from interactive_utils import action_enqueue_discord_edit
                                msg_key = str(backtest_row_id) if backtest_row_id is not None else str(backtest_id_int or _backtest_result_id)
                                bid = backtest_id_int if backtest_id_int is not None else _backtest_result_id
                                tickers_str = ", ".join((all_traded or symbols or [])[:8])
                                _tickers_list = all_traded if all_traded else (symbols or [])
                                if _tickers_list and len(_tickers_list) > 8:
                                    tickers_str += " (+%d)" % (len(_tickers_list) - 8)
                                total_trades = len(trades) if trades else 0
                                pnl_str = "$%.2f" % final_pnl if final_pnl is not None else "—"
                                pct_str = "%.2f%%" % final_pnl_percent if final_pnl_percent is not None else "—"
                                # Format strategy for Discord (name + sub-strategies; field value max 1024)
                                strategy_name = "—"
                                strategy_detail = "—"
                                if _backtest_strategy_schema:
                                    strategy_name = (_backtest_strategy_schema.get("name") or "—").strip() or "—"
                                    subs = _backtest_strategy_schema.get("strategies") or []
                                    if subs:
                                        parts = []
                                        for s in subs[:12]:
                                            st = (s.get("strategy") or "?").strip() or "?"
                                            w = s.get("weight")
                                            wstr = ("%.2f" % w) if w is not None else "?"
                                            parts.append("%s (w=%s)" % (st, wstr))
                                        strategy_detail = "\n".join(parts) if len(parts) <= 8 else "\n".join(parts[:8]) + "\n... +%d more" % (len(parts) - 8)
                                _res = r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).run(conn)
                                _d = _res.get('difficulty') if _res else None
                                diff_str = ("%.1f" % float(_d) + (" (HIGH USAGE)" if backtest_high_usage else "")) if _d is not None else _backtest_difficulty_discord_str()
                                fields = [
                                    {"name": "ID", "value": str(bid), "inline": True},
                                    {"name": "Instance", "value": str(instance_id_for_db), "inline": True},
                                    {"name": "Status", "value": "finished", "inline": True},
                                    {"name": "Difficulty", "value": diff_str, "inline": True},
                                    {"name": "P&L", "value": pnl_str, "inline": True},
                                    {"name": "P&L %", "value": pct_str, "inline": True},
                                    {"name": "Trades", "value": str(total_trades), "inline": True},
                                    {"name": "Strategy", "value": strategy_name[:256], "inline": False},
                                    {"name": "Sub-strategies", "value": strategy_detail[:1020] if strategy_detail else "—", "inline": False},
                                    {"name": "Period", "value": "%s → %s" % (backtest_start_date or "—", backtest_end_date or "—"), "inline": False},
                                    {"name": "Tickers", "value": tickers_str or "—", "inline": False},
                                ]
                                action_enqueue_discord_edit(conn, "backtests", msg_key, content=None, embed={
                                    "title": "Backtest Finished",
                                    "description": "A backtest run completed." + (" **Strategy:** %s" % strategy_name if strategy_name != "—" else ""),
                                    "color": 0x2ECC71 if (final_pnl or 0) >= 0 else 0xE74C3C,
                                    "fields": fields,
                                })
                            except Exception as _discord_err:
                                try:
                                    _log("Discord backtest notify failed (non-fatal): %s" % _discord_err, "yellow")
                                except NameError:
                                    pass
                        finally:
                            if conn is not None:
                                try:
                                    conn.close()
                                except Exception:
                                    pass
                            _backtest_db_conn = None
                    except Exception as e:
                        _log(f"Backtest lost connection to RethinkDB or failed to save results (exiting): {e}", "red")
                        if _backtest_db_conn is not None:
                            try:
                                _backtest_db_conn.close()
                            except Exception:
                                pass
                            _backtest_db_conn = None
                        sys.exit(1)
                
                # Write history to CSV for inspection (keep this for backward compatibility)
                try:
                    import csv
                    with open("backtest_trades.csv", "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["timestamp", "action", "ticker", "shares", "price", "total", "cash_after"])
                        for t in trades:
                            w.writerow([t.get("timestamp"), t.get("action"), t.get("ticker"), t.get("shares"), t.get("price"), t.get("total"), t.get("cash_after")])
                    with open("backtest_portfolio_value.csv", "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["timestamp", "value", "cash"])
                        for s in snapshots:
                            w.writerow([s.get("timestamp"), s.get("value"), s.get("cash")])
                    # Stock price series (timestamp, symbol, close) for plot_util; prefer bars within backtest [start_dt, end_dt]
                    start_date_only = start_dt.date() if start_dt else None
                    end_date_only = end_dt.date() if end_dt else None
                    if start_date_only is None and snapshots:
                        try:
                            first_ts = snapshots[0].get("timestamp")
                            if hasattr(first_ts, "date"):
                                start_date_only = first_ts.date()
                            elif first_ts:
                                start_date_only = _bar_time_to_datetime(first_ts).date() if _bar_time_to_datetime(first_ts) else None
                        except Exception:
                            pass
                    if end_date_only is None and snapshots:
                        try:
                            last_ts = snapshots[-1].get("timestamp")
                            if hasattr(last_ts, "date"):
                                end_date_only = last_ts.date()
                            elif last_ts:
                                end_date_only = _bar_time_to_datetime(last_ts).date() if _bar_time_to_datetime(last_ts) else None
                        except Exception:
                            pass
                    # Collect bars in range (or all bars if none in range, so file is never empty).
                    # Same de-duplicated series as the DB path — snapshot-derived
                    # closes fill only (date, symbol) the bars didn't cover, so
                    # no phantom second same-date bar is written (incident 586767).
                    from backtest_summary import build_backtest_price_series
                    price_symbols = all_traded if all_traded else (symbols or [])
                    _price_series = build_backtest_price_series(
                        data, snapshots, price_symbols,
                        start_date_only, end_date_only,
                        bar_time_to_datetime=_bar_time_to_datetime,
                    )
                    with open("backtest_prices.csv", "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["timestamp", "symbol", "close"])
                        for _row in _price_series:
                            w.writerow([_row["timestamp"], _row["symbol"], _row["close"]])
                    _log("Backtest prices CSV: %s to %s (%d bars)." % (start_date_only, end_date_only, len(_price_series)), "cyan")
                    _log("Wrote backtest_trades.csv, backtest_portfolio_value.csv, and backtest_prices.csv.", "green")
                except Exception as e:
                    _log("Could not write backtest history CSV: " + str(e), "yellow")
                if _backtest_db_conn is not None:
                    try:
                        _backtest_db_conn.close()
                    except Exception:
                        pass
                    _backtest_db_conn = None
                try:
                    from intellistock_logger import intellistock_logger
                    intellistock_logger.clear_backtest_log_buffer()
                    intellistock_logger.close_backtest_log_file()
                except Exception:
                    pass
                sys.exit(0)
            else:
                # current_time <= end_date: fetch prices and run this tick
                prices = _get_prices_at_time(data, symbols, current_time, use_cursor=True)
                price_history = get_price_history_up_to_current(data, symbols_for_data, current_time)
        else:
            # get_live_prices() hits Robinhood's batch-quotes endpoint. Only call it
            # when Robinhood is the trading broker — a non-RH (e.g. Alpaca) instance
            # must NOT call Robinhood from the server IP. Held-position prices are
            # backfilled by _ensure_prices_include_positions (Alpaca/yfinance) and the
            # per-symbol fetchers + pre-submit quote refresh cover the rest.
            prices = get_live_prices(symbols) if robinhood_data_fallback_allowed(live_broker_type) else {}
            price_history = None

        # Backtest: every bar, execute any pending future trades for TODAY before running strategies.
        # NOTE: loops over _strategy_cache (all strategies that ever scheduled trades), NOT _run_once_specs,
        # so execution works even if earnings has execution_scope="per_symbol" in the DB.
        extended_prices = None
        if mode == MODE_BACKTEST and portfolio_emulator is not None:
            today_str = (current_time.strftime("%Y-%m-%d") if hasattr(current_time, "strftime") else str(current_time)[:10])
            try:
                from future_trades_util import get_all_pending_trades_for_date, pop_all_pending_trades_for_date
                # Count queue state for logging
                queue_total = 0
                for_today_count = 0
                for _sname, _sc in (_strategy_cache or {}).items():
                    if not _sc:
                        continue
                    q = _sc.get("_future_trades_queue") or []
                    queue_total += len(q)
                    for_today_count += len(get_all_pending_trades_for_date(_sc, current_time))
                _log("[Pending] %s — queue_total=%d, for_today=%d" % (today_str, queue_total, for_today_count), "cyan")

                if for_today_count > 0:
                    _key = key or os.environ.get("KEY", "")
                    _secret = secret or os.environ.get("SECRET", "")
                    if not _key or not _secret:
                        _log("[Pending] Alpaca keys not set — cannot fetch prices for non-watchlist symbols. Set KEY/SECRET.", "yellow")
                    try:
                        _set_strategy_tick_phase("warm_boot_eppi")
                    except Exception:
                        pass
                    # 2026-05-07 scheduler refactor: bound EPPI to 30s.
                    # This was the second unbounded site identified in the
                    # adversarial review (the wedge at line 8064 was the third).
                    prices = _bounded_eppi_call(
                        portfolio_emulator, prices, current_time,
                        data=data, symbols=symbols, key=key, secret=secret,
                        label="warm-boot pending-trade EPPI", timeout=30.0,
                    ) if mode == MODE_LIVE else _ensure_prices_include_positions(
                        portfolio_emulator, prices, current_time,
                        data=data, symbols=symbols, key=key, secret=secret,
                    )
                    pv = portfolio_emulator.get_portfolio_value(prices)
                    extended_prices = dict(prices) if prices else {}
                    executed_start = 0
                    skipped_start_no_price = []
                    skipped_start_no_cash = []
                    # Task 7 review-fix (IMPORTANT 1): pending-trade buys run
                    # BEFORE the main emission loop's max_positions gate arms —
                    # gate NEW-name buys here too with the same helper. Held
                    # names snapshotted once at block entry; pending sells sort
                    # first per strategy, so a funding full-exit is credited
                    # before the buy it pays for. Adds to held names exempt.
                    _pnd_cap, _pnd_cap_reason = resolve_max_positions_cap(_cached_strategies)
                    _pnd_held = set()
                    _pnd_full_exits = set()
                    _pnd_new_emitted = set()
                    if _pnd_cap is not None:
                        try:
                            _pnd_pos = portfolio_emulator.get_positions() if hasattr(portfolio_emulator, "get_positions") else (getattr(portfolio_emulator, "_positions", {}) or {})
                            _pnd_held = {str(_s).strip().upper() for _s, _q in (_pnd_pos or {}).items() if float(_q or 0.0) > 0.0}
                        except Exception:
                            _pnd_cap = None  # fail-open: gate inert this block
                    for _sname, _sc in list((_strategy_cache or {}).items()):
                        if not _sc:
                            continue
                        pending = pop_all_pending_trades_for_date(_sc, current_time)
                        if not pending:
                            continue
                        # Fetch prices for any symbol not already in bar data
                        for t in pending:
                            sym = (t.get("symbol") or "").upper()
                            if sym and (sym not in extended_prices or not extended_prices.get(sym)):
                                _fetch = _fetch_price_with_fallback(sym, current_time, key=_key, secret=_secret, log_fn=_log, feed=data_feed)
                                if _fetch and _fetch > 0:
                                    extended_prices[sym] = _fetch
                                    if sym not in _fetch_price_fail_logged:
                                        _log("[Pending] Fetched price %s=%.2f for scheduled trade" % (sym, _fetch), "cyan")
                                else:
                                    if sym not in _fetch_price_fail_logged:
                                        _fetch_price_fail_logged.add(sym)
                                        _log("[Pending] Could not fetch price for %s (Alpaca+yfinance both failed)" % sym, "yellow")
                        # Find spec for capital allocation
                        _spec = next((s for s in (_cached_strategies or []) if (s.get("strategy") or "").strip().lower() == _sname.lower()), None)
                        cap_pct = _get_reserved_capital_pct(_spec) if _spec else 1.0
                        reserved_budget = (pv * cap_pct) if (pv and cap_pct > 0) else 0.0
                        remaining_budget = float(reserved_budget)
                        # Track shares bought by this strategy for sell-fraction calculation
                        _epos = _sc.get("_earnings_positions") if _sc else None
                        # Process sells first so freed cash is available for buys
                        pending.sort(key=lambda _t: (0 if _t.get("signal", 0) == -1 else 1))
                        for t in pending:
                            sym = (t.get("symbol") or "").upper()
                            sig = t.get("signal", 0)
                            if not sym or sig not in (1, -1):
                                continue
                            price_sym = extended_prices.get(sym)
                            if not price_sym or price_sym <= 0:
                                skipped_start_no_price.append(sym)
                                _log("[Pending] SKIP %s %s — no price (target_date=%s, reason=%s)" % (
                                    "buy" if sig == 1 else "sell", sym,
                                    t.get("target_date", "?"), t.get("reason", "")[:80]), "yellow")
                                continue
                            if sig == 1:
                                alloc_pct = t.get("allocation_pct")
                                if alloc_pct is not None and float(alloc_pct) > 0:
                                    cap_for_this = reserved_budget * (float(alloc_pct) / 100.0)
                                    cash_use = min(cap_for_this, remaining_budget, portfolio_emulator.get_cash())
                                else:
                                    cash_use = min(remaining_budget, portfolio_emulator.get_cash())
                                if cash_use <= 0:
                                    skipped_start_no_cash.append(sym)
                                    _log("[Pending] SKIP buy %s — no cash remaining (budget=%.2f, cash=%.2f)" % (
                                        sym, remaining_budget, portfolio_emulator.get_cash()), "yellow")
                                    continue
                                # Task 7 review-fix (IMPORTANT 1): hard cap gate
                                # on NEW-name pending buys (adds to held exempt).
                                if _pnd_cap is not None and not max_positions_gate(_pnd_held, _pnd_cap, _pnd_full_exits, _pnd_new_emitted, sym):
                                    _pnd_proj = max_positions_projected_count(_pnd_held, _pnd_full_exits, _pnd_new_emitted)
                                    _log("MAX_POSITIONS_GATE: blocked %s (held=%d, cap=%d)" % (sym, _pnd_proj, _pnd_cap), "yellow")
                                    continue
                                remaining_budget -= cash_use
                                shares_before = portfolio_emulator._positions.get(sym, 0.0)
                                portfolio_emulator.execute_signal(sym, sig, price_sym, timestamp=current_time,
                                    cash_per_trade=cash_use, sell_fraction=1.0)
                                shares_after = portfolio_emulator._positions.get(sym, 0.0)
                                if _pnd_cap is not None and sym not in _pnd_held:
                                    _pnd_new_emitted.add(sym)
                                if _epos is not None:
                                    _epos[sym] = _epos.get(sym, 0.0) + max(0.0, shares_after - shares_before)
                            else:
                                # Sell: only sell shares this strategy bought (tracked in _earnings_positions)
                                sell_frac = 1.0
                                if _epos is not None and sym in _epos and _epos[sym] > 0:
                                    total_shares = portfolio_emulator._positions.get(sym, 0.0)
                                    if total_shares > 0:
                                        sell_frac = min(1.0, _epos[sym] / total_shares)
                                _pnd_sell_ok = portfolio_emulator.execute_signal(sym, sig, price_sym, timestamp=current_time,
                                    cash_per_trade=1000.0, sell_fraction=sell_frac)
                                # Task 7 review-fix (IMPORTANT 2): credit the
                                # freed slot only when the sell actually
                                # executed/submitted (execute_signal -> True).
                                if _pnd_cap is not None and _pnd_sell_ok and sell_frac >= 0.999 and sym in _pnd_held:
                                    _pnd_full_exits.add(sym)
                                if _epos is not None and sym in _epos:
                                    _epos.pop(sym, None)
                                # After sell frees cash, update remaining_budget so subsequent buys can use it
                                remaining_budget = min(reserved_budget, portfolio_emulator.get_cash())
                            executed_start += 1
                            _log("[Pending] EXECUTED %s %s @ %.2f (strategy=%s, alloc=%.0f%%)" % (
                                "buy" if sig == 1 else "sell", sym, price_sym, _sname,
                                t.get("allocation_pct", 0) or 0), "green")
                    _log("[Pending] Start-of-bar done: %d executed, %d skipped(no price: %s), %d skipped(no cash: %s)" % (
                        executed_start,
                        len(skipped_start_no_price), ", ".join(skipped_start_no_price) or "—",
                        len(skipped_start_no_cash), ", ".join(skipped_start_no_cash) or "—"), "cyan")
                    extended_prices_merged = dict(prices) if prices else {}
                    extended_prices_merged.update(extended_prices)
                    extended_prices = extended_prices_merged
            except Exception as _e:
                _log("[Pending] Start-of-bar execution error: %s" % _e, "red")
                import traceback as _tb
                _log(_tb.format_exc(), "red")
            if extended_prices is not None:
                prices = extended_prices

        # Only run strategies and execute when within session.
        # 2026-04-30 v2 Task A: live mode uses NYSE-aware gate
        # (holiday + early-close aware via exchange_calendars). Backtest
        # keeps the legacy PT-window gate to preserve determinism.
        # 2026-05-05: when any strategy has dual-cadence enabled, force
        # extended-hours mode (1AM-5PM PT) regardless of RH_RTH_ONLY env.
        # The dual-cadence gate inside the strategy enforces its own
        # tighter 5AM-5PM PT window; the broker just needs to tick during
        # that window. Without this override, RH_RTH_ONLY=true would clip
        # the broker to 6:30AM-1PM PT and dual-cadence's 1PM-5PM PT
        # monitor cycles would never run.
        _dc_enabled_any = False
        try:
            for _spec_dc in (_run_once_specs or []):
                _cfg_dc = (_spec_dc or {}).get("config") or {}
                if bool(_cfg_dc.get("nexus_dual_cadence_enabled", False)):
                    _dc_enabled_any = True
                    break
        except Exception:
            _dc_enabled_any = False
        if _is_crypto_instance_runtime():
            # Crypto trades 24/7/365 — never "outside session". Skip all NYSE
            # market-hours gating so strategies + execution run round the clock.
            within_session = True
        elif mode == MODE_LIVE:
            if _dc_enabled_any:
                # Force extended-hours gate (NYSE pre-market + RTH + after-hours)
                # regardless of RH_RTH_ONLY. The dual-cadence gate in the
                # strategy enforces its own tighter 5AM-5PM PT window.
                try:
                    from live_calendar import is_nyse_open_extended as _live_isoe
                    _ct_dc = (
                        current_time
                        if current_time.tzinfo
                        else current_time.replace(tzinfo=timezone.utc)
                    )
                    within_session = bool(_live_isoe(_ct_dc))
                except Exception:
                    within_session = _is_within_live_session(current_time)
            else:
                within_session = _is_within_live_session(current_time)
        else:
            within_session = _is_within_trading_session_pt(current_time)
        if not within_session:
            # Log only on transition (inside→outside). We intentionally do NOT
            # heartbeat this line while we remain outside — the "Running" line
            # above already heartbeats every LOOP_LOG_HEARTBEAT_SEC, so emitting
            # "Outside session" on the same cadence would double every heartbeat.
            try:
                if _loop_log_last_outside is not True:
                    _rth_only_active = (
                        os.environ.get("RH_RTH_ONLY", "")
                        or os.environ.get("LIVE_RTH_ONLY", "")
                        or ""
                    ).strip().lower() in ("1", "true", "yes")
                    if _rth_only_active:
                        _log(
                            "Outside session (RTH-only mode: Mon–Fri 6:30AM–1PM PT / "
                            "9:30AM–4PM ET); skipping strategies and execution.",
                            "cyan",
                        )
                    else:
                        _log(
                            "Outside session (Mon–Fri 1AM–5PM PT / 4AM–8PM ET, "
                            "extended hours); skipping strategies and execution.",
                            "cyan",
                        )
                _loop_log_last_outside = True
            except NameError:
                pass
        else:
            # Log the inside-session transition exactly once so operators can
            # see the loop picked up market-open.
            try:
                if _loop_log_last_outside is True:
                    _log("Entering trading session — strategies resuming.", "green")
                _loop_log_last_outside = False
            except NameError:
                pass
            ###################################
            ### Main Loop: run run_once strategies once, then per-symbol pre-decision, then decide; run post-decision for size/pricing
            ###################################
            per_symbol_strategies = _per_symbol_specs if _per_symbol_specs is not None else []
            # V6 Fix A + live cp=$0 fix: Ensure held-position prices are in the
            # `prices` dict BEFORE strategy runs. The broker already fetched
            # `prices` earlier in the loop, but that fetch is keyed on
            # `symbols` (the instance's configured universe), which for
            # discovery-mode live instances does NOT include held positions.
            # Without this patch, live strategies saw cp=$0 for every held
            # ticker and the risk pipeline skipped all P&L checks.
            if portfolio_emulator is not None:
                try:
                    prices = _ensure_prices_include_positions(
                        portfolio_emulator, prices, current_time,
                        data=data if mode == MODE_BACKTEST else None,
                        symbols=symbols, key=key, secret=secret,
                    )
                except Exception as _e_pos_px:
                    _log(f"Held-position price patch failed (non-fatal): {_e_pos_px}", "yellow")
            run_once_results = []
            if _run_once_specs:
                # V32 Phase 5: pass DATA creds (not trading creds) to strategies.
                # Strategies fetch news/bars with these; they can be sourced
                # from a separate live-data brokerage when the trading account
                # is paper (no data subscription).
                # R15: drop mode==MODE_LIVE gate on data creds — data brokerage
                # holds the API subscription regardless of mode.
                _strat_data_key = (data_key or key or os.environ.get("KEY", "")) or ""
                _strat_data_secret = (data_secret or secret or os.environ.get("SECRET", "")) or ""
                # F1: persist per-strategy strategy_cache across container restarts.
                if mode == MODE_LIVE and not globals().get("_strategy_cache_loaded_from_db"):
                    try:
                        from strategy_cache_persistence import (
                            load_strategy_cache_from_db as _scp_load,
                            merge_loaded_cache_into as _scp_merge,
                        )
                        _scp_conn = get_conn_retry(max_attempts=3, delay=2)
                        if _scp_conn is not None:
                            try:
                                for _spec_sc in (_run_once_specs or []):
                                    _nm = str((_spec_sc or {}).get("strategy") or "").strip()
                                    if not _nm:
                                        continue
                                    _loaded = _scp_load(_scp_conn, r, str(instance_id), _nm)
                                    if _loaded:
                                        _tgt = _strategy_cache.setdefault(_nm, {})
                                        _scp_merge(_tgt, _loaded)
                                        _log(
                                            f"strategy_cache restored for {_nm}: {len(_loaded)} key(s) "
                                            f"(bar_index={_tgt.get('_deployment_bar_index')}, "
                                            f"halt_active={(_tgt.get('_portfolio_drawdown_state') or {}).get('halt_active')})",
                                            "cyan",
                                        )
                            finally:
                                try:
                                    _scp_conn.close()
                                except Exception:
                                    pass
                        globals()["_strategy_cache_loaded_from_db"] = True
                    except Exception as _scp_le:
                        try:
                            _log(
                                f"strategy_cache load failed (continuing with empty cache): {type(_scp_le).__name__}: {_scp_le}",
                                "yellow",
                            )
                        except Exception:
                            pass
                        globals()["_strategy_cache_loaded_from_db"] = True
                # 2026-05-05 — pre-cycle RH refresh (LIVE only). The adapter
                # caches refresh_account / refresh_positions for 1 hour. The
                # hourly cycle DEPENDS on fresh data — risk evaluation against
                # stale positions/cash would mis-trigger sells or skip entries.
                # So we retry up to 5x with exponential backoff and HARD-SKIP
                # the cycle if both endpoints can't be refreshed. Every step
                # is logged so the operator can see what happened each hour.
                _precycle_ok = True
                # 2026-05-07 scheduler refactor: skip pre-cycle RH refresh on
                # IDLE ticks. Strategy returns {} immediately on IDLE so
                # there's no point burning a fresh refresh_account /
                # refresh_positions when no decision will be made.
                if mode == MODE_LIVE and live_adapter is not None and _tick_mode != "IDLE":
                    def _retry_refresh(name: str, fn, max_retries: int = 5) -> bool:
                        last_err = None
                        for attempt in range(1, max_retries + 1):
                            _t0 = time.time()
                            try:
                                _log(
                                    f"Pre-cycle {name}: attempt "
                                    f"{attempt}/{max_retries}...",
                                    "cyan",
                                )
                                # Bound each attempt at 30s so a wedged TLS
                                # recv can't blow the budget. Uses the same
                                # _PRICE_FETCH_EXECUTOR pool with bounded
                                # zombie threads (max 4).
                                _fut = _PRICE_FETCH_EXECUTOR.submit(
                                    lambda: fn(force=True)
                                )
                                try:
                                    _fut.result(timeout=30.0)
                                except TypeError:
                                    # AlpacaAdapter doesn't take force= kwarg.
                                    _fut2 = _PRICE_FETCH_EXECUTOR.submit(fn)
                                    _fut2.result(timeout=30.0)
                                _ms = (time.time() - _t0) * 1000
                                _log(
                                    f"Pre-cycle {name}: SUCCESS in {_ms:.0f}ms "
                                    f"(attempt {attempt}/{max_retries})",
                                    "green",
                                )
                                return True
                            except _live_cf.TimeoutError:
                                _ms = (time.time() - _t0) * 1000
                                last_err = TimeoutError(
                                    f"hard-bound 30s exceeded"
                                )
                                _log(
                                    f"Pre-cycle {name}: attempt {attempt} "
                                    f"TIMED OUT after {_ms:.0f}ms (>30s)",
                                    "yellow",
                                )
                            except Exception as _e:
                                _ms = (time.time() - _t0) * 1000
                                last_err = _e
                                _log(
                                    f"Pre-cycle {name}: attempt {attempt} "
                                    f"FAILED in {_ms:.0f}ms: "
                                    f"{type(_e).__name__}: {_e}",
                                    "yellow",
                                )
                            if attempt < max_retries:
                                # 2026-05-05 third pass: rebuild the RH
                                # session between retries so the wedged
                                # urllib3 conn pool gets flushed before
                                # the next attempt. The wedged thread
                                # keeps its own ref to the OLD session.
                                try:
                                    _rebuild = getattr(live_adapter, "_rebuild_session", None)
                                    if callable(_rebuild):
                                        _rebuild()
                                        _log(
                                            f"Pre-cycle {name}: rebuilt "
                                            f"adapter session before retry",
                                            "yellow",
                                        )
                                except Exception:
                                    pass
                                _backoff = min(2 ** (attempt - 1), 16)
                                _log(
                                    f"Pre-cycle {name}: backing off "
                                    f"{_backoff}s before retry "
                                    f"{attempt + 1}/{max_retries}",
                                    "yellow",
                                )
                                time.sleep(_backoff)
                        _log(
                            f"Pre-cycle {name}: EXHAUSTED {max_retries} "
                            f"retries — last err: {type(last_err).__name__}: "
                            f"{last_err}",
                            "red",
                        )
                        return False

                    _log(
                        "Pre-cycle RH refresh starting (hourly run depends "
                        "on this; will retry up to 5x)...",
                        "cyan",
                    )
                    try:
                        _set_strategy_tick_phase("rh_refresh")
                    except Exception:
                        pass
                    _acct_ok = _retry_refresh(
                        "refresh_account", live_adapter.refresh_account, 5
                    )
                    _pos_ok = _retry_refresh(
                        "refresh_positions", live_adapter.refresh_positions, 5
                    )
                    if _acct_ok and _pos_ok:
                        _log(
                            "Pre-cycle RH refresh COMPLETE — proceeding to "
                            "hourly cycle with fresh data",
                            "green",
                        )
                    else:
                        _precycle_ok = False
                        _log(
                            f"Pre-cycle RH refresh FAILED "
                            f"(account_ok={_acct_ok}, positions_ok={_pos_ok}) "
                            f"— SKIPPING this hourly cycle to avoid trading "
                            f"on stale data. Will retry on next loop tick.",
                            "red",
                        )

                if not _precycle_ok:
                    # Hard-skip the hourly cycle.
                    run_once_results = []
                else:
                    try:
                        if mode == MODE_LIVE:
                            # 2026-05-06 — bound run_run_once_strategies in
                            # a watchdog so a wedged strategy (LLM hung,
                            # DB cursor stuck, etc.) can't silence the
                            # broker forever. 30min is generous for legit
                            # full-cycle work (typical 5-15min) but tight
                            # enough to recover within an hour.
                            _RR_WATCHDOG_SEC = float(
                                os.environ.get(
                                    "RUN_ONCE_STRATEGIES_WATCHDOG_SEC",
                                    "1800",
                                )
                            )
                            # Back-to-back guard: if a prior tick's
                            # run_once is STILL running, do not submit
                            # another (would saturate _PRICE_FETCH_EXECUTOR
                            # with up to max_workers=4 zombie strategies).
                            # Skip this tick instead.
                            _rr_prev = globals().get("_rr_prev_fut")
                            if _rr_prev is not None and not _rr_prev.done():
                                # 2026-05-07 scheduler refactor: 3-strike
                                # escalation. If the prior tick's future is
                                # STILL running for 3 ticks in a row, the
                                # main-thread wedge isn't going to resolve —
                                # `fut.cancel()` doesn't kill running threads
                                # in CPython. Only honest recovery is process
                                # exit + supervisor restart.
                                _strategy_consecutive_skips += 1
                                _log(
                                    f"run_run_once_strategies prior-tick STILL "
                                    f"RUNNING (zombie skip #{_strategy_consecutive_skips}/3); "
                                    f"skipping this tick to avoid pool saturation. "
                                    f"Will retry on next.",
                                    "yellow",
                                )
                                if _strategy_consecutive_skips >= 3:
                                    _log(
                                        f"3 consecutive zombie skips — "
                                        f"main thread wedged. Exiting process so "
                                        f"supervisor can restart cleanly.",
                                        "red",
                                    )
                                    try:
                                        from live_alerts import alert_strategy_error
                                        alert_strategy_error(
                                            instance_id=str(instance_id),
                                            tag="run_once:3-strike-os-exit",
                                            message="Strategy main thread wedged; broker calling os._exit(1) for supervisor restart.",
                                        )
                                    except Exception:
                                        pass
                                    os._exit(1)
                                run_once_results = []
                            else:
                                # 2026-05-07 scheduler refactor: pass _tick_mode
                                # through. mode=IDLE → strategy returns {} fast.
                                # mode=MONITOR → strategy routes to monitor cycle.
                                # mode=FULL or None → full pipeline.
                                # R2 Task 4: preflight OpenRouter credit guard
                                # at the START of a live FULL run (the LLM-heavy
                                # pipeline). MONITOR/IDLE ticks are cheap and
                                # skip it. A "halt" raises LLMCriticalFailure
                                # (insufficient_credits) → outer-except runs
                                # live_critical_abort + exit(7) rather than
                                # trading LLM-blind.
                                if mode == MODE_LIVE and _tick_mode in (None, "FULL"):
                                    _credit_guard_or_raise(call_site="live_full_run")
                                try:
                                    _set_strategy_tick_phase("strategy_run")
                                except Exception:
                                    pass
                                # 2026-07-18: LIVE crypto bars for run_once strategies.
                                # Backtest builds price_history; live passed data=None, so
                                # crypto strategies saw an EMPTY universe and exit_blind_held
                                # would risk-off-sell every held coin each tick. Crypto-only
                                # branch — equity live path still passes None (byte-identical).
                                # On a failed fetch with no last-good snapshot we run the tick
                                # with EMPTY specs (a no-op) instead of trading blind.
                                _rr_data = None
                                _rr_specs_eff = _run_once_specs
                                if _is_crypto_instance_runtime() and _tick_mode != "IDLE":
                                    try:
                                        from live_crypto_bars import build_live_crypto_data as _lcb_build
                                        from live_crypto_bars import band_increment_seconds as _lcb_band_inc
                                        from strategies.crypto.meanrev import DEFAULT_MAJORS as _lcb_majors
                                        _lcb_now = datetime.datetime.now(datetime.timezone.utc)
                                        try:
                                            _lcb_inc = max(60, int(float(time_increment or 3600)))
                                        except (TypeError, ValueError):
                                            _lcb_inc = 3600
                                        # Prefer the BAND's bar size (the validated cadence,
                                        # low=60min) over the row's time increment, which UI
                                        # creates historically hardcoded to 900.
                                        try:
                                            _ck_b, _ccfg_b = _instance_kind_and_crypto_config()
                                            _lcb_inc = _lcb_band_inc((_ccfg_b or {}).get("band"), _lcb_inc)
                                        except Exception:
                                            pass
                                        _lcb_lookback = int(os.environ.get("LIVE_CRYPTO_LOOKBACK_BARS", "5040"))
                                        _lcb_tf = _time_increment_to_alpaca_timeframe(str(_lcb_inc))
                                        try:
                                            _lcb_held = list((portfolio_emulator.get_positions() or {}).keys()) if portfolio_emulator is not None else []
                                        except Exception:
                                            _lcb_held = []

                                        def _lcb_fetch(_syms, _start, _end,
                                                       _tf=_lcb_tf,
                                                       _k=_strat_data_key,
                                                       _s=_strat_data_secret):
                                            _db = None
                                            try:
                                                _db = get_conn()
                                            except Exception:
                                                _db = None
                                            try:
                                                return fetch_alpaca_historical_bars(
                                                    _syms, _start, _end, _k, _s,
                                                    timeframe=_tf, db_conn=_db, feed=data_feed,
                                                )
                                            finally:
                                                try:
                                                    if _db is not None:
                                                        _db.close()
                                                except Exception:
                                                    pass

                                        _rr_data = _lcb_build(
                                            _lcb_fetch,
                                            list(symbols or []),
                                            _lcb_held,
                                            sorted(globals().get("_live_crypto_discovered") or set()),
                                            list(_lcb_majors),
                                            _lcb_now, _lcb_inc, _lcb_lookback,
                                            last_good=globals().get("_live_crypto_bars_last_good"),
                                            log=_log,
                                        )
                                        if _rr_data:
                                            globals()["_live_crypto_bars_last_good"] = _rr_data
                                        elif _rr_data is None:
                                            _rr_specs_eff = []
                                            _log(
                                                "Live crypto bars unavailable (fetch failed, no last-good) — "
                                                "skipping strategies this tick; will retry next tick.",
                                                "red",
                                            )
                                    except Exception as _lcb_e:
                                        _rr_data = None
                                        _rr_specs_eff = []
                                        _log(
                                            f"Live crypto bars error: {type(_lcb_e).__name__}: {_lcb_e} — "
                                            f"skipping strategies this tick.",
                                            "red",
                                        )
                                _rr_fut = _PRICE_FETCH_EXECUTOR.submit(
                                    run_run_once_strategies,
                                    _rr_specs_eff, list(symbols or []), prices, current_time,
                                    _rr_data,
                                    portfolio_emulator=portfolio_emulator,
                                    time_increment=time_increment,
                                    alpaca_key=_strat_data_key,
                                    alpaca_secret=_strat_data_secret,
                                    alpaca_data_feed=data_feed,
                                    mode=_tick_mode,
                                )
                                globals()["_rr_prev_fut"] = _rr_fut
                                # Use a tighter watchdog when mode=MONITOR
                                # (price refresh + risk only, no LLM/discovery).
                                _wd_sec = (
                                    _WATCHDOG_MONITOR_SEC if _tick_mode == "MONITOR"
                                    else _RR_WATCHDOG_SEC
                                )
                                try:
                                    run_once_results = _rr_fut.result(
                                        timeout=_wd_sec
                                    )
                                    # 2026-05-07 scheduler refactor: reset
                                    # consecutive-skip counter on success.
                                    _strategy_consecutive_skips = 0
                                except _live_cf.TimeoutError:
                                    _log(
                                        f"run_run_once_strategies WATCHDOG TIMEOUT "
                                        f"(>{_wd_sec:.0f}s mode={_tick_mode or 'FULL'}) — strategy "
                                        f"wedged. Leaving worker to finish in "
                                        f"background; this tick proceeds with "
                                        f"empty results. Next tick will retry "
                                        f"only after the wedged future completes.",
                                        "red",
                                    )
                                    run_once_results = []
                                    # Fire alert via daemon thread so a
                                    # wedged Discord webhook / RethinkDB
                                    # can't extend the broker tick further.
                                    def _wd_fire_alert(_iid, _wd):
                                        try:
                                            from live_alerts import (
                                                alert_strategy_error as _ase_to,
                                            )
                                            _ase_to(
                                                instance_id=str(_iid),
                                                tag="run_once:watchdog-timeout",
                                                message=(
                                                    f"run_run_once_strategies "
                                                    f"exceeded {_wd:.0f}s "
                                                    f"watchdog. Strategy "
                                                    f"wedged; broker continued."
                                                ),
                                            )
                                        except Exception:
                                            pass
                                    try:
                                        threading.Thread(
                                            target=_wd_fire_alert,
                                            args=(instance_id, _wd_sec),
                                            daemon=True,
                                        ).start()
                                    except Exception:
                                        pass
                        else:
                            # BACKTEST: synchronous, no watchdog.
                            # Per-bar snapshot capture for LLM-critical rewind support.
                            # Captures the last-good-bar state immediately BEFORE the
                            # strategy fires; if LLMCriticalFailure raises mid-bar the
                            # outer-except handler restores from this snapshot and the
                            # bar replays on resume. See backtest_bar_snapshot.py.
                            if mode == MODE_BACKTEST:
                                try:
                                    from backtest_bar_snapshot import capture as _bs_capture
                                    _bs_capture(
                                        strategy_caches=(_strategy_cache if isinstance(_strategy_cache, dict) else {}),
                                        portfolio_emulator=portfolio_emulator if 'portfolio_emulator' in dir() else None,
                                        current_time=current_time,
                                    )
                                except Exception as _capture_err:
                                    try:
                                        _log(f"bar snapshot capture failed (non-fatal): {_capture_err}", "yellow")
                                    except Exception:
                                        pass
                            run_once_results = run_run_once_strategies(
                                _run_once_specs, list(symbols or []), prices, current_time,
                                price_history if mode == MODE_BACKTEST else None,
                                portfolio_emulator=portfolio_emulator,
                                time_increment=time_increment,
                                alpaca_key=_strat_data_key,
                                alpaca_secret=_strat_data_secret,
                                alpaca_data_feed=data_feed,
                            )
                    except Exception as _rr_exc:
                        # Round-4 Fix 7 + bug-sweep: surface strategy-loop crashes.
                        # In LIVE: skip a tick (results=[]) and let the next tick
                        # retry rather than killing the subprocess for transient
                        # errors. In BACKTEST: re-raise for the supervisor.
                        try:
                            _log(
                                f"run_run_once_strategies crashed: {type(_rr_exc).__name__}: {_rr_exc}",
                                "red",
                            )
                        except Exception:
                            pass
                        if mode == MODE_LIVE:
                            try:
                                from live_alerts import alert_strategy_error as _ase
                                _spec_names = ",".join(
                                    str((s or {}).get("name") or (s or {}).get("strategy") or "?")
                                    for s in (_run_once_specs or [])
                                )[:200]
                                _ase(
                                    instance_id=str(instance_id),
                                    tag=f"run_once:{_spec_names}",
                                    message=f"{type(_rr_exc).__name__}: {_rr_exc}",
                                )
                            except Exception:
                                pass
                            # Tick-level recovery: empty results, let outer loop
                            # continue to the NEXT tick rather than exiting the
                            # subprocess. Strategy meta extraction below iterates
                            # an empty list — a no-op.
                            run_once_results = []
                        else:
                            # BACKTEST: preserve historical behavior — outer
                            # backtest supervisor catches this, persists the
                            # crash snapshot, and exits cleanly.
                            raise
                # 2026-05-06 — emit an explicit "end of strategy execution"
                # marker so operators can see the cycle FINISHED and the
                # broker is moving on to order submission / sleep. Without
                # this line, a quiet broker after the per-symbol decision
                # logs is hard to distinguish from a wedge.
                if mode == MODE_LIVE:
                    try:
                        _n_results = len(run_once_results) if isinstance(run_once_results, (list, tuple)) else 0
                        _log(
                            f"Strategy execution complete | "
                            f"results={_n_results} | "
                            f"mode={_tick_mode or 'FULL'} | "
                            f"proceeding to per-symbol decision + order submission",
                            "green",
                        )
                    except Exception:
                        pass
                    # 2026-05-07 scheduler refactor: per-MONITOR Discord ping
                    # (per user request). Async via daemon thread so a slow
                    # webhook never extends the strategy tick. Gated on
                    # `_scheduler_call_ok` so a scheduler-error fallback
                    # (which forces _tick_mode='MONITOR' for safety) doesn't
                    # spam Discord every 60s.
                    if _tick_mode == "MONITOR" and _scheduler_call_ok:
                        try:
                            _meta_dict = {}
                            try:
                                # run_once_results is list[(spec, scores, reasons, metadata)].
                                # Pull nexus's metadata dict for held/sells counts.
                                for _spec_t, _scores_t, _reasons_t, _meta_t in (run_once_results or []):
                                    if isinstance(_meta_t, dict) and (
                                        (_spec_t or {}).get("strategy") == "graph_nexus_analysis"
                                    ):
                                        _meta_dict = dict(_meta_t)
                                        # Also fold scores into _meta_dict so the
                                        # held-count is computed correctly.
                                        if isinstance(_scores_t, dict):
                                            for _k, _v in _scores_t.items():
                                                _meta_dict.setdefault(_k, _v)
                                        break
                            except Exception:
                                pass
                            try:
                                from zoneinfo import ZoneInfo as _ZI_disc
                                _now_pt_disc = current_time.astimezone(_ZI_disc("America/Los_Angeles")).strftime("%H:%M PT")
                            except Exception:
                                _now_pt_disc = current_time.strftime("%H:%M UTC")
                            _date_key_disc = current_time.strftime("%Y-%m-%d")
                            _send_monitor_discord_notification(
                                str(instance_id), _date_key_disc, _now_pt_disc, _meta_dict,
                            )
                        except Exception:
                            pass
                # 2026-04-23 F1: persist strategy_cache AFTER each successful
                # run_once so `_deployment_bar_index` (and sibling keys)
                # survive container restarts. Live only.
                # 2026-05-02: surface persistence failures. The dual-cadence
                # marker (_nexus_full_cycle_completed_date) lives in this
                # cache; silent save loss → next-restart re-runs the full
                # cycle (one extra LLM cost, prevented from duplicate buys
                # by _orders_today idempotency). Log RED and emit a
                # consecutive-failure alert so operators see the issue.
                if mode == MODE_LIVE:
                    # 2026-05-03 live-hang investigation: get_conn_retry +
                    # _safe_enqueue can synchronously block the tick loop for
                    # >60s when RethinkDB is degraded. Submit the flush to a
                    # long-lived module-level executor (_SCP_PERSIST_EXECUTOR)
                    # and wait at most 10s for the result. A wedged worker
                    # leaks one thread (bounded by max_workers=2) but never
                    # blocks the live tick. On timeout, mark failed — next
                    # tick will retry.
                    def _scp_persist_blocking():
                        _conn = None
                        try:
                            from broker_snapshot_helpers import (
                                _invoke_save_strategy_cache,
                                _collect_prompt_versions,
                                _collect_llm_stages,
                                _collect_history_scope_inputs,
                            )
                            _conn = get_conn_retry(max_attempts=3, delay=2)
                            if _conn is None:
                                return (True, "RethinkDB unreachable after 3 retries")
                            for _spec_sc in (_run_once_specs or []):
                                _nm = str((_spec_sc or {}).get("strategy") or "").strip()
                                if not _nm:
                                    continue
                                _payload = _strategy_cache.get(_nm)
                                if isinstance(_payload, dict) and _payload:
                                    # Only wrap with new-schema kwargs for graph_nexus_analysis (the only
                                    # strategy that produces snapshots). Other strategies continue with
                                    # legacy 2-segment PK via save_strategy_cache_to_db's back-compat.
                                    if _nm == "graph_nexus_analysis":
                                        _bt_cfg_save = (_spec_sc or {}).get("config") or {}
                                        _ok = _invoke_save_strategy_cache(
                                            conn=_conn,
                                            r=r,
                                            instance_id=str(instance_id),
                                            strategy_name=_nm,
                                            cache=_payload,
                                            config_dict={
                                                "strategy_name": _nm,
                                                "prompt_versions": _collect_prompt_versions(_bt_cfg_save),
                                                "llm_stages": _collect_llm_stages(_bt_cfg_save),
                                                "history_scope_id_inputs": _collect_history_scope_inputs(_bt_cfg_save),
                                                "lookback_learning_days": int(_bt_cfg_save.get("lookback_learning_days", 120) or 120),
                                            },
                                        )
                                    else:
                                        # Legacy path for non-nexus strategies — preserves prior behavior.
                                        from strategy_cache_persistence import save_strategy_cache_to_db as _scp_save_legacy
                                        _ok = bool(_scp_save_legacy(_conn, r, str(instance_id), _nm, _payload))
                                    if not _ok:
                                        return (True, f"save_strategy_cache_to_db returned False for {_nm}")
                            return (False, None)
                        except Exception as _e:
                            return (True, f"{type(_e).__name__}: {_e}")
                        finally:
                            if _conn is not None:
                                try:
                                    _conn.close()
                                except Exception:
                                    pass
                    _save_failed = False
                    _save_err = None
                    try:
                        # ThreadPoolExecutor.submit() never blocks (work
                        # queue is unbounded SimpleQueue). The 10s
                        # result(timeout=10) below catches every real
                        # failure mode — wedged worker, slow query, etc.
                        _scp_fut = _SCP_PERSIST_EXECUTOR.submit(_scp_persist_blocking)
                        _save_failed, _save_err = _scp_fut.result(timeout=10)
                    except _live_cf.TimeoutError:
                        _save_failed = True
                        _save_err = "watchdog timeout (>10s) — leaving worker to finish in background"
                    except Exception as _scp_outer:
                        _save_failed = True
                        _save_err = f"{type(_scp_outer).__name__}: {_scp_outer}"
                    # Track consecutive failures to alert once at threshold.
                    if _save_failed:
                        _scp_fail_count = globals().get("_scp_consecutive_failures", 0) + 1
                        globals()["_scp_consecutive_failures"] = _scp_fail_count
                        _log(
                            f"strategy_cache persistence FAILED (#{_scp_fail_count}): "
                            f"{_save_err} — marker may be lost on restart",
                            "red",
                        )
                        if _scp_fail_count == 3 and not globals().get("_scp_alert_sent"):
                            # alert_strategy_error → _safe_enqueue → get_conn()
                            # can itself block when RethinkDB is the thing that's
                            # degraded. Fire as a daemon thread so this alert
                            # can never wedge the tick. The success flag is set
                            # INSIDE the thread so a thread-start failure (OS
                            # thread limit hit, etc.) does not permanently
                            # suppress future alerts.
                            def _scp_fire_alert(_msg, _iid):
                                try:
                                    from live_alerts import alert_strategy_error as _ase
                                    _ase(instance_id=_iid, tag="strategy-cache-persist", message=_msg)
                                    globals()["_scp_alert_sent"] = True
                                except Exception:
                                    pass
                            try:
                                _alert_msg = (
                                    f"strategy_cache persistence has failed {_scp_fail_count} "
                                    f"consecutive ticks. Last error: {_save_err}. "
                                    f"Dual-cadence marker (_nexus_full_cycle_completed_date) "
                                    f"may be lost on next restart, causing the full cycle "
                                    f"to re-run."
                                )
                                threading.Thread(
                                    target=_scp_fire_alert,
                                    args=(_alert_msg, str(instance_id)),
                                    daemon=True,
                                ).start()
                            except Exception:
                                pass
                    else:
                        if globals().get("_scp_consecutive_failures", 0) > 0:
                            _log("strategy_cache persistence recovered", "green")
                        globals()["_scp_consecutive_failures"] = 0
                        globals()["_scp_alert_sent"] = False

            # ── Extract nexus metadata from run_once results ──────────────
            nexus_discovered_syms = set()
            nexus_sell_enforcement = set()
            nexus_position_sizes: dict = {}  # sym -> {"buy_cash": float} and/or {"sell_fraction": float}
            nexus_executable_buys = set()
            nexus_action_intents_merged: dict = {}  # P1A: sym -> action_intent (for buy sort)
            for _spec_r, _scores_r, _reasons_r, *_meta_r in run_once_results:
                meta = _meta_r[0] if _meta_r else {}
                for d in (meta.get("_nexus_discovered") or []):
                    nexus_discovered_syms.add(d)
                for s in (meta.get("_nexus_sell_enforcement") or []):
                    nexus_sell_enforcement.add(s)
                for sym, hint in (meta.get("_nexus_position_sizes") or {}).items():
                    nexus_position_sizes[sym] = hint
                for sym in (meta.get("_nexus_executable_buys") or []):
                    nexus_executable_buys.add(sym)
                for sym, intent in (meta.get("_nexus_action_intents") or {}).items():
                    nexus_action_intents_merged[sym] = intent

            # Fix 15: Extract propagation-expansion BUY tickers from run_once results
            nexus_expansion_buys = set()
            for _spec_r, _scores_r, _reasons_r, *_meta_r in run_once_results:
                meta = _meta_r[0] if _meta_r else {}
                for eb in (meta.get("_nexus_expansion_buys") or []):
                    nexus_expansion_buys.add(eb)

            # Expand symbols with discovered stocks and fetch their prices
            expanded_symbols = set(symbols or [])

            # Fix 15: Add propagation-expansion BUY tickers to expanded symbols
            if nexus_expansion_buys:
                _exp_new = nexus_expansion_buys - expanded_symbols - nexus_discovered_syms
                if _exp_new:
                    _log(f"Nexus expansion buys: adding {len(_exp_new)} propagation-promoted ticker(s): {', '.join(sorted(_exp_new)[:10])}", "green")
                    expanded_symbols |= _exp_new
                    # V32 Phase 5: prefer live data-brokerage creds when set;
                    # else fall back to trading/env creds. Keeps data fetches
                    # working when trading brokerage is paper-only.
                    # R15 (2026-04-24): drop the `mode == MODE_LIVE` gate.
                    # Data brokerage holds the data-API subscription regardless
                    # of mode; using paper trading creds in backtest 401s the
                    # bars endpoint for every ticker (mega-caps included).
                    _k = data_key or key or os.environ.get("KEY", "")
                    _s = data_secret or secret or os.environ.get("SECRET", "")
                    if mode == MODE_BACKTEST:
                        loaded_syms = _ensure_backtest_history_for_symbols(data, list(_exp_new), key=_k, secret=_s)
                        _rebuild_ph = False
                        for loaded_sym in loaded_syms:
                            if loaded_sym not in symbols_for_data:
                                symbols_for_data.append(loaded_sym)
                                _rebuild_ph = True
                        if _rebuild_ph and price_history is not None:
                            price_history = get_price_history_up_to_current(data, symbols_for_data, current_time)
                    for ns in _exp_new:
                        if ns not in prices or not prices.get(ns):
                            _block_reason = _backtest_symbol_price_lookup_block_reason(data, ns, current_time) if mode == MODE_BACKTEST else ""
                            if _block_reason:
                                if _block_reason == "no_history":
                                    _nexus_cache = _strategy_cache.get("graph_nexus_analysis")
                                    if isinstance(_nexus_cache, dict):
                                        _nexus_cache.setdefault("_overlay_no_data_tickers", set()).add(ns)
                                expanded_symbols.discard(ns)
                                continue
                            p = None
                            if mode == MODE_BACKTEST and data:
                                try:
                                    p = _get_prices_at_time(data, [ns], current_time).get(ns)
                                except Exception:
                                    p = None
                            if p is None or p <= 0:
                                p = _fetch_price_for_symbol(
                                    ns, current_time, key=_k, secret=_s, feed=data_feed,
                                    allow_non_alpaca_fallback=(mode == MODE_LIVE),
                                )
                            if p and p > 0:
                                prices[ns] = p
                            else:
                                _log(f"Nexus expansion buy: could not fetch price for {ns}; will skip", "yellow")
                                expanded_symbols.discard(ns)

            # V20b fix: Add executable buys (including backfill rotation buys) to expanded symbols
            # so the broker includes them in the execution order and fetches their prices.
            if nexus_executable_buys:
                _exec_new = nexus_executable_buys - expanded_symbols - nexus_discovered_syms
                if _exec_new:
                    _log(f"Nexus executable buys: adding {len(_exec_new)} ticker(s) to execution: {', '.join(sorted(_exec_new)[:10])}", "green")
                    expanded_symbols |= _exec_new
                    # V32 Phase 5: prefer live data-brokerage creds when set;
                    # else fall back to trading/env creds. Keeps data fetches
                    # working when trading brokerage is paper-only.
                    # R15 (2026-04-24): see comment at the propagation-buys
                    # site above — same backtest-vs-data-brokerage fix.
                    _k = data_key or key or os.environ.get("KEY", "")
                    _s = data_secret or secret or os.environ.get("SECRET", "")
                    if mode == MODE_BACKTEST:
                        loaded_syms = _ensure_backtest_history_for_symbols(data, list(_exec_new), key=_k, secret=_s)
                        _rebuild_ph = False
                        for loaded_sym in loaded_syms:
                            if loaded_sym not in symbols_for_data:
                                symbols_for_data.append(loaded_sym)
                                _rebuild_ph = True
                        if _rebuild_ph and price_history is not None:
                            price_history = get_price_history_up_to_current(data, symbols_for_data, current_time)
                    for ns in list(_exec_new):
                        if ns not in prices or not prices.get(ns):
                            p = None
                            if mode == MODE_BACKTEST and data:
                                try:
                                    p = _get_prices_at_time(data, [ns], current_time).get(ns)
                                except Exception:
                                    p = None
                            if p is None or p <= 0:
                                p = _fetch_price_for_symbol(
                                    ns, current_time, key=_k, secret=_s, feed=data_feed,
                                    allow_non_alpaca_fallback=(mode == MODE_LIVE),
                                )
                            if p and p > 0:
                                prices[ns] = p
                            else:
                                expanded_symbols.discard(ns)

            # 2026-07-18: live crypto bars — remember the discovered universe
            # across ticks so the pre-dispatch bars fetch covers it next tick
            # (expanded_symbols is rebuilt per tick and never persists).
            # Live-only: backtest path byte-identical.
            if mode == MODE_LIVE and nexus_discovered_syms and _is_crypto_instance_runtime():
                try:
                    globals().setdefault("_live_crypto_discovered", set()).update(nexus_discovered_syms)
                except Exception:
                    pass
            if nexus_discovered_syms:
                new_syms = nexus_discovered_syms - expanded_symbols
                if new_syms:
                    _log(f"Nexus discovered: expanding symbols with {len(new_syms)} new tickers: {', '.join(sorted(new_syms)[:10])}", "cyan")
                    expanded_symbols |= new_syms
                    # V32 Phase 5: prefer live data-brokerage creds when set;
                    # else fall back to trading/env creds. Keeps data fetches
                    # working when trading brokerage is paper-only.
                    # R15 (2026-04-24): see comment at the propagation-buys
                    # site above — same backtest-vs-data-brokerage fix.
                    _k = data_key or key or os.environ.get("KEY", "")
                    _s = data_secret or secret or os.environ.get("SECRET", "")
                    if mode == MODE_BACKTEST:
                        loaded_syms = _ensure_backtest_history_for_symbols(data, list(new_syms), key=_k, secret=_s)
                        _rebuild_ph = False
                        for loaded_sym in loaded_syms:
                            if loaded_sym not in symbols_for_data:
                                symbols_for_data.append(loaded_sym)
                                _rebuild_ph = True
                        if _rebuild_ph and price_history is not None:
                            price_history = get_price_history_up_to_current(data, symbols_for_data, current_time)
                    # Fetch prices for newly discovered symbols
                    for ns in new_syms:
                        if ns not in prices or not prices.get(ns):
                            _block_reason = _backtest_symbol_price_lookup_block_reason(data, ns, current_time) if mode == MODE_BACKTEST else ""
                            if _block_reason:
                                if _block_reason == "no_history":
                                    _nexus_cache = _strategy_cache.get("graph_nexus_analysis")
                                    if isinstance(_nexus_cache, dict):
                                        _nexus_cache.setdefault("_overlay_no_data_tickers", set()).add(ns)
                                expanded_symbols.discard(ns)
                                continue
                            p = None
                            if mode == MODE_BACKTEST and data:
                                try:
                                    p = _get_prices_at_time(data, [ns], current_time).get(ns)
                                except Exception:
                                    p = None
                            if p is None or p <= 0:
                                p = _fetch_price_for_symbol(
                                    ns, current_time, key=_k, secret=_s, feed=data_feed,
                                    allow_non_alpaca_fallback=(mode == MODE_LIVE),
                                )
                            if p and p > 0:
                                prices[ns] = p
                            else:
                                _log(f"Nexus discovered: could not fetch price for {ns}; will skip", "yellow")
                                expanded_symbols.discard(ns)

            if nexus_sell_enforcement:
                _log(f"Nexus sell enforcement: {', '.join(sorted(nexus_sell_enforcement))}", "yellow")

            # V7.5: Ensure sell-enforcement tickers are in expanded_symbols so they enter _exec_order
            # Without this, fast loser cuts and trailing stops for tickers not in the current
            # discovery/expansion set silently fail to execute.
            if nexus_sell_enforcement and portfolio_emulator is not None:
                _enforce_missing = set()
                for _enf_sym in nexus_sell_enforcement:
                    if _enf_sym not in expanded_symbols:
                        _pos_qty = float((portfolio_emulator._positions or {}).get(_enf_sym, 0))
                        if _pos_qty > 0:
                            _enforce_missing.add(_enf_sym)
                if _enforce_missing:
                    _log(f"V7.5 sell enforcement injection: {len(_enforce_missing)} held ticker(s) added to execution: {', '.join(sorted(_enforce_missing))}", "yellow")
                    expanded_symbols |= _enforce_missing
                    # R15 (2026-04-24): same backtest cred fix as the other
                    # symbol-expansion sites — data brokerage first.
                    _enf_k = data_key or key or os.environ.get("KEY", "")
                    _enf_s = data_secret or secret or os.environ.get("SECRET", "")
                    # Ensure prices are available for these tickers
                    if mode == MODE_BACKTEST:
                        _ensure_backtest_history_for_symbols(data, list(_enforce_missing), key=_enf_k, secret=_enf_s)
                    for _enf_sym in _enforce_missing:
                        if _enf_sym not in prices or not prices.get(_enf_sym):
                            _enf_p = _get_prices_at_time(data, [_enf_sym], current_time).get(_enf_sym) if isinstance(data, dict) else None
                            if _enf_p is None or _enf_p <= 0:
                                # 2026-05-06: bound this HTTP fallback (Alpaca + yfinance)
                                # at 10s. A wedged fallback would silence the strategy
                                # thread for OS TCP timeout (60-180s+). On timeout:
                                # sell may be skipped this tick; sell-enforcement
                                # re-fires next cycle.
                                try:
                                    _enf_fut = _PRICE_FETCH_EXECUTOR.submit(
                                        _fetch_price_for_symbol,
                                        _enf_sym, current_time,
                                        key=_enf_k, secret=_enf_s, feed=data_feed,
                                        allow_non_alpaca_fallback=(mode == MODE_LIVE),
                                    )
                                    _enf_p = _enf_fut.result(timeout=10.0)
                                except _live_cf.TimeoutError:
                                    _log(
                                        f"V7.5 sell-enforcement price-fetch hard-timeout (>10s) "
                                        f"for {_enf_sym}; sell skipped this tick",
                                        "yellow",
                                    )
                                    _enf_p = None
                                except Exception as _enf_e:
                                    _log(
                                        f"V7.5 sell-enforcement price-fetch failed for "
                                        f"{_enf_sym}: {type(_enf_e).__name__}: {_enf_e}",
                                        "yellow",
                                    )
                                    _enf_p = None
                            if _enf_p and _enf_p > 0:
                                prices[_enf_sym] = _enf_p
                            else:
                                _log(f"V7.5 sell enforcement: could not fetch price for {_enf_sym}; forced sell may be skipped", "yellow")

            reserved_total = 0.0
            # Reserved-capital accounting applies in BOTH modes: without it,
            # live instances with earnings-pending or capital_pct>0 strategies
            # would silently let the first strategy consume all cash.
            if portfolio_emulator is not None:
                # 2026-05-06 — bound the price-fetch loop with a hard 30s
                # watchdog. _ensure_prices_include_positions iterates 7+
                # held positions and on each cache miss makes a synchronous
                # HTTP call (Alpaca → yfinance fallback) with NO inner
                # timeout. If RH/Alpaca/yfinance wedges, the strategy
                # thread silently hangs for OS TCP timeout (60-180s+) and
                # the next hourly tick never fires. Submit to the same
                # _PRICE_FETCH_EXECUTOR used by snapshot path; on
                # timeout, fall back to whatever prices we already have.
                _eppi_log_at = time.time()
                try:
                    _log(
                        f"Reserved-capital accounting: ensuring prices for "
                        f"{len(portfolio_emulator.get_positions() or {})} held "
                        f"position(s)...",
                        "white",
                    )
                except Exception:
                    pass
                _eppi_data = data if mode == MODE_BACKTEST else None
                _eppi_kwargs = {
                    "data": _eppi_data,
                    "symbols": symbols,
                    "key": key,
                    "secret": secret,
                }
                _eppi_fut = _PRICE_FETCH_EXECUTOR.submit(
                    _ensure_prices_include_positions,
                    portfolio_emulator, prices, current_time,
                    **_eppi_kwargs,
                )
                try:
                    prices = _eppi_fut.result(timeout=30.0)
                except _live_cf.TimeoutError:
                    _log(
                        "Reserved-capital price-fetch hard-timeout (>30s); "
                        "proceeding with current prices dict — held positions "
                        "without prices will value at 0 this tick.",
                        "yellow",
                    )
                except Exception as _eppi_e:
                    _log(
                        f"Reserved-capital price-fetch failed: "
                        f"{type(_eppi_e).__name__}: {_eppi_e}",
                        "yellow",
                    )
                try:
                    _eppi_dur = time.time() - _eppi_log_at
                    _log(
                        f"Reserved-capital price-fetch done in {_eppi_dur:.1f}s",
                        "white",
                    )
                except Exception:
                    pass
                pv = portfolio_emulator.get_portfolio_value(prices)
                if pv is not None and pv > 0:
                    for s in (_cached_strategies or []):
                        reserved_total += pv * _get_reserved_capital_pct(s)
                    if reserved_total > 0:
                        pct = 100.0 * reserved_total / pv
                        if pct >= 99.99:
                            _log("Reserved capital: %.2f (100%% of portfolio) for strategies with capital_pct (e.g. earnings-only)" % reserved_total, "cyan")
                        else:
                            _log("Reserved capital (other strategies cannot use): %.2f (%.1f%% of portfolio)" % (reserved_total, pct), "cyan")

            # After run_once / per-symbol strategies: execute any trades scheduled for TODAY by this bar's strategies.
            # Loops over _strategy_cache (all strategies), not _run_once_specs, so earnings per_symbol path also works.
            if mode == MODE_BACKTEST and portfolio_emulator is not None:
                try:
                    today_str_sb = (current_time.strftime("%Y-%m-%d") if hasattr(current_time, "strftime") else str(current_time)[:10])
                    from future_trades_util import get_all_pending_trades_for_date, pop_all_pending_trades_for_date
                    # Count queue state after strategies ran
                    queue_total_sb = 0
                    for_today_sb = 0
                    for _sname2, _sc2 in (_strategy_cache or {}).items():
                        if not _sc2:
                            continue
                        q2 = _sc2.get("_future_trades_queue") or []
                        queue_total_sb += len(q2)
                        for_today_sb += len(get_all_pending_trades_for_date(_sc2, current_time))
                    _log("[Pending] After strategies (%s) — queue_total=%d, for_today=%d" % (today_str_sb, queue_total_sb, for_today_sb), "cyan")

                    if for_today_sb > 0:
                        _key_sb = key or os.environ.get("KEY", "")
                        _secret_sb = secret or os.environ.get("SECRET", "")
                        ext2 = dict(prices) if prices else {}
                        pv2 = portfolio_emulator.get_portfolio_value(prices)
                        executed_same = 0
                        skipped_same_no_price = []
                        skipped_same_no_cash = []
                        # Task 7 review-fix (IMPORTANT 1): same-bar pending buys
                        # also run before the main emission gate — apply the same
                        # hard max_positions gate here (adds to held exempt).
                        _sbg_cap, _sbg_cap_reason = resolve_max_positions_cap(_cached_strategies)
                        _sbg_held = set()
                        _sbg_full_exits = set()
                        _sbg_new_emitted = set()
                        if _sbg_cap is not None:
                            try:
                                _sbg_pos = portfolio_emulator.get_positions() if hasattr(portfolio_emulator, "get_positions") else (getattr(portfolio_emulator, "_positions", {}) or {})
                                _sbg_held = {str(_s).strip().upper() for _s, _q in (_sbg_pos or {}).items() if float(_q or 0.0) > 0.0}
                            except Exception:
                                _sbg_cap = None  # fail-open: gate inert this block
                        for _sname2, _sc2 in list((_strategy_cache or {}).items()):
                            if not _sc2:
                                continue
                            pending2 = pop_all_pending_trades_for_date(_sc2, current_time)
                            if not pending2:
                                continue
                            for t in pending2:
                                sym = (t.get("symbol") or "").upper()
                                if sym and (sym not in ext2 or not ext2.get(sym)):
                                    f = _fetch_price_with_fallback(sym, current_time, key=_key_sb, secret=_secret_sb, log_fn=_log, feed=data_feed)
                                    if f and f > 0:
                                        ext2[sym] = f
                                        if sym not in _fetch_price_fail_logged:
                                            _log("[Pending] Fetched price %s=%.2f for same-bar trade" % (sym, f), "cyan")
                                    else:
                                        if sym not in _fetch_price_fail_logged:
                                            _fetch_price_fail_logged.add(sym)
                                            _log("[Pending] Could not fetch price for %s (Alpaca+yfinance both failed)" % sym, "yellow")
                            _spec2 = next((s for s in (_cached_strategies or []) if (s.get("strategy") or "").strip().lower() == _sname2.lower()), None)
                            cap_pct2 = _get_reserved_capital_pct(_spec2) if _spec2 else 1.0
                            rbudget = (pv2 * cap_pct2) if (pv2 and cap_pct2 > 0) else 0.0
                            remaining2 = float(rbudget)
                            # Track shares bought by this strategy for sell-fraction calculation
                            _epos2 = _sc2.get("_earnings_positions") if _sc2 else None
                            # Process sells first so freed cash is available for buys
                            pending2.sort(key=lambda _t: (0 if _t.get("signal", 0) == -1 else 1))
                            for t in pending2:
                                sym = (t.get("symbol") or "").upper()
                                sig = t.get("signal", 0)
                                if not sym or sig not in (1, -1):
                                    continue
                                price_sym = ext2.get(sym)
                                if not price_sym or price_sym <= 0:
                                    skipped_same_no_price.append(sym)
                                    _log("[Pending] SKIP %s %s — no price (target_date=%s, reason=%s)" % (
                                        "buy" if sig == 1 else "sell", sym,
                                        t.get("target_date", "?"), t.get("reason", "")[:80]), "yellow")
                                    continue
                                if sig == 1:
                                    alloc = t.get("allocation_pct")
                                    cap_for_this = (rbudget * (float(alloc) / 100.0)) if (alloc is not None and float(alloc) > 0) else remaining2
                                    cash_use = min(cap_for_this, remaining2, portfolio_emulator.get_cash())
                                    if cash_use <= 0:
                                        skipped_same_no_cash.append(sym)
                                        _log("[Pending] SKIP buy %s — no cash remaining (budget=%.2f, cash=%.2f)" % (
                                            sym, remaining2, portfolio_emulator.get_cash()), "yellow")
                                        continue
                                    # Task 7 review-fix (IMPORTANT 1): hard cap
                                    # gate on NEW-name same-bar pending buys.
                                    if _sbg_cap is not None and not max_positions_gate(_sbg_held, _sbg_cap, _sbg_full_exits, _sbg_new_emitted, sym):
                                        _sbg_proj = max_positions_projected_count(_sbg_held, _sbg_full_exits, _sbg_new_emitted)
                                        _log("MAX_POSITIONS_GATE: blocked %s (held=%d, cap=%d)" % (sym, _sbg_proj, _sbg_cap), "yellow")
                                        continue
                                    remaining2 -= cash_use
                                    shares_before = portfolio_emulator._positions.get(sym, 0.0)
                                    portfolio_emulator.execute_signal(sym, sig, price_sym, timestamp=current_time,
                                        cash_per_trade=cash_use, sell_fraction=1.0)
                                    shares_after = portfolio_emulator._positions.get(sym, 0.0)
                                    if _sbg_cap is not None and sym not in _sbg_held:
                                        _sbg_new_emitted.add(sym)
                                    if _epos2 is not None:
                                        _epos2[sym] = _epos2.get(sym, 0.0) + max(0.0, shares_after - shares_before)
                                else:
                                    # Sell: only sell shares this strategy bought (tracked in _earnings_positions)
                                    sell_frac = 1.0
                                    if _epos2 is not None and sym in _epos2 and _epos2[sym] > 0:
                                        total_shares = portfolio_emulator._positions.get(sym, 0.0)
                                        if total_shares > 0:
                                            sell_frac = min(1.0, _epos2[sym] / total_shares)
                                    _sbg_sell_ok = portfolio_emulator.execute_signal(sym, sig, price_sym, timestamp=current_time,
                                        cash_per_trade=1000.0, sell_fraction=sell_frac)
                                    # Task 7 review-fix (IMPORTANT 2): credit the
                                    # freed slot only on actual execution.
                                    if _sbg_cap is not None and _sbg_sell_ok and sell_frac >= 0.999 and sym in _sbg_held:
                                        _sbg_full_exits.add(sym)
                                    if _epos2 is not None and sym in _epos2:
                                        _epos2.pop(sym, None)
                                    # After sell frees cash, update remaining budget so subsequent buys can use it
                                    remaining2 = min(rbudget, portfolio_emulator.get_cash())
                                executed_same += 1
                                _log("[Pending] EXECUTED %s %s @ %.2f (strategy=%s, alloc=%.0f%%)" % (
                                    "buy" if sig == 1 else "sell", sym, price_sym, _sname2,
                                    t.get("allocation_pct", 0) or 0), "green")
                        _log("[Pending] Same-bar done: %d executed, %d skipped(no price: %s), %d skipped(no cash: %s)" % (
                            executed_same,
                            len(skipped_same_no_price), ", ".join(skipped_same_no_price) or "—",
                            len(skipped_same_no_cash), ", ".join(skipped_same_no_cash) or "—"), "cyan")
                        if ext2:
                            prices = ext2
                except Exception as _e2:
                    _log("[Pending] Same-bar execution error: %s" % _e2, "red")
                    import traceback as _tb2
                    _log(_tb2.format_exc(), "red")

            # ── Fix #2 + #3: Sell-first execution order, then buys ranked by allocation ──
            # P1A (2026-04-28): paired-rotation atomicity. backfill_rotation_buy is paired
            # with a sell on the same tick — that sell's proceeds belong to the paired buy.
            # Without intent priority, an alphabetically-earlier `initial_buy` can grab those
            # proceeds before the paired buy's gate runs (SNDK skipped for cash=$0 in #239009
            # despite WMT sell already executed). Solution: sort buys by (intent_priority,
            # buy_cash desc, ticker asc) so paired rotations consume their paired proceeds
            # first, and initial_buys only take the residual cash that's actually theirs.
            _BUY_INTENT_PRIORITY = {
                # Paired with explicit sell on same tick — must run first
                "backfill_rotation_buy": 0,
                # Adds to existing high-conviction positions (winner-add, amplifier)
                "winner_add_buy": 1,
                "momentum_amplifier_buy": 1,
                # Discretionary nexus-promoted buys
                "backfill_queue_buy": 2,
                "direct_reserved_buy": 2,
                "top_momentum_break_glass_buy": 2,
                # Default / initial_buy / unknown — process last
            }
            _DEFAULT_BUY_INTENT_PRIORITY = 3
            _nexus_sell_set = set(nexus_sell_enforcement)
            # Also treat symbols with sell_fraction in nexus_position_sizes as sells
            for _ps_sym, _ps_hint in (nexus_position_sizes or {}).items():
                if isinstance(_ps_hint, dict) and 'sell_fraction' in _ps_hint and 'buy_cash' not in _ps_hint:
                    _nexus_sell_set.add(_ps_sym)
            _sell_first = [s for s in sorted(expanded_symbols) if s in _nexus_sell_set]
            _buy_rest = [s for s in sorted(expanded_symbols) if s not in _nexus_sell_set]
            def _buy_sort_key(s: str) -> tuple:
                _hint = (nexus_position_sizes or {}).get(s) or {}
                # action_intent lives in nexus_action_intents_merged (extracted above),
                # not in nexus_position_sizes — different metadata channels.
                _intent = nexus_action_intents_merged.get(s)
                _intent_pri = _BUY_INTENT_PRIORITY.get(_intent, _DEFAULT_BUY_INTENT_PRIORITY)
                _alloc = float(_hint.get("buy_cash", 0) or 0) if isinstance(_hint, dict) else 0.0
                # tuple: (intent_priority asc, alloc desc via negation, ticker asc)
                return (_intent_pri, -_alloc, s)
            _buy_rest.sort(key=_buy_sort_key)
            _exec_order = _sell_first + _buy_rest
            if _sell_first:
                _log(f"Execution order: {len(_sell_first)} sell(s) first, then {len(_buy_rest)} buy/hold candidate(s) by (intent_priority, allocation, ticker)", "cyan")

            # 2026-04-23 F3: NY-midnight rollover for `_orders_today`. Cheap
            # (1 attribute compare) on 99.99% of ticks; only hits Alpaca's
            # REST once per NY-day when the date flips. Without this, a
            # long-lived container serves yesterday's order cache into
            # today's buy-loop and permanently blocks any re-buy of a
            # symbol that filled yesterday.
            # 2026-05-06: bound maybe_rollover_orders_today (calls
            # live_adapter.refresh_orders_today which is an unbounded HTTP
            # call to RH/Alpaca). 15s ceiling so a wedge can't silence
            # the broker.
            if mode == MODE_LIVE and portfolio_emulator is not None and hasattr(portfolio_emulator, "maybe_rollover_orders_today"):
                try:
                    _ro_fut = _PRICE_FETCH_EXECUTOR.submit(
                        portfolio_emulator.maybe_rollover_orders_today
                    )
                    try:
                        _ro_fut.result(timeout=15.0)
                    except _live_cf.TimeoutError:
                        _log(
                            "maybe_rollover_orders_today hard-timeout (>15s) — "
                            "proceeding; orders cache may be stale this tick",
                            "yellow",
                        )
                except Exception:
                    pass

            # Live-readiness P0 #5 / HIGH #6: poll kill-switch pre-strategy.
            # `live_kill_switch.halt_live_trading()` flips runCommand=False on
            # the Instances row. Old code only noticed when the socket dropped;
            # this gates submission per-tick. Reads the live row from RethinkDB
            # (cheap: single get by id, ~5ms typical). 2026-05-06: bound with
            # 10s timeout — RethinkDB degradation could otherwise hang the tick.
            if mode == MODE_LIVE and instance_id is not None and _KS_RDB is not None:
                def _ks_poll_blocking():
                    _ks_r = _KS_RDB()
                    _ks_conn = _ks_r.connect(
                        host=os.environ.get("RETHINKDB_HOST", "localhost"),
                        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                        timeout=5,
                    )
                    try:
                        return _ks_r.db("IntelliStock").table("Instances").get(str(instance_id)).run(_ks_conn)
                    finally:
                        try:
                            _ks_conn.close(noreply_wait=False)
                        except Exception:
                            pass
                try:
                    _ks_fut = _PRICE_FETCH_EXECUTOR.submit(_ks_poll_blocking)
                    try:
                        _inst = _ks_fut.result(timeout=10.0)
                        if _inst and _inst.get("runCommand") is False:
                            _halt_reason = str(_inst.get("halt_reason") or "kill switch flipped")
                            _log(f"KILL SWITCH ACTIVE: {_halt_reason} — skipping all submits this tick", "red")
                            _exec_order = []  # short-circuit the submit loop
                    except _live_cf.TimeoutError:
                        _log(
                            "Kill-switch poll hard-timeout (>10s) — "
                            "fail-open, continuing trading",
                            "yellow",
                        )
                except Exception as _ks_e:
                    _log(f"Kill-switch poll failed: {type(_ks_e).__name__}: {_ks_e} (failing open — continuing)", "yellow")

            # 2026-04-22 Fix 3b: sync emulator cash from Alpaca's authoritative
            # get_account().cash BEFORE the per-symbol buy-gate math runs.
            # 2026-05-06: bound the call with a 15s executor timeout so a
            # wedged RH/Alpaca HTTP can't silence the broker indefinitely.
            if mode == MODE_LIVE and portfolio_emulator is not None and _exec_order:
                try:
                    _pre_sync_cash = float(getattr(portfolio_emulator, "_cash", 0.0) or 0.0)
                    _cs_fut = _PRICE_FETCH_EXECUTOR.submit(portfolio_emulator.refresh_cash)
                    try:
                        _dto = _cs_fut.result(timeout=15.0)
                    except _live_cf.TimeoutError:
                        _log(
                            "Pre-cycle cash sync hard-timeout (>15s) — "
                            "proceeding with cached emulator._cash",
                            "yellow",
                        )
                        raise RuntimeError("cash-sync timeout")
                    _post_sync_cash = float(getattr(_dto, "cash", _pre_sync_cash) or _pre_sync_cash)
                    if abs(_post_sync_cash - _pre_sync_cash) > 0.01:
                        _log(
                            f"Pre-cycle cash sync: emulator ${_pre_sync_cash:.2f} "
                            f"→ Alpaca ${_post_sync_cash:.2f} "
                            f"(drift=${_post_sync_cash - _pre_sync_cash:+.2f})",
                            "cyan",
                        )
                        globals()["_cash_sync_was_drift"] = True
                    elif globals().get("_cash_sync_was_drift", True):
                        _log(
                            f"Pre-cycle cash sync: in-sync (${_post_sync_cash:.2f})",
                            "cyan",
                        )
                        globals()["_cash_sync_was_drift"] = False
                except Exception as _sync_e:
                    _log(
                        f"Pre-cycle cash sync failed: {type(_sync_e).__name__}: "
                        f"{_sync_e}; proceeding with cached emulator._cash",
                        "yellow",
                    )

            # A6 hoisted: broker-calendar market-open check ONCE per tick (not
            # per-symbol) to avoid log spam during holidays/half-days. Dedup
            # mirrors the "Outside session" / "Running" pattern so we only
            # print on transitions + periodic heartbeats.
            _live_market_open_this_tick = True
            if (not _is_crypto_instance_runtime()) and mode == MODE_LIVE and portfolio_emulator is not None and _exec_order:
                try:
                    # 2026-05-06: bound the market-open check at 10s. Some
                    # adapter implementations make an HTTP call here; a
                    # wedge would silence the broker.
                    _mo_fut = _PRICE_FETCH_EXECUTOR.submit(
                        portfolio_emulator.is_market_open, current_time
                    )
                    try:
                        _live_market_open_this_tick = bool(_mo_fut.result(timeout=10.0))
                    except _live_cf.TimeoutError:
                        _log(
                            "Market-open check hard-timeout (>10s) — "
                            "fail-open, treating market as open this tick",
                            "yellow",
                        )
                        _live_market_open_this_tick = True
                except Exception:
                    _live_market_open_this_tick = True  # fail-open on adapter issues
                _last_mkt = _loop_log_last_market_closed
                if not _live_market_open_this_tick:
                    if _last_mkt is not True or (time.time() - _loop_log_last_heartbeat_at) >= LOOP_LOG_HEARTBEAT_SEC:
                        _log(f"Live broker: market closed per broker calendar — deferring {len(_exec_order)} symbol decision(s) this tick.", "yellow")
                        _loop_log_last_heartbeat_at = time.time()
                    _loop_log_last_market_closed = True
                else:
                    if _last_mkt is True:
                        _log("Live broker: market open — resuming executions.", "green")
                    _loop_log_last_market_closed = False

            # 2026-04-30 v2 Task C: refresh quotes for the executable subset
            # immediately before per-symbol broker decisions. Tick-start
            # prices can be 5+ minutes stale by the time we get here. Without
            # this, qty=cash/price uses a stale price and the price-sanity
            # gate compares to a stale value too. 5s broker-level deadline
            # so a network stall doesn't block the whole tick.
            if mode == MODE_LIVE and _exec_order:
                _bt = (live_broker_type or "").strip().lower()
                _exec_syms = sorted({str(s).strip().upper() for s in _exec_order if s})
                _refresh_deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5)
                try:
                    _fresh: dict[str, float] = {}
                    if _bt == "alpaca":
                        # Use Alpaca's 1-min historical bars (last 20 minutes).
                        # Same source the price-sanity check uses, so values
                        # stay consistent with the gate's reference.
                        # 2026-05-06: 10s executor bound — fetch_alpaca_historical_bars
                        # makes a synchronous HTTP request with no inner timeout;
                        # a wedge would silence the broker.
                        _ph_now = datetime.datetime.now(datetime.timezone.utc)
                        def _alpaca_bars_blocking():
                            return fetch_alpaca_historical_bars(
                                _exec_syms,
                                (_ph_now - datetime.timedelta(minutes=20)),
                                _ph_now,
                                key=(getattr(live_adapter, "_api_key", None) or data_key or key),
                                secret=(getattr(live_adapter, "_api_secret", None) or data_secret or secret),
                                feed=data_feed,
                                timeframe="1Min",
                            ) or {}
                        _ph_fut = _PRICE_FETCH_EXECUTOR.submit(_alpaca_bars_blocking)
                        try:
                            _ph_bars_now = _ph_fut.result(timeout=10.0)
                        except _live_cf.TimeoutError:
                            _log(
                                "Pre-submit Alpaca bars hard-timeout (>10s) — "
                                "using tick-start prices",
                                "yellow",
                            )
                            _ph_bars_now = {}
                        for _s, _bars in _ph_bars_now.items():
                            if not _bars:
                                continue
                            _last = _bars[-1] if isinstance(_bars, list) else _bars
                            _c = _last.get("c") if isinstance(_last, dict) else None
                            if _c and float(_c) > 0:
                                _fresh[str(_s).strip().upper()] = float(_c)
                    elif _bt == "robinhood":
                        # 2026-05-06: 10s executor bound for the same reason.
                        from robinhood_engine import get_live_prices as _rh_gl
                        _rh_fut = _PRICE_FETCH_EXECUTOR.submit(_rh_gl, _exec_syms)
                        try:
                            _fresh = _rh_fut.result(timeout=10.0) or {}
                        except _live_cf.TimeoutError:
                            _log(
                                "Pre-submit RH live prices hard-timeout (>10s) — "
                                "using tick-start prices",
                                "yellow",
                            )
                            _fresh = {}
                    _refreshed = 0
                    for _s, _p in _fresh.items():
                        if datetime.datetime.now(datetime.timezone.utc) > _refresh_deadline:
                            _log("Pre-submit refresh deadline exceeded; using partial result", "yellow")
                            break
                        if _p and float(_p) > 0:
                            prices[_s] = float(_p)
                            _refreshed += 1
                    if _refreshed:
                        _log(f"Pre-submit quote refresh: {_refreshed}/{len(_exec_syms)} {_bt} symbols updated", "cyan")
                except Exception as _qe:
                    _log(f"Pre-submit quote refresh failed: {_qe} — using tick-start prices", "yellow")

            # ── Task 7 (round-2 credit-safety): hard max_positions gate at
            # order emission. GNA sizes NEW-name buys across several independent
            # paths (main allocation, BFQ direct-reserved, BFQ dequeue,
            # momentum-window adds) each against its OWN local headroom snapshot,
            # and rotation buys are cap-EXEMPT on the assumption their paired
            # full-exit sell nets the count to zero — nothing recounted the TOTAL
            # here, so the sums (plus any rotation whose sell didn't net) overshot
            # the cap (bt 586767: 13 held vs cap 10). This is the missing single
            # authoritative emission-time recount. Adds to existing names are
            # exempt; a same-cycle rotation pair nets to zero. Fail-open: if the
            # nexus config / cap can't be resolved the gate stays inert.
            _mpg_cap = None
            _mpg_held: set = set()
            _mpg_full_exits: set = set()   # names FULLY exited this cycle (sells run first)
            _mpg_new_emitted: set = set()  # NEW names already emitted this cycle
            if portfolio_emulator is not None:
                try:
                    _mpg_cap, _mpg_cap_reason = resolve_max_positions_cap(_cached_strategies)
                    _mpg_warn = max_positions_arm_warning(_mpg_cap_reason)
                    if _mpg_warn:
                        _log(_mpg_warn, "yellow")
                    _mpg_pos = portfolio_emulator.get_positions() if hasattr(portfolio_emulator, "get_positions") else (getattr(portfolio_emulator, "_positions", {}) or {})
                    _mpg_held = {str(_s).strip().upper() for _s, _q in (_mpg_pos or {}).items() if float(_q or 0.0) > 0.0}
                except Exception as _mpg_e:
                    _log(f"max_positions gate setup failed ({type(_mpg_e).__name__}: {_mpg_e}) — gate inert this tick", "yellow")
                    _mpg_cap = None
            if _mpg_cap is not None:
                _log(f"max_positions gate armed: held={len(_mpg_held)}, cap={_mpg_cap}", "cyan")

            # ── Task 13 (spec 5.8): same-cycle sell-proceeds crediting (LIVE
            # only). Live sells free cash on the async trade_updates WS fill,
            # so buys sized later in this cycle read pre-sell cached cash and
            # a rotation's paired buy starves. Book each submit-SUCCESSFUL
            # sell's expected proceeds (qty×frac×price) here; the buy path
            # lifts its sizing ceiling by 95% of the booked total via
            # buy_ceiling(). Backtest is untouched (emulator credits
            # synchronously). Kill-switch: live_credit_sell_proceeds_enabled
            # (default True) in the strategy config.
            _scp_enabled = True
            _scp_sell_proceeds: list = []
            if mode == MODE_LIVE:
                try:
                    for _scp_spec in (_cached_strategies or []):
                        _scp_cfg = (_scp_spec or {}).get("config") or {}
                        if "live_credit_sell_proceeds_enabled" in _scp_cfg:
                            _scp_enabled = bool(_scp_cfg.get("live_credit_sell_proceeds_enabled"))
                            break
                except Exception:
                    _scp_enabled = True

            # Residual sleeve (P&L sweep 2026-07-19): free sleeve capital for
            # active picks BEFORE the execution pass when cash runs low.
            # Inert unless residual_sleeve_enabled=true in the nexus config.
            _residual_sleeve_prepare(
                data, prices, current_time, _cached_strategies,
                key=key, secret=secret)
            _residual_sleeve_release(
                portfolio_emulator, prices, current_time, _cached_strategies)
            for symbol in _exec_order:
                # Step 1: Run all per-symbol pre-decision (voting) strategies and collect scores + weight overrides
                normalized = None  # reset per-symbol to avoid stale values from previous iteration
                strategy_results = []
                original_weights = []
                for spec in per_symbol_strategies:
                    original_weight = float(spec.get('weight', 0.0))
                    if original_weight <= 0:
                        _log(f"Skipping strategy '{spec.get('strategy')}' with weight {original_weight}", "yellow")
                        continue
                    # In backtest pass price_history (bars up to current_time only) and portfolio_emulator; live pass None
                    # 2026-05-06: bound at 15s per (spec, symbol). Most per-symbol strategies are
                    # pure Python; a hung strategy would silence the broker for 60×15s = 15min
                    # max even with 60 symbols. On timeout: skip this spec for this symbol.
                    if mode == MODE_LIVE:
                        try:
                            _rs_fut = _PRICE_FETCH_EXECUTOR.submit(
                                run_strategy,
                                spec, symbol, prices, current_time,
                                None,
                                portfolio_emulator=portfolio_emulator,
                                time_increment=time_increment,
                            )
                            score, weight_override, size_hint, reason = _rs_fut.result(timeout=15.0)
                        except _live_cf.TimeoutError:
                            _log(
                                f"run_strategy hard-timeout (>15s) for {symbol}/"
                                f"{spec.get('strategy', '?')}; skipping this spec this tick",
                                "yellow",
                            )
                            # 2026-05-06: timed-out strategy ABSTAINS, not HOLDS.
                            # Setting original_weight=0 below excludes this spec
                            # from the weighted sum so a hung strategy doesn't
                            # dilute the aggregate decision toward zero.
                            score, weight_override, size_hint, reason = 0, None, None, "watchdog-timeout"
                            original_weight = 0.0
                    else:
                        score, weight_override, size_hint, reason = run_strategy(
                            spec, symbol, prices, current_time,
                            price_history if mode == MODE_BACKTEST else None,
                            portfolio_emulator=portfolio_emulator,
                            time_increment=time_increment,
                        )
                    strategy_results.append({
                        'spec': spec,
                        'score': score,
                        'original_weight': original_weight,
                        'weight_override': weight_override,
                        'size_hint': size_hint,
                        'reason': reason,
                    })
                    original_weights.append(original_weight)
            
                # Step 2: Apply dynamic weight redistribution if any strategy overrides weight
                final_weights = []
                has_overrides = any(sr['weight_override'] is not None for sr in strategy_results)
            
                if has_overrides:
                    # Calculate total override weight and remaining weight
                    override_sum = sum(sr['weight_override'] or 0.0 for sr in strategy_results)
                    remaining_weight = 1.0 - override_sum
                
                    if remaining_weight < 0:
                        _log(f"Warning: Total weight overrides ({override_sum:.3f}) exceed 1.0. Clamping to 1.0.", "yellow")
                        override_sum = 1.0
                        remaining_weight = 0.0
                
                    # Calculate sum of original weights for strategies that didn't override
                    non_override_original_sum = sum(
                        sr['original_weight'] for sr in strategy_results
                        if sr['weight_override'] is None
                    )
                
                    # Redistribute remaining weight proportionally among non-override strategies
                    for sr in strategy_results:
                        if sr['weight_override'] is not None:
                            # Use override weight
                            final_weights.append(sr['weight_override'])
                            _log(f"Strategy '{sr['spec'].get('strategy')}': weight {sr['original_weight']:.3f} -> {sr['weight_override']:.3f} (override)", "cyan")
                        else:
                            # Scale proportionally
                            if non_override_original_sum > 0 and remaining_weight > 0:
                                scale_factor = remaining_weight / non_override_original_sum
                                new_weight = sr['original_weight'] * scale_factor
                                final_weights.append(new_weight)
                                _log(f"Strategy '{sr['spec'].get('strategy')}': weight {sr['original_weight']:.3f} -> {new_weight:.3f} (scaled by {scale_factor:.3f})", "cyan")
                            else:
                                # No remaining weight or no non-override strategies
                                final_weights.append(0.0)
                                _log(f"Strategy '{sr['spec'].get('strategy')}': weight {sr['original_weight']:.3f} -> 0.0 (no remaining weight)", "yellow")
                else:
                    # No overrides, use original weights
                    final_weights = original_weights
            
                # Step 3: Build weighted_scores from per-symbol strategies, then add run_once strategy scores for this symbol
                weighted_scores = [(w, sr['score']) for w, sr in zip(final_weights, strategy_results) if w > 0]
                for spec, scores_dict, reasons_dict, *_meta in run_once_results:
                    if symbol in scores_dict:
                        w = float(spec.get("weight", 0))
                        if w > 0:
                            weighted_scores.append((w, scores_dict[symbol]))
            
                if weighted_scores:
                    weighted_sum = sum(w * s for w, s in weighted_scores)
                    total_weight = sum(w for w, _ in weighted_scores)
                    normalized = weighted_sum / total_weight if total_weight > 0 else 0
                    _log(f"Weighted sum: {weighted_sum:.3f}, Total weight: {total_weight:.3f}, Normalized: {normalized:.3f}", "white")
                decision = aggregate_weighted_scores(weighted_scores)
                # Nexus sell enforcement: override decision to sell if trend reversal detected
                if symbol in nexus_sell_enforcement and decision != -1:
                    _log(f"Nexus sell enforcement: overriding {symbol} from {decision} to -1 (trend reversal)", "yellow")
                    decision = -1
                # ADDED BY CODEX (Fix 3): pull nexus action_intent so the human-readable trade
                # log attributes the decision (winner_add_buy, momentum_amplifier_buy, etc).
                # Sweep-3 fix: collect ALL intents (multi-nexus support) instead of breaking on first.
                _trade_intents = []
                for spec, scores_dict, reasons_dict, *_meta in run_once_results:
                    if symbol in scores_dict and float(spec.get("weight", 0) or 0) > 0:
                        _meta_dict = _meta[0] if _meta else {}
                        if isinstance(_meta_dict, dict):
                            _ai = (_meta_dict.get("_nexus_action_intents") or {}).get(symbol)
                            if _ai:
                                _trade_intents.append(_ai)
                _trade_action_intent = ",".join(_trade_intents) if _trade_intents else None
                # Sweep-3 fix: nexus buy promotion override. If aggregator returned hold (0)
                # but nexus has an explicit BUY action_intent (winner_add, amplifier, backfill,
                # rotation, top_momentum), honor the nexus decision. Multi-nexus dilution would
                # otherwise silently drop these high-conviction buys.
                _NEXUS_BUY_INTENTS = {
                    "winner_add_buy", "momentum_amplifier_buy", "backfill_queue_buy",
                    "backfill_rotation_buy", "top_momentum_break_glass_buy",
                    "direct_reserved_buy",
                }
                if decision == 0 and _trade_intents and any(i in _NEXUS_BUY_INTENTS for i in _trade_intents):
                    _log(f"Nexus buy promotion: overriding {symbol} from 0 to 1 (intents={_trade_intents})", "cyan")
                    decision = 1
                action = {1: "buy", 0: "hold", -1: "sell"}.get(decision, "hold")
                _intent_label = f" action_intent={_trade_action_intent}" if _trade_action_intent else ""
                _log(f"{symbol} @ {current_time} (${prices.get(symbol)}): {action}{_intent_label} (weighted scores from {len(weighted_scores)} strategies)", "white")

                # Build strategy summary (strategy name, weight, decision, reason) for post-decision strategies that need it (e.g. ai-trading-decision)
                strategy_summary = []
                for w, sr in zip(final_weights, strategy_results):
                    if w > 0:
                        strategy_summary.append({
                            "strategy": (sr["spec"].get("strategy") or "").strip(),
                            "weight": w,
                            "decision": sr["score"],
                            "reason": (sr.get("reason") or "")[:1500],
                        })
                for spec, scores_dict, reasons_dict, *_meta in run_once_results:
                    if symbol in scores_dict:
                        w = float(spec.get("weight", 0))
                        if w > 0:
                            meta = _meta[0] if _meta else {}
                            action_intent = ((meta.get("_nexus_action_intents") or {}).get(symbol) if isinstance(meta, dict) else None)
                            strategy_summary.append({
                                "strategy": (spec.get("strategy") or "").strip(),
                                "weight": w,
                                "decision": scores_dict[symbol],
                                "reason": ((reasons_dict or {}).get(symbol) or "")[:1500],
                                "action_intent": action_intent,
                            })

                # Last 30 days/bars of price history for this symbol (for ai-trading-decision)
                price_history_symbol = []
                if mode == MODE_BACKTEST and price_history and symbol in price_history:
                    bars = price_history[symbol] or []
                    price_history_symbol = bars[-30:] if len(bars) > 30 else bars

                # Resolve size: default sell all (1.0), buy up to cash_per_trade (1000). Use first pre-decision strategy that voted for decision and provided a size_hint.
                sell_fraction = 1.0
                cash_per_trade = 1000.0
                for w, sr in zip(final_weights, strategy_results):
                    if w > 0 and sr['score'] == decision and (sr.get('size_hint') or {}):
                        hint = sr['size_hint']
                        if decision == -1 and 'sell_fraction' in hint:
                            sell_fraction = hint['sell_fraction']
                            break
                        if decision == 1 and 'buy_cash' in hint:
                            cash_per_trade = hint['buy_cash']
                            break
                # Apply nexus position sizing if available (overrides default per-symbol sizes)
                nexus_hint = nexus_position_sizes.get(symbol) or {}
                if decision == 1 and 'buy_cash' in nexus_hint:
                    cash_per_trade = nexus_hint['buy_cash']
                elif decision == -1 and 'sell_fraction' in nexus_hint:
                    sell_fraction = nexus_hint['sell_fraction']
                # Run post-decision strategies (order size, pricing, and optional final decision override)
                pre_override_decision = decision
                pre_override_action = action
                post_decision_trace = []
                if _post_decision_specs:
                    price = prices.get(symbol)
                    # 2026-05-06: bound at 120s. Post-decision strategies often
                    # include LLM calls (ai-trading-decision). A wedged LLM
                    # call would silence the strategy thread until OS TCP
                    # timeout (60-180s+). On timeout: skip post-decision
                    # override, use raw decision from per-symbol loop.
                    if mode == MODE_LIVE:
                        try:
                            _pd_fut = _PRICE_FETCH_EXECUTOR.submit(
                                run_post_decision_strategies,
                                _post_decision_specs, symbol, decision, price,
                                portfolio_emulator, prices,
                                strategy_summary=strategy_summary,
                                price_history_symbol=price_history_symbol,
                            )
                            post_hints, final_decision_override, post_decision_trace = _pd_fut.result(timeout=120.0)
                        except _live_cf.TimeoutError:
                            _log(
                                f"run_post_decision_strategies hard-timeout (>120s) for "
                                f"{symbol}; using raw decision (no override)",
                                "yellow",
                            )
                            post_hints, final_decision_override, post_decision_trace = {}, None, []
                    else:
                        post_hints, final_decision_override, post_decision_trace = run_post_decision_strategies(
                            _post_decision_specs, symbol, decision, price, portfolio_emulator, prices,
                            strategy_summary=strategy_summary,
                            price_history_symbol=price_history_symbol,
                        )
                    if final_decision_override is not None and final_decision_override in (1, 0, -1):
                        # V6: Never override forced exits (fast loser, trailing stop, hold-limit)
                        if symbol in nexus_sell_enforcement and final_decision_override != -1:
                            _log(f"{symbol}: AI override blocked — nexus forced exit (sell enforcement)", "yellow")
                        else:
                            decision = final_decision_override
                            action = {1: "buy", 0: "hold", -1: "sell"}.get(decision, "hold")
                            _log(f"{symbol}: AI/final-decision override -> {action}", "cyan")
                    if post_hints:
                        if decision == 1 and 'buy_cash' in post_hints:
                            cash_per_trade = post_hints['buy_cash']
                        if decision == -1 and 'sell_fraction' in post_hints:
                            sell_fraction = post_hints['sell_fraction']
                nexus_buy_guard = build_nexus_buy_guard(
                    strategy_summary,
                    symbol,
                    decision,
                    nexus_executable_buys,
                    nexus_position_sizes,
                )
                # Execute buy/sell via PortfolioEmulator (backtest) or AlpacaAdapter
                # (live) — both share the same execute_signal() contract via
                # BrokerAdapter. Cap buy size by available cash minus reserved
                # capital. Before this fix this block was gated on
                # `mode == MODE_BACKTEST` which silently dropped every live order.
                _trade_skipped_no_price = False
                if portfolio_emulator is not None and decision != 0:
                    price = prices.get(symbol)
                    if not price or price <= 0:
                        _log(f"SKIP {action} {symbol} — no price at {current_time}", "yellow")
                        _trade_skipped_no_price = True
                    else:
                        nexus_hint = nexus_position_sizes.get(symbol) or {}
                        if not isinstance(nexus_hint, dict):
                            nexus_hint = {}
                        _nexus_block = get_nexus_buy_block_details(symbol, float(price), nexus_buy_guard) if decision == 1 else None
                        _nexus_block_reason = str((_nexus_block or {}).get("message") or "") if _nexus_block else None
                        if _nexus_block_reason:
                            _log(f"SKIP BUY {symbol} - {_nexus_block_reason}", "yellow")
                            _trade_skipped_no_price = True
                            _nexus_cache = _strategy_cache.get("graph_nexus_analysis")
                            if _nexus_cache is not None:
                                _skip_code = str((_nexus_block or {}).get("code") or "execution_gate")
                                _nexus_cache.setdefault("_broker_skipped_buys", []).append({
                                    "ticker": symbol,
                                    "allocated": round(float(cash_per_trade or 0.0), 2),
                                    "reason": _skip_code,
                                    "price": round(float(price), 4),
                                    "raw_net_score": round(float(nexus_hint.get("raw_net_score", 0.0) or 0.0), 4),
                                    "signal_source": str(nexus_hint.get("signal_source") or ""),
                                    "is_watchlist_member": bool(nexus_hint.get("is_watchlist_member")),
                                    "is_watchlist_priority": bool(nexus_hint.get("is_watchlist_priority")),
                                    "is_propagation_expansion": bool(nexus_hint.get("is_propagation_expansion")),
                                })
                                _log(f"Gate skips reported back: {symbol} ({_skip_code})", "magenta")
                            continue
                        # 2026-07-19 regime-safety: airtight per-regime position
                        # cap at the ONE choke point every buy lane passes
                        # through (backfill queue, momentum watchlist,
                        # direct-reserved, rotations, initial buys). A buy for
                        # a symbol NOT already held is refused whenever the
                        # book already fills the regime cap. Held-symbol adds
                        # (winner adds, partial fills) are exempt.
                        if decision == 1:
                            # 2026-07-19 adversarial review MED: the bear
                            # symbol is reserved for the sleeve — a strategy
                            # position in it would collide with the leg's
                            # protective exit / stop-loss accounting.
                            _rsv_cfg = _residual_sleeve_config(_cached_strategies)
                            if _rsv_cfg.get("enabled") and symbol == (_rsv_cfg.get("bear_symbol") or None):
                                _log(f"SKIP BUY {symbol} — reserved sleeve bear symbol", "yellow")
                                continue
                            _rc = _regime_position_cap_hard(_cached_strategies)
                            if _rc is not None:
                                _rc_positions = getattr(portfolio_emulator, "_positions", {}) or {}
                                _rc_held_qty = float(_rc_positions.get(symbol, 0.0) or 0.0)
                                # Sleeve legs (SPY bull leg / inverse bear leg)
                                # are broker-level cash parking, not strategy
                                # positions — they must not consume cap slots.
                                _rc_sleeve_cfg = _residual_sleeve_config(_cached_strategies)
                                _rc_exclude = {_rc_sleeve_cfg.get("symbol") or "",
                                               _rc_sleeve_cfg.get("bear_symbol") or ""}
                                _rc_open = sum(
                                    1 for _s, _q in _rc_positions.items()
                                    if float(_q or 0.0) > 0.0 and _s not in _rc_exclude)
                                if _rc_held_qty <= 0.0 and _rc_open >= _rc[1]:
                                    _log(
                                        f"REGIME CAP HARD BLOCK: {symbol} skipped — "
                                        f"held={_rc_open} >= cap={_rc[1]} (regime={_rc[0]})",
                                        "yellow",
                                    )
                                    _nexus_cache = _strategy_cache.get("graph_nexus_analysis")
                                    if _nexus_cache is not None:
                                        _nexus_cache.setdefault("_broker_skipped_buys", []).append({
                                            "ticker": symbol,
                                            "allocated": round(float(cash_per_trade or 0.0), 2),
                                            "reason": "regime_cap",
                                            "price": round(float(price), 4),
                                            "raw_net_score": round(float(nexus_hint.get("raw_net_score", 0.0) or 0.0), 4),
                                            "signal_source": str(nexus_hint.get("signal_source") or ""),
                                            "is_watchlist_member": bool(nexus_hint.get("is_watchlist_member")),
                                            "is_watchlist_priority": bool(nexus_hint.get("is_watchlist_priority")),
                                            "is_propagation_expansion": bool(nexus_hint.get("is_propagation_expansion")),
                                        })
                                        _log(f"Gate skips reported back: {symbol} (regime_cap)", "magenta")
                                    continue
                        cash_to_use = cash_per_trade
                        if decision == 1:
                            _cash_floor_pct = float((nexus_position_sizes or {}).get("_cash_reserve_floor_pct", 0.10))
                            _cash_floor = portfolio_emulator._initial_value * _cash_floor_pct
                            _cash_floor_hard = bool((nexus_position_sizes or {}).get("_cash_reserve_floor_hard", True))
                            _cash_floor_min_positions = int((nexus_position_sizes or {}).get("_cash_reserve_hard_min_positions", 5) or 5)
                            _cash_floor_release_after_min_positions = bool((nexus_position_sizes or {}).get("_cash_reserve_release_after_min_positions", True))
                            _high_conviction = (nexus_position_sizes or {}).get(symbol, {}).get("high_conviction", False)
                            _open_positions = sum(1 for _qty in (portfolio_emulator._positions or {}).values() if float(_qty or 0.0) > 0.0)
                            if _cash_floor_hard:
                                _can_bypass_floor = _cash_floor_release_after_min_positions and _high_conviction and _open_positions >= _cash_floor_min_positions
                            else:
                                _can_bypass_floor = _high_conviction
                            _effective_floor = 0.0 if _can_bypass_floor else _cash_floor
                            # 2026-04-22: snapshot cash ONCE so the per-symbol
                            # diagnostic log and the `available` arithmetic
                            # read the same value. Without this snapshot, a
                            # fill arriving on the trade_updates WS between
                            # the two reads would make the log report a cash
                            # value that disagrees with the one the gate
                            # actually used, confusing post-mortem reads.
                            _cash_now = float(portfolio_emulator.get_cash() or 0.0)
                            # 2026-04-22 Fix 5: opt-in margin sizing via the
                            # `live_use_buying_power_for_sizing` config flag
                            # (default False — stay cash-only). Live-mode
                            # only; backtest path skips (PortfolioEmulator
                            # has no `_buying_power` attr). Uses
                            # max(cash, buying_power) so an API hiccup
                            # returning BP=0 never SIZES BELOW cash.
                            # R19 (2026-04-25): bare `config` was undefined at
                            # this module-level scope — NameError reached the
                            # backtest exception handler and aborted the run
                            # the moment the first BUY tried to execute. R15
                            # masked it (every backtest 401'd before reaching
                            # this branch). Read from the active strategy
                            # specs' configs; flag is live-only by design so
                            # backtest path always evaluates to False.
                            _use_bp = False
                            if mode == MODE_LIVE:
                                _use_bp = any(
                                    bool(((sr.get("spec") or {}).get("config") or {}).get(
                                        "live_use_buying_power_for_sizing"
                                    ))
                                    for sr in (strategy_results or [])
                                )
                            _sizing_ceiling = _cash_now
                            _bp_cached = 0.0
                            if _use_bp and mode == MODE_LIVE:
                                _bp_cached = float(getattr(portfolio_emulator, "_buying_power", 0.0) or 0.0)
                                if _bp_cached > 0:
                                    _sizing_ceiling = max(_cash_now, _bp_cached)
                            # Task 13 (spec 5.8): lift the live sizing ceiling
                            # by 95% of this cycle's submit-successful sell
                            # proceeds (booked below after each sell submit).
                            # Live-only; backtest cash is already credited
                            # synchronously by the emulator. buy_ceiling()
                            # clamps the haircut ≤ 1 so the ceiling can never
                            # exceed cash + proceeds; disabled via the
                            # live_credit_sell_proceeds_enabled kill-switch.
                            if mode == MODE_LIVE and _scp_sell_proceeds:
                                _scp_ceiling = buy_ceiling(_sizing_ceiling, _scp_sell_proceeds, enabled=_scp_enabled)
                                if _scp_ceiling > _sizing_ceiling:
                                    _log(
                                        f"Sell-proceeds credit: sizing ceiling ${_sizing_ceiling:.2f} → "
                                        f"${_scp_ceiling:.2f} (+95% of ${sum(_scp_sell_proceeds):.2f} "
                                        f"same-cycle submitted sells)",
                                        "cyan",
                                    )
                                _sizing_ceiling = _scp_ceiling
                            available = max(0.0, _sizing_ceiling - reserved_total - _effective_floor)
                            cash_to_use = min(cash_per_trade, available)
                            # Live-readiness HIGH #9: broker-side max_single_position_pct cap.
                            # Strategy can stack to 30-40% sector cap; broker enforces a hard
                            # ceiling (default 15% of equity) regardless of strategy output.
                            # Failsafe — disable by setting BROKER_MAX_SINGLE_POSITION_PCT=0.
                            try:
                                _max_single_pct = float(os.environ.get("BROKER_MAX_SINGLE_POSITION_PCT", "0.15") or "0.15")
                            except (ValueError, TypeError):
                                _max_single_pct = 0.15
                            # Crypto instances set explicit per-coin allocations
                            # (e.g. 20%, 50%), so the equity 15% single-position
                            # safety cap must NOT trim them — honor the user's donut.
                            if _max_single_pct > 0 and mode == MODE_LIVE and not _is_crypto_instance_runtime():
                                try:
                                    # Q4 fix: use CURRENT portfolio value (cash + positions) so
                                    # the cap scales with equity growth, not stuck at start-equity.
                                    _equity = 0.0
                                    if hasattr(portfolio_emulator, "get_portfolio_value"):
                                        try:
                                            _equity = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
                                        except Exception:
                                            _equity = 0.0
                                    if _equity <= 0:
                                        _equity = float(getattr(portfolio_emulator, "_initial_value", 0.0) or 0.0)
                                    if _equity > 0:
                                        _existing_qty = float((getattr(portfolio_emulator, "_positions", {}) or {}).get(symbol, 0.0) or 0.0)
                                        _existing_value = _existing_qty * float(price or 0.0)
                                        _max_position_value = _equity * _max_single_pct
                                        _headroom = max(0.0, _max_position_value - _existing_value)
                                        if cash_to_use > _headroom:
                                            _log(
                                                f"Broker single-position cap: {symbol} cash_to_use ${cash_to_use:.2f} "
                                                f"trimmed to ${_headroom:.2f} (existing=${_existing_value:.2f}, "
                                                f"cap={_max_single_pct*100:.0f}%=${_max_position_value:.2f})",
                                                "magenta",
                                            )
                                            cash_to_use = _headroom
                                except Exception as _cap_e:
                                    _log(f"Single-position cap check failed for {symbol}: {type(_cap_e).__name__}: {_cap_e}", "yellow")
                            # Live-readiness HIGH #10: price-sanity check. Reject buys at
                            # prices >20% off recent close (fat-finger / stale-feed protection).
                            # Skipped in backtest where prices come from clean historical bars.
                            # Q5 fix: in live mode, `price_history` is typically None — fetch
                            # last close on-demand from Alpaca daily bars (1 day back).
                            if mode == MODE_LIVE and price and price > 0:
                                try:
                                    _last_close = None
                                    _ph = (price_history or {}).get(symbol) or [] if price_history else []
                                    if _ph and isinstance(_ph, list):
                                        for _b in reversed(_ph[-5:]):  # check last 5 bars for a usable close
                                            if isinstance(_b, dict):
                                                _c = _b.get("c") or _b.get("close")
                                                if _c is not None:
                                                    try:
                                                        _last_close = float(_c)
                                                        if _last_close > 0:
                                                            break
                                                    except (TypeError, ValueError):
                                                        _last_close = None
                                    if not _last_close:
                                        # Live-mode fallback: ask Alpaca for last 5 days of daily bars.
                                        try:
                                            import datetime as _ps_dt
                                            _ps_today = _ps_dt.datetime.utcnow()
                                            _ps_start = (_ps_today - _ps_dt.timedelta(days=7)).strftime("%Y-%m-%d")
                                            _ps_end = _ps_today.strftime("%Y-%m-%d")
                                            # 2026-04-30 v2 Task B: skip price-sanity for non-Alpaca brokers
                                            # (RH has no /v2/stocks/bars endpoint). Cred order: adapter (in-
                                            # process, guaranteed) -> data_key (separate live-data brokerage,
                                            # may be missing) -> module-level (now-wiped at line ~4339).
                                            if (live_broker_type or "").strip().lower() == "alpaca":
                                                # 2026-05-06: bound at 10s — runs PER-BUY,
                                                # an unbounded HTTP wedge would silence the
                                                # strategy thread for OS TCP timeout. On
                                                # timeout: skip price-sanity for this symbol
                                                # (rely on adapter quote / cached price).
                                                try:
                                                    _ph_fut = _PRICE_FETCH_EXECUTOR.submit(
                                                        fetch_alpaca_historical_bars,
                                                        [symbol], _ps_start, _ps_end,
                                                        key=(getattr(live_adapter, "_api_key", None) or data_key or key),
                                                        secret=(getattr(live_adapter, "_api_secret", None) or data_secret or secret),
                                                        feed=data_feed,
                                                        timeframe="1Day",
                                                    )
                                                    _ph_bars = _ph_fut.result(timeout=10.0) or {}
                                                except _live_cf.TimeoutError:
                                                    _log(
                                                        f"Per-buy price-sanity Alpaca-bars hard-timeout "
                                                        f"(>10s) for {symbol}; falling back to adapter quote",
                                                        "yellow",
                                                    )
                                                    _ph_bars = {}
                                                except Exception:
                                                    _ph_bars = {}
                                            else:
                                                _ph_bars = {}  # RH: skip Alpaca-specific sanity, rely on adapter quote
                                            _sym_bars = _ph_bars.get(symbol) or []
                                            for _b in reversed(_sym_bars[-3:]):
                                                if isinstance(_b, dict):
                                                    _c = _b.get("c") or _b.get("close")
                                                    if _c is not None:
                                                        try:
                                                            _last_close = float(_c)
                                                            if _last_close > 0:
                                                                break
                                                        except (TypeError, ValueError):
                                                            _last_close = None
                                        except Exception:
                                            pass
                                    if _last_close and _last_close > 0:
                                        _delta_pct = abs(float(price) - _last_close) / _last_close
                                        try:
                                            _max_delta = float(os.environ.get("BROKER_MAX_PRICE_DELTA_PCT", "0.20") or "0.20")
                                        except (ValueError, TypeError):
                                            _max_delta = 0.20
                                        if _delta_pct > _max_delta:
                                            _log(
                                                f"PRICE SANITY REJECT: {symbol} price ${price:.2f} vs last_close ${_last_close:.2f} "
                                                f"({_delta_pct*100:.1f}% off > {_max_delta*100:.0f}% threshold) — skipping buy",
                                                "red",
                                            )
                                            cash_to_use = 0.0
                                except Exception as _ps_e:
                                    _log(f"Price-sanity check failed for {symbol}: {type(_ps_e).__name__}: {_ps_e}", "yellow")
                            # Diagnostic so skipped buys (e.g. GOOGL+BE this
                            # morning) self-explain. Logs every gate input.
                            try:
                                _exec_min_pos_preview = 50.0
                                _will_skip = cash_to_use < _exec_min_pos_preview and cash_to_use < cash_per_trade
                                _bp_part = f" bp=${_bp_cached:.2f}" if _use_bp else ""
                                _log(
                                    f"Buy gate inputs for {symbol}: "
                                    f"cash=${_cash_now:.2f}{_bp_part} "
                                    f"reserved=${reserved_total:.2f} "
                                    f"floor=${_cash_floor:.2f} "
                                    f"effective_floor=${_effective_floor:.2f} "
                                    f"high_conv={_high_conviction} "
                                    f"open_pos={_open_positions} "
                                    f"cash_per_trade=${cash_per_trade:.2f} "
                                    f"available=${available:.2f} "
                                    f"cash_to_use=${cash_to_use:.2f} "
                                    f"→ {'SKIP' if _will_skip else 'PASS'}",
                                    "yellow" if _will_skip else "cyan",
                                )
                            except Exception:
                                pass
                        # V3: Execution-time min position size check for buys
                        _exec_min_pos = 50.0
                        if decision == 1 and cash_to_use < _exec_min_pos and cash_to_use < cash_per_trade:
                            _log(f"SKIP BUY {symbol} — cash_to_use ${cash_to_use:.2f} < min ${_exec_min_pos:.0f} (allocated ${cash_per_trade:.2f})", "yellow")
                            _trade_skipped_no_price = True  # reuse flag to prevent recording
                            # V7.1: Report skipped buys to strategy cache for backfill queue
                            _nexus_cache = _strategy_cache.get("graph_nexus_analysis")
                            if _nexus_cache is not None:
                                _nexus_cache.setdefault("_broker_skipped_buys", []).append({
                                    "ticker": symbol,
                                    "allocated": round(cash_per_trade, 2),
                                    "reason": "insufficient_cash",
                                    "price": round(float(price), 4),
                                    "raw_net_score": round(float(nexus_hint.get("raw_net_score", 0.0) or 0.0), 4),
                                    "signal_source": str(nexus_hint.get("signal_source") or ""),
                                    "is_watchlist_member": bool(nexus_hint.get("is_watchlist_member")),
                                    "is_watchlist_priority": bool(nexus_hint.get("is_watchlist_priority")),
                                    "is_propagation_expansion": bool(nexus_hint.get("is_propagation_expansion")),
                                })
                                _log(f"Gate skips reported back: {symbol} (insufficient_cash)", "magenta")
                        else:
                            # A6: gate on the hoisted per-tick market-open flag.
                            # Hoisted above the per-symbol loop so we call
                            # is_market_open() ONCE per tick (not N times) and
                            # dedup the "market closed" log line.
                            if mode == MODE_LIVE and not _live_market_open_this_tick:
                                _trade_skipped_no_price = True
                                continue
                            # 2026-04-22 Fix B: skip re-submitting if we
                            # already have a non-rejected order of this side
                            # for this symbol today (e.g. container restarted
                            # after the morning cycle already bought AAPL).
                            # Fix A (date-keyed cid) would make Alpaca
                            # idempotency return the existing order anyway,
                            # but short-circuiting here avoids the round-trip
                            # and gives operators a clear log trail.
                            _side_word = "buy" if decision == 1 else "sell"

                            # Z2.1 phase 1 (log-only): observe ghost-sells in
                            # BOTH live and backtest modes. Action is "sell"
                            # but no legitimate sell intent in the strategy
                            # summary. The whitelist matches _VALID_ACTION_INTENTS
                            # sell-side enum values in
                            # backend/strategies/graph_nexus_analysis.py:554.
                            # Phase 1 only logs; phase 2 will enforce.
                            if _side_word == "sell":
                                try:
                                    _z21_intents = {
                                        str(_s.get("action_intent", "")).strip().lower()
                                        for _s in (strategy_summary or [])
                                        if _s.get("decision") == decision
                                    }
                                except Exception:
                                    _z21_intents = set()
                                _Z21_SELL_WHITELIST = {
                                    "sell", "sell_override",
                                    "rotation_sell", "trend_reversal_sell",
                                    "fast_loser_cut", "trailing_stop_sell",
                                    "circuit_breaker_sell", "hold_limit_sell",
                                    "deep_loser_protect", "forced_exit",
                                    "downtrend_protection_sell",
                                    "consecutive_neutral_pruning",
                                    "panel_sell", "etf_sell",
                                }
                                if not (_z21_intents & _Z21_SELL_WHITELIST):
                                    _z21_pre = (pre_override_action if 'pre_override_action' in locals() else None)
                                    _log(
                                        f"[ghost_sell_observation] symbol={symbol} "
                                        f"intents={sorted(_z21_intents)!r} pre_action={_z21_pre!r} "
                                        f"would_block_in_phase2=True",
                                        "yellow",
                                    )

                            try:
                                _already = (
                                    mode == MODE_LIVE
                                    and hasattr(portfolio_emulator, "ordered_today")
                                    and portfolio_emulator.ordered_today(symbol, _side_word)
                                )
                            except Exception:
                                _already = False
                            if _already:
                                _log(
                                    f"SKIP {_side_word.upper()} {symbol} — already ordered "
                                    f"today (Fix-B restart guard); Fix-A cid would "
                                    f"idempotently return the existing order anyway",
                                    "yellow",
                                )
                                _trade_skipped_no_price = True
                                continue
                            # ── Task 7: hard max_positions gate. Block a NEW-name
                            # buy that would push the projected open-position count
                            # to/over the cap. Adds/winner-adds to names already
                            # held are exempt; a same-cycle rotation pair (full
                            # exit of another name funding this buy) nets to zero
                            # because that sell already ran (sells sort first) and
                            # is booked into _mpg_full_exits below.
                            if _mpg_cap is not None and decision == 1:
                                if not max_positions_gate(_mpg_held, _mpg_cap, _mpg_full_exits, _mpg_new_emitted, symbol):
                                    _mpg_proj = max_positions_projected_count(_mpg_held, _mpg_full_exits, _mpg_new_emitted)
                                    _log(f"MAX_POSITIONS_GATE: blocked {symbol} (held={_mpg_proj}, cap={_mpg_cap})", "yellow")
                                    _trade_skipped_no_price = True
                                    continue
                            # 2026-05-06: bound order submission at 90s. Adapter
                            # internally has ~70s retry budget + 25-45s inter-order
                            # delay = ~115s worst case naturally; outer 90s ceiling
                            # prevents the strategy thread from being silenced if
                            # _submit_lock contention or RH backend stalls. RH
                            # idempotency via client_order_id (cid) prevents
                            # duplicate orders if the abandoned worker eventually
                            # succeeds and the next tick re-tries.
                            # Task 7 review-fix (IMPORTANT 2): track whether the
                            # submit actually succeeded so a FAILED funding sell
                            # cannot credit a rotation slot (which would admit
                            # the paired buy and land the book at cap+1 live).
                            _mpg_submit_ok = False
                            if mode == MODE_LIVE:
                                try:
                                    _es_fut = _PRICE_FETCH_EXECUTOR.submit(
                                        portfolio_emulator.execute_signal,
                                        symbol, decision, price,
                                        timestamp=current_time,
                                        cash_per_trade=cash_to_use,
                                        sell_fraction=sell_fraction,
                                    )
                                    _es_placed = _es_fut.result(timeout=90.0)
                                    _mpg_submit_ok = bool(_es_placed)
                                    # Telemetry only — record the confirmed
                                    # buy/sell + reasoning for the app's "Bot
                                    # activity". Fully off the trade path.
                                    if _es_placed and decision in (1, -1):
                                        _log_live_trade_decision(
                                            symbol, decision, price, current_time,
                                            strategy_summary, post_decision_trace,
                                            pre_override_decision, normalized,
                                        )
                                except _live_cf.TimeoutError:
                                    _log(
                                        f"execute_signal hard-timeout (>90s) for "
                                        f"{_side_word.upper()} {symbol}; abandoning "
                                        f"future to background. RH cid-idempotency "
                                        f"prevents duplicate if it eventually succeeds.",
                                        "yellow",
                                    )
                                except Exception as _es_e:
                                    _log(
                                        f"execute_signal failed for {_side_word.upper()} "
                                        f"{symbol}: {type(_es_e).__name__}: {_es_e}",
                                        "yellow",
                                    )
                            else:
                                _mpg_submit_ok = bool(portfolio_emulator.execute_signal(symbol, decision, price, timestamp=current_time, cash_per_trade=cash_to_use, sell_fraction=sell_fraction))

                            # ── Task 7: keep the running cycle counts current so
                            # later buys in this _exec_order see this emission. A
                            # NEW-name buy grows the count on INTENT (a failed
                            # buy blocking a later buy errs on the safe side of
                            # the cap); a FULL-exit sell (sell_fraction ~1.0) of
                            # a held name frees a slot ONLY when the submit
                            # actually succeeded (review IMPORTANT 2 — a failed
                            # funding sell must not admit the paired rotation
                            # buy). Sells sort first, so a rotation's funding
                            # exit is booked before its buy. Async live fills
                            # can still fail post-submit — accepted residual.
                            if _mpg_cap is not None:
                                _mpg_sym_u = str(symbol).strip().upper()
                                if decision == 1 and _mpg_sym_u not in _mpg_held:
                                    _mpg_new_emitted.add(_mpg_sym_u)
                                elif decision == -1 and _mpg_sym_u in _mpg_held and _mpg_submit_ok:
                                    try:
                                        _mpg_is_full_exit = float(sell_fraction or 0.0) >= 0.999
                                    except Exception:
                                        _mpg_is_full_exit = False
                                    if _mpg_is_full_exit:
                                        _mpg_full_exits.add(_mpg_sym_u)

                            # ── Task 13 (spec 5.8): book expected proceeds of a
                            # submit-SUCCESSFUL live sell so later buys in this
                            # SAME cycle may spend 95% of them (buy_ceiling).
                            # Positions are read AFTER the submit on purpose:
                            # if the WS fill already landed during the wait,
                            # the adapter already credited cash AND removed the
                            # position — qty reads 0 and nothing double-books.
                            if mode == MODE_LIVE and decision == -1 and _mpg_submit_ok and _scp_enabled:
                                try:
                                    _scp_pos = portfolio_emulator.get_positions() if hasattr(portfolio_emulator, "get_positions") else (getattr(portfolio_emulator, "_positions", {}) or {})
                                    _scp_qty = float((_scp_pos or {}).get(str(symbol).strip().upper(), 0.0) or 0.0)
                                    _scp_frac = max(0.0, min(1.0, float(sell_fraction if sell_fraction is not None else 1.0)))
                                    _scp_expected = _scp_qty * _scp_frac * float(price)
                                    if _scp_expected > 0:
                                        _scp_sell_proceeds.append(_scp_expected)
                                        _log(
                                            f"Sell-proceeds credit: booked ${_scp_expected:.2f} expected from "
                                            f"{symbol} sell (cycle total ${sum(_scp_sell_proceeds):.2f}; buys may "
                                            f"spend 95% after partial-fill haircut)",
                                            "cyan",
                                        )
                                except Exception as _scp_e:
                                    _log(f"Sell-proceeds booking failed for {symbol}: {type(_scp_e).__name__}: {_scp_e}", "yellow")

                # Capture per-symbol sub-strategy decision for playback UI (skip if no price — trade wasn't executed)
                if mode == MODE_BACKTEST and _backtest_decisions is not None and not _trade_skipped_no_price:
                    _norm = normalized
                    aligned_strategies = [s for s in strategy_summary if s.get("decision") == decision]
                    primary_pool = aligned_strategies or list(strategy_summary)
                    primary_entry = None
                    if primary_pool:
                        primary_entry = max(
                            primary_pool,
                            key=lambda item: (
                                float(item.get("weight") or 0.0),
                                1 if (item.get("reason") or "").strip() else 0,
                                len(str(item.get("reason") or "")),
                            ),
                        )
                    post_primary = post_decision_trace[-1] if post_decision_trace else None
                    override_applied = pre_override_decision != decision
                    primary_strategy = None
                    primary_action_intent = None
                    final_reason = ""
                    if post_primary and (override_applied or (post_primary.get("reason") or "").strip()):
                        primary_strategy = post_primary.get("strategy")
                        final_reason = str(post_primary.get("reason") or "").strip()[:1500]
                    elif primary_entry:
                        primary_strategy = primary_entry.get("strategy")
                        primary_action_intent = primary_entry.get("action_intent")
                        final_reason = str(primary_entry.get("reason") or "").strip()[:1500]

                    _backtest_decisions.append({
                        "timestamp": current_time.isoformat() if hasattr(current_time, "isoformat") else str(current_time),
                        "symbol": symbol,
                        "action": action,
                        "decision": decision,
                        "normalized_score": round(_norm, 4) if _norm is not None else None,
                        "override_applied": override_applied,
                        "pre_override_action": pre_override_action if override_applied else None,
                        "pre_override_decision": pre_override_decision if override_applied else None,
                        "primary_strategy": primary_strategy,
                        "primary_action_intent": primary_action_intent,
                        "final_reason": final_reason,
                        "strategies": list(strategy_summary) if strategy_summary else [],
                        "post_decision": list(post_decision_trace) if post_decision_trace else [],
                    })
    
            ###################################
            ## Save portfolio snapshot every loop (value at current time with current prices).
            ## Applies in BOTH modes so LiveState's portfolio_history and the UI's
            ## equity curve are populated — previously gated backtest-only, leaving
            ## live instances with a flat equity chart forever.
            ###################################
            if portfolio_emulator is not None:
                # 2026-05-07 scheduler refactor: this was THE wedge site —
                # main thread blocking on TLS recv with no inner timeout
                # while the snapshot daemon kept emitting heartbeats. Bound
                # the EPPI call to 30s; on timeout we keep cached prices for
                # the snapshot rather than blocking the loop forever. The
                # snapshot itself goes via _SNAPSHOT_EXECUTOR with its own
                # 15s budget. On IDLE ticks, skip the post-tick snapshot
                # entirely — equity didn't change during a no-op tick and
                # the snapshot worker (3s cadence) covers the equity-curve
                # need anyway.
                if mode == MODE_LIVE and _tick_mode == "IDLE":
                    pass  # skip — the snapshot daemon handles equity-curve updates
                elif mode == MODE_LIVE:
                    try:
                        _set_strategy_tick_phase("post_tick_snapshot")
                    except Exception:
                        pass
                    prices = _bounded_eppi_call(
                        portfolio_emulator, prices, current_time,
                        data=None, symbols=symbols, key=key, secret=secret,
                        label="post-tick portfolio-snapshot EPPI", timeout=30.0,
                    )
                    try:
                        _snap_fut = _SNAPSHOT_EXECUTOR.submit(
                            portfolio_emulator.save_portfolio_snapshot,
                            prices, timestamp=current_time,
                        )
                        _snap_fut.result(timeout=10.0)
                    except _live_cf.TimeoutError:
                        _log("post-tick snapshot write >10s; dropping (next tick recovers)", "yellow")
                    except Exception as _snap_exc:
                        _log(f"post-tick snapshot write failed: {type(_snap_exc).__name__}: {_snap_exc}", "yellow")
                else:
                    # BACKTEST: keep the synchronous unbounded path — backtest
                    # runs against in-process data, no TCP wedge risk.
                    # 2026-05-07 perf: in dual-cadence backtest harness mode
                    # the strategy decides FULL/MONITOR/IDLE itself. EPPI is
                    # expensive (per-position price-source chain); snapshot
                    # is cheap (one dict append). Skip rules:
                    #   IDLE   → skip BOTH (no fills, no equity change)
                    #   MONITOR → skip EPPI only (positions already priced
                    #             from the upstream bar fetch); KEEP snapshot
                    #             so the equity curve preserves intra-day
                    #             granularity for HWM / drawdown / UI charts
                    #   FULL   → unchanged
                    # _nexus_last_tick_mode is stamped by
                    # graph_nexus_analysis at every entry path.
                    _nexus_tick_mode = (
                        (_strategy_cache.get("graph_nexus_analysis", {}) or {})
                        .get("_nexus_last_tick_mode")
                    )
                    _skip_eppi = (
                        _dc_bt_sim
                        and _nexus_tick_mode in ("MONITOR", "IDLE")
                    )
                    _skip_snapshot = (
                        _dc_bt_sim
                        and _nexus_tick_mode == "IDLE"
                    )
                    if not _skip_eppi:
                        prices = _ensure_prices_include_positions(
                            portfolio_emulator, prices, current_time,
                            data=data, symbols=symbols, key=key, secret=secret,
                        )
                        # Residual sleeve (P&L sweep 2026-07-19): park idle
                        # cash above the buffer at cycle end. Inert unless
                        # residual_sleeve_enabled=true.
                        _residual_sleeve_deploy(
                            portfolio_emulator, prices, current_time,
                            _cached_strategies)
                    if not _skip_snapshot:
                        portfolio_emulator.save_portfolio_snapshot(prices, timestamp=current_time)
    
        ###################################
        ## Backtesting: update progress, P&L, status in DB (throttled for fast loops)
        ###################################
        if mode == MODE_BACKTEST and _backtest_result_id is not None and portfolio_emulator is not None:
            import time as _time
            now_loop = _time.time()
            try:
                total_sec = (end_dt - start_dt).total_seconds()
                elapsed_sec = (current_time - start_dt).total_seconds()
                progress_pct = min(100.0, max(0.0, (elapsed_sec / total_sec * 100.0) if total_sec > 0 else 0))
            except NameError:
                progress_pct = 0.0
            # V7.5: Update DB only when a new day completes (not on every intraday bar).
            # For daily bars (time_increment >= 86400), this updates every bar.
            # For intraday bars, this batches updates until the date changes.
            _current_date_str = current_time.strftime("%Y-%m-%d") if hasattr(current_time, "strftime") else str(current_time)[:10]
            _is_daily_bar = time_increment is not None and int(time_increment) >= 86400
            should_update = False
            if _is_daily_bar:
                # Daily bars: always update (each bar = one day)
                should_update = True
            elif _current_date_str != getattr(portfolio_emulator, '_last_db_update_date', ''):
                # Intraday bars: update when date changes
                should_update = True
                portfolio_emulator._last_db_update_date = _current_date_str
            elif progress_pct >= _last_progress_updated + 2.0 or progress_pct >= 99.99:
                # Fallback: update every 2% progress for very long intraday runs
                should_update = True
                _last_progress_updated = progress_pct
            # 2026-05-07 perf: only refresh prices + recompute portfolio value
            # when we're actually about to write a DB row. Previously this ran
            # on every backtest bar (~3000x at 1200s) even when most bars were
            # short-circuited by the throttle below.
            if should_update:
                prices = _ensure_prices_include_positions(
                    portfolio_emulator, prices, current_time,
                    data=data if mode == MODE_BACKTEST else None,
                    symbols=symbols, key=key, secret=secret,
                )
                current_value = portfolio_emulator.get_portfolio_value(prices)
                pnl = (current_value - initial_cash) if (current_value is not None and initial_cash is not None) else None
                pnl_percent = ((current_value - initial_cash) / initial_cash * 100.0) if (pnl is not None and initial_cash and initial_cash != 0) else None
                progress_update_ok = False
                conn = _backtest_db_conn
                if conn is None:
                    conn = get_conn_retry(max_attempts=5, delay=3)
                    if conn is not None:
                        _backtest_db_conn = conn
                if conn is not None:
                    try:
                        backtest_id_raw = backtest_row_id
                        backtest_id_int = int(backtest_id_raw) if backtest_id_raw and str(backtest_id_raw).isdigit() else None
                        current_tickers = sorted(set(symbols or []) | set((portfolio_emulator.get_positions() or {}).keys()))
                        update_payload = {
                            'backtest_id': backtest_id_int,
                            'progress': round(progress_pct, 2),
                            'pnl': pnl,
                            'pnl_percent': round(pnl_percent, 4) if pnl_percent is not None else None,
                            'status': 'running',
                            'timestamp': __import__('datetime').datetime.now().isoformat(),
                            '_last_active': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                            'tickers': current_tickers,
                        }
                        try:
                            update_payload['backtest_trades'] = _convert_datetimes_to_iso(
                                list(portfolio_emulator.get_trade_history() or [])[-1000:]
                            )
                        except Exception:
                            pass
                        try:
                            # Downsample (keep true start + shape), not tail-slice,
                            # so a long/high-cadence RUNNING backtest shows the real
                            # start value and curve instead of a mid-run window.
                            from broker_snapshot_helpers import downsample_history as _downsample_history
                            update_payload['portfolio_value_history'] = _convert_datetimes_to_iso(
                                _downsample_history(list(portfolio_emulator.get_portfolio_history() or []), 3000)
                            )
                        except Exception:
                            pass
                        if backtest_start_time is not None:
                            try:
                                update_payload['time_elapsed_seconds'] = max(0, int(now_loop - backtest_start_time))
                            except Exception:
                                pass
                        if _backtest_log_buffer is not None:
                            update_payload['logs'] = list(_backtest_log_buffer)[-500:]
                        if _backtest_decisions is not None:
                            update_payload['backtest_decisions'] = _convert_datetimes_to_iso(list(_backtest_decisions))
                        r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).update(update_payload).run(conn)
                        progress_update_ok = True
                        _backtest_progress_fail_count = 0
                        try:
                            _log(f"Progress updated in DB: {progress_pct:.1f}%", "cyan")
                        except NameError:
                            pass
                        # Edit #backtests Discord message with progress and P&L (use difficulty from BacktestResults so each backtest shows its own)
                        if backtest_row_id is not None:
                            try:
                                from interactive_utils import action_enqueue_discord_edit
                                pnl_str = "$%.2f" % pnl if pnl is not None else "—"
                                pct_str = "%.2f%%" % pnl_percent if pnl_percent is not None else "—"
                                tickers_str = ", ".join((current_tickers or [])[:8])
                                if current_tickers and len(current_tickers) > 8:
                                    tickers_str += " (+%d)" % (len(current_tickers) - 8)
                                result_doc = r.db(DB_NAME).table('BacktestResults').get(_backtest_result_id).run(conn)
                                diff_from_db = result_doc.get('difficulty') if result_doc else None
                                if diff_from_db is not None:
                                    diff_str = "%.1f" % float(diff_from_db)
                                    if backtest_high_usage:
                                        diff_str += " (HIGH USAGE)"
                                else:
                                    diff_str = _backtest_difficulty_discord_str()
                                action_enqueue_discord_edit(
                                    conn, "backtests", str(backtest_row_id),
                                    content=None,
                                    embed={
                                        "title": "Backtest Running",
                                        "description": "Progress and P&L update as the backtest runs.",
                                        "color": 0xF39C12,
                                        "fields": [
                                            {"name": "ID", "value": str(backtest_row_id), "inline": True},
                                            {"name": "Status", "value": "Running", "inline": True},
                                            {"name": "Difficulty", "value": diff_str, "inline": True},
                                            {"name": "Progress", "value": "%.1f%%" % progress_pct, "inline": True},
                                            {"name": "P&L", "value": pnl_str, "inline": True},
                                            {"name": "P&L %", "value": pct_str, "inline": True},
                                            {"name": "Tickers", "value": tickers_str or "—", "inline": False},
                                        ],
                                    },
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        _backtest_db_conn = None
                        try:
                            conn.close()
                        except Exception:
                            pass
                        _backtest_progress_fail_count += 1
                        _log(
                            f"Backtest progress update failed ({_backtest_progress_fail_count}/{_BACKTEST_PROGRESS_FAIL_MAX}), reconnecting next time: {e}",
                            "yellow",
                        )
                else:
                    _backtest_progress_fail_count += 1
                    _log(
                        f"Backtest progress update: could not connect to RethinkDB (attempt {_backtest_progress_fail_count}/{_BACKTEST_PROGRESS_FAIL_MAX}). Will retry next update.",
                        "yellow",
                    )
                if not progress_update_ok and _backtest_progress_fail_count >= _BACKTEST_PROGRESS_FAIL_MAX:
                    progress_pct_on_fail = None
                    try:
                        total_sec = (end_dt - start_dt).total_seconds()
                        elapsed_sec = (current_time - start_dt).total_seconds()
                        progress_pct_on_fail = min(100.0, max(0.0, (elapsed_sec / total_sec * 100.0) if total_sec > 0 else 0))
                    except Exception:
                        pass
                    _backtest_save_error_and_exit(
                        f"RethinkDB connection failed {_BACKTEST_PROGRESS_FAIL_MAX} times in a row. Last error: lost connection.",
                        progress_pct_on_fail,
                    )
    
        ###################################
        ## Backtesting/LIVE footer (MUST run for BOTH modes - previously
        ## nested under the backtest-only progress block, causing a live
        ## hot loop that spammed "Outside session" with no sleep.)
        ###################################
        if mode == MODE_BACKTEST:
            while _backtest_paused and not shutdown_requested:
                try:
                    _log("Backtest paused; waiting for resume...", "cyan")
                except NameError:
                    pass
                time.sleep(1)
            if shutdown_requested:
                break
            print("Time Increment: ", time_increment)
            # Crypto trades 24/7 — never apply the equity session gate / market-
            # open skip. Equity keeps the legacy skip-to-next-open behavior.
            _adv_is_crypto = _is_crypto_instance_runtime()
            _adv_next = _advance_backtest_time(
                current_time, backtest_increment_td, _adv_is_crypto,
                _is_within_trading_session_pt, _next_market_open_utc,
            )
            if (not _adv_is_crypto) and _adv_next != current_time + backtest_increment_td:
                # Equity, outside trading hours: skipped to the next market open.
                try:
                    _log("Skipped to next market open: %s" % _adv_next, "cyan")
                except NameError:
                    pass
            current_time = _adv_next
        else:
            # 2026-05-07 scheduler refactor (commit 4): sleep until the
            # `_next_wake_utc` computed at the TOP of this loop iteration by
            # backend.scheduler.get_next_wake. This replaces the legacy
            # drift-free boundary calc which had no concept of session
            # windows — the scheduler returns a wake time that already
            # respects open/close hours, weekends, and the FULL anchor.
            #
            # Sleep is interruptible via the existing _live_trading_stop_event
            # (set by the snapshot worker's shutdown signal handler) so
            # SIGTERM doesn't have to wait up to a tick to take effect.
            _now_for_sleep = datetime.datetime.now(datetime.timezone.utc)
            if _next_wake_utc is None:
                # Scheduler unavailable / fallback path: 60s cap.
                _delay = 60.0
            else:
                _delay = max(0.1, (_next_wake_utc - _now_for_sleep).total_seconds())
                # Clock-skew safety: cap at 1h so a bogus next_wake doesn't
                # block the loop forever.
                _delay = min(_delay, 3600.0)
            try:
                from zoneinfo import ZoneInfo as _ZI_log
                _wake_pt_log = (
                    _next_wake_utc.astimezone(_ZI_log("America/Los_Angeles")).strftime("%H:%M PT")
                    if _next_wake_utc else "unknown"
                )
            except Exception:
                _wake_pt_log = (_next_wake_utc.strftime("%H:%M UTC") if _next_wake_utc else "unknown")
            try:
                _log(
                    f"strategy-tick #{_strategy_tick_n} COMPLETED | "
                    f"mode={_tick_mode or 'FULL'} | sleeping {_delay:.0f}s "
                    f"until next_wake={_wake_pt_log}",
                    "white",
                )
            except Exception:
                pass
            try:
                _set_strategy_tick_phase("sleep", completed=True)
            except Exception:
                pass
            # Interruptible sleep — Event.wait returns True on signal, False on timeout.
            try:
                if _live_trading_stop_event.wait(_delay):
                    # Shutdown signaled mid-sleep.
                    break
            except Exception:
                # Defensive — fall back to plain sleep if the Event is somehow unusable.
                time.sleep(_delay)
            # Refresh current_time as tz-aware UTC for the next iteration's
            # session gate, bar alignment, and wall-clock TTLs.
            current_time = datetime.datetime.now(datetime.timezone.utc)

        #time.sleep(0.1)
    except BaseException as _outer_err:
        # CRITICAL-GUARD route: route LLMCriticalFailure to the dedicated abort handler
        # BEFORE the generic crash path. The dedicated handler updates BacktestResults
        # with status='aborted_llm_failure' (not the generic crash status), sets
        # _skip_snapshot_persist, and pages Discord with full diagnostics.
        #
        # NOTE: outer except is `BaseException` (not `Exception`) because
        # LLMCriticalFailure inherits from BaseException — this guarantees it
        # is never accidentally swallowed by an intermediate `except Exception`.
        # The trade-off is we also catch KeyboardInterrupt/SystemExit here, so
        # we explicitly re-raise non-Exception cases below.
        try:
            from llm_critical_guard import LLMCriticalFailure
            _is_llm_critical = isinstance(_outer_err, LLMCriticalFailure)
        except Exception:
            _is_llm_critical = False

        if _is_llm_critical:
            try:
                # R2 Task 3 (2026-07): role-INDEPENDENT fatal classes
                # (insufficient_credits / HTTP 402) take a CLEAN-STOP pause in
                # backtest mode, NOT the paused_llm_critical idle-wait below.
                # Incident 586767: OpenRouter credits died mid-run and the
                # backtest simulated an entire month LLM-blind. A credit
                # top-up can take days — idling a container that long is
                # waste; instead write status='paused_credits' (merge-only
                # update: partial results preserved), fire ONE operator alert
                # via the alert_strategy_error seam, and exit 0. The engine's
                # containers.run(detach=False) finally block reaps the
                # container and deletes the queue row WITHOUT touching
                # BacktestResults.status (same lifecycle as a normal finish),
                # so 'paused_credits' survives. Resume = top up + re-queue;
                # the existing resume-date query skips processed days.
                try:
                    from llm_critical_guard import failure_is_role_independent as _f_role_indep
                    _is_credit_fatal = _f_role_indep(_outer_err)
                except Exception:
                    _is_credit_fatal = False
                if _is_credit_fatal and mode == MODE_BACKTEST and _backtest_result_id is not None:
                    # Stop the heartbeat first so its 15s log/_last_active
                    # writes can't race the final row state.
                    try:
                        _heartbeat_stop.set()
                    except (NameError, Exception):
                        pass
                    try:
                        from backtest_critical_abort import (
                            _pause_backtest_on_credit_exhaustion as _bt_credit_pause,
                        )
                        _bt_credit_pause(
                            _backtest_result_id,
                            _outer_err,
                            current_time,
                            _backtest_db_conn,
                        )
                    except Exception as _cp_err:
                        try:
                            _log(f"credit-exhaustion pause handler failed: {_cp_err}", "red")
                        except Exception:
                            pass
                    try:
                        _log(
                            "Backtest paused (insufficient credits) at "
                            f"{current_time}; exiting cleanly — top up credits "
                            "and re-queue to resume.",
                            "yellow",
                        )
                    except Exception:
                        pass
                    try:
                        from intellistock_logger import intellistock_logger as _isl
                        _isl.clear_backtest_log_buffer()
                        _isl.close_backtest_log_file()
                    except Exception:
                        pass
                    import sys as _sys
                    _sys.exit(0)
                if mode == MODE_BACKTEST and _backtest_result_id is not None:
                    # PAUSE flow (2026-05-22): backtest no longer exits on
                    # LLM-critical. backtest_critical_abort.handle() restores
                    # the last-good-bar snapshot, sets BacktestInstances.paused=True
                    # and writes BacktestResults.status='paused_llm_critical'.
                    # The main loop's wait block (~line 9118) then idles the
                    # worker until the operator resumes via the UI.
                    try:
                        from backtest_critical_abort import handle as _bt_handle
                    except Exception as _imp_err:
                        try:
                            _log(f"backtest_critical_abort import failed: {_imp_err}", "red")
                        except Exception:
                            pass
                        _bt_handle = None
                    if _bt_handle is not None:
                        _bt_handle(
                            backtest_id=str(_backtest_result_id),
                            instance_id=str(instance_id),
                            failure=_outer_err,
                        )
                    # CRITICAL (bug fix 2026-05-22): synchronously flip the
                    # LOCAL pause flag. _backtest_paused is normally driven by
                    # the watch_backtest_run_command changefeed thread (see
                    # ~line 2510), which observes the DB write made above by
                    # handle() and propagates it into this module global. But
                    # that's a cross-thread DB roundtrip — the local `continue`
                    # below is much faster and would otherwise run an ENTIRE
                    # next bar iteration before the wait block at ~line 9177
                    # ever sees paused=True. During that wasted iteration:
                    #   - llm_critical_guard._already_raised is still True,
                    #     so every LLM call short-circuits to empty text
                    #   - strategies run with garbage LLM responses against
                    #     the freshly-restored portfolio, possibly producing
                    #     bogus trades
                    # Flipping the local global here closes that race.
                    #
                    # Note: no `global _backtest_paused` declaration here.
                    # This entire outer-except is at MODULE scope (the main
                    # backtest/live loop at line 6263 is a top-level `while`,
                    # NOT inside a def), so a bare assignment already binds
                    # the module attribute. Adding `global` at module top-
                    # level is a SyntaxError when the name has already been
                    # read earlier (e.g. the wait block at line 9177 reads
                    # _backtest_paused before this point).
                    _backtest_paused = True
                    # DO NOT sys.exit — handle() restored snapshot and set paused=True;
                    # the main loop will hit the existing wait block and idle until resume.
                    continue  # re-enter the while loop; the wait block holds the worker
                elif mode == MODE_LIVE:
                    try:
                        from live_critical_abort import handle as _lv_handle
                    except Exception as _imp_err:
                        try:
                            _log(f"live_critical_abort import failed: {_imp_err}", "red")
                        except Exception:
                            pass
                        _lv_handle = None
                    if _lv_handle is not None:
                        _lv_handle(instance_id=str(instance_id), failure=_outer_err)
                    # Live still exits — live mode can't pause (no snapshot model).
                    # Distinct exit code so engine knows this was an operator-actionable
                    # LLM failure, not a generic crash. Exit code 7 chosen to avoid
                    # collision with argparse pre-check failure (exit 2) and existing
                    # exit codes 0,1,2,3,4,5,6 used elsewhere in broker.py.
                    import sys as _sys
                    _sys.exit(7)
                else:
                    # Defensive: backtest mode but no result_id — log loudly so operator notices.
                    try:
                        _log(
                            "LLM critical fired but _backtest_result_id is None; "
                            "no BacktestResults update possible",
                            "red",
                        )
                    except Exception:
                        pass
            except Exception as _abort_err:
                try:
                    _log(f"critical-guard handler crashed: {_abort_err}", "red")
                except Exception:
                    pass

        # Not LLM critical — we caught BaseException, so KeyboardInterrupt/
        # SystemExit also land here. Re-raise non-Exception cases so they
        # propagate to Python's default handler (preserves Ctrl-C semantics
        # and explicit sys.exit() codes from downstream code).
        if not isinstance(_outer_err, Exception):
            raise

        # Existing generic crash path for normal Exceptions.
        loop_err = _outer_err
        if mode == MODE_BACKTEST and _backtest_result_id is not None:
            progress_pct_crash = None
            try:
                if current_time is not None and start_dt is not None and end_dt is not None:
                    total_sec = (end_dt - start_dt).total_seconds()
                    elapsed_sec = (current_time - start_dt).total_seconds()
                    progress_pct_crash = min(100.0, max(0.0, (elapsed_sec / total_sec * 100.0) if total_sec > 0 else 0))
            except Exception:
                pass
            _backtest_save_error_and_exit(loop_err, progress_pct_crash)
        raise

# If socket thread set shutdown_requested (e.g. DB says do not keep alive), exit
try:
    if mode == MODE_LIVE:
        _shutdown_live_trading_state(reason="clean shutdown (runCommand=False)")
except Exception:
    pass
sys.exit(0)

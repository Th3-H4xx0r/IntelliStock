# Copyright (c) 2020 Pranav Krishna — MIT License (see LICENSE)
# Backtest engine: watches BacktestInstances table, runs each backtest in a Docker container,
# then deletes the container and the row when done. Equity credentials are
# resolved by broker.py from the exact encrypted BrokerageAccounts link.

import concurrent.futures
import os
import queue
import re
import sys
import threading
import time

# CPU gating: don't start a new backtest container when the host is already heavily loaded.
# On Linux Docker, /proc/stat reflects the real host CPU (containers share the host kernel).
CPU_GATE_MAX_PCT = 90.0   # wait before starting if host CPU is at or above this %
CPU_GATE_POLL_SEC = 10    # seconds between CPU re-checks while waiting
DIFFICULTY_TO_CPU_FACTOR = 3.5   # multiply avg strategy difficulty (1-10) by this to estimate CPU %
DEFAULT_DIFFICULTY = 3.0          # when strategy cannot be resolved, use this for logging
# No fixed concurrency cap: run as many containers as CPU headroom allows (projected CPU < 90%).
MAX_POOL_WORKERS = 64             # max threads in pool; actual concurrency is limited by CPU gate
# Cap concurrent backtests that have any substrategy with difficulty >= 8 (heavy CPU load).
HIGH_DIFFICULTY_THRESHOLD = 8.0
MAX_CONCURRENT_HIGH_DIFFICULTY = 1

# Run from backend/engines/; backend is parent dir for imports and cwd
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, '.env'))
load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))

from rethinkdb import RethinkDB
from intellistock_logger import intellistock_logger

r = RethinkDB()
DB_NAME = 'IntelliStock'
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

# BacktestInstances schema: id (6-digit int), instance, stocks, start-date,
# end-date, granularity_sec, status (pending|running), run. Legacy non-equity
# rows may still carry key/secret during their compatibility migration.
TABLE_NAME = 'BacktestInstances'

# Queue of backtests to run (FIFO); changefeed thread pushes, main thread consumes.
_backtest_queue = queue.Queue()
# High-difficulty rows waiting for a slot (preserve order); we run these when n_high < MAX.
_deferred_high_difficulty = []
_deferred_lock = threading.Lock()
# Count of backtests currently running that have a substrategy with difficulty >= HIGH_DIFFICULTY_THRESHOLD.
# Lock is only held briefly (read or +/- 1); never hold across queue.get, sleep, or I/O to avoid deadlock.
_high_difficulty_running = 0
_high_difficulty_lock = threading.Lock()
# Dedup: track row IDs that are queued or actively being launched to prevent double-spawn.
_queued_or_active_ids: set = set()
_queued_or_active_lock = threading.Lock()


# ---------------------------------------------------------------------------
# CPU gate — measured on the real host via /proc/stat (works inside Docker)
# ---------------------------------------------------------------------------

def _read_proc_stat_cpu():
    """Return (idle_jiffies, total_jiffies) from /proc/stat, or None on failure.
    On Linux Docker containers /proc/stat is the host kernel's file, so this
    always reflects real host CPU — no special flags or mounts needed."""
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return None
        vals = [int(x) for x in parts[1:]]
        # user nice system idle iowait irq softirq steal guest guest_nice
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)
    except Exception:
        return None


def _host_cpu_pct(interval: float = 0.5) -> float:
    """Sample host CPU utilisation over `interval` seconds (0–100).
    Primary: /proc/stat (works inside Docker on Linux — shares host kernel).
    Fallback: psutil, then 0.0 (safe default — never blocks)."""
    snap1 = _read_proc_stat_cpu()
    if snap1 is not None:
        time.sleep(interval)
        snap2 = _read_proc_stat_cpu()
        if snap2 is not None:
            idle1, total1 = snap1
            idle2, total2 = snap2
            diff_total = total2 - total1
            if diff_total > 0:
                return 100.0 * (1.0 - (idle2 - idle1) / diff_total)
    try:
        import psutil
        return psutil.cpu_percent(interval=interval)
    except Exception:
        return 0.0


def _wait_for_cpu_headroom(row_id, avg_difficulty: float, est_cpu_pct: float):
    """Block until projected CPU (host + this backtest's est) is below CPU_GATE_MAX_PCT.
    Allows as many concurrent containers as CPU headroom permits."""
    while True:
        cpu = _host_cpu_pct()
        projected = cpu + est_cpu_pct
        if projected < CPU_GATE_MAX_PCT:
            intellistock_logger.log(
                "Backtest %s: projected CPU %.1f%% (host %.1f%% + est %.1f%%) < %.0f%% — proceeding. Avg difficulty=%.1f." % (
                    row_id, projected, cpu, est_cpu_pct, CPU_GATE_MAX_PCT, avg_difficulty),
                "green", service="BACKTEST_ENGINE",
            )
            return
        intellistock_logger.log(
            "[CPU gate] Backtest %s: projected %.1f%% (host %.1f%% + est +%.1f%%) ≥ %.0f%% — "
            "waiting %ds before re-checking." % (
                row_id, projected, cpu, est_cpu_pct, CPU_GATE_MAX_PCT, CPU_GATE_POLL_SEC),
            "yellow", service="BACKTEST_ENGINE",
        )
        time.sleep(CPU_GATE_POLL_SEC)


# ---------------------------------------------------------------------------
# Strategy difficulty (for logging and CPU context)
# ---------------------------------------------------------------------------
_strategy_difficulty = {}  # normalized strategy_id -> float 1-10


def _normalize_strategy_id(name: str) -> str:
    """Canonical form for strategy names so DB 'TieredRisk', 'Tiered Risk', 'tiered_risk' all match. CamelCase -> snake_case, lower, spaces -> underscore."""
    if not name or not isinstance(name, str):
        return ""
    s = name.strip().replace(" ", "_")
    # Insert underscore before uppercase letters, then lower (e.g. TieredRisk -> tiered_risk)
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s).strip("_")
    return s or ""


def _load_strategy_difficulties():
    """Scan backend/strategies/*.py for # DIFFICULTY and INTELLISTOCK_SCHEMA; populate _strategy_difficulty at startup."""
    global _strategy_difficulty
    import json
    strategies_dir = os.path.join(BACKEND_DIR, "strategies")
    if not os.path.isdir(strategies_dir):
        return
    loaded = {}
    for fname in os.listdir(strategies_dir):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        fpath = os.path.join(strategies_dir, fname)
        strategy_id = None
        difficulty = None
        try:
            with open(fpath, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i > 10:
                        break
                    line = line.strip()
                    if line.startswith("# INTELLISTOCK_SCHEMA:"):
                        try:
                            schema_json = line[len("# INTELLISTOCK_SCHEMA:"):].strip()
                            schema = json.loads(schema_json)
                            raw_id = (schema.get("strategy") or "").strip()
                            if raw_id:
                                strategy_id = _normalize_strategy_id(raw_id)
                        except Exception:
                            pass
                    elif line.startswith("# DIFFICULTY:"):
                        try:
                            difficulty = float(line[len("# DIFFICULTY:"):].strip())
                        except Exception:
                            pass
        except Exception:
            continue
        if strategy_id and difficulty is not None:
            loaded[strategy_id] = difficulty
    _strategy_difficulty = loaded
    intellistock_logger.log(
        "Loaded %d strategy difficulty scores (1-10) from strategies/." % len(loaded),
        "cyan", service="BACKTEST_ENGINE",
    )


def _get_instance_doc(conn, instance_id):
    """Return instance document by id. Tries string and int so BacktestInstances.instance (string) matches Instances.id (int or string)."""
    if instance_id is None:
        return None
    inst = r.db(DB_NAME).table("Instances").get(instance_id).run(conn)
    if inst is not None:
        return inst
    try:
        return r.db(DB_NAME).table("Instances").get(int(instance_id)).run(conn)
    except (TypeError, ValueError):
        return None


def _resolve_data_brokerage_creds(conn, instance_id):
    """If the instance has a separate market-data Alpaca brokerage linked
    (`alpaca_data_brokerage_id`), decrypt its key/secret and return them.

    Backtests run against `data.alpaca.markets` (bars + news) — paper
    accounts get 401 on those endpoints. Operators link a LIVE account
    via the Market-Data Source UI to fix this; here we surface those
    creds so the backtest container's KEY/SECRET (and therefore every
    strategy that calls Alpaca data) point at the right account.

    Returns (key, secret) or (None, None) if no link, decrypt fails, or
    the linked row isn't an Alpaca account.
    """
    if not instance_id:
        return None, None
    try:
        inst = _get_instance_doc(conn, instance_id)
        if not inst:
            return None, None
        data_bid = inst.get("alpaca_data_brokerage_id") or None
        if not data_bid:
            return None, None
        b_doc = r.db(DB_NAME).table("BrokerageAccounts").get(data_bid).run(conn)
        if not b_doc or (b_doc.get("brokerage_type") or "").strip().lower() != "alpaca":
            intellistock_logger.log(
                f"alpaca_data_brokerage_id {data_bid} on instance {instance_id} "
                f"is not an Alpaca account; ignoring.",
                "yellow", service="BACKTEST_ENGINE",
            )
            return None, None
        try:
            from secret_store import decrypt
            k = decrypt(b_doc.get("alpaca_key")) or None
            s = decrypt(b_doc.get("alpaca_secret")) or None
        except Exception as e:
            intellistock_logger.log(
                f"Decrypt failed for data BrokerageAccount {data_bid} "
                f"(is INTELLISTOCK_CRED_KEY set?): {type(e).__name__}: {e}",
                "red", service="BACKTEST_ENGINE",
            )
            return None, None
        if k and s:
            intellistock_logger.log(
                f"Backtest using data brokerage {data_bid} "
                f"(paper={bool(b_doc.get('alpaca_paper', False))}) for KEY/SECRET",
                "cyan", service="BACKTEST_ENGINE",
            )
            return k, s
        return None, None
    except Exception as e:
        intellistock_logger.log(
            f"_resolve_data_brokerage_creds error: {type(e).__name__}: {e}",
            "red", service="BACKTEST_ENGINE",
        )
        return None, None


def _backtest_avg_difficulty(conn, row) -> float:
    """Resolve instance -> strategy -> sub-strategies and return average difficulty (1-10). Uses DEFAULT_DIFFICULTY if unknown."""
    instance_id = row.get("instance")
    if not instance_id:
        return DEFAULT_DIFFICULTY
    try:
        inst = _get_instance_doc(conn, instance_id)
        if not inst:
            return DEFAULT_DIFFICULTY
        strategy_id = inst.get("strategy_id")
        if strategy_id is None:
            return DEFAULT_DIFFICULTY
        strat_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
        if not strat_doc:
            return DEFAULT_DIFFICULTY
        subs = strat_doc.get("strategies") or []
        if not subs:
            return DEFAULT_DIFFICULTY
        total = 0.0
        for sub in subs:
            sid = _normalize_strategy_id(sub.get("strategy") or "")
            total += _strategy_difficulty.get(sid, DEFAULT_DIFFICULTY)
        return total / len(subs)
    except Exception:
        return DEFAULT_DIFFICULTY


def _backtest_high_difficulty_trigger(conn, row):
    """Return (is_high: bool, trigger_name: str|None). Only True when a substrategy is in cache with value >= threshold."""
    instance_id = row.get("instance")
    if not instance_id:
        return False, None
    try:
        inst = _get_instance_doc(conn, instance_id)
        if not inst:
            return False, None
        strategy_id = inst.get("strategy_id")
        if strategy_id is None:
            return False, None
        strat_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
        if not strat_doc:
            return False, None
        subs = strat_doc.get("strategies") or []
        for sub in subs:
            raw_name = (sub.get("strategy") or "").strip()
            sid = _normalize_strategy_id(raw_name)
            if not sid:
                continue
            d = _strategy_difficulty.get(sid)
            if d is not None and d >= HIGH_DIFFICULTY_THRESHOLD:
                return True, raw_name or sid
        return False, None
    except Exception:
        return False, None


def _backtest_has_high_difficulty_sub(conn, row) -> bool:
    is_high, _ = _backtest_high_difficulty_trigger(conn, row)
    return is_high


def get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def wait_for_rethinkdb(max_attempts=30, delay=2):
    """
    Wait until RethinkDB is ready and BacktestInstances table has a primary replica.
    If RETHINKDB_HOST is 'rethinkdb' (Docker) and connection is refused, tries localhost
    so the same .env works when running the engine outside Docker.
    Returns (conn, True) on success or (None, False) on failure.
    """
    global RETHINKDB_HOST
    for attempt in range(1, max_attempts + 1):
        conn = None
        try:
            conn = get_conn()
            ensure_table(conn)
            list(r.db(DB_NAME).table(TABLE_NAME).limit(1).run(conn))
            return conn, True
        except Exception as e:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            msg = str(e).lower()
            refused = "connection refused" in msg or "errno 111" in msg
            # If running outside Docker with .env set for Docker (host=rethinkdb), try localhost once
            if refused and RETHINKDB_HOST == "rethinkdb" and attempt == 1:
                intellistock_logger.log(
                    "RethinkDB at 'rethinkdb' unreachable (running outside Docker?). Trying localhost...",
                    "yellow", service="BACKTEST_ENGINE",
                )
                RETHINKDB_HOST = "localhost"
                time.sleep(1)
                continue
            if attempt == max_attempts:
                intellistock_logger.log(
                    f"RethinkDB not ready after {max_attempts} attempts: {e}",
                    "red", service="BACKTEST_ENGINE",
                )
                if refused:
                    intellistock_logger.log(
                        "Hint: If running outside Docker, ensure RethinkDB is running (e.g. docker run -p 28015:28015 rethinkdb:2.4) and set RETHINKDB_HOST=localhost in .env",
                        "yellow", service="BACKTEST_ENGINE",
                    )
                return None, False
            if "primary replica" in msg or "not available" in msg or "not ready" in msg:
                intellistock_logger.log(
                    f"RethinkDB/table not ready (attempt {attempt}/{max_attempts}), retrying in {delay}s...",
                    "yellow", service="BACKTEST_ENGINE",
                )
            else:
                intellistock_logger.log(
                    f"RethinkDB connection error (attempt {attempt}/{max_attempts}): {e}",
                    "yellow", service="BACKTEST_ENGINE",
                )
            time.sleep(delay)
    return None, False


RESULTS_TABLE = 'BacktestResults'


def ensure_table(conn):
    """Ensure the BacktestInstances table exists."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if TABLE_NAME not in tables:
        r.db(DB_NAME).table_create(TABLE_NAME).run(conn)
        intellistock_logger.log(f"Created table {TABLE_NAME}", "green", service="BACKTEST_ENGINE")
    # BacktestResults is split across three Postgres tables. ensure_schema is
    # idempotent (CREATE ... IF NOT EXISTS under an advisory lock), so this is
    # a cheap no-op on every boot but keeps the self-heal the table_create
    # bootstrap used to provide on a fresh deploy.
    from db import schema as _db_schema
    _db_schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                     "BacktestProgress"])


def _backtest_container_name(instance_id, row_id):
    safe_inst = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(instance_id))[:30]
    return f"backtest-instance-{safe_inst}-{row_id}"


def _get_docker_client():
    try:
        import docker
        return docker.from_env()
    except Exception as e:
        intellistock_logger.log(f"Docker client error: {e}", "red", service="BACKTEST_ENGINE")
        return None


def _cleanup_stale_backtest_containers():
    client = _get_docker_client()
    if not client:
        return
    try:
        for c in client.containers.list(all=True):
            name = (c.name or "")
            if "backtest-instance" in name:
                try:
                    c.remove(force=True)
                    intellistock_logger.log(f"Removed stale backtest container: {name}", "yellow", service="BACKTEST_ENGINE")
                except Exception as e:
                    intellistock_logger.log(f"Could not remove container {name}: {e}", "yellow", service="BACKTEST_ENGINE")
    except Exception as e:
        intellistock_logger.log(f"Cleanup of stale backtest containers failed: {e}", "yellow", service="BACKTEST_ENGINE")


CONTAINER_HEALTH_CHECK_INTERVAL = 120  # seconds between health checks
# Belt-and-braces catch-up: periodically re-scan for pending rows the
# changefeed missed (e.g. inserted during a RethinkDB outage/restart).
PENDING_SWEEP_INTERVAL_SEC = 300
CONTAINER_LAUNCH_GRACE_SEC = 300      # skip health check for containers launched within this window
# Track launch times to avoid marking freshly-started containers as dead
_container_launch_times: dict = {}  # row_id -> time.time()
_container_launch_times_lock = threading.Lock()

def _check_dead_backtest_containers():
    """Every 2 minutes: for every backtest the DB thinks is running, verify its Docker
    container is actually alive. If it's gone, mark BacktestResults.status='stopped'
    and BacktestInstances.run=False so the AI agent and UI see the correct state.

    2026-07-18: opens its OWN RethinkDB connection per invocation. It previously
    reused main()'s boot-time connection, so a RethinkDB restart left it erroring
    "Connection is closed" every cycle FOREVER (observed after the 09:41 outage) —
    the health check never healed. A fresh conn per 2-min cycle self-heals."""
    docker_client = _get_docker_client()
    if not docker_client:
        return
    conn = None
    try:
        conn = get_conn()
        # Build set of names for all currently-running containers (fast single call)
        running_names = {c.name for c in docker_client.containers.list()}

        # Reconcile only rows that have actually transitioned to running.
        # Pending/deferred rows also carry run=True, but do not have a container
        # yet and must remain queued until a worker slot is available.
        ensure_table(conn)
        running_rows = list(
            r.db(DB_NAME).table(TABLE_NAME)
            .filter(
                r.row["run"].eq(True)
                & r.row["status"].eq("running")
            )
            .pluck("id", "instance", "status")
            .run(conn)
        )
        if not running_rows:
            return

        tables = list(r.db(DB_NAME).table_list().run(conn))
        has_results_table = "BacktestResults" in tables
        now = time.time()

        for row in running_rows:
            if str(row.get("status") or "").strip().lower() != "running":
                continue
            bid        = row.get("id")
            instance_id = str(row.get("instance") or "")
            expected_name = _backtest_container_name(instance_id, bid)

            if expected_name not in running_names:
                # Grace period: skip if container was launched recently (may still be starting)
                with _container_launch_times_lock:
                    launch_time = _container_launch_times.get(bid)
                if launch_time and (now - launch_time) < CONTAINER_LAUNCH_GRACE_SEC:
                    intellistock_logger.log(
                        f"Backtest {bid}: container '{expected_name}' not found but within grace period ({int(now - launch_time)}s since launch) — skipping.",
                        "cyan", service="BACKTEST_ENGINE",
                    )
                    continue
                intellistock_logger.log(
                    f"Backtest {bid}: container '{expected_name}' not found in Docker — marking stopped.",
                    "yellow", service="BACKTEST_ENGINE",
                )
                # Paused runs idle with their container alive; a paused row
                # whose container crashed must survive for operator resume —
                # keep the row (run=False) instead of deleting it.
                is_paused = False
                if has_results_table:
                    try:
                        _res = r.db(DB_NAME).table("BacktestResults").get(bid).pluck("status").run(conn)
                        is_paused = "paused" in str((_res or {}).get("status") or "").lower()
                    except Exception:
                        is_paused = False
                if is_paused:
                    try:
                        r.db(DB_NAME).table(TABLE_NAME).get(bid).update({"run": False}).run(conn)
                    except Exception:
                        pass
                else:
                    # DELETE the queue row: the queue-row contract is
                    # delete-on-completion; leaving dead rows as
                    # status='running'/run=False accumulated 100+ zombies
                    # that clutter every scan (observed 2026-07-18).
                    try:
                        r.db(DB_NAME).table(TABLE_NAME).get(bid).delete().run(conn)
                    except Exception:
                        pass
                    if has_results_table:
                        try:
                            r.db(DB_NAME).table("BacktestResults").get(bid).update({"status": "stopped"}).run(conn)
                        except Exception:
                            pass
                with _queued_or_active_lock:
                    _queued_or_active_ids.discard(bid)
                with _container_launch_times_lock:
                    _container_launch_times.pop(bid, None)
    except Exception as e:
        intellistock_logger.log(f"Container health check failed: {e}", "yellow", service="BACKTEST_ENGINE")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        try:
            docker_client.close()
        except Exception:
            pass


def _get_network(client):
    env_network = os.environ.get('INSTANCE_DOCKER_NETWORK', '').strip()
    if env_network:
        try:
            client.networks.get(env_network)
            return env_network
        except Exception:
            pass
    try:
        container_id = os.environ.get('HOSTNAME') or (open('/etc/hostname').read().strip() if os.path.isfile('/etc/hostname') else None)
        if container_id:
            current = client.containers.get(container_id)
            networks = current.attrs.get('NetworkSettings', {}).get('Networks') or {}
            names = [n for n in networks if n != 'bridge']
            return names[0] if names else (list(networks.keys())[0] if networks else None)
    except Exception:
        pass
    return None


def run_one_backtest(row, avg_difficulty=None, is_high=False):
    row_id = row.get('id')
    instance_id = str(row.get('instance') or '')
    stocks = row.get('stocks') or []
    if not isinstance(stocks, list):
        raw = str(stocks).strip()
        stocks = [s.strip().upper() for s in raw.replace(',', ' ').split() if s.strip()] if raw else []
    else:
        expanded = []
        for s in stocks:
            if not s:
                continue
            raw = str(s).strip()
            expanded.extend([part.strip().upper() for part in raw.replace(',', ' ').split() if part.strip()])
        stocks = expanded
    start_date = (row.get('start-date') or '').strip()
    end_date = (row.get('end-date') or '').strip()
    granularity_sec = row.get('granularity_sec')
    if granularity_sec is None:
        granularity_sec = 60
    try:
        granularity_sec = int(granularity_sec)
    except (TypeError, ValueError):
        granularity_sec = 60
    # Equity secrets must never cross the Docker argv/environment boundary.
    # The spawned broker has RethinkDB access plus INTELLISTOCK_CRED_KEY and
    # resolves the exact linked BrokerageAccounts row itself. Keep the legacy
    # non-equity path byte-for-byte compatible for crypto/Kalshi.
    key = ""
    secret = ""
    non_equity_compatibility = False
    try:
        _conn = get_conn()
        try:
            _instance_doc = _get_instance_doc(_conn, instance_id) or {}
            _kind = str(_instance_doc.get("kind") or "").strip().lower()
            non_equity_compatibility = _kind in {"crypto", "kalshi"}
            if non_equity_compatibility:
                key = (row.get('key') or '').strip()
                secret = (row.get('secret') or '').strip()
                _data_key, _data_secret = _resolve_data_brokerage_creds(_conn, instance_id)
                if _data_key and _data_secret:
                    key, secret = _data_key, _data_secret
        finally:
            try:
                _conn.close()
            except Exception:
                pass
    except Exception as _e:
        # Classification uncertainty is stock-safe: do not pass any credentials.
        key = ""
        secret = ""
        non_equity_compatibility = False
        intellistock_logger.log(
            f"Backtest {row_id}: instance credential mode could not be classified; "
            "launching without argv/env broker credentials.",
            "red", service="BACKTEST_ENGINE",
        )
    try:
        initial_cash = float(row.get('initial_cash') or 100000.0)
        if initial_cash <= 0:
            initial_cash = 100000.0
    except (TypeError, ValueError):
        initial_cash = 100000.0

    symbols = [str(s).strip().upper() for s in stocks if s and str(s).strip()]
    # V7.3: Allow empty symbols for pure discovery mode (Nexus strategy discovers its own tickers)
    if not start_date or not end_date:
        intellistock_logger.log(f"Backtest {row_id}: missing start-date or end-date, skipping", "yellow", service="BACKTEST_ENGINE")
        _remove_row_and_mark_done(row_id)
        return

    time_increment = str(granularity_sec)
    cmd = [
        'python', 'broker.py',
        instance_id, 'backtest',
        start_date, end_date, time_increment,
        key if non_equity_compatibility and key else "NULL",
        secret if non_equity_compatibility and secret else "NULL",
    ] + symbols + ['--initial-cash', str(initial_cash), '--backtest-id', str(row_id)]

    # Crypto fee emulation: pass the resolved taker fee so the PortfolioEmulator
    # fills at the chosen venue's rate instead of the instance's own brokerage.
    try:
        _fee = row.get('emulate_taker_rate')
        if _fee is not None:
            cmd += ['--taker-fee', str(float(_fee))]
    except (TypeError, ValueError):
        pass

    name = _backtest_container_name(instance_id, row_id)
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    rethink_host = os.environ.get('INSTANCE_RETHINKDB_HOST', RETHINKDB_HOST)
    if rethink_host in ('localhost', '127.0.0.1'):
        rethink_host = os.environ.get('DOCKER_HOST_RETHINKDB', 'host.docker.internal')
        intellistock_logger.log(
            f"Backtest {row_id}: RETHINKDB_HOST is localhost; passing {rethink_host} to container.",
            "cyan", service="BACKTEST_ENGINE",
        )
    if os.environ.get('BACKTEST_RETHINKDB_VIA_HOST_DOCKER', '').lower() in ('1', 'true', 'yes'):
        rethink_host = os.environ.get('DOCKER_HOST_RETHINKDB', 'host.docker.internal')
    env = {
        'RETHINKDB_HOST': rethink_host,
        'RETHINKDB_PORT': str(RETHINKDB_PORT),
        'USE_NNPACK': '0',
        'GLOG_minloglevel': '2',
    }
    # Phase α.3 (2026-05-18, BT109429 follow-up): forward determinism env
    # vars into the spawned broker container. PYTHONHASHSEED must be set
    # in the container env BEFORE python starts (the interpreter reads it
    # at startup; setting it from inside the process is too late) — passing
    # it via the `env` dict to `docker run` does exactly that. BACKTEST_SEED
    # is also forwarded so operators can pin a specific RNG seed across
    # paired re-runs of the same backtest_id.
    # Default PYTHONHASHSEED=0 (+ forward BACKTEST_SEED when set) so the spawned
    # broker gets deterministic set iteration even if the deployment env omits
    # it. Helper is unit-tested in tests/test_phase_alpha_variance.py.
    from _phase_alpha_helpers import backtest_determinism_env_vars
    env.update(backtest_determinism_env_vars(os.environ))
    if avg_difficulty is not None:
        env['BACKTEST_DIFFICULTY'] = str(avg_difficulty)
    neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    env['NEO4J_URI'] = neo4j_uri
    env['NEO4J_USER'] = os.environ.get('NEO4J_USER', 'neo4j')
    env['NEO4J_PASSWORD'] = os.environ.get('NEO4J_PASSWORD', 'intellistock')
    if non_equity_compatibility:
        # Compatibility only: equity backtests never receive KEY/SECRET.
        if key:
            env['KEY'] = key
        else:
            env_key = (os.environ.get('APCA_API_KEY_ID') or os.environ.get('KEY') or '').strip()
            if env_key:
                env['KEY'] = env_key
        if secret:
            env['SECRET'] = secret
        else:
            env_secret = (os.environ.get('APCA_API_SECRET_KEY') or os.environ.get('SECRET') or '').strip()
            if env_secret:
                env['SECRET'] = env_secret
    # LLM keys for strategies that need them (e.g. Earnings)
    gemini_key = (os.environ.get('GEMINI_API_KEY') or '').strip()
    if gemini_key:
        env['GEMINI_API_KEY'] = gemini_key
    # 2026-04-26: intentionally NOT forwarding DEEPSEEK_API_KEY into the spawned
    # backtest broker container. The Nexus strategy's `_hierarchy_llm_config`
    # (graph_nexus_analysis.py:~10579) hardcodes provider="deepseek" for the
    # private-entity bridge and resolves the key purely from env via
    # `_default_api_key_for_provider("deepseek")`. With the key forwarded, the
    # bridge fires ~15 deepseek-reasoner calls per bar (~5s each) — non-
    # deterministic LLM output AND ~$3-5 per backtest run. The bridge's
    # built-in kill switch `if not api_key: return None` (line ~10615) makes
    # it a silent no-op when the key is absent — restoring the eb20269/#700872
    # behavior. Other strategies that legitimately need DeepSeek can read it
    # from a strategy-row config (post-70f745c routes per-strategy keys).
    # R19 (2026-04-25): forward INTELLISTOCK_CRED_KEY into ephemeral broker
    # container so secret_store.decrypt() can resolve Fernet-encrypted data
    # brokerage credentials. Without this, _resolve_data_brokerage_creds_now()
    # in broker.py raises "INTELLISTOCK_CRED_KEY env var is required" and the
    # broker silently falls back to paper-trading creds, causing every Alpaca
    # bars fetch to 401. Mirrors the live-instance forward in
    # backend/server.py:start_instance_container.
    cred_key = (os.environ.get('INTELLISTOCK_CRED_KEY') or '').strip()
    if cred_key:
        env['INTELLISTOCK_CRED_KEY'] = cred_key
    else:
        intellistock_logger.log(
            f"Backtest {row_id}: INTELLISTOCK_CRED_KEY missing in backtest-engine env. "
            "Spawned broker WILL fail to decrypt Fernet-encrypted data brokerage. "
            "Set the env var on the backtest-engine service (Dockploy → service env).",
            "red", service="BACKTEST_ENGINE",
        )
    allow_legacy = (os.environ.get('ALLOW_LEGACY_ENV_CREDS') or '').strip()
    if allow_legacy and non_equity_compatibility:
        env['ALLOW_LEGACY_ENV_CREDS'] = allow_legacy
    # Pass log directory to broker so it writes full logs to the persistent volume
    log_dir = os.environ.get('BACKTEST_LOG_DIR', '/app/backtest_logs')
    env['BACKTEST_LOG_DIR'] = log_dir

    # Named volume for persistent backtest log files
    log_volume_name = os.environ.get('BACKTEST_LOG_VOLUME', 'backtest_logs')
    container_volumes = {log_volume_name: {'bind': log_dir, 'mode': 'rw'}}

    # Share the host's claude login into the spawned broker container so
    # strategies (graph_nexus, earnings, ml_news, …) that select a
    # claude-cli model can hit the operator's Pro/Max subscription. The
    # backtest-engine service itself receives this path via the
    # CLAUDE_HOST_HOME env (see docker-compose.yml); if it's unset, skip
    # the mount and let any claude-cli strategy call surface a clear
    # "Not logged in" error.
    claude_host_home = (os.environ.get('CLAUDE_HOST_HOME') or '').strip()
    if claude_host_home:
        # Read-only mount: CC only needs to read the operator's OAuth
        # state, never to mutate it during a backtest.
        container_volumes[claude_host_home] = {'bind': '/root/.claude', 'mode': 'ro'}
        # CC also requires $HOME/.claude.json (sibling of .claude/) in
        # MCP mode — missing it surfaces a misleading "Not logged in"
        # error even with valid creds in .claude/. Mount it too when
        # the operator has configured CLAUDE_HOST_CONFIG.
        claude_host_config = (os.environ.get('CLAUDE_HOST_CONFIG') or '').strip()
        if claude_host_config:
            container_volumes[claude_host_config] = {'bind': '/root/.claude.json', 'mode': 'ro'}

    # Share the codex-cli OAuth state via the named volume that the api
    # service writes to during device-code login. Unlike claude (which
    # bind-mounts the host's ~/.claude RO), codex auth lives in a
    # container-local Docker volume so the web-UI login flow can write
    # to it without ever touching the host filesystem. RW because codex
    # may refresh tokens during a long-running call.
    container_volumes['codex_auth'] = {'bind': '/root/.codex', 'mode': 'rw'}

    try:
        client = _get_docker_client()
        if not client:
            intellistock_logger.log(f"Backtest {row_id}: Docker not available", "red", service="BACKTEST_ENGINE")
            _remove_row_and_mark_done(row_id)
            return
        try:
            client.images.get(image)
        except Exception:
            intellistock_logger.log(
                f"Image '{image}' not found. Build first: docker build -t {image} ./backend",
                "red", service="BACKTEST_ENGINE",
            )
            _remove_row_and_mark_done(row_id)
            return
        network = _get_network(client)
        # Check for existing container with same name — if it's running, abort to prevent double-spawn
        try:
            existing = client.containers.get(name)
            if existing.status == 'running':
                intellistock_logger.log(
                    f"Backtest {row_id}: container '{name}' is ALREADY RUNNING — aborting duplicate launch.",
                    "red", service="BACKTEST_ENGINE",
                )
                _remove_row_and_mark_done(row_id)
                return
            # Container exists but isn't running (exited/created) — safe to remove
            existing.remove(force=True)
            intellistock_logger.log(f"Backtest {row_id}: removed stale container {name} before starting", "yellow", service="BACKTEST_ENGINE")
        except Exception:
            pass  # container doesn't exist, proceed normally
        intellistock_logger.log(f"Backtest {row_id}: starting container {name} ({instance_id}, {start_date} to {end_date}, {len(symbols)} tickers)", "green", service="BACKTEST_ENGINE")
        client.containers.run(
            image,
            command=cmd,
            name=name,
            environment=env,
            network=network,
            volumes=container_volumes,
            detach=False,
            remove=True,
        )
        intellistock_logger.log(f"Backtest {row_id}: finished successfully", "green", service="BACKTEST_ENGINE")
    except Exception as e:
        err_str = str(e)
        intellistock_logger.log(f"Backtest {row_id}: container error: {e}", "red", service="BACKTEST_ENGINE")
        if "137" in err_str or "exit status 137" in err_str:
            intellistock_logger.log(
                "Exit 137 usually means the container was killed (e.g. out of memory).",
                "yellow", service="BACKTEST_ENGINE",
            )
        try:
            client = _get_docker_client()
            if client:
                c = client.containers.get(name)
                c.remove(force=True)
        except Exception:
            pass
    finally:
        _remove_row_and_mark_done(row_id)


def _remove_row_and_mark_done(row_id):
    conn = None
    try:
        conn = get_conn()
        r.db(DB_NAME).table(TABLE_NAME).get(row_id).delete().run(conn)
        intellistock_logger.log(f"Backtest {row_id}: row removed from queue", "white", service="BACKTEST_ENGINE")
    except Exception as e:
        intellistock_logger.log(f"Backtest {row_id}: failed to delete row: {e}", "red", service="BACKTEST_ENGINE")
    finally:
        with _queued_or_active_lock:
            _queued_or_active_ids.discard(row_id)
        with _container_launch_times_lock:
            _container_launch_times.pop(row_id, None)
        if conn:
            conn.close()


def _is_replica_unavailable_error(e):
    msg = str(e).lower()
    return "primary replica" in msg or "not available" in msg or "cannot subscribe" in msg


def _changefeed_run_once(conn):
    for change in r.db(DB_NAME).table(TABLE_NAME).changes().run(conn):
        new_val = change.get('new_val')
        if new_val is None:
            continue
        status = (new_val.get('status') or '').strip().lower()
        if status == 'pending':
            row_id = new_val.get('id')
            with _queued_or_active_lock:
                if row_id in _queued_or_active_ids:
                    intellistock_logger.log(
                        "Backtest %s: changefeed duplicate suppressed (already queued/active)." % row_id,
                        "yellow", service="BACKTEST_ENGINE",
                    )
                    continue
                _queued_or_active_ids.add(row_id)
            _backtest_queue.put(new_val)


def _sweep_pending(conn, label="sweep"):
    """Queue every pending row not already queued/active. The changefeed only
    delivers events it was connected for — a row inserted while RethinkDB was
    down/restarting (or the feed reconnecting) is otherwise ORPHANED until an
    engine restart. Called at boot, on every changefeed (re)connect, and
    periodically from the main loop. Dedup-guarded: never double-queues."""
    try:
        cursor = r.db(DB_NAME).table(TABLE_NAME).filter(
            r.row['status'].eq('pending')
        ).order_by('id').run(conn)
        picked = 0
        for row in cursor:
            row_id = row.get('id')
            with _queued_or_active_lock:
                if row_id in _queued_or_active_ids:
                    continue
                _queued_or_active_ids.add(row_id)
            _backtest_queue.put(row)
            picked += 1
        if picked:
            intellistock_logger.log(
                f"Pending sweep ({label}): queued {picked} missed backtest(s).",
                "green", service="BACKTEST_ENGINE",
            )
        return picked
    except Exception as e:
        intellistock_logger.log(f"Pending sweep ({label}) error: {e}", "yellow", service="BACKTEST_ENGINE")
        return 0


def _changefeed_worker():
    delay = 5
    while True:
        conn = None
        try:
            conn = get_conn()
            # Catch-up BEFORE re-entering the feed: rows inserted while the
            # feed was down never produce an event on the new feed.
            _sweep_pending(conn, label="changefeed reconnect")
            _changefeed_run_once(conn)
        except Exception as e:
            if _is_replica_unavailable_error(e):
                intellistock_logger.log(
                    f"BacktestInstances changefeed (replica): {e}. Reconnecting in {delay}s...",
                    "yellow", service="BACKTEST_ENGINE",
                )
            else:
                intellistock_logger.log(
                    f"BacktestInstances changefeed error: {e}. Reconnecting in {delay}s...",
                    "red", service="BACKTEST_ENGINE",
                )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        time.sleep(delay)


def _ensure_backtest_result_row(conn, row, status):
    row_id = row.get('id')
    from datetime import datetime
    instance_id = row.get('instance')
    try:
        instance_id_int = int(instance_id) if instance_id is not None else None
    except (TypeError, ValueError):
        instance_id_int = None
    stub = {
        'id': row_id,
        'backtest_id': row_id,
        'status': status,
        'progress': 0,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'instance_id': instance_id_int,
        'strategy_id': None,
        'pnl': None,
        'pnl_percent': None,
        'start_date': row.get('start-date'),
        'end_date': row.get('end-date'),
        'tickers': row.get('stocks') or [],
        'time_elapsed_seconds': None,
        'portfolio_value_history': [],
        'backtest_trades': [],
        'backtest_prices': [],
        'logs': [],
    }
    # ``stub`` keeps its four empty arrays: they are the legacy contract that
    # portfolio_value_history / backtest_trades / backtest_prices / logs exist
    # from the first read. write_stub strips them out of the metadata row and
    # assemble() puts them back (backtest_result_store._ALWAYS_PRESENT).
    import backtest_result_store as _brs
    _brs.write_stub(stub)


def _load_initial_queue(conn):
    """Load only pending rows. Running rows are already being executed by brokers;
    re-queuing them would cause duplicate container launches (409 Conflict).
    Delegates to the dedup-guarded sweep (set is empty at boot => identical)."""
    _sweep_pending(conn, label="startup")


def _harvest_completed(active_futures):
    """Pop completed backtest futures, decrement _high_difficulty_running for high-difficulty ones, and consume .result()."""
    global _high_difficulty_running
    for f in list(active_futures):
        if f.done():
            entry = active_futures.pop(f, (None, False))
            rid, is_high = entry if isinstance(entry, tuple) else (entry, False)
            if is_high:
                with _high_difficulty_lock:
                    if _high_difficulty_running > 0:
                        _high_difficulty_running -= 1
            try:
                f.result()
            except Exception as e:
                intellistock_logger.log(
                    "Backtest %s: thread raised: %s" % (rid, e),
                    "red", service="BACKTEST_ENGINE",
                )


def main():
    global _high_difficulty_running
    intellistock_logger.log(
        "Backtest engine starting (queue mode; concurrency limited by CPU — projected < %.0f%%)." % CPU_GATE_MAX_PCT,
        "green", service="BACKTEST_ENGINE",
    )
    _load_strategy_difficulties()
    _cleanup_stale_backtest_containers()
    conn, ok = wait_for_rethinkdb()
    if not ok or not conn:
        return
    try:
        _load_initial_queue(conn)
        changefeed_thread = threading.Thread(target=_changefeed_worker, daemon=True)
        changefeed_thread.start()
        active_futures = {}  # future -> (row_id, is_high) for harvest and high-difficulty count
        _last_health_check = time.time()
        _last_pending_sweep = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_POOL_WORKERS) as executor:
            while True:
                _harvest_completed(active_futures)

                # Periodic Docker container health check (every 2 minutes).
                # Opens its own conn per cycle — self-heals after DB restarts.
                now = time.time()
                if now - _last_health_check >= CONTAINER_HEALTH_CHECK_INTERVAL:
                    _last_health_check = now
                    try:
                        _check_dead_backtest_containers()
                    except Exception as _hc_err:
                        intellistock_logger.log(f"Health check error: {_hc_err}", "yellow", service="BACKTEST_ENGINE")

                # Periodic pending-row sweep (belt-and-braces vs changefeed gaps).
                if now - _last_pending_sweep >= PENDING_SWEEP_INTERVAL_SEC:
                    _last_pending_sweep = now
                    _sw_conn = None
                    try:
                        _sw_conn = get_conn()
                        _sweep_pending(_sw_conn, label="periodic")
                    except Exception as _sw_err:
                        intellistock_logger.log(f"Periodic sweep error: {_sw_err}", "yellow", service="BACKTEST_ENGINE")
                    finally:
                        if _sw_conn is not None:
                            try:
                                _sw_conn.close()
                            except Exception:
                                pass
                with _high_difficulty_lock:
                    n_high = _high_difficulty_running

                # Prefer running a deferred high-difficulty row when we have a slot (preserves order, uses machine fully)
                row = None
                is_high = False
                with _deferred_lock:
                    if _deferred_high_difficulty and n_high < MAX_CONCURRENT_HIGH_DIFFICULTY:
                        row = _deferred_high_difficulty.pop(0)
                        is_high = True

                if row is None:
                    try:
                        row = _backtest_queue.get(timeout=CPU_GATE_POLL_SEC)
                    except queue.Empty:
                        if _deferred_high_difficulty:
                            time.sleep(CPU_GATE_POLL_SEC)
                        continue
                    except (KeyboardInterrupt, SystemExit):
                        break
                    conn_here = None
                    try:
                        conn_here = get_conn()
                        ensure_table(conn_here)
                        is_high, trigger_name = _backtest_high_difficulty_trigger(conn_here, row)
                        if is_high and n_high >= MAX_CONCURRENT_HIGH_DIFFICULTY:
                            with _deferred_lock:
                                _deferred_high_difficulty.append(row)
                            msg = "Backtest %s: high-difficulty (>=%.0f); %d slots in use — deferred, running other queued items." % (
                                row.get("id"), HIGH_DIFFICULTY_THRESHOLD, n_high)
                            if trigger_name:
                                d_val = _strategy_difficulty.get(_normalize_strategy_id(trigger_name))
                                if d_val is not None:
                                    msg += " (sub '%s' = %.0f)" % (trigger_name, d_val)
                            intellistock_logger.log(msg, "cyan", service="BACKTEST_ENGINE")
                            if conn_here:
                                try:
                                    conn_here.close()
                                except Exception:
                                    pass
                            continue
                    except Exception as e:
                        intellistock_logger.log(f"Backtest {row.get('id')}: failed before launch: {e}", "red", service="BACKTEST_ENGINE")
                        if conn_here:
                            try:
                                conn_here.close()
                            except Exception:
                                pass
                        try:
                            _backtest_queue.put(row)
                        except Exception:
                            pass
                        continue
                else:
                    conn_here = None
                    try:
                        conn_here = get_conn()
                        ensure_table(conn_here)
                    except Exception as e:
                        intellistock_logger.log(f"Backtest {row.get('id')}: failed to get conn for deferred: {e}", "red", service="BACKTEST_ENGINE")
                        with _deferred_lock:
                            _deferred_high_difficulty.insert(0, row)
                        continue

                row_id = row.get('id')
                try:
                    # Only transition pending -> running so we never start the same backtest twice (avoid duplicate containers / 409)
                    update_result = r.db(DB_NAME).table(TABLE_NAME).filter(
                        r.row['id'].eq(row_id) & r.row['status'].eq('pending')
                    ).update({'status': 'running', 'run': True}).run(conn_here)
                    if (update_result.get('replaced') or 0) < 1:
                        intellistock_logger.log(
                            "Backtest %s: skipped (already running or removed)." % row_id,
                            "yellow", service="BACKTEST_ENGINE",
                        )
                        with _queued_or_active_lock:
                            _queued_or_active_ids.discard(row_id)
                        if conn_here:
                            try:
                                conn_here.close()
                            except Exception:
                                pass
                        continue
                    _ensure_backtest_result_row(conn_here, row, 'pending')
                    _ensure_backtest_result_row(conn_here, row, 'running')
                    avg_difficulty = _backtest_avg_difficulty(conn_here, row)  # per-row: instance -> strategy -> subs
                    import backtest_result_store as _brs_engine
                    _brs_engine.write_difficulty(row_id, avg_difficulty)
                    # Edit #backtests Discord message from Queued → Running (broker will keep editing with progress/P&L)
                    try:
                        from interactive_utils import action_enqueue_discord_edit
                        tickers_str = ", ".join((row.get("stocks") or [])[:8])
                        if row.get("stocks") and len(row["stocks"]) > 8:
                            tickers_str += " (+%d)" % (len(row["stocks"]) - 8)
                        period_str = "%s → %s" % (row.get("start-date") or "—", row.get("end-date") or "—")
                        diff_val = "%.1f" % avg_difficulty
                        if is_high:
                            diff_val += " (HIGH USAGE)"
                        action_enqueue_discord_edit(
                            conn_here, "backtests", str(row_id),
                            content=None,
                            embed={
                                "title": "Backtest Running",
                                "description": "Backtest started; progress and P&L will update as it runs.",
                                "color": 0xF39C12,
                                "fields": [
                                    {"name": "ID", "value": str(row_id), "inline": True},
                                    {"name": "Instance", "value": str(row.get("instance") or "—"), "inline": True},
                                    {"name": "Status", "value": "Running", "inline": True},
                                    {"name": "Difficulty", "value": diff_val, "inline": True},
                                    {"name": "Progress", "value": "0%", "inline": True},
                                    {"name": "P&L", "value": "—", "inline": True},
                                    {"name": "Period", "value": period_str, "inline": False},
                                    {"name": "Tickers", "value": tickers_str or "—", "inline": False},
                                ],
                            },
                        )
                    except Exception:
                        pass
                    est_cpu_pct = avg_difficulty * DIFFICULTY_TO_CPU_FACTOR
                    host_cpu_now = _host_cpu_pct()
                    projected_now = host_cpu_now + est_cpu_pct
                    intellistock_logger.log(
                        "Backtest %s: dequeued. Avg difficulty=%.1f, est CPU=%.1f%%. Host CPU=%.1f%%, projected=%.1f%% (limit %.0f%%). %s (%d running)%s" % (
                            row_id, avg_difficulty, est_cpu_pct, host_cpu_now, projected_now, CPU_GATE_MAX_PCT,
                            "Proceeding." if projected_now < CPU_GATE_MAX_PCT else "Waiting for CPU headroom...",
                            len(active_futures), " [high-difficulty]" if is_high else ""),
                        "cyan", service="BACKTEST_ENGINE",
                    )
                except Exception as e:
                    intellistock_logger.log(f"Backtest {row_id}: failed to set status running: {e}", "red", service="BACKTEST_ENGINE")
                    if conn_here:
                        try:
                            conn_here.close()
                        except Exception:
                            pass
                    try:
                        if is_high:
                            with _deferred_lock:
                                _deferred_high_difficulty.insert(0, row)
                        else:
                            _backtest_queue.put(row)
                    except Exception:
                        pass
                    continue
                finally:
                    if conn_here:
                        try:
                            conn_here.close()
                        except Exception:
                            pass
                _wait_for_cpu_headroom(row_id, avg_difficulty, est_cpu_pct)
                host_cpu_final = _host_cpu_pct()
                intellistock_logger.log(
                    "Backtest %s: launching container (host CPU=%.1f%%, est CPU=+%.1f%%, %d running)." % (
                        row_id, host_cpu_final, est_cpu_pct, len(active_futures) + 1),
                    "green", service="BACKTEST_ENGINE",
                )
                try:
                    with _container_launch_times_lock:
                        _container_launch_times[row_id] = time.time()
                    future = executor.submit(run_one_backtest, row, avg_difficulty, is_high)
                    active_futures[future] = (row_id, is_high)
                    if is_high:
                        with _high_difficulty_lock:
                            _high_difficulty_running += 1
                except Exception as e:
                    intellistock_logger.log(f"Backtest {row_id}: submit failed: {e}", "red", service="BACKTEST_ENGINE")
                    try:
                        if is_high:
                            with _deferred_lock:
                                _deferred_high_difficulty.insert(0, row)
                        else:
                            _backtest_queue.put(row)
                    except Exception:
                        pass
    except KeyboardInterrupt:
        intellistock_logger.log("Backtest engine stopped", "yellow", service="BACKTEST_ENGINE")
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()

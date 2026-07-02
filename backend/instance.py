# Copyright (c) 2020 Pranav Krishna — MIT License (see LICENSE)
# All rights reserved.
# This file is part of the IntelliStock-v2 Tool,
# and is released under the "Non distributable agreement".

# Instance process: connects to server via Socket.IO, runs a single broker with all tickers.
# Reads tickers from Instances[instance_id].stocks (list of symbols). Watches Instances for changes
# and relaunches broker when the stocks list changes. Uses RethinkDB only. Logs via intellistock_logger.

###########################
# IMPORTS
############################

import time
import itertools
import sys
import os
import subprocess
import threading
import traceback
from collections import deque
from datetime import datetime
from os import system

from rethinkdb import RethinkDB
import socketio
from dotenv import load_dotenv

from intellistock_logger import intellistock_logger

# Live-mode crash-loop cap: if broker subprocess dies too often in a short
# window, stop auto-restarting to avoid thrashing Alpaca auth and burning logs.
CRASH_LOOP_WINDOW_SEC = int(os.environ.get("BROKER_CRASH_LOOP_WINDOW_SEC", "60"))
CRASH_LOOP_MAX_RESTARTS = int(os.environ.get("BROKER_CRASH_LOOP_MAX_RESTARTS", "5"))
_broker_restart_times: deque = deque(maxlen=CRASH_LOOP_MAX_RESTARTS + 1)
_crash_loop_latched = False

# Crash keep-alive: when an equities instance dies for any reason OTHER than an
# operator Stop, hold the container open (block indefinitely) instead of exiting,
# so the live-trading log stays viewable in logs-history and the operator gets a
# notification. Operator Stop (runCommand=False) still exits cleanly. Gated to
# equities — Kalshi keeps its own kalshi/runner.py crash handling.
CHANGEFEED_RETRY_MAX = int(os.environ.get("INSTANCE_CHANGEFEED_RETRY_MAX", "3"))
_KEEPALIVE_ENABLED = True   # set from the Instances row 'kind' at startup
_crash_lock = threading.Lock()
_crash_entered = False

###########################
# GLOBAL VARIABLES
############################

load_dotenv()
r = RethinkDB()
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))
DB_NAME = 'IntelliStock'

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

broker_process = None  # single broker subprocess
current_symbols = []   # list of symbols the broker was started with
current_granularity_time_increment = '60'  # from Instances[instance_id].granularity_time_increment
instance_key = ''      # from Instances[instance_id].key (loaded from DB)
instance_secret = ''   # from Instances[instance_id].secret (loaded from DB)
args_list = sys.argv


def get_conn():
    """Create a new RethinkDB connection (connections are not thread-safe)."""
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def safe_close(conn):
    """Close RethinkDB connection without raising when the connection is already dead (e.g. server shut down)."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass  # Socket may already be None or closed when RethinkDB server shuts down first


# ---------------------------------------------------------------------------
# Crash keep-alive
# ---------------------------------------------------------------------------

def _keepalive_enabled_for_kind(kind) -> bool:
    """Crash keep-alive applies to equities instances only. Kalshi instances keep
    today's behavior (exit on changefeed loss / propagate exceptions) because the
    Kalshi engine has its own kalshi/runner.py crash handling."""
    return str(kind or "").strip().lower() != "kalshi"


def _changefeed_backoff(attempt):
    """Seconds to wait before the next changefeed reconnect attempt (capped)."""
    return min(2.0 * max(1, attempt), 10.0)


def _operator_stop_requested(conn, instance_id):
    """True iff the operator pressed Stop (runCommand is False) — the one signal
    that may end a crash keep-alive block. DB errors read as 'no stop' so a flaky
    DB never ends the block prematurely."""
    try:
        doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
        return bool(doc) and doc.get('runCommand', True) is False
    except Exception:
        return False


def _write_crash_to_logfile(instance_id, text):
    """Append a crash banner to the instance's CURRENT live-trading log file
    (append mode — NO rotation, so the crash tail is preserved) so the reason
    shows up in the logs-history UI. Best-effort."""
    try:
        import live_state as _ls_mod
        path = _ls_mod.log_file_path_for(str(instance_id))
        with open(path, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except Exception:
        pass


def _mark_instance_crashed(instance_id, reason):
    """Flag the Instances row crashed (running stays True so the container is not
    reaped and the dashboard can show a 'crashed' badge). Best-effort."""
    try:
        conn = get_conn()
        try:
            r.db(DB_NAME).table('Instances').get(str(instance_id)).update({
                'crashed': True,
                'crashed_at': r.now(),
                'running': True,
                'status': 'crashed',
            }).run(conn)
        finally:
            safe_close(conn)
    except Exception as e:
        intellistock_logger.log(f"Failed to record crashed status: {e}", "yellow", service="INSTANCE")


def _block_until_operator_stop(poll_sec=2.0, max_iterations=None):
    """Block the process indefinitely. The ONLY thing that ends the block is an
    operator Stop (runCommand=False), which exits cleanly via os._exit(0). Nothing
    automatic or accidental reaps the container while this loop runs.

    ``max_iterations`` is a test seam (None = forever)."""
    instance_id = str(args_list[1]) if len(args_list) > 1 else ''
    i = 0
    while max_iterations is None or i < max_iterations:
        i += 1
        time.sleep(poll_sec)
        conn = None
        try:
            conn = get_conn()
            if _operator_stop_requested(conn, instance_id):
                intellistock_logger.log(
                    "Operator Stop during crash keep-alive; exiting.", "green", service="STATUS",
                )
                safe_close(conn)
                os._exit(0)
        except Exception:
            pass  # DB unreachable → keep blocking
        finally:
            safe_close(conn)


def trip_crash(reason, exc=None):
    """Convert a non-operator death into a halted, notified, held-open instance.
    Runs the side effects exactly once (idempotent across threads): terminate the
    broker, write the crash to the log file + flag the row, and notify. Does NOT
    block — the main thread owns the indefinite block (see enter_crash_keepalive /
    the supervisor loop). Safe to call from any thread; never raises."""
    global _crash_entered, broker_process
    with _crash_lock:
        if _crash_entered:
            return
        _crash_entered = True

    instance_id = str(args_list[1]) if len(args_list) > 1 else ''

    # 1. Halt trading: stop the broker subprocess if it is still alive.
    try:
        if broker_process is not None and broker_process.poll() is None:
            broker_process.terminate()
    except Exception:
        pass

    # 2. Capture a traceback if we have one.
    detail = ""
    try:
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            tb = traceback.format_exc()
            detail = "" if tb.strip() == "NoneType: None" else tb
    except Exception:
        detail = ""

    intellistock_logger.log(f"INSTANCE CRASH [{instance_id}] {reason}", "red", service="INSTANCE")
    intellistock_logger.log(
        "Holding container open (blocking); logs preserved. Only an operator Stop will exit.",
        "yellow", service="INSTANCE",
    )
    if detail:
        for line in detail.rstrip().splitlines():
            intellistock_logger.log(line, "red", service="INSTANCE")

    # 3. Persist the crash to the live-trading log file + flag the row.
    log_text = f"\n# ---\n# INSTANCE CRASH\n# reason: {reason}\n"
    if detail:
        log_text += detail.rstrip() + "\n"
    log_text += "# held open (blocking) for log capture; operator Stop will exit\n"
    _write_crash_to_logfile(instance_id, log_text)
    _mark_instance_crashed(instance_id, reason)

    # 4. Notify (best-effort).
    try:
        from live_alerts import alert_instance_crash
        alert_instance_crash(
            instance_id=instance_id, reason=reason,
            detail=(detail[-900:] if detail else ""),
        )
    except Exception:
        pass


def enter_crash_keepalive(reason, exc=None):
    """trip_crash() then block the CURRENT thread indefinitely. Call this from the
    MAIN thread (setup failure / outermost wrapper). Daemon threads call trip_crash
    instead and let the main supervisor loop run the block."""
    trip_crash(reason, exc)
    _block_until_operator_stop()


def terminate_thread_on_command(conn):
    """Watch this instance's document in Instances; exit when runCommand becomes False.

    On a changefeed error, reconnect up to CHANGEFEED_RETRY_MAX times with backoff
    so a transient RethinkDB blip self-heals. A persistent failure trips crash
    keep-alive (equities) instead of silently stopping the container. Kalshi keeps
    the original behavior (exit on the first error)."""
    global args_list, broker_process
    instance_id = args_list[1]
    attempts = 0
    while True:
        try:
            for change in r.db(DB_NAME).table('Instances').get(instance_id).changes().run(conn):
                attempts = 0  # a healthy feed resets the retry budget
                new_val = change.get('new_val')
                if new_val is None:
                    continue
                run_cmd = new_val.get('runCommand', True)
                if run_cmd is False:
                    intellistock_logger.log("Exiting program (runCommand=False)", "yellow", service="INSTANCE")
                    try:
                        r.db(DB_NAME).table('Instances').get(instance_id).update({'running': False}).run(conn)
                    except Exception:
                        pass
                    if broker_process and broker_process.poll() is None:
                        broker_process.terminate()
                    intellistock_logger.log("Instance stopped by command", "green", service="STATUS")
                    safe_close(conn)
                    os._exit(0)
            # Changefeed ended without raising (server closed the cursor).
            if not _KEEPALIVE_ENABLED:
                # Original behavior: the daemon thread ends quietly; process continues.
                safe_close(conn)
                return
            raise RuntimeError("Instances changefeed closed")
        except Exception as e:
            safe_close(conn)
            intellistock_logger.log(f"Instances changefeed error: {e}", "red", service="RethinkDB")
            if not _KEEPALIVE_ENABLED:
                # Original behavior: RethinkDB likely shut down; exit so container stops.
                os._exit(0)
            attempts += 1
            if attempts > CHANGEFEED_RETRY_MAX:
                trip_crash(f"Instances changefeed lost after {attempts} attempts: {e}", e)
                return
            time.sleep(_changefeed_backoff(attempts))
            try:
                conn = get_conn()
            except Exception:
                conn = None  # next iteration's query will raise and re-enter retry


def get_symbols_for_instance(conn, instance_id):
    """Return list of symbol strings for this instance from Instances[instance_id].stocks."""
    try:
        doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
        if doc is None:
            return []
        stocks = doc.get('stocks')
        if stocks is None:
            return []
        if not isinstance(stocks, list):
            return []
        return [str(s).strip().upper() for s in stocks if s and str(s).strip()]
    except Exception as e:
        intellistock_logger.log(f"get_symbols_for_instance: {e}", "yellow", service="INSTANCE")
        return []


def start_broker(symbols):
    """Start a single broker process with all tickers.

    Secrets are NOT passed on the command line - broker.py re-reads them from
    the Instances RethinkDB row inside the subprocess. This prevents
    `ps -ef` / WMI / crash-dump leakage of API credentials.
    """
    global broker_process, args_list, current_granularity_time_increment
    global _broker_restart_times, _crash_loop_latched
    if _crash_entered:
        # Instance is in crash keep-alive — never resurrect the broker (e.g. from a
        # late stocks-changefeed event).
        return
    if broker_process is not None and broker_process.poll() is None:
        broker_process.terminate()
        broker_process.wait()
        broker_process = None
    # NOTE: we intentionally DO NOT bail out when symbols is empty. Strategies
    # like graph_nexus_analysis self-discover their universe via
    # _nexus_executable_buys / momentum_watchlist and do not require seed
    # tickers in instance.stocks. Non-self-discovering strategies will simply
    # no-op per cycle with an empty universe, which is safe.
    if _crash_loop_latched:
        intellistock_logger.log(
            "Broker crash-loop latched; not restarting until operator intervention",
            "red", service="INSTANCE",
        )
        return
    # Crash-loop detection: track recent restart times.
    now = time.time()
    _broker_restart_times.append(now)
    recent = [t for t in _broker_restart_times if (now - t) <= CRASH_LOOP_WINDOW_SEC]
    if len(recent) > CRASH_LOOP_MAX_RESTARTS:
        _crash_loop_latched = True
        intellistock_logger.log(
            f"Broker crashed {len(recent)} times in {CRASH_LOOP_WINDOW_SEC}s - LATCHING (no more restarts)",
            "red", service="INSTANCE",
        )
        try:
            conn = get_conn()
            try:
                r.db(DB_NAME).table('Instances').get(str(args_list[1])).update({
                    'status': 'crash-looping',
                    'crash_loop_latched_at': r.now(),
                }).run(conn)
            finally:
                safe_close(conn)
        except Exception as e:
            intellistock_logger.log(f"Failed to record crash-loop status: {e}", "yellow", service="INSTANCE")
        try:
            from live_alerts import alert_crash_loop
            alert_crash_loop(instance_id=str(args_list[1]), restarts=len(recent), window_sec=CRASH_LOOP_WINDOW_SEC)
        except Exception:
            pass
        if _KEEPALIVE_ENABLED:
            # Stop spinning/relogging every 2s — halt cleanly and hold the
            # container open so logs stay viewable. trip_crash sets _crash_entered;
            # the main supervisor loop then runs the indefinite block.
            trip_crash(
                f"broker crash-loop latched ({len(recent)} restarts in {CRASH_LOOP_WINDOW_SEC}s)"
            )
        return

    instance_id = str(args_list[1])
    # Kalshi instances run the lean prediction-markets engine, NOT the equities
    # broker. Dispatch on the Instances row's 'kind'. Equities instances (no
    # 'kind') take the unchanged broker.py path below.
    _kind = None
    try:
        _kc = get_conn()
        try:
            _kdoc = r.db(DB_NAME).table('Instances').get(instance_id).run(_kc) or {}
            _kind = _kdoc.get('kind')
        finally:
            safe_close(_kc)
    except Exception:
        _kind = None

    if _kind == 'kalshi':
        # kalshi/runner.py reads kalshi_config + linked brokerage from the DB.
        cmd = ['python', '-m', 'kalshi.runner', instance_id]
        intellistock_logger.log(
            f"Started Kalshi engine for instance {instance_id}", "green", service="INSTANCE",
        )
    else:
        time_increment = (current_granularity_time_increment or '60').strip()
        # broker.py <instance_id> <mode> <start_date> <end_date> <time_increment> [SYM1 ...]
        # Secrets are read from DB inside broker.py; NOT passed as argv.
        cmd = [
            'python', 'broker.py', instance_id, 'live',
            'NULL', 'NULL', time_increment,
        ] + list(symbols)
    broker_process = subprocess.Popen(
        cmd,
        cwd=BACKEND_DIR,
        creationflags=0,  # same terminal (no CREATE_NEW_CONSOLE)
    )
    if symbols:
        intellistock_logger.log(
            f"Started broker with {len(symbols)} ticker(s): {', '.join(symbols)} (secrets read from DB)",
            "green", service="INSTANCE",
        )
    else:
        intellistock_logger.log(
            "Started broker with 0 seed tickers; strategy will self-discover "
            "universe (e.g. graph_nexus_analysis). Secrets read from DB.",
            "green", service="INSTANCE",
        )


def run_instance_change(change):
    """Handle Instances changefeed: when this instance's document changes, restart broker with new stocks or granularity."""
    global current_symbols, current_granularity_time_increment, instance_key, instance_secret, args_list
    instance_id = args_list[1]
    conn = None
    try:
        conn = get_conn()
        new_val = change.get('new_val')
        if new_val is None:
            return
        instance_key = (new_val.get('key') or '') if isinstance(new_val.get('key'), str) else str(new_val.get('key') or '')
        instance_secret = (new_val.get('secret') or '') if isinstance(new_val.get('secret'), str) else str(new_val.get('secret') or '')
        granularity = new_val.get('granularity_time_increment') or '60'
        if isinstance(granularity, (int, float)):
            granularity = str(granularity)
        new_granularity = (granularity or '60').strip()
        symbols = list(new_val.get('stocks') or [])
        symbols = [str(s).strip().upper() for s in symbols if s and str(s).strip()]
        symbols_changed = symbols != current_symbols
        granularity_changed = new_granularity != current_granularity_time_increment
        if symbols_changed:
            current_symbols[:] = symbols
        if granularity_changed:
            current_granularity_time_increment = new_granularity
        if symbols_changed or granularity_changed:
            start_broker(current_symbols)
            if symbols:
                intellistock_logger.log(f"Broker restarted with tickers: {', '.join(symbols)}", "green", service="INSTANCE")
            else:
                intellistock_logger.log("Broker stopped (no tickers in instance.stocks)", "yellow", service="INSTANCE")
    except Exception as e:
        intellistock_logger.log(str(e), "red", service="INSTANCE")
    finally:
        safe_close(conn)


def run_instance_stocks_changefeed():
    """Watch this instance's document in Instances; restart broker when stocks list
    changes. Same retry-then-keep-alive policy as terminate_thread_on_command."""
    conn = get_conn()
    instance_id = args_list[1]
    attempts = 0
    while True:
        try:
            for change in r.db(DB_NAME).table('Instances').get(instance_id).changes().run(conn):
                attempts = 0  # a healthy feed resets the retry budget
                run_instance_change(change)
            # Changefeed ended without raising (server closed the cursor).
            if not _KEEPALIVE_ENABLED:
                # Original behavior: the daemon thread ends quietly; process continues.
                safe_close(conn)
                return
            raise RuntimeError("Instances (stocks) changefeed closed")
        except Exception as e:
            safe_close(conn)
            intellistock_logger.log(f"Instances (stocks) changefeed error: {e}", "red", service="RethinkDB")
            if not _KEEPALIVE_ENABLED:
                # Original behavior: RethinkDB likely shut down; exit so container stops.
                os._exit(0)
            attempts += 1
            if attempts > CHANGEFEED_RETRY_MAX:
                trip_crash(f"Instances (stocks) changefeed lost after {attempts} attempts: {e}", e)
                return
            time.sleep(_changefeed_backoff(attempts))
            try:
                conn = get_conn()
            except Exception:
                conn = None  # next iteration's query will raise and re-enter retry


def run():
    global current_symbols, current_granularity_time_increment, instance_key, instance_secret
    global _KEEPALIVE_ENABLED
    if not os.environ.get('TERM'):
        os.environ['TERM'] = 'dumb'
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except Exception:
        pass
    intellistock_logger.log(f"""
   ========================================
   IntelliStock Instance: {args_list[1]}
   ========================================
   """, "green")

    intellistock_logger.log("Service is currently Offline", "green", service="STATUS")

    instance_id = args_list[1]
    intellistock_logger.log(f"Running as instance ID: {instance_id} (usage: python backend/instance.py <instance_id>; key/secret from DB)", "green", service="INSTANCE")
    conn = None
    try:
        conn = get_conn()
        # Fresh start clears any prior crash flags (crashed badge) too, plus any
        # stale halt fields — the server only spawns this process for a row with
        # runCommand=True, i.e. the operator has already un-halted it.
        r.db(DB_NAME).table('Instances').get(instance_id).update({
            'uptimeStart': r.now(),
            'running': True,
            'crashed': False,
            'crashed_at': None,
            'halt_reason': None,
            'halted_at': None,
        }).run(conn)
        current_symbols = get_symbols_for_instance(conn, instance_id)
        doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
        if doc:
            instance_key = (doc.get('key') or '') if isinstance(doc.get('key'), str) else str(doc.get('key') or '')
            instance_secret = (doc.get('secret') or '') if isinstance(doc.get('secret'), str) else str(doc.get('secret') or '')
        g = doc.get('granularity_time_increment') if doc else None
        current_granularity_time_increment = (str(g).strip() or '60') if g is not None and str(g).strip() else '60'
        # Crash keep-alive is for equities instances only (Kalshi has its own runner).
        _KEEPALIVE_ENABLED = _keepalive_enabled_for_kind(doc.get('kind') if doc else None)
    except Exception as e:
        intellistock_logger.log(f"Cannot connect to RethinkDB or failed to load instance: {e}", "red", service="RethinkDB")
        safe_close(conn)
        if _KEEPALIVE_ENABLED:
            # Hold the container open + notify instead of vanishing on a startup DB blip.
            enter_crash_keepalive(f"startup DB connect/load failed: {e}", e)
            return
        os._exit(1)
    finally:
        safe_close(conn)

    sio = socketio.Client()

    def socketIO():
        @sio.event
        def connect():
            intellistock_logger.log("Connection established to socket server", "green", service="SOCKET")
            sio.emit('clientType', {"UUID": args_list[1], "instance": args_list[1], "symbol": None})

        @sio.event
        def terminate(data):
            if data.get('terminate') is True:
                intellistock_logger.log("Terminate received from server", "yellow", service="SOCKET")
                os._exit(0)

        @sio.event
        def disconnect():
            intellistock_logger.log("Disconnected from server", "yellow", service="SOCKET")

        server_url = os.environ.get('SERVER_URL', 'http://localhost:5000')
        time.sleep(2)
        max_attempts = 8
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    time.sleep(2)
                sio.connect(server_url, transports=['polling'])
                break
            except Exception as e:
                if attempt == max_attempts:
                    intellistock_logger.log(f"Socket connection failed after {max_attempts} attempts: {e}", "red", service="SOCKET")
                    raise
                intellistock_logger.log(f"Socket connect attempt {attempt}/{max_attempts} failed, retrying: {e}", "yellow", service="SOCKET")
        sio.wait()

    socket_thread = threading.Thread(target=socketIO, daemon=True)
    socket_thread.start()

    intellistock_logger.log("Service starting", "yellow", service="SERVER")
    service_status = 0
    spinner = itertools.cycle(['-', '/', '|', '\\'])
    while service_status <= 40:
        sys.stdout.write(next(spinner))
        sys.stdout.flush()
        sys.stdout.write('\b')
        service_status += 1
        time.sleep(0.1)

    intellistock_logger.log("Service is currently Online", "green", service="STATUS")
    intellistock_logger.log("Instance started", "green", service="STATUS")

    # Start the broker regardless of initial tickers - self-discovering
    # strategies like graph_nexus_analysis populate their own universe via
    # _nexus_executable_buys. If the broker doesn't need seed tickers, empty
    # stocks is a valid configuration.
    start_broker(current_symbols)
    if current_symbols:
        intellistock_logger.log(f"Broker running with {len(current_symbols)} ticker(s): {', '.join(current_symbols)}", "green", service="INSTANCE")
    else:
        intellistock_logger.log(
            "Broker running with 0 seed tickers (strategy will self-discover universe).",
            "cyan",
            service="INSTANCE",
        )

    # Watch Instances for runCommand (terminate when False)
    instances_feed_thread = threading.Thread(
        target=lambda: terminate_thread_on_command(get_conn()),
        daemon=True,
    )
    instances_feed_thread.start()

    # Watch this instance's document for stocks list changes (restart broker when it changes)
    instance_stocks_feed_thread = threading.Thread(target=run_instance_stocks_changefeed, daemon=True)
    instance_stocks_feed_thread.start()

    intellistock_logger.log("Server running", "green", service="INSTANCE")

    # Keep main thread alive; restart broker if it exited (e.g. after strategy reload).
    # Auto-restart even when current_symbols is empty because self-discovering
    # strategies treat that as a valid universe. If a crash trips keep-alive (here
    # or in a daemon thread), stop supervising and block indefinitely so the
    # container stays up and logs stay viewable.
    try:
        while not _crash_entered:
            time.sleep(2)
            if _crash_entered:
                break
            if broker_process is not None and broker_process.poll() is not None:
                intellistock_logger.log(
                    f"Broker exited, restarting with {len(current_symbols)} seed ticker(s)...",
                    "yellow", service="INSTANCE",
                )
                start_broker(current_symbols)
    except KeyboardInterrupt:
        return
    except BaseException as e:
        if _KEEPALIVE_ENABLED:
            trip_crash(f"supervisor loop crashed: {e}", e)
        else:
            raise

    # A crash tripped keep-alive (by us or a daemon thread): block here on the MAIN
    # thread so the broker stays terminated process-wide and the container persists.
    if _crash_entered:
        _block_until_operator_stop()


if __name__ == '__main__':
    if len(args_list) > 1:
        if os.name == 'nt':
            system("title " + " Instance " + str(args_list[1]))
        try:
            run()
        except BaseException as e:
            # Backstop for anything run()'s inner handlers didn't catch (e.g. a
            # failure during thread/socket setup before the supervisor loop).
            if _KEEPALIVE_ENABLED:
                enter_crash_keepalive(f"instance crashed: {e}", e)
            else:
                raise
    else:
        intellistock_logger.log("Did not provide proper parameters (need instance id only; key/secret are read from DB)", "red", service="ERROR")

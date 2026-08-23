import datetime as dt
import os
import subprocess
import sys
import threading
import time
from os import system

# Run from backend/engines/; backend is parent for cwd and imports (must be before any backend imports)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from db import pool as _dbpool
from db import store as db_store
from db import watch as db_watch
from intellistock_logger import intellistock_logger

DB_NAME = 'IntelliStock'
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

if os.name == 'nt':
    system("title " + "Discover Engine")


def get_conn():
    """R26: the store takes its own pooled connection per operation, so there
    is nothing to hand out. Kept so the readiness probe below reads as it did.
    """
    return _dbpool.STORE_CONN


def _is_retryable_rethink_error(exc) -> bool:
    msg = str(exc).lower()
    return "primary replica" in msg or "not available" in msg or "connection refused" in msg


def wait_for_rethinkdb(max_attempts=30, delay=2, require_primary=False):
    """Wait until RethinkDB accepts connections and, optionally, table primaries are ready."""
    for attempt in range(1, max_attempts + 1):
        try:
            if require_primary:
                db_store.get('Config', 'Pings')
                db_store.get('EngineControl', 'discover_engine')
            else:
                db_store.table_list()
            return True
        except Exception as e:
            if attempt == max_attempts:
                intellistock_logger.log(f"RethinkDB not ready after {max_attempts} attempts: {e}", "red", service="DiscoverEngine")
                return False
            readiness_label = "RethinkDB primary not ready" if require_primary and _is_retryable_rethink_error(e) else "RethinkDB not ready"
            intellistock_logger.log(f"{readiness_label} (attempt {attempt}/{max_attempts}), retrying in {delay}s...", "yellow", service="DiscoverEngine")
            time.sleep(delay)
    return False


terminate_requested = False


def run_config_pings_changefeed():
    """Watch Config.Pings and copy discoverServicePing -> discoverServiceResponse."""
    global terminate_requested
    delay = 2
    while not terminate_requested:
        try:
            for change in db_watch.feed('Config', row_id='Pings',
                                        include_initial=False):
                if terminate_requested:
                    return
                new_val = change.get('new_val')
                if new_val and 'discoverServicePing' in new_val:
                    try:
                        request = new_val['discoverServicePing']
                        db_store.update('Config', 'Pings', {
                            'discoverServiceResponse': request
                        })
                    except Exception as e:
                        intellistock_logger.log(str(e), "red", service="DiscoverEngine")
            delay = 2
        except Exception as e:
            if terminate_requested:
                return
            level = "yellow" if _is_retryable_rethink_error(e) else "red"
            intellistock_logger.log(f"Config Pings changefeed error: {e}", level, service="DiscoverEngine")
            time.sleep(delay)
            delay = min(delay * 1.5, 30)


def run_engine_control_changefeed():
    """Watch EngineControl.discover_engine: exit when terminate is True."""
    global terminate_requested
    try:
        from engine_control import ENGINE_CONTROL_TABLE, ENGINE_ID_DISCOVER
    except ImportError:
        ENGINE_CONTROL_TABLE = "EngineControl"
        ENGINE_ID_DISCOVER = "discover_engine"
    delay = 2
    while not terminate_requested:
        try:
            for change in db_watch.feed(ENGINE_CONTROL_TABLE,
                                        row_id=ENGINE_ID_DISCOVER,
                                        include_initial=False):
                new_val = change.get('new_val')
                if new_val and new_val.get('terminate') is True:
                    terminate_requested = True
                    intellistock_logger.log("Terminate requested; exiting.", "yellow", service="DiscoverEngine")
                    os._exit(0)
            delay = 2
        except Exception as e:
            if terminate_requested:
                return
            level = "yellow" if _is_retryable_rethink_error(e) else "red"
            intellistock_logger.log(f"EngineControl changefeed error: {e}", level, service="DiscoverEngine")
            time.sleep(delay)
            delay = min(delay * 1.5, 30)


def run_discover():
    intellistock_logger.log("Starting discover.py subprocess", "green", service="DiscoverEngine")
    process = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND_DIR, 'discover.py')],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    intellistock_logger.log(f"Discover subprocess started (PID: {process.pid})", "cyan", service="DiscoverEngine")
    return process


def main():
    intellistock_logger.log("Discover Service started", "green", service="DiscoverEngine")
    intellistock_logger.log(f"Connecting to RethinkDB at {RETHINKDB_HOST}:{RETHINKDB_PORT}...", "cyan", service="DiscoverEngine")
    if not wait_for_rethinkdb(max_attempts=30, delay=2, require_primary=True):
        intellistock_logger.log("Exiting: could not connect to RethinkDB", "red", service="DiscoverEngine")
        sys.exit(1)
    intellistock_logger.log(f"Connected to RethinkDB at {RETHINKDB_HOST}:{RETHINKDB_PORT}", "cyan", service="DiscoverEngine")

    pings_feed = threading.Thread(target=run_config_pings_changefeed, daemon=True)
    pings_feed.start()
    config_feed = threading.Thread(target=run_engine_control_changefeed, daemon=True)
    config_feed.start()

    intellistock_logger.log("EngineControl changefeed started", "cyan", service="DiscoverEngine")

    start = '00:00:00'
    end = '1:59:00'
    in_interval = 0
    discover_process = None
    loop_count = 0

    while True:
        if terminate_requested:
            intellistock_logger.log("Terminating main loop", "yellow", service="DiscoverEngine")
            break
        now = dt.datetime.now()
        current_time = now.strftime("%H:%M:%S")
        loop_count += 1

        if discover_process and discover_process.poll() is None:
            if loop_count % 12 == 0:
                intellistock_logger.log("Discover still running", "cyan", service="DiscoverEngine")
        elif discover_process and discover_process.poll() is not None:
            code = discover_process.returncode
            intellistock_logger.log(f"Discover exited (code: {code})", "green" if code == 0 else "red", service="DiscoverEngine")
            discover_process = None

        if start < current_time < end:
            if in_interval == 0:
                intellistock_logger.log(f"Starting discover (window {start}-{end})", "yellow", service="DiscoverEngine")
                discover_process = run_discover()
            in_interval = 1
        else:
            in_interval = 0

        time.sleep(300)


if __name__ == "__main__":
    main()

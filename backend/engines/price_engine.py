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

from dotenv import load_dotenv
from db import schema as db_schema
from db import pool as _dbpool
from db import store as db_store
from db import watch as db_watch
from intellistock_logger import intellistock_logger

load_dotenv(os.path.join(BACKEND_DIR, '.env'))
load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))

DB_NAME = 'IntelliStock'
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

try:
    from engine_control import ENGINE_CONTROL_TABLE, ENGINE_ID_PRICE
except ImportError:
    ENGINE_CONTROL_TABLE = "EngineControl"
    ENGINE_ID_PRICE = "price_engine"


def get_conn():
    """R26: the store pools its own connection per operation, so there is
    nothing to hand out. Kept for call-site arity."""
    return _dbpool.STORE_CONN


def ensure_tables(c=None):
    """Ensure LivePricesStocks, LivePrices and PriceHistory exist.

    R25: the DDL (and PriceHistory's monthly partitions) lives in db.schema,
    so this is now one idempotent call instead of a table_list diff."""
    db_schema.ensure_schema(
        tables=('LivePricesStocks', 'LivePrices', 'PriceHistory'))


class myThread(threading.Thread):
    def __init__(self, threadID):
        threading.Thread.__init__(self)
        self.threadID = threadID

    def run(self):
        intellistock_logger.log("Starting price broker subprocess.", "green", service="PriceEngine")
        kwargs = dict(
            cwd=BACKEND_DIR,
            env=os.environ.copy(),
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        subprocess.Popen([sys.executable, 'priceBroker.py'], **kwargs)


def run_config_pings_changefeed():
    """Watch Config.Pings and copy priceServicePing -> priceServiceResponse."""
    try:
        for change in db_watch.feed('Config', row_id='Pings',
                                    include_initial=False):
            new_val = change.get('new_val')
            if new_val and 'priceServicePing' in new_val:
                try:
                    request = new_val['priceServicePing']
                    db_store.update('Config', 'Pings', {
                        'priceServiceResponse': request
                    })
                    intellistock_logger.log("Responded to priceServicePing.", "white", service="PriceEngine")
                except Exception as e:
                    intellistock_logger.log(str(e), "red", service="PriceEngine")
    except Exception as e:
        intellistock_logger.log(f"Config Pings changefeed error: {e}", "red", service="PriceEngine")


def run_engine_control_changefeed():
    """Watch EngineControl.price_engine: start broker when run_price_service is True; exit when terminate is True."""
    try:
        for change in db_watch.feed(ENGINE_CONTROL_TABLE,
                                    row_id=ENGINE_ID_PRICE,
                                    include_initial=False):
            new_val = change.get('new_val')
            if new_val is None:
                continue
            if new_val.get('terminate') is True:
                intellistock_logger.log("Terminate requested; exiting.", "yellow", service="PriceEngine")
                os._exit(0)
            if new_val.get('run_price_service') is True:
                intellistock_logger.log("Starting price broker (run_price_service=True).", "green", service="PriceEngine")
                thread = myThread("Thread-1")
                thread.start()
    except Exception as e:
        intellistock_logger.log(f"EngineControl changefeed error: {e}", "red", service="PriceEngine")


if os.name == 'nt':
    system("title " + "Price Engine")
intellistock_logger.log("Price engine starting.", "green", service="PriceEngine")

# Connect to Postgres with retries
conn = None
for attempt in range(1, 7):
    try:
        intellistock_logger.log(f"Connecting to Postgres (attempt {attempt}/6)...", "white", service="PriceEngine")
        conn = get_conn()
        intellistock_logger.log("Postgres connected. Ensuring tables exist.", "green", service="PriceEngine")
        ensure_tables(conn)
        # Set run_price_service=True in EngineControl so broker starts
        try:
            from engine_control import ensure_engine_control_table, update_engine_doc
            ensure_engine_control_table(conn)
            update_engine_doc(conn, ENGINE_ID_PRICE, {"run_price_service": True})
        except Exception:
            pass
        break
    except Exception as e:
        intellistock_logger.log(f"Postgres connection attempt {attempt}/6 failed: {e}", "red", service="PriceEngine")
        if attempt < 6:
            time.sleep(5)
        else:
            intellistock_logger.log("Could not connect to Postgres. Exiting.", "red", service="PriceEngine")
            sys.exit(1)

# Initial run: start broker
intellistock_logger.log("Starting initial price broker subprocess.", "green", service="PriceEngine")
thread = myThread("Thread-1")
thread.start()

# Changefeed threads: Config.Pings (ping response) and EngineControl.price_engine (terminate / run_price_service)
config_pings_feed = threading.Thread(target=run_config_pings_changefeed, daemon=True)
config_pings_feed.start()
config_doc_feed = threading.Thread(target=run_engine_control_changefeed, daemon=True)
config_doc_feed.start()

# LivePricesStocks changefeed: on add/remove ticker, restart broker
try:
    for item in db_watch.feed('LivePricesStocks', include_initial=False):
        intellistock_logger.log(
            "LivePricesStocks changed; restarting price broker to pick up changes.",
            "green", service="PriceEngine",
        )
        try:
            from engine_control import update_engine_doc
            update_engine_doc(conn, ENGINE_ID_PRICE, {"run_price_service": False})
        except Exception:
            pass
        time.sleep(2)
        try:
            from engine_control import update_engine_doc
            update_engine_doc(conn, ENGINE_ID_PRICE, {"run_price_service": True})
        except Exception:
            pass
except Exception as e:
    intellistock_logger.log(f"LivePricesStocks changefeed error: {e}", "red", service="PriceEngine")

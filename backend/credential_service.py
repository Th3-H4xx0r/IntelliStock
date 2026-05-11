#!/usr/bin/env python
"""
Credential Service - Monitors and refreshes brokerage credentials in RethinkDB.

Polls every 5 minutes (configurable via CREDENTIAL_CHECK_INTERVAL env var).
For each linked Robinhood account, checks if the token is within 30 minutes of
expiry and refreshes it using the stored refresh_token.
"""

import os
import sys
import time

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_dir, ".env"))
load_dotenv(os.path.join(os.path.dirname(_backend_dir), ".env"))

from rethinkdb import RethinkDB

# Use intellistock_logger so the _redact filter scrubs any accidentally-leaked
# tokens from exception messages. stdlib logging.basicConfig does NOT run
# through _redact and was previously a secret-leak vector.
from intellistock_logger import intellistock_logger


class _LogShim:
    """Stdlib-logging compatible shim over intellistock_logger."""

    def _fmt(self, msg, args):
        try:
            return msg % args if args else msg
        except Exception:
            return f"{msg} {args}" if args else msg

    def info(self, msg, *args):
        intellistock_logger.log(self._fmt(msg, args), "green", service="CREDS")

    def warning(self, msg, *args):
        intellistock_logger.log(self._fmt(msg, args), "yellow", service="CREDS")

    def error(self, msg, *args):
        intellistock_logger.log(self._fmt(msg, args), "red", service="CREDS")


log = _LogShim()

r = RethinkDB()
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))
DB_NAME = "IntelliStock"
BROKERAGE_ACCOUNTS_TABLE = "BrokerageAccounts"

# How often to check (seconds). Default: 5 minutes.
CHECK_INTERVAL = int(os.environ.get("CREDENTIAL_CHECK_INTERVAL", "300"))
# Refresh a token this many seconds before it expires. Default: 30 minutes.
REFRESH_BEFORE_EXPIRY = int(os.environ.get("CREDENTIAL_REFRESH_BEFORE_EXPIRY", "1800"))


def get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def _ensure_table(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if BROKERAGE_ACCOUNTS_TABLE not in tables:
        r.db(DB_NAME).table_create(BROKERAGE_ACCOUNTS_TABLE).run(conn)


def check_and_refresh():
    try:
        conn = get_conn()
    except Exception as e:
        log.warning("RethinkDB connection failed: %s", e)
        return

    try:
        _ensure_table(conn)
        docs = list(
            r.db(DB_NAME)
            .table(BROKERAGE_ACCOUNTS_TABLE)
            .filter({"brokerage_type": "robinhood"})
            .run(conn)
        )
    except Exception as e:
        log.warning("Failed to fetch brokerage accounts: %s", e)
        conn.close()
        return

    now_epoch = int(time.time())
    for doc in docs:
        brokerage_id = doc.get("id")
        account_name = doc.get("account_name", brokerage_id)

        obtained_at = doc.get("robinhood_obtained_at_epoch") or 0
        expires_in = doc.get("robinhood_expires_in") or 86400
        expires_at = obtained_at + expires_in
        secs_remaining = expires_at - now_epoch

        if secs_remaining > REFRESH_BEFORE_EXPIRY:
            log.info("[%s] Token valid for %ds, no refresh needed.", account_name, secs_remaining)
            continue

        log.info(
            "[%s] Token expiring in %ds (threshold=%ds), refreshing...",
            account_name,
            max(0, secs_remaining),
            REFRESH_BEFORE_EXPIRY,
        )

        try:
            import datetime
            from robinhood_engine import (
                RobinhoodClient,
                RobinhoodSessionState,
                RobinhoodAPIError,
            )

            # Decrypt stored tokens before handing to client; encrypt new ones on write.
            try:
                from secret_store import decrypt as _decrypt, encrypt as _encrypt
            except Exception:
                _decrypt = lambda v: v
                _encrypt = lambda v: v
            state = RobinhoodSessionState(
                access_token=_decrypt(doc.get("robinhood_access_token")),
                refresh_token=_decrypt(doc.get("robinhood_refresh_token")),
                token_type=doc.get("robinhood_token_type", "Bearer"),
                expires_in=expires_in,
                obtained_at_epoch=obtained_at,
                device_token=_decrypt(doc.get("robinhood_device_token")),
                account_number=doc.get("robinhood_account_number"),
                account_url=doc.get("robinhood_account_url"),
            )
            client = RobinhoodClient(state=state)
            new_state = client.refresh()
            ts = datetime.datetime.utcnow().isoformat()
            # Attempt to encrypt on write; fall back to plaintext only if the
            # Fernet key is missing (CRED_KEY not configured on this host).
            try:
                new_access = _encrypt(new_state.access_token)
                new_refresh = _encrypt(new_state.refresh_token)
            except RuntimeError:
                new_access = new_state.access_token
                new_refresh = new_state.refresh_token
                log.warning(
                    "[%s] Storing tokens in plaintext (INTELLISTOCK_CRED_KEY not set).",
                    account_name,
                )
            r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update({
                "robinhood_access_token": new_access,
                "robinhood_refresh_token": new_refresh,
                "robinhood_obtained_at_epoch": new_state.obtained_at_epoch,
                "robinhood_expires_in": new_state.expires_in,
                "status": "active",
                "last_error": None,
                "last_refresh_at": ts,
                # 2026-04-30 — auth chain CRITICAL #3 single-writer guard.
                # Adapter's _maybe_refresh_token reads _last_refresh_epoch and
                # skips its own refresh if cred_service wrote in last 5 min,
                # avoiding the rotated-refresh_token race described in the
                # bug-sweep findings.
                "_last_refresh_epoch": now_epoch,
                "updated_at": ts,
            }).run(conn)
            log.info("[%s] Token refreshed successfully.", account_name)

        except Exception as e:
            log.error("[%s] Refresh failed: %s", account_name, e)
            try:
                import datetime
                ts = datetime.datetime.utcnow().isoformat()
                r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update({
                    "status": "expired",
                    "last_error": str(e),
                    "updated_at": ts,
                }).run(conn)
            except Exception:
                pass

    conn.close()


def main():
    log.info(
        "Credential service started. Check interval=%ds, refresh threshold=%ds.",
        CHECK_INTERVAL,
        REFRESH_BEFORE_EXPIRY,
    )
    while True:
        try:
            check_and_refresh()
        except Exception as e:
            log.error("Unexpected error in check cycle: %s", e)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

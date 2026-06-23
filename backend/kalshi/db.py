"""RethinkDB persistence for the Kalshi feature. New tables live alongside the
existing IntelliStock tables. Table creation is idempotent (mirrors
credential_service._ensure_table / server.py). Doc builders are pure so they
unit-test without a live DB.
"""
from __future__ import annotations

import os

try:
    from rethinkdb import RethinkDB  # type: ignore
    _r = RethinkDB()
except Exception:  # pragma: no cover - unit tests stub the connection
    _r = None

DB_NAME = "IntelliStock"
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))

# (table_name, primary_key)
KALSHI_TABLES: list[tuple[str, str]] = [
    ("sports_fixtures", "fixture_id"),
    ("kalshi_markets", "market_ticker"),
    ("kalshi_odds_snapshots", "id"),
    ("kalshi_edges", "id"),
    ("kalshi_orders", "client_order_id"),
    ("kalshi_positions", "id"),
    ("kalshi_portfolio_snapshots", "id"),
    ("kalshi_clv_log", "id"),
    ("team_stats", "id"),
    ("kalshi_scan_budget", "window"),
    # v2 intelligence
    ("kalshi_decisions", "id"),
    ("player_stats", "id"),
    ("h2h_history", "id"),
    ("lineups", "fixture_id"),
    ("match_features", "fixture_id"),
    ("kalshi_market_listings", "fixture_id"),
    ("kalshi_capital_plan", "instance_id"),
]


def get_conn():
    if _r is None:
        raise RuntimeError("rethinkdb driver unavailable")
    return _r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def ensure_tables(conn) -> list[str]:
    """Create any missing kalshi_* tables. Returns the names created."""
    existing = set(_r.db(DB_NAME).table_list().run(conn))
    created = []
    for name, pk in KALSHI_TABLES:
        if name not in existing:
            _r.db(DB_NAME).table_create(name, primary_key=pk).run(conn)
            created.append(name)
    return created


# --- pure doc builders (unit-tested) ---

def portfolio_snapshot_doc(*, brokerage_id: str, ts: str, value_cents: int, cash_cents: int) -> dict:
    return {
        "id": f"{brokerage_id}|{ts}",
        "brokerage_id": brokerage_id,
        "ts": ts,
        "value_cents": int(value_cents),
        "cash_cents": int(cash_cents),
    }


def edge_doc(*, brokerage_id: str, ts: str, flag: dict) -> dict:
    return {
        "id": f"{brokerage_id}|{flag.get('market_ticker')}|{ts}",
        "brokerage_id": brokerage_id,
        "ts": ts,
        **flag,
    }


def scan_budget_window(ts_iso: str) -> str:
    """Monthly budget window key 'YYYY-MM' derived from an ISO timestamp."""
    return ts_iso[:7]


# --- thin DB helpers (exercised against a live DB, not unit-tested) ---

def save_portfolio_snapshot(conn, **kw) -> None:
    _r.db(DB_NAME).table("kalshi_portfolio_snapshots").insert(
        portfolio_snapshot_doc(**kw), conflict="replace"
    ).run(conn)


def read_portfolio_snapshots(conn, brokerage_id: str, limit: int = 5000) -> list[dict]:
    return list(
        _r.db(DB_NAME)
        .table("kalshi_portfolio_snapshots")
        .filter({"brokerage_id": brokerage_id})
        .order_by("ts")
        .limit(limit)
        .run(conn)
    )


def bump_scan_budget(conn, ts_iso: str, n: int = 1) -> int:
    """Increment and return the OddsPapi request count for the month."""
    window = scan_budget_window(ts_iso)
    tbl = _r.db(DB_NAME).table("kalshi_scan_budget")
    row = tbl.get(window).run(conn)
    used = int((row or {}).get("used", 0)) + n
    tbl.insert({"window": window, "used": used}, conflict="replace").run(conn)
    return used

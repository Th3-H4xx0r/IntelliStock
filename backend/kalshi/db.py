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
    ("kalshi_live", "id"),   # live in-match cards: id = "{instance_id}|{fixture_id}"
    ("kalshi_fills", "id"),  # actual fills — ground-truth entry price for reconcile
    ("kalshi_edge_history", "id"),  # rolling per-side edge series for UI sparklines
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


# --- feedback loop: persist fills/orders + reconcile settlements (the loop the bot lacked) ---

def write_order(conn, *, client_order_id, decision_id, market_ticker, status,
                requested, filled=0, ts="") -> None:
    """Persist a submitted order so fills can be joined back to the decision."""
    _r.db(DB_NAME).table("kalshi_orders").insert({
        "client_order_id": client_order_id,
        "decision_id": decision_id,
        "market_ticker": market_ticker,
        "status": status,
        "requested": int(requested),
        "filled": int(filled),
        "ts": ts,
    }, conflict="replace").run(conn)


def upsert_fills(conn, fills) -> int:
    """Upsert fills (ground-truth entry price). Accepts KalshiFill dataclasses OR
    raw dicts. Stable id dedupes re-pulls."""
    docs = []
    for f in (fills or []):
        if not isinstance(f, dict):
            f = getattr(f, "__dict__", {})
        tk = f.get("market_ticker") or f.get("ticker") or ""
        ts = f.get("ts") or f.get("created_time") or ""
        price = f.get("price_cents", f.get("yes_price", f.get("price", 0)))
        cnt = f.get("contracts", f.get("count", 0))
        fid = f"{tk}|{ts}|{price}|{cnt}"
        docs.append({"id": fid, "market_ticker": tk, "ts": ts,
                     "price_cents": price, "contracts": cnt,
                     "action": f.get("action", ""), "side": f.get("side", "")})
    if docs:
        _r.db(DB_NAME).table("kalshi_fills").insert(docs, conflict="replace").run(conn)
    return len(docs)


def append_edge_history(conn, instance_id, decisions, ts, cap: int = 60) -> int:
    """Append each evaluated side's current edge to a capped rolling series (one row
    per instance|market_ticker) so the UI can draw a sparkline of how the edge moved
    over time. Read-modify-write per market (small volume per tick)."""
    n = 0
    for d in (decisions or []):
        mt = d.get("market_ticker")
        e = d.get("edge")
        if not mt or e is None:
            continue
        rid = f"{instance_id}|{mt}"
        try:
            existing = _r.db(DB_NAME).table("kalshi_edge_history").get(rid).run(conn)
            hist = list((existing or {}).get("history") or [])
            # de-dupe identical consecutive ts (re-runs of the same tick) by ts.
            if not hist or hist[-1].get("ts") != ts:
                hist.append({"ts": ts, "edge": e})
            hist = hist[-cap:]
            _r.db(DB_NAME).table("kalshi_edge_history").insert(
                {"id": rid, "instance_id": str(instance_id), "market_ticker": mt,
                 "side": d.get("side"), "history": hist}, conflict="replace").run(conn)
            n += 1
        except Exception:
            pass
    return n


def mark_paper_positions(conn, instance_id, price_map) -> dict:
    """Mark open PAPER (mock) positions to the current Kalshi price so the UI can
    show LIVE unrealized P&L (like Kalshi's live position P&L). price_map maps
    market_ticker -> current YES mid in cents. For each placed, paper, not-yet-
    settled decision, stamp mark_cents + unrealized_pnl_cents = (mark-entry)*size.
    Returns {marked, unrealized_pnl_cents}. Measurement only — never trades."""
    import datetime
    try:
        rows = list(
            _r.db(DB_NAME).table("kalshi_decisions")
            .filter({"instance_id": str(instance_id), "decision": "placed", "paper": True})
            .run(conn)
        )
    except Exception:
        return {"marked": 0, "unrealized_pnl_cents": 0}
    now = datetime.datetime.utcnow().isoformat() + "Z"
    marked, total = 0, 0
    for r in rows:
        if r.get("outcome") is not None:   # already settled -> realized P&L, skip
            continue
        mark = (price_map or {}).get(r.get("market_ticker"))
        entry = r.get("entry_avg_cents")
        size = int(r.get("size") or 0)
        if mark is None or entry is None or not size:
            continue
        upl = int(round((float(mark) - float(entry)) * size))
        total += upl
        try:
            _r.db(DB_NAME).table("kalshi_decisions").get(r["id"]).update(
                {"mark_cents": int(round(float(mark))),
                 "unrealized_pnl_cents": upl, "mark_ts": now}).run(conn)
            marked += 1
        except Exception:
            pass
    return {"marked": marked, "unrealized_pnl_cents": total}


def settle_and_learn(conn, client, brokerage_id, *, fee_rate: float = 0.07) -> dict:
    """Pull settlements, reconcile un-settled PLACED decisions at the POSITION level
    (cost-weighted, one settlement counted once), write back outcome/realized_pnl/clv,
    and append clv_log rows. Idempotent: rows already reconciled are skipped. This is
    the measurement loop — it does NOT trade and NEVER flips live_enabled."""
    from kalshi import reconcile as _rec
    try:
        settlements = client.get_settlements(limit=500)
    except Exception:
        return {"settled": 0, "error": "get_settlements failed"}
    result_by_ticker = {}
    for s in (settlements or []):
        tk = s.get("ticker") or s.get("market_ticker")
        res = s.get("market_result") or s.get("result")
        if tk and res in ("yes", "no"):
            result_by_ticker[tk] = res
    if not result_by_ticker:
        return {"settled": 0}
    rows = list(
        _r.db(DB_NAME).table("kalshi_decisions")
        .filter(lambda d: (d["brokerage_id"] == brokerage_id)
                & (d["decision"] == "placed")
                & (d["realized_pnl_cents"].default(None).eq(None)))
        .run(conn)
    )
    rows = [r for r in rows if r.get("market_ticker") in result_by_ticker]
    if not rows:
        return {"settled": 0}
    rows_by_id = {r["id"]: r for r in rows}
    # Terminal-mark live exit/reduce SELL rows so they stop re-pulling every tick
    # (they aren't positions; P&L is captured at the net-position level).
    for r in rows:
        if r.get("live_action") in ("exit", "reduce"):
            _r.db(DB_NAME).table("kalshi_decisions").get(r["id"]).update(
                {"realized_pnl_cents": 0, "outcome": "exit"}).run(conn)
    # Ground-truth fills per settled ticker — ONLY positions that actually FILLED get
    # reconciled (a maker post_only rest can stay unfilled; never grade a phantom fill).
    settled_tickers = list({r.get("market_ticker") for r in rows})
    fills_by_ticker: dict = {}
    try:
        fl = list(_r.db(DB_NAME).table("kalshi_fills")
                  .filter(lambda f: _r.expr(settled_tickers).contains(f["market_ticker"]))
                  .run(conn))
    except Exception:
        fl = []
    for f in fl:
        tb = fills_by_ticker.setdefault(f.get("market_ticker"),
                                        {"buy_c": 0, "buy_cost": 0.0, "sell_c": 0})
        c = int(f.get("contracts") or 0)
        p = float(f.get("price_cents") or 0)
        if str(f.get("action")).lower() == "sell":
            tb["sell_c"] += c
        else:
            tb["buy_c"] += c
            tb["buy_cost"] += c * p
    positions = _rec.aggregate_positions(rows, fee_rate=fee_rate)
    settled = 0
    for pos in positions:
        res = result_by_ticker.get(pos["market_ticker"])
        if res is None:
            continue
        rows_for = [rows_by_id.get(did) or {} for did in pos["decision_ids"]]
        is_paper = bool(rows_for) and all(r.get("paper") for r in rows_for)
        if not is_paper:
            # LIVE position: require real fills — never grade a phantom/unfilled order.
            fb = fills_by_ticker.get(pos["market_ticker"])
            if not fb or fb["buy_c"] <= 0:
                for did in pos["decision_ids"]:
                    _r.db(DB_NAME).table("kalshi_decisions").get(did).update(
                        {"realized_pnl_cents": 0, "outcome": "unfilled"}).run(conn)
                continue
            net_c = fb["buy_c"] - fb["sell_c"]
            if net_c <= 0:                   # fully exited before settlement
                for did in pos["decision_ids"]:
                    _r.db(DB_NAME).table("kalshi_decisions").get(did).update(
                        {"realized_pnl_cents": 0, "outcome": "closed"}).run(conn)
                continue
            # ground-truth contracts + cost-weighted entry from actual buy fills
            pos = {**pos, "contracts": net_c, "avg_entry_cents": fb["buy_cost"] / fb["buy_c"]}
        # PAPER positions: grade at the modeled would-be entry (entry_avg_cents) — the
        # valid dry-run-against-real-prices test, no real fill required.
        rep = rows_by_id.get(pos["decision_ids"][0], {}) if pos["decision_ids"] else {}
        rec = _rec.reconcile_position(
            pos, result=res,
            close_cents=rep.get("pre_settle_mid_cents"),
            sharp_close_prob=rep.get("sharp_close_prob"),
            fee_rate=fee_rate,
        )
        total = pos["contracts"] or 1
        full_pnl = int(rec["realized_pnl_cents"])
        ids = pos["decision_ids"]
        allocated = 0
        for i, did in enumerate(ids):
            row = rows_by_id.get(did) or {}
            # running-residual split so the per-row parts sum EXACTLY to the position
            # P&L (independent rounding would drift by up to len(ids)-1 cents).
            if i == len(ids) - 1:
                part = full_pnl - allocated
            else:
                part = int(round(full_pnl * (int(row.get("size") or 0) / total)))
                allocated += part
            _r.db(DB_NAME).table("kalshi_decisions").get(did).update({
                "outcome": rec["outcome"],
                "clv": rec["clv"],
                "realized_pnl_cents": part,
            }).run(conn)
        if rec.get("clv_graded"):
            _r.db(DB_NAME).table("kalshi_clv_log").insert({
                "id": f"{brokerage_id}|{pos['market_ticker']}",
                "brokerage_id": brokerage_id,
                "league": rep.get("league", ""),
                "market_ticker": pos["market_ticker"],
                "clv": rec["clv"],
                "ts": rep.get("ts", ""),
            }, conflict="replace").run(conn)
        settled += 1
    return {"settled": settled}

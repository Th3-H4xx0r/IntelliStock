"""Clear inherited recent_sell_block by deleting this instance's filled SELL
rows from the GLOBAL LiveOrderWAL.

WHY:
Clean-room boot rebuilds _trades from this instance's filled LiveOrderWAL rows
(cid prefix, e.g. "main-"). The strategy's recent_sell_block then blocks
re-buying any ticker SOLD within recent_sell_block_bars (default 30) calendar
days — unless its price retraced >=recent_sell_block_recovery_pct% or it's
currently held. After a real trading history, that means the new clean-room run
inherits "don't re-buy what you recently sold" for those tickers.

This is opportunity-cost, NOT a risk (it never forces a trade or touches a
position). Disabling the feature via config is the WRONG tool: recent_sell_block_*
lives on the SHARED strategy doc, so it would change every instance AND kill the
block for FUTURE live sells too. Deleting the WAL SELL rows is scoped to THIS
instance (via the cid prefix) and only removes the INHERITED blocks; future live
sells re-arm the block normally.

SAFE because positions are managed off the BROKER, not the WAL: with 0 (or any)
broker positions, the classifier trusts the broker, so removing WAL history does
not create/lose any position. V32 entry-anchors only matter for currently-held
positions (whose entry BUY rows you can keep with the default --sells-only).

Dry run by default. Pass --apply to actually delete. Run BEFORE the broker boot
(the _trades rebuild happens at boot).

Usage:
  python3 scripts/clear_main_recent_sell_block.py                      # dry-run, instance 'main'
  python3 scripts/clear_main_recent_sell_block.py --apply              # delete filled SELL rows
  python3 scripts/clear_main_recent_sell_block.py --all-fills --apply  # delete ALL filled rows (true blank _trades)
  python3 scripts/clear_main_recent_sell_block.py --symbols SXI,TEM    # restrict to specific tickers
  python3 scripts/clear_main_recent_sell_block.py --instance main --block-bars 30
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, date

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass

DB_NAME = "IntelliStock"
WAL_TABLE = "LiveOrderWAL"


def _derive_cid_prefix(instance_id: str) -> str:
    """Mirror broker_adapters/_client_order_id.py::_safe(instance, 8)."""
    safe = re.sub(r"[^A-Za-z0-9]", "", instance_id or "")[:8] or "x"
    return f"{safe}-"


def _connect():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, db=DB_NAME)


def main(apply: bool, instance_id: str, all_fills: bool,
         symbols: set[str] | None, block_bars: int) -> int:
    prefix = _derive_cid_prefix(instance_id)
    r, conn = _connect()
    try:
        rows = [
            x for x in r.table(WAL_TABLE).run(conn)
            if (x.get("client_order_id") or "").startswith(prefix)
            and x.get("filled_qty")
            and not str(x.get("broker_order_id") or "").startswith("dry-")
        ]
    finally:
        pass  # keep conn open for the delete below

    # Analyze the recent_sell_block set as of today (price-retrace exception
    # cannot be evaluated here without live quotes; the real block may be a
    # SUBSET of what's shown if any symbol retraced enough).
    net = defaultdict(float)
    latest_sell: dict[str, str] = {}
    for x in rows:
        sym = (x.get("symbol") or "").upper()
        side = (x.get("side") or "").upper()
        qty = float(x.get("filled_qty") or 0.0)
        ts = str(x.get("updated_at_utc") or x.get("created_at_utc") or "")[:10]
        net[sym] += qty if side == "BUY" else -qty
        if side == "SELL" and ts and (sym not in latest_sell or ts > latest_sell[sym]):
            latest_sell[sym] = ts

    today = datetime.now(timezone.utc).date()
    blocked: list[str] = []
    for sym, ts in sorted(latest_sell.items()):
        try:
            days = (today - date.fromisoformat(ts)).days
        except Exception:
            continue
        if 0 <= days < block_bars and net.get(sym, 0.0) <= 1e-9:
            blocked.append(sym)

    print(f"instance={instance_id!r}  cid_prefix={prefix!r}  block_bars={block_bars}d  (today={today})")
    print(f"filled {prefix} WAL rows: {len(rows)}")
    print(f"recent_sell_block would-block as of today (pre-price-retrace check): {blocked or '(none)'}")

    # Decide which rows to delete.
    def _match(x) -> bool:
        sym = (x.get("symbol") or "").upper()
        if symbols and sym not in symbols:
            return False
        if all_fills:
            return True
        return (x.get("side") or "").upper() == "SELL"

    to_delete = [x for x in rows if _match(x)]
    scope = "ALL filled rows" if all_fills else "filled SELL rows"
    if symbols:
        scope += f" for symbols {sorted(symbols)}"
    print(f"\n{'WOULD DELETE' if not apply else 'DELETING'} {len(to_delete)} {scope}:")
    for x in sorted(to_delete, key=lambda z: str(z.get('updated_at_utc') or '')):
        print(f"  {str(x.get('client_order_id'))[:32]:<32} {x.get('symbol'):<6} "
              f"{x.get('side'):<4} qty={x.get('filled_qty')} {str(x.get('updated_at_utc'))[:10]}")

    deleted = 0
    if apply and to_delete:
        ids = [x.get("client_order_id") for x in to_delete if x.get("client_order_id")]
        res = r.table(WAL_TABLE).get_all(*ids).delete().run(conn)
        deleted = int((res or {}).get("deleted", 0) or 0)
        print(f"\n-> deleted={deleted}")
    try:
        conn.close()
    except Exception:
        pass

    if not apply:
        print(f"\nDRY RUN. {len(to_delete)} row(s) would be deleted. Re-run with --apply.")
        print("Run BEFORE the broker boot (the _trades rebuild + recent_sell_block happen at boot).")
    else:
        print(f"\nDone. Cleared {deleted} row(s). The next clean-room boot will rebuild _trades "
              f"without the deleted fills, so the inherited recent_sell_block is gone. Future live "
              f"sells re-arm the block normally.")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="Clear inherited recent_sell_block by deleting filled WAL rows for one instance.")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    p.add_argument("--instance", default="main", help="instance_id whose cid-prefix rows to clear (default: main)")
    p.add_argument("--all-fills", action="store_true", help="delete ALL filled rows (sells + buys), not just sells")
    p.add_argument("--symbols", default="", help="comma-separated tickers to restrict to (default: all)")
    p.add_argument("--block-bars", type=int, default=30, help="recent_sell_block_bars for the analysis (default: 30)")
    return p.parse_args()


if __name__ == "__main__":
    a = _parse_args()
    syms = {s.strip().upper() for s in a.symbols.split(",") if s.strip()} or None
    raise SystemExit(main(a.apply, a.instance, a.all_fills, syms, a.block_bars))

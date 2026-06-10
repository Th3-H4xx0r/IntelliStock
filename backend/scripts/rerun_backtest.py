"""Re-run a backtest by cloning the config from an existing row.

Usage (run inside the backend or api container, where RETHINKDB_HOST is set):

    docker exec -i intellistock-backend python scripts/rerun_backtest.py 832260

The script:
  1. Looks up the existing backtest row by id (BacktestInstances table).
  2. Calls ``action_create_backtest`` with the SAME parameters — instance,
     stocks, date range, granularity, initial cash, and broker key/secret.
  3. Prints the new backtest id so you can follow progress via the UI or
     the existing ``/backtests/{id}/status`` endpoint.

We intentionally don't reuse the original row id — that would race with
any reaper/observer thread that already saw the row as terminal. A fresh
row puts the new run cleanly into the queue.
"""

import argparse
import os
import sys

# Path setup so this can be run from inside the container (where /app is
# the backend root) or from the repo via ``python backend/scripts/rerun_backtest.py``.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone & re-run an existing backtest by id.")
    parser.add_argument("backtest_id", help="Existing BacktestInstances row id to clone (e.g. 832260)")
    parser.add_argument(
        "--initial-cash", type=float, default=None,
        help="Override initial cash (default: same as source row).",
    )
    args = parser.parse_args()

    from rethinkdb import RethinkDB
    from interactive_utils import action_create_backtest  # type: ignore[import-not-found]

    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    r = RethinkDB()
    conn = r.connect(host=host, port=port)
    try:
        src = r.db("IntelliStock").table("BacktestInstances").get(args.backtest_id).run(conn)
        if src is None:
            print(f"[rerun] Backtest {args.backtest_id!r} not found in BacktestInstances.", file=sys.stderr)
            return 2

        instance_id = src.get("instance") or ""
        stocks = src.get("stocks") or []
        start_date = src.get("start-date") or src.get("start_date")
        end_date = src.get("end-date") or src.get("end_date")
        granularity_sec = int(src.get("granularity_sec") or 60)
        initial_cash = float(args.initial_cash if args.initial_cash is not None else (src.get("initial_cash") or 100000.0))
        key = src.get("key") or None
        secret = src.get("secret") or None

        print(
            f"[rerun] Cloning backtest {args.backtest_id} → instance={instance_id!r} "
            f"stocks={len(stocks)} period={start_date}→{end_date} "
            f"granularity={granularity_sec}s cash=${initial_cash:g}",
            file=sys.stderr,
        )

        result = action_create_backtest(
            conn,
            instance_id=instance_id,
            stocks=stocks,
            start_date=start_date,
            end_date=end_date,
            granularity_sec=granularity_sec,
            key=key,
            secret=secret,
            initial_cash=initial_cash,
        )
        new_id = result.get("id")
        print(f"[rerun] Queued new backtest id={new_id} (source={args.backtest_id})")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

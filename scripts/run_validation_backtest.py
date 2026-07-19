"""Launch a validation backtest on the deployed IntelliStock API.

Usage: python3 scripts/run_validation_backtest.py 2026-03-02 2026-03-30 [--cash 6000] [--granularity 3600]
Prints the created backtest id. Auth/env identical to pull_backtest_logs.py.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _http  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("start_date")
    p.add_argument("end_date")
    p.add_argument("--cash", type=float, default=6000.0)
    p.add_argument("--granularity", default="3600")
    p.add_argument("--instance", default="alpaca-main")
    a = p.parse_args(argv)

    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or os.environ.get("API_URL")
           or f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api,
        os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
    )
    status, body = _http(
        "POST", f"{api}/backtests",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        body=json.dumps({
            "instance_id": a.instance,
            "stocks": [],
            "start_date": a.start_date,
            "end_date": a.end_date,
            "granularity": a.granularity,
            "initial_cash": a.cash,
        }),
    )
    print(json.dumps(body if isinstance(body, dict) else {"raw": str(body)[:400], "status": status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

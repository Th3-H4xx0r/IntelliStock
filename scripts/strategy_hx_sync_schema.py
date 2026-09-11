#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_hx with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift
from `backend/strategy_hx.py:DEFAULTS` means an operator configures a key the
strategy does not have, or misses one it does.

The two single-position keys are BROKER-side — read by
`engines/backtest_engine._instance_single_position_pct`, not by the strategy
module — but they live in HX's DEFAULTS so the header is a plain copy. The
assertion below is what keeps that true if someone ever moves them out: a 65%
core cannot be built under the broker's 15% failsafe, and the failure is a
silent trim to $0.00 rather than an error.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_hx import DEFAULTS  # noqa: E402

_BROKER_SIDE_KEYS = ("broker_max_single_position_pct",
                     "honour_single_position_cap")

path = pathlib.Path("backend/strategies/strategy_hx.py")
source = path.read_text()
match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
schema = json.loads(match.group(1))
config = dict(DEFAULTS)
missing = [k for k in _BROKER_SIDE_KEYS if k not in config]
if missing:
    raise SystemExit(
        "strategy_hx DEFAULTS is missing broker-side key(s) "
        + ", ".join(missing)
        + ": the single-position cap would silently stay at the 15% failsafe "
          "and every levered buy would be trimmed to $0.00.")
schema["config"] = config
schema["execution_position"] = 10
path.write_text(source.replace(match.group(0),
                               "# INTELLISTOCK_SCHEMA: " + json.dumps(schema)))
print(f"schema synced from DEFAULTS: {len(config)} config keys")

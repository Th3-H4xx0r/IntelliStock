#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_x with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift from
`backend/strategy_x.py:DEFAULTS` means an operator configures a key the strategy
does not have, or misses one it does.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_x import DEFAULTS  # noqa: E402

p = pathlib.Path("backend/strategies/strategy_x.py")
s = p.read_text()
m = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", s)
d = json.loads(m.group(1))
cfg = dict(DEFAULTS)
# Broker-side key: read by backtest_engine, not by the strategy module.
cfg["broker_max_single_position_pct"] = d["config"].get(
    "broker_max_single_position_pct", 0.95)
cfg["core_once_per_session"] = d["config"].get("core_once_per_session", True)
d["config"] = cfg
d["execution_position"] = 10
p.write_text(s.replace(m.group(0), "# INTELLISTOCK_SCHEMA: " + json.dumps(d)))
print(f"schema synced from DEFAULTS: {len(cfg)} config keys")

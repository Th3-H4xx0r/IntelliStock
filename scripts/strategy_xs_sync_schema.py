#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_xs with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift from
`backend/strategy_xs.py:DEFAULTS` means an operator configures a key the
strategy does not have, or misses one it does.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_xs import DEFAULTS  # noqa: E402

p = pathlib.Path("backend/strategies/strategy_xs.py")
s = p.read_text()
m = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", s)
d = json.loads(m.group(1))
d["config"] = dict(DEFAULTS)
d["execution_position"] = 10
p.write_text(s.replace(m.group(0), "# INTELLISTOCK_SCHEMA: " + json.dumps(d)))
print(f"schema synced from DEFAULTS: {len(d['config'])} config keys")

#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA headers of strategy_swing and strategy_wheel
with swing_trader.constants SWING_DEFAULTS / WHEEL_DEFAULTS.

    python3 scripts/strategy_swing_sync_schema.py [--root DIR]

The header is what the UI and /strategies/available read; letting it drift
from the constants means an operator configures a key the lane does not read,
or misses one it does. Only `config` and `execution_position` are written;
every other header key keeps its order and value. --root names the tree whose
files are rewritten (default: this repo); the defaults always come from this
repo's code.
"""
import argparse
import json
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402

LANES = (("strategy_swing.py", SWING_DEFAULTS, 10),
         ("strategy_wheel.py", WHEEL_DEFAULTS, 20))


def sync(path: pathlib.Path, defaults: dict, position: int) -> int:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
    if match is None:
        raise SystemExit(f"{path}: no INTELLISTOCK_SCHEMA header")
    schema = json.loads(match.group(1))
    schema["config"] = dict(defaults)
    schema["execution_position"] = position
    path.write_text(source.replace(match.group(0),
                                   "# INTELLISTOCK_SCHEMA: " + json.dumps(schema), 1),
                    encoding="utf-8")
    return len(schema["config"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(_REPO))
    args = ap.parse_args(argv)
    for name, defaults, position in LANES:
        path = pathlib.Path(args.root) / "backend" / "strategies" / name
        n = sync(path, defaults, position)
        print(f"{name}: schema synced from defaults, {n} config keys, "
              f"execution_position {position}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

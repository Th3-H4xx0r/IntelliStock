"""The committed swing and wheel headers are exactly what the sync script
writes from SWING_DEFAULTS / WHEEL_DEFAULTS."""
import json
import os
import re
import shutil
import subprocess
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "strategy_swing_sync_schema.py")
FILES = {"strategy_swing.py": (SWING_DEFAULTS, 10), "strategy_wheel.py": (WHEEL_DEFAULTS, 20)}


def header(text):
    return json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)", text).group(1))


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=_ROOT,
                          capture_output=True, text=True)


def test_the_committed_headers_are_byte_identical_after_a_sync():
    paths = {n: os.path.join(_BACKEND, "strategies", n) for n in FILES}
    before = {n: open(p, encoding="utf-8").read() for n, p in paths.items()}
    result = run()
    assert result.returncode == 0, result.stderr
    for name, path in paths.items():
        after = open(path, encoding="utf-8").read()
        assert after == before[name], f"{name}: the committed header is not what the defaults say"
        defaults, position = FILES[name]
        schema = header(after)
        assert schema["config"] == defaults and list(schema["config"]) == list(defaults)
        assert schema["execution_position"] == position
        assert after.splitlines()[0].startswith("# INTELLISTOCK_SCHEMA: ")
        assert after.splitlines()[1].startswith("# INTELLISTOCK_DESCRIPTION: ")


def test_a_drifted_header_is_rewritten_and_nothing_else_moves(tmp_path):
    strategies = tmp_path / "backend" / "strategies"
    strategies.mkdir(parents=True)
    for name in FILES:
        shutil.copy(os.path.join(_BACKEND, "strategies", name), strategies / name)
    path = strategies / "strategy_swing.py"
    original = path.read_text(encoding="utf-8")
    schema = header(original)
    schema["config"].pop("vix_max")
    schema["config"]["stale_key"] = 1
    schema["execution_position"] = 99
    drifted = original.replace(original.splitlines()[0],
                               "# INTELLISTOCK_SCHEMA: " + json.dumps(schema), 1)
    path.write_text(drifted, encoding="utf-8")
    result = run("--root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == original
    assert "strategy_swing.py" in result.stdout and "strategy_wheel.py" in result.stdout

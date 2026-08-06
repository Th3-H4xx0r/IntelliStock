"""The dead-key registry has to stay true, or it becomes the thing it warns about.

A registry that lists a key which HAS a reader is worse than an empty one: the
operator removes a live control on the strength of a red log line. So the first
test re-derives the claim from the source rather than trusting the list.

Uses AST extraction rather than importing broker.py — importing it pulls in the
whole live trading runtime.
"""
import ast
import os
import re
import types

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROKER = os.path.join(BACKEND, "broker.py")

WANTED = {
    "_DEAD_STRATEGY_CONFIG_KEYS",
    "_CONFIG_BAND_PAIRS",
    "_warn_unsatisfiable_config_bands",
}


def _load():
    with open(BROKER, "r") as fh:
        src = fh.read()
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if getattr(node, "name", None) in WANTED:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) in WANTED for t in node.targets):
            keep.append(node)
    mod = types.ModuleType("_dead")
    logged = []
    mod._log = lambda msg, *a, **k: logged.append(msg)
    exec(compile(ast.Module(body=keep, type_ignores=[]), BROKER, "exec"), mod.__dict__)
    mod._logged = logged
    return mod


def _sources():
    """Every backend non-test source file, keyed by repo-relative path."""
    out = {}
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs
                   if d not in {"tests", "__pycache__", "scripts", "node_modules"}]
        for f in files:
            if not f.endswith(".py") or f == "cli.py":
                continue
            path = os.path.join(root, f)
            try:
                out[path] = open(path).read()
            except Exception:
                pass
    return out


MOD = _load()
SRC = _sources()


@pytest.mark.parametrize("key", sorted(MOD._DEAD_STRATEGY_CONFIG_KEYS))
def test_registered_key_really_has_no_reader(key):
    """A key in the registry must not be read anywhere in production code.

    `config.get("key")` / `cfg["key"]` are the reader forms. Bare mentions in
    comments and in the line-1 INTELLISTOCK_SCHEMA blob are not.
    """
    reader = re.compile(
        r"""(?:get|pop)\(\s*["']%s["']|\[\s*["']%s["']\s*\]""" % (re.escape(key), re.escape(key)))
    hits = []
    for path, text in SRC.items():
        for m in reader.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            # line 1 of graph_nexus_analysis.py is the schema advertisement
            if path.endswith("graph_nexus_analysis.py") and line_no == 1:
                continue
            hits.append(f"{os.path.relpath(path, BACKEND)}:{line_no}")
    assert not hits, (
        f"{key!r} is registered as DEAD but is read at {hits}. Either the "
        "registry entry is wrong, or a reader was added and the entry was not "
        "removed — both mislead the operator."
    )


def test_every_entry_explains_itself():
    for key, why in MOD._DEAD_STRATEGY_CONFIG_KEYS.items():
        assert isinstance(why, str) and len(why) > 15, (
            f"{key!r} has no useful explanation; the message is what the "
            f"operator acts on")


def test_registry_covers_the_known_families():
    """Guards against a silent regression to the one-entry registry."""
    reg = MOD._DEAD_STRATEGY_CONFIG_KEYS
    assert len(reg) >= 30, f"registry shrank to {len(reg)} entries"
    for expect in ("regime_bear_max_positions", "momentum_amplification",
                   "fast_loser_cut_pct_high_vol", "max_single_position_pct"):
        assert expect in reg, f"{expect} dropped out of the registry"


# --- unsatisfiable bands ---

def test_empty_band_is_reported():
    MOD._logged.clear()
    MOD._warn_unsatisfiable_config_bands(
        {"backfill_rotation_winner_lock_bypass_min_held_pnl_pct": 5,
         "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 3}, "doc-179")
    assert any("UNSATISFIABLE" in m for m in MOD._logged), MOD._logged
    assert any("doc-179" in m for m in MOD._logged)


def test_valid_band_is_silent():
    MOD._logged.clear()
    MOD._warn_unsatisfiable_config_bands(
        {"backfill_rotation_winner_lock_bypass_min_held_pnl_pct": 3,
         "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 5}, "ok")
    assert not MOD._logged


def test_equal_bounds_are_satisfiable():
    """min == max is a single admissible point, not an empty set."""
    MOD._logged.clear()
    MOD._warn_unsatisfiable_config_bands(
        {"rank_band_entry_pct": 10, "rank_band_exit_pct": 10}, "ok")
    assert not MOD._logged


def test_partial_pair_is_ignored():
    """Only one half set means the other takes a code default we do not know."""
    MOD._logged.clear()
    MOD._warn_unsatisfiable_config_bands(
        {"momentum_discovery_min_20d_return": 999}, "partial")
    assert not MOD._logged


def test_non_numeric_does_not_raise():
    MOD._logged.clear()
    MOD._warn_unsatisfiable_config_bands(
        {"rank_band_entry_pct": "high", "rank_band_exit_pct": None}, "junk")
    assert not MOD._logged


def test_band_pairs_are_well_formed():
    for entry in MOD._CONFIG_BAND_PAIRS:
        assert len(entry) == 3
        lo, hi, what = entry
        assert lo != hi and what

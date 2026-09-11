"""F1: a DB miss at boot must not cost the process its whole strategy cache.

The live loop restores each run_once lane's persisted `strategy_cache` once,
then sets `_strategy_cache_loaded_from_db` and never looks again. It used to set
that flag even when `get_conn_retry` returned None — silently, with no log — so
a container that booted during a DB blip ran for its whole life with an EMPTY
cache. For Strategy EB that means no `_strategy_eb_exit_issued_session`, no
persisted trend state and no pending book: a cold start on a levered position,
and the exit-dedupe and trend machine both reading their "never happened"
defaults.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _extract(*names):
    """AST-extract broker functions into a stub namespace. broker.py argparses
    at module scope and SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in set(names)]
    found = {n.name for n in wanted}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


class Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _logger():
    lines = []

    def log(message, color="white"):
        lines.append((str(message), color))

    return lines, log


SPECS = [{"strategy": "strategy_eb"}, {"strategy": "graph_nexus_analysis"}]


def _restore(**kw):
    ns = _extract("_restore_strategy_cache_from_db")
    return ns["_restore_strategy_cache_from_db"](**kw)


def test_a_missing_db_connection_is_reported_red_and_is_not_a_restore():
    lines, log = _logger()
    cache = {}
    done = _restore(run_once_specs=SPECS, instance_id="alpaca-main",
                    strategy_cache=cache, connect=lambda: None,
                    load=lambda *a: {"x": 1}, merge=lambda t, s: t.update(s),
                    log=log)
    assert done is False, "a tick with no DB connection restored nothing"
    assert cache == {}
    red = [m for m, c in lines if c == "red"]
    assert red, f"the silent branch: no red log, only {lines}"
    assert "NOT restored" in red[0] and "no DB connection" in red[0]


def test_a_load_that_raises_is_not_a_restore_either():
    lines, log = _logger()
    conn = Conn()

    def boom(*_a):
        raise RuntimeError("postgres went away")

    done = _restore(run_once_specs=SPECS, instance_id="alpaca-main",
                    strategy_cache={}, connect=lambda: conn, load=boom,
                    merge=lambda t, s: t.update(s), log=log)
    assert done is False
    assert conn.closed, "the connection leaked on the failure path"
    assert [m for m, c in lines if c == "red"], f"no red log, only {lines}"


def test_a_completed_restore_merges_and_reports_done():
    lines, log = _logger()
    conn = Conn()
    cache = {}
    rows = {"strategy_eb": {"_strategy_eb_exit_issued_session": "2026-09-10"}}
    done = _restore(
        run_once_specs=SPECS, instance_id="alpaca-main", strategy_cache=cache,
        connect=lambda: conn, load=lambda c, r, i, name: rows.get(name),
        merge=lambda t, s: t.update(s), log=log)
    assert done is True
    assert cache["strategy_eb"]["_strategy_eb_exit_issued_session"] == "2026-09-10"
    assert conn.closed


def test_a_reachable_db_with_nothing_persisted_still_counts_as_restored():
    """A first boot has no rows. Retrying that every tick would be a query
    storm for an answer that is already correct."""
    done = _restore(run_once_specs=SPECS, instance_id="fresh",
                    strategy_cache={}, connect=lambda: Conn(),
                    load=lambda *a: None, merge=lambda t, s: None, log=None)
    assert done is True


def test_a_lane_with_no_name_is_skipped_without_failing_the_restore():
    done = _restore(run_once_specs=[{"strategy": ""}, None, {"strategy": "strategy_eb"}],
                    instance_id="alpaca-main", strategy_cache={},
                    connect=lambda: Conn(), load=lambda *a: None,
                    merge=lambda t, s: None, log=None)
    assert done is True


def test_a_lane_the_boot_sequence_already_hydrated_is_left_alone():
    """graph_nexus_analysis is restored from its 5-segment snapshot row during
    the live boot. Re-reading its legacy row underneath that would merge keys
    the snapshot deliberately did not carry."""
    read = []
    done = _restore(run_once_specs=SPECS, instance_id="alpaca-main",
                    strategy_cache={"graph_nexus_analysis": {"_deployment_bar_index": 9}},
                    connect=lambda: Conn(),
                    load=lambda c, r, i, name: read.append(name),
                    merge=lambda t, s: None, log=None,
                    skip_lanes={"graph_nexus_analysis"})
    assert done is True
    assert read == ["strategy_eb"], f"queried {read}"


def test_the_loop_gates_the_per_lane_restore_on_its_own_flag():
    """A source assertion: the gate is in the module-level main loop, which
    cannot be AST-extracted. The GNA boot sequence sets
    `_strategy_cache_loaded_from_db` unconditionally, so sharing that flag is
    what made this restore dead code on every live boot."""
    source = open(_BROKER).read()
    assert source.count("_restore_strategy_cache_from_db(") >= 2
    assignments = [line.strip() for line in source.splitlines()
                   if '"_run_once_cache_restored"' in line
                   and "globals()[" in line and "=" in line.split("]", 1)[1]]
    assert assignments, "the done-flag assignment vanished"
    for line in assignments:
        assert "= True" in line
    body = source.split("F1: persist per-strategy strategy_cache", 1)[1][:2000]
    assert 'if _restore_strategy_cache_from_db(' in body, (
        "the done-flag must be conditional on a completed restore")

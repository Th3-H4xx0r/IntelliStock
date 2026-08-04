"""The clean-room baseline preflight (backend/instance.py).

`LIVE_CLEAN_ROOM_MODE` is set host-wide on this deployment, so EVERY instance
requires `initial_value`. When it is absent the adapter correctly refuses to
build — but that refusal used to surface as six broker restarts in 60s, six
Discord alerts, and a latched instance, with a message that never mentions the
field lives on `Instances.<id>`. Both `alpaca-paper-pit` and the crypto soak
`test` hit it on 2026-08-03.

These pin the resolution order (env beats row, for BOTH keys) so this preflight
can never disagree with the broker it protects.
"""
import ast
import os
import pathlib
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = pathlib.Path(_backend) / "instance.py"


def _load():
    """Extract the two pure helpers without importing instance.py.

    instance.py starts a live supervisor at import; the AST-extraction pattern
    is the same one test_residual_sleeve.py and test_core_sleeve_wiring.py use
    for broker.py.
    """
    tree = ast.parse(_SRC.read_text())
    wanted = {"_clean_room_requires_initial_value"}
    ns = {"os": os}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module([node], []), str(_SRC), "exec"), ns)
    return ns["_clean_room_requires_initial_value"]


needs = _load()


# ── the gate fires only when it should ────────────────────────────────────

def test_clean_room_off_never_requires_a_baseline():
    assert needs({}, {}) is False
    assert needs({"initial_value": None}, {"LIVE_CLEAN_ROOM_MODE": "false"}) is False


def test_clean_room_on_with_no_baseline_is_blocked():
    assert needs({}, {"LIVE_CLEAN_ROOM_MODE": "true"}) is True
    assert needs({"initial_value": None}, {"LIVE_CLEAN_ROOM_MODE": "1"}) is True
    assert needs({"initial_value": 0}, {"LIVE_CLEAN_ROOM_MODE": "on"}) is True


def test_a_real_baseline_satisfies_the_gate():
    for value in (6000, 6000.0, "10000", 0.01):
        assert needs({"initial_value": value},
                     {"LIVE_CLEAN_ROOM_MODE": "true"}) is False, value


def test_a_garbage_baseline_is_treated_as_missing():
    """Fail CLOSED: an unparseable value must not read as 'configured'."""
    for bad in ("abc", [], {}, "  "):
        assert needs({"initial_value": bad},
                     {"LIVE_CLEAN_ROOM_MODE": "true"}) is True, bad


# ── resolution order must match broker.py exactly ─────────────────────────

def test_env_overrides_the_row_in_both_directions():
    on = {"LIVE_CLEAN_ROOM_MODE": "true"}
    off = {"LIVE_CLEAN_ROOM_MODE": "false"}
    # env ON beats a row that never opted in
    assert needs({"clean_room_mode": False}, on) is True
    # env OFF beats a row that did opt in
    assert needs({"clean_room_mode": True, "initial_value": None}, off) is False


def test_the_row_decides_when_env_is_unset_or_junk():
    for env in ({}, {"LIVE_CLEAN_ROOM_MODE": ""}, {"LIVE_CLEAN_ROOM_MODE": "maybe"}):
        assert needs({"clean_room_mode": True}, env) is True, env
        assert needs({"clean_room_mode": False}, env) is False, env


def test_LIVE_INITIAL_VALUE_env_satisfies_the_gate():
    """broker.py resolves initial_value env-first, so this must too."""
    assert needs({}, {"LIVE_CLEAN_ROOM_MODE": "true",
                      "LIVE_INITIAL_VALUE": "6000"}) is False


def test_a_malformed_instance_doc_does_not_crash_the_launcher():
    for doc in (None, [], "nope", 7):
        needs(doc, {"LIVE_CLEAN_ROOM_MODE": "true"})


# ── it is actually wired in front of the spawn ────────────────────────────

def test_the_preflight_runs_before_the_broker_is_spawned():
    """Asserted structurally: the check must precede subprocess.Popen, or it
    protects nothing — the point is to refuse to launch, not to notice after."""
    src = _SRC.read_text()
    guard = src.index("_assert_clean_room_initial_value(instance_id")
    spawn = src.index("broker_process = subprocess.Popen")
    assert guard < spawn, "the baseline preflight must run BEFORE the spawn"

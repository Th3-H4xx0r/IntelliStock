"""Per-instance alpha-watchdog opt-in (2026-08-31).

The unified order gate requires watchdog control-health for ALL new
exposure, but the sidecar only launched via deployment env — a run_once
paper instance could never buy (strategy-eb tick #1: every buy blocked on
dependency.watchdog.unknown). The Instances row can now opt in, and the
subprocess env falls back to the instance's own brokerage credentials —
the shared-credential path watchdog_main's docstring explicitly blesses.
"""
import ast
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


#: Shared by `_watchdog_subprocess_env` and `_assert_watchdog_preflight`, so it
#: is always compiled into the extraction namespace — otherwise every caller of
#: `_extract` would have to name it.
_ALWAYS = ("_decrypt_watchdog_credential",)


def _extract(*names):
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    ns = {"os": os, "subprocess": None, "BACKEND_DIR": "/tmp",
          "alpha_watchdog_process": None}
    wanted = set(names) | set(_ALWAYS)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(mod, "instance.py", "exec"), ns)
    return ns


def test_subprocess_env_decrypts_brokerage_credentials(monkeypatch):
    """Stored creds are Fernet ciphertext ('fern…', len 127 in prod) — the
    first sidecar run forwarded ciphertext and auth-failed silently."""
    import secret_store
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.delenv("ALPACA_WATCHDOG_KEY", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_SECRET", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_PAPER", raising=False)
    monkeypatch.setattr(secret_store, "decrypt",
                        lambda v: {"fernK": "PKPLAIN", "fernS": "SECPLAIN"}[v])
    env = ns["_watchdog_subprocess_env"](
        {"alpaca_key": "fernK", "alpaca_secret": "fernS", "alpaca_paper": True})
    assert env["ALPACA_WATCHDOG_KEY"] == "PKPLAIN"
    assert env["ALPACA_WATCHDOG_SECRET"] == "SECPLAIN"
    assert env["ALPACA_WATCHDOG_PAPER"] == "1"


def test_decrypt_failure_never_forwards_ciphertext(monkeypatch):
    import secret_store
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.delenv("ALPACA_WATCHDOG_KEY", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_SECRET", raising=False)
    def _boom(v): raise RuntimeError("no cred key")
    monkeypatch.setattr(secret_store, "decrypt", _boom)
    env = ns["_watchdog_subprocess_env"](
        {"alpaca_key": "fernK", "alpaca_secret": "fernS", "alpaca_paper": True})
    assert "ALPACA_WATCHDOG_KEY" not in env
    assert "ALPACA_WATCHDOG_SECRET" not in env


def test_scoped_env_credentials_win_over_the_brokerage_row(monkeypatch):
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.setenv("ALPACA_WATCHDOG_KEY", "scoped")
    monkeypatch.setenv("ALPACA_WATCHDOG_SECRET", "scopedsec")
    env = ns["_watchdog_subprocess_env"](
        {"alpaca_key": "k1", "alpaca_secret": "s1", "alpaca_paper": False})
    assert env["ALPACA_WATCHDOG_KEY"] == "scoped"
    assert env["ALPACA_WATCHDOG_SECRET"] == "scopedsec"
    assert env["ALPACA_WATCHDOG_PAPER"] == "0"


def test_the_doc_flag_or_env_flag_enables_and_default_stays_off(monkeypatch):
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_maybe_start_alpha_watchdog")
    dump = ast.dump(fn)
    assert "alpha_watchdog_enabled" in dump
    assert "ALPHA_MARK_WATCHDOG_ENABLED" in dump
    # call site passes the docs so the flag is reachable
    assert "_maybe_start_alpha_watchdog(\n            instance_id, _instance_doc, _brokerage_doc)" in src
    # subprocess env comes from the dedicated builder, never bare os.environ
    assert "_watchdog_subprocess_env(brokerage_doc)" in src


def test_pg_dsn_is_synthesized_for_the_sidecar_preflight(monkeypatch):
    """watchdog_main refuses without PG_DSN/PGHOST, but containers carry
    POSTGRES_* parts — the second sidecar run died on this preflight."""
    import db.pool as pool
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.setattr(pool, "dsn_from_env", lambda: "host=x port=5432 user=u dbname=d")
    env = ns["_watchdog_subprocess_env"]({})
    assert env["PG_DSN"] == "host=x port=5432 user=u dbname=d"


def test_existing_pg_dsn_is_left_alone(monkeypatch):
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.setenv("PG_DSN", "host=real")
    env = ns["_watchdog_subprocess_env"]({})
    assert env["PG_DSN"] == "host=real"


# ---------------------------------------------------------------------------
# Funded-Alpaca preflight (2026-09-11)
#
# `alpaca-main` crashed at boot on
#   "funded Alpaca watchdog prerequisites are incomplete:
#    ALPACA_WATCHDOG_KEY,ALPACA_WATCHDOG_SECRET"
# with the host carrying neither -- while `_watchdog_subprocess_env`, the code
# this preflight exists to protect, already falls back to the brokerage row's
# decrypted credentials. A preflight stricter than the code it guards refuses a
# start that would have worked.
# ---------------------------------------------------------------------------

_FUNDED_DOC = {"alpaca_key": "fernK", "alpaca_secret": "fernS",
               "alpaca_paper": False}


def _preflight_env(monkeypatch):
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "1")
    monkeypatch.setenv("RETHINKDB_HOST", "rethink")
    monkeypatch.delenv("ALPACA_WATCHDOG_KEY", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_SECRET", raising=False)


def test_preflight_passes_on_the_scoped_env_pair(monkeypatch):
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)
    monkeypatch.setenv("ALPACA_WATCHDOG_KEY", "scoped")
    monkeypatch.setenv("ALPACA_WATCHDOG_SECRET", "scopedsec")
    assert ns["_assert_watchdog_preflight"]({}) is None


def test_preflight_passes_when_the_brokerage_row_decrypts(monkeypatch):
    """The exact alpaca-main refusal: no env pair, a decryptable row."""
    import secret_store
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)
    monkeypatch.setattr(secret_store, "decrypt",
                        lambda v: {"fernK": "PKPLAIN", "fernS": "SECPLAIN"}[v])
    assert ns["_assert_watchdog_preflight"](_FUNDED_DOC) is None


def test_preflight_refuses_when_the_row_cannot_be_decrypted(monkeypatch):
    import secret_store
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)

    def _boom(_v):
        raise RuntimeError("no cred key")

    monkeypatch.setattr(secret_store, "decrypt", _boom)
    try:
        ns["_assert_watchdog_preflight"](_FUNDED_DOC)
    except RuntimeError as exc:
        assert "ALPACA_WATCHDOG_KEY" in str(exc)
        assert "ALPACA_WATCHDOG_SECRET" in str(exc)
    else:
        raise AssertionError("an undecryptable row must refuse the start")


def test_preflight_refuses_on_a_missing_row(monkeypatch):
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)
    try:
        ns["_assert_watchdog_preflight"](None)
    except RuntimeError as exc:
        assert "ALPACA_WATCHDOG_KEY" in str(exc)
    else:
        raise AssertionError("no credentials anywhere must refuse the start")


def test_preflight_still_requires_the_enable_flag(monkeypatch):
    import secret_store
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "0")
    monkeypatch.setattr(secret_store, "decrypt",
                        lambda v: {"fernK": "PKPLAIN", "fernS": "SECPLAIN"}[v])
    try:
        ns["_assert_watchdog_preflight"](_FUNDED_DOC)
    except RuntimeError as exc:
        assert "ALPHA_MARK_WATCHDOG_ENABLED=1" in str(exc)
    else:
        raise AssertionError("the enable flag is still mandatory")


def test_preflight_still_requires_rethinkdb_host(monkeypatch):
    import secret_store
    ns = _extract("_assert_watchdog_preflight")
    _preflight_env(monkeypatch)
    monkeypatch.delenv("RETHINKDB_HOST", raising=False)
    monkeypatch.setattr(secret_store, "decrypt",
                        lambda v: {"fernK": "PKPLAIN", "fernS": "SECPLAIN"}[v])
    try:
        ns["_assert_watchdog_preflight"](_FUNDED_DOC)
    except RuntimeError as exc:
        assert "RETHINKDB_HOST" in str(exc)
    else:
        raise AssertionError("RETHINKDB_HOST is still mandatory")


def test_preflight_and_the_subprocess_env_share_one_decrypt(monkeypatch):
    """Two copies of the fallback would drift back apart."""
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "_decrypt_watchdog_credential" in names
    for fn_name in ("_watchdog_subprocess_env", "_assert_watchdog_preflight"):
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        assert "_decrypt_watchdog_credential" in ast.dump(fn), fn_name


def test_start_broker_hands_the_brokerage_row_to_the_preflight():
    """A preflight that cannot see the row cannot honour the fallback."""
    src = open(os.path.join(_backend, "instance.py")).read()
    assert "_assert_watchdog_preflight(_brokerage_doc)" in src


# ---------------------------------------------------------------------------
# "Started" must mean RUNNING (2026-09-11)
#
# `watchdog_main` refuses in its own preflight and returns 2 — a process that
# spawned and exited. Popen succeeding said nothing about that, so a funded
# broker kept running beside a dead sidecar and the unified order gate blocked
# every new position on dependency.watchdog.unknown: a real-money account that
# is silently sell-only, with a green "Started alpha mark watchdog" line above
# it. The funded refusal path in `start_broker` only fires on a False return.
# ---------------------------------------------------------------------------

def _instance_module():
    from unittest.mock import MagicMock
    sys.modules.setdefault("socketio", MagicMock())
    import instance
    instance.alpha_watchdog_process = None
    return instance


class _FakeProc:
    def __init__(self, code):
        self._code = code

    def poll(self):
        return self._code

    def terminate(self):
        self._code = -15

    def wait(self, timeout=None):
        return self._code


def test_a_sidecar_that_immediately_refused_is_not_reported_as_started(monkeypatch):
    inst = _instance_module()
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "1")
    monkeypatch.setattr(inst, "WATCHDOG_START_GRACE_SEC", 0.05)
    monkeypatch.setattr(inst.subprocess, "Popen",
                        lambda *a, **k: _FakeProc(2))
    assert inst._maybe_start_alpha_watchdog("alpaca-main", {}, {}) is False
    assert inst.alpha_watchdog_process is None


def test_a_running_sidecar_is_reported_as_started(monkeypatch):
    inst = _instance_module()
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "1")
    monkeypatch.setattr(inst, "WATCHDOG_START_GRACE_SEC", 0.05)
    alive = _FakeProc(None)
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **k: alive)
    assert inst._maybe_start_alpha_watchdog("alpaca-main", {}, {}) is True
    assert inst.alpha_watchdog_process is alive


def test_the_disabled_default_still_short_circuits(monkeypatch):
    inst = _instance_module()
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "0")

    def _must_not_spawn(*_a, **_k):
        raise AssertionError("the watchdog is DISABLED by default")

    monkeypatch.setattr(inst.subprocess, "Popen", _must_not_spawn)
    assert inst._maybe_start_alpha_watchdog("alpaca-main", {}, {}) is False

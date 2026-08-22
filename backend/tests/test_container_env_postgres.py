"""Every container server.py spawns must inherit the Postgres coordinates.

`db/pool.py:dsn_from_env()` reads PG_DSN first and otherwise assembles the DSN
from POSTGRES_*. A container that inherits neither silently falls back to
`host=localhost user=intellistock` -- its OWN loopback -- so after the ReQL
port every store call inside it fails against a database that isn't there.

Six env dicts feed `docker run`: the instance container, the AI agent, the
daily digest, discover, self-learning, and graph nexus. All six are covered
here, the four builder functions directly and the two inline dicts through a
fake docker client that captures `environment=`.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

for _mod in ("socketio", "waitress", "docker"):
    sys.modules.setdefault(_mod, MagicMock())

import server  # noqa: E402


_PG_KEYS = ("PG_DSN", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
            "POSTGRES_PASSWORD", "POSTGRES_DB", "PG_POOL_MIN", "PG_POOL_MAX",
            "PG_POOL_TIMEOUT", "PG_RECONNECT_TIMEOUT", "PG_MAX_ROWS",
            "DB_WATCH_POLL_SECONDS", "PG_SEARCH_PATH",
            "INSTANCE_PG_DSN", "INSTANCE_POSTGRES_HOST")

# The four env builders that are plain functions.
_ENV_BUILDERS = ("_agent_container_env", "_discover_container_env",
                 "_self_learning_container_env", "_nexus_container_env")


@pytest.fixture(autouse=True)
def _clean_pg_env(monkeypatch):
    """No PG/Postgres var leaks in from the developer's shell or .env."""
    for key in _PG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # The builders re-run load_dotenv(); stop it repopulating the environment.
    monkeypatch.setattr(server, "load_dotenv", lambda *a, **k: False)
    yield


def _builders():
    return [(name, getattr(server, name)) for name in _ENV_BUILDERS]


# --- the four builder functions -------------------------------------------

def test_pg_dsn_reaches_every_container_env(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u:p@db:5432/IntelliStock")
    for name, build in _builders():
        env = build()
        assert env.get("PG_DSN") == "postgresql://u:p@db:5432/IntelliStock", name


def test_postgres_parts_reach_every_container_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "intellistock")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "IntelliStock")
    for name, build in _builders():
        env = build()
        assert env.get("POSTGRES_HOST") == "postgres", name
        assert env.get("POSTGRES_PORT") == "5432", name
        assert env.get("POSTGRES_USER") == "intellistock", name
        assert env.get("POSTGRES_PASSWORD") == "s3cret", name
        assert env.get("POSTGRES_DB") == "IntelliStock", name
        assert "PG_DSN" not in env, name


def test_pool_tuning_passes_through(monkeypatch):
    monkeypatch.setenv("PG_POOL_MAX", "16")
    monkeypatch.setenv("PG_MAX_ROWS", "250000")
    monkeypatch.setenv("DB_WATCH_POLL_SECONDS", "0.5")
    for name, build in _builders():
        env = build()
        assert env.get("PG_POOL_MAX") == "16", name
        assert env.get("PG_MAX_ROWS") == "250000", name
        assert env.get("DB_WATCH_POLL_SECONDS") == "0.5", name


def test_unset_keys_are_absent_not_empty():
    """An empty string is not "unset": POSTGRES_HOST='' would override
    pool.py's own default with a DSN fragment that cannot connect."""
    for name, build in _builders():
        env = build()
        for key in ("PG_DSN", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                    "POSTGRES_PASSWORD", "POSTGRES_DB", "PG_POOL_MAX"):
            assert key not in env, "%s leaked an unset %s" % (name, key)


def test_empty_string_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "")
    for name, build in _builders():
        assert "POSTGRES_HOST" not in build(), name


def test_pg_search_path_never_leaks_into_a_container(monkeypatch):
    """PG_SEARCH_PATH is the per-test schema isolation seam. Leaking it would
    point a production container's writes at a test schema."""
    monkeypatch.setenv("PG_DSN", "postgresql://u@db/IntelliStock")
    monkeypatch.setenv("PG_SEARCH_PATH", "t_deadbeef")
    for name, build in _builders():
        assert "PG_SEARCH_PATH" not in build(), name


def test_instance_pg_dsn_overrides_the_inherited_dsn(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u@localhost/IntelliStock")
    monkeypatch.setenv("INSTANCE_PG_DSN", "postgresql://u@postgres/IntelliStock")
    for name, build in _builders():
        assert build()["PG_DSN"] == "postgresql://u@postgres/IntelliStock", name


def test_instance_postgres_host_override_drops_the_inherited_dsn(monkeypatch):
    """dsn_from_env checks PG_DSN first, so an inherited localhost DSN would
    win over the container-reachable host override and silently undo it."""
    monkeypatch.setenv("PG_DSN", "postgresql://u@localhost/IntelliStock")
    monkeypatch.setenv("INSTANCE_POSTGRES_HOST", "postgres")
    for name, build in _builders():
        env = build()
        assert env["POSTGRES_HOST"] == "postgres", name
        assert "PG_DSN" not in env, name


def test_rethinkdb_coordinates_still_shipped(monkeypatch):
    """G2-G11 are unported: ~40 modules still read RETHINKDB_HOST/PORT, so the
    containers must keep receiving them. Drop this test in G12."""
    monkeypatch.setenv("PG_DSN", "postgresql://u@db/IntelliStock")
    for name, build in _builders():
        env = build()
        assert env.get("RETHINKDB_HOST"), name
        assert env.get("RETHINKDB_PORT"), name


# --- the two inline env dicts ---------------------------------------------

class _FakeContainers:
    def __init__(self, sink):
        self._sink = sink

    def run(self, image, **kwargs):
        self._sink.append(kwargs.get("environment") or {})
        return MagicMock(status="running")

    def get(self, name):
        raise RuntimeError("no such container")


class _FakeImages:
    def get(self, image):
        return MagicMock(id="sha256:" + "a" * 64, attrs={})


class _FakeClient:
    def __init__(self, sink):
        self.containers = _FakeContainers(sink)
        self.images = _FakeImages()


def test_digest_container_env_carries_postgres(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u@db:5432/IntelliStock")
    sink = []
    monkeypatch.setattr(server, "_get_docker_client", lambda: _FakeClient(sink))
    monkeypatch.setattr(server, "_get_instance_network", lambda c: "net")
    monkeypatch.setattr(server, "_augment_volumes_with_claude", lambda v: v)
    monkeypatch.setattr(server, "digest_container_obj", None)

    server.start_digest_container()

    assert len(sink) == 1
    assert sink[0]["PG_DSN"] == "postgresql://u@db:5432/IntelliStock"


def test_instance_container_env_carries_postgres(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u@db:5432/IntelliStock")
    # 64 hex chars with >=8 distinct symbols, per derive_socket_control_token
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    monkeypatch.setenv("EQUITIES_INSTANCE_AUTOSTART_ALLOWED", "true")
    sink = []
    digest = "a" * 64
    preflight = server.InstanceLaunchPreflight(
        client=_FakeClient(sink),
        image_id="sha256:" + digest,
        image_digest=digest,
        instance_id="42",
        instance={"id": "42", "kind": "equities"},
        brokerage={},
    )
    monkeypatch.setattr(server, "_preflight_instance_launch", lambda iid: preflight)
    monkeypatch.setattr(server, "_get_instance_network", lambda c: "net")
    monkeypatch.setattr(server, "_augment_volumes_with_claude", lambda v: v)
    monkeypatch.setattr(server, "_remove_existing_instance_container",
                        lambda c, n: None)

    container = server.start_instance_container("42")

    assert container is not None, "launch failed before docker run"
    assert len(sink) == 1
    assert sink[0]["PG_DSN"] == "postgresql://u@db:5432/IntelliStock"
    assert sink[0]["RETHINKDB_HOST"]          # still shipped, see above

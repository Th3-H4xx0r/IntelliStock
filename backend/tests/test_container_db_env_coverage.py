"""Every spawned container must be told where the database is.

The backtest engine was missed when the six server.py spawn sites were wired
up: its container inherited nothing, fell back to db/pool.py's `localhost`
default, and every backtest died on "connection to server at 127.0.0.1, port
5432 failed: Connection refused". The engine's own `localhost` is not the
container's.

This is a source-level check on purpose. The failure needs a real Docker
daemon and a real spawn to reproduce, which no unit test does — so it went to
production instead of to a test.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _spawn_sites():
    """(file, line, env_var) for every containers.run(environment=...) call."""
    out = []
    for path in sorted(_BACKEND.rglob("*.py")):
        if "/tests/" in str(path):
            continue
        lines = path.read_text().split("\n")
        for i, line in enumerate(lines):
            if "containers.run(" not in line or line.strip().startswith("#"):
                continue
            call = "\n".join(lines[i:i + 22])
            m = re.search(r"environment\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", call)
            if m:
                out.append((path, i + 1, m.group(1), "\n".join(lines[max(0, i - 300):i])))
    return out


def test_every_container_spawn_is_given_the_database():
    sites = _spawn_sites()
    assert sites, "found no containers.run sites — the scanner broke, not the code"
    missing = [f"{p.relative_to(_BACKEND)}:{ln} (env={var})"
               for p, ln, var, before in sites
               if "_pg_container_env()" not in before]
    assert not missing, (
        "these container spawns never merge _pg_container_env(), so the "
        "container falls back to localhost and cannot reach Postgres: "
        + ", ".join(missing))


def test_the_helper_forwards_the_coordinates_a_container_needs():
    src = (_BACKEND / "server.py").read_text()
    block = src[src.index("_PG_CONTAINER_ENV_KEYS"):src.index("_PG_CONTAINER_ENV_KEYS") + 700]
    for key in ("PG_DSN", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert key in block, f"{key} is not forwarded into spawned containers"

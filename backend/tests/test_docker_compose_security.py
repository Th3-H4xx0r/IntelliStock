from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_rethinkdb_host_ports_bind_through_a_configurable_address():
    """RethinkDB runs unauthenticated, so its publish address is a security
    control. It was pinned to 127.0.0.1, which made the database unreachable
    from any other host — including over a private Tailscale network — so the
    address is now RETHINKDB_BIND_ADDR. The invariant that still matters is that
    BOTH ports go through that single variable: a bare "28015:28015" would
    publish an unauthenticated database on every interface with no way to
    override it per host.
    """
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    assert '"${RETHINKDB_BIND_ADDR:-0.0.0.0}:28015:28015"' in compose
    assert ('"${RETHINKDB_BIND_ADDR:-0.0.0.0}:${RETHINKDB_WEB_PORT:-8080}:8080"'
            in compose)
    assert '\n      - "28015:28015"' not in compose
    assert '\n      - "8080:8080"' not in compose


def test_backend_requires_socket_control_master_key_during_compose_interpolation():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    backend_service = compose.split("\n  backend:", 1)[1].split("\n  api:", 1)[0]

    assert (
        "SOCKET_CONTROL_MASTER_KEY="
        "${SOCKET_CONTROL_MASTER_KEY:?Set SOCKET_CONTROL_MASTER_KEY in .env "
        "(run install.sh/install.ps1 to auto-generate)}"
    ) in backend_service
    assert "SOCKET_CONTROL_MASTER_KEY=${SOCKET_CONTROL_MASTER_KEY:-" not in compose


def test_env_template_contains_only_a_mandatory_socket_key_placeholder():
    template = (REPO_ROOT / ".env.example").read_text()
    assignments = [
        line for line in template.splitlines()
        if line.startswith("SOCKET_CONTROL_MASTER_KEY=")
    ]

    assert assignments == ["SOCKET_CONTROL_MASTER_KEY=<FILL THIS IN>"]
    assert "openssl rand -hex 32" in template
    assert "stable" in template.lower()


def test_posix_installer_securely_provisions_missing_socket_key_without_a_fallback():
    installer = (REPO_ROOT / "install.sh").read_text()

    assert 'openssl rand -hex 32' in installer
    assert "ensure_socket_control_master_key" in installer
    assert "\nfi\n\nensure_socket_control_master_key\n\n# ── Build & launch" in installer
    assert '[[ "$current_value" =~ ^[0-9a-f]{64}$ ]]' in installer
    assert "SOCKET_CONTROL_MASTER_KEY:-" not in installer
    assert "SOCKET_CONTROL_MASTER_KEY=" in installer
    assert 'echo "$socket_control_master_key"' not in installer
    assert 'printf "%s\\n" "$socket_control_master_key"' not in installer


def test_powershell_installer_securely_provisions_missing_socket_key_without_a_fallback():
    installer = (REPO_ROOT / "install.ps1").read_text()

    assert "New-SocketControlMasterKey" in installer
    assert "New-Object byte[] 32" in installer
    assert "RandomNumberGenerator" in installer
    assert ".ToString('x2')" in installer
    assert "Ensure-SocketControlMasterKey" in installer
    assert (
        "\n}\n\nEnsure-SocketControlMasterKey -Path $EnvFile\n\n"
        "# ── Helper: read a value from .env"
    ) in installer
    assert "-cnotmatch '^[0-9a-f]{64}$'" in installer
    assert "SOCKET_CONTROL_MASTER_KEY:-" not in installer
    assert "Write-Host $socketControlMasterKey" not in installer


def test_postgres_binds_to_loopback_by_default_unlike_rethinkdb():
    """Postgres is authenticated, but the RethinkDB service next to it defaults
    to 0.0.0.0 and copying that default would export a database to every
    interface by accident. The Postgres publish address therefore defaults to
    127.0.0.1; an operator who needs Tailscale access sets POSTGRES_BIND_ADDR
    explicitly. A bare "5432:5432" would publish it with no way to override.
    """
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert '"${POSTGRES_BIND_ADDR:-127.0.0.1}:5432:5432"' in compose
    assert '\n      - "5432:5432"' not in compose
    assert "${POSTGRES_BIND_ADDR:-0.0.0.0}" not in compose


def test_postgres_password_is_mandatory_at_compose_interpolation():
    """":?" makes compose refuse to start rather than silently booting a
    database with an empty password."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}" in compose


def test_postgres_declares_shm_size():
    """The Docker default /dev/shm is 64MB and is NOT shared_buffers; parallel
    scans fail with a confusing error without an explicit shm_size."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "shm_size: 1gb" in compose


# ---------------------------------------------------------------------------
# Postgres wiring, asserted against the PARSED compose file.
#
# The string assertions above are security invariants: a substring is exactly
# the right tool for "this literal must never appear". Wiring is different --
# "every backend service can reach the database" is a statement about the
# structure, and a substring check passes just as happily when the variable
# landed in the wrong service's block.
# ---------------------------------------------------------------------------

import yaml  # noqa: E402


# Services that talk to the store. Each one declares RETHINKDB_* today and must
# declare the POSTGRES_* twin, because the flip is an env change, not a rebuild.
STORE_SERVICES = ("backend", "api", "price-service", "backtest-engine",
                  "discord-bot")

# The env dsn_from_env() assembles a DSN from when PG_DSN is unset.
REQUIRED_PG_ENV = {
    "POSTGRES_HOST": "postgres",
    "POSTGRES_PORT": "5432",
}


def _compose():
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def _env(service: dict) -> dict:
    """Compose accepts both `KEY=value` list form and mapping form."""
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    out = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


def test_every_store_service_gets_the_postgres_coordinates():
    services = _compose()["services"]
    for name in STORE_SERVICES:
        env = _env(services[name])
        for key, value in REQUIRED_PG_ENV.items():
            assert env.get(key) == value, "%s is missing %s=%s" % (name, key, value)
        for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            assert key in env, "%s is missing %s" % (name, key)


def test_every_store_service_keeps_its_rethinkdb_coordinates():
    """The rollback is unsetting PG_DSN, which only works while the old
    variables are still there. Deleting them is a later, separate decision."""
    services = _compose()["services"]
    for name in STORE_SERVICES:
        env = _env(services[name])
        assert env.get("RETHINKDB_HOST") == "rethinkdb", name
        assert env.get("RETHINKDB_PORT") == "28015", name


def test_container_spawning_services_forward_an_instance_postgres_host():
    """server.py and backtest_engine.py spawn containers whose `localhost` is
    not the server's. INSTANCE_POSTGRES_HOST is the escape hatch
    _pg_container_env() reads; without it every spawned broker dials its own
    loopback and finds nothing."""
    services = _compose()["services"]
    for name in ("backend", "backtest-engine"):
        env = _env(services[name])
        assert env.get("INSTANCE_POSTGRES_HOST") == "postgres", name
        assert env.get("INSTANCE_RETHINKDB_HOST") == "rethinkdb", name


def test_every_store_service_waits_for_postgres():
    services = _compose()["services"]
    for name in STORE_SERVICES:
        depends = services[name].get("depends_on") or []
        names = list(depends) if isinstance(depends, dict) else [str(d) for d in depends]
        assert "postgres" in names, "%s does not depend_on postgres" % name
        assert "rethinkdb" in names, "%s dropped its rethinkdb dependency" % name


def test_postgres_service_is_built_from_the_partman_image():
    postgres = _compose()["services"]["postgres"]
    assert postgres["build"]["context"] == "./docker/postgres"
    dockerfile = (REPO_ROOT / "docker/postgres/Dockerfile").read_text()
    assert "FROM postgres:17" in dockerfile
    assert "postgresql-17-partman" in dockerfile


def test_postgres_memory_settings_are_internally_consistent():
    """shared_buffers is 25% of the container's memory limit. They are set in
    two different places, so nothing but a test keeps them in step."""
    postgres = _compose()["services"]["postgres"]
    assert postgres["shm_size"] == "1gb"
    limits = postgres["deploy"]["resources"]["limits"]
    assert limits["memory"] == "4G"
    assert "-c shared_buffers=1GB" in postgres["command"]


def test_postgres_is_not_the_oom_killers_first_choice():
    """The postmaster resets its own oom_score_adj for child backends. Without
    these the kernel kills the postmaster, which takes the whole cluster down
    instead of one backend."""
    env = _env(_compose()["services"]["postgres"])
    assert env["PG_OOM_ADJUST_FILE"] == "/proc/self/oom_score_adj"
    assert env["PG_OOM_ADJUST_VALUE"] == "0"


def test_postgres_has_a_readiness_healthcheck():
    postgres = _compose()["services"]["postgres"]
    probe = " ".join(postgres["healthcheck"]["test"])
    assert "pg_isready" in probe


def test_the_rethinkdb_service_and_its_volume_survive_the_port():
    compose = _compose()
    assert "rethinkdb" in compose["services"]
    assert "rethinkdb_data" in compose["volumes"]
    assert "postgres_data" in compose["volumes"]


def test_env_template_documents_the_postgres_password():
    template = (REPO_ROOT / ".env.example").read_text()
    assert "POSTGRES_PASSWORD=<FILL THIS IN>" in template
    assert "PG_DSN" in template


def test_migration_mismatch_dumps_are_never_committed():
    """A mismatch dump is two whole documents, verbatim, including anything the
    row happened to hold. It is evidence to read, not source to commit."""
    ignored = (REPO_ROOT / ".gitignore").read_text()
    assert ".migration-mismatches/" in ignored
    assert ".devpg/" in ignored

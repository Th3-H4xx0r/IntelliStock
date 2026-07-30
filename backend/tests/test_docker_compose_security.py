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

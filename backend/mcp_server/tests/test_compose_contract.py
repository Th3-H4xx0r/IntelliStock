"""The deployment contract (contract section 7) as docker-compose.yml, the Dockerfile and .env.example state it.

The mcp container holds the auth store and the service token. It joins mcp_net
and nothing else, publishes only on the host's loopback (tailscale serve fronts
it), and gets an explicit environment: no env_file, so none of the stack's other
secrets reach it. The api joins mcp_net beside its default network, which is how
the MCP server reaches the API's private port.
"""
import re
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]

# Contract section 7, "mcp service".
CONTRACT_ENV = {"MCP_API_BASE", "MCP_SERVICE_TOKEN", "MCP_PUBLIC_BASE", "MCP_RP_ID", "MCP_DATA_DIR",
                "MCP_JARVIS_CLIENT_ID", "MCP_JARVIS_REDIRECT_URI", "MCP_TOTP_ENC_KEY"}
FOREIGN_SECRET = re.compile(r"POSTGRES|RETHINK|NEO4J|JWT_SECRET|INTELLISTOCK_CRED_KEY|ALPACA|KALSHI|OPENAI|ANTHROPIC")
# The .env.example assignments the MCP block documents, values as committed.
TEMPLATE_ASSIGNMENTS = {"MCP_SERVICE_TOKEN=", "MCP_PUBLIC_BASE=", "MCP_RP_ID=", "MCP_JARVIS_CLIENT_ID=jarvis",
                        "MCP_JARVIS_REDIRECT_URI=", "MCP_TOTP_ENC_KEY=", "MCP_EXTRA_ALLOWED_HOSTS="}


def _compose():
    return yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


def _networks(service):
    """A service's network names; compose accepts a list or a mapping."""
    raw = service.get("networks") or []
    return list(raw)


def _env(service):
    """A service's environment as {key: value}; compose accepts KEY=value items or a mapping."""
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    pairs = (str(item).partition("=") for item in raw)
    return {key: value for key, _, value in pairs}


def test_mcp_service_isolated():
    mcp = _compose()["services"]["mcp"]
    assert _networks(mcp) == ["mcp_net"]
    assert mcp["ports"] == ["127.0.0.1:8090:8090"]
    assert "labels" not in mcp  # no Traefik route: tailscale serve on the host is the only way in
    assert "env_file" not in mcp
    assert mcp["volumes"] == ["mcp_data:/data"]  # and so no docker.sock
    assert mcp["read_only"] is True
    assert mcp["tmpfs"] == ["/tmp"]
    assert mcp["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in mcp["security_opt"]
    assert mcp["image"] == "intellistock-mcp"
    assert mcp["container_name"] == "intellistock-mcp"
    assert mcp["build"] == {"context": "./backend/mcp_server", "dockerfile": "Dockerfile"}
    assert mcp["depends_on"] == ["api"]


def test_mcp_resources_and_named_volume():
    compose = _compose()
    mcp = compose["services"]["mcp"]
    assert mcp["deploy"]["resources"]["limits"] == {"cpus": "0.5", "memory": "256M"}
    assert mcp["oom_score_adj"] == 1000  # lose this before the live broker under memory pressure
    assert compose["volumes"]["mcp_data"] == {"name": "intellistock_mcp_data"}


def test_only_api_and_mcp_join_mcp_net():
    services = _compose()["services"]
    assert {name for name, service in services.items() if "mcp_net" in _networks(service)} == {"api", "mcp"}


def test_api_keeps_default_network():
    # Declaring networks drops the implicit default, which every other service and Postgres are on.
    api = _compose()["services"]["api"]
    assert set(_networks(api)) >= {"default", "mcp_net"}


def test_mcp_net_fixed_subnet():
    net = _compose()["networks"]["mcp_net"]
    assert net["name"] == "intellistock_mcp_net"  # a changed project slug cannot make a second, overlapping one
    assert net["driver"] == "bridge"
    assert net["ipam"]["config"][0]["subnet"] == "172.31.250.0/24"  # the api's MCP_ALLOWED_CIDR
    assert not net.get("internal"), "an internal network cannot publish 127.0.0.1:8090"


def test_mcp_env_is_explicit_and_secret_free():
    env = _env(_compose()["services"]["mcp"])
    assert set(env) >= CONTRACT_ENV
    assert [key for key in env if not key.startswith("MCP_")] == []
    assert [key for key in env if FOREIGN_SECRET.search(key)] == []
    # The CI deployment-contract job renders compose from .env.example, where these are empty.
    assert [key for key, value in env.items() if ":?" in value] == []
    assert env["MCP_DATA_DIR"] == "/data"
    assert env["MCP_API_BASE"] == "${MCP_API_BASE:-http://api:8012}"


def test_env_template_documents_each_mcp_variable_once():
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line.startswith("MCP_")]
    assert set(assignments) >= TEMPLATE_ASSIGNMENTS
    names = [line.split("=", 1)[0] for line in assignments]
    assert sorted(name for name in set(names) if names.count(name) > 1) == []
    assert [line for line in assignments if "#" in line] == []  # comments go on their own lines


def test_dockerfile_hash_locked_non_root():
    dockerfile = (PKG / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(r"^FROM\s+(\S+)", dockerfile, re.M) == ["python:3.11-slim"]
    assert "--require-hashes" in dockerfile
    users = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)
    assert users and users[-1].split(":")[0] == "10001"  # the last USER is the one the server runs as
    assert "COPY .." not in dockerfile  # the build context is backend/mcp_server alone
    assert 'CMD ["python", "-m", "mcp_server"]' in dockerfile
    assert "--workers" not in dockerfile  # sign-in's throttle locks live in one process
    assert "EXPOSE 8090" in dockerfile
    assert re.search(r"^HEALTHCHECK .*\n?.*http://127\.0\.0\.1:8090/healthz", dockerfile, re.M)
    ignored = (PKG / ".dockerignore").read_text(encoding="utf-8").split()
    assert set(ignored) >= {"tests/", "__pycache__/", "*.pyc", "requirements*.in", "requirements-test.txt",
                            "pytest.ini"}

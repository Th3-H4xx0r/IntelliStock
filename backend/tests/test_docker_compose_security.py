from pathlib import Path


def test_rethinkdb_host_ports_bind_only_to_loopback():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    assert '"127.0.0.1:28015:28015"' in compose
    assert '"127.0.0.1:${RETHINKDB_WEB_PORT:-8080}:8080"' in compose
    assert '\n      - "28015:28015"' not in compose

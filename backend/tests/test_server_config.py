"""Uvicorn startup: whose X-Forwarded-For the API believes.

Behind Traefik every request reaches the API from Traefik's container address,
so the login limiter keyed on ``request.client.host`` put every client in one
bucket: ten bad guesses from anyone locked the operator out. Uvicorn can hand
the app the real client from X-Forwarded-For, but only from peers it trusts,
and trusting everyone (``*``) would let a caller on the host-published :8011
forge any address it likes. ``api.server_config`` trusts exactly the proxies
named in FORWARDED_ALLOW_IPS and refuses the "trust everything" spellings.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from unittest.mock import Mock

import pytest
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api.server_config import (  # noqa: E402
    build_uvicorn_config,
    run_server,
    trusted_proxy_configured,
    trusted_proxy_entries,
)
from api import server_config  # noqa: E402


async def tiny_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@contextlib.contextmanager
def _logging_state_restored():
    """Put back what building a uvicorn.Config does to logging.

    The Config constructor runs dictConfig(uvicorn's LOGGING_CONFIG). That gives
    the uvicorn loggers their own handlers and levels, stops them propagating,
    and sets ``disabled = False`` on every other existing logger. Left in place
    for the rest of the run, caplog on "uvicorn.error" in a later test file sees
    nothing (test_brokerage_place_order.py did). Root's handlers are left alone:
    pytest attaches and detaches its capture handlers there each test phase, and
    dictConfig detaches none.
    """
    manager = logging.Logger.manager

    def is_uvicorn(name):
        return name == "uvicorn" or name.startswith("uvicorn.")

    loggers = {n: lg for n, lg in manager.loggerDict.items() if isinstance(lg, logging.Logger)}
    saved = {n: (lg.level, list(lg.handlers), lg.propagate, lg.disabled)
             for n, lg in loggers.items() if is_uvicorn(n)}
    saved_disabled = {n: lg.disabled for n, lg in loggers.items()}
    root_level, root_disabled = logging.root.level, logging.root.disabled
    try:
        yield
    finally:
        for name, lg in list(manager.loggerDict.items()):
            if not isinstance(lg, logging.Logger):
                continue
            if is_uvicorn(name):
                # A uvicorn logger dictConfig created goes back to the defaults.
                level, handlers, propagate, disabled = saved.get(name, (logging.NOTSET, [], True, False))
                lg.setLevel(level)
                lg.handlers[:] = handlers
                lg.propagate = propagate
                lg.disabled = disabled
            elif name in saved_disabled:
                lg.disabled = saved_disabled[name]
        logging.root.setLevel(root_level)
        logging.root.disabled = root_disabled


@pytest.fixture(autouse=True)
def _restore_logging():
    with _logging_state_restored():
        yield


@pytest.fixture(autouse=True)
def _no_ambient_proxy_setting(monkeypatch):
    # api/main.py runs load_dotenv at import, so a developer's .env could set
    # this for every test after the first that imports the app.
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)


@pytest.fixture
def served(store, monkeypatch):
    """The real app behind the listener build_uvicorn_config makes, with auth
    wired to the test store and 10.0.0.0/8 (Traefik's side) trusted."""
    import auth_utils
    from api import main

    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-the-proxy-tests")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "ensure_users_table", lambda conn=None: None)
    auth_utils.reset_login_rate_limit()
    monkeypatch.setattr(main, "_UNNAMED_PROXY_WARNED", set(), raising=False)
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
        cfg = build_uvicorn_config(main.app, host="127.0.0.1", port=8011, proxy_headers=True)
        cfg.load()
        auth_utils.create_user(None, "root", "the-right-password")
        yield cfg.loaded_app, auth_utils
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        auth_utils.reset_login_rate_limit()


def _via(app, peer):
    from fastapi.testclient import TestClient
    return TestClient(app, client=(peer, 50000))


# --- parsing FORWARDED_ALLOW_IPS --------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (None, ["127.0.0.1"]), ("", ["127.0.0.1"]), ("  ", ["127.0.0.1"]),
    ("10.0.1.0/24, 172.18.0.5 ,,", ["10.0.1.0/24", "172.18.0.5"]),
    ("10.0.1.5/24", ["10.0.1.0/24"]), ("fd00::/64", ["fd00::/64"]),
    ("*", ["127.0.0.1"]), ("0.0.0.0/0,10.0.1.0/24", ["10.0.1.0/24"]), ("::/0", ["127.0.0.1"]),
    ("traefik,10.0.1.300", ["127.0.0.1"]),
])
def test_trusted_proxy_entries(raw, expected):
    assert trusted_proxy_entries(raw) == expected


def test_unspecified_addresses_are_refused_like_the_wildcard():
    # "0.0.0.0" reads as "everything" to an operator but trusts no real peer,
    # while still switching the per-IP login limit on for a shared address.
    assert trusted_proxy_entries("0.0.0.0, ::") == ["127.0.0.1"]
    assert trusted_proxy_configured("0.0.0.0") is False


def test_each_refused_entry_warns_once(monkeypatch):
    log = Mock()
    monkeypatch.setattr(server_config, "_log", log)
    monkeypatch.setattr(server_config, "_WARNED", set())
    raw = "*, traefik, 0.0.0.0/0, 10.0.1.0/24"
    trusted_proxy_entries(raw)
    trusted_proxy_entries(raw)            # called per login request in Task 6: no log spam
    warned = [c.args[1] for c in log.warning.call_args_list]
    assert sorted(warned) == ["*", "0.0.0.0/0", "traefik"]
    assert all("FORWARDED_ALLOW_IPS" in c.args[0] for c in log.warning.call_args_list)


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("127.0.0.1", False), ("::1,127.0.0.1", False), ("*", False), ("traefik", False),
    ("10.0.1.0/24", True), ("127.0.0.1,10.0.1.5", True), ("fd00::/64", True),
])
def test_trusted_proxy_configured(raw, expected):
    assert trusted_proxy_configured(raw) is expected


def test_trusted_proxy_configured_reads_the_environment(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    assert trusted_proxy_configured() is True
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    assert trusted_proxy_configured() is False


# --- the uvicorn listener ----------------------------------------------------


def test_proxy_listener_trusts_only_the_configured_proxies(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    cfg = build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert (cfg.host, cfg.port, cfg.proxy_headers, cfg.forwarded_allow_ips) == ("0.0.0.0", 8011, True, "10.0.1.0/24")
    cfg.load(); assert isinstance(cfg.loaded_app, ProxyHeadersMiddleware)


def test_a_wildcard_in_the_environment_never_reaches_uvicorn(monkeypatch):
    # uvicorn reads FORWARDED_ALLOW_IPS itself when not handed a value, and
    # would honour "*"; the builder always hands it the filtered list.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    cfg = build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert cfg.forwarded_allow_ips == "127.0.0.1"


def test_listener_without_proxy_headers_never_rewrites(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    cfg = build_uvicorn_config(tiny_app, host="0.0.0.0", port=8012, proxy_headers=False)
    cfg.load(); assert cfg.proxy_headers is False and not isinstance(cfg.loaded_app, ProxyHeadersMiddleware)


def test_unset_forwarded_allow_ips_warns(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    log = Mock(); monkeypatch.setattr(server_config, "_log", log)
    build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert "FORWARDED_ALLOW_IPS" in log.warning.call_args.args[0]


@pytest.mark.parametrize("raw", ["", "127.0.0.1", "::1,127.0.0.1", "*"])
def test_a_value_that_names_no_proxy_warns_too(monkeypatch, raw):
    # docker-compose defaults the variable to 127.0.0.1, so in production it is
    # never unset; the warning has to fire for loopback-only values as well.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", raw)
    log = Mock(); monkeypatch.setattr(server_config, "_log", log)
    build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert "FORWARDED_ALLOW_IPS names no reverse proxy" in log.warning.call_args.args[0]


def test_a_configured_proxy_logs_what_it_trusts_and_does_not_warn(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    log = Mock(); monkeypatch.setattr(server_config, "_log", log)
    build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    log.info.assert_called_once_with("Proxy headers: trusting X-Forwarded-For from %s", "10.0.1.0/24")
    log.warning.assert_not_called()


def test_building_a_config_leaves_logging_as_it_found_it(caplog):
    uv = logging.getLogger("uvicorn")
    before = (uv.level, list(uv.handlers), uv.propagate)
    with _logging_state_restored():
        build_uvicorn_config(tiny_app, host="127.0.0.1", port=8011, proxy_headers=True)
        assert uv.propagate is False                    # dictConfig really ran
    assert (uv.level, list(uv.handlers), uv.propagate) == before
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        logging.getLogger("uvicorn.error").info("still reaches caplog")
    assert "still reaches caplog" in caplog.text


def test_run_server_exits_3_when_startup_fails(monkeypatch):
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)   # never sets started
    with pytest.raises(SystemExit) as e:
        run_server(build_uvicorn_config(tiny_app, host="127.0.0.1", port=8011, proxy_headers=False))
    assert e.value.code == 3


def test_run_server_returns_after_a_clean_run(monkeypatch):
    def ran(self, sockets=None): self.started = True
    monkeypatch.setattr(uvicorn.Server, "run", ran)
    assert run_server(build_uvicorn_config(tiny_app, host="127.0.0.1", port=8011, proxy_headers=False)) is None


def test_main_entrypoint_uses_the_builder():
    src = open(os.path.join(BACKEND, "api", "main.py"), encoding="utf-8").read()
    tail = src[src.index('if __name__ == "__main__":'):]
    assert "build_uvicorn_config(" in tail and "proxy_headers=True" in tail and "uvicorn.run(" not in tail


# --- the login limiter, through the real listener ----------------------------


BAD = {"username": "root", "password": "wrong-password"}


def test_one_clients_failures_do_not_lock_out_the_operator(served):
    app, _ = served
    via_traefik = _via(app, "10.0.1.2")
    for _ in range(10):
        assert via_traefik.post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 401
    assert via_traefik.post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 429
    ok = via_traefik.post("/auth/login", json={"username": "root", "password": "the-right-password"},
                          headers={"X-Forwarded-For": "198.51.100.7"})
    assert ok.status_code == 200                                   # human JWT login still works
    me = via_traefik.get("/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}",
                                              "X-Forwarded-For": "198.51.100.7"})
    assert me.status_code == 200 and me.json()["username"] == "root"


def test_multi_hop_forwarded_for_keys_on_the_rightmost_untrusted_hop(served):
    app, auth_utils = served
    for _ in range(10):
        _via(app, "10.0.1.2").post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5, 10.0.1.9"})
    assert auth_utils.login_is_rate_limited("root", "203.0.113.5")
    assert not auth_utils.login_is_rate_limited("root", "10.0.1.9")


def test_forwarded_for_from_an_untrusted_peer_is_ignored(served):   # straight to the host-published :8011
    app, auth_utils = served
    direct = _via(app, "198.51.100.9")
    for i in range(10):
        direct.post("/auth/login", json=BAD, headers={"X-Forwarded-For": f"192.0.2.{i}"})
    assert auth_utils.login_is_rate_limited("root", "198.51.100.9")


def test_an_unnamed_hop_cannot_lock_everyone_out(served):
    # The frontend nginx /api/ fallback or a tailscale serve path: it sends
    # X-Forwarded-For, but its address is outside 10.0.0.0/8, so uvicorn keeps
    # the hop's address and every client behind it would share one per-IP count.
    app, _ = served
    via_hop = _via(app, "172.18.0.9")
    for i in range(30):
        bad = {"username": f"user{i}", "password": "wrong-password"}
        assert via_hop.post("/auth/login", json=bad, headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code == 401
    ok = via_hop.post("/auth/login", json={"username": "root", "password": "the-right-password"},
                      headers={"X-Forwarded-For": "198.51.100.7"})
    assert ok.status_code == 200



def test_listing_the_hops_address_locks_out_no_one_else(served):
    # nginx's $proxy_add_x_forwarded_for appends the address it saw to the
    # client's own header, so a client behind the hop can write the hop's
    # address into every entry but the last. Those requests then count against
    # the hop's address, but the operator's request does not list the hop, so
    # it is never checked against that count.
    app, _ = served
    via_hop = _via(app, "172.18.0.9")
    for i in range(30):
        bad = {"username": f"user{i}", "password": "wrong-password"}
        forged = {"X-Forwarded-For": f"172.18.0.9, 203.0.113.{i}"}
        assert via_hop.post("/auth/login", json=bad, headers=forged).status_code == 401
    ok = via_hop.post("/auth/login", json={"username": "root", "password": "the-right-password"},
                      headers={"X-Forwarded-For": "198.51.100.7"})
    assert ok.status_code == 200


def test_through_a_named_proxy_the_per_ip_count_still_applies(served):
    # X-Forwarded-For with a port: uvicorn hands the route the bare host, which
    # must still count as a rewritten request.
    app, _ = served
    via_traefik = _via(app, "10.0.1.2")
    client = {"X-Forwarded-For": "203.0.113.5:4711"}
    for i in range(30):
        bad = {"username": f"user{i}", "password": "wrong-password"}
        assert via_traefik.post("/auth/login", json=bad, headers=client).status_code == 401
    right = {"username": "root", "password": "the-right-password"}
    assert via_traefik.post("/auth/login", json=right, headers=client).status_code == 429
    assert via_traefik.post("/auth/login", json=right, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200

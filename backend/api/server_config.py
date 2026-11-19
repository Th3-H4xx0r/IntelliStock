"""How the API's uvicorn listener is built: whose X-Forwarded-For it believes.

Behind Traefik every request reaches the API from Traefik's container address.
Uvicorn rewrites ``request.client`` from X-Forwarded-For, but only for peers
named in FORWARDED_ALLOW_IPS, so that variable decides whether the login
limiter sees the real client or one shared proxy address.

The filtering here refuses the spellings that mean "trust every peer" (``*``,
``0.0.0.0/0``, ``::/0``, the unspecified addresses) and anything that is not
an IP or CIDR. Trusting every peer would let a caller who reaches the
host-published port directly send any X-Forwarded-For it likes and pick its
own rate-limit bucket. Uvicorn is always handed the filtered list, never the
raw variable, which it would otherwise read itself and honour ``*`` from.

``serve`` runs the app on a second, MCP-only listener (MCP_API_PORT, contract
§2) beside the public one when MCP is usable, and never on the public port.
That listener binds IPv4 only and never believes X-Forwarded-For: the gate
checks the raw socket peer against MCP_ALLOWED_CIDR and refuses an IPv4-mapped
IPv6 peer, which a dual-stack bind would hand it. The public server keeps the
signal handlers and the app's startup and shutdown hooks; the MCP server has
neither, and stops when the public server does. Its failure never takes the
public listener down.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
from collections.abc import Iterator, Mapping
from typing import List, Optional

import uvicorn

from mcp_policy import McpConfig

_log = logging.getLogger("uvicorn.error")

_LOOPBACK_ONLY = ["127.0.0.1"]

# Dropped entries already warned about. The variable is read on every login
# request (trusted_proxy_configured), and a bad entry must not log each time.
_WARNED: set = set()

# Logged at startup whenever the variable names no proxy: unset, blank, or
# loopback only. docker-compose defaults it to 127.0.0.1, so "unset" alone
# would never fire in production.
_NO_PROXY_WARNING = (
    "FORWARDED_ALLOW_IPS names no reverse proxy: X-Forwarded-For is trusted "
    "only from %s, so behind a reverse proxy every client shares the proxy's "
    "address; the per-IP login limit stays off until it names one."
)


def _drop(entry: str, why: str) -> None:
    if entry in _WARNED:
        return
    _WARNED.add(entry)
    _log.warning("FORWARDED_ALLOW_IPS: ignoring %r (%s)", entry, why)


def trusted_proxy_entries(raw: Optional[str]) -> List[str]:
    """The proxy IPs and CIDRs from a FORWARDED_ALLOW_IPS value.

    Comma-separated; blanks are skipped. CIDRs are normalised with
    ``strict=False`` (``10.0.1.5/24`` becomes ``10.0.1.0/24``), because
    uvicorn parses them strictly and would otherwise keep a host-bits CIDR as
    a literal string that never matches a peer. Wildcards, hostnames and
    invalid entries are dropped with a warning. Nothing left means loopback
    only, which is uvicorn's own default.
    """
    kept: List[str] = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry == "*":
            _drop(entry, "it trusts every peer")
            continue
        try:
            if "/" in entry:
                net = ipaddress.ip_network(entry, strict=False)
                if net.prefixlen == 0:
                    _drop(entry, "it trusts every peer")
                    continue
                kept.append(str(net))
            else:
                addr = ipaddress.ip_address(entry)
                if addr.is_unspecified:
                    _drop(entry, "an unspecified address is not a proxy")
                    continue
                kept.append(entry)
        except ValueError:
            _drop(entry, "not an IP address or CIDR; hostnames are not resolved")
    return kept or list(_LOOPBACK_ONLY)


def trusted_proxy_configured(raw: Optional[str] = None) -> bool:
    """True when FORWARDED_ALLOW_IPS names at least one non-loopback proxy.

    Only then does uvicorn hand the app real client addresses from behind a
    reverse proxy; unset, loopback-only or garbage means it does not.
    """
    if raw is None:
        raw = os.environ.get("FORWARDED_ALLOW_IPS")
    return _names_a_proxy(trusted_proxy_entries(raw))


def _names_a_proxy(entries: List[str]) -> bool:
    return any(not ipaddress.ip_network(entry, strict=False).is_loopback for entry in entries)


def build_uvicorn_config(app, *, host: str, port: int, proxy_headers: bool) -> uvicorn.Config:
    """The uvicorn config for one listener.

    ``proxy_headers=True`` is the Traefik-facing listener: X-Forwarded-For is
    believed from the filtered FORWARDED_ALLOW_IPS only. ``False`` never
    rewrites the client address, whatever the environment says.
    """
    if not proxy_headers:
        return uvicorn.Config(app, host=host, port=port, proxy_headers=False)
    entries = trusted_proxy_entries(os.environ.get("FORWARDED_ALLOW_IPS"))
    trusted = ",".join(entries)
    # Built before logging: constructing the Config installs uvicorn's log
    # handlers, without which an INFO line here would go nowhere.
    config = uvicorn.Config(app, host=host, port=port, proxy_headers=True,
                            forwarded_allow_ips=trusted)
    _log.info("Proxy headers: trusting X-Forwarded-For from %s", trusted)
    if not _names_a_proxy(entries):
        _log.warning(_NO_PROXY_WARNING, trusted)
    return config


def run_server(config: uvicorn.Config) -> None:
    """Serve until shutdown, keeping ``uvicorn.run``'s exit on startup failure."""
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        # uvicorn re-raises a caught SIGINT after a graceful shutdown;
        # uvicorn.run swallows it, and so does this.
        pass
    if not server.started:
        raise SystemExit(3)   # uvicorn.main.STARTUP_FAILURE


def build_mcp_uvicorn_config(app, *, port: int) -> uvicorn.Config:
    """The uvicorn config for the MCP-only listener.

    IPv4 only (0.0.0.0) and no proxy headers, so the gate sees the raw socket
    peer. Lifespan is off, so the app's startup and shutdown hooks run once, on
    the public listener; and no Server header names the software.
    """
    config = build_uvicorn_config(app, host="0.0.0.0", port=port, proxy_headers=False)
    config.lifespan = "off"
    config.server_header = False
    return config


class _NoSignalServer(uvicorn.Server):
    """A uvicorn server that installs no signal handlers. The public server owns
    SIGINT and SIGTERM, and _serve_both stops this one when the public one ends."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def _serve_mcp(mcp: uvicorn.Server) -> None:
    """Serve the MCP listener; if it stops with an error, log it and return.

    uvicorn calls sys.exit(1) when it cannot bind its port. Uncaught in its
    task, that SystemExit would stop the event loop, and the public listener
    with it.
    """
    try:
        await mcp.serve()
    except (Exception, SystemExit) as exc:
        _log.error("MCP listener stopped (%s); the public listener keeps serving",
                   type(exc).__name__, exc_info=not isinstance(exc, SystemExit))


async def _serve_both(public: uvicorn.Server, mcp: uvicorn.Server) -> None:
    """Serve the public listener, with the MCP listener beside it until it ends."""
    mcp_task = asyncio.create_task(_serve_mcp(mcp))
    try:
        await public.serve()
    finally:
        mcp.should_exit = True
        await mcp_task


def _loop_factory(config: uvicorn.Config):
    """The event loop uvicorn.run would use for this config: uvloop under
    uvicorn[standard], else asyncio's own."""
    get_loop_factory = getattr(config, "get_loop_factory", None)
    return get_loop_factory() if get_loop_factory is not None else None


def serve(app, public_config: uvicorn.Config, *, environ: Optional[Mapping[str, str]] = None) -> None:
    """Serve the public listener, plus the MCP-only listener when MCP is usable.

    MCP off, or on without a usable token or CIDR: the public listener alone,
    exactly as run_server. MCP_API_PORT equal to the public port: an ERROR and
    the public listener alone. Otherwise both, in one event loop, keeping
    run_server's exit codes: SIGINT ends quietly, and 3 when the public listener
    never started.
    """
    cfg = McpConfig.from_env(environ)
    if not cfg.usable:
        if cfg.enabled:
            _log.warning("MCP_ENABLED is set, but MCP_SERVICE_TOKEN (at least 32 bytes) or "
                         "MCP_ALLOWED_CIDR is not usable; MCP listener off")
        else:
            _log.info("MCP listener off")
        run_server(public_config)
        return
    if cfg.port == public_config.port:
        _log.error("MCP_API_PORT must differ from API_PORT; MCP listener off")
        run_server(public_config)
        return
    public = uvicorn.Server(public_config)
    mcp = _NoSignalServer(build_mcp_uvicorn_config(app, port=cfg.port))
    try:
        with asyncio.Runner(loop_factory=_loop_factory(public_config)) as runner:
            runner.run(_serve_both(public, mcp))
    except KeyboardInterrupt:
        # As in run_server: uvicorn re-raises a caught SIGINT after a graceful
        # shutdown, and uvicorn.run swallows it.
        pass
    if not public.started:
        raise SystemExit(3)   # uvicorn.main.STARTUP_FAILURE

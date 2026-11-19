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
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import List, Optional

import uvicorn

_log = logging.getLogger("uvicorn.error")

_LOOPBACK_ONLY = ["127.0.0.1"]

# Dropped entries already warned about. The variable is read on every login
# request (trusted_proxy_configured), and a bad entry must not log each time.
_WARNED: set = set()

_UNSET_WARNING = (
    "FORWARDED_ALLOW_IPS is not set: X-Forwarded-For is trusted only from "
    "127.0.0.1, so behind a reverse proxy every client shares the proxy's "
    "address; the per-IP login limit stays off until it is set."
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
    return any(
        not ipaddress.ip_network(entry, strict=False).is_loopback
        for entry in trusted_proxy_entries(raw)
    )


def build_uvicorn_config(app, *, host: str, port: int, proxy_headers: bool) -> uvicorn.Config:
    """The uvicorn config for one listener.

    ``proxy_headers=True`` is the Traefik-facing listener: X-Forwarded-For is
    believed from the filtered FORWARDED_ALLOW_IPS only. ``False`` never
    rewrites the client address, whatever the environment says.
    """
    if not proxy_headers:
        return uvicorn.Config(app, host=host, port=port, proxy_headers=False)
    raw = os.environ.get("FORWARDED_ALLOW_IPS")
    trusted = ",".join(trusted_proxy_entries(raw))
    # Built before logging: constructing the Config installs uvicorn's log
    # handlers, without which an INFO line here would go nowhere.
    config = uvicorn.Config(app, host=host, port=port, proxy_headers=True,
                            forwarded_allow_ips=trusted)
    _log.info("Proxy headers: trusting X-Forwarded-For from %s", trusted)
    if raw is None or not raw.strip():
        _log.warning(_UNSET_WARNING)
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

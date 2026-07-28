# Copyright (c) 2020 Pranav Krishna — MIT License (see LICENSE)
# All rights reserved.
# This file is part of the IntelliStock-v2 Tool,
# and is released under the "Non distributable agreement". 

# MAIN SERVER FILE

###########################
# IMPORTS
############################

#from webull import paper_webull
from dataclasses import dataclass
from dotenv import load_dotenv
from intellistock_logger import intellistock_logger
import time as time
import itertools
import sys
import os
import threading
import logging
import hmac
import hashlib
import re
from datetime import datetime
from typing import Dict
from rethinkdb import RethinkDB
from rethink_changefeed import run_reconnecting_changefeed
import socketio
from waitress import serve
from os import system
from live_readiness import LiveReadinessError

# Load .env from backend dir or project root so KEY/SECRET are available to spawned services
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND_DIR, '.env'))
load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))

###########################
# GLOBAL VARIABLES
############################

r = RethinkDB()
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = os.environ.get('RETHINKDB_PORT', '28015')
DB_NAME = 'IntelliStock'

running_threads = []
running_threads_objs = {}
threads_running = False
thread_count = 0

# Engines: server starts/stops containers based on EngineControl table (one doc per engine).
from engine_control import (
    ENGINE_CONTROL_TABLE,
    ENGINE_ID_AI_BACKTEST,
    ENGINE_ID_DAILY_DIGEST,
    ENGINE_ID_DISCOVER,
    ENGINE_ID_NEXUS_GRAPH,
    get_engine_doc,
)

AGENT_CONTAINER_NAME = 'intellistock-ai-agent'
agent_container_obj = None

DIGEST_CONTAINER_NAME = 'intellistock-daily-digest-engine'
digest_container_obj = None

DISCOVER_CONTAINER_NAME = 'intellistock-discover-service'
DISCOVER_DATA_VOLUME_NAME = os.environ.get('DISCOVER_DATA_VOLUME_NAME', 'intellistockv4_discover_stocks_data')
discover_container_obj = None

NEXUS_CONTAINER_NAME = 'intellistock-graph-nexus'
# Fixed named volume for nexus cache (supply chain CSV, SEC filings, etc.) so the same volume is reused
# and we don't create new anonymous volumes each time the nexus container starts.
NEXUS_CACHE_VOLUME_NAME = os.environ.get('NEXUS_CACHE_VOLUME_NAME', 'intellistock_graph_nexus_cache')
# Shared log volume for nexus_graph_engine build logs; must match docker-compose "nexus_graph_logs" volume
# name and the volume mounted on backend/api services so /nexus-graph-builds/{id}/logs can read full files.
NEXUS_GRAPH_LOG_VOLUME_NAME = os.environ.get('NEXUS_GRAPH_LOG_VOLUME', 'nexus_graph_logs')
NEXUS_GRAPH_LOG_DIR_IN_CONTAINER = os.environ.get('NEXUS_GRAPH_LOG_DIR', '/app/nexus_graph_logs')
# Shared volume for per-instance broker log files (/app/live_trading_logs/instance_<id>.log).
# Mounted on backend + api services AND every instance container server.py starts, so the
# /instances/{id}/live-logs endpoint can tail what the broker writes.
LIVE_TRADING_LOG_VOLUME_NAME = os.environ.get('LIVE_TRADING_LOG_VOLUME', 'live_trading_logs')
LIVE_TRADING_LOG_DIR_IN_CONTAINER = os.environ.get('LIVE_TRADING_LOG_DIR', '/app/live_trading_logs')
nexus_container_obj = None
nexus_container_poll_thread = None

callback_done = threading.Event()

# RethinkDB connection (set in run() after connecting)
conn = None

clientList = {}
brokersList = {}
priceBrokerUID = ''
clientOwners = {}

# Price service runs separately. Discover and Nexus are server-managed engine containers.


def get_conn():
    """Create a new RethinkDB connection (connections are not thread-safe)."""
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def register_socket_client(sio, sid, data, *, master_key=None):
    """Register a client ID; a duplicate can replace a worker only with proof."""
    global clientList, priceBrokerUID, brokersList, clientOwners
    if not isinstance(data, dict):
        return False
    uuid = data.get("UUID")
    if not isinstance(uuid, str) or not uuid or uuid == "PriceBroker" or data.get("instance") == "PriceBroker":
        return False
    configured_token = (master_key if master_key is not None
                        else os.environ.get("SOCKET_CONTROL_MASTER_KEY", ""))
    provided_token = data.get("control_token")
    role = "broker" if uuid == str(data.get("instance")) + "_broker" else ""
    expected_token = derive_socket_control_token(
        configured_token, data.get("instance"), role)
    authenticated = (isinstance(configured_token, str) and bool(configured_token)
                     and isinstance(provided_token, str)
                     and bool(expected_token)
                     and hmac.compare_digest(provided_token, expected_token))
    if (not isinstance(data.get("instance"), str) or not data["instance"]
            or not role
            or not authenticated):
        return False
    old_sid = clientList.get(uuid)
    if old_sid is not None and old_sid != sid:
        if clientOwners.get(uuid) != (data["instance"], role):
            return False
        if not authenticated:
            return False
        sio.emit('terminate', {'terminate': True}, room=old_sid)
        brokersList.pop(old_sid, None)
    if data.get('instance') is not None and data.get('symbol') is not None:
        brokersList[sid] = {'instance': data['instance'], 'symbol': data['symbol']}
    clientList[uuid] = sid
    clientOwners[uuid] = (data["instance"], role)
    if uuid == "PriceBroker":
        priceBrokerUID = sid
    return True


def unregister_socket_client(sid):
    global clientList, clientOwners, brokersList
    brokersList.pop(sid, None)
    for uuid, current_sid in list(clientList.items()):
        if current_sid == sid:
            clientList.pop(uuid, None)
            clientOwners.pop(uuid, None)


def derive_socket_control_token(master_key, instance_id, role="broker"):
    if (type(master_key) is not str or not re.fullmatch(r"[0-9a-f]{64}", master_key)
            or len(set(master_key)) < 8 or type(instance_id) is not str or not instance_id
            or role != "broker"):
        return ""
    return hmac.new(master_key.encode("utf-8"), (instance_id + ":" + role).encode("utf-8"), hashlib.sha256).hexdigest()


def is_funded_kalshi_live(instance_doc, brokerage_doc) -> bool:
    if type(instance_doc) is not dict or instance_doc.get("kind") != "kalshi":
        return False
    cfg = instance_doc.get("kalshi_config")
    if type(cfg) is not dict or type(cfg.get("live_enabled")) is not bool or type(cfg.get("paper_mode")) is not bool:
        raise LiveReadinessError("Kalshi execution mode is malformed")
    if cfg["live_enabled"] is not True or cfg["paper_mode"] is not False:
        return False
    environment = brokerage_doc.get("kalshi_environment") if type(brokerage_doc) is dict else None
    if type(environment) is not str or environment.lower() not in {"demo", "live", "prod"}:
        raise LiveReadinessError("Kalshi brokerage environment is malformed")
    return environment.lower() in {"live", "prod"}


def image_identity(image_obj):
    value = getattr(image_obj, "id", "")
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise LiveReadinessError("Docker image identity is malformed")
    return value.split(":", 1)[1]


@dataclass(frozen=True)
class InstanceLaunchPreflight:
    client: object
    image_id: str
    image_digest: str
    instance_id: str
    instance: dict
    brokerage: dict


def _preflight_instance_launch(instance_id, *, client=None):
    client = client or _get_docker_client()
    if client is None:
        raise LiveReadinessError("Docker client is unavailable")
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    image_obj = client.images.get(image)
    digest = image_identity(image_obj)
    image_id = "sha256:" + digest
    instance, brokerage = _fresh_instance_docs(instance_id)
    if instance.get("id") != str(instance_id):
        raise LiveReadinessError("instance identity does not match launch")
    if is_funded_kalshi_live(instance, brokerage):
        from live_readiness import assert_live_start_allowed, report_from_mapping
        assert_live_start_allowed(report_from_mapping(instance.get("live_readiness_report"), instance_id=str(instance_id)), deployed_artifact_hash=digest)
    return InstanceLaunchPreflight(
        client=client,
        image_id=image_id,
        image_digest=digest,
        instance_id=str(instance_id),
        instance=instance,
        brokerage=brokerage,
    )


def _fresh_instance_docs(instance_id):
    connection = get_conn()
    try:
        instance = r.db(DB_NAME).table("Instances").get(str(instance_id)).run(connection)
        if type(instance) is not dict:
            raise LiveReadinessError("instance configuration is unavailable")
        brokerage = r.db(DB_NAME).table("BrokerageAccounts").get(instance.get("brokerage_id")).run(connection) if instance.get("brokerage_id") else {}
        return instance, brokerage if type(brokerage) is dict else {}
    finally:
        connection.close()


def wait_for_rethinkdb(max_attempts=30, delay=2):
    """
    Wait until RethinkDB is ready (connection + primary replica available).
    Returns (conn, True) on success or (None, False) on failure.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            c = get_conn()
            ensure_db_and_tables(c)
            # Verify Instances table is readable (primary replica ready)
            list(r.db(DB_NAME).table('Instances').run(c))
            return c, True
        except Exception as e:
            try:
                c.close()
            except Exception:
                pass
            if attempt == max_attempts:
                intellistock_logger.log(f"RethinkDB not ready after {max_attempts} attempts: {e}", "red", service="SERVER")
                return None, False
            intellistock_logger.log(f"RethinkDB not ready (attempt {attempt}/{max_attempts}), retrying in {delay}s...", "yellow", service="SERVER")
            time.sleep(delay)
    return None, False


def ensure_db_and_tables(c):
    """Ensure IntelliStock database and all required tables exist."""
    try:
        r.db_list().run(c)
    except Exception:
        raise
    dbs = list(r.db_list().run(c))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(c)
    tables = ('Config', 'Instances', 'LivePricesStocks', 'LivePrices', 'PriceHistory', 'Strategies', 'BacktestResults', 'BacktestInstances', 'AIBacktestingResults', 'AgentBest', 'GraphNexusNewsCache', 'GraphNexusProgress', 'EngineControl', 'EarningsLLMCache', 'BrokerageAccounts', 'Models')
    for table in tables:
        if table not in list(r.db(DB_NAME).table_list().run(c)):
            r.db(DB_NAME).table_create(table).run(c)
    # Ensure Config has Pings and Config documents (Pings for core/price/discover ping; Config kept for backward compat)
    if r.db(DB_NAME).table('Config').get('Pings').run(c) is None:
        r.db(DB_NAME).table('Config').insert({'id': 'Pings', 'corePing': None, 'coreResponse': None}).run(c)
    if r.db(DB_NAME).table('Config').get('Config').run(c) is None:
        r.db(DB_NAME).table('Config').insert({
            'id': 'Config',
            'runPriceService': True,
            'terminatePriceService': False,
            'terminatePriceBroker': False,
            'terminateDiscoverService': False,
        }).run(c)
    # Single EngineControl table: one document per engine (all setup/config here)
    try:
        from engine_control import ensure_engine_control_table
        ensure_engine_control_table(c)
    except Exception as e:
        intellistock_logger.log("EngineControl setup failed: %s" % e, "yellow", service="SERVER")
    # Users table and default admin (for API auth) - create table and index, then ensure default admin
    try:
        from auth_utils import ensure_users_table, ensure_default_admin
        ensure_users_table(c)
        ensure_default_admin(c)
    except Exception as e:
        intellistock_logger.log("Auth setup (Users table / default admin) failed: %s" % e, "yellow", service="SERVER")


###########################
# SERVER CONFIGURATION
############################


def _instance_container_name(instance_id):
    """Return a valid Docker container name for this instance."""
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(instance_id))[:50]
    return f"intellistock-instance-{safe}"


def _get_docker_client():
    """Return Docker client (from_env)."""
    try:
        import docker
        return docker.from_env()
    except Exception as e:
        intellistock_logger.log(f"Docker client error: {e}", "red", service="SERVER")
        return None


def _detect_container_network(client):
    """Get the Docker network of the container this process runs in (if any)."""
    try:
        container_id = os.environ.get('HOSTNAME')
        if not container_id and os.path.isfile('/etc/hostname'):
            with open('/etc/hostname', 'r') as f:
                container_id = f.read().strip()
        if not container_id:
            return None
        current = client.containers.get(container_id)
        networks = current.attrs.get('NetworkSettings', {}).get('Networks') or {}
        names = [n for n in networks if n != 'bridge']
        return names[0] if names else (list(networks.keys())[0] if networks else None)
    except Exception:
        return None


def _claude_home_mount():
    """Return the host->container claude-auth bind-mounts as a dict suitable
    for the Docker SDK, or ``None`` if the operator hasn't configured a
    ``CLAUDE_HOST_HOME`` path.

    Spawned containers (live trading instances, AI backtesting agent,
    daily digest, graph nexus) reuse the parent service's claude login
    by bind-mounting:
      - the host's ``~/.claude`` dir   at ``/root/.claude``
      - the host's ``~/.claude.json`` file at ``/root/.claude.json``

    Both are required for CC's MCP mode (the bare directory mount alone
    triggers a misleading "Not logged in" error because CC also reads
    the sibling config file at ``$HOME/.claude.json``). With no env var
    set, we skip the mounts — claude-cli calls in the child will surface
    a clean "Not logged in" error instead of failing obscurely on a
    missing volume.

    Mounted **read-only** so a compromised CC subprocess can't tamper
    with the host's auth state. CC only reads ``.credentials.json``,
    ``settings.json``, and ``.claude.json`` from this dir; it doesn't
    write back during the short-lived calls we make.
    """
    host_home = (os.environ.get('CLAUDE_HOST_HOME') or '').strip()
    if not host_home:
        return None
    mounts: Dict[str, Dict[str, str]] = {
        host_home: {'bind': '/root/.claude', 'mode': 'ro'},
    }
    host_config = (os.environ.get('CLAUDE_HOST_CONFIG') or '').strip()
    if host_config:
        mounts[host_config] = {'bind': '/root/.claude.json', 'mode': 'ro'}
    return mounts


_CODEX_AUTH_VOLUME_NAME = "codex_auth"


def _augment_volumes_with_claude(volumes):
    """Append the claude-auth + codex-auth mounts to a ``volumes`` arg.

    The Docker SDK accepts either a dict shape
    ``{host_path: {bind, mode}}`` or a list of ``"name:/path"`` /
    ``"host:/container"`` strings. The list-with-mode form
    (``"host:/container:rw"``) is the Docker *CLI* syntax and is
    inconsistently supported by the SDK across versions. To be safe we
    always emit dict shape when any auth mount is added.

    Returns a NEW container suitable for the SDK's ``volumes=`` arg.
    - claude is mounted **read-only** (CC only reads
      ``.credentials.json`` / ``settings.json``).
    - codex_auth (named volume) is mounted **read-write** so spawned
      strategies share the same auth.json the operator established via
      the Models UI device-code login. The volume is created by
      docker-compose; if it doesn't exist on this host, the mount is
      skipped silently — the codex provider's auth probe will report
      "not authenticated" cleanly.
    """
    claude_mounts = _claude_home_mount() or {}
    # Convert any input shape to a dict.
    out: Dict[str, Dict[str, str]] = {}
    if isinstance(volumes, dict):
        out = dict(volumes)
    elif isinstance(volumes, list):
        for entry in volumes:
            if not isinstance(entry, str):
                continue
            parts = entry.split(":")
            if len(parts) == 2:
                host, bind = parts
                out[host] = {"bind": bind, "mode": "rw"}
            elif len(parts) == 3:
                host, bind, mode = parts
                out[host] = {"bind": bind, "mode": mode}
            else:
                out[entry] = {"bind": "/data", "mode": "rw"}
    for host_path, spec in claude_mounts.items():
        out[host_path] = spec
    # Always try to mount the codex_auth named volume. The Docker SDK
    # auto-creates a *new* named volume if it doesn't exist, which would
    # be empty (no auth) — that's still safe; the codex provider just
    # reports "not authenticated" cleanly.
    out[_CODEX_AUTH_VOLUME_NAME] = {"bind": "/root/.codex", "mode": "rw"}
    return out


_instance_network_warned = False


def _get_instance_network(client):
    """
    Return the Docker network name to attach instance containers to.
    Uses INSTANCE_DOCKER_NETWORK if set and that network exists; otherwise
    auto-detects from the current container (works on Dockploy/remote).

    When INSTANCE_DOCKER_NETWORK points at a network that no longer exists
    (compose/Dockploy project drift), auto-detect is authoritative and spawned
    containers still land on the backend's own network. We surface the detected
    name once (not per-launch) so the stale env var is visible and fixable.
    """
    global _instance_network_warned
    env_network = os.environ.get('INSTANCE_DOCKER_NETWORK', '').strip()
    if env_network:
        try:
            client.networks.get(env_network)
            return env_network
        except Exception:
            # Network from env doesn't exist (e.g. on Dockploy); fall back to auto-detect.
            detected = _detect_container_network(client)
            if not _instance_network_warned:
                intellistock_logger.log(
                    f"INSTANCE_DOCKER_NETWORK='{env_network}' does not exist; "
                    f"auto-detected '{detected}' from this container instead. "
                    f"Set INSTANCE_DOCKER_NETWORK='{detected}' (or unset it) to silence this.",
                    "yellow",
                    service="SERVER",
                )
                _instance_network_warned = True
            return detected
    return _detect_container_network(client)


def _docker_object_not_found(exc) -> bool:
    return type(exc).__name__.lower().endswith("notfound")


def _remove_existing_instance_container(client, name) -> None:
    """Remove a prior named worker or fail without launching a duplicate."""
    try:
        old = client.containers.get(name)
    except Exception as exc:
        if _docker_object_not_found(exc):
            return
        raise
    try:
        old.stop(timeout=5)
    except Exception as exc:
        if _docker_object_not_found(exc):
            return
        raise
    try:
        old.remove()
    except Exception as exc:
        if _docker_object_not_found(exc):
            return
        raise


def start_instance_container(instance_id, *, preflight=None):
    """
    Create and start a Docker container running instance.py.
    Instance reads key/secret from the Instances table in the DB; only instance_id is passed.
    Returns the container object or None on failure.
    """
    global running_threads_objs
    name = _instance_container_name(instance_id)
    # Env for the container: RethinkDB and server URL (so instance can connect)
    rethink_host = os.environ.get('INSTANCE_RETHINKDB_HOST', RETHINKDB_HOST)
    server_url = os.environ.get('INSTANCE_SERVER_URL', os.environ.get('SERVER_URL', 'http://localhost:5000'))
    env = {
        'RETHINKDB_HOST': rethink_host,
        'RETHINKDB_PORT': str(RETHINKDB_PORT),
        'SERVER_URL': server_url,
    }
    # Pass through API keys needed by broker strategies.
    # INTELLISTOCK_CRED_KEY is REQUIRED for the broker to decrypt Fernet-
    # encrypted BrokerageAccounts credentials - without it the adapter silently
    # falls back to legacy plaintext instance-level keys that may not match the
    # linked brokerage's paper/live mode, causing 401 at boot.
    for _k in ('GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'BENZINGA_API_KEY',
               'GRAPH_NEXUS_LLM_PROVIDER', 'GRAPH_NEXUS_LLM_API_KEY', 'GRAPH_NEXUS_LLM_MODEL',
               'INTELLISTOCK_CRED_KEY',
               'ALLOW_LEGACY_ENV_CREDS',
               # When set, broker_session narrows the live tick gate from
               # extended hours to regular trading hours.
               'LIVE_RTH_ONLY',
               # 2026-05-01 — auto-reset on detected account migration
               # (peak >> current equity + 0 broker positions). Without
               # this set the boot sequence logs RED + warns but does not
               # touch state; with it, migration-sensitive cache keys are
               # cleared on detection.
               'LIVE_AUTO_RESET_ON_MIGRATION',
               # Strict PIT evidence capture is disabled by default and reaches
               # equities workers only when the operator explicitly enables it.
               'PIT_CAPTURE_ENABLED'):
        _v = os.environ.get(_k)
        if _v:
            env[_k] = _v
    # LLM output logging endpoint (defaults to Docker service name for api container)
    env['LLM_LOG_API_URL'] = os.environ.get('LLM_LOG_API_URL', 'http://api:8011')
    # Live-trading log volume wiring: the per-instance broker log file is
    # written to /app/live_trading_logs/instance_<id>.log, which the api
    # service also mounts so /instances/{id}/live-logs can serve it.
    env['LIVE_TRADING_LOG_DIR'] = LIVE_TRADING_LOG_DIR_IN_CONTAINER
    env['LIVE_TRADING_LOG_VOLUME'] = LIVE_TRADING_LOG_VOLUME_NAME
    # v2: a Kalshi instance runs a dedicated lean engine, NOT instance.py — which
    # spins up Socket.IO + a broker subprocess ("0 seed tickers") that are
    # irrelevant to Kalshi and pollute its logs. Default to the equities command;
    # branch only on kind='kalshi'. DEFENSIVE: any lookup failure falls back to
    # the unchanged equities path, so this CRITICAL-blast-radius launch point
    # cannot break the equities instances.
    master_key = os.environ.get("SOCKET_CONTROL_MASTER_KEY", "")
    broker_token = derive_socket_control_token(master_key, str(instance_id), "broker")
    if not broker_token:
        intellistock_logger.log("Instance launch blocked: socket ownership secret is unavailable", "red", service="SERVER")
        return None
    env["INSTANCE_SOCKET_BROKER_TOKEN"] = broker_token
    try:
        # This call must remain internal and immediately precede launch.  A
        # caller cannot supply a stale authorization snapshot.
        # A legacy caller may still pass ``preflight``; never trust or reuse it.
        # The authoritative snapshot is always rebuilt here.
        authoritative_preflight = _preflight_instance_launch(instance_id)
        if authoritative_preflight.instance_id != str(instance_id):
            raise LiveReadinessError("launch preflight instance does not match")
        client = authoritative_preflight.client
        kind = authoritative_preflight.instance.get("kind")
        if (
            kind in (None, "", "equities")
            and os.environ.get(
                "EQUITIES_INSTANCE_AUTOSTART_ALLOWED", "false"
            ).strip().lower()
            not in {"1", "true", "yes", "on"}
        ):
            intellistock_logger.log(
                f"Equities instance {instance_id} remains stopped: "
                "EQUITIES_INSTANCE_AUTOSTART_ALLOWED is not explicitly enabled",
                "yellow",
                service="SERVER",
            )
            return None
        if kind == "kalshi":
            cmd = ['python', '-m', 'kalshi.runner', str(instance_id)]
        elif kind in (None, "", "equities"):
            cmd = ['python', 'instance.py', str(instance_id)]
        else:
            raise LiveReadinessError("instance kind is malformed")
        network = _get_instance_network(client)
        # Use only local image (do not pull); otherwise Docker tries Docker Hub and fails
        actual_image_digest = authoritative_preflight.image_digest
        immutable_image_id = authoritative_preflight.image_id
        if immutable_image_id != "sha256:" + actual_image_digest:
            raise LiveReadinessError("launch image identity is inconsistent")
        env["INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256"] = actual_image_digest
        # Fresh validation is deliberately before any old container mutation.
        # If a container with this name already exists (e.g. from a previous run), remove it
        _remove_existing_instance_container(client, name)
        # Mount the live_trading_logs volume so the broker's log file is
        # readable from the api container via the same shared volume.
        volumes = [
            f'{LIVE_TRADING_LOG_VOLUME_NAME}:{LIVE_TRADING_LOG_DIR_IN_CONTAINER}',
        ]
        container = client.containers.run(
            immutable_image_id,
            command=cmd,
            name=name,
            environment=env,
            network=network,
            volumes=_augment_volumes_with_claude(volumes),
            detach=True,
            remove=False,
        )
        running_threads_objs[instance_id] = container
        intellistock_logger.log(f"Started instance container: {instance_id} ({name})", "green", service="SERVER")
        return container
    except Exception as exc:
        intellistock_logger.log(
            f"Failed to start instance container {instance_id} "
            f"({type(exc).__name__})",
            "red",
            service="SERVER",
        )
        return None


def stop_instance_container(instance_id):
    """Stop/remove a worker and forget it only after authoritative success."""
    global running_threads_objs, thread_count
    container = running_threads_objs.get(instance_id)
    if not container:
        return True
    try:
        container.stop(timeout=5)
    except Exception as exc:
        if not _docker_object_not_found(exc):
            intellistock_logger.log(
                f"Instance stop not confirmed for {instance_id} "
                f"({type(exc).__name__})",
                "red",
                service="SERVER",
            )
            return False
    else:
        try:
            container.remove()
        except Exception as exc:
            if not _docker_object_not_found(exc):
                intellistock_logger.log(
                    f"Instance removal not confirmed for {instance_id} "
                    f"({type(exc).__name__})",
                    "red",
                    service="SERVER",
                )
                return False
    running_threads_objs.pop(instance_id, None)
    if instance_id in running_threads:
        running_threads.remove(instance_id)
    thread_count = max(0, thread_count - 1)
    intellistock_logger.log(
        f"Stopped and removed instance container: {instance_id}",
        "green",
        service="SERVER",
    )
    return True


def _agent_container_env():
    """Env vars for the AI agent container (pass through from server env so agent can reach API and use LLM)."""
    # Re-load .env so we have DEFAULT_ADMIN_* etc. when building agent env (e.g. server in Docker may not have had them at startup)
    load_dotenv(os.path.join(BACKEND_DIR, '.env'))
    load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))
    rethink_host = os.environ.get('INSTANCE_RETHINKDB_HOST', RETHINKDB_HOST)
    api_url = os.environ.get('API_URL', '').strip() or os.environ.get('SERVER_URL', 'http://localhost:8011')
    # Agent container runs in Docker; use Docker service name to reach API service
    # Check if AGENT_API_URL is explicitly set (for testing/override)
    agent_api_url = os.environ.get('AGENT_API_URL', '').strip()
    if agent_api_url:
        api_url = agent_api_url
    elif api_url.startswith('http://localhost:') or api_url.startswith('http://127.0.0.1:'):
        # localhost -> use host.docker.internal or Docker service name
        api_url = os.environ.get('DOCKER_HOST_API_URL', 'http://api:8011')
    else:
        # API_URL points to a host IP / hostname (e.g. http://api-host:8011); agent container should use Docker service name
        # Extract port from API_URL if present, otherwise default to 8011
        import re
        port_match = re.search(r':(\d+)(?:/|$)', api_url)
        port = port_match.group(1) if port_match else '8011'
        api_url = f'http://api:{port}'
    keys = (
        # API_URL is handled above (converted to Docker service name), so don't copy from env
        'RETHINKDB_HOST', 'RETHINKDB_PORT',
        'AGENT_API_USERNAME', 'AGENT_API_PASSWORD',
        'DEFAULT_ADMIN_USERNAME', 'DEFAULT_ADMIN_PASSWORD',
        'AI_BACKTESTING_AGENT_MODEL', 'AI_BACKTESTING_AGENT_API_KEY', 'AI_BACKTESTING_AGENT_PROVIDER',
        'GEMINI_API_KEY', 'DEEPSEEK_API_KEY',
        'KEY', 'SECRET',
        'APCA_API_KEY_ID', 'APCA_API_SECRET_KEY',
        'AI_BACKTESTING_STRATEGY_GENERATION_PROVIDER', 'AI_BACKTESTING_STRATEGY_GENERATION_MODEL', 'AI_BACKTESTING_STRATEGY_GENERATION_API_KEY',
        'AI_BACKTESTING_VALIDATION_PROVIDER', 'AI_BACKTESTING_VALIDATION_MODEL', 'AI_BACKTESTING_VALIDATION_API_KEY',
        'AI_BACKTESTING_BEST_SELECTION_PROVIDER', 'AI_BACKTESTING_BEST_SELECTION_MODEL', 'AI_BACKTESTING_BEST_SELECTION_API_KEY',
        'AI_BACKTESTING_DAILY_GOAL', 'AI_BACKTESTING_BATCH_SIZE', 'AI_BACKTESTING_STOCK_POOL',
        'NEWS_API_MODEL_KEY',
        'BENZINGA_API_KEY',
    )
    env = {
        'RETHINKDB_HOST': rethink_host,
        'RETHINKDB_PORT': str(RETHINKDB_PORT),
        'API_URL': api_url,  # Set corrected API_URL (Docker service name for agent container)
    }
    for k in keys:
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    if not env.get('DEFAULT_ADMIN_USERNAME') and not env.get('AGENT_API_USERNAME'):
        intellistock_logger.log(
            "Agent container will miss credentials: set DEFAULT_ADMIN_USERNAME and DEFAULT_ADMIN_PASSWORD (or AGENT_API_*) in server env or .env",
            "yellow", service="SERVER",
        )
    return env


def start_agent_container(force_restart=False):
    """Start a Docker container running the AI backtesting agent. One container only.
    If an existing container with the same name is already running (e.g. from a previous deploy),
    adopt it instead of killing and recreating — unless force_restart=True."""
    global agent_container_obj
    if agent_container_obj is not None:
        return agent_container_obj
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    name = AGENT_CONTAINER_NAME
    cmd = ['python', 'engines/ai_backtest_engine.py']
    try:
        client = _get_docker_client()
        if not client:
            return None
        network = _get_instance_network(client)
        try:
            client.images.get(image)
        except Exception:
            intellistock_logger.log(
                f"Image '{image}' not found; cannot start AI agent container. Build backend image first.",
                "red", service="SERVER",
            )
            return None
        # Check if container already exists
        try:
            existing = client.containers.get(name)
            if existing.status == 'running' and not force_restart:
                # Container is already running (e.g. survived a redeploy) — adopt it
                agent_container_obj = existing
                intellistock_logger.log(
                    "AI agent container '%s' is already running (status=%s). Adopted existing container instead of recreating."
                    % (name, existing.status), "cyan", service="SERVER",
                )
                return existing
            # Container exists but is stopped, or force_restart requested — remove and recreate
            try:
                existing.stop(timeout=10)
            except Exception:
                pass
            try:
                existing.remove()
            except Exception:
                pass
            agent_container_obj = None
        except Exception:
            pass  # Container doesn't exist — create fresh
        env = _agent_container_env()
        container = client.containers.run(
            image,
            command=cmd,
            name=name,
            environment=env,
            network=network,
            volumes=_augment_volumes_with_claude([]),
            detach=True,
            remove=False,
        )
        agent_container_obj = container
        intellistock_logger.log("Started AI backtesting agent container (%s)" % name, "green", service="SERVER")
        return container
    except Exception as e:
        intellistock_logger.log("Failed to start AI agent container: %s" % e, "red", service="SERVER")
        return None


def stop_agent_container():
    """Stop and remove the AI agent container. Also checks for orphaned containers by name
    (e.g. if server restarted but the container survived from a previous deploy)."""
    global agent_container_obj
    name = AGENT_CONTAINER_NAME
    stopped = False
    # Stop via tracked object
    if agent_container_obj is not None:
        try:
            try:
                agent_container_obj.stop(timeout=10)
            except Exception:
                pass
            try:
                agent_container_obj.remove()
            except Exception:
                pass
            stopped = True
        except Exception as e:
            intellistock_logger.log("Error stopping AI agent container: %s" % e, "yellow", service="SERVER")
        finally:
            agent_container_obj = None
    # Also check for orphaned container by name (survives redeploy when agent_container_obj was lost)
    try:
        client = _get_docker_client()
        if client:
            try:
                orphan = client.containers.get(name)
                try:
                    orphan.stop(timeout=10)
                except Exception:
                    pass
                try:
                    orphan.remove()
                except Exception:
                    pass
                stopped = True
            except Exception:
                pass  # Container doesn't exist — nothing to clean up
    except Exception:
        pass
    if stopped:
        intellistock_logger.log("Stopped and removed AI agent container (%s)" % name, "green", service="SERVER")


def start_digest_container():
    """Start a Docker container running the daily digest engine. One container only."""
    global digest_container_obj
    if digest_container_obj is not None:
        return digest_container_obj
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    name = DIGEST_CONTAINER_NAME
    cmd = ['python', 'engines/daily_digest_engine.py']
    try:
        client = _get_docker_client()
        if not client:
            return None
        network = _get_instance_network(client)
        try:
            client.images.get(image)
        except Exception:
            intellistock_logger.log(
                f"Image '{image}' not found; cannot start digest container. Build backend image first.",
                "red", service="SERVER",
            )
            return None
        try:
            old = client.containers.get(name)
            old.stop(timeout=10)
            old.remove()
            digest_container_obj = None
        except Exception:
            pass
        # Get environment variables from .env file for digest engine
        # Re-load .env so we pick up vars from both backend/ and project root
        load_dotenv(os.path.join(BACKEND_DIR, '.env'))
        load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))
        env = {
            'RETHINKDB_HOST': os.environ.get('RETHINKDB_HOST', 'localhost'),
            'RETHINKDB_PORT': os.environ.get('RETHINKDB_PORT', '28015'),
        }
        # Pass through digest-specific and LLM API keys from os.environ
        _digest_passthrough_keys = (
            'AI_DIGEST_PROVIDER', 'AI_DIGEST_MODEL', 'AI_DIGEST_API_KEY',
            'GEMINI_API_KEY', 'DEEPSEEK_API_KEY',
            'DISCORD_BOT_TOKEN',
        )
        for k in _digest_passthrough_keys:
            v = os.environ.get(k)
            if v is not None:
                env[k] = v
        container = client.containers.run(
            image,
            command=cmd,
            name=name,
            environment=env,
            network=network,
            volumes=_augment_volumes_with_claude([]),
            detach=True,
            remove=False,
        )
        digest_container_obj = container
        intellistock_logger.log("Started daily digest engine container (%s)" % name, "green", service="SERVER")
        return container
    except Exception as e:
        intellistock_logger.log("Failed to start digest container: %s" % e, "red", service="SERVER")
        return None


def stop_digest_container():
    """Stop and remove the digest container."""
    global digest_container_obj
    if digest_container_obj is None:
        return
    name = DIGEST_CONTAINER_NAME
    try:
        try:
            digest_container_obj.stop(timeout=10)
        except Exception:
            pass
        try:
            digest_container_obj.remove()
        except Exception:
            pass
    except Exception as e:
        intellistock_logger.log("Error stopping digest container: %s" % e, "yellow", service="SERVER")
    finally:
        digest_container_obj = None
    intellistock_logger.log("Stopped and removed digest container (%s)" % name, "green", service="SERVER")


def _container_is_terminal(status: str | None) -> bool:
    """True only for clearly dead Docker states; startup should not disturb anything else."""
    s = (status or '').strip().lower()
    return s in {'exited', 'dead', 'removing'}


def _container_can_reuse(status: str | None) -> bool:
    """Reuse any non-terminal Docker state to avoid disrupting live engine containers."""
    s = (status or '').strip().lower()
    return bool(s) and not _container_is_terminal(s)


def _discover_container_env():
    """Build env dict for the Discover container."""
    load_dotenv(os.path.join(BACKEND_DIR, '.env'))
    load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))
    env = {
        'RETHINKDB_HOST': os.environ.get('RETHINKDB_HOST', RETHINKDB_HOST),
        'RETHINKDB_PORT': str(RETHINKDB_PORT),
    }
    for k in ('KEY', 'SECRET', 'GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'BENZINGA_API_KEY'):
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    return env


def start_discover_container():
    """Start a Docker container running the Discover engine. One container only."""
    global discover_container_obj
    if discover_container_obj is not None:
        try:
            discover_container_obj.reload()
            if _container_can_reuse(getattr(discover_container_obj, 'status', '')):
                return discover_container_obj
            if _container_is_terminal(getattr(discover_container_obj, 'status', '')):
                discover_container_obj.remove()
        except Exception:
            pass
        discover_container_obj = None
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    name = DISCOVER_CONTAINER_NAME
    cmd = ['python', 'engines/discover_engine.py']
    try:
        client = _get_docker_client()
        if not client:
            return None
        network = _get_instance_network(client)
        try:
            client.images.get(image)
        except Exception:
            intellistock_logger.log(
                f"Image '{image}' not found; cannot start Discover container. Build backend image first.",
                "red", service="SERVER",
            )
            return None
        try:
            old = client.containers.get(name)
            old.reload()
            old_status = getattr(old, 'status', '')
            if _container_can_reuse(old_status):
                discover_container_obj = old
                intellistock_logger.log(
                    "Discover container (%s) already exists with status=%s — reusing" % (name, old_status),
                    "green", service="SERVER",
                )
                return old
            if _container_is_terminal(old_status):
                old.remove()
                discover_container_obj = None
            else:
                discover_container_obj = old
                intellistock_logger.log(
                    "Discover container (%s) exists with non-terminal status=%s — leaving it untouched" % (name, old_status or "unknown"),
                    "yellow", service="SERVER",
                )
                return old
        except Exception:
            pass
        container = client.containers.run(
            image,
            command=cmd,
            name=name,
            environment=_discover_container_env(),
            network=network,
            volumes=_augment_volumes_with_claude([f'{DISCOVER_DATA_VOLUME_NAME}:/app/discoverStocks']),
            detach=True,
            remove=False,
        )
        discover_container_obj = container
        intellistock_logger.log("Started Discover engine container (%s)" % name, "green", service="SERVER")
        return container
    except Exception as e:
        intellistock_logger.log("Failed to start Discover container: %s" % e, "red", service="SERVER")
        discover_container_obj = None
        try:
            c = get_conn()
            from engine_control import update_engine_doc
            update_engine_doc(c, ENGINE_ID_DISCOVER, {"running": False})
            c.close()
        except Exception:
            pass
        return None


def stop_discover_container():
    """Stop and remove the Discover engine container."""
    global discover_container_obj
    if discover_container_obj is None:
        return
    name = DISCOVER_CONTAINER_NAME
    try:
        try:
            discover_container_obj.stop(timeout=10)
        except Exception:
            pass
        try:
            discover_container_obj.remove()
        except Exception:
            pass
    except Exception as e:
        intellistock_logger.log("Error stopping Discover container: %s" % e, "yellow", service="SERVER")
    finally:
        discover_container_obj = None
    intellistock_logger.log("Stopped and removed Discover container (%s)" % name, "green", service="SERVER")


def _nexus_container_env():
    """Build env dict for the Graph Nexus container (RethinkDB, Neo4j, Polygon, SEC EDGAR, etc.)."""
    load_dotenv(os.path.join(BACKEND_DIR, '.env'))
    load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))
    # Required infra keys — always pass with sensible defaults so container can connect
    env = {
        'RETHINKDB_HOST': os.environ.get('RETHINKDB_HOST', RETHINKDB_HOST),
        'RETHINKDB_PORT': str(RETHINKDB_PORT),
        'RETHINKDB_DB': os.environ.get('RETHINKDB_DB', 'IntelliStock'),
        'NEO4J_URI': os.environ.get('NEO4J_URI', 'bolt://localhost:7687'),
        'NEO4J_USER': os.environ.get('NEO4J_USER', 'neo4j'),
        'NEO4J_PASSWORD': os.environ.get('NEO4J_PASSWORD', 'intellistock'),
    }
    # Tuneable keys — only pass through if explicitly set in .env / environment.
    # If not set, the nexus engine uses its own built-in defaults (defined at the
    # top of nexus_graph_engine.py).  This avoids server.py defaults silently
    # overriding the engine's own defaults.
    _passthrough_keys = (
        'POLYGON_API_KEY', 'POLYGON_CALLS_PER_MINUTE',
        'GRAPH_NEXUS_SCOPE', 'GRAPH_NEXUS_MAX_COMPANIES',
        'GRAPH_NEXUS_LLM_PROVIDER', 'GRAPH_NEXUS_LLM_MODEL', 'GRAPH_NEXUS_LLM_API_KEY',
        'GRAPH_NEXUS_UPDATE_TIME', 'GRAPH_NEXUS_LIVE_UPDATE_HOURS', 'GRAPH_NEXUS_LIVE_UPDATE',
        'GRAPH_NEXUS_CACHE_DIR',
        'GRAPH_NEXUS_SUPPLY_CHAIN_CACHE_MAX_AGE', 'GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE',
        'GRAPH_NEXUS_SUPPLY_CHAIN_CSV', 'GRAPH_NEXUS_SUPPLY_CHAIN_URL',
        'GRAPH_NEXUS_COMMODITIES_CSV',
        'SEC_EDGAR_COMPANY_NAME', 'SEC_EDGAR_EMAIL', 'SEC_EDGAR_RATE_LIMIT_DELAY',
        'SEC_EDGAR_PARALLEL_WORKERS',
        'SEC_EDGAR_SUPPLY_CHAIN_MAX_COMPANIES', 'SEC_EDGAR_SUPPLY_CHAIN_OUTPUT_CSV',
        'BEA_API_KEY', 'PATENTSVIEW_API_KEY', 'PATENTSVIEW_MAX_ROWS',
        'PATENTSVIEW_LLM_PROVIDER', 'PATENTSVIEW_LLM_MODEL', 'PATENTSVIEW_LLM_API_KEY',
        'PATENTSVIEW_ZERO_PAGE_EXIT',
        'GRAPH_EDGE_INTERVAL_SYNC_BATCH_SIZE', 'GRAPH_EDGE_INTERVAL_SYNC_MIN_BATCH_SIZE',
        'PHASE7_HOLDS_WRITE_BATCH_SIZE', 'PHASE7_CLOSE_INTERVAL_BATCH_SIZE', 'PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE',
        'PHASE7_INFLATION_FIX_BATCH_SIZE', 'PHASE7_INFLATION_FIX_MIN_BATCH_SIZE',
        'GLEIF_MAX_COMPANIES', 'GLEIF_COOLDOWN_EVERY', 'GLEIF_COOLDOWN_SECONDS',
        'USASPENDING_MAX_ROWS', 'USASPENDING_WINDOW_DAYS', 'USASPENDING_BREAK_EVERY', 'USASPENDING_BREAK_SECONDS',
        'USASPENDING_FAILURE_BURST_THRESHOLD', 'USASPENDING_FAILURE_BURST_WINDOW_SECONDS',
        'USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS', 'USASPENDING_FAILURE_BURST_MAX_COOLDOWN_SECONDS',
        'USASPENDING_DEGRADED_BREAK_EVERY', 'USASPENDING_DEGRADED_BREAK_SECONDS',
        'USASPENDING_DEGRADED_MODE_SECONDS',
        'WIKIDATA_MAX_ROWS',
        'GEMINI_API_KEY', 'DEEPSEEK_API_KEY',
        # Build history + log persistence (NexusGraphBuilds table + nexus_graph_logs volume)
        'NEXUS_GRAPH_LOG_DIR', 'NEXUS_GRAPH_LOG_VOLUME',
        'NEXUS_GRAPH_LOG_RETENTION_DAYS', 'NEXUS_GRAPH_LOG_MAX_FILES',
        'NEXUS_FULL_REBUILD_MAX_AGE',
        'NEXUS_CACHE_FORCE_REFRESH', 'NEXUS_BEA_YEARS', 'NEXUS_BEA_MIN_YEAR',
        'GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED',
    )
    for k in _passthrough_keys:
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    # Always ensure the child knows the log dir matches the mount point, even if .env omits it.
    env.setdefault('NEXUS_GRAPH_LOG_DIR', NEXUS_GRAPH_LOG_DIR_IN_CONTAINER)
    env.setdefault('NEXUS_GRAPH_LOG_VOLUME', NEXUS_GRAPH_LOG_VOLUME_NAME)
    return env


def _nexus_container_is_terminal(status: str | None) -> bool:
    return _container_is_terminal(status)


def _nexus_container_can_reuse(status: str | None) -> bool:
    return _container_can_reuse(status)


def _nexus_container_poll():
    """Background thread: when nexus container has exited, clear nexus_container_obj so status is accurate."""
    global nexus_container_obj
    while True:
        time.sleep(15)
        if nexus_container_obj is None:
            continue
        try:
            nexus_container_obj.reload()
            if _nexus_container_is_terminal(getattr(nexus_container_obj, 'status', '')):
                nexus_container_obj = None
                intellistock_logger.log("Graph Nexus container exited.", "cyan", service="SERVER")
        except Exception:
            nexus_container_obj = None


def start_nexus_container():
    """Start a Docker container running Nexus Graph Engine (engines/nexus_graph_engine.py). One container only."""
    global nexus_container_obj
    if nexus_container_obj is not None:
        try:
            nexus_container_obj.reload()
            if _nexus_container_can_reuse(getattr(nexus_container_obj, 'status', '')):
                return nexus_container_obj
            if _nexus_container_is_terminal(getattr(nexus_container_obj, 'status', '')):
                nexus_container_obj.remove()
        except Exception:
            pass
        nexus_container_obj = None
    image = os.environ.get('DOCKER_INSTANCE_IMAGE', 'intellistock-backend')
    name = NEXUS_CONTAINER_NAME
    cmd = ['python', 'engines/nexus_graph_engine.py']
    try:
        client = _get_docker_client()
        if not client:
            return None
        network = _get_instance_network(client)
        try:
            client.images.get(image)
        except Exception:
            intellistock_logger.log(
                f"Image '{image}' not found; cannot start Nexus container. Build backend image first.",
                "red", service="SERVER",
            )
            return None
        try:
            old = client.containers.get(name)
            old.reload()
            old_status = getattr(old, 'status', '')
            if _nexus_container_can_reuse(old_status):
                nexus_container_obj = old
                intellistock_logger.log(
                    "Graph Nexus container (%s) already exists with status=%s — reusing" % (name, old_status),
                    "green", service="SERVER",
                )
                return old
            if _nexus_container_is_terminal(old_status):
                old.remove()
                nexus_container_obj = None
            else:
                nexus_container_obj = old
                intellistock_logger.log(
                    "Graph Nexus container (%s) exists with non-terminal status=%s — leaving it untouched" % (name, old_status or "unknown"),
                    "yellow", service="SERVER",
                )
                return old
        except Exception:
            pass
        env = _nexus_container_env()
        # Mount a fixed named volume at /app/.cache so the same volume is reused (avoids new volumes each start)
        env['GRAPH_NEXUS_CACHE_DIR'] = '/app/.cache'
        # Also mount nexus_graph_logs so per-build log files survive container exit and are readable
        # by backend + api services (which mount the same named volume in docker-compose.yml).
        volumes = [
            f'{NEXUS_CACHE_VOLUME_NAME}:/app/.cache',
            f'{NEXUS_GRAPH_LOG_VOLUME_NAME}:{NEXUS_GRAPH_LOG_DIR_IN_CONTAINER}',
        ]
        container = client.containers.run(
            image,
            command=cmd,
            name=name,
            environment=env,
            network=network,
            volumes=_augment_volumes_with_claude(volumes),
            detach=True,
            remove=False,
        )
        nexus_container_obj = container
        intellistock_logger.log("Started Graph Nexus container (%s)" % name, "green", service="SERVER")
        return container
    except Exception as e:
        intellistock_logger.log("Failed to start Graph Nexus container: %s" % e, "red", service="SERVER")
        nexus_container_obj = None
        try:
            c = get_conn()
            from engine_control import update_engine_doc
            update_engine_doc(c, ENGINE_ID_NEXUS_GRAPH, {"running": False})
            c.close()
        except Exception:
            pass
        return None


def stop_nexus_container():
    """Stop and remove the Graph Nexus container."""
    global nexus_container_obj
    if nexus_container_obj is None:
        return
    name = NEXUS_CONTAINER_NAME
    try:
        try:
            nexus_container_obj.stop(timeout=10)
        except Exception:
            pass
        try:
            nexus_container_obj.remove()
        except Exception:
            pass
    except Exception as e:
        intellistock_logger.log("Error stopping Nexus container: %s" % e, "yellow", service="SERVER")
    finally:
        nexus_container_obj = None
    intellistock_logger.log("Stopped and removed Graph Nexus container (%s)" % name, "green", service="SERVER")


def run_nexus_control_change(change, c):
    """Handle EngineControl.nexus_graph_engine: when running=True start nexus container, when running=False stop it."""
    global nexus_container_obj
    try:
        new_val = change.get("new_val")
        if new_val is None:
            return
        if new_val.get("id") != ENGINE_ID_NEXUS_GRAPH:
            return
        running = new_val.get("running", False)
        if nexus_container_obj is not None:
            try:
                nexus_container_obj.reload()
                if _nexus_container_is_terminal(getattr(nexus_container_obj, 'status', '')):
                    nexus_container_obj = None
            except Exception:
                nexus_container_obj = None
        if running and nexus_container_obj is None:
            start_nexus_container()
        elif not running and nexus_container_obj is not None:
            stop_nexus_container()
    except Exception as e:
        intellistock_logger.log("Nexus control change error: %s" % e, "red", service="SERVER")


def run_nexus_control_changefeed():
    """Run changefeed on EngineControl; start/stop nexus container when nexus_graph_engine.running changes.

    Self-healing: reconnects on any transient RethinkDB connection loss instead
    of letting the daemon thread die (2026-07-06 outage regression)."""
    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
        run_nexus_control_change,
        "NexusControl",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


def run_discover_control_change(change, c):
    """Handle EngineControl.discover_engine: when running=True start discover container, when running=False stop it."""
    global discover_container_obj
    try:
        new_val = change.get('new_val')
        if new_val is None:
            if discover_container_obj is not None:
                stop_discover_container()
            return
        if new_val.get('id') != ENGINE_ID_DISCOVER:
            return
        running = new_val.get('running', True)
        if discover_container_obj is not None:
            try:
                discover_container_obj.reload()
                if _container_is_terminal(getattr(discover_container_obj, 'status', '')):
                    discover_container_obj = None
            except Exception:
                discover_container_obj = None
        if running and discover_container_obj is None:
            start_discover_container()
        elif not running and discover_container_obj is not None:
            stop_discover_container()
    except Exception as e:
        intellistock_logger.log("Discover control change error: %s" % e, "red", service="SERVER")


def run_discover_control_changefeed():
    """Run changefeed on EngineControl; start/stop discover container when discover_engine.running changes.

    Self-healing: reconnects on any transient RethinkDB connection loss instead
    of letting the daemon thread die (2026-07-06 outage regression)."""
    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
        run_discover_control_change,
        "DiscoverControl",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


def launch_digest_from_db():
    """On startup, start the digest container if EngineControl.daily_digest_engine.running is True."""
    global digest_container_obj
    try:
        c = get_conn()
        doc = get_engine_doc(c, ENGINE_ID_DAILY_DIGEST)
        c.close()
        if doc and doc.get("running") and digest_container_obj is None:
            start_digest_container()
    except Exception as e:
        intellistock_logger.log("Could not read EngineControl (digest) for startup: %s" % e, "yellow", service="SERVER")


def launch_discover_from_db():
    """On startup, start the discover container if EngineControl.discover_engine.running is True."""
    global discover_container_obj
    try:
        c = get_conn()
        doc = get_engine_doc(c, ENGINE_ID_DISCOVER)
        c.close()
        if doc and doc.get("running") and discover_container_obj is None:
            start_discover_container()
    except Exception as e:
        intellistock_logger.log("Could not read EngineControl (discover) for startup: %s" % e, "yellow", service="SERVER")


def launch_nexus_from_db():
    """On startup, start the Graph Nexus container if EngineControl.nexus_graph_engine.running is True."""
    global nexus_container_obj
    try:
        c = get_conn()
        doc = get_engine_doc(c, ENGINE_ID_NEXUS_GRAPH)
        c.close()
        if doc and doc.get("running") and nexus_container_obj is None:
            start_nexus_container()
    except Exception as e:
        intellistock_logger.log("Could not read EngineControl (nexus) for startup: %s" % e, "yellow", service="SERVER")


def status_service_change(change, c):
    """Handle Config.Pings changefeed: copy corePing -> coreResponse."""
    try:
        new_val = change.get('new_val')
        if new_val and 'corePing' in new_val:
            request = new_val['corePing']
            r.db(DB_NAME).table('Config').get('Pings').update({'coreResponse': request}).run(c)
    except Exception as e:
        print(e)


def run_thread_service_change(change, c):
    """Handle Instances changefeed: start/stop instance Docker containers based on runCommand."""
    global thread_count
    global running_threads
    global running_threads_objs

    try:
        old_val = change.get('old_val')
        new_val = change.get('new_val')

        # Document removed or runCommand=False -> stop and remove container
        if new_val is None:
            instance_id = old_val.get('id') if old_val else None
            if instance_id and instance_id in running_threads_objs:
                stop_instance_container(instance_id)
            return

        instance_id = new_val.get('id')
        run_approval = new_val.get('runCommand', False)

        if run_approval and instance_id not in running_threads_objs:
            running_threads.append(instance_id)
            thread_count += 1
            if start_instance_container(instance_id) is None:
                thread_count -= 1
                if instance_id in running_threads:
                    running_threads.remove(instance_id)
        elif not run_approval and instance_id in running_threads_objs:
            stop_instance_container(instance_id)
        elif run_approval and instance_id in running_threads_objs:
            # Already running: recycle the container ONLY when the kalshi paper/live mode
            # flipped. The engine's EngineConfig is frozen at boot, so a paper toggle needs
            # a fresh process to take effect. Other config edits (name/caps/tier) keep
            # today's behaviour (apply on next manual restart) to keep the blast radius on
            # a real-money engine minimal.
            from kalshi.mode import kalshi_mode_changed
            if kalshi_mode_changed(old_val, new_val):
                try:
                    preflight = _preflight_instance_launch(instance_id)
                except Exception as exc:
                    intellistock_logger.log(f"Kalshi mode change blocked before restart: {type(exc).__name__}", "red", service="SERVER")
                    return
                intellistock_logger.log(
                    f"Kalshi instance {instance_id}: paper/live mode changed — restarting engine.",
                    "yellow", service="SERVER")
                stop_instance_container(instance_id)
                running_threads.append(instance_id)
                thread_count += 1
                if start_instance_container(instance_id, preflight=preflight) is None:
                    thread_count -= 1
                    if instance_id in running_threads:
                        running_threads.remove(instance_id)
    except Exception as e:
        intellistock_logger.log(str(e), "red", service="SERVER")
    callback_done.set()


def run_agent_control_change(change, c):
    """Handle EngineControl.ai_backtest_engine: when running=True start agent container, when running=False stop it.
    Note: resume_at is handled by the agent itself (it waits), server doesn't interfere.
    Don't stop container if resume_at is set (agent is waiting for timer)."""
    global agent_container_obj
    try:
        new_val = change.get('new_val')
        if new_val is None:
            if agent_container_obj is not None:
                stop_agent_container()
            return
        if new_val.get('id') != ENGINE_ID_AI_BACKTEST:
            return
        running = new_val.get('running', False)
        resume_at_str = new_val.get('resume_at')
        
        # Server only manages container start/stop based on running flag
        # resume_at is handled by the agent process itself (it will wait)
        # Don't stop container if resume_at is set (agent is waiting for timer)
        if running and agent_container_obj is None:
            start_agent_container()
        elif not running and agent_container_obj is not None:
            # Only stop if resume_at is not set (if resume_at is set, agent is waiting for timer)
            if not resume_at_str:
                stop_agent_container()
            else:
                intellistock_logger.log("Agent has resume_at set; keeping container running (agent will wait for timer).", "cyan", service="SERVER")
    except Exception as e:
        intellistock_logger.log("Agent control change error: %s" % e, "red", service="SERVER")


def run_digest_control_change(change, c):
    """Handle EngineControl.daily_digest_engine: when running=True start digest container, when running=False stop it."""
    global digest_container_obj
    try:
        new_val = change.get('new_val')
        if new_val is None:
            if digest_container_obj is not None:
                stop_digest_container()
            return
        if new_val.get('id') != ENGINE_ID_DAILY_DIGEST:
            return
        running = new_val.get('running', True)
        if running and digest_container_obj is None:
            start_digest_container()
        elif not running and digest_container_obj is not None:
            stop_digest_container()
    except Exception as e:
        intellistock_logger.log("Digest control change error: %s" % e, "red", service="SERVER")


def run_digest_control_changefeed():
    """Run changefeed on EngineControl; start/stop digest container when daily_digest_engine.running changes.

    Self-healing: reconnects on any transient RethinkDB connection loss instead
    of letting the daemon thread die (2026-07-06 outage regression)."""
    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
        run_digest_control_change,
        "DigestControl",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


def launch_instances_from_db():
    """On startup, start a Docker container running instance.py for every Instances document with runCommand=True."""
    global running_threads
    global running_threads_objs
    global thread_count
    for attempt in range(1, 16):
        try:
            c = get_conn()
            cursor = r.db(DB_NAME).table('Instances').run(c)
            docs = list(cursor)
            c.close()
            break
        except Exception as e:
            try:
                c.close()
            except Exception:
                pass
            if "primary replica" in str(e).lower() or "not available" in str(e).lower():
                if attempt < 15:
                    intellistock_logger.log(f"Instances table not ready (attempt {attempt}/15), retrying in 2s...", "yellow", service="SERVER")
                    time.sleep(2)
                    continue
            intellistock_logger.log(f"Initial Instances load error: {e}", "red", service="SERVER")
            return
    else:
        return
    try:
        for doc in docs:
            instance_id = doc.get('id')
            if not instance_id:
                continue
            run_approval = doc.get('runCommand', False)
            if run_approval and instance_id not in running_threads_objs:
                running_threads.append(instance_id)
                thread_count += 1
                if start_instance_container(instance_id) is None:
                    thread_count -= 1
                    if instance_id in running_threads:
                        running_threads.remove(instance_id)
    except Exception as e:
        intellistock_logger.log(f"Initial Instances load error: {e}", "red", service="SERVER")


def launch_agent_from_db():
    """On startup, start the AI agent container if EngineControl.ai_backtest_engine.running is True."""
    global agent_container_obj
    for attempt in range(1, 6):
        try:
            c = get_conn()
            doc = get_engine_doc(c, ENGINE_ID_AI_BACKTEST)
            c.close()
            if doc and doc.get('running') and agent_container_obj is None:
                start_agent_container()
            return
        except Exception as e:
            try:
                c.close()
            except Exception:
                pass
            if attempt < 5:
                time.sleep(2)
    intellistock_logger.log("Could not read EngineControl (agent) for startup", "yellow", service="SERVER")


def check_resume_timer():
    """Background thread: check EngineControl.ai_backtest_engine.resume_at and clear it when time is reached."""
    from datetime import datetime
    from engine_control import update_engine_doc
    while True:
        try:
            c = get_conn()
            doc = get_engine_doc(c, ENGINE_ID_AI_BACKTEST)
            if doc:
                resume_at_str = doc.get('resume_at')
                if resume_at_str:
                    try:
                        resume_at = datetime.fromisoformat(resume_at_str.replace("Z", "+00:00"))
                        now = datetime.utcnow().replace(tzinfo=resume_at.tzinfo) if resume_at.tzinfo else datetime.utcnow()
                        if resume_at <= now:
                            intellistock_logger.log("Scheduled resume time reached; clearing resume_at, ensuring running=True, and clearing paused.", "green", service="SERVER")
                            update_engine_doc(c, ENGINE_ID_AI_BACKTEST, {
                                'resume_at': None,
                                'running': True,
                                'paused': False,
                                'resume_at_cleared_at': datetime.utcnow().isoformat() + 'Z',
                            })
                    except Exception as e:
                        intellistock_logger.log(f"Error checking resume_at: {e}", "yellow", service="SERVER")
            c.close()
        except Exception as e:
            intellistock_logger.log(f"Resume timer check error: {e}", "yellow", service="SERVER")
        time.sleep(10)


def run_agent_control_changefeed():
    """Run changefeed on EngineControl; start/stop agent container when ai_backtest_engine.running changes.

    Self-healing: reconnects on any transient RethinkDB connection loss instead
    of letting the daemon thread die (2026-07-06 outage regression)."""
    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
        run_agent_control_change,
        "AgentControl",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


def run():
    global conn
    # Avoid "TERM environment variable not set" in Docker / non-interactive shells
    if not os.environ.get('TERM'):
        os.environ['TERM'] = 'dumb'
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except Exception:
        pass
    intellistock_logger.log("""
   ========================================
   Welcome to IntelliStock - V2
   ========================================
   """, "green")

    intellistock_logger.log("Service is currently \033[31mOffline", "green", service="STATUS")

    intellistock_logger.log("\nConnecting to RethinkDB...", "yellow", service="SERVER")
    conn, ok = wait_for_rethinkdb(max_attempts=30, delay=2)
    if not ok:
        intellistock_logger.log("Exiting: could not connect to RethinkDB.", "red", service="SERVER")
        return
    intellistock_logger.log("RethinkDB connected.", "green", service="SERVER")

    intellistock_logger.log("\nService starting", "yellow", service="SERVER")

    ############################################
    ### SOCKET IO SERVER
    ###########################################

    # Discover service and price service run in separate Docker containers.

    # Waitress does not support WebSockets; allow polling only so Socket.IO works.
    sio = socketio.Server(
        async_mode='threading',
        engineio_options={'allowed_transports': ['polling']},
    )
    app = socketio.WSGIApp(sio, static_files={
        '/': {'content_type': 'text/html', 'filename': 'index.html'}
    },)

    def socketServer():
        # Use a dedicated connection per thread (RethinkDB connections are not thread-safe)
        thread_conn = get_conn()
        try:
            @sio.event
            def connect(sid, environ):
                pass

            @sio.event
            def clientType(sid, data):
                register_socket_client(sio, sid, data)

            @sio.event
            def disconnect(sid):
                global clientList
                global priceBrokerUID
                global brokersList

                unregister_socket_client(sid)

                if priceBrokerUID == sid:
                    r.db(DB_NAME).table('Config').get('Config').update({'runPriceService': True}).run(thread_conn)
                    try:
                        from engine_control import ENGINE_ID_PRICE, update_engine_doc
                        update_engine_doc(thread_conn, ENGINE_ID_PRICE, {"run_price_service": True})
                    except Exception:
                        pass
                    sio.emit('priceBroker', {'run': True})
                    print("PRICE BROKER STOPPED")
                    priceBrokerUID = ''

                clientList = {key: val for key, val in clientList.items() if val != sid}

            serve(app, host='0.0.0.0', port=5000, threads=8)
        finally:
            thread_conn.close()

    socketThread = threading.Thread(target=socketServer)
    socketThread.start()
    intellistock_logger.log("\nService is currently \033[04m\033[01mOnline", "green", service="STATUS")

    def run_config_changefeed():
        """Run changefeed on Config.Pings and call status_service_change.

        Self-healing: reconnects on any transient RethinkDB connection loss."""
        run_reconnecting_changefeed(
            lambda c: r.db(DB_NAME).table('Config').get('Pings').changes().run(c),
            status_service_change,
            "Config",
            get_conn=get_conn,
            log=intellistock_logger.log,
        )

    def run_instances_changefeed():
        """Run changefeed on Instances and call run_thread_service_change.

        Self-healing: reconnects on any transient RethinkDB connection loss."""
        run_reconnecting_changefeed(
            lambda c: r.db(DB_NAME).table('Instances').changes().run(c),
            run_thread_service_change,
            "Instances",
            get_conn=get_conn,
            log=intellistock_logger.log,
        )

    config_feed_thread = threading.Thread(target=run_config_changefeed, daemon=True)
    config_feed_thread.start()
    instances_feed_thread = threading.Thread(target=run_instances_changefeed, daemon=True)
    instances_feed_thread.start()
    agent_control_feed_thread = threading.Thread(target=run_agent_control_changefeed, daemon=True)
    agent_control_feed_thread.start()

    digest_control_feed_thread = threading.Thread(target=run_digest_control_changefeed, daemon=True)
    digest_control_feed_thread.start()

    discover_control_feed_thread = threading.Thread(target=run_discover_control_changefeed, daemon=True)
    discover_control_feed_thread.start()

    nexus_control_feed_thread = threading.Thread(target=run_nexus_control_changefeed, daemon=True)
    nexus_control_feed_thread.start()

    global nexus_container_poll_thread
    nexus_container_poll_thread = threading.Thread(target=_nexus_container_poll, daemon=True)
    nexus_container_poll_thread.start()

    resume_timer_thread = threading.Thread(target=check_resume_timer, daemon=True)
    resume_timer_thread.start()

    # Launch any instances already in DB with runCommand=True (changefeed only sees future changes)
    launch_instances_from_db()
    # Launch engine containers if EngineControl says running (changefeed only sees future changes)
    launch_agent_from_db()
    launch_digest_from_db()
    launch_discover_from_db()
    launch_nexus_from_db()

    intellistock_logger.log("Server running. Press Ctrl+C to stop.", "green", service="SERVER")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        intellistock_logger.log("Shutting down...", "yellow", service="SERVER")
        os._exit(0)

if __name__ == '__main__':
    if os.name == 'nt':
        try:
            system("title " + "Intellistock Server")
        except Exception:
            pass
    run()

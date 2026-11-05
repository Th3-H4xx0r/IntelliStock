"""Server jobs for the TR ingests and ledger builds (plan Task 13).

The production server has no shell access, so the news, theme, macro and
filings backfills and the research-ledger build run as jobs submitted
through the API (``POST /trend-research/jobs``).

Execution. ``submit`` writes a ``queued`` row to ``TrendResearchJobs`` and
launches a detached container from the backend image -- the same kind the
backtest engine launches (``engines/backtest_engine.py``): same network,
database environment and read-only ``~/.claude`` mount, so ``claude-cli``
models work -- named ``tr-job-<short id>``, removed when it exits, running
``python3 -m trend_research.jobs run <job_id>``. That process re-reads the
row, marks it ``running``, runs the kind's builder in chunks (a day of news,
a theme or title window, a macro window, a symbol, a ledger week), writes
progress after every chunk, and ends ``done`` / ``failed`` / ``stopped``.

Resumability. The builders skip work they already finished (ingest progress
markers, ``ok`` ledger rows), so a job that dies is resumed by submitting
the same kind and params again. A ``queued`` or ``running`` job whose
container is gone (OOM, redeploy, host restart) is reported -- and stored --
as ``failed`` with an error starting ``lost``; that frees its kind.

Stop. ``stop`` stops the container, then marks the row ``stopped`` if it is
still queued or running (a job that finished meanwhile keeps its state). If
the container cannot be stopped the row keeps its state, with the failure in
``error``, so the kind stays locked. The runner also checks the row between
chunks and exits on ``stopped``.

Pacing. Each ingest's client paces its own requests, but a fresh client per
chunk would send its first request unpaced; the runners therefore share one
pacing ``http_get`` per job (GDELT 15 s, ALFRED 1 s, Alpaca 190/min), so the
source's interval holds across chunk boundaries. EDGAR's throttle is already
process-wide.

LLM providers. The ledger build supports what ``trend_research.llm``
supports: ``claude-cli`` (the host's subscription login, mounted read-only)
and API-key providers (``anthropic``, key from the Models row). The job
container does not mount ``codex_auth``, so codex-cli models are not
available to a job.

Secrets. Errors and log lines are redacted (``key=value`` pairs with
credential-like names, bearer tokens, URL passwords, ``sk-`` keys, and every
known secret value: the paper data credentials, the model's API key and
credential-named environment variables) before they reach the row.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import logging
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

__all__ = ["JOB_KINDS", "TABLE", "STATES", "submit", "status", "stop", "run", "main",
           "redact", "container_name", "DockerLauncher", "JobConflict", "JobLaunchError"]

log = logging.getLogger(__name__)

JOB_KINDS = ("ingest_news", "ingest_themes", "ingest_macro", "ingest_filings",
             "build_ledger")
TABLE = "TrendResearchJobs"
STATES = ("queued", "running", "done", "failed", "stopped")
ACTIVE_STATES = ("queued", "running")
ID_PREFIX = "tr-job|"
_KEY_END = "\uffff"                 # above every ASCII key byte under COLLATE "C"

DEFAULT_MAX_CALLS = 2000            # config max_llm_calls_per_build default
#: A queued row with no launch recorded this long after creation is lost.
LAUNCH_GRACE_SEC = 300
ERROR_CHARS = 1000
LOG_TAIL = 20
LOG_LINE_CHARS = 300
THEME_TITLE_WINDOW_DAYS = 91        # ~13 title weeks per progress update
MACRO_WINDOW_DAYS = 360             # 30 twelve-day ALFRED chunks per update
STOP_TIMEOUT_SEC = 10
#: Progress heartbeat interval while the ledger build sleeps out a quota.
QUOTA_HEARTBEAT_SEC = 60

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JobConflict(ValueError):
    """A job of the same kind is still queued or running."""


class JobLaunchError(RuntimeError):
    """The job row was written but its container could not be launched."""

    def __init__(self, job_id: str, message: str):
        super().__init__(message)
        self.job_id = job_id


class _Stopped(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


# -- redaction ------------------------------------------------------------------------

_SECRET_NAME = (r"[A-Za-z0-9_\-]*(?:secret|token|passw(?:or)?d|passwd|pwd|api[_\-]?key|apikey|"
                r"access[_\-]?key|private[_\-]?key|credential|cred[_\-]?key|dsn|key)"
                r"[A-Za-z0-9_\-]*")
_KV_RE = re.compile(r"(?i)\b(%s)(\s*[\"']?\s*[=:]\s*[\"']?)(?!\[redacted\])([^\s\"',;&}\]]+)"
                    % _SECRET_NAME)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}")
_URL_AUTH_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+@")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_ENV_SECRET_RE = re.compile(r"(?i)(secret|token|passw|api_?key|cred|dsn|private)")
_MIN_SECRET_LEN = 6


def _env_secrets() -> list:
    return [v for k, v in os.environ.items()
            if _ENV_SECRET_RE.search(k) and v and len(v) >= 8]


def redact(text, secrets=(), limit: int = ERROR_CHARS) -> str:
    """``text`` with credentials removed, cut to ``limit`` characters."""
    out = str(text or "")
    for s in sorted({str(s) for s in list(secrets) + _env_secrets() if s}, key=len,
                    reverse=True):
        if len(s) >= _MIN_SECRET_LEN:
            out = out.replace(s, "[redacted]")
    out = _URL_AUTH_RE.sub(r"\1[redacted]@", out)
    out = _BEARER_RE.sub(r"\1 [redacted]", out)
    out = _SK_RE.sub("[redacted]", out)
    out = _KV_RE.sub(r"\1\2[redacted]", out)
    return out[:limit]


# -- params ---------------------------------------------------------------------------

def _date_param(params: dict, name: str, *, required: bool = True) -> str | None:
    value = params.get(name)
    if value in (None, ""):
        if required:
            raise ValueError("param %r is required (YYYY-MM-DD)" % name)
        return None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("param %r must be a date YYYY-MM-DD, got %r" % (name, value))
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("param %r is not a valid date: %r" % (name, value)) from None
    return value


def _str_param(params: dict, name: str, *, required: bool) -> str | None:
    value = params.get(name)
    if value in (None, ""):
        if required:
            raise ValueError("param %r is required" % name)
        return None
    if not isinstance(value, str) or not value.strip() or "|" in value:
        raise ValueError("param %r must be a non-empty string without '|'" % name)
    return value.strip()


def _str_list_param(params: dict, name: str) -> list | None:
    value = params.get(name)
    if value is None:
        return None
    if (not isinstance(value, list) or not value
            or not all(isinstance(v, str) and v.strip() for v in value)):
        raise ValueError("param %r must be a non-empty list of strings" % name)
    return [v.strip() for v in value]


_ALLOWED = {
    "ingest_news": {"start", "end", "instance"},
    "ingest_themes": {"start", "end", "articles", "articles_start", "themes"},
    "ingest_macro": {"start", "end", "series"},
    "ingest_filings": {"start", "end", "symbols", "chain_id"},
    "build_ledger": {"start", "end", "chain_id", "model_id", "max_calls", "prompt_version",
                     "instance"},
}


def validate_params(kind: str, params) -> dict:
    """The job's params, checked and normalized to JSON values; ValueError
    when ``kind`` is unknown or a param is missing, malformed or unknown."""
    if kind not in JOB_KINDS:
        raise ValueError("unknown job kind %r (known: %s)" % (kind, ", ".join(JOB_KINDS)))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    unknown = sorted(set(params) - _ALLOWED[kind])
    if unknown:
        raise ValueError("unknown param(s) for %s: %s (allowed: %s)"
                         % (kind, ", ".join(unknown), ", ".join(sorted(_ALLOWED[kind]))))
    out = {"start": _date_param(params, "start"), "end": _date_param(params, "end")}
    if out["end"] < out["start"]:
        raise ValueError("end %s is before start %s" % (out["end"], out["start"]))

    if kind == "ingest_news":
        from trend_research.ingest_alpaca_news import LIVE_INSTANCES, PAPER_INSTANCE
        out["instance"] = _str_param(params, "instance", required=False) or PAPER_INSTANCE
        if out["instance"] in LIVE_INSTANCES:
            raise ValueError("instance %s refused: news ingest uses paper data credentials "
                             "only" % out["instance"])
    elif kind == "ingest_themes":
        from trend_research.ingest_gdelt import THEME_QUERIES, THEMES
        articles = params.get("articles", True)
        if not isinstance(articles, bool):
            raise ValueError("param 'articles' must be true or false")
        out["articles"] = articles
        out["articles_start"] = _date_param(params, "articles_start", required=False)
        if out["articles_start"] and out["articles_start"] > out["end"]:
            raise ValueError("articles_start must be on or before end")
        themes = _str_list_param(params, "themes") or list(THEMES)
        bad = [t for t in themes if t not in THEME_QUERIES]
        if bad:
            raise ValueError("unknown GDELT theme(s): %s" % ", ".join(bad))
        out["themes"] = themes
    elif kind == "ingest_macro":
        from trend_research.ingest_macro import MACRO_SERIES, _check_series
        series = _str_list_param(params, "series") or list(MACRO_SERIES)
        for s in series:
            _check_series(s)
        out["series"] = series
    elif kind == "ingest_filings":
        symbols = params.get("symbols")
        if symbols == "ledger":
            out["symbols"] = "ledger"
            out["chain_id"] = _str_param(params, "chain_id", required=False)
            if not out["chain_id"]:
                raise ValueError('symbols "ledger" needs param chain_id')
        else:
            if isinstance(symbols, str):
                raise ValueError('param symbols must be a list of tickers or "ledger"')
            out["symbols"] = _str_list_param(params, "symbols")
            if not out["symbols"]:
                raise ValueError('param symbols is required: a list of tickers or "ledger"')
            if params.get("chain_id") is not None:
                raise ValueError('chain_id is only used with symbols "ledger"')
    elif kind == "build_ledger":
        from trend_research import prompts
        from trend_research.ingest_alpaca_news import LIVE_INSTANCES, PAPER_INSTANCE
        out["chain_id"] = _str_param(params, "chain_id", required=True)
        out["model_id"] = _str_param(params, "model_id", required=True)
        max_calls = params.get("max_calls", DEFAULT_MAX_CALLS)
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
            raise ValueError("param max_calls must be a positive integer")
        out["max_calls"] = max_calls
        out["prompt_version"] = (_str_param(params, "prompt_version", required=False)
                                 or prompts.PROMPT_VERSION)
        out["instance"] = _str_param(params, "instance", required=False) or PAPER_INSTANCE
        if out["instance"] in LIVE_INSTANCES:
            raise ValueError("instance %s refused: bars use paper data credentials only"
                             % out["instance"])
    return out


# -- the launcher ---------------------------------------------------------------------

def container_name(job_id: str) -> str:
    """``tr-job-<12 hex>``: a Docker-safe name derived from the job id."""
    return "tr-job-" + hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:12]


# Fallback copy of server._PG_CONTAINER_ENV_KEYS, used only when server.py
# will not import (the backtest engine falls back the same way).
_PG_ENV_KEYS = ("PG_DSN", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                "POSTGRES_PASSWORD", "POSTGRES_DB", "PG_POOL_MIN", "PG_POOL_MAX",
                "PG_POOL_TIMEOUT", "PG_RECONNECT_TIMEOUT", "PG_MAX_ROWS",
                "DB_WATCH_POLL_SECONDS")
_PASS_ENV_KEYS = ("RETHINKDB_HOST", "RETHINKDB_PORT", "INTELLISTOCK_CRED_KEY",
                  "SEC_EDGAR_USER_AGENT", "TZ")


def _fallback_pg_env() -> dict:
    env = {k: str(os.environ[k]) for k in _PG_ENV_KEYS if os.environ.get(k) not in (None, "")}
    dsn_override = (os.environ.get("INSTANCE_PG_DSN") or "").strip()
    if dsn_override:
        env["PG_DSN"] = dsn_override
    host_override = (os.environ.get("INSTANCE_POSTGRES_HOST") or "").strip()
    if host_override:
        env["POSTGRES_HOST"] = host_override
        if not dsn_override:
            env.pop("PG_DSN", None)       # PG_DSN would win over POSTGRES_HOST
    return env


def _container_env() -> dict:
    """The job container's environment: the database coordinates every
    spawned container gets (server._pg_container_env, as the backtest engine
    uses it) plus the credential key and the few settings the builders read."""
    try:
        from server import _pg_container_env
        env = _pg_container_env()
    except Exception:
        env = _fallback_pg_env()
    env.update({k: str(os.environ[k]) for k in _PASS_ENV_KEYS
                if os.environ.get(k) not in (None, "")})
    env["PYTHONUNBUFFERED"] = "1"
    return env


class DockerLauncher:
    """Runs a job in a detached ``tr-job-*`` container on the host's Docker."""

    mode = "container"
    _LIVE = ("created", "running", "restarting")

    def __init__(self, client_factory: Callable | None = None):
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import docker
        return docker.from_env()

    @staticmethod
    def _self_container(client):
        cid = os.environ.get("HOSTNAME")
        if not cid and os.path.isfile("/etc/hostname"):
            with open("/etc/hostname") as fh:
                cid = fh.read().strip()
        if not cid:
            return None
        try:
            return client.containers.get(cid)
        except Exception:
            return None

    @staticmethod
    def _network(client) -> str:
        """The network backtest containers get (``backtest_engine._get_network``:
        INSTANCE_DOCKER_NETWORK if it exists, else this container's own);
        RuntimeError when none can be determined -- a guessed network would
        leave the job unable to reach Postgres."""
        from engines.backtest_engine import _get_network
        network = _get_network(client)
        if not network:
            raise RuntimeError("no Docker network for the job container: set "
                               "INSTANCE_DOCKER_NETWORK to the network the api service is on")
        return network

    @staticmethod
    def _claude_volumes(me) -> dict:
        # The backtest engine mounts CLAUDE_HOST_HOME / CLAUDE_HOST_CONFIG
        # read-only; when the API service lacks those variables, the host
        # paths of its own /root/.claude mounts are the same login.
        mounts = {m.get("Destination"): m.get("Source")
                  for m in ((me.attrs.get("Mounts") if me is not None else None) or ())
                  if isinstance(m, dict)}
        home = (os.environ.get("CLAUDE_HOST_HOME") or "").strip() or mounts.get("/root/.claude")
        config = ((os.environ.get("CLAUDE_HOST_CONFIG") or "").strip()
                  or mounts.get("/root/.claude.json"))
        volumes = {}
        if home:
            volumes[home] = {"bind": "/root/.claude", "mode": "ro"}
            if config:
                volumes[config] = {"bind": "/root/.claude.json", "mode": "ro"}
        return volumes

    def launch(self, job_id: str) -> dict:
        client = self._client()
        me = self._self_container(client)
        image = ((os.environ.get("DOCKER_INSTANCE_IMAGE") or "").strip()
                 or (me.attrs.get("Image") if me is not None else None)
                 or "intellistock-backend")
        name = container_name(job_id)
        network = self._network(client)
        try:
            old = client.containers.get(name)
        except Exception:
            old = None
        if old is not None:
            if getattr(old, "status", "") in self._LIVE:
                raise RuntimeError("container %s is already running" % name)
            old.remove(force=True)
        volumes = self._claude_volumes(me)
        client.containers.run(
            image,
            command=["python3", "-m", "trend_research.jobs", "run", job_id],
            name=name,
            environment=_container_env(),
            network=network,
            volumes=volumes,
            labels={"intellistock.tr_job": job_id},
            detach=True,
            auto_remove=True,
        )
        return {"mode": self.mode, "container": name, "image": str(image)[:80],
                "claude_mount": bool(volumes)}

    def is_alive(self, job_id: str) -> bool | None:
        """True / False, or None when Docker cannot be asked."""
        try:
            client = self._client()
        except Exception:
            return None
        try:
            c = client.containers.get(container_name(job_id))
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return False
            return None
        return getattr(c, "status", "") in self._LIVE

    def stop(self, job_id: str) -> None:
        client = self._client()
        try:
            c = client.containers.get(container_name(job_id))
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return
            raise
        c.stop(timeout=STOP_TIMEOUT_SEC)


def _launcher(launcher):
    return launcher if launcher is not None else DockerLauncher()


# -- the job row ----------------------------------------------------------------------

_PUBLIC = ("id", "kind", "params", "state", "progress", "created_at", "started_at",
           "finished_at", "error", "runner")


def _public(row: dict) -> dict:
    out = {k: row.get(k) for k in _PUBLIC}
    out["progress"] = out["progress"] or {}
    return out


def _get(st, job_id: str) -> dict:
    row = st.get(TABLE, job_id) if isinstance(job_id, str) and job_id else None
    if not isinstance(row, dict):
        raise KeyError(job_id)
    return row


def _reconcile(st, row: dict, launcher, now: datetime) -> dict:
    """``row``, or -- when it is queued/running and its container is gone --
    the row after marking it ``failed`` (lost)."""
    if row.get("state") not in ACTIVE_STATES:
        return row
    if not row.get("launched_at"):
        created = _parse_iso(row.get("created_at"))
        if created is None or (now - created).total_seconds() < LAUNCH_GRACE_SEC:
            return row
        reason = "lost: never launched"
    else:
        try:
            alive = launcher.is_alive(row["id"])
        except Exception:
            alive = None
        if alive is not False:
            return row
        reason = "lost: container %s is gone" % container_name(row["id"])
    fresh = st.get(TABLE, row["id"]) or row        # it may have finished meanwhile
    if fresh.get("state") not in ACTIVE_STATES:
        return fresh
    st.update(TABLE, row["id"], {"state": "failed", "finished_at": _iso(now),
                                 "error": reason + "; resubmit to resume"})
    return st.get(TABLE, row["id"])


def _rows(st) -> list:
    return st.run(st.between(TABLE, ID_PREFIX, ID_PREFIX + _KEY_END))


def submit(kind: str, params: dict, *, store=None, launcher=None, now=None) -> str:
    """Validate, record and launch a job; return its id.

    ValueError for an unknown kind or bad params, ``JobConflict`` when a job
    of the same kind is still queued or running, ``JobLaunchError`` (the row
    is then ``failed``) when the container cannot be started."""
    clean = validate_params(kind, params)
    st = _store(store)
    launcher = _launcher(launcher)
    now = now or _utcnow
    t = now()
    for row in _rows(st):
        if row.get("kind") == kind and row.get("state") in ACTIVE_STATES:
            row = _reconcile(st, row, launcher, t)
            if row.get("state") in ACTIVE_STATES:
                raise JobConflict("a %s job is already %s: %s"
                                  % (kind, row["state"], row["id"]))
    job_id = "%s%s|%s" % (ID_PREFIX,
                          t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), kind)
    st.insert(TABLE, {"id": job_id, "kind": kind, "params": clean, "state": "queued",
                      "progress": {}, "created_at": _iso(t), "started_at": None,
                      "finished_at": None, "error": None,
                      "runner": {"mode": getattr(launcher, "mode", "container"),
                                 "container": container_name(job_id)}})
    try:
        info = launcher.launch(job_id)
    except Exception as exc:
        error = redact("launch failed: %s: %s" % (type(exc).__name__, exc))
        st.update(TABLE, job_id, {"state": "failed", "finished_at": _iso(now()),
                                  "error": error})
        raise JobLaunchError(job_id, error) from None
    patch = {"launched_at": _iso(now())}
    if isinstance(info, dict):
        patch["runner"] = {k: v for k, v in info.items() if isinstance(v, (str, bool, int))}
    st.update(TABLE, job_id, patch)
    log.info("TR job %s launched (%s)", job_id, container_name(job_id))
    return job_id


def status(job_id: str, *, store=None, launcher=None, now=None) -> dict:
    """``{id, kind, params, state, progress, started_at, finished_at, error,
    ...}``; KeyError for an unknown id. A queued/running job whose container
    is gone is marked ``failed`` (error ``lost: ...``) first."""
    st = _store(store)
    row = _reconcile(st, _get(st, job_id), _launcher(launcher), (now or _utcnow)())
    return _public(row)


def stop(job_id: str, *, store=None, launcher=None, now=None) -> dict:
    """Stop a queued or running job (its container, then the row); a
    finished job is returned unchanged. KeyError for an unknown id."""
    st = _store(store)
    row = _get(st, job_id)
    if row.get("state") not in ACTIVE_STATES:
        return _public(row)
    try:
        _launcher(launcher).stop(job_id)
    except Exception as exc:
        # The job may still be running: keep its state (and the kind's lock).
        error = redact("stop failed, job may still be running: %s: %s"
                       % (type(exc).__name__, exc))
        log.warning("TR job %s: %s", job_id, error)
        if _get(st, job_id).get("state") in ACTIVE_STATES:
            st.update(TABLE, job_id, {"error": error})
        return _public(_get(st, job_id))
    if _get(st, job_id).get("state") in ACTIVE_STATES:     # it may have finished meanwhile
        st.update(TABLE, job_id, {"state": "stopped", "finished_at": _iso((now or _utcnow)())})
    return _public(_get(st, job_id))


# -- running a job --------------------------------------------------------------------

class _TailHandler(logging.Handler):
    def __init__(self, job):
        super().__init__(logging.INFO)
        self._job = job
        self.lines = collections.deque(maxlen=LOG_TAIL)
        self._lock_tail = threading.Lock()

    def emit(self, record):
        try:
            line = "%s %s %s: %s" % (_iso(datetime.fromtimestamp(record.created, timezone.utc)),
                                     record.levelname, record.name, record.getMessage())
            with self._lock_tail:
                self.lines.append(redact(line, self._job.secrets, LOG_LINE_CHARS))
        except Exception:
            pass

    def snapshot(self) -> list:
        with self._lock_tail:
            return list(self.lines)


class Job:
    """What a runner sees: its params, progress writes and the stop check."""

    def __init__(self, st, job_id: str, now: Callable[[], datetime], *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.st = st
        self.id = job_id
        self.now = now
        self.sleep = sleep
        self.clock = clock
        self.secrets: set = set()
        self.tail = _TailHandler(self)

    def add_secrets(self, *values) -> None:
        self.secrets.update(str(v) for v in values if v)

    def check_stop(self) -> None:
        row = self.st.get(TABLE, self.id) or {}
        if row.get("state") == "stopped":
            raise _Stopped()

    def step(self, **progress) -> None:
        """Merge ``progress`` into the row (string values redacted), with
        ``updated_at`` and the recent log lines."""
        clean = {}
        for k, v in progress.items():
            if isinstance(v, str):
                v = redact(v, self.secrets, LOG_LINE_CHARS)
            clean[k] = v
        clean["updated_at"] = _iso(self.now())
        clean["log"] = self.tail.snapshot()
        self.st.update(TABLE, self.id, {"progress": clean})


def paced_http_get(http_get: Callable, interval: float, *, sleep: Callable[[float], None],
                   clock: Callable[[], float]) -> Callable:
    """``http_get`` with request starts at least ``interval`` seconds apart
    across every call that shares it."""
    lock = threading.Lock()
    last = [None]

    def get(url, **kw):
        with lock:
            if last[0] is not None:
                wait = last[0] + interval - clock()
                if wait > 0:
                    sleep(wait)
            last[0] = clock()
        return http_get(url, **kw)
    return get


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _windows(start: date, end: date, days: int):
    lo = start
    while lo <= end:
        hi = min(end, lo + timedelta(days=days - 1))
        yield lo, hi
        lo = hi + timedelta(days=1)


def _run_news(params: dict, job: Job) -> None:
    from trend_research import ingest_alpaca_news as news
    key, secret = news.resolve_paper_credentials(params["instance"], store=job.st)
    job.add_secrets(key, secret)
    http_get = paced_http_get(news._default_http_get, news.MIN_REQUEST_INTERVAL,
                              sleep=job.sleep, clock=job.clock)
    start, end = _d(params["start"]), _d(params["end"])
    total = (end - start).days + 1
    written = 0
    for i in range(total):
        job.check_stop()
        d = start + timedelta(days=i)
        written += news.backfill_news(d, d, key=key, secret=secret, http_get=http_get,
                                      sleep=job.sleep, clock=job.clock, store=job.st)
        job.step(phase="days", done=i + 1, total=total, current=d.isoformat(),
                 rows_written=written)


def _run_themes(params: dict, job: Job) -> None:
    from trend_research import ingest_gdelt as gd
    start, end = _d(params["start"]), _d(params["end"])
    themes = params["themes"]
    windows = []
    if params["articles"]:
        windows = list(_windows(_d(params["articles_start"] or params["start"]), end,
                                THEME_TITLE_WINDOW_DAYS))
    total = len(themes) * (1 + len(windows))
    http_get = paced_http_get(gd._default_http_get, gd.MIN_REQUEST_INTERVAL,
                              sleep=job.sleep, clock=job.clock)
    pace = {"http_get": http_get, "sleep": job.sleep, "clock": job.clock, "store": job.st}
    done = written = 0
    for theme in themes:
        job.check_stop()
        written += gd.backfill_themes(start, end, themes=[theme], articles=False, **pace)
        done += 1
        job.step(phase="timelines", done=done, total=total, current=theme,
                 rows_written=written)
    for theme in themes:
        for lo, hi in windows:
            job.check_stop()
            # timelines were fetched for the whole range above
            written += gd.backfill_themes(lo, hi, themes=[theme], articles=True,
                                          articles_start=lo, timelines=False, **pace)
            done += 1
            job.step(phase="titles", done=done, total=total,
                     current="%s %s..%s" % (theme, lo, hi), rows_written=written)


def _run_macro(params: dict, job: Job) -> None:
    from trend_research import ingest_macro as macro
    windows = list(_windows(_d(params["start"]), _d(params["end"]), MACRO_WINDOW_DAYS))
    total = len(params["series"]) * len(windows)
    http_get = paced_http_get(macro._default_http_get, macro.MIN_REQUEST_INTERVAL,
                              sleep=job.sleep, clock=job.clock)
    done = written = 0
    for series in params["series"]:
        for lo, hi in windows:
            job.check_stop()
            written += macro.backfill_macro(lo, hi, series=(series,), http_get=http_get,
                                            sleep=job.sleep, clock=job.clock, store=job.st)
            done += 1
            job.step(phase="series", done=done, total=total,
                     current="%s %s..%s" % (series, lo, hi), rows_written=written)


def ledger_symbols(chain_id: str, *, store=None) -> list:
    """Every ticker in the chain's ledger rows (researched candidates, notes,
    recommendations and book picks), normalized and sorted."""
    from trend_research import book, ledger
    st = _store(store)
    out = set()
    for row in st.iter(st.between(ledger.TABLE, chain_id + "|", chain_id + "|" + _KEY_END)):
        if row.get("chain_id") != chain_id:
            continue
        items = []
        for field in ("candidates", "notes", "recommended"):
            items += list(row.get(field) or ())
        items += list((row.get("book") or {}).get("picks") or ())
        for item in items:
            sym = book.normalize_symbol((item or {}).get("symbol"))
            if sym:
                out.add(sym)
    return sorted(out)


class _SymbolsFailed(RuntimeError):
    pass


def _run_filings(params: dict, job: Job) -> None:
    from trend_research import filings
    start, end = _d(params["start"]), _d(params["end"])
    if params["symbols"] == "ledger":
        symbols = ledger_symbols(params["chain_id"], store=job.st)
    else:
        symbols = sorted({s.strip().upper() for s in params["symbols"]})
    failed, written = [], 0
    job.step(phase="symbols", done=0, total=len(symbols), rows_written=0, failed_symbols=[])
    for i, sym in enumerate(symbols):
        job.check_stop()
        try:
            written += filings.fetch_filings(sym, start, end, store=job.st)
        except filings.FilingsFetchError as exc:
            log.error("TR job filings %s failed: %s", sym, redact(exc, job.secrets))
            failed.append(sym)
        job.step(phase="symbols", done=i + 1, total=len(symbols), current=sym,
                 rows_written=written, failed_symbols=failed)
    if failed:
        raise _SymbolsFailed("%d of %d symbols failed (resubmit to retry): %s"
                             % (len(failed), len(symbols), ",".join(failed[:50])))


def _run_ledger(params: dict, job: Job) -> None:
    from swing_trader import clock
    from trend_research import build_ledger as bl
    from trend_research.llm import StageCaller
    m = bl.resolve_model_row(params["model_id"])
    job.add_secrets(m.get("api_key"))
    caller = StageCaller(m["provider"], m["model"], m["api_key"],
                         max_calls=params["max_calls"], provider_config=m["provider_config"])
    mondays = []
    d, last = clock.week_monday(_d(params["start"])), clock.week_monday(_d(params["end"]))
    while d <= last:
        mondays.append(d)
        d += timedelta(days=7)
    totals = {"model": m["model"], "provider": m["provider"],
              "prompt_version": params["prompt_version"], "built_ok": 0,
              "built_failed": 0, "skipped_ok": 0, "not_yet_due": 0, "quota_waits": 0,
              "calls_used": 0, "stopped": None}
    job.step(phase="weeks", done=0, total=len(mondays), ledger=totals)

    def quota_sleep(seconds):
        # The builder sleeps out a quota here; keep updated_at moving (and
        # honour a stop) instead of going silent for up to hours.
        until = _iso(job.now() + timedelta(seconds=seconds))
        remaining = float(seconds)
        while remaining > 0:
            job.check_stop()
            job.step(phase="quota_wait", quota_wait_until=until)
            chunk = min(float(QUOTA_HEARTBEAT_SEC), remaining)
            job.sleep(chunk)
            remaining -= chunk
        job.step(phase="weeks", quota_wait_until=None)

    for i, monday in enumerate(mondays):
        job.check_stop()
        s = bl.build(params["chain_id"], monday, monday, provider=m["provider"],
                     model=m["model"], api_key=m["api_key"], max_calls=params["max_calls"],
                     prompt_version=params["prompt_version"],
                     provider_config=m["provider_config"], caller=caller,
                     sleep=quota_sleep, store=job.st, bars_instance=params["instance"])
        for k in ("built_ok", "built_failed", "skipped_ok", "not_yet_due", "quota_waits",
                  "calls_used"):
            totals[k] += int(s.get(k) or 0)
        totals["stopped"] = s.get("stopped")
        job.step(phase="weeks", done=i + 1, total=len(mondays), current=monday.isoformat(),
                 ledger=totals)
        if s.get("stopped") == "budget":
            log.warning("TR job %s: LLM call budget %d reached at week %s", job.id,
                        params["max_calls"], monday)
            break


RUNNERS = {"ingest_news": _run_news, "ingest_themes": _run_themes,
           "ingest_macro": _run_macro, "ingest_filings": _run_filings,
           "build_ledger": _run_ledger}


def run(job_id: str, *, store=None, runners=None, now=None, sleep=None, clock=None) -> str:
    """Run a recorded job in this process and return its final state. A job
    already done, failed or stopped is left alone."""
    st = _store(store)
    now = now or _utcnow
    row = _get(st, job_id)
    if row.get("state") not in ACTIVE_STATES:
        log.warning("TR job %s is %s; not running it", job_id, row.get("state"))
        return row.get("state")
    runner = (runners or RUNNERS).get(row.get("kind"))
    if runner is None:
        st.update(TABLE, job_id, {"state": "failed", "finished_at": _iso(now()),
                                  "error": "no runner for kind %r" % row.get("kind")})
        return "failed"
    st.update(TABLE, job_id, {"state": "running", "started_at": _iso(now()), "error": None})
    job = Job(st, job_id, now, sleep=sleep or time.sleep, clock=clock or time.monotonic)
    tr_log = logging.getLogger("trend_research")
    tr_log.addHandler(job.tail)
    try:
        runner(dict(row.get("params") or {}), job)
    except _Stopped:
        log.info("TR job %s stopped", job_id)
        return "stopped"
    except Exception as exc:
        error = redact("%s: %s" % (type(exc).__name__, exc), job.secrets)
        log.error("TR job %s failed: %s", job_id, error)
        if (st.get(TABLE, job_id) or {}).get("state") == "stopped":
            return "stopped"
        st.update(TABLE, job_id, {"state": "failed", "finished_at": _iso(now()),
                                  "error": error,
                                  "progress": {"log": job.tail.snapshot()}})
        return "failed"
    finally:
        tr_log.removeHandler(job.tail)
    if (st.get(TABLE, job_id) or {}).get("state") == "stopped":
        return "stopped"
    st.update(TABLE, job_id, {"state": "done", "finished_at": _iso(now()),
                              "progress": {"log": job.tail.snapshot()}})
    log.info("TR job %s done", job_id)
    return "done"


# -- CLI ------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.jobs",
        description="Run a recorded TR job (launched by the API in a tr-job container).")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run one TrendResearchJobs row")
    p_run.add_argument("job_id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = run(args.job_id)
    print("TR job %s: %s" % (args.job_id, state))
    return 0 if state in ("done", "stopped") else 1


if __name__ == "__main__":
    sys.exit(main())

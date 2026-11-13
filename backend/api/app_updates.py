"""
Over-the-air updates for the IntelliStock iPhone app (ported from JarvisCopilot).

``scripts/publish-app-update.sh`` uploads a development-signed .ipa to
``POST /app-updates/upload``. The API keeps the current build and the one before
it, pushes "update available" to every registered iPhone, and hands out install
links from ``GET /app-updates/latest``, which the app polls on launch and on
every return to the foreground.

The links live under ``/ota/`` and need no session: iOS's installer fetches the
itms-services manifest and the IPA itself and sends no Authorization header.
Each URL instead carries a token that names one build, expires in minutes, and
is an HMAC under a key derived from ``JWT_SECRET``, so a leaked or guessed link
is useless. iOS insists on https for both URLs; the public origin is
``APP_UPDATES_PUBLIC_URL`` when set, otherwise the https origin the request came
in on (the reverse proxy's X-Forwarded-Proto / X-Forwarded-Host).

Builds are plain files plus a JSON index in ``APP_UPDATES_DIR`` (a named Docker
volume in production), not database rows: they are tens of MB and only the
newest two are ever kept.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import plistlib
import re
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, Response

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent

# platform → (file name served to the device, where the app's Info.plist sits in the archive)
PLATFORMS = {
    "ios": ("IntelliStock.ipa", re.compile(r"^Payload/[^/]+\.app/Info\.plist$")),
}
# The app every upload must be. An archive of anything else is refused, so a
# wrong IPA can never be offered to the phone.
DEFAULT_BUNDLE_ID = "dev.pkrishna.intellistockMobile"
LINK_TTL = 600
MAX_BYTES = 512 * 1024 * 1024
MAX_BUILD = 10 ** 12 - 1          # the token format allows 12 digits
_CHUNK = 1024 * 1024
_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"^(ios)\.(\d{1,12})\.(\d{1,12})\.([0-9a-f]{32})$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


def _root() -> Path:
    return Path(os.environ.get("APP_UPDATES_DIR", "").strip() or (_BACKEND_DIR / "app_updates"))


def _expected_bundle_id() -> str:
    return os.environ.get("APP_UPDATES_BUNDLE_ID", "").strip() or DEFAULT_BUNDLE_ID


def _load_index() -> dict:
    try:
        data = json.loads((_root() / "index.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(index: dict) -> None:
    path = _root() / "index.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def current(platform: str) -> Optional[dict]:
    return (_load_index().get(platform) or {}).get("current")


def _known(platform: str, build: int) -> Optional[dict]:
    slot = _load_index().get(platform) or {}
    for rec in (slot.get("current"), slot.get("previous")):
        if rec and rec.get("build") == build:
            return rec
    return None


# ── Signed links ────────────────────────────────────────────────────────────

def _signing_key() -> Optional[bytes]:
    """A key of its own, derived from the API's JWT secret: an install-link
    signature can never double as a session token, and no new secret has to be
    provisioned. None when the API has no JWT secret (then no link is issued)."""
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        return None
    return hmac.new(secret.encode("utf-8"), b"intellistock/app-updates/v1", hashlib.sha256).digest()


def _sig(platform: str, build: int, exp: int) -> Optional[str]:
    key = _signing_key()
    if key is None:
        return None
    msg = f"ota|{platform}|{build}|{exp}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def make_token(platform: str, build: int, *, ttl: int = LINK_TTL, now: Optional[float] = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl)
    sig = _sig(platform, build, exp)
    if sig is None:
        raise RuntimeError("JWT_SECRET is not set; install links cannot be signed")
    return f"{platform}.{build}.{exp}.{sig}"


def check_token(token: str, *, now: Optional[float] = None) -> Optional[dict]:
    """The build a token grants, or None when it is malformed, forged, expired or
    names a build the server no longer keeps."""
    m = _TOKEN_RE.match(token or "")
    if not m:
        return None
    platform, build, exp, sig = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    expected = _sig(platform, build, exp)
    if expected is None or not hmac.compare_digest(sig, expected):
        return None
    if exp < (now if now is not None else time.time()):
        return None
    rec = _known(platform, build)
    return dict(rec, platform=platform) if rec else None


def public_base(request: Optional[Request]) -> str:
    """The https origin the /ota/ links point at, or "" when there is none (iOS
    refuses an itms-services manifest or package over plain http, so a link to
    one would only fail halfway through the install)."""
    configured = os.environ.get("APP_UPDATES_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured if configured.lower().startswith("https://") else ""
    if request is None:
        return ""
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    if proto != "https" and _visitor_used_https(headers):
        proto = "https"
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    if proto != "https" or not host or not _HOST_RE.match(host):
        return ""
    return f"https://{host}".lower()


def _visitor_used_https(headers) -> bool:
    """Whether a proxy in front says the visitor came in over https when the
    hop to us was plain http: Cloudflare's `CF-Visitor: {"scheme":"https"}`
    (Cloudflare terminates TLS and the proxy behind it reports http), or an
    RFC 7239 `Forwarded: proto=https`."""
    visitor = headers.get("cf-visitor") or ""
    if visitor:
        try:
            if str(json.loads(visitor).get("scheme", "")).lower() == "https":
                return True
        except (ValueError, AttributeError):
            pass
    forwarded = (headers.get("forwarded") or "").split(",")[0]
    for part in forwarded.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "proto" and value.strip('"').lower() == "https":
            return True
    return False


def install_url(platform: str, build: int, base: str) -> str:
    token = make_token(platform, build)
    manifest = f"{base}/ota/m/{token}.plist"
    return "itms-services://?action=download-manifest&url=" + quote(manifest, safe="")


def manifest_plist(rec: dict, base: str, token: str) -> bytes:
    return plistlib.dumps({"items": [{
        "assets": [{"kind": "software-package",
                    "url": f"{base}/ota/d/{token}/{PLATFORMS['ios'][0]}"}],
        "metadata": {"bundle-identifier": rec.get("bundle_id") or _expected_bundle_id(),
                     "bundle-version": rec.get("version") or "1.0",
                     "kind": "software", "title": "IntelliStock"},
    }]})


# ── Storing a build ─────────────────────────────────────────────────────────

class UploadError(ValueError):
    pass


def _read_info_plist(path: Path, platform: str) -> dict:
    pattern = PLATFORMS[platform][1]
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if pattern.match(name):
                    return plistlib.loads(zf.read(name))
    except (zipfile.BadZipFile, plistlib.InvalidFileException, OSError, ValueError) as exc:
        raise UploadError(f"not a readable {platform} build: {exc}") from exc
    raise UploadError(f"no app Info.plist in the {platform} archive")


def parse_build(value) -> int:
    """A build number from a query string: digits only, else 0 (refused later)."""
    text = str(value or "").strip()
    return int(text) if text.isdigit() and len(text) <= 12 else 0


def precheck(platform: str, build: int, length: int) -> None:
    """Everything that can be refused before a byte of the body is read."""
    if platform not in PLATFORMS:
        raise UploadError("platform must be ios")
    if build <= 0 or build > MAX_BUILD:
        raise UploadError("build must be a positive integer of at most 12 digits")
    if length <= 0 or length > MAX_BYTES:
        raise UploadError(f"Content-Length must be between 1 byte and {MAX_BYTES // (1024 * 1024)} MiB")
    cur = current(platform)
    if cur and build <= int(cur.get("build") or 0):
        raise UploadError(f"build {build} is not newer than the current build {cur.get('build')}")


def new_part(platform: str, build: int) -> tuple:
    """A temp file of its own in the store (fd, Path): a retry racing the first
    attempt must not write into the file the first one is about to move into place."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=root, prefix=f".{platform}-{build}-", suffix=".part")
    return fd, Path(name)


def finish_upload(platform: str, build: int, tmp: Path, length: int, sha256: str, *,
                  commit: str = "", notes: str = "") -> dict:
    """Make a fully received ``tmp`` the platform's current build. The archive's own
    Info.plist must say ``build`` (else the app would install, read an older number
    and offer itself again) and must be this app."""
    info = _read_info_plist(tmp, platform)
    if str(info.get("CFBundleVersion") or "") != str(build):
        raise UploadError(f"the archive is build {info.get('CFBundleVersion')!r}, not {build}")
    bundle_id = str(info.get("CFBundleIdentifier") or "")
    if bundle_id != _expected_bundle_id():
        raise UploadError(f"the archive is {bundle_id or 'an app with no bundle id'}, "
                          f"not {_expected_bundle_id()}")
    final = _root() / f"{platform}-{build}.ipa"
    rec = {
        "build": build,
        "version": str(info.get("CFBundleShortVersionString") or ""),
        "bundle_id": bundle_id,
        "commit": (commit or "")[:40],
        "notes": (notes or "").strip()[:2000],
        "size": length,
        "sha256": sha256,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file": final.name,
    }
    with _LOCK:
        index = _load_index()
        slot = index.get(platform) or {}
        cur = slot.get("current")
        if cur and build <= int(cur.get("build") or 0):
            raise UploadError(f"build {build} is not newer than the current build {cur.get('build')}")
        os.replace(tmp, final)
        index[platform] = {"current": rec, "previous": cur}
        _save_index(index)
        _prune(index)
    return rec


def store_upload(platform: str, build: int, rfile, length: int, *,
                 commit: str = "", notes: str = "") -> dict:
    """Stream ``length`` bytes from ``rfile`` into the store as ``platform``'s new
    current build (the synchronous path; the HTTP route streams the request body
    through the same steps)."""
    precheck(platform, build, length)
    fd, tmp = new_part(platform, build)
    digest = hashlib.sha256()
    remaining = length
    try:
        with os.fdopen(fd, "wb") as out:
            while remaining:
                chunk = rfile.read(min(_CHUNK, remaining))
                if not chunk:
                    raise UploadError("body shorter than Content-Length")
                out.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        return finish_upload(platform, build, tmp, length, digest.hexdigest(),
                             commit=commit, notes=notes)
    finally:
        tmp.unlink(missing_ok=True)


def rollback(platform: str) -> Optional[dict]:
    """Make the previous build current again (the bad one is dropped)."""
    with _LOCK:
        index = _load_index()
        slot = index.get(platform) or {}
        prev = slot.get("previous")
        if not prev:
            return None
        index[platform] = {"current": prev, "previous": None}
        _save_index(index)
        _prune(index)
        return prev


def _prune(index: dict) -> None:
    keep = {rec["file"] for slot in index.values() if isinstance(slot, dict)
            for rec in (slot.get("current"), slot.get("previous")) if rec}
    for path in _root().iterdir():
        if path.suffix == ".ipa" and path.name not in keep:
            path.unlink(missing_ok=True)


def announce(platform: str, rec: dict) -> dict:
    """Push "update available" to every user with a registered iPhone. The app
    recognises the payload by ``kind: app_update`` and opens onto the update
    sheet. Best effort: a push failure never fails the upload."""
    if platform != "ios":
        return {"sent": 0}
    first = next((ln.strip(" -•*\t") for ln in (rec.get("notes") or "").splitlines() if ln.strip()), "")
    body = f"Build {rec['build']} — {first}" if first else f"Build {rec['build']} is ready to install."
    data = {"kind": "app_update", "platform": platform, "build": rec["build"]}
    sent = 0
    try:
        from apns_sender import send_to_user
        from interactive_utils import action_push_device_user_ids, get_conn
        for user_id in action_push_device_user_ids(get_conn()):
            res = send_to_user(user_id, title="IntelliStock update available", body=body[:220],
                               category="app_update", data=data)
            sent += int((res or {}).get("sent", 0) or 0)
    except Exception:
        logger.warning("app_update push failed", exc_info=True)
    return {"sent": sent}


def latest(platform: str, client_build: int, base: str) -> dict:
    rec = current(platform)
    # Build 1 (or 0) is an unstamped build: it may well be newer code than
    # anything published, so it's never offered one.
    if not rec or client_build <= 1 or int(rec.get("build") or 0) <= client_build:
        return {"available": False, "platform": platform,
                "build": int(rec.get("build") or 0) if rec else 0}
    offer = {
        "available": True, "platform": platform,
        "build": rec["build"], "version": rec.get("version") or "",
        "notes": rec.get("notes") or "", "commit": rec.get("commit") or "",
        "size": rec.get("size") or 0, "uploaded_at": rec.get("uploaded_at") or "",
        "install_url": "",
    }
    if not base:
        offer["error"] = ("Install links need the API's public https address "
                          "(open the app against it, or set APP_UPDATES_PUBLIC_URL).")
    elif _signing_key() is None:
        offer["error"] = "The server has no JWT_SECRET to sign install links with."
    else:
        offer["install_url"] = install_url(platform, rec["build"], base)
    return offer


# Install links whose package iOS has started fetching. iOS fetches it only after
# the user taps Install on its own confirm, so this is how the app learns to step
# aside: iOS won't install over an app that is still running.
_STARTED: dict = {}
_STARTED_LOCK = threading.Lock()


def mark_started(token: str, *, now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    with _STARTED_LOCK:
        for old in [t for t, at in _STARTED.items() if now - at > 2 * LINK_TTL]:
            del _STARTED[old]
        _STARTED[token] = now


def install_started(token: str) -> bool:
    with _STARTED_LOCK:
        return token in _STARTED


# ── HTTP ────────────────────────────────────────────────────────────────────

def _forbidden() -> Response:
    return PlainTextResponse("forbidden", status_code=403, headers={"Cache-Control": "no-store"})


def build_router(current_user_dependency: Callable) -> APIRouter:
    """The routes, closed over the API's session dependency (``get_current_user``
    in api/main.py) so this module never imports main."""
    router = APIRouter()
    authed = Depends(current_user_dependency)

    @router.get("/app-updates/latest")
    def api_app_updates_latest(request: Request, platform: str = "ios", build: str = "0",
                               current_user: dict = authed):
        if platform not in PLATFORMS:
            raise HTTPException(status_code=400, detail="platform must be ios")
        return latest(platform, parse_build(build), public_base(request))

    @router.get("/app-updates/install-status")
    def api_app_updates_install_status(token: str = "", current_user: dict = authed):
        return {"started": install_started(token)}

    @router.post("/app-updates/upload")
    async def api_app_updates_upload(request: Request, background_tasks: BackgroundTasks,
                                     platform: str = "ios", build: str = "", commit: str = "",
                                     notes: str = "", current_user: dict = authed):
        """The raw .ipa as the request body (Content-Length required, no multipart)."""
        build_no = parse_build(build)
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        try:
            precheck(platform, build_no, length)
        except UploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        fd, tmp = await run_in_threadpool(new_part, platform, build_no)
        digest = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(fd, "wb") as out:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > length:
                        raise UploadError("body longer than Content-Length")
                    out.write(chunk)
                    digest.update(chunk)
            if received != length:
                raise UploadError("body shorter than Content-Length")
            rec = await run_in_threadpool(finish_upload, platform, build_no, tmp, length,
                                          digest.hexdigest(), commit=commit, notes=notes)
        except UploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            tmp.unlink(missing_ok=True)
        background_tasks.add_task(announce, platform, rec)
        logger.info("app update: %s build %s uploaded by %s", platform, build_no,
                    (current_user or {}).get("username"))
        return {"ok": True, "build": rec}

    @router.post("/app-updates/rollback")
    def api_app_updates_rollback(platform: str = "ios", current_user: dict = authed):
        if platform not in PLATFORMS:
            raise HTTPException(status_code=400, detail="platform must be ios")
        rec = rollback(platform)
        if not rec:
            raise HTTPException(status_code=404, detail="there is no previous build to roll back to")
        return {"ok": True, "build": rec}

    # Public install links: no session (iOS's installer has none to send). Every
    # one is gated by its signed, expiring token; anything else is a bare 403.

    @router.get("/ota/m/{token}.plist")
    def ota_manifest(token: str, request: Request):
        rec = check_token(token)
        base = public_base(request)
        if not rec or not base:
            return _forbidden()
        return Response(manifest_plist(rec, base, token), media_type="application/xml",
                        headers={"Cache-Control": "no-store"})

    @router.get("/ota/d/{token}/{name}")
    def ota_download(token: str, name: str):
        rec = check_token(token)
        if not rec or name != PLATFORMS[rec["platform"]][0]:
            return _forbidden()
        path = _root() / rec["file"]
        if not path.is_file():
            return _forbidden()
        mark_started(token)
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"Cache-Control": "no-store"})

    return router

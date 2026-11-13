"""Over-the-air iOS updates: an upload becomes the offered build, the phone gets a
signed install link (ported from JarvisCopilot's webui/tests/test_app_updates.py).

The install links are public (iOS's installer sends no Authorization header), so
everything about them rests on the token: it names one build, expires, and can't
be forged.
"""
from __future__ import annotations

import io
import os
import plistlib
import sys
import zipfile
from urllib.parse import quote, unquote

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api import app_updates as au  # noqa: E402

BUNDLE = au.DEFAULT_BUNDLE_ID
BASE = "https://api.example.dev"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_UPDATES_DIR", str(tmp_path / "app_updates"))
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.delenv("APP_UPDATES_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_UPDATES_BUNDLE_ID", raising=False)
    monkeypatch.setattr(au, "_STARTED", {})
    return tmp_path / "app_updates"


def _archive(build, bundle_id=BUNDLE, version="1.0.0") -> bytes:
    info = plistlib.dumps({"CFBundleVersion": str(build), "CFBundleShortVersionString": version,
                           "CFBundleIdentifier": bundle_id})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Payload/IntelliStock.app/Info.plist", info)
        zf.writestr("Payload/IntelliStock.app/PlugIns/PortfolioWidget.appex/Info.plist",
                    plistlib.dumps({"CFBundleVersion": "widget"}))
        zf.writestr("filler.bin", b"x" * 1000)
    return buf.getvalue()


def _upload(build, data=None, **kw):
    data = data if data is not None else _archive(build)
    return au.store_upload("ios", build, io.BytesIO(data), len(data), **kw)


# ── Offers ──────────────────────────────────────────────────────────────────

def test_an_uploaded_build_is_offered_to_an_older_app():
    _upload(200, notes="• Order history\n• Faster charts", commit="abc1234")
    offer = au.latest("ios", 100, BASE)
    assert offer["available"] and offer["build"] == 200 and offer["notes"].startswith("• Order")
    assert offer["version"] == "1.0.0" and offer["commit"] == "abc1234"
    assert offer["size"] > 0 and offer["uploaded_at"]
    assert offer["install_url"].startswith("itms-services://?action=download-manifest&url=")
    manifest = unquote(offer["install_url"].split("url=", 1)[1])
    assert manifest.startswith(f"{BASE}/ota/m/ios.200.") and manifest.endswith(".plist")


def test_the_same_or_a_newer_app_is_not_offered_anything():
    _upload(200)
    assert not au.latest("ios", 200, BASE)["available"]
    assert not au.latest("ios", 300, BASE)["available"]
    assert au.latest("ios", 300, BASE)["build"] == 200


def test_nothing_published_is_no_offer():
    assert au.latest("ios", 2, BASE) == {"available": False, "platform": "ios", "build": 0}


def test_an_unstamped_build_is_never_offered_an_update():
    # Build 1 / 0 is Xcode Run or an unstamped build: possibly newer code than anything published.
    _upload(200)
    assert not au.latest("ios", 1, BASE)["available"]
    assert not au.latest("ios", 0, BASE)["available"]
    # The shipped project.yml stamps 2, so a cable-installed build still gets offered one.
    assert au.latest("ios", 2, BASE)["available"]


def test_without_an_https_origin_no_link_is_handed_out():
    _upload(200)
    offer = au.latest("ios", 100, "")
    assert offer["available"] and offer["install_url"] == "" and "https" in offer["error"]


def test_without_a_jwt_secret_no_link_is_handed_out(monkeypatch):
    _upload(200)
    monkeypatch.delenv("JWT_SECRET")
    offer = au.latest("ios", 100, BASE)
    assert offer["install_url"] == "" and "JWT_SECRET" in offer["error"]


# ── Validation ──────────────────────────────────────────────────────────────

def test_an_archive_whose_build_number_disagrees_is_refused():
    with pytest.raises(au.UploadError, match="not 200"):
        _upload(200, data=_archive(1))


def test_an_archive_of_another_app_is_refused():
    with pytest.raises(au.UploadError, match="not dev.pkrishna.intellistockMobile"):
        _upload(5, data=_archive(5, bundle_id="com.example.other"))


def test_a_build_that_is_not_newer_is_refused():
    _upload(300)
    with pytest.raises(au.UploadError, match="not newer"):
        _upload(300)
    with pytest.raises(au.UploadError, match="not newer"):
        _upload(299)


@pytest.mark.parametrize("build", [0, -1, 10 ** 12])
def test_build_numbers_must_be_positive_and_fit_the_token(build):
    with pytest.raises(au.UploadError, match="positive integer"):
        au.precheck("ios", build, 10)


def test_only_ios_is_a_platform():
    with pytest.raises(au.UploadError, match="platform"):
        au.precheck("mac", 5, 10)


def test_the_size_is_capped():
    with pytest.raises(au.UploadError, match="Content-Length"):
        au.precheck("ios", 5, au.MAX_BYTES + 1)
    with pytest.raises(au.UploadError, match="Content-Length"):
        au.precheck("ios", 5, 0)


def test_build_query_values_parse_digits_only():
    assert au.parse_build("263161830") == 263161830
    for bad in ("", "12a", "-3", "1.5", None, "1" * 13):
        assert au.parse_build(bad) == 0


def test_a_short_or_bogus_body_leaves_nothing_behind(store):
    data = _archive(5)
    with pytest.raises(au.UploadError, match="shorter"):
        au.store_upload("ios", 5, io.BytesIO(data[:10]), len(data))
    with pytest.raises(au.UploadError):
        _upload(6, data=b"not a zip")
    assert au.current("ios") is None
    assert not [p for p in store.iterdir() if p.suffix in (".ipa", ".part")]


def test_only_current_and_previous_builds_are_kept_and_rollback_restores(store):
    for build in (10, 20, 30):
        _upload(build)
    files = sorted(p.name for p in store.glob("*.ipa"))
    assert files == ["ios-20.ipa", "ios-30.ipa"]
    assert au.rollback("ios")["build"] == 20
    assert au.current("ios")["build"] == 20
    assert sorted(p.name for p in store.glob("*.ipa")) == ["ios-20.ipa"]
    assert au.rollback("ios") is None


# ── Tokens ──────────────────────────────────────────────────────────────────

def test_tokens_expire_and_cannot_be_forged_or_retargeted():
    _upload(7)
    token = au.make_token("ios", 7, now=1000)
    assert au.check_token(token, now=1000)["build"] == 7
    assert au.check_token(token, now=1000 + au.LINK_TTL + 1) is None
    platform, build, exp, sig = token.split(".")
    assert au.check_token(f"{platform}.8.{exp}.{sig}", now=1000) is None
    assert au.check_token(f"mac.{build}.{exp}.{sig}", now=1000) is None
    assert au.check_token(f"{platform}.{build}.{int(exp) + 999}.{sig}", now=1000) is None
    assert au.check_token(f"{platform}.{build}.{exp}.{'0' * 32}", now=1000) is None
    assert au.check_token("../../etc/passwd") is None
    assert au.check_token("") is None


def test_a_token_signed_under_another_secret_is_refused(monkeypatch):
    _upload(7)
    token = au.make_token("ios", 7)
    monkeypatch.setenv("JWT_SECRET", "rotated")
    assert au.check_token(token) is None


def test_the_link_key_is_not_the_jwt_secret_itself():
    # A link signature must never be usable as (or reveal) the session-signing secret.
    assert au._signing_key() != b"test-jwt-secret"
    assert len(au._signing_key()) == 32


def test_a_token_for_a_dropped_build_stops_working():
    _upload(1)
    token = au.make_token("ios", 1)
    _upload(2)
    _upload(3)
    assert au.check_token(token) is None


# ── Public origin ───────────────────────────────────────────────────────────

class _Req:
    def __init__(self, headers, scheme="http"):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.url = type("U", (), {"scheme": scheme})()


def test_the_public_origin_comes_from_the_proxy_or_the_setting(monkeypatch):
    req = _Req({"host": "intellistock-api.example.dev", "x-forwarded-proto": "https"})
    assert au.public_base(req) == "https://intellistock-api.example.dev"
    assert au.public_base(_Req({"host": "10.0.0.5:8011"})) == ""  # plain http: no link
    assert au.public_base(_Req({"host": "evil\nhost", "x-forwarded-proto": "https"})) == ""
    monkeypatch.setenv("APP_UPDATES_PUBLIC_URL", "https://pinned.example.dev/")
    assert au.public_base(req) == "https://pinned.example.dev"
    monkeypatch.setenv("APP_UPDATES_PUBLIC_URL", "http://insecure.example.dev")
    assert au.public_base(req) == ""


# ── Push ────────────────────────────────────────────────────────────────────

def test_publishing_pushes_to_every_user_with_an_iphone(monkeypatch):
    import apns_sender
    import interactive_utils
    sent = []
    monkeypatch.setattr(interactive_utils, "get_conn", lambda: None)
    monkeypatch.setattr(interactive_utils, "action_push_device_user_ids", lambda conn: ["u1", "u2"])
    monkeypatch.setattr(apns_sender, "send_to_user",
                        lambda uid, **kw: sent.append((uid, kw)) or {"sent": 1})
    rec = _upload(42, notes="• Orders on the dashboard\n• Faster")
    assert au.announce("ios", rec) == {"sent": 2}
    assert [uid for uid, _ in sent] == ["u1", "u2"]
    kw = sent[0][1]
    assert kw["title"] == "IntelliStock update available"
    assert kw["body"] == "Build 42 — Orders on the dashboard"
    assert kw["category"] == "app_update"
    assert kw["data"] == {"kind": "app_update", "platform": "ios", "build": 42}


def test_a_failing_push_never_fails_the_publish(monkeypatch):
    import interactive_utils

    def boom(conn):
        raise RuntimeError("db down")
    monkeypatch.setattr(interactive_utils, "get_conn", lambda: None)
    monkeypatch.setattr(interactive_utils, "action_push_device_user_ids", boom)
    assert au.announce("ios", _upload(43)) == {"sent": 0}


# ── HTTP ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from api import main

    announced = []
    monkeypatch.setattr(au, "announce", lambda platform, rec: announced.append(rec["build"]))
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "u1", "username": "op"}
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        c = TestClient(main.app)  # no context manager: startup would reach for a database
        c.announced = announced
        yield c
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)
        main.app.dependency_overrides.pop(main.conn_dependency, None)


HTTPS = {"x-forwarded-proto": "https", "host": "api.example.dev"}


def _post_upload(client, build, data=None, notes="• one"):
    data = data if data is not None else _archive(build)
    return client.post(f"/app-updates/upload?platform=ios&build={build}&commit=abc&notes={quote(notes)}",
                       content=data, headers={"Content-Type": "application/octet-stream"})


def test_upload_over_http_stores_the_build_and_announces_it(client):
    res = _post_upload(client, 263161830, notes="• Orders\n• Charts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] and body["build"]["build"] == 263161830 and body["build"]["notes"] == "• Orders\n• Charts"
    assert au.current("ios")["build"] == 263161830
    assert client.announced == [263161830]


def test_upload_over_http_refuses_bad_input(client):
    assert _post_upload(client, 5, data=_archive(4)).status_code == 400
    assert client.post("/app-updates/upload?platform=ios&build=abc", content=b"x").status_code == 400
    assert client.post("/app-updates/upload?platform=mac&build=5", content=b"x").status_code == 400
    _post_upload(client, 10)
    res = _post_upload(client, 9)
    assert res.status_code == 400 and "not newer" in res.json()["detail"]
    assert client.announced == [10]


def test_latest_over_http_hands_out_a_link_on_the_https_origin(client):
    _post_upload(client, 50)
    offer = client.get("/app-updates/latest?platform=ios&build=2", headers=HTTPS).json()
    assert offer["available"] and offer["build"] == 50
    manifest_url = unquote(offer["install_url"].split("url=", 1)[1])
    assert manifest_url.startswith("https://api.example.dev/ota/m/ios.50.")
    plain = client.get("/app-updates/latest?platform=ios&build=2").json()  # http origin
    assert plain["available"] and plain["install_url"] == ""


def test_the_manifest_and_ipa_are_served_for_a_valid_token_only(client):
    data = _archive(9)
    assert _post_upload(client, 9, data=data).status_code == 200
    token = au.make_token("ios", 9)
    res = client.get(f"/ota/m/{token}.plist", headers=HTTPS)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    item = plistlib.loads(res.content)["items"][0]
    assert item["metadata"]["bundle-identifier"] == "dev.pkrishna.intellistockMobile"
    assert item["metadata"]["bundle-version"] == "1.0.0"
    assert item["metadata"]["title"] == "IntelliStock"
    assert item["assets"][0]["url"] == f"https://api.example.dev/ota/d/{token}/IntelliStock.ipa"
    res = client.get(f"/ota/d/{token}/IntelliStock.ipa")
    assert res.status_code == 200 and res.content == data
    for bad in (f"/ota/d/{token}/Other.ipa", "/ota/m/nope.plist", "/ota/d/x/IntelliStock.ipa",
                f"/ota/m/{token[:-1]}0.plist"):
        assert client.get(bad, headers=HTTPS).status_code == 403, bad
    # A manifest asked for over plain http would point iOS at an http package.
    assert client.get(f"/ota/m/{token}.plist").status_code == 403


def test_the_ota_links_need_no_session_but_the_api_does():
    from fastapi.testclient import TestClient

    from api import main

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        anon = TestClient(main.app)
        _upload(11)
        token = au.make_token("ios", 11)
        assert anon.get(f"/ota/d/{token}/IntelliStock.ipa").status_code == 200
        bad = {"Authorization": "Bearer not-a-real-token"}
        assert anon.get("/app-updates/latest", headers=bad).status_code == 401
        assert anon.get("/app-updates/install-status?token=x", headers=bad).status_code == 401
        assert anon.post("/app-updates/upload?build=12", content=b"x", headers=bad).status_code == 401
        assert anon.post("/app-updates/rollback", headers=bad).status_code == 401
        assert au.current("ios")["build"] == 11
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)


def test_install_status_turns_on_once_ios_fetches_the_package(client):
    _post_upload(client, 9)
    token = au.make_token("ios", 9)

    def started(t):
        return client.get(f"/app-updates/install-status?token={t}").json()["started"]

    client.get(f"/ota/m/{token}.plist", headers=HTTPS)   # iOS reads the manifest before its confirm
    assert started(token) is False
    client.get(f"/ota/d/{token}/IntelliStock.ipa")       # ...and the package only after Install
    assert started(token) is True
    assert started("other") is False
    client.get("/ota/d/forged/IntelliStock.ipa")
    assert started("forged") is False


def test_rollback_over_http(client):
    assert client.post("/app-updates/rollback?platform=ios").status_code == 404
    _post_upload(client, 100)
    _post_upload(client, 200)
    res = client.post("/app-updates/rollback?platform=ios")
    assert res.status_code == 200 and res.json()["build"]["build"] == 100
    assert client.get("/app-updates/latest?build=50").json()["build"] == 100

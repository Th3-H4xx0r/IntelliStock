"""The Kalshi crest/flag image proxy + URL rewriting. Verifies the rewrite routes
crests through /kalshi/img (so the client never hits a CDN directly — works abroad),
never double-wraps an already-proxied URL, leaves non-logo / None fields alone, and
that the public base URL honors X-Forwarded-Proto (https behind a TLS-terminating
nginx — otherwise mixed-content blocks every logo).
"""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_proxy_logos_rewrites_crest_fields():
    from api.main import _proxy_logos
    obj = {"pick_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/660.png",
           "home_logo": "https://flagcdn.com/w80/br.png",
           "away_logo": "https://flagcdn.com/w80/ar.png",
           "match": "Brazil vs Argentina"}
    out = _proxy_logos(obj, "https://api.intellistock.app/")
    assert out["pick_logo"].startswith("https://api.intellistock.app/kalshi/img?u=")
    assert out["home_logo"].startswith("https://api.intellistock.app/kalshi/img?u=")
    # The original CDN URL is URL-encoded into the ?u= param.
    assert "a.espncdn.com" in out["pick_logo"]  # quote() keeps the host readable
    assert "%2F" in out["pick_logo"]  # the slashes are encoded
    # Non-logo fields are untouched.
    assert out["match"] == "Brazil vs Argentina"


def test_proxy_logos_does_not_double_wrap():
    from api.main import _proxy_logos
    obj = {"pick_logo": "https://flagcdn.com/w80/br.png"}
    once = _proxy_logos(dict(obj), "https://api.intellistock.app/")
    twice = _proxy_logos(dict(once), "https://api.intellistock.app/")
    # Re-running the rewrite must NOT wrap the already-proxied URL again (that would
    # point the proxy at its own host, which isn't in the allowlist -> 400).
    assert twice["pick_logo"] == once["pick_logo"]
    assert twice["pick_logo"].count("/kalshi/img?u=") == 1


def test_proxy_logos_skips_none_and_recurses_lists():
    from api.main import _proxy_logos
    obj = {"positions": [
        {"pick_logo": None, "match": "A vs B"},
        {"pick_logo": "https://flagcdn.com/w80/de.png", "match": "C vs D"},
        {"pick_logo": ""},
    ]}
    out = _proxy_logos(obj, "https://x/")
    assert out["positions"][0]["pick_logo"] is None       # None left alone
    assert out["positions"][2]["pick_logo"] == ""          # empty left alone
    assert out["positions"][1]["pick_logo"].startswith("https://x/kalshi/img?u=")


def test_public_base_url_upgrades_scheme_behind_proxy():
    from api.main import _public_base_url

    class _Req:
        def __init__(self, base, fwd):
            self.base_url = base
            self.headers = {"x-forwarded-proto": fwd} if fwd else {}

    # uvicorn behind TLS-terminating nginx reports http:// but the client is https.
    assert _public_base_url(_Req("http://api.host/", "https")) == "https://api.host/"
    # Already https / no forwarded header -> unchanged.
    assert _public_base_url(_Req("https://api.host/", None)) == "https://api.host/"
    assert _public_base_url(_Req("http://localhost:8000/", None)) == "http://localhost:8000/"
    # Comma-joined forwarded chain takes the first hop.
    assert _public_base_url(_Req("http://api.host/", "https,http")) == "https://api.host/"

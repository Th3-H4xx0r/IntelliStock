"""
Robinhood API engine:
- Public market data: live and historical price retrieval (no auth).
- Experimental authenticated client: account/portfolio/positions/orders (used by robinhood_cli.py).
- Live prices: batch instruments + batch quotes.
- Price history: GET /quotes/historicals/$symbol/ or /quotes/historicals/?symbols=csv (up to 75).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import random
import time
import uuid
from typing import Any

import requests
import pandas as pd

ROBINHOOD_INSTRUMENTS = "https://api.robinhood.com/instruments/"
ROBINHOOD_QUOTES = "https://api.robinhood.com/marketdata/quotes/"
# GET /quotes/historicals/$symbol/ or /quotes/historicals/?symbols=csv (up to 75 symbols)
ROBINHOOD_QUOTES_HISTORICALS = "https://api.robinhood.com/quotes/historicals/"

# --- Authenticated (private) API base ---
ROBINHOOD_BASE = "https://api.robinhood.com"
ROBINHOOD_OAUTH2_TOKEN = f"{ROBINHOOD_BASE}/oauth2/token/"
ROBINHOOD_OAUTH2_REVOKE = f"{ROBINHOOD_BASE}/oauth2/revoke_token/"
ROBINHOOD_CHALLENGE = f"{ROBINHOOD_BASE}/challenge/"
ROBINHOOD_ORDERS = f"{ROBINHOOD_BASE}/orders/"
ROBINHOOD_ACCOUNTS = f"{ROBINHOOD_BASE}/accounts/"
ROBINHOOD_ACCOUNT_SWITCHER = "https://bonfire.robinhood.com/home/account_switcher/v2"
ROBINHOOD_PORTFOLIOS = f"{ROBINHOOD_BASE}/portfolios/"
ROBINHOOD_POSITIONS = f"{ROBINHOOD_BASE}/positions/"
OAUTH_CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"

BATCH_SIZE = 50
HISTORICALS_BATCH_SIZE = 75  # up to 75 symbols per batch for historicals
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

AUTH_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Robinhood-API-Version": "1.0.0",
    # Origin/Referer can help Robinhood treat the client more like the web app.
    "Origin": "https://robinhood.com",
    "Referer": "https://robinhood.com/",
}


class RobinhoodAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: str | None = None, response_json: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.response_json = response_json or {}


class RobinhoodMFARequired(RobinhoodAPIError):
    def __init__(
        self,
        message: str = "Robinhood MFA required.",
        *,
        status_code: int | None = None,
        detail: str | None = None,
        response_json: dict | None = None,
        mfa_type: str | None = None,
    ):
        super().__init__(message, status_code=status_code, detail=detail, response_json=response_json)
        self.mfa_type = mfa_type


class RobinhoodChallengeRequired(RobinhoodAPIError):
    def __init__(
        self,
        message: str = "Robinhood challenge required.",
        *,
        status_code: int | None = None,
        detail: str | None = None,
        response_json: dict | None = None,
        challenge_id: str | None = None,
        challenge_type: str | None = None,
    ):
        super().__init__(message, status_code=status_code, detail=detail, response_json=response_json)
        self.challenge_id = challenge_id
        self.challenge_type = challenge_type


# Phase A (2026-04-29) — semantic error sub-classes the adapter layer can map
# to the BrokerAdapter taxonomy in broker_adapters/errors.py without string
# matching. Mirrors AlpacaAdapter._parse_error semantics.
class RobinhoodRateLimited(RobinhoodAPIError):
    def __init__(
        self,
        message: str = "Robinhood rate-limited.",
        *,
        status_code: int | None = None,
        detail: str | None = None,
        response_json: dict | None = None,
        retry_after_sec: float | None = None,
    ):
        super().__init__(message, status_code=status_code, detail=detail, response_json=response_json)
        self.retry_after_sec = retry_after_sec


class RobinhoodInsufficientBuyingPower(RobinhoodAPIError):
    pass


class RobinhoodFractionalNotAllowed(RobinhoodAPIError):
    pass


class RobinhoodPDTRestricted(RobinhoodAPIError):
    pass


class RobinhoodWashSale(RobinhoodAPIError):
    pass


class RobinhoodAccountRestricted(RobinhoodAPIError):
    pass


class RobinhoodMarketClosed(RobinhoodAPIError):
    pass


def _classify_robinhood_error(
    *, status_code: int | None, detail: str | None, payload: dict | None,
    retry_after_sec: float | None = None,
) -> RobinhoodAPIError:
    """Map a raw RH error response to a typed sub-class so the adapter
    layer can isinstance() check instead of string-matching the detail.
    """
    sc = int(status_code or 0)
    det_lc = (detail or "").lower()
    body_str = ""
    if isinstance(payload, dict):
        try:
            body_str = " ".join(str(v).lower() for v in payload.values() if isinstance(v, (str, int, float)))
        except Exception:
            body_str = ""
    blob = f"{det_lc} {body_str}"

    if sc == 429:
        return RobinhoodRateLimited(
            "Robinhood rate-limited.",
            status_code=sc, detail=detail, response_json=payload or {},
            retry_after_sec=retry_after_sec,
        )
    # 2026-04-30 MED #14: detect MFA/challenge required mid-session (non-login
    # path). Without this, RH demanding re-challenge on /orders/ surfaces as
    # a generic RobinhoodAPIError → mapped to BrokerError → silent order
    # failure with no operator alert.
    _mfa_required = False
    if isinstance(payload, dict):
        if payload.get("mfa_required") is True or str(payload.get("mfa_required") or "").lower() == "true":
            _mfa_required = True
        if payload.get("mfa_type") and not _mfa_required:
            _mfa_required = True
    if _mfa_required or "mfa required" in blob or "mfa_required" in blob:
        return RobinhoodMFARequired(
            "Robinhood MFA required mid-session.",
            status_code=sc, detail=detail, response_json=payload or {},
            mfa_type=(payload or {}).get("mfa_type") if isinstance(payload, dict) else None,
        )
    _challenge_id = None
    _challenge_kind = None
    if isinstance(payload, dict):
        ch = payload.get("challenge")
        if isinstance(ch, dict):
            _challenge_id = ch.get("id")
            _challenge_kind = ch.get("type") or ch.get("challenge_type")
    if _challenge_id or "challenge required" in blob or "challenge_required" in blob:
        return RobinhoodChallengeRequired(
            "Robinhood challenge required mid-session.",
            status_code=sc, detail=detail, response_json=payload or {},
            challenge_id=_challenge_id, challenge_type=_challenge_kind,
        )
    # Common 401 detail-string for re-auth on a non-login request.
    if sc == 401 and (
        "authentication credentials were not provided" in blob
        or "invalid token" in blob
        or "token has expired" in blob
        or "expired token" in blob
    ):
        return RobinhoodMFARequired(
            "Robinhood session no longer authenticated; re-link required.",
            status_code=sc, detail=detail, response_json=payload or {},
            mfa_type=None,
        )
    if "insufficient" in blob and ("buying" in blob or "fund" in blob):
        return RobinhoodInsufficientBuyingPower(
            "Insufficient buying power.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    # Bug-check fix #7: tighten PDT match. The previous regex over-matched
    # advisory "day trade limit will be reached" warnings RH returns in 200 OK
    # responses. Only classify as PDT-restricted when the message clearly says
    # the limit was REACHED/EXCEEDED, or contains the specific PDT terminology.
    if (
        "pattern day trader" in blob
        or "pdt" in blob
        or ("day trade" in blob and ("limit reached" in blob or "exceeded" in blob or "exceed" in blob))
    ):
        return RobinhoodPDTRestricted(
            "PDT restriction.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    if ("fractional" in blob) and ("not" in blob or "unsupported" in blob or "supported" in blob or "allowed" in blob):
        return RobinhoodFractionalNotAllowed(
            "Fractional shares not allowed for this instrument/order.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    if "wash sale" in blob:
        return RobinhoodWashSale(
            "Wash sale advisory.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    if any(t in blob for t in ("account restricted", "account locked", "account is locked", "account_blocked", "deactivated", "trading halted on this account")):
        return RobinhoodAccountRestricted(
            "Account restricted/locked.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    if "market is closed" in blob or "market closed" in blob or "outside market hours" in blob:
        return RobinhoodMarketClosed(
            "Market closed.",
            status_code=sc, detail=detail, response_json=payload or {},
        )
    # Include `detail` in the user-facing message so logs surface the
    # actual rejection reason (e.g. "Insufficient buying power",
    # "Fractional shares not supported for this symbol", etc.) instead
    # of just "HTTP 400" — operator can act without code archaeology.
    _msg = f"Robinhood API error (HTTP {sc or '?'})"
    if detail:
        _msg += f": {str(detail)[:300]}"
    elif payload:
        _msg += f": payload={str(payload)[:300]}"
    _msg += "."
    return RobinhoodAPIError(
        _msg,
        status_code=sc or None, detail=detail, response_json=payload or {},
    )


@dataclass
class RobinhoodSessionState:
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int | None = None
    obtained_at_epoch: int | None = None
    device_token: str | None = None
    account_number: str | None = None
    account_url: str | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "RobinhoodSessionState":
        d = d or {}
        return cls(
            access_token=d.get("access_token"),
            refresh_token=d.get("refresh_token"),
            token_type=d.get("token_type") or "Bearer",
            expires_in=d.get("expires_in"),
            obtained_at_epoch=d.get("obtained_at_epoch"),
            device_token=d.get("device_token"),
            account_number=d.get("account_number"),
            account_url=d.get("account_url"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class RobinhoodClient:
    """
    Experimental authenticated Robinhood client (private/undocumented endpoints).

    This is intended for local testing only. Robinhood may change or block these
    flows; do not rely on it for production trading systems.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        state: RobinhoodSessionState | None = None,
        timeout_sec: int = 20,
        debug_http: bool = False,
    ):
        self.session = session or requests.Session()
        self.session.headers.update(AUTH_DEFAULT_HEADERS)
        # 2026-05-07 connection-pool poisoning defense: force
        # `Connection: close` on every request so urllib3's keep-alive
        # pool can NEVER hand a wedged TCP/TLS connection from one
        # thread to another. Cost: ~50ms TLS handshake per call.
        # Benefit: a stuck `get_order` in the polling thread cannot
        # poison the strategy thread's `refresh_account` call. Skip
        # only if the caller explicitly opts out via env (for tests
        # where keep-alive amortization matters more than safety).
        if os.environ.get("RH_DISABLE_KEEP_ALIVE", "true").strip().lower() in ("1", "true", "yes"):
            self.session.headers["Connection"] = "close"
        self.state = state or RobinhoodSessionState()
        self.timeout_sec = int(timeout_sec or 20)
        self.debug_http = bool(debug_http)
        # 2026-05-05 live-hang investigation: defense-in-depth. Most RH
        # call sites pass timeout_sec, but the snapshot worker shares this
        # session via the broker adapter and a forgotten timeout on any
        # call site would wedge the live tick. Inject a 30s default
        # timeout when none is supplied (caller-provided timeout wins).
        try:
            if not getattr(self.session, "_intellistock_timeout_patched", False):
                _orig_request = self.session.request
                # 2026-05-05 bug-sweep finding (HIGH): the prior `if not args and
                # kwargs.get("timeout") is None` guard was inert because the main
                # `_request_json_once` path always passes timeout as a kwarg with
                # a non-None integer. Use setdefault unconditionally so the patch
                # provides defense-in-depth for any future caller AND covers the
                # positional-timeout case (8th positional arg in Session.request).
                def _request_with_timeout(method, url, *args, **kwargs):
                    # 8th positional arg is `timeout` per requests.Session.request
                    # signature; if caller passed it positionally, leave it alone.
                    if len(args) < 7:
                        kwargs.setdefault("timeout", 30)
                    return _orig_request(method, url, *args, **kwargs)
                self.session.request = _request_with_timeout
                self.session._intellistock_timeout_patched = True
        except Exception:
            pass

        if not self.state.device_token:
            self.state.device_token = self.generate_device_token()

    # --- Core helpers ---

    @staticmethod
    def generate_device_token() -> str:
        return str(uuid.uuid4())

    def _auth_headers(self) -> dict[str, str]:
        if not self.state.access_token:
            return {}
        token_type = (self.state.token_type or "Bearer").strip()
        return {"Authorization": f"{token_type} {self.state.access_token}".strip()}

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        data: dict | None = None,
        extra_headers: dict | None = None,
        timeout_sec: int | None = None,
        retry: bool = False,
        max_attempts: int = 3,
    ) -> dict:
        """HTTP request with optional retry on 429/5xx and semantic error mapping.

        Phase A (2026-04-29): when retry=True, transient 429 / 5xx errors get
        exponential backoff + jitter (max 3 attempts). Retry-After header is
        honored when present. Auth flows (login/refresh/logout) keep retry=False
        so failed login attempts surface immediately.
        """
        if not retry:
            return self._request_json_once(
                method, url,
                params=params, json_body=json_body, data=data,
                extra_headers=extra_headers, timeout_sec=timeout_sec,
            )

        last_exc: RobinhoodAPIError | None = None
        for attempt in range(max(1, int(max_attempts))):
            try:
                return self._request_json_once(
                    method, url,
                    params=params, json_body=json_body, data=data,
                    extra_headers=extra_headers, timeout_sec=timeout_sec,
                )
            except RobinhoodRateLimited as e:
                last_exc = e
                # Bug-check round 2 (codex SHOULD-FIX #9): honor server-
                # provided Retry-After up to 5 minutes; previously capped
                # at 60s which fired retries early on a 90s+ limit and
                # promptly hit it again. If RH says wait > 5 min, surface
                # the typed error so the caller decides (can re-poll later).
                wait = e.retry_after_sec if (e.retry_after_sec and e.retry_after_sec > 0) else (2.0 ** attempt)
                wait_capped = min(float(wait), 300.0) + random.uniform(0.0, 0.5)
                if float(wait or 0) > 300.0:
                    # Don't sleep 5+ minutes inside a request; surface so the
                    # caller's higher-level retry / backoff can handle.
                    raise
                if attempt < max_attempts - 1:
                    time.sleep(wait_capped)
                    continue
                raise
            except RobinhoodAPIError as e:
                last_exc = e
                sc = int(e.status_code or 0)
                if 500 <= sc < 600 and attempt < max_attempts - 1:
                    time.sleep((2.0 ** attempt) + random.uniform(0.0, 0.5))
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        return {}

    def _request_json_once(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        data: dict | None = None,
        extra_headers: dict | None = None,
        timeout_sec: int | None = None,
    ) -> dict:
        headers = {}
        headers.update(extra_headers or {})
        auth_headers = self._auth_headers()
        if auth_headers:
            headers.update(auth_headers)

        timeout = self.timeout_sec if timeout_sec is None else int(timeout_sec)
        try:
            resp = self.session.request(
                method.upper(),
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers if headers else None,
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise RobinhoodAPIError(f"Request failed: {e}") from e

        if self.debug_http:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                path = parsed.path or url
            except Exception:
                path = url
            print(f"[HTTP] {method.upper()} {path} -> {resp.status_code}")

        try:
            payload = resp.json()
        except Exception:
            payload = {}

        if not resp.ok:
            detail = None
            if isinstance(payload, dict):
                detail = (
                    payload.get("detail")
                    or payload.get("message")
                    or payload.get("error_description")
                    or payload.get("error")
                )
                if not detail:
                    nfe = payload.get("non_field_errors")
                    if isinstance(nfe, list) and nfe:
                        detail = "; ".join(str(x) for x in nfe if x is not None)
                    elif isinstance(nfe, str) and nfe.strip():
                        detail = nfe.strip()
                if not detail:
                    # Try to extract field-level error lists: {"field": ["msg", ...], ...}
                    field_msgs: list[str] = []
                    for k, v in payload.items():
                        if k in ("detail", "message", "error_description", "error", "non_field_errors"):
                            continue
                        if isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v):
                            msg = ", ".join(str(x) for x in v[:3])
                            field_msgs.append(f"{k}: {msg}")
                    if field_msgs:
                        detail = "; ".join(field_msgs[:5])
            if not detail:
                detail = (resp.text or "").strip() or None

            # Phase A (2026-04-29): parse Retry-After + classify into typed
            # sub-class so the adapter doesn't have to string-match `detail`.
            retry_after_sec: float | None = None
            try:
                _ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                if _ra is not None:
                    retry_after_sec = float(_ra)
            except Exception:
                retry_after_sec = None
            raise _classify_robinhood_error(
                status_code=resp.status_code,
                detail=detail,
                payload=payload if isinstance(payload, dict) else {},
                retry_after_sec=retry_after_sec,
            )

        return payload if isinstance(payload, dict) else {}

    def _token_request(
        self,
        payload: dict,
        *,
        challenge_response_id: str | None = None,
        use_json: bool = True,
    ) -> tuple[int, dict]:
        extra_headers: dict[str, str] = {}
        if challenge_response_id:
            extra_headers["X-ROBINHOOD-CHALLENGE-RESPONSE-ID"] = str(challenge_response_id)

        # Robinhood commonly expects form-encoded; we try JSON first (web-like), then fallback.
        if use_json:
            try:
                res = self._request_json(
                    "POST",
                    ROBINHOOD_OAUTH2_TOKEN,
                    json_body=payload,
                    extra_headers=extra_headers,
                    timeout_sec=20,
                )
                return (200, res)
            except RobinhoodAPIError as e:
                # Preserve structured body if present; caller will inspect for challenge/mfa.
                status = e.status_code or 0
                body = e.response_json or {}
                return (status, body)

        # Form-encoded fallback.
        headers = dict(extra_headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        try:
            res = self._request_json(
                "POST",
                ROBINHOOD_OAUTH2_TOKEN,
                data=payload,
                extra_headers=headers,
                timeout_sec=20,
            )
            return (200, res)
        except RobinhoodAPIError as e:
            return (e.status_code or 0, e.response_json or {})

    def _expires_in_request(self) -> int:
        """
        Best-effort expires_in to request for OAuth token/refresh.
        Robinhood may ignore or cap this value.
        """
        try:
            v = int(self.state.expires_in) if self.state.expires_in is not None else 86400
        except Exception:
            v = 86400
        if v <= 0:
            v = 86400
        # Avoid requesting extreme values; keep within 60 days.
        max_v = 60 * 24 * 60 * 60
        if v > max_v:
            v = max_v
        return v

    # --- Auth ---

    def login_password(
        self,
        *,
        username: str,
        password: str,
        challenge_type: str = "sms",
        mfa_code: str | None = None,
        challenge_response_id: str | None = None,
        force_form_encoded_fallback: bool = True,
    ) -> RobinhoodSessionState:
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            raise ValueError("username and password are required")

        if not self.state.device_token:
            self.state.device_token = self.generate_device_token()

        payload: dict[str, Any] = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": "internal",
            "client_id": OAUTH_CLIENT_ID,
            "device_token": self.state.device_token,
            "expires_in": self._expires_in_request(),
            "challenge_type": (challenge_type or "sms"),
        }
        if mfa_code:
            payload["mfa_code"] = str(mfa_code).strip()

        status, res = self._token_request(payload, challenge_response_id=challenge_response_id, use_json=True)
        # If JSON attempt didn't yield a clear auth response, try form-encoded.
        if force_form_encoded_fallback and not (res.get("access_token") or res.get("mfa_required") or res.get("challenge")):
            status2, res2 = self._token_request(payload, challenge_response_id=challenge_response_id, use_json=False)
            # Prefer the second attempt if it looks more informative.
            if res2.get("access_token") or res2.get("mfa_required") or res2.get("challenge") or res2.get("detail"):
                status, res = status2, res2

        # Success
        access_token = res.get("access_token")
        if access_token:
            self.state.access_token = str(access_token)
            self.state.refresh_token = res.get("refresh_token") or self.state.refresh_token
            self.state.token_type = res.get("token_type") or self.state.token_type or "Bearer"
            try:
                self.state.expires_in = int(res.get("expires_in")) if res.get("expires_in") is not None else self.state.expires_in
            except Exception:
                pass
            self.state.obtained_at_epoch = int(time.time())
            return self.state

        # MFA required
        if res.get("mfa_required") is True or res.get("mfa_required") == "true":
            raise RobinhoodMFARequired(
                status_code=status or None,
                detail=res.get("detail") or res.get("error_description"),
                response_json=res,
                mfa_type=res.get("mfa_type"),
            )

        # Challenge required
        challenge = res.get("challenge") if isinstance(res, dict) else None
        challenge_id = None
        challenge_kind = None
        if isinstance(challenge, dict):
            challenge_id = challenge.get("id")
            challenge_kind = challenge.get("type") or challenge.get("challenge_type")
        # Some responses use "detail" text to indicate a challenge.
        detail = res.get("detail") or res.get("error_description") or res.get("message")
        if challenge_id or (isinstance(detail, str) and "challenge" in detail.lower()):
            raise RobinhoodChallengeRequired(
                status_code=status or None,
                detail=detail,
                response_json=res,
                challenge_id=challenge_id,
                challenge_type=challenge_kind,
            )

        raise RobinhoodAPIError(
            "Login failed.",
            status_code=status or None,
            detail=detail,
            response_json=res,
        )

    def respond_to_challenge(self, challenge_id: str, response_code: str) -> str:
        challenge_id = (challenge_id or "").strip()
        response_code = (response_code or "").strip()
        if not challenge_id or not response_code:
            raise ValueError("challenge_id and response_code are required")

        url = f"{ROBINHOOD_CHALLENGE}{challenge_id}/respond/"
        # Try JSON then form.
        try:
            res = self._request_json("POST", url, json_body={"response": response_code}, timeout_sec=20)
        except RobinhoodAPIError:
            headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
            res = self._request_json("POST", url, data={"response": response_code}, extra_headers=headers, timeout_sec=20)

        # Prefer returned response id (if any) for X-ROBINHOOD-CHALLENGE-RESPONSE-ID.
        response_id = res.get("id") or challenge_id
        return str(response_id)

    def poll_challenge_validated(self, challenge_id: str, *, timeout_sec: int = 120, interval_sec: int = 3) -> bool:
        challenge_id = (challenge_id or "").strip()
        if not challenge_id:
            return False
        end = time.time() + max(1, int(timeout_sec))
        url = f"{ROBINHOOD_CHALLENGE}{challenge_id}/"
        while time.time() < end:
            try:
                res = self._request_json("GET", url, timeout_sec=20)
                status = (res.get("status") or "").strip().lower()
                if status == "validated":
                    return True
            except Exception:
                # Challenge GET may not be supported; keep polling briefly then give up.
                pass
            time.sleep(max(1, int(interval_sec)))
        return False

    def refresh(self) -> RobinhoodSessionState:
        if not self.state.refresh_token:
            raise ValueError("refresh_token missing; cannot refresh")
        payload: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": self.state.refresh_token,
            "client_id": OAUTH_CLIENT_ID,
            "device_token": self.state.device_token,
            "scope": "internal",
            "expires_in": self._expires_in_request(),
        }

        # Refresh endpoint usually expects form-encoded.
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
        res = self._request_json("POST", ROBINHOOD_OAUTH2_TOKEN, data=payload, extra_headers=headers, timeout_sec=20)
        access_token = res.get("access_token")
        if not access_token:
            raise RobinhoodAPIError("Refresh failed.", detail=res.get("detail"), response_json=res)
        self.state.access_token = str(access_token)
        self.state.refresh_token = res.get("refresh_token") or self.state.refresh_token
        self.state.token_type = res.get("token_type") or self.state.token_type or "Bearer"
        try:
            self.state.expires_in = int(res.get("expires_in")) if res.get("expires_in") is not None else self.state.expires_in
        except Exception:
            pass
        self.state.obtained_at_epoch = int(time.time())
        return self.state

    def logout(self) -> None:
        if not self.state.refresh_token:
            self.state.access_token = None
            return
        payload = {"client_id": OAUTH_CLIENT_ID, "token": self.state.refresh_token}
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
        try:
            self._request_json("POST", ROBINHOOD_OAUTH2_REVOKE, data=payload, extra_headers=headers, timeout_sec=20)
        finally:
            self.state.access_token = None
            self.state.refresh_token = None

    # --- Account / portfolio / positions ---

    def get_accounts(self) -> list[dict]:
        res = self._request_json(
            "GET",
            ROBINHOOD_ACCOUNTS,
            params={"default_to_all_accounts": "true"},
            timeout_sec=20,
        )
        results = list(res.get("results") or [])
        # Follow pagination to collect all accounts
        next_url = res.get("next")
        while next_url:
            try:
                page = self._request_json("GET", next_url, timeout_sec=20)
                results.extend(page.get("results") or [])
                next_url = page.get("next")
            except Exception:
                break

        active = [a for a in results if isinstance(a, dict) and not a.get("deactivated")]
        known_numbers = {(a.get("account_number") or "").strip() for a in active}

        # Supplement with account_switcher/v2 which reliably returns all accounts
        # including newer ones that may not appear in the standard /accounts/ response.
        switcher_meta: dict[str, dict] = {}  # account_number -> {display_name, ...}
        try:
            sw = self._request_json("GET", ROBINHOOD_ACCOUNT_SWITCHER, timeout_sec=20)
            for entry in (sw.get("accounts") or []):
                num = (entry.get("identifier") or "").strip()
                if not num:
                    continue
                switcher_meta[num] = {
                    "display_name":    entry.get("expanded_primary_text") or entry.get("primary_text") or "",
                    "secondary_text":  entry.get("secondary_text") or "",
                    "management_type": entry.get("management_type") or "self_directed",
                }
                if num not in known_numbers:
                    # Try to fetch the full account object individually
                    try:
                        acct = self._request_json(
                            "GET",
                            f"{ROBINHOOD_ACCOUNTS}{num}/",
                            params={"default_to_all_accounts": "true"},
                            timeout_sec=20,
                        )
                        if isinstance(acct, dict) and not acct.get("deactivated"):
                            active.append(acct)
                            known_numbers.add(num)
                    except Exception:
                        # Add a minimal stub so the account still shows in the list
                        active.append({"account_number": num, "type": "individual"})
                        known_numbers.add(num)
        except Exception:
            pass

        # Attach display_name and management_type from switcher onto every account dict
        for a in active:
            num = (a.get("account_number") or "").strip()
            if num in switcher_meta:
                meta = switcher_meta[num]
                if meta.get("display_name"):
                    a.setdefault("display_name", meta["display_name"])
                a.setdefault("management_type", meta.get("management_type") or "self_directed")

        return active

    def select_default_account(self, *, preferred_account_number: str | None = None) -> dict:
        accounts = self.get_accounts()
        if not accounts:
            raise RobinhoodAPIError("No Robinhood accounts found.")

        if preferred_account_number:
            pref = preferred_account_number.strip()
            for a in accounts:
                if (a.get("account_number") or "").strip() == pref:
                    self.state.account_number = a.get("account_number")
                    self.state.account_url = a.get("url")
                    return a

        a0 = accounts[0]
        self.state.account_number = a0.get("account_number")
        self.state.account_url = a0.get("url")
        return a0

    def get_portfolio(self, account_number: str | None = None) -> dict:
        acct_num = (account_number or self.state.account_number or "").strip()
        # If account is known, portfolios/{account_number}/ tends to work.
        if acct_num:
            try:
                return self._request_json("GET", f"{ROBINHOOD_PORTFOLIOS}{acct_num}/", timeout_sec=20)
            except Exception:
                pass
        # Fallback to listing and selecting the first.
        res = self._request_json("GET", ROBINHOOD_PORTFOLIOS, timeout_sec=20)
        results = res.get("results") or []
        if results and isinstance(results[0], dict):
            return results[0]
        return {}

    def get_positions(self, *, account_number: str | None = None, nonzero: bool = True) -> list[dict]:
        params: dict[str, str] = {}
        if nonzero:
            params["nonzero"] = "true"
        acct_num = (account_number or self.state.account_number or "").strip()
        if acct_num:
            params["account_number"] = acct_num
        res = self._request_json("GET", ROBINHOOD_POSITIONS, params=params or None, timeout_sec=20)
        results = res.get("results") or []
        return [p for p in results if isinstance(p, dict)]

    def get_instrument(self, instrument_url: str) -> dict:
        instrument_url = (instrument_url or "").strip()
        if not instrument_url:
            return {}
        return self._request_json("GET", instrument_url, timeout_sec=20)

    def find_instrument_url_by_symbol(self, symbol: str) -> str | None:
        # 2026-04-30 MED #23 — RH instruments endpoint accepts dash-form
        # tickers ("BRK-A", "BF-B") but rejects the dot-form ("BRK.A").
        # Normalize before any HTTP call so two-class share tickers work.
        sym = (symbol or "").strip().upper().replace(".", "-")
        if not sym:
            return None
        # Try direct symbol lookup.
        try:
            res = self._request_json("GET", ROBINHOOD_INSTRUMENTS, params={"symbol": sym}, timeout_sec=20)
            results = res.get("results") or []
            if results and isinstance(results[0], dict) and results[0].get("url"):
                return results[0]["url"]
        except Exception:
            pass
        # Fallback to query search.
        try:
            res = self._request_json("GET", ROBINHOOD_INSTRUMENTS, params={"query": sym}, timeout_sec=20)
            results = res.get("results") or []
            if results and isinstance(results[0], dict) and results[0].get("url"):
                return results[0]["url"]
        except Exception:
            pass
        return None

    # --- Orders ---

    def list_orders(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        account_number: str | None = None,
    ) -> list[dict]:
        """List orders. Phase A: optional state filter (client-side because
        RH's server-side `?state=` is inconsistent across API versions).

        state values:
          - 'open' / None-ish-open  → queued + confirmed + partially_filled + unconfirmed
          - 'queued' / 'confirmed' / 'partially_filled' / 'filled' / 'rejected' / 'canceled' / 'cancelled' / 'expired' → exact match
          - None → no filter
        """
        limit = max(1, int(limit or 10))
        state_norm = (state or "").strip().lower() or None
        OPEN_STATES = {"queued", "confirmed", "partially_filled", "unconfirmed"}
        CANCELLED_ALIASES = {"canceled", "cancelled"}
        out: list[dict] = []
        url: str | None = ROBINHOOD_ORDERS
        params: dict[str, str] | None = None
        if account_number:
            params = {"account_numbers": account_number.strip()}
        while url and len(out) < limit:
            res = self._request_json(
                "GET", url,
                params=params if url == ROBINHOOD_ORDERS else None,
                timeout_sec=20, retry=True,
            )
            results = res.get("results") or []
            for o in results:
                if not isinstance(o, dict):
                    continue
                if state_norm:
                    o_state = (o.get("state") or "").strip().lower()
                    if state_norm == "open":
                        if o_state not in OPEN_STATES:
                            continue
                    elif state_norm in CANCELLED_ALIASES:
                        if o_state not in CANCELLED_ALIASES:
                            continue
                    elif o_state != state_norm:
                        continue
                out.append(o)
                if len(out) >= limit:
                    break
            url = res.get("next") if len(out) < limit else None
        return out[:limit]

    def get_order(self, order_id: str) -> dict:
        order_id = (order_id or "").strip()
        if not order_id:
            return {}
        return self._request_json(
            "GET", f"{ROBINHOOD_ORDERS}{order_id}/", timeout_sec=20, retry=True,
        )

    def find_order_by_ref_id(
        self, ref_id: str, *, raise_on_transient: bool = False,
    ) -> dict | None:
        """Look up an order by client-supplied ref_id. Returns None if not found.

        Phase A (2026-04-29): Robinhood honors `?ref_id={uuid}` as a
        server-side filter on /orders/. Used for adapter idempotency —
        before submit, check if the ref_id we're about to use already
        landed (network blip retry path).

        Bug-check round 2 (2026-04-29): `raise_on_transient=True` propagates
        429/5xx/network errors instead of swallowing as None. The pre-submit
        idempotency check passes True so a transient failure can fail-closed
        and skip the submit (strategy retries next bar with same cid →
        deterministic ref_id → next attempt's pre-check finds the order).
        Default False preserves the lenient behavior reconcile / fallback
        flows depend on.
        """
        rid = (ref_id or "").strip()
        if not rid:
            return None
        try:
            res = self._request_json(
                "GET",
                ROBINHOOD_ORDERS,
                params={"ref_id": rid},
                timeout_sec=20,
                retry=True,
            )
        except RobinhoodAPIError as e:
            sc = int(getattr(e, "status_code", 0) or 0)
            # Transient: 0 (network), 429, 5xx. 4xx other than 429 means RH
            # answered definitively — caller can treat as "not found".
            is_transient = (sc == 0) or (sc == 429) or (500 <= sc < 600)
            if raise_on_transient and is_transient:
                raise
            return None
        results = res.get("results") or []
        if results and isinstance(results[0], dict):
            return results[0]
        # Fallback: client-side scan over the most-recent N orders. Some
        # RH API versions ignore the server-side ref_id filter.
        # Bug-check fix #6: bump from 50 → 200 to cover high-volume accounts
        # where the target ref_id may be older than 50 orders.
        try:
            recent = self.list_orders(limit=200)
            for o in recent:
                if (o.get("ref_id") or "").strip().lower() == rid.lower():
                    return o
        except RobinhoodAPIError as e:
            sc = int(getattr(e, "status_code", 0) or 0)
            if raise_on_transient and ((sc == 0) or (sc == 429) or (500 <= sc < 600)):
                raise
        except Exception:
            pass
        return None

    def cancel_order(self, *, cancel_url: str | None = None, order_id: str | None = None) -> bool:
        cancel_url = (cancel_url or "").strip() or None
        order_id = (order_id or "").strip() or None
        if not cancel_url and not order_id:
            raise ValueError("cancel_url or order_id is required")
        url = cancel_url or f"{ROBINHOOD_ORDERS}{order_id}/cancel/"
        self._request_json("POST", url, timeout_sec=20, retry=True)
        return True

    def cancel_all_open_orders(self, *, account_number: str | None = None) -> int:
        """Cancel every queued/confirmed/partially_filled order. Returns count
        actually canceled. Used by kill-switch / halt path.
        """
        try:
            open_orders = self.list_orders(limit=200, state="open", account_number=account_number)
        except Exception:
            open_orders = []
        canceled = 0
        for o in open_orders:
            oid = (o.get("id") or "").strip()
            cancel_url = (o.get("cancel") or "").strip() or None
            if not oid and not cancel_url:
                continue
            try:
                self.cancel_order(order_id=oid or None, cancel_url=cancel_url)
                canceled += 1
            except RobinhoodAPIError:
                # Already terminal, race with broker — non-fatal
                continue
        return canceled

    _ORDER_TERMINAL_STATES = frozenset({"filled", "cancelled", "canceled", "rejected", "expired", "failed"})

    def poll_order_until_terminal(
        self,
        order_id: str,
        *,
        timeout_sec: int = 60,
        interval_sec: int = 2,
    ) -> dict:
        """Poll an order until it reaches a terminal state, or until timeout.

        Returns the final order dict (may still be non-terminal on timeout).
        Used by the adapter's polling fill detector substitute since
        Robinhood has no public WebSocket for order updates.
        """
        end = time.time() + max(1, int(timeout_sec))
        last: dict = {}
        while time.time() < end:
            try:
                last = self.get_order(order_id) or {}
            except RobinhoodAPIError:
                time.sleep(max(1, int(interval_sec)))
                continue
            state = (last.get("state") or "").strip().lower()
            if state in self._ORDER_TERMINAL_STATES:
                return last
            time.sleep(max(1, int(interval_sec)))
        return last

    def get_account_summary(self, account_number: str | None = None) -> dict:
        """Normalized account summary with PDT + buying-power + restriction
        fields the adapter's refresh_account / refresh_cash need.

        Returns flat dict with: equity, buying_power, cash, unsettled_funds,
        pattern_day_trader, day_trade_count, day_trade_buying_power,
        account_blocked, trading_blocked, state, account_number, account_url.
        """
        accounts = self.get_accounts()
        if not accounts:
            return {}
        acct = None
        if account_number:
            target = account_number.strip()
            for a in accounts:
                if (a.get("account_number") or "").strip() == target:
                    acct = a
                    break
        if acct is None:
            acct = accounts[0]

        portfolio: dict = {}
        try:
            portfolio = self.get_portfolio(account_number=acct.get("account_number"))
        except Exception:
            portfolio = {}

        def _f(v, default: float = 0.0) -> float:
            try:
                return float(v) if v is not None else default
            except Exception:
                return default

        def _i(v, default: int = 0) -> int:
            try:
                return int(float(v)) if v is not None else default
            except Exception:
                return default

        equity = (
            _f(portfolio.get("equity"))
            or _f(portfolio.get("market_value"))
            or _f(acct.get("portfolio_value"))
            or _f(acct.get("equity"))
        )
        # Robinhood stores buying_power on the account row; some
        # cash-account variants only expose `cash_available_for_withdrawal`.
        buying_power = _f(acct.get("buying_power")) or _f(acct.get("cash_available_for_withdrawal"))
        cash = _f(acct.get("cash"))
        unsettled = _f(acct.get("unsettled_funds"))
        state = (acct.get("state") or "").strip().lower()
        account_blocked = bool(acct.get("deactivated")) or state in {"restricted", "locked", "deactivated"}
        trading_blocked = bool(acct.get("only_position_closing_trades"))

        return {
            "account_number": acct.get("account_number") or "",
            "account_url": acct.get("url") or "",
            "equity": equity,
            "buying_power": buying_power,
            "cash": cash,
            "unsettled_funds": unsettled,
            "pattern_day_trader": bool(acct.get("pattern_day_trader")),
            "day_trade_count": _i(acct.get("day_trade_count")),
            "day_trade_buying_power": _f(acct.get("day_trade_buying_power")),
            "account_blocked": account_blocked,
            "trading_blocked": trading_blocked,
            "state": state,
            # Pass-through useful raw fields for diagnostics.
            "type": (acct.get("type") or "").strip().lower(),
        }

    @staticmethod
    def _round_price_for_robinhood(value: float) -> float:
        """
        Round a price/decimal to what Robinhood commonly accepts.
        Mirrors robin_stocks' round_price behavior.
        """
        v = float(value)
        if v <= 1e-2:
            return round(v, 6)
        if v < 1.0:
            return round(v, 4)
        return round(v, 2)

    @staticmethod
    def _round_quantity_for_robinhood(value: float) -> float:
        # Robinhood fractional shares are typically supported up to 6 decimals.
        return round(float(value), 6)

    @staticmethod
    def _is_int_like(value: float, *, eps: float = 1e-9) -> bool:
        try:
            v = float(value)
        except Exception:
            return False
        return abs(v - round(v)) <= eps

    def get_latest_quote(self, symbol: str) -> dict:
        """
        Best-effort quote dict (bid/ask/last) using Robinhood public marketdata quotes.
        """
        sym = (symbol or "").strip().upper().replace(".", "-")
        if not sym:
            return {}
        pairs = instruments_batch([sym])
        if not pairs:
            return {}
        _sym, inst_id = pairs[0]
        if not inst_id:
            return {}
        quotes = quotes_batch([inst_id])
        if not quotes:
            return {}
        q = quotes[0] or {}
        return q if isinstance(q, dict) else {}

    def _price_from_quote_side(self, quote: dict, side: str) -> float | None:
        """
        Pick a reasonable price from quote for a given side.
        buy  -> prefer ask_price
        sell -> prefer bid_price
        """
        side = (side or "").strip().lower()
        preferred_key = "ask_price" if side == "buy" else "bid_price"
        raw = quote.get(preferred_key)
        try:
            if raw is not None:
                return float(raw)
        except Exception:
            pass
        # Fallback: last trade / mid
        try:
            p = price_from_quote(quote)
            return float(p) if p is not None else None
        except Exception:
            return None

    def build_order_payload_equity(
        self,
        *,
        account_url: str,
        instrument_url: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price: float | None = None,
        time_in_force: str = "gtc",
        trigger: str = "immediate",
        extended_hours: bool = False,
        market_hours: str = "regular_hours",
        ref_id: str | None = None,
    ) -> dict:
        sym = (symbol or "").strip().upper()
        side = (side or "").strip().lower()
        order_type = (order_type or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if order_type not in ("market", "limit"):
            raise ValueError("order_type must be 'market' or 'limit'")
        qty_f = float(quantity)
        if qty_f <= 0:
            raise ValueError("quantity must be positive")
        qty_f = self._round_quantity_for_robinhood(qty_f)
        is_fractional = not self._is_int_like(qty_f)

        # Normalize inputs
        time_in_force = (time_in_force or "").strip().lower() or "gtc"
        trigger = (trigger or "").strip().lower() or "immediate"
        market_hours = (market_hours or "").strip().lower() or "regular_hours"

        # Fractional equity orders: Robinhood only supports market orders, and typically only "gfd".
        # (Robinhood may still internally convert a market buy to a limit-with-collar.)
        if is_fractional:
            time_in_force = "gfd"
            extended_hours = False
            market_hours = "regular_hours"

        if market_hours not in ("regular_hours", "extended_hours", "all_day_hours"):
            raise ValueError("market_hours must be one of: regular_hours, extended_hours, all_day_hours")
        if market_hours in ("extended_hours", "all_day_hours") and is_fractional:
            raise ValueError("Fractional shares are not supported in extended/all-day hours.")

        if order_type == "limit":
            if is_fractional:
                raise ValueError("Fractional share limit orders are not supported.")
            if limit_price is None:
                raise ValueError("limit_price is required for limit orders")
            px = float(limit_price)
            if px <= 0:
                raise ValueError("limit_price must be positive")
            px = self._round_price_for_robinhood(px)
        else:
            if limit_price is not None:
                raise ValueError("limit_price is only valid for limit orders")
            px = None

        # Quote snapshot for payload (Robinhood expects these fields for many equity orders).
        quote = self.get_latest_quote(sym)
        ask = None
        bid = None
        try:
            ask = float(quote.get("ask_price")) if quote.get("ask_price") is not None else None
        except Exception:
            ask = None
        try:
            bid = float(quote.get("bid_price")) if quote.get("bid_price") is not None else None
        except Exception:
            bid = None

        if order_type == "market":
            px = self._price_from_quote_side(quote, side)
            if side == "buy" and (px is None or px <= 0):
                raise ValueError("Could not determine ask/market price for buy market order.")
            if px is not None and px > 0:
                px = self._round_price_for_robinhood(px)

        from datetime import datetime

        payload: dict[str, Any] = {
            "account": account_url,
            "instrument": instrument_url,
            "symbol": sym,
            "type": order_type,
            "time_in_force": time_in_force,
            "trigger": trigger,
            "quantity": qty_f if is_fractional else int(round(qty_f)),
            "side": side,
            # Phase A: caller-supplied ref_id for idempotency. If absent,
            # generate a fresh UUID (legacy behavior). Adapter passes a
            # deterministic UUID derived from client_order_id so a network
            # blip retry hits the same RH-side dedup record.
            "ref_id": (ref_id or str(uuid.uuid4())),
            "price": px,
            "ask_price": self._round_price_for_robinhood(ask) if ask is not None and ask > 0 else 0.00,
            "bid_price": self._round_price_for_robinhood(bid) if bid is not None and bid > 0 else 0.00,
            "bid_ask_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "market_hours": market_hours,
            "extended_hours": bool(extended_hours),
            "order_form_version": 4,
        }

        # Market order adjustments (mirror common Robinhood behavior used by robin_stocks).
        if order_type == "market":
            if market_hours == "regular_hours":
                if side == "buy":
                    payload["preset_percent_limit"] = "0.05"
                    payload["type"] = "limit"
                else:
                    # Regular-hours market sell: Robinhood often expects price omitted.
                    payload.pop("price", None)
            else:
                # Extended/all-day: Robinhood tends to require limit-type with whole shares.
                payload["type"] = "limit"
                payload["quantity"] = int(round(qty_f))

        # Remove any Nones for form-encoded fallback safety.
        for k in list(payload.keys()):
            if payload[k] is None:
                payload.pop(k, None)

        return payload

    def place_order_equity(
        self,
        *,
        account_url: str,
        instrument_url: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price: float | None = None,
        time_in_force: str = "gtc",
        extended_hours: bool = False,
        market_hours: str = "regular_hours",
        ref_id: str | None = None,
    ) -> dict:
        payload = self.build_order_payload_equity(
            account_url=account_url,
            instrument_url=instrument_url,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            time_in_force=time_in_force,
            extended_hours=extended_hours,
            market_hours=market_hours,
            ref_id=ref_id,
        )

        # Phase A: retry=True on the POST so 429 / 5xx get exponential
        # backoff. The 4xx classified errors (insufficient BP, fractional,
        # PDT, etc.) raise immediately without retry — adapter decides
        # what to do from the typed sub-class.
        try:
            return self._request_json(
                "POST", ROBINHOOD_ORDERS, json_body=payload, timeout_sec=20, retry=True,
            )
        except RobinhoodAPIError as e:
            # Fallback to form-encoded for older behaviors. ONLY retry the
            # form-encoded fallback for the same transient cases the JSON
            # path handles (the typed sub-class makes that explicit).
            if isinstance(e, (RobinhoodInsufficientBuyingPower, RobinhoodPDTRestricted,
                              RobinhoodFractionalNotAllowed, RobinhoodWashSale,
                              RobinhoodAccountRestricted, RobinhoodMarketClosed)):
                raise
            headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
            try:
                return self._request_json(
                    "POST", ROBINHOOD_ORDERS, data=payload, extra_headers=headers,
                    timeout_sec=20, retry=True,
                )
            except RobinhoodAPIError:
                raise e


def instruments_batch(symbols_chunk):
    """Fetch instrument IDs for symbols. Returns list of (symbol, instrument_id)."""
    symbols_param = ",".join(s.strip().upper().replace(".", "-") for s in symbols_chunk)
    try:
        rq = requests.get(
            f"{ROBINHOOD_INSTRUMENTS}?symbols={symbols_param}",
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        data = rq.json()
        results = data.get("results") or []
        return [(item.get("symbol", "").upper(), item["id"]) for item in results if item.get("id") and item.get("symbol")]
    except Exception:
        return []


def quotes_batch(instrument_ids):
    """Fetch quotes for instrument IDs. Returns list of quote dicts."""
    ids_param = ",".join(instrument_ids)
    try:
        rq = requests.get(
            f"{ROBINHOOD_QUOTES}?bounds=24_5&ids={ids_param}&include_bbo_source=true&include_inactive=true",
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        data = rq.json()
        return data.get("results") or []
    except Exception:
        return []


def price_from_quote(quote):
    """Extract single price from quote dict."""
    if not quote:
        return None
    last = quote.get("last_non_reg_trade_price") or quote.get("last_trade_price")
    if last is not None:
        try:
            return float(last)
        except (TypeError, ValueError):
            pass
    bid, ask = quote.get("bid_price"), quote.get("ask_price")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2
        except (TypeError, ValueError):
            pass
    return None


def get_live_prices(symbols):
    """
    Get current live price for each symbol via Robinhood API (batch).
    Returns dict: symbol -> float price (missing/failed symbols omitted).
    """
    symbols = [s.strip().upper().replace(".", "-") for s in symbols if s]
    if not symbols:
        return {}
    out = {}
    for start in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[start : start + BATCH_SIZE]
        pairs = instruments_batch(chunk)
        if not pairs:
            continue
        ids = [p[1] for p in pairs]
        quotes = quotes_batch(ids)
        for q in quotes:
            sym = (q.get("symbol") or "").upper()
            price = price_from_quote(q)
            if sym and price is not None:
                out[sym] = price
    return out


def _historicals_to_dataframe(rows):
    """Parse Robinhood historicals list into DataFrame with DatetimeIndex and OHLCV."""
    if not rows:
        return pd.DataFrame()
    records = []
    for r in rows:
        begins = r.get("begins_at")
        if not begins:
            continue
        try:
            dt = pd.to_datetime(begins).tz_localize(None)
        except Exception:
            continue
        records.append({
            "Date": dt,
            "Open": float(r.get("open_price", 0)),
            "High": float(r.get("high_price", 0)),
            "Low": float(r.get("low_price", 0)),
            "Close": float(r.get("close_price", 0)),
            "Adj Close": float(r.get("close_price", 0)),
            "Volume": int(r.get("volume", 0)),
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df = df.set_index("Date")
    df = df.sort_index()
    return df


def get_price_history(symbol, interval="day", span="year", bounds="trading", start_date=None, end_date=None):
    """
    Get historical OHLC for one symbol from Robinhood.
    GET /quotes/historicals/$symbol/?interval=$i&span=$s&bounds=$b
    interval: 'day' | 'week' | '10minute' | '5minute' | None (all)
    span: 'day' | 'week' | 'year' | '5year' | 'all'
    bounds: 'extended' | 'regular' | 'trading'
    Returns pandas DataFrame with DatetimeIndex and columns: Open, High, Low, Close, Adj Close, Volume.
    """
    sym = symbol.strip().upper().replace(".", "-")
    params = {"interval": interval, "span": span, "bounds": bounds, "start": start_date, "end": end_date}
    try:
        rq = requests.get(
            f"{ROBINHOOD_QUOTES_HISTORICALS}{sym}/",
            params=params,
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        data = rq.json()
        rows = data.get("historicals") or []
        return _historicals_to_dataframe(rows)
    except Exception:
        return pd.DataFrame()


def get_price_histories(symbols, interval="day", span="year", bounds="trading"):
    """
    Get historical OHLC for multiple symbols. Uses batch endpoint when possible (up to 75 symbols per request).
    GET /quotes/historicals/?symbols=$csv&interval=$i&span=$s&bounds=$b
    Returns dict: symbol -> DataFrame (same shape as get_price_history).
    """
    symbols = [s.strip().upper().replace(".", "-") for s in symbols if s]
    out = {}
    for start in range(0, len(symbols), HISTORICALS_BATCH_SIZE):
        chunk = symbols[start : start + HISTORICALS_BATCH_SIZE]
        symbols_param = ",".join(chunk)
        try:
            rq = requests.get(
                f"{ROBINHOOD_QUOTES_HISTORICALS}",
                params={
                    "symbols": symbols_param,
                    "interval": interval,
                    "span": span,
                    "bounds": bounds,
                },
                headers=REQUEST_HEADERS,
                timeout=30,
            )
            data = rq.json()
            results = data.get("results") or []
            for res in results:
                sym = (res.get("symbol") or "").upper()
                rows = res.get("historicals") or []
                if not sym:
                    continue
                df = _historicals_to_dataframe(rows)
                if not df.empty:
                    out[sym] = df
        except Exception:
            for sym in chunk:
                if sym not in out:
                    df = get_price_history(sym, interval=interval, span=span, bounds=bounds)
                    if not df.empty:
                        out[sym] = df
    return out

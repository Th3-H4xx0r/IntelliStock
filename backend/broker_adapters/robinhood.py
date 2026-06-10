"""Robinhood live broker adapter.

Phase B (2026-04-29) — bridges BrokerAdapter contract → RobinhoodClient.

UNOFFICIAL API WARNING: Robinhood does not publish a brokerage API. This
adapter speaks the same JSON-over-HTTP surface the web app uses. ToS
violation risk and account-ban risk are non-zero. The UI surfaces an
explicit warning when the user picks Robinhood, and the adapter defaults
to RH_DRY_RUN=true at first boot so accidental live orders are blocked
until an operator flips the env.

Order path (mirrors AlpacaAdapter, polling-substitute for WebSocket):

  1. record_intent() in LiveOrderWAL                      (before any HTTP)
  2. resolve client_order_id → deterministic UUID ref_id  (idempotency key)
  3. find_order_by_ref_id() short-circuit                 (replay protection)
  4. place_order_equity() via RobinhoodClient
  5. mark_submitted() in WAL with broker_order_id
  6. polling thread (start_trade_updates) detects state transitions
     queued → confirmed → partially_filled → filled
                                          → rejected
                                          → cancelled
     and updates WAL + _positions/_trades/_cash + Discord alerts.

Crash safety: if place_order_equity raises after RH may have accepted,
find_order_by_ref_id is queried before re-raising. Restart reconciliation
walks WAL.list_open() and queries each ref_id.

Dry-run: RH_DRY_RUN=true (default) routes every submit through a synthetic
fill at the quote mid-price; no HTTP POST hits Robinhood's order endpoint.
The user must explicitly set RH_DRY_RUN=false to enable real-money trading.
"""

from __future__ import annotations

import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from broker_adapters.base import (
    BrokerAdapter,
    OrderRef,
    PositionDTO,
    CashDTO,
    AccountDTO,
    HealthStatus,
)
from broker_adapters.errors import (
    BrokerError,
    InsufficientBuyingPower,
    PDTRestricted,
    AssetHalted,
    AssetNotTradable,
    WashSale,
    FractionalNotAllowed,
    BrokerRateLimited,
    BrokerMFARequired,
)
from broker_adapters._client_order_id import make_client_order_id
from broker_adapters._wal import LiveOrderWAL


# Fixed namespace for deriving deterministic ref_id from client_order_id.
# Do NOT change once any account has placed orders — would break the
# uniqueness guarantee our crash-recovery relies on.
_RH_REF_NAMESPACE = uuid.UUID("a3c9f2e4-5d6b-4f8c-9d2e-1f3a5b7c9d1e")


def _alog(service: str, msg: str, color: str = "white") -> None:
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service=service)
    except Exception:
        print(f"[{service}] {msg}")


def _decode_jwt_expires_in(access_token):
    """Decode JWT exp claim → seconds until expiry. RH issues 31-day tokens
    but historic link code stored expires_in=86400, causing _maybe_refresh_token
    to attempt a refresh at the 12h mark of a multi-week token. Trust the
    JWT itself over the DB value when the JWT says longer."""
    import base64
    import json
    if not access_token or not isinstance(access_token, str):
        return None
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        exp = claims.get("exp")
        if not exp:
            return None
        derived = int(exp) - int(time.time())
        return derived if derived > 0 else None
    except Exception:
        return None


def _is_dry_run() -> bool:
    """Default ON. Operator must explicitly set RH_DRY_RUN=false to trade."""
    v = (os.environ.get("RH_DRY_RUN", "true") or "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def _available_cash_for_sizing(settled_cash: float, buying_power: float) -> float:
    """Spendable cash for position sizing AND portfolio valuation.

    1-A (bug-sweep 2026-05-28): Robinhood's ``buying_power`` on a margin /
    Instant / Gold account EXCEEDS settled cash (buying_power can sit a few
    hundred dollars above settled cash). The strategy is a cash-deployment
    system (cash_reserve_floor etc.) and must NOT silently deploy instant /
    margin credit. We cap at settled cash so deployment never exceeds what the
    operator actually has, and use the LOWER of the two so a hold that pushes
    buying_power below settled is also respected. On a cash account
    settled == buying_power, so this is a no-op. Clamped to >= 0 so a margin
    debit (negative cash) or a garbage reading can never over-deploy.
    """
    try:
        s = float(settled_cash or 0.0)
        b = float(buying_power or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(s, b))


def _order_style_for_now(price: float, side: str, qty: Optional[float], in_rth: bool) -> dict:
    """Order type/limit/extended_hours/tif appropriate for the current session.

    2-F (bug-sweep 2026-05-28): the live trade-gate treats extended hours as
    'open', but a plain extended_hours=False MARKET order submitted in pre/after
    hours cannot fill until the next regular-hours open — while the strategy's
    books treat the exit as issued. Outside RTH we want a marketable LIMIT with
    extended_hours=True so a forced exit actually fills.

    HARD CONSTRAINT: Robinhood prohibits fractional shares in extended hours AND
    fractional limit orders (robinhood_engine.py:1224-1236). The graph_nexus
    strategy trades fractional shares, so a fractional off-hours order MUST stay a
    regular-hours market order (it queues to the next open) — turning it into an
    ext-hours limit would be rejected outright. Only WHOLE-share quantities can use
    the ext-hours limit path. qty=None (notional buys) is treated as fractional.
    """
    is_fractional = qty is None or abs(float(qty) - round(float(qty))) > 1e-9
    if in_rth or is_fractional:
        return {"order_type": "market", "limit_price": None, "extended_hours": False,
                "tif": "day", "market_hours": "regular_hours"}
    try:
        buffer_pct = float(os.environ.get("RH_EXTENDED_HOURS_SLIPPAGE_PCT", "0.005"))
    except (TypeError, ValueError):
        buffer_pct = 0.005
    limit = round(float(price) * (1.0 + buffer_pct if side == "buy" else 1.0 - buffer_pct), 2)
    if limit <= 0:
        limit = round(float(price), 2)
    # 2-F / Scope D B1: market_hours is the field RH actually routes orders by.
    # extended_hours=True WITHOUT market_hours="extended_hours" is routed to the
    # regular session and will not fill off-hours.
    return {"order_type": "limit", "limit_price": limit, "extended_hours": True,
            "tif": "gfd", "market_hours": "extended_hours"}


_RH_CALENDAR_FALLBACK_WARNED = False


def _in_regular_hours(now_utc) -> bool:
    """RTH check that fails OPEN to regular-hours semantics if the calendar is
    unavailable (so we never accidentally route a fractional order to ext-hours).

    Scope D (half-day): warn ONCE if exchange_calendars is missing. The weekday
    fallback hardcodes a 16:00 close and ignores holidays / half-day early
    closes, so on an early-close day a post-13:00 ET order is mis-classified as
    in-RTH (plain market) when the regular session is already closed.
    """
    global _RH_CALENDAR_FALLBACK_WARNED
    try:
        from live_calendar import is_nyse_open, _HAS_LIB
        if not _HAS_LIB and not _RH_CALENDAR_FALLBACK_WARNED:
            _RH_CALENDAR_FALLBACK_WARNED = True
            _alog(
                "BROKER",
                "live_calendar: exchange_calendars NOT installed — RTH / "
                "extended-hours order routing uses a weekday fallback that does "
                "NOT honor holidays or half-day early closes. Install it "
                "(`pip install exchange_calendars`) for correct off-hours routing.",
                "red",
            )
        return bool(is_nyse_open(now_utc))
    except Exception:
        return True


def _map_rh_error_to_broker(e: Exception) -> BrokerError:
    """Translate a typed RobinhoodAPIError sub-class to the BrokerAdapter
    error taxonomy strategies isinstance() against.
    """
    try:
        from robinhood_engine import (
            RobinhoodAPIError,
            RobinhoodRateLimited,
            RobinhoodInsufficientBuyingPower,
            RobinhoodFractionalNotAllowed,
            RobinhoodPDTRestricted,
            RobinhoodWashSale,
            RobinhoodAccountRestricted,
            RobinhoodMarketClosed,
            RobinhoodMFARequired,
        )
    except Exception:
        return BrokerError(str(e))

    if isinstance(e, RobinhoodInsufficientBuyingPower):
        return InsufficientBuyingPower(str(e))
    if isinstance(e, RobinhoodPDTRestricted):
        return PDTRestricted(str(e))
    if isinstance(e, RobinhoodFractionalNotAllowed):
        return FractionalNotAllowed(str(e))
    if isinstance(e, RobinhoodWashSale):
        return WashSale(str(e))
    if isinstance(e, RobinhoodAccountRestricted):
        return AssetNotTradable(f"Account restricted: {e}")
    if isinstance(e, RobinhoodMarketClosed):
        # Caller decides whether to retry at next open or reject. Map to
        # AssetNotTradable so non-retryable taxonomy handles it without
        # a special-case sub-class.
        return AssetNotTradable(f"Market closed: {e}")
    if isinstance(e, RobinhoodRateLimited):
        return BrokerRateLimited(str(e), retry_after_sec=getattr(e, "retry_after_sec", None))
    if isinstance(e, RobinhoodMFARequired):
        return BrokerMFARequired(str(e))
    if isinstance(e, RobinhoodAPIError):
        return BrokerError(str(e))
    return BrokerError(str(e))


class RobinhoodAdapter(BrokerAdapter):
    """Production Robinhood live-trading adapter (UNOFFICIAL API)."""

    def __init__(
        self,
        *,
        api_key: str,                    # access_token
        api_secret: str,                 # refresh_token
        instance_id: str,
        wal: LiveOrderWAL,
        account_number: Optional[str] = None,
        device_token: Optional[str] = None,
        initial_value: Optional[float] = None,
        seed_trades_from_broker: bool = True,
        # 2026-04-30 — auth chain CRITICAL #1: session-state extras so the
        # in-process refresh helper has a real obtained_at_epoch to compute
        # the TTL gate against. Without these, _maybe_refresh_token's
        # ``obtained <= 0`` early-return runs every tick and the adapter
        # NEVER refreshes — surviving only on credential_service writes.
        obtained_at_epoch: Optional[int] = None,
        expires_in: Optional[int] = None,
        account_url: Optional[str] = None,
        # 2026-04-30 — auth chain CRITICAL #2: brokerage row id so the
        # adapter can persist refreshed tokens back to the SAME row that
        # credential_service reads. Without this, in-process refresh
        # rotates the refresh_token in memory only; on next backend
        # restart we boot on a stale (now invalid) refresh_token.
        brokerage_id: Optional[str] = None,
        # Clean-room mode (2026-05-28): when True, broker state is reconciled
        # against this-instance's LiveOrderWAL. Broker positions matched to a
        # filled WAL row are strategy-owned; everything else is quarantined.
        # Backward-compatible: defaults False -> existing legacy behavior.
        clean_room_mode: bool = False,
        cid_prefix: Optional[str] = None,
        clean_room_retention_days: int = 180,
        # Test-only injection point. Production callers leave this None.
        # When provided, the adapter skips the lazy robinhood_engine import
        # (which is heavy and may not be installed in the test environment)
        # AND skips select_default_account, instead trusting the operator
        # to wire account_number / account_url directly.
        _test_client: Optional[Any] = None,
    ):
        if _test_client is not None:
            # Test path: skip the real robinhood_engine import + session setup
            # so the adapter can be exercised in unit tests without the unofficial
            # RH SDK available. Tests pass account_number / account_url directly.
            self._RobinhoodClient = type(_test_client) if not isinstance(_test_client, type) else _test_client
            self._rh_get_live_prices = lambda *a, **kw: {}
            _RH_TEST_MODE = True
        else:
            try:
                from robinhood_engine import (
                    RobinhoodClient,
                    RobinhoodSessionState,
                    get_live_prices as _rh_get_live_prices,
                )
            except ImportError as e:
                raise BrokerError(
                    "robinhood_engine import failed; cannot start RobinhoodAdapter."
                ) from e

            self._RobinhoodClient = RobinhoodClient
            self._rh_get_live_prices = _rh_get_live_prices
            _RH_TEST_MODE = False

        # Session state — refresh_token is mandatory for unattended trading
        # (access_token rotates every 24h; the credential_service refreshes
        # in the background but the adapter must accept refreshed values
        # too). Both come pre-decrypted from broker.py:_load_live_credentials.
        if not _RH_TEST_MODE and (not api_key or not api_secret):
            raise BrokerError(
                "RobinhoodAdapter requires both access_token and refresh_token. "
                "Re-link the brokerage from the UI or run "
                "`python robinhood_cli.py --paste-tokens`."
            )
        if _RH_TEST_MODE:
            # Skip JWT decoding + RobinhoodClient construction in test mode.
            self._client = _test_client
        else:
            # 2026-04-30 — seed obtained_at_epoch + expires_in + account_url so
            # _maybe_refresh_token has real values to compute against. Default
            # expires_in to 86400 (RH's standard 24h) when DB is missing it; the
            # adapter then assumes a worst-case "token may already be old" and
            # the first refresh-gate check forces a refresh on first poll tick.
            # Defaulting obtained_at_epoch to now() rather than 0 prevents the
            # legacy ``obtained <= 0`` early-return; if the DB row genuinely
            # lacks the field we treat the in-memory token as just-minted, but
            # _maybe_refresh_token's persistence helper will write the real
            # value on the first refresh.
            _seed_obtained = int(obtained_at_epoch) if (obtained_at_epoch and obtained_at_epoch > 0) else int(time.time())
            # 2026-05-02: derive real TTL from JWT exp claim. RH issues 31-day
            # tokens but historic link code stored the wrong 86400 default; use
            # the JWT's authoritative expiry to override stale DB values.
            _seed_expires = int(expires_in) if (expires_in and expires_in > 0) else 86400
            try:
                _jwt_exp = _decode_jwt_expires_in(api_key)
                if _jwt_exp and _jwt_exp > _seed_expires:
                    _seed_expires = _jwt_exp
            except Exception:
                pass
            state = RobinhoodSessionState(
                access_token=api_key,
                refresh_token=api_secret,
                token_type="Bearer",
                device_token=device_token or None,
                account_number=account_number or None,
                account_url=account_url or None,
                obtained_at_epoch=_seed_obtained,
                expires_in=_seed_expires,
            )
            # 2026-05-05 third-pass: match live_broker_fetch's RobinhoodClient
            # default (timeout_sec=20). Prior 30s was longer than the snapshot
            # bound (12s), so requests' own timeout never had a chance to fire
            # before our wrapper aborted. With 20s here + 25s outer bound, the
            # requests-level timeout fires FIRST on real outages and produces a
            # clean RobinhoodAPIError exception instead of an executor abandonment.
            self._client = RobinhoodClient(state=state, timeout_sec=20, debug_http=False)
        # Brokerage row id for token persistence (auth chain CRITICAL #2).
        self._brokerage_id = (brokerage_id or "").strip() or None

        # Resolve account_number / account_url. If caller passed account_number,
        # use it; otherwise pick the first active account.
        if _RH_TEST_MODE:
            picked = {
                "account_number": account_number or "TEST-ACCT",
                "url": account_url or "https://api.robinhood.com/accounts/TEST/",
            }
        else:
            try:
                picked = self._client.select_default_account(
                    preferred_account_number=account_number,
                )
            except Exception as e:
                # Bug-check fix #11: close the requests.Session on failed init so
                # we don't leak the connection pool. The session was opened by
                # RobinhoodClient.__init__ at line ~136 above.
                try:
                    if getattr(self._client, "session", None) is not None:
                        self._client.session.close()
                except Exception:
                    pass
                raise BrokerError(
                    f"RobinhoodAdapter: select_default_account failed ({type(e).__name__}: {e}). "
                    "Token may be expired — try refresh from the UI."
                ) from e
        self._account_number = (picked.get("account_number") or "").strip()
        self._account_url = (picked.get("url") or "").strip()
        if not self._account_url:
            raise BrokerError(
                "RobinhoodAdapter: could not resolve account_url. Re-link account."
            )

        self._instance_id = instance_id
        self._wal = wal
        # Clean-room state (always set; only used when clean_room_mode=True).
        self._clean_room_mode = bool(clean_room_mode)
        self._cid_prefix = cid_prefix or ""
        self._clean_room_retention_days = int(clean_room_retention_days)
        self._external_positions: dict[str, dict] = {}
        # `paper` is meaningless for Robinhood (no paper account exists).
        # We expose a distinct "_paper" attribute for the UI badge so the
        # operator sees the dry-run state truthfully instead of "paper".
        self._dry_run = _is_dry_run()
        self._paper = self._dry_run  # UI hint: dry-run shows as PAPER badge
        self._api_key = api_key
        self._api_secret = api_secret
        self._buying_power: float = 0.0
        self._account_id = self._account_number

        # Discord alert function refs cached at construction so we don't
        # re-import per trade event and a missing module is visible ONCE.
        self._alert_submit = None
        self._alert_fill = None
        self._alert_reject = None
        self._alert_retry = None
        self._alert_strategy_error = None
        try:
            from live_alerts import (
                alert_order_submit as _alert_submit,
                alert_order_fill as _alert_fill,
                alert_order_reject as _alert_reject,
                alert_order_retry as _alert_retry,
                alert_strategy_error as _alert_strategy_error,
            )
            self._alert_submit = _alert_submit
            self._alert_fill = _alert_fill
            self._alert_reject = _alert_reject
            self._alert_retry = _alert_retry
            self._alert_strategy_error = _alert_strategy_error
        except Exception as _imp_e:
            _alog(
                "BROKER",
                f"Discord live-alerts DISABLED (RobinhoodAdapter): "
                f"{type(_imp_e).__name__}: {_imp_e}",
                "red",
            )

        # Polling thread state (substitute for AlpacaAdapter's WebSocket)
        # 2026-05-07 deadlock defense-in-depth: was threading.Lock() — the
        # `save_portfolio_snapshot` self-deadlock (now fixed) proved that
        # the 28 lock sites in this file are too easy to accidentally
        # nest. Switch to RLock so any future same-thread re-entrance is
        # safe. Cross-thread mutual-exclusion semantics are unchanged
        # (RLock is still mutually exclusive between threads).
        self._lock = threading.RLock()
        self._stream_thread: Optional[threading.Thread] = None
        self._stop_polling = False
        # Tracked open orders so we can detect terminal transitions and
        # fire alerts/WAL updates exactly once.
        self._tracked_orders: dict[str, dict] = {}
        # Last seen state per broker_order_id so a transition queued→
        # confirmed (still non-terminal) doesn't double-fire alerts.
        self._tracked_states: dict[str, str] = {}
        self._poll_interval_sec = float(os.environ.get("RH_POLL_INTERVAL_SEC", "5") or 5)
        self._last_heartbeat_utc: Optional[datetime] = None
        self._trade_updates_connected = False

        # 2026-04-30 inter-order delay knob — random uniform between
        # RH_INTER_ORDER_DELAY_MIN_SEC / RH_INTER_ORDER_DELAY_MAX_SEC
        # (defaults 25 / 45). Burst submissions on RH frequently 429 or
        # silently fail; pacing makes sequential orders survive. Set
        # both to 0 to disable. Tracked per-adapter via _last_submit_ts
        # so the FIRST order of a tick never pays the wait.
        self._inter_order_delay_min: float = float(
            os.environ.get("RH_INTER_ORDER_DELAY_MIN_SEC", "25") or 25
        )
        self._inter_order_delay_max: float = float(
            os.environ.get("RH_INTER_ORDER_DELAY_MAX_SEC", "45") or 45
        )
        self._last_submit_ts: float = 0.0

        # PortfolioEmulator-compatible mirror
        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []
        self._cash: float = 0.0
        self._last_prices: dict[str, float] = {}
        self._portfolio_snapshots: list[dict] = []
        self._positions_stale_since: Optional[float] = None
        self._positions_stale_max_sec: float = float(
            os.environ.get("RH_POSITIONS_STALE_MAX_SEC", "600") or 600
        )
        self._orders_today: dict[str, set[str]] = {}
        self._last_orders_refresh_ny_date: Optional[str] = None

        # 2026-04-30 — caches previously initialized lazily via hasattr-guards
        # in their first callsite; centralizing here avoids the readability/
        # racy-init footguns flagged in LOW #39.
        self._instrument_symbol_cache: dict[str, str] = {}
        self._oid_to_cid: dict[str, str] = {}
        self._rh_frac_cache: dict[str, tuple[Optional[bool], float]] = {}
        # Per-oid running cumulative_quantity already accounted to _trades
        # so partial-fill increments don't get double-counted (HIGH #4/#5).
        self._prev_filled_qty: dict[str, float] = {}

        # 2026-04-30 — last-known cash/account DTOs so REST failures don't
        # surface lies (e.g., refresh_account returning equity=_initial_value
        # makes drawdown look stuck). MED #29/#30.
        self._last_cash_dto: Optional[CashDTO] = None
        self._last_account_dto: Optional[AccountDTO] = None
        # Cached settled-cash separately from buying_power (MED #29).
        self._settled_cash: float = 0.0
        # 2026-05-05 — TTL caches for refresh_account / refresh_positions.
        # The snapshot worker was calling these every 3s = 1200 hits/hr per
        # endpoint, which trips RH's per-session rate-limit and shows up as
        # 25s+ HTTP-layer stalls. The user's directive: snapshot doesn't
        # need to refresh — that should only happen on the hourly dual-cadence
        # run and via the polling thread's 60s safety-net refresh.
        #
        # TTL = 1h (matches dual-cadence cadence). Snapshot calls (no force)
        # hit the cache. Polling thread + dual-cadence pass force=True to
        # bypass and actually refresh. Net: ~60 calls/hr (polling) vs prior
        # ~1200/hr (snapshot) — 95% reduction.
        self._refresh_account_ttl_sec = float(os.environ.get("RH_REFRESH_ACCOUNT_TTL_SEC", "3600"))
        self._refresh_positions_ttl_sec = float(os.environ.get("RH_REFRESH_POSITIONS_TTL_SEC", "3600"))
        self._last_account_refresh_ts: float = 0.0
        self._last_positions_dtos: Optional[list] = None
        self._last_positions_refresh_ts: float = 0.0

        # Submit serialization lock — distinct from _lock (which protects the
        # state mutator dicts). _submit_lock serializes the FULL submit body
        # so two ticks can't race the inter-order delay window. HIGH #6.
        self._submit_lock = threading.Lock()
        # Kill-switch event so cancel_all_open_orders can preempt an
        # in-flight inter-order delay sleep. HIGH #21.
        self._halt_event = threading.Event()
        # Burst-poll bookkeeping — set after every successful submit so the
        # polling loop sleeps for a short interval briefly before reverting
        # to the steady-state interval. Enhancement 2026-04-30.
        self._burst_poll_until: float = 0.0
        self._burst_poll_interval_sec: float = float(
            os.environ.get("RH_BURST_POLL_INTERVAL_SEC", "5") or 5
        )
        self._burst_poll_window_sec: float = float(
            os.environ.get("RH_BURST_POLL_WINDOW_SEC", "120") or 120
        )

        # Token refresh tracking
        self._last_token_refresh: float = time.time()
        # 2026-04-30 — auth chain MED #16: backoff state for refresh failures
        # so a degraded RH endpoint doesn't get hammered every poll-tick.
        self._token_refresh_failures: int = 0
        self._token_refresh_next_attempt: float = 0.0
        # Whether we've alerted on the current run of consecutive failures
        # (one alert at threshold, not one-per-tick).
        self._token_refresh_alerted: bool = False
        # Why: MFARequired is terminal until manual re-link via /brokerages UI.
        self._refresh_disabled_until_relink: bool = False

        # Seed from Robinhood. force=True so the cache-only refresh path
        # (introduced 2026-05-05) actually does HTTP and populates
        # _last_positions_dtos / _last_account_dto. Without force=True,
        # cold cache → returns [] / raises BrokerError, breaking init.
        self.refresh_cash()
        self.refresh_positions(force=True)

        if self._clean_room_mode:
            # Reconcile broker view against this-instance's LiveOrderWAL.
            from ._classifier import classify_broker_positions, derive_cid_prefix
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            _now = _dt.now(_tz.utc)
            _prefix = self._cid_prefix or derive_cid_prefix(self._instance_id)
            # Scope D D1: pass since_utc=None so the classifier receives ALL
            # filled rows and applies retention itself (it computes its own cutoff
            # and re-filters). The store-level since_utc pre-filter dropped rows
            # older than the cutoff BEFORE the classifier could use them to tag an
            # aged-out (beyond-retention) still-held position vs a never-seen one
            # (2-C). The full-table scan cost is unchanged (the store scans all
            # rows regardless; since_utc only trimmed the returned list).
            _wal_rows = self._wal.list_filled_for_prefix(_prefix, since_utc=None)
            _broker_view = []
            for _sym, _qty in (self._positions or {}).items():
                _broker_view.append({
                    "symbol": _sym,
                    "qty": float(_qty),
                    "market_value": float(self._last_prices.get(_sym, 0.0) or 0.0) * float(_qty),
                })
            _owned, _external, _wal_trades = classify_broker_positions(
                positions=_broker_view,
                wal_rows=_wal_rows,
                instance_id=self._instance_id,
                cid_prefix=_prefix,
                retention_days=self._clean_room_retention_days,
                now_utc=_now,
            )
            self._positions = dict(_owned)
            self._external_positions = dict(_external)
            self._trades = list(_wal_trades)

            if initial_value is None:
                raise BrokerError(
                    "RobinhoodAdapter clean_room_mode=True requires an explicit "
                    "initial_value (configure Instances.<id>.initial_value or "
                    "pass LIVE_INITIAL_VALUE env)."
                )
            self._initial_value = float(initial_value)
        else:
            if seed_trades_from_broker:
                try:
                    self._seed_trades_from_broker(limit=200)
                except Exception:
                    pass

            if initial_value is not None:
                self._initial_value = float(initial_value)
            else:
                try:
                    acct_dto = self.refresh_account(force=True)
                    self._initial_value = float(acct_dto.equity)
                except Exception:
                    self._initial_value = self._cash + self.get_positions_value({})

        if self._initial_value <= 0:
            raise BrokerError(
                "RobinhoodAdapter init: account equity <= 0. "
                "Check tokens and that the linked account is funded."
            )

        _alog(
            "BROKER",
            f"RobinhoodAdapter ready: account={self._account_number} "
            f"equity=${self._initial_value:.2f} positions={len(self._positions)} "
            f"dry_run={self._dry_run}",
            "yellow" if self._dry_run else "green",
        )
        if self._dry_run:
            _alog(
                "BROKER",
                "RH_DRY_RUN=true — orders will NOT be submitted to Robinhood. "
                "Set RH_DRY_RUN=false to enable real trading.",
                "yellow",
            )

    # ------------------------------------------------------------------
    # Helpers

    def _ref_id_from_cid(self, client_order_id: str) -> str:
        """Deterministic UUID derived from client_order_id. Same cid → same
        ref_id, so a network-blip retry hits Robinhood's own dedup record.
        """
        return str(uuid.uuid5(_RH_REF_NAMESPACE, client_order_id or ""))

    def _instrument_url_to_symbol(self, instrument_url: str) -> str:
        """Look up symbol for an instrument URL. Cached to avoid repeated GETs.

        Used by refresh_positions and the polling fill detector.
        """
        cache = self._instrument_symbol_cache
        url = (instrument_url or "").strip()
        if not url:
            return ""
        if url in cache:
            return cache[url]
        try:
            inst = self._client.get_instrument(url)
            sym = (inst.get("symbol") or "").strip().upper()
            if sym:
                cache[url] = sym
            return sym
        except Exception:
            return ""

    def _is_rh_fractionable(self, symbol: str) -> Optional[bool]:
        """Return cached fractionable flag for symbol. Cache is populated
        by submit_order's rejection handler (NOT by a pre-fetch via
        get_instrument). Returns None on cache miss — caller treats as
        'unknown' and submits optimistically.

        Negative cache TTL still applies if a transient lookup once set
        None (future-proofing — there is no upfront network call here
        anymore, but keeping the TTL semantics consistent with Alpaca).
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        cache = self._rh_frac_cache
        now = time.time()
        if sym in cache:
            val, exp = cache[sym]
            if val is True or val is False:
                return val
            if now < exp:
                return None
        # Cache miss: do NOT pre-fetch via get_instrument. Return None so
        # caller submits optimistically; the rejection handler will
        # populate cache on a real "not fractionable" response.
        return None

    def _maybe_refresh_token(self) -> None:
        """Refresh access_token before expiry. RH access tokens default to 24h.

        2026-04-30 — auth chain CRITICAL #1/#2/#3 + MED #16:
          * Reads real obtained_at_epoch / expires_in seeded from the DB row
            (was missing → ``obtained <= 0`` → early-return → never fired).
          * Persists refreshed tokens back to the SAME BrokerageAccounts row
            credential_service reads, so the next backend boot uses the live
            refresh_token instead of the stale one rotated out by RH.
          * Skips when the DB row was refreshed by credential_service in the
            last 5 minutes (single-writer via last_refresh_at) so the two
            refreshers don't race over the rotating refresh_token.
          * Tracks consecutive failures, fires Discord alert ONCE at >=3,
            and applies exponential backoff (cap 1h) so a degraded RH
            endpoint doesn't get hammered every poll-tick.
        """
        # Why: MFARequired is terminal until manual re-link via /brokerages UI.
        # But if a re-link DID happen (DB row's _last_refresh_epoch is newer
        # than our in-memory state), the suppression should clear so we don't
        # require a container restart to recover. Check the DB once before
        # respecting the flag.
        if self._refresh_disabled_until_relink:
            try:
                _recovery_doc = self._read_brokerage_doc_from_db()
                if _recovery_doc:
                    _db_last = int(_recovery_doc.get("_last_refresh_epoch", 0) or 0)
                    _our_obt = int(self._client.state.obtained_at_epoch or 0)
                    if _db_last and _db_last > _our_obt:
                        self._reload_tokens_from_doc(_recovery_doc)
                        self._refresh_disabled_until_relink = False
                        self._token_refresh_failures = 0
                        self._token_refresh_next_attempt = 0.0
                        self._token_refresh_alerted = False
                        _alog(
                            "BROKER",
                            "Robinhood refresh re-enabled — DB shows fresh tokens "
                            "(operator re-linked via /brokerages).",
                            "green",
                        )
                        return
            except Exception:
                pass
            return
        try:
            now = time.time()
            # Backoff gate — if we just failed N times, wait until the
            # backoff window elapses before retrying.
            if self._token_refresh_next_attempt and now < self._token_refresh_next_attempt:
                return

            # Single-writer guard: if credential_service refreshed AT ANY
            # POINT after our in-memory tokens were minted, reload from DB
            # and skip our own refresh. Previously this only fired when
            # cred_service had refreshed in the last 5 minutes — outside
            # that window the broker would attempt its own refresh against
            # an already-burned refresh_token and hit RobinhoodMFARequired
            # (terminal, suppression flag fires, operator must re-link).
            # Now: any DB write newer than our in-memory state wins.
            db_doc = self._read_brokerage_doc_from_db()
            if db_doc:
                _last_refresh_at = int(db_doc.get("_last_refresh_epoch", 0) or 0)
                _our_obtained = int(self._client.state.obtained_at_epoch or 0)
                if _last_refresh_at and _last_refresh_at > _our_obtained:
                    # cred_service has rotated tokens since we minted ours;
                    # adopt the newer state and let cred_service own the next
                    # refresh cycle. Avoids the "two-writer race burns the
                    # refresh_token" failure mode.
                    self._reload_tokens_from_doc(db_doc)
                    _alog(
                        "BROKER",
                        f"Robinhood adopted DB-rotated tokens "
                        f"(db_last_refresh={_last_refresh_at}, our_obtained={_our_obtained})",
                        "cyan",
                    )
                    return

            obtained = int(self._client.state.obtained_at_epoch or 0)
            expires_in = int(self._client.state.expires_in or 86400)
            if obtained <= 0:
                # Defensive — should never happen after the seeded init,
                # but if it does, force a refresh (assume worst case).
                obtained = int(now - expires_in * 0.51)
            elapsed = now - obtained
            if elapsed <= expires_in * 0.50:
                return

            self._client.refresh()
            self._last_token_refresh = now
            self._api_key = self._client.state.access_token or self._api_key
            self._api_secret = self._client.state.refresh_token or self._api_secret
            # Persist BEFORE clearing failure-count so a write failure
            # doesn't reset the backoff and hammer DB next tick.
            self._persist_refreshed_tokens_to_db()
            self._token_refresh_failures = 0
            self._token_refresh_next_attempt = 0.0
            self._token_refresh_alerted = False
            _alog("BROKER", "Robinhood access_token refreshed (and persisted)", "cyan")
        except Exception as e:
            # Why: MFARequired/ChallengeRequired during a programmatic refresh
            # is terminal — no silent recovery is possible until the user
            # re-links via /brokerages UI. Suppress further attempts.
            try:
                from robinhood_engine import (
                    RobinhoodMFARequired,
                    RobinhoodChallengeRequired,
                )
                _is_terminal = isinstance(e, (RobinhoodMFARequired, RobinhoodChallengeRequired))
            except Exception:
                _is_terminal = False
            if _is_terminal:
                self._refresh_disabled_until_relink = True
                self._token_refresh_next_attempt = float("inf")
                self._token_refresh_failures = max(self._token_refresh_failures, 999)
                _already_alerted = self._token_refresh_alerted
                self._token_refresh_alerted = True
                if not _already_alerted:
                    _alog(
                        "BROKER",
                        "Robinhood refresh requires manual re-link via /brokerages UI "
                        "— suppressing further refresh attempts. Access token continues "
                        "to work until expiry.",
                        "yellow",
                    )
                    if self._alert_strategy_error:
                        try:
                            self._alert_strategy_error(
                                instance_id=self._instance_id,
                                tag="rh-refresh-terminal",
                                message=(
                                    f"Robinhood token refresh hit a terminal auth failure "
                                    f"({type(e).__name__}: {e}). Automatic refresh has been "
                                    f"DISABLED until you re-link the brokerage via the "
                                    f"/brokerages UI. The current access token will continue "
                                    f"to work until it expires."
                                ),
                            )
                        except Exception:
                            pass
                return
            self._token_refresh_failures += 1
            # Exponential backoff: 60s, 2m, 4m, 8m, ..., cap 1h.
            _backoff = min(3600.0, 60.0 * (2.0 ** max(0, self._token_refresh_failures - 1)))
            self._token_refresh_next_attempt = time.time() + _backoff
            _alog(
                "BROKER",
                f"Robinhood token refresh failed (#{self._token_refresh_failures}): "
                f"{type(e).__name__}: {e} — next attempt in {_backoff:.0f}s",
                "yellow",
            )
            # Alert ONCE at threshold so the channel doesn't get spammed.
            if (
                self._token_refresh_failures >= 3
                and not self._token_refresh_alerted
                and self._alert_strategy_error
            ):
                try:
                    self._alert_strategy_error(
                        instance_id=self._instance_id,
                        tag="rh-refresh-stuck",
                        message=(
                            f"Robinhood token refresh has failed {self._token_refresh_failures} "
                            f"consecutive times. Last error: {type(e).__name__}: {e}. "
                            f"Backoff now {_backoff:.0f}s. credential_service may also be "
                            f"failing — check brokerage row status."
                        ),
                    )
                    self._token_refresh_alerted = True
                except Exception:
                    pass

    def _read_brokerage_doc_from_db(self) -> Optional[dict]:
        """Best-effort: read the BrokerageAccounts row for this adapter.
        Returns None if no _brokerage_id, RethinkDB unreachable, or row is
        missing. Used by _maybe_refresh_token's single-writer guard.
        """
        if not self._brokerage_id:
            return None
        try:
            from rethinkdb import RethinkDB
            _r = RethinkDB()
            host = os.environ.get("RETHINKDB_HOST", "localhost")
            port = int(os.environ.get("RETHINKDB_PORT", "28015"))
            conn = _r.connect(host=host, port=port, timeout=10)
            try:
                doc = (
                    _r.db("IntelliStock")
                    .table("BrokerageAccounts")
                    .get(self._brokerage_id)
                    .run(conn)
                )
                return doc if isinstance(doc, dict) else None
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            return None

    def _reload_tokens_from_doc(self, doc: dict) -> None:
        """Pick up rotated tokens written by credential_service. Best-effort —
        if decrypt fails we leave in-memory state alone (will hit 401 on
        next request and the caller can re-attempt).
        """
        try:
            from secret_store import decrypt as _decrypt
        except Exception:
            _decrypt = lambda v: v
        try:
            new_access = _decrypt(doc.get("robinhood_access_token"))
            new_refresh = _decrypt(doc.get("robinhood_refresh_token"))
            if new_access:
                self._client.state.access_token = str(new_access)
                self._api_key = str(new_access)
            if new_refresh:
                self._client.state.refresh_token = str(new_refresh)
                self._api_secret = str(new_refresh)
            try:
                _oa = doc.get("robinhood_obtained_at_epoch")
                if _oa is not None:
                    self._client.state.obtained_at_epoch = int(_oa)
            except Exception:
                pass
            try:
                _ei = doc.get("robinhood_expires_in")
                if _ei is not None:
                    self._client.state.expires_in = int(_ei)
            except Exception:
                pass
        except Exception as e:
            _alog("BROKER", f"_reload_tokens_from_doc failed: {e}", "yellow")

    def _persist_refreshed_tokens_to_db(self) -> None:
        """Write new tokens + obtained_at_epoch + expires_in + last_refresh
        marker back to the BrokerageAccounts row credential_service reads.
        Best-effort: a write failure is logged but does NOT raise (the
        in-memory state is already updated; worst case is the next backend
        restart boots on a stale refresh_token, which is the bug we had
        before this fix and is no worse than not persisting at all).
        """
        if not self._brokerage_id:
            return
        try:
            from rethinkdb import RethinkDB
            try:
                from secret_store import encrypt as _encrypt
            except Exception:
                _encrypt = lambda v: v
            _r = RethinkDB()
            host = os.environ.get("RETHINKDB_HOST", "localhost")
            port = int(os.environ.get("RETHINKDB_PORT", "28015"))
            conn = _r.connect(host=host, port=port, timeout=10)
            try:
                # Encrypt each token; fall through to plaintext only if the
                # Fernet key is missing (matches credential_service.py).
                try:
                    _enc_access = _encrypt(self._client.state.access_token or "")
                    _enc_refresh = _encrypt(self._client.state.refresh_token or "")
                except RuntimeError:
                    _enc_access = self._client.state.access_token or ""
                    _enc_refresh = self._client.state.refresh_token or ""
                import datetime as _dt
                _now_iso = _dt.datetime.utcnow().isoformat()
                _now_epoch = int(time.time())
                _r.db("IntelliStock").table("BrokerageAccounts").get(self._brokerage_id).update({
                    "robinhood_access_token": _enc_access,
                    "robinhood_refresh_token": _enc_refresh,
                    "robinhood_obtained_at_epoch": int(self._client.state.obtained_at_epoch or _now_epoch),
                    "robinhood_expires_in": int(self._client.state.expires_in or 86400),
                    "status": "active",
                    "last_error": None,
                    "last_refresh_at": _now_iso,
                    # Epoch field used by single-writer guard (credential_service
                    # uses ISO last_refresh_at; we add an epoch alias for fast
                    # arithmetic without parsing).
                    "_last_refresh_epoch": _now_epoch,
                    "updated_at": _now_iso,
                }).run(conn)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as e:
            _alog(
                "BROKER",
                f"Robinhood token persistence to DB failed: {type(e).__name__}: {e}. "
                f"In-memory tokens are fresh but next restart may use stale ones.",
                "red",
            )

    def _to_orderref(self, o: dict) -> OrderRef:
        """Robinhood order dict → OrderRef.

        2026-04-30 LOW #35: client_order_id should be the actual cid (from
        our oid→cid reverse cache) rather than the ref_id (which is the
        uuid5 hash of cid; uuid5 is one-way so a downstream comparator
        against the original cid would mismatch). Falls back to ref_id
        only when the cache miss is genuine.
        """
        side = (o.get("side") or "").strip().lower()
        state = (o.get("state") or "").strip().lower()
        try:
            qty = float(o.get("quantity") or 0.0)
        except Exception:
            qty = 0.0
        try:
            filled_qty = float(o.get("cumulative_quantity") or 0.0)
        except Exception:
            filled_qty = 0.0
        try:
            avg = float(o.get("average_price")) if o.get("average_price") is not None else None
        except Exception:
            avg = None
        sub_at = None
        try:
            ts = o.get("created_at") or o.get("updated_at")
            if ts:
                sub_at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            sub_at = None
        sym = (o.get("symbol") or "").strip().upper()
        if not sym:
            inst_url = (o.get("instrument") or "").strip()
            sym = self._instrument_url_to_symbol(inst_url)
        oid = str(o.get("id") or "")
        cid = self._cid_for_broker_order_id(oid) if oid else None
        if not cid:
            cid = str(o.get("ref_id") or "")
        return OrderRef(
            broker_order_id=oid,
            client_order_id=cid,
            symbol=sym,
            side=side,
            qty=qty,
            status=state,
            filled_qty=filled_qty,
            filled_avg_price=avg,
            submitted_at_utc=sub_at,
        )

    def _seed_trades_from_broker(self, limit: int = 200) -> None:
        """Best-effort: pull recent filled orders into _trades so strategies
        that look back via reversed(_trades) see pre-restart positions.
        """
        try:
            recent = self._client.list_orders(limit=limit, state="filled",
                                               account_number=self._account_number)
        except Exception:
            return
        all_fills: list[dict] = []
        for o in recent:
            try:
                if (o.get("state") or "").strip().lower() != "filled":
                    continue
                side = (o.get("side") or "").strip().lower()
                if side not in ("buy", "sell"):
                    continue
                shares = float(o.get("cumulative_quantity") or 0.0)
                price = float(o.get("average_price") or 0.0)
                if shares <= 0 or price <= 0:
                    continue
                inst_url = (o.get("instrument") or "").strip()
                sym = (o.get("symbol") or "").strip().upper() or self._instrument_url_to_symbol(inst_url)
                if not sym:
                    continue
                ts = None
                try:
                    raw_ts = o.get("last_transaction_at") or o.get("updated_at") or o.get("created_at")
                    if raw_ts:
                        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                except Exception:
                    ts = None
                all_fills.append({
                    "timestamp": ts,
                    "action": side,
                    "ticker": sym,
                    "shares": shares,
                    "price": price,
                    "total": shares * price,
                    "cash_after": None,
                })
            except Exception:
                continue
        all_fills.sort(key=lambda t: t.get("timestamp") or datetime.min.replace(tzinfo=timezone.utc))
        # 2026-04-30 LOW #38: extend + dedupe instead of overwrite. Even
        # though _seed_trades_from_broker is intended to be called once at
        # init time, defending against a future post-init call avoids
        # nuking accumulated fills if anyone wires up a re-seed path.
        with self._lock:
            _seen = set()
            for t in self._trades:
                _key = (
                    str(t.get("ticker") or ""),
                    str(t.get("action") or ""),
                    str(t.get("timestamp") or ""),
                    float(t.get("shares") or 0.0),
                    float(t.get("price") or 0.0),
                )
                _seen.add(_key)
            for t in all_fills:
                _key = (
                    str(t.get("ticker") or ""),
                    str(t.get("action") or ""),
                    str(t.get("timestamp") or ""),
                    float(t.get("shares") or 0.0),
                    float(t.get("price") or 0.0),
                )
                if _key in _seen:
                    continue
                self._trades.append(t)
                _seen.add(_key)
            self._trades.sort(key=lambda t: t.get("timestamp") or datetime.min.replace(tzinfo=timezone.utc))

    # ------------------------------------------------------------------
    # Order submission

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
        order_type: str,
        limit_price: Optional[float],
        tif: str,
        extended_hours: bool,
        client_order_id: str,
        market_hours: str = "regular_hours",
    ) -> OrderRef:
        """Serialized full-body submit. The HIGH-#6 agent finding noted
        that the previous implementation only held ``self._lock`` for tiny
        mutator blocks, leaving the inter-order-delay sleep + the actual
        ``place_order_equity`` POST unprotected from concurrent ticks. Two
        parallel submits could observe the same ``_last_submit_ts``, skip
        the sleep, and burst-submit into a 429.

        We acquire ``self._submit_lock`` (a separate threading.Lock from
        the state-mutator ``self._lock``) at the top of submit_order and
        hold it across the entire body — pre-flight checks, intent record,
        delay sleep, retry path, and final stamp/track. This serializes
        ALL submits adapter-wide, which is the correct rate-limit window
        for an unofficial API where 429s are aggressive.
        """
        with self._submit_lock:
            return self._submit_order_locked(
                symbol=symbol, side=side, qty=qty, notional=notional,
                order_type=order_type, limit_price=limit_price, tif=tif,
                extended_hours=extended_hours, client_order_id=client_order_id,
                market_hours=market_hours,
            )

    def _submit_order_locked(
        self,
        *,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
        order_type: str,
        limit_price: Optional[float],
        tif: str,
        extended_hours: bool,
        client_order_id: str,
        market_hours: str = "regular_hours",
    ) -> OrderRef:
        from robinhood_engine import RobinhoodAPIError
        # Bug-check fix #1 (BLOCKER): reject empty/whitespace cid. uuid5(NS, "")
        # collapses every empty-cid order to the same ref_id → false-positive
        # idempotency hits across unrelated submissions.
        if not (client_order_id and str(client_order_id).strip()):
            raise BrokerError(
                "submit_order: client_order_id is required and must be non-empty "
                "(deterministic ref_id derivation depends on it)."
            )
        # 2026-04-30 — agent MED #23: RH instruments endpoint accepts dash-
        # form tickers (BRK-A, BF-B) but rejects the dot-form (BRK.A).
        # Normalize once at adapter entry so every downstream call (instrument
        # lookup, ref_id, WAL, order POST, fill bookkeeping) uses the same
        # symbol shape. 2-D / Scope D C3: use the shared helper so the WAL write
        # path and the classifier read path can't diverge.
        from ._symbols import normalize_broker_symbol
        symbol = normalize_broker_symbol(symbol)
        side_lc = (side or "").strip().lower()
        order_type_lc = (order_type or "market").strip().lower()
        ref_id = self._ref_id_from_cid(client_order_id)

        # Idempotency: if we've already attempted this cid (same ref_id), surface
        # the existing order rather than resubmit. This is the core crash-safety
        # contract — a network blip retry must not place a duplicate buy.
        # Bug-check fix #10 (MUST-FIX): dry-run orders are NOT in RH; they live
        # only in our WAL. Same-cid retry would produce a double synthetic fill.
        # Check WAL FIRST for a dry-run record before querying RH.
        try:
            wal_existing = self._wal.get(client_order_id)
        except Exception:
            wal_existing = None
        if wal_existing is not None:
            wal_state = getattr(wal_existing, "state", "") or ""
            broker_oid = getattr(wal_existing, "broker_order_id", "") or ""
            if str(broker_oid).startswith("dry-") and wal_state in ("submitted", "filled"):
                _alog("BROKER",
                      f"Robinhood DRY-RUN idempotent hit: cid={client_order_id[:24]} "
                      f"order_id={broker_oid} — returning prior synthetic fill", "cyan")
                return OrderRef(
                    broker_order_id=str(broker_oid),
                    client_order_id=client_order_id,
                    symbol=symbol, side=side_lc, qty=float(qty or 0.0),
                    status="filled",
                    filled_qty=float(qty or 0.0),
                    filled_avg_price=getattr(wal_existing, "filled_avg_price", None),
                    submitted_at_utc=datetime.now(timezone.utc),
                )
        try:
            existing_ref = self.get_order_by_client_id(client_order_id)
            if existing_ref is not None:
                _alog("BROKER",
                      f"Robinhood SUBMIT idempotent hit: cid={client_order_id[:24]} "
                      f"ref_id={ref_id} order_id={existing_ref.broker_order_id} — "
                      f"returning existing order", "cyan")
                # 2026-04-30 MED #26: if RH has the order (queued/confirmed/
                # unconfirmed/etc.) but our WAL is still at 'intent' (e.g.,
                # a crash between RH POST ack and our mark_submitted), advance
                # WAL through 'submitted' AND start tracking so the polling
                # loop detects its terminal transition. Without this, WAL
                # was stuck at 'intent' forever and the order's fill was
                # invisible until manual reconcile.
                try:
                    _wal_rec = self._wal.get(client_order_id)
                    _wal_state = getattr(_wal_rec, "state", None) if _wal_rec else None
                    if _wal_state == "intent" and existing_ref.broker_order_id:
                        self._wal.mark_submitted(
                            client_order_id, existing_ref.broker_order_id
                        )
                        # Track for polling-fill detection. We don't have
                        # the full RH order dict here, so synthesize from
                        # the OrderRef.
                        _synth_for_track = {
                            "id": existing_ref.broker_order_id,
                            "state": existing_ref.status or "",
                            "ref_id": ref_id,
                            "symbol": symbol,
                            "side": side_lc,
                        }
                        self._track_order(_synth_for_track, client_order_id=client_order_id)
                except Exception:
                    pass
                return existing_ref
        except BrokerError as _pre_e:
            # Bug-check round 2 (codex BLOCKER #1): FAIL CLOSED on transient
            # pre-submit lookup failure. Previously this was a swallow-and-
            # proceed pattern (matching AlpacaAdapter), but Alpaca relies on
            # documented server-side cid uniqueness; Robinhood's ref_id
            # dedup is undocumented. Skipping the submit avoids a worst-case
            # double-buy on a 5xx blip; the strategy will retry next bar
            # with the same cid → next pre-check might succeed.
            self._wal.mark_rejected(
                client_order_id,
                f"pre-submit-transient: {_pre_e}",
            )
            raise BrokerError(
                f"submit_order: pre-submit RH lookup transient failure for "
                f"cid={client_order_id[:24]}: {_pre_e}. Skipping this attempt; "
                f"strategy will retry next bar."
            ) from _pre_e

        # Pre-submit account-state preflight (broker-side will reject anyway,
        # but failing fast saves a round-trip).
        #
        # 2026-04-30 HIGH #8 (fail-closed) + MED #31 (tighter PDT):
        #  - Use RH's authoritative ``pattern_day_trader`` flag rather than
        #    deriving from equity (so equity==0 from a transient portfolio
        #    fetch failure no longer skips PDT enforcement).
        #  - Tighter day-trade detection: only block a buy when there is an
        #    *earlier-same-day buy followed by a sell* on the symbol (the
        #    pattern that opens a day-trade slot). The previous heuristic
        #    blocked any buy after a same-day sell, false-positive-ing on
        #    legitimate rebuy-after-stop-out where the sold position was
        #    opened on a prior day.
        try:
            summary = self._client.get_account_summary(self._account_number)
            if summary.get("account_blocked"):
                raise AssetNotTradable(
                    f"Robinhood account {self._account_number} is restricted/locked."
                )
            if summary.get("trading_blocked"):
                # Still allow sells (only_position_closing_trades flag)
                if side_lc == "buy":
                    raise AssetNotTradable(
                        f"Robinhood account is in position-closing-only mode; cannot buy."
                    )
            _eq = float(summary.get("equity", 0.0) or 0.0)
            _pdt_flagged = bool(summary.get("pattern_day_trader"))
            _dt_count = int(summary.get("day_trade_count", 0) or 0)
            # PDT trigger: RH-authoritative flag set, OR sub-$25k AND already
            # at the 3-day-trade limit. The latter only fires when equity is
            # known (>0) so a transient summary glitch with equity=0 doesn't
            # falsely trip; pdt_flagged is checked unconditionally because
            # it's not derived from equity.
            _pdt_triggered = _pdt_flagged or (
                _eq > 0.0 and _eq < 25000.0 and _dt_count >= 3
            )
            if _pdt_triggered and side_lc == "buy":
                # Tighter heuristic: walk _trades for today's sequence on
                # this symbol. Only block when buy → sell happened earlier
                # today (i.e., a day-trade slot was already used and this
                # buy would re-open it).
                opened_today = False
                sold_after_open_today = False
                try:
                    from zoneinfo import ZoneInfo as _ZI
                    _ny_tz = _ZI("America/New_York")
                    _today_ny = datetime.now(timezone.utc).astimezone(_ny_tz).date()
                except Exception:
                    _ny_tz = None
                    _today_ny = None
                with self._lock:
                    for _t in self._trades:
                        if str(_t.get("ticker") or "").upper() != symbol:
                            continue
                        _ts = _t.get("timestamp")
                        if _ts is None or _today_ny is None or _ny_tz is None:
                            continue
                        try:
                            _ts_date_ny = _ts.astimezone(_ny_tz).date()
                        except Exception:
                            continue
                        if _ts_date_ny != _today_ny:
                            continue
                        _act = (_t.get("action") or "").lower()
                        if _act == "buy":
                            opened_today = True
                        elif _act == "sell" and opened_today:
                            sold_after_open_today = True
                if sold_after_open_today:
                    raise PDTRestricted(
                        f"PDT restriction: pattern_day_trader={_pdt_flagged}, "
                        f"equity=${_eq:.0f}, day_trade_count={_dt_count}, "
                        f"already day-traded {symbol} today (buy→sell observed); "
                        f"this re-buy would re-open a day-trade slot. "
                        f"Wait until next trading day, or fund account to $25k+ to lift PDT."
                    )
        except (AssetNotTradable, PDTRestricted):
            raise
        except Exception as e:
            # 2026-04-30 HIGH #8: when the operator opts in via
            # RH_FAIL_CLOSED_PDT=true, refuse on a missing-summary path
            # rather than soft-bypass. Default behavior is unchanged
            # (yellow log + continue) so transient RH outages don't grind
            # all live submissions to a halt — but paranoid setups can
            # opt for fail-closed semantics.
            _fc = (os.environ.get("RH_FAIL_CLOSED_PDT", "") or "").strip().lower()
            if _fc in ("1", "true", "yes"):
                self._wal.mark_rejected(
                    client_order_id,
                    f"preflight-summary-failed: {type(e).__name__}: {e}",
                )
                raise BrokerError(
                    f"submit_order: preflight summary unavailable "
                    f"(RH_FAIL_CLOSED_PDT=true): {type(e).__name__}: {e}"
                )
            _alog("BROKER",
                  f"Robinhood preflight summary failed (continuing without gate): {e}",
                  "yellow")

        self._wal.record_intent(client_order_id, symbol, side_lc, qty, notional)

        # Resolve qty when only notional was given. RH doesn't natively
        # accept a notional buy in build_order_payload_equity — we convert
        # using the latest ask/bid quote. Round to 6 decimals.
        if qty is None and notional is not None and notional > 0:
            try:
                quote = self._client.get_latest_quote(symbol)
                px_for_calc = self._client._price_from_quote_side(quote, side_lc)
                if px_for_calc is None or px_for_calc <= 0:
                    raise BrokerError(
                        f"{symbol}: cannot resolve quote price to convert "
                        f"notional ${notional:.2f} to qty"
                    )
                qty = round(float(notional) / float(px_for_calc), 6)
            except Exception as _qerr:
                self._wal.mark_rejected(client_order_id, str(_qerr))
                raise BrokerError(f"qty resolution failed for {symbol}: {_qerr}")

        if qty is None or qty <= 0:
            self._wal.mark_rejected(client_order_id, "qty is None or <= 0")
            raise BrokerError(f"submit_order: qty must be > 0 (got {qty})")

        # 2026-04-30 Task D-revision: pre-submit ONLY checks cached
        # fractionable flag. If cached False (populated by a previous
        # rejection), floor before submit to skip a known-doomed POST.
        # Otherwise submit optimistically — the rejection handler below
        # catches RobinhoodFractionalNotAllowed and retries with floor.
        _rh_frac_cached = self._is_rh_fractionable(symbol)
        if qty is not None and qty > 0 and qty != int(qty) and _rh_frac_cached is False:
            _floored_pre = float(int(qty))
            if _floored_pre < 1.0:
                self._wal.mark_rejected(
                    client_order_id,
                    f"qty<1 after fractional-floor (qty={qty:.4f}, cached non-fractionable {symbol})",
                )
                _alog("BROKER",
                      f"SKIP {side_lc} {symbol} — qty={qty:.4f} floored to 0 "
                      f"(cached non-fractionable, cash too small for 1 share)",
                      "yellow")
                self._fire_reject_alert(
                    symbol, side_lc, "FractionalNotAllowed",
                    f"floored qty={qty:.4f} -> 0 (cached non-fractionable)",
                    client_order_id,
                )
                raise FractionalNotAllowed(
                    f"{symbol} non-fractionable (cached); floored qty={qty:.4f} -> 0"
                )
            _alog("BROKER",
                  f"Pre-submit floor for cached non-fractionable {symbol}: "
                  f"{qty:.4f} -> {_floored_pre:.0f}",
                  "cyan")
            qty = _floored_pre

        # Resolve instrument URL.
        instrument_url = None
        try:
            instrument_url = self._client.find_instrument_url_by_symbol(symbol)
        except Exception:
            pass
        if not instrument_url:
            self._wal.mark_rejected(client_order_id, f"{symbol} not found at Robinhood")
            raise AssetNotTradable(f"{symbol} not found on Robinhood")

        # tif normalization. RH uses gfd/gtc/ioc/opg.
        tif_map = {"day": "gfd", "gfd": "gfd", "gtc": "gtc", "ioc": "ioc"}
        rh_tif = tif_map.get((tif or "").lower(), "gtc")

        _qty_part = f"qty={qty}" if qty is not None else "qty=-"
        _notional_part = (
            f"notional=${notional:.2f}" if (notional is not None and qty is None) else "notional=-"
        )
        _alog(
            "BROKER",
            (
                f"Robinhood SUBMIT{' (DRY)' if self._dry_run else ''}: "
                f"side={side_lc} symbol={symbol} {_qty_part} {_notional_part} "
                f"type={order_type_lc} tif={rh_tif} "
                f"ext_hours={extended_hours} "
                f"limit={('$%.2f' % limit_price) if limit_price is not None else '-'} "
                f"cid={client_order_id[:24]} ref_id={ref_id}"
            ),
            "cyan",
        )

        if self._dry_run:
            return self._dry_run_submit(
                symbol=symbol, side=side_lc, qty=qty, notional=notional,
                order_type=order_type_lc, limit_price=limit_price, tif=rh_tif,
                client_order_id=client_order_id, ref_id=ref_id,
            )

        # 2026-04-30 inter-order delay: sleep BEFORE the real submit so
        # consecutive RH order POSTs are spaced by a random interval
        # within [_inter_order_delay_min, _inter_order_delay_max].
        # Skipped on the FIRST submit of the adapter's lifetime
        # (_last_submit_ts == 0). If the strategy already burned the
        # target time computing/IO between submits, no extra sleep.
        #
        # 2026-04-30 HIGH #9: cap the wait to time-left-in-session for
        # ``gfd`` orders so a 25-45 s sleep doesn't push a market order
        # past the 16:00 ET close. If even after capping the wait would
        # still cross close, raise — the strategy can decide to wait for
        # next open (gtc) or skip.
        # 2026-04-30 HIGH #21: use _halt_event.wait() instead of
        # time.sleep() so cancel_all_open_orders' kill-switch can preempt
        # an in-flight delay (the previous time.sleep blocked halt for up
        # to 45 s).
        if (
            self._last_submit_ts > 0
            and self._inter_order_delay_max > 0
            and self._inter_order_delay_min >= 0
        ):
            try:
                _target_delay = random.uniform(
                    min(self._inter_order_delay_min, self._inter_order_delay_max),
                    max(self._inter_order_delay_min, self._inter_order_delay_max),
                )
                _elapsed = time.time() - self._last_submit_ts
                _wait = _target_delay - _elapsed
                if _wait > 0 and (rh_tif == "gfd" and not bool(extended_hours)):
                    try:
                        from live_calendar import next_close_utc as _nc_utc
                        _now = datetime.now(timezone.utc)
                        _close = _nc_utc(_now, extended=False)
                        if _close is not None:
                            _safety_margin = 30.0  # don't fire within 30s of close
                            _max_wait = (_close - _now).total_seconds() - _safety_margin
                            if _max_wait <= 0:
                                self._wal.mark_rejected(
                                    client_order_id,
                                    f"market-close-imminent: gfd order would arrive after 16:00 ET close",
                                )
                                raise BrokerError(
                                    f"submit_order: gfd order for {symbol} would arrive after "
                                    f"market close (now={_now.isoformat()} close={_close.isoformat()}); "
                                    f"strategy should retry next session or use gtc tif."
                                )
                            if _wait > _max_wait:
                                _alog(
                                    "BROKER",
                                    f"RH inter-order delay capped: would-be {_wait:.1f}s -> "
                                    f"{_max_wait:.1f}s (close in {(_close - _now).total_seconds():.0f}s)",
                                    "yellow",
                                )
                                _wait = _max_wait
                    except BrokerError:
                        raise
                    except Exception:
                        # Calendar unavailable — fall through and just sleep.
                        pass

                if _wait > 0:
                    _alog(
                        "BROKER",
                        f"RH inter-order delay: sleeping {_wait:.1f}s "
                        f"(target={_target_delay:.1f}s, elapsed={_elapsed:.1f}s) "
                        f"before {side_lc} {symbol}",
                        "cyan",
                    )
                    # _halt_event.wait returns True if event was set (preempt),
                    # False if timeout reached. On preempt: raise so the kill-
                    # switch is observable (the WAL row stays at 'intent' which
                    # reconcile_wal_with_broker handles on next boot).
                    if self._halt_event.wait(timeout=_wait):
                        raise BrokerError(
                            f"submit_order: halt requested during inter-order delay; "
                            f"aborting submit for {symbol}"
                        )
            except BrokerError:
                raise
            except Exception:
                pass

        # Real submit
        try:
            order = self._client.place_order_equity(
                account_url=self._account_url,
                instrument_url=instrument_url,
                symbol=symbol,
                side=side_lc,
                order_type=order_type_lc,
                quantity=qty,
                limit_price=limit_price,
                time_in_force=rh_tif,
                extended_hours=bool(extended_hours),
                market_hours=market_hours,
                ref_id=ref_id,
            )
        except RobinhoodAPIError as e:
            # Ambiguous: submit raised — but RH may have still accepted. Query
            # by ref_id BEFORE surfacing the error (mirrors AlpacaAdapter).
            try:
                existing_dict = self._client.find_order_by_ref_id(ref_id)
                if existing_dict:
                    _alog(
                        "BROKER",
                        f"Robinhood ACCEPTED (recovered): symbol={symbol} "
                        f"order_id={existing_dict.get('id', '-')} "
                        f"(submit raised {type(e).__name__} but RH actually recorded it)",
                        "green",
                    )
                    self._wal.mark_submitted(client_order_id, str(existing_dict.get("id") or ""))
                    self._track_order(existing_dict, client_order_id=client_order_id)
                    # Burst-poll: even though our local POST raised, RH
                    # has the order — track its terminal transition fast.
                    self._last_submit_ts = time.time()
                    self._burst_poll_until = time.time() + self._burst_poll_window_sec
                    self._fire_submit_alert(symbol, side_lc, qty, notional,
                                            client_order_id, str(existing_dict.get("id") or ""),
                                            order_type_lc, extended_hours)
                    return self._to_orderref(existing_dict)
            except Exception:
                pass

            broker_err = _map_rh_error_to_broker(e)

            # 2026-04-30 Task D-revision: try-then-floor for fractional
            # rejection. If RH rejected with RobinhoodFractionalNotAllowed
            # and qty was fractional, populate cache, floor to int, retry.
            if (
                isinstance(broker_err, FractionalNotAllowed)
                and qty is not None
                and qty > 0
                and qty != int(qty)
            ):
                # Populate cache so future submits for this symbol skip
                # the failed POST and floor immediately.
                self._rh_frac_cache[str(symbol).strip().upper()] = (False, 0.0)
                _floored_retry = float(int(qty))
                if _floored_retry < 1.0:
                    self._wal.mark_rejected(
                        client_order_id,
                        f"qty<1 after fractional-floor (qty={qty:.4f}, non-fractionable {symbol})",
                    )
                    _alog(
                        "BROKER",
                        f"REJECTED {side_lc} {symbol} — qty={qty:.4f} floored to 0 "
                        f"(non-fractionable, cash too small for 1 share)",
                        "red",
                    )
                    self._fire_reject_alert(
                        symbol, side_lc, "FractionalNotAllowed",
                        f"floored qty={qty:.4f} -> 0 (non-fractionable)",
                        client_order_id,
                    )
                    raise FractionalNotAllowed(
                        f"{symbol} non-fractionable; floored qty={qty:.4f} -> 0"
                    )
                _alog(
                    "BROKER",
                    f"Robinhood rejected fractional {symbol} qty={qty:.4f}; "
                    f"retrying with whole shares qty={_floored_retry:.0f}",
                    "yellow",
                )
                # 2026-04-30 D''-retry-alerts: Discord visibility for the
                # retry transition. Fire BEFORE qty=_floored_retry so we
                # still have the original fractional qty in scope.
                self._fire_retry_alert(
                    symbol=symbol,
                    side=side_lc,
                    original_qty=qty,
                    retry_qty=_floored_retry,
                    reason_class=type(e).__name__,
                    reason_text=str(broker_err),
                    cid=client_order_id,
                )
                qty = _floored_retry
                try:
                    order = self._client.place_order_equity(
                        account_url=self._account_url,
                        instrument_url=instrument_url,
                        symbol=symbol,
                        side=side_lc,
                        order_type=order_type_lc,
                        quantity=qty,
                        limit_price=limit_price,
                        time_in_force=rh_tif,
                        extended_hours=bool(extended_hours),
                        market_hours=market_hours,
                        ref_id=ref_id,
                    )
                except RobinhoodAPIError as e2:
                    broker_err2 = _map_rh_error_to_broker(e2)
                    self._wal.mark_rejected(client_order_id, str(broker_err2))
                    _alog(
                        "BROKER",
                        f"Robinhood REJECTED retry: symbol={symbol} side={side_lc} "
                        f"cid={client_order_id[:24]} reason=\"{type(e2).__name__}: {e2}\"",
                        "red",
                    )
                    self._fire_reject_alert(
                        symbol, side_lc, type(e2).__name__, str(e2), client_order_id,
                    )
                    raise broker_err2
                # 2026-04-30 HIGH #10: stamp _last_submit_ts on retry success
                # too. Previously only the OUTER success block stamped it, so
                # if the retry succeeded the next caller's elapsed-since-last
                # measurement used the original attempt time → could skip the
                # next pacing window entirely.
                self._last_submit_ts = time.time()
                # Retry succeeded — fall through to mark_submitted +
                # alert_submit flow below.
            else:
                # Non-fractional rejection — original behavior.
                self._wal.mark_rejected(client_order_id, str(broker_err))
                _alog(
                    "BROKER",
                    f"Robinhood REJECTED: symbol={symbol} side={side_lc} "
                    f"cid={client_order_id[:24]} reason=\"{type(e).__name__}: {e}\"",
                    "red",
                )
                self._fire_reject_alert(symbol, side_lc, type(e).__name__, str(e), client_order_id)
                raise broker_err

        order_id = str(order.get("id") or "")
        if not order_id:
            self._wal.mark_rejected(client_order_id, f"RH returned no order id: {order}")
            raise BrokerError(f"Robinhood place_order_equity returned no id: {order}")

        self._wal.mark_submitted(client_order_id, order_id)
        with self._lock:
            self._orders_today.setdefault(symbol, set()).add(side_lc)
        # 2026-04-30 inter-order delay: stamp the successful-submit
        # timestamp so the NEXT submit knows when the last one landed.
        self._last_submit_ts = time.time()
        # 2026-04-30 burst-poll enhancement: poll the broker more
        # aggressively for the next ``_burst_poll_window_sec`` seconds so
        # fill-detection latency drops from the steady-state ~60 s to
        # ~5 s for the immediate post-submit window. Reverts automatically.
        self._burst_poll_until = time.time() + self._burst_poll_window_sec
        self._track_order(order, client_order_id=client_order_id)

        _alog(
            "BROKER",
            f"Robinhood ACCEPTED: symbol={symbol} side={side_lc} "
            f"order_id={order_id} state={order.get('state', '-')} qty={qty}",
            "green",
        )
        self._fire_submit_alert(symbol, side_lc, qty, notional, client_order_id,
                                 order_id, order_type_lc, extended_hours)
        return self._to_orderref(order)

    def _dry_run_submit(
        self, *, symbol: str, side: str, qty: float, notional: Optional[float],
        order_type: str, limit_price: Optional[float], tif: str,
        client_order_id: str, ref_id: str,
    ) -> OrderRef:
        """Synthetic order: skip the HTTP POST, populate _trades + _positions
        as if the order filled at the current quote mid, and return a
        synthetic OrderRef. Used when RH_DRY_RUN=true.
        """
        try:
            quote = self._client.get_latest_quote(symbol)
            fill_px = (
                limit_price
                if (order_type == "limit" and limit_price is not None)
                else self._client._price_from_quote_side(quote, side)
            ) or 0.0
            fill_px = float(fill_px) if fill_px else 0.0
        except Exception:
            fill_px = float(limit_price or 0.0)
        if fill_px <= 0:
            self._wal.mark_rejected(client_order_id, "dry-run: cannot resolve fill price")
            raise BrokerError(f"DRY-RUN cannot resolve fill price for {symbol}")

        synth_order_id = f"dry-{uuid.uuid4().hex[:12]}"
        self._wal.mark_submitted(client_order_id, synth_order_id)
        # Seed reverse-lookup cache so cancel_order / _cid_for_broker_order_id
        # can resolve cid ↔ broker_order_id without a WAL scan.
        with self._lock:
            self._oid_to_cid[synth_order_id] = client_order_id
        # Synthetic immediate fill
        self._wal.mark_filled(client_order_id, qty, fill_px)
        with self._lock:
            self._orders_today.setdefault(symbol, set()).add(side)
            cur = float(self._positions.get(symbol, 0.0) or 0.0)
            if side == "buy":
                self._positions[symbol] = cur + qty
                self._cash -= qty * fill_px
            else:
                self._positions[symbol] = max(0.0, cur - qty)
                self._cash += qty * fill_px
            self._last_prices[symbol] = fill_px
            self._trades.append({
                "timestamp": datetime.now(timezone.utc),
                "action": side,
                "ticker": symbol,
                "shares": qty,
                "price": fill_px,
                "total": qty * fill_px,
                "cash_after": self._cash,
                "_dry_run": True,
            })

        _alog(
            "BROKER",
            f"Robinhood DRY-RUN FILL: {side} {qty} {symbol} @ ${fill_px:.2f} "
            f"= ${qty * fill_px:.2f} cid={client_order_id[:24]}",
            "yellow",
        )
        # Discord alert (still fire so operator sees the synthetic flow)
        self._fire_submit_alert(symbol, side, qty, notional, client_order_id,
                                 synth_order_id, order_type, False)
        self._fire_fill_alert(symbol, side, qty, fill_px, client_order_id, synth_order_id)

        return OrderRef(
            broker_order_id=synth_order_id,
            client_order_id=ref_id,
            symbol=symbol,
            side=side,
            qty=qty,
            status="filled",
            filled_qty=qty,
            filled_avg_price=fill_px,
            submitted_at_utc=datetime.now(timezone.utc),
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """2026-04-30 MED #22: log on exception (kill-switch flow needs to
        observe failure), surface auth-class errors as BrokerError so the
        caller can react instead of treating silent False as "already
        canceled".
        """
        if not broker_order_id:
            return True
        if broker_order_id.startswith("dry-"):
            cid = self._cid_for_broker_order_id(broker_order_id)
            if cid:
                try:
                    self._wal.mark_canceled(cid)
                except Exception:
                    pass
            return True
        try:
            ok = bool(self._client.cancel_order(order_id=broker_order_id))
        except Exception as e:
            _alog(
                "BROKER",
                f"Robinhood cancel_order({broker_order_id}) failed: "
                f"{type(e).__name__}: {e}",
                "red",
            )
            # Surface auth-class as BrokerError so kill-switch sees a real
            # failure rather than False==already-canceled.
            try:
                from robinhood_engine import (
                    RobinhoodAPIError,
                    RobinhoodMFARequired,
                    RobinhoodChallengeRequired,
                )
                if isinstance(e, (RobinhoodMFARequired, RobinhoodChallengeRequired)):
                    raise BrokerMFARequired(str(e)) from e
                if isinstance(e, RobinhoodAPIError) and e.status_code in (401, 403):
                    raise BrokerError(
                        f"cancel_order auth failure: {type(e).__name__}: {e}"
                    ) from e
            except (BrokerError, BrokerMFARequired):
                raise
            except Exception:
                pass
            return False
        if ok:
            cid = self._cid_for_broker_order_id(broker_order_id)
            if cid:
                try:
                    self._wal.mark_canceled(cid)
                except Exception:
                    pass
        return ok

    def cancel_all_open_orders(self) -> int:
        """2026-04-30 MED #21: signal _halt_event so any in-flight submit
        sleeping inside the inter-order delay aborts immediately. Returns
        the number of orders canceled. MED #32: in dry-run, walk the WAL
        and mark_canceled instead of returning 0 (which left zombie WAL
        rows from synthesized dry-run orders).
        """
        # Signal any in-flight submit to abort the inter-order-delay sleep.
        self._halt_event.set()
        try:
            if self._dry_run:
                count = 0
                try:
                    open_records = self._wal.list_open()
                except Exception:
                    open_records = []
                for rec in open_records:
                    cid = getattr(rec, "client_order_id", None) or rec.get("client_order_id")
                    if not cid:
                        continue
                    try:
                        self._wal.mark_canceled(cid)
                        count += 1
                    except Exception:
                        continue
                _alog("BROKER", f"Robinhood DRY-RUN cancel_all: WAL-marked {count} cid(s) canceled", "yellow")
                return count
            try:
                return int(self._client.cancel_all_open_orders(account_number=self._account_number))
            except Exception as e:
                _alog(
                    "BROKER",
                    f"Robinhood cancel_all_open_orders failed: {type(e).__name__}: {e}",
                    "red",
                )
                return 0
        finally:
            # Clear the event so future submits don't see a stuck halt.
            # Operators who want a permanent halt should rely on
            # stop_trade_updates (broker-process exit).
            self._halt_event.clear()

    def get_order(self, broker_order_id: str) -> OrderRef:
        if broker_order_id.startswith("dry-"):
            return OrderRef(
                broker_order_id=broker_order_id,
                client_order_id="",
                symbol="",
                side="",
                qty=0.0,
                status="filled",
            )
        try:
            o = self._client.get_order(broker_order_id)
            return self._to_orderref(o)
        except Exception as e:
            raise BrokerError(f"get_order failed: {e}") from e

    def get_order_by_client_id(self, client_order_id: str) -> Optional[OrderRef]:
        """Return OrderRef if RH has this cid (via deterministic ref_id), or
        None if truly absent. Raises BrokerError on transient failures so
        the pre-submit idempotency check can distinguish "not at broker"
        from "unknown — try again later" and fail-closed on the latter.

        2026-04-30 MED #25: when the existing order is in a terminal-failure
        state (rejected / cancelled / expired), return None so a fresh
        submit re-attempts. Without this, a previously-rejected order
        with the same ref_id was returned as a "successful idempotent hit"
        and the strategy thought the order was placed.
        """
        if not client_order_id:
            return None
        ref_id = self._ref_id_from_cid(client_order_id)
        try:
            # Bug-check round 2 (codex BLOCKER #1): raise_on_transient=True
            # so a 5xx during pre-submit lookup propagates as BrokerError
            # rather than getting swallowed as None (which would let
            # submit_order proceed and risk double-buy if RH actually has
            # the order but couldn't tell us in time).
            existing = self._client.find_order_by_ref_id(ref_id, raise_on_transient=True)
        except Exception as e:
            raise BrokerError(f"transient RH query failure for {client_order_id}: {e}") from e
        if not existing:
            return None
        _state = (existing.get("state") or "").strip().lower()
        if _state in ("rejected", "failed", "cancelled", "canceled", "expired"):
            # Terminal failure — caller should re-attempt with a fresh submit.
            # Note: RH will reject duplicate ref_id submissions; that's the
            # signal for the ambiguity-recovery path at submit_order's
            # exception handler to look up the existing record.
            return None
        return self._to_orderref(existing)

    def list_open_orders(self, limit: int = 200) -> list[OrderRef]:
        try:
            orders = self._client.list_orders(limit=limit, state="open",
                                               account_number=self._account_number)
        except Exception:
            return []
        return [self._to_orderref(o) for o in orders]

    # ------------------------------------------------------------------
    # Per-day order cache (Fix B parity)

    def refresh_orders_today(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        try:
            # Fetch a generous window — RH paginates by 100, we cap at 500.
            orders = self._client.list_orders(limit=500, account_number=self._account_number)
        except Exception as e:
            # Bug-check fix #9 (MUST-FIX): preserve the previous cache on a
            # transient RH outage. Wiping to {} would let the buy-loop
            # re-attempt symbols already ordered today and trip the broker's
            # "duplicate" rejections (or worse — bypass them and double-buy
            # if cid generation drifted).
            _alog(
                "BROKER",
                f"refresh_orders_today (RH) failed: {e} — preserving prior cache",
                "yellow",
            )
            with self._lock:
                return dict(self._orders_today)
        # 2026-04-30 MED #28: cutoff at NY midnight (not UTC midnight). After
        # 20:00 ET, "today" in NY is "yesterday" in UTC for several hours so
        # the previous UTC-midnight cutoff dropped same-NY-day orders out of
        # the cache, allowing duplicate same-day attempts.
        try:
            from zoneinfo import ZoneInfo as _ZI
            _ny_tz = _ZI("America/New_York")
            _now_ny = datetime.now(timezone.utc).astimezone(_ny_tz)
            _ny_midnight = _now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff = _ny_midnight.astimezone(timezone.utc)
        except Exception:
            cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for o in orders:
            try:
                state = (o.get("state") or "").strip().lower()
                if state in ("rejected", "canceled", "cancelled", "expired", "failed"):
                    continue
                ts_raw = o.get("created_at") or o.get("updated_at") or ""
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                inst_url = (o.get("instrument") or "").strip()
                sym = (o.get("symbol") or "").strip().upper() or self._instrument_url_to_symbol(inst_url)
                side = (o.get("side") or "").strip().lower()
                if sym and side in ("buy", "sell"):
                    out.setdefault(sym, set()).add(side)
            except Exception:
                continue
        with self._lock:
            self._orders_today = dict(out)
            try:
                from zoneinfo import ZoneInfo
                _ny = ZoneInfo("America/New_York")
                self._last_orders_refresh_ny_date = datetime.now(timezone.utc).astimezone(_ny).strftime("%Y-%m-%d")
            except Exception:
                self._last_orders_refresh_ny_date = None
        _alog(
            "BROKER",
            f"refresh_orders_today (RH): {len(out)} symbol(s) — "
            f"buys={sum(1 for s in out.values() if 'buy' in s)} "
            f"sells={sum(1 for s in out.values() if 'sell' in s)}",
            "cyan",
        )
        return out

    def maybe_rollover_orders_today(self) -> bool:
        try:
            from zoneinfo import ZoneInfo
            _ny = ZoneInfo("America/New_York")
            today_str = datetime.now(timezone.utc).astimezone(_ny).strftime("%Y-%m-%d")
        except Exception:
            return False
        if self._last_orders_refresh_ny_date == today_str:
            return False
        self.refresh_orders_today()
        return True

    def ordered_today(self, symbol: str, side: str) -> bool:
        try:
            with self._lock:
                cache = getattr(self, "_orders_today", {}) or {}
            return side.lower() in (cache.get(str(symbol).upper()) or set())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # State refresh

    def refresh_positions(self, *, force: bool = False) -> list[PositionDTO]:
        """REST refresh. Build new dict outside the lock then atomically rebind
        (mirrors AlpacaAdapter pattern for thread-safety with the polling
        loop / strategy reads).

        2026-05-05 third pass: snapshot strictly cache-only. When
        ``force=False`` (snapshot worker, every 3s), return whatever the
        cache holds — populated by the prior successful ``force=True`` call
        from broker.py's hourly pre-cycle hook. NEVER call HTTP from
        force=False. Empty list if cache cold (broker returns empty UI
        state until first hourly pre-cycle succeeds — strictly correct
        per user directive: snapshot doesn't hit RH, only the hourly
        cycle does).
        """
        if not force:
            return (
                list(self._last_positions_dtos)
                if self._last_positions_dtos is not None
                else []
            )
        try:
            positions = self._client.get_positions(
                account_number=self._account_number, nonzero=True,
            )
        except Exception as e:
            _alog("BROKER", f"refresh_positions (RH) failed: {e} — keeping cache", "yellow")
            with self._lock:
                if self._positions_stale_since is None:
                    self._positions_stale_since = time.time()
            return []
        new_positions: dict[str, float] = {}
        new_last_prices: dict[str, float] = {}
        dtos: list[PositionDTO] = []
        symbols_for_quotes: list[str] = []
        rows_by_symbol: dict[str, dict] = {}
        for p in positions:
            try:
                qty = float(p.get("quantity") or 0.0)
            except Exception:
                qty = 0.0
            if qty <= 0:
                continue
            inst_url = (p.get("instrument") or "").strip()
            sym = self._instrument_url_to_symbol(inst_url)
            if not sym:
                continue
            new_positions[sym] = qty
            symbols_for_quotes.append(sym)
            rows_by_symbol[sym] = p
        # Quote batch for last_prices + market_value
        prices: dict[str, float] = {}
        if symbols_for_quotes:
            try:
                prices = self._rh_get_live_prices(symbols_for_quotes) or {}
            except Exception:
                prices = {}
        for sym, qty in new_positions.items():
            p = rows_by_symbol.get(sym, {})
            try:
                avg_buy = float(p.get("average_buy_price") or 0.0)
            except Exception:
                avg_buy = 0.0
            last_px = float(prices.get(sym, 0.0) or 0.0)
            if last_px > 0:
                new_last_prices[sym] = last_px
            mv = qty * last_px if last_px > 0 else qty * avg_buy
            dtos.append(PositionDTO(
                symbol=sym,
                qty=qty,
                avg_entry_price=avg_buy,
                market_value=mv,
            ))
        with self._lock:
            # 2-A (bug-sweep 2026-05-28): in clean_room_mode the boot-time
            # classifier split broker positions into strategy-owned vs external
            # (quarantined). A naive rebind to the full broker set here would
            # silently RE-ADOPT the quarantined external shares on the warm-boot
            # force probe (broker.py) and every post-fill refresh, defeating the
            # split before the strategy even runs. Enforce the quarantine on every
            # rebind: owned_qty = broker_qty - external_qty. This correctly handles
            # fully-external (owned 0), partial-external (owned = broker - external),
            # fully-owned (external 0), and brand-new strategy buys (external 0 ->
            # full qty owned). Legacy (non-clean-room) path is byte-for-byte unchanged.
            # 2-A (bug-sweep 2026-05-28): in clean_room_mode the boot-time
            # classifier split broker positions into strategy-owned vs external
            # (quarantined). A naive rebind to the full broker set here would
            # silently RE-ADOPT the quarantined external shares on the warm-boot
            # force probe (broker.py) and every post-fill refresh, defeating the
            # split before the strategy even runs. Enforce the quarantine on every
            # rebind: owned_qty = broker_qty - external_qty. This correctly handles
            # fully-external (owned 0), partial-external (owned = broker - external),
            # fully-owned (external 0), and brand-new strategy buys (external 0 ->
            # full qty owned). Legacy (non-clean-room) path is byte-for-byte unchanged.
            #
            # NOTE (Scope D): a reconcile that anchored owned on self._positions was
            # tried and REVERTED — RobinhoodAdapter._handle_order_transition does NOT
            # update self._positions on live fills (only the RH_DRY_RUN path does), so
            # anchoring on it stranded every post-boot real buy as external. The
            # operator-partial-sell-of-an-external-lot edge (owned mis-charged) remains
            # a known latent MEDIUM; the correct fix is RH fill-level position tracking.
            if self._clean_room_mode:
                owned_new: dict[str, float] = {}
                for _sym, _bqty in new_positions.items():
                    _ext = float((self._external_positions.get(_sym) or {}).get("qty", 0.0) or 0.0)
                    _ext = max(0.0, _ext)
                    _owned_qty = float(_bqty) - _ext
                    if _owned_qty > 1e-6:
                        owned_new[_sym] = _owned_qty
                # Keep the external quarantine current: drop entries the broker no
                # longer shows (operator flattened) and clamp qty down if the
                # broker now shows fewer shares than the boot snapshot.
                for _sym in list(self._external_positions.keys()):
                    if _sym not in new_positions:
                        self._external_positions.pop(_sym, None)
                    else:
                        _row = self._external_positions[_sym]
                        _row["qty"] = min(
                            float(_row.get("qty", 0.0) or 0.0), float(new_positions[_sym])
                        )
                self._positions = owned_new
            else:
                self._positions = new_positions
            self._last_prices.update(new_last_prices)
            self._positions_stale_since = None
        # Update TTL cache so subsequent non-force callers (snapshot every 3s)
        # hit the in-memory copy until the dual-cadence cycle next refreshes.
        self._last_positions_dtos = list(dtos)
        self._last_positions_refresh_ts = time.time()
        return dtos

    def refresh_cash(self) -> CashDTO:
        """2026-04-30 MED #29: cache last successful CashDTO and return it
        on transient failure. ``_cash`` continues to mirror ``buying_power``
        (the right "available to spend" value for RH cash accounts where
        settled-cash excludes still-clearing deposits); ``_settled_cash``
        tracks the literal cash field separately.
        """
        try:
            summary = self._client.get_account_summary(self._account_number)
        except Exception as e:
            _alog("BROKER", f"refresh_cash (RH) failed: {e} — using cached", "yellow")
            if self._last_cash_dto is not None:
                return self._last_cash_dto
            return CashDTO(
                cash=self._settled_cash,
                buying_power=self._buying_power,
                daytrading_buying_power=0.0,
            )
        cash = float(summary.get("cash", 0.0))
        bp = float(summary.get("buying_power", 0.0))
        dtbp = float(summary.get("day_trade_buying_power", 0.0))
        unsettled = float(summary.get("unsettled_funds", 0.0))
        dto = CashDTO(
            cash=cash, buying_power=bp,
            daytrading_buying_power=dtbp, unsettled=unsettled,
        )
        # 1-A (bug-sweep 2026-05-28): broker.py treats _cash as available-to-spend
        # AND get_portfolio_value() = get_cash() + positions_value drives the
        # drawdown baseline. Cap at SETTLED cash so a margin/Instant account's
        # inflated buying_power never deploys non-settled credit nor inflates the
        # valuation. On a cash account settled == bp, so this is a no-op.
        available = _available_cash_for_sizing(cash, bp)
        with self._lock:
            self._cash = available
            self._buying_power = bp
            self._settled_cash = cash
            self._last_cash_dto = dto
        if bp - cash > 1.0:
            _alog(
                "BROKER",
                f"refresh_cash: buying_power ${bp:,.2f} exceeds settled cash "
                f"${cash:,.2f} (Δ ${bp - cash:,.2f}; margin/Instant). Sizing + "
                f"valuation capped to settled cash ${available:,.2f}.",
                "yellow",
            )
        return dto

    def refresh_account(self, *, force: bool = False) -> AccountDTO:
        """2026-04-30 MED #30: cache last successful AccountDTO and return
        it on transient failure (rather than fabricating a dangerous
        account_blocked=False, trading_blocked=False, equity=initial_value
        snapshot that could mislead callers into trading against a
        blocked account). If we have NO last-successful DTO we raise so
        the caller fails closed.

        2026-05-05 third pass: snapshot strictly cache-only. When
        ``force=False`` (snapshot worker), return whatever cache holds.
        If cache is cold AND not force, raise so the caller fails closed
        (mirrors the prior contract for "no last-successful DTO yet").
        Only ``force=True`` callers (broker.py hourly pre-cycle hook)
        actually make the HTTP call.
        """
        if not force:
            if self._last_account_dto is not None:
                return self._last_account_dto
            raise BrokerError(
                "refresh_account: cache cold and force=False. "
                "First successful pre-cycle (force=True) hasn't completed yet."
            )
        try:
            summary = self._client.get_account_summary(self._account_number)
        except Exception as e:
            _alog(
                "BROKER",
                f"refresh_account (RH) failed: {type(e).__name__}: {e} — "
                f"{'using cached' if self._last_account_dto else 'no cache, raising'}",
                "yellow",
            )
            if self._last_account_dto is not None:
                return self._last_account_dto
            raise BrokerError(
                f"refresh_account: RH summary unavailable and no cached "
                f"AccountDTO yet ({type(e).__name__}: {e}). Cannot guarantee "
                f"account_blocked/trading_blocked correctness."
            ) from e
        dto = AccountDTO(
            equity=float(summary.get("equity", 0.0)),
            pattern_day_trader=bool(summary.get("pattern_day_trader", False)),
            daytrade_count=int(summary.get("day_trade_count", 0)),
            account_blocked=bool(summary.get("account_blocked", False)),
            trading_blocked=bool(summary.get("trading_blocked", False)),
            buying_power=float(summary.get("buying_power", 0.0)),
            last_equity=float(summary.get("equity", 0.0)),
            cash=float(summary.get("cash", 0.0)),
        )
        self._last_account_dto = dto
        self._last_account_refresh_ts = time.time()
        return dto

    def is_market_open(self, now_utc: datetime) -> bool:
        from live_calendar import is_nyse_open_extended
        return is_nyse_open_extended(now_utc)

    def _rebuild_session(self) -> None:
        """Force-rebuild the underlying requests.Session to flush a wedged
        urllib3 connection pool. Called by broker._compute_live_state_snapshot
        after a hard-bounded adapter call timed out — the wedged conn is still
        in the OLD pool and would re-wedge any new call that picks it up.

        2026-05-05 fix: must NOT acquire self._lock. The polling thread
        scatters lock-acquisitions across ~28 sites, and if one of them is
        held during the snapshot's rebuild attempt we deadlock here while
        the snapshot watchdog times out at "wedged at: start". Pointer
        assignment in CPython is atomic under the GIL, so the lock provides
        no additional safety — drop it.

        Polling thread mid-call holds a local ref to the old session via its
        bound .request method; that call continues until the OS TCP closes the
        wedged conn (~60-180s). Once it errors out, the old session has no
        more refs and is GC'd. Until then, the OLD session and its zombie
        socket stay in memory but don't affect the NEW session's pool.
        """
        import requests as _requests
        try:
            from robinhood_engine import AUTH_DEFAULT_HEADERS as _RH_HDR
        except Exception:
            _RH_HDR = {}
        try:
            new_session = _requests.Session()
            try:
                if _RH_HDR:
                    new_session.headers.update(_RH_HDR)
            except Exception:
                pass
            # Mirror RobinhoodClient.__init__'s defense-in-depth timeout patch
            # so the new session also has a 30s default if any call site
            # forgot to pass one.
            try:
                if not getattr(new_session, "_intellistock_timeout_patched", False):
                    _orig = new_session.request
                    def _patched(method, url, *args, **kwargs):
                        if len(args) < 7:
                            kwargs.setdefault("timeout", 30)
                        return _orig(method, url, *args, **kwargs)
                    new_session.request = _patched
                    new_session._intellistock_timeout_patched = True
            except Exception:
                pass
            # Atomic pointer assignment — no lock needed (and acquiring one
            # here is a deadlock risk given polling thread's lock pattern).
            self._client.session = new_session
        except Exception as e:
            try:
                _alog("BROKER", f"_rebuild_session (RH) failed: {e}", "yellow")
            except Exception:
                pass

    def health_check(self) -> HealthStatus:
        # Token freshness: consider fresh if within last 22h (RH default 24h TTL)
        token_age = float("inf")
        try:
            if self._client.state.obtained_at_epoch:
                token_age = time.time() - int(self._client.state.obtained_at_epoch)
        except Exception:
            pass
        auth_fresh = token_age < (22 * 3600)
        errors: list[str] = []
        if not auth_fresh:
            errors.append(f"access_token age={token_age/3600:.1f}h (>22h)")
        # Phase C: tighten polling-thread liveness check — _trade_updates_connected
        # is a best-effort flag set in start_trade_updates and cleared in
        # the polling loop's finally. Cross-check against the actual thread.
        thread_alive = bool(self._stream_thread and self._stream_thread.is_alive())
        connected = bool(self._trade_updates_connected and thread_alive)
        if not connected:
            errors.append(
                f"polling thread not running (flag={self._trade_updates_connected}, alive={thread_alive})"
            )
        if self._dry_run:
            errors.append("RH_DRY_RUN=true (no real orders)")
        return HealthStatus(
            auth_fresh=auth_fresh,
            trade_updates_connected=connected,
            last_heartbeat_utc=self._last_heartbeat_utc,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Polling thread (WebSocket substitute)

    def start_trade_updates(self) -> None:
        """Start the background polling thread that detects fills and pushes
        them to WAL + _positions/_trades + Discord alerts. Replaces Alpaca's
        TradingStream WebSocket.
        """
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        self._stop_polling = False
        self._stream_thread = threading.Thread(
            target=self._polling_loop, daemon=True, name="rh-poll-fills",
        )
        self._stream_thread.start()
        self._trade_updates_connected = True
        _alog("BROKER", f"Robinhood polling fills thread started (interval={self._poll_interval_sec}s)", "green")

    def stop_trade_updates(self) -> None:
        self._stop_polling = True
        self._trade_updates_connected = False

    def _track_order(self, order: dict, client_order_id: Optional[str] = None) -> None:
        """Add an order to the polling tracker so we detect its terminal
        transition exactly once. Also seeds the broker_order_id → cid cache
        and zeros the prev_filled_qty so the first observed cumulative
        fill is treated as the first delta.
        """
        oid = str(order.get("id") or "")
        if not oid:
            return
        with self._lock:
            self._tracked_orders[oid] = dict(order)
            self._tracked_states[oid] = (order.get("state") or "").strip().lower()
            self._prev_filled_qty.setdefault(oid, 0.0)
            if client_order_id:
                self._oid_to_cid[oid] = client_order_id

    def _polling_loop(self) -> None:
        """Per-iteration: refresh tracked orders, detect transitions, fire
        alerts/WAL updates, periodically refresh cash + positions, refresh
        token if needed. Hardened so any single iteration's failure doesn't
        kill the thread; if iterations fail repeatedly we hard-fail so the
        outer try/finally fires the dead-thread alert.
        """
        try:
            self._polling_loop_inner()
            # Clean exit (stop_polling): no alert.
            self._trade_updates_connected = False
        except BaseException as e:
            # 2026-04-30 MED #17: dead-thread alert is now actually reachable.
            # The previous loop_died_unexpectedly was always overwritten with
            # False because nothing inside the inner loop raised. Now the
            # inner loop re-raises after N consecutive errors so the alert
            # path is exercised on a real failure, not just KeyboardInterrupt.
            self._trade_updates_connected = False
            if self._alert_strategy_error:
                try:
                    self._alert_strategy_error(
                        instance_id=self._instance_id,
                        tag="rh-polling-thread-died",
                        message=(
                            f"RobinhoodAdapter polling thread exited unexpectedly: "
                            f"{type(e).__name__}: {e}. Fills will NOT be detected until "
                            f"restart. Check logs."
                        ),
                    )
                except Exception:
                    pass
            raise

    def _polling_loop_inner(self) -> None:
        """Polling loop body. Raises after too many consecutive errors so
        the wrapper fires the dead-thread alert. Returns normally only on
        clean stop via ``self._stop_polling``.
        """
        last_full_refresh = 0.0
        consecutive_errors = 0
        # Hard-fail threshold — if we hit this many consecutive errors,
        # let the outer wrapper fire the dead-thread alert and exit. Set
        # high enough that a brief RH degradation doesn't kill polling
        # but a multi-hour outage does (~30 min @ 60s poll).
        HARD_FAIL_THRESHOLD = 30
        # Alert-once gate so a sustained outage doesn't spam the channel.
        alerted_recovery_threshold = False

        while not self._stop_polling:
            try:
                # Step 1: poll each tracked order until terminal
                with self._lock:
                    tracked = dict(self._tracked_orders)
                for oid, prev in list(tracked.items()):
                    try:
                        cur = self._client.get_order(oid)
                    except Exception:
                        continue
                    self._handle_order_transition(oid, prev, cur)

                # Step 2: every 60s, refresh token only. Per user directive
                # 2026-05-05: cash + positions refreshes happen ONLY before
                # the hourly dual-cadence cycle (broker.py pre-cycle hook
                # passes force=True), not on a 60s safety-net cadence. RH
                # was rate-limiting the per-session quota when both polling
                # and snapshot were hitting /accounts/ + /positions/. The
                # polling thread keeps doing get_order above (fill detection)
                # which is its primary purpose; periodic safety-net refresh
                # is dropped.
                now = time.time()
                if now - last_full_refresh > 60.0:
                    last_full_refresh = now
                    self._maybe_refresh_token()

                if consecutive_errors > 0:
                    consecutive_errors = 0
                    alerted_recovery_threshold = False

                # 2026-04-30 MED #19: bump heartbeat at END of iteration
                # so a stalled get_order/refresh_positions doesn't make the
                # thread look healthy to health_check.
                self._last_heartbeat_utc = datetime.now(timezone.utc)
            except Exception as e:
                consecutive_errors += 1
                _alog(
                    "BROKER",
                    f"RH polling loop iteration error ({consecutive_errors}/{HARD_FAIL_THRESHOLD}): {e}",
                    "yellow",
                )
                # Alert ONCE at threshold, not every-tick. Subsequent
                # alerts only at hard-fail boundary.
                if (
                    consecutive_errors == 5
                    and not alerted_recovery_threshold
                    and self._alert_strategy_error
                ):
                    try:
                        self._alert_strategy_error(
                            instance_id=self._instance_id,
                            tag="rh-polling",
                            message=(
                                f"RH polling thread has had {consecutive_errors} consecutive "
                                f"errors. Will hard-fail at {HARD_FAIL_THRESHOLD}. Last: {e}"
                            ),
                        )
                        alerted_recovery_threshold = True
                    except Exception:
                        pass
                if consecutive_errors >= HARD_FAIL_THRESHOLD:
                    raise RuntimeError(
                        f"RH polling thread hard-fail after {consecutive_errors} consecutive errors: {e}"
                    )
            # 2026-04-30 burst-poll enhancement: if we're inside the post-
            # submit burst window, sleep the short interval; otherwise the
            # steady-state interval. The window is set in submit_order
            # success paths so fill-detection latency drops from ~60s to
            # ~5s for the immediate post-submit burst.
            if time.time() < self._burst_poll_until:
                _interval = float(self._burst_poll_interval_sec or 5.0)
            else:
                _interval = float(self._poll_interval_sec or 5.0)
            time.sleep(max(0.5, _interval))

    def _handle_order_transition(self, oid: str, prev: dict, cur: dict) -> None:
        """Process an order's latest snapshot. Detects fill deltas (incl. mid-
        partial-fill increments where state stays partially_filled but
        cumulative_quantity grows) and terminal transitions.

        2026-04-30 rewrite (HIGH #4 + #5):
        - Always tracks per-oid prev_filled_qty and appends ONLY the fill
          DELTA to _trades (not the full cumulative). This eliminates the
          double-counting that previously happened when a partial-then-
          terminal sequence appended cumulative twice.
        - Decrements _cash on buy fill / increments on sell fill — mirrors
          AlpacaAdapter._on_alpaca_trade_update so the in-memory cash
          mirror stays accurate during the multi-fill window between 60s
          refresh ticks. Without this, multi-symbol burst buys can size
          off pre-trade buying-power and overspend.
        - Skips early-return when state didn't change but cumulative did
          (mid-partial-fill increments).
        - Cleans up _oid_to_cid alongside other terminal pops (fixes
          MED #18 unbounded-growth memory leak).
        """
        prev_state = (prev.get("state") or "").strip().lower()
        cur_state = (cur.get("state") or "").strip().lower()
        ref_id = (cur.get("ref_id") or "").strip()
        # Find cid by inverse-mapping is impossible (uuid5 is one-way). Look up
        # WAL entry by broker_order_id instead. ref_id is only used as a
        # last-ditch fallback for logging — never for WAL writes (LOW #34).
        cid_lookup = self._cid_for_broker_order_id(oid)
        cid = cid_lookup or ""
        symbol = (cur.get("symbol") or "").strip().upper()
        if not symbol:
            symbol = self._instrument_url_to_symbol((cur.get("instrument") or "").strip())
        side = (cur.get("side") or "").strip().lower()
        try:
            filled_qty = float(cur.get("cumulative_quantity") or 0.0)
            avg_px = float(cur.get("average_price") or 0.0) if cur.get("average_price") else 0.0
        except Exception:
            filled_qty, avg_px = 0.0, 0.0

        # Always update tracker dict so the latest cumulative_quantity is
        # visible (even when state itself didn't change).
        state_changed = cur_state != prev_state
        with self._lock:
            self._tracked_orders[oid] = cur
            if state_changed:
                self._tracked_states[oid] = cur_state

        # Compute fill delta. This works across all transitions (queued ->
        # partial, partial -> partial-with-more-filled, partial -> filled,
        # partial -> rejected/canceled/expired). Each branch then handles
        # WAL state machine + alerts; the delta accounting is centralized
        # here so we never double-count.
        prev_filled = float(self._prev_filled_qty.get(oid, 0.0) or 0.0)
        delta_qty = filled_qty - prev_filled
        if delta_qty < 0:
            # cumulative_quantity moving backwards is RH telling us nonsense;
            # log and clamp.
            _alog(
                "BROKER",
                f"RH order {oid} cumulative_quantity went backwards "
                f"(prev={prev_filled} cur={filled_qty}); ignoring delta",
                "yellow",
            )
            delta_qty = 0.0

        if delta_qty > 0 and avg_px > 0 and side in ("buy", "sell"):
            cash_delta = delta_qty * avg_px
            with self._lock:
                if side == "buy":
                    self._cash -= cash_delta
                else:
                    self._cash += cash_delta
                self._trades.append({
                    "timestamp": datetime.now(timezone.utc),
                    "action": side,
                    "ticker": symbol,
                    "shares": delta_qty,
                    "price": avg_px,
                    "total": cash_delta,
                    "cash_after": self._cash,
                    "order_id": oid,
                    # Mark partial-vs-terminal so consumers can distinguish.
                    "_partial": (cur_state == "partially_filled"),
                })
                if avg_px > 0:
                    self._last_prices[symbol] = avg_px
                self._prev_filled_qty[oid] = filled_qty
            _alog(
                "BROKER",
                f"Robinhood {'PARTIAL' if cur_state == 'partially_filled' else 'FILL'} delta: "
                f"{side} +{delta_qty:.4f} {symbol} @ ${avg_px:.2f} = ${cash_delta:.2f} "
                f"cum={filled_qty:.4f} order_id={oid}",
                "green",
            )

        # If state didn't change AND there was no new delta, nothing else
        # to do. This is the common case during queued->queued polling.
        if not state_changed:
            return

        # ----- terminal-state handling -----
        # 2026-04-30 LOW #34: skip WAL writes when cid lookup failed.
        # mark_* with empty cid silently no-ops on most stores but is
        # noisy on others; either way the state machine isn't advanced
        # for the actual WAL row, so the empty-cid path was always wrong.
        # Log a warning so the operator knows fills are landing for
        # orders we can't tie back to a cid (e.g., manually-placed orders
        # outside the adapter).
        _wal_writable = bool(cid)
        if not _wal_writable:
            _alog(
                "BROKER",
                f"RH transition for oid={oid} state={cur_state} but no cid "
                f"resolvable from cache or WAL; WAL not updated. "
                f"This usually means the order was placed outside the adapter.",
                "yellow",
            )
        if cur_state == "filled":
            if _wal_writable:
                try:
                    self._wal.mark_filled(cid, filled_qty, avg_px or None)
                except Exception:
                    pass
            with self._lock:
                self._tracked_orders.pop(oid, None)
                self._tracked_states.pop(oid, None)
                self._oid_to_cid.pop(oid, None)
                self._prev_filled_qty.pop(oid, None)
            # Trigger an authoritative position refresh OUTSIDE the lock so
            # _positions reflects the new fill within a few seconds.
            try:
                self.refresh_positions(force=True)
            except Exception:
                pass
            _alog(
                "BROKER",
                f"Robinhood TERMINAL FILL: {side} cum={filled_qty} {symbol} @ ${avg_px:.2f} = "
                f"${filled_qty*avg_px:.2f} order_id={oid}",
                "green",
            )
            # Fill alert reports the AGGREGATE order, not the last delta.
            self._fire_fill_alert(symbol, side, filled_qty, avg_px, cid, oid)
        elif cur_state == "partially_filled":
            # WAL marker if available; the delta accounting above already
            # handled cash + _trades.
            if _wal_writable:
                try:
                    _mp = getattr(self._wal, "mark_partial", None)
                    if callable(_mp):
                        _mp(cid, filled_qty, avg_px or None)
                except Exception:
                    pass
        elif cur_state in ("rejected", "failed"):
            reason = (cur.get("reject_reason") or cur.get("state") or "rejected")
            if _wal_writable:
                try:
                    self._wal.mark_rejected(cid, str(reason))
                except Exception:
                    pass
            with self._lock:
                self._tracked_orders.pop(oid, None)
                self._tracked_states.pop(oid, None)
                self._oid_to_cid.pop(oid, None)
                self._prev_filled_qty.pop(oid, None)
            if filled_qty > 0:
                # delta accounting above already recorded any partial fills
                # incrementally; refresh positions to reconcile.
                try:
                    self.refresh_positions(force=True)
                except Exception:
                    pass
            _alog(
                "BROKER",
                f"Robinhood REJECTED (after submit): symbol={symbol} side={side} "
                f"order_id={oid} reason={reason} partial_filled={filled_qty}",
                "red",
            )
            self._fire_reject_alert(symbol, side, "RobinhoodRejected", str(reason), cid)
        elif cur_state in ("cancelled", "canceled"):
            if _wal_writable:
                try:
                    self._wal.mark_canceled(cid)
                except Exception:
                    pass
            with self._lock:
                self._tracked_orders.pop(oid, None)
                self._tracked_states.pop(oid, None)
                self._oid_to_cid.pop(oid, None)
                self._prev_filled_qty.pop(oid, None)
            if filled_qty > 0:
                try:
                    self.refresh_positions(force=True)
                except Exception:
                    pass
            _alog(
                "BROKER",
                f"Robinhood CANCELED: order_id={oid} partial_filled={filled_qty}",
                "yellow",
            )
        elif cur_state == "expired":
            if _wal_writable:
                try:
                    self._wal.mark_expired(cid)
                except Exception:
                    pass
            with self._lock:
                self._tracked_orders.pop(oid, None)
                self._tracked_states.pop(oid, None)
                self._oid_to_cid.pop(oid, None)
                self._prev_filled_qty.pop(oid, None)
            if filled_qty > 0:
                try:
                    self.refresh_positions(force=True)
                except Exception:
                    pass
            _alog(
                "BROKER",
                f"Robinhood EXPIRED: order_id={oid} partial_filled={filled_qty}",
                "yellow",
            )
        # else: still in flight (queued / confirmed / unconfirmed) — keep tracking

    def _cid_for_broker_order_id(self, broker_order_id: str) -> Optional[str]:
        """Reverse lookup: broker_order_id → client_order_id.

        Bug-check fix #16: maintain an in-memory cache populated whenever an
        order is accepted by RH (or synthetically by dry-run). Falls back to
        the linear WAL scan only on a cache miss.
        """
        with self._lock:
            cached = self._oid_to_cid.get(broker_order_id)
        if cached:
            return cached
        try:
            for rec in self._wal.list_open():
                if rec.broker_order_id == broker_order_id:
                    with self._lock:
                        self._oid_to_cid[broker_order_id] = rec.client_order_id
                    return rec.client_order_id
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # WAL reconciliation

    def reconcile_wal_with_broker(self) -> dict:
        """At broker boot, query RH for each non-terminal WAL entry and
        update its state. Returns a dict summary of resolutions.
        """
        out = {"adopted": 0, "rejected": 0, "filled": 0, "canceled": 0, "still_open": 0, "missing": 0}
        try:
            open_records = self._wal.list_open()
        except Exception:
            return out
        for rec in open_records:
            cid = rec.client_order_id
            # 2026-04-30 MED #27: skip dry-run synthetic broker_order_ids.
            # They exist only in WAL; querying RH for them would always
            # return None and the "not found at broker" branch would mark
            # them rejected, destroying dry-run history on a mode-flip.
            _broker_oid = getattr(rec, "broker_order_id", None) or ""
            if _broker_oid.startswith("dry-"):
                continue
            ref_id = self._ref_id_from_cid(cid)
            try:
                existing = self._client.find_order_by_ref_id(ref_id)
            except Exception:
                continue
            if not existing:
                # No order at RH — wipe intent state so a subsequent retry
                # re-creates it.
                try:
                    self._wal.mark_rejected(cid, "reconcile: not found at broker")
                except Exception:
                    pass
                out["missing"] += 1
                continue
            state = (existing.get("state") or "").strip().lower()
            order_id = str(existing.get("id") or "")
            try:
                # Bug-check round 2 (codex SHOULD-FIX #8): if the WAL row is
                # still in `intent` state but RH has the order, transition
                # through `submitted` first so the WAL state machine is
                # respected. Without this, mark_filled jumps from intent →
                # filled, skipping the documented submitted state.
                if rec.state == "intent" and order_id:
                    try:
                        self._wal.mark_submitted(cid, order_id)
                    except Exception:
                        pass
                if state == "filled":
                    fq = float(existing.get("cumulative_quantity") or 0.0)
                    ap = float(existing.get("average_price") or 0.0) if existing.get("average_price") else None
                    self._wal.mark_filled(cid, fq, ap)
                    out["filled"] += 1
                elif state in ("rejected", "failed"):
                    self._wal.mark_rejected(cid, str(existing.get("reject_reason") or "reconcile"))
                    out["rejected"] += 1
                elif state in ("cancelled", "canceled"):
                    self._wal.mark_canceled(cid)
                    out["canceled"] += 1
                elif state == "expired":
                    self._wal.mark_expired(cid)
                else:
                    # Still open — adopt and start tracking. mark_submitted
                    # already fired above if rec.state was intent.
                    if rec.state == "intent":
                        out["adopted"] += 1
                    self._track_order(existing, client_order_id=cid)
                    out["still_open"] += 1
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # PortfolioEmulator-compat shims (used by strategies through portfolio_emulator)

    def buy(self, ticker: str, shares: float, price: float, timestamp: Optional[datetime] = None) -> bool:
        try:
            ts = timestamp or datetime.now(timezone.utc)
            cid = make_client_order_id(self._instance_id, ticker, ts.isoformat(), "buy", 0)
            style = _order_style_for_now(price, "buy", shares, _in_regular_hours(ts))
            self.submit_order(
                symbol=ticker, side="buy", qty=shares, notional=None,
                order_type=style["order_type"], limit_price=style["limit_price"],
                tif=style["tif"], extended_hours=style["extended_hours"],
                market_hours=style["market_hours"],
                client_order_id=cid,
            )
            return True
        except BrokerError as e:
            _alog("BROKER", f"RH buy failed for {ticker}: {e}", "red")
            return False

    def sell(self, ticker: str, shares: float, price: float, timestamp: Optional[datetime] = None) -> bool:
        try:
            ts = timestamp or datetime.now(timezone.utc)
            cid = make_client_order_id(self._instance_id, ticker, ts.isoformat(), "sell", 0)
            style = _order_style_for_now(price, "sell", shares, _in_regular_hours(ts))
            self.submit_order(
                symbol=ticker, side="sell", qty=shares, notional=None,
                order_type=style["order_type"], limit_price=style["limit_price"],
                tif=style["tif"], extended_hours=style["extended_hours"],
                market_hours=style["market_hours"],
                client_order_id=cid,
            )
            return True
        except BrokerError as e:
            _alog("BROKER", f"RH sell failed for {ticker}: {e}", "red")
            return False

    def execute_signal(
        self, ticker: str, signal: int, price: float, timestamp: Optional[datetime] = None,
        cash_per_trade: float = 1000.0, sell_fraction: float = 1.0,
    ) -> bool:
        """signal: +1 buy, -1 sell, 0 hold. Mirrors PortfolioEmulator.execute_signal."""
        if signal not in (-1, 1):
            return False
        if signal == 1:
            # Buy with notional cash_per_trade
            try:
                cid = make_client_order_id(self._instance_id, ticker, (timestamp or datetime.now(timezone.utc)).isoformat(), "buy", 0)
                self.submit_order(
                    symbol=ticker, side="buy", qty=None, notional=cash_per_trade,
                    order_type="market", limit_price=None, tif="day",
                    extended_hours=False, client_order_id=cid,
                )
                return True
            except BrokerError as e:
                _alog("BROKER", f"RH execute_signal buy failed for {ticker}: {e}", "red")
                return False
        # Sell: reduce position by sell_fraction
        with self._lock:
            cur_qty = float(self._positions.get(ticker, 0.0) or 0.0)
        if cur_qty <= 0:
            return False
        sell_qty = round(cur_qty * max(0.01, min(1.0, float(sell_fraction))), 6)
        if sell_qty <= 0:
            return False
        try:
            ts = timestamp or datetime.now(timezone.utc)
            cid = make_client_order_id(self._instance_id, ticker, ts.isoformat(), "sell", 0)
            style = _order_style_for_now(price, "sell", sell_qty, _in_regular_hours(ts))
            self.submit_order(
                symbol=ticker, side="sell", qty=sell_qty, notional=None,
                order_type=style["order_type"], limit_price=style["limit_price"],
                tif=style["tif"], extended_hours=style["extended_hours"],
                market_hours=style["market_hours"],
                client_order_id=cid,
            )
            return True
        except BrokerError as e:
            _alog("BROKER", f"RH execute_signal sell failed for {ticker}: {e}", "red")
            return False

    def get_positions(self) -> dict[str, float]:
        with self._lock:
            return dict(self._positions)

    def get_positions_value(self, prices: dict[str, float]) -> float:
        total = 0.0
        with self._lock:
            for sym, qty in self._positions.items():
                px = float(prices.get(sym) or self._last_prices.get(sym) or 0.0)
                total += float(qty) * px
        return total

    def get_portfolio_value(self, prices: dict[str, float]) -> float:
        return self.get_cash() + self.get_positions_value(prices)

    def get_trade_history(self) -> list[dict]:
        with self._lock:
            return list(self._trades)

    def get_portfolio_history(self) -> list[dict]:
        with self._lock:
            return list(self._portfolio_snapshots)

    def get_cash(self) -> float:
        with self._lock:
            return float(self._cash or 0.0)

    def get_available_cash(self, reserved: float = 0.0) -> float:
        return max(0.0, self.get_cash() - float(reserved or 0.0))

    def get_initial_value(self) -> float:
        return float(getattr(self, "_initial_value", 0.0) or 0.0)

    def save_portfolio_snapshot(self, prices: dict[str, float], timestamp: Optional[datetime] = None) -> None:
        # 2026-05-07 deadlock fix: this previously held `self._lock` and
        # then called `self.get_positions_value(prices)`, which also
        # acquires `self._lock`. With a vanilla `threading.Lock()` the
        # same-thread re-entrance blocks forever; the watchdog dropped
        # the orphan worker after 10s but the orphan kept `_lock`. The
        # next tick's `refresh_*` call wedged the strategy thread.
        #
        # Fix: hold `_lock` JUST long enough to take GIL-atomic snapshots
        # of cash + positions + last_prices, then release. Compute and
        # append outside the lock so no nested-acquire path is possible.
        # The lock is still needed briefly because `dict(self._positions)`
        # iterates the hash table and CPython 3.11 may release the GIL
        # mid-iteration on large dicts → `RuntimeError: dictionary
        # changed size during iteration`. We hold the lock for ~10 µs.
        with self._lock:
            cash_snap = float(self._cash or 0.0)
            positions_snap = dict(self._positions)
            last_prices_snap = dict(self._last_prices)
        positions_value = 0.0
        for sym, qty in positions_snap.items():
            px = float(prices.get(sym) or last_prices_snap.get(sym) or 0.0)
            positions_value += float(qty) * px
        total = cash_snap + positions_value
        # `list.append` is GIL-atomic; safe outside the lock.
        # The "value" key mirrors AlpacaAdapter.save_portfolio_snapshot's
        # schema so broker.py:3877's `s.get("value")` returns non-zero
        # data — without this alias the live UI's equity-curve was
        # always flat at $0 for Robinhood instances (pre-existing bug
        # surfaced by this audit).
        self._portfolio_snapshots.append({
            "timestamp": timestamp or datetime.now(timezone.utc),
            "cash": cash_snap,
            "positions_value": positions_value,
            "total": total,
            "value": total,
        })

    def print_portfolio(self, prices: dict[str, float], logger: Any = None) -> None:
        try:
            total = self.get_portfolio_value(prices)
            _alog(
                "BROKER",
                f"RH portfolio: cash=${self._cash:.2f} positions={len(self._positions)} "
                f"total=${total:.2f}",
                "white",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Discord alert helpers (keep callsites uniform)

    def _fire_submit_alert(
        self, symbol: str, side: str, qty: Optional[float], notional: Optional[float],
        cid: str, broker_order_id: str, order_type: str, ext_hours: bool,
    ) -> None:
        if self._alert_submit is None:
            return
        try:
            self._alert_submit(
                instance_id=self._instance_id,
                symbol=symbol, side=side, qty=qty, notional=notional,
                client_order_id=cid, broker_order_id=broker_order_id,
                extra={"type": order_type, "ext_hours": ext_hours,
                       "broker": "robinhood",
                       "dry_run": self._dry_run},
            )
        except Exception as e:
            _alog("BROKER", f"alert_order_submit failed: {e}", "yellow")

    def _fire_fill_alert(
        self, symbol: str, side: str, filled_qty: float, avg_px: float,
        cid: str, broker_order_id: str,
    ) -> None:
        if self._alert_fill is None:
            return
        try:
            self._alert_fill(
                instance_id=self._instance_id,
                symbol=symbol, side=side,
                filled_qty=filled_qty, filled_avg_price=avg_px,
                client_order_id=cid, broker_order_id=broker_order_id,
            )
        except Exception as e:
            _alog("BROKER", f"alert_order_fill failed: {e}", "yellow")

    def _fire_reject_alert(
        self, symbol: str, side: str, reason_class: str, reason_text: str, cid: str,
    ) -> None:
        if self._alert_reject is None:
            return
        try:
            self._alert_reject(
                instance_id=self._instance_id,
                symbol=symbol, side=side,
                reason_class=reason_class, reason_text=reason_text,
                client_order_id=cid,
            )
        except Exception as e:
            _alog("BROKER", f"alert_order_reject failed: {e}", "yellow")

    def _fire_retry_alert(
        self, symbol: str, side: str, original_qty: Optional[float],
        retry_qty: float, reason_class: str, reason_text: str, cid: str,
    ) -> None:
        """2026-04-30 D''-retry-alerts: paged when an order is being retried
        after a recoverable rejection (RH fractional -> floor). Distinct
        from _fire_reject_alert (final failure) so operators can audit
        retry behavior on the Robinhood path.
        """
        if self._alert_retry is None:
            return
        try:
            self._alert_retry(
                instance_id=self._instance_id,
                symbol=symbol, side=side,
                original_qty=original_qty, retry_qty=retry_qty,
                reason_class=reason_class, reason_text=reason_text,
                client_order_id=cid,
            )
        except Exception as e:
            _alog("BROKER", f"alert_order_retry failed: {e}", "yellow")

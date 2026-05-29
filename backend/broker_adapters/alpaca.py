"""Alpaca live broker adapter.

Wraps alpaca-py TradingClient + TradingStream for order submission + fill
confirmation. Exposes PortfolioEmulator-compatible private attrs so strategy
code in graph_nexus_analysis.py and broker.py reads through transparently.

Order path:
1. record_intent() in LiveOrderWAL (before any broker API call)
2. submit_order() via TradingClient
3. mark_submitted() in WAL with broker_order_id
4. trade_updates stream -> on_trade_update() -> WAL state transitions +
   _positions / _trades mirror updates under a threading.Lock

Crash safety:
- If submit_order raises after Alpaca may have accepted the order, we call
  get_order_by_client_id() before surfacing the error. This is the core
  idempotency guarantee.
- Restart reconciliation lives in broker.py startup; it reads WAL open rows
  and asks Alpaca about each one.
"""

from __future__ import annotations

import copy
import os
import threading
import time
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
)
from broker_adapters._client_order_id import make_client_order_id
from broker_adapters._wal import LiveOrderWAL


def _alog(service: str, msg: str, color: str = "white") -> None:
    """Thin wrapper around intellistock_logger so adapter modules don't
    have to re-import it at every call site. Falls back to plain print on
    import failure so logging never becomes a blocker during boot.
    """
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service=service)
    except Exception:
        print(f"[{service}] {msg}")


def _parse_error(exc: Exception) -> BrokerError:
    """Classify raw Alpaca SDK exceptions into typed broker errors."""
    msg = str(exc).lower()
    if "insufficient" in msg or "buying power" in msg:
        return InsufficientBuyingPower(str(exc))
    if "pattern day trader" in msg or "pdt" in msg:
        return PDTRestricted(str(exc))
    if "halted" in msg:
        return AssetHalted(str(exc))
    if "not tradable" in msg or "asset not found" in msg:
        return AssetNotTradable(str(exc))
    if "wash" in msg:
        return WashSale(str(exc))
    # 2026-04-30 fix: broaden the fractional-rejection match. Production
    # log on 2026-04-30 23:29 showed BBGI/XNDU rejections with body
    # `{"code":40310000,"message":"asset \"BBGI\" is not fractionable"}`
    # but our retry path didn't fire — meaning _parse_error didn't
    # classify these as FractionalNotAllowed. Most likely alpaca-py's
    # APIError.__str__ returns just the JSON repr or a truncated form
    # whose lowercased text didn't contain literal "fractional"
    # (or the substring matched a previous branch). Add the explicit
    # Alpaca error code 40310000 + the verbatim message phrase so the
    # match is robust regardless of how the SDK formats the exception.
    if (
        "fractional" in msg
        or "fractionable" in msg
        or "40310000" in msg
        or "not fractionable" in msg
    ):
        return FractionalNotAllowed(str(exc))
    if "429" in msg or "rate limit" in msg:
        # 2026-04-22 Fix 2: parse Retry-After header when Alpaca exposes it
        # via `exc.response.headers`. alpaca-py's APIError sometimes exposes
        # `.response` (a requests.Response); not always. Fall back to None
        # which callers interpret as "use default cooldown" (60s).
        _retry_after = None
        try:
            _resp = getattr(exc, "response", None)
            if _resp is not None:
                _hdrs = getattr(_resp, "headers", None) or {}
                _ra = _hdrs.get("Retry-After") or _hdrs.get("retry-after")
                if _ra is not None:
                    _retry_after = float(_ra)
        except Exception:
            _retry_after = None
        _err = BrokerRateLimited(str(exc), retry_after_sec=_retry_after)
        # Loud log so operators see the hint even if the caller's exception
        # handler doesn't surface it.
        try:
            _alog(
                "BROKER",
                f"Alpaca 429 rate-limit: retry_after={_retry_after}s "
                f"(next cycle will naturally throttle). Full error: {exc}",
                "red",
            )
        except Exception:
            pass
        return _err
    return BrokerError(str(exc))


class AlpacaAdapter(BrokerAdapter):
    """Alpaca live broker adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        paper: bool,
        instance_id: str,
        wal: LiveOrderWAL,
        initial_value: Optional[float] = None,
        seed_trades_from_broker: bool = True,
        # Clean-room mode (2026-05-28): when True, broker state is reconciled
        # against this-instance's LiveOrderWAL. Broker positions matched to a
        # filled WAL row are strategy-owned; everything else is quarantined
        # in self._external_positions and the strategy never reads it.
        # Backward-compatible: defaults False -> existing legacy behavior.
        clean_room_mode: bool = False,
        cid_prefix: Optional[str] = None,
        clean_room_retention_days: int = 180,
        # Test-only injection points. Production callers leave these None.
        # When provided, the adapter skips the lazy alpaca-py import (which
        # is heavy and may not be installed in the test environment).
        _test_client: Optional[Any] = None,
        _test_stream_cls: Optional[Any] = None,
    ):
        if _test_client is not None:
            self._TradingClient = type(_test_client) if not isinstance(_test_client, type) else _test_client
            self._TradingStreamCls = _test_stream_cls or type(_test_client)
            self._client = _test_client
        else:
            try:
                from alpaca.trading.client import TradingClient
                from alpaca.trading.stream import TradingStream
            except ImportError as e:
                raise BrokerError(
                    "alpaca-py not installed. pip install alpaca-py to enable live trading."
                ) from e

            self._TradingClient = TradingClient
            self._TradingStreamCls = TradingStream
            self._client = TradingClient(api_key=api_key, secret_key=api_secret, paper=paper)
        # 2026-05-03 live-hang investigation: alpaca-py uses requests.Session
        # without a default timeout; half-open TCP through Docker NAT after
        # idle periods wedges get_account/get_all_positions forever. Inject
        # a 30s default timeout on every session call. Caller-provided
        # timeouts are preserved.
        try:
            _session = getattr(self._client, "_session", None)
            if _session is not None and not getattr(_session, "_intellistock_timeout_patched", False):
                _orig_request = _session.request
                # Accept *args so a positional timeout (8th positional arg in
                # requests.Session.request signature) is preserved if any future
                # alpaca-py version passes it that way.
                def _request_with_timeout(method, url, *args, **kwargs):
                    if not args and kwargs.get("timeout") is None:
                        kwargs["timeout"] = 30
                    return _orig_request(method, url, *args, **kwargs)
                _session.request = _request_with_timeout
                _session._intellistock_timeout_patched = True
        except Exception as _to_exc:
            _alog("BROKER", f"alpaca-py timeout patch failed: {type(_to_exc).__name__}: {_to_exc}", "yellow")
        self._api_key = api_key
        self._api_secret = api_secret
        self._paper = paper

        self._instance_id = instance_id
        self._wal = wal
        # Clean-room state (always populated; only used when clean_room_mode=True).
        self._clean_room_mode = bool(clean_room_mode)
        self._cid_prefix = cid_prefix or ""
        self._clean_room_retention_days = int(clean_room_retention_days)
        self._external_positions: dict[str, dict] = {}

        # 2026-04-22 Discord alert function refs cached at construction so
        # (a) we don't re-import per trade event, and (b) an ImportError is
        # visible ONCE at startup instead of silently swallowed forever.
        self._alert_submit = None
        self._alert_fill = None
        self._alert_reject = None
        self._alert_retry = None
        try:
            from live_alerts import (
                alert_order_submit as _alert_submit,
                alert_order_fill as _alert_fill,
                alert_order_reject as _alert_reject,
                alert_order_retry as _alert_retry,
            )
            self._alert_submit = _alert_submit
            self._alert_fill = _alert_fill
            self._alert_reject = _alert_reject
            self._alert_retry = _alert_retry
        except Exception as _imp_e:
            _alog(
                "BROKER",
                f"Discord live-alerts DISABLED: live_alerts import failed "
                f"({type(_imp_e).__name__}: {_imp_e})",
                "red",
            )

        # 2026-05-07 deadlock defense-in-depth: was threading.Lock(). The
        # RobinhoodAdapter sibling had a self-deadlock in
        # ``save_portfolio_snapshot`` (calling ``get_positions_value``
        # while holding ``_lock``); identical pattern is reachable here
        # via 16 lock sites if a future refactor introduces nesting.
        # RLock makes same-thread re-entrance survivable. Cross-thread
        # mutual-exclusion semantics unchanged.
        self._lock = threading.RLock()
        self._stream = None
        self._stream_thread: Optional[threading.Thread] = None
        self._last_heartbeat_utc: Optional[datetime] = None
        self._trade_updates_connected = False

        # PortfolioEmulator-compatible mirror
        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []
        self._cash: float = 0.0
        self._last_prices: dict[str, float] = {}
        self._portfolio_snapshots: list[dict] = []
        # 2026-04-22 Round 4 Fix 5: track when positions snapshot went stale
        # due to a REST outage. When non-None, downstream gates know the
        # cached `_positions` dict is older than the last successful REST
        # refresh. Readers (e.g. SELL branch) use it to differentiate
        # "position truly 0" from "cache stale, quantity unknown".
        self._positions_stale_since: Optional[float] = None
        self._positions_stale_max_sec: float = float(
            os.environ.get("ALPACA_POSITIONS_STALE_MAX_SEC", "600")
        )
        # 2026-04-22 Fix B: cache of `{symbol: set(sides)}` for orders
        # submitted today. Populated by refresh_orders_today() at broker
        # boot. Broker buy-loop gates on ordered_today() to skip
        # duplicate submissions after a restart (complements Fix A's
        # date-keyed cid idempotency at the Alpaca API layer).
        self._orders_today: dict[str, set[str]] = {}
        # F3: NY-midnight rollover for _orders_today cache.
        self._last_orders_refresh_ny_date: Optional[str] = None
        # Cached buying-power. Refreshed by refresh_cash().
        self._buying_power: float = 0.0

        # 2026-04-30 v2 Task D: cached `fractionable` flag per symbol with
        # negative TTL so failed lookups don't re-hit Alpaca every submit.
        # Cache value: (Optional[bool], expires_at_epoch). None=unknown.
        self._fractionable_cache: dict[str, tuple[Optional[bool], float]] = {}
        self._fractionable_negative_ttl_sec: float = 60.0

        # Seed from Alpaca
        self.refresh_cash()
        self.refresh_positions()

        if self._clean_room_mode:
            # Clean-room: broker positions just populated by refresh_positions
            # are the FULL broker view. Reconcile against this-instance's
            # LiveOrderWAL to split into strategy-owned (_positions) and
            # external (_external_positions). _trades is rebuilt from WAL,
            # not from the broker-history fetch.
            from ._classifier import classify_broker_positions, derive_cid_prefix
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            _now = _dt.now(_tz.utc)
            _prefix = self._cid_prefix or derive_cid_prefix(self._instance_id)
            # Scope D D1: since_utc=None so the classifier (which computes its own
            # retention cutoff + re-filters) can see beyond-retention rows and tag
            # aged-out still-held positions (2-C). Mirror of RobinhoodAdapter.
            _wal_rows = self._wal.list_filled_for_prefix(_prefix, since_utc=None)

            # Convert the dict[ticker->qty] in self._positions into PositionDTO-like
            # entries for the classifier. Use _last_prices (seeded by refresh_positions)
            # for market_value where available.
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

            # initial_value is REQUIRED in clean_room_mode; broker equity is
            # NOT a safe fallback (it includes external/bad-test positions).
            if initial_value is None:
                raise BrokerError(
                    "AlpacaAdapter clean_room_mode=True requires an explicit "
                    "initial_value (configure Instances.<id>.initial_value or "
                    "pass LIVE_INITIAL_VALUE env)."
                )
            self._initial_value = float(initial_value)
        else:
            # Legacy path: seed _trades from broker history; equity from broker.
            if seed_trades_from_broker:
                try:
                    self._seed_trades_from_broker(limit=500)
                except Exception:
                    # Seeding is best-effort; never block adapter construction on it.
                    pass

            # Use Alpaca's authoritative `account.equity` (cash + positions valued
            # at live market) for _initial_value rather than reconstructing it from
            # cash + positions_value({}), which evaluates positions at $0 since the
            # empty prices dict returns 0 for every holding.
            if initial_value is not None:
                self._initial_value = float(initial_value)
            else:
                try:
                    acct_dto = self.refresh_account()
                    self._initial_value = float(acct_dto.equity)
                except Exception:
                    self._initial_value = self._cash + self.get_positions_value({})

        if self._initial_value <= 0:
            raise BrokerError(
                "AlpacaAdapter init: account equity <= 0. "
                "Check credentials and that the account is funded."
            )

    def _seed_trades_from_broker(self, limit: int = 500) -> None:
        """Fetch filled orders from Alpaca and seed ``_trades``.

        This lets strategies that replay trade history (V32 entry-price lookups,
        trailing stops, HC break-glass, V28 rotation `days_held`) work correctly
        across broker restarts.

        2026-04-23 F2: paginate backwards through order history until every
        currently-held position has at least one BUY fill in the seeded list
        (or we hit the hard cap of ``page_cap × limit`` orders). Previously
        we fetched exactly 500 closed orders, which on a high-churn account
        didn't reach far enough back to find the original BUY for older
        positions → `_get_open_position_held_days` returned 0/1 → V28
        rotation gate blocked every candidate with `mode=min_hold
        allowed=False`. Without correct ``days_held``, the strategy's
        rotation logic (correct in backtest) refuses to rotate in live.
        """
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
        except Exception:
            return

        # Snapshot of held symbols we need entry dates for. Use Alpaca's
        # authoritative positions list (not our cached `_positions` which
        # may be empty on fresh boot).
        held_symbols: set[str] = set()
        try:
            for p in self._client.get_all_positions() or []:
                try:
                    if float(getattr(p, "qty", 0.0) or 0.0) > 0:
                        held_symbols.add(str(getattr(p, "symbol", "") or "").upper())
                except Exception:
                    continue
        except Exception:
            held_symbols = set()

        page_cap = 10  # 10 pages × 500 = 5000 orders hard limit
        seeded_by_symbol: dict[str, dict] = {}  # symbol -> earliest BUY fill dict
        all_fills: list[dict] = []
        seen_order_ids: set[str] = set()  # bug-sweep: dedupe boundary (until is inclusive)
        remaining_held = set(held_symbols)
        until_ts: Optional[datetime] = None

        for page in range(page_cap):
            try:
                req_kwargs = {
                    "status": QueryOrderStatus.CLOSED,
                    "limit": limit,
                }
                if until_ts is not None:
                    req_kwargs["until"] = until_ts
                req = GetOrdersRequest(**req_kwargs)
                orders = self._client.get_orders(filter=req) or []
            except Exception:
                break
            if not orders:
                break
            page_min_submitted: Optional[datetime] = None  # bug-sweep: cursor on submitted_at not filled_at
            for o in orders:
                try:
                    # Dedupe: alpaca-py's `until` is inclusive, so page 2 re-emits
                    # the page-1 boundary order. Tracking order.id catches that.
                    _oid = str(getattr(o, "id", "") or "")
                    if _oid and _oid in seen_order_ids:
                        continue
                    if _oid:
                        seen_order_ids.add(_oid)
                    status = str(
                        getattr(o.status, "value", o.status) if getattr(o, "status", None) else ""
                    ).lower()
                    if status != "filled":
                        # Still update cursor so we don't get stuck on non-filled orders.
                        _sub_ts = getattr(o, "submitted_at", None)
                        if _sub_ts is not None and (page_min_submitted is None or _sub_ts < page_min_submitted):
                            page_min_submitted = _sub_ts
                        continue
                    filled_qty = float(o.filled_qty or 0.0)
                    filled_avg = float(o.filled_avg_price) if o.filled_avg_price else 0.0
                    side = str(getattr(o.side, "value", o.side)).lower()
                    # bug-sweep: bare equality, not substring — avoids misclassifying
                    # hypothetical "sell_short" / "buy_to_cover" variants. Alpaca's
                    # OrderSide enum today is just BUY/SELL but defensive is cheap.
                    action = "buy" if side == "buy" else "sell"
                    ts = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
                    sym = str(getattr(o, "symbol", "") or "").upper()
                    rec = {
                        "timestamp": ts,
                        "action": action,
                        "ticker": sym,
                        "shares": filled_qty,
                        "price": filled_avg,
                        "total": filled_qty * filled_avg,
                        "cash_after": None,
                    }
                    all_fills.append(rec)
                    # Track earliest BUY per held symbol for fast
                    # days-held lookups.
                    if action == "buy" and sym in remaining_held:
                        prior = seeded_by_symbol.get(sym)
                        if prior is None or (ts is not None and prior.get("timestamp") and ts < prior["timestamp"]):
                            seeded_by_symbol[sym] = rec
                    # bug-sweep: cursor on submitted_at (alpaca-py `until` filters
                    # on submitted_at, not filled_at). Using filled_at for paging
                    # silently skips limit orders that sat open for days before
                    # filling — we'd never find their actual order rows.
                    _sub_ts = getattr(o, "submitted_at", None) or ts
                    if _sub_ts is not None and (page_min_submitted is None or _sub_ts < page_min_submitted):
                        page_min_submitted = _sub_ts
                except Exception:
                    continue
            # Recompute remaining held symbols that still need a BUY.
            remaining_held = {
                s for s in held_symbols
                if s not in seeded_by_symbol
            }
            if not remaining_held:
                break
            if page_min_submitted is None or (until_ts is not None and page_min_submitted >= until_ts):
                # Can't page backwards further.
                break
            # Next page: orders submitted strictly before this page's earliest.
            until_ts = page_min_submitted

        if remaining_held:
            try:
                _alog(
                    "BROKER",
                    f"_seed_trades: paginated {page + 1} page(s); "
                    f"still missing BUY history for {len(remaining_held)} "
                    f"held symbol(s): {sorted(list(remaining_held))[:10]}"
                    f"{'...' if len(remaining_held) > 10 else ''} — "
                    "V28 rotation `days_held` will default to 0 for these",
                    "yellow",
                )
            except Exception:
                pass
        else:
            try:
                _alog(
                    "BROKER",
                    f"_seed_trades: paginated {page + 1} page(s); "
                    f"found BUY history for all {len(held_symbols)} held symbol(s) "
                    f"across {len(all_fills)} fill(s)",
                    "cyan",
                )
            except Exception:
                pass

        # Sort oldest-first so `reversed(_trades)` yields most recent fills first,
        # matching PortfolioEmulator's append-order convention.
        def _sort_key(t):
            ts = t.get("timestamp")
            return ts if ts is not None else datetime.min.replace(tzinfo=timezone.utc)
        all_fills.sort(key=_sort_key)
        # bug-sweep: MERGE rather than REBIND. Between the top of
        # _seed_trades_from_broker (which runs synchronously at broker boot,
        # potentially AFTER start_trade_updates has been called on retry paths)
        # and this final write, a WebSocket fill can have already appended
        # to self._trades. Rebinding would silently drop those live fills.
        # Dedupe by (timestamp, ticker, action, shares) since order.id isn't
        # in the stored dict shape. Stable sort re-applies afterwards.
        with self._lock:
            if self._trades:
                _key = lambda t: (
                    t.get("timestamp"),
                    t.get("ticker"),
                    t.get("action"),
                    t.get("shares"),
                )
                _seen_keys = {_key(t) for t in all_fills}
                for _extant in self._trades:
                    if _key(_extant) not in _seen_keys:
                        all_fills.append(_extant)
                all_fills.sort(key=_sort_key)
            self._trades = all_fills

    def refresh_orders_today(self) -> dict[str, set[str]]:
        """Load every order submitted since today's UTC midnight and return
        a `{symbol: set(sides)}` mapping, EXCLUDING rejected/canceled/expired
        orders. Caches the result on `self._orders_today` so the broker can
        gate buy decisions against it without re-hitting the Alpaca REST API
        every tick.

        Intended callers:
        - Broker boot, right after `reconcile_wal_with_broker()` so the live
          loop never re-submits a buy for a symbol already ordered today
          even if Nexus's in-memory state was wiped by the restart.
        - Ad-hoc via the live-command "halt/close" flow if we ever want to
          force a cache rebuild.

        Fix-A (date-keyed cid) already gives Alpaca-side idempotency for
        duplicate buys — Alpaca returns the existing order for the same cid.
        This Fix-B cache complements that by short-circuiting the broker
        loop BEFORE the Alpaca round-trip, saving latency and giving
        operators a clear "already bought today — skip" log line.
        """
        out: dict[str, set[str]] = {}
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            # Today UTC midnight — matches Alpaca's `submitted_at` filter.
            today_utc_midnight = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=today_utc_midnight,
                limit=500,
            )
            orders = self._client.get_orders(filter=req) or []
        except Exception as e:
            _alog(
                "BROKER",
                f"refresh_orders_today failed: {type(e).__name__}: {e} — "
                f"cache stays empty; Fix-A cid idempotency still protects "
                f"against duplicate submits",
                "yellow",
            )
            with self._lock:
                self._orders_today = dict(out)
            return out
        for o in orders:
            try:
                status = str(
                    getattr(o.status, "value", o.status) if getattr(o, "status", None) else ""
                ).lower()
                # Exclude terminal-negative states so a rejected / canceled
                # order doesn't block a legitimate new attempt today.
                if status in ("rejected", "canceled", "expired", "denied"):
                    continue
                sym = str(getattr(o, "symbol", "") or "").upper()
                side = str(getattr(o.side, "value", o.side) if getattr(o, "side", None) else "").lower()
                if sym and side in ("buy", "sell"):
                    out.setdefault(sym, set()).add(side)
            except Exception:
                continue
        with self._lock:
            self._orders_today = dict(out)
            # 2026-04-23 F3: stamp the NY session date so maybe_rollover()
            # can detect a midnight rollover without a second REST call.
            try:
                if _ny is not None:
                    self._last_orders_refresh_ny_date = _now.astimezone(_ny).strftime("%Y-%m-%d")
                else:
                    self._last_orders_refresh_ny_date = _now.strftime("%Y-%m-%d")
            except Exception:
                self._last_orders_refresh_ny_date = None
        _alog(
            "BROKER",
            f"refresh_orders_today: {len(out)} symbol(s) with non-rejected orders "
            f"since {today_utc_midnight.isoformat()} — "
            f"buys={sum(1 for s in out.values() if 'buy' in s)} "
            f"sells={sum(1 for s in out.values() if 'sell' in s)}",
            "cyan",
        )
        return out

    def maybe_rollover_orders_today(self) -> bool:
        """2026-04-23 F3: if the NYSE session date has advanced past
        ``_last_orders_refresh_ny_date``, re-call refresh_orders_today()
        so the buy-loop doesn't serve yesterday's cache into today's
        decisions. Returns True if a rollover fired.

        Cheap to call every tick — only invokes a REST round-trip on
        the single tick where the date actually changes.
        """
        try:
            from zoneinfo import ZoneInfo
            _ny = ZoneInfo("America/New_York")
        except Exception:
            return False
        try:
            _today_str = datetime.now(timezone.utc).astimezone(_ny).strftime("%Y-%m-%d")
        except Exception:
            return False
        if self._last_orders_refresh_ny_date == _today_str:
            return False
        prior = self._last_orders_refresh_ny_date
        try:
            _alog(
                "BROKER",
                f"refresh_orders_today: NY date rollover {prior} -> {_today_str}, "
                "re-fetching today's order cache",
                "cyan",
            )
        except Exception:
            pass
        try:
            self.refresh_orders_today()
            return True
        except Exception as _e:
            try:
                _alog(
                    "BROKER",
                    f"maybe_rollover_orders_today: refresh failed "
                    f"{type(_e).__name__}: {_e} — cache serving stale",
                    "yellow",
                )
            except Exception:
                pass
            return False

    def ordered_today(self, symbol: str, side: str) -> bool:
        """Return True if `symbol` has a non-rejected `side` order submitted
        today (per the last refresh_orders_today call). Used by the broker's
        buy-loop to skip duplicate submissions after a restart. Safe to call
        before refresh_orders_today (returns False if cache is empty)."""
        try:
            with self._lock:
                cache = getattr(self, "_orders_today", {}) or {}
            return side.lower() in (cache.get(str(symbol).upper()) or set())
        except Exception:
            return False

    # --- Order submission ---

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
    ) -> OrderRef:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        # Idempotency: if we've already attempted this cid, query by client id first.
        # On transient failure we swallow and proceed (intent path will re-reconcile).
        try:
            existing_ref = self.get_order_by_client_id(client_order_id)
            if existing_ref is not None:
                return existing_ref
        except BrokerError:
            pass  # transient; fall through to submit. WAL+reconcile handles dup safety.

        self._wal.record_intent(client_order_id, symbol, side, qty, notional)

        side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif_map = {
            "day": TimeInForce.DAY,
            "gfd": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }
        tif_enum = tif_map.get(tif.lower(), TimeInForce.DAY)

        # 2026-04-30 Task D-revision: pre-submit ONLY checks cached
        # fractionable flag. If cached False (populated by a previous
        # rejection), floor before submit to skip a known-doomed POST.
        # Otherwise submit optimistically — the rejection handler below
        # catches FractionalNotAllowed and retries with floored qty.
        _frac_cached = self._is_fractionable(symbol)
        if qty is not None and qty > 0 and qty != int(qty) and _frac_cached is False:
            _floored_pre = float(int(qty))
            if _floored_pre < 1.0:
                self._wal.mark_rejected(
                    client_order_id,
                    f"qty<1 after fractional-floor (qty={qty:.4f}, cached non-fractionable {symbol})",
                )
                _alog("BROKER",
                      f"SKIP {side.lower()} {symbol} — qty={qty:.4f} floored to 0 "
                      f"(cached non-fractionable, cash too small for 1 share)",
                      "yellow")
                if self._alert_reject is not None:
                    try:
                        self._alert_reject(
                            instance_id=self._instance_id,
                            symbol=symbol, side=side.lower(),
                            reason_class="FractionalNotAllowed",
                            reason_text=f"floored qty={qty:.4f} -> 0 (cached non-fractionable)",
                            client_order_id=client_order_id,
                        )
                    except Exception:
                        pass
                raise FractionalNotAllowed(
                    f"{symbol} non-fractionable (cached); floored qty={qty:.4f} -> 0"
                )
            _alog("BROKER",
                  f"Pre-submit floor for cached non-fractionable {symbol}: "
                  f"{qty:.4f} -> {_floored_pre:.0f}",
                  "cyan")
            qty = _floored_pre

        kw = {
            "symbol": symbol,
            "side": side_enum,
            "time_in_force": tif_enum,
            "extended_hours": extended_hours,
            "client_order_id": client_order_id,
        }
        if qty is not None:
            kw["qty"] = qty
        if notional is not None and qty is None:
            kw["notional"] = notional

        if order_type.lower() == "market":
            req = MarketOrderRequest(**kw)
        elif order_type.lower() == "limit":
            if limit_price is None:
                raise BrokerError("limit_price required for limit orders")
            req = LimitOrderRequest(limit_price=limit_price, **kw)
        else:
            raise BrokerError(f"unsupported order_type: {order_type}")

        # 2026-04-22 Fix 4.1: log every order submission attempt so operators
        # can see at which stage a buy dropped out (Nexus signal → broker
        # decision → Alpaca submit → Alpaca accept → WebSocket fill).
        #
        # NOTE: alpaca-py only honours `notional` when `qty` is None (see the
        # `if qty is not None: kw["qty"] = qty; elif notional: kw["notional"]`
        # split above). The log must reflect what was ACTUALLY sent rather
        # than the raw args — otherwise operators see `notional=$5000` for a
        # request Alpaca processed as a 3-share market order.
        _qty_part = f"qty={qty}" if qty is not None else "qty=-"
        _notional_part = (
            f"notional=${notional:.2f}"
            if (notional is not None and qty is None)
            else "notional=-"
        )
        _alog(
            "BROKER",
            (
                f"Alpaca SUBMIT: side={side.lower()} symbol={symbol} "
                f"{_qty_part} {_notional_part} "
                f"type={order_type.lower()} tif={tif.lower()} "
                f"ext_hours={extended_hours} "
                f"limit={('$%.2f' % limit_price) if limit_price is not None else '-'} "
                f"cid={client_order_id[:24]}"
            ),
            "cyan",
        )

        try:
            order = self._client.submit_order(order_data=req)
        except Exception as e:
            # Ambiguous: the submit raised, but Alpaca may have still accepted.
            # Check by client_order_id BEFORE surfacing the error.
            try:
                existing = self.get_order_by_client_id(client_order_id)
                if existing is not None:
                    # 2026-04-22 Fix 4.2: ACCEPTED via idempotent recovery.
                    _alog(
                        "BROKER",
                        f"Alpaca ACCEPTED (recovered): symbol={symbol} "
                        f"order_id={getattr(existing, 'broker_order_id', '-')} "
                        f"(original submit raised {type(e).__name__} but Alpaca "
                        f"actually recorded it)",
                        "green",
                    )
                    return existing
            except BrokerError:
                # Query itself is transient - leave WAL in `intent` for next
                # reconcile cycle rather than marking rejected (avoids duplicate
                # order if Alpaca actually accepted).
                _alog(
                    "BROKER",
                    f"Alpaca AMBIGUOUS: symbol={symbol} cid={client_order_id[:24]} "
                    f"submit raised {type(e).__name__}: {e} — WAL left in intent "
                    f"for next reconcile cycle.",
                    "yellow",
                )
                raise BrokerError(
                    f"submit_order ambiguous result for {client_order_id}: "
                    f"original {type(e).__name__}: {e} - WAL left in 'intent' "
                    "for next reconcile cycle."
                ) from e

            parsed = _parse_error(e)

            # 2026-04-30 Task D-revision: try-then-floor for fractional
            # rejection. If qty was fractional AND Alpaca rejected with
            # "not fractionable", populate cache, floor to int, retry once.
            if (
                isinstance(parsed, FractionalNotAllowed)
                and qty is not None
                and qty > 0
                and qty != int(qty)
            ):
                # Populate cache so future submits for this symbol skip
                # the failed POST and floor immediately.
                self._fractionable_cache[str(symbol).strip().upper()] = (False, 0.0)
                _floored_retry = float(int(qty))
                if _floored_retry < 1.0:
                    self._wal.mark_rejected(
                        client_order_id,
                        f"qty<1 after fractional-floor (qty={qty:.4f}, non-fractionable {symbol})",
                    )
                    _alog(
                        "BROKER",
                        f"REJECTED {side.lower()} {symbol} — qty={qty:.4f} floored to 0 "
                        f"(non-fractionable, cash too small for 1 share)",
                        "red",
                    )
                    if self._alert_reject is not None:
                        try:
                            self._alert_reject(
                                instance_id=self._instance_id,
                                symbol=symbol, side=side.lower(),
                                reason_class="FractionalNotAllowed",
                                reason_text=f"floored qty={qty:.4f} -> 0 (non-fractionable)",
                                client_order_id=client_order_id,
                            )
                        except Exception:
                            pass
                    raise FractionalNotAllowed(
                        f"{symbol} non-fractionable; floored qty={qty:.4f} -> 0"
                    )
                _alog(
                    "BROKER",
                    f"Alpaca rejected fractional {symbol} qty={qty:.4f}; "
                    f"retrying with whole shares qty={_floored_retry:.0f}",
                    "yellow",
                )
                # 2026-04-30 D''-retry-alerts: Discord visibility for the
                # retry transition. Operator sees original reject + retry +
                # final outcome instead of just the final outcome.
                if getattr(self, "_alert_retry", None) is not None:
                    try:
                        self._alert_retry(
                            instance_id=self._instance_id,
                            symbol=symbol,
                            side=side.lower(),
                            original_qty=qty,
                            retry_qty=_floored_retry,
                            reason_class=type(e).__name__,
                            reason_text=str(parsed),
                            client_order_id=client_order_id,
                        )
                    except Exception:
                        pass
                qty = _floored_retry
                kw["qty"] = qty
                if order_type.lower() == "market":
                    req = MarketOrderRequest(**kw)
                elif order_type.lower() == "limit":
                    req = LimitOrderRequest(limit_price=limit_price, **kw)
                try:
                    order = self._client.submit_order(order_data=req)
                except Exception as e2:
                    parsed2 = _parse_error(e2)
                    self._wal.mark_rejected(client_order_id, str(parsed2))
                    _alog(
                        "BROKER",
                        f"Alpaca REJECTED retry: symbol={symbol} side={side.lower()} "
                        f"cid={client_order_id[:24]} reason=\"{parsed2}\"",
                        "red",
                    )
                    if self._alert_reject is not None:
                        try:
                            self._alert_reject(
                                instance_id=self._instance_id,
                                symbol=symbol, side=side.lower(),
                                reason_class=type(e2).__name__,
                                reason_text=str(parsed2),
                                client_order_id=client_order_id,
                            )
                        except Exception:
                            pass
                    raise parsed2
                # Retry succeeded — fall through to mark_submitted +
                # alert_submit flow below.
            else:
                # Non-fractional rejection — original behavior.
                self._wal.mark_rejected(client_order_id, str(parsed))
                # 2026-04-22 Fix 4.3: REJECTED log with Alpaca's rejection reason.
                _alog(
                    "BROKER",
                    f"Alpaca REJECTED: symbol={symbol} side={side.lower()} "
                    f"cid={client_order_id[:24]} reason=\"{parsed}\"",
                    "red",
                )
                # 2026-04-30 v2 Task E: parity with WS reject path. Without
                # this, every Alpaca-side validation rejection (40310000
                # fractionable, insufficient buying power, PDT, etc.) was
                # logged red but the operator got no Discord notification.
                # Wrapped so a Discord enqueue failure doesn't mask the
                # original BrokerError raised below.
                if self._alert_reject is not None:
                    try:
                        self._alert_reject(
                            instance_id=self._instance_id,
                            symbol=symbol,
                            side=side.lower(),
                            reason_class=type(e).__name__,
                            reason_text=str(parsed),
                            client_order_id=client_order_id,
                        )
                    except Exception:
                        pass
                raise parsed

        self._wal.mark_submitted(client_order_id, str(order.id))
        # 2026-04-22 Fix 4.2: ACCEPTED log (order accepted, awaiting fill).
        _alog(
            "BROKER",
            f"Alpaca ACCEPTED: symbol={symbol} side={side.lower()} "
            f"order_id={order.id} status={getattr(order, 'status', '-')} "
            f"qty={getattr(order, 'qty', '-')} "
            f"limit={('$%.2f' % limit_price) if limit_price is not None else '-'}",
            "green",
        )
        # 2026-04-22 Discord alert on successful submit (synchronous path,
        # safe to call directly — not in an asyncio callback). Uses cached
        # function ref from __init__ so we don't re-import per call; skips
        # cleanly if the module failed to import at boot (logged once there).
        if self._alert_submit is not None:
            try:
                self._alert_submit(
                    instance_id=self._instance_id,
                    symbol=symbol,
                    side=side.lower(),
                    qty=qty,
                    notional=notional,
                    client_order_id=client_order_id,
                    broker_order_id=str(order.id) if getattr(order, "id", None) else "pending",
                    extra={"type": order_type, "ext_hours": extended_hours},
                )
            except Exception:
                pass
        return self._to_orderref(order)

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(broker_order_id)
            return True
        except Exception:
            return False

    def cancel_all_open_orders(self) -> int:
        count = 0
        for ref in self.list_open_orders():
            if self.cancel_order(ref.broker_order_id):
                count += 1
        return count

    def get_order(self, broker_order_id: str) -> OrderRef:
        o = self._client.get_order_by_id(broker_order_id)
        return self._to_orderref(o)

    def get_order_by_client_id(self, client_order_id: str) -> Optional[OrderRef]:
        """Return OrderRef if Alpaca has this cid, None if truly absent.

        Raises BrokerError on transient errors (network, 5xx) so callers can
        distinguish "not at broker" from "unknown; try again later".
        This is critical for restart reconciliation: treating a transient
        failure as "not found" would let a filled order be resubmitted.
        """
        try:
            o = self._client.get_order_by_client_id(client_order_id)
            return self._to_orderref(o)
        except Exception as e:
            msg = str(e).lower()
            # Alpaca returns 404 for truly absent orders
            if "404" in msg or "not found" in msg:
                return None
            # Everything else is transient — surface as typed error
            raise BrokerError(f"transient broker query failure for {client_order_id}: {e}") from e

    def list_open_orders(self, limit: int = 200) -> list[OrderRef]:
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=limit)
            orders = self._client.get_orders(filter=req)
        except Exception:
            return []
        return [self._to_orderref(o) for o in orders]

    def _to_orderref(self, o: Any) -> OrderRef:
        side = str(getattr(o.side, "value", o.side) if getattr(o, "side", None) else "")
        status = str(getattr(o.status, "value", o.status) if getattr(o, "status", None) else "")
        qty = 0.0
        if getattr(o, "qty", None) is not None:
            try:
                qty = float(o.qty)
            except (TypeError, ValueError):
                qty = 0.0
        elif getattr(o, "notional", None) is not None:
            try:
                qty = float(o.notional)
            except (TypeError, ValueError):
                qty = 0.0
        return OrderRef(
            broker_order_id=str(o.id),
            client_order_id=str(o.client_order_id or ""),
            symbol=str(o.symbol),
            side=side,
            qty=qty,
            status=status,
            filled_qty=float(o.filled_qty or 0.0),
            filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
            submitted_at_utc=getattr(o, "submitted_at", None),
        )

    # --- REST refresh ---

    def refresh_positions(self) -> list[PositionDTO]:
        """REST refresh of positions.

        Thread-safety contract: we BUILD a new dict outside the lock, then
        REBIND `self._positions` under the lock. CPython's attribute-rebind is
        atomic under the GIL, so readers that captured the prior reference
        continue iterating the old (now unreferenced) dict without raising
        "dictionary changed size during iteration". A future maintainer must
        keep this rebind pattern; do NOT switch to dict.clear() + update().

        2026-04-22 Round 4 Fix 5: on REST failure (connection flake, 5xx,
        timeout, Alpaca outage), PRESERVE the prior ``self._positions``
        snapshot instead of clobbering it to ``{}``. Wiping live-portfolio
        state on a transient outage is worse than serving a slightly-stale
        view: downstream SELL branches would silently skip ("no position
        held"), and PortfolioEmulator-style readers would mis-compute
        portfolio value. A max-staleness escape hatch (``_positions_stale_max_sec``,
        default 10 min) eventually clobbers to ``{}`` so we never lie forever
        when the credentials are genuinely revoked.

        A successful REST response — even if the account is empty — clears
        the stale flag and clobbers ``_positions`` as before (that's the
        normal "user liquidated" path, distinct from an outage).
        """
        try:
            positions = self._client.get_all_positions()
            rest_ok = True
        except Exception as _pos_exc:
            positions = []
            rest_ok = False
            _now = time.time()
            _stale_since = self._positions_stale_since
            if _stale_since is None:
                self._positions_stale_since = _now
                _stale_for = 0.0
            else:
                _stale_for = _now - _stale_since
            _alog(
                "BROKER",
                f"refresh_positions REST failure: {type(_pos_exc).__name__}: {_pos_exc} "
                f"(stale for {_stale_for:.0f}s, preserving {len(self._positions)} cached position(s))",
                "yellow",
            )
            if _stale_for < self._positions_stale_max_sec:
                # Preserve prior snapshot. Rebuild the DTO list from the
                # cached dict so callers still get a non-empty response
                # (UI keeps rendering, SELL branch keeps seeing real qtys).
                #
                # Bug-sweep 2026-04-22: seed market_value from the cached
                # _last_prices map. Returning market_value=0 here silently
                # zeroed out equity in _compute_live_state_snapshot when
                # refresh_account() was also failing (same outage), making
                # the UI show cash-only equity — worse than an empty-positions
                # display. With this seed the UI shows a plausible MTM even
                # through a multi-minute REST outage.
                preserved: list[PositionDTO] = []
                with self._lock:
                    _snap = dict(self._positions)
                    _px_snap = dict(self._last_prices)
                for _sym, _qty in _snap.items():
                    try:
                        _qf = float(_qty)
                        _px = float(_px_snap.get(_sym, 0.0) or 0.0)
                        preserved.append(PositionDTO(
                            symbol=_sym,
                            qty=_qf,
                            avg_entry_price=0.0,
                            market_value=_qf * _px if _qf > 0 and _px > 0 else 0.0,
                        ))
                    except (TypeError, ValueError):
                        continue
                return preserved
            # Stale too long — clobber to {} as last resort. Operator will
            # have already received the snapshot-worker alert; this is the
            # fall-through that prevents us from lying forever.
            _alog(
                "BROKER",
                f"refresh_positions stale > {self._positions_stale_max_sec}s — clobbering cache (account may be unauthenticated)",
                "red",
            )
        new_positions: dict[str, float] = {}
        # 2026-04-22 CRITICAL: derive per-share price = market_value / qty so
        # downstream MTM math sees real prices instead of $0 marks. Consumed
        # below under the lock to seed _last_prices for any symbol that
        # doesn't have a fill-priced entry yet.
        new_last_prices: dict[str, float] = {}
        out: list[PositionDTO] = []
        for p in positions:
            try:
                sym = str(p.symbol)
                qty = float(p.qty)
                mv = float(p.market_value or 0.0)
                new_positions[sym] = qty
                if qty > 0 and mv > 0:
                    new_last_prices[sym] = mv / qty
                out.append(PositionDTO(
                    symbol=sym,
                    qty=qty,
                    avg_entry_price=float(p.avg_entry_price or 0.0),
                    market_value=mv,
                ))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        # Atomic rebind (GIL-protected in CPython). See docstring.
        with self._lock:
            # 2-A (bug-sweep 2026-05-28): enforce the clean-room quarantine on
            # every rebind so a forced refresh can't re-adopt external positions.
            # owned_qty = broker_qty - external_qty. Mirror of RobinhoodAdapter.
            # 2-A (bug-sweep 2026-05-28): enforce the clean-room quarantine on
            # every rebind so a forced refresh can't re-adopt external positions.
            # owned_qty = broker_qty - external_qty. (Scope D: a reconcile that
            # anchored on self._positions was reverted — see RobinhoodAdapter for
            # the rationale; kept symmetric.)
            if self._clean_room_mode:
                owned_new: dict[str, float] = {}
                for _sym, _bqty in new_positions.items():
                    _ext = max(0.0, float((self._external_positions.get(_sym) or {}).get("qty", 0.0) or 0.0))
                    _owned_qty = float(_bqty) - _ext
                    if _owned_qty > 1e-6:
                        owned_new[_sym] = _owned_qty
                for _sym in list(self._external_positions.keys()):
                    if _sym not in new_positions:
                        self._external_positions.pop(_sym, None)
                    else:
                        _row = self._external_positions[_sym]
                        _row["qty"] = min(float(_row.get("qty", 0.0) or 0.0), float(new_positions[_sym]))
                self._positions = owned_new
            else:
                self._positions = new_positions
            # Successful REST refresh — clear the staleness flag set by
            # any prior outage so SELL branches stop suppressing.
            if self._positions_stale_since is not None:
                _alog(
                    "BROKER",
                    f"refresh_positions recovered (cache was stale, now {len(new_positions)} position(s))",
                    "green",
                )
                self._positions_stale_since = None
            # Seed market prices for positions but DON'T overwrite an
            # existing entry — _on_trade_update writes authoritative fill
            # prices, and REST market_value/qty is a stale mark.
            if new_last_prices:
                for _sym, _price in new_last_prices.items():
                    if _sym not in self._last_prices or self._last_prices.get(_sym, 0.0) == 0.0:
                        self._last_prices[_sym] = _price
        return out

    def refresh_cash(self) -> CashDTO:
        acct = self._client.get_account()
        cash = float(acct.cash or 0.0)
        bp = float(acct.buying_power or 0.0)
        with self._lock:
            self._cash = cash
            # 2026-04-22 Fix 5: cache buying_power alongside cash so the
            # broker gate can opt-in to margin sizing via config flag
            # `live_use_buying_power_for_sizing`. Read via the
            # `_buying_power` attribute (hasattr-gated so backtest
            # PortfolioEmulator paths don't accidentally pick it up).
            self._buying_power = bp
        return CashDTO(
            cash=cash,
            buying_power=bp,
            daytrading_buying_power=float(getattr(acct, "daytrading_buying_power", 0) or 0.0),
        )

    def refresh_account(self) -> AccountDTO:
        """One-shot fetch of everything the LiveState UI needs from Alpaca
        in a single REST call — cash, equity, buying_power, last_equity
        (for day-change), and the PDT flags. Also refreshes the cached
        ``_cash`` so downstream code that still reads it stays consistent.
        """
        acct = self._client.get_account()
        cash = float(acct.cash or 0.0)
        # Keep the cached _cash in sync so sizing code that reads it
        # (PortfolioEmulator-style) doesn't drift from Alpaca.
        with self._lock:
            self._cash = cash
        return AccountDTO(
            equity=float(acct.equity or 0.0),
            pattern_day_trader=bool(getattr(acct, "pattern_day_trader", False)),
            daytrade_count=int(getattr(acct, "daytrade_count", 0) or 0),
            account_blocked=bool(getattr(acct, "account_blocked", False)),
            trading_blocked=bool(getattr(acct, "trading_blocked", False)),
            buying_power=float(getattr(acct, "buying_power", 0) or 0.0),
            last_equity=float(getattr(acct, "last_equity", 0) or 0.0),
            cash=cash,
        )

    def is_market_open(self, now_utc: datetime) -> bool:
        """True during NYSE regular OR extended trading hours.

        Alpaca's ``get_clock().is_open`` reports only regular hours (9:30 AM –
        4:00 PM ET), but operators expect the broker to also trade pre-market
        (4:00-9:30 AM ET) and after-hours (4:00-8:00 PM ET) when orders are
        submitted with ``extended_hours=True``. Use the calendar helper so the
        gate covers the full 4:00 AM – 8:00 PM ET window and stays consistent
        with ``_is_within_trading_session_pt``.
        """
        try:
            from live_calendar import is_nyse_open_extended
            return is_nyse_open_extended(now_utc)
        except Exception:
            try:
                clk = self._client.get_clock()
                return bool(clk.is_open)
            except Exception:
                return False

    # --- Trade updates stream ---

    def start_trade_updates(self) -> None:
        """Start the trade_updates WebSocket listener in a background thread.

        Thread-safe against double-start. Connected flag is set only after
        first real event (in _on_trade_update) so a crashed startup doesn't
        falsely report connected=True.

        Live-readiness P0 #1 (2026-04-29): the inner _run thread now wraps
        self._stream.run() in a watchdog loop with exponential backoff. Old
        code exited the thread on any exception, leaving fills un-mirrored
        until container restart. Now: reconnect attempts forever (capped at
        60s backoff); only stops when self._stop_stream is set.
        """
        with self._lock:
            if self._stream_thread is not None and self._stream_thread.is_alive():
                return

            def _run():
                import time as _ws_time
                _max_backoff = 60.0
                while not getattr(self, "_stop_stream", False):
                    # Q7 fix: reset backoff to 1s at the START of each attempt;
                    # `_on_trade_update` resets `self._stream_backoff_reset=True`
                    # on first successful event so a stable connection brings
                    # backoff back to 1s. Without this, repeated short-lived
                    # connections kept backoff stuck at the previous max.
                    _backoff = float(getattr(self, "_stream_backoff", 1.0) or 1.0)
                    try:
                        with self._lock:
                            self._stream = self._TradingStreamCls(
                                self._api_key, self._api_secret, paper=self._paper
                            )
                            self._stream.subscribe_trade_updates(self._on_trade_update)
                        # run() blocks until disconnect/exception
                        self._stream.run()
                        # Clean exit (rare) — treat as disconnect, retry.
                        with self._lock:
                            self._trade_updates_connected = False
                    except Exception as _stream_exc:
                        with self._lock:
                            self._trade_updates_connected = False
                        try:
                            from intellistock_logger import log as _log
                            _log(
                                f"Alpaca trade_updates stream error: {type(_stream_exc).__name__}: "
                                f"{_stream_exc} — reconnecting in {_backoff:.0f}s",
                                "yellow",
                            )
                        except Exception:
                            pass
                    if getattr(self, "_stop_stream", False):
                        break
                    _ws_time.sleep(_backoff)
                    # Bump backoff for next attempt unless first successful event
                    # reset us via _stream_backoff_reset flag.
                    if getattr(self, "_stream_backoff_reset", False):
                        self._stream_backoff = 1.0
                        self._stream_backoff_reset = False
                    else:
                        self._stream_backoff = min(_max_backoff, _backoff * 2)

            self._stop_stream = False
            self._stream_thread = threading.Thread(
                target=_run, daemon=True, name="alpaca-trade-updates"
            )
            self._stream_thread.start()

    async def _on_trade_update(self, data: Any) -> None:
        """Trade-updates callback.

        Runs inside alpaca-py's asyncio event loop. We MUST NOT call sync
        blocking REST methods here or the stream heartbeat dies. Instead:
        1. Update mirror state from the event payload directly (fast, pure).
        2. Offload any REST refresh to a thread executor if needed.
        """
        import asyncio

        now_utc = datetime.now(timezone.utc)
        with self._lock:
            self._last_heartbeat_utc = now_utc
            # Set connected=True on first real event (not pre-emptively in _run)
            self._trade_updates_connected = True
            # Q7 fix: signal _run watchdog to reset backoff on next reconnect
            self._stream_backoff_reset = True
        ev = str(getattr(data, "event", "")).lower()
        order = getattr(data, "order", None)
        if order is None:
            return

        cid = str(getattr(order, "client_order_id", "") or "")
        sym = str(getattr(order, "symbol", ""))
        filled_qty = float(getattr(order, "filled_qty", 0) or 0)
        filled_avg = float(getattr(order, "filled_avg_price", 0) or 0) or None
        side = str(getattr(order, "side", "")).lower()

        # 2026-04-22 Fix 4.4: log EVERY trade_update terminal-state event so
        # operators see the full lifecycle: SUBMIT → ACCEPTED → FILLED (or
        # REJECTED / CANCELED). Fires BEFORE mirror-state updates so the log
        # accurately reflects what Alpaca said even if the mirror write fails.
        try:
            if ev in ("fill", "partial_fill", "rejected", "canceled", "expired"):
                _prefix = {
                    "fill": "Alpaca FILLED",
                    "partial_fill": "Alpaca PARTIAL-FILL",
                    "rejected": "Alpaca REJECTED (stream)",
                    "canceled": "Alpaca CANCELED",
                    "expired": "Alpaca EXPIRED",
                }[ev]
                _color = {
                    "fill": "green",
                    "partial_fill": "cyan",
                    "rejected": "red",
                    "canceled": "yellow",
                    "expired": "yellow",
                }[ev]
                _msg = (
                    f"{_prefix}: symbol={sym} side={side} "
                    f"qty={filled_qty} "
                    f"price={('$%.2f' % filled_avg) if filled_avg else '-'} "
                    f"order_id={str(getattr(order, 'id', '-'))[:24]} "
                    f"cid={cid[:24]}"
                )
                if ev == "rejected":
                    _msg += f" reason=\"{getattr(data, 'message', '')}\""
                _alog("BROKER", _msg, _color)
        except Exception:
            pass

        with self._lock:
            if ev == "fill":
                self._wal.mark_filled(cid, filled_qty, filled_avg)
                # Update mirror FROM EVENT payload (no REST call in hot path).
                # Alpaca's filled_qty is absolute for the order; for position
                # tracking we use the order side delta directly.
                delta = filled_qty
                current = self._positions.get(sym, 0.0)
                new_pos = current + delta if "buy" in side else current - delta
                if abs(new_pos) < 1e-9:
                    self._positions.pop(sym, None)
                else:
                    self._positions[sym] = new_pos
                price = filled_avg or 0.0
                # Best-effort cash delta (adapter.refresh_cash() is authoritative).
                cash_delta = price * filled_qty
                if "buy" in side:
                    self._cash -= cash_delta
                else:
                    self._cash += cash_delta
                # 2026-04-22 populate _last_prices on every fill so downstream
                # price-readers (outcome tracking, snapshot UI, strategy
                # sizing) always have a recent Alpaca-authoritative quote for
                # any symbol we've traded today. Previously only
                # save_portfolio_snapshot() or _ensure_prices_for_portfolio()
                # wrote here — leaving a cold restart with no prices at all.
                if price > 0:
                    self._last_prices[sym] = price
                self._trades.append({
                    "timestamp": now_utc,
                    "action": "buy" if "buy" in side else "sell",
                    "ticker": sym,
                    "shares": filled_qty,
                    "price": price,
                    "total": cash_delta,
                    "cash_after": self._cash,
                    # 2026-04-22 Fix 7: thread Alpaca's broker order_id so the
                    # UI's Recent Executions rows can correlate with SUBMIT
                    # logs. Consumer at broker.py:2903 already reads this.
                    "order_id": str(getattr(order, "id", "") or ""),
                })
            elif ev == "partial_fill":
                self._wal.mark_partial(cid, filled_qty, filled_avg)
                # Partial-fill mirror update: Alpaca sends cumulative filled_qty;
                # we defer position mirror update until `fill` or cancel for
                # simplicity (strategies rarely act on partial).
            elif ev == "rejected":
                self._wal.mark_rejected(cid, str(getattr(data, "message", "rejected")))
            elif ev == "canceled":
                self._wal.mark_canceled(cid)
            elif ev == "expired":
                self._wal.mark_expired(cid)

        # Offload authoritative REST refresh to a thread so we don't block the
        # stream event loop. Best-effort; failures swallowed.
        if ev in ("fill", "partial_fill"):
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, self._refresh_account_state)
            except Exception:
                pass

        # 2026-04-22 Discord live-alerts: wire the dead wrapper functions
        # from backend/live_alerts.py into the fill/reject paths. Offloaded
        # via run_in_executor because live_alerts._safe_enqueue opens a
        # RethinkDB connection synchronously — calling it directly from
        # this asyncio callback would block the alpaca-py WebSocket event
        # loop and kill the stream heartbeat (docstring at top of this
        # function warns about this).
        #
        # Uses cached function refs from __init__ so we avoid per-event
        # imports and an import failure is visible ONCE at startup rather
        # than silently swallowed every tick.
        try:
            if ev == "fill" and sym and side and self._alert_fill is not None:
                _fn_fill = self._alert_fill
                _inst = self._instance_id
                # 2026-04-22 Round 4 Fix 6: forward Alpaca broker order_id
                # so the Discord fill embed carries it next to client_order_id.
                _broker_oid = str(getattr(order, "id", "") or "")
                def _run_fill_alert(_sym=sym, _side=side, _qty=filled_qty, _avg=(filled_avg or 0.0), _cid=cid, _inst=_inst, _fn=_fn_fill, _boid=_broker_oid):
                    _fn(
                        instance_id=_inst,
                        symbol=_sym,
                        side=_side,
                        filled_qty=_qty,
                        filled_avg_price=_avg,
                        client_order_id=_cid,
                        broker_order_id=_boid,
                    )
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, _run_fill_alert)
                except Exception:
                    pass
            elif ev == "rejected" and sym and side and self._alert_reject is not None:
                _fn_reject = self._alert_reject
                _inst = self._instance_id
                _reason = str(getattr(data, "message", "rejected"))
                def _run_reject_alert(_sym=sym, _side=side, _reason=_reason, _cid=cid, _inst=_inst, _fn=_fn_reject):
                    _fn(
                        instance_id=_inst,
                        symbol=_sym,
                        side=_side,
                        reason_class="broker_reject",
                        reason_text=_reason,
                        client_order_id=_cid,
                    )
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, _run_reject_alert)
                except Exception:
                    pass
        except Exception:
            # Defensive — cached-ref failure shouldn't crash the stream.
            pass

    def _refresh_account_state(self) -> None:
        """REST refresh of positions + cash. Meant to be run off the event loop."""
        try:
            self.refresh_positions()
            self.refresh_cash()
        except Exception:
            pass

    def health_check(self) -> HealthStatus:
        errs: list[str] = []
        auth_fresh = True
        try:
            self._client.get_account()
        except Exception as e:
            auth_fresh = False
            errs.append(f"auth: {e}")
        return HealthStatus(
            auth_fresh=auth_fresh,
            trade_updates_connected=self._trade_updates_connected,
            last_heartbeat_utc=self._last_heartbeat_utc,
            errors=errs,
        )

    def _is_fractionable(self, symbol: str) -> Optional[bool]:
        """Return cached fractionable flag for symbol. Cache is populated
        by the submit_order's rejection handler (NOT by an upfront API
        call). Returns None on cache miss — caller treats as 'unknown'
        and submits optimistically.

        Negative cache TTL still applies: if a transient lookup once set
        None, future calls within 60s also return None without retrying.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        cache = self._fractionable_cache
        now = time.time()
        if sym in cache:
            val, exp = cache[sym]
            if val is True or val is False:
                return val
            if now < exp:
                return None
        # Cache miss: do NOT pre-fetch via get_asset. Return None so caller
        # submits optimistically; the rejection handler will populate cache
        # on a real "not fractionable" response.
        return None

    # --- PortfolioEmulator compatibility shims ---

    def _order_style_for_now(self, price: float, side: str, now_utc: datetime) -> dict:
        """Return order_type/limit_price/extended_hours appropriate for the
        current session. Alpaca rejects non-LIMIT-DAY orders in pre/post-market
        with 422, so outside RTH we auto-flip to marketable LIMIT + DAY +
        extended_hours=True. Buys get a small upward slippage buffer; sells get
        a downward buffer — both marketable against the last known price.
        """
        try:
            from live_calendar import is_nyse_open
            _in_rth = bool(is_nyse_open(now_utc))
        except Exception:
            _in_rth = True  # fail-open to RTH semantics if the calendar fails
        if _in_rth:
            return {
                "order_type": "market",
                "limit_price": None,
                "extended_hours": False,
            }
        # Extended hours: build a marketable limit.
        try:
            buffer_pct = float(os.environ.get("ALPACA_EXTENDED_HOURS_SLIPPAGE_PCT", "0.005"))
        except Exception:
            buffer_pct = 0.005
        if side == "buy":
            limit = round(float(price) * (1.0 + buffer_pct), 2)
        else:
            limit = round(float(price) * (1.0 - buffer_pct), 2)
        if limit <= 0:
            limit = round(float(price), 2)
        return {
            "order_type": "limit",
            "limit_price": limit,
            "extended_hours": True,
        }

    def buy(
        self,
        ticker: str,
        shares: float,
        price: float,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        if shares <= 0 or price <= 0:
            return False
        ts = timestamp or datetime.now(timezone.utc)
        cid = make_client_order_id(
            self._instance_id, ticker, ts.isoformat(), "buy", 0
        )
        style = self._order_style_for_now(price, "buy", ts)
        try:
            self.submit_order(
                ticker, "buy", qty=shares, notional=None,
                order_type=style["order_type"],
                limit_price=style["limit_price"], tif="day",
                extended_hours=style["extended_hours"],
                client_order_id=cid,
            )
            return True
        except BrokerError:
            return False

    def sell(
        self,
        ticker: str,
        shares: float,
        price: float,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        if shares <= 0 or price <= 0:
            return False
        current = self._positions.get(ticker, 0.0)
        if current < shares:
            shares = current
        if shares <= 0:
            return False
        ts = timestamp or datetime.now(timezone.utc)
        cid = make_client_order_id(
            self._instance_id, ticker, ts.isoformat(), "sell", 0
        )
        style = self._order_style_for_now(price, "sell", ts)
        try:
            self.submit_order(
                ticker, "sell", qty=shares, notional=None,
                order_type=style["order_type"],
                limit_price=style["limit_price"], tif="day",
                extended_hours=style["extended_hours"],
                client_order_id=cid,
            )
            return True
        except BrokerError:
            return False

    def execute_signal(
        self,
        ticker: str,
        signal: int,
        price: float,
        timestamp: Optional[datetime] = None,
        cash_per_trade: float = 1000.0,
        sell_fraction: float = 1.0,
    ) -> bool:
        # 2026-04-22 Fix M2: normalize ticker to match _positions dict keys
        # (Alpaca returns uppercase, but callers may pass lowercase).
        # Prevents a spurious "SKIP SELL aapl — no position held" when AAPL
        # IS held.
        ticker = str(ticker or "").strip().upper()
        if price is None or price <= 0:
            return False
        if signal == 1:
            amount = min(cash_per_trade, self._cash)
            if amount <= 0:
                return False
            shares = amount / price
            return self.buy(ticker, shares, price, timestamp=timestamp)
        if signal == -1:
            total = self._positions.get(ticker, 0.0)
            if total <= 0:
                # 2026-04-22 Round 4 Fix 5: differentiate "cache stale"
                # from "position truly 0". When a REST flake preserved
                # a prior snapshot and this ticker wasn't in it, we STILL
                # show 0 here — but the operator needs to see that we're
                # serving stale data, not silently dropping a valid SELL.
                _stale_since = self._positions_stale_since
                if _stale_since is not None:
                    _alog(
                        "BROKER",
                        f"SKIP SELL {ticker} — cache stale for {time.time() - _stale_since:.0f}s, "
                        f"cannot determine quantity (signal dropped)",
                        "red",
                    )
                else:
                    # 2026-04-22 Fix 3: log the silent drop. Strategy can emit
                    # sell signals for tickers the account doesn't own; operators
                    # need to see this in logs rather than silent no-op.
                    _alog(
                        "BROKER",
                        f"SKIP SELL {ticker} — no position held (signal dropped)",
                        "yellow",
                    )
                return False
            frac = max(0.0, min(1.0, float(sell_fraction)))
            shares = total * frac if frac < 1.0 else total
            return self.sell(ticker, shares, price, timestamp=timestamp) if shares > 0 else False
        return False

    def get_positions(self) -> dict[str, float]:
        return copy.copy(self._positions)

    def get_positions_value(self, prices: dict[str, float]) -> float:
        v = 0.0
        for t, q in self._positions.items():
            p = (prices or {}).get(t)
            if p is not None:
                v += q * float(p)
        return v

    def get_portfolio_value(self, prices: dict[str, float]) -> float:
        return self._cash + self.get_positions_value(prices)

    def get_trade_history(self) -> list[dict]:
        return list(self._trades)

    def get_portfolio_history(self) -> list[dict]:
        return list(self._portfolio_snapshots)

    def get_cash(self) -> float:
        return self._cash

    def get_available_cash(self, reserved: float = 0.0) -> float:
        return max(0.0, self._cash - float(reserved))

    def get_initial_value(self) -> float:
        return self._initial_value

    def save_portfolio_snapshot(
        self,
        prices: dict[str, float],
        timestamp: Optional[datetime] = None,
    ) -> None:
        value = self.get_portfolio_value(prices)
        if prices:
            self._last_prices.update(prices)
        self._portfolio_snapshots.append({
            "timestamp": timestamp,
            "value": value,
            "cash": self._cash,
            "positions_snapshot": copy.copy(self._positions),
            "prices": copy.copy(prices) if prices else {},
        })

    def print_portfolio(self, prices: dict[str, float], logger: Any = None) -> None:
        from portfolio_emulator import PortfolioEmulator
        pe = PortfolioEmulator(initial_cash=self._initial_value)
        pe._cash = self._cash
        pe._positions = copy.copy(self._positions)
        pe._trades = list(self._trades)
        pe._portfolio_snapshots = list(self._portfolio_snapshots)
        pe.print_portfolio(prices, logger=logger)

    # --- Restart reconciliation helper ---

    def reconcile_wal_with_broker(self) -> dict[str, str]:
        """For each non-terminal WAL row, query broker for current state.

        Returns a map of client_order_id -> resolution string, for logging.
        Does NOT resubmit - that decision belongs to the caller (broker.py).

        2026-04-22 Fix 1: for terminal-negative resolutions (rejected /
        canceled / expired), also discard the (symbol, side) entry from
        `_orders_today` so Fix-B doesn't permanently skip a symbol whose
        only same-day order was rejected. Mirrors the stream-handler
        cleanup at `_on_trade_update`. Takes `self._lock` to match that
        path's concurrency discipline.
        """
        resolutions: dict[str, str] = {}
        for rec in self._wal.list_open():
            cid = rec.client_order_id
            ref = self.get_order_by_client_id(cid)
            if ref is None:
                resolutions[cid] = "not_found_at_broker"
                continue
            status = ref.status.lower()
            _sym = str(getattr(ref, "symbol", "") or "").upper()
            _side = str(getattr(ref, "side", "") or "").lower()
            if status in {"filled"}:
                self._wal.mark_filled(cid, ref.filled_qty, ref.filled_avg_price)
                resolutions[cid] = "already_filled"
            elif status in {"canceled", "cancelled"}:
                self._wal.mark_canceled(cid)
                resolutions[cid] = "already_canceled"
                self._discard_orders_today(_sym, _side)
            elif status in {"rejected"}:
                self._wal.mark_rejected(cid, "rejected_per_broker")
                resolutions[cid] = "already_rejected"
                self._discard_orders_today(_sym, _side)
            elif status in {"expired", "done_for_day"}:
                self._wal.mark_expired(cid)
                resolutions[cid] = f"already_{status}"
                self._discard_orders_today(_sym, _side)
            elif status in {"partially_filled"}:
                self._wal.mark_partial(cid, ref.filled_qty, ref.filled_avg_price)
                resolutions[cid] = "partially_filled_continuing"
            else:
                self._wal.mark_submitted(cid, ref.broker_order_id)
                resolutions[cid] = f"still_open_{status}"
        return resolutions

    def _discard_orders_today(self, symbol: str, side: str) -> None:
        """Helper: remove (symbol, side) from the Fix-B cache under the
        adapter lock. No-op when inputs are empty."""
        if not symbol or side not in ("buy", "sell"):
            return
        try:
            with self._lock:
                bucket = self._orders_today.get(symbol)
                if bucket is not None:
                    bucket.discard(side)
                    if not bucket:
                        self._orders_today.pop(symbol, None)
        except Exception:
            pass

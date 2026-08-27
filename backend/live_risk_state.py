"""Durable, account-scoped risk state for Alpaca stock trading.

The state is independent of strategy/module hashes.  Its high-water mark can
only increase, equity is refreshed from broker truth, and sleeve accounting is
updated only by confirmed incremental fills.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional


SCHEMA_VERSION = 1
DEFAULT_SOFT_DRAWDOWN = Decimal("0.05")
DEFAULT_HARD_DRAWDOWN = Decimal("0.09")
DEFAULT_KILL_DRAWDOWN = Decimal("0.12")
# Exposure caps are fractions of CURRENT equity, never absolute dollars. They
# live here rather than inline in initialize_risk_state because
# evaluate_drawdown has to reapply the identical fractions on every refresh;
# they are also the defaults of `RiskLimits`, which is what a strategy document
# overrides when its design needs a different envelope.
DEFAULT_MAX_ORDER_FRACTION = Decimal("0.10")
DEFAULT_MAX_SYMBOL_FRACTION = Decimal("0.20")
DEFAULT_MAX_LEVERAGED_FRACTION = Decimal("0.10")
DEFAULT_SLEEVE_COOLDOWN = timedelta(hours=24)
DEFAULT_LEVERAGED_SYMBOLS = frozenset(
    {"SQQQ", "TQQQ", "QLD", "SPXU", "UPRO", "SOXL", "SOXS"}
)


class RiskStateUnavailable(RuntimeError):
    """Missing, corrupt, stale, or unreachable risk state."""


class RiskStateConflict(RuntimeError):
    """Another writer advanced the same risk record."""


class RiskStateBootstrapRefused(RiskStateUnavailable):
    """A funded account asked to create risk state from an unsafe posture."""


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value, name: str, *, positive: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be decimal-compatible") from exc
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "finite and > 0" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return result


def _account_key(instance_id: str, account_id: str) -> str:
    identity = f"{str(instance_id).strip()}|{str(account_id).strip()}"
    return "live-risk:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RiskLimits:
    """One strategy document's live risk envelope.

    Exists because a single set of module constants cannot serve two strategies
    with different designs: a 5% soft buy-freeze is correct for a diversified
    stock book and fatal for a vol-targeted levered core, which is BUILT to sit
    through a -30% drawdown. The gate still BLOCKS rather than clips; only the
    number it blocks against moves.

    Validated at construction, so a config typo fails where an operator can see
    it rather than silently disarming a real-money failsafe. Fields accept any
    decimal-compatible value (a str or float straight out of a strategy config);
    what is stored is always a Decimal.
    """

    max_order_fraction: Decimal = DEFAULT_MAX_ORDER_FRACTION
    max_symbol_fraction: Decimal = DEFAULT_MAX_SYMBOL_FRACTION
    max_leveraged_fraction: Decimal = DEFAULT_MAX_LEVERAGED_FRACTION
    soft: Decimal = DEFAULT_SOFT_DRAWDOWN
    hard: Decimal = DEFAULT_HARD_DRAWDOWN
    kill: Decimal = DEFAULT_KILL_DRAWDOWN

    def __post_init__(self) -> None:
        for name in (
            "max_order_fraction",
            "max_symbol_fraction",
            "max_leveraged_fraction",
            "soft",
            "hard",
            "kill",
        ):
            value = _decimal(getattr(self, name), name, positive=True)
            if not Decimal("0") < value <= Decimal("1"):
                raise ValueError(f"{name} must be in (0, 1]")
            object.__setattr__(self, name, value)
        if not Decimal("0") < self.soft < self.hard < self.kill < Decimal("1"):
            raise ValueError("drawdown thresholds must be increasing in (0, 1)")


#: Exactly today's behaviour for every document that declares nothing.
DEFAULT_RISK_LIMITS = RiskLimits()


@dataclass(frozen=True, slots=True)
class SleeveRiskState:
    symbol: str
    quantity: Decimal = Decimal("0")
    entry_basis: Optional[Decimal] = None
    peak_price: Optional[Decimal] = None
    stop_episode: bool = False
    cooldown_until: Optional[datetime] = None
    allocation: Decimal = Decimal("0")
    outstanding_intent_ids: tuple[str, ...] = ()
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        symbol = str(self.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("sleeve symbol is required")
        quantity = _decimal(self.quantity, "quantity")
        allocation = _decimal(self.allocation, "allocation")
        basis = (
            None
            if self.entry_basis is None
            else _decimal(self.entry_basis, "entry_basis", positive=True)
        )
        peak = (
            None
            if self.peak_price is None
            else _decimal(self.peak_price, "peak_price", positive=True)
        )
        if quantity < 0 or allocation < 0:
            raise ValueError("sleeve quantity/allocation cannot be negative")
        if quantity == 0 and basis is not None:
            raise ValueError("empty sleeve cannot retain an entry basis")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "allocation", allocation)
        object.__setattr__(self, "entry_basis", basis)
        object.__setattr__(self, "peak_price", peak)
        object.__setattr__(
            self,
            "cooldown_until",
            _utc(self.cooldown_until, "cooldown_until")
            if self.cooldown_until is not None
            else None,
        )
        object.__setattr__(
            self,
            "updated_at",
            _utc(self.updated_at, "updated_at")
            if self.updated_at is not None
            else None,
        )
        object.__setattr__(
            self,
            "outstanding_intent_ids",
            tuple(sorted({str(value) for value in self.outstanding_intent_ids})),
        )

    def to_doc(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "entry_basis": (
                str(self.entry_basis) if self.entry_basis is not None else None
            ),
            "peak_price": (
                str(self.peak_price) if self.peak_price is not None else None
            ),
            "stop_episode": self.stop_episode,
            "cooldown_until": (
                self.cooldown_until.isoformat()
                if self.cooldown_until is not None
                else None
            ),
            "allocation": str(self.allocation),
            "outstanding_intent_ids": list(self.outstanding_intent_ids),
            "updated_at": (
                self.updated_at.isoformat()
                if self.updated_at is not None
                else None
            ),
        }

    @classmethod
    def from_doc(cls, doc: dict) -> "SleeveRiskState":
        def dt(name):
            value = (doc or {}).get(name)
            return datetime.fromisoformat(value) if value else None

        return cls(
            symbol=(doc or {}).get("symbol"),
            quantity=(doc or {}).get("quantity", "0"),
            entry_basis=(doc or {}).get("entry_basis"),
            peak_price=(doc or {}).get("peak_price"),
            stop_episode=bool((doc or {}).get("stop_episode", False)),
            cooldown_until=dt("cooldown_until"),
            allocation=(doc or {}).get("allocation", "0"),
            outstanding_intent_ids=tuple(
                (doc or {}).get("outstanding_intent_ids") or ()
            ),
            updated_at=dt("updated_at"),
        )


@dataclass(frozen=True, slots=True)
class AccountRiskState:
    instance_id: str
    account_id: str
    version: int
    high_water_equity: Decimal
    last_equity: Decimal
    observed_at: datetime
    drawdown: Decimal = Decimal("0")
    level: str = "normal"
    new_exposure_allowed: bool = True
    max_order_notional: Optional[Decimal] = None
    max_symbol_notional: Optional[Decimal] = None
    max_leveraged_notional: Optional[Decimal] = None
    sleeves: tuple[SleeveRiskState, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        instance = str(self.instance_id or "").strip()
        account = str(self.account_id or "").strip()
        if not instance or not account:
            raise ValueError("instance_id and account_id are required")
        version = int(self.version)
        if version < 0:
            raise ValueError("version cannot be negative")
        high = _decimal(
            self.high_water_equity, "high_water_equity", positive=True
        )
        last = _decimal(self.last_equity, "last_equity", positive=True)
        drawdown = _decimal(self.drawdown, "drawdown")
        if not Decimal("0") <= drawdown <= Decimal("1"):
            raise ValueError("drawdown must be in [0, 1]")
        level = str(self.level or "").strip().lower()
        if level not in {"normal", "soft", "hard", "kill"}:
            raise ValueError(f"unsupported risk level: {level!r}")
        limits = {}
        for name in (
            "max_order_notional",
            "max_symbol_notional",
            "max_leveraged_notional",
        ):
            raw = getattr(self, name)
            limits[name] = (
                _decimal(raw, name, positive=True) if raw is not None else None
            )
        sleeve_values = tuple(sorted(self.sleeves, key=lambda item: item.symbol))
        if len({item.symbol for item in sleeve_values}) != len(sleeve_values):
            raise ValueError("duplicate sleeve symbol")
        if int(self.schema_version) != SCHEMA_VERSION:
            raise ValueError("unsupported risk-state schema")
        object.__setattr__(self, "instance_id", instance)
        object.__setattr__(self, "account_id", account)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "high_water_equity", high)
        object.__setattr__(self, "last_equity", last)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "drawdown", drawdown)
        object.__setattr__(self, "level", level)
        object.__setattr__(
            self, "new_exposure_allowed", bool(self.new_exposure_allowed)
        )
        object.__setattr__(self, "sleeves", sleeve_values)
        for name, value in limits.items():
            object.__setattr__(self, name, value)

    def sleeve(self, symbol: str) -> SleeveRiskState:
        normalized = str(symbol or "").strip().upper()
        for sleeve in self.sleeves:
            if sleeve.symbol == normalized:
                return sleeve
        return SleeveRiskState(symbol=normalized)

    def to_doc(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "account_id": self.account_id,
            "version": self.version,
            "high_water_equity": str(self.high_water_equity),
            "last_equity": str(self.last_equity),
            "observed_at": self.observed_at.isoformat(),
            "drawdown": str(self.drawdown),
            "level": self.level,
            "new_exposure_allowed": self.new_exposure_allowed,
            "max_order_notional": (
                str(self.max_order_notional)
                if self.max_order_notional is not None
                else None
            ),
            "max_symbol_notional": (
                str(self.max_symbol_notional)
                if self.max_symbol_notional is not None
                else None
            ),
            "max_leveraged_notional": (
                str(self.max_leveraged_notional)
                if self.max_leveraged_notional is not None
                else None
            ),
            "sleeves": [item.to_doc() for item in self.sleeves],
        }

    @classmethod
    def from_doc(cls, doc: dict) -> "AccountRiskState":
        if not isinstance(doc, dict):
            raise RiskStateUnavailable("risk state is corrupt")
        try:
            return cls(
                instance_id=doc.get("instance_id"),
                account_id=doc.get("account_id"),
                version=doc.get("version", 0),
                high_water_equity=doc.get("high_water_equity"),
                last_equity=doc.get("last_equity"),
                observed_at=datetime.fromisoformat(doc.get("observed_at")),
                drawdown=doc.get("drawdown", "0"),
                level=doc.get("level", "normal"),
                new_exposure_allowed=doc.get(
                    "new_exposure_allowed", False
                ),
                max_order_notional=doc.get("max_order_notional"),
                max_symbol_notional=doc.get("max_symbol_notional"),
                max_leveraged_notional=doc.get(
                    "max_leveraged_notional"
                ),
                sleeves=tuple(
                    SleeveRiskState.from_doc(item)
                    for item in (doc.get("sleeves") or ())
                ),
                schema_version=doc.get("schema_version", 0),
            )
        except RiskStateUnavailable:
            raise
        except Exception as exc:
            raise RiskStateUnavailable(
                f"risk state is corrupt: {type(exc).__name__}"
            ) from exc


def initialize_risk_state(
    instance_id: str,
    account_id: str,
    equity,
    observed_at: datetime,
    *,
    limits: RiskLimits = DEFAULT_RISK_LIMITS,
) -> AccountRiskState:
    equity_value = _decimal(equity, "equity", positive=True)
    return AccountRiskState(
        instance_id=instance_id,
        account_id=account_id,
        version=0,
        high_water_equity=equity_value,
        last_equity=equity_value,
        observed_at=observed_at,
        max_order_notional=equity_value * limits.max_order_fraction,
        max_symbol_notional=equity_value * limits.max_symbol_fraction,
        max_leveraged_notional=equity_value * limits.max_leveraged_fraction,
    )


def initialize_live_risk_state(
    instance_id: str,
    account_id: str,
    equity,
    observed_at: datetime,
    *,
    paper: bool,
    open_position_count: int,
    open_order_count: int,
    trading_blocked: bool = False,
    limits: RiskLimits = DEFAULT_RISK_LIMITS,
) -> AccountRiskState:
    """Bootstrap risk state for a real-money account, but only from a flat book.

    Only paper was ever allowed to create this row, for a good reason: a
    bootstrap sets the high-water mark to whatever equity exists right now, so
    a row that goes missing mid-drawdown would come back forgiving the entire
    drawdown and re-arming new exposure. The cost of that rule was that a
    funded account had no row at all, ``risk_state`` health stayed ``unknown``,
    and the gate blocked every buy - a live account would boot silently
    sell-only and nobody would know until the first signal did nothing.

    A flat book resolves the conflict. With no positions and no working orders
    there is no unrealised loss for a stale high-water mark to hide: current
    equity IS the peak by construction, so bootstrapping is exactly as safe as
    it is on paper. Anything else refuses, which leaves the caller in the same
    fail-closed posture as before and gives an operator a legible reason.
    """

    if paper:
        return initialize_risk_state(
            instance_id, account_id, equity, observed_at, limits=limits
        )

    refusals: list[str] = []
    if trading_blocked:
        refusals.append("account trading is blocked at the broker")
    try:
        positions = int(open_position_count)
    except (TypeError, ValueError):
        positions = -1
    try:
        orders = int(open_order_count)
    except (TypeError, ValueError):
        orders = -1
    if positions != 0:
        refusals.append(
            "book is not flat"
            if positions > 0
            else "open position count is unknown"
        )
    if orders != 0:
        refusals.append(
            "working orders exist"
            if orders > 0
            else "open order count is unknown"
        )
    try:
        _decimal(equity, "equity", positive=True)
    except ValueError:
        refusals.append("broker equity is unavailable or non-positive")
    if refusals:
        raise RiskStateBootstrapRefused(
            "live risk-state bootstrap refused: " + "; ".join(refusals)
        )
    return initialize_risk_state(
        instance_id, account_id, equity, observed_at, limits=limits
    )


def evaluate_drawdown(
    state: AccountRiskState,
    fresh_equity,
    observed_at: datetime,
    *,
    soft_threshold=None,
    hard_threshold=None,
    kill_threshold=None,
    limits: RiskLimits = DEFAULT_RISK_LIMITS,
) -> AccountRiskState:
    observed = _utc(observed_at, "observed_at")
    if observed < state.observed_at:
        raise ValueError("equity observation predates persisted risk state")
    equity = _decimal(fresh_equity, "fresh_equity", positive=True)
    high = max(state.high_water_equity, equity)
    drawdown = (high - equity) / high
    # An explicit threshold still wins; None means "use this document's limits".
    soft = _decimal(
        limits.soft if soft_threshold is None else soft_threshold,
        "soft_threshold",
    )
    hard = _decimal(
        limits.hard if hard_threshold is None else hard_threshold,
        "hard_threshold",
    )
    kill = _decimal(
        limits.kill if kill_threshold is None else kill_threshold,
        "kill_threshold",
    )
    if not Decimal("0") < soft < hard < kill < Decimal("1"):
        raise ValueError("drawdown thresholds must be increasing in (0, 1)")
    level = (
        "kill"
        if drawdown >= kill
        else "hard"
        if drawdown >= hard
        else "soft"
        if drawdown >= soft
        else "normal"
    )
    # Rescale the exposure caps against the equity we just observed. They were
    # written once at bootstrap and never touched again, so they stayed pinned
    # to whatever the account was worth the day the row was created: an account
    # that doubled kept a cap sized for half of it, and - the dangerous
    # direction - an account that halved kept permission to put 20% of its
    # ORIGINAL equity into a single symbol, which is 40% of what is actually
    # left. A cap denominated in stale dollars is not a risk limit.
    #
    # A cap of None means "no limit configured"; rescaling it would invent one,
    # so those are preserved as-is.
    #
    # The fractions come from the strategy document's own `RiskLimits`, which is
    # why they must be passed here as well as at bootstrap - this loop reapplies
    # them on every observation, so an override in only one place is overwritten
    # each tick.
    caps = {}
    for name, fraction in (
        ("max_order_notional", limits.max_order_fraction),
        ("max_symbol_notional", limits.max_symbol_fraction),
        ("max_leveraged_notional", limits.max_leveraged_fraction),
    ):
        caps[name] = (
            equity * fraction if getattr(state, name) is not None else None
        )
    return replace(
        state,
        high_water_equity=high,
        last_equity=equity,
        observed_at=observed,
        drawdown=drawdown,
        level=level,
        new_exposure_allowed=(level == "normal"),
        **caps,
    )


def apply_confirmed_fill(
    state: AccountRiskState,
    fill,
    *,
    leveraged_symbols=DEFAULT_LEVERAGED_SYMBOLS,
    cooldown=DEFAULT_SLEEVE_COOLDOWN,
) -> AccountRiskState:
    """Apply one exactly-once incremental fill to durable sleeve protection."""

    symbol = str(fill.event.symbol or "").strip().upper()
    if symbol not in {str(value).upper() for value in leveraged_symbols}:
        return state
    quantity = _decimal(fill.incremental_quantity, "incremental_quantity")
    price = _decimal(fill.incremental_price, "incremental_price", positive=True)
    if quantity <= 0:
        return state
    current = state.sleeve(symbol)
    if str(fill.event.side.value) == "buy":
        total = current.quantity + quantity
        basis = (
            (
                current.quantity * current.entry_basis
                + quantity * price
            )
            / total
            if current.quantity > 0 and current.entry_basis is not None
            else price
        )
        updated = replace(
            current,
            quantity=total,
            entry_basis=basis,
            peak_price=max(current.peak_price or price, price),
            cooldown_until=None,
            allocation=(total * price) / state.last_equity,
            updated_at=fill.event.occurred_at,
        )
    else:
        remaining = max(Decimal("0"), current.quantity - quantity)
        updated = replace(
            current,
            quantity=remaining,
            entry_basis=current.entry_basis if remaining > 0 else None,
            peak_price=current.peak_price if remaining > 0 else None,
            cooldown_until=(
                current.cooldown_until
                if remaining > 0
                else fill.event.occurred_at + cooldown
            ),
            allocation=(
                (remaining * price) / state.last_equity
                if remaining > 0
                else Decimal("0")
            ),
            updated_at=fill.event.occurred_at,
        )
    sleeves = {
        sleeve.symbol: sleeve for sleeve in state.sleeves
    }
    sleeves[symbol] = updated
    return replace(state, sleeves=tuple(sleeves.values()))


class InMemoryRiskBackend:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, key: str) -> Optional[dict]:
        row = self.rows.get(str(key))
        return dict(row) if row is not None else None

    def compare_and_swap(
        self, key: str, expected_version: int, doc: dict
    ) -> bool:
        current = self.rows.get(str(key))
        current_version = int((current or {}).get("version", 0))
        if (current is None and expected_version != 0) or (
            current is not None and current_version != int(expected_version)
        ):
            return False
        self.rows[str(key)] = dict(doc)
        return True


class PostgresRiskBackend:
    """CAS backend stored in the existing ``NexusRuntimeState`` table."""

    def __init__(self, r_module=None, conn_factory=None) -> None:
        # Both parameters are accepted and ignored: the store takes its own
        # pooled connection per operation. They stay in the signature because
        # broker.py and the tests construct this class positionally.
        self._r = r_module
        self._conn_factory = conn_factory

    def get(self, key: str) -> Optional[dict]:
        from db import store
        from nexus_runtime_state import STATE_TABLE

        row = store.get(STATE_TABLE, str(key))
        if row is not None:
            row.pop("id", None)
        return row

    def compare_and_swap(
        self, key: str, expected_version: int, doc: dict
    ) -> bool:
        """version==0 is a first write (insert, fails if the row exists);
        otherwise a compare-and-swap.

        The ReQL form was ``.update(lambda cur: r.branch(cur['version']
        .default(-1).eq(expected), payload, {}))`` -- a DEEP MERGE of the
        payload on match, not a replace. ``store.update`` over a Selection
        carrying the identity AND the version guard is that same deep merge in
        one server-side statement, and it reports a no-change write as
        ``unchanged`` exactly as ReQL did.
        """
        from db import store
        from nexus_runtime_state import STATE_TABLE

        payload = {"id": str(key), **dict(doc)}
        if int(expected_version) == 0:
            try:
                res = store.insert(STATE_TABLE, payload, conflict="error")
                return not res["errors"]
            except Exception:
                return False
        rid = store.coerce_id(STATE_TABLE, str(key))
        # The dict predicate carries the guarded ::numeric compare, so a
        # version stored as 3.0 still matches an expected 3, as it did in ReQL.
        guard = store.filter(
            STATE_TABLE, {"version": int(expected_version)}
        ).where("id = %s", (rid,))
        result = store.update(STATE_TABLE, guard, payload)
        return int(result.get("replaced", 0) or 0) == 1


# The class was named for the driver, not for what it does. Both names refer
# to the same object so broker.py (a different port group) keeps importing.
RethinkRiskBackend = PostgresRiskBackend


class RiskStateStore:
    def __init__(self, backend) -> None:
        self._backend = backend

    def load_required(
        self, instance_id: str, account_id: str
    ) -> AccountRiskState:
        key = _account_key(instance_id, account_id)
        try:
            doc = self._backend.get(key)
        except Exception as exc:
            raise RiskStateUnavailable(
                f"risk state unavailable: {type(exc).__name__}"
            ) from exc
        if doc is None:
            raise RiskStateUnavailable("risk state missing")
        state = AccountRiskState.from_doc(doc)
        if (
            state.instance_id != str(instance_id)
            or state.account_id != str(account_id)
        ):
            raise RiskStateUnavailable("risk state identity mismatch")
        return state

    def save(self, state: AccountRiskState) -> AccountRiskState:
        key = _account_key(state.instance_id, state.account_id)
        try:
            current_doc = self._backend.get(key)
        except Exception as exc:
            raise RiskStateUnavailable(
                f"risk state unavailable: {type(exc).__name__}"
            ) from exc
        expected = 0
        if current_doc is not None:
            current = AccountRiskState.from_doc(current_doc)
            expected = current.version
            if state.version != current.version:
                raise RiskStateConflict(
                    f"stale risk state version {state.version}; "
                    f"current is {current.version}"
                )
            if state.high_water_equity < current.high_water_equity:
                raise ValueError("risk high-water mark cannot decrease")
        elif state.version != 0:
            raise RiskStateConflict("non-zero version has no persisted record")
        saved = replace(state, version=expected + 1)
        try:
            written = self._backend.compare_and_swap(
                key, expected, saved.to_doc()
            )
        except Exception as exc:
            raise RiskStateUnavailable(
                f"risk state persistence failed: {type(exc).__name__}"
            ) from exc
        if not written:
            raise RiskStateConflict("risk state compare-and-swap failed")
        return saved

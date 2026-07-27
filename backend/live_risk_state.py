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
DEFAULT_SLEEVE_COOLDOWN = timedelta(hours=24)
DEFAULT_LEVERAGED_SYMBOLS = frozenset(
    {"SQQQ", "TQQQ", "SPXU", "UPRO", "SOXL", "SOXS"}
)


class RiskStateUnavailable(RuntimeError):
    """Missing, corrupt, stale, or unreachable risk state."""


class RiskStateConflict(RuntimeError):
    """Another writer advanced the same risk record."""


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
) -> AccountRiskState:
    equity_value = _decimal(equity, "equity", positive=True)
    return AccountRiskState(
        instance_id=instance_id,
        account_id=account_id,
        version=0,
        high_water_equity=equity_value,
        last_equity=equity_value,
        observed_at=observed_at,
        max_order_notional=equity_value * Decimal("0.10"),
        max_symbol_notional=equity_value * Decimal("0.20"),
        max_leveraged_notional=equity_value * Decimal("0.10"),
    )


def evaluate_drawdown(
    state: AccountRiskState,
    fresh_equity,
    observed_at: datetime,
    *,
    soft_threshold=DEFAULT_SOFT_DRAWDOWN,
    hard_threshold=DEFAULT_HARD_DRAWDOWN,
    kill_threshold=DEFAULT_KILL_DRAWDOWN,
) -> AccountRiskState:
    observed = _utc(observed_at, "observed_at")
    if observed < state.observed_at:
        raise ValueError("equity observation predates persisted risk state")
    equity = _decimal(fresh_equity, "fresh_equity", positive=True)
    high = max(state.high_water_equity, equity)
    drawdown = (high - equity) / high
    soft = _decimal(soft_threshold, "soft_threshold")
    hard = _decimal(hard_threshold, "hard_threshold")
    kill = _decimal(kill_threshold, "kill_threshold")
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
    return replace(
        state,
        high_water_equity=high,
        last_equity=equity,
        observed_at=observed,
        drawdown=drawdown,
        level=level,
        new_exposure_allowed=(level == "normal"),
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


class RethinkRiskBackend:
    """CAS backend stored in the existing ``NexusRuntimeState`` table."""

    def __init__(self, r_module=None, conn_factory=None) -> None:
        if r_module is None or conn_factory is None:
            import nexus_runtime_state as runtime

            r_module = runtime._r
            conn_factory = runtime._conn
        self._r = r_module
        self._conn_factory = conn_factory

    def get(self, key: str) -> Optional[dict]:
        from nexus_runtime_state import DB_NAME, STATE_TABLE

        with self._conn_factory() as conn:
            row = (
                self._r.db(DB_NAME)
                .table(STATE_TABLE)
                .get(str(key))
                .run(conn)
            )
        if row is not None:
            row.pop("id", None)
        return row

    def compare_and_swap(
        self, key: str, expected_version: int, doc: dict
    ) -> bool:
        from nexus_runtime_state import DB_NAME, STATE_TABLE

        payload = {"id": str(key), **dict(doc)}
        with self._conn_factory() as conn:
            table = self._r.db(DB_NAME).table(STATE_TABLE)
            if int(expected_version) == 0:
                try:
                    table.insert(payload, conflict="error").run(conn)
                    return True
                except Exception:
                    return False
            result = table.get(str(key)).update(
                lambda current: self._r.branch(
                    current["version"].default(-1).eq(
                        int(expected_version)
                    ),
                    payload,
                    {},
                )
            ).run(conn)
        return int(result.get("replaced", 0) or 0) == 1


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

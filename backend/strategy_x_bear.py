"""Pure, default-off policy helpers for the Strategy X bear overlay.

This module deliberately has no broker, clock, filesystem, network, or
environment dependencies.  Callers supply already-visible prices and state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping


VALID_BEAR_SYSTEM_MODES = frozenset({"off", "shadow", "active"})
Q = 6

_MAX_SAFE_COUNT = 100_000
_FAST_MA_DEFAULT = 20
_MID_MA_DEFAULT = 50
_LONG_MA_DEFAULT = 200
_MIN_HISTORY_DEFAULT = 60
_MAX_HOLD_DEFAULT = 5
_COOLDOWN_DEFAULT = 10


class BearSystemStateError(RuntimeError):
    """Research state cannot be reconciled without claiming another owner."""


@dataclass(frozen=True)
class FastCrashSignal:
    stacked: bool
    fresh: bool
    below_fast: bool
    reason: str


@dataclass(frozen=True)
class KickerDecision:
    state: str
    engaged: bool
    bars: int
    cooldown: int
    reason: str


@dataclass(frozen=True)
class BearAllocation:
    targets: Mapping[str, float]
    applied: bool
    reason: str
    eligible: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))


def bear_system_mode(config) -> str:
    raw = str((config or {}).get("bear_system_mode", "off") or "off").strip().lower()
    return raw if raw in VALID_BEAR_SYSTEM_MODES else "off"


def _symbol(value) -> str:
    return str(value or "").strip().upper()


def _symbols(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(_symbol(value) for value in (values or []) if _symbol(value)))


def bear_system_universe(config) -> list[str]:
    cfg = config or {}
    if bear_system_mode(cfg) == "off":
        return []
    managers = _symbols(cfg.get("crisis_alpha_symbols") or [])
    return _symbols([
        cfg.get("bear_cash_symbol", "BIL"),
        *managers,
        cfg.get("bear_kicker_symbol", "SQQQ"),
    ])


def _finite_int(value) -> int | None:
    """Return a bounded integer without ever coercing untrusted input by int()."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer() or abs(number) > _MAX_SAFE_COUNT:
        return None
    return int(number)


def _config_count(config, key: str, default: int) -> int | None:
    cfg = config or {}
    return _finite_int(cfg.get(key, default))


def _positive_finite(values) -> list[float] | None:
    result = []
    for value in values:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        result.append(number)
    return result


def _ma(values: list[float]) -> float:
    return sum(values) / len(values)


def fast_crash_signal(closes, config) -> FastCrashSignal:
    """Return a fast-crash signal using independent today and yesterday states."""
    fast = _config_count(config, "bear_kicker_fast_ma_bars", _FAST_MA_DEFAULT)
    mid = _config_count(config, "bear_kicker_mid_ma_bars", _MID_MA_DEFAULT)
    long = _config_count(config, "bear_kicker_long_ma_bars", _LONG_MA_DEFAULT)
    if fast is None or mid is None or long is None:
        return FastCrashSignal(False, False, False, "invalid moving-average lookback")
    fast, mid, long = max(fast, 2), max(mid, 2), max(long, 2)
    longest = max(fast, mid, long)
    try:
        window = list(closes or [])[-(longest + 1):]
    except TypeError:
        return FastCrashSignal(False, False, False, "invalid close history")
    if len(window) < longest + 1:
        return FastCrashSignal(False, False, False, "insufficient close history")
    values = _positive_finite(window)
    if values is None:
        return FastCrashSignal(False, False, False, "nonfinite close history")

    def stacked_at(index: int) -> tuple[bool, bool]:
        price = values[index]
        averages = [_ma(values[index - bars + 1:index + 1])
                    for bars in (fast, mid, long)]
        return all(price < average for average in averages), price < averages[0]

    today, below_fast = stacked_at(len(values) - 1)
    yesterday, _ = stacked_at(len(values) - 2)
    fresh = today and not yesterday
    reason = "fresh stacked breakdown" if fresh else (
        "stacked breakdown" if today else "not stacked")
    return FastCrashSignal(today, fresh, below_fast, reason)


def _kicker_limits(config) -> tuple[int, int, bool]:
    max_bars = _config_count(config, "bear_kicker_max_bars", _MAX_HOLD_DEFAULT)
    cooldown_bars = _config_count(
        config, "bear_kicker_cooldown_bars", _COOLDOWN_DEFAULT)
    if max_bars is None or cooldown_bars is None:
        return _MAX_HOLD_DEFAULT, _COOLDOWN_DEFAULT, False
    return max(max_bars, 1), max(cooldown_bars, 0), True


def _counter(value, upper: int) -> tuple[int, bool]:
    parsed = _finite_int(value)
    if parsed is None:
        return 0, False
    return min(max(parsed, 0), upper), True


def _cooldown_decision(cooldown_bars: int, reason: str) -> KickerDecision:
    return KickerDecision("cooldown", False, 0, cooldown_bars, reason)


def advance_kicker(signal, *, state, bars, cooldown, risk_on, bull_held,
                   kicker_held, kicker_priceable, shadow, prior_targeted,
                   config) -> KickerDecision:
    """Advance the kicker's defensive state without inventing a real position."""
    max_bars, cooldown_bars, settings_valid = _kicker_limits(config)
    normalized_state = str(state or "idle").strip().lower()
    if normalized_state not in {"idle", "armed", "holding", "cooldown"}:
        normalized_state = "idle"
    held_bars, bars_valid = _counter(bars, max_bars)
    cooldown_left, cooldown_valid = _counter(cooldown, cooldown_bars)

    if not settings_valid:
        return _cooldown_decision(cooldown_bars, "invalid kicker limits")
    if not bars_valid or not cooldown_valid:
        return _cooldown_decision(cooldown_bars, "invalid persisted kicker counter")
    if not kicker_priceable:
        return _cooldown_decision(cooldown_bars, "kicker unavailable or unpriceable")
    if risk_on:
        return _cooldown_decision(cooldown_bars, "risk-on regime")
    if normalized_state == "idle" and kicker_held:
        return _cooldown_decision(cooldown_bars, "adopted kicker has unknown age")
    if (normalized_state == "holding" and not shadow and not kicker_held
            and not prior_targeted):
        return _cooldown_decision(cooldown_bars, "stale active holding state")
    if normalized_state == "holding":
        if not signal.stacked or not signal.below_fast:
            return _cooldown_decision(cooldown_bars, "kicker recovery exit")
        if held_bars >= max_bars:
            return _cooldown_decision(cooldown_bars, "kicker maximum hold reached")
        return KickerDecision("holding", True, held_bars + 1, cooldown_left,
                              "kicker holding")
    if normalized_state == "cooldown":
        remaining = max(cooldown_left - 1, 0)
        next_state = "cooldown" if remaining else "idle"
        return KickerDecision(next_state, False, 0, remaining, "kicker cooldown")
    if normalized_state == "idle":
        if signal.fresh:
            return KickerDecision("armed", False, 0, 0, "fresh breakdown armed")
        return KickerDecision("idle", False, 0, 0, "no fresh breakdown")
    if bull_held:
        return _cooldown_decision(cooldown_bars, "bull holding blocks kicker entry")
    if signal.stacked and signal.below_fast:
        return KickerDecision("holding", True, 1, 0, "armed breakdown confirmed")
    return KickerDecision("idle", False, 0, 0, "armed breakdown did not confirm")


def _finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_price(prices, symbol: str) -> bool:
    try:
        price = _finite_number((prices or {}).get(symbol))
    except AttributeError:
        return False
    return price is not None and price > 0


def bear_role_conflict(config) -> str:
    """Return the first deterministic collision among normalized bear roles."""
    cfg = config or {}
    roles = (
        ("cash", _symbols([cfg.get("bear_cash_symbol", "BIL")])),
        ("kicker", _symbols([cfg.get("bear_kicker_symbol", "SQQQ")])),
        ("manager", _symbols(cfg.get("crisis_alpha_symbols") or [])),
        ("filter", _symbols([cfg.get("core_filter_symbol", "QQQ")])),
        ("core", _symbols([
            cfg.get("core_bull_symbol", "TQQQ"),
            cfg.get("core_chop_symbol", "SPY"),
        ])),
        ("legacy-bear", _symbols([cfg.get("core_bear_symbol", "")])),
        ("commodity", _symbols(cfg.get("commodity_symbols") or [])),
    )
    owners: dict[str, str] = {}
    for role, symbols in roles:
        for symbol in symbols:
            owner = owners.get(symbol)
            if owner is not None and owner != role:
                return f"{symbol} assigned to both {owner} and {role}"
            owners[symbol] = role
    return ""


def eligible_crisis_alpha(closes_by_symbol, prices, config) -> tuple[str, ...]:
    """Choose priceable crisis managers in the configured, normalized order."""
    minimum = _config_count(
        config, "crisis_alpha_min_history_bars", _MIN_HISTORY_DEFAULT)
    if minimum is None:
        return ()
    minimum = max(minimum, 1)
    try:
        histories = closes_by_symbol or {}
    except (AttributeError, TypeError):
        return ()
    selected = []
    for symbol in _symbols((config or {}).get("crisis_alpha_symbols") or []):
        try:
            history = list(histories.get(symbol, ()))
            enough_history = (len(history) >= minimum
                              and _positive_finite(history[-minimum:]) is not None)
        except (AttributeError, TypeError):
            enough_history = False
        if _positive_price(prices, symbol) and enough_history:
            selected.append(symbol)
    return tuple(selected)


def _baseline(base_targets) -> tuple[dict[str, float] | None, bool]:
    try:
        copied = dict(base_targets or {})
    except (TypeError, ValueError):
        return None, False
    for weight in copied.values():
        number = _finite_number(weight)
        if number is None or number < 0:
            return copied, False
    return copied, True


def _configured_pct(config, key: str, default: float) -> float | None:
    value = _finite_number((config or {}).get(key, default))
    if value is None:
        return None
    return min(max(value, 0.0), 1.0)


def _floor_weight(weight: float) -> float:
    return math.floor(weight * (10 ** Q)) / (10 ** Q)


def _unchanged(base: dict[str, float], reason: str,
               eligible: tuple[str, ...] = ()) -> BearAllocation:
    return BearAllocation(base, False, reason, eligible)


def plan_bear_overlay(base_targets, *, risk_on, config, eligible_symbols,
                      kicker_engaged, prices) -> BearAllocation:
    """Replace only the risk-off chop allocation with defensive bear sleeves."""
    baseline, baseline_valid = _baseline(base_targets)
    if baseline is None:
        return BearAllocation({}, False, "invalid baseline targets", ())
    if not baseline_valid:
        return _unchanged(baseline, "invalid baseline targets")
    cfg = config or {}
    declared_eligible = tuple(_symbols(cfg.get("crisis_alpha_symbols") or []))
    allowed = set(_symbols(eligible_symbols))
    eligible = tuple(symbol for symbol in declared_eligible if symbol in allowed)
    if risk_on:
        return _unchanged(baseline, "risk-on regime", eligible)
    if bear_system_mode(cfg) != "active":
        return _unchanged(baseline, "bear system is not active", eligible)
    if _symbol(cfg.get("core_bear_symbol", "")):
        return _unchanged(baseline, "legacy bear configuration is enabled", eligible)
    conflict = bear_role_conflict(cfg)
    if conflict:
        return _unchanged(baseline, f"role conflict: {conflict}", eligible)
    chop = _symbol(cfg.get("core_chop_symbol", "SPY"))
    cash = _symbol(cfg.get("bear_cash_symbol", "BIL"))
    kicker = _symbol(cfg.get("bear_kicker_symbol", "SQQQ"))
    if not chop or chop not in baseline:
        return _unchanged(baseline, "missing chop target", eligible)
    if not cash or not _positive_price(prices, cash):
        return _unchanged(baseline, "missing or unpriceable bear cash", eligible)
    alpha_pct = _configured_pct(cfg, "crisis_alpha_pct", 0.20)
    kicker_pct = _configured_pct(cfg, "bear_kicker_pct", 0.05)
    if alpha_pct is None or kicker_pct is None:
        return _unchanged(baseline, "nonfinite bear sleeve weight", eligible)
    protected = {cash, kicker, *eligible}
    if any(symbol in baseline and symbol != chop for symbol in protected):
        return _unchanged(baseline, "overlay symbol already has a baseline target", eligible)

    targets = dict(baseline)
    defensive_budget = targets.pop(chop)
    remaining = defensive_budget
    if eligible:
        manager_budget = min(alpha_pct, remaining)
        per_manager = _floor_weight(manager_budget / len(eligible))
        for symbol in eligible:
            targets[symbol] = per_manager
        remaining -= per_manager * len(eligible)
    if kicker_engaged and _positive_price(prices, kicker):
        if kicker_pct > 0 and kicker_pct <= remaining:
            targets[kicker] = kicker_pct
            remaining -= kicker_pct
    if remaining > 0:
        targets[cash] = round(remaining, Q)

    total_before = sum(baseline.values())
    total_after = sum(targets.values())
    overlay_before = baseline[chop]
    overlay_after = sum(
        weight for symbol, weight in targets.items() if symbol not in baseline)
    valid_targets = all(
        (number := _finite_number(weight)) is not None and number >= 0
        for weight in targets.values())
    baseline_preserved = all(
        targets.get(symbol) == weight
        for symbol, weight in baseline.items() if symbol != chop)
    if (not valid_targets or not baseline_preserved
            or round(total_after, Q) != round(total_before, Q)
            or round(overlay_after, Q) != round(overlay_before, Q)):
        return _unchanged(baseline, "bear allocation invariants failed", eligible)
    return BearAllocation(targets, True, "bear overlay applied", eligible)

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

#: The regime ladder, richest exposure first. `full` and `defensive` are the
#: two states the shipped binary system already had; `caution` and
#: `recovering` are the transition rungs that exist because the slow filter is
#: late at BOTH ends — it de-levers ~22% into a decline and re-levers weeks
#: after a bottom.
REGIME_STATES = ("full", "caution", "defensive", "recovering")
_REGIME_ENVELOPE_VERSION = 1
_REGIME_FAST_MA_DEFAULT = 20
_REGIME_MID_MA_DEFAULT = 50
_REGIME_CONFIRM_DEFAULT = 2
_REGIME_TRANSITION_FRACTION_DEFAULT = 0.5

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
class RegimeSignal:
    """What the two fast timescales and the slow filter each say this bar.

    `fast_bad` and `fast_good` are deliberately NOT complements: the price must
    be below BOTH moving averages to be a confirmed break and above BOTH to be
    a confirmed reclaim, so the band between them is a dead zone. That single
    choice gives the asymmetry the design needs, in both tapes and without a
    second threshold:

      rising tape  (MA_fast above MA_mid) — de-levering waits for the DEEPER
                    MA_mid break, so an ordinary bull dip through MA_fast does
                    not halve the core; re-levering only needs MA_fast back.
      falling tape (MA_fast below MA_mid) — de-levering fires on the EARLY
                    MA_fast break; re-levering must clear the higher MA_mid,
                    so a bear-market poke does not buy the falling knife.
    """

    fast_bad: bool
    fast_good: bool
    slow_on: bool
    vol_unsafe: bool
    emergency: bool
    reason: str


@dataclass(frozen=True)
class RegimeState:
    """The persisted ladder position. `observation_id` is a SESSION clock, not
    a call counter: these strategies run many times inside one session at 15m
    cadence, and a holiday row repeats the last completed close."""

    state: str
    confirm_kind: str
    confirm_count: int
    observation_id: int


@dataclass(frozen=True)
class RegimeDecision:
    state: str
    risk_fraction: float
    confirm_kind: str
    confirm_count: int
    observation_id: int
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
                   config, observation_id=None,
                   last_observation_id=None) -> KickerDecision:
    """Advance the kicker's defensive state without inventing a real position.

    `observation_id` is the SESSION clock. At 15m cadence this runs ~26 times a
    session and a holiday row repeats the last completed close, so counting
    every call spent a five-bar hold inside one afternoon. When the observation
    is unchanged the counters are handed back untouched — but only the
    counters: every safety exit still fires, because a duplicate observation is
    not a reason to keep holding an inverse fund whose regime just flipped or
    whose price went away. A state CHANGE is therefore always honoured; only a
    same-state counter tick is suppressed.
    """
    decision = _kicker_transition(
        signal, state=state, bars=bars, cooldown=cooldown, risk_on=risk_on,
        bull_held=bull_held, kicker_held=kicker_held,
        kicker_priceable=kicker_priceable, shadow=shadow,
        prior_targeted=prior_targeted, config=config)
    current_observation = _finite_int(observation_id)
    last_observation = _finite_int(last_observation_id)
    if current_observation is None or current_observation != last_observation:
        return decision
    normalized = str(state or "idle").strip().lower()
    if decision.state != normalized:
        return decision
    max_bars, cooldown_bars, _ = _kicker_limits(config)
    held_bars, bars_valid = _counter(bars, max_bars)
    cooldown_left, cooldown_valid = _counter(cooldown, cooldown_bars)
    if not bars_valid or not cooldown_valid:
        return decision
    return KickerDecision(decision.state, decision.engaged, held_bars,
                          cooldown_left, "observation unchanged")


def _kicker_transition(signal, *, state, bars, cooldown, risk_on, bull_held,
                       kicker_held, kicker_priceable, shadow, prior_targeted,
                       config) -> KickerDecision:
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
    return _cooldown_decision(cooldown_bars, "armed breakdown did not confirm")


def _regime_fraction(state: str, config) -> float:
    """Exposure the state carries, as a fraction of the regime core budget."""
    if state == "full":
        return 1.0
    if state == "defensive":
        return 0.0
    value = _finite_number((config or {}).get(
        "bear_regime_transition_risk_fraction",
        _REGIME_TRANSITION_FRACTION_DEFAULT))
    if value is None:
        value = _REGIME_TRANSITION_FRACTION_DEFAULT
    return min(max(value, 0.0), 1.0)


def _regime_enabled(config) -> bool:
    return bool((config or {}).get("bear_regime_enabled", False))


def dual_timescale_signal(closes, *, slow_on, vol_unsafe,
                          config) -> RegimeSignal:
    """Read the two fast moving averages without touching `core_signal`.

    The slow verdict is passed IN rather than recomputed: `core_signal` already
    owns the MA200 and the volatility gate, it is the tested boundary, and a
    second implementation of it here would drift.
    """
    unsafe = bool(vol_unsafe)
    on = bool(slow_on)
    fast = _config_count(config, "bear_regime_fast_ma_bars",
                         _REGIME_FAST_MA_DEFAULT)
    mid = _config_count(config, "bear_regime_mid_ma_bars",
                        _REGIME_MID_MA_DEFAULT)
    if fast is None or mid is None:
        return RegimeSignal(False, False, on, unsafe, True,
                            "invalid regime moving-average lookback")
    fast, mid = max(fast, 2), max(mid, 2)
    try:
        window = list(closes or [])[-max(fast, mid):]
    except TypeError:
        return RegimeSignal(False, False, on, unsafe, True,
                            "invalid close history")
    if len(window) < max(fast, mid):
        # A cold start is not an emergency — the shipped filter already fails
        # closed on short history, and forcing DEFENSIVE here would sell the
        # book every time the bar cache is trimmed.
        return RegimeSignal(False, False, on, unsafe, False,
                            f"insufficient history: {len(window)} closes")
    values = _positive_finite(window)
    if values is None:
        return RegimeSignal(False, False, on, unsafe, True,
                            "nonfinite close history")
    price = values[-1]
    fast_ma = _ma(values[-fast:])
    mid_ma = _ma(values[-mid:])
    lower, upper = min(fast_ma, mid_ma), max(fast_ma, mid_ma)
    bad = price < lower
    good = price > upper
    reason = ("below MA%d and MA%d" % (fast, mid) if bad else
              "above MA%d and MA%d" % (fast, mid) if good else
              "between MA%d and MA%d" % (fast, mid))
    return RegimeSignal(bad, good, on, unsafe, unsafe, reason)


def _regime_refusal(config, observation_id, reason) -> RegimeDecision:
    """Every failure lands here, and every failure lands on DEFENSIVE.

    Never on FULL: an unreadable counter, a state name nobody wrote, or a clock
    that moved backwards are all states in which the safe answer is to hold no
    leverage, and the ladder must not be able to invent exposure out of
    corruption.
    """
    return RegimeDecision("defensive", 0.0, "", 0,
                          _finite_int(observation_id) or 0, reason)


def advance_regime_state(signal, previous, *, observation_id,
                         config) -> RegimeDecision:
    """Advance the four-state ladder by at most one rung.

        FULL  --mid/fast break confirmed-->  CAUTION
        CAUTION --reclaim confirmed-->       FULL
        FULL/CAUTION --slow filter off-->    DEFENSIVE   (immediate)
        DEFENSIVE --reclaim confirmed-->     RECOVERING
        RECOVERING --break confirmed-->      DEFENSIVE
        DEFENSIVE/RECOVERING --slow on-->    FULL        (immediate)

    The slow edges are immediate because `core_signal` is already a confirmed,
    gated verdict — adding a second confirmation on top of it would just make
    the shipped behaviour later than it is today. The fast edges are confirmed
    because they are, by construction, noisier.
    """
    def fraction_for(name):
        return _regime_fraction(name, config)

    current = _finite_int(observation_id)
    clock_usable = current is not None and current >= 0
    if not _regime_enabled(config) or not clock_usable:
        # No rungs and no counters — byte-identical to the shipped binary
        # system. An unusable clock lands here too rather than on a refusal:
        # confirmations cannot be counted without a clock, but the slow filter
        # is still a complete answer, and forcing DEFENSIVE through a bull on a
        # malformed session label would be a far worse failure than deferring
        # to the behaviour that already shipped.
        state = "full" if signal.slow_on and not signal.emergency else "defensive"
        return RegimeDecision(
            state, fraction_for(state), "", 0, current if clock_usable else 0,
            "regime ladder disabled" if clock_usable
            else "unusable observation clock")

    name = getattr(previous, "state", None)
    if not isinstance(name, str) or name.strip().lower() not in REGIME_STATES:
        return _regime_refusal(config, current, "unknown persisted regime state")
    name = name.strip().lower()
    last = _finite_int(getattr(previous, "observation_id", None))
    if last is None or last < 0:
        return _regime_refusal(config, current, "invalid persisted observation")
    if current < last:
        return _regime_refusal(config, current, "observation clock moved back")

    kind = getattr(previous, "confirm_kind", "")
    kind = kind if isinstance(kind, str) else ""
    count = _finite_int(getattr(previous, "confirm_count", None))
    if count is None or count < 0:
        kind, count = "", 0

    if current == last:
        return RegimeDecision(name, fraction_for(name), kind, count, current,
                              "observation unchanged")

    needed = _config_count(config, "bear_regime_confirm_bars",
                           _REGIME_CONFIRM_DEFAULT)
    if needed is None:
        return _regime_refusal(config, current, "invalid confirmation setting")
    needed = max(needed, 1)

    if signal.emergency:
        return RegimeDecision("defensive", 0.0, "", 0, current,
                              "emergency: " + signal.reason)

    # ── de-risking is immediate; re-risking is staged ──
    # Not a symmetry for its own sake. The slow filter whipsaws hardest at a
    # bottom — 2018 Q4 flipped risk-on and back off three times, 2022 H1 ten
    # times, and 2025 flipped on 03-25, one session before the April crash.
    # Sending DEFENSIVE straight to FULL bought the whole levered core back for
    # every one of those false dawns. Going down costs nothing if it is wrong;
    # going up costs the next leg of the decline.
    if name in ("full", "caution") and not signal.slow_on:
        return RegimeDecision("defensive", 0.0, "", 0, current,
                              "slow filter risk-off")
    if name == "defensive" and signal.slow_on:
        return RegimeDecision("recovering", fraction_for("recovering"), "", 0,
                              current, "slow filter risk-on: staging re-entry")
    if name == "recovering" and not signal.slow_on and not signal.fast_good:
        # Immediate, and NOT a confirmed edge. This state is a half position
        # held on the strength of a reclaim; once both the slow filter and the
        # reclaim are gone there is nothing holding it up, and spending two
        # confirmation sessions at 38.5% TQQQ inside a decline cost more than
        # the whipsaw the confirmation was there to avoid.
        return RegimeDecision("defensive", 0.0, "", 0, current,
                              "reclaim lost while the slow filter is off")

    # ── the confirmed edges ──
    wanted = {"full": ("mid_break", signal.fast_bad, "caution"),
              "caution": ("mid_reclaim", signal.fast_good, "full"),
              "defensive": ("fast_reclaim", signal.fast_good, "recovering"),
              "recovering": ("slow_reclaim", signal.slow_on, "full")}[name]
    edge, evidence, destination = wanted
    if not evidence:
        return RegimeDecision(name, fraction_for(name), "", 0, current,
                              f"holding {name}: {signal.reason}")
    count = count + 1 if kind == edge else 1
    if count < needed:
        return RegimeDecision(name, fraction_for(name), edge, count, current,
                              f"{edge} {count}/{needed}")
    return RegimeDecision(destination, fraction_for(destination), "", 0,
                          current, f"{edge} confirmed")


def blend_target_books(risk_targets, defensive_targets, risk_fraction):
    """Convex blend of the risk-on and defensive books, or None if unusable.

    Returning None rather than a partial book is deliberate: a silently
    truncated weight set asks for a clip the account cannot fund, and the
    caller already has a correct book to fall back to.
    """
    fraction = _finite_number(risk_fraction)
    if fraction is None or not 0.0 <= fraction <= 1.0:
        return None
    books = []
    for book in (risk_targets, defensive_targets):
        try:
            copied = {_symbol(symbol): weight for symbol, weight in
                      dict(book or {}).items()}
        except (AttributeError, TypeError, ValueError):
            return None
        for weight in copied.values():
            number = _finite_number(weight)
            if number is None or number < 0:
                return None
        books.append({symbol: float(weight) for symbol, weight in copied.items()})
    risk, defensive = books
    if fraction == 1.0:
        return risk
    if fraction == 0.0:
        return defensive
    # NOT rounded to the quantization grid. Rounding each leg independently
    # loses up to half an ulp per symbol, and the one invariant this function
    # must keep is that the blend of two books summing to the same total sums
    # to that same total.
    blended = {}
    for symbol in set(risk) | set(defensive):
        weight = (risk.get(symbol, 0.0) * fraction
                  + defensive.get(symbol, 0.0) * (1.0 - fraction))
        if weight > 0:
            blended[symbol] = weight
    return blended


def encode_regime_state(state, *, authority, fingerprint) -> dict:
    """One versioned envelope, carrying who wrote it and under what settings."""
    return {
        "v": _REGIME_ENVELOPE_VERSION,
        "auth": str(authority),
        "fp": str(fingerprint),
        "state": state.state,
        "kind": state.confirm_kind,
        "count": state.confirm_count,
        "obs": state.observation_id,
    }


def decode_regime_state(raw, *, authority, fingerprint):
    """Return the persisted state, or None when it cannot be trusted.

    None means "start fresh", and a fresh start is DEFENSIVE at the caller.
    Shadow state can never be read under active authority: a mode flip must not
    silently promote a counter that was never allowed to trade.
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("v") != _REGIME_ENVELOPE_VERSION:
        return None
    if raw.get("auth") != str(authority) or raw.get("fp") != str(fingerprint):
        return None
    name = raw.get("state")
    if not isinstance(name, str) or name not in REGIME_STATES:
        return None
    kind = raw.get("kind", "")
    if not isinstance(kind, str):
        return None
    count = _finite_int(raw.get("count"))
    observation = _finite_int(raw.get("obs"))
    if count is None or count < 0 or observation is None or observation < 0:
        return None
    return RegimeState(name, kind, count, observation)


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


def _fits_budget(requested: float, remaining: float) -> bool:
    """Allow only the few ulps of error introduced by prior float subtraction."""
    tolerance = 4 * max(math.ulp(requested), math.ulp(remaining))
    return requested <= remaining + tolerance


def _unchanged(base: dict[str, float], reason: str,
               eligible: tuple[str, ...] = ()) -> BearAllocation:
    return BearAllocation(base, False, reason, eligible)


def plan_bear_overlay(base_targets, *, risk_on, config, eligible_symbols,
                      kicker_engaged, prices,
                      defensive_budget=None) -> BearAllocation:
    """Replace only the risk-off chop allocation with defensive bear sleeves.

    `defensive_budget` is how much of the chop target the bear sleeves may
    claim. It exists because `plan_targets` merges two different things into
    one SPY number: the true regime core, and the fallback for a satellite
    sleeve that found no names. Under the intended 10/10/80 book with Graph
    silent, that SPY target is 90% — and replacing all of it converts the stock
    sleeve's fallback into managed futures, which is not what 10% Graph means.
    Passing the configured 80% core replaces only the core. Omitted, the
    deployed whole-target behaviour is preserved exactly.
    """
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

    chop_target = baseline[chop]
    if defensive_budget is None:
        claimed = chop_target
    else:
        claimed = _finite_number(defensive_budget)
        if claimed is None or claimed <= 0:
            return _unchanged(baseline, "invalid defensive budget", eligible)
        if claimed > chop_target + 4 * math.ulp(chop_target):
            return _unchanged(
                baseline, "defensive budget exceeds the chop target", eligible)
    targets = dict(baseline)
    leftover = round(chop_target - claimed, Q)
    if leftover > 0:
        targets[chop] = leftover
    else:
        targets.pop(chop)
    remaining = claimed
    if eligible:
        manager_budget = min(alpha_pct, remaining)
        per_manager = _floor_weight(manager_budget / len(eligible))
        for symbol in eligible:
            targets[symbol] = per_manager
        remaining -= per_manager * len(eligible)
    if kicker_engaged and _positive_price(prices, kicker):
        if kicker_pct > 0 and _fits_budget(kicker_pct, remaining):
            targets[kicker] = kicker_pct
            remaining -= kicker_pct
    if remaining > 0:
        targets[cash] = round(remaining, Q)

    total_before = sum(baseline.values())
    total_after = sum(targets.values())
    overlay_before = claimed
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

# Strategy X Bear System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off, shadowable Strategy X bear subsystem, then evaluate its bounded settings grid with point-in-time, next-bar backtests across frozen bear, bull, recovery, and chop windows.

**Architecture:** Keep all bear signal, state-transition, eligibility, and allocation math pure in `backend/strategy_x_bear.py`. The existing `StrategyX.run_once` orchestrator supplies point-in-time histories, persists namespaced state, records shadow telemetry, and sends either unchanged baseline targets or research-authorized active overlay targets through the existing order-sizing path. The repository's existing production backtest engine and authenticated API drive exactly five continuous runs and persist the authoritative ledger; settings are frozen before launch, selection uses only slices ending in 2022, and 2023+ slices are reported separately. No second simulator or fill engine is introduced.

**Tech Stack:** Python 3, dataclasses, pytest, the existing `POST /backtests` API, `backend/engines/backtest_engine.py`, `backend/broker.py`, `BacktestResults` graph data, and the existing Strategy X API helpers.

**Spec:** `docs/superpowers/specs/2026-08-25-strategy-x-bear-system-design.md`

## Global Constraints

- Work directly on the already-isolated `main-session` linked worktree on branch `main`; the user explicitly approved direct work on `main`.
- After local review and verification, push `main`; the user confirmed that this auto-deploys. Wait for the deployment and verify the deployed code before launching evidence runs. The user explicitly authorized edits to the non-real-money Strategy X document (ID 198) and instance `strategy-x`; snapshot it first and touch no other strategy document or brokerage setting.
- Follow strict red-green TDD: write and observe each behavior test fail before its production implementation.
- Before modifying an existing function, class, or method, run GitNexus upstream impact analysis for that exact symbol and report direct callers, affected processes, and risk. Warn before proceeding if the result is HIGH or CRITICAL. If the index remains degraded, record risk as UNKNOWN and manually inspect callers and covering tests.
- Before every commit, run `git diff --check` and `gitnexus_detect_changes()`; stage only files owned by the task.
- Preserve unrelated untracked files, including `bt143282.json`, `bt186463.json`, and `scripts/_deploy_then_*`.
- `bear_system_mode` defaults to `off`; invalid values normalize to `off`; `shadow` must emit the same decisions and `_nexus_position_sizes` as `off` for identical inputs.
- `active` may change targets only when `StrategyX.run_once(..., mode="backtest")`; every other runtime treats an active request as shadow/refused.
- The new subsystem may redistribute only the risk-off weight assigned to `core_chop_symbol`; it must never increase total targets, TQQQ, SQQQ, or cash demand after a failure.
- `core_bear_symbol` and the new subsystem are mutually exclusive; a legacy bear configuration retains existing behavior unchanged.
- All histories pass through `pit_daily_closes`; quotes override the last visible close; decisions use only data visible at the decision time; simulated orders fill on the next bar.
- Research arms run continuously from one common inception and retain portfolio, pending orders, provenance, and cache state across window boundaries; window metrics are ledger slices, not fresh simulations.
- Only provenance-recorded bear holdings may be managed or unwound. An unprovenanced holding or a symbol-role collision suppresses the overlay rather than claiming another strategy's position.
- Use the production engine's nominal execution-cost model identically for every arm and record its version and realized fees/spread/slippage; the API does not support an artificial 2-basis-point nominal override. Search windows end on `2022-12-31`; windows beginning `2023-01-01` or later cannot affect candidate selection.
- Start no more than five new API backtests for this task. The five frozen arms are: `off`; `shadow`; defense-only (`crisis_alpha_pct=0.20`, `bear_kicker_pct=0`); conservative full (`crisis_alpha_pct=0.10`, `bear_kicker_pct=0.025`, `bear_kicker_max_bars=3`, `bear_kicker_cooldown_bars=10`); and central full (`crisis_alpha_pct=0.20`, `bear_kicker_pct=0.05`, `bear_kicker_max_bars=5`, `bear_kicker_cooldown_bars=10`). All other Strategy X settings and the production cost model stay identical.
- Freeze all five configurations, common dates, windows, and selection key before launch. Select the diagnostic best tested active arm using only slices ending by `2022-12-31`; report 2023+ slices separately. Because the five full-period ledgers are generated together and the search is intentionally bounded, call 2023+ a pseudo-out-of-sample diagnostic—not a pristine locked holdout or exhaustive optimization.
- A completed implementation is not an investment-performance claim. Report separate defense and kicker verdicts; if a component does not clear every applicable gate, leave it disabled, identify the diagnostic best candidate, and label it non-promotable.

---

### Task 1: Pure Bear Policy and State Machine

**Files:**
- Create: `backend/strategy_x_bear.py`
- Create: `backend/tests/test_strategy_x_bear.py`

**Interfaces:**
- Consumes: plain configuration mappings, visible QQQ closes, effective prices, current state values, and baseline target mappings.
- Produces: `VALID_BEAR_SYSTEM_MODES`, `BearSystemStateError`, `FastCrashSignal`, `KickerDecision`, `BearAllocation`, `bear_system_mode(config) -> str`, `bear_system_universe(config) -> list[str]`, `bear_role_conflict(config) -> str`, `fast_crash_signal(closes, config) -> FastCrashSignal`, `advance_kicker(signal, *, state, bars, cooldown, risk_on, bull_held, kicker_held, kicker_priceable, shadow, prior_targeted, config) -> KickerDecision`, `eligible_crisis_alpha(closes_by_symbol, prices, config) -> tuple[str, ...]`, and `plan_bear_overlay(base_targets, *, risk_on, config, eligible_symbols, kicker_engaged, prices) -> BearAllocation`.

- [ ] **Step 1: Write failing normalization, universe, and signal tests**

Add tests with literal expected values. The break caught is accidental activation, symbol spelling drift, or a fresh-event calculation that uses the current bar in the prior comparison.

```python
from strategy_x_bear import (
    advance_kicker,
    bear_system_mode,
    bear_system_universe,
    eligible_crisis_alpha,
    fast_crash_signal,
    plan_bear_overlay,
)


def cfg(**overrides):
    value = {
        "bear_system_mode": "off",
        "bear_cash_symbol": "BIL",
        "crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"],
        "crisis_alpha_pct": 0.20,
        "crisis_alpha_min_history_bars": 60,
        "bear_kicker_symbol": "SQQQ",
        "bear_kicker_pct": 0.05,
        "bear_kicker_fast_ma_bars": 20,
        "bear_kicker_mid_ma_bars": 50,
        "bear_kicker_long_ma_bars": 200,
        "bear_kicker_max_bars": 5,
        "bear_kicker_cooldown_bars": 10,
        "core_filter_symbol": "QQQ",
        "core_bull_symbol": "TQQQ",
        "core_chop_symbol": "SPY",
        "core_bear_symbol": "",
    }
    value.update(overrides)
    return value


def test_invalid_mode_is_off_and_off_declares_no_extra_symbols():
    assert bear_system_mode(cfg(bear_system_mode="ACTIVE-ish")) == "off"
    assert bear_system_universe(cfg()) == []


def test_shadow_universe_normalizes_and_deduplicates_declared_order():
    got = bear_system_universe(cfg(
        bear_system_mode=" shadow ", bear_cash_symbol=" bil ",
        crisis_alpha_symbols=["dbmf", " BIL ", "kmlm", "dbmf"],
        bear_kicker_symbol=" sqqq "))
    assert got == ["BIL", "DBMF", "KMLM", "SQQQ"]


def test_fresh_breakdown_compares_two_point_in_time_states():
    closes = [300.0] * 201
    closes[-2] = 301.0
    closes[-1] = 100.0
    signal = fast_crash_signal(closes, cfg())
    assert signal.stacked is True
    assert signal.fresh is True
    assert signal.below_fast is True


def test_signal_refuses_insufficient_or_nonfinite_history():
    short = fast_crash_signal([100.0] * 200, cfg())
    bad = fast_crash_signal([100.0] * 200 + [float("nan")], cfg())
    bad_lookback = fast_crash_signal([100.0] * 260,
        cfg(bear_kicker_fast_ma_bars=float("nan")))
    assert (short.stacked, short.fresh) == (False, False)
    assert (bad.stacked, bad.fresh) == (False, False)
    assert (bad_lookback.stacked, bad_lookback.fresh) == (False, False)
```

- [ ] **Step 2: Run the new tests and observe the import failure**

Run: `python3 -m pytest backend/tests/test_strategy_x_bear.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'strategy_x_bear'`.

- [ ] **Step 3: Implement immutable results and normalized pure helpers**

Create the module with no broker, clock, filesystem, network, or environment access. Use these exact result fields and normalization rules:

```python
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

VALID_BEAR_SYSTEM_MODES = frozenset({"off", "shadow", "active"})
Q = 6


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
    return list(dict.fromkeys(_symbol(v) for v in (values or []) if _symbol(v)))


def bear_system_universe(config) -> list[str]:
    cfg = config or {}
    if bear_system_mode(cfg) == "off":
        return []
    return _symbols([
        cfg.get("bear_cash_symbol", "BIL"),
        *(cfg.get("crisis_alpha_symbols") or []),
        cfg.get("bear_kicker_symbol", "SQQQ"),
    ])
```

Implement one finite integer parser used by every MA, minimum-history, maximum-hold, cooldown, and counter input so `NaN`, infinity, strings, and huge values never reach an unsafe `int(...)`. Invalid configured lookbacks refuse the signal/eligibility result; invalid max-hold/cooldown settings produce a non-engaged safe cooldown using documented defaults. Implement `bear_role_conflict` to return a deterministic reason when cash, kicker, manager, filter, core, legacy-bear, or commodity roles collide after normalization; an empty string means valid. Implement `fast_crash_signal` by clamping all three valid MA lengths to at least 2, requiring `max_lookback + 1` positive finite closes, and independently calculating today's and yesterday's stacked states. `fresh` is `today and not yesterday`; `below_fast` is today's price below today's fast average. Never slice beyond the supplied closes.

- [ ] **Step 4: Add failing state-machine tests**

```python
def signal(*, stacked=True, fresh=False, below_fast=True):
    from strategy_x_bear import FastCrashSignal
    return FastCrashSignal(stacked, fresh, below_fast, "fixture")


def test_kicker_arms_then_holds_for_exactly_five_target_sessions():
    c = cfg()
    armed = advance_kicker(signal(fresh=True), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=True, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=c)
    assert (armed.state, armed.engaged, armed.bars) == ("armed", False, 0)
    held = advance_kicker(signal(), state=armed.state, bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=c)
    assert (held.state, held.engaged, held.bars) == ("holding", True, 1)
    for expected in (2, 3, 4, 5):
        held = advance_kicker(signal(), state=held.state, bars=held.bars,
            cooldown=held.cooldown, risk_on=False, bull_held=False,
            kicker_held=True, kicker_priceable=True, shadow=False,
            prior_targeted=True, config=c)
        assert (held.state, held.engaged, held.bars) == ("holding", True, expected)
    exited = advance_kicker(signal(), state=held.state, bars=held.bars,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=True,
        kicker_priceable=True, shadow=False, prior_targeted=True, config=c)
    assert (exited.state, exited.engaged, exited.cooldown) == ("cooldown", False, 10)


def test_recovery_exits_and_cooldown_spends_ten_decision_sessions():
    out = advance_kicker(signal(stacked=False, below_fast=False),
        state="holding", bars=2, cooldown=0, risk_on=False, bull_held=False,
        kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=True, config=cfg())
    assert (out.state, out.engaged, out.cooldown) == ("cooldown", False, 10)
    for remaining in range(9, -1, -1):
        out = advance_kicker(signal(stacked=False, below_fast=False),
            state=out.state, bars=out.bars, cooldown=out.cooldown,
            risk_on=False, bull_held=False, kicker_held=False,
            kicker_priceable=True, shadow=False, prior_targeted=False,
            config=cfg())
        assert out.cooldown == remaining
    assert out.state == "idle"


def test_cache_reset_adopts_a_real_kicker_holding_but_never_invents_one():
    adopted = advance_kicker(signal(fresh=False), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=True,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    absent = advance_kicker(signal(fresh=False), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (adopted.state, adopted.engaged, adopted.cooldown) == \
        ("cooldown", False, 10)
    assert (absent.state, absent.engaged) == ("idle", False)


def test_armed_state_never_flips_directly_from_tqqq_to_sqqq():
    out = advance_kicker(signal(), state="armed", bars=0, cooldown=0,
        risk_on=False, bull_held=True, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (out.state, out.engaged) == ("cooldown", False)


def test_stale_active_holding_state_cannot_buy_mid_event():
    out = advance_kicker(signal(fresh=False), state="holding", bars=-4,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (out.state, out.engaged, out.cooldown) == ("cooldown", False, 10)


def test_nonfinite_hold_and_cooldown_settings_fail_safely():
    out = advance_kicker(signal(fresh=True), state="idle", bars=float("inf"),
        cooldown=float("nan"), risk_on=False, bull_held=False,
        kicker_held=False, kicker_priceable=True, shadow=False,
        prior_targeted=False,
        config=cfg(bear_kicker_max_bars=float("nan"),
                   bear_kicker_cooldown_bars=float("inf")))
    assert out.engaged is False
    assert out.state == "cooldown"
```

- [ ] **Step 5: Run the state tests and observe missing behavior**

Run: `python3 -m pytest backend/tests/test_strategy_x_bear.py -q`

Expected: FAIL because `advance_kicker` is missing.

- [ ] **Step 6: Implement the state transitions**

Normalize unknown states to `idle`; clamp counters to their configured ranges; clamp `max_bars >= 1` and `cooldown_bars >= 0`. Treat `state="idle"` plus an actual held kicker as a cache-reset adoption with unknown age, and fail safely to an immediate cooldown/exit rather than granting it a fresh holding period. In active mode, a cached `holding` state with neither an actual holding nor `prior_targeted=True` is stale and also fails to cooldown; shadow may advance its logical holding without an actual position. Apply transitions in this order: missing/unpriceable kicker, risk-on, cache-reset adoption, stale-state refusal, holding recovery-or-limit, cooldown decrement, idle fresh-event arming, armed confirmation, holding continuation. An `idle -> armed` result is never engaged; the first `armed -> holding` result has `bars=1`; a holding with `bars >= max_bars` exits before another engaged session.

- [ ] **Step 7: Add failing eligibility and budget tests**

```python
def test_allocator_equal_weights_only_eligible_funds_and_uses_bil_residual():
    histories = {"DBMF": [10.0] * 60, "KMLM": [20.0] * 60, "CTA": [30.0] * 59}
    prices = {"DBMF": 10.0, "KMLM": 20.0, "CTA": 30.0, "BIL": 91.0, "SQQQ": 8.0}
    eligible = eligible_crisis_alpha(histories, prices, cfg())
    out = plan_bear_overlay({"SPY": 0.90, "GLD": 0.10}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=eligible,
        kicker_engaged=True, prices=prices)
    assert eligible == ("DBMF", "KMLM")
    assert out.targets == {
        "GLD": 0.10, "DBMF": 0.10, "KMLM": 0.10,
        "SQQQ": 0.05, "BIL": 0.65,
    }
    assert sum(out.targets.values()) == 1.0


def test_missing_managers_route_their_budget_to_bil():
    out = plan_bear_overlay({"SPY": 0.9}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=(),
        kicker_engaged=False, prices={"BIL": 91.0})
    assert out.targets == {"BIL": 0.9}


def test_missing_bil_or_legacy_conflict_returns_baseline_unchanged():
    baseline = {"SPY": 0.9, "GLD": 0.1}
    missing = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"DBMF": 10.0, "SQQQ": 8.0})
    conflict = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", core_bear_symbol="SQQQ"),
        eligible_symbols=("DBMF",), kicker_engaged=True,
        prices={"BIL": 91.0, "DBMF": 10.0, "SQQQ": 8.0})
    assert missing.targets == baseline and missing.applied is False
    assert conflict.targets == baseline and conflict.applied is False


def test_allocator_clamps_percentages_and_never_exceeds_defensive_budget():
    out = plan_bear_overlay({"SPY": 0.12, "GLD": 0.88}, risk_on=False,
        config=cfg(bear_system_mode="active", crisis_alpha_pct=2,
                   bear_kicker_pct=2), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"BIL": 91, "DBMF": 10, "SQQQ": 8})
    assert out.targets == {"GLD": 0.88, "DBMF": 0.12}
    assert sum(out.targets.values()) == 1.0


def test_colliding_roles_or_nonfinite_weights_fail_closed():
    baseline = {"SPY": 0.9, "GLD": 0.1}
    collision = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", bear_cash_symbol="SPY"),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"SPY": 500.0, "DBMF": 10.0})
    nonfinite = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", crisis_alpha_pct=float("nan")),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"BIL": 91.0, "DBMF": 10.0})
    assert collision.targets == baseline and collision.applied is False
    assert nonfinite.targets == baseline and nonfinite.applied is False
    assert eligible_crisis_alpha({"DBMF": [10.0] * 100}, {"DBMF": 10.0},
        cfg(crisis_alpha_min_history_bars=float("inf"))) == ()
```

- [ ] **Step 8: Implement eligibility and allocation, then run the pure suite**

`eligible_crisis_alpha` must preserve normalized declared order and require both a positive finite effective price and at least the clamped minimum visible history. `plan_bear_overlay` must copy its input, refuse risk-on/off-mode/legacy-conflict/role-collision/missing-chop/missing-BIL/nonfinite inputs, remove only the chop target, floor equal managed-fund weights to six decimals, cap each sleeve by the remaining defensive budget, suppress an unpriceable kicker, and put all rounding residual in BIL. Before returning an applied allocation, assert every target is finite and nonnegative, every non-chop baseline target is unchanged, and both the total target sum and the overlay delta equal the baseline values at six-decimal precision; otherwise return the untouched baseline.

Run: `python3 -m pytest backend/tests/test_strategy_x_bear.py -q`

Expected: PASS.

- [ ] **Step 9: Verify scope and commit**

Run `git diff --check`, run `gitnexus_detect_changes()`, inspect the diff, then:

```bash
git add backend/strategy_x_bear.py backend/tests/test_strategy_x_bear.py
git commit -m "feat(strategy-x): add pure bear policy"
```

---

### Task 2: Strategy Defaults, Universe, and Broker Discovery

**Files:**
- Modify: `backend/strategy_x.py` (`DEFAULTS`, `strategy_x_universe`)
- Modify: `backend/tests/test_strategy_x.py`
- Modify: `backend/tests/test_strategy_x_broker_wiring.py`

**Interfaces:**
- Consumes: `bear_system_universe(config) -> list[str]` from Task 1.
- Produces: merged default configuration and one deterministic universe used by both `StrategyX` and the broker fetch wiring.

- [ ] **Step 1: Run mandatory impact analysis before existing-symbol edits**

Run upstream GitNexus impact analysis for `strategy_x_universe`. Report direct callers, affected processes, and risk before editing. Manually confirm that broker `_strategy_x_universe_symbols` delegates to it and identify existing unit coverage if the index is degraded.

- [ ] **Step 2: Write failing default and universe behavior tests**

```python
def test_bear_system_defaults_are_inert_and_bounded():
    assert DEFAULTS["bear_system_mode"] == "off"
    assert strategy_x_universe(DEFAULTS) == ["QQQ", "TQQQ", "SPY"]


def test_shadow_and_active_declare_the_normalized_bear_universe():
    expected = ["QQQ", "TQQQ", "SPY", "BIL", "DBMF", "KMLM", "CTA", "SQQQ"]
    assert strategy_x_universe({**DEFAULTS, "bear_system_mode": "shadow"}) == expected
    assert strategy_x_universe({**DEFAULTS, "bear_system_mode": "active"}) == expected


def test_invalid_bear_mode_does_not_expand_the_universe():
    got = strategy_x_universe({**DEFAULTS, "bear_system_mode": "invalid"})
    assert got == ["QQQ", "TQQQ", "SPY"]
```

Add broker-wiring assertions that an enabled shadow spec contains all five default bear symbols, while the same spec in `off` contains none of them except any symbol independently serving as a core leg.

Extend the broker-wiring AST harness to extract `_strategy_x_prepare` with focused stubs for its price/history dependencies. In backtest mode, give it visible bars but an empty `prices` mapping and assert it fills positive point-in-time prices for BIL, DBMF, KMLM, CTA, and SQQQ before dispatch. Repeat with mode off and assert those extra symbols are neither fetched nor priced. This tests the actual broker price-preparation boundary, not only universe declaration.

- [ ] **Step 3: Run focused tests and observe failures**

Run: `python3 -m pytest backend/tests/test_strategy_x.py backend/tests/test_strategy_x_broker_wiring.py -q`

Expected: FAIL because the new defaults and bear universe are absent.

- [ ] **Step 4: Add exact defaults and delegate universe expansion**

Add the configuration block from the approved spec verbatim to `DEFAULTS`. Import `bear_system_universe` and extend the current core/commodity list with it; preserve the existing final normalization/deduplication loop. Do not modify `backend/broker.py`: its existing delegation is the contract under test.

```python
"bear_system_mode": "off",
"bear_cash_symbol": "BIL",
"crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"],
"crisis_alpha_pct": 0.20,
"crisis_alpha_min_history_bars": 60,
"bear_kicker_symbol": "SQQQ",
"bear_kicker_pct": 0.05,
"bear_kicker_fast_ma_bars": 20,
"bear_kicker_mid_ma_bars": 50,
"bear_kicker_long_ma_bars": 200,
"bear_kicker_max_bars": 5,
"bear_kicker_cooldown_bars": 10,
```

- [ ] **Step 5: Run focused and baseline Strategy X tests**

Run: `python3 -m pytest backend/tests/test_strategy_x.py backend/tests/test_strategy_x_broker_wiring.py backend/tests/test_strategy_x_run_once.py -q`

Expected: PASS, including all pre-existing tests.

- [ ] **Step 6: Verify scope and commit**

Run `git diff --check`, `gitnexus_detect_changes()`, and inspect any affected broker-fetch process, then:

```bash
git add backend/strategy_x.py backend/tests/test_strategy_x.py backend/tests/test_strategy_x_broker_wiring.py
git commit -m "feat(strategy-x): declare bear-system universe"
```

---

### Task 3: Run-Once Shadow, Research-Active, and Provenance Integration

**Files:**
- Modify: `backend/strategies/strategy_x.py` (`StrategyX.run_once`, schema header)
- Modify: `backend/broker.py` (`run_run_once_strategies`, residual-sleeve conflict injection only)
- Modify: `backend/tests/test_strategy_x_run_once.py`
- Create: `backend/tests/test_strategy_x_broker_coexistence.py`

**Interfaces:**
- Consumes: all Task 1 pure helpers; Task 2 `DEFAULTS` and `strategy_x_universe`; existing `pit_daily_closes`, `plan_targets`, and `targets_to_orders`.
- Produces: namespaced cache state (`_sx_bear_system_state`, `_sx_bear_kicker_bars`, `_sx_bear_kicker_cooldown`, `_sx_bear_shadow`, `_sx_bear_state_version`, `_sx_bear_kicker_entry_day`, `_sx_bear_kicker_targeted`, `_sx_bear_owned`), unchanged clean-state off/shadow orders, provenance-safe unwinds, an explicit `BearSystemStateError` for irreconcilable active research state, a broker-injected residual-sleeve collision flag, and backtest-authorized active targets routed through existing sizing.

- [ ] **Step 1: Run mandatory impact analysis before existing-symbol edits**

Run upstream GitNexus impact analysis separately for `StrategyX.run_once` and `run_run_once_strategies`. Report callers, participating execution flows, and risk. If either result is HIGH or CRITICAL, warn the user before proceeding. If unavailable, record UNKNOWN and manually inspect the broker dispatcher plus its focused AST harnesses and `test_strategy_x_run_once.py`.

- [ ] **Step 2: Write a failing off-versus-shadow parity test**

Refactor the local test helper only enough to accept explicit `prices`, `cache`, and `current_time`, then compare real outputs and sizing from two clean emulators:

```python
def test_shadow_matches_off_orders_and_sizing_exactly():
    down = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    for sym, value in {"BIL": 91.0, "DBMF": 30.0, "KMLM": 25.0,
                       "CTA": 20.0, "SQQQ": 8.0}.items():
        down[sym] = {"bars": bars(80, start=value, step=0.01)}
    px = {**PRICES, "BIL": 91.0, "DBMF": 30.0, "KMLM": 25.0,
          "CTA": 20.0, "SQQQ": 8.0}
    off_cache, shadow_cache = {}, {}
    off = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="off"), {}, data=down,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache=off_cache)
    shadow = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="shadow"), {}, data=down,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache=shadow_cache)
    off_orders = {k: v for k, v in off.items() if not k.startswith("_")}
    shadow_orders = {k: v for k, v in shadow.items() if not k.startswith("_")}
    assert shadow_orders == off_orders
    assert shadow["_nexus_position_sizes"] == off["_nexus_position_sizes"]
    assert not any(k.startswith("_sx_bear_") for k in off_cache)
    assert shadow_cache["_sx_bear_shadow"]["proposed_targets"] != \
        shadow_cache["_sx_bear_shadow"]["baseline_targets"]
```

- [ ] **Step 3: Run the parity test and observe missing telemetry**

Run: `python3 -m pytest backend/tests/test_strategy_x_run_once.py::test_shadow_matches_off_orders_and_sizing_exactly -q`

Expected: FAIL because `_sx_bear_shadow` is absent.

- [ ] **Step 4: Add point-in-time evaluation and telemetry without changing selected targets**

Import the pure helpers. After baseline `plan_targets` returns, and only when normalized mode is `shadow` or `active`:

1. Preserve an off-equivalent baseline price view for satellite ranking: in shadow/active, remove configured bear-only quotes added by broker preparation unless that symbol was already present in the incoming `symbols` argument. Build a separate effective bear-price view from quotes first, then the last `pit_daily_closes` value.
2. Build visible histories for all managed-futures symbols.
3. Compute eligible funds, QQQ fast-crash signal, and current kicker decision.
4. Store the decision's state counters under the exact namespaced keys.
5. Build proposed targets and a sorted target-delta mapping.
6. Store a plain-JSON telemetry dict containing core state/reason, refusal reason, eligible/unavailable funds, signal fields, kicker fields, baseline/proposed targets, and target delta.
7. Keep `selected_targets = baseline_targets` in shadow. Use proposed targets only when mode is active, `run_once(mode="backtest")`, and `BearAllocation.applied` is true; an active request in every other runtime records a research-only refusal and remains baseline.

Call `targets_to_orders` exactly once with `selected_targets`, except for the explicit early emergency-unwind path described below. Never claim the entire configured universe. Load `_sx_bear_owned`, add only symbols this subsystem actually targeted, and retain them until both their actual holding and pending target are absent. Include prior provenance in `owned` in every mode so active-to-off/shadow/config-change transitions unwind safely. Refuse the overlay when a configured bear symbol is present in satellite selections/ages. In an active backtest, an unprovenanced configured bear holding raises `BearSystemStateError` and invalidates the run for explicit reconciliation; off/shadow never claims it. Log one concise line with mode, applied/refused reason, eligible symbols, kicker state, provenance, and delta.

Prevent the broker's separate residual sleeve from sharing SQQQ. Immediately before dispatching Strategy X, `run_run_once_strategies` compares the normalized Strategy X kicker with `_residual_sleeve_config(_cached_strategies or specs)`. When the residual sleeve is enabled with the same bear symbol, inject `_strategy_x_bear_residual_conflict=True` into the per-call config. Strategy X then suppresses new kicker targets; if its provenance already contains that ticker, active research raises `BearSystemStateError` rather than entering competing ownership. Defense-only remains eligible. This guard is fail-closed if the broker cannot verify coexistence.

- [ ] **Step 5: Add failing active allocation, runtime, provenance, and coexistence tests**

```python
def test_active_risk_off_buys_managed_futures_and_bil():
    data, px = bear_ready_downtrend_fixture()
    cache = {}
    out = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="active"), {}, data=data,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache=cache,
        mode="backtest")
    assert out["BIL"] == 1
    assert out["DBMF"] == out["KMLM"] == out["CTA"] == 1
    assert "SPY" not in out
    sizes = out["_nexus_position_sizes"]
    assert sizes["BIL"]["buy_cash"] > sizes["DBMF"]["buy_cash"]
    assert cache["_strategy_x_last"]["targets"] == {
        "DBMF": 0.066666, "KMLM": 0.066666, "CTA": 0.066666,
        "BIL": 0.800002,
    }


def test_active_refuses_when_legacy_bear_symbol_is_configured():
    data, px = bear_ready_downtrend_fixture()
    out = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="active", core_bear_symbol="SQQQ"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=px), strategy_cache={},
        mode="backtest")
    assert "BIL" not in out and "DBMF" not in out


def test_dynamic_satellite_role_collision_refuses_overlay_without_changing_baseline():
    data, px = bear_ready_downtrend_fixture()
    data["conviction_scores"] = {"DBMF": 9, "AAPL": 1}
    c = base_cfg(bear_system_mode="active", satellite_pct=0.2,
                 satellite_max_names=1)
    cache = {}
    out = StrategyX().run_once(list(px), px, NOW, c, {}, data=data,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache=cache,
        mode="backtest")
    assert "BIL" not in out
    assert cache["_sx_bear_shadow"]["reason"].startswith("role conflict")


def test_active_request_cannot_change_orders_outside_backtest_runtime():
    data, px = bear_ready_downtrend_fixture()
    baseline = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="off"), {}, data=data,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache={})
    refused = StrategyX().run_once(list(px), px, NOW,
        base_cfg(bear_system_mode="active"), {}, data=data,
        portfolio_emulator=FakeEmulator(prices=px), strategy_cache={}, mode="FULL")
    assert {k: v for k, v in refused.items() if not k.startswith("_")} == \
        {k: v for k, v in baseline.items() if not k.startswith("_")}
    assert refused["_nexus_position_sizes"] == baseline["_nexus_position_sizes"]
```

In `test_strategy_x_broker_coexistence.py`, reuse the established AST-extraction pattern from `test_broker_graph_nexus_pit.py` with a fake Strategy X class that captures its received config. Assert an enabled residual sleeve whose normalized bear symbol is SQQQ causes `run_run_once_strategies` to inject `_strategy_x_bear_residual_conflict=True`; a disabled sleeve or different symbol injects false/absent. Then run the real wrapper with the conflict flag and prove defense targets remain possible but SQQQ is absent. If SQQQ already appears in `_sx_bear_owned`, assert the active backtest raises `BearSystemStateError`.

Add a broker A/A test where the incoming symbol set and off price map omit DBMF, conviction scores contain DBMF plus AAPL, and shadow `_strategy_x_prepare` adds DBMF. Run off against its original map and shadow against the expanded map; assert executable orders and `_nexus_position_sizes` remain identical because satellite ranking uses the off-equivalent baseline price view.

Add a multi-session real-wrapper/emulator test that passes `mode="backtest"` and asserts exact decision and fill indices: the fresh QQQ event first emits no SQQQ buy while TQQQ is held; the following still-stacked session may target SQQQ after TQQQ is absent; its buy fills exactly one row later; recovery or the bar limit targets an exit; the sell fills exactly one row later; close-to-close SQQQ exposure never exceeds the configured target-session bound; and re-arming cannot occur before ten complete cooldown decisions. Include one missing-price row and assert the requested fill is not silently carried.

Add tests for: state-key-reset SQQQ holdings still listed in `_sx_bear_owned` exit immediately; a full cache reset plus an active-backtest bear holding raises `BearSystemStateError`; stale/negative/huge counters cannot manufacture a buy; an active-owned DBMF holding unwinds after switching mode off; off/shadow do not claim an unprovenanced DBMF/BIL/SQQQ holding; and missing QQQ history still emits a priceable provenance-owned bear exit before refusing new decisions.

- [ ] **Step 6: Run the focused tests and observe failures**

Run: `python3 -m pytest backend/tests/test_strategy_x_run_once.py -q`

Expected: FAIL on active targets, ranking exclusion, state transitions, or recovery exit until integration is complete.

- [ ] **Step 7: Complete active target selection, ownership, and cache-reset handling**

Use actual positions to derive `bull_held` and `kicker_held`. Pass `kicker_priceable`, whether this is shadow, and the previous `_sx_bear_kicker_targeted` flag into `advance_kicker`. Validate state version, state name, counters, and entry day before trusting cached holding state. When state cache is absent but a provenance-owned SQQQ is actually held, fail to an immediate cooldown target that omits SQQQ so `targets_to_orders` enforces the exit. Risk-on targets remain baseline except for provenance-owned bear exits.

Before the ordinary insufficient-QQQ-history return, construct effective prices only for `_sx_bear_owned` and emit full exits for any priceable held provenance symbols. Reuse the existing order metadata contract and do not open or resize anything in this path. Persist only symbols targeted by the overlay; remove a provenance symbol only after it is flat and no prior target remains. Hard legacy conflicts must neither advance new state nor change executable orders/sizing relative to a multi-session `bear_system_mode="off"` legacy open/hold/exit sequence.

Update `_strategy_x_last["targets"]` to the selected targets and add `bear_system_mode` plus the overlay reason without removing existing keys. Invalid raw modes act as off and log once on that daily decision.

- [ ] **Step 8: Synchronize the schema header mechanically**

Run: `python3 scripts/strategy_x_sync_schema.py`

Add an automated test that parses the one-line JSON header and asserts every `DEFAULTS` key/value is present, including `bear_system_mode: "off"`; then run the sync script. Do not change any live document. Update the schema description only if it currently implies the research-active subsystem can run live.

- [ ] **Step 9: Run integration and regression tests**

Run: `python3 -m pytest backend/tests/test_strategy_x_bear.py backend/tests/test_strategy_x.py backend/tests/test_strategy_x_run_once.py backend/tests/test_strategy_x_broker_wiring.py backend/tests/test_strategy_x_broker_coexistence.py -q`

Expected: PASS.

- [ ] **Step 10: Verify scope and commit**

Run `git diff --check`, `gitnexus_detect_changes()`, inspect affected execution flows, then:

```bash
git add backend/strategies/strategy_x.py backend/broker.py \
  backend/tests/test_strategy_x_run_once.py \
  backend/tests/test_strategy_x_broker_coexistence.py
git commit -m "feat(strategy-x): integrate shadow bear system"
```

---

### Task 4: Existing Production Harness and Frozen Settings Search

**User-approved execution amendment (2026-08-26):** The repository already has the authoritative backtest harness. Do not create `scripts/strategy_x_bear_research.py`, a second portfolio emulator, or a second fill clock. The former Task 4 and Task 5 below are retained only as superseded historical detail; do not execute them.

**Existing execution path:**

- `scripts/_api.py` authenticates from `.env` without exposing credentials.
- `POST /backtests` queues `backend/engines/backtest_engine.py`.
- The engine launches the real `backend/broker.py` backtest path.
- `GET /backtests/{id}/graph-data` returns the continuous `portfolio_value_history`, trades, prices, and decisions used for slicing and reconciliation.
- `GET /backtests/{id}/summary` returns the immutable `strategy_schema` snapshot and aggregate metrics.

**Files:**

- Modify only for production compatibility defects with tests: existing Strategy X/broker files from Tasks 1–3.
- Reuse: `scripts/_api.py`, `scripts/_strategy_x_deploy.py`, `scripts/pull_backtest_logs.py`, and the API endpoints above.
- Create only evidence artifacts under `docs/superpowers/research/`; a read-only result analyzer is allowed only if existing reporting commands cannot compute the frozen slices. Such an analyzer may consume completed API results but may not simulate decisions, orders, fills, or portfolio state.

- [ ] **Step 1: Prove the API backtest authorization boundary**

The production broker call omits the dispatcher scheduler-mode parameter. Add a focused AST regression proving that module run mode `MODE_BACKTEST` reaches Strategy X aliases as literal `mode="backtest"`, while sibling strategies and all live scheduler modes retain their existing values. Run the full Task 3 suite and independent review before deployment.

- [ ] **Step 2: Freeze the API research protocol**

Use strategy document 198 and instance `strategy-x`. Before the first mutation, save the complete document to a permission-restricted temporary backup and record a secret-free hash/config snapshot. For research arms, set the Graph Nexus entry weight to zero, set `satellite_pct=0.0`, `commodity_pct=0.0`, `core_weight=0.9`, `core_bear_symbol=""`, and vary only `bear_system_mode` plus the four frozen grid keys. Do not change any other strategy document.

Freeze one manifest containing the exact five arms declared in Global Constraints, their stable IDs, the common full-period dates, named windows, non-overlapping two-month slices, production cost model, and the search-only selection key. Before every arm, use the existing full-instance clear-state API on the non-real-money `strategy-x` instance and attest that its steering cache is empty; a failed clear/attestation blocks launch. The engine runs one backtest at a time: never mutate the document for the next arm until the current run is terminal and its `strategy_schema` exactly matches the requested config. Count each successfully queued API backtest against the cap even if it fails; do not launch a replacement without explicit user approval.

- [ ] **Step 3: Preserve continuous state and frozen selection**

Each of the five arms is one continuous API backtest from common inception through the latest complete session; named and non-overlapping two-month windows are slices of that stored ledger, never fresh runs. Confirm off/shadow equality in terminal equity, full equity series, and trades.

Rank only the three frozen active arms using pre-2023 two-month lattice rows and the frozen lexicographic key: `(search_violations, -search_worst_bear_excess, -search_median_bear_excess, search_turnover, candidate_id)`. Named search windows are strict post-selection gates, not ranking inputs. Record the winning tested arm/config plus all five result IDs and strategy-schema hashes before computing any 2023+ aggregate.

- [ ] **Step 4: Report the frozen 2023+ diagnostic without launching more runs**

After the pre-2023 selection freeze is written, slice the already-completed five continuous ledgers into the frozen 2023+ named windows and non-overlapping two-month lattice; classify auto windows from QQQ return with the predeclared thresholds. Do not launch any additional backtest. Treat every 2023+ comparison as diagnostic and report all three active arms so the bounded-search limitation is visible.

- [ ] **Step 5: Validate API artifacts independently**

For every run require: terminal `finished`; exact requested `strategy_schema`; daily portfolio snapshots spanning the request; finite equity and prices; no impossible target/order evidence; and production cost metadata. Reconcile trades and position snapshots where the API fields permit it. SPY uses `backtest_prices` on the same snapshot dates. Any unavailable required slice, stale deployment, schema mismatch, missing graph-data ledger, or nonterminal run invalidates that arm.

Compute terminal return, max drawdown, excess versus baseline and SPY, turnover, trades, and component P&L where stored fields permit. Promotion gates and the search/holdout firewall remain exactly as specified in the design. Report production nominal costs rather than claiming the superseded 2-basis-point convention.

- [ ] **Step 6: Deploy using the authorized workflow**

Run local verification and `gitnexus_detect_changes()`, push `main`, wait about five minutes, and use the existing deployed-code checker plus a safe API read to prove the expected revision is serving. Only then snapshot/update document 198 and start backtests. Queue exactly the five frozen arms sequentially and never exceed five new backtests. Never print `.env` contents or credentials.

---

### Task 5: API Real-Data Matrix, Best Settings, and Evidence Report

**Files:**

- Create: `docs/superpowers/research/2026-08-26-strategy-x-bear-results.md`
- Create if compact and secret-free: `docs/superpowers/research/2026-08-26-strategy-x-bear-results.json`

- [ ] **Step 1: Execute exactly five frozen arms sequentially through the API**

Maintain a durable, secret-free run manifest containing candidate ID, requested config hash, backtest ID, status, deployed revision, stored schema hash, dates, and cost model. Refuse duplicate launches and refuse to launch while another backtest is active.

- [ ] **Step 2: Freeze pre-2023 selection before computing 2023+ aggregates**

Write the pre-2023 aggregates and selected tested arm to the manifest before computing any 2023+ aggregate. Independently recompute the sort from pre-2023 lattice rows and require the same winner. No additional API request may start a backtest.

- [ ] **Step 3: Validate all five full-period ledgers and slice 2023+ diagnostics**

Use only the five already-completed full-interval ledgers, then validate graph-data and summary artifacts. Preserve the current-survivor and fund-inception caveats.

- [ ] **Step 4: Write the evidence report**

State exact API result IDs, deployed revision, schema/config hashes, dates, production cost model, available data, search/holdout rule, window counts, off/shadow parity, named-window and regime tables, full-period metrics, failed gates, best settings, and separate defense/kicker promotability verdicts. If no candidate clears every no-harm gate, leave the subsystem off/shadow and label the best settings diagnostic only.

End with the small-bullet mechanics required by the approved design.

- [ ] **Step 5: Restore or deliberately park document 198**

After evidence capture, either restore the exact pre-research document or park the reviewed configuration in `shadow`; never leave an unreviewed candidate active. Verify the final API document and record its hash.

---

### Superseded Task 4 (do not execute): Point-in-Time Research Harness and Frozen Settings Search

**Files:**
- Create: `scripts/strategy_x_bear_research.py`
- Create: `backend/tests/test_strategy_x_bear_research.py`

**Interfaces:**
- Consumes: the real `strategies.strategy_x.StrategyX`, adjusted daily closes for `QQQ`, `TQQQ`, `SPY`, `BIL`, `SQQQ`, `DBMF`, `KMLM`, and `CTA`, and the exact candidate grid from Global Constraints.
- Produces: `candidate_grid() -> tuple[dict, ...]`, `classify_regime(qqq_return) -> str`, `run_continuous(prices, config, arm, *, end_exclusive=None) -> dict`, `slice_metrics(ledger, window) -> dict`, `evaluate_no_harm(candidate, baseline, regime) -> tuple[bool, tuple[str, ...]]`, `aggregate_search_rows(raw_rows) -> list[dict]`, `select_candidate(candidate_rows) -> dict`, and a CLI JSON report containing ledgers/slices for required arms, settings, costs, fills, availability, window metrics, component P&L, turnover, trade count, parity checks, input digest, selection freeze, and separate defense/kicker verdicts.

- [ ] **Step 1: Write failing grid, leakage, and classification tests**

```python
from scripts.strategy_x_bear_research import (
    aggregate_search_rows,
    candidate_grid,
    classify_regime,
    evaluate_no_harm,
    select_candidate,
)


def test_grid_has_twenty_seven_unique_predeclared_candidates():
    grid = candidate_grid()
    got = {(x["crisis_alpha_pct"], x["bear_kicker_pct"],
            x["bear_kicker_max_bars"], x["bear_kicker_cooldown_bars"])
           for x in grid}
    expected = {(a, 0.0, 3, 5) for a in (0.10, 0.20, 0.30)} | {
        (a, k, bars, cooldown)
        for a in (0.10, 0.20, 0.30)
        for k in (0.025, 0.05)
        for bars in (3, 5)
        for cooldown in (5, 10)
    }
    assert len(grid) == 27
    assert got == expected
    assert [x["candidate_id"] for x in grid] == sorted(
        x["candidate_id"] for x in grid)


def test_regime_thresholds_are_literal_and_exhaustive():
    assert classify_regime(-4.01) == "bear"
    assert classify_regime(-4.00) == "chop"
    assert classify_regime(4.00) == "chop"
    assert classify_regime(4.01) == "bull"


def test_search_aggregation_and_selection_cannot_read_holdout_rows():
    raw = [
        {"candidate_id": "a", "partition": "search", "regime": "bear",
         "return_excess": 1.0, "drawdown_excess": 0.0, "turnover": 9.0},
        {"candidate_id": "b", "partition": "search", "regime": "bear",
         "return_excess": -1.0, "drawdown_excess": 0.0, "turnover": 1.0},
        {"candidate_id": "a", "partition": "holdout", "regime": "bear",
         "return_excess": -999.0, "drawdown_excess": -999.0, "turnover": 0.0},
        {"candidate_id": "b", "partition": "holdout", "regime": "bear",
         "return_excess": 999.0, "drawdown_excess": 999.0, "turnover": 0.0},
    ]
    aggregates = aggregate_search_rows(raw)
    assert select_candidate(aggregates)["candidate_id"] == "a"
    mutated = [dict(x, return_excess=-x["return_excess"] * 10)
               if x["partition"] == "holdout" else x for x in raw]
    assert aggregate_search_rows(mutated) == aggregates


def test_gate_rules_are_regime_specific_and_signed_correctly():
    baseline = {"return_pct": 2.0, "max_drawdown_pct": -10.0}
    assert evaluate_no_harm(
        {"return_pct": 2.0, "max_drawdown_pct": -10.0}, baseline, "bear")[0]
    assert not evaluate_no_harm(
        {"return_pct": 2.0, "max_drawdown_pct": -10.01}, baseline, "bear")[0]
    assert evaluate_no_harm(
        {"return_pct": 2.0, "max_drawdown_pct": -99.0}, baseline, "bull")[0]
    assert not evaluate_no_harm(
        {"return_pct": 1.99, "max_drawdown_pct": -1.0}, baseline, "recovery")[0]
```

- [ ] **Step 2: Run the tests and observe the import failure**

Run: `python3 -m pytest backend/tests/test_strategy_x_bear_research.py -q`

Expected: FAIL during collection because `strategy_x_bear_research` does not exist.

- [ ] **Step 3: Implement immutable declarations and selection key**

Declare `COST_BPS = 2.0`, `SEARCH_END = pd.Timestamp("2022-12-31")`, `DATA_START = "2010-01-01"`, `DATA_END_EXCLUSIVE = "2026-08-25"`, the eight-symbol universe, the four grid tuples, and `BASE_RESEARCH_CONFIG = {**DEFAULTS, "strategy_x_enabled": True, "core_bear_symbol": "", "satellite_pct": 0.0, "commodity_pct": 0.0}`. Do not tune any existing Strategy X default. Add these frozen named slices:

```python
FROZEN_WINDOWS = (
    ("2018_q4_bear", "2018-10-01", "2019-01-01", "bear", "search"),
    ("2019_h1_recovery", "2019-01-01", "2019-07-01", "recovery", "search"),
    ("2020_q1_crash", "2020-02-18", "2020-04-01", "bear", "search"),
    ("2020_q2_q3_recovery", "2020-04-01", "2020-10-01", "recovery", "search"),
    ("2021_bull", "2021-01-01", "2022-01-01", "bull", "search"),
    ("2022_inflation_bear", "2022-01-01", "2023-01-01", "bear", "search"),
    ("2023_recovery", "2023-01-01", "2024-01-01", "recovery", "holdout"),
    ("2024_bull", "2024-01-01", "2025-01-01", "bull", "holdout"),
    ("2025_spring_drawdown", "2025-02-18", "2025-04-09", "bear", "holdout"),
    ("2025_spring_recovery", "2025-04-09", "2025-07-01", "recovery", "holdout"),
    ("2026_h1", "2026-01-01", "2026-07-01", "auto", "holdout"),
)
```

Generate non-overlapping two-month windows beginning `2013-01-01` and label them from QQQ return (`bear < -4%`, `bull > 4%`, else `chop`). Their partition is based only on start date. Candidate ranking uses only search-partition two-month lattice rows—not overlapping named windows—and is lexicographic: `(search_violations, -search_worst_bear_excess, -search_median_bear_excess, search_turnover, candidate_id)`. Named search windows remain strict post-selection gates.

- [ ] **Step 4: Add failing deterministic continuous-clock tests**

Build a synthetic 420-session DataFrame with literal paths for all eight symbols; never mock `StrategyX`. Run off and shadow once from common inception, then slice two adjacent windows from each continuous ledger:

```python
def test_continuous_runner_preserves_state_and_shadow_is_exact_aa():
    prices = synthetic_prices()
    off = run_continuous(prices, research_config(), "off")
    shadow = run_continuous(prices, research_config(), "shadow")
    assert shadow["orders"] == off["orders"]
    assert shadow["position_sizes"] == off["position_sizes"]
    assert shadow["equity"] == off["equity"]
    left = slice_metrics(off, ("left", prices.index[300], prices.index[340], "auto", "search"))
    right = slice_metrics(off, ("right", prices.index[340], prices.index[380], "auto", "search"))
    assert left["ending_positions"] == right["starting_positions"]
    boundary_orders = [x for x in off["fills"] if x["decision_index"] == 339]
    assert boundary_orders and {x["fill_index"] for x in boundary_orders} == {340}


def test_decision_uses_close_t_and_fills_exactly_on_row_t_plus_one():
    prices = synthetic_prices()
    changed_future = prices.copy()
    changed_future.iloc[321, changed_future.columns.get_loc("TQQQ")] *= 10
    a = run_continuous(prices.iloc[:323], research_config(), "off")
    b = run_continuous(changed_future.iloc[:323], research_config(), "off")
    assert a["orders"][320] == b["orders"][320]
    assert all(fill["fill_index"] == fill["decision_index"] + 1
               for fill in a["fills"])
```

The break caught is either a reset at a reporting boundary, a decision that reads row `t+1`, or an order that fills later than exactly one trading row.

- [ ] **Step 5: Implement the continuous emulator and slicing**

Reuse the existing run-once output contract rather than duplicating strategy logic. For each trading row `t`: fill the prior row's requested sells then buys at close `t` with 2 bps one-way cost; append row `t` to timestamped histories; create a synthetic post-close decision timestamp at 12:00 UTC on the next calendar date so `pit_daily_closes` treats row `t` as visible; pass only row `t` quotes; call `StrategyX.run_once(..., mode="backtest")`; and store executable decisions/sizing for one fill attempt at row `t+1`. Mutating any row after `t` must not change the decision at `t`.

A requested symbol without a positive finite row-`t+1` price records a missed fill, cancels that request rather than carrying it, and invalidates the canonical run. Do not fill a final pending order beyond the data interval. Use expanding histories: a symbol before inception is absent from quotes and its bar stream. Preserve NaNs and never pad manager history.

Track pre-fill initial equity, daily equity/cash/positions, executable orders and size instructions by decision index, fills with both decision/fill indices, maximum requested target weight, gross fill notional, turnover (`gross fill notional / mean daily NAV`), trade count, missed fills, fund availability, and per-symbol P&L. For the full ledger and every slice, symbol P&L is `ending market value - starting market value + net sales - buys`. Fees are already embedded in buy cash and net sale proceeds: disclose them separately but never add them again. With non-interest-bearing cash, require the sum of symbol P&L to equal ending NAV minus starting NAV within one cent. `slice_metrics` rebases the continuous equity curve at the first pre-action observation inside the slice and includes positions/orders crossing either boundary.

- [ ] **Step 6: Implement two-phase search, frozen holdout, and separate gates**

Run baseline off and shadow continuously over the canonical full dataset once. Run each of the 27 candidate defense/full pairs only through `2023-01-01`, slice only the non-overlapping two-month search lattice, aggregate those raw rows, and select by the frozen lexicographic key. Freeze the winning candidate ID/config together with the normalized input SHA-256 before computing any holdout result. Then run only that selected defense/full pair continuously from common inception through the full dataset so its positions/cache naturally cross into 2023.

Named search windows are strict post-selection gates and reports, not candidate-ranking observations. Holdout lattice and named rows are computed only for the frozen winner. Resolve `auto` from QQQ return. `evaluate_no_harm` applies return plus maximum-drawdown equality-or-better only to bear rows, terminal-return equality-or-better to bull/recovery/chop rows, and treats unavailable/missing rows as failure. Maximum drawdown is signed, so `-9%` is better than `-10%`.

Report five comparisons where available: baseline, shadow, selected defense-only, selected full, and SPY. SPY enters on the same first executable row with 2 bps cost and is marked on identical dates. For each slice report return, excess versus baseline/SPY, drawdown, CAGR where meaningful, turnover, trades, availability, missed fills, and component P&L. Off/shadow orders, sizes, trades, and complete equity series must match exactly or the artifact is invalid.

`defense_promotable` requires defense-only to clear every lattice/named/full-common no-harm gate against baseline. `kicker_promotable` requires a nonzero kicker, full to clear the same gates against baseline, and full terminal return to be strictly above defense-only over the all-funds common interval. Define that interval after DBMF, KMLM, and CTA each have 60 actual observations; report the longer expanding-universe interval separately. A failed kicker verdict leaves defense eligible for its own verdict.

The CLI accepts `--prices-csv`, `--output-json`, `--output-prices`, `--workers`, `--start`, and `--end-exclusive`. Without a CSV it downloads once with `yf.download(..., auto_adjust=True, progress=False)`. Normalize/sort the matrix, retain it losslessly (gzip CSV is acceptable), and record SHA-256, provider date, last included session, Python/pandas/NumPy/yfinance versions, and ticker inception/last-observation dates. State that current-survivor fund selection creates survivorship bias. Only the exact default date range with a retained normalized matrix can emit promotion verdicts; custom input/ranges are diagnostic and force both verdicts false. Parallelize search candidates only, and sort results before serialization.

- [ ] **Step 7: Run deterministic tests and targeted regressions**

Run: `python3 -m pytest backend/tests/test_strategy_x_bear_research.py backend/tests/test_strategy_x_bear.py backend/tests/test_strategy_x_run_once.py -q`

Expected: PASS.

- [ ] **Step 8: Verify scope and commit**

Run `git diff --check`, `gitnexus_detect_changes()`, inspect the complete diff, then:

```bash
git add scripts/strategy_x_bear_research.py backend/tests/test_strategy_x_bear_research.py
git commit -m "feat(strategy-x): add frozen bear research harness"
```

---

### Superseded Task 5 (do not execute): Real-Data Matrix, Best Settings, and Evidence Report

**Files:**
- Create: `docs/superpowers/research/2026-08-25-strategy-x-bear-results.md`
- Create only if small enough for review: `docs/superpowers/research/2026-08-25-strategy-x-bear-results.json`
- Create: `docs/superpowers/research/2026-08-25-strategy-x-bear-prices.csv.gz`
- Modify only if a reproducible defect is found: files and covering tests from Tasks 1-4.

**Interfaces:**
- Consumes: Task 4 CLI and frozen declarations.
- Produces: a reproducible empirical verdict, diagnostic best settings, strict promotion result, and a compact human explanation. No code path consumes the report.

- [ ] **Step 1: Run the complete actual-price search and locked holdout**

Run:

```bash
python3 scripts/strategy_x_bear_research.py \
  --start 2010-01-01 \
  --end-exclusive 2026-08-25 \
  --workers 3 \
  --output-json /tmp/strategy-x-bear-results.json \
  --output-prices docs/superpowers/research/2026-08-25-strategy-x-bear-prices.csv.gz
```

Expected: exit 0; 27 unique search candidates; search-only selection freeze before holdout evaluation; full ledgers for baseline/shadow and only the selected defense/full arms; named and non-overlapping two-month slices; exact off/shadow parity; no missed fills; an explicit diagnostic winner; and separate `defense_promotable`/`kicker_promotable` booleans.

- [ ] **Step 2: Validate the artifact independently**

Run a short read-only Python command that loads the JSON and asserts: all metrics are finite; every required slice is available; holdout dates begin in 2023; raw holdout rows exist only for the frozen winner; the selection digest was written before holdout evaluation; selection aggregates derive only from search-lattice rows; named windows are absent from candidate ranking; full target weights never exceed one; every frozen window has all required selected arms; component P&L reconciles; SPY uses the same first executable row and costs; the normalized matrix hash matches the retained gzip CSV; and the reported best ID equals a fresh search-only sort using the frozen key.

Expected: exit 0 and a printed summary of candidate count, window counts by regime/partition, parity status, best candidate ID, missed fills, and both promotion verdicts.

- [ ] **Step 3: Diagnose failures without tuning locked values**

If execution exposes a code defect, first use the systematic-debugging skill, add a failing deterministic test that reproduces it, run mandatory impact analysis for any existing symbol to edit, implement the minimal fix, rerun focused tests, and commit the fix after `gitnexus_detect_changes()`. Do not expand the grid, move windows, alter costs, change fill timing, or choose settings using holdout outcomes.

- [ ] **Step 4: Write the evidence report from the validated JSON**

The Markdown report must state: data source, input SHA-256, package versions, and exact last included session; adjusted-close, fund-inception, and survivor-selection limitations; the search/locked-holdout rule; counts of bear, bull, chop, and recovery windows; baseline/shadow parity result; a table for all named windows; a by-regime table; expanding and all-funds-common CAGR/max drawdown/return/turnover/trades; best settings; every failed gate; defense-only versus full-system result; SPY comparison; and separate bold defense/kicker `PROMOTABLE` or `NOT PROMOTABLE` verdicts. If neither component passes, say the implementation remains default-off/shadow-only. If defense passes but kicker fails, say active defense remains research-only until the separate future paper-shadow gate, while SQQQ stays disabled.

End with these literal small-bullet mechanics, filled with the measured verdict but without changing their meaning:

- Risk-on: keep current Strategy X behavior.
- Risk-off: move only the SPY defensive budget into BIL plus eligible managed-futures ETFs.
- Fast fresh crash: optionally add a capped SQQQ kicker after one defensive transition day.
- Exit SQQQ: after recovery, the bar limit, or risk-on; then enforce cooldown.
- Safety: missing BIL or conflicting legacy SQQQ leaves baseline unchanged.
- Deployment: off by default; shadow cannot place different orders; active is research-only unless every frozen gate passes.

- [ ] **Step 5: Run final repository verification**

Run:

```bash
python3 -m pytest backend/tests/test_strategy_x_bear.py \
  backend/tests/test_strategy_x.py \
  backend/tests/test_strategy_x_run_once.py \
  backend/tests/test_strategy_x_broker_wiring.py \
  backend/tests/test_strategy_x_broker_coexistence.py \
  backend/tests/test_strategy_x_bear_research.py -q
python3 -m compileall -q backend/strategy_x.py backend/strategy_x_bear.py \
  backend/strategies/strategy_x.py backend/broker.py \
  scripts/strategy_x_bear_research.py
git diff --check
```

Expected: all tests pass, compilation exits 0, and no whitespace errors.

- [ ] **Step 6: Verify scope and commit the evidence**

Run `gitnexus_detect_changes()` and confirm only expected Strategy X symbols and execution flows changed. Copy the validated JSON beside the report only when its size is reasonable and contains no secrets; otherwise include its SHA-256 digest and the exact regeneration command in the report. Then:

```bash
git add docs/superpowers/research/2026-08-25-strategy-x-bear-results.md \
  docs/superpowers/research/2026-08-25-strategy-x-bear-prices.csv.gz
git add docs/superpowers/research/2026-08-25-strategy-x-bear-results.json  # only if retained
git commit -m "docs: record Strategy X bear-system evidence"
```

---

### Task 6: Whole-Change Adversarial Review and Final Verification

**Files:**
- Modify only through reviewed fixes: files changed by Tasks 1-5.

**Interfaces:**
- Consumes: the approved spec, this plan, task ledger, all commits since the implementation base, test output, and real-data report.
- Produces: a clean final review or one reviewed fix wave with explicit residual rulings.

- [ ] **Step 1: Generate a whole-change review package**

Use the subagent-driven-development review-package script with the commit before Task 1 as base and current HEAD as head. Include the full diff, commit list, task ledger, spec path, plan path, verification output, and real-data artifact digest without pasting large artifacts into the coordinator context.

- [ ] **Step 2: Dispatch the most capable reviewer for an adversarial audit**

Require a read-only review of: lookahead/fill timing, continuous-state window slicing, off/shadow parity, state-machine off-by-one behavior, recovery exits, target-budget conservation, provenance/ownership/sell enforcement, legacy conflict, missing-data fallbacks, schema/broker wiring, search/holdout leakage, selected-settings reproducibility, SPY comparison honesty, and whether the report claims more than the data supports.

- [ ] **Step 3: Apply one reviewed fix wave if needed**

Send all Critical and Important findings to one fresh implementation agent. Require new failing tests for behavior defects, impact analysis before existing-symbol edits, focused and full test reruns, and a commit. Generate a scoped package and dispatch one re-review. Record any residual ruling in the ledger; do not silently waive it.

- [ ] **Step 4: Re-run final verification and inspect repository state**

Repeat Task 5 Step 5, run `gitnexus_detect_changes()`, inspect `git status --short --branch`, and confirm the unrelated untracked files remain untouched. Do not push.

- [ ] **Step 5: Finish on main without a merge operation**

Because the user explicitly requested direct work on `main`, do not create a PR, merge, push, or deploy. Retain the reviewed local commits on `main` and report exact commit IDs, tests, backtest verdict, best settings, limitations, and the small-bullet strategy TLDR.

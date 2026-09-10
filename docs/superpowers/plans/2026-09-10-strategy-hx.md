# Strategy HX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Strategy HX — a three-state QQQ regime machine that enters a bear book fast and leaves it slowly, sitting on top of the volatility-targeted leveraged core that is the only construction this repo has measured to survive.

**Architecture:** A pure module (`backend/strategy_hx.py`) holds `DEFAULTS`, the state machine `hx_state`, the book builder `hx_targets` and `strategy_hx_universe`; it has no clock, no network and no filesystem, so its tests import it directly. A thin wrapper (`backend/strategies/strategy_hx.py`) owns the point-in-time boundary, the cache, the band and the broker payload. `eb_core_weight` is imported unchanged from `backend/strategy_eb.py` and `targets_to_orders` unchanged from `backend/strategy_x.py` — HX adds a regime layer above them and forks nothing.

**Tech Stack:** Python 3.14, pytest. The engine API is reached only through `scripts/_api.py`; there is no local simulation anywhere in this plan.

**Spec:** `docs/superpowers/specs/2026-09-10-strategy-hx-design.md`

## Global Constraints

Copied from the spec. These are frozen before the first run and none of them are negotiable during execution.

- **Gate 1 — bears positive.** Each canonical bear window ends with positive P&L: rb1 2022-01-01→2022-06-30 (SPY −19.40%), rb2 2026-02-01→2026-04-01 (SPY −5.84%), rb3 2025-02-15→2025-04-15 (SPY −11.40%).
- **Gate 2 — beats SPY everywhere.** Beats SPY total return in every canonical window listed in `scripts/outlier_engine_test.py:33-46` (bear, bull, chop) and over the cycle 2021-11-01→2026-08-27 (SPY +77.11%).
- **Gate 3 — drawdown.** Max drawdown in each window no worse than SPY's in that window.
- **Gate 4 — hard stop.** Any run reaching 25% drawdown is stopped and rejected.
- **Order of runs.** rb3, rb2, rb1 (bear first, cheapest kill), then chop (rc1, rc2, rc3), then bull (ru1, ru3), then cycle. A failed bear window ends the variant; the next preregistered bear book is tried. If every variant fails, HX is killed and the fallback is built (not started).
- **Docs 200 and 201 are never touched.** Not read-and-written, not restored, not "temporarily". `hx_lab_setup.py` and `hx_run_native_api.py` both refuse when the resolved doc id is 200 or 201.
- **`ETF_LIQUID_SYMBOLS` is not widened** for PSQ or SH. Their 23.2 bps tier is conservative and stays. Do not edit `backend/simulated_execution.py`.
- **No push during a job, and never within 30 minutes of 08:30 CDT.** Check `GET /backtests` for a running/pending/queued/paused row before pushing.
- **Daily stepping.** Every `POST /backtests` carries `granularity: "86400"`. The `"60"` default is 1-minute stepping and would make every run unaffordable and un-comparable.
- **`equity_cost_tiers: "etf-liquid"`** on every POST.
- **Class name is `StrategyHx`.** `broker.py:_strategy_name_to_module_and_class` CamelCases `strategy_hx` to exactly that. `StrategyHX` loads nothing and runs the whole backtest inert — BT634331 completed 1,259 sessions that way.
- All verdicts come from the API engine, one job at a time, starting cash 6000. Local harness numbers never count.
- `backend/strategy_hx.py` is pure: no clock read, no network, no filesystem, no `import broker`. `broker.py` argparses at module scope and `SystemExit`s under pytest, which is why anything testable lives in the pure module.
- Every numeric parser fails toward LESS exposure. Non-finite or malformed config coerces to the documented default, never to a larger position.
- Before editing ANY existing symbol (this plan edits `broker.py` and `scripts/check_deployed_code.py`), run `mcp__gitnexus__impact({target: "<symbol>", direction: "upstream"})` and report the blast radius. Stop and warn on HIGH or CRITICAL.
- Run `mcp__gitnexus__detect_changes()` before every push, not before every commit.
- Credentials come from the environment or `.env` only. Never print a token, a password, or a DSN.
- The controller's archive under `output/research/hx-2026-09-10/` is run evidence, not source. Do not `git add` it.

---

### Task 1: DEFAULTS, parsers, and the declared universe

**Files:**
- Create: `backend/strategy_hx.py`
- Create: `backend/tests/test_strategy_hx.py`

**Interfaces:**
- Consumes: `Q`, `_finite` from `backend/strategy_x.py` (unchanged).
- Produces: `DEFAULTS: dict`, `_f(cfg, key, default=None) -> float`, `_i(cfg, key, default=None) -> int`, `_s(cfg, key, default=None) -> str`, `strategy_hx_universe(cfg) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_hx.py`:

```python
"""Pure tests for Strategy HX: the state machine, the books, the universe."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402


def cfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def test_the_default_universe_is_the_four_declared_legs():
    assert strategy_hx_universe(DEFAULTS) == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_every_bear_book_leg_is_declared():
    """A leg the broker never fetches has no bars and no price, and
    `targets_to_orders` silently skips it — the bear book would simply not
    exist. V5 is the widest preregistered book."""
    syms = strategy_hx_universe(
        cfg(bear_book={"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40}))
    assert syms == ["BIL", "GLD", "PSQ", "QQQ", "TQQQ"]


def test_a_malformed_bear_book_still_declares_the_fixed_legs():
    for junk in (None, [], "PSQ", 7):
        assert strategy_hx_universe(cfg(bear_book=junk)) == [
            "BIL", "QQQ", "TQQQ"], junk


def test_the_defaults_carry_the_working_single_position_cap():
    """`max_single_position_pct` is in broker.py's `_DEAD_STRATEGY_CONFIG_KEYS`
    — nothing reads it. The key the backtest engine actually reads is
    `broker_max_single_position_pct`, gated on `honour_single_position_cap`.
    Without it every 65% TQQQ buy is trimmed to $0.00 under the 15% failsafe
    (BT102936) and the whole battery measures an inert strategy."""
    assert DEFAULTS["broker_max_single_position_pct"] == 0.95
    assert DEFAULTS["honour_single_position_cap"] is True


def test_the_parsers_fail_toward_less_exposure():
    from strategy_hx import _f, _i, _s
    for bad in (None, "", "wide", float("nan"), float("inf")):
        assert _f(cfg(core_max_weight=bad), "core_max_weight") == 0.65, bad
        assert _i(cfg(sma_bars=bad), "sma_bars") == 50, bad
    assert _s({"core_symbol": " tqqq "}, "core_symbol") == "TQQQ"
    assert _s({"core_symbol": None}, "core_symbol") == "TQQQ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy_hx'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/strategy_hx.py`:

```python
"""Strategy HX sizing — fast-in, slow-out bear hedge on a vol-targeted core.

Design: docs/superpowers/specs/2026-09-10-strategy-hx-design.md

Two of the three canonical bear windows are TWO MONTHS long. No slow trend
filter can be positive inside one, which is why Strategy EB's inverse line —
gated on a 25-session weekly filter — has never passed. HX tests the one
construction that can react in days: a fast entry into a bear book and a slow,
hysteresis-gated exit. It contains NO exotic signal; the 34-claim audit of
2026-09-10 found nothing retail-viable this engine can express. It is a risk
transform on the only thing that has survived here, a volatility-targeted
leveraged index core, imported from strategy_eb unchanged.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.
"""
from __future__ import annotations

import math

from strategy_eb import eb_core_weight
from strategy_x import Q, _finite

__all__ = ["DEFAULTS", "hx_state", "hx_targets", "strategy_hx_universe"]


DEFAULTS = {
    "strategy_hx_enabled": False,
    # Volatility is measured on the UNLEVERED index; measuring it on TQQQ
    # would divide a 3x-inflated vol by the 3x leverage a second time.
    "reference_symbol": "QQQ",
    "core_symbol": "TQQQ",
    "core_leverage": 3.0,
    # ── the core transform, identical to the EB champion's numbers ──
    "target_vol": 0.20,
    "core_max_weight": 0.65,
    "weight_step": 0.05,
    "vol_fast_bars": 10,
    "vol_slow_bars": 40,
    # 70 covers all three consumers: the 40-bar slow vol window needs 41, the
    # 50-bar SMA compared against itself 20 sessions back needs 70, and the
    # 20-session low with a 10-session drawdown scan needs 30.
    "min_history_bars": 70,
    "bull_remainder_symbol": "QQQ",
    "cash_symbol": "BIL",
    "chop_core_damp": 0.5,
    # {SYMBOL: fraction of NAV}. Five preregistered variants; this is V1.
    "bear_book": {"PSQ": 0.60, "BIL": 0.40},
    # ── the state machine. The asymmetry IS the design: one session enters
    # BEAR, five consecutive closes above the average leave it. ──
    "fast_low_bars": 20,
    "sma_bars": 50,
    "drawdown_pct": 0.07,
    "drawdown_bars": 10,
    "exit_confirm_sessions": 5,
    "chop_slope_bars": 20,
    "chop_slope_pct": 0.015,
    # Passed to targets_to_orders as `core_band_pct`. A state change
    # rebalances the full book regardless of it; within a state it is what
    # keeps the book from trading.
    "rebalance_band": 0.10,
    "min_order_usd": 25.0,
    # ── broker-side, read by engines/backtest_engine.py, not by this module ──
    "honour_single_position_cap": True,
    # DEAD KEY, kept because the design brief names it. broker.py lists it in
    # `_DEAD_STRATEGY_CONFIG_KEYS` and warns on boot; that warning documents
    # the trap in the log rather than only in a comment.
    "max_single_position_pct": 0.95,
    # THE key that works. `_instance_single_position_pct` reads exactly this
    # name off any lane setting `honour_single_position_cap` and forwards it
    # as BROKER_MAX_SINGLE_POSITION_PCT. Without it the 15% failsafe trims a
    # 65%-of-NAV core buy to $0.00 and holds whatever it had — BT102936, and
    # Strategy XS shipped inert the same way.
    "broker_max_single_position_pct": 0.95,
}

#: The keys `eb_core_weight` reads, named explicitly rather than passing the
#: whole HX config through. They coincide today; naming them means a later HX
#: rename breaks a test instead of silently resizing a 3x position.
_EB_WEIGHT_KEYS = ("core_leverage", "target_vol", "core_max_weight",
                   "weight_step", "vol_fast_bars", "vol_slow_bars",
                   "min_history_bars")


# Own parsers rather than strategy_x's: its `_i` raises OverflowError on
# float("inf"), and it resolves a missing default against strategy_x's
# DEFAULTS, so any HX-only key without one raises TypeError. Both fail OPEN,
# the wrong direction for a parser guarding a levered position.
def _f(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0.0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return float(default)
        out = float(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _i(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return int(default)
        if isinstance(value, float) and not math.isfinite(value):
            return int(default)
        return int(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return int(default)


def _s(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, "")
    value = (cfg or {}).get(key, default)
    return str(value if value is not None else default).strip().upper()


def strategy_hx_universe(cfg) -> list:
    """Every symbol this strategy reads or trades, sorted and de-duplicated.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the reference symbol has no bars and the traded
    legs have no price, and BOTH failures are silent — the strategy just emits
    nothing. `broker._strategy_hx_universe_symbols` reads this to decide what
    to fetch.
    """
    out = {_s(cfg, "reference_symbol"), _s(cfg, "core_symbol"),
           _s(cfg, "bull_remainder_symbol"), _s(cfg, "cash_symbol")}
    raw = (cfg or {}).get("bear_book")
    if isinstance(raw, dict):
        for sym in raw:
            out.add(str(sym or "").strip().upper())
    return sorted(s for s in out if s)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_hx.py backend/tests/test_strategy_hx.py && git commit -m "feat(strategy-hx): defaults, parsers, and the declared universe

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 2: `hx_state` — the three-state machine

**Files:**
- Modify: `backend/strategy_hx.py` (append after `_s`, before `strategy_hx_universe`)
- Modify: `backend/tests/test_strategy_hx.py` (append)

**Interfaces:**
- Consumes: `_finite` from `backend/strategy_x.py`; `_f`, `_i` from this module.
- Produces: `hx_state(closes: list[float], prev_state: str, confirm: int, cfg) -> tuple[str, int]` — one of `"UNKNOWN"`, `"BULL"`, `"CHOP"`, `"BEAR"`, and the updated consecutive-close-above-SMA counter.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_hx.py`:

```python
from strategy_hx import hx_state  # noqa: E402


def rising(n, start=100.0, rate=0.001):
    """A clean 0.1%/day uptrend. Its realised volatility is exactly zero, so
    it is a STATE fixture only — `eb_core_weight` refuses on it by design."""
    return [start * ((1.0 + rate) ** i) for i in range(n)]


def crash(rise=110, days=10, pct=0.09):
    """110 rising sessions, then a 9% fall over 10 sessions."""
    closes = rising(rise)
    top = closes[-1]
    for i in range(1, days + 1):
        closes.append(top * (1.0 - pct * i / days))
    return closes


def test_a_clean_uptrend_is_bull():
    assert hx_state(rising(120), "BULL", 0, cfg()) == ("BULL", 0)


def test_a_nine_percent_ten_day_fall_fires_bear():
    """The fast leg. A 7% fall from a 20-session high, reached within 10
    sessions of that high, is what no weekly filter can react to in time."""
    assert hx_state(crash(), "BULL", 0, cfg()) == ("BEAR", 0)


def test_a_flat_tape_is_chop_not_bull():
    """A flat series never closes BELOW its own average, so the slope clause
    is what has to catch it. Without that clause a dead tape would hold the
    full 3x core."""
    assert hx_state([100.0] * 120, "BULL", 0, cfg()) == ("CHOP", 0)


def test_short_history_is_unknown_and_never_guesses():
    assert hx_state(rising(30), "BULL", 0, cfg()) == ("UNKNOWN", 0)
    assert hx_state(rising(69), "BULL", 0, cfg()) == ("UNKNOWN", 0)


def test_a_nan_close_reads_as_unknown_not_as_no_signal():
    series = rising(120)
    series[-4] = float("nan")
    assert hx_state(series, "BULL", 0, cfg()) == ("UNKNOWN", 0)


def test_four_sessions_above_the_average_do_not_leave_bear_and_five_do():
    """The slow leg, and the whole reason HX is not just a fast filter: a
    four-session bounce inside a bear must not put a 3x fund back on."""
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for _ in range(4):
        closes.append(top * 1.10)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert (state, confirm) == ("BEAR", 4)
    closes.append(top * 1.10)
    assert hx_state(closes, state, confirm, cfg()) == ("BULL", 0)


def test_one_close_below_the_average_resets_the_exit_counter():
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for px in [top * 1.10] * 4 + [top * 0.5]:
        closes.append(px)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert (state, confirm) == ("BEAR", 0)


def test_the_drawdown_trigger_does_not_refire_on_stale_history():
    """After an exit the last 10 sessions still contain the crash lows. A
    trigger that scanned them would slam straight back into BEAR one session
    after confirming its way out, and the 5-session hysteresis would buy
    nothing at all."""
    closes = crash()
    top = max(closes)
    state, confirm = "BEAR", 0
    for _ in range(6):
        closes.append(top * 1.10)
        state, confirm = hx_state(closes, state, confirm, cfg())
    assert state == "BULL"


def test_a_corrupted_previous_state_reads_as_unknown_not_as_bull():
    """A damaged cache row must not be a third behaviour. Anything
    unrecognised takes the entry path, which can only ever DE-risk."""
    for junk in (None, "", "ON", 7, "bearish"):
        state, _ = hx_state(crash(), junk, 0, cfg())
        assert state == "BEAR", junk
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: FAIL — `ImportError: cannot import name 'hx_state' from 'strategy_hx'`

- [ ] **Step 3: Write minimal implementation**

Insert into `backend/strategy_hx.py`, after `_s` and before `strategy_hx_universe`:

```python
_STATES = frozenset({"UNKNOWN", "BULL", "CHOP", "BEAR"})


def _sma(values, bars):
    if bars <= 0 or len(values) < bars:
        return None
    return sum(values[-bars:]) / float(bars)


def _entered_bear(prices, cfg) -> bool:
    """The FAST leg. Either rule alone fires; both are one-session tests.

    Rule A — a 20-session low that is ALSO below the 50-session average. A new
    low alone happens in every healthy pullback.

    Rule B — a `drawdown_pct` fall from the highest close of the prior
    `fast_low_bars` sessions, where that high is at most `drawdown_bars`
    sessions old. The AGE test is load-bearing: scanning the last ten sessions
    for any historical 7% drop instead re-fires on stale crash lows the
    session after a confirmed exit, which cancels the 5-session hysteresis and
    turns the slow leg into a no-op.
    """
    low_bars = max(2, _i(cfg, "fast_low_bars"))
    dd_bars = max(1, _i(cfg, "drawdown_bars"))
    dd_pct = max(0.0, min(1.0, _f(cfg, "drawdown_pct")))
    sma_bars = max(2, _i(cfg, "sma_bars"))
    close = prices[-1]

    sma = _sma(prices, sma_bars)
    if sma is not None and len(prices) > low_bars:
        prior = prices[-(low_bars + 1):-1]
        if close < min(prior) and close < sma:
            return True

    window = prices[-(low_bars + 1):]
    peak = max(window)
    if peak <= 0:
        return False
    age = len(window) - 1 - max(i for i, v in enumerate(window) if v == peak)
    return age <= dd_bars and close <= peak * (1.0 - dd_pct)


def hx_state(closes, prev_state, confirm, cfg) -> tuple:
    """(state, confirm counter) for this session.

    Read-only on everything. The caller persists both; passing them back in is
    what makes the exit hysteresis survive a restart, and a cold start reads
    UNKNOWN rather than inventing a regime.

    The exit names its destination — BEAR leaves to BULL, as the spec writes
    the rule — and the BULL/CHOP test applies from the NEXT session. Running
    the CHOP test on the flip session would land a confirmed five-session
    recovery in a damped book on a technicality.
    """
    prices = _finite(closes)
    # Whichever consumer needs most history. Without the window terms a raised
    # `sma_bars` would compare a truncated average against itself, and a
    # shorter window on the same tape measures LESS risk — the one direction
    # this module must never fail in.
    minimum = max(2, _i(cfg, "min_history_bars"),
                  _i(cfg, "sma_bars") + _i(cfg, "chop_slope_bars"),
                  _i(cfg, "fast_low_bars") + _i(cfg, "drawdown_bars"))
    if prices is None or len(prices) < minimum:
        return "UNKNOWN", 0

    state = str(prev_state or "").strip().upper()
    if state not in _STATES:
        state = "UNKNOWN"
    try:
        count = int(confirm)
    except (TypeError, ValueError, OverflowError):
        count = 0
    # Bounded like every other parsed counter in this repo, so a corrupted row
    # cannot carry an unbounded integer into the comparison below.
    count = max(0, min(100_000, count))

    sma_bars = max(2, _i(cfg, "sma_bars"))
    sma = _sma(prices, sma_bars)
    if sma is None or not math.isfinite(sma) or sma <= 0:
        return "UNKNOWN", 0
    close = prices[-1]

    if state == "BEAR":
        need = max(1, _i(cfg, "exit_confirm_sessions"))
        count = count + 1 if close > sma else 0
        return ("BULL", 0) if count >= need else ("BEAR", count)

    if _entered_bear(prices, cfg):
        return "BEAR", 0

    slope_bars = max(1, _i(cfg, "chop_slope_bars"))
    prior_sma = _sma(prices[:-slope_bars], sma_bars)
    # A dead tape closes AT its own average, never below it, so the slope
    # clause is the only thing standing between a flat market and a full 3x
    # core. Unmeasurable slope reads as flat: CHOP is the cheaper error.
    flat = True
    if prior_sma is not None and prior_sma > 0:
        flat = abs(sma / prior_sma - 1.0) < max(0.0, _f(cfg, "chop_slope_pct"))
    if close < sma or flat:
        return "CHOP", 0
    return "BULL", 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: PASS — 14 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_hx.py backend/tests/test_strategy_hx.py && git commit -m "feat(strategy-hx): the fast-in, slow-out state machine

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 3: `hx_targets` — one book per state

**Files:**
- Modify: `backend/strategy_hx.py` (append after `hx_state`)
- Modify: `backend/tests/test_strategy_hx.py` (append)

**Interfaces:**
- Consumes: `eb_core_weight(closes, cfg, trend_state="ON") -> float | None` from `backend/strategy_eb.py`, unchanged; `_EB_WEIGHT_KEYS`, `_f`, `_s` from this module; `Q` from `backend/strategy_x.py`.
- Produces: `hx_targets(closes, state, cfg) -> dict[str, float]` — target weight per symbol as a fraction of NAV.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_hx.py`:

```python
from strategy_hx import hx_targets  # noqa: E402


def wobbly(n, start=100.0, rate=0.001, amp=0.004):
    """An uptrend with a deterministic alternating overlay. `rising` has
    exactly zero realised volatility, so `eb_core_weight` correctly refuses on
    it; any test of the CORE needs a tape with actual variance."""
    return [start * ((1.0 + rate) ** i) * (1.0 + (amp if i % 2 else -amp))
            for i in range(n)]


def total(targets):
    return round(sum(targets.values()), 6)


def test_bull_holds_the_vol_targeted_core_with_the_remainder_in_qqq():
    targets = hx_targets(wobbly(120), "BULL", cfg())
    assert targets == {"TQQQ": 0.45, "QQQ": 0.55}
    assert total(targets) == 1.0


def test_chop_halves_the_core_and_parks_the_rest_in_cash():
    targets = hx_targets(wobbly(120), "CHOP", cfg())
    assert targets == {"TQQQ": 0.225, "BIL": 0.775}
    assert total(targets) == 1.0


def test_an_unmeasurable_core_goes_to_cash_not_to_a_default_weight():
    """`eb_core_weight` returns None on a flat tape, a short history or a
    zero leverage. Every one of those would otherwise resolve to MORE
    leverage, so None means cash."""
    assert hx_targets(rising(120), "BULL", cfg()) == {"BIL": 1.0}
    assert hx_targets(wobbly(120), "BULL", cfg(core_leverage=0)) == {"BIL": 1.0}


def test_unknown_is_one_hundred_percent_cash():
    assert hx_targets(rising(30), "UNKNOWN", cfg()) == {"BIL": 1.0}
    assert hx_targets(wobbly(120), "nonsense", cfg()) == {"BIL": 1.0}


def test_the_default_bear_book_is_held_as_written():
    assert hx_targets(crash(), "BEAR", cfg()) == {"PSQ": 0.60, "BIL": 0.40}


def test_a_short_bear_book_puts_the_shortfall_in_cash():
    """V2 and V4 are deliberately small inverse sleeves. The unspent weight is
    cash, never a bigger short."""
    assert hx_targets(crash(), "BEAR",
                      cfg(bear_book={"SQQQ": 0.25})) == {"SQQQ": 0.25,
                                                         "BIL": 0.75}


def test_a_bear_book_summing_past_one_is_renormalised_not_clipped():
    """Clipping would silently drop whichever leg came last; renormalising
    keeps the operator's intended RATIO and only scales it into budget."""
    targets = hx_targets(crash(), "BEAR",
                         cfg(bear_book={"PSQ": 0.8, "GLD": 0.6}))
    assert total(targets) == 1.0
    assert round(targets["PSQ"] / targets["GLD"], 4) == round(0.8 / 0.6, 4)


def test_a_bear_book_of_junk_is_cash_not_an_empty_book():
    for junk in (None, {}, {"": 0.5}, {"PSQ": "lots"}, {"PSQ": -0.4},
                 {"PSQ": float("nan")}):
        assert hx_targets(crash(), "BEAR", cfg(bear_book=junk)) == {
            "BIL": 1.0}, junk


def test_the_three_variant_books_the_battery_will_actually_run():
    for book, expected in (
            ({"SQQQ": 0.25, "BIL": 0.75}, {"SQQQ": 0.25, "BIL": 0.75}),
            ({"SH": 0.60, "BIL": 0.40}, {"SH": 0.60, "BIL": 0.40}),
            ({"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40},
             {"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40})):
        assert hx_targets(crash(), "BEAR", cfg(bear_book=book)) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: FAIL — `ImportError: cannot import name 'hx_targets' from 'strategy_hx'`

- [ ] **Step 3: Write minimal implementation**

Insert into `backend/strategy_hx.py`, after `hx_state`:

```python
def _eb_cfg(cfg):
    """The seven keys `eb_core_weight` reads, and nothing else. Passing the
    whole HX config works today because the names coincide, and would keep
    working silently after an HX rename — while the core quietly resized
    itself against strategy_eb's own DEFAULTS."""
    return {key: (cfg or {}).get(key, DEFAULTS[key])
            for key in _EB_WEIGHT_KEYS}


def _bear_book(cfg) -> dict:
    """The configured bear book, normalised to sum at most 1.

    Renormalised rather than clipped when it sums past 1: clipping drops
    whichever leg iterated last, which is a different portfolio from the one
    the operator wrote. Any shortfall is the caller's to send to cash.
    """
    raw = (cfg or {}).get("bear_book")
    if not isinstance(raw, dict):
        return {}
    book: dict = {}
    for sym, weight in raw.items():
        name = str(sym or "").strip().upper()
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        if not name or not math.isfinite(value) or value <= 0:
            continue
        book[name] = round(book.get(name, 0.0) + value, Q)
    total = round(sum(book.values()), Q)
    if total > 1.0:
        book = {s: round(w / total, Q) for s, w in book.items()}
    return book


def hx_targets(closes, state, cfg) -> dict:
    """Target weight per symbol as a fraction of NAV, for one state.

    Every path that cannot evaluate its own risk resolves to CASH. There is no
    fallback weight anywhere here: a short history, a flat tape, a junk bear
    book and an unrecognised state all mean the same thing, and T-bills is the
    only answer that cannot be wrong in the expensive direction.
    """
    cash = _s(cfg, "cash_symbol")
    core = _s(cfg, "core_symbol")
    label = str(state or "").strip().upper()

    if label == "BEAR":
        book = _bear_book(cfg)
        spent = round(sum(book.values()), Q)
        if spent <= 0:
            return {cash: 1.0}
        rest = round(1.0 - spent, Q)
        if rest > 0:
            book[cash] = round(book.get(cash, 0.0) + rest, Q)
        return book

    if label not in {"BULL", "CHOP"}:
        return {cash: 1.0}

    weight = eb_core_weight(closes, _eb_cfg(cfg), "ON")
    if weight is None:
        return {cash: 1.0}
    weight = max(0.0, min(1.0, float(weight)))

    if label == "CHOP":
        # The damp is applied AFTER the EB quantisation, which is what the
        # spec says: "core weight x chop_core_damp". Damping before it would
        # re-enter the 0.05 grid and change the measured construction.
        damp = max(0.0, min(1.0, _f(cfg, "chop_core_damp")))
        weight = round(weight * damp, Q)
        remainder = cash
    else:
        remainder = _s(cfg, "bull_remainder_symbol") or cash

    targets: dict = {}
    if weight > 0:
        targets[core] = weight
    rest = round(1.0 - weight, Q)
    if rest > 0:
        targets[remainder] = round(targets.get(remainder, 0.0) + rest, Q)
    return targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py -q`
Expected: PASS — 23 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_hx.py backend/tests/test_strategy_hx.py && git commit -m "feat(strategy-hx): one book per state, every failure to cash

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 4: The wrapper — `StrategyHx.run_once`

**Files:**
- Create: `backend/strategies/strategy_hx.py`
- Create: `backend/tests/test_strategy_hx_run_once.py`

**Interfaces:**
- Consumes: `DEFAULTS`, `_f`, `_s`, `hx_state`, `hx_targets`, `strategy_hx_universe` from `backend/strategy_hx.py`; `pit_daily_observations(bars, as_of) -> list[tuple[str, float]]` and `targets_to_orders(targets, *, nav, positions, prices, cash, config, owned=None) -> tuple[dict, dict]` from `backend/strategy_x.py`.
- Produces: `class StrategyHx` with `run_once(self, symbols, prices, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None, mode=None, **kwargs) -> dict`.
- Cache keys owned: `_strategy_hx_state`, `_strategy_hx_confirm`, `_strategy_hx_last`, `_strategy_hx_logged`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_hx_run_once.py`:

```python
"""Wrapper tests for Strategy HX: the broker contract and cache behaviour."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path. Adding backend/strategies/ too would make
# `strategy_x` resolve to the WRAPPER rather than the pure module — they share
# a name — and the wrapper imports the pure one, so it self-imports.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_hx import StrategyHx  # noqa: E402
from strategy_hx import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"QQQ": 400.0, "TQQQ": 60.0, "BIL": 91.0, "PSQ": 10.0}


def bars(closes, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - i))).isoformat(), "c": c}
            for i, c in enumerate(closes)]


def wobbly(n, start=100.0, rate=0.001, amp=0.004):
    return [start * ((1.0 + rate) ** i) * (1.0 + (amp if i % 2 else -amp))
            for i in range(n)]


def crash(rise=110, days=10, pct=0.09):
    closes = wobbly(rise)
    top = max(closes)
    for i in range(1, days + 1):
        closes.append(top * (1.0 - pct * i / days))
    return closes


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None):
        self._cash = cash
        self._positions = dict(positions or {})

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or PRICES
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_hx_enabled"] = True
    value.update(overrides)
    return value


def data_for(closes):
    out = {"QQQ": {"bars": bars(closes)}}
    for symbol in ("TQQQ", "BIL", "PSQ"):
        out[symbol] = {"bars": bars([50.0] * len(closes))}
    return out


def test_a_disabled_flag_or_a_missing_emulator_emits_nothing():
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, dict(DEFAULTS), {},
                                 data=data_for(wobbly(120)),
                                 portfolio_emulator=FakeEmulator()) == {}
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                 data=data_for(wobbly(120))) == {}


def test_a_blind_tick_does_nothing_rather_than_flattening_the_book():
    """Live dispatch passes data=None. A strategy that cannot see its own
    reference index must do NOTHING, not exit to cash."""
    cache = {}
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {}, data=None,
                                 portfolio_emulator=FakeEmulator(),
                                 strategy_cache=cache) == {}
    assert "_strategy_hx_state" not in cache


def test_bull_buys_the_core_and_the_remainder():
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(120)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out["TQQQ"] == 1 and out["QQQ"] == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0
    assert out["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0
    assert cache["_strategy_hx_state"] == "BULL"


def test_bear_sells_the_core_and_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary.
    Strategy X shipped without it and all 965 of its sells logged
    would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(crash()),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert cache["_strategy_hx_state"] == "BEAR"
    sells = [s for s, d in out.items() if not s.startswith("_") and d == -1]
    assert sells == ["TQQQ"]
    assert out["_nexus_action_intents"]["TQQQ"] == "etf_sell"


def test_short_history_parks_the_book_in_cash():
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(30)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_hx_state"] == "UNKNOWN"
    assert out["BIL"] == 1 and "TQQQ" not in out


def test_a_book_already_at_target_sends_nothing():
    """The band is what makes this a rarely-trading strategy. 75 TQQQ at $60
    and 13.75 QQQ at $400 is exactly 45/55 of a $10,000 NAV."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 75.0, "QQQ": 13.75})
    assert StrategyHx().run_once(
        ["QQQ"], PRICES, NOW, cfg(), {}, data=data_for(wobbly(120)),
        portfolio_emulator=emu,
        strategy_cache={"_strategy_hx_state": "BULL",
                        "_strategy_hx_confirm": 0}) == {}


def test_an_unpriced_leg_sends_its_weight_to_cash_not_to_the_core():
    """Missing a price for a leg must never concentrate the book into the
    legs that DO have one — least of all a 3x fund."""
    cache = {}
    out = StrategyHx().run_once(
        ["QQQ"], {"QQQ": 400.0, "BIL": 91.0}, NOW, cfg(), {},
        data={"QQQ": {"bars": bars(crash())},
              "BIL": {"bars": bars([91.0] * 120)}},
        portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    assert cache["_strategy_hx_last"]["targets"] == {"BIL": 1.0}
    assert out["BIL"] == 1


def test_it_publishes_its_own_universe():
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(120)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out["_nexus_discovered"] == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_the_schema_header_contains_every_default():
    import json
    import re
    path = os.path.join(_backend, "strategies", "strategy_hx.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert schema["strategy"] == "strategy_hx"
    assert schema["execution_scope"] == "run_once"
    assert schema["config"] == DEFAULTS


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    """broker.py resolves a run-once strategy by CamelCasing its id, so the
    class name is part of the contract. Strategy XS shipped once as
    `StrategyXS` and BT634331 ran 1,259 sessions completely inert."""
    import ast
    import importlib

    module = importlib.import_module("strategies.strategy_hx")
    assert hasattr(getattr(module, "StrategyHx"), "run_once")

    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_name_to_module_and_class")
    # `re` goes in as a GLOBAL rather than as a synthesised import node: an
    # ast.Import built by hand has no lineno and compile() rejects it.
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), broker, "exec"), ns)
    assert ns["_strategy_name_to_module_and_class"]("strategy_hx") == (
        "strategy_hx", "StrategyHx")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx_run_once.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.strategy_hx'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/strategies/strategy_hx.py`. Line 1 and line 2 are the header comments; keep them on ONE line each — `strategies_meta._parse_header_meta` reads only the first 80 lines and `json.loads` the remainder of the marker line.

```python
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_hx", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_hx_enabled": false, "reference_symbol": "QQQ", "core_symbol": "TQQQ", "core_leverage": 3.0, "target_vol": 0.2, "core_max_weight": 0.65, "weight_step": 0.05, "vol_fast_bars": 10, "vol_slow_bars": 40, "min_history_bars": 70, "bull_remainder_symbol": "QQQ", "cash_symbol": "BIL", "chop_core_damp": 0.5, "bear_book": {"PSQ": 0.6, "BIL": 0.4}, "fast_low_bars": 20, "sma_bars": 50, "drawdown_pct": 0.07, "drawdown_bars": 10, "exit_confirm_sessions": 5, "chop_slope_bars": 20, "chop_slope_pct": 0.015, "rebalance_band": 0.1, "min_order_usd": 25.0, "honour_single_position_cap": true, "max_single_position_pct": 0.95, "broker_max_single_position_pct": 0.95}}
# INTELLISTOCK_DESCRIPTION: Fast-in, slow-out bear hedge. A three-state QQQ machine enters a bear book on a single session (a 20-day low under the 50-day average, or a 7% fall from a 20-day high inside 10 sessions) and needs five consecutive closes above the average to leave it. BULL holds a volatility-targeted TQQQ core with the remainder in QQQ; CHOP halves the core into BIL. A risk transform, not an alpha.
"""Strategy HX wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_hx.py`, which is pure. This
file owns only what needs the broker: the point-in-time boundary, the
emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-09-10-strategy-hx-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_hx import (  # noqa: E402
    DEFAULTS,
    _f,
    _s,
    hx_state,
    hx_targets,
    strategy_hx_universe,
)
from strategy_x import pit_daily_observations, targets_to_orders  # noqa: E402

# Route through intellistock_logger, NOT print() and NOT utils.log_message.
# The backtest engine runs the broker with `detach=False, remove=True` and
# DISCARDS container stdout on success. intellistock_logger fans out to
# BacktestResults.logs, the only log an operator reads; Strategy XS used
# utils.log_message and its lines never reached the sink.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyHx")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyHx] {msg}")


#: Every HX exit is a rebalance of an ETF book, which is what the broker's
#: sell whitelist calls `etf_sell`. broker.py's Z2.1 check reads it off the
#: strategy summary; a sell with no recognised intent logs
#: would_block_in_phase2=True — 965 of 965 sells on BT406990.
_SELL_INTENT = "etf_sell"

_STATE_KEY = "_strategy_hx_state"
_CONFIRM_KEY = "_strategy_hx_confirm"
_LAST_DECISION_KEY = "_strategy_hx_last"
_LOGGED_KEY = "_strategy_hx_logged"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a recurring condition at most once per `scope`. Every refusal here
    is a STANDING condition, so it repeats identically on every tick of a
    session; the sink is BacktestResults.logs, read by eye, and drowning it is
    how a real refusal goes unnoticed."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _emit(decisions, sizes, universe) -> dict:
    """The broker payload for a set of decisions."""
    out = dict(decisions)
    sizes = dict(sizes)
    # The broker's buy gate reserves `_cash_reserve_floor_pct` (default 0.10)
    # of STARTING value as untouchable cash, sized for a many-name discovery
    # book. On a small ETF book it blocked 775 of 805 buys in BT 400783. This
    # book's risk control is the state machine, not a cash floor.
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = list(universe)
    out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
    out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                      if d == -1]
    out["_nexus_action_intents"] = {s: _SELL_INTENT
                                    for s, d in decisions.items() if d == -1}
    return out


class StrategyHx:
    # The class name is NOT free. `broker.py` resolves a run-once strategy by
    # CamelCasing its id — `strategy_hx` -> `StrategyHx` — and logs
    # "Class not found ... has no run_once method; skipping" when it misses,
    # then runs the whole backtest inert.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_hx_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        universe = strategy_hx_universe(cfg)
        reference = _s(cfg, "reference_symbol")

        # THE point-in-time boundary: strictly-earlier NY sessions only.
        observations = pit_daily_observations(_bars_for(data, reference),
                                              current_time)
        if not observations:
            # No session is knowable here, so the throttle falls back to the
            # call DATE. The state is deliberately NOT written: a blind tick
            # must leave the machine exactly as it found it.
            _log_once(cache, "blind", str(current_time)[:10],
                      f"StrategyHx: REFUSING to trade — no visible {reference}"
                      " daily closes. Live passes data=None; a strategy that "
                      "cannot see its regime must do NOTHING.", "red")
            return {}
        session_id = observations[-1][0]
        closes = [close for _, close in observations]

        prev_state = str(cache.get(_STATE_KEY) or "").strip().upper()
        state, confirm = hx_state(closes, prev_state,
                                  cache.get(_CONFIRM_KEY), cfg)
        cache[_STATE_KEY] = state
        cache[_CONFIRM_KEY] = confirm
        if state == "UNKNOWN":
            _log_once(cache, "unknown", session_id,
                      f"StrategyHx {session_id} | UNKNOWN — {len(closes)} "
                      f"{reference} closes, need "
                      f"{cfg.get('min_history_bars')}, or a close is not "
                      "finite and positive. Book is 100% cash; a cold start "
                      "never levers up.", "red")

        targets = hx_targets(closes, state, cfg)

        # Prices the broker did not carry: a declared leg absent from the
        # operator's watchlist falls back to the last VISIBLE close, which is
        # the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in (prices or {}).items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_observations(_bars_for(data, symbol),
                                             current_time)
            if visible and float(visible[-1][1]) > 0:
                eff[symbol] = float(visible[-1][1])

        # A leg with no price at all is skipped by `targets_to_orders`, which
        # would silently CONCENTRATE the book into whatever is left. Move its
        # weight to cash explicitly instead.
        cash_symbol = _s(cfg, "cash_symbol")
        missing = sorted(s for s in targets if float(eff.get(s) or 0.0) <= 0)
        if missing:
            spare = 0.0
            for symbol in missing:
                spare += targets.pop(symbol, 0.0)
            if spare > 0 and float(eff.get(cash_symbol) or 0.0) > 0:
                targets[cash_symbol] = round(
                    targets.get(cash_symbol, 0.0) + spare, 6)
            _log_once(cache, "unpriced", f"{session_id}:{','.join(missing)}",
                      f"StrategyHx {session_id} | no price for "
                      f"{', '.join(missing)} — weight to {cash_symbol}", "red")

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}

        band = max(0.0, _f(cfg, "rebalance_band"))
        held = {}
        for symbol in set(list(targets) + list(universe)):
            held[symbol] = (float(positions.get(symbol) or 0.0)
                            * float(eff.get(symbol) or 0.0)) / nav
        drift = max((abs(targets.get(s, 0.0) - held.get(s, 0.0))
                     for s in held), default=0.0)
        changed = state != prev_state
        # A state change rebalances the FULL book immediately — that is the
        # whole point of a fast entry. Within a state the band suppresses
        # drift, which is what keeps turnover down.
        if not changed and drift <= band:
            return {}

        # `targets_to_orders` reads the band under the name `core_band_pct`.
        # HX calls it `rebalance_band`, so without this mapping the band would
        # silently fall back to strategy_x's own 0.05 default — half the
        # intended width, and twice the turnover.
        order_cfg = {**cfg, "core_band_pct": band}
        try:
            cash = float(portfolio_emulator.get_buying_power(prices=eff))
        except (AttributeError, TypeError):
            cash = float(portfolio_emulator.get_cash() or 0.0)

        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=order_cfg, owned=set(universe))

        cache[_LAST_DECISION_KEY] = {
            "session": session_id, "state": state, "confirm": confirm,
            "drift": round(drift, 6), "targets": dict(targets),
            "orders": len(decisions),
        }
        reason = "state change" if changed else f"band breach {drift:.1%}"
        _log_once(cache, "decision", f"{session_id}:{state}:{reason}",
                  f"StrategyHx {session_id} | {state} ({reason}) | targets="
                  + ", ".join(f"{s} {w:.1%}"
                              for s, w in sorted(targets.items()))
                  + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        return _emit(decisions, sizes, universe)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx_run_once.py -q`
Expected: PASS — 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/strategy_hx.py backend/tests/test_strategy_hx_run_once.py && git commit -m "feat(strategy-hx): the broker-facing wrapper and its cache contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 5: Broker wiring and the deploy check

**Files:**
- Modify: `backend/broker.py` — add `_strategy_hx_universe_symbols` immediately after `_strategy_eb_universe_symbols` (ends at `broker.py:4592`, just before `_strategy_x_prepare`); add one branch in the universe fetch loop after the `strategy_eb` loop at `broker.py:10468-10471`.
- Modify: `scripts/check_deployed_code.py:27-45` — two entries appended to `FILES`.
- Create: `backend/tests/test_strategy_hx_broker_wiring.py`

**Interfaces:**
- Consumes: `DEFAULTS`, `strategy_hx_universe` from `backend/strategy_hx.py`; `_truthy` from `broker.py`.
- Produces: `_strategy_hx_universe_symbols(cached_strategies) -> list[str]`.

- [ ] **Step 1: Run impact analysis before touching broker.py**

The project CLAUDE.md requires this before editing any symbol. Run, and report the blast radius (direct callers, affected processes, risk level). Stop and warn the user if either returns HIGH or CRITICAL.

```
mcp__gitnexus__impact({target: "_strategy_eb_universe_symbols", direction: "upstream"})
mcp__gitnexus__impact({target: "local_hashes", direction: "upstream"})
```

Expect LOW for both: the new function is additive and the fetch-loop branch mirrors the three that precede it; `local_hashes` reads a module-level tuple.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_strategy_hx_broker_wiring.py`:

```python
"""Strategy HX must get bars for legs the watchlist never lists.

Missing the fetch site is SILENT: the reference index has no bars, the state
machine reads UNKNOWN forever, and the strategy parks the whole book in cash
for the length of the backtest while every unit test still passes.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _extract(*names):
    """AST-extract broker functions into a stub namespace. broker.py argparses
    at module scope and SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    # `_truthy` comes along because the HX config reader calls it, and the
    # blanket `except Exception` would turn the resulting NameError into an
    # empty result rather than a failure.
    keep = set(names) | {"_truthy"}
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in keep]
    found = {n.name for n in wanted}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_hx", "config": config}]


def test_the_declared_hx_universe_is_returned():
    ns = _extract("_strategy_hx_universe_symbols")
    assert ns["_strategy_hx_universe_symbols"](spec(strategy_hx_enabled=True)) \
        == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_a_variant_bear_book_adds_its_legs():
    ns = _extract("_strategy_hx_universe_symbols")
    syms = ns["_strategy_hx_universe_symbols"](
        spec(strategy_hx_enabled=True, bear_book={"SQQQ": 0.25, "BIL": 0.75}))
    assert "SQQQ" in syms and "PSQ" not in syms


def test_a_disabled_or_absent_hx_contributes_no_symbols():
    """The string "false" counts as disabled: raw truthiness says
    bool("false") is True, so a config storing the flag as a string would
    otherwise fetch bars for an inert strategy."""
    ns = _extract("_strategy_hx_universe_symbols")
    fn = ns["_strategy_hx_universe_symbols"]
    assert fn(spec(strategy_hx_enabled=False)) == []
    assert fn(spec(strategy_hx_enabled="false")) == []
    assert fn([{"strategy": "graph_nexus_analysis", "config": {}}]) == []


def test_a_malformed_spec_list_does_not_raise():
    ns = _extract("_strategy_hx_universe_symbols")
    for junk in (None, [], [None], ["strategy_hx"], [{"strategy": None}]):
        assert ns["_strategy_hx_universe_symbols"](junk) == [], junk


def test_the_fetch_site_references_the_hx_universe():
    """A source assertion, because the fetch site is inline in a 4,000-line
    function and cannot be AST-extracted."""
    source = open(_BROKER).read()
    uses = source.count("_strategy_hx_universe_symbols(")
    assert uses >= 2, f"expected the definition and the fetch site, saw {uses}"


def test_the_deploy_check_hashes_both_hx_files():
    """2026-09-03: the EB pair was missing from FILES, so a push that changed
    only strategy_eb.py reported 'deployed' instantly and a pre-registered
    engine run started on the OLD image."""
    path = os.path.join(os.path.dirname(_BACKEND), "scripts",
                        "check_deployed_code.py")
    source = open(path).read()
    assert '"backend/strategy_hx.py"' in source
    assert '"backend/strategies/strategy_hx.py"' in source
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx_broker_wiring.py -q`
Expected: FAIL — `AssertionError: missing from broker.py: {'_strategy_hx_universe_symbols'}`

- [ ] **Step 4: Add the broker function**

Insert into `backend/broker.py` immediately after `_strategy_eb_universe_symbols` returns (line 4592), before `def _strategy_x_prepare`:

```python
def _strategy_hx_universe_symbols(cached_strategies):
    """Symbols strategy_hx needs bars for, from its own config.

    Same contract as `_strategy_eb_universe_symbols` and the same reason: a
    strategy that trades symbols the operator never listed must declare them,
    or `price_history` is built without them and the strategy is silently
    inert. HX is worse than inert without this — with no reference bars the
    state machine reads UNKNOWN and parks the whole book in T-bills for the
    length of the run. Returns [] when strategy_hx is absent or disabled, so
    this is a no-op for every other instance.
    """
    try:
        from strategy_hx import DEFAULTS as _HX_DEFAULTS, strategy_hx_universe
    except Exception:
        return []
    out = []
    try:
        for spec in (cached_strategies or []):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("strategy") or "").strip().lower()
            if name not in {"strategy_hx", "strategyhx"}:
                continue
            merged = {**_HX_DEFAULTS, **(spec.get("config") or {})}
            if not _truthy(merged.get("strategy_hx_enabled", False)):
                continue
            for sym in strategy_hx_universe(merged):
                if sym and sym not in out:
                    out.append(sym)
    except Exception:
        return []
    return out
```

- [ ] **Step 5: Add the fetch-loop branch**

Insert into `backend/broker.py` immediately after the `strategy_eb` loop (`broker.py:10468-10471`), before `symbols_for_data = symbols_for_fetch`:

```python
    # 2026-09-10: strategy_hx declares its own universe the same way — the
    # reference index its state machine reads, the levered core, the bull
    # remainder, cash, and every leg of the configured bear book. HX does not
    # use `_strategy_x_prepare`; its wrapper prices unlisted legs itself from
    # the last visible close, so this fetch site is the ONLY wiring point and
    # missing it is silent.
    for _hx_sym in _strategy_hx_universe_symbols(_cached_strategies):
        if _hx_sym not in symbols_for_fetch:
            symbols_for_fetch.append(_hx_sym)
            _log(f"Adding {_hx_sym} to bar data for strategy_hx", "cyan")
```

- [ ] **Step 6: Add both files to the deploy check**

In `scripts/check_deployed_code.py`, replace the last entry of `FILES`:

```python
    "backend/outlier_features.py",
```

with:

```python
    "backend/outlier_features.py",
    # 2026-09-10: HX, for the same reason the EB pair is here. A push that
    # changes only the strategy pair must not report "deployed" before the
    # image carrying it exists.
    "backend/strategy_hx.py",
    "backend/strategies/strategy_hx.py",
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx_broker_wiring.py -q`
Expected: PASS — 7 passed.

- [ ] **Step 8: Run the whole HX set plus the neighbours the edit could disturb**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py tests/test_strategy_hx_run_once.py tests/test_strategy_hx_broker_wiring.py tests/test_strategy_eb_broker_wiring.py tests/test_strategy_xs_broker_wiring.py -q`
Expected: PASS, zero failures. The EB and XS wiring tests are in the list because they AST-parse the same `broker.py` — a syntax slip in the added function takes all three down, not just HX's.

- [ ] **Step 9: Commit**

```bash
git add backend/broker.py scripts/check_deployed_code.py backend/tests/test_strategy_hx_broker_wiring.py && git commit -m "feat(strategy-hx): declare the HX universe to the broker fetch loop

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 6: The schema sync script

**Files:**
- Create: `scripts/strategy_hx_sync_schema.py`
- Modify: `backend/tests/test_strategy_hx_run_once.py` (one added test)

**Interfaces:**
- Consumes: `DEFAULTS` from `backend/strategy_hx.py`.
- Produces: a rewritten `# INTELLISTOCK_SCHEMA:` line in `backend/strategies/strategy_hx.py`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_hx_run_once.py`:

```python
def test_the_sync_script_reproduces_the_header_byte_for_byte():
    """The header is what the UI and /strategies/available read. Letting it
    drift from DEFAULTS means an operator configures a key the strategy does
    not have, or misses one it does."""
    import json
    import re
    import subprocess

    root = os.path.dirname(_backend)
    script = os.path.join(root, "scripts", "strategy_hx_sync_schema.py")
    assert os.path.exists(script)
    path = os.path.join(_backend, "strategies", "strategy_hx.py")
    before = open(path).read()
    result = subprocess.run([sys.executable, script], cwd=root,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    after = open(path).read()
    assert after == before, "the committed header is not what DEFAULTS says"
    schema = json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)",
                                  after).group(1))
    assert schema["config"] == DEFAULTS
    assert schema["execution_position"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx_run_once.py -q -k sync`
Expected: FAIL — `AssertionError: assert False` on `os.path.exists(script)`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/strategy_hx_sync_schema.py`:

```python
#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_hx with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift
from `backend/strategy_hx.py:DEFAULTS` means an operator configures a key the
strategy does not have, or misses one it does.

The two single-position keys are BROKER-side — read by
`engines/backtest_engine._instance_single_position_pct`, not by the strategy
module — but they live in HX's DEFAULTS so the header is a plain copy. The
assertion below is what keeps that true if someone ever moves them out: a 65%
core cannot be built under the broker's 15% failsafe, and the failure is a
silent trim to $0.00 rather than an error.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_hx import DEFAULTS  # noqa: E402

_BROKER_SIDE_KEYS = ("broker_max_single_position_pct",
                     "honour_single_position_cap")

path = pathlib.Path("backend/strategies/strategy_hx.py")
source = path.read_text()
match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
schema = json.loads(match.group(1))
config = dict(DEFAULTS)
missing = [k for k in _BROKER_SIDE_KEYS if k not in config]
if missing:
    raise SystemExit(
        "strategy_hx DEFAULTS is missing broker-side key(s) "
        + ", ".join(missing)
        + ": the single-position cap would silently stay at the 15% failsafe "
          "and every levered buy would be trimmed to $0.00.")
schema["config"] = config
schema["execution_position"] = 10
path.write_text(source.replace(match.group(0),
                               "# INTELLISTOCK_SCHEMA: " + json.dumps(schema)))
print(f"schema synced from DEFAULTS: {len(config)} config keys")
```

- [ ] **Step 4: Run the script, then the test**

Run: `python3 scripts/strategy_hx_sync_schema.py && cd backend && python3 -m pytest tests/test_strategy_hx_run_once.py -q`
Expected: `schema synced from DEFAULTS: 26 config keys`, then PASS — 11 passed. If `git diff backend/strategies/strategy_hx.py` is non-empty after the sync, the hand-written header was wrong; keep the synced version.

- [ ] **Step 5: Commit**

```bash
git add scripts/strategy_hx_sync_schema.py backend/strategies/strategy_hx.py backend/tests/test_strategy_hx_run_once.py && git commit -m "feat(strategy-hx): sync the schema header from DEFAULTS

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 7: Track `scripts/_api.py` and build the lab

**Files:**
- Create: `scripts/_api.py` — a verbatim copy of `.claude/worktrees/main-session/scripts/_api.py`, which is untracked and lives only in that worktree.
- Create: `scripts/hx_lab_setup.py`
- Create: `backend/tests/test_hx_lab_setup.py`

**Interfaces:**
- Consumes: `_http`, `_load_dotenv`, `_login` from `scripts/pull_backtest_logs.py` (tracked, unchanged); `DEFAULTS`, `strategy_hx_universe` from `backend/strategy_hx.py`.
- Produces: `call(method: str, path: str, body=None, *, retries: int = 4) -> tuple[int, object]`; `lane(cfg) -> dict`, `doc_payload(cfg) -> dict`, `main() -> int` in `hx_lab_setup.py`.

- [ ] **Step 1: Copy `_api.py` into the main tree**

It is not tracked anywhere. Copy it verbatim — do not retype it, and do not edit the `_PRIMARY` path (in the main tree `_REPO == _PRIMARY`, so `_load_dotenv` simply runs twice over the same file).

```bash
cp .claude/worktrees/main-session/scripts/_api.py scripts/_api.py && git add scripts/_api.py && git diff --cached --stat scripts/_api.py
```

Credentials come from `INTELLISTOCK_API_TOKEN`, or `DEFAULT_ADMIN_USERNAME`/`DEFAULT_ADMIN_PASSWORD` in `.env`. Never echo any of them.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_hx_lab_setup.py`:

```python
"""Transport-only tests for the HX lab setup. No network, no engine."""
import importlib.util
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_hx import DEFAULTS  # noqa: E402


def _module(name):
    """Load a scripts/ module without importing scripts._api, which reads .env
    and would try to log in."""
    path = os.path.join(_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_hx_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_lane_is_a_run_once_lane_carrying_enabled_defaults():
    setup = _module("hx_lab_setup")
    lane = setup.lane(DEFAULTS)
    assert lane["strategy"] == "strategy_hx"
    assert lane["execution_scope"] == "run_once"
    assert lane["decision_phase"] == "pre"
    assert lane["execution_position"] == 10
    assert lane["weight"] == 1.0
    assert lane["conditions"] == {}
    assert lane["config"]["strategy_hx_enabled"] is True
    assert lane["config"]["core_symbol"] == "TQQQ"
    # Every other key is DEFAULTS untouched.
    assert set(lane["config"]) == set(DEFAULTS)


def test_the_lab_stocks_are_the_universe_plus_the_benchmark():
    setup = _module("hx_lab_setup")
    assert setup.STOCKS == ["BIL", "PSQ", "QQQ", "SPY", "TQQQ"]


def test_a_protected_doc_id_is_refused_outright():
    """Doc 200 is the live champion and 201 is the EB lab. A killed runner has
    already left doc 201 holding a candidate's config once; this script must
    never be the thing that writes to either."""
    setup = _module("hx_lab_setup")
    for doc_id in (200, "200", 201, "201"):
        try:
            setup.assert_writable(doc_id)
        except SystemExit as error:
            assert "200" in str(error) or "201" in str(error)
        else:
            raise AssertionError(f"{doc_id!r} was not refused")
    setup.assert_writable(444)


def test_an_existing_doc_is_updated_in_place_rather_than_duplicated():
    setup = _module("hx_lab_setup")
    seen = []

    def fake_call(method, path, body=None, **kwargs):
        seen.append((method, path))
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": [{"id": 444,
                                         "name": setup.DOC_NAME}]}
        if method == "PUT":
            return 200, {"id": 444}
        if path == f"/instances/{setup.INSTANCE_ID}":
            return 200, {"id": setup.INSTANCE_ID, "strategy_id": 444,
                         "granularity_time_increment": 86400}
        if path == "/instances/strategy-eb":
            return 200, {"brokerage_id": "brk-1"}
        return 200, {}

    assert setup.main(call=fake_call) == 0
    assert ("PUT", "/strategies/444") in seen
    assert not any(m == "POST" and p == "/strategies" for m, p in seen)


def test_a_missing_instance_is_cloned_from_the_eb_brokerage():
    setup = _module("hx_lab_setup")
    posted = {}

    def fake_call(method, path, body=None, **kwargs):
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": []}
        if (method, path) == ("POST", "/strategies"):
            return 200, {"id": 555}
        if path == f"/instances/{setup.INSTANCE_ID}" and method == "GET":
            if not posted:
                raise SystemExit("HTTP 404 on GET /instances/strategy-hx-lab")
            return 200, dict(posted["body"])
        if path == "/instances/strategy-eb":
            return 200, {"brokerage_id": "brk-1"}
        if (method, path) == ("POST", "/instances"):
            posted["body"] = body
            return 200, body
        return 200, {}

    assert setup.main(call=fake_call) == 0
    assert posted["body"]["brokerage_id"] == "brk-1"
    assert posted["body"]["granularity_time_increment"] == 86400
    assert posted["body"]["strategy_id"] == 555
    assert posted["body"]["stocks"] == setup.STOCKS
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_hx_lab_setup.py -q`
Expected: FAIL — `FileNotFoundError: ... scripts/hx_lab_setup.py`

- [ ] **Step 4: Write minimal implementation**

Create `scripts/hx_lab_setup.py`:

```python
#!/usr/bin/env python3
"""Create the Strategy HX lab document and its backtest-only instance.

    python3 scripts/hx_lab_setup.py

Idempotent: re-running finds the existing rows by name/id and PUTs the lane
back to enabled DEFAULTS. Docs 200 (the live champion) and 201 (the EB lab)
are REFUSED outright — a killed runner has already left 201 holding a
candidate's config once, and a control that reproduces a candidate to the
decimal is a contaminated doc.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402

DOC_NAME = "Strategy HX lab"
INSTANCE_ID = "strategy-hx-lab"
CLONE_FROM = "strategy-eb"
PROTECTED_DOC_IDS = frozenset({"200", "201"})
#: The default universe plus the benchmark. Variant bear books add SQQQ, SH
#: and GLD; the controller passes those per POST, and the broker fetch loop
#: adds them from the lane config regardless of this list.
STOCKS = sorted(set(strategy_hx_universe(DEFAULTS)) | {"SPY"})


def assert_writable(doc_id):
    """Refuse doc 200 and doc 201, in every string/int spelling."""
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(
            f"REFUSING to write doc {doc_id}: 200 is the live champion and "
            "201 is the EB lab. HX runs in its own document or not at all.")
    return doc_id


def lane(cfg) -> dict:
    return {"strategy": "strategy_hx", "weight": 1.0,
            "execution_position": 10, "decision_phase": "pre",
            "execution_scope": "run_once", "conditions": {},
            "config": {**cfg, "strategy_hx_enabled": True}}


def doc_payload(cfg) -> dict:
    return {"name": DOC_NAME, "strategies": [lane(cfg)]}


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def main(call=None) -> int:
    if call is None:
        from _api import call as call  # noqa: PLW0127
    payload = doc_payload(DEFAULTS)

    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME),
                    None)
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("lab doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = assert_writable(
            created.get("id") or created.get("strategy_id")
            or created.get("new_id"))
        print("lab doc created:", doc_id)

    code, inst = _safe_get(call, f"/instances/{INSTANCE_ID}")
    if code == 404 or not inst:
        _, broker_row = call("GET", f"/instances/{CLONE_FROM}")
        body = {"id": INSTANCE_ID, "name": "Strategy HX lab (backtest only)",
                "strategy_id": doc_id, "granularity_time_increment": 86400,
                "brokerage_id": broker_row.get("brokerage_id"),
                "stocks": list(STOCKS)}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("PATCH", f"/instances/{INSTANCE_ID}", {"strategy_id": doc_id})
        print("instance exists; strategy_id set to", doc_id)

    for symbol in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks",
                 {"symbol": symbol})
        except BaseException:
            # Already listed. The API 4xxs on a duplicate and _api.call turns
            # that into SystemExit, which is not a failure of this script.
            pass

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps(
        {k: check.get(k) for k in ("id", "strategy_id", "runCommand",
                                   "granularity_time_increment")}))
    return 0


def _safe_get(call, path):
    try:
        return call("GET", path)
    except BaseException as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_hx_lab_setup.py -q`
Expected: PASS — 5 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/_api.py scripts/hx_lab_setup.py backend/tests/test_hx_lab_setup.py && git commit -m "feat(strategy-hx): track the API client and build the lab doc + instance

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 8: The sequential engine controller

**Files:**
- Create: `scripts/hx_run_native_api.py`
- Create: `backend/tests/test_hx_run_native_api.py`

**Interfaces:**
- Consumes: `call(method, path, body=None, *, retries=4)` from `scripts/_api.py`; `DEFAULTS`, `strategy_hx_universe` from `backend/strategy_hx.py`; `DOC_NAME`, `INSTANCE_ID`, `assert_writable` from `scripts/hx_lab_setup.py`.
- Produces: `WINDOWS`, `VARIANTS`, `SPY_BENCHMARK`, `BEAR_ORDER`, `FULL_ORDER`, `engine_busy(call) -> list`, `variant_config(variant) -> dict`, `post_body(variant, start, end) -> dict`, `apply_variant(call, variant) -> str`, `drawdown_of(summary) -> float | None`, `should_stop(call, bid) -> bool`, `archive(call, bid, out_dir) -> dict`, `verdict(variant, tag, summary) -> str`, `run_window(call, variant, tag, *, sleep=time.sleep) -> dict`, `main(argv=None, call=None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_hx_run_native_api.py`:

```python
"""Transport-only tests for the HX engine controller.

No network, no engine, no simulated performance. Every test injects a fake
`call` and asserts on what the controller would have SENT.
"""
import importlib.util
import json
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _module():
    path = os.path.join(_ROOT, "scripts", "hx_run_native_api.py")
    spec = importlib.util.spec_from_file_location("_hx_ctl", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _listing(rows):
    return {"backtests": rows, "total": len(rows), "total_pages": 1}


def test_the_frozen_variants_and_windows_are_what_the_spec_registered():
    """Both tables are preregistered. A silent edit to either turns a
    pass/fail verdict into an unfalsifiable one."""
    ctl = _module()
    assert ctl.VARIANTS == {
        "V1": {"PSQ": 0.60, "BIL": 0.40},
        "V2": {"SQQQ": 0.25, "BIL": 0.75},
        "V3": {"SH": 0.60, "BIL": 0.40},
        "V4": {"GLD": 0.30, "BIL": 0.70},
        "V5": {"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40},
    }
    assert ctl.WINDOWS["rb1"] == ("bear", "2022-01-01", "2022-06-30")
    assert ctl.WINDOWS["rb2"] == ("bear", "2026-02-01", "2026-04-01")
    assert ctl.WINDOWS["rb3"] == ("bear", "2025-02-15", "2025-04-15")
    assert ctl.WINDOWS["cyc"] == ("multi", "2021-11-01", "2026-08-27")
    assert len(ctl.WINDOWS) == 25
    assert ctl.BEAR_ORDER == ("rb3", "rb2", "rb1")


def test_a_running_job_refuses_the_whole_run():
    """One job at a time is a user veto from 2026-09-03, not a preference."""
    ctl = _module()

    def fake_call(method, path, body=None, **kwargs):
        return 200, _listing([{"id": 9, "status": "running"}])

    assert ctl.engine_busy(fake_call) == [9]


def test_the_queue_listing_is_paginated_until_total():
    """A clipped listing reads as an idle engine, which is exactly how two
    containers end up on it at once."""
    ctl = _module()
    pages = {1: [{"id": i, "status": "finished"} for i in range(100)],
             2: [{"id": 100, "status": "queued"}]}
    seen = []

    def fake_call(method, path, body=None, **kwargs):
        seen.append(path)
        page = 2 if "page=2" in path else 1
        return 200, {"backtests": pages[page], "total": 101,
                     "total_pages": 2}

    assert ctl.engine_busy(fake_call) == [100]
    assert any("per_page=100" in p for p in seen)
    assert any("page=2" in p for p in seen)


def test_applying_a_variant_puts_the_bear_book_and_refuses_protected_docs():
    ctl = _module()
    sent = {}

    def fake_call(method, path, body=None, **kwargs):
        if path == f"/instances/{ctl.INSTANCE_ID}":
            return 200, {"strategy_id": 444}
        if path == "/strategies/444" and method == "GET":
            return 200, {"name": ctl.DOC_NAME, "strategies": [
                {"strategy": "strategy_hx",
                 "config": {"bear_book": {"PSQ": 0.6, "BIL": 0.4}}}]}
        if method == "PUT":
            sent["body"] = body
            return 200, {}
        return 200, {}

    assert ctl.apply_variant(fake_call, "V2") == "444"
    assert sent["body"]["strategies"][0]["config"]["bear_book"] == {
        "SQQQ": 0.25, "BIL": 0.75}

    def protected_call(method, path, body=None, **kwargs):
        if path == f"/instances/{ctl.INSTANCE_ID}":
            return 200, {"strategy_id": 201}
        raise AssertionError("must refuse before reading doc 201")

    try:
        ctl.apply_variant(protected_call, "V2")
    except SystemExit as error:
        assert "201" in str(error)
    else:
        raise AssertionError("doc 201 was not refused")


def test_the_post_body_is_the_frozen_engine_contract():
    ctl = _module()
    body = ctl.post_body("V5", "2025-02-15", "2025-04-15")
    assert body["instance_id"] == "strategy-hx-lab"
    assert body["granularity"] == "86400"
    assert body["initial_cash"] == 6000
    assert body["equity_cost_tiers"] == "etf-liquid"
    assert body["evidence_mode"] == "off"
    # V5's GLD leg must be in the stocks list or it has no bars.
    assert body["stocks"] == ["BIL", "GLD", "PSQ", "QQQ", "SPY", "TQQQ"]


def test_a_twenty_five_percent_drawdown_stops_the_job():
    ctl = _module()
    calls = []

    def fake_call(method, path, body=None, **kwargs):
        calls.append((method, path))
        if path.endswith("/status"):
            return 200, {"status": "running"}
        if path.endswith("/summary"):
            return 200, {"risk_metrics": {"max_drawdown_pct": 0.2512,
                                          "observation_count": 40}}
        return 200, {}

    assert ctl.should_stop(fake_call, "bt1") is True
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": 0.19}}) == 0.19
    assert ctl.drawdown_of({"risk_metrics": {}}) is None
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": True}}) is None
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": 4.0}}) is None


def test_the_archive_drops_the_equity_curve_and_writes_three_files(tmp_path):
    """A cycle summary carries thousands of NAV points. Keeping them turns a
    verdict file into a megabyte nobody reads."""
    ctl = _module()

    def fake_call(method, path, body=None, **kwargs):
        if path.endswith("/summary"):
            return 200, {"equity_curve": [1, 2, 3], "pnl_percent": 4.2,
                         "risk_metrics": {"max_drawdown_pct": 0.11}}
        if path.endswith("/logs"):
            return 200, {"logs": ["line"]}
        return 200, {"nodes": []}

    out = tmp_path / "V1-rb3"
    summary = ctl.archive(fake_call, "bt9", str(out))
    assert "equity_curve" not in summary
    assert summary["pnl_percent"] == 4.2
    for name in ("summary.json", "logs.json", "graph-data.json"):
        assert (out / name).exists()
    saved = json.loads((out / "summary.json").read_text())
    assert "equity_curve" not in saved


def test_the_verdict_line_names_the_spy_benchmark_or_says_it_is_unknown():
    ctl = _module()
    line = ctl.verdict("V1", "rb1", {"pnl_percent": 3.1,
                                     "risk_metrics": {"max_drawdown_pct": .09}})
    assert "rb1" in line and "-19.40" in line and "+3.10" in line
    assert "SPY n/a" in ctl.verdict("V1", "h3", {"pnl_percent": 1.0,
                                                 "risk_metrics": {}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_hx_run_native_api.py -q`
Expected: FAIL — `FileNotFoundError: ... scripts/hx_run_native_api.py`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/hx_run_native_api.py`:

```python
#!/usr/bin/env python3
"""Sequential API controller for the Strategy HX battery.

    python3 scripts/hx_run_native_api.py --variant V1 --window rb3
    python3 scripts/hx_run_native_api.py --variant V1 --window all-bear
    python3 scripts/hx_run_native_api.py --variant V1 --window all

STRICTLY SEQUENTIAL: refuse if any job is on the engine, post ONE window, poll
until it is terminal, archive it, print a verdict. Parallel posts were vetoed
by the operator on 2026-09-03. There is no simulation and no strategy
execution here — every number comes back from the engine.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from hx_lab_setup import DOC_NAME, INSTANCE_ID, assert_writable  # noqa: E402
from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402

#: Copied verbatim from scripts/outlier_engine_test.py:33-46 so HX is judged
#: on the SAME windows every other candidate on this engine was judged on.
_REGIME_WINDOWS = [
    ("bear", "rb1", "2022-01-01", "2022-06-30"), ("bear", "rb2", "2026-02-01", "2026-04-01"),
    ("bear", "rb3", "2025-02-15", "2025-04-15"),
    ("bull", "ru1", "2023-01-01", "2023-07-31"), ("bull", "ru2", "2026-04-01", "2026-06-01"),
    ("bull", "ru3", "2024-01-01", "2024-06-30"), ("bull", "p21bull", "2021-01-01", "2021-10-31"),
    ("bull", "nu1", "2023-10-25", "2024-03-28"), ("bull", "nu2", "2024-08-06", "2024-12-31"),
    ("chop", "rc1", "2025-11-10", "2026-02-24"), ("chop", "rc2", "2022-07-01", "2022-12-31"),
    ("chop", "rc3", "2024-07-01", "2024-10-31"), ("chop", "nc1", "2023-02-01", "2023-06-15"),
    ("chop", "nc2", "2024-03-15", "2024-08-30"),
    ("handoff", "h1", "2021-11-01", "2021-12-31"), ("handoff", "h2", "2023-08-01", "2023-10-31"),
    ("handoff", "h3", "2025-05-01", "2025-10-31"), ("handoff", "h4", "2024-11-01", "2025-02-14"),
    ("handoff", "h5", "2026-06-01", "2026-08-27"),
    ("year", "y22", "2022-01-01", "2022-12-31"), ("year", "y23", "2023-01-01", "2023-12-31"),
    ("year", "y24", "2024-01-01", "2024-12-31"), ("year", "y25", "2025-01-01", "2025-12-31"),
    ("multi", "ny2", "2023-01-01", "2024-12-31"), ("multi", "cyc", "2021-11-01", "2026-08-27")]
WINDOWS = {tag: (regime, start, end)
           for regime, tag, start, end in _REGIME_WINDOWS}

#: The five preregistered bear books, in run order. Config changes only.
VARIANTS = {
    "V1": {"PSQ": 0.60, "BIL": 0.40},
    "V2": {"SQQQ": 0.25, "BIL": 0.75},
    "V3": {"SH": 0.60, "BIL": 0.40},
    "V4": {"GLD": 0.30, "BIL": 0.70},
    "V5": {"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40},
}

#: SPY total return per window, from the spec's gate table. Windows with no
#: recorded number print "SPY n/a" rather than a guess.
SPY_BENCHMARK = {"rb1": -19.40, "rb2": -5.84, "rb3": -11.40,
                 "rc1": 2.08, "rc2": 2.24, "rc3": 7.03, "cyc": 77.11}

#: The order the spec freezes: bear first (cheapest kill), then chop, bull,
#: cycle.
BEAR_ORDER = ("rb3", "rb2", "rb1")
FULL_ORDER = BEAR_ORDER + ("rc1", "rc2", "rc3", "ru1", "ru3", "cyc")

#: Same starting cash as every prior EB run on this engine
#: (.claude/worktrees/main-session/output/research/bear-slow-full-2026-09-08/
#: NATIVE-PREREGISTRATION.json: "initial_cash": 6000). Hard-coded so a run is
#: comparable to the bil25 card without depending on an untracked worktree.
INITIAL_CASH = 6000
DRAWDOWN_REJECT = 0.25
POLL_SECONDS = 20
POLL_LIMIT = 360  # 2 hours at 20s
OUT_ROOT = os.path.join(_ROOT, "output", "research", "hx-2026-09-10")
_BUSY = ("running", "pending", "queued", "paused")


def engine_busy(call) -> list:
    """Ids of jobs occupying the engine, paginated to the full total.

    A clipped listing reads as an idle engine, which is exactly how two
    containers end up on it at once.
    """
    _, page = call("GET", "/backtests?per_page=100")
    rows = list(page.get("backtests") or [])
    for number in range(2, int(page.get("total_pages") or 1) + 1):
        _, extra = call("GET", f"/backtests?per_page=100&page={number}")
        rows.extend(extra.get("backtests") or [])
    total = page.get("total")
    if isinstance(total, int) and len(rows) < total:
        raise SystemExit(
            f"queue listing truncated: {len(rows)} of {total}. Refusing to "
            "post: an unseen row may be a running job.")
    return [r.get("id") for r in rows if r.get("status") in _BUSY]


def variant_config(variant) -> dict:
    return {**DEFAULTS, "strategy_hx_enabled": True,
            "bear_book": dict(VARIANTS[variant])}


def post_body(variant, start, end) -> dict:
    cfg = variant_config(variant)
    return {"instance_id": INSTANCE_ID,
            "stocks": sorted(set(strategy_hx_universe(cfg)) | {"SPY"}),
            "start_date": start, "end_date": end,
            "granularity": "86400", "initial_cash": INITIAL_CASH,
            "equity_cost_tiers": "etf-liquid", "evidence_mode": "off"}


def apply_variant(call, variant) -> str:
    """PUT the variant's bear book onto the lab doc. Returns the doc id."""
    _, inst = call("GET", f"/instances/{INSTANCE_ID}")
    doc_id = assert_writable(inst["strategy_id"])
    _, doc = call("GET", f"/strategies/{doc_id}")
    if doc.get("name") != DOC_NAME:
        raise SystemExit(
            f"doc {doc_id} is named {doc.get('name')!r}, not {DOC_NAME!r}. "
            "Refusing to write a document this script did not create.")
    lanes = [dict(lane) for lane in (doc.get("strategies") or [])]
    hx = [lane for lane in lanes if lane.get("strategy") == "strategy_hx"]
    if len(hx) != 1:
        raise SystemExit(f"expected exactly one strategy_hx lane, saw {len(hx)}")
    hx[0]["config"] = variant_config(variant)
    call("PUT", f"/strategies/{doc_id}", {"name": doc["name"],
                                          "strategies": lanes})
    return str(doc_id)


def drawdown_of(summary):
    """The engine's own drawdown metric as a fraction, or None.

    Never computed here. A number outside [0, 1], a bool, or a missing key is
    None, and the caller treats None as "not yet measurable" rather than as
    "safe".
    """
    metrics = summary.get("risk_metrics") if isinstance(summary, dict) else None
    if not isinstance(metrics, dict):
        return None
    raw = metrics.get("max_drawdown_pct")
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def should_stop(call, bid) -> bool:
    _, summary = call("GET", f"/backtests/{bid}/summary")
    drawdown = drawdown_of(summary)
    return drawdown is not None and drawdown >= DRAWDOWN_REJECT


def archive(call, bid, out_dir) -> dict:
    """Save summary (scalars only), full logs and graph-data. Returns summary."""
    os.makedirs(out_dir, exist_ok=True)
    _, summary = call("GET", f"/backtests/{bid}/summary")
    scalars = {k: v for k, v in (summary or {}).items()
               if k not in ("equity_curve", "portfolio_values", "nav_series")}
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(scalars, fh, indent=1, default=str)
    for endpoint, name in (("logs", "logs.json"),
                           ("graph-data", "graph-data.json")):
        _, body = call("GET", f"/backtests/{bid}/{endpoint}")
        with open(os.path.join(out_dir, name), "w") as fh:
            json.dump(body, fh, default=str)
    return scalars


def verdict(variant, tag, summary) -> str:
    try:
        ret = float(summary.get("pnl_percent"))
    except (TypeError, ValueError):
        ret = float("nan")
    drawdown = drawdown_of(summary)
    spy = SPY_BENCHMARK.get(tag)
    spy_text = f"SPY {spy:+.2f}%" if spy is not None else "SPY n/a"
    delta = f" | delta {ret - spy:+.2f}" if spy is not None else ""
    dd_text = f"{drawdown * 100:.1f}%" if drawdown is not None else "n/a"
    return (f"{variant} {tag} [{WINDOWS[tag][0]}] {ret:+.2f}% vs {spy_text}"
            f"{delta} | maxDD {dd_text}")


def run_window(call, variant, tag, *, sleep=time.sleep) -> dict:
    busy = engine_busy(call)
    if busy:
        raise SystemExit(f"engine occupied: {busy}. One job at a time.")
    regime, start, end = WINDOWS[tag]
    apply_variant(call, variant)
    body = post_body(variant, start, end)
    out_dir = os.path.join(OUT_ROOT, f"{variant}-{tag}")
    with open(os.path.join(_ensure(out_dir), "request.json"), "w") as fh:
        json.dump(body, fh, indent=1)
    # An uncertain POST is never retried: a duplicated job would put two
    # containers on the engine, which is exactly what the veto forbids.
    _, accepted = call("POST", "/backtests", body, retries=1)
    bid = accepted["id"]
    print(f"POSTED {variant} {tag} {bid}", flush=True)

    stopped = False
    for _ in range(POLL_LIMIT):
        _, state = call("GET", f"/backtests/{bid}/status")
        status = state.get("status")
        if status in ("finished", "completed", "error", "stopped", "failed"):
            summary = archive(call, bid, out_dir)
            print(f"ARCHIVED {variant} {tag} {bid} {status}", flush=True)
            print(verdict(variant, tag, summary), flush=True)
            return {"id": bid, "status": status, "summary": summary,
                    "stopped": stopped}
        if not stopped and should_stop(call, bid):
            print(f"RISK STOP {variant} {tag} {bid}: drawdown >= "
                  f"{DRAWDOWN_REJECT:.0%}", flush=True)
            call("POST", f"/backtests/{bid}/stop", {}, retries=1)
            stopped = True
        sleep(POLL_SECONDS)
    raise SystemExit(f"job still unresolved after "
                     f"{POLL_LIMIT * POLL_SECONDS}s: {bid}")


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def main(argv=None, call=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    parser.add_argument("--window", required=True)
    args = parser.parse_args(argv)
    if args.window == "all-bear":
        tags = list(BEAR_ORDER)
    elif args.window == "all":
        tags = list(FULL_ORDER)
    elif args.window in WINDOWS:
        tags = [args.window]
    else:
        raise SystemExit(f"unknown window {args.window!r}; "
                         f"choose one of {sorted(WINDOWS)}, all-bear, all")
    if call is None:
        from _api import call as call  # noqa: PLW0127
    for tag in tags:
        result = run_window(call, args.variant, tag)
        try:
            ret = float(result["summary"].get("pnl_percent"))
        except (TypeError, ValueError):
            ret = float("nan")
        if WINDOWS[tag][0] == "bear" and not ret > 0:
            print(f"BEAR GATE FAILED: {args.variant} {tag} {ret:+.2f}%. "
                  "Variant ends here.", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_hx_run_native_api.py -q`
Expected: PASS — 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/hx_run_native_api.py backend/tests/test_hx_run_native_api.py && git commit -m "feat(strategy-hx): sequential engine controller with the frozen gate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

---

### Task 9: Review, merge, push, and prove the deploy

No new code. Nothing here is optional — a run started on a stale image costs a paid backtest and produces a number that means nothing.

**Files:** none created. Possible fixes land in the files from Tasks 1-8.

- [ ] **Step 1: Run the whole HX set plus the neighbours**

Run: `cd backend && python3 -m pytest tests/test_strategy_hx.py tests/test_strategy_hx_run_once.py tests/test_strategy_hx_broker_wiring.py tests/test_hx_lab_setup.py tests/test_hx_run_native_api.py tests/test_strategy_eb.py tests/test_strategy_eb_run_once.py tests/test_strategy_eb_broker_wiring.py tests/test_strategy_xs.py tests/test_strategy_xs_run_once.py tests/test_strategy_xs_broker_wiring.py -q`
Expected: PASS, zero failures. HX imports `eb_core_weight` and `targets_to_orders`; if an EB or X test moved, the import contract moved with it.

- [ ] **Step 2: Check the affected scope**

Run `mcp__gitnexus__detect_changes()`. Expected: the eight new files plus `backend/broker.py` and `scripts/check_deployed_code.py`. Anything else in the list is an accidental edit — revert it before continuing.

- [ ] **Step 3: Review the diff with four ECC agents, in parallel**

Launch all four in one message against `git diff main...feat/strategy-hx`:
`ecc:python-reviewer`, `ecc:silent-failure-hunter`, `ecc:security-reviewer`, `ecc:code-reviewer`.

Fix every CRITICAL and HIGH finding and commit each fix separately with the two trailer lines. Record advisory findings in the task notes; do not silently drop them. The silent-failure hunter is the one that matters most here — the entire failure mode of this subsystem is a strategy that emits nothing and reports success.

- [ ] **Step 4: Confirm the engine is idle and the clock is clear**

Run: `python3 scripts/_api.py GET /backtests | python3 -c "import json,sys; rows=json.load(sys.stdin)['backtests']; print([r['id'] for r in rows if r['status'] in ('running','pending','queued','paused')])"`
Expected: `[]`. Also confirm the current time is not within 30 minutes of 08:30 CDT. If either check fails, wait; do not push.

- [ ] **Step 5: Merge and push**

```bash
git checkout main && git merge --no-ff feat/strategy-hx -m "feat: Strategy HX — fast-in, slow-out bear hedge

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey" && git push origin main
```

- [ ] **Step 6: Prove the deployed image carries this commit**

The backend auto-deploys from main. Wait 5 minutes, then run `python3 scripts/check_deployed_code.py` and repeat every 60 seconds until it exits 0. Do not post a backtest before it does. This is the check that exists because two pushes were measured on code that was never live.

---

### Task 10: Run the battery and record the verdict

No new code. Every number comes from the engine; local harness numbers never count.

**Files:**
- Create: `docs/superpowers/research/2026-09-10-strategy-hx-results.md`

- [ ] **Step 1: Build the lab**

Run: `python3 scripts/hx_lab_setup.py`
Expected: a doc id that is neither 200 nor 201, and `granularity_time_increment: 86400` on `strategy-hx-lab`. If the printed `runCommand` is truthy, stop — the lab instance must be backtest-only.

- [ ] **Step 2: V1 through the bear gate, in spec order**

Run: `python3 scripts/hx_run_native_api.py --variant V1 --window all-bear`
This runs rb3, then rb2, then rb1, one at a time. A negative bear window prints `BEAR GATE FAILED` and exits 1 — that ends the variant. Do not re-run it, do not tune it, move to V2.

- [ ] **Step 3: Repeat for V2..V5 until one passes all three bears**

Run each in turn, only after the previous one has failed:
`python3 scripts/hx_run_native_api.py --variant V2 --window all-bear`, then `V3`, `V4`, `V5`.

- [ ] **Step 4: The passing variant takes the rest of the battery**

For the first variant that clears all three bears, run each window on its own so a stop is cheap:
`python3 scripts/hx_run_native_api.py --variant <V> --window rc1`, then `rc2`, `rc3`, `ru1`, `ru3`, `cyc`.

- [ ] **Step 5: Record the verdict table**

Create `docs/superpowers/research/2026-09-10-strategy-hx-results.md` with one row per run: variant, window, regime, backtest id, span, HX return, SPY return (from `SPY_BENCHMARK`, or "n/a"), delta, HX max drawdown, SPY max drawdown, and PASS/FAIL against each of the four frozen gates. Cite the archived paths under `output/research/hx-2026-09-10/<variant>-<window>/`. Commit it:

```bash
git add docs/superpowers/research/2026-09-10-strategy-hx-results.md && git commit -m "docs: Strategy HX engine results

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VD1xjNAiRSzFdpL4kMidey"
```

- [ ] **Step 6: If all five variants fail the bear gate**

Write, as the last line of the results document: **HX killed; build the three-state fixed-book classifier per the spec's Fallback section.** Do NOT start it. The fallback is pre-registered so that the second attempt is a decision the operator makes with the first attempt's numbers in front of them, not a reflex.

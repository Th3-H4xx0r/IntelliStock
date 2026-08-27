# Strategy EB ("Efficient Beta") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a vol-targeted leveraged-Nasdaq core with a SPY/BIL remainder — a
weekly-cadence, banded, quantized risk transform — end to end: pure sizing
module, broker wrapper, broker wiring, an honest ETF execution-cost tier, a live
daily-bar carrier, per-document live risk limits, a local harness, and the
frozen acceptance gate.

**Architecture:** The same two-file split every strategy in this repo uses. All
testable math lives in the pure module `backend/strategy_eb.py` (no clock, no
RNG, no I/O) because `broker.py` argparses at module scope and SystemExits under
pytest. The wrapper `backend/strategies/strategy_eb.py` owns only what needs the
broker — the emulator, the strategy cache, order emission — and reuses Strategy
X's `pit_daily_observations` and `targets_to_orders` rather than re-deriving
them. Four engine-side changes make the measurement honest: a symbol-tiered
execution cost model, a generalised single-position-cap opt-in, a live equity
bar carrier, and per-strategy-document live risk limits.

**Tech Stack:** Python 3.11, pytest, PostgreSQL 17 + JSONB via `backend/db/`,
FastAPI (`backend/api/main.py`), yfinance + pandas + numpy (local harness only).

**Spec:** `docs/superpowers/specs/2026-08-27-strategy-eb-design.md`
**Research:** `docs/superpowers/research/2026-08-27-all-regime-research.md`

---

## Global Constraints

Copied from the spec. Every task's requirements implicitly include this section.

- **Class name is a contract.** The class must be exactly `StrategyEb`.
  `broker.py:_strategy_name_to_module_and_class` CamelCases the strategy id
  (`strategy_eb` → `StrategyEb`), identical to
  `strategies_meta._module_to_class_name`. Strategy XS shipped once as
  `StrategyXS` and BT634331 ran 1,259 sessions completely inert.
- **`broker.py` is not import-safe.** It argparses at module scope and
  SystemExits under pytest. Tests AST-extract broker functions into a stub
  namespace (`backend/tests/test_strategy_xs_broker_wiring.py` is the model).
  Anything testable must live in a pure module.
- **Tests put ONLY `backend/` on `sys.path`.** Adding `backend/strategies/` too
  makes `strategy_x` resolve to the WRAPPER rather than the pure module — they
  share a name and the wrapper imports the pure one, so it self-imports.
- **Pytest command:** `PYTHONPATH=.:backend python3 -m pytest -q <files>`.
- **Sizing must be published** via `_nexus_position_sizes`. A bare `1` is sized
  by the broker's default `cash_per_trade` (~$1,000): `index_core_tilt` asked
  for $6,000 of SPY and received $900.
- **Every sell needs `_nexus_action_intents = {sym: "etf_sell"}`.** `broker.py`'s
  Z2.1 check (broker.py:17467-17484) whitelists `etf_sell`; a sell with no
  recognised intent logs `would_block_in_phase2=True`. On BT406990 that was
  965 of 965 sells.
- **`data` is `None` in live today.** Both Strategy X and XS refuse to trade
  live for this reason. Task 6 fixes it for the equity lane.
- **`evaluate_drawdown` re-derives the exposure caps from module constants on
  every refresh** (live_risk_state.py:432-439). An override applied only at
  `initialize_risk_state` is overwritten each tick.
- **`equity_total_cost_bps` accepts only 25 or 50** (`COST_SCENARIO_TARGETS_BPS`,
  backtest_evidence_options.py:50). It can only stress *up*. That is why the
  ETF tier is a separate option, not a value of this one.
- **`assert_execution_provenance_promotable` requires every fill's
  `cost_model_version` to equal the summary's `execution_cost_model_version`**
  (backtest_summary.py:131-134). A tiered model therefore stamps ONE composite
  version on every fill, never the per-symbol tier's version.
- **`store.update()` deep-merges; `store.between(lo, hi)` is `[lo, hi)`.** No
  module outside `backend/db/` opens a connection.
- **The single-position-cap env var is process-wide inside the container.** The
  EB strategy document must contain no other enabled lane.
- **No re-tuning to pass.** If the frozen gate in section 11 of the spec fails,
  the strategy ships disabled with the numbers recorded in `DEFAULTS` comments,
  per the XS precedent.

### Two deliberate deviations from the spec, with reasons

1. **§6 says the tier preset is "threaded to `create_backtest_emulator(...,
   cost_model_tiers=)`".** This plan instead resolves the tiered model in
   `backtest_evidence_options.resolve_execution_cost_tiers()` next to
   `resolve_execution_cost_model()`, and passes the resulting single object
   through the existing `cost_model=` parameter. Reason: broker.py hashes
   `_evidence_cost_model` into the experiment preregistration
   (broker.py:10013) while the emulator does the filling. Passing a preset id
   down a second parameter would let the receipt hash a flat model whose fills
   were tiered — the exact provenance defect
   `create_backtest_emulator`'s docstring and
   `LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL`'s comment were written to prevent.
   One immutable cost object per run is preserved.
2. **§5.4 gives `build_live_equity_data(symbols, api_key, api_secret, db_conn,
   feed, lookback_days=400)`.** This plan uses an injected `fetch_bars`
   callable instead of credentials, exactly like
   `backend/live_crypto_bars.py:build_live_crypto_data`. Reason: taking
   credentials means importing `fetch_alpaca_historical_bars` from `broker.py`,
   which cannot be imported under pytest. The broker closes over the
   credentials at the call site, as it already does for crypto
   (broker.py:14148-14167).

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/strategy_eb.py` | Pure sizing: `DEFAULTS`, parsers, `eb_core_weight`, `eb_targets`, `strategy_eb_universe`, `eb_should_trade`. No clock, no I/O. |
| `backend/strategies/strategy_eb.py` | Wrapper `StrategyEb`: schema header, emulator/cache/order emission, `_nexus_*` channels. |
| `backend/live_equity_bars.py` | Live daily-bar carrier for equity run_once strategies. Import-safe. |
| `scripts/strategy_eb_sync_schema.py` | Regenerates the wrapper's `INTELLISTOCK_SCHEMA` header from `DEFAULTS`. |
| `scripts/strategy_eb_matrix.py` | Local yfinance harness, 2010-2026. Never the verdict. |
| `scripts/strategy_eb_bootstrap.py` | Creates the strategy doc + instance through the API and verifies the round-trip. |
| `scripts/strategy_eb_gate.py` | Evaluates the frozen gate G1-G6 against a finished backtest. |
| `backend/tests/test_strategy_eb.py` | Pure math. |
| `backend/tests/test_strategy_eb_run_once.py` | Wrapper contract + schema header + class name. |
| `backend/tests/test_strategy_eb_broker_wiring.py` | Both wiring points. |
| `backend/tests/test_tiered_cost_model.py` | Tier routing, composite version, byte-identity, validator. |
| `backend/tests/test_live_equity_bars.py` | Last-good fallback and total failure. |
| `backend/tests/test_live_risk_limits.py` | Per-document risk limits across refresh. |
| `backend/tests/test_backtest_engine_single_position_cap.py` | `honour_single_position_cap`. |

**Modified**

| File | Change |
|---|---|
| `backend/broker.py` | `_strategy_eb_universe_symbols`, fetch site, price site, live bar hook, risk limits, leveraged-set de-inlining. |
| `backend/simulated_execution.py` | `TieredExecutionCostModel`, `ETF_LIQUID_*`, per-symbol reads in `NextEventExecutionSimulator`. |
| `backend/portfolio_emulator.py` | `_equity_fill(symbol=)`, `_equity_model_for`, per-symbol quote spreads, tiered `create_backtest_emulator`. |
| `backend/backtest_evidence_options.py` | `equity_cost_tiers` option + `resolve_execution_cost_tiers`. |
| `backend/api/main.py` | `equity_cost_tiers` request field + pass-through. |
| `backend/engines/backtest_engine.py` | `honour_single_position_cap` in `_instance_single_position_pct`. |
| `backend/live_risk_state.py` | `RiskLimits`, threading, `QLD` in the leveraged set. |

---

## Task 1: Pure sizing module `backend/strategy_eb.py`

**Files:**
- Create: `backend/strategy_eb.py`
- Test: `backend/tests/test_strategy_eb.py`

**Interfaces:**
- Consumes: `strategy_x.Q` (= 6), `strategy_x._finite(values) -> list|None`,
  `strategy_x._stdev(xs) -> float`. All three exist in `backend/strategy_x.py`
  (`Q` at :89, `_finite` at :473, `_stdev` at :493).
- Produces, all importable as `from strategy_eb import ...`:
  - `DEFAULTS: dict`
  - `_f(cfg, key, default=None) -> float`, `_i(cfg, key, default=None) -> int`,
    `_s(cfg, key, default=None) -> str` (upper-cased, stripped)
  - `rebalance_weekdays(cfg) -> tuple[int, ...]` (Monday=0)
  - `session_ordinal(session_id) -> int` (days since 1970-01-01, 0 when unusable)
  - `session_weekday(session_id) -> int` (Monday=0, `-1` when unusable)
  - `eb_core_weight(closes, cfg) -> float | None` (`None` = fail closed)
  - `eb_targets(w, cfg) -> dict[str, float]`
  - `strategy_eb_universe(cfg) -> list[str]`
  - `eb_should_trade(session_id, w_target, w_held, cfg, cache) -> tuple[bool, float]`
  - `LAST_REBALANCE_KEY = "_eb_last_rebalance_session"`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_strategy_eb.py`:

```python
"""Pure sizing tests for Strategy EB.

Every series here is an EXACT alternating-return construction, so the realised
volatility, and therefore the target weight, is arithmetic rather than a
regression fixture: closes[i+1] = closes[i] * (1 +/- pct) alternating gives a
sample stdev over any EVEN window of exactly pct-ish, and the numbers below were
computed from that construction, not read off an implementation.
"""
import math
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_eb import (  # noqa: E402
    DEFAULTS,
    LAST_REBALANCE_KEY,
    eb_core_weight,
    eb_should_trade,
    eb_targets,
    rebalance_weekdays,
    session_ordinal,
    session_weekday,
    strategy_eb_universe,
)


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    value.update(overrides)
    return value


def alternating(pct, n=101, start=100.0):
    """n closes whose returns alternate exactly +pct, -pct, +pct, ..."""
    out = [start]
    for i in range(n - 1):
        out.append(out[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return out


# ── eb_core_weight: the vol transform ───────────────────────────────────────

def test_a_one_percent_tape_targets_forty_percent_of_a_3x_fund():
    """rv = 0.16287 annualised; 0.20 / (3.0 * 0.16287) = 0.40933, floored to
    the 0.05 grid = 0.40."""
    assert eb_core_weight(alternating(0.01), cfg()) == 0.40


def test_the_same_tape_targets_sixty_percent_of_a_2x_fund():
    """Same rv, leverage 2.0: 0.20 / (2.0 * 0.16287) = 0.61399 -> 0.60."""
    got = eb_core_weight(alternating(0.01), cfg(core_leverage=2.0))
    assert got == 0.60


def test_a_calm_tape_is_clamped_at_core_max_weight():
    """w_raw = 4.09; the clamp, not the grid, is what stops it."""
    assert eb_core_weight(alternating(0.001), cfg()) == 0.65


def test_a_violent_tape_floors_to_zero_rather_than_a_dust_position():
    """w_raw = 0.0409, below one grid step. Flooring means quantization only
    ever holds LESS."""
    assert eb_core_weight(alternating(0.10), cfg()) == 0.0


def test_quantization_always_floors_never_rounds():
    """0.0819 is nearer 0.10 than 0.05; it must still land on 0.05."""
    assert eb_core_weight(alternating(0.05), cfg()) == 0.05


def test_the_slow_window_governs_when_it_is_the_more_dangerous_one():
    """max(stdev20, stdev60), not the fast window alone: a calm last month
    inside a violent quarter must not re-lever. The tail CONTINUES from the
    violent series' last close — concatenating two series that both start at
    100 would inject one enormous seam return into both windows."""
    violent = alternating(0.05, n=81)
    closes = violent + alternating(0.001, n=21, start=violent[-1])[1:]
    calm_only = eb_core_weight(alternating(0.001), cfg())
    assert eb_core_weight(closes, cfg()) < calm_only


def test_too_little_history_fails_closed():
    """A cold start must never silently lever up."""
    assert eb_core_weight(alternating(0.01, n=69), cfg()) is None


def test_exactly_min_history_bars_is_enough():
    assert eb_core_weight(alternating(0.01, n=70), cfg()) is not None


def test_a_nonfinite_close_fails_closed():
    closes = alternating(0.01)
    closes[-3] = float("nan")
    assert eb_core_weight(closes, cfg()) is None


def test_a_flat_tape_has_no_measurable_risk_and_fails_closed():
    """rv == 0 would divide by zero and ask for infinite leverage."""
    assert eb_core_weight([100.0] * 101, cfg()) is None


def test_an_infinite_target_vol_falls_back_to_the_default_rather_than_levering():
    """strategy_x's own `_i` raises OverflowError on inf; EB's parsers fail
    CLOSED, which is the only safe direction for a levered position."""
    assert eb_core_weight(alternating(0.01), cfg(target_vol=float("inf"))) == 0.40


def test_a_zero_leverage_config_fails_closed_instead_of_dividing_by_zero():
    assert eb_core_weight(alternating(0.01), cfg(core_leverage=0)) is None


# ── eb_targets: the remainder dial ──────────────────────────────────────────

def test_the_default_remainder_is_all_spy():
    assert eb_targets(0.40, cfg()) == {"TQQQ": 0.40, "SPY": 0.60}


def test_the_dial_at_one_puts_the_whole_remainder_in_bills():
    assert eb_targets(0.40, cfg(remainder_bil_fraction=1.0)) == {
        "TQQQ": 0.40, "BIL": 0.60}


def test_the_dial_at_a_half_splits_the_remainder():
    got = eb_targets(0.40, cfg(remainder_bil_fraction=0.5))
    assert got == {"TQQQ": 0.40, "SPY": 0.30, "BIL": 0.30}


def test_every_target_set_sums_to_exactly_one():
    for dial in (0.0, 0.25, 0.5, 1.0):
        for w in (0.0, 0.05, 0.4, 0.65):
            got = eb_targets(w, cfg(remainder_bil_fraction=dial))
            assert round(sum(got.values()), 6) == 1.0, (w, dial)


def test_a_zero_core_weight_emits_no_core_leg_at_all():
    got = eb_targets(0.0, cfg())
    assert "TQQQ" not in got
    assert got == {"SPY": 1.0}


def test_colliding_symbols_accumulate_rather_than_overwrite():
    """off_symbol == cash_symbol must not silently drop half the book."""
    got = eb_targets(0.40, cfg(off_symbol="BIL", remainder_bil_fraction=0.5))
    assert got == {"TQQQ": 0.40, "BIL": 0.60}


# ── strategy_eb_universe ────────────────────────────────────────────────────

def test_the_universe_is_reference_core_off_and_cash_in_order():
    assert strategy_eb_universe(cfg()) == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_the_universe_deduplicates():
    assert strategy_eb_universe(cfg(off_symbol="QQQ")) == ["QQQ", "TQQQ", "BIL"]


def test_the_universe_is_freshly_allocated_so_callers_cannot_mutate_defaults():
    first = strategy_eb_universe(cfg())
    first.append("JUNK")
    assert "JUNK" not in strategy_eb_universe(cfg())


# ── session clock ───────────────────────────────────────────────────────────

def test_the_session_ordinal_is_days_since_1970_not_the_proleptic_ordinal():
    """A raw ordinal is ~739,000 and reads as corruption to anything that
    bounds a parsed counter at 100,000."""
    assert session_ordinal("1970-01-01") == 0
    assert session_ordinal("2026-06-03") == 20607


def test_an_unusable_session_label_is_zero_not_an_exception():
    for junk in (None, "", "not-a-date", 20607, "2026-13-45"):
        assert session_ordinal(junk) == 0, junk


def test_the_weekday_comes_from_the_session_date():
    assert session_weekday("2026-06-01") == 0   # Monday
    assert session_weekday("2026-06-03") == 2   # Wednesday
    assert session_weekday("2026-06-05") == 4   # Friday
    assert session_weekday("junk") == -1


def test_rebalance_weekdays_parses_and_bounds():
    assert rebalance_weekdays(cfg()) == (2,)
    assert rebalance_weekdays(cfg(rebalance_weekdays=[1, 3])) == (1, 3)
    assert rebalance_weekdays(cfg(rebalance_weekdays=[3, 1, 3])) == (1, 3)
    for junk in (None, [], "wed", [9], [-1], [None]):
        assert rebalance_weekdays(cfg(rebalance_weekdays=junk)) == (2,), junk


# ── eb_should_trade: cadence, band, tranches, unconditional exit ────────────

WED = "2026-06-03"
THU = "2026-06-04"
MON = "2026-06-01"


def test_it_trades_on_the_configured_weekday_when_the_band_is_breached():
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), {}) == (True, 0.40)


def test_it_does_not_trade_on_any_other_weekday():
    assert eb_should_trade(THU, 0.40, 0.00, cfg(), {}) == (False, 0.00)
    assert eb_should_trade(MON, 0.40, 0.00, cfg(), {}) == (False, 0.00)


def test_a_drift_inside_the_band_does_not_trade_even_on_the_decision_day():
    assert eb_should_trade(WED, 0.40, 0.35, cfg(), {}) == (False, 0.35)


def test_a_drift_exactly_at_the_band_does_trade():
    """`>=`, per the spec: |w - w_held| >= core_rebalance_band."""
    traded, target = eb_should_trade(WED, 0.45, 0.35, cfg(), {})
    assert traded is True and target == 0.45


def test_an_exit_to_zero_is_unconditional_and_ignores_the_weekday():
    """The band is meaningless around a target of zero, and waiting until
    Wednesday to leave a 3x fund is the failure this exists to prevent."""
    assert eb_should_trade(THU, 0.0, 0.30, cfg(), {}) == (True, 0.0)


def test_an_exit_to_zero_from_flat_is_not_an_order():
    assert eb_should_trade(THU, 0.0, 0.0, cfg(), {}) == (False, 0.0)


def test_it_refuses_a_second_trade_in_the_same_session():
    """The engine calls run_once on every tick; at 15m granularity that is ~26
    evaluations per session."""
    cache = {LAST_REBALANCE_KEY: WED}
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), cache) == (False, 0.00)


def test_the_same_session_guard_does_not_leak_into_the_next_session():
    cache = {LAST_REBALANCE_KEY: "2026-05-27"}
    assert eb_should_trade(WED, 0.40, 0.00, cfg(), cache) == (True, 0.40)


def test_two_tranches_move_half_the_way_on_each_listed_weekday():
    """1/N tranching removes rebalance-timing luck, which is >100 bp/yr."""
    two = cfg(rebalance_weekdays=[1, 3])
    assert eb_should_trade("2026-06-02", 0.60, 0.20, two, {}) == (True, 0.40)
    assert eb_should_trade("2026-06-04", 0.60, 0.40, two, {}) == (True, 0.50)


def test_a_tranche_still_exits_the_whole_position_at_a_zero_target():
    two = cfg(rebalance_weekdays=[1, 3])
    assert eb_should_trade("2026-06-02", 0.0, 0.40, two, {}) == (True, 0.0)


def test_an_unusable_session_label_never_trades():
    assert eb_should_trade("junk", 0.40, 0.00, cfg(), {}) == (False, 0.00)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb.py`
Expected: FAIL — collection error, `ModuleNotFoundError: No module named 'strategy_eb'`.

- [ ] **Step 3: Write `backend/strategy_eb.py`**

```python
"""Strategy EB sizing — a vol-targeted levered Nasdaq core with a SPY/BIL tail.

Design: docs/superpowers/specs/2026-08-27-strategy-eb-design.md
Research: docs/superpowers/research/2026-08-27-all-regime-research.md

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is a RISK TRANSFORM, not an alpha. Nine pre-registered signal tests on this
universe returned nine KILLs; the one construction that survived is leverage
efficiency, worth ~+2.6pp CAGR at MATCHED maximum drawdown against a static
levered blend (49 of 52 configurations, same sign in both halves of 2010-2026).
The strategy makes no directional prediction. It holds LESS of the same position
when that position is more dangerous.

Measured locally (yfinance 2010-2026, 4.4 bps on ETF legs, next-bar fills):
    CAGR ~24%, max drawdown ~-40%, one-way turnover ~250-300%/yr.
Every local harness in this repo over-states. The engine is the verdict.

WHAT IT DELIBERATELY DOES NOT CONTAIN, each with measured evidence
------------------------------------------------------------------
  * No MA200 gate           - below the always-long base rate.
  * No inverse leg          - the bottom detector fails (n=77, t -0.95).
  * No binary drawdown halt - fits the one bear in the window.
  * No slow/fast trend gate - a wash: -0.4pp CAGR for +99%/yr turnover.
  * No commodity / managed-futures / gold sleeve - all KILLed.
  * No Graph Nexus sleeve   - zero measured cross-sectional signal, and
                              untestable in any bear in the engine window.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.
"""
from __future__ import annotations

import math

from strategy_x import Q, _finite, _stdev

__all__ = [
    "DEFAULTS", "LAST_REBALANCE_KEY", "eb_core_weight", "eb_should_trade",
    "eb_targets", "rebalance_weekdays", "session_ordinal", "session_weekday",
    "strategy_eb_universe",
]


#: Days since this epoch, not the proleptic ordinal (~739,000 today), so a
#: session counter stays inside the 100,000 bound the bear module applies to
#: every parsed counter. Same constant as strategies/strategy_x.py:284.
_SESSION_EPOCH_ORDINAL = 719163  # date(1970, 1, 1).toordinal()

#: 1970-01-01 was a THURSDAY, i.e. weekday() == 3. So Monday-based weekday is
#: (days_since_epoch + 3) % 7, with no second date parse.
_EPOCH_WEEKDAY = 3

#: Where the wrapper records the session it last traded in, so intraday
#: granularity cannot produce a second rebalance in the same session.
LAST_REBALANCE_KEY = "_eb_last_rebalance_session"

_TRADING_DAYS = 252


DEFAULTS = {
    "strategy_eb_enabled": False,
    # ── the legs ──
    # TQQQ at 3x rather than QLD at 2x: the volatility drag depends on TOTAL
    # exposure m = k*w, not on the fund's multiple, so 40% TQQQ and 60% QLD are
    # the same beta on paper — and the 3x fund reaches it with a third of the
    # capital, leaving the remainder in SPY instead of idle. `core_leverage`
    # must match the fund: it is the divisor in the vol target, and setting
    # 3.0 against a 2x fund would size to two-thirds of the intended beta.
    "core_symbol": "TQQQ",
    "core_leverage": 3.0,
    # The vol is measured on the UNLEVERED index. Measuring it on TQQQ itself
    # would divide a 3x-inflated vol by the 3x leverage a second time.
    "reference_symbol": "QQQ",
    "off_symbol": "SPY",
    "cash_symbol": "BIL",
    # ── the transform ──
    # 0.20 annualised on the whole book. Raising it is the single most
    # dangerous edit in this file: exposure is linear in it.
    "target_vol": 0.20,
    # The clamp, not the vol target, is what bounds the worst case. At 0.65 of
    # a 3x fund the book carries 195% Nasdaq beta on the calmest tape in the
    # sample, which is where the ~-40% local maximum drawdown comes from.
    "core_max_weight": 0.65,
    # Quantization is the turnover control. Unquantized daily vol-scaling of a
    # 3x leg measured 1,000-2,000%/yr turnover in the Strategy X work; a 0.05
    # grid plus the band below brings it to 207-299%/yr. FLOORING (never
    # rounding) means quantization can only ever hold LESS.
    "weight_step": 0.05,
    "vol_fast_bars": 20,
    "vol_slow_bars": 60,
    # 70 closes = 69 returns, enough for the 60-bar slow window. Below this the
    # strategy returns {} and logs red. A cold start must never lever up.
    "min_history_bars": 70,
    # ── cadence ──
    # 0.10 of NAV. The band is what makes a weekly cadence a weekly TRADE
    # count rather than a weekly evaluation: most Wednesdays the drift is
    # inside it and nothing is sent.
    "core_rebalance_band": 0.10,
    # NY weekday of the LAST VISIBLE SESSION, Monday=0 — not of the call.
    # `pit_daily_observations` returns strictly-earlier sessions, so [2]
    # (Wednesday) means "decide on the first call that can see Wednesday's
    # close", which at daily granularity is Thursday's call. One tranche. Two entries (e.g.
    # [1, 3]) moves half the way on each and removes rebalance-timing luck,
    # which is worth >100 bp/yr — at the cost of doubling order count, which
    # is why it is not the default on a $6k account.
    "rebalance_weekdays": [2],
    # THE DIAL. 0.0 = the whole de-levered remainder in SPY (approach A);
    # 1.0 = the whole remainder in T-bills (approach B), which costs ~8pp of
    # CAGR and takes 2022 from about -30% to about -12%. Anything between is
    # a linear blend. With weight >= 0 and a SPY remainder, a 2022 above SPY's
    # own -18% is impossible by construction; this key is the honest answer.
    "remainder_bil_fraction": 0.0,
    # ── execution (read by strategy_x.targets_to_orders) ──
    "core_band_pct": 0.03,
    "min_order_usd": 25.0,
    "cost_haircut_pct": 0.005,
    # ── broker-side keys, read by backtest_engine, not by this module ──
    # The broker trims ANY single position to BROKER_MAX_SINGLE_POSITION_PCT
    # (default 0.15) and trims the buy to ZERO rather than clipping it. A
    # 65%-of-NAV core cannot be built underneath it: on BT102936 every levered
    # buy logged "trimmed to $0.00 ... cap=15%".
    "broker_max_single_position_pct": 0.95,
    "honour_single_position_cap": True,
    # ── live risk envelope, read by broker.py for THIS document only ──
    # A strategy designed to ride a -30% drawdown cannot live under the module
    # default 5% soft buy-freeze, and a 65% core cannot be built under a 10%
    # per-order cap. The gate keeps BLOCKING, never clipping; the caps are
    # simply set to what this strategy asks for. Every other document keeps
    # live_risk_state's module defaults untouched.
    "live_max_order_fraction": 0.70,
    "live_max_symbol_fraction": 0.70,
    "live_max_leveraged_fraction": 0.70,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
}


# Own parsers rather than strategy_x's, for two measured reasons. Its `_i`
# raises OverflowError on float("inf") — `int(inf)` is not caught by its
# (TypeError, ValueError, AttributeError) — and it resolves a missing default
# against strategy_x's DEFAULTS, so any EB-only key without an explicit default
# raises TypeError. Both fail OPEN, which is the wrong direction for a parser
# guarding a levered position.
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


def session_ordinal(session_id) -> int:
    """A monotonic integer per NY session, or 0 when the label is unusable.

    Derived from the session DATE rather than a bar count: bar buffers get
    trimmed, and a clock that ran backwards whenever the cache was trimmed
    would read as corruption.
    """
    from datetime import date as _date

    try:
        return max(0, _date.fromisoformat(str(session_id)).toordinal()
                   - _SESSION_EPOCH_ORDINAL)
    except (TypeError, ValueError):
        return 0


def session_weekday(session_id) -> int:
    """NY weekday of a session label, Monday=0, or -1 when unusable.

    From the session ordinal, never from a call count: at 15m granularity
    run_once fires ~26 times a session and a holiday row repeats the last
    completed close.
    """
    ordinal = session_ordinal(session_id)
    if ordinal <= 0 and str(session_id) != "1970-01-01":
        return -1
    return (ordinal + _EPOCH_WEEKDAY) % 7


def rebalance_weekdays(cfg) -> tuple:
    """Configured decision weekdays, sorted and de-duplicated.

    Falls back to the default rather than to "every day": a malformed list must
    never turn a weekly strategy into a daily one, which is the turnover
    failure this whole design exists to avoid.
    """
    raw = (cfg or {}).get("rebalance_weekdays", DEFAULTS["rebalance_weekdays"])
    out = set()
    try:
        for value in (raw or []):
            if isinstance(value, bool):
                continue
            day = int(value)
            if 0 <= day <= 6:
                out.add(day)
    except (TypeError, ValueError, AttributeError, OverflowError):
        out = set()
    if not out:
        return tuple(DEFAULTS["rebalance_weekdays"])
    return tuple(sorted(out))


def _quantize_floor(value: float, step: float) -> float:
    """Floor `value` onto a `step` grid. Rounding to 9 dp first is load-bearing:
    0.65 / 0.05 is 12.999999999999998 in binary floating point on some inputs,
    and a bare floor would silently hold 0.60 whenever the clamp bound."""
    if step <= 0:
        return value
    return round(math.floor(round(value / step, 9)) * step, Q)


def eb_core_weight(closes, cfg) -> float | None:
    """Target core weight as a fraction of NAV, or None to REFUSE.

        rv    = max(stdev(ret, 20), stdev(ret, 60)) * sqrt(252)
        w_raw = target_vol / (leverage * rv)
        w     = floor(clamp(w_raw, 0, core_max_weight) / step) * step

    None means "the strategy cannot evaluate its own risk". The caller must
    return {} — NOT fall back to a default weight. Every failure mode here
    (short history, a NaN close, a flat tape, a zero leverage) would otherwise
    resolve to MORE leverage, not less.
    """
    prices = _finite(closes)
    minimum = max(2, _i(cfg, "min_history_bars"))
    if prices is None or len(prices) < minimum:
        return None

    leverage = _f(cfg, "core_leverage")
    if not math.isfinite(leverage) or leverage <= 0:
        return None

    returns = [prices[i + 1] / prices[i] - 1.0 for i in range(len(prices) - 1)]
    fast = max(2, _i(cfg, "vol_fast_bars"))
    slow = max(2, _i(cfg, "vol_slow_bars"))
    if len(returns) < 2:
        return None
    rv = max(_stdev(returns[-fast:]), _stdev(returns[-slow:]))
    rv *= math.sqrt(_TRADING_DAYS)
    if not math.isfinite(rv) or rv <= 0:
        return None

    target_vol = max(0.0, _f(cfg, "target_vol"))
    cap = max(0.0, min(1.0, _f(cfg, "core_max_weight")))
    step = _f(cfg, "weight_step")
    if not math.isfinite(step) or step <= 0:
        return None

    w_raw = target_vol / (leverage * rv)
    if not math.isfinite(w_raw):
        return None
    return _quantize_floor(max(0.0, min(cap, w_raw)), step)


def eb_targets(w, cfg) -> dict:
    """Target weight per symbol as a fraction of NAV. Sums to exactly 1.0.

        core = w
        bil  = (1 - w) * remainder_bil_fraction
        spy  = 1 - core - bil

    `spy` is computed as the RESIDUAL rather than as `(1-w)*(1-dial)` so the
    three legs sum to 1.0 at Q decimals by construction. A weight set summing
    past 1.0 asks for a clip the account cannot fund.
    """
    cfg = cfg or {}
    try:
        core = float(w)
    except (TypeError, ValueError):
        core = 0.0
    if not math.isfinite(core):
        core = 0.0
    core = round(max(0.0, min(1.0, core)), Q)

    dial = max(0.0, min(1.0, _f(cfg, "remainder_bil_fraction")))
    bil = round((1.0 - core) * dial, Q)
    spy = round(1.0 - core - bil, Q)

    targets: dict = {}
    for symbol, weight in ((_s(cfg, "core_symbol"), core),
                           (_s(cfg, "cash_symbol"), bil),
                           (_s(cfg, "off_symbol"), spy)):
        if symbol and weight > 0:
            targets[symbol] = round(targets.get(symbol, 0.0) + weight, Q)
    return targets


def strategy_eb_universe(cfg) -> list:
    """Every symbol this strategy reads or trades, deterministic order.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the reference symbol has no bars and the traded legs
    have no price, and BOTH failures are silent — the strategy simply emits
    nothing. `broker._strategy_eb_universe_symbols` reads this to decide what to
    fetch and what to price.
    """
    cfg = cfg or {}
    out: list = []
    for key in ("reference_symbol", "core_symbol", "off_symbol", "cash_symbol"):
        symbol = _s(cfg, key)
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def eb_should_trade(session_id, w_target, w_held, cfg, cache) -> tuple:
    """(trade?, core weight to move to) for this session.

    Read-only on `cache`. The CALLER writes `LAST_REBALANCE_KEY` after it has
    actually decided, so a refusal here never consumes the session.

    Order of the rules is the design:
      1. an unusable session label never trades (fail closed);
      2. one decision per session, whatever the granularity;
      3. an exit to zero is UNCONDITIONAL — it ignores both the band and the
         weekday, because the band is meaningless around a target of zero and
         waiting four days to leave a 3x fund is the failure the vol transform
         exists to prevent;
      4. otherwise only the configured weekdays decide;
      5. otherwise only a drift of at least `core_rebalance_band` trades;
      6. a multi-weekday config moves 1/N of the way, not all the way.
    """
    try:
        target = float(w_target)
        held = float(w_held)
    except (TypeError, ValueError):
        return (False, 0.0)
    if not math.isfinite(target) or not math.isfinite(held):
        return (False, held if math.isfinite(held) else 0.0)

    if session_weekday(session_id) < 0:
        return (False, held)
    if (cache or {}).get(LAST_REBALANCE_KEY) == session_id:
        return (False, held)

    if target <= 0.0:
        return (True, 0.0) if held > 0.0 else (False, held)

    days = rebalance_weekdays(cfg)
    if session_weekday(session_id) not in days:
        return (False, held)

    band = max(0.0, _f(cfg, "core_rebalance_band"))
    if round(abs(target - held), Q) < band:
        return (False, held)

    tranches = max(1, len(days))
    return (True, round(held + (target - held) / tranches, Q))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb.py`
Expected: PASS, 34 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_eb.py backend/tests/test_strategy_eb.py
git commit -m "feat(strategy-eb): pure vol-targeted sizing, cadence and band"
```

---

## Task 2: Wrapper `StrategyEb`, schema sync, run_once contract

**Files:**
- Create: `backend/strategies/strategy_eb.py`
- Create: `scripts/strategy_eb_sync_schema.py`
- Test: `backend/tests/test_strategy_eb_run_once.py`

**Interfaces:**
- Consumes from Task 1: `DEFAULTS`, `LAST_REBALANCE_KEY`, `eb_core_weight`,
  `eb_should_trade`, `eb_targets`, `strategy_eb_universe`, `_s`, `_f`.
- Consumes from `strategy_x` (pure module, `backend/strategy_x.py`):
  `pit_daily_observations(bars, as_of) -> list[tuple[str, float]]`
  (`(session_id, close)` pairs, oldest first, session_id is an ISO date string)
  and `targets_to_orders(targets, *, nav, positions, prices, cash, config,
  owned=None) -> tuple[dict, dict]`.
- Produces: `strategies.strategy_eb.StrategyEb` with
  `run_once(symbols, prices, current_time, config, conditions, data=None,
  portfolio_emulator=None, strategy_cache=None, time_increment=None,
  mode=None, **kwargs) -> dict`, and the module-level
  `_SELL_INTENT = "etf_sell"`.

**Note on test placement:** the spec lists the schema-header-equality and
class-name tests under "Pure". They test the WRAPPER file, which does not exist
until this task, so they live in `test_strategy_eb_run_once.py` — the same
placement `test_strategy_xs_run_once.py` uses.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_strategy_eb_run_once.py`:

```python
"""Wrapper tests for Strategy EB: the broker contract and cache behaviour."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path. Adding backend/strategies/ too would make
# `strategy_x` resolve to the WRAPPER rather than the pure module — they share
# a name, and the wrapper imports the pure one, so it would self-import.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS, LAST_REBALANCE_KEY  # noqa: E402

#: THE DECISION WEEKDAY IS THE DATA DATE, NOT THE CALL DATE. `pit_daily_
#: observations` returns only STRICTLY EARLIER sessions, so a call on Thursday
#: sees through Wednesday and `rebalance_weekdays=[2]` fires then. Getting this
#: backwards makes every cadence test pass against a strategy that never trades.
#:   DECIDES: called Thu 2026-06-04, last visible session Wed 2026-06-03 (wd 2).
#:   SKIPS:   called Fri 2026-06-05, last visible session Thu 2026-06-04 (wd 3).
DECIDES = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
SKIPS = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)
DECISION_SESSION = "2026-06-03"

PRICES = {"TQQQ": 80.0, "SPY": 500.0, "BIL": 91.0, "QQQ": 480.0}


def alternating(pct, n=120, end_day=None, start=100.0):
    """Daily bars whose returns alternate exactly +pct, -pct, ..., the LAST one
    stamped `end_day` (default the Wednesday that `DECIDES` sees)."""
    end_day = end_day or datetime(2026, 6, 3, tzinfo=timezone.utc)
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": closes[i]} for i in range(n)]


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    value.update(overrides)
    return value


def data_for(ref_bars, legs=("TQQQ", "SPY", "BIL")):
    out = {"QQQ": {"bars": ref_bars}}
    for symbol in legs:
        out[symbol] = {"bars": alternating(0.002)}
    return out


def test_disabled_by_default_emits_nothing():
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, dict(DEFAULTS), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator())
    assert out == {}


def test_no_emulator_emits_nothing():
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01)),
                                 portfolio_emulator=None) == {}


def test_a_wednesday_opens_the_core_and_the_spy_remainder():
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out.get("TQQQ") == 1 and out.get("SPY") == 1
    assert cache["_strategy_eb_last"]["core_weight"] == 0.40
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "SPY": 0.60}


def test_every_decision_carries_its_own_size():
    """A bare 1 is sized by the broker's default cash_per_trade (~$1,000):
    index_core_tilt asked for $6,000 of SPY and received $900."""
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    sizes = out["_nexus_position_sizes"]
    for symbol, decision in out.items():
        if symbol.startswith("_"):
            continue
        assert symbol in sizes, symbol
        assert sizes[symbol].get("buy_cash", 0) > 0 or "sell_fraction" in sizes[symbol]


def test_it_does_not_trade_when_the_last_visible_session_is_not_a_decision_day():
    """Called on Friday, the last visible session is Thursday (weekday 3)."""
    assert StrategyEb().run_once(
        ["TQQQ"], PRICES, SKIPS, cfg(), {},
        data=data_for(alternating(0.01, end_day=datetime(
            2026, 6, 4, tzinfo=timezone.utc))),
        portfolio_emulator=FakeEmulator(), strategy_cache={}) == {}


def test_it_does_not_trade_twice_in_one_session():
    """The engine calls run_once on EVERY tick."""
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator()
    first = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                           data=data_for(alternating(0.01)),
                           portfolio_emulator=emu, strategy_cache=cache)
    assert first
    assert cache[LAST_REBALANCE_KEY] == DECISION_SESSION
    second = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                            data=data_for(alternating(0.01)),
                            portfolio_emulator=emu, strategy_cache=cache)
    assert second == {}


def test_it_refuses_on_short_history_rather_than_levering_up():
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01, n=40)),
                                 portfolio_emulator=FakeEmulator(),
                                 strategy_cache={}) == {}


def test_it_refuses_on_empty_bars():
    """Live passes data=None today; a blind strategy must do NOTHING."""
    for blind in (None, {}, {"QQQ": {"bars": []}}):
        assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                     data=blind,
                                     portfolio_emulator=FakeEmulator(),
                                     strategy_cache={}) == {}, blind


def test_it_prices_declared_legs_the_broker_did_not_carry():
    """The declared legs are absent from the operator's watchlist, so
    targets_to_orders would skip them at px <= 0 and emit nothing."""
    out = StrategyEb().run_once(["TQQQ"], {"TQQQ": 80.0}, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache={})
    assert out.get("SPY") == 1


def test_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary and
    whitelists only a fixed enum. Strategy X shipped without this and all 965
    of its sells logged would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.10)),
                                portfolio_emulator=emu, strategy_cache={})
    sells = [s for s, d in out.items() if not s.startswith("_") and d == -1]
    assert sells
    for symbol in sells:
        assert out["_nexus_action_intents"][symbol] == "etf_sell"


def test_it_never_sells_a_symbol_outside_its_own_universe():
    """`owned` scoping: walking the whole book liquidates a co-deployed
    strategy's positions, and _nexus_sell_enforcement is a HARD override."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0, "AAPL": 50.0})
    out = StrategyEb().run_once(["TQQQ"], dict(PRICES, AAPL=200.0), DECIDES,
                                cfg(), {}, data=data_for(alternating(0.10)),
                                portfolio_emulator=emu, strategy_cache={})
    assert "AAPL" not in out


def test_it_publishes_every_nexus_channel():
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    for key in ("_nexus_position_sizes", "_nexus_discovered",
                "_nexus_executable_buys", "_nexus_sell_enforcement",
                "_nexus_action_intents"):
        assert key in out, key
    assert set(out["_nexus_discovered"]) == {"QQQ", "TQQQ", "SPY", "BIL"}


def test_the_bil_dial_routes_the_remainder_to_bills():
    cache = {}
    StrategyEb().run_once(["TQQQ"], PRICES, DECIDES,
                          cfg(remainder_bil_fraction=1.0), {},
                          data=data_for(alternating(0.01)),
                          portfolio_emulator=FakeEmulator(),
                          strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "BIL": 0.60}


def test_the_schema_header_contains_exactly_every_default():
    path = os.path.join(_backend, "strategies", "strategy_eb.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
    assert schema["strategy"] == "strategy_eb"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 10


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    """broker.py resolves a run-once strategy by CamelCasing its id, so the
    class name is part of the contract. Strategy XS shipped once as
    `StrategyXS` and BT634331 ran 1,259 sessions completely inert — the only
    sign was one log line, and every unit test still passed because they import
    by name."""
    import strategies.strategy_eb as mod
    from strategies_meta import _module_to_class_name

    derived = _module_to_class_name("strategy_eb")
    assert derived == "StrategyEb"
    assert hasattr(mod, derived), f"broker looks for {derived}"
    assert hasattr(getattr(mod, derived), "run_once")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb_run_once.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.strategy_eb'`.

- [ ] **Step 3: Write the wrapper**

Create `backend/strategies/strategy_eb.py`. Lines 1 and 2 are the header
comments; write them as the placeholder below and let Step 5 regenerate line 1
from `DEFAULTS`.

```python
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_eb", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {}}
# INTELLISTOCK_DESCRIPTION: Efficient beta — a volatility-targeted leveraged Nasdaq core with the de-levered remainder in SPY, rebalanced once a week on a fixed weekday, every weight quantized and banded so it trades rarely. A risk transform, not an alpha: it makes no directional prediction and holds less of the same position when that position is more dangerous. One lever, remainder_bil_fraction, moves the remainder from SPY toward T-bills, trading CAGR for drawdown.
"""Strategy EB wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_eb.py`, which is pure. This file
owns only what needs the broker: the emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-08-27-strategy-eb-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_eb import (  # noqa: E402
    DEFAULTS,
    LAST_REBALANCE_KEY,
    _f,
    _s,
    eb_core_weight,
    eb_should_trade,
    eb_targets,
    strategy_eb_universe,
)
from strategy_x import (  # noqa: E402
    pit_daily_observations,
    targets_to_orders,
)

# Route through intellistock_logger, NOT print() and NOT utils.log_message. The
# backtest engine runs the broker with `detach=False, remove=True` and DISCARDS
# container stdout on success, so a print() goes nowhere durable — the one line
# that would expose an inert run would be invisible. intellistock_logger fans
# out to the backtest log buffer, which becomes BacktestResults.logs, the only
# log an operator actually reads. Strategy XS used utils.log_message and its
# lines never reached the sink.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyEb")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyEb] {msg}")


#: Every Strategy EB exit is a rebalance of an ETF book, which is what the
#: broker's sell whitelist calls `etf_sell`. Publishing it is not cosmetic:
#: broker.py's Z2.1 check reads `action_intent` off the strategy summary, and a
#: sell with no recognised intent logs would_block_in_phase2=True. Measured on
#: Strategy X's BT406990, that was 965 of 965 sells — the whole book.
_SELL_INTENT = "etf_sell"

_LAST_DECISION_KEY = "_strategy_eb_last"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyEb:
    # The class name is NOT free. `broker.py` resolves a run-once strategy by
    # CamelCasing its id — `strategy_eb` -> `StrategyEb` — and logs
    # "Class not found ... has no run_once method; skipping" when it misses,
    # then runs the whole backtest inert.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_eb_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        universe = strategy_eb_universe(cfg)
        reference = _s(cfg, "reference_symbol")

        # THE point-in-time boundary: strictly-earlier NY sessions only. At the
        # 15m/1h cadence these backtests actually run, "today's daily bar" IS
        # that session's 16:00 close — six hours in the future of a 09:45
        # decision.
        observations = pit_daily_observations(_bars_for(data, reference),
                                              current_time)
        if not observations:
            _log(f"StrategyEb: REFUSING to trade — no visible {reference} "
                 "daily closes. Live passes data=None; a strategy that cannot "
                 "evaluate its own risk must do NOTHING.", "red")
            return {}
        session_id = observations[-1][0]
        closes = [close for _, close in observations]

        weight = eb_core_weight(closes, cfg)
        if weight is None:
            _log(f"StrategyEb: REFUSING to trade — {len(closes)} {reference} "
                 f"closes, need {cfg.get('min_history_bars')}, or realised "
                 "volatility is not finite and positive. A cold start must "
                 "never silently lever up.", "red")
            return {}

        # Prices the broker did not carry: the declared legs are absent from the
        # operator's watchlist, so fall back to the last VISIBLE close, which is
        # the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in (prices or {}).items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_observations(_bars_for(data, symbol),
                                             current_time)
            if visible and float(visible[-1][1]) > 0:
                eff[symbol] = float(visible[-1][1])

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}
        core = _s(cfg, "core_symbol")
        held = (float(positions.get(core) or 0.0)
                * float(eff.get(core) or 0.0)) / nav

        trade, effective_weight = eb_should_trade(session_id, weight, held,
                                                  cfg, cache)
        if not trade:
            return {}

        targets = eb_targets(effective_weight, cfg)
        cash = float(portfolio_emulator.get_cash() or 0.0)
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=cfg, owned=set(universe))

        # Written whether or not orders came out: the session HAS been decided,
        # and at 15m granularity there are ~26 more ticks in it.
        cache[LAST_REBALANCE_KEY] = session_id
        cache[_LAST_DECISION_KEY] = {
            "session": session_id,
            "core_weight": weight,
            "effective_weight": effective_weight,
            "held_weight": round(held, 6),
            "targets": dict(targets),
            "orders": len(decisions),
        }
        _log(f"StrategyEb {session_id} | core {core} target {weight:.0%} "
             f"(held {held:.0%} -> {effective_weight:.0%}) | targets="
             + ", ".join(f"{s} {w:.1%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        out = dict(decisions)
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = list(universe)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items()
                                         if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                          if d == -1]
        out["_nexus_action_intents"] = {
            s: _SELL_INTENT for s, d in decisions.items() if d == -1}
        return out
```

- [ ] **Step 4: Write the schema sync script**

Create `scripts/strategy_eb_sync_schema.py`:

```python
#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_eb with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift from
`backend/strategy_eb.py:DEFAULTS` means an operator configures a key the
strategy does not have, or misses one it does.

Unlike the Strategy X variant, nothing is re-injected: `broker_max_single_
position_pct` and `honour_single_position_cap` are BROKER-side keys read by
backtest_engine rather than by the strategy module, but they live in EB's
DEFAULTS so the header is a plain copy. The assertion below is what keeps that
true if someone ever moves them out.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_eb import DEFAULTS  # noqa: E402

_BROKER_SIDE_KEYS = ("broker_max_single_position_pct",
                     "honour_single_position_cap")

path = pathlib.Path("backend/strategies/strategy_eb.py")
source = path.read_text()
match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
schema = json.loads(match.group(1))
config = dict(DEFAULTS)
missing = [k for k in _BROKER_SIDE_KEYS if k not in config]
if missing:
    raise SystemExit(
        "strategy_eb DEFAULTS is missing broker-side key(s) "
        + ", ".join(missing)
        + ": the single-position cap would silently stay at the 15% failsafe "
          "and every levered buy would be trimmed to $0.00.")
schema["config"] = config
schema["execution_position"] = 10
path.write_text(source.replace(match.group(0),
                               "# INTELLISTOCK_SCHEMA: " + json.dumps(schema)))
print(f"schema synced from DEFAULTS: {len(config)} config keys")
```

- [ ] **Step 5: Run the sync script**

Run: `python3 scripts/strategy_eb_sync_schema.py`
Expected: `schema synced from DEFAULTS: 25 config keys`

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb.py backend/tests/test_strategy_eb_run_once.py`
Expected: PASS, 49 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/strategies/strategy_eb.py scripts/strategy_eb_sync_schema.py backend/tests/test_strategy_eb_run_once.py
git commit -m "feat(strategy-eb): StrategyEb wrapper, schema header, order emission"
```

---

## Task 3: Broker wiring — universe, fetch site, price site

**Files:**
- Modify: `backend/broker.py:4376` (new function after `_strategy_xs_universe_symbols`), `backend/broker.py:4402-4409` (price site inside `_strategy_x_prepare`), `backend/broker.py:10182-10186` (fetch site)
- Test: `backend/tests/test_strategy_eb_broker_wiring.py`

**Interfaces:**
- Consumes: `strategy_eb.DEFAULTS`, `strategy_eb.strategy_eb_universe(cfg) -> list[str]` (Task 1).
- Produces: `broker._strategy_eb_universe_symbols(cached_strategies) -> list[str]`.
  Returns `[]` for an absent, disabled, or malformed spec list. Never raises.

There are TWO wiring points and missing either one is silent. The fetch site
decides which symbols get BARS downloaded; the prepare site decides which get a
PRICE on the bar. Without bars the vol transform is blind; without a price
`targets_to_orders` skips the leg at `px <= 0`. Either way the strategy just
emits nothing.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_eb_broker_wiring.py`:

```python
"""Strategy EB must get bars and prices for legs the watchlist never lists."""
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
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    assert wanted, f"none of {names} found in broker.py"
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_eb", "config": config}]


def test_the_declared_eb_universe_is_returned():
    ns = _extract("_strategy_eb_universe_symbols")
    syms = ns["_strategy_eb_universe_symbols"](spec(strategy_eb_enabled=True))
    assert syms == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_the_qld_variant_is_returned_when_configured():
    ns = _extract("_strategy_eb_universe_symbols")
    syms = ns["_strategy_eb_universe_symbols"](
        spec(strategy_eb_enabled=True, core_symbol="QLD", core_leverage=2.0))
    assert "QLD" in syms and "TQQQ" not in syms


def test_a_disabled_eb_contributes_no_symbols():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        spec(strategy_eb_enabled=False)) == []


def test_an_absent_eb_contributes_no_symbols():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        [{"strategy": "graph_nexus_analysis", "config": {}}]) == []


def test_the_legacy_unseparated_id_is_matched_too():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        [{"strategy": "StrategyEb", "config": {"strategy_eb_enabled": True}}])


def test_a_malformed_spec_list_does_not_raise():
    ns = _extract("_strategy_eb_universe_symbols")
    for junk in (None, [], [None], ["strategy_eb"], [{"strategy": None}]):
        assert ns["_strategy_eb_universe_symbols"](junk) == [], junk


def test_both_wiring_points_reference_the_eb_universe():
    """A source assertion, because the fetch site is inline in a 4,000-line
    function and cannot be AST-extracted. Missing either point is silent."""
    source = open(_BROKER).read()
    uses = source.count("_strategy_eb_universe_symbols(")
    # one definition + the prepare (price) site + the fetch site
    assert uses >= 3, f"expected the EB universe at both wiring points, saw {uses}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb_broker_wiring.py`
Expected: FAIL — `AssertionError: none of ('_strategy_eb_universe_symbols',) found in broker.py`.

- [ ] **Step 3: Add the universe function**

Insert immediately after `_strategy_xs_universe_symbols` ends (`backend/broker.py:4376`, the blank line before `def _strategy_x_prepare`):

```python
def _strategy_eb_universe_symbols(cached_strategies):
    """Symbols strategy_eb needs bars and prices for, from its own config.

    Same contract as `_strategy_x_universe_symbols` and the same reason: a
    strategy that trades symbols the operator never listed must declare them,
    or `price_history` is built without them and the strategy is silently
    inert. Returns [] when strategy_eb is absent or disabled, so this is a
    no-op for every other instance.
    """
    try:
        from strategy_eb import DEFAULTS as _EB_DEFAULTS, strategy_eb_universe
    except Exception:
        return []
    out = []
    try:
        for spec in (cached_strategies or []):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("strategy") or "").strip().lower()
            if name not in {"strategy_eb", "strategyeb"}:
                continue
            merged = {**_EB_DEFAULTS, **(spec.get("config") or {})}
            if not merged.get("strategy_eb_enabled", False):
                continue
            for sym in strategy_eb_universe(merged):
                if sym and sym not in out:
                    out.append(sym)
    except Exception:
        return []
    return out
```

- [ ] **Step 4: Add the price site**

Inside `_strategy_x_prepare`, after the existing XS union block
(`backend/broker.py:4402-4409`, immediately before
`syms = [s for s in _declared if s and not (prices or {}).get(s)]`):

```python
        # Guarded SEPARATELY for the same reason the XS lookup is: this whole
        # function runs inside a try/except that logs and continues, so a
        # failure resolving strategy_eb must not take strategy_x's pricing down
        # with it. An unpriced leg is skipped in `targets_to_orders` with no
        # error anywhere.
        try:
            _eb_declared = _strategy_eb_universe_symbols(cached_strategies)
        except Exception:
            _eb_declared = []
        for _eb in _eb_declared:
            if _eb not in _declared:
                _declared.append(_eb)
```

- [ ] **Step 5: Add the fetch site**

After the existing XS fetch loop (`backend/broker.py:10182-10186`, immediately
before `symbols_for_data = symbols_for_fetch`):

```python
    # 2026-08-27: strategy_eb declares its own universe the same way — the
    # reference index it measures volatility on, the levered leg, the off leg
    # and the cash leg, none of which need to be in the instance watchlist.
    # This is the FETCH site; the one inside `_strategy_x_prepare` only fixes
    # PRICES. Missing either is silent, so both are asserted in
    # test_strategy_eb_broker_wiring.py.
    for _eb_sym in _strategy_eb_universe_symbols(_cached_strategies):
        if _eb_sym not in symbols_for_fetch:
            symbols_for_fetch.append(_eb_sym)
            _log(f"Adding {_eb_sym} to bar data for strategy_eb", "cyan")
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb_broker_wiring.py`
Expected: PASS, 7 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/broker.py backend/tests/test_strategy_eb_broker_wiring.py
git commit -m "feat(strategy-eb): declare the EB universe at both broker wiring points"
```

---

## Task 4: Symbol-tiered execution cost model

**Files:**
- Modify: `backend/simulated_execution.py` (new classes after
  `LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL:122`; `NextEventExecutionSimulator.__init__:395-398`;
  `affordable_buy_quantity:448-460`; `on_quote` reads at `:507`, `:521`, `:564`, `:569`, `:577`, `:624`)
- Modify: `backend/portfolio_emulator.py:246-254`, `:510-536`, `:704`, `:764`,
  `:1324`, `:1348`, `:1608`, `create_backtest_emulator:1703-1758`
- Modify: `backend/backtest_evidence_options.py:52-57` (`_OPTION_KEYS`), `:180-192`
  (validator), `:240-252` (return dict), and a new `resolve_execution_cost_tiers`
  after `resolve_execution_cost_model:280`
- Modify: `backend/api/main.py:708` and `:3055`
- Modify: `backend/broker.py:10012-10014`
- Test: `backend/tests/test_tiered_cost_model.py`

**Interfaces:**
- Produces in `simulated_execution`:
  - `ETF_LIQUID_SYMBOLS: frozenset[str]`
  - `ETF_LIQUID_EQUITY_COST_MODEL: ExecutionCostModel`
  - `COST_TIER_PRESETS: dict[str, tuple[frozenset[str], ExecutionCostModel]]`
  - `class TieredExecutionCostModel(default: ExecutionCostModel, tiers: dict[frozenset[str], ExecutionCostModel], version: str)`
    with `.default`, `.version`, `.spread_bps`, `.slippage_bps`, `.fee_bps`,
    `.latency`, `.model_for(symbol) -> ExecutionCostModel`, `.as_dict() -> dict`
  - `tiered_cost_model(preset_id: str, default: ExecutionCostModel) -> TieredExecutionCostModel`
- Produces in `backtest_evidence_options`:
  - `EQUITY_COST_TIER_PRESETS: frozenset[str]` (= `{"etf-liquid"}`)
  - `resolve_execution_cost_tiers(preset_id, base) -> ExecutionCostModel | TieredExecutionCostModel`
    (returns `base` **unchanged** when `preset_id` is None)
  - `validate_evidence_options` returns the extra key `"equity_cost_tiers"`.
- Produces: `NextEventExecutionSimulator._model_for(symbol) -> ExecutionCostModel`;
  `PortfolioEmulator._equity_model_for(symbol) -> ExecutionCostModel`;
  `PortfolioEmulator._equity_fill(side, shares, mid, symbol=None)`;
  `NextEventExecutionSimulator.affordable_buy_quantity(cash, reference_price, symbol=None)`.

**The invariant this task must not break:** every fill's `cost_model_version`
equals the summary's `execution_cost_model_version`
(`backtest_summary.py:131-134`). A tiered model therefore stamps ONE composite
version — never the per-symbol tier's version — on every fill.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tiered_cost_model.py`:

```python
"""Symbol-tiered execution costs.

The engine charges a flat 23.2 bps one-way on every symbol — a notional-weighted
spread measured on small-cap Nexus fills — and `equity_total_cost_bps` can only
stress UP (25/50). An ETF book measured that way is mis-priced by roughly 19 bps
a side, which is what mis-measured Strategy X by ~20pp.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.backtest_evidence_options import (
    EvidenceOptionError,
    resolve_execution_cost_model,
    resolve_execution_cost_tiers,
    validate_evidence_options,
)
from backend.backtest_summary import assert_execution_provenance_promotable
from backend.portfolio_emulator import PortfolioEmulator, create_backtest_emulator
from backend.simulated_execution import (
    ETF_LIQUID_EQUITY_COST_MODEL,
    ETF_LIQUID_SYMBOLS,
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationOrder,
    SimulationQuote,
    TieredExecutionCostModel,
    tiered_cost_model,
)

T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
BASE = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL


def _tiered():
    return tiered_cost_model("etf-liquid", BASE)


# ── routing ─────────────────────────────────────────────────────────────────

def test_an_etf_routes_to_the_cheap_tier():
    model = _tiered()
    assert model.model_for("SPY") is ETF_LIQUID_EQUITY_COST_MODEL
    assert model.model_for("tqqq") is ETF_LIQUID_EQUITY_COST_MODEL


def test_everything_else_stays_on_the_measured_book_cost():
    assert _tiered().model_for("SNDK") is BASE


def test_the_preset_covers_every_leg_this_strategy_can_trade():
    assert {"SPY", "QQQ", "TQQQ", "QLD", "SQQQ", "BIL", "GLD", "IWM"} <= ETF_LIQUID_SYMBOLS


def test_the_etf_preset_is_four_point_four_bps_one_way():
    m = ETF_LIQUID_EQUITY_COST_MODEL
    assert m.spread_bps / 2.0 + m.slippage_bps + m.fee_bps == pytest.approx(4.4)


def test_the_composite_version_names_the_preset():
    assert _tiered().version == "equity-tiered-v1[etf-liquid]"


def test_the_tiered_model_delegates_its_scalars_to_the_default():
    """`SimulationQuote.from_mid(spread_bps=...)` and the promotion checker both
    read bare scalars off the model."""
    model = _tiered()
    assert model.spread_bps == BASE.spread_bps
    assert model.latency == BASE.latency


def test_an_unknown_preset_is_refused_at_construction():
    with pytest.raises(ValueError):
        tiered_cost_model("no-such-preset", BASE)


# ── provenance ──────────────────────────────────────────────────────────────

def _fill_one(cost_model, symbol):
    sim = NextEventExecutionSimulator(cost_model)
    sim.submit(SimulationOrder(order_id="o1", symbol=symbol, side="buy",
                               quantity=10.0, decision_at=T0,
                               execute_not_before=T0, source="main_signal"))
    fills = sim.on_quote(SimulationQuote.from_mid(
        symbol=symbol, timestamp=T1, mid=100.0, spread_bps=8.0))
    return sim, fills


def test_every_fill_stamps_the_composite_version_not_the_tiers():
    sim, fills = _fill_one(_tiered(), "SPY")
    assert fills
    assert fills[0].cost_model_version == "equity-tiered-v1[etf-liquid]"
    assert sim.execution_summary()["execution_cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


def test_a_tiered_run_is_promotion_eligible():
    sim, _ = _fill_one(_tiered(), "SPY")
    assert_execution_provenance_promotable(sim.execution_summary())


def test_the_tier_actually_changes_the_fill_price():
    _, cheap = _fill_one(_tiered(), "SPY")
    _, dear = _fill_one(_tiered(), "SNDK")
    assert cheap[0].price < dear[0].price


def test_a_symbol_outside_every_tier_fills_byte_identically_to_the_flat_model():
    """With no matching tier the object graph must be indistinguishable, or
    every existing backtest silently changes."""
    _, tiered = _fill_one(_tiered(), "SNDK")
    _, flat = _fill_one(BASE, "SNDK")
    assert tiered[0].price == flat[0].price
    assert tiered[0].fees == flat[0].fees
    assert tiered[0].spread_cost == flat[0].spread_cost
    assert tiered[0].slippage_cost == flat[0].slippage_cost


def test_a_plain_cost_model_is_still_accepted_unchanged():
    sim, fills = _fill_one(BASE, "SPY")
    assert sim.execution_summary()["execution_cost_model_version"] == BASE.version
    assert fills[0].cost_model_version == BASE.version


def test_a_non_model_is_still_rejected():
    with pytest.raises(ValueError):
        NextEventExecutionSimulator("cheap")


# ── the emulator's legacy immediate path ────────────────────────────────────

def test_the_emulator_charges_the_tier_for_an_etf_and_the_book_cost_otherwise():
    emu = PortfolioEmulator(100_000.0, equity_cost_model=_tiered())
    etf_price, _, _, _ = emu._equity_fill("buy", 1.0, 100.0, symbol="SPY")
    other_price, _, _, _ = emu._equity_fill("buy", 1.0, 100.0, symbol="SNDK")
    assert etf_price < other_price


def test_the_emulator_stamps_the_composite_version_on_its_trades():
    emu = PortfolioEmulator(100_000.0, equity_cost_model=_tiered())
    assert emu.buy("SPY", 10.0, 100.0, timestamp=T0)
    assert emu.get_trade_history()[-1]["cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


def test_create_backtest_emulator_accepts_a_tiered_model():
    emu = create_backtest_emulator(initial_cash=6_000.0, taker_fee=0.0,
                                   is_crypto=False,
                                   execution_delay=timedelta(days=1),
                                   cost_model=_tiered())
    assert emu.get_realism_summary()["equity_cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


# ── the evidence option ─────────────────────────────────────────────────────

def test_the_option_is_absent_by_default():
    assert validate_evidence_options({})["equity_cost_tiers"] is None


def test_the_preset_is_accepted():
    assert validate_evidence_options(
        {"equity_cost_tiers": "etf-liquid"})["equity_cost_tiers"] == "etf-liquid"


def test_an_unknown_preset_is_refused():
    with pytest.raises(EvidenceOptionError):
        validate_evidence_options({"equity_cost_tiers": "cheap-please"})


def test_a_non_string_preset_is_refused():
    for junk in (1, True, ["etf-liquid"]):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"equity_cost_tiers": junk})


def test_no_preset_returns_the_base_model_object_itself():
    """Byte-identity for every existing run: not an equal object, the SAME one."""
    base = resolve_execution_cost_model(None)
    assert resolve_execution_cost_tiers(None, base) is base


def test_a_preset_wraps_the_stressed_model_not_the_nominal_one():
    """A cost stress arm must still mean what it says."""
    stressed = resolve_execution_cost_model(50.0)
    wrapped = resolve_execution_cost_tiers("etf-liquid", stressed)
    assert isinstance(wrapped, TieredExecutionCostModel)
    assert wrapped.default is stressed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_tiered_cost_model.py`
Expected: FAIL — `ImportError: cannot import name 'TieredExecutionCostModel'`.

- [ ] **Step 3: Add the tier model to `simulated_execution.py`**

After `LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL` (ends at `:122`):

```python
#: THE 8 bps ETF SPREAD IS AN ASSUMPTION, NOT A MEASUREMENT.
#: The 45.6 bps above was priced against SIP NBBO on 61 real fills. This was
#: not. It is conservative for SPY/QQQ (~1 bp quoted) and roughly right for
#: TQQQ; the first live EB fills must be priced the same way the original 61
#: were, and this preset updated if it is off by more than 2x.
#: One-way = 8.0/2 + 0.1 + 0.3 = 4.4 bps.
ETF_LIQUID_EQUITY_COST_MODEL = ExecutionCostModel(
    version="equity-etf-liquid-v1",
    spread_bps=8.0,
    slippage_bps=0.1,
    fee_bps=0.3,
    latency=timedelta(0),
)

#: Every leg Strategy EB and its siblings can trade, plus the two index proxies
#: a comparison run needs. Deliberately a CLOSED list: an open rule ("any ETF")
#: would quietly re-price a thin sector fund at mega-cap-index costs.
ETF_LIQUID_SYMBOLS = frozenset(
    {"SPY", "QQQ", "TQQQ", "QLD", "SQQQ", "BIL", "GLD", "IWM"}
)


class TieredExecutionCostModel:
    """One cost model per symbol tier, one VERSION for the whole run.

    `assert_execution_provenance_promotable` requires every fill's
    `cost_model_version` to equal the summary's. So the composite version — not
    the matched tier's version — is what gets stamped, and `as_dict()` carries
    the default model's scalars so the promotion checker's finite-number rules
    still have something to read.

    Not an `ExecutionCostModel` subclass on purpose: the dataclass is frozen and
    compared by value in `create_backtest_emulator`'s nominal-substitution
    check, and a subclass would compare equal to a plain model with the same
    fields.
    """

    __slots__ = ("_default", "_tiers", "_version", "_index")

    def __init__(self, default, tiers, version):
        if not isinstance(default, ExecutionCostModel):
            raise ValueError("default must be an ExecutionCostModel")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        index = {}
        for symbols, model in (tiers or {}).items():
            if not isinstance(model, ExecutionCostModel):
                raise ValueError("every tier must be an ExecutionCostModel")
            for symbol in symbols:
                key = str(symbol).strip().upper()
                if not key:
                    continue
                if key in index:
                    raise ValueError(f"symbol {key} appears in two tiers")
                index[key] = model
        self._default = default
        self._tiers = {frozenset(s): m for s, m in (tiers or {}).items()}
        self._version = version.strip()
        self._index = index

    @property
    def default(self):
        return self._default

    @property
    def version(self):
        return self._version

    # Delegated so callers that read a bare scalar off `cost_model` — the quote
    # constructors in portfolio_emulator and the promotion checker — keep
    # working without knowing about tiers.
    @property
    def spread_bps(self):
        return self._default.spread_bps

    @property
    def slippage_bps(self):
        return self._default.slippage_bps

    @property
    def fee_bps(self):
        return self._default.fee_bps

    @property
    def latency(self):
        return self._default.latency

    def model_for(self, symbol):
        return self._index.get(str(symbol or "").strip().upper(), self._default)

    def as_dict(self):
        payload = dict(self._default.as_dict())
        payload["version"] = self._version
        payload["tiers"] = [
            {"symbols": sorted(symbols), "model": model.as_dict()}
            for symbols, model in sorted(
                self._tiers.items(), key=lambda kv: sorted(kv[0]))
        ]
        return payload


#: preset id -> (symbols, model). One preset today.
COST_TIER_PRESETS = {
    "etf-liquid": (ETF_LIQUID_SYMBOLS, ETF_LIQUID_EQUITY_COST_MODEL),
}


def tiered_cost_model(preset_id, default) -> TieredExecutionCostModel:
    """Build the tiered model for a named preset over `default`."""
    key = str(preset_id or "").strip()
    if key not in COST_TIER_PRESETS:
        raise ValueError(
            f"unknown execution cost tier preset {preset_id!r}; "
            f"known: {sorted(COST_TIER_PRESETS)}")
    symbols, model = COST_TIER_PRESETS[key]
    return TieredExecutionCostModel(
        default=default, tiers={symbols: model},
        version=f"equity-tiered-v1[{key}]")
```

- [ ] **Step 4: Make the simulator symbol-aware**

Replace `NextEventExecutionSimulator.__init__` (`:395-398`):

```python
    def __init__(self, cost_model):
        if not isinstance(cost_model,
                          (ExecutionCostModel, TieredExecutionCostModel)):
            raise ValueError("cost_model must be an ExecutionCostModel")
        self.cost_model = cost_model
        self._tiered = isinstance(cost_model, TieredExecutionCostModel)
```

Add immediately after `__init__` (before the `pending_orders` property):

```python
    def _model_for(self, symbol):
        """The cost model for one symbol. Identity when untiered, so an
        untiered run's object graph is unchanged."""
        return (self.cost_model.model_for(symbol) if self._tiered
                else self.cost_model)
```

Change `affordable_buy_quantity` (`:448-460`) to take the symbol:

```python
    def affordable_buy_quantity(self, cash, reference_price, symbol=None) -> float:
        cash_value = _finite_number(cash, field="cash", positive=True)
        mid = _finite_number(
            reference_price, field="reference_price", positive=True
        )
        model = self._model_for(symbol)
        modeled_ask = mid * (1.0 + model.spread_bps / 20_000.0)
        fill_price = modeled_ask * (1.0 + model.slippage_bps / 10_000.0)
        all_in_per_share = fill_price * (1.0 + model.fee_bps / 10_000.0)
        return cash_value / all_in_per_share
```

In `on_quote`, bind the per-order model once at the top of the per-order body
(immediately after the loop obtains `order` / `state`) and use it at every read:

```python
            model = self._model_for(order.symbol)
```

Then substitute, keeping the surrounding expressions byte-identical:
- `:507` `self.cost_model.latency` → `model.latency`
- `:521` `self.cost_model.spread_bps` → `model.spread_bps`
- `:564` and `:569` `self.cost_model.slippage_bps` → `model.slippage_bps`
- `:577` `self.cost_model.fee_bps` → `model.fee_bps`
- `:624` `self.cost_model.fee_bps` → `model.fee_bps`
- `:658` `cost_model_version=self.cost_model.version` — **LEAVE UNCHANGED.**
  Add above it:
  ```python
                # The COMPOSITE version, never the matched tier's:
                # assert_execution_provenance_promotable requires every fill to
                # carry the summary's version, and a per-tier stamp would make
                # a mixed run permanently promotion-ineligible.
  ```

`execution_summary` (`:690-691`) needs no change — `.version` and `.as_dict()`
are both defined on the tiered object.

- [ ] **Step 5: Make the emulator symbol-aware**

`backend/portfolio_emulator.py`:

1. Import `TieredExecutionCostModel` alongside `NextEventExecutionSimulator`
   in both import blocks (`:28` and `:41`).
2. `:252` — widen the type check:
   ```python
        if not isinstance(equity_cost_model,
                          (ExecutionCostModel, TieredExecutionCostModel)):
            raise ValueError("equity_cost_model must be an ExecutionCostModel")
        self._equity_cost_model = equity_cost_model
        self._equity_tiered = isinstance(equity_cost_model,
                                         TieredExecutionCostModel)
   ```
3. Add beside `_equity_fill`:
   ```python
    def _equity_model_for(self, symbol):
        return (self._equity_cost_model.model_for(symbol)
                if self._equity_tiered else self._equity_cost_model)
   ```
4. `:510` — `def _equity_fill(self, side, shares, mid, symbol=None):` and
   `:519` — `model = self._equity_model_for(symbol)`.
5. `:704` — `self._equity_fill("buy", shares, price, symbol=ticker)`.
   `:764` — `self._equity_fill("sell", shares, price, symbol=ticker)`.
6. `:1324` and `:1348` — the quote spread must match the symbol being filled:
   `spread_bps=self._execution_simulator._model_for(symbol).spread_bps` and
   `spread_bps=self._execution_simulator._model_for(event.symbol).spread_bps`.
7. `:1518` — `self._execution_simulator.affordable_buy_quantity(amount_to_use, price, symbol=ticker)`.
8. `:1608` — `model = self._equity_model_for(ticker)`.
9. `create_backtest_emulator` (`:1736`) — widen the guard:
   ```python
    if not isinstance(cost_model,
                      (ExecutionCostModel, TieredExecutionCostModel)):
        raise ValueError("cost_model must be an ExecutionCostModel")
   ```
   The `cost_model == DEFAULT_EQUITY_EXECUTION_COST_MODEL` substitution check
   below it needs no change: `TieredExecutionCostModel` is not a dataclass and
   never compares equal to one.

`get_realism_summary`'s `equity_one_way_cost_bps` (`:1378-1381`) keeps reading
the delegated scalars, i.e. the DEFAULT tier's cost. That is the honest headline
for a mixed book; the per-tier detail is in `equity_cost_model["tiers"]`.

- [ ] **Step 6: Add the evidence option**

`backend/backtest_evidence_options.py`:

1. Import `TieredExecutionCostModel` and `tiered_cost_model` from
   `simulated_execution` (`:26-30`).
2. After `COST_SCENARIO_TARGETS_BPS` (`:50`):
   ```python
   #: Symbol-tier presets a queued run may select. Closed set: a run must not be
   #: able to invent its own cost basis from a backtest payload.
   EQUITY_COST_TIER_PRESETS = frozenset({"etf-liquid"})
   ```
3. Add `"equity_cost_tiers"` to `_OPTION_KEYS` (`:52-57`).
4. After `_validate_cost_bps` (`:192`):
   ```python
   def _validate_cost_tiers(value):
       if value is None:
           return None
       if isinstance(value, bool) or not isinstance(value, str):
           raise EvidenceOptionError(
               "equity_cost_tiers must be one of "
               + ", ".join(sorted(EQUITY_COST_TIER_PRESETS)))
       preset = value.strip()
       if preset not in EQUITY_COST_TIER_PRESETS:
           raise EvidenceOptionError(
               f"unknown equity_cost_tiers preset {value!r}; known: "
               + ", ".join(sorted(EQUITY_COST_TIER_PRESETS)))
       return preset
   ```
5. In the `validate_evidence_options` return dict (`:248`), beside
   `equity_total_cost_bps`:
   ```python
        "equity_cost_tiers": _validate_cost_tiers(payload.get("equity_cost_tiers")),
   ```
6. After `resolve_execution_cost_model` (`:280` onward, at the end of that
   function):
   ```python
   def resolve_execution_cost_tiers(preset_id, base):
       """Wrap ONE resolved cost model in a symbol tier, or return it untouched.

       Returns `base` ITSELF (not a copy) when no preset is selected, so an
       ordinary run's object graph — and therefore its fills — are unchanged.

       This wraps the model that `resolve_execution_cost_model` already
       resolved, rather than being a second independent cost input, because
       broker.py hashes exactly one model into the experiment preregistration
       while the emulator does the filling. Two inputs would let a receipt
       claim a cost basis the fills never used.
       """
       if preset_id is None:
           return base
       preset = _validate_cost_tiers(preset_id)
       if not isinstance(base, ExecutionCostModel):
           raise EvidenceOptionError("base must be an ExecutionCostModel")
       return tiered_cost_model(preset, base)
   ```

- [ ] **Step 7: Thread it through the API and the broker**

`backend/api/main.py:708`, after `equity_total_cost_bps`:
```python
    equity_cost_tiers: Optional[str] = None      # None (flat 23.2 bps) or "etf-liquid"
```
`backend/api/main.py:3055`, in the `evidence_options` dict:
```python
            "equity_cost_tiers": body.equity_cost_tiers,
```

`backend/broker.py:10009-10014` — extend the import and the resolution:
```python
    from backtest_evidence_options import (
        apply_candidate_overrides as _apply_candidate_overrides,
        resolve_execution_cost_model as _resolve_execution_cost_model,
        resolve_execution_cost_tiers as _resolve_execution_cost_tiers,
    )
    _evidence_options = _load_backtest_evidence_options(backtest_row_id)
    _evidence_cost_model = _resolve_execution_cost_model(
        _evidence_options.get("equity_total_cost_bps"))
    # ONE immutable cost object per run: the tier wraps the model that is
    # already hashed into preregistration and handed to the emulator, so a
    # receipt can never claim a cost basis the fills did not use.
    _evidence_cost_model = _resolve_execution_cost_tiers(
        _evidence_options.get("equity_cost_tiers"), _evidence_cost_model)
```
No change is needed at `broker.py:10338-10349`: `cost_model=_evidence_cost_model`
already carries the tiered object into `create_backtest_emulator`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_tiered_cost_model.py`
Expected: PASS, 22 passed.

- [ ] **Step 9: Prove nothing else moved**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_backtest_execution_costs.py backend/tests/test_backtest_live_realism.py backend/tests/test_passive_limit_execution.py backend/tests/test_backtest_evidence_api.py backend/tests/test_fill_never_exceeds_spendable_cash.py backend/tests/test_backtest_execution_snapshot_contract.py backend/tests/test_crypto_backtest_fees.py backend/tests/test_backtest_candidate_overrides.py backend/tests/test_backtest_research_default.py backend/tests/test_nexus_evidence_matrix_script.py`
Expected: PASS, no failures. These are every test that touches the cost model,
the evidence options, or provenance. Any failure here means the untiered path
was NOT left byte-identical — fix the implementation, never the test.

- [ ] **Step 10: Commit**

```bash
git add backend/simulated_execution.py backend/portfolio_emulator.py backend/backtest_evidence_options.py backend/api/main.py backend/broker.py backend/tests/test_tiered_cost_model.py
git commit -m "feat(engine): symbol-tiered execution cost model with an etf-liquid preset"
```

---

## Task 5: Generalise the single-position-cap opt-in

**Files:**
- Modify: `backend/engines/backtest_engine.py:236-250`
- Test: `backend/tests/test_backtest_engine_single_position_cap.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_instance_single_position_pct(conn, instance_doc)` now honours an
  entry whose config has `honour_single_position_cap` truthy, in addition to
  `strategy_x_enabled`. Signature and return type unchanged
  (`float | None`, bounded to `(0, 1]`).

`broker_max_single_position_pct` is honoured today only when `strategy_x_enabled`
is truthy, so Strategy XS's 0.65 has always been inert. EB sets 0.95 and
`honour_single_position_cap: true`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_backtest_engine_single_position_cap.py`:

```python
"""`honour_single_position_cap` generalises the cap opt-in beyond Strategy X.

The env var it produces is process-wide inside the container, so the opt-in must
still require an ENABLED entry — otherwise a dormant lane would lift the broker's
15% real-money failsafe for every sibling in the same document.
"""
from __future__ import annotations

import os

import pytest


def _engine():
    original_cwd = os.getcwd()
    try:
        from engines import backtest_engine as engine
    finally:
        os.chdir(original_cwd)
    return engine


def _doc(**config):
    return {"id": 200, "strategies": [{"strategy": "strategy_eb",
                                       "config": dict(config)}]}


def _pct(monkeypatch, doc):
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: doc)
    return engine._instance_single_position_pct(None, {"strategy_id": 200})


def test_the_new_opt_in_honours_the_cap_without_strategy_x(monkeypatch):
    assert _pct(monkeypatch, _doc(broker_max_single_position_pct=0.95,
                                  honour_single_position_cap=True)) == 0.95


@pytest.mark.parametrize("raw", ["true", "True", "yes", "on", "1"])
def test_a_string_opt_in_is_truthy(monkeypatch, raw):
    assert _pct(monkeypatch, _doc(broker_max_single_position_pct=0.95,
                                  honour_single_position_cap=raw)) == 0.95


@pytest.mark.parametrize("raw", [False, "false", "no", "0", "", None])
def test_a_falsy_opt_in_leaves_the_failsafe_alone(monkeypatch, raw):
    assert _pct(monkeypatch, _doc(broker_max_single_position_pct=0.95,
                                  honour_single_position_cap=raw)) is None


def test_the_strategy_x_opt_in_is_unchanged(monkeypatch):
    assert _pct(monkeypatch, _doc(broker_max_single_position_pct=0.95,
                                  strategy_x_enabled=True)) == 0.95


def test_neither_opt_in_means_the_key_is_still_inert(monkeypatch):
    """Strategy XS's 0.65 has been inert since it shipped; that stays true
    until it declares the new key."""
    assert _pct(monkeypatch, _doc(broker_max_single_position_pct=0.65)) is None


def test_the_bounds_still_apply_under_the_new_opt_in(monkeypatch):
    for raw in (0, -1, 2.0, True, "abc", "", None):
        assert _pct(monkeypatch, _doc(broker_max_single_position_pct=raw,
                                      honour_single_position_cap=True)) is None, raw
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_backtest_engine_single_position_cap.py`
Expected: FAIL — the first five assertions return `None` because only
`strategy_x_enabled` is consulted.

- [ ] **Step 3: Generalise the check**

`backend/engines/backtest_engine.py`, replacing lines 243-247:

```python
            # The declaring entry must ALSO be enabled. The env var is
            # process-wide inside the container, not lane-scoped, so honouring
            # the key on a disabled entry would lift the broker failsafe from
            # 15% to 95% for every sibling strategy in the same document while
            # the strategy that asked for it does nothing.
            #
            # `honour_single_position_cap` generalises what was a
            # strategy_x-only opt-in. Strategy XS declared a 0.65 cap and it
            # was silently inert for that reason; a strategy that needs a
            # >15% position now says so explicitly instead of having to
            # impersonate Strategy X.
            def _truthy(value):
                if isinstance(value, bool):
                    return value
                return str(value or "").strip().lower() in (
                    "1", "true", "yes", "on")

            if not (_truthy(cfg.get("honour_single_position_cap", False))
                    or _truthy(cfg.get("strategy_x_enabled", False))):
                continue
```

Update the docstring paragraph at `:220-224` to name both keys.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_backtest_engine_single_position_cap.py backend/tests/test_backtest_single_position_cap_env.py`
Expected: PASS. The existing file must stay green — its `_strategy_doc` helper
sets `strategy_x_enabled: True`, which the new condition still accepts.

- [ ] **Step 5: Commit**

```bash
git add backend/engines/backtest_engine.py backend/tests/test_backtest_engine_single_position_cap.py
git commit -m "feat(engine): honour_single_position_cap generalises the cap opt-in"
```

---

## Task 6: Live daily-bar carrier for equity run_once strategies

**Files:**
- Create: `backend/live_equity_bars.py`
- Modify: `backend/broker.py:14121-14195` (the live `_rr_data` branch)
- Test: `backend/tests/test_live_equity_bars.py`

**Interfaces:**
- Consumes: `broker._strategy_eb_universe_symbols` (Task 3).
- Produces:
  - `live_equity_bars.LOOKBACK_DAYS_DEFAULT: int` (= 400)
  - `live_equity_bars.lookback_start(now_utc: datetime, lookback_days: int) -> datetime`
  - `live_equity_bars.build_live_equity_data(fetch_bars, symbols, now_utc, lookback_days=LOOKBACK_DAYS_DEFAULT, last_good=None, log=None) -> dict | None`
    where `fetch_bars(symbols: list[str], start: datetime, end: datetime) -> Mapping | None`.
    Returns `{SYM: [bar dicts]}`; `None` ONLY when the fetch failed outright and
    there is no last-good snapshot.

Live passes `data=None` to `run_run_once_strategies` today — only BACKTEST
builds a bars history — so Strategy X and XS both refuse to trade live. This is
the equity mirror of `backend/live_crypto_bars.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_live_equity_bars.py`:

```python
"""The live equity bar carrier: stale bars beat a blind strategy."""
import datetime
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_equity_bars import (  # noqa: E402
    LOOKBACK_DAYS_DEFAULT,
    build_live_equity_data,
    lookback_start,
)

NOW = datetime.datetime(2026, 6, 3, 20, 0, tzinfo=datetime.timezone.utc)
UNIVERSE = ["QQQ", "TQQQ", "SPY", "BIL"]


def bar(day, close):
    return {"t": f"2026-06-{day:02d}T05:00:00+00:00", "c": close}


def full(symbols=UNIVERSE):
    return {s: [bar(1, 100.0), bar(2, 101.0)] for s in symbols}


def test_the_window_covers_the_full_lookback():
    start = lookback_start(NOW, 400)
    assert (NOW - start).days == 400
    assert LOOKBACK_DAYS_DEFAULT == 400


def test_a_clean_fetch_is_returned_per_symbol():
    got = build_live_equity_data(lambda s, a, b: full(), UNIVERSE, NOW)
    assert set(got) == set(UNIVERSE)
    assert got["QQQ"][-1]["c"] == 101.0


def test_symbols_are_normalised_deduplicated_and_sorted_for_the_fetch():
    seen = {}

    def fetch(symbols, start, end):
        seen["symbols"] = list(symbols)
        return full()

    build_live_equity_data(fetch, [" spy ", "SPY", "qqq"], NOW)
    assert seen["symbols"] == ["QQQ", "SPY"]


def test_an_empty_universe_fetches_nothing():
    def fetch(*_a):
        raise AssertionError("must not fetch")

    assert build_live_equity_data(fetch, [], NOW) == {}


def test_one_empty_symbol_is_backfilled_from_last_good():
    """A transient per-symbol hiccup must not blind the vol transform."""
    partial = full()
    partial["TQQQ"] = []
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW,
                                 last_good={"TQQQ": [bar(1, 55.0)]})
    assert got["TQQQ"][-1]["c"] == 55.0
    assert got["QQQ"][-1]["c"] == 101.0


def test_one_empty_symbol_with_no_last_good_is_simply_empty():
    partial = full()
    partial["BIL"] = []
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW)
    assert got["BIL"] == []


def test_a_total_failure_falls_back_to_the_whole_last_good_snapshot():
    snapshot = full()
    got = build_live_equity_data(lambda s, a, b: None, UNIVERSE, NOW,
                                 last_good=snapshot)
    assert got == snapshot


def test_a_raising_fetch_is_caught_and_falls_back():
    def boom(*_a):
        raise RuntimeError("alpaca 502")

    snapshot = full()
    assert build_live_equity_data(boom, UNIVERSE, NOW,
                                  last_good=snapshot) == snapshot


def test_a_total_failure_with_no_last_good_returns_none():
    """None means: SKIP the tick's strategies. Running them with no data is how
    a held position gets blind-exited."""
    assert build_live_equity_data(lambda s, a, b: None, UNIVERSE, NOW) is None
    assert build_live_equity_data(lambda s, a, b: {}, UNIVERSE, NOW) is None


def test_the_broker_wires_the_carrier_into_the_live_equity_branch():
    """A source assertion: the hook is inline in a 4,000-line function."""
    source = open(os.path.join(_backend, "broker.py")).read()
    assert "build_live_equity_data" in source
    assert "_live_equity_bars_last_good" in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_live_equity_bars.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_equity_bars'`.

- [ ] **Step 3: Write the module**

Create `backend/live_equity_bars.py`:

```python
"""Live-mode daily bars for equity run_once strategies.

WHY: the live main loop passes ``data=None`` to ``run_run_once_strategies`` —
only BACKTEST builds a bars history. Strategy X, XS and EB all read daily closes
from ``data``, so live they see nothing and correctly REFUSE to trade. This
module builds the per-tick ``data`` dict for equity instances from a
broker-injected fetch function.

Import-safe on purpose (no broker import): broker.py is a script with side
effects (argparse at module scope SystemExits under pytest), so the pure logic
lives here where tests can reach it — same pattern as live_crypto_bars.

Safety contract:
- Returns {symbol: [bar, ...]} on success (a symbol may be empty when there is
  nothing yet — the strategy refuses on its own and that is the correct answer).
- A symbol whose fetch came back empty is backfilled from ``last_good``: stale
  bars beat blinding a strategy that is holding a 3x fund.
- Returns ``None`` ONLY when the fetch failed outright and there is no last-good
  snapshot. The caller must then SKIP the tick's run_once strategies.
"""

from __future__ import annotations

import datetime
from typing import Callable, Iterable, Mapping, Optional

#: 400 calendar days ~= 275 trading sessions: more than the 60-bar slow vol
#: window and the 200-bar filters other strategies on this path use, with room
#: for holidays. Cheap — one 1Day request per symbol per tick.
LOOKBACK_DAYS_DEFAULT = 400


def lookback_start(now_utc: datetime.datetime,
                   lookback_days: int = LOOKBACK_DAYS_DEFAULT
                   ) -> datetime.datetime:
    return now_utc - datetime.timedelta(days=max(1, int(lookback_days)))


def build_live_equity_data(
    fetch_bars: Callable[[list, datetime.datetime, datetime.datetime],
                         Optional[Mapping]],
    symbols: Iterable[str],
    now_utc: datetime.datetime,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
    last_good: Optional[Mapping] = None,
    log: Optional[Callable[[str, str], None]] = None,
) -> Optional[dict]:
    """Assemble the live ``data`` dict for equity run_once strategies."""

    def _log(message, color="yellow"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    syms, seen = [], set()
    for raw in (symbols or []):
        upper = str(raw or "").strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            syms.append(upper)
    if not syms:
        return {}
    syms.sort()

    start = lookback_start(now_utc, lookback_days)
    try:
        fetched = fetch_bars(syms, start, now_utc)
    except Exception as exc:
        _log(f"Live equity bars fetch raised: {type(exc).__name__}: {exc}",
             "red")
        fetched = None

    if isinstance(fetched, Mapping) and any(fetched.get(s) for s in syms):
        out, stale = {}, []
        for symbol in syms:
            bars = list(fetched.get(symbol) or [])
            if not bars and last_good and last_good.get(symbol):
                bars = list(last_good[symbol])
                stale.append(symbol)
            out[symbol] = bars
        if stale:
            _log("Live equity bars: empty fetch for " + ", ".join(stale)
                 + " — reusing last-good bars (stale) rather than blinding a "
                   "strategy that is holding a levered position.", "yellow")
        return out

    if last_good:
        _log("Live equity bars fetch FAILED — reusing the last-good snapshot "
             "(stale) for this tick.", "red")
        return dict(last_good)
    _log("Live equity bars fetch FAILED with no last-good snapshot — caller "
         "must skip strategies this tick.", "red")
    return None
```

- [ ] **Step 4: Hook it into the broker**

`backend/broker.py`: the crypto branch begins at `:14123`
(`if _is_crypto_instance_runtime() and _tick_mode != "IDLE":`) and its
`except` block ends at `:14195`. Append a sibling `elif` immediately after,
before the `_PRICE_FETCH_EXECUTOR.submit` at `:14196`:

```python
                                # 2026-08-27: LIVE daily bars for equity
                                # run_once strategies. Until now live passed
                                # data=None and strategy_x / _xs / _eb all
                                # correctly REFUSED to trade — a strategy that
                                # cannot see its own filter must do nothing, so
                                # the live lane was inert by construction.
                                # Scoped to instances that actually declare an
                                # EB universe, so every other equity instance
                                # stays byte-identical (data=None).
                                elif _tick_mode != "IDLE":
                                    try:
                                        _eb_syms = _strategy_eb_universe_symbols(
                                            _cached_strategies)
                                    except Exception:
                                        _eb_syms = []
                                    if _eb_syms:
                                        try:
                                            from live_equity_bars import (
                                                build_live_equity_data as _leb_build,
                                            )

                                            def _leb_fetch(_syms, _start, _end,
                                                           _k=_strat_data_key,
                                                           _s=_strat_data_secret):
                                                _db = None
                                                try:
                                                    _db = get_conn()
                                                except Exception:
                                                    _db = None
                                                try:
                                                    return fetch_alpaca_historical_bars(
                                                        _syms, _start, _end, _k, _s,
                                                        timeframe="1Day",
                                                        db_conn=_db, feed=data_feed,
                                                    )
                                                finally:
                                                    try:
                                                        if _db is not None:
                                                            _db.close()
                                                    except Exception:
                                                        pass

                                            _rr_data = _leb_build(
                                                _leb_fetch, _eb_syms,
                                                datetime.datetime.now(
                                                    datetime.timezone.utc),
                                                last_good=globals().get(
                                                    "_live_equity_bars_last_good"),
                                                log=_log,
                                            )
                                            if _rr_data:
                                                globals()["_live_equity_bars_last_good"] = _rr_data
                                            elif _rr_data is None:
                                                _rr_specs_eff = []
                                                _log(
                                                    "Live equity bars unavailable (fetch "
                                                    "failed, no last-good) — skipping "
                                                    "strategies this tick; will retry.",
                                                    "red",
                                                )
                                        except Exception as _leb_e:
                                            _rr_data = None
                                            _rr_specs_eff = []
                                            _log(
                                                f"Live equity bars error: "
                                                f"{type(_leb_e).__name__}: {_leb_e} — "
                                                f"skipping strategies this tick.",
                                                "red",
                                            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_live_equity_bars.py`
Expected: PASS, 10 passed.

- [ ] **Step 6: Prove broker.py still parses**

Run: `python3 -c "import ast; ast.parse(open('backend/broker.py').read()); print('ok')"`
Expected: `ok`. (broker.py cannot be imported under test; this is the only
syntax check available.)

- [ ] **Step 7: Commit**

```bash
git add backend/live_equity_bars.py backend/broker.py backend/tests/test_live_equity_bars.py
git commit -m "feat(live): daily bar carrier so equity run_once strategies can see live"
```

---

## Task 7: Per-document live risk limits

**Files:**
- Modify: `backend/live_risk_state.py:18-30` (constants + `RiskLimits`),
  `initialize_risk_state:308-326`, `initialize_live_risk_state:328-391`,
  `evaluate_drawdown:393-450`
- Modify: `backend/broker.py` — new `_strategy_eb_risk_limits` beside
  `_strategy_eb_universe_symbols`; call sites at `:9077` (`initialize_live_risk_state`),
  `:9103` (`initialize_risk_state`), `:9127` (`evaluate_drawdown`); inline
  leveraged set at `:9236-9241`
- Test: `backend/tests/test_live_risk_limits.py`

**Interfaces:**
- Consumes: `strategy_eb.DEFAULTS` (Task 1).
- Produces in `live_risk_state`:
  - `@dataclass(frozen=True) class RiskLimits` with fields
    `max_order_fraction, max_symbol_fraction, max_leveraged_fraction, soft,
    hard, kill` — all `Decimal`, validated in `__post_init__`.
  - `DEFAULT_RISK_LIMITS: RiskLimits` (exactly today's module constants:
    0.10 / 0.20 / 0.10 and 0.05 / 0.09 / 0.12).
  - `initialize_risk_state(instance_id, account_id, equity, observed_at, *, limits=DEFAULT_RISK_LIMITS)`
  - `initialize_live_risk_state(..., limits=DEFAULT_RISK_LIMITS)`
  - `evaluate_drawdown(state, fresh_equity, observed_at, *, soft_threshold=None, hard_threshold=None, kill_threshold=None, limits=DEFAULT_RISK_LIMITS)`
  - `DEFAULT_LEVERAGED_SYMBOLS` gains `"QLD"`.
- Produces in `broker`: `_strategy_eb_risk_limits(cached_strategies) -> RiskLimits | None`
  (`None` when no enabled `strategy_eb` lane exists — every other document keeps
  the module defaults).

`evaluate_drawdown` re-derives the exposure caps from the module constants on
EVERY refresh (`:432-439`), so an override applied only at bootstrap is
overwritten each tick. That is why the limits must be threaded into both.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_live_risk_limits.py`:

```python
"""Per-strategy-document live risk limits.

A strategy designed to ride a -30% drawdown cannot live under a 5% soft
buy-freeze, and a 65%-of-NAV core cannot be built under a 10% per-order cap. The
gate keeps BLOCKING rather than clipping; the cap is simply set to what the
strategy asks for. Every other document must keep the module defaults.
"""
import ast
import datetime
import os
import sys
from decimal import Decimal

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_risk_state import (  # noqa: E402
    DEFAULT_LEVERAGED_SYMBOLS,
    DEFAULT_RISK_LIMITS,
    RiskLimits,
    evaluate_drawdown,
    initialize_risk_state,
)

T0 = datetime.datetime(2026, 6, 3, 14, 0, tzinfo=datetime.timezone.utc)
T1 = datetime.datetime(2026, 6, 3, 15, 0, tzinfo=datetime.timezone.utc)
EB = RiskLimits(max_order_fraction="0.70", max_symbol_fraction="0.70",
                max_leveraged_fraction="0.70", soft="0.25", hard="0.35",
                kill="0.45")


def test_the_module_defaults_are_exactly_todays_numbers():
    assert DEFAULT_RISK_LIMITS.max_order_fraction == Decimal("0.10")
    assert DEFAULT_RISK_LIMITS.max_symbol_fraction == Decimal("0.20")
    assert DEFAULT_RISK_LIMITS.max_leveraged_fraction == Decimal("0.10")
    assert DEFAULT_RISK_LIMITS.soft == Decimal("0.05")
    assert DEFAULT_RISK_LIMITS.hard == Decimal("0.09")
    assert DEFAULT_RISK_LIMITS.kill == Decimal("0.12")


def test_bootstrap_without_limits_is_unchanged():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0)
    assert state.max_order_notional == Decimal("600.00")
    assert state.max_symbol_notional == Decimal("1200.00")
    assert state.max_leveraged_notional == Decimal("600.00")


def test_bootstrap_with_eb_limits_uses_them():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0, limits=EB)
    assert state.max_order_notional == Decimal("4200.00")
    assert state.max_symbol_notional == Decimal("4200.00")
    assert state.max_leveraged_notional == Decimal("4200.00")


def test_the_override_survives_an_evaluate_drawdown_refresh():
    """The refresh re-derives caps from fractions every tick; an override in
    only one place is overwritten on the next observation."""
    state = initialize_risk_state("i", "a", Decimal("6000"), T0, limits=EB)
    refreshed = evaluate_drawdown(state, Decimal("6000"), T1, limits=EB)
    assert refreshed.max_order_notional == Decimal("4200.00")
    assert refreshed.max_leveraged_notional == Decimal("4200.00")


def test_a_document_with_no_limits_still_refreshes_to_module_defaults():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0)
    refreshed = evaluate_drawdown(state, Decimal("5000"), T1)
    assert refreshed.max_symbol_notional == Decimal("1000.00")


def test_the_eb_drawdown_ladder_replaces_the_module_one():
    """-26% is 'kill' under the module defaults and merely 'soft' under EB's."""
    state = initialize_risk_state("i", "a", Decimal("10000"), T0, limits=EB)
    refreshed = evaluate_drawdown(state, Decimal("7400"), T1, limits=EB)
    assert refreshed.level == "soft"
    default_state = initialize_risk_state("i", "a", Decimal("10000"), T0)
    assert evaluate_drawdown(default_state, Decimal("7400"), T1).level == "kill"


def test_explicit_thresholds_still_win_over_limits():
    """Every existing caller passes soft/hard/kill positionally-by-keyword."""
    state = initialize_risk_state("i", "a", Decimal("10000"), T0)
    got = evaluate_drawdown(state, Decimal("9000"), T1,
                            soft_threshold=Decimal("0.30"),
                            hard_threshold=Decimal("0.40"),
                            kill_threshold=Decimal("0.50"))
    assert got.level == "normal"


@pytest.mark.parametrize("kwargs", [
    {"soft": "0.35", "hard": "0.25"},          # not increasing
    {"kill": "1.5"},                            # outside (0, 1)
    {"max_order_fraction": "0"},                # a zero cap is not a cap
    {"max_symbol_fraction": "1.5"},
    {"soft": "nope"},
])
def test_a_malformed_limit_set_is_refused_at_construction(kwargs):
    with pytest.raises(ValueError):
        RiskLimits(**kwargs)


def test_qld_is_a_leveraged_symbol():
    assert "QLD" in DEFAULT_LEVERAGED_SYMBOLS
    assert {"TQQQ", "SQQQ", "SPXU", "UPRO", "SOXL", "SOXS"} <= DEFAULT_LEVERAGED_SYMBOLS


def test_the_broker_no_longer_inlines_its_own_leveraged_set():
    """The inline literal at broker.py:9236-9241 could not gain QLD without
    someone remembering two places."""
    source = open(os.path.join(_backend, "broker.py")).read()
    assert '"SQQQ", "TQQQ", "SPXU", "UPRO", "SOXL", "SOXS"' not in source
    assert "DEFAULT_LEVERAGED_SYMBOLS" in source


def _extract(*names):
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    assert wanted, f"none of {names} found in broker.py"
    ns = {"_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), broker, "exec"), ns)
    return ns


def test_the_broker_reads_the_eb_lanes_limits():
    ns = _extract("_strategy_eb_risk_limits")
    limits = ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}])
    assert limits == EB


def test_no_eb_lane_means_no_override():
    ns = _extract("_strategy_eb_risk_limits")
    for specs in (None, [], [{"strategy": "graph_nexus_analysis", "config": {}}],
                  [{"strategy": "strategy_eb",
                    "config": {"strategy_eb_enabled": False}}]):
        assert ns["_strategy_eb_risk_limits"](specs) is None, specs


def test_a_malformed_eb_limit_set_degrades_to_the_module_defaults():
    """A typo in a config value must not take the live loop down; it must fall
    back to the TIGHTER module defaults, never to no limit."""
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "config": {"strategy_eb_enabled": True,
                     "live_soft_drawdown": 0.9}}]) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_live_risk_limits.py`
Expected: FAIL — `ImportError: cannot import name 'RiskLimits'`.

- [ ] **Step 3: Add `RiskLimits` to `live_risk_state.py`**

After `DEFAULT_LEVERAGED_SYMBOLS` (`:28-30`), and add `"QLD"` to that frozenset:

```python
DEFAULT_LEVERAGED_SYMBOLS = frozenset(
    {"SQQQ", "TQQQ", "QLD", "SPXU", "UPRO", "SOXL", "SOXS"}
)


@dataclass(frozen=True)
class RiskLimits:
    """One strategy document's live risk envelope.

    Exists because a single set of module constants cannot serve two strategies
    with different designs: a 5% soft buy-freeze is correct for a diversified
    stock book and fatal for a vol-targeted levered core, which is BUILT to sit
    through a -30% drawdown. The gate still BLOCKS rather than clips; only the
    number it blocks against moves.

    Validated at construction, so a config typo fails where an operator can see
    it rather than silently disarming a real-money failsafe.
    """

    max_order_fraction=DEFAULT_MAX_ORDER_FRACTION
    max_symbol_fraction=DEFAULT_MAX_SYMBOL_FRACTION
    max_leveraged_fraction=DEFAULT_MAX_LEVERAGED_FRACTION
    soft=DEFAULT_SOFT_DRAWDOWN
    hard=DEFAULT_HARD_DRAWDOWN
    kill=DEFAULT_KILL_DRAWDOWN

    def __post_init__(self):
        for name in ("max_order_fraction", "max_symbol_fraction",
                     "max_leveraged_fraction", "soft", "hard", "kill"):
            value = _decimal(getattr(self, name), name, positive=True)
            if not (Decimal("0") < value <= Decimal("1")):
                raise ValueError(f"{name} must be in (0, 1]")
            object.__setattr__(self, name, value)
        if not (Decimal("0") < self.soft < self.hard < self.kill < Decimal("1")):
            raise ValueError("drawdown thresholds must be increasing in (0, 1)")


#: Exactly today's behaviour for every document that declares nothing.
DEFAULT_RISK_LIMITS = RiskLimits()
```

**Annotation note:** the six fields above must carry real annotations for
`@dataclass` to see them. Write them as, e.g.,
`max_order_fraction: Decimal = DEFAULT_MAX_ORDER_FRACTION` — the values are
coerced in `__post_init__`, so a `str` or `float` is accepted at the call site
while the stored field is always a `Decimal`.

- [ ] **Step 4: Thread the limits through**

`initialize_risk_state` (`:308-326`):
```python
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
        ...
        max_order_notional=equity_value * limits.max_order_fraction,
        max_symbol_notional=equity_value * limits.max_symbol_fraction,
        max_leveraged_notional=equity_value * limits.max_leveraged_fraction,
    )
```

`initialize_live_risk_state` (`:328-391`): add the same
`limits: RiskLimits = DEFAULT_RISK_LIMITS` keyword-only parameter and pass it on
in its final `return initialize_risk_state(...)` at `:390`.

`evaluate_drawdown` (`:393-450`):
```python
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
    ...
    soft = _decimal(limits.soft if soft_threshold is None else soft_threshold,
                    "soft_threshold")
    hard = _decimal(limits.hard if hard_threshold is None else hard_threshold,
                    "hard_threshold")
    kill = _decimal(limits.kill if kill_threshold is None else kill_threshold,
                    "kill_threshold")
```
and in the cap-rescale loop (`:433-436`) replace the three module constants with
`limits.max_order_fraction`, `limits.max_symbol_fraction`,
`limits.max_leveraged_fraction`. Extend the comment above it: *"The fractions
come from the strategy document's own `RiskLimits`, which is why they must be
passed here as well as at bootstrap — this loop reapplies them on every
observation, so an override in only one place is overwritten each tick."*

The three explicit-threshold keywords stay for backward compatibility: existing
callers and tests pass them, and `None` now means "use the limits".

- [ ] **Step 5: Wire the broker**

Add after `_strategy_eb_universe_symbols` in `backend/broker.py`:

```python
def _strategy_eb_risk_limits(cached_strategies):
    """The enabled strategy_eb lane's live risk envelope, or None.

    None means "this document declares nothing", and every other document keeps
    live_risk_state's module defaults untouched. A malformed value also returns
    None — degrading to the TIGHTER default is the only safe direction, and a
    config typo must never take the live loop down.
    """
    try:
        from live_risk_state import RiskLimits
        from strategy_eb import DEFAULTS as _EB_DEFAULTS
    except Exception:
        return None
    try:
        for spec in (cached_strategies or []):
            if not isinstance(spec, dict):
                continue
            if str(spec.get("strategy") or "").strip().lower() not in {
                    "strategy_eb", "strategyeb"}:
                continue
            merged = {**_EB_DEFAULTS, **(spec.get("config") or {})}
            if not merged.get("strategy_eb_enabled", False):
                continue
            return RiskLimits(
                max_order_fraction=merged["live_max_order_fraction"],
                max_symbol_fraction=merged["live_max_symbol_fraction"],
                max_leveraged_fraction=merged["live_max_leveraged_fraction"],
                soft=merged["live_soft_drawdown"],
                hard=merged["live_hard_drawdown"],
                kill=merged["live_kill_drawdown"],
            )
    except Exception as _eb_exc:
        try:
            _log(f"[strategy_eb] live risk limits ignored ({_eb_exc}); "
                 "using the module defaults", "yellow")
        except Exception:
            pass
        return None
    return None
```

Then:
1. `:9077` — `initialize_live_risk_state(..., trading_blocked=_blocked, limits=_strategy_eb_risk_limits(_cached_strategies) or DEFAULT_RISK_LIMITS)`.
2. `:9103` — `initialize_risk_state(..., limits=_strategy_eb_risk_limits(_cached_strategies) or DEFAULT_RISK_LIMITS)`.
3. `:9127` — `evaluate_drawdown(_live_risk_state, equity, observed_at, limits=_strategy_eb_risk_limits(_cached_strategies) or DEFAULT_RISK_LIMITS)`.
   Import `DEFAULT_RISK_LIMITS` alongside `evaluate_drawdown` at `:9120`, and
   alongside `initialize_risk_state` / `initialize_live_risk_state` wherever
   those are imported in `_initialize_live_risk_authority`.
4. `:9233-9241` — replace the inline literal set:
   ```python
    if _live_risk_state is not None:
        from live_risk_state import DEFAULT_LEVERAGED_SYMBOLS
        max_order_notional = _live_risk_state.max_order_notional
        position_limit = (
            _live_risk_state.max_leveraged_notional
            if symbol in DEFAULT_LEVERAGED_SYMBOLS
            else _live_risk_state.max_symbol_notional
        )
   ```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_live_risk_limits.py`
Expected: PASS, 17 passed.

- [ ] **Step 7: Prove the existing risk-state tests still pass**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests -k "risk_state or live_order or gate"`
Expected: PASS. Any failure means a default moved — fix the implementation.

- [ ] **Step 8: Commit**

```bash
git add backend/live_risk_state.py backend/broker.py backend/tests/test_live_risk_limits.py
git commit -m "feat(live): per-document RiskLimits, threaded through refresh; QLD is leveraged"
```

---

## Task 8: Local harness `scripts/strategy_eb_matrix.py`

**Files:**
- Create: `scripts/strategy_eb_matrix.py`

**Interfaces:**
- Consumes: `strategies.strategy_eb.StrategyEb` and `strategy_eb.DEFAULTS`
  (Tasks 1-2).
- Produces: nothing importable. A CLI that prints a calendar-year table, CAGR,
  max drawdown, Sharpe and one-way turnover for EB and SPY.

This is **not a test** and it is **never the verdict**. Its only job is to show
that the implementation reproduces the research sweep — CAGR ~24%, max drawdown
~−40%, weekly turnover ~250-300%/yr — before an engine run is spent.

- [ ] **Step 1: Write the harness**

```python
#!/usr/bin/env python3
"""Strategy EB over sixteen years, driven through the REAL run_once.

Same convention as `scripts/strategy_xs_matrix.py`: the actual
`StrategyEb.run_once` bar by bar through a minimal emulator, with the strategy's
own point-in-time observations and NEXT-BAR fills.

    python3 scripts/strategy_eb_matrix.py                  # frozen defaults
    python3 scripts/strategy_eb_matrix.py remainder_bil_fraction=1.0
    python3 scripts/strategy_eb_matrix.py rebalance_weekdays=1,3
    EB_COST_BPS=23 python3 scripts/strategy_eb_matrix.py   # cost sensitivity

THIS HARNESS IS NEVER THE VERDICT. Every local harness in this repo over-states:
the Strategy X harness reported +147.6% on a window the engine scored +67.55%.
Its job is to show the implementation reproduces the research sweep (CAGR ~24%,
maxDD ~-40%, turnover ~250-300%/yr) before an engine run is spent.
"""
import os
import sys
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS  # noqa: E402

#: 4.4 bps one-way: the `etf-liquid` engine tier (8.0/2 + 0.1 + 0.3). This
#: harness and the engine must charge the SAME thing or the comparison is
#: meaningless. THE 8 bps SPREAD IS AN ASSUMPTION — see
#: simulated_execution.ETF_LIQUID_EQUITY_COST_MODEL.
COST_BPS = float(os.environ.get("EB_COST_BPS", "4.4"))
BAR_WINDOW = 400
UNIVERSE = ["QQQ", "TQQQ", "QLD", "SPY", "BIL"]


# ── COPY VERBATIM from scripts/strategy_xs_matrix.py, changing nothing ──
# `class Emu` (that file's lines 52-90): cash + share counts, COST_BPS charged
# on every fill. `def load_prices` (lines 92-103), changing only the cache path
# to /tmp/strategy_eb_prices.pkl via the EB_PRICE_CACHE env var and the default
# start to "2010-01-01". `def cagr`, `def maxdd`, `def sharpe`, `def yearly`
# (lines 157-176). They are already correct and already reviewed; retyping them
# is how the two harnesses drift and stop being comparable.


def replay(frame, cfg):
    strat, emu = StrategyEb(), Emu(100_000.0)
    cache, bars = {}, {sym: [] for sym in UNIVERSE}
    equity, dates, orders, pending = [], [], 0, None
    watchlist = [s for s in UNIVERSE if s != "QQQ"]

    for ts, row in frame.iterrows():
        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        prices = {s: float(row[s]) for s in UNIVERSE
                  if pd.notna(row[s]) and float(row[s]) > 0}

        # Fill what the PREVIOUS bar decided, at THIS bar's price. Filling on
        # the close the decision saw is a real lookahead.
        if pending:
            out, sizes = pending
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == -1 and sym in prices:
                    emu.sell(sym, sizes.get(sym, {}).get("sell_fraction", 1.0),
                             prices[sym])
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == 1 and sym in prices:
                    emu.buy(sym, sizes.get(sym, {}).get("buy_cash", 0.0),
                            prices[sym])
            pending = None

        for sym in UNIVERSE:
            if sym in prices:
                bars[sym].append({"t": ts_utc.isoformat(), "c": prices[sym]})
                if len(bars[sym]) > BAR_WINDOW:
                    del bars[sym][0]

        out = strat.run_once(
            watchlist, prices, ts_utc, cfg, {},
            data={s: {"bars": bars[s]} for s in UNIVERSE if bars[s]},
            portfolio_emulator=emu, strategy_cache=cache, mode="backtest")
        if out:
            orders += len([s for s in out if not s.startswith("_")])
            pending = (dict(out), out.get("_nexus_position_sizes", {}))

        equity.append(emu.get_portfolio_value(prices))
        dates.append(ts)

    curve = pd.Series(equity, index=pd.DatetimeIndex(dates))
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
    return curve, {"orders": orders,
                   "turnover_pct_yr": emu.traded / max(curve.mean(), 1e-9)
                   / years * 100}


def main():
    overrides = {}
    for arg in sys.argv[1:]:
        key, _, value = arg.partition("=")
        if "," in value:
            overrides[key] = [int(v) for v in value.split(",") if v.strip()]
        elif value.lower() in ("true", "false"):
            overrides[key] = value.lower() == "true"
        else:
            try:
                overrides[key] = float(value) if "." in value else int(value)
            except ValueError:
                overrides[key] = value

    frame = load_prices()
    cfg = {**DEFAULTS, "strategy_eb_enabled": True, **overrides}
    warm = int(cfg["min_history_bars"]) + 5     # first decidable session + slack

    curve, stats = replay(frame, cfg)
    eb = curve.iloc[warm:]
    spy = frame["SPY"].loc[eb.index]

    print("=" * 78)
    print(f"STRATEGY EB  {eb.index[0].date()} -> {eb.index[-1].date()}"
          f"   cost {COST_BPS:.1f} bps"
          + (f"   overrides: {overrides}" if overrides else ""))
    print("=" * 78)
    print(f"{'':<10}{'CAGR':>9}{'maxDD':>9}{'Sharpe':>8}{'turnover':>11}")
    print(f"{'EB':<10}{cagr(eb):>8.2f}%{maxdd(eb):>8.2f}%{sharpe(eb):>8.2f}"
          f"{stats['turnover_pct_yr']:>10.0f}%")
    print(f"{'SPY':<10}{cagr(spy):>8.2f}%{maxdd(spy):>8.2f}%{sharpe(spy):>8.2f}"
          f"{0:>10.0f}%")
    print(f"orders: {stats['orders']}")

    eb_yr, spy_yr = yearly(eb), yearly(spy)
    print(f"\n{'year':<6}{'EB':>9}{'SPY':>9}   verdict")
    for i, stamp in enumerate(eb_yr.index):
        x, b = eb_yr.iloc[i], spy_yr.iloc[i]
        marks = [m for m, ok in (("negative", x < 0), ("below SPY", x < b)) if ok]
        print(f"{stamp.year:<6}{x:>8.1f}%{b:>8.1f}%   "
              + (", ".join(marks) if marks else "ok"))

    # Reproduction check against the research sweep, NOT an acceptance gate.
    print("\nREPRODUCES THE RESEARCH SWEEP?")
    for name, ok in (
        ("CAGR in 20-28%", 20.0 <= cagr(eb) <= 28.0),
        ("maxDD in -33..-45%", -45.0 <= maxdd(eb) <= -33.0),
        ("turnover in 180-400%/yr",
         180.0 <= stats["turnover_pct_yr"] <= 400.0),
    ):
        print(f"  {'yes ' if ok else 'NO  '} {name}")
    print("\nThis harness OVER-STATES. The engine is the verdict.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it on the defaults**

Run: `python3 scripts/strategy_eb_matrix.py`
Expected: a table; the three reproduction checks should read `yes`. If any reads
`NO`, the implementation does not reproduce the sweep — debug the
implementation, do NOT move the bounds.

- [ ] **Step 3: Run the BIL dial to confirm the trade-off is present**

Run: `python3 scripts/strategy_eb_matrix.py remainder_bil_fraction=1.0`
Expected: CAGR roughly 8pp lower and 2022 roughly −12% instead of roughly −30%,
matching the research memo's section 5.

- [ ] **Step 4: Commit**

```bash
git add scripts/strategy_eb_matrix.py
git commit -m "feat(strategy-eb): local yfinance harness reproducing the research sweep"
```

---

## Task 9: Deployment bootstrap and the frozen acceptance gate

**Files:**
- Create: `scripts/strategy_eb_bootstrap.py`
- Create: `scripts/strategy_eb_gate.py`

**Interfaces:**
- Consumes: `strategy_eb.DEFAULTS` (Task 1), `scripts/_api.call(method, path, body=None)`
  which returns `(status_code, parsed_json)`.
- Produces: two CLIs. Neither is imported by anything.
  - `strategy_eb_bootstrap.py show|create|verify`
  - `strategy_eb_gate.py <backtest_id>` — exit code 0 when all of G1-G6 pass,
    1 otherwise.

- [ ] **Step 1: Write the bootstrap script**

```python
#!/usr/bin/env python3
"""Create the Strategy EB document and instance through the API, and verify.

    python3 scripts/strategy_eb_bootstrap.py create
    python3 scripts/strategy_eb_bootstrap.py show
    python3 scripts/strategy_eb_bootstrap.py verify <doc_id>

Following `_sx_doc198_patch.py`: write, RE-FETCH, and verify every key round-
trips. A silent schema coercion on save is how a Strategy XS edit reverted
(schema.strategy CapitalCase vs the lower-case id) and nobody noticed.

The document carries ONE lane. `broker_max_single_position_pct` becomes a
process-wide env var inside the backtest container, so a second enabled lane
here would inherit a 95% cap it was never measured under.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from _api import call  # noqa: E402
from strategy_eb import DEFAULTS  # noqa: E402

DOC_NAME = "Strategy EB"
INSTANCE_ID = "strategy-eb"
#: Daily. The strategy decides once per NY session; a finer granularity only
#: multiplies evaluations that the once-per-session guard then discards.
GRANULARITY = "86400"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ"]

LANE = {
    "strategy": "strategy_eb",
    "weight": 1.0,
    "execution_position": 10,
    "decision_phase": "pre",
    "execution_scope": "run_once",
    "conditions": {},
    # Enabled at creation so a backtest of this document actually runs it. The
    # rollback is setting this false; the gate in section 11 of the spec decides
    # whether it stays true for LIVE.
    "config": {**DEFAULTS, "strategy_eb_enabled": True},
}


def _ok(status, *allowed):
    return status in (allowed or (200, 201))


def create():
    status, doc = call("POST", "/strategies",
                       {"name": DOC_NAME, "strategies": [LANE]})
    if not _ok(status, 200, 201):
        raise SystemExit(f"POST /strategies -> {status}: {doc}")
    doc_id = doc.get("id") if isinstance(doc, dict) else None
    if doc_id is None:
        # Some deployments return {"ok":..} only; find it by name.
        _, listing = call("GET", "/strategies")
        matches = [d for d in (listing or []) if d.get("name") == DOC_NAME]
        if not matches:
            raise SystemExit(f"created but not findable by name: {doc}")
        doc_id = matches[-1]["id"]
    print(f"strategy document {doc_id} — {DOC_NAME}")

    status, inst = call("POST", "/instances", {
        "id": INSTANCE_ID, "name": DOC_NAME, "strategy_id": int(doc_id),
        "granularity": GRANULARITY, "run_command": False, "stocks": STOCKS,
    })
    if not _ok(status, 200, 201):
        raise SystemExit(f"POST /instances -> {status}: {inst}")

    status, linked = call("POST", f"/instances/{INSTANCE_ID}/link-strategy",
                          {"strategy_id": int(doc_id)})
    if not _ok(status, 200, 201):
        raise SystemExit(f"link-strategy -> {status}: {linked}")
    print(f"instance {INSTANCE_ID} linked to document {doc_id}")
    verify(doc_id)
    return doc_id


def verify(doc_id):
    status, doc = call("GET", f"/strategies/{doc_id}")
    if status != 200:
        raise SystemExit(f"GET /strategies/{doc_id} -> {status}")
    lanes = doc.get("strategies") or []
    if len(lanes) != 1:
        raise SystemExit(f"expected exactly one lane, saw {len(lanes)}: "
                         "a second lane would inherit this document's 95% "
                         "single-position cap")
    lane = lanes[0]
    if str(lane.get("strategy")) != "strategy_eb":
        raise SystemExit(f"lane id came back as {lane.get('strategy')!r}; the "
                         "broker resolves the class from this string")
    saved = lane.get("config") or {}
    drift = {k: (v, saved.get(k)) for k, v in LANE["config"].items()
             if saved.get(k) != v}
    if drift:
        raise SystemExit("NOT SAVED as requested:\n"
                         + json.dumps(drift, indent=2, default=str))
    missing = sorted(set(DEFAULTS) - set(saved))
    if missing:
        raise SystemExit(f"keys dropped on save: {missing}")

    status, inst = call("GET", f"/instances/{INSTANCE_ID}")
    if status != 200:
        raise SystemExit(f"GET /instances/{INSTANCE_ID} -> {status}")
    if str(inst.get("strategy_id")) != str(doc_id):
        raise SystemExit(f"instance links strategy_id={inst.get('strategy_id')}, "
                         f"expected {doc_id}")
    print(f"verified: {len(saved)} config keys round-tripped, instance linked")


def show():
    _, listing = call("GET", "/strategies")
    for doc in (listing or []):
        if doc.get("name") == DOC_NAME:
            print(f"doc {doc['id']} — {doc['name']}")
            for lane in doc.get("strategies") or []:
                cfg = lane.get("config") or {}
                print(f"  {lane.get('strategy')} enabled="
                      f"{cfg.get('strategy_eb_enabled')} "
                      f"core={cfg.get('core_symbol')} "
                      f"dial={cfg.get('remainder_bil_fraction')} "
                      f"weekdays={cfg.get('rebalance_weekdays')}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    if action == "create":
        create()
    elif action == "verify":
        verify(sys.argv[2])
    elif action == "show":
        show()
    else:
        raise SystemExit(__doc__)
```

- [ ] **Step 2: Write the gate script**

```python
#!/usr/bin/env python3
"""Evaluate Strategy EB's PRE-REGISTERED acceptance gate on a finished backtest.

    python3 scripts/strategy_eb_gate.py 812345

The gate was frozen in
docs/superpowers/specs/2026-08-27-strategy-eb-design.md section 11 BEFORE any
engine run. It is not re-tuned to pass. If it fails, the strategy ships disabled
with the numbers recorded in DEFAULTS comments, per the XS precedent.

Exit code 0 = all six pass. 1 = any failure.

Data sources, all through the API (the serving truth post-Postgres-cutover;
direct RethinkDB reads are a stale mirror):
  GET /backtests/{id}/graph-data   -> portfolio_value_history + backtest_trades
  GET /backtests/{id}/logs         -> the two silent-failure greps
Each pv snapshot carries {"timestamp", "value", "cash", "positions_snapshot",
"prices"}, so the SPY benchmark comes from the run's OWN price series rather
than from a separate download that could disagree about adjustment.
"""
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _api import call  # noqa: E402

#: The tail/downsample caps in backtest_result_store: pv is downsampled above
#: 3,000 rows and trades are TAILED at 1,000. A daily 2021-11..2026-08 run is
#: ~1,200 pv rows and ~750 trades, so neither should bind — but if trades comes
#: back at exactly the cap, turnover is understated and the run must be read
#: from Postgres instead.
_TRADE_TAIL_CAP = 1000


def _ts(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _cagr(first, last, years):
    if first <= 0 or years <= 0:
        return float("nan")
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _maxdd(series):
    peak, worst = -math.inf, 0.0
    for value in series:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def _monthly_last(points):
    """(month_key, value) for the LAST observation in each calendar month."""
    out = {}
    for stamp, value in points:
        out[(stamp.year, stamp.month)] = value
    return [out[k] for k in sorted(out)]


def main(backtest_id):
    status, data = call("GET", f"/backtests/{backtest_id}/graph-data")
    if status != 200:
        raise SystemExit(f"graph-data -> {status}: {data}")
    pv = data.get("portfolio_value_history") or []
    trades = data.get("backtest_trades") or []
    if len(pv) < 2:
        raise SystemExit("no portfolio value history")

    stamps = [_ts(row["timestamp"]) for row in pv]
    equity = [float(row["value"]) for row in pv]
    years = (stamps[-1] - stamps[0]).days / 365.25

    spy = [(s, float((row.get("prices") or {}).get("SPY") or 0.0))
           for s, row in zip(stamps, pv)]
    spy = [(s, p) for s, p in spy if p > 0]
    if len(spy) < 2:
        raise SystemExit("the run carries no SPY price series; the benchmark "
                         "must come from the run's own prices")

    eb_cagr = _cagr(equity[0], equity[-1], years)
    spy_cagr = _cagr(spy[0][1], spy[-1][1],
                     (spy[-1][0] - spy[0][0]).days / 365.25)
    eb_dd, spy_dd = _maxdd(equity), _maxdd([p for _, p in spy])

    # G3: calendar 2022.
    def _year(points, year):
        inside = [v for s, v in points if s.year == year]
        return (inside[-1] / inside[0] - 1.0) * 100.0 if len(inside) > 1 else None

    eb_2022 = _year(list(zip(stamps, equity)), 2022)
    spy_2022 = _year(spy, 2022)

    # G4: rolling 12-month windows on month-end observations.
    eb_m = _monthly_last(list(zip(stamps, equity)))
    spy_m = _monthly_last(spy)
    wins = total = 0
    for i in range(len(eb_m) - 12):
        if spy_m[i] <= 0 or eb_m[i] <= 0 or i + 12 >= len(spy_m):
            continue
        total += 1
        wins += (eb_m[i + 12] / eb_m[i]) > (spy_m[i + 12] / spy_m[i])
    win_rate = 100.0 * wins / total if total else 0.0

    # G5: one-way turnover against mean equity.
    if len(trades) >= _TRADE_TAIL_CAP:
        print(f"WARNING: {len(trades)} trades == the tail cap; turnover is a "
              "LOWER BOUND. Read the run from Postgres before trusting G5.")
    traded = sum(abs(float(t.get("total") or 0.0)) for t in trades)
    mean_equity = sum(equity) / len(equity)
    turnover = traded / mean_equity / max(years, 1e-9) * 100.0

    # G6: the two silent failures that burned XS.
    status, logs = call("GET", f"/backtests/{backtest_id}/logs")
    lines = (logs or {}).get("logs") or []
    text = "\n".join(str(line) for line in lines)
    ghost = text.count("would_block_in_phase2=True")
    capped = text.count("Broker single-position cap:")

    checks = [
        ("G1", f"CAGR {eb_cagr:.2f}% >= SPY {spy_cagr:.2f}% + 4pp",
         eb_cagr >= spy_cagr + 4.0),
        ("G2", f"maxDD {eb_dd:.2f}% within 1.2x SPY {spy_dd:.2f}%",
         abs(eb_dd) <= 1.2 * abs(spy_dd)),
        ("G3", f"2022 {eb_2022:.2f}% >= SPY 2022 {spy_2022:.2f}% - 12pp"
               if eb_2022 is not None else "2022 not in the window",
         eb_2022 is not None and spy_2022 is not None
         and eb_2022 >= spy_2022 - 12.0),
        ("G4", f"rolling 12m win rate {win_rate:.1f}% >= 60% (n={total})",
         win_rate >= 60.0),
        ("G5", f"one-way turnover {turnover:.0f}%/yr <= 400%", turnover <= 400.0),
        ("G6", f"{ghost} ghost sells, {capped} cap trims (both must be 0)",
         ghost == 0 and capped == 0),
    ]
    print(f"FROZEN GATE — backtest {backtest_id}, "
          f"{stamps[0].date()} -> {stamps[-1].date()}, {years:.2f}y")
    for name, description, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {description}")
    passed = all(ok for _, _, ok in checks)
    print("\n" + ("ALL SIX PASS — ship enabled."
                  if passed else
                  "GATE FAILED — ship DISABLED with these numbers recorded in "
                  "strategy_eb.DEFAULTS comments. Do NOT re-tune to pass."))
    return 0 if passed else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
```

- [ ] **Step 3: Smoke-test both scripts against the API**

Run: `python3 scripts/strategy_eb_bootstrap.py show`
Expected: either nothing (not created yet) or the document line. It must not
traceback.

Run: `python3 scripts/strategy_eb_gate.py 0`
Expected: `SystemExit: graph-data -> 4xx` — the argument handling and auth work.

- [ ] **Step 4: Commit**

```bash
git add scripts/strategy_eb_bootstrap.py scripts/strategy_eb_gate.py
git commit -m "feat(strategy-eb): API bootstrap and the frozen G1-G6 acceptance gate"
```

---

## Task 10: Full suite, schema re-sync, and the DEFAULTS record

**Files:**
- Modify: `backend/strategy_eb.py` (the measured-numbers comment block)
- Modify: `backend/strategies/strategy_eb.py` (header, via the sync script)

**Interfaces:**
- Consumes: everything from Tasks 1-9.
- Produces: no new symbols.

- [ ] **Step 1: Re-sync the schema header**

Run: `python3 scripts/strategy_eb_sync_schema.py`
Expected: `schema synced from DEFAULTS: 25 config keys`. If the count changed
since Task 2, a key was added or dropped and
`test_the_schema_header_contains_exactly_every_default` is what catches it.

- [ ] **Step 2: Run the EB suite**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb.py backend/tests/test_strategy_eb_run_once.py backend/tests/test_strategy_eb_broker_wiring.py backend/tests/test_tiered_cost_model.py backend/tests/test_live_equity_bars.py backend/tests/test_live_risk_limits.py backend/tests/test_backtest_engine_single_position_cap.py`
Expected: PASS, all green.

- [ ] **Step 3: Run the full backend suite**

Run: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests`
Expected: no NEW failures against the pre-change baseline. Record the baseline
first with `git stash` if one is not already known. Any new failure is a real
regression from Tasks 4, 5 or 7 — those are the three that touch shared code.

- [ ] **Step 4: Confirm no config key is dead**

Run:
```bash
for k in $(python3 -c "import sys; sys.path.insert(0,'backend'); from strategy_eb import DEFAULTS; print(' '.join(DEFAULTS))"); do
  n=$(grep -rl "$k" backend --include=*.py | grep -v /tests/ | wc -l)
  [ "$n" -eq 0 ] && echo "DEAD KEY: $k"
done; echo done
```
Expected: `done` with no `DEAD KEY` lines. Every key must be read by something —
`broker.py:_DEAD_STRATEGY_CONFIG_KEYS` exists because "operator sets the key one
letter off from the real one" is a recurring failure here.

- [ ] **Step 5: Record the measured numbers in DEFAULTS**

Run the local harness once more and paste its headline into the module docstring
of `backend/strategy_eb.py`, replacing the placeholder line:

```
    CAGR ~24%, max drawdown ~-40%, one-way turnover ~250-300%/yr.
```

with the actual figures, in the XS format:

```
Measured locally (yfinance <START> -> <END>, 4.4 bps on ETF legs, next-bar
fills), harness `scripts/strategy_eb_matrix.py`:

    design                CAGR    maxDD   Sharpe  turnover
    SPY buy & hold      <...>   <...>   <...>       0%
    Strategy EB, SPY    <...>   <...>   <...>    <...>%
    Strategy EB, BIL    <...>   <...>   <...>    <...>%

Every local harness in this repo OVER-STATES. The engine is the verdict; the
frozen gate is in the design doc, section 11, and
`scripts/strategy_eb_gate.py` evaluates it.
```

After the engine run, append the gate result — pass or fail — in the same block.
If the gate failed, `strategy_eb_enabled` stays `False` and the failing
conditions are named there. It is not re-tuned to pass.

- [ ] **Step 6: Commit**

```bash
git add backend/strategy_eb.py backend/strategies/strategy_eb.py
git commit -m "docs(strategy-eb): record the measured local numbers in DEFAULTS"
```

---

## Post-implementation sequence (operator, not the implementer)

Not part of the plan's tasks — recorded so the next session does not have to
rediscover it. From spec sections 11 and 12:

1. Merge, deploy, and prove prod runs the commit:
   `python3 scripts/check_deployed_code.py`.
2. `python3 scripts/strategy_eb_bootstrap.py create`.
3. Queue the gate run: `POST /backtests` with `instance_id="strategy-eb"`,
   `start_date="2021-11-01"`, `end_date="2026-08-27"`, `granularity="86400"`,
   `initial_cash=6000`, `equity_cost_tiers="etf-liquid"`.
   Granularity is SECONDS and the API default `"60"` is 1-minute stepping.
4. `python3 scripts/strategy_eb_gate.py <id>`.
5. If all six pass: paper instance for one full weekly cycle — confirm the live
   bar carrier populates, one rebalance executes at the expected weights, the
   order gate blocks nothing, and `LiveOrderWAL` shows the fills. Then price
   the first live fills against SIP NBBO and update the `etf-liquid` preset if
   the 8 bps assumption is off by more than 2x. Only then flip a live instance.
6. Rollback at any point: set `strategy_eb_enabled=false`; delete the document
   and the instance.

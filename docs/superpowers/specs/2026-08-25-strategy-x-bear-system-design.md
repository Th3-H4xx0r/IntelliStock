# Strategy X Bear System Design

**Status:** proposed for implementation; default-off and backtest-only
**Date:** 2026-08-25
**Extends:** `docs/superpowers/specs/2026-08-23-strategy-x-design.md`

## 1. Decision

Add a separate, opt-in bear subsystem to Strategy X. It combines:

1. a T-bill defensive destination;
2. a diversified managed-futures ETF sleeve for potential crisis alpha; and
3. a small, time-limited SQQQ fast-crash kicker.

The subsystem ships in `off` mode. `shadow` computes and records every decision
without changing target weights or orders. `active` may be used only in research
documents until the frozen no-harm tests pass. This change does not modify a live
strategy document, enable Strategy X, or change the live leveraged-exposure cap.

SQQQ remains an instrument inside the system, not the system itself. The legacy
`core_bear_symbol` gate stays unchanged and default-off. The new subsystem uses
different configuration and state keys so the rejected gate cannot be enabled by
accident.

## 2. Why this design

The existing crisis gate identifies deep, disorderly selloffs. The repository's
own studies show that it often fires near capitulation, misses grinding bears,
and loses money when held. A new SQQQ path must therefore enter only on a fresh
breakdown and leave after a few sessions.

Professional crisis-alpha evidence supports diversified time-series momentum
across futures markets. Strategy X cannot reproduce an institutional futures
book safely in its current ETF allocator, so the first implementation outsources
that exposure to managed-futures ETFs. This is intentionally smaller than a
home-built long/short futures engine.

## 3. Goals

- Preserve current Strategy X orders exactly when the subsystem is `off`.
- Preserve current Strategy X orders exactly when the subsystem is `shadow`.
- Reduce equity exposure in risk-off periods without forcing a permanent cash
  allocation during risk-on periods.
- Add potential bear-market return through a diversified managed-futures sleeve.
- Permit a small SQQQ position only after a fresh, confirmed breakdown.
- Bound SQQQ by weight, holding time, recovery exit, and cooldown.
- Fail to the current `core_chop_symbol` risk-off plan when required defensive
  data is missing.
- Keep every decision point-in-time and every simulated fill on the next bar.
- Emit enough structured state to prove whether each component was active.

## 4. Non-goals

- No claim that the system profits in every bear market.
- No options, futures, VIX products, naked shorts, or short-SQQQ decay trades.
- No LLM, news, graph, or macro model decides market direction.
- No parameter search in the implementation script.
- No rewrite of the existing commodity or stock satellite sleeves.
- No live activation or production strategy-document mutation.
- No synthetic managed-futures history presented as actual fund performance.

## 5. Approaches considered

### A. Integrated, isolated extension to Strategy X — selected

Add pure bear-policy functions in a focused module, then let `StrategyX.run_once`
apply their result to the risk-off allocation. This reuses the existing
point-in-time boundary, target planner, owned-symbol controls, order sizing, and
broker universe wiring. The subsystem remains isolated through a master mode and
new configuration names.

### B. Separate run-once overlay strategy — rejected

A second allocator would have cleaner conceptual ownership but would compete
with Strategy X for cash, position ownership, sell enforcement, and the broker's
single-position cap. Coordinating two target books expands the blast radius far
beyond the bear logic.

### C. Replace the current risk-off path — rejected

Replacing SPY directly is mechanically simple but changes the shipped baseline
before the candidate has evidence. It violates the no-harm constraint and makes
A/A comparison impossible.

## 6. Architecture

Create `backend/strategy_x_bear.py` for pure calculations and immutable result
types. It will not read clocks, files, the network, broker state, or environment
variables.

`backend/strategies/strategy_x.py` remains the stateful orchestrator:

1. Build visible daily closes through `pit_daily_closes`.
2. Obtain the existing `CoreSignal`.
3. Build the current baseline targets through `plan_targets`.
4. Evaluate the new bear policy and SQQQ state machine.
5. In `off`, execute the baseline targets without fetching bear-system symbols.
6. In `shadow`, execute the baseline targets and record proposed bear targets.
7. In `active`, replace only the baseline risk-off `core_chop_symbol`
   allocation with the defensive allocation.
8. Convert the selected targets through the existing `targets_to_orders` path.

The stock and commodity sleeves retain their existing targets. The new policy
may redistribute only the weight that the baseline planner assigned to
`core_chop_symbol` while `risk_on=False`.

## 7. Configuration

All new behavior is disabled by default.

```python
"bear_system_mode": "off",  # off | shadow | active
"bear_cash_symbol": "BIL",
"crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"],
"crisis_alpha_pct": 0.20,              # absolute NAV target
"crisis_alpha_min_history_bars": 60,
"bear_kicker_symbol": "SQQQ",
"bear_kicker_pct": 0.05,               # absolute NAV target
"bear_kicker_fast_ma_bars": 20,
"bear_kicker_mid_ma_bars": 50,
"bear_kicker_long_ma_bars": 200,
"bear_kicker_max_bars": 5,
"bear_kicker_cooldown_bars": 10,
```

Invalid modes normalize to `off`. Percentages clamp to `[0, 1]`. Lookbacks
clamp to at least two sessions. Symbols normalize to uppercase and deduplicate
in declared order.

`core_bear_symbol` and the new bear system are mutually exclusive. When
`core_bear_symbol` is non-empty, the new subsystem refuses to engage, records a
configuration conflict, and leaves the legacy behavior unchanged.

## 8. Defensive allocation

The defensive allocator runs only when all of these hold:

- `bear_system_mode` is `shadow` or `active`;
- the existing core signal is risk-off;
- the legacy bear symbol is empty; and
- `bear_cash_symbol` has a positive point-in-time price.

Let `defensive_budget` equal the `core_chop_symbol` weight in the baseline target
plan. The allocator applies these steps without exceeding that budget:

1. Find managed-futures symbols with a positive current price and at least
   `crisis_alpha_min_history_bars` visible closes.
2. Allocate up to `crisis_alpha_pct` of NAV equally across eligible funds.
3. If the SQQQ kicker is engaged, allocate up to `bear_kicker_pct` of NAV to it.
4. Allocate the remaining defensive budget to `bear_cash_symbol`.

If no managed-futures fund is eligible, its budget goes to
`bear_cash_symbol`. If that symbol is not priceable, the entire new subsystem
fails closed to the original baseline targets; it must not create a partial
defensive book or leave an accidental cash hole.

Managed-futures funds are not ranked by recent fund return. They already contain
dynamic long/short programs, and a second momentum rule would add an unvalidated
timing layer. Equal weighting reduces dependence on one manager or model.

## 9. Fast-crash signal and state machine

The kicker uses QQQ closes only. Define `stacked_breakdown` on a session when
QQQ closes below its 20-, 50-, and 200-session moving averages. A fresh event is
true when today's condition is true and the prior visible session's condition
was false.

The state transitions are based on active daily target decisions, which keeps
shadow and active evaluation identical under next-bar execution:

- `idle -> armed`: a fresh event occurs while Strategy X is risk-off. Arming
  preserves the event across the mandatory one-session transition out of TQQQ.
- `armed -> holding`: on the next decision session, QQQ is still below all
  three averages and no TQQQ position is held.
- `armed -> cooldown`: the one-session arm expires, QQQ recovers, or TQQQ is
  still held.
- `holding -> holding`: QQQ remains below its 20-session average and fewer than
  five active target sessions have elapsed.
- `holding -> cooldown`: QQQ closes at or above its 20-session average or the
  five-session limit is reached.
- `cooldown -> idle`: ten decision sessions elapse.

The system never averages down and never increases the kicker above its fixed
target. A direct TQQQ-to-SQQQ transition is prohibited; the first risk-off
session routes through the defensive book. With next-bar fills, five active
target sessions produce at most approximately five sessions of market exposure.
A missing cache cannot manufacture a fresh entry because freshness is derived
from the last two visible price states. If a cache reset occurs while SQQQ is
actually held, the wrapper adopts and exits that position under the recovery and
time-limit rules rather than abandoning it outside the owned-symbol set.

Cache keys are new and namespaced:

```text
_sx_bear_system_state
_sx_bear_kicker_bars
_sx_bear_kicker_cooldown
_sx_bear_shadow
```

## 10. Shadow telemetry

Shadow mode records, per decision session:

- core risk state and reason;
- subsystem eligibility or refusal reason;
- eligible and unavailable managed-futures symbols;
- fast-crash condition, freshness, state, holding bars, and cooldown;
- baseline targets;
- proposed bear targets; and
- the exact target delta that active mode would have applied.

It writes this structure to `strategy_cache["_sx_bear_shadow"]` and emits one
concise Strategy X log line. Shadow mode may add metadata and data fetches, but
its decisions and `_nexus_position_sizes` must match `off` mode exactly.

## 11. Universe and price handling

`strategy_x_universe` adds BIL, DBMF, KMLM, CTA, and SQQQ only in `shadow` or
`active` mode. The broker already delegates to this function, so one source of
truth controls both bar fetching and strategy ownership.

Managed-futures and defensive symbols are excluded from the stock satellite
ranking. The run-once wrapper owns the whole configured bear-system universe so
a previously held fund remains sellable after it becomes unavailable or leaves
the target.

All history passes through `pit_daily_closes`. Quotes take precedence over the
last visible close. A fund with insufficient history is unavailable rather than
backfilled, guessed, or treated as a zero-return asset.

## 12. Research harness

Add `scripts/strategy_x_bear_research.py`. It drives the real `StrategyX` class
bar by bar with next-bar fills and a fixed two-basis-point one-way cost. It
compares four predeclared arms:

1. current baseline (`off`);
2. `shadow`, which must have identical equity and orders;
3. active BIL plus managed futures, with the kicker disabled; and
4. the full active system.

The script downloads adjusted QQQ, TQQQ, SPY, BIL, SQQQ, DBMF, KMLM, and CTA
prices. It uses the expanding fund universe point in time: a fund becomes
eligible only after 60 actual observations. It reports missing-history periods
explicitly.

Frozen evaluation slices include:

- fast drawdowns: 2018 Q4, 2020 Q1, and 2025 spring;
- grinding/inflation bear: 2022;
- recovery periods immediately following each drawdown;
- representative bull and chop periods; and
- the full common-history interval for each comparison.

DBMF, KMLM, and CTA do not cover 2000 or 2008. The report must state that those
crises are untested rather than fill them with synthetic fund returns. SQQQ-only
event diagnostics may use its actual post-2010 history, but they cannot validate
the managed-futures sleeve.

The script has no optimizer and accepts no parameter grid. Changing a frozen
value requires editing the research declaration and produces a reviewable diff.

## 13. Tests

Unit tests cover:

- mode normalization and default-off behavior;
- fresh stacked-breakdown detection without future bars;
- five-session maximum hold, recovery exit, and ten-session cooldown;
- cache-reset behavior;
- equal-weight managed-futures allocation;
- insufficient-history and missing-BIL fallbacks;
- target weights never exceeding the baseline defensive budget or 100% of NAV;
- no direct TQQQ-to-SQQQ flip;
- mutual exclusion with the legacy bear leg;
- universe declaration and symbol normalization;
- exclusion from the stock satellite sleeve;
- exact `off` versus `shadow` order and sizing parity; and
- active end-to-end buy and exit behavior through `StrategyX.run_once`.

Every behavior change follows red-green TDD. Existing Strategy X tests must stay
green.

## 14. Promotion gates

Implementation completion does not promote the subsystem. `active` remains a
research-only setting until all gates pass on frozen data:

1. `off` matches the current baseline orders and equity exactly.
2. `shadow` matches `off` orders and equity exactly.
3. Every frozen bear window has terminal return no lower than baseline and
   maximum drawdown no worse than baseline.
4. Every frozen bull, recovery, and chop window has terminal return no lower
   than baseline.
5. The full comparable interval has CAGR no lower than baseline and maximum
   drawdown no worse than baseline.
6. The full system beats the defense-only arm; otherwise SQQQ remains disabled.
7. Results include actual fund availability, next-bar fills, costs, trade count,
   turnover, and component P&L.
8. A future paper-trading shadow period shows no order-parity or data failures.

There is no tolerance hidden behind averaging: a losing frozen window fails the
strict no-harm gate. If no candidate passes, the completed subsystem remains in
shadow and the current Strategy X behavior stays in force.

## 15. Failure handling

- Invalid mode: act as `off` and log the invalid value once per session.
- Missing QQQ history: retain Strategy X's existing refusal to trade.
- Missing BIL price: use baseline targets unchanged.
- Missing managed-futures history: omit that fund and reweight eligible funds.
- Missing SQQQ price: suppress the kicker and retain the defensive allocation.
- Legacy/new-system conflict: suppress the new system and retain legacy behavior.
- Weight or finite-number error: reject the overlay and retain baseline targets.

No failure may silently increase TQQQ, SQQQ, total target weight, or cash demand.

## 16. Main risks

- Managed-futures ETFs have short live histories and material manager/model
  differences.
- Activating the sleeve only after the equity filter turns risk-off may miss the
  first part of a crash and may double-time an internally dynamic product.
- The fresh SQQQ event has weak post-hoc evidence and may not survive a frozen
  out-of-sample test.
- Five percent SQQQ may be too small to offset the long book but is intentionally
  capped while evidence is weak.
- A sharp recovery can hurt both a lagging managed-futures sleeve and SQQQ at the
  same time.
- BIL preserves nominal capital but can lose purchasing power and is not crisis
  alpha.
- Shadow order parity does not prove economic value; active promotion requires
  the frozen return tests.

These risks are reasons for the mode gate and research harness, not reasons to
weaken the tests.

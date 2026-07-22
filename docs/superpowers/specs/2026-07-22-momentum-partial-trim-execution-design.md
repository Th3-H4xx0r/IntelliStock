# Momentum-watchlist partial-trim execution — Design (2026-07-22)

## Problem (root cause, evidence-backed)

In the bull backtest #701112 (+7.24%, peaked +36%) the portfolio round-tripped a
$8,166 peak down to $6,394. Per-bar decomposition (`portfolio_value_history`)
shows the cliff is **one name: CAR** (−$2,016 peak→trough), plus FLY/TOYO/TSEM —
all parabolic momentum names that gave their gains back. It is a **real** move
(CAR fell through ~8 confirming down-bars), not a marking mirage.

CAR was bought via `momentum_watchlist_rotation` @ $311, ran to **$752 (+142%)**,
and was held at a **constant 2.6794 shares the entire time** — never trimmed —
then dumped near the bottom at $253 (−18%).

Log forensics established the exact mechanism (three findings, the third is the
open bug):

1. **The ML overlay does NOT veto trims.** "ML overlay PRESERVE profit-take …
   partial trim retained" means the overlay *keeps* the trim. FLY and RKLB got
   the identical line and their 40% trims **executed**. (Corrects a long-standing
   wrong assumption that PRESERVE = veto.)
2. **CAR's profit-take tiers DID trigger** three times (+21.4% → 40%, +48.7% →
   50%, +113.7% → 60%) and were retained.
3. **They never executed — a broker-side routing gap.** CAR is a
   momentum-watchlist holding (∈ `_mw_protected`). The gna sizing-side hole that
   used to drop such trims was closed 2026-07-05 (commit 15de7854), so the trim
   *is* sized into `nexus_position_sizes["CAR"] = {"sell_fraction": …}`. But the
   broker only executes sells whose symbol is in `expanded_symbols`
   (`broker.py:9578`, `_sell_first = [s for s in sorted(expanded_symbols) if s in
   _nexus_sell_set]`). The V7.5 force-include block (`broker.py:9294-9306`)
   injects only **enforcement** (forced-exit) sells into `expanded_symbols`, not
   **partial-trim** (profit-take) sells. A momentum name not re-discovered each
   bar is absent from `expanded_symbols`, so its retained partial trim is
   silently filtered out.

So the strategy computes the right scale-out, retains it, and then drops it at
the execution boundary — the winner rides the full parabola untrimmed and
round-trips.

## Fix

Close the broker-side gap: **force-include held tickers that carry a partial
`sell_fraction` hint into `expanded_symbols`**, exactly as the V7.5 block already
does for enforcement sells — so a retained profit-take trim on a momentum-
watchlist holding reaches `_sell_first` and executes like a normal holding
(FLY/RKLB). CAR then scales out on the way up (banks ~+21/+49/+114% tiers)
instead of round-tripping.

Config-gated, **default OFF** → byte-identical for live `alpaca-main`/doc-179
until enabled.

### Changes (two files, both additive, default-off)

1. **`backend/broker.py`** — a pure, testable helper
   `_momentum_partial_trim_missing(nexus_position_sizes, expanded_symbols,
   positions)` returning the set of held symbols with `0 < sell_fraction < 1`
   that are absent from `expanded_symbols`; plus a gated caller placed
   immediately after the V7.5 enforcement injector (~line 9349) that, when
   `nexus_position_sizes["_momentum_partial_trim_execution_enabled"]` is true,
   `expanded_symbols |= missing` and backfills their prices (mirrors V7.5).
2. **`backend/strategies/graph_nexus_analysis.py`** — one line in `run_once`
   alongside the existing `nexus_position_sizes["_cash_reserve_floor_pct"] = …`
   control-key passthrough (~line 27969):
   `nexus_position_sizes["_momentum_partial_trim_execution_enabled"] =
   bool(config.get("momentum_partial_trim_execution_enabled", False))`.

New config key: `momentum_partial_trim_execution_enabled` (default `false`).
Honored at runtime via `config.get` without a schema-header edit (same
DEAD-config-key pattern as `single_position_max_pct`).

### Why it can't create new sells or new risk
- It only surfaces trims **already computed** by `_evaluate_position_risk` and
  **retained** by the overlay; guarded to `0 < sell_fraction < 1` and
  `"buy_cash" not in hint`, so it can never touch a buy or a full liquidation.
- No `_mw_protected` / enforcement / forced-exit logic is modified.

### Bear-safety (must keep the validated bear window ~+2.29%)
Profit-take tiers only arm at **+20/+40/+70% unrealized gain** — structurally a
bull phenomenon. A bear-side down position never reaches a tier, so this path
cannot fire on losers. The monitor cycle already defers profit-take entirely, so
this only affects the daily full cycle. Bear-safe by construction; default-off
makes it inert everywhere until doc-179 enables it.

## Validation
- Unit test `_momentum_partial_trim_missing`: partial-trim-not-in-expanded →
  included; full sell (sf=1.0) → excluded; buy_cash entry → excluded; control
  keys (non-dict) → excluded; already-in-expanded → excluded; not-held →
  excluded.
- One backtest on the bull window: v6 config (= bt701112 baseline) +
  `momentum_partial_trim_execution_enabled: true`. Expect CAR/FLY/TOYO/TSEM to
  scale out on the way up and the result to clear SPY (+13.6%). Baseline A is the
  known bt701112 (+7.24%) — the code change is default-off, so no separate
  baseline run is needed.
- Then confirm the bear window (2026-03-02→03-30) stays ≈ +2.29% before any live
  consideration.
```
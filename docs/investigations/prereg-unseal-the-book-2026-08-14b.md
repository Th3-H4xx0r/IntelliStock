# Preregistration: unseal the book

Written before the run. Registered so the result cannot be reinterpreted afterwards.

## What changed in the diagnosis

Seven parallel audits of bt 523085 / 718107 / 624674 / 553341 (2026-08-14b) moved the conversion
problem off "the satellite cap trims below the floor" (D1, real but the smaller half) onto two
mechanisms that between them account for nearly every refused conviction buy.

**1. The book is arithmetically sealed at six names.**

```
satellite_max_share = 1 - core_min_pct(0.10) - cash_reserve_floor_pct(0.02) = 0.88
per-name target     = total_spend_cap_target_weight_pct                     = 0.14
6 names             = 0.84   ->  room 0.04 of NAV = ~$250
min-position floor  = max($50, NAV * min_position_nav_pct 0.06)             = ~$370
```

Nothing can enter and nothing can be added once the sixth name is held. Measured: satellite share
median 84.67% of NAV, conviction room median $207, **373 of 495 bars (75%) with no band clearing the
floor**. Median held position weight is 13.96% — the sizing intent was never the problem. All five
planned winner-adds in W0/W1 were refused by the satellite cap on the tick they were planned
(L8048, L21978, L37126, L40506).

At four names the same config produces the objective's design exactly: 4 x 14% = 56% satellite,
core at its 40% ceiling, $1,920 of conviction room permanently open.

**2. The passive core takes the cash the alpha book needs, and holds it across bars.**

`buy = min(drift_usd, _spendable)` is CASH-bound on four of five deploys — the core sat at
11.6-12.1% of NAV against a 27-30% target all window, so the clip collapsed to every dollar
available. Under next-event execution that order pends into the next bar, and its
`_execution_cash_reservations` entry is invisible to the buy gate (`get_cash()`) but binding on the
executor (`get_buying_power(reserved)`):

```
L13193 01-16  [core] deploy of $1165.72 SPY was NOT confirmed (band_deploy)
L13631 01-19  Buy gate inputs for AMZN: cash=$1299.21 reserved=$0.00 -> PASS
L13632 01-19  SKIP BUY AMZN - fundable $133.49 ... < min $379
L13649 01-19  SKIP BUY SNDK - fundable $133.49 ... < min $379
```

$1,299.21 − $1,165.72 = $133.49 exactly. All five core deploys held at least one gate-passed alpha
buy hostage: $5,774.16 of core notional against $5,756.28 deployed.

## Arms

| | control | treatment |
|---|---|---|
| document | 194 | 195 |
| instance | fresh, unused | fresh, unused |
| `max_positions` (+ chop/bull/recovery/regime variants) | 6 / 8 / 14 / 14 | **4** |
| `core_deploy_alpha_headroom_pct` | absent (0.0) | **0.07** |
| `conversion_fixes_enabled` | absent | **true** |

Both arms: `benchmark_quote_logging_enabled=true`, a brand-new `history_scope_salt`, a brand-new
`active_event_history_scope_salt`, `nexus_discovery_bootstrap_enabled=false`,
`nexus_discovery_snapshot_enabled=false`.

Window W0 2026-01-01..2026-03-01, granularity 3600, $6,000, `pit_mode=research`, strictly
sequential, **both to completion**. A killed run gives false negatives and a stopped run's return is
meaningless; both were demonstrated on 2026-08-14.

## Why the isolation recipe is what it is

A fresh `history_scope_salt` alone is **not** isolation. `_active_event_history_scope_id` hashes its
own salt (`graph_nexus_analysis.py:4617`), and the two W0 arms already audited shared active-event
scope `de83e7d59f26` and consumed it asymmetrically — 17 LLM-skip cache hits against 16. Separately,
`GraphNexusDiscoverySnapshots` is keyed by BASE instance id and bootstraps precisely when the
current scope's discovery table is empty (`:12930-12932`), which is the state a fresh salt creates —
so a fresh salt *invites* an arm to import its sibling's discovered universe. That is the most
plausible mechanism behind the AGQ artifact and the ~10pp dispersion.

Residual, and accepted: the sentiment cache is forced on in backtest
(`_phase_alpha_helpers.py:100-121`) and a hit restores only `sentiment_data`, so
`_apply_trend_updates` never runs — 42 hits / 0 saves on 42 of 42 bars in both prior W0 runs. It is
symmetric across arms under separate history salts and is left alone here rather than changed in the
same experiment.

## Endpoints, fixed now

Read from the treatment log, not from the config:

1. **Did the mechanism fire.** `grep` for `deploy_alpha_headroom` and for `satellite_cap_below_floor`.
   If neither appears, the levers are inert and the run says nothing about them.
2. **Conversion.** Is SNDK bought near its first actionable signal (2026-01-12, ~$388) instead of
   2026-02-04 at $617, and at a size at or above the floor? Count `FILL BUY` against `SKIP BUY`.
3. **Funnel.** Share of >=30% movers receiving a buy intent. Stable at 17-20% across five prior runs;
   a lever that does not move it has not touched the binding constraint.
4. **Turnover.** Any rise is disqualifying regardless of return. ~208%/mo in bt 523085 against a
   ~50%/mo break-even.
5. **Return vs SPY**, benchmarked from `BENCHMARK QUOTE` lines via `scripts/spy_benchmark.py`, with
   the span checked against the window. Never from `spy_series`, never from fills.
6. **Max drawdown.** A materially worse figure is not offset by return.

## Prior, recorded so it cannot be revised afterwards

Confident on the mechanism, uncertain on the return. The sealed-book arithmetic is not a hypothesis
— it is arithmetic, and four names demonstrably reopens $1,920 of room. What is genuinely uncertain
is whether the names that then get bought are the winners: discovery fires a median 19 days and
+24.2% into a move, and that is untouched here. Expect the funnel to move and the return to be noisy.
A single-window return difference under ~10pp is not evidence of anything.

The treatment bundles three changes. That is deliberate: they all address one mechanism (is there
cash available when the #1 name signals), and prior sessions spent roughly a dozen runs on
single-lever A/Bs that all came back null because the book was sealed underneath them. If this
converts, decompose it; if it does not, the mechanism is refuted as a bundle and no decomposition is
warranted.

## Cost and scope

Two full runs. doc 193 is untouched. `doc-179`/`alpaca-main` is not involved and is not started.
Both runs are `pit_mode=research` and not promotion-eligible.

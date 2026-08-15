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

At four names: 4 x 14% = 56% satellite.

**Corrected by review before launch — the reopened room is smaller than it first looked.** 0.88 is
the ceiling a CONVICTION name reaches; `broker.py:3531-3533` picks `satellite_max_share` only when
`raw_net_score >= satellite_conviction_overflow_min_raw_score` (1.5 on both docs). A plain new name
is bounded by `satellite_design_share = 1 - core_target_pct(0.35) - cash_floor(0.02) = 0.63`.

| names | satellite used | room to MAX 0.88 | room to DESIGN 0.63 |
|---|---|---|---|
| 3 | 0.42 = $2,520 | $2,760 | $1,260 |
| **4** | **0.56 = $3,360** | **$1,920** | **$420** |
| 5 | 0.70 = $4,200 | $1,080 | sealed |
| 6 | 0.84 = $5,040 | $240 (< the $360 floor) | sealed |

Four names therefore reopen **$420 for an ordinary name — exactly one plain slot** — and $1,920 for
a conviction name. The sealed-book claim at six names is unaffected and stands.

A prerequisite found by the same review and fixed in this commit: the V28.8.1 breach counter
(`graph_nexus_analysis.py:29522`) is a FIFTH `max_positions` counter that never moved onto
`slot_exclusions`. With the core leg held and a cap of 4 it reads 5 > 4, latches a permanent BREACH
and blocks every new-ticker buy — the alpha book would converge to THREE names while
`max_positions` read 4, and only the treatment arm would breach. Without
`slot_exclusions_all_counters_enabled` on both arms this experiment measures breached rotation
machinery, not a four-name book.

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

A third channel the salts do not reach, closed here: `overlay_result_cache_enabled` is True on both
documents and its key is `md5(symbol|date_key|round(raw_net_score,1)|event_types|model)`
(`graph_nexus_analysis.py:22490-22498`) — no instance, no scope, no salt, and the score bucketed to
one decimal so near-miss scores collide. The treatment changes slots and sizing, not scores, so most
names produce matching keys and the second arm would replay the first arm's LLM overlay verdicts.
Off on both arms.

Known and NOT closed, recorded so it is not discovered afterwards: `GraphNexusTickerHistory` is
keyed by the bare ticker with no as-of filter on read (`graph_nexus_analysis.py:16084-16090`), and
it feeds the sentiment prompt. A prior run over a later window leaves headlines dated after this
window under the same ticker, so day-1 prompts can contain future headlines. This is a lookahead
channel, not merely an asymmetry, and nothing in the codebase purges it. It is left in place because
purging a shared production table is not a change to make unattended; it biases both arms in the
same direction, so the paired comparison survives, but no absolute return from either arm should be
treated as clean.

Residual, and accepted: the sentiment cache is forced on in backtest
(`_phase_alpha_helpers.py:100-121`) and a hit restores only `sentiment_data`, so
`_apply_trend_updates` never runs — 42 hits / 0 saves on 42 of 42 bars in both prior W0 runs. It is
symmetric across arms under separate history salts and is left alone here rather than changed in the
same experiment.

## Endpoints, fixed now

Read from the treatment log, not from the config:

1. **Did the mechanism fire.** `grep` the treatment log for `ALPHA HEADROOM: withheld` (the core
   left cash for the alpha book) and for `satellite_cap_below_floor` (the D1 decline). If neither
   appears, the levers are inert and the run says nothing about them.

   The first of those exists because review caught that the lever's common path is a *shrunk*
   deploy, which logs identically to a smaller deploy with the lever off. A refusal-only diagnostic
   would have been unreachable — greppable-and-absent reads as "never fired", which is exactly how
   five previous levers were mis-read.
2. **Conversion.** Is SNDK bought near its first actionable signal (2026-01-12, ~$388) instead of
   2026-02-04 at $617, and at a size at or above the floor? Count `FILL BUY` against `SKIP BUY`.
3. **Funnel.** Share of >=30% movers receiving a buy intent. Stable at 17-20% across five prior runs;
   a lever that does not move it has not touched the binding constraint.
4. **Turnover.** Any rise is disqualifying regardless of return. ~208%/mo in bt 523085 against a
   ~50%/mo break-even.

   **Confound registered in advance.** `_v288_at_cap = (_position_breach_active or
   _position_headroom <= 0)` (`graph_nexus_analysis.py:30094`) gates the V31.7 CONVERT path that
   turns 50% partial trims into 100% full exits. A lower `max_positions` drives headroom to zero
   sooner, so it makes that path fire more often **mechanically**, with nothing to do with cash
   availability. If turnover rises, check the CONVERT count before attributing the rise to the
   hypothesis — and note that `max_positions` therefore cannot be read as independent of
   `conversion_fixes_enabled`, since both feed this expression.
5. **Return vs SPY**, benchmarked from `BENCHMARK QUOTE` lines via `scripts/spy_benchmark.py`, with
   the span checked against the window. Never from `spy_series`, never from fills.
6. **Max drawdown.** A materially worse figure is not offset by return.

## Prior, recorded so it cannot be revised afterwards

Confident on the mechanism, uncertain on the return. The sealed-book arithmetic is not a hypothesis
— it is arithmetic, and four names demonstrably reopens $420 of plain room and $1,920 of conviction
room where six names leave nothing fundable at all. What is genuinely uncertain
is whether the names that then get bought are the winners: discovery fires a median 19 days and
+24.2% into a move, and that is untouched here. Expect the funnel to move and the return to be noisy.
A single-window return difference under ~10pp is not evidence of anything.

The treatment bundles three changes. That is deliberate: they all address one mechanism (is there
cash available when the #1 name signals), and prior sessions spent roughly a dozen runs on
single-lever A/Bs that all came back null because the book was sealed underneath them. If this
converts, decompose it; if it does not, the mechanism is refuted as a bundle and no decomposition is
warranted.

## Generalisation — registered before any result

W0 is where the mechanism was FOUND. A win there is in-sample and proves nothing on its own; the
sealed-book arithmetic was derived from W0's own logs, so W0 is the window most likely to reward it
for the wrong reason. The sweep exists to find that out.

| window | dates | what it tests |
|---|---|---|
| W0 | 2026-01-01..2026-03-01 | reference bull, semiconductor-heavy — IN SAMPLE |
| W1 | 2026-03-30..2026-04-27 | out of sample bull |
| W2 | 2026-03-02..2026-03-30 | bear — safety veto, must not be harmed |
| W3 | 2026-06-01..2026-07-01 | non-semiconductor, and the window that currently LOSES 10.14pp |

**W3 is the acid test.** It is the worst window on record and the least like the one the fix was
derived from. If the diagnosis is right — the book seals at six names and the core hoards the cash —
then W3 should improve, because nothing about that arithmetic is semiconductor-specific. If W0
improves and W3 does not, the honest reading is that the lever is fitted to W0, not that W3 is
special.

**Salts rotate per (arm, window).** Reusing W0's salts on a later window rebuilds the exact
contamination this experiment controls for: every row the W0 run wrote in Jan-Feb is `< date_key`
for a March window, therefore immortal, and is served to the later run as legitimate lookback. That
is how one scoped instance came to carry 178 / 242 / 285 trends across three windows.
`scripts/arm_unseal_pair.py --window w3` handles it.

### Acceptance rule, fixed now

Accept the bundle only if ALL of:
1. treatment beats control by more than the dispersion in **at least two of {W0, W1, W3}**, and W3 is
   one of them;
2. **no turnover rise in any window** — this is the objective's named leak and it is disqualifying
   regardless of return;
3. **no harm in W2** (bear). Note the treatment is structurally INERT in bear —
   `max_positions_bear` stays 2 on both arms — so W2 is a safety veto, not a test.

A win in W0 alone is what we already have from a single arm and is not sufficient.

### The dispersion is NOT known, and the old figure does not transfer

The ~10pp noise floor came from bt 873929 vs bt 523085 — two runs whose difference was later
attributed to AGQ appearing in one and not its twin, i.e. to the contamination this design now
controls for. **Under the new isolation the dispersion is unmeasured.** It could be far smaller
(making a 5pp difference meaningful) or the isolation could have removed one contamination channel
while leaving others (making 20pp still a draw). Either way, differencing against a floor measured on
contaminated runs is not sound.

Registered consequence: before any cross-window claim is published, ONE window must be repeated
same-config, same-arm, fresh salts, to measure the dispersion under the current isolation. Until
that exists, every delta below is reported with the floor stated as UNKNOWN rather than as 10pp.

## Cost and scope

Two full runs. doc 193 is untouched. `doc-179`/`alpaca-main` is not involved and is not started.
Both runs are `pit_mode=research` and not promotion-eligible.

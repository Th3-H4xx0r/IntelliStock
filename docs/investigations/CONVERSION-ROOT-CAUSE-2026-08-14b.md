# The conversion bug is a cash-reservation deadlock, not a sizing bug

Session 2026-08-14b. Everything here is read off `bt 523085` (the W0 reference control,
2026-01-01..2026-03-01, $6,000, +6.00%). No backtest was run to produce this document.

## 0. What changed in the diagnosis

The prior session's D1 said the satellite cap trims a buy below the floor that then refuses it,
and shipped `conversion_fixes_enabled` to decline instead of emitting an unfillable order. That is
real, but it is the **smaller half** of the problem and by construction cannot create a fill — it
declines a buy that had no room, it does not find room.

Classifying **all 67 `SKIP BUY` lines in bt 523085** by their actual binding cause:

| cause | count | intended notional |
|---|---:|---:|
| `TRIM` — satellite cap trimmed below the floor (D1) | 35 | $6,759 |
| `INFLIGHT` — "orders already in flight this tick reserve the rest" | 25 | **$16,117** |
| `CASH` — the account genuinely lacked the floor | 7 | $6,134 |

Of the 25 `INFLIGHT` refusals, **16 had cash at or above the min-position floor at the gate**:
**$14,119 of intended conviction notional was refused for want of ORDER, not capital.**

## 1. The mechanism, reconciled to the cent

`residual_bull_deploy` — the passive index core buying SPY — submits an order that is
`accepted=True, filled=False` and then **pends across trading sessions**. While it pends, its entry
in `PortfolioEmulator._execution_cash_reservations` is subtracted by
`get_buying_power(reserved)` (portfolio_emulator.py:456, reservations at :366/:1475/:1566), but the
alpha buy gate sizes off `get_cash()` (broker.py ~16337). The gate prints `PASS`; the executor then
refuses the order it just passed.

Worked example, exact to the cent:

```
L13193  2026-01-16  [core] deploy of $1165.72 SPY was NOT confirmed (band_deploy)
L13194  2026-01-16  [core] bought $1165.72 SPY @ 691.58 (band_deploy: 11.6% -> 30.2% of NAV,
                    ok=SimulationSubmission(order_id='sim-000000000013-SPY', ..., filled=False))
L13631  2026-01-19  Buy gate inputs for AMZN: cash=$1299.21 reserved=$0.00 ... → PASS
L13632  2026-01-19  SKIP BUY AMZN — fundable $133.49 ... (orders already in flight this tick
                    reserve the rest) < min $379 (allocated $885.03)
L13642  2026-01-19  SKIP BUY SKYT — fundable $133.49 ... < min $379 (allocated $885.03)
L13649  2026-01-19  SKIP BUY SNDK — fundable $133.49 ... < min $379 (allocated $885.03)
L14816  2026-01-20  FILL BUY SPY qty=1.68169158 price=685.375940 quote=2026-01-20 16:00:00
```

$1,299.21 − $1,165.72 = **$133.49**. The number the gate calls "fundable" is cash minus a
three-day-old passive index order. No alpha buy filled on 2026-01-19 at all. The same reservation
was still in force the next session (L14563 AVNT, L14570 CYTK, L14577 ORLY, L14584 TT — all
`fundable $133.52`, i.e. cash $1,299.24 − $1,165.72).

Note also that the gate line prints `reserved=$0.00` while $1,165.72 is in fact reserved. **The
diagnostic that was used to rule things out was itself wrong.**

Five such unconfirmed core deploys exist in the run, totalling $5,774.16 on a $6,000 account:
L1917 $2,400.00, L4744 $115.41, L13193 $1,165.72, L15954 $1,127.69, L23404 $965.34.

## 2. The loop the reservation sits inside

The core does not merely deploy once. Reading the `[core]` lane end to end, it runs a
release→re-buy cycle every other session:

```
01-19  [core] funding request trimmed $3,540 -> $1,275 — satellite headroom will refuse the
       remainder; releasing core for it would only be bought back        (L13625)
01-20  FILL BUY SPY $1,152.51                                            (L14816)
01-21  [core] released 1.6309 SPY @ 677.66 (core rebalance: funding 29.6% -> 29.9%)  (L15456)
01-21  FILL SELL SPY 1.63091190 @ 683.201892                             (L15710)
01-21  [core] deploy of $1127.69 SPY was NOT confirmed (band_deploy)     (L15954)
01-22  alpha refused again: SCHW fundable $77.51, USB fundable $77.51    (L16422, L16430)
01-22  FILL BUY SPY 1.63383924 @ 690.186933                              (L16662)
01-23  [core] released 1.6373 SPY (funding 29.7% -> 29.8%)               (L17367)
```

The core sells SPY to fund a named satellite buy, the satellite buy is refused because the core's
*next* deploy has already reserved the proceeds, and the core buys the SPY straight back. The
codebase already knows this shape — `core_sleeve.py:405-470` documents it against four earlier runs
($11,474 released, $7,104 recycled, 62%) and ships `core_funding_release_reserve_decisions` to stop
it. **That flag is set to 4 (ON) in this run and the loop still ran**, so either the credit is
consulted only on the release path and not the deploy path, or its budget is exhausted before the
satellite gets its bar. That is the open question A1 is answering.

## 2b. Why the shipped anti-re-buy credit does not hold

`core_funding_release_reserve_decisions = 4` is ON in this run. Probed against the project's own
extracted-AST harness (`test_core_sleeve_adversarial._Book`, in-process, 0.16s):

```
credit OFF  -> [('sell','SPY',1300.0), ('buy','SPY',1292.25)]   # the documented round trip
credit = 4  -> [('sell','SPY',1300.0)]                          # re-buy correctly blocked
  deploy #1..#4 refused, reserve 1300 -> decisions 3,2,1,0
  deploy #5   -> [('sell','SPY',1300.0), ('buy','SPY',1292.25)] # credit expired, core re-buys
```

So the mechanism is correct and the **budget is the problem**. Two reasons it drains faster than
the design note (`core_sleeve.py:445-465`) assumes:

1. The note assumes broker.py evaluates the core twice per bar. Measured over bt 523085's 41 bars:
   median 2 core decisions per bar, **max 8**.
2. `_consume_funding_reserve_decision()` (core_sleeve.py:695) is called whenever the reserve
   changes the size of the deploy — including when the *reduced* deploy still goes through and is
   logged as `band_deploy`. Only the case where the reduced buy falls under the minimum logs
   `funding_release_reserved`. The run shows **3 `funding_release_reserved` lines against 4
   `band_deploy` executions**, so most of the budget burned invisibly.

`core_funding_release_reserve_decisions` is plumbed straight from the strategy document
(core_sleeve.py:338), so raising it is a **pure-config lever needing no deploy**.

The deeper defect is that the credit is keyed to the *release event* rather than to the alpha
book's outstanding demand. The correct rule is: while conviction buys stand unfunded, the passive
core may not deploy — regardless of where the cash came from.

## 2c. `need = funding_request - cash` has the same get_cash bug

`core_sleeve.py:587`:

```python
need = max(0.0, float(funding_request or 0.0) - max(0.0, float(cash or 0.0)))
```

If the caller passes `get_cash()`, then on exactly the bars where cash is nominally present but
reserved by a pending order, `need` computes to 0 and the core declines to release — while the
alpha book cannot in fact spend a cent of that cash. This is defect D3, and it is the same
`get_cash` vs `get_buying_power` confusion as §1, in a second place.

## 3. Why this is *the* objective's gap

`docs/OBJECTIVE.txt` asks for four names at ~10% of NAV each. The intended sizes in this run were
already right — `cash_per_trade` was $874-$890 against a ~$6,000 NAV, i.e. **14.7%**. The intent
was never the problem. On every tick where a #1-ranked name signalled, the money to act on it was
either sitting inside a pending SPY order or had just been recycled into one.

SNDK is the whole thesis in one symbol: discovered 2026-01-01, ranked #1 on the momentum watchlist
on twenty separate sessions, `raw_net_score` 1.700 rising to 2.107, first actionable at $388 on
01-12, and finally bought on 02-04 at $617. It moved +166%. The run captured +2.28% of it.

## 3b. What the seven parallel audits changed

| claim | verdict |
|---|---|
| the book is **arithmetically sealed** at 6 names: 6x14% = 84% of an 88% ceiling leaves $250 against a $370 floor | **NEW, and the largest finding.** 373/495 bars (75%) had no band clearing the floor. At 4 names the same config gives the objective's design exactly: core at its 40% ceiling, $1,920 of room. |
| the satellite cap refuses **adds to held winners**, not just entries | **NEW.** 5 of 5 planned winner-adds in W0/W1 were killed by it on the tick they were planned (L8048, L21978, L37126, L40506). |
| median held position is 4.73% of NAV (objective blocker #2) | **STALE.** Measured median 13.96% across all three windows. `total_spend_cap_concentrate` fixed it. |
| a pending passive core order is the biggest single refusal cause | **CONFIRMED.** 14 of 25 in-flight refusals, $12,384.57. Reconciled to the cent on two separate orders. |
| `buy_order_conviction_ranked_enabled` would fix the ordering | **REFUTED — it would HARM.** It is wired correctly but ranks on `raw_net_score`, which the momentum lane floors at a constant **1.700** for every pick (`graph_nexus_analysis.py:22180-22189`). Enabling it demotes the entire lane that finds the big movers below routine graph buys at 1.800. It would have changed **0 of 24** competing ticks. Leave OFF. |
| the alphabetical tie-break is the leak | **REFUTED for this run.** On 22 of 24 competing ticks ZERO buys funded, so order could not matter. The one real casualty (OI, 01-06) had four byte-identical raws and would have lost either way. |
| D6: the bear leg's 70%-of-NAV park trips the turnover brake | arithmetic **CONFIRMED to the cent** (102.8%), consequence **REFUTED**: the brake blocked zero orders. The alpha book was starved upstream by `Regime capacity gate (Z4.1): max_positions 6->2` with zero headroom on every cycle. |
| D6b: same-bar cash double-spend | **CONFIRMED twice** with exact arithmetic ($681.43 x2 + $568.26 against $681.43 of idle; $716.09 x2 + $534.62). $3,898 of ledger, $1,759 of forced churn. FIXED. |
| D8: a fresh `history_scope_salt` isolates an A/B arm | **REFUTED.** `_active_event_history_scope_id` has its **own** salt (`graph_nexus_analysis.py:4617`) — both prior W0 arms shared scope `de83e7d59f26` and consumed it asymmetrically (17 vs 16 LLM-skip hits). Worse, `GraphNexusDiscoverySnapshots` is BASE-instance-keyed and bootstraps precisely when the new scope is empty (`:12930-12932`), so a fresh salt *invites* importing the sibling's universe — the most plausible AGQ mechanism. |
| the sentiment cache skips `_apply_trend_updates` | **CONFIRMED and quantified.** 42 cache hits / 0 saves on 42 of 42 bars in BOTH W0 runs; the trend layer is frozen at inherited state for the whole window. |
| displacement is inert | **CONFIRMED, three independent breaks** — the trim is filtered out of `expanded_symbols`, it never gets the sell-signal override, and its one-tick deferral lands on `symbols=0` ticks. 24 EXECUTE, 0 fills. Separately, bt 718107 vs bt 523085 is a near-clean A/B on the flag and it **cost 1.12pp**. Do not enable. |

## 4. Status

- The evidence above is direct and reproducible from the log; it does not depend on any A/B.
- The fix is NOT yet written. Seven parallel agents are auditing the code paths (core loop,
  gate/executor divergence, within-tick ordering, bear leg, reproducibility, concentration
  arithmetic, displacement inertness) before anything is edited.
- `conversion_fixes_enabled` (the D1 fix, committed 10b315f, armed on doc 195) is still worth
  keeping — it stops emitting unfillable orders and reports the skip — but on this evidence it
  should not be expected to change conversion on its own.

# Preregistration: stop the core buying back what it just sold

Written BEFORE the run. Session 2026-08-16. This is where the core-band result pointed.

## Why this lever, and why the band was not it

The band A/B (bt 545803) came back **INERT on turnover** — 303% → 302% of NAV, 26 fills → 26 —
because `core_rebalance_band_pct` gates only the REBALANCE path. The suppressed rebalance was
immediately replaced by a **funding** release, which is not band-gated:

```
control    [core] released 0.7143 SPY (core rebalance: band_release 49.0% -> 40.0%)
treatment  [core] hold (release) — within_band: core 49.0% vs target 40.0%
treatment  [core] funding $630 of conviction overflow out of the core
           [core] released 0.8051 SPY (core rebalance: funding 48.9% -> 40.0%)
```

So core turnover is driven by **satellite funding demand, not drift**. The band cannot reach it.

The recycle is visible in the cold control (bt 790588) as a sell-then-rebuy cycle:

```
[core] funding $630 of conviction overflow out of the core
[core] funding $1,233 of conviction overflow out of the core
[core] bought $684.04 SPY (band_deploy: 0.0% -> 37.6% of NAV)
[core] bought $684.92 SPY (band_deploy: 11.7% -> 37.5% of NAV)
[core] bought $858.34 SPY (band_deploy: 23.3% -> 38.0% of NAV)
```

`core_sleeve.py:442-443` already quantifies this across three runs: **$11,474 released, $7,104
recycled — 62%.** The core sells to fund a satellite buy, the buy is refused or undersized, and
the core buys the same exposure back. Every leg crosses ~22.8 bps of spread.

The mechanism that exists to prevent this is a RESERVED CREDIT: while a funding release is
outstanding, `band_deploy` may not count it as spendable. The credit expires after
`core_funding_release_reserve_decisions` core deploy decisions that it actually refuses.

**It is set to 4, and a previous investigation measured a median of 2 core decisions per bar with
a max of 8 over 41 bars.** So the credit drains within a bar or two and the cash comes straight
home — which is exactly the recycle above.

## The change

| key | control | treatment |
|---|---|---|
| `core_funding_release_reserve_decisions` | **4** | **60** |

ONE key. 60 is chosen as "longer than the window's decision budget" rather than tuned: at a
measured ~2 decisions/bar over ~44 bars, 60 means the credit effectively survives until the
satellite either spends it or the run ends. A value tuned to a return would be curve-fitting; a
value chosen to make the mechanism reachable is a test of the mechanism.

Control is **bt 790588** (cold, band reverted to 0.05, chop target back to 0.35). The document is
otherwise identical.

## Endpoints, fixed in advance

1. **Core turnover MUST fall.** Specifically SPY fill notional, and total notional turnover
   against the control's 303% of NAV. If it does not fall, the lever is inert like the band was —
   report and stop, whatever the return says.
2. **Comparability first.** Overlap < 60% or a non-cold arm ⇒ VOID.
3. **Satellite buys must not be starved.** The credit is a BLOCK on the core re-buying; if it
   also reduces satellite fills or notional, the lever is trading one problem for another and
   that must be reported, not hidden behind a return number.
4. **Return** readable at >0.5pp, but secondary to endpoint 1.
5. **SQQQ BUY notional must not fall** (deployment, not P&L).

## RESULT — INERT, and the premise of this document was WRONG

**bt 790588 (reserve=4) vs bt 185794 (reserve=60)**, both cold. **100% overlap.**

| | control | treatment |
|---|---:|---:|
| return | −2.70% | −2.69% |
| total notional | $18,206 | $18,137 |
| fills | 26 | 26 |
| **core (SPY) notional** | **$8,040** | **$7,971** |
| core fills | 7 | 7 |
| satellite notional | $5,725 | $5,725 |
| SQQQ BUY notional | $2,210 | $2,210 |
| turnover | 303% | 302% |

Endpoint 1 fails: core turnover did not fall. Reverted to 4.

### Why — two corrections against myself

**1. The value 4 was already right, and this document mis-read the unit.** I argued 4 was too
small against "a median of 2 core decisions per bar". But `core_sleeve.py:461-478` defines the
unit as a **REFUSED DEPLOY DECISION**, not a bar decision, and works the ladder out explicitly:
protecting the satellite for one bar costs THREE decisions, so *"4 is the value to ship: it
covers the bar with one decision of margin."* A credit is consumed **only when it changes the
outcome**. Raising 4 → 60 therefore cannot do anything: the credit already survived long enough,
and it self-clears when the satellite spends the cash. The comment even warns to bias UP rather
than down, which is what 4 already does.

**2. The recycle I set out to stop is NOT happening in this window.** Tracing the actual
chronology, every funding release was consumed by the satellite buy it was raised for:

```
[core] funding release $630   -> 06-16 sat BUY CVLT  $431
[core] funding release $1,233 -> 06-25 sat BUY CSCO  $616 + GCMG $585   (= $1,201)
```

That is the mechanism working exactly as designed. The `core_sleeve.py:442` figure — $11,474
released, $7,104 recycled (62%) — is from **three older runs on a different configuration**, and
it does not reproduce here. Quoting it as a live prize would have been wrong.

### What the trace DID find: the churn is regime whipsaw, not funding

The core's real turnover in this window is the hedge cycle:

```
06-29  CORE  SELL SPY  $2,272     <- raise cash for the bear leg
06-30  HEDGE BUY  SQQQ $1,321 + $889
06-30  HEDGE SELL SQQQ $741 ...   <- hedge starts unwinding the SAME DAY
07-02  CORE  BUY  SPY  $684       <- core buys back
07-07  CORE  BUY  SPY  $681       <- and again
```

**$2,272 out of the core and $1,365 straight back within eight days**, because the regime flipped
to bear and back. That single cycle is ~60% of the core's $8,040 of notional. It is not the
funding path and not the rebalance band — it is the **bear-leg entry/exit cadence**, and it is
the third distinct mechanism this line of investigation has ruled in or out.

The lever that would address it is the hedge's own freshness/dwell gating
(`residual_sleeve_bear_*`), not anything in the core sleeve. That is the next test, and it must
be run knowing that the hedge is the ONE validated edge this system has — a change that reduces
hedge effectiveness to cut turnover is a bad trade even if turnover falls.

## What I will not claim

- Not that reduced turnover is itself a win. It is only a win if return holds or improves.
- Not that one window generalises — the recycle is measured across three runs, the FIX in one.
- Not a causal return result if endpoint 1 fails, no matter how good the P&L looks. That rule
  has already killed one +1.17pp result tonight and it applies equally here.

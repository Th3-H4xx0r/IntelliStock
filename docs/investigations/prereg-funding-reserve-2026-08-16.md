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

## What I will not claim

- Not that reduced turnover is itself a win. It is only a win if return holds or improves.
- Not that one window generalises — the recycle is measured across three runs, the FIX in one.
- Not a causal return result if endpoint 1 fails, no matter how good the P&L looks. That rule
  has already killed one +1.17pp result tonight and it applies equally here.

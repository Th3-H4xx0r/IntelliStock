# What actually blocks the money, ranked by measured prize

Written 2026-08-16 so the next session does not re-derive it. Every number here comes from a
completed run's own output, not a projection.

## 0. The gate above everything

**No config lever in this repository can currently be measured.** Three paired runs, each one
flag apart, shared 20%, 23% and 11% of their traded names. The cause is measured, not guessed:
the instance carried **4,213 rows** of decision-steering state between runs (2,939
`GraphNexusDiscoveredStocks`, 1,241 `GraphNexusMarketTrends`, 1,907 `GraphNexusActiveEvents`).

`scripts/attest_arm_start.py` now detects this, `scripts/run_paired_experiment.py` enforces a
cold start, and an A/A determinism run is in flight to find out whether a cold start is enough.
**Until that returns, every item below is an estimate, and no lever should be promoted.**

A residual is already known: `clear_instance_state` PRESERVES shared caches (article, sentiment,
FinBERT) by design, and a sentiment cache hit changes behaviour — 42 cache hits against 0
"LLM trends:" lines in one audited run. A cold per-instance start is not a cold GLOBAL start.

## 1. Chop stand-down — the largest measured prize

**Measured on bt 325136 (window f, the only losing window).** P&L by sleeve:

| sleeve | P&L |
|---|---:|
| satellite longs | **−$225.47** |
| SPY core | −$82.81 |
| total | −$308.28 (−5.09%) |

The satellite sleeve lost money in chop while SPY returned **+0.69%**. Holding the index instead
of momentum names over that window is worth roughly **+$246, about +4.1% of a $6,000 account** —
which moves window f from −5.09% to about −1%.

Why it is causally motivated rather than fitted: window f classified **40 of 59 bars as chop**
with only 3 bear bars, so the SQQQ hedge never deployed and the book bought momentum names that
mean-reverted (TSLA −25.72% captured of a −23.45% move; **RCL −7.50% captured of a POSITIVE
+8.10% move**). Window c was also chop-dominated (46 chop) but had **19 bear bars**, the hedge
fired, and SQQQ captured 77% of a +16.98% move for +$416.61. **The difference between the two
chop windows is whether a bear signal deployed the hedge.**

Implementation is pure config: `regime_profiles.chop.core_target_pct` (profiles accept any config
key — the bull profile carries 18). It does **not** violate the objective's standing rule, which
forbids adding a *bear* profile because that routes bear de-risk to cash and drops the hedge.
Chop is not bear, and doc 195 still has no bear profile.

**Risk, stated:** window c earned its chop return from XOM (+15.13% captured) and APD. Raising
core in chop would have cost some of that. This is a genuine trade-off and needs the A/B.

## 2. Passive execution — the objective's own blocker #4, built and never switched on

`passive_execution_enabled` is **absent from doc 195**, so every fill crosses the spread.
`simulated_execution.py:139` calls this "the largest unexploited cost lever on the book", and
passive limit orders shipped in `a31deaf`.

Sizing the prize from real fills: explicit fees are only **0.3 bps**, but the crossing cost lives
inside the fill price — **~22.8 bps of half-spread** under `equity-measured-v3-nbbo23` (measured
to the cent in `67a4918`: 23.2 bps on a $210.82 sale).

| run | fills | notional | ≈ crossing cost @22.8 bps | as % of $6,000 |
|---|---:|---:|---:|---:|
| bt 325136 | 35 | $24,829 | ~$57 | **~0.95%** |
| bt 333727 | 20 | $15,390 | ~$35 | ~0.59% |
| bt 826225 | 15 | $10,820 | ~$25 | ~0.41% |

So roughly **+0.4% to +1.0% per window, ~5-8%/yr** — real, bounded, and it buys non-fill risk in
exchange (the simulator models expiry, `passive_expire_quotes` default 8).

**It is measurable without a comparable pair.** Spread and slippage are per-fill deterministic
costs, not draw-dependent, so this can be judged the way the conversion fix was: on mechanism
counts rather than return.

## 3. The core underperforms its own benchmark

The passive SPY core does **worse than SPY**, consistently:

| run | SPY moved | core captured | drag |
|---|---:|---:|---:|
| bt 333727 | +16.66% | +14.95% | **−1.7pp** |
| bt 325136 | +0.69% | −1.55% | **−2.2pp** |

~1.7-2.2pp per 6-8 week window is **10-15pp a year** given away by the leg that is supposed to be
the safe one — rebalance churn plus spread crossing on every band deploy. Item 2 attacks part of
this directly; the rebalance bands (`core_rebalance_band_pct` 0.05, `core_rebalance_min_days` 5)
are the other half and have never been swept.

## 4. Entry timing — mechanism confirmed, prize unquantified

Median discovery→buy lag is 3 days, but the TAIL is where the money went: AAOI 14d, AEHR 23d,
AXTI 35d — **the three names bt 333727 lost money on**. The breakout-freshness tie-break is now
armed and proven live (44/44 reorderings), and the treatment had no entry beyond 6 days. At 11%
book overlap that is directional, not attributable.

## 5. The arithmetic nobody has written down

The objective's 1x bar is **+12% per two-month window**. Current per-window results, best
available config:

| window | result | vs +12% bar |
|---|---:|---|
| d (bull) | +20.53% | **met** |
| a (mild bull) | +8.19% ⚠ stopped run | short |
| c (chop→bear) | +0.46% | far short |
| f (chop) | −5.09% | negative |

Adding EVERY estimate above — chop stand-down (+4.1% on f), passive execution (+0.4-1.0%), core
drag recovery (+1.7-2.2%) — moves f to roughly break-even and c to perhaps +3-4%. **That is not
+12%, and it is not +100%/yr.** It is a system that would roughly track SPY with a good bull
window, which is a real thing to own but is not the stated objective.

Saying this plainly is the point: the remaining levers are worth doing, and they do not close the
gap to +100-300%/yr. Closing that gap needs the big movers held at size, which is blocked by
entry timing (item 4) and by a book that cannot hold more than ~6 names.

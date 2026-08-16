# Production readiness — 2026-08-16

Written for the operator, who has asked for a production decision and is tired of iteration. This
answers the question directly and does not hedge. Where the answer is "no", the reason is a number,
not a feeling.

## 1. The recommendation, first

**Ship doc 195 to PAPER today. Do not fund real money on it this week.**

That is not a stall. The forward-paper clock is the only instrument that can promote this system,
it costs nothing, and it starts the moment you flip it. Funding it this week would be betting on a
number that this project has now measured to be unreliable, and the specific way it fails is
already known: **it loses money in flat tape**, which is the most common regime there is.

If you want risk on the table immediately, the honest version is at the end (§6) — it is small, it
is bounded, and it is a decision I can defend, unlike funding the headline.

## 2. Which strategy, and why

**doc 195 — `PRODUCTION v1 — Nexus core/satellite`** (instance `v2-conv-trt`).

It is the only document that carries all five of the 2026-08-15 root-cause fixes
(`selection_uses_natural_score`, `sizing_respects_satellite_share`, `satellite_share_counts_held`,
`dd_kill_blocks_entries`, `ticker_history_as_of`) plus `slot_exclusions_all_counters`, **and** the
only one with the A/B isolation recipe that actually works armed (both `history_scope_salt` and
`active_event_history_scope_salt` set, `nexus_discovery_bootstrap`/`snapshot` both false).

Everything else is now labelled `OLD` (11 documents) or is legacy `AI Temp` (105). Active documents
are down from 40+ to five: 179 (real money, untouched), 182 (PIT capture), 193 (the objective's
named reference), 195 (production), 5 (Agent Best).

## 3. What it actually returns

| window | dates | regime | strategy | SPY | vs SPY | run |
|---|---|---|---:|---:|---:|---|
| a | 2025-11-10..2026-01-10 | mild bull | +8.19% ⚠ | +1.86% | **+6.33pp** | 443154 — **STOPPED at 97.65%**, no summary block |
| c | 2026-02-01..2026-04-01 | chop→bear | +0.46% ⚠ | −5.29% | **+5.75pp** | 235194 — **PRE-fix**, never re-run |
| d | 2026-04-01..2026-06-01 | bull | **+20.53%** | +16.66% | **+3.87pp** | 333727 |
| f | 2026-06-15..2026-08-01 | chop | **−5.09%** | +0.69% | **−5.78pp** | 325136 (this session) |

Beats SPY in three of four windows and loses in the fourth. Mean ≈ **+6% per two-month window**,
which annualises to roughly **+40%/yr** — against an objective of **+100% to +300%/yr**. The
objective's own two-month bar is +12% for 1x; only window d clears it.

**Two of those four numbers are not clean.** Window a's run was stopped before it printed a
summary; window c has never been run with the fixes. So the shippable configuration has exactly
**two clean completed backtests**, and one of them loses.

## 4. What is genuinely fixed — this is the real progress

The conversion bug, which has dominated this project for months, **is fixed**. This is the one
result that does not depend on the return noise, because refusal counts are deterministic
mechanism counts on the identical window:

| run | window | config | `SKIP BUY` | `insufficient_cash` | book-full bars |
|---|---|---|---:|---:|---:|
| 288424 | f | pre-fix | **41** | 40 | 180 |
| 325136 | f | **post-fix** | **2** | **2** | 59 |

Refusals collapsed **41 → 2**. `satellite_cap_below_floor` never fires in any post-fix run. The
book holds 4-5 of 6 slots instead of jamming at 6. The system now buys the names it ranks.

**Objective blocker #5 is also closed:** the SQQQ bear leg profits in a bear. In bt 235194, SPY fell
−5.29% and the strategy returned +0.46%, with SQQQ capturing 77% of a +16.98% move for **+$416.61,
+6.9% of the account**.

## 5. Why it is still not fundable

**(a) It loses in flat tape, and it is not a plumbing problem any more.** Window f post-fix bought
a clean large-cap book (AAPL, ABBV, AMZN, EQT, VVX) with almost no refusals — and lost. It bought
TSLA (−25.72% captured of a −23.45% move), CCL (−12.53% of a −4.60% move), and **RCL, which rose
+8.10% over the window while the system lost −7.50% on it.** 35 trades on a six-name book in six
weeks. In chop the selection has no edge and the churn costs money. No config lever in this
repository fixes that.

**(b) Every equity backtest here is lookahead-biased.** Each run emits `PIT RESEARCH MODE … carries
lookahead bias and is NOT promotion-eligible` 634-675 times. The engine itself refuses to promote
these numbers. That is not a formality — it is the difference between a backtest and a forecast.

**(c) The measurement noise is larger than most of the effects.** Same-config dispersion in this
project is **~10pp** (bt 873929 +16.41% vs bt 523085 +6.00%, identical configuration, the entire
gap one lucky name). Three of the four deltas above are smaller than that. Single runs cannot
separate them.

**(d) The best number is mostly unrealized.** bt 333727's +20.53% is **78% open marks** — $958.93
of $1,232.31 — on five positions, three bought 13 days before the window ended. Realized P&L was
**+4.55%**, on **six round trips**. The objective's own rule is "n=5 round trips is not evidence."

**(e) Entry timing is unfixed and it is the root cause.** Placing each entry inside its own name's
range in bt 333727: entered early (<40% through) → 4 of 5 profitable, mean +13.95%; entered late →
1 of 5 profitable excluding MXL, mean −7.6%. AEHR, AXTI and D were bought **above the price at
which the window ended**. The system's three biggest movers — AEHR +152.43%, AAOI +118.22%, AXTI
+90.11% — were all bought and all closed at a loss.

**(f) No A/B in this project is currently interpretable — and this was proved, not suspected.**
bt 453789 re-ran window d against bt 333727 changing exactly ONE config flag. The two runs shared
**4 of 20 names — 20% overlap.** AAOI and AEHR, the two names the experiment was designed around,
did not appear in the treatment at all. doc 195 carries the isolation recipe the handoffs call the
one that works (both salts set, discovery bootstrap and snapshot off) and **it was not enough.**

This generalises backwards: every paired A/B here, including the ones that produced the flags now
shipping, measures the draw as much as the lever. It is the mechanism behind the ~10pp same-config
dispersion. `backend/frozen_paired_state.py` was written for exactly this problem, is pure, and is
imported by nothing. **Wiring it is now the highest-leverage engineering task in the repository**,
because until it exists, config tuning — which is what the last several months have consisted of —
cannot be measured.

## 5a. The repo's OWN promotion gate agrees — 0 of 6

`python3 scripts/assess_live_readiness.py` hands the evidence we actually have to the repository's
own `evaluate_promotion` and prints the gate's reasons. It is not my judgement:

```
LIVE READINESS - alpaca-main (real money)
state          : RESEARCH
checks passed  : 0/6

BLOCKING:
  [FAIL] secrets              secret_migration, rollback
  [FAIL] research_integrity   point_in_time_provenance, unseen_months, regime_count,
                              sealed_holdout, median_repeat, max_drawdown, ...
  [FAIL] execution_safety     lifecycle_chaos, dependency_chaos
  [FAIL] risk_state           restart_state
  [FAIL] operations           watchdog
  [FAIL] paper_observation    paper_build, paper_days
```

Note `paper_observation` is itself a **blocking** gate, and `paper_days` is zero. The system will
not promote anything to real money until a paper run has accumulated days — so "paper first" is not
a preference I am imposing, it is the shortest path the gate allows.

Most of the remaining failures are operational rather than strategic (`watchdog`, `restart_state`,
`lifecycle_chaos`, secret rotation/rollback rehearsal). Those are days of work, not months, and
they are worth doing precisely because they are the cheap half of the list.

## 6. If you want money at risk now

The defensible version, in order:

1. **Flip doc 195 to paper today.** Zero risk, and it starts the only clock that can ever promote
   this. Also restart `alpaca-paper-pit` (doc 182) — it has never booted, and every day it is down
   pushes the PIT promotion date back a day.
2. **If you fund it, fund an amount whose loss does not matter,** and size the expectation to
   −5% to −11% in a flat quarter. That is not pessimism; it is the measured window-f result before
   and after the fixes.
3. **Do not point doc 179 / `alpaca-main` at this yet.** It is real money and it is stopped, so
   nothing is being lost by waiting. It was not modified in this session.
4. **The next real work is NOT another config lever.** In order: (a) wire
   `frozen_paired_state.py` so experiments become measurable at all — nothing else can be trusted
   until this exists; (b) entry timing, which is the root cause of the lost movers and is not a
   flag. Every config lever in this repo has now been tried, and the honest reason they keep
   returning null is partly that the measurement cannot resolve them.

## 6a. One data-quality defect found in passing

`AKTX` appears in bt 453789 with a "movement" of **$0.12 → $13.41 (+10,815%)**, while the decision
log quotes it flat at **$6.10** for days. A sub-penny start, a four-order-of-magnitude move and a
frozen quote together read as an **unadjusted reverse split**, not a real price series — and the
system traded it, lost $120.68, and fired the circuit breaker on it five times. This repo has
already shipped two split-handling fixes (`4d5c705`, `3c09d81`); this one escaped them. It did not
affect the conclusions above, but it is a live defect in the data path that decides real trades and
should be looked at before funding anything.

## 7. What was done this session

- Struck the withdrawn `DO NOT RETRY` claim that the ranking score is not noise (IC +0.17, t=3.1)
  from both objective files — no reproducible artifact exists and 717 of 723 candidates scored
  exactly +1.000.
- Verified the fill-boundary cash crash (`HANDOFF-2026-08-15` §5) was already fixed in `67a4918`.
- Ran bt 325136 (window f post-fix) and bt 453789 (window d, circuit-breaker treatment).
- Found and preregistered the inverted bull circuit-breaker (§`prereg-circuit-breaker-inversion`).
- Consolidated 40+ strategy documents down to five active.
- Suite: 19 failed / 5482 passed — the exact documented baseline set, no new failures.
- Deploy verified; the self-learning engine's stale-container blocker has cleared on its own.

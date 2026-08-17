# Preregistration: the hedge sells itself to rebuild cash

Written BEFORE the run. Third lever in the turnover chain; the first two were rejected and each
one narrowed where the churn actually lives.

## The chain that led here

1. **Chop stand-down** — return improved +1.17pp, **turnover ROSE** 303%→361%. Disqualified.
2. **Core rebalance band** 0.05→0.12 — **inert**: 303%→302%, 26→26 fills. The band gates only the
   *rebalance* path; the suppressed trade reappeared as a *funding* release.
3. **Funding reserve** 4→60 — **inert**: 100% overlap, 0.01pp. The value 4 was already correct
   (the unit is a refused DEPLOY decision, not a bar), and the recycle it targets does not occur
   in this window — every funding release was consumed by the satellite buy it was raised for.

Each null narrowed the search. Tracing the actual fill chronology found the churn:

```
06-29  CORE  SELL SPY  $2,272     raise cash for the bear leg
06-30  HEDGE BUY  SQQQ $1,321 + $889
06-30  HEDGE SELL SQQQ $741       <- unwinding the SAME DAY
07-02  CORE  BUY  SPY  $684
07-07  CORE  BUY  SPY  $681
```

## The mechanism, from the log

It is not the trailing stop and not the regime flipping. It is a **cash-target refill**:

```
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (regime=bear, leg=1322/4130 cap, alloc=70%)
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (leg=2641/4130 cap)
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (leg=3529/4130 cap)
[sleeve] released 20.0983 SQQQ @ 38.26 (bear-leg refill: cash 2.0% -> target 15% of NAV)
[sleeve] released  0.4408 SQQQ @ 36.95 (bear-leg refill: cash 14.7% -> target 15% of NAV)
[sleeve] release SKIPPED SQQQ: $0.55 < $5.00 minimum — the live broker would reject this order
```

Measured in the cold control (bt 790588): **3 park events totalling $3,966.51, then 48 refill
releases selling $2,230.84 straight back.** The hedge liquidates **56% of itself within days** to
rebuild a cash target — and the same 48 releases appear in bt 185794, so it is systematic, not a
one-off.

Two configs are fighting: `residual_sleeve_bear_alloc_pct = 0.70` says put 70% of NAV in the
hedge; `residual_sleeve_release_cash_pct = 0.15` says hold 15% of NAV in cash. The sleeve satisfies
the first, then immediately sells the hedge to satisfy the second. Note also that some releases are
sub-dollar — the log itself says *"the live broker would reject this order"* — so this is spraying
tiny orders that would not even execute live.

## The change

| key | control | treatment |
|---|---|---|
| `residual_sleeve_release_cash_pct` | **0.15** | **0.02** |

ONE key, set to match `residual_sleeve_buffer_pct` (0.02), which is the sleeve's own idea of the
cash it needs. Chosen for internal consistency, not tuned to a return.

Control is **bt 790588** (cold; band, chop target and funding reserve all reverted).

## Endpoints, fixed in advance

1. **Refill releases MUST fall** from 48, and SQQQ sold-back notional from $2,230.84. This is the
   mechanism. If they do not fall, inert — report and stop, whatever the return.
2. **Comparability first.** Overlap < 60% or non-cold arm ⇒ VOID.
3. **SQQQ BUY notional must NOT fall** — the hedge must still deploy. This lever should let it
   HOLD more, not deploy less.
4. **Total turnover** against the control's 303% of NAV.
5. **Return** readable at >0.5pp, secondary to 1 and 3.

## The risk, stated plainly

Cutting the cash target means the sleeve holds less dry powder in a bear. If the hedge then cannot
fund a satellite buy or a stop-out, that shows up as fewer satellite fills — endpoint 3's sibling,
and I will report it if it happens. The bear leg is the ONE validated edge this system has
(window c: SQQQ captured 77% of a +16.98% move, +$416.61), so a change that weakens it is a bad
trade even if turnover falls.

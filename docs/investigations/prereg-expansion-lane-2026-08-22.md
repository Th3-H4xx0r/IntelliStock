# Preregistration: trend-expansion lane slot reservation (WARM pair, window d)

Date: 2026-08-22. Registered BEFORE the runs. First lever tested under the accepted warm
protocol (prereg-warm-protocol-acceptance: byte-identical arms, 100% overlap, +0.00pp).

## Motivation (from the warm acceptance run's own book)
Arm A (bt 198193): the trend-expansion lane promoted GLD/AGMI/GDX/PSLV straight to executable
on tick 1 — carrying the WARMUP regime's gold trend at saturated +1.000 sentiment — filled the
book, and the rank band then blocked AAOI/AEHR/AXTI (all discovered) for the entire window.
Result +3.09% vs SPY +16.66%. The expansion lane front-runs the book with last-regime ETFs
while rank discipline holds back the movers. This is the conversion gap's current form.

## Lever (config-only)
`propagation_expansion_reserved_slots: 4 → 0` (treatment arm only), doc 195, window d
(2026-04-01..2026-06-01), $6,000, 3600s. Both arms start from the SAME warm snapshot
(/tmp/_pair_warm_v2-conv-trt_2026-04-01.state.json — Feb–Mar warmup, 15,421 rows,
digest-verified) via `run_paired_experiment.py --snapshot`. Note: both arms run on the
post-b4e0f52 code (overlay prompt changed), so neither arm is comparable to bt 198193 — the
pair is self-contained.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** the treatment's tick-1/early book is NOT dominated by
   expansion-promoted ETFs — 'Propagation expansion buys (promoted)' fills drop to 0 slots,
   and ≥1 rank-band single name enters the book inside the first 5 sessions.
2. **SECONDARY:** return delta vs the warm floor (0.0pp measured; use 0.5pp as the guard band).
3. **GUARDS:**
   - The lever must not touch the sleeve: SQQQ/SPY behavior identical between arms.
   - No pathological cash idling: if the treatment simply holds cash all window (nothing
     replaces the ETF deploys), record that as the finding — the lane is load-bearing for
     deployment speed, and the fix must be rank-validation, not slot removal.
4. Fallback: if overlap goes VOID anyway (chop-style cascade in a bull window is not
   expected but possible), judge on the mechanism endpoints, which are deterministic.

## Decision rule
Adopt only if PRIMARY fires AND secondary > +0.5pp AND no guard breach. A negative or flat
delta with the mechanism fired = the ETFs were not the problem (the movers still don't get
bought) — in that case the next lever is rank-band admission for expansion promotions
(code change), preregistered separately.

## Result (bt 151824 control / bt 460555 treatment)
**LEVER INERT — NOT ADOPTED, and the pair doubles as a new-code warm A/A.**
IDENTICAL_WARM starts, **100% traded-name overlap, +0.00pp** — the treatment made byte-identical
decisions. `propagation_expansion_reserved_slots` only binds under slot CONTENTION; the
expansion-lane promotions themselves fire regardless of the reservation. PRIMARY failed
(promotions did not drop), so per the decision rule the next lever is **rank-band admission for
expansion promotions** (code change, preregistered separately).

What the pair delivered anyway:
1. **New-code byte-determinism confirmed** (first A/A on the b4e0f52 deploy).
2. **The new-code warm book contains single-name movers** — AXTI/CODX/IMNM/RIVN alongside the
   metals — where the old-code book was 4 metals ETFs only. The overlay slim (dropping fb/fp/ra)
   shifted selection materially; window-d warm return moved +3.09% → +0.58%. The rationale-field
   effect needs its own A/B before any further overlay prompt surgery.
3. **Cache economics demonstrated**: treatment ran at ZERO paid LLM calls (100% hit against its
   control's fresh entries; overlay completions at ~130ms = cache reads).

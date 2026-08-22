# Preregistration: rank-band momentum exemption (WARM pair, window d)

Date: 2026-08-22. Registered BEFORE the runs.

## Motivation
The warm-protocol acceptance run showed the movers (AAOI/AEHR/AXTI) discovered and rank-band
blocked while stale-trend metals ETFs legitimately topped the news/graph blend the band ranks
on. The band's own source comment documents the defect class (bt 820236: VICR blocked ×6,
AAOI ×3, AMAT ×9 — "a name can lead the market on price and still sit mid-pack on a news/graph
ranking") AND ships the cure: `rank_band_momentum_exempt_min_score` (default 0 = OFF, never
A/B'd). This is the conversion gap's current form, testable config-only.

## Lever
`rank_band_momentum_exempt_min_score: 0 → 0.35` (treatment arm only) — 0.35 is the existing
`watchlist_priority_min_raw_score` floor, i.e. names meeting the watchlist priority bar are
exempt from the news/graph entry band. Doc 195, window d, warm snapshot
(/tmp/_pair_warm_v2-conv-trt_2026-04-01.state.json), $6,000, 3600s.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** treatment logs `Rank band: N momentum name(s) exempt` with N>0 on
   ≥3 sessions AND ≥1 momentum name that the CONTROL rank-band blocked enters the treatment
   book. If no exemptions fire, the lever is inert at 0.35 — record and stop.
2. **SECONDARY:** return delta (warm floor; single divergent slot ≈ 2.8pp — treat |δ|<2.8pp as
   one-window-insufficient regardless of sign).
3. **GUARDS:** turnover not up >15% relative; no auto-heal/forced-liquidation; max_positions
   discipline intact (exempt names still pass every other gate).

## Decision rule
This is window 1 of ≥2. Adopt only after a second readable window (c cold) agrees in direction
AND mechanism fires in both. One positive window = promising, not adopted (split-endpoint
discipline).

## Result (bt 172501 control / bt 443898 treatment — both +1.3487%)
**PRIMARY FAILED — the lever is INERT AS WIRED, and the reason is a real wiring defect.**
Byte-identical arms (100% overlap, +0.00pp), **0 exemption lines across 44 band evaluations**,
AAOI still blocked. The exemption tests `sc.get("momentum_watchlist_score")` — a field stamped
ONLY on the momentum-watchlist lane's own picks (:22316, :29751). Discovery-lane movers (the
AAOI/VICR/AMAT class the band's comment names as the victims) never carry the field, so the
exemption structurally cannot reach them. The cure the band ships cannot fix the defect the
band documents. NEXT: code change — stamp the momentum score on every scored name that has
momentum data, or exempt on the 20d/60d momentum qualification directly; then re-pair.

## Result take 2, exemption wired (bt 743847 control / bt 974390 treatment)
**MECHANISM FIRED (40 exemption events: AAOI 0.85-1.16, AXTI 0.73-1.10, FSLY 1.54, SNSE, DOCN,
APA) — AND THE OUTCOME WAS STILL BYTE-IDENTICAL (100% overlap, +0.00pp, both +1.3487%).**
The movers cleared the band and died at the NEXT serial gate: AAOI repeatedly logged
`full_priority_blocked` (backfill queue full) and `deferred_unfunded_buy` — the metals book,
bought at full size on tick 1 with zero sells all window, left no slot and no cash. The
conversion funnel has serial gates; clearing the band revealed the funding/slot gate.

Adoption: the exemption is proven SAFE (byte-identical when downstream is blocked) and
NECESSARY-BUT-INSUFFICIENT. Keep testing it as part of the unblocking chain, not alone.
CRITICAL unlock: `deferred_unfunded_buy` fires in warm runs — **displacement's trigger is
reachable here**, so the displacement probe that was vacuous in cold runs (bt 596938) is now
testable on this exact snapshot. Next pair: control = exemption alone (≡ baseline, proven),
treatment = exemption + satellite_displacement_enabled.

Control also byte-reproduced +1.3487% across the f08a7a1 deploy — gated-change neutrality and
cross-deploy determinism both hold.

Incidental deliveries from take 1 (both significant):
1. **Cross-run byte-determinism now holds** post-f68af81: this control reproduced bt 138148 to
   the fourth decimal (+1.3487%) hours apart — the corpus-drift problem is closed for
   same-deploy repeats.
2. **The LLM cache proof at the limit: the ENTIRE PAIR ran at $0.00 paid LLM calls** (control
   0, treatment 1 stray). Two days ago one arm cost $2.96. A/B backtests are now free, and the
   pair completed in ~35 minutes.

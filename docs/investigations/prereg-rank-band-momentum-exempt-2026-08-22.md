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

## Result (appended after the runs)
_pending_

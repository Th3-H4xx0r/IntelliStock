# Preregistration: displacement break #3 — does a DISPLACEMENT EXECUTE now FILL?

Date: 2026-08-21. Registered BEFORE the run. Mechanism check, NOT an A/B.

## Question
Commit 59798b2 fixed break #1 (`expanded_symbols` filter) and found #2 already fixed. Break #3
(one-tick deferral on symbols=0 ticks) is "plausibly subsumed by #1" but explicitly NOT claimed
fixed "until a run shows a displacement FILL". This run answers exactly that.

## Setup
- Doc **196** = THROWAWAY copy of 195 with `satellite_displacement_enabled=true`. Doc 195 is
  NOT touched (its one near-clean A/B measured −1.12pp; this is a mechanism probe, not adoption).
- Instance `v2-conv-ctl` relinked 194→196 for this run (restore to 194 after).
- Cold: clear-state + attest cold=True. Window d (2026-04-01..2026-06-01), $6000, 3600s.

## Endpoints
1. **PRIMARY:** ≥1 `DISPLACEMENT EXECUTE` line followed by an actual FILL of that trim (sell
   executes; proceeds visible). PASS = break #3 subsumed by #1; FAIL (EXECUTE lines with 0
   fills again) = break #3 real and still open.
2. Secondary observations only: count of EXECUTE lines vs fills, which symbols, whether the
   displacement freed budget got spent on the displacing candidate.

## Not measured here
Return. This is a single uncontrolled run on a throwaway doc; its P&L means nothing.

## Cleanup after run
- Relink v2-conv-ctl → 194.
- Doc 196 left in place labelled THROWAWAY (or deleted if an endpoint exists).

## Result (bt 596938, cold, doc 196, v2-conv-ctl, window d)
**INCONCLUSIVE BY VACUITY — the trigger is unreachable in this run.** Zero `DISPLACEMENT` lines
of any kind. The displacement path sits inside the `_emp_skip` branch (broker.py ~16880-16951),
i.e. it fires only when a wanted buy is refused for funds — and this run logged **0**
`SKIP BUY` / `insufficient_cash` events. Post-conversion-fix cold runs do not jam the book, so
displacement never gets a turn.

Two byproduct findings, both solid:
1. **Cross-instance cold determinism.** bt 596938 (v2-conv-ctl, doc 196) = **+$609.45 (+10.16%),
   9 buys — byte-identical** to bt 760962 (v2-conv-trt, doc 195). The cold protocol reproduces
   across instance AND document identity, not just within one instance.
2. **`satellite_displacement_enabled=true` is provably inert on the cold path** — relevant to
   any future temptation to enable it: in the configuration that would ship, it does nothing
   until the book jams.

Break #3 therefore remains **UNCONFIRMED**. To reach the trigger the probe needs a run whose
book actually jams: window f (59 book-full bars post-fix) or reduced cash (e.g. $3000). Queued
behind tonight's window-c pair, time permitting.

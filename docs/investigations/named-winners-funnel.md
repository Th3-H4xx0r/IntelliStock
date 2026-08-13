# The eight named winners died at four different stages

Date: 2026-08-13. Source: bt **873929** (W0 control, 2026-01-01..2026-03-01). No credits spent.

The objective names eight winners that "none were bought". Traced individually through the funnel:

| name | in-window move | buy intent | buy gate | bought | where it died |
|---|---:|:--:|:--:|:--:|---|
| SNDK | +187.9% | yes | yes | **yes** | bought — but 94.9% through the move |
| AAOI | +112.5% | no | no | no | `Entry extension gate: recent runup +60.0% > 25% — buy blocked` |
| VIAV | +72.4% | no | no | no | no buy intent, no explicit block logged |
| VICR | +50.9% | no | no | no | no buy intent, no explicit block logged |
| AMAT | +40.9% | **yes** | no | no | buy intent, never reached the gate (queue/cap) |
| ADI | +31.8% | **yes** | no | no | buy intent, never reached the gate (queue/cap) |
| TTMI | +20.0% | no | no | no | only +20% here; below the 30% bar in this window |
| LASR | — | no | no | no | **never quoted at all — not in the universe** |

## Four distinct failures, which is why single-lever tests keep returning noise

1. **Entry timing, not refusal — SNDK.** It *is* bought by the current code. The objective's "none
   were bought" is stale on this name; the failure is that entry lands 94.9% through the move,
   capturing 5.1%. Displacement and conviction ordering target exactly this.
2. **A deliberate, already-measured refusal — AAOI.** Blocked by the entry-extension gate at
   +60% runup against a 25% threshold. Loosening that gate is on the do-not-retry list
   (blocked basket -7.95%), so AAOI is a *priced* tradeoff, not an accident.
3. **Queue/cap starvation with intent present — AMAT, ADI.** These are the cases portfolio
   construction can fix; they were proposed and never got to the gate.
4. **No proposal at all — VIAV, VICR** (and 84 of 103 movers window-wide). Upstream of everything
   built so far.
5. **Universe gap — LASR.** Never quoted once in 39,748 lines. Not a conversion problem.

## What this licenses

It confirms conversion matters (SNDK, AMAT, ADI) and simultaneously shows that no lever aimed at
portfolio construction can recover AAOI, VIAV, VICR or LASR. Expecting displacement to "fix the
eight names" would be wrong by construction: it can reach at most three of them, and SNDK only by
making an existing buy earlier rather than by making it happen.

It also means the headline "discovery already finds the winners" needs one qualification: LASR was
never in the universe, and VIAV/VICR were ranked but never proposed.

Every number above is from the merged action-intent stream and the buy-gate lines of one finished
run, `pit_mode=research` (lookahead), not promotion-eligible.

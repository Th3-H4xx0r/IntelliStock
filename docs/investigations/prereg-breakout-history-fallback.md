# Preregistration: breakout history fallback

Written before any run of `breakout_history_fallback_enabled`. Two stages, and stage 2 is only
reached if stage 1 passes.

## Stage 1 — mechanism probe (cheap, kill as soon as it answers)

One run on doc 195, W0, with `breakout_history_fallback_enabled=true` and
`breakout_diagnostics_enabled=true`. No control arm, no P&L reading.

Pass requires **all** of:

1. `BREAKOUT SKIP ... skip:bars=0<25` falls well below 100% of skips. The baseline is absolute:
   bt 278531 logged 7,156 evaluations and **every one** exited at `bars=0`.
2. At least one evaluation reports `bars>0`.
3. At least one breakout **promotion** occurs — a name reaching `score=1` via the boost with no LLM
   sentiment and no graph path. Baseline is zero across the whole window.
4. No `BREAKOUT FALLBACK ERROR` lines.

Any of these failing means the lever is inert or broken, exactly like the five inert levers this
project shipped before. Stop there and diagnose; do not spend a pair.

**This stage is why the probe exists.** bt 550605 cost ~$4 and showed displacement firing 12 times
while moving nothing. Eight P&L runs would have returned eight nulls and no explanation.

## Stage 2 — P&L pair, only if stage 1 passes

Control doc 194 vs treatment doc 195, differing only in this flag, same window/instance/cash/
granularity, both equally warm, **both run to completion**, launched sequentially.

Endpoints, fixed now:

* **return** — judged against the **~10pp** same-config dispersion measured in
  `result-displacement-pair-and-noise-floor.md`, not the old 4.94pp floor. A gap under 10pp is
  noise. This bar is deliberately brutal and most levers will not clear it on one pair.
* **turnover** — any rise is disqualifying regardless of return.
* **max drawdown** — a materially worse figure is not offset by return.
* **funnel** — the reason to expect anything: names moving >=30% that receive a buy intent should
  rise from the 18-25% measured across four runs. If the funnel does not widen, the lever did not
  do the thing it was built to do, whatever the return says.

Then repeat on the bear window (2026-03-02..03-30, safety veto) and the non-semiconductor window
(2026-06-01..07-01, currently -10.14pp vs SPY) before any claim. One window is not a result.

## Prior, recorded so it cannot be revised afterwards

The mechanism is well evidenced: 7,156 evaluations all at `bars=0`, a map measured healthy, a
disjointness test that could have refuted it and did not, and bars demonstrably present in scope.
But **four earlier hypotheses in this investigation were confidently wrong**, and each looked sound
until tested. Expect the mechanism to fire; hold no expectation about P&L. Widening the funnel adds
candidates, and more candidates can mean more turnover — the endpoint most likely to disqualify it.

## Not in scope

doc 193 untouched. doc 179 / `alpaca-main` untouched. All runs `pit_mode=research`, not
promotion-eligible.

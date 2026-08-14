# Why the breakout rescue finds nothing on names that doubled

bt 778288, `BREAKOUT NOPATTERN` instrumentation, 1,678 samples.

## The numbers

```
BREAKOUT NOPATTERN: AAOI bars=104 last=37.01 h25=41.00(-9.7%)  h252=41.00(-9.7%)  vol=1.6x gap=+3.7%
BREAKOUT NOPATTERN: LITE bars=104 last=343.08 h25=397.17(-13.6%) h252=397.17(-13.6%) vol=1.0x gap=+1.5%
BREAKOUT NOPATTERN: SNDK bars=99  last=334.44 h25=353.84(-5.5%)  h252=353.84(-5.5%)  vol=1.9x gap=+1.8%
BREAKOUT NOPATTERN: VICR bars=102 last=138.45 h25=142.07(-2.5%)  h252=142.07(-2.5%)  vol=0.8x gap=-0.1%
```

Three facts, all measured:

**1. The history is live and correct.** Across AAOI's evaluations `bars` advances 94 -> 104 and
`last` takes 11 distinct values. The point-in-time filter is working; the bars are not stale. Both
candidate explanations from the previous document - stale bars, and thresholds untested against the
data - are now answered, and the answer is neither.

**2. `h25` and `h252` are identical.** With ~104 bars available, `closes[-252:]` is just all of
them, so the "52-week high" is really a five-month high and the 52w test can never be stricter than
the 5w test. The `breakout_52w_boost` of +0.5 is unreachable as a separate signal - it fires only
when the 5w test already has.

**3. The movers are not at highs when they are evaluated.** `AAOI` sits 9.7% below its own 25-bar
high, `LITE` 13.6% below, `SNDK` 5.5%, `VICR` 2.5%. The promotion test requires
`current_close >= 0.99 * high_25` - within 1% of the high. None of them qualify at the moments they
are scored.

## What that means, and it is not a bug

The scorer is behaving exactly as written. The mismatch is upstream, and it lines up precisely with
blocker (1) in the objective:

* Momentum discovery finds a name *after* it has run - `Discovered stock (momentum): AAOI
  (20d=+33.9%, 60d=+3.1%)`. Discovery is triggered by the move having already happened.
* By the time the name is in `symbols_list` and evaluated, it is typically consolidating off its
  high, not printing a fresh one.
* The breakout rescue is a "**at a new high right now**" test. A name discovered on the strength of
  a completed 20-day run is, at that moment, usually 2-14% below its high.

So the two mechanisms are pointed at different instants: discovery selects on the move *having
happened*, promotion requires the move to be *happening now*. A name can rarely satisfy both, and
that - not missing history, not calibration - is why 7,340 evaluations produced almost no
promotions.

## What follows, and what does not

This does **not** license widening the promotion test. Relaxing `0.99` to catch names 10% off their
highs would buy consolidating names on price alone, and the entry-extension gate exists precisely
because that was measured at -7.95%. The objective lists loosening it under DO NOT RETRY.

The honest reading is that the objective's own framing is the right one: *enter while the move still
has room*. The system currently discovers after the run and then asks for a fresh high, which is the
narrowest possible intersection. Any real fix is about **when a name enters the candidate set**, not
about the threshold it faces once there.

That is a design question for the operator, not a parameter to tune. Nothing further is proposed
here.

## Status

`breakout_history_fallback_enabled` reverted to OFF; `breakout_diagnostics_enabled` is log-only and
its behavioural invariance is now under test. doc 193 untouched at 580 keys. No return claim rests
on any of this.

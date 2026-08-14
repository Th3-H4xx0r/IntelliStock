# Why bt 523085 is "low", and what that costs every earlier verdict

## The 10pp gap is one name

bt 873929 and bt 523085 are the **same instance, window, cash and granularity** (`v2-conv-ctl`,
2026-01-01..2026-03-01, 3600s, $6,000) with the same config. They returned **+16.41%** and
**+6.00%**.

The whole difference is `AGQ`:

| | bt 873929 | bt 523085 |
|---|---|---|
| log lines mentioning AGQ | **627** | **0** |
| AGQ P&L | **+$1,111.28 (113% of total)** | never held |
| return | +16.41% | +6.00% |

AGQ entered 873929 as a *discovered trend ETF*, keyed off stored Nexus trends
(`v2-conv-ctl|4f430a0ae8cdd108951ff2c3_price_pslv`). Six runs later that trend no longer fires, so
AGQ never enters the universe at all. Nothing was refused — it was never seen.

**bt 523085 is not a regression. bt 873929 was the lucky draw**, and its entire result was one
lottery ticket handed over by shared mutable Nexus state.

## What this costs

The objective warns that Nexus state is shared and mutable. Measured, that hazard is worth about
**10pp of return on identical configuration**. The working noise floor in this project has been
**4.94pp** — roughly half the real dispersion.

Everything judged against 4.94pp is therefore underpowered and must be treated as unproven:

* the conviction-reserve pair "rejection" (median -0.62pp over 4 windows) measured nothing;
* the W1 OOS "noise" call (+3.44pp) was correct by luck, not by power;
* the chained "+41.26% over 4 windows = +129%/yr" figure inherits AGQ and **is withdrawn** — it is
  not reproducible on the same configuration.

A paired design cannot fix this. Two arms drawing different lottery tickets differ by more than any
lever being tested. Either the state is frozen, or verdicts need many more repetitions than one run
per arm.

## The displacement pair itself

Preregistered in `prereg-displacement-pair.md` before either run. doc 194 vs doc 195, differing in
one operative key, both run to completion.

| endpoint | control 523085 | treatment 102463 | direction |
|---|---:|---:|---|
| return | +6.00% | **+11.12%** | +5.12pp |
| max drawdown | 7.8% | **4.6%** | better |
| trades | 32 | **16** | halved |
| core lane gross | 1.80x NAV | **0.96x NAV** | halved |
| round trips | 5 | 2 | fewer |

`DISPLACEMENT EXECUTE` fired 24 times, so the lever genuinely acted. `AMAT` — one of the eight
named winners — was bought and contributed +$272.37. Neither arm saw AGQ, so this pair is not
contaminated by that specific draw.

**Verdict: NOT a return win.** +5.12pp sits below the ~10pp same-config dispersion just measured,
so by the corrected standard it is noise, and the preregistered rule was written against a floor now
known to be too small.

What is *not* explained by noise is the turnover result. Trades halved, gross core-lane exposure
halved, drawdown fell by 3.2pp, and the mechanism is documented (24 executions funding buys from
existing holdings rather than opening fresh positions). Turnover is the objective's known leak
(~290%/mo live against ~50%/mo break-even), and this is the first change measured to move it in the
right direction while return did not fall.

That is a reason to test it properly, not a reason to enable it.

## Next

1. Repeat this pair 3-5 times per arm on the same window to estimate dispersion directly, or freeze
   Nexus state so a single pair means something. Until then no lever can be accepted or rejected on
   one pair.
2. Re-open the conviction reserve as **undecided**, not rejected.
3. Do not quote the +129%/yr chained figure again.

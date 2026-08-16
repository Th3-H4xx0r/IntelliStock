# Preregistration: the bull circuit-breaker adjustment is inverted

Written BEFORE the treatment run was launched, deliberately, so the result cannot be
reinterpreted afterwards. Session 2026-08-16.

## The mechanism (a sign error, not a threshold to tune)

`_resolve_effective_open_loss_floor` (`graph_nexus_analysis.py:8790-8811`) applies the regime
adjustment by ADDING a positive `circuit_breaker_regime_adjustment_bull_pp` (default +5.0) to a
NEGATIVE floor:

```
LOW  tier: -15 + 5 = -10   # bull
MID  tier: -20 + 5 = -15   # bull
```

Adding a positive number to a negative floor makes it **less negative**, which **TIGHTENS** the
stop. The code's own comment says the opposite is intended — *"bull widens (lets winners breathe);
bear tightens"* — and the codebase already documents the defect at `:8793-8799`:

> "the legacy arithmetic below is INVERTED ... opposite to both the comments and the risk intent,
> and the source of the rally exits clustered on that unintended -10% boundary."

The correction ships as `circuit_breaker_regime_adjustment_semantics_v2`, which uses the
adjustment's MAGNITUDE (`floor - |pp|`, so bull → −20/−25). It has been **default-OFF and never
validated** since 2026-07-28.

This is why it is worth a run: it is a correction to an arithmetic inversion, not a threshold
fitted to a window. A number chosen because it rescues one backtest carries no information about
the future; a sign error does.

## Why it matters here — measured, not assumed

**bt 333727** (window d, 2026-04-01..2026-06-01, +20.53%), the best post-fix run in the project.
**Every single exit in that run was a circuit-breaker fire, and all four were in `regime=bull`:**

| name | tier | unrealized at fire | floor | realized | held |
|---|---|---|---|---|---|
| AAOI | LOW | −11.8% | −10.0% | **−$85.79** | 3 days |
| AEHR | LOW | −11.8% | −10.0% | **−$74.78** | 3 days |
| AXTI | LOW | −11.1% | −10.0% | **−$75.82** | 5 days |
| RIVN | MID | −15.0% | −15.0% | **−$125.93** | 42 days |

Total: **−$362.32, or −6.04% of a $6,000 account.** Under v2 the LOW floor in a bull is −20% and
the MID floor is −25%; none of the four would have fired at the loss that stopped them.

`AAOI` is one of the eight winners the OBJECTIVE names by ticker (+102% over the window). The
system finally BOUGHT it — the conversion fixes worked — and then stopped it out at −11.8% on day
three, three days after entry.

**The same defect binds on the worst window.** bt 288424 (window f, 2026-06-15..2026-08-01,
−11.26%) fired the circuit breaker 8 times, **7 of them in `regime=bull` at the same −10.0%
floor** (AMBQ ×6 at −12.3%, AGIX at −10.4%). So the lever is NOT inert on window f, and window f
is not where the mechanism was discovered.

## The design

| arm | run | doc | config |
|---|---|---|---|
| control | **bt 325136** | 195 | as-is (`semantics_v2` absent = OFF) |
| treatment | to be launched | 195 | `circuit_breaker_regime_adjustment_semantics_v2 = true` |

Window **f** (2026-06-15..2026-08-01), instance `v2-conv-trt`, granularity 3600, cash $6,000.
ONE variable. Window f is **out-of-sample for this hypothesis** — the mechanism was found on
window d.

The control was launched BEFORE the treatment config was written, so the control cannot have seen
it. The document is not edited while either arm is in flight (this codebase reads config live —
see the P&L-lever note that A/Bs must be sequential).

## What counts as a result — decided in advance

The measured same-config dispersion in this project is **~10pp** (`result-displacement-pair-and-
noise-floor.md`: bt 873929 +16.41% vs bt 523085 +6.00%, identical config, the whole gap one name).
The older 4.94pp floor is known to be about half the real dispersion. Therefore:

1. **A return difference smaller than ~10pp is NOISE.** It will be reported as such. n=1 per arm
   cannot clear that bar on return alone, and I am not going to pretend otherwise.
2. **The mechanism check is the real endpoint, and it is not noise-bound.** Count
   `gate=circuit_breaker ... result=fired` with `regime=bull` in each arm. Control has 7. If the
   treatment does not show materially fewer bull fires, the flag did not act and the run says
   nothing — report inert and stop.
3. **Per-name follow-through is the honest evidence.** For each name the control stopped, what did
   it do afterwards in the treatment? That is a direct read on "exit on a real turn not noise" and
   does not depend on the portfolio-level number.
4. **A turnover increase is disqualifying** (the objective's standing constraint). Fewer stops
   should REDUCE trades; if trades rise, that contradicts the mechanism.
5. **A drawdown worsening of ≥10pp is disqualifying** even if return improves. Widening a stop
   necessarily accepts deeper per-name losses; that is the trade being tested, and it has a limit.

## What I will NOT claim

- Not that one paired window proves an edge. It does not, at this dispersion.
- Not that a positive result licenses real money. Every equity run here is `pit_mode=research`
  and the engine itself stamps it lookahead-biased and not promotion-eligible.
- Not that this generalises until it is seen on a window where leadership is not semiconductors.
- I will not re-pick the window, the endpoints, or the noise bar after seeing the result.

## Pre-committed decision rule

If the treatment shows materially fewer bull circuit-breaker fires AND does not worsen turnover or
drawdown past the bars above, the recommendation is: **enable `semantics_v2` on the production
document and paper-trade forward.** Not fund it. Forward paper on unseen data is the only thing
that can promote it, and that is the operator's call, not mine.

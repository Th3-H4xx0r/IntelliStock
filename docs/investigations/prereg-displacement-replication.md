# Preregistration: replicate the displacement result

Written before any run. This is the only open question in the project with a defined answer.

## Why this and nothing else

Displacement is the single result from 2026-08-14 that clears the corrected ~10pp floor, and it
clears by **0.67pp** on one window against a benchmark built from 17 points, with a floor estimated
from n=1. Everything else measured that day is either noise, a withdrawn claim, or a diagnosis with
no repair. Replication is therefore worth more than any new lever.

## Design

Six runs, strictly sequential, **all to completion** — a killed run produces false negatives, and a
stopped run's return is meaningless. Both were demonstrated on 2026-08-14.

| window | dates | role |
|---|---|---|
| W0 reference | 2026-01-01..2026-03-01 | replicate the observed result |
| W2 bear | 2026-03-02..2026-03-30 | safety veto; the bear leg must not be harmed |
| W3 non-semiconductor | 2026-06-01..2026-07-01 | currently the worst window, and the ONLY window with a trustworthy SPY benchmark to date (-10.14pp) |

Per window: doc 194 (control) vs doc 195 (`satellite_displacement_enabled=True`), same instance
family, cash $6,000, granularity 3600, both arms equally warm, differing in that one operative key
plus their required separate salts.

## Endpoints, fixed now

1. **Return vs SPY**, with `benchmark_quote_logging_enabled=true` on **both arms** so SPY and QQQ
   are logged every tick with timestamps. Do **not** benchmark from fills and do **not** use
   `spy_series`: fills gave 4-17 points depending on whether the core lane traded, and only one
   window in the project's history ever had a series covering it. Verify the SPY span covers the
   strategy window before differencing — a partial benchmark is the same error as a stopped run's
   P&L. `scripts/spy_benchmark.py` refuses series under three points and prints the span.
2. **Turnover** — any rise is disqualifying regardless of return. This is the objective's known leak
   and the mechanism displacement is supposed to help.
3. **Max drawdown** — a materially worse figure is not offset by return.
4. **Funnel** — share of >=30% movers receiving a buy intent. Stable at 17-20% across five runs; a
   lever that does not move it has not addressed the binding constraint.

## Decision rule

Accept only if the treatment beats SPY by more than the dispersion in **at least two of three
windows**, with no turnover rise in any, and no bear-window harm. A single-window win is what we
already have and is not sufficient.

If W0 does not replicate, stop. The 2026-08-14 result was then a draw from the same shared-state
lottery that produced the AGQ artifact, and no further windows are warranted.

## Prior, recorded so it cannot be revised afterwards

Weakly positive on turnover, genuinely uncertain on return. The turnover halving (32 -> 16 trades,
1.80x -> 0.96x NAV core gross) has a clear mechanism — funding buys from existing holdings instead
of opening new positions — and reproduced within a single pair. The return edge does not have an
established mechanism and sits 0.67pp past a floor estimated from one comparison. Expect turnover to
hold and return to regress.

## Cost and scope

Six full runs. On 2026-08-14 the operator's budget was $15 and ten runs were spent, so this needs
explicit authorisation. doc 193 stays untouched; `doc-179`/`alpaca-main` is not involved; all runs
are `pit_mode=research` and not promotion-eligible.

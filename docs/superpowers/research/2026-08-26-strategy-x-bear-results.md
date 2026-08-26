# Strategy X bear-system implementation and backtest report

Date: 2026-08-26  
Instance: `strategy-x`  
Strategy document: `198`  
Backtest budget: 5 of 5 runs used

## Outcome

The system is implemented, but the evidence does not support promotion or a claim that it makes money in every regime. The valid shadow run beat SPY over the full interval because of its leveraged risk-on core, yet it lost much more than SPY in major drawdowns. The active overlay improved several bear windows, but it still trailed SPY in 2022, missed the summer recovery, and ended early on a next-event ownership bug.

The backtest exposed two defects. The code now retains ownership evidence for pending BIL and managed-futures orders, and a disabled Graph lane no longer imposes its six-position cap on Strategy X. Unit and integration tests cover both failures. The five-run limit prevents another API validation run.

## Allocation semantics

The intended risk-on allocation is:

- 10% Graph-ranked stocks.
- 10% commodity ETFs, across at most two names.
- 80% TQQQ.

Graph Nexus ranks stocks; Strategy X owns position sizing. A lane `weight` controls vote influence, not portfolio capital.

The paired comparison disabled Graph Nexus because the historical Graph path lacks frozen point-in-time snapshots and explicitly uses current-state data. Therefore BT244790 and BT867475 did not hold Graph stocks. Their unused 10% stock sleeve routed to SPY:

- 0% Graph stocks.
- 10% commodity ETFs.
- 80% TQQQ.
- 10% SPY fallback.

In active risk-off mode, the overlay replaced the 90% core-plus-fallback allocation with BIL, managed-futures ETFs, and an optional 5% SQQQ kicker. Commodities retained their 10% sleeve. Logged targets summed to 100%, and realized capital never exceeded 100% except for floating-point noise.

## Backtest accounting

| Slot | Backtest | Purpose | Result | Selection use |
|---:|---:|---|---|---|
| 1 | 902983 | Initial off-mode diagnostic | Stopped; wrong allocation and empty cached market data | Invalid |
| 2 | 794182 | Corrected off mode, 2010 start | Stopped; linked IEX feed had no 2010 history | Invalid |
| 3 | 775122 | 2021 coverage and full Graph integration | Stopped; Graph path was lookahead-biased and too slow | Integration only |
| 4 | 244790 | Graph-disabled shadow baseline | Finished; 663 trades | Valid baseline |
| 5 | 867475 | Graph-disabled active overlay | Error at 26.07% | Invalid partial diagnostic |

No sixth backtest was launched.

## Full-period shadow result

BT244790 ran from 2021-11-01 through 2026-08-25.

| Metric | Shadow | SPY |
|---|---:|---:|
| Total return | +120.83% | +66.28% |
| Maximum drawdown | -44.68% | — |
| Trades | 663 | — |

The full-period result does not prove bear protection. Shadow mode records the defensive proposal but executes the original risk-on/risk-off baseline.

## Regime comparison

Returns use the API's persisted equity curve and the nearest available close at each window boundary.

| Window | Shadow | Active | SPY | Reading |
|---|---:|---:|---:|---|
| 2022 full year | -31.81% | -23.84% | -19.49% | Active improved shadow by 7.97 pp but still lost 4.35 pp more than SPY |
| 2022 H1 selloff | -34.89% | -21.88% | -20.37% | Active reduced the loss but still trailed SPY |
| 2022 summer recovery | +10.99% | -1.31% | +12.61% | Active stayed defensive too long and missed the rebound |
| 2022 Q3 selloff | -13.08% | +0.67% | -16.51% | Active provided strong protection |

The active run ended on 2023-02-02. Later active windows are unavailable and must not be inferred from the shadow run.

Shadow-only diagnostics show the leveraged core's upside and downside:

| Window | Shadow | SPY |
|---|---:|---:|
| 2023 recovery | +61.91% | +24.35% |
| 2024 bull | +58.48% | +26.11% |
| 2025 spring drawdown | -34.61% | -17.26% |
| 2025 spring recovery | +28.42% | +21.88% |
| 2026 H1 | +10.96% | +6.18% |

## Defects found and fixed

### Pending bear-order ownership

On 2023-02-01, Strategy X flipped risk-on before the previous risk-off entries had received their next-event fills. It cleared BIL, CTA, DBMF, and KMLM provenance. Those entries filled later, and the next session correctly rejected the now-unprovenanced holdings.

The fix retains validated ownership evidence for every pending Strategy X bear leg. When the execution book cannot be read, it preserves only the strict evidence record, never the loose compatibility list. The ownership guard still rejects genuinely external holdings.

### Disabled-lane position cap

The zero-weight Graph lane still imposed `max_positions=6`. An active kicker target may contain seven legitimate names: BIL, three managed-futures ETFs, two commodities, and SQQQ. Six SQQQ orders were blocked by this stale cap.

The cap resolver now ignores explicitly disabled Graph specs and continues to honor positive-weight or legacy weightless Graph specs. BT867475 eventually completed one small SQQQ round trip, but the six blocks prove the old behavior was unreliable.

## Verification

- `307 passed` across Strategy X, bear overlay, broker wiring, next-event execution, max-position, and regime-cap tests.
- Python compilation passed for both changed production modules.
- The new tests first reproduced each defect, then passed after the fixes.
- BT244790 logs contained zero Graph runtime, point-in-time warning, refusal, or execution error hits.
- BT867475 targets and realized allocation stayed at or below 100%.

## Decision

Do not enable active mode for live money. The overlay needs a fresh API run after deployment, but the approved five-run budget is exhausted. It also needs explicit combined Graph-plus-Strategy-X slot ownership and a decision on stale order cancellation; evidence retention prevents a crash but does not cancel a risk-off order when the next decision flips risk-on.

## TL;DR

- Risk-on target: 10% Graph stocks, 10% commodities, 80% TQQQ.
- Missing Graph stocks route their 10% sleeve to SPY.
- Risk-off replaces the core with BIL, managed futures, and a short SQQQ kicker.
- Active mode protected Q3 2022 but still trailed SPY for the full year.
- The valid shadow run beat SPY overall but had a 44.68% drawdown.
- Two backtest-discovered execution bugs are fixed and covered by 307 passing tests.
- Active mode remains research-only; no result supports guaranteed profit in every regime.

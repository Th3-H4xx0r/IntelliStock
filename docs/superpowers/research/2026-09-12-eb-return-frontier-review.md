# Strategy EB: +300–500% return frontier review

The requested objective is **+300%, +400%, or +500% cumulative profit over 2021-11-01 through 2026-08-27, with maximum backtested drawdown near 25%**. This review finds no verified candidate meeting it. A bounded increase in core exposure deserves investigation; the evidence does not justify promising the target or changing the funded strategy.

This is a source and archived-results sweep, not a new candidate backtest. The brainstorming and installed Karpathy guidelines were used to make assumptions explicit, avoid repeating failed searches, and define a small measurable follow-up.

## Baseline and target arithmetic

The latest handoff quotes the older +233.8% champion, but the current `origin/main` wrapper and pure-module defaults specify the later **bil25** configuration. A live API document was not read, so this is a source/configuration distinction, not a fresh assertion about production state.

| Case | Cycle profit | Ending multiple | Ending $6,000 | CAGR over 4.8186 years |
| --- | ---: | ---: | ---: | ---: |
| bil25 archived engine control, 785201 | +197.783% | 2.978× | $17,866.98 | 25.41% |
| Same holdings plus dated distribution attribution | +207.041% | 3.070× | $18,422.43 | 26.21% |
| K3, target_vol 0.25, archived regime-battery cycle | +219.65% | 3.197× | $19,179.00 | 27.27% |
| Older full-remainder champion | +233.842% | 3.338× | $20,030.51 | 28.42% |
| Requested first target | +300% | 4× | $24,000 | 33.34% |
| Requested second target | +400% | 5× | $30,000 | 39.65% |
| Requested third target | +500% | 6× | $36,000 | 45.04% |

The distribution row is fixed-share attribution, with income outside trading cash; it is not a reinvested engine result. It corrects measurement rather than creating a new trading edge.

I independently recomputed +197.782998% and -21.091995% maximum drawdown from the archived 2,518 raw observations of run 785201. These are historical results, not a reproduction on the latest execution code. The source checkout is at 5fcf177, 60 commits behind the saved origin/main reference at 386287a. Both strategy files changed in that interval. A new engine comparison needs a new same-code control.

## Where performance comes from

After retaining the last observation of each engine date, the 1,259-date control has these average portfolio weights:

| Holding | Mean NAV weight |
| --- | ---: |
| TQQQ | 17.51% |
| GLD | 34.99% |
| GDX | 17.36% |
| XLE | 17.37% |
| BIL | 10.16% |
| Cash | 2.61% |

These are observation averages, not unique-market-session or time-weighted averages. TQQQ is absent on 46.23% of those observations; its maximum observed holding is 64.38%. Gold and miners together average 52.35% of NAV. Raising TQQQ exposure therefore replaces substantial gold/energy exposure; it is not an additive return source.

A diagnostic marking prior-observation holdings to the next observed prices attributes approximately $4,427 to TQQQ, $3,164 to GLD, $2,810 to GDX, $1,592 to XLE, and $17 to BIL. Its -$144 residual against NAV gains includes execution/accounting effects; this is not exact fill-level attribution. The useful conclusion is concentration of historical contribution, not precision of the dollar labels.

The code sizes the core as `target_vol / (3 × max(fast_vol, slow_vol))`, then clamps and floors it to 5% increments. That parameter controls core exposure, not whole-portfolio volatility. The remainder has no covariance-based sizing. The 25% drawdown constraint must be evaluated on the entire portfolio.

## Sweep findings

1. **Core exposure is the best-supported simple research lever.** K3 raised target_vol from 0.20 to 0.25: +219.65% versus +197.78%, with archived drawdown around -21.6% versus -21.1%. This is a modest improvement, not a path already proven to +300%. K3 failed the prior short-window objective; the new cumulative-return objective is different and must be registered separately. Its corrected continuous 2022 H1 return was worse than bil25's.

2. **A larger core cap alone is ineffective at the existing volatility target.** The archived 0.65, 0.85 and 1.00 cap arms returned exactly +233.8418%. Cap changes may matter at higher volatility targets, but cannot be credited with benefit at the old setting.

3. **Removing defensive BIL buys return with drawdown.** Full-remainder champion: +233.84%, -27.54% drawdown. bil25: +197.78%, -21.09%. The full-remainder version already misses the user's approximately 25% limit. The relationship is path-dependent; interpolating these endpoints is not a backtest.

4. **Adding more equity to the remainder has already disappointed.** K1/K2 QQQ remainder allocations returned +181.0%/+186.5%, below bil25. The older SMH25 arm reached +252.4424%, but its separately initialized 2022 and 2026 bear runs returned -4.50% and -0.97%. Its maximum drawdown was not established by the compact result file reviewed here. There is no evidence to treat it as satisfying the new constraint.

5. **Rotation is not an established independent edge.** Iteration 8 found 96% of the apparent rotation improvement disappeared when the gold/energy exposure was neutralized; the best lookback was unstable and turnover increased substantially. The strategy family also underperformed SPY in the earlier 2010–2021 design context. Repeating the same asset/threshold search would add selection bias.

6. **Whole-book protection helps drawdown but has not solved returns.** The prior funded shadow-reference screen returned +230.96%/-21.53% for its own control, +170.92%/-18.65% for its risk ceiling, and +198.34%/-15.50% for its managed-futures variant. These are screen results, not interchangeable engine returns. They do not establish that releveraging the lower-drawdown variant would satisfy the target.

7. **Execution correctness is a prerequisite.** Pending orders, settled cash, next-bar fills, rejected submissions and cache restoration determine realized holdings. Profits produced by duplicate buys or optimistic fill timing must not count as strategy improvement. Zero-cost/passive-execution assumptions were already rejected in earlier research.

8. **Options are not a validated shortcut.** The earlier options study lacked a verified historical bid/ask archive. No new options data access was checked here, and no options-return claim is supportable from that work.

Across the eight committed iteration JSON files reviewed, 24 cycle-result entries were present, including duplicates. None reached +300%; the largest was +252.4424%. This is a statement about those recovered engine artifacts, not proof of an absolute achievable-return ceiling.

## Recommended follow-up experiment

Use a separate research document and instance on a fixed code revision. Do not edit doc 200 or use the funded instance. First reproduce the complete current configuration and its control, including the execution changes since the archived runs. Ensure the research instance has no other pending/running job before each sequential run.

Freeze this eight-cell configuration study before observing results:

| Dimension | Values |
| --- | --- |
| Core target_vol | 0.20, 0.25, 0.30, 0.35 |
| Risk-OFF BIL share of remainder | 25%, 12.5% |

For 25% BIL, retain OFF book GLD 0.375 / GDX 0.1875 / XLE 0.1875. For 12.5% BIL, use GLD 0.4375 / GDX 0.21875 / XLE 0.21875; the remainder falls through to BIL. Keep the ON book, 0.65 core cap, 25-session trend filter, 1%/2% hysteresis, Wednesday-close cadence, volatility windows, execution rules and account size fixed. Values above 0.25 are unverified exposure experiments, not recommendations for live settings.

Score all cells against their same-code control. Do not assume increasing target_vol increases cycle profit monotonically. Exclude a cell from promotion if observed full-cycle maximum drawdown exceeds 25%. Report +300/+400/+500% attainment separately; do not lower the profit target to manufacture success.

For any survivor:

- Verify actual dated distributions on a consistent split basis, settled cash, no borrowing, and next-bar execution. Preserve the historical price-only comparison separately until the total-return engine is validated.
- Repeat at doubled execution costs and a one-session additional execution delay; a candidate that fails the drawdown limit under these stresses is not ready for promotion.
- Evaluate continuous-state bear intervals using actual market-session boundaries. Fresh-cash bear runs are a separate diagnostic: the aligned bil25 2022 H1 loss was -3.13% including distributions, despite positive reset-run figures.
- Inspect calendar years, halves of the cycle, benchmark-relative returns and contribution concentration. Overlapping rolling windows are dependent observations.
- Require nearby configurations to improve consistently. One isolated +300% cell is a research lead, not validation.
- Report drawdown duration and recovery, not just depth. A historical 25% maximum is not a future loss guarantee.
- Treat all previously searched 2021–2026 intervals as development data. Use genuinely untouched history if available and forward paper evidence; reused windows are not a fresh holdout.

Stop after this registered batch and its validation. If no cell clears +300% and the risk limit, record that outcome. A new return source would then need its own economic rationale, data and validation; a larger parameter grid is not evidence of one.

## Evidence and limitations

- `docs/superpowers/research/2026-08-31-strategy-eb-champion.md`
- `docs/superpowers/research/2026-09-03-short-window-preregistration.md`
- `docs/superpowers/research/2026-09-06-eb-causal-results.md`
- `docs/superpowers/research/strategy-eb-iterations/{iter5-results,iter7-results,iter8-results}.md` and eight `*out.json` files
- `.claude/worktrees/main-session/output/research/strategy-eb-2026-09-06/run-785201-pv.json.gz`
- `.claude/worktrees/main-session/output/research/eb-causal-2026-09-06/results.json`
- `backend/strategy_eb.py`, `backend/strategies/strategy_eb.py`, and their saved `origin/main` versions

The GitNexus MCP tools were unavailable, and the CLI query exited with signal 139. Direct source inspection was used; no symbols were edited. No new strategy runs, production mutations, deployments or trades were performed.

Leveraged ETF multiples apply to daily returns; long-horizon returns depend on the path and daily resets. Therefore multiplying EB's historic return or CAGR by an exposure factor is not a valid candidate backtest. See [FINRA's explanation of geared products](https://syndication.finra.org/content/lowdown-leveraged-and-inverse-exchange-traded-products).

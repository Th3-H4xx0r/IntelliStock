# EB: corrected baseline and protection screen

**Decision: keep the running strategy unchanged.** None of the three frozen protection variants improves both bear profitability and bull outperformance. Options performance testing is blocked on historical quote data.

## Correction to the earlier report

All 1,259 daily stored mark vectors match Alpaca IEX split-adjusted closes for the **previous XNYS session** within $0.011. They map to 1,210 actual mark sessions, 2021-10-29 through 2026-08-26. No holdings change was collapsed within a repeated mark session. The original A/A paths remain exactly equal across 2,518 raw rows.

The previous report's February-April 2026 negative interval included a January 30 market loss under a February 2 engine timestamp. Its boundary caveat is now resolved: that was not a February market loss. The correctly aligned, fixed-holdings bil25 results are:

| Actual market interval | Price only | With dated distributions |
| --- | ---: | ---: |
| 2022 H1 | -3.54% | -3.13% |
| February-April 2026 | +3.10% | +3.36% |
| February-April 2025 | +1.59% | +1.75% |
| Calendar 2022 | -1.33% | -0.05% |

Distribution attribution adds $555.45 over the stored cycle: total return rises from +197.78% to +207.04%. This holds the original share schedule fixed and accumulates income outside trading cash; it does **not** claim an engine rerun with reinvestment or changed sizing. K3 still has a worse corrected 2022 H1 result, -4.15%.

Corporate-action amounts were rebased for later TQQQ/XLE splits. KMLM's two 2022 distribution components sum to $4.037692/share; using only one component misses income. The source has conflicting QQQ payment records on 2022-09-19; QQQ is never held here, so its unused distributions were explicitly excluded, not guessed.

## Frozen protection screen

Each account starts at $6,000 and follows the prior close's observable reference weights at the next open. This is a **funded shadow-reference screen**, not an exact broker-engine reproduction. All four cells share the follower delay, daily execution rules, $25 minimum order, SIP prices, costs and payment-date cash accounting. No borrowing. The reference strategy itself is not recalculated inside the follower.

R: trailing 40-session complete-book volatility ceiling 20%, combined GLD/GDX ceiling 40%, released weight to BIL; never increase core weight. D: put 20% of invested non-core reference weight in KMLM. RD: both. Parameters were fixed before candidate results; no second threshold round was run.

| Screen | Cycle return | Max drawdown | 2022 H1 | 2023 | 2024 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Same-model control | +230.96% | -21.53% | -4.05% | +29.70% | +24.90% |
| R: risk ceiling | +170.92% | -18.65% | -3.90% | +29.62% | +19.60% |
| D: managed futures | +198.34% | -15.50% | +0.77% | +27.05% | +21.20% |
| RD: both | +164.86% | -15.70% | +0.50% | +27.71% | +18.93% |
| SPY actual-distribution TR index | +77.94% | — | -19.98% | +26.19% | +24.89% |

D keeps all three selected bear intervals positive under doubled execution costs (+0.57%, +4.38%, +0.60%). That does **not** establish broad bear profitability: only 50.32% of 63-session bear windows are positive, versus 45.22% for the same-model control. Its bull-window SPY beat rate falls from 58.60% to 50.80%; 2024 lags SPY. R and RD also fail the combined objective. No variant is promoted, and a native engine deployment is not justified by these screens.

Costs: 4.4 bps/fill for liquid-tier legs, 23.2 bps/fill for GDX/XLE/KMLM; repeated at 2x. Fund expenses are already reflected in ETF prices. Dividends become receivables on ex-date and spendable cash on known payable dates in the screen. The SPY comparator reinvests actual distributions at ex-date close, a stated index convention. Full 63/126/252-session, calendar 3/6/12-month, sideways and doubled-cost statistics are in the evidence bundle. Of the original 25 battery windows, 22 fit the complete continuous path; three are outside its start/end coverage. On those 22, the control beats SPY in 18, R in 12, D in 13 and RD in 12. These continuous results are not the earlier fresh-start battery. Overlapping windows are dependent; all periods are development evidence.

The prior managed-futures rejection was recovered in `2026-08-27-strategy-x-bear-ladder-results.md`: blends reduced drawdown and returns. Its original instrument-level input data was not recovered. The new screen is consistent with the opportunity-cost finding and does not reverse its failed registration.

## Options and implementation status

The exact linked Alpaca paper account reports options trading/approval level 3. Both official OPRA latest-quote and chain endpoints return HTTP 403: **"OPRA agreement is not signed."** The attempted historical-quotes route returns 404; Alpaca's documented historical options endpoints list bars/trades, not historical quotes. Signing the agreement alone would not supply a validated historical bid/ask archive. No agreement was signed and no subscription was purchased.

The offline gate validates standard same-underlying, same-expiry debit verticals, long-ask/short-bid entry pricing, actual OPRA quote provenance, age, displayed size, integer contract counts, multipliers, fees and aggregate $600 maximum open payoff loss. It is not an order sender, an assignment-risk solution or proof of positive expectancy. The user's annual options loss budget remains unspecified.

Verification: 26 new tests plus 202 existing EB/emulator tests, **228 passing**; immutable A/A equality and cash-plus-holdings reconciliation; all eight sequential screen runs completed. Research code: `scripts/eb_causal_research.py`, `scripts/run_eb_causal_research.py`. No deployed code, strategy document, orders or native engine runs changed.

Reproduce with `python3 scripts/run_eb_causal_research.py` from this worktree. Local evidence is under `output/research/eb-causal-2026-09-06/`; the original saved-run bundle is also required. Input hashes and source-fetch code are retained there.

Primary documentation: [Alpaca corporate actions](https://docs.alpaca.markets/us/reference/corporateactions-1), [historical options coverage](https://docs.alpaca.markets/us/docs/historical-option-data), [latest option quotes](https://docs.alpaca.markets/us/reference/optionlatestquotes), [KMLM distributions](https://kraneshares.com/etf/kmlm/). Account-access conclusions come from the saved authenticated read responses, not generic broker documentation.

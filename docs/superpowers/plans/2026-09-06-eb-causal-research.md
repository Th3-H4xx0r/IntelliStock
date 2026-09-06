# EB causal research implementation plan

> **For agentic workers:** Execute inline with the executing-plans and test-driven-development skills. User approved the report's plan and autonomous execution on 6 September 2026 UTC.

**Goal:** Correct the interpretation of existing EB performance, screen portfolio protection without changing the running strategy, and establish whether an executable options experiment fits $600 maximum open risk.

**Architecture:** A separate, deterministic research package consumes immutable engine snapshots and dated public market data. The saved path is the exact control. Accounting correction preserves its share schedule and holds distribution credits separately; strategy counterfactuals must be identified as research screens until broker-engine parity is established.

**Tech stack:** Python, NumPy, pandas, exchange_calendars, pytest; existing pure EB functions where needed.

**Spec:** output/research/strategy-eb-2026-09-06/report-source.md, approved via user instruction to continue the plan autonomously.

## Global constraints

- Initial research capital $6,000; aggregate open options maximum loss including fee allowance <= $600. Annual options loss budget remains undefined; no live options orders.
- Preserve doc 200. No running-strategy or deployment changes. No concurrent backtests. Existing frozen failed registrations remain unchanged.
- Distinguish unchanged engine control, fixed-holdings accounting correction and policy counterfactual. Do not call a nonmatching offline simulator an engine reproduction.
- Dividend-adjusted marks never receive duplicate dividend cash. Missing quotes or session alignment fail closed.
- Candidate policy parameters fixed below before inspecting candidate performance. All historical periods are development evidence.

## Task 1: accounting and clock audit

Create research module `scripts/eb_causal_research.py`, tests `backend/tests/test_eb_causal_research.py`, evidence under `output/research/eb-causal-2026-09-06/`.

- [x] Write failing tests for exact cash+shares reconciliation, last-before-start interval returns, holiday exclusion, monotonic bar alignment, prior-session dividend entitlement, dividend-adjusted rejection and split-basis mismatch.
- [x] Implement immutable saved-path control and alignment report using independently fetched split-adjusted Yahoo bars; use exchange-calendars XNYS, never a weekday fallback.
- [x] Run actual data audit. Require matching portfolio prices within rounding tolerance and unique chronological session mapping before claiming corrected calendar returns. Report unresolved marks explicitly.
- [x] Correct NAV with dated distributions and eligible prior-ex-date holdings. For this fixed-holdings attribution, accumulated income stays outside the traded book; it is not simulated reinvestment or a changed strategy. Do not infer payable dates from ex-dates.
- [x] Reproduce original A/A and compare original, session-aligned price-only, distribution-corrected values with an identically constructed SPY benchmark.

## Task 2: frozen portfolio-protection screen

- [x] Test long-only sum-of-weights, no future covariance inputs and zero-change identity.
- [x] Control = reference holdings; R = trailing 40-session whole-book risk scaled down to 20% annualized, plus GLD+GDX combined cap 40%, leftover BIL. Never increase reference core.
- [x] D = move 20% of non-core reference allocation to KMLM (chosen for non-equity multiasset trend exposure), R+D = both. Keep parameters fixed and include costs; no alternate thresholds if failed.
- [x] Only run a schedule-based counterfactual if reference session mapping is valid. Such a screen uses the observable reference portfolio and a separate funded counterfactual account; report turnover, continuous bears, bull/sideways periods and SPY comparisons. It is not a direct engine deployment candidate.
- [x] If accounting/session evidence does not support a valid reference schedule, complete tests and report the concrete blocker rather than inventing returns.

## Task 3: options feasibility

- [x] Implement/test a fee-inclusive spread risk gate and quote-quality checks: integer contract quantities, same underlying/expiry, valid strikes, 100x multiplier, no crossed/stale/synthetic quotes, no unbounded short leg, account-wide summed maximum loss.
- [ ] **BLOCKED after access assessment:** select a non-0DTE debit-spread family and evaluate performance only with actual historical bid/ask data. The account returns OPRA agreement not signed, and no quote archive is available; no fallback to trade prints or fabricated historical quotes.
- [x] If quotes/data entitlement are unavailable, report that precise remaining input; do not book hypothetical payoff as strategy return.

## Completion evidence

Run targeted new tests and existing EB/emulator tests. Record all input hashes, method limitations, measured results and next blockers. Review changes before any commit; preserve prior untracked files. Keep the user-facing result short.

### Screen execution contract (fixed before candidate returns)

Native Alpaca IEX closes reproduce all 1,259 stored daily marks exactly within $0.011 rounding tolerance. Yahoo is an independent distribution/price cross-check, not the engine's price feed. Use the independently retrieved consolidated SIP daily bars for the funded overlay screen; no mixing daily IEX and SIP prices within a screen.

The four screen accounts begin at $6,000 and follow the *prior close's* observable reference portfolio at the next open. This adds a common follower delay and is deliberately a separate control from the stored engine. Daily target alignment, $25 order minimum, sell-before-buy funding and common costs apply to all four cells. Use 4.4 bps/fill for TQQQ/GLD/BIL/SPY and 23.2 bps/fill for GDX/XLE/KMLM, then double all costs as stress. No borrowing, no extra core exposure, and no broker deployment claims. Dividends accrue to pre-ex-date holdings; cash becomes spendable at the actual payable date. Unknown payment dates remain receivables. Reference-state dependence is a limitation: a winning screen would still require a native engine experiment.

R: scale risk only downward using the prior 40 completed session total returns, 20% annualized whole-book ceiling, 40% GLD+GDX cap; freed weight goes to BIL. D: shift 20% of invested non-core reference weight into KMLM. No additional parameter rounds. Control and R do not require KMLM history; D and R+D fail closed on missing KMLM bars.

## Outcome

Accounting and the frozen ETF screen are complete. No tested variant satisfies the combined objective, so none advances to production. Options risk/quote checks are implemented; the performance experiment remains blocked on actual historical bid/ask data and the linked account returns OPRA-agreement-not-signed. The checkbox for assessing access is complete; no options performance claim is made. See docs/superpowers/research/2026-09-06-eb-causal-results.md.

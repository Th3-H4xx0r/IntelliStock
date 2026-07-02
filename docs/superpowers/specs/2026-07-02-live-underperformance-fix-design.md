# Live Underperformance Fix — alpaca-main (graph_nexus)

**Date:** 2026-07-02
**Branch:** `investigate/live-vs-backtest-divergence`
**Status:** Approved (aggressive path: fix + tune live now, 8% drawdown circuit breaker)

## 1. Problem

The live real-money instance `alpaca-main` ($6,000 Alpaca account, Strategies doc 179,
graph_nexus) returned **+0.5% since funding (06-09 → 07-01)** vs SPY +1.17% over the same
window, while historical backtests advertised 90–266% over 6 months. The user's goal:
increase profit and beat the S&P.

## 2. Investigation findings (2026-07-02, four parallel forensic pulls)

Data sources: prod RethinkDB (LiveOrderWAL, GraphNexusTradeContexts/Outcomes,
Instances, LiveBootAudit, BacktestResults, Strategies doc 179) and Alpaca live API
(account, portfolio history, activities, orders, positions). Raw dumps in session
scratchpad (`rdb_analysis/`, `account.json`, `orders.json`, etc.).

### Exonerated
- **Execution costs:** $0.27 total fees; slippage mean −1.1 bps (favorable). Negligible.
- **Cash drag:** avg ~76–77% deployed since 06-10 (86% now); explains only ~0.6pp of gap.
- **Drawdown halt:** never triggered (max DD 3.95% vs 12% threshold).

### Root causes (ranked)
1. **The backtest baseline is invalid.** All 180–266% runs share one fixed window start
   (2025-11-10, AI/semis bull leg; `FIXED_START_DATE` in `backend/engines/ai_backtest_engine.py:45`),
   were tuned in-sample across 1,058 stored runs, vary +66%→+266% on identical re-runs
   (LLM nondeterminism), ran `full_every_tick` cadence (live runs dual-cadence; the one
   dual-cadence sim scored +71.7%), filled friction-free at prior-day close
   (`backend/portfolio_emulator.py:24-79`, `broker.py:6210-6240`), and used a different LLM
   stack (Azure gpt-5.4-mini). Current live doc 179 diverges from the +266% config on
   **55 keys**. The current live model (OpenRouter `nvidia/nemotron-3-ultra-550b-a55b`)
   has **zero** backtest validation.
2. **Live buy generation is starved.** June: 3,887 decision contexts, 96.1% hold, only 8
   funnel buys. 63% of contexts end "No graph signal | Base=+0.00 | ML up=0.50 down=0.50"
   — the ML leg is a constant no-op (`nexus_ml_enabled=false`). Live runs half the graph
   propagation width of the validated runs (`max_propagated_scoring_slots` 20 vs 40).
3. **Confirmed defects:**
   - `GraphNexusTradeOutcomes` has **zero rows ever** for alpaca-main despite
     `outcome_tracking_enabled=true` (all 210 rows belong to old `nexus-testing`);
     the learning loop is blind. Likely scope-suffix (`instance_id|config_hash`) mismatch.
   - **CRWV at −19.3% was never cut** past the −15% `fast_loser_cut` (single biggest drag,
     −$113). Suspects: V32 risk exits read the `_trades` entry row
     (`graph_nexus_analysis.py:5123-5181`; silently disabled on missing/malformed row —
     lowercase-action gotcha), and exits only evaluate in the once-daily FULL run, which
     was missed 07-01.
   - Doc 179's embedded Alpaca key returns 401 (dead); real trading uses the
     BrokerageAccounts row key. `Instances.halt_reason` stale since 06-23.
4. **Ops lost ~1/3 of the month.** Of 17 trading days the instance existed: down
   06-08/09/19; codex-cli (`macro_article` role) quota exhaustion killed 06-22/23 with a
   4h16m formal halt + full-day losses; 15-restart churn through the 07-01 open meant the
   daily FULL run never completed — the account fell −2.32% that day with zero response.
   129 boots in LiveBootAudit since 06-06.
5. **Sizing tuned for a bigger bankroll.** $100 `min_position_size` floors +
   `new_entry_reserved_budget` 30% left $800–1,266 idle while 28 priority buy signals
   were refused "no executable size". One $97 position (COHR) even slipped below the floor.
6. **Realized live edge was negative:** 9 closed round-trips, 33% win rate, −$56.08 FIFO.
   All account P&L is two open winners (ATEN/LRCX +$186) minus CRWV (−$113).

## 3. Goals

- Eliminate the confirmed defects so the strategy's own risk/learning machinery works.
- Deploy capital more aggressively at $6k scale (user-accepted risk) with an 8% portfolio
  drawdown circuit breaker.
- Establish the first honest (Nemotron, dual-cadence, multi-window, cost-aware) backtest
  baseline in parallel; it informs iteration 2, it does not gate iteration 1.

## 4. Non-goals

- Enabling `nexus_ml_enabled` blindly (investigate first; flip only if the leg is trained
  and functional — separate follow-up).
- Changing `buy_threshold` (funnel data shows signal generation, not thresholding, is the
  bottleneck).
- Renewing Benzinga (user action: licensing@benzinga.com; same key works once renewed).
- Any change to the Kalshi instance or other strategies.

## 5. Design

### Track A — defect fixes (code; ships first)

**A1. Risk-exit failure (fast_loser_cut / CRWV).**
Root-cause why −19.3% wasn't cut: audit `_get_open_position_entry_trade`
(`graph_nexus_analysis.py:5123-5181`) and the `_trades` seeding path for positions like
CRWV (bought 06-25): missing row, non-lowercase `action`, or ordering violation silently
disables V32 trailing-stop/fast-loser/hold-limit for that position. Fix the root cause
AND make the failure loud (log + Discord when a held position has no resolvable entry
trade). Safety net: evaluate pure risk exits (fast_loser_cut, trailing stop) on the
20-minute MONITOR cadence as well as the daily FULL run, so a missed open never leaves
positions undefended. Risk exits on MONITOR must be sell-only (never generate buys) and
idempotent with the FULL run.

**A2. Dead outcomes/learning loop.**
Find where the TradeOutcomes writer drops alpaca-main (worked for nexus-testing).
Prime suspect: exact-match on scope-suffixed `instance_id` vs base id, or the outcome
measurement job matching on a config_hash that changed (734add9f → 3df5616c). Fix,
backfill June outcomes from LiveOrderWAL where feasible, and add a startup self-check
that alerts if outcome tracking is enabled but no outcome row has been written N days
after the first closed trade.

**A3. codex-cli single point of failure.**
Move `macro_article` role from codex-cli gpt-5.4-mini to OpenRouter
`nvidia/nemotron-3-ultra-550b-a55b` (same as other decision roles) in doc 179 (Track B
config edit; code change here is the failure semantics): single-role LLM failure must
degrade that signal (skip macro articles, log, alert once) instead of tripping the
`LLM critical` kill switch that halts the whole instance.

**A4. Missed-open resilience.**
If the daily FULL run (13:30 UTC anchor) has not completed by a deadline (e.g. 15:00 UTC),
scheduler retries it (bounded retries, once-per-day idempotence guard keyed on date).
Covers restart churn like 07-01's 15-boot storm.

**A5. Hygiene.**
Clear stale `halt_reason`/`halted_at` on healthy boot (when `halt_active` is false and the
boot completes). Remove/replace the dead `alpaca_key` in doc 179 so nothing silently 401s;
document that BrokerageAccounts is the authoritative key source.

### Track B — doc 179 config tuning (real money; ships after A deploys)

| Key | Current | New | Rationale |
|---|---|---|---|
| portfolio drawdown halt | 12% | **8%** | user-selected circuit breaker |
| `profitable_min_hold_conviction_override_enabled` | true | **false** | confirmed net drag (−$130/run, n=2 controlled) |
| `new_entry_reserved_budget` | 30% | **10%** | $800–1,266 idle all June |
| `cash_reserve_floor_pct` | 0.05 | **0.02** | backtest ran 0.01; frees ~$180 |
| `allocation_max_new_stock_buys` | 6 | **10** | matches +266% run |
| priority buys | blocked at reserve | **may tap reserve** | 28 "no executable size" refusals in June |
| `max_propagated_scoring_slots` | 20 | **40** | live runs half the validated signal width |
| `rotation_break_glass_delta` / `raw_score` | 1 / 1.5 | **2.5 / 3.5** | loose rotation drove the −$56 losing churn |
| `rotation_profitable_min_incoming_raw_score` | 1.5 | **2.0** | match validated config |
| `macro_article` role provider/model | codex-cli gpt-5.4-mini | **openrouter nemotron-3-ultra-550b** | kill the SPOF (pairs with A3) |
| `benzinga_*_enabled` | true | **false** (until sub renewed) | all calls 401 since 06-30; removes error spam + latency |

Deployment: **before any edit, snapshot the live doc 179**: (i) a fresh full dump
(secrets redacted) committed to the repo next to this spec, and (ii) the pre-change
values of every edited key stored in Jarvis memory for one-command revert. A redacted
snapshot as of 2026-07-02 is already committed at
`docs/superpowers/specs/2026-07-02-strategy-179-pre-change-snapshot.json` (secrets are
never changed by Track B, so revert = restore the edited non-secret keys only).
Doc-179 edits restart the broker and change config identity. Use the
preserve-history restamp flow (PR #62: `backend/nexus_config_identity.py`,
`backend/nexus_restamp.py`, `POST /strategies/{id}/config-change-preview`) so the change
re-stamps saved state instead of triggering a destructive 85-day lookback. NOTE from the
06-30 incident: if any nexus module code shipped in Track A changes `nexus_module_hash`,
the module hash must be re-stamped too (or accept one lookback deliberately, pre-market).

### Track C — validation in parallel (does not gate A/B)

- First Nemotron backtests, dual-cadence (`dual_cadence_backtest_sim`), with the exact
  current doc-179 config: (i) June 2026 replay (apples-to-apples with live), (ii) the
  Track-B tuned config over 2–3 distinct windows (not just 2025-11-10), ≥2 repeats each
  for variance. Report gross AND with a fill-cost haircut.
- Post-deploy monitoring checklist: outcomes rows accumulating for alpaca-main; win rate
  on closed lots; 8% halt arms correctly; no `STRATEGY ERROR` spam; boots/day back to ~1.

## 6. Rollout order

1. Track A implemented on this branch → tests → PR → merge → deploy pre-market.
2. Verify a healthy FULL run and that CRWV gets cut by the fixed exit (user may close it
   manually sooner — endorsed).
3. Track B config change via preview + restamp, pre-market, then watch first FULL run.
4. Track C backtests launched immediately (independent); results drive iteration 2.

## 7. Testing

- Unit tests: entry-trade resolution failure modes (missing row, uppercase action,
  ordering); MONITOR-cadence risk exits (sell-only, idempotent vs FULL); outcomes writer
  scope matching; scheduler missed-open retry (deadline, single retry per date);
  halt_reason clearing.
- Dry-run Track B through `POST /strategies/179/config-change-preview` and assert the
  restamp plan before applying.
- Local runs use `python3` and may need the `socketio` stub (known env quirk).

## 8. Risks

- **Real-money risk (accepted):** Track B deploys an unvalidated signal harder. Mitigated
  by the 8% halt, tightened rotation, and the confirmed-drag override being disabled.
- **Config identity drift:** mishandled restamp → 85-day lookback or state loss. Mitigated
  by preview endpoint + the documented 06-30 recovery procedure.
- **MONITOR-cadence exits** introduce a new execution path; sell-only + idempotence
  constraints and tests bound the blast radius.
- Backtest costs: Track C burns OpenRouter tokens; keep repeats modest (≥2, not 10).

## 9. Open items (user)

- Renew Benzinga APIs data subscription (licensing@benzinga.com); flip
  `benzinga_*_enabled` back on afterward.
- Optionally close CRWV manually before the fix deploys.
- Rotate the OpenRouter key exposed in a prior session terminal (still recommended).

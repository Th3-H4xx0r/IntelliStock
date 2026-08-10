# 2026-08-09 — the conviction band was smaller than one position

**HEAD:** `9bd7c3c` (clean tree, pushed) · **Tests:** 4,773 passed / 13 skipped
**Doc:** 193 (`v2-let-run-core`) · **Deploy:** hash-verified, `scripts/check_deployed_code.py`
**Goal status:** returns clear 1x on 3 windows. **NOT achieved** — see NEXT STEPS.

---

## THE HEADLINE

The entire conversion failure was one piece of arithmetic:

```
conviction overflow band = (core_target 0.35 − core_min 0.25) × NAV = $600
one position clip        =  total_spend_cap_target_weight_pct 0.14 × NAV = $840
$600 < $840  ->  THE BAND COULD NEVER FUND A SINGLE POSITION
```

That is why the logs read `SATELLITE CAP: SNDK skipped ($12 room / −$1 / −$28)` and why SNDK was
refused six times through a +166% move. `core_min_pct` 0.25 → **0.10** widened the band to $1,500
and took the reference window from **+9.70% → +17.36%**.

Six parallel agents converged on this independently. It was NOT max_positions (blocked 0 times in
all 5 runs), NOT sizing (already 14% clips), NOT exits (net +$262 across 3 runs).

---

## RESULTS — current config, cold state before every run

| window | bt | result | maxDD | churn | top contributors |
|---|---|---|---|---|---|
| bull/chop 2026-01-01..03-01 (2mo) | 571147 | **+17.36%** | 10.6% | 8.49 | SBLK +293, AMAT +274, XOM +225, SLV +219 |
| bull/chop 2026-01-01..03-01 (2mo) | 676939 | **+14.65%** | 6.8% | 7.10 | SBLK +293, AMAT +274, XOM +225, GLD +169 |
| bear 2026-03-02..03-30 (1mo) | 321638 | **+10.44%** | 7.1% | 4.16 | **SQQQ +965** |
| OOS bull 2026-03-30..04-27 (1mo) | 337615 | **+13.35%** | 11.4% | 11.71 | AEHR +482, AXTI +476, AAOI +248, **SQQQ −514** |

Target 1x = +12%/2mo. Pre-session baseline on the same window was +9.70% (bt 915207).
Entry conversion is visibly working: AEHR/AXTI/AAOI were caught early in the OOS window.

---

## CONFIG NOW LIVE ON doc-193 (`strategies[0].config`)

```
core_min_pct 0.10          core_max_pct 0.40      regime_profiles.{bull,chop,recovery}.core_target_pct 0.35
total_spend_cap_concentrate True   total_spend_cap_target_weight_pct 0.14   min_position_nav_pct 0.06
bfq_include_momentum_lane True     momentum_swap_vs_portfolio_enabled False (rotation OFF)
momentum_scan_cached_bars True     momentum_missing_60d_excluded True       momentum_rank_on_60d False
entry_extension_metric "range"     entry_extension_block_pct 25
residual_sleeve_bear_alloc_pct 0.70 (max 0.70)     core_funding_release_reserve_decisions 4
core_funding_max_positions_aware False             max_positions 6
max_positions_exclude_sleeve_legs True             max_positions_honour_regime_cap True
turnover_budget_monthly_pct 0.5    turnover_budget_conviction_bypass_enabled True
overlay_bars_min_history_bars 70   quality_filter_missing_metadata_policy "block"
portfolio_drawdown_halt_enabled True               history_scope_salt "let-run-core-193"
NO bear regime profile (deliberate — keeps the core OFF in a bear so the hedge runs)
```

**Shipped but OFF (validated code, not yet enabled):** `peak_giveback_forced_exit_enabled`,
`bfq_conviction_target_weight_pct`, `momentum_breakout_freshness_pct`, `entry_extension_metric="anchor"`.

---

## MANDATORY RUN PROCEDURE

```bash
python3 scripts/check_deployed_code.py                                    # verify hash FIRST
python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core --apply
python3 scripts/run_validation_backtest.py <start> <end> --cash 6000 --granularity 3600 \
        --instance v2-let-run-core
```
Runs execute REMOTELY on `intellistock-api.pkrishna.dev`. `pull_backtest_logs.py <id> --filter RE
--stdout` is free. **Never push while a run is in flight** — it auto-deploys and kills the run.

Why the reset matters: `GraphNexusActiveEvents` AND the `turnover_ledger` (inside
`NexusStrategyCache`) are inherited between runs. Tick-1 turnover was 56–72% of a 50% budget
before a single decision. Without the reset you are measuring the previous run.

---

## WHAT WAS FIXED (each root-caused from a log, each default-OFF)

1. **Conviction band** — `core_min_pct` 0.25→0.10. THE fix.
2. **BFQ never saw the name** — `_bfq_candidate_syms` is built ~650 lines ABOVE the momentum lane
   that writes `_momentum_new_buys`. SNDK was never *offered* to a queue with 11 free slots; by the
   time it fired natively the queue was 60/60 (399 `full_priority_blocked`). Key `bfq_include_momentum_lane`.
3. **Cash race** — 41 cash-bound buys, $14,801 approved-but-unfunded, 95% of it a same-tick core
   release submitted-and-unfilled. Crediting at the broker gate alone was inert because
   `PortfolioEmulator.execute_signal` re-clamps; the credit now lives in `get_buying_power`.
4. **Core recycles its own release** — a funding release is re-bought as SPY 68/93/40/70% across
   4 runs ($7,104). `core_funding_release_reserve_decisions=4` reserves it.
5. **Min-position floor tested the REQUEST not the FUNDABLE amount** — `AVY 1680.42−83.94−1548.85 =
   $47.63` to the dollar. Adds to held names are exempt (an add takes no slot).
6. **Discovery closed loop** — the momentum screen only saw graph neighbours of names already held;
   `momentum_scan_cached_bars` screens symbols whose bars we already fetched, at zero cost.
7. **Regime cap plumbing** — Z4.1 lifts 6→8 and the broker read the static config. Three hops, all
   now wired + tested.
8. **Infrastructure**: equity curve + `sleeve_churn` on the result row, `BACKTEST_SEED` passthrough,
   `reset_backtest_event_state.py`, `replay_gate_decisions.py`, `simulate_allocation.py`,
   `assess_live_readiness.py`.

---

## MISTAKES I MADE — do not repeat

* **Five inert levers shipped.** `peak_giveback` fired **151 times and sold nothing** (reason string
  not in `_FORCED_EXIT_TAGS`, so `_forced_exit=False`). Also `watchlist_priority_slots`
  (`sector_watchlist` is `{}`), `rank_band_momentum_exempt` (reader 5,606 lines before writer),
  `max_positions_honour_regime_cap` (0 blocks), `backtest_credit_pending_sell_proceeds`.
  **Before claiming a lever works, grep its log signature in a real run.**
* **I cited bt 542754 (+11.94%) as evidence for `bear_alloc=0.35`.** That run logged `alloc=70%`.
  0.35 has never run in a bear. Reverted to 0.70.
* **I shipped `peak_giveback` on one agent's recommendation when another agent had already
  rejected that family across 4 windows.** Cross-check agents against each other.
* **I called AXTI/GLUE runts** — $84/$47 were their LOSSES, not their sizes (they filled $687/$770).
* **A test that mirrors the implementation agrees with itself** — that is how the runt bug survived
  two fixes. Drive the real function.

---

## KNOWN-GOOD / KNOWN-BAD (measured, do not re-litigate)

**Leave alone:** the exit stack. Capture vs actual entry is 99.99%; only 2 non-sleeve sells in
bt 820236, both losers, and they MADE +$303. `trailing_stop_disabled` is correct — re-arming it
kills all 8 big winners (WDC, SNDK×2, LRCX, AMAT, AGMI, XOM, AAOI drew down 6.9–22.4% on the way up).

**Rotation must stay OFF.** Turning it on gave 54 fills / 16 round trips / −3.04%: it sold NTR and
VOYA (winners) to chase new names, and 5/5 rotations sold without buying the replacement.

**Nothing separates winners from losers at entry.** XOM/NTR/VOYA were bought the same bar, same
lane, same score, same size, same extension → +26.9% / +21.2% / −8.9%. Do not build a filter.

**Entry lateness is the whole P&L.** frac-of-move-elapsed-at-fill vs capture r = −0.895 (n=21,
p<0.0001), perfect separation: every fill ≤55% elapsed made money, every fill >100% lost.

**Noise floor ≥4.94pp.** Two runs of the same window/config had 0/18 held-name overlap. Do not
attribute anything smaller than ~5pp to a lever.

---

## NEXT STEPS (in priority order)

1. **Run the non-semi-led window `2026-06-01..07-01`.** THE outstanding overfit test — every window
   validated so far is semiconductor-led (AMAT, AXTI, AEHR, AAOI, SNDK). `fix-generalize` identified
   this as the only non-semi window in the data (leaders RXD +86 / ATEN +26 / UAL +18). If the config
   fails here, it is fitted and the +17% is not real.

2. **Fix the SQQQ bull-window misfire.** It lost **−$514** in the OOS bull run (bt 337615); without
   it that run is ~+21.9%. The sleeve parks into SQQQ on a window that OPENS bear-labelled and then
   turns bull. `docs/investigations/gap-oos.md` has the tested rule: skip opening a bear episode when
   the proxy is at a fresh 20-session low (`bars_since_20d_low == 0`) plus enable the already-written
   `regime_rally_onset_enabled`. Verified ON in bt 542754 (the real bear, untouched) and OFF in
   bt 383778. `bars_since_20d_low` is not logged today — stamp the diagnostic first.

3. **Salted paired arms.** No run has ever used its own `history_scope_salt`. Until then every
   comparison shares mutable Nexus state. Procedure is in `docs/investigations/fix-generalize.md`.

4. **Then evaluate the OFF levers, one at a time, each with its declared log signature:**
   `peak_giveback_forced_exit_enabled` (proven to reach enforcement on the recorded tape, +$112/+$90),
   `bfq_conviction_target_weight_pct` (59/79 conviction drains sized at exactly $100).

5. **Blockers still untouched:** trim-back / disciplined displacement (objective blocker #3 — I
   disabled rotation instead of making it extension-aware), and passive execution (blocker #4,
   `simulated_execution.py:135`, ~22.8 bps/side).

6. **Production is NOT close.** `python3 scripts/assess_live_readiness.py` → `RESEARCH, 0/6`.
   Zero PIT manifests (every backtest is `pit_mode=research`, forced to RESEARCH regardless of
   return), 0 of 60 paper-trading days, and `Instances.<id>.live_readiness_report` has **no writer**
   anywhere in `backend/`. doc-179 / `alpaca-main` is real money and must not be touched.

---

## WHERE THE EVIDENCE LIVES

`docs/investigations/` — 20+ agent reports, all with file:line and log quotes:
`_SYNTHESIS.md`, `_RUNS2/3/4.md`, `gap-{bull,oos,capital,winners,target,bugsweep}.md`,
`fix-{exit-execution,bfq-sizing,core-recycle,audit-levers,generalize,runt-leak}.md`,
`dd-drop.md`, `sndk-priority-block.md`, `ext-still-blocking.md`, `sweep2.md`, `hold-check.md`,
`entry-conversion.md`, `timing-per-stock.md`, `why-late-per-name.md`,
`extension-gate-inversion.md`, `when-to-buy-signal.md`, `losers-and-holds.md`,
`size-vs-outcome.md`, `discovery-and-ranking.md`, `what-made-it-worse.md`,
`local-simulation.md`, `2026-08-08-production-readiness-research.md`.

`fix-audit-levers.md` has a 63-row table of every doc-193 key with per-run fire counts and a
WORKS / INERT / BACKWARDS / UNVERIFIABLE verdict. **Read it before enabling anything.**


---

## APPENDIX — SESSION MEMORY (carry these into the new session)

```
AUTHORITATIVE SESSION STATE 2026-08-09 — supersedes earlier bt-specific memories.
HEAD 9bd7c3c, clean, pushed. Tests 4,773 passed / 13 skipped.
FULL HANDOFF: docs/handoffs/2026-08-09-conviction-band-and-conversion.md

THE FIX THAT MATTERED: conviction overflow band = (core_target 0.35 - core_min 0.25) x NAV = $600
while one clip = 0.14 x NAV = $840. The band could NEVER fund one position. core_min_pct 0.25->0.10
took the reference window +9.70% -> +17.36%.

RESULTS (current config, cold state each run):
  bull/chop 2026-01-01..03-01  bt 571147 +17.36% (dd 10.6) | bt 676939 +14.65% (dd 6.8)
  bear      2026-03-02..03-30  bt 321638 +10.44%  SQQQ +$965
  OOS bull  2026-03-30..04-27  bt 337615 +13.35%  AEHR+482 AXTI+476 AAOI+248 but SQQQ -$514
  target 1x = +12%/2mo. Three windows clear it. GOAL NOT ACHIEVED - see NEXT.

NEXT, in order:
 1. Run 2026-06-01..07-01 — the ONLY non-semi-led window; the outstanding overfit test.
 2. Fix the SQQQ bull-window misfire (-$514 in bt 337615). Rule in gap-oos.md:
    skip opening a bear episode when bars_since_20d_low==0 + enable regime_rally_onset_enabled.
 3. Salted paired arms (history_scope_salt has never been varied).
 4. Then evaluate the OFF levers one at a time: peak_giveback_forced_exit_enabled,
    bfq_conviction_target_weight_pct.
 5. Untouched blockers: trim-back/displacement (#3), passive execution (#4).
 6. Production = RESEARCH 0/6: zero PIT manifests, 0/60 paper days, live_readiness_report has NO writer.

HARD-WON RULES:
 * FIVE inert levers shipped this session. peak_giveback fired 151x and sold 0. GREP THE LOG
   SIGNATURE in a real run before claiming any lever works. fix-audit-levers.md has a 63-row
   verdict table for every doc-193 key.
 * Noise floor >=4.94pp (two runs, same window/config, 0/18 name overlap). Attribute nothing smaller.
 * ALWAYS reset_backtest_event_state.py --apply before a run: GraphNexusActiveEvents AND the
   turnover_ledger (in NexusStrategyCache) are inherited; tick-1 turnover was 56-72% of a 50% budget.
 * Rotation must stay OFF (momentum_swap_vs_portfolio_enabled=False): ON gave 54 fills/16 rt/-3.04%.
 * Do NOT touch the exit stack; trailing_stop_disabled is correct (re-arming kills all 8 winners).
 * Nothing separates winners from losers at entry — do not build a filter. Entry LATENESS is the
   P&L: frac-of-move-elapsed vs capture r=-0.895, perfect separation at 55%/100%.
 * I cited bt 542754 as evidence for bear_alloc=0.35; that run logged alloc=70%. Verify the RUN.
```

Other durable memories in the harness (32 entries) worth querying by name:
  - IntelliStock: verify every mechanism against the RUN, not the config or the code comment
  - IntelliStock: deploy -> hash-verify -> launch -> stop backtest loop (exact commands, v3)
  - IntelliStock: cross-run A/B is invalid — THREE contamination sources, two now cleared per run
  - IntelliStock ROOT CAUSE: entry lateness IS discovery latency
  - IntelliStock: strategy->broker `_nexus_*` values need THREE hops
  - IntelliStock: the turnover budget CANNOT see the core lane — two false comments in broker.py
  - IntelliStock: measured SPY churn cost is ~87% modelling artifact
  - IntelliStock tim-signal: four entry-timing ideas MEASURED AS FAILURES
  - IntelliStock: the turnover ledger is INHERITED between runs
  - IntelliStock: active-event cache misses from STATE drift, not the cache
  - rlm.harness memory API: exact signatures

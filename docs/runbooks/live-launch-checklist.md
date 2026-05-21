# Live Launch Checklist

**Audience:** Operator preparing to start a fresh nexus strategy in live mode on `instance_id="main"` (or any other instance).

**Spec:** `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md`
**Plan:** `docs/superpowers/plans/2026-05-21-live-mode-safe-startup-phase1.md`

## T-24h (evening before launch)

- [ ] **Lock strategy config.** Decide model, prompt versions, history_scope_id ingredients. Do not change between now and launch.
- [ ] **Run a backtest** with `base_instance_id="main"` and `end_date=today`. Use the configured lookback length (default 120 trading days).
- [ ] **Verify backtest log:** look for the line `[snapshot] persisted: id=main|graph_nexus_analysis|<hash>|backtest|<end_date>`. If absent, the snapshot wasn't written; investigate before proceeding.

## T-1h to T-30min (morning of launch)

- [ ] **Liquidate all Robinhood positions.** Sell everything currently held in the broker account. Wait for fills to confirm.
- [ ] **Wait for settlement.** Best-effort: try to launch when cash_available approximately equals cash_total. Live mode now logs `[live_boot] BLOCKER #1 settlement: operator confirmed via launch checklist (no programmatic check)` — the burden is on you to verify settlement manually.
- [ ] **Run cleanup script (dry-run first):**
  ```bash
  python scripts/clear_main_instance_lookback_state.py --instance main
  ```
  Read the row counts to be deleted. Confirm they look reasonable.
- [ ] **Run cleanup script (apply):**
  ```bash
  python scripts/clear_main_instance_lookback_state.py --instance main --apply
  ```
  Expected: summary listing cleared row counts across all 14 per-instance tables. Backtest-origin `NexusStrategyCache` rows should be PRESERVED.
- [ ] **Run validation:**
  ```bash
  python scripts/validate_live_launch_readiness.py --instance main
  ```
  Expected: `VERDICT: GREEN`. If YELLOW, read the warning and decide. If RED, do not proceed.

## T-15min

- [ ] **Start the live instance.** Use the UI button or API call.
- [ ] **Tail the live log.** Look for this boot sequence:
  ```
  [snapshot] decision: reason=ok gap_days=<small N>
  [snapshot] hydrated <N> keys into _strategy_cache[...]
  [lookback] restricted to N gap day(s): [...]   (only if gap_days > 0)
  [live_boot] warm_positions=0, F1b_bypass=disabled, ramp_starting_bar_index=0
  [live_boot] _nexus_full_cycle_completed_date=<yesterday>; next FULL cycle expected ~06:30 AM PT
  [live_boot] BLOCKER #1 settlement: operator confirmed via launch checklist (no programmatic check)
  ```
- [ ] **Confirm Discord post.** If you have Discord notifications configured for the instance, expect a startup ping.

## T+0 (market open / first FULL cycle ~06:30 AM PT)

- [ ] **Watch first FULL cycle.** Confirm first buy/sell decisions appear in the log.
- [ ] **Sanity-check AI Credits card.** Open `/backtests/<recent-backtest-id>`; the AI Credits card should still render (Session #8 feature unbroken).

## Rollback (if anything looks wrong during the first hour)

- Set the env flag on the Instances row: `NEXUS_LIVE_SNAPSHOT_LOAD=off`.
- Restart the broker. A fresh full 120-day lookback will run from scratch (no snapshot used).
- Or stop the instance entirely and investigate before resuming trading.

## What this checklist protects against

- Old (deprecated) nexus version's persisted state (cooldowns, blacklists, peak HWM, discovered-stock "sold" flags) leaking into the new strategy's decision-making.
- Stale Robinhood positions distorting the new strategy's deployment ramp.
- Stale `LiveOrderWAL` entries causing spurious order replays on boot.

## What this checklist does NOT protect against

- Bugs in the new strategy itself (your backtest report is the judge).
- Network or broker outages.
- Sudden config drift made after T-24h (re-run the backtest if you change anything).

## Phase 2 notes

When the versioned per-instance schema (Phase 2 per spec §11) ships, the cleanup script becomes unnecessary. Multiple strategy versions can run side-by-side on the same instance without contaminating each other.

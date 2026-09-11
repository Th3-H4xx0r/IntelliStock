# Live Launch Checklist

**Audience:** Operator preparing to start a fresh nexus strategy in live mode on `instance_id="main"` (or any other instance).

**Spec:** `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md`
**Plan:** `docs/superpowers/plans/2026-05-21-live-mode-safe-startup-phase1.md`

## T-24h (evening before launch)

- [ ] **Lock strategy config.** Decide model, prompt versions, history_scope_id ingredients. Do not change between now and launch.
- [ ] **Verify strict point-in-time coverage.** Historical Graph Nexus runs now
  fail closed unless all four dated inputs (graph, fundamentals, universe, and
  news) are finalized for every requested NYSE session. Run:
  ```bash
  cd backend
  python3 -m scripts.audit_point_in_time_coverage \
    --start YYYY-MM-DD --end YYYY-MM-DD
  ```
  Exit 0 means every session is covered. A missing session or incomplete
  manifest is not valid promotion evidence. Existing rows without
  `pit_provenance=strict_verified` remain classified as legacy and cannot make
  a strict lookback resume or promotion pass.
- [ ] **Capture or import evidence intentionally.** `PIT_CAPTURE_ENABLED=0` is
  the safe default. Set it to `1` only for paper/live FULL cycles whose inputs
  should be recorded; the flag does not start an instance. To validate an
  offline bundle without mutation:
  ```bash
  cd backend
  python3 -m scripts.import_point_in_time_bundle --bundle /path/to/bundle.json
  ```
  After reviewing its manifest ID and hashes, repeat with `--apply` to publish
  snapshots and then the manifest. Never put credentials in a bundle.
- [ ] **Run a backtest** with `base_instance_id="main"` and `end_date=today`. Use the configured lookback length (default 120 trading days).
- [ ] **Verify backtest log:** look for the line `[snapshot] persisted: id=main|graph_nexus_analysis|<hash>|backtest|<end_date>`. If absent, the snapshot wasn't written; investigate before proceeding.

## T-1h to T-30min (morning of launch)

- [ ] **Reconcile the Alpaca account.** Confirm every open position and order is expected for this instance. Quarantine or manually close unrelated holdings before launch.
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
  Expected: summary listing cleared row counts across the per-instance tables. Backtest-origin `NexusStrategyCache` rows should be PRESERVED.
- [ ] **(Optional) Check LiveOrderWAL state manually.** The WAL is globally-scoped (one broker per host -> one WAL) and is NOT cleared by the per-instance cleanup script. Open `r.db("IntelliStock").table("LiveOrderWAL").filter(lambda d: r.expr(["intent", "open", "pending", "partial"]).contains(d["state"].default(""))).count()` in RethinkDB and confirm 0 non-terminal entries before launch (or accept that the broker's WAL reconciliation runs on boot).
- [ ] **Run validation:**
  ```bash
  python scripts/validate_live_launch_readiness.py --instance main
  ```
  Expected: `VERDICT: GREEN`. If YELLOW, read the warning and decide. If RED, do not proceed.

## The live-readiness gate, and the standing waiver

A funded broker will not spawn without a fingerprinted readiness report on
`Instances.<id>.live_readiness_report`
(`instance.py:_assert_live_broker_start_allowed`). The report binds to the
sha256 of the Docker image it was written against.

Two kinds of report live on that key, and they behave differently across a
deploy:

| | Earned report | Operator waiver |
|---|---|---|
| Written by | evidence, gathered | **Waive live-readiness gate…** on the instance page (`POST /instances/{id}/readiness-waiver`) |
| Every check's reason | describes what passed | begins `OPERATOR WAIVED` |
| After a deploy | **invalidated** — it is evidence about one artifact and says nothing about the next | **carried forward** — the launcher re-binds it to the new image |

The carry-forward (`live_readiness.rebind_operator_waiver`, called from
`server._preflight_instance_launch`) is why there is no longer a step here
reading "re-waive after every deploy". Pressing the button before the
instance restarts was a race nobody reliably won, and losing it meant a
funded instance quietly failing to start. The waiver is a standing decision
now: *this instance may start live on my say-so*, not *against image
ab12cd34*.

Each carry-forward is loud and on the record — a RED log line
(`live-readiness waiver carried forward to image <short> for <instance>
(waived by <who> at <when>)`), the same page the waiver itself sends, and
`live_readiness_rebound_at` / `live_readiness_rebound_from` on the row,
shown on the card as **Last carried forward**. `live_readiness_waived_by`
and `live_readiness_waived_at` are never overwritten: the decision was made
once, by a person, on a date.

Nothing is laundered. A report whose persisted fingerprint does not verify is
refused rather than re-signed, a report with one earned check among the
waived ones is not a waiver, and an earned report is never touched.

- [ ] **Ending it is deliberate.** A deploy no longer revokes a waiver, so
  **Revoke waiver** on the card (`DELETE /instances/{id}/readiness-waiver`)
  is the only thing that does. It clears the report and every stamp, and the
  next funded start refuses exactly as it would have before anyone waived
  anything. It refuses (409) on an earned report — that is evidence, and this
  is not the route for deleting it.

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
- Stale or unrelated Alpaca positions distorting the new strategy's deployment ramp.

> Note: `LiveOrderWAL` is globally-scoped (one broker process per host -> one WAL). It is NOT touched by the per-instance cleanup script; the broker's startup reconciliation handles non-terminal WAL entries on boot. See the optional "Check LiveOrderWAL state manually" step above.

## What this checklist does NOT protect against

- Bugs in the new strategy itself (your backtest report is the judge).
- Profitability or outperformance. Strict PIT provenance prevents hidden
  future/current-data substitution; it does not prove alpha or authorize live
  money by itself.
- Network or broker outages.
- Sudden config drift made after T-24h (re-run the backtest if you change anything).

## Phase 2 notes

When the versioned per-instance schema (Phase 2 per spec §11) ships, the cleanup script becomes unnecessary. Multiple strategy versions can run side-by-side on the same instance without contaminating each other.

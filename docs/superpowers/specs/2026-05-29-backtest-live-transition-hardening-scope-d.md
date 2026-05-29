# Backtest→Live Transition Hardening (Scope D)

**Date:** 2026-05-29
**Branch:** `claude-code-integration`
**Origin:** Adversarial re-sweep (Workflow `w023bhyjx`, 8 grouped auditors + single-skeptic verify + critic) of the Scope-C-hardened backtest→live transition, plus a GitNexus call-graph pass and manual verification of every critical/high finding against the deployed code (HEAD `9fd194b`).

> **GitNexus:** connected this session. `gitnexus_impact` is run before each symbol edit and `gitnexus_detect_changes` before commits, per CLAUDE.md.

## Context (instance `main`, 2026-05-29)
- `main` is now the ONLY live instance (nexus-live / nexus-testing deleted). Robinhood, REAL MONEY, acct `REDACTED-ACCT`, MARGIN.
- Scope C (commit `5520a79`) is deployed. **Day-1 first clean-room boot is safe.** Scope D fixes the latent bugs that bite on the first daemon RESTART after `main` has traded, plus several real-money execution/abort gaps.
- `0-A` (WAL dry-filter) and `1-A` (`get_cash` settled-cash cap) were re-verified SOUND and are out of scope.

## Root cause theme
The "first clean-room boot vs. restart" distinction is broken: `2-B` and `2-E` are gated only on the persistent `clean_room_mode` flag (fire every restart, clobbering live state), and `0-B`'s first-boot marker is written too late to be reliable.

---

## Fixes (acceptance criteria + test per item)

### Group A — first-boot gating (critical safety; dominant root cause)

**A1 — 2-B drawdown re-baseline fires on EVERY clean-room boot. [CRITICAL]**
`broker.py:5870` gates only on `if _clean_room_mode:`; `rebaseline_clean_room_drawdown` (live_boot_setup.py) sets `peak=last=equity, halt_active=False` whenever a drawdown state is present, and is fed the FROZEN `_initial_value`. On every restart the daemon-persisted `origin="live"` drawdown state is loaded and wiped → an active drawdown halt is silently cleared and the live high-water mark lost on a real-money account.
- **Design:** Gate the rebaseline on `is_first_clean_room_boot(...)` (reuse the value computed at boot) AND the hydrated snapshot's `origin == "backtest"`; no-op when `origin == "live"`. Thread the loaded snapshot `origin` out of the load path. Use the live portfolio valuation, not the frozen `_initial_value`, for the baseline equity.
- **Accept:** a backtest-origin first boot re-baselines to live equity; a live-origin or non-first boot preserves the stored peak and `halt_active`.
- **Test:** unit — rebaseline gated helper: backtest+first ⇒ reset; live-origin ⇒ preserved; non-first ⇒ preserved.

**A2 — 2-E momentum `first_seen_price` strip never re-seeds + every-boot + snapshot-path-only. [MED]**
`_build_momentum_watchlist` writes `first_seen_price` only for NEW entries (`if sym in watchlist: continue`), so a stripped existing entry never re-seeds → runup buy-gate permanently disabled. Also runs every boot and only inside the `if _snap_cache is not None` branch.
- **Design:** Gate the strip like A1 (first-boot/backtest-origin). Run it once after EITHER load path. Add a re-seed path: the consumer (or builder) re-establishes `first_seen_price` from the current close when missing for an existing entry.
- **Accept:** post-strip, after one live bar, the runup ceiling re-establishes; a live-origin restart does not re-strip.
- **Test:** unit — strip then re-seed restores a positive `first_seen_price`; gating preserves live-origin entries.

**A3 — 0-B cleanup idempotency marker written too late (after adapter build / `sys.exit`, separate conn). [HIGH]**
A first-boot cleanup followed by an adapter-build failure (`sys.exit(5)` at broker.py:5433) exits before the audit "marker" is written → next boot re-runs the destructive wipe (doom-loop on a stale token).
- **Design:** Write a dedicated `{instance}|cleanup-done` sentinel row on the SAME `_setup_conn` immediately after the cleanup succeeds, BEFORE `_build_adapter`. Gate `is_first_clean_room_boot` on that sentinel (in addition to / instead of the audit-row count).
- **Accept:** a cleanup that runs but whose boot later fails does NOT re-run the destructive cleanup on the next boot.
- **Test:** unit — sentinel present ⇒ not-first; cleanup writes the sentinel before adapter build.

### Group B — Robinhood real-money execution / abort

**B1 — 2-F off-hours limit never routed to the extended-hours session. [HIGH]**
`_order_style_for_now` sets `extended_hours=True` but no `market_hours`; the adapter never passes `market_hours`; the engine defaults `market_hours="regular_hours"` and RH routes by that field → off-hours forced exits don't fill.
- **Design:** `_order_style_for_now` returns `market_hours="extended_hours"` (whole-share off-hours) / `"regular_hours"` otherwise. Thread `market_hours` through `submit_order → _submit_order_locked → place_order_equity`.
- **Accept:** an off-hours whole-share exit produces an engine payload with `market_hours="extended_hours"` and `type="limit"`; RTH/fractional unchanged.
- **Test:** unit — capture place_order_equity kwargs (or build_order_payload_equity) for an off-hours whole-share order ⇒ `market_hours=="extended_hours"`.

**B2 — Kill-switch RH cancel has no token refresh → 401 on stale token. [HIGH]**
`_request_json` does not refresh on 401; the kill switch builds its own client and never refreshes → on a stale access token the emergency cancel 401s and real orders stay open.
- **Design:** Before list/cancel, proactively refresh via `RobinhoodClient.refresh()` when near/past expiry (persist back), and retry once on a 401 from list/cancel.
- **Accept:** a stale-token cancel refreshes then succeeds; a 401 surfaces a loud error if refresh fails.
- **Test:** unit — fake client raises 401 once then succeeds ⇒ refreshed + retried + orders_canceled > 0.

### Group C — contamination / correctness

**C1 — 1-C force_fresh leaves stale backtest cache on an empty first-touch fetch. [HIGH]**
The one-shot flag is consumed before the fetch; the cache is re-saved only `if conn and articles`. An empty fetch leaves the stale backtest doc and the flag is spent.
- **Design:** Mark the date refreshed only AFTER a non-empty fresh fetch (re-arm on empty), or invalidate the stale doc when a force_fresh fetch returns empty.
- **Accept:** an empty first-touch fetch does not leave backtest sentiment servable and re-arms the bypass for a later cycle.
- **Test:** unit — force_fresh + empty fetch ⇒ stale doc invalidated / flag re-armed.

**C2 — 2-A quarantine clamp un-adopts strategy shares on a partial-external selldown / corporate action. [MED — FIX ATTEMPTED THEN REVERTED, see Bug-sweep]**
`owned = broker_qty - boot_external_qty` with `external = min(boot_external, broker_now)` mis-attributes a reduction to owned.
- **Attempted design:** a shared `reconcile_clean_room_positions` anchoring owned on the strategy's tracked `self._positions`.
- **Reverted:** the bug-sweep found this is a CRITICAL regression on the live RH path — `RobinhoodAdapter._handle_order_transition` does NOT update `self._positions` on live fills (only the `RH_DRY_RUN` path does), so anchoring on it would quarantine every post-boot real buy as unsellable external. Restored the known-good Scope-C behavior (correct for new buys + strategy sells). The operator-partial-sell-of-an-external-lot edge remains a known **latent MEDIUM** (main has 0 external positions); the correct fix is RH fill-level position tracking (mirror Alpaca's `_on_alpaca_trade_update`) + a real fill integration test — deferred to avoid changing the real-money fill path under this scope.

**C3 — 2-D `normalize_broker_symbol` is dead code; classifier does no normalization. [MED]**
The shared helper is imported only by its test; the classifier matches raw symbols. Latent share-class (`BRK.B`) mis-quarantine.
- **Design:** Call `normalize_broker_symbol` in the classifier's broker + WAL symbol reads (and the RH adapter submit / migration script) so write/read can't diverge.
- **Accept:** a dot-form WAL symbol matches a dash-form broker position in the classifier.
- **Test:** unit — classifier matches `BRK.B` WAL row to `BRK-B` broker position.

### Group D — low / observability

**D1 — 2-C aged-out detection unreachable. [DONE]** The `since_utc` store pre-filter dropped beyond-retention rows before the classifier's `ts<cutoff` branch. Fix applied: both adapters now call `list_filled_for_prefix(prefix, since_utc=None)` so the classifier (which computes its own cutoff and re-filters) can see aged-out rows. Full-table scan cost unchanged.

**RTH half-day warning. [DONE]** `_in_regular_hours` now logs a one-time RED warning when `exchange_calendars` is missing (the weekday fallback ignores holidays / early closes, so post-13:00 ET orders on a half-day mis-route).

**D2 — `LiveBootAudit.snapshot_loaded`/`snapshot_keys` hardcoded False/0. [DEFERRED — by design]** Forensic-only inaccuracy (the `[snapshot] hydrated N keys` log already shows the truth). A clean fix needs a post-hydrate audit-row update (extra connection + row-id plumbing) for no operational benefit; deferred to avoid more boot-path churn in this real-money change.

**D3 — `recent_sell_block` re-arms from inherited WAL SELL rows. [SKIPPED — by design, see note]** The suggested fix (ignore `source=="wal"` sells, or clamp to post-boot trades) would also suppress *legitimate* recent live sells after a daemon restart (live RH fills don't tag a `source`, so they re-seed as `source="wal"` on the next boot) → a real-money **unwanted re-buy** risk. That is worse than the LOW issue. Left to the operator's existing `clear_main_recent_sell_block.py` mechanism. Revisit only with a first-clean-room-boot-scoped clear in the strategy layer.

---

## Bug-sweep outcomes (post-implementation adversarial review of the diff)

A lean 7-reviewer adversarial sweep of the working-tree diff (single-skeptic verify) returned 8 confirmed findings → all resolved before push:
- **C2 reconcile broke the live RH path (CRITICAL)** — reverted to Scope-C behavior (see C2 above). Removed `reconcile_clean_room_positions` + its unit tests + the `_clean_room_classified` gate.
- **A3 sentinel written even on cleanup ERROR (HIGH)** — guarded the marker write with `if not _cleanup_result.get("error")` so a failed/partial cleanup leaves no sentinel and the next boot retries. (This also resolves the related blank-origin-after-adapter-fail interaction for the normal clean-room path, where boot-2 reloads the origin="backtest" 5-segment snapshot and re-baselines via the origin signal.)
- **B2 401 retry covered list_orders but not cancel_order (MED)** — refactored into a one-shot `_with_401_refresh(fn)` applied to BOTH the list and each cancel; added a cancel-401 regression test.
- The reconcile short/negative-position drop (low) and the C2 test-codifies-broken-behavior (med) were resolved by the C2 revert.
- Dismissed (1): operator-CLI `submit_order` ext-hours routing — pre-existing, not in this diff.

## Implementation order & safety
1. Group A (critical safety) → B (real-money exec/abort) → C → D.
2. TDD per fix; every behavior change clean-room-gated or default-off; legacy path unchanged.
3. **Regression gate:** `python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py` — baseline **18** failures; any increase is a regression.
4. `gitnexus_impact` before each symbol edit; `gitnexus_detect_changes` before commit.
5. Parallel bug-sweep the diff; fix all findings; commit per group (footer `Co-Authored-By: Claude Opus 4.8 (1M context)`); never stage AGENTS.md / CLAUDE.md; push `claude-code-integration` (no live doc-179 change).

# Backtest→Live Transition Hardening (Scope C)

**Date:** 2026-05-28
**Branch:** `claude-code-integration`
**Origin:** 9-auditor parallel bug sweep (Workflow `wqe9c2qxp`, 35 agents, adversarial-verified) of the backtest→live transition for contamination / missing-data / live-correctness, + read-only pre-launch verifications V1–V4 against the live `main` (Robinhood, real money) account.

> **Note on GitNexus:** CLAUDE.md mandates `gitnexus_impact` before edits, but the GitNexus MCP is not connected this session. Impact analysis is done manually (reading callers/tests) instead.

## Verified launch context (instance `main`, 2026-05-28)
- Account `REDACTED-ACCT` is **type=margin**: `buying_power $6,967.67` > settled `cash $6,434.48` (Δ $533). `leverage_enabled:false`, PDT False.
- **0 open positions, 0 open orders** (real RH read).
- `LiveOrderWAL`: 21 *real* filled `main-` rows (May 1–12); **0** dry-run synthetic. WAL is not purged by clean-room cleanup → these replay into `_trades`.
- `LiveBootAudit` table absent (0 rows) → first-clean-room cleanup will run.

## Goal
Eliminate every confirmed way backtest / stale / cross-instance / pre-existing-broker state leaks into live decisions, and every confirmed missing-data gap, on the backtest→live transition — without changing legacy (non-clean-room) behavior. Default-off / clean-room-gated where behavior changes.

## Non-goals
- No strategy-logic/alpha changes. No change to the gap-fill design (it is correct). No schema migrations beyond additive fields.

---

## Fixes (acceptance criteria + test per item)

### Batch 1 — WAL / classifier contamination

**0-A — Synthetic & stale WAL fills replayed into `_trades`.**
`RH_DRY_RUN=true` writes terminal `filled` WAL rows with `broker_order_id='dry-…'`; nothing filters them on rebuild, and the WAL is never purged on clean-room boot.
- **Design:** Exclude synthetic rows at the narrowest layer. In `WALStore.list_filled_for_prefix` (`nexus_runtime_state.py`) and `InMemoryStore.list_filled_for_prefix` (`_wal.py`), skip rows whose `broker_order_id` starts `dry-`. Mirror the same guard in `_classifier.py` and any `_seed_trades_from_broker` WAL read. Synthetic fills must never feed `_trades`, `recent_sell_block`, or classification.
- **Accept:** a `dry-` filled row is absent from `list_filled_for_prefix`, classification, and `_trades`; a real filled row is unaffected.
- **Test:** unit on `list_filled_for_prefix` (dry-excluded, real-kept); classifier test with a mixed WAL.

**2-C — Still-held position whose entry fill predates the 180-day retention window is quarantined as external (unmanaged).**
- **Design:** In `classify_broker_positions`, when a broker position has no recent WAL trace (`wal_qty ≤ tol`) or a partial gap (`wal_qty < broker_qty`), do a second **unbounded-lookback** WAL pass for that specific ticker before quarantining.
- **Accept:** a >180-day-old still-held BUY is classified strategy-owned and gets an entry trade.
- **Test:** classifier test with a fill older than `retention_days`.

### Batch 2 — Robinhood adapter

**1-A — `get_cash()` returns `buying_power`, not settled cash → over-deploys on a margin account (ACTIVE on main).**
- **Design:** In `RobinhoodAdapter.refresh_cash` capture both: `self._buying_power = bp` and `self._settled_cash = cash` (from the account summary `cash`/`portfolio_cash`). `get_cash()`/`get_available_cash()` (sizing) return **`min(settled_cash, buying_power)`** — i.e. never size above settled cash. Portfolio **valuation** (`get_portfolio_value`) uses settled cash + positions, not BP. Add a boot log when `bp != settled` so the divergence is observable. Alpaca already returns literal cash — unchanged.
- **Accept:** on a margin account where `bp>cash`, sizing budget is bounded by settled cash; on a cash account behavior is identical to today.
- **Test:** unit on `refresh_cash`/`get_cash` with a margin-style summary (bp>cash) and a cash-style summary (bp==cash).

**2-A — Clean-room quarantine defeated by `refresh_positions(force=True)` rebinding to the full broker set.**
- **Design:** At `__init__` after the partition, capture `self._owned_symbols = set(_owned)` and `self._quarantined_symbols = set(_external)`. In `refresh_positions`, when `clean_room_mode`, after building `new_positions`, drop any symbol in `_quarantined_symbols` (route it back to `_external_positions`). Mirror in `alpaca.py`. New symbols that appear post-boot and match a `main-` WAL fill may join owned; otherwise quarantine.
- **Accept:** after any `refresh_positions(force=True)`, a quarantined external symbol never appears in `_positions`.
- **Test:** unit — init partition, then forced refresh, assert external stays quarantined.

**2-F — RH submits `extended_hours=False` market orders while the trade-gate treats extended hours as open → off-hours forced exits don't fill.**
- **Design:** Add `RobinhoodAdapter._order_style_for_now(price, side, now_utc)` mirroring Alpaca: inside RTH → market/day; outside RTH → marketable **limit** with `extended_hours=True`, `market_hours="extended_hours"`, `tif="gfd"` (RH requires limit for ext-hours). `buy/sell/execute_signal` consult it.
- **Accept:** an exit decided outside RTH submits an ext-hours-eligible limit; inside RTH unchanged.
- **Test:** unit on `_order_style_for_now` for RTH vs pre/after-hours timestamps.

### Batch 3 — Boot / migration

**0-B — First-clean-room cleanup gated on *any* `LiveBootAudit` row (written every boot incl. legacy, after adapter build) → can skip or re-run.**
- **Design:** Decouple idempotency from the per-boot audit log. In `run_first_clean_room_boot_cleanup`, after the wipe succeeds, write a dedicated marker `LiveBootAudit` row `id="{instance}|cleanup-done"` on the *same* connection, before `_build_adapter`. `is_first_clean_room_boot` checks that marker (or counts only `mode="clean_room"` rows), not any row.
- **Accept:** legacy/smoke boots don't suppress the clean-room cleanup; a crash before the forensic audit row doesn't re-wipe state on the next boot.
- **Test:** `live_boot_setup` unit — marker present ⇒ not-first; legacy row present ⇒ still first.

**2-B — Stale backtest drawdown peak rides the snapshot; migration reset only fires >1.40× equity.**
- **Design:** On clean-room boot, after snapshot hydrate + migration block, **unconditionally** re-baseline `_portfolio_drawdown_state` to live equity (`peak_value=last_value=_cur_equity`, `halt_active=False`, `up_days=0`) when the loaded snapshot row origin is `backtest`. Independent of the 1.40× heuristic.
- **Accept:** any backtest-origin snapshot ⇒ day-1 peak == live equity, `halt_active` False.
- **Test:** unit on the re-baseline helper given a backtest-origin cache with an inflated peak.

### Batch 4 — Strategy news / watchlist

**1-C — Aggregate `GraphNexusNewsCache` keyed by `date_key` alone; a backtest row for the launch date is served live (LLM skipped).** (Does not bite the 5/25→5/28 gap, but real.)
- **Design:** In `_fetch_articles_cached`/sentiment adoption, when `_GN_LIVE_MODE_FLAG` and not `historical_lookback_mode`, force a one-time fresh fetch + LLM for the current `date_key` (idempotent via a `_strategy_cache` flag so it fires once per live boot, not every tick).
- **Accept:** first live cycle ignores any pre-existing same-date cache row and recomputes; subsequent ticks reuse the fresh row.
- **Test:** unit — live + pre-seeded same-date cache ⇒ refetch path taken once.

**2-E — Stale `_momentum_watchlist.first_seen_price` from the backtest skews the runup buy-gate.**
- **Design:** Right after `_scp_merge_boot(_nexus_cache, _snap_cache)` in `broker.py`, strip `first_seen_price` (and any runup baseline) from each hydrated `_momentum_watchlist` entry so it re-baselines from live bars.
- **Accept:** post-hydrate watchlist entries have no backtest-era `first_seen_price`.
- **Test:** unit on the strip helper.

### Batch 5 — Kill-switch blast radius

**1-B — Automatic LLM-critical abort is global: halts all instances + cancels real-money RH orders.**
- **Design:** Add `instance_id` param to `live_kill_switch.halt_live_trading`. When provided (the automatic path via `live_critical_abort.handle`): update only that instance's `Instances` row and cancel only that instance's orders. The manual CLI (`python -m backend.live_kill_switch`) keeps the global behavior (no instance_id ⇒ all).
- **Accept:** a paper instance's auto-abort leaves `main.runCommand` and main's RH orders untouched; the manual CLI still halts everything.
- **Test:** unit — `halt_live_trading(instance_id="nexus-live")` issues a filtered Instances update and does not cancel main's orders; no-arg call is global.

### Batch 6 — Tooling / scripts

**0-C — `inspect_broker_state.py` RH branch reads `p.get('symbol')` (absent) → reports 0 owned/0 external for every RH instance.**
- **Design:** Resolve each RH position's symbol via its `instrument` URL (reuse the client / `_instrument_url_to_symbol`), normalized `.`→`-`.
- **Accept:** for an RH account with holdings, the inspector lists real owned/external counts.
- **Test:** manual (script) — covered by the symbol-normalization helper test shared with 2-D.

**2-D — `migrate_external_position.py` writes WAL symbol without `.`→`-` normalization → share-class adoptions stay orphaned.**
- **Design:** Extract `normalize_broker_symbol(s) -> s.strip().upper().replace('.','-')` into `broker_adapters/_symbols.py`; call it from the migration script's WAL write, the RH adapter submit/refresh symbol reads, and the classifier's broker+WAL symbol reads so write/read paths can't diverge.
- **Accept:** adopting `BRK.B` writes WAL symbol `BRK-B`, which the classifier matches to the dash-form broker key.
- **Test:** unit on `normalize_broker_symbol` + a classifier test with a dot-form WAL symbol vs dash-form broker symbol.

---

## Implementation order & safety
1. Batches are mostly file-disjoint; implement sequentially (shared files: `robinhood.py`, `broker.py`, `_classifier.py`).
2. TDD per fix where a unit boundary exists; otherwise targeted unit + manual reasoning.
3. **Regression gate:** `python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py` — baseline **18** failures; any increase is a regression.
4. Every behavior change is clean-room-gated or default-off; legacy path byte-for-byte unchanged.
5. Post-implementation: re-run `/tmp/verify_v1v2.py` + token-safe `/tmp/verify_v3v4.py`; bug-sweep the diff; commit; push (operator pref).

## Blast radius notes
- Doc 179 is shared by main / nexus-live / nexus-testing — no doc-179 writes in this scope.
- `get_cash()` semantics (1-A) feed sizing across the strategy — the `min(settled, bp)` change is conservative (never increases deployment) and Alpaca is untouched.

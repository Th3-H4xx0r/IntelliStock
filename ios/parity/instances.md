# Parity checklist — instances + swing (Wave 2, agent "trading", area 2)

Ports `features/instances/application/{instances,pinned_instances}_controller.dart`,
`features/instances/presentation/{instances_screen,instance_detail_screen}.dart`,
`features/swing/application/swing_controller.dart` and
`features/swing/presentation/{pending_signals_section,wheel_card}.dart`.
`live_logs_panel.dart` is core's shared `LiveLogsPanel`; the data layers (`instance.dart`,
`instance_repository.dart`, `swing_repository.dart`) were ported in Wave 1.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## instances_controller.dart → `InstancesModel`, `InstanceDetailModel`

- [x] `InstanceFilter { all, user, ai }`; `filtered` (ai → `created_by == ai`, user → `== user`), `allCount`, `userCount` (non-ai), `aiCount`.
- [x] `InstancesController`: `GET /instances` (kalshi/crypto excluded by the repository), 30 s poll, lifecycle-aware; `fetch` keeps filter/busy/error from the current value.
- [x] A failed poll or `refreshNow` → error state (Dart `AsyncError`): the screen shows the error with Retry.
- [x] `setFilter` only with data.
- [x] `start`/`stop`/`delete(force:)` mark the id busy, run, then `refreshNow`; an `ApiError` sets `errorMessage` (banner under the list) and clears busy.
- [x] `removeStock` / `addStock` / `createInstance` / `linkStrategy` / `linkBrokerage` then `refreshNow`; errors propagate to the caller (sheets show them).
- [x] `createBacktest(instanceId, stocks, startDate, endDate, granularity '60', initialCash 100000)` (list controller variant; unused by screens, kept for parity).
- [x] `brokeragesProvider` (`GET /brokerages` accounts) / `strategiesProvider` (`GET /strategies`) for the pickers.
- [x] `InstanceDetailController(id)`: `getInstance` + `listBacktests(page 1, completed_at desc)`; `btTotal` (`total` ?? rows), `btTotalPages` (`total_pages` ?? 1); `liveUptimeSecs` = `uptime_seconds` ?? 0.
- [x] Uptime ticks +1 every second while `run_command` is true; restarts from the server value on every `refreshInstance`.
- [x] Backtest progress: every 3 s while any row is running/queued/pending → `GET /backtests/{id}/status` per running row (failures skipped); `progress` stored per id; a terminal status (`completed finished stopped failed error cancelled`) refetches the page; stops when none run.
- [x] `refreshInstance`: success replaces the instance, resets uptime, clears the error; `ApiError` → `errorMessage`.
- [x] `toggleRun`: `POST /instances/{id}/stop` when running else `/start`, then `refreshInstance`; `ApiError` → `errorMessage`.
- [x] `_refreshBacktests`: `btLoading` while fetching with the current page/sort; `ApiError` → `errorMessage`.
- [x] `goToBacktestPage(p)`; `sortBacktests(field)`: same field toggles asc/desc, a new field starts desc; page resets to 1.
- [x] Detail `removeStock` / `addStock` / `linkStrategy` / `unlinkStrategy` / `linkBrokerage` / `unlinkBrokerage` then `refreshInstance`.
- [x] `previewClearState(scope)` (`apply:false`) / `applyClearState(scope)` (`apply:true, confirm:id`).
- [x] Detail `createBacktest` → `POST /backtests` then refetch the page.

## pinned_instances_controller.dart → `PinnedInstancesModel`

- [x] Key `pinned_instances`, a JSON array string in the keychain (`flutter_secure_storage` layout); unreadable/non-list → empty; best-effort.
- [x] `toggle(id)` adds/removes and persists `jsonEncode(list)` in insertion order.
- [x] `sortPinnedFirst(items, pinned)`: pinned first, each group's order kept; unchanged when nothing pinned.
- [x] → native form: hydrated synchronously at init (the keychain read is synchronous), so pins show on the first frame.

## instances_screen.dart → `InstancesView`

- [x] Loading → skeleton (header, pills, 4 cards); error → `ErrorBanner(e)` + Retry (re-load).
- [x] Header eyebrow `TRADING`, title `Instances`, `Manage live trading and backtesting instances.` → native form: large nav title `Instances`; eyebrow + subtitle as the list header; Refresh (label `Refresh`) and `New Instance` as toolbar items.
- [x] Pull-to-refresh → `refreshNow`.
- [x] Filter pills `All (n)`, `User Created (n)`, `AI Created (n)` → native form: segmented `Picker`.
- [x] No instances → EmptyState `memory`, `No instances yet`, `Create an instance to run live trading or backtesting strategies.`, `Create your first instance` → native form: `Create Your First Instance`.
- [x] Filter empty → `filter_alt_off`, `No AI-created instances` / `No user-created instances`.
- [x] List = filtered, pinned first; `errorMessage` banner below the list.
- [x] Card: memory tile, name (or id), id (mono), `AI`/`User` badge, status `Crashed` (red) / `Running` (green, pulsing) / `Stopped`.
- [x] `Strategy: <strategy.name ?? strategy_id>` or italic `No strategy linked`; `Brokerage: <account_name (brokerage_type)>` (or id) when linked.
- [x] `STOCKS (n)` + `Add` (disabled while busy) → Add Stock sheet; chips with ✕ remove (no remove while busy); italic `No stocks added`.
- [x] Actions: `Pin`/`Pinned` (toggle), `View` → `/instances/{id}`, `Live` → `/instances/{id}/live`, `Stop` (running) / `Start`, both disabled + spinner glyph while busy; `Delete` (disabled while busy) → confirm.
- [x] Delete confirm: `Delete Instance`, `Delete "NAME"? This cannot be undone.`, `Delete`, danger → native form: destructive `.alert`; trigger disabled while in flight; `onError` toast.
- [x] Start/Stop have no confirmation in the Dart → none added (byte parity); a busy guard prevents a double submit.
- [x] Create sheet `New Instance`: `Instance ID *` (`my-instance`), `Name` (`Optional display name`), `Granularity` (`1 min` 60 · `5 min` 300 · `15 min` 900 · `1 hr` 3600 · `1 day` 86400, default 60), `Start after creation` toggle, `Brokerage (optional)` (`— None —` + `account_name (type)`; loading / `Failed to load brokerages`), `Max Usage ($)` (`e.g. 1000`, `double.tryParse`), `Strategy (optional)` (`— None —` + name or id; `Failed to load strategies`); `Instance ID is required`; error → banner; Cancel / `Create` (busy) → native form: sheet with `Form`, segmented granularity, `Picker`s, toolbar Cancel/Create.
- [x] Add Stock sheet: `Add Stock`, `Symbol *` (`e.g. AAPL`, upper-cased, trimmed), `Symbol is required`, `Add` → native form: sheet with `Form`.

## instance_detail_screen.dart → `InstanceDetailView`

- [x] `_granLabel`: nil `—`, `<60` `Ns`, `<3600` `Nm`, `<86400` `Nh`, else `Nd`. `_fmtUptime`: ≤0 `—`, `Hh Mm Ss` / `Mm Ss` / `Ss`.
- [x] Loading skeleton; error → banner + Retry; no instance → `Instance not found`.
- [x] Breadcrumb `Instances / NAME` → native form: system back button (`Instances`) + inline title.
- [x] Pull-to-refresh: `refreshInstance`, and re-load pending signals / wheel when those lanes exist.
- [x] Header: memory tile, name, `AI`/`User`, id; status badge; `Stop`/`Start` (toggleRun); `Live Trading` → `/instances/{id}/live`; Refresh (label `Refresh`).
- [x] `Instance Info` + `Clear State`; tiles `Uptime` (green while running), `Granularity`, `Max Usage` (when set), `Created By` (`AI`/`User`).
- [x] `Brokerage` card: `Change`/`Link` → Link Brokerage sheet; `Trading Account: name (type)` (or id, or `—`); `Market Data Source: alpaca_data_brokerage_id or —`; `Unlink Brokerage` → confirm `Unlink Brokerage` / `Remove the brokerage from this instance?` / `Unlink`.
- [x] `Strategy` card: `Link` (none) → Link Strategy sheet, or `Unlink` → confirm `Unlink Strategy` / `Remove the strategy from this instance?` / `Unlink`; italic `No strategy linked`; `Name:`, `ID:`; `Sub-strategies` (first 5 `strategy` names, `?` fallback) + `+N more`.
- [x] Swing lanes (`swingLanesOf(strategy)`): pending signals section when any lane; wheel card when the wheel lane.
- [x] `Stocks (n)` + `Add` → Add Stock sheet; chips with ✕ (`removeStock`); italic `No stocks added`.
- [x] Live logs panel (core `LiveLogsPanel`).
- [x] `Backtests` + `New Backtest` → Create Backtest sheet; skeleton while loading empty; `No backtests yet`; sort `Date` (completed_at) / `PnL` (pnl) with arrow on the active one, `unfold_more` otherwise; rows: stocks or `(no stocks)`, status badge (pulsing when running), `start → end` (`?`), PnL (`fmtPnl`, coloured), progress bar + `N%` while running with progress, `Completed: <fmtDateTime>`; tap → `/backtests/{id}`; pagination `Page p of n` with prev/next.
- [x] Clear State sheet `Clear Instance State`: scopes `Lookback only` / `DB strategy cache only` / `Full instance reset` with their blurbs verbatim (radio) → native form: `Picker(.inline)` in a `Form`; `Preview Dry Run` (busy) → `Preview Results`, `Total rows to delete: N`, `• table` (≤10); typed confirm `Type "ID" to enable destructive confirm` (exact phrase = instance id); `Confirm and Clear` (danger) only enabled when matched; result `Deleted N row(s) across N table(s).`; errors `Preview failed: e` / `Clear failed: e`; controls disabled while busy.
- [x] Add Stock (detail), Link Brokerage (`Select a brokerage`), Link Strategy (`Select a strategy`), Create Backtest (`Stocks (comma-separated)` `AAPL, TSLA`, `Start *`/`End *` `YYYY-MM-DD`, `Granularity`, `Initial Cash ($)` `100000`; `Start date is required`, `End date is required`, `End date must be after start date` (string compare); stocks split/trim/upper/non-empty; cash `double.tryParse ?? 100000`) → native form: sheets with `Form`, toolbar Cancel / submit (busy, disabled).

## swing_controller.dart → `PendingSignalsModel`, `WheelModel` (+ free functions)

- [x] `SwingLanes`, `swingLanesOf` (canonical id: trim, camel → snake, lower; `strategy_swing`, `strategy_wheel`; non-list/non-map ignored).
- [x] `decisionLabel`, `decisionConfirmBody`, `decisionSuccessMessage`, `uncertainMessage`, `kUncertainApproval`, `stuckAfter` (2 min), `stuckApprovals`, `stuckLabel`, `nyDate` (exact US DST rule), `resendBlockedReason`, `resendConfirmBody`, `uncertainBadge`, `waitingCopy` — all copy verbatim.
- [x] `DecisionOutcome` (recorded, uncertain, noLongerPending, failed, ignored), `DecisionResult`, `UncertainCard` (`badge`, `canDismiss`, `settled`).
- [x] `PendingSignalsState` (signals, stuck, uncertain, deciding, resending, refreshError, asOf).
- [x] Build: clears hides; generation; pending + approved read in parallel, settled independently; submitted/failed read only while a card waits; no pending list → error; first state via `_apply`; then a 30 s poll, lifecycle-aware.
- [x] Pull-to-refresh / Retry rebuild (same model: private maps survive, hides cleared).
- [x] `refresh`: new generation; an older fetch landing after a newer one is dropped; both reads failed → keep state + `refreshError`; else `_apply`.
- [x] `_apply`: fold uncertain cards (pending → removed; submitted WITH order key → `submitted`; failed → `failed`; still approved > 2 min after the 202 → joins stuck); joined ids pruned to those still approved; stuck = age rule + joined (unless snoozed by a re-send) minus uncertain and dismissed; signals filtered by hides and same-fetch approved ids; first error → `refreshError`.
- [x] `decide`: ignored while deciding or hidden; deciding set; 2xx → hide at current generation, forget dismissal, drop; 202 → waiting card + server detail (or `uncertainMessage()`); 400/404/409 → dropped, `This signal is no longer pending — it was decided elsewhere.` fallback; 401 `Session expired — please sign in again.`; 403 detail or `You are not allowed to decide this signal.`; 503 detail or `Not queued — the signal is still pending; try again.`; other `Could not record that decision.`; non-API `Could not record that decision: e`.
- [x] `resend`: ignored while resending; 2xx → snooze, `Re-sent SYM to the broker.`; 202 → waiting card, `uncertainMessage('Re-send')`; 404/409 → snooze, detail or `Nothing to re-send for this signal.`; 401; 503 detail or `Not queued — try again.`; other detail or `Could not re-send that approval.`; non-API `Could not re-send that approval: e`.
- [x] `dismissStuck`, `dismissUncertain`.
- [x] Disposed during the first fetch → no poller outlives the screen (the poll runs inside the view's task).
- [x] `wheelSnapshotProvider` → `WheelModel` (load, Retry, pull-to-refresh).

## pending_signals_section.dart → `PendingSignalsSection`

- [x] Title `Pending AI signals` / `Pending AI signals (N)`; `Loading…`; error banner + Retry.
- [x] `Last refresh failed: E` (warning); `Nothing waiting for review.`; signal cards.
- [x] Signal card: symbol, lane badge (wheel accent, swing info), score badge (≥75 green, ≥50 amber, else red, nil `—`), `session X`/`—`; proposal grid (swing ENTRY/STOP/TARGET/SHARES; wheel CONTRACT/STRIKE/EXPIRY/QTY/LIMIT/PREMIUM `$p ($credit)`/COLLATERAL); reasoning (collapsed to 4 lines past 240 chars, `Show more`/`Show less`); `Risks: …`; buttons `Approve`, `Approve ½` (swing only), `Reject`; inert while deciding with `Working…`.
- [x] Decision confirm: title `<Label> SYM`, body `decisionConfirmBody`, confirm `<Label>`, reject danger / approve success → native form: `.alert` (reject destructive role); the result shows as a toast except ignored/uncertain (which live on the waiting card).
- [x] Waiting cards: `Waiting for the broker (N)`; symbol, lane, badge (`UNCERTAIN — WAITING FOR THE BROKER` / `SUBMITTED` / `FAILED`, upper-cased like `AppBadge`), session, copy (`waitingCopy` / submitted / failed text verbatim), `Dismiss` when `canDismiss`.
- [x] Stuck cards: `Approved, not yet sent (N)`; symbol, lane, `approved`/`approved ½`; session; `stuckLabel`; blocked reason or `Re-send` (confirm `Re-send SYM` / `resendConfirmBody` / `Re-send`, warning) + `Dismiss`; inert while resending with `Working…`.
- [x] Footer `Approval rebuilds the order at the live price.`
- [x] Confirmations are REAL trading actions: kept verbatim; a local busy flag also disables the trigger while the request runs (orchestrator, 2026-10-01).

## wheel_card.dart → `WheelCard`

- [x] `fmtItm` (`N.N% ITM` / `N.N% OTM` / `—`), `fmtAsOf` (`as of HH:MM`, local), `wheelErrorMessage` (FastAPI `Not Found` 404 → `This API build has no wheel endpoint yet.`; else the error).
- [x] Title `Wheel` + `as of`; `Loading…`; error banner + Retry.
- [x] Stats `OPEN PUTS` / `COLLATERAL` / `CASH`; put rows (`UND $strike P · expiry`, contract mono, QTY/ENTRY/MARK, ITM (red when the monitor will buy back, amber ITM, green OTM, dim nil)/DTE/P&L (dim when nil)); `No open puts.`; `RECENT SCANS` top 5 (`SYM $strike P · expiry`, skip reason, status badge placed/pending/rejected colours); `No scans recorded yet.`.

## Tests ported

- [x] instances_filter_test → `InstancesStateTests` (+ Instance/InstanceBacktestRow parsing).
- [x] pinned_instances_test → `PinnedInstancesTests` (+ keychain persistence).
- [x] swing_controller_test (all groups) → `PendingSignalsModelTests` with `SwingFakeSource`.
- [x] pending_signals_section_test (logic) → `PendingSignalsSectionLogicTests` (proposal fields, labels, wheel copy).
- [x] swing_fakes → `SwingFakeSource`, `swingTestSignal`, `approvedTestSignal`, `wheelTestSignal`, `withTestStatus`.

## Copy changes (HIG title case)

- `Create your first instance` → `Create Your First Instance`.
- Native nav titles: `Instances` (tab root), the instance name (detail); sheet titles as in Dart.

## Rulings

- Ruling: start/stop instance keep Dart's behaviour of no confirmation (only delete, unlink and clear-state were confirmed) — byte parity; the brief's "native confirmations" applies to the confirmations that exist — cost if wrong: one alert to add.
- Ruling: every confirmed action (delete, unlink, decision, re-send, clear state) disables its trigger while the request is in flight (core's `confirmAlert(_:isRunning:)` after fix round 1) and passes `onError` (orchestrator instruction) — cost if wrong: none.
- Ruling: while one swing decision or re-send runs, every card's actions are inert (Dart disabled only the card being decided) — core's confirm runner drops a second request while one runs, so an enabled button would raise an alert whose confirm does nothing; stricter on a real-trading control — cost if wrong: a second approval waits for the first to land.
- Ruling: the uptime ticker and backtest-progress poll run while the detail screen is on screen (task-scoped); Dart's `Timer.periodic` ran until dispose — cost if wrong: none.
- Ruling: `PinnedInstancesModel` lives on the Instances tab root's `@State` (app-lifetime, like the keepAlive provider) — cost if wrong: none.
- Ruling: the swing repository is reached through a `SwingSignalsSource` protocol so the ported fakes can stand in, exactly as Dart's `FakeSwingRepo implements SwingRepository` — cost if wrong: none.
- Ruling: a pending-signals rebuild failure shows the error state (Dart `.when` error branch) even when an older list existed — cost if wrong: none.

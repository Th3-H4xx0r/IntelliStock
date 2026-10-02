# Parity checklist — nexus + learning (Wave 2, agent "markets")

Ports `mobile/lib/features/nexus/{application,presentation}/**` and
`mobile/lib/features/learning/{application,presentation}/**`, plus the presentation parts of
`test/features/nexus/nexus_test.dart`. Agent Runs belongs to the "app" agent (scope change in the
combined brief).

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## nexus_controller.dart → NexusModel

- [x] `PollingNotifier`: first fetch, then every 2 s while building, 5 s idle (interval re-read every cycle) → `PollingLoop`, paused in the background.
- [x] `fetch` keeps `busy` from the previous state and clears the error message.
- [x] `postControl` / `rebuild` / `deleteEdges` through `_withBusy`: busy on, error cleared; failure → busy off + `e.toString()`; success → refresh now.
- [x] `fetchCache` → nil on error.

## nexus_screen.dart — NexusView

- [x] Loading skeleton → native form: redacted placeholder cards.
- [x] Load error → hub tile, "Unable to load Nexus status", "Check that the backend is reachable.", Retry.
- [x] Header "Nexus Graph" + "Knowledge graph builder — S&P 500 company relationships." → native form: inline title "Nexus Graph" + subtitle; status pill Building (info, pulsing) / Ready (success) / Idle.
- [x] Buttons: Start / Re-run (hidden while building; Re-run when built and running), Stop (when running → `{'running': false}`), Auto-update, Full Rebuild (disabled while busy), Delete edges (disabled while busy, running, or a rebuild is active) — busy shows a spinner on Start / Stop.
- [x] Error banner (`errorMessage`).
- [x] Auto-update card: "Auto-update", summary ("Disabled" / "Every N day(s)" / "Every N hour(s)"), "Next: … / not scheduled", "Range: start → end" (labels from control, else options, else the fallback phase labels), "13F history: N quarter(s)", Configure.
- [x] Historical Bootstrap card: status badge text (Bootstrap Ready / Running / Never Built / Disabled / Bootstrap Partial / Bootstrap Pending) and colour, coverage summary, completed stats grid (Coverage, Duration, Phases, Completed relative).
- [x] Graph Counts card (when any counts): "Current Neo4j relationship totals.", Companies / Intervals, per relationship label + key + active count + "N total" / "active"; `_fmtNum` (k / M, 1 dp).
- [x] Built section: "Knowledge Graph is Built", "c of n stages completed · Ready for strategy use.", Rebuild / Delete edges; tiles Stages Done / SEC Tickers / SEC Edges / Last Updated; Stage Summary with Total Runtime and per-stage rows (status icon, label, duration).
- [x] Building / idle section: phase label ("Building graph…" / "Graph not yet built"), message, "N%" + "~eta remaining", progress bar; Build Stages stepper (spinning icon while running, connector line, label colour by status, duration / "Running…" / "pending", message, substep bar "c/t substeps") or "No stage data yet." / "Start the Nexus engine to begin building."
- [x] `_stageColor`, `_stageIcon`, `_fmtDuration`, `_autoUpdateSummary` ported 1:1 (unit-tested).
- [x] Start modal: "Start Nexus" / "Re-run Nexus", "Choose phases for this manual execution.", Phase Selection All / None + checkboxes, "Select at least one phase.", "13F History (quarters)" (`int.tryParse ?? 1`), Historical bootstrap switch + start date, Force bootstrap rebuild switch; submit disabled without phases; blocked when historical mode has no date; body `running`, `phase7_history_quarters`, `historical_mode_enabled`, conditional `historical_start_date`, conditional `force_bootstrap_rebuild`, sorted `selected_phases` → native form: sheet with a Form.
- [x] Auto-update modal: "Nexus Auto-update", "Keep Nexus online and rerun on a schedule.", Enable switch + caption, "Interval (hours)" (`int.tryParse ?? 168`), From / To phase pickers (value falls back to the first option), "Save Schedule"; body `auto_update_enabled`, `auto_update_interval_hours`, `auto_update_start_phase`, `auto_update_end_phase`, `running: true` when enabled → native form: sheet with a Form.
- [x] Full Rebuild modal: mode (In-place rebuild / Destructive rebuild with descriptions), Force bootstrap rebuild, Cache cleanup (loading "Loading cache…", Select all / Clear, entries with folder / file glyph and "N bytes", "Cache not accessible from this context." / "No cache entries found."), typed confirm "confirm", CTA "Confirm Rebuild" / "Confirm Destructive Rebuild" enabled only when typed; body `confirm: true`, `destructive`, `force_bootstrap_rebuild`, `delete_cache_paths` → native form: sheet with a Form and `TypedConfirmField`.
- [x] Delete modal: "Delete Nexus Edges" / "Deleting Nexus Edges" (progress), Phase Selection All / None, "Select at least one phase.", "Delete selected edges" disabled without phases; body sorted `selected_phases`; progress view (Overall Progress "c / t unit", step, error, per-phase rows with status badge, bar, "c / t unit", "N deleted"), Close → native form: sheet.
- [x] Modals close before the request, as in Dart; the model's busy flag disables the header buttons until the refresh lands.
- [x] Pull-to-refresh.

## nexus_logs_panel.dart → NexusLogsPanel

- [x] `LogTailer` on `/nexus-graph-builds/latest/logs?since_line=n`, 2 s while building / 15 s idle; starts on first open, pauses on close, pauses in the background.
- [x] Header: pulsing status dot (info / danger / success / faint), "nexus-<last 8 of build id | latest>.log", friendly status + "· N lines", "(last 500 — log file not available)", Pause / Resume and Copy (open with lines), "View Build Logs" / "Hide Logs".
- [x] Search field "Search logs..." (case-insensitive contains); loading "Loading logs…"; empty copy ("Can't reach logs endpoint. Retrying…", "No active build. Trigger a Nexus build to see live logs.", "Waiting for log output…"); rows with "MM-dd, HH:mm:ss" stamps and level colours; sticky bottom with "Jump to latest" → native form: glass "Jump to Latest" button.
- [x] Copy → native form: `UIPasteboard` + "Copied" toast.

## learning_controller.dart → LearningModel

- [x] Seven endpoints in parallel, each failure recorded as "label: error" (overview, findings, runs, approvals, noise floors, control, targets); `engineRunning` from control `running`; `mode` from `config.mode ?? 'observe'`; `partialError` joined with "; "; throws when nothing loaded.
- [x] `liveApprovals`, `observeOnly` (`!actsAutonomously`), `isEmptyFailure`.

## learning_screen.dart — LearningView

- [x] Title "Learning" (inline); loading "Loading learning data…"; error banner + retry.
- [x] Header pills: "Observe only" (info) or the mode (success, pulsing); "Engine on" / "Engine off"; tiles Open findings / Runs observed / Decisions / Refusals.
- [x] Controls card: "Engine running" / "Engine stopped" + Stop / Start → `POST /learning/control {running}`; Mode chips observe / propose / act → `{config: {mode}}` → native form: segmented Picker; targets button (`_targetsLabel`); web-only caption.
- [x] Partial error banner.
- [x] Pending approvals: "No approvals waiting" + observe-only copy, or cards (rung badge, action class, "doc N", summary, target / "· this one waits until you answer" for live rungs, Approve → `approved`, Reject → `rejected` via `POST /learning/approvals/{id}`); failure SnackBar "Could not record that decision: …" → native form: error Toast.
- [x] Measured noise floors: "No floor measured yet" + copy, or rows (target, window class, reason when unmeasured, "N.NNpp" / "—").
- [x] Findings & reports: EmptyState "Nothing raised yet" / "Findings appear as completed runs are observed.", or expandable cards (severity badge, target, title, detail; ladder: Detected + evidence lines + six locked rungs with "not reached — the subsystem observes only").
- [x] Observed runs: EmptyState "No runs observed yet", or rows ("Run id", target, "d decided · e executed · r refused", "N.N%" (red under 25) / "—", "buy conv.").
- [x] Engine / mode failures → "Could not change the engine: …" / "Could not change the mode: …" → native form: error Toasts.
- [x] Targets sheet: "Documents & instances"; "Documents the subsystem may write to" + "Empty means it writes nowhere." with checkboxes (real money / unverified badges, "#id · not attached to an instance" / "#id · names"); "Instances to watch" + "None selected — watching every instance." / "Only the selected instances are observed." with checkboxes (real money / unverified / running badges, "kind · doc #id"); error banner; "Saving…" / "Save" → `setDocumentAllowlist` then `setWatchedInstances`; closing refetches.
- [x] Pull-to-refresh awaits the refetch.

## Copy changes (title-style capitalisation only)

"Delete edges" → "Delete Edges"; "Delete selected edges" → "Delete Selected Edges"; "Select all" → "Select All".

## Rulings

- Ruling: the Dart nexus_test.dart "Phase-selection payload", "Auto-update summary label" and "_fmtDuration" groups exercised inline replicas that differ from the shipped screen (an `action: start` / `force_bootstrap` payload, an "Every 1d · from → to" label, no hour branch). The port follows the screen code that actually ran, and the Swift tests assert the screen's behaviour — cost if wrong: none, the screen is what the operator used.
- Ruling: Approve / Reject disable both buttons while the decision is in flight, and engine / mode / targets saves disable their triggers — orchestrator guidance (no double submit); Dart had no guard — cost if wrong: none.
- Ruling: a cancelled request leaves state untouched and shows no error — orchestrator guidance — cost if wrong: none.
- Ruling: the Nexus modals (Dart `Dialog`s) become sheets with Forms; Full Rebuild keeps its typed "confirm" gate as an inline `TypedConfirmField` — cost if wrong: none.
- Ruling: the spinning stage icon uses SF Symbols' rotate effect and stops under Reduce Motion — cost if wrong: none.
- Ruling: `busy` clears after a successful action's refresh — Dart's `fetch` copied the previous `busy` (true) forward, so after any successful Start / Stop / Rebuild / Delete the buttons stayed in their spinner state for the screen's lifetime — cost if wrong: none; a failure already cleared it in Dart.

## Tests

`IntelliStockTests/Features/Nexus/NexusLearningModelTests.swift`: `_fmtDuration`, `_autoUpdateSummary`, start / auto-update / rebuild bodies, counts / bootstrap pill / range labels / log helpers, the controller's interval, action success and failure, first-load failure, the learning provider's partial errors and empty failure, decide / engine / mode bodies and error copy, targets save order, severity colours.

# Parity checklist — agent runs (Wave 2, agent "app", area 4)

Ports `mobile/lib/features/agent_runs/{application,presentation}/**`. Tests: the controller parts
of `test/features/agent_runs/agent_runs_test.dart` (countdown, copyWith, pagination); the model
parts were ported by the data agent.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## agent_runs_controller.dart → `Features/AgentRuns/Model/AgentRunsModel.swift`

- [x] `AgentRunsState`: runs, total, totalPages 1, page 1, perPage 20, control, busy, errorMessage, scheduledResumeAt, scheduledTotalMs; `copyWith` with sentinels for the error and the resume time.
- [x] `countdownFraction`: 0 without a schedule or total; else elapsed / total clamped 0…1.
- [x] `countdownSecsRemaining`: 0 without a schedule; else whole seconds left clamped to 0…total/1000.
- [x] Fetch: `GET /agent/runs?page&per_page` + `GET /agent/control` together; keeps page/perPage/schedule; clears the error; an agent resumed elsewhere cancels the countdown.
- [x] Polling every 5 s, paused in the background; the first fetch failing → full-screen error with Retry; later failures → the error state (Dart's AsyncError).
- [x] `startAgent(specialRequest:)` → `POST /agent/control {running: true, special_request?}`.
- [x] `pauseAgent` → `{paused: true}`; `resumeAgentNow` (cancels the countdown) → `{paused: false}`; `stopAgent` (cancels the countdown) → `{running: false}`.
- [x] `scheduleResume(minutes)`: 0 → resume now; else sets resume time + total and ticks every second; at zero cancels and resumes.
- [x] `cancelCountdown`.
- [x] `forceStop(logId)` → `POST /agent/runs/:id/force-stop`.
- [x] `goToPage(page)` / `setPerPage(n)` (page back to 1) then refresh.
- [x] Control actions: busy + error cleared; failure → busy false + `e.toString()`; success → refresh.

## agent_runs_screen.dart → `Features/AgentRuns/Views/AgentRunsView.swift`

- [x] Header `AI Agent Runs`, `Strategy attempts by the AI Backtest Agent.`, status pill `Running` (green, pulsing) / `Paused` (orange) / `Stopped` (faint).
- [x] Controls: `Start` (stopped; opens the start sheet), `Pause` (running), `Unpause` (paused, no countdown; opens the resume sheet), countdown ring (`mm:ss`, `resuming`, tap the stop square to cancel), `Stop` (running or paused); all busy-aware.
- [x] Per-page `10/page` `20/page` `50/page` `100/page`; refresh (spinner while busy).
- [x] Error banner; skeleton when busy with no runs; empty `smart_toy` `No agent runs yet` / `Start the AI Backtest Agent to see strategy attempts here.`
- [x] Runs grouped by `cycleId ?? id` (first-seen order), each under a centred timestamp divider (`fmtDateTime` / `—`).
- [x] Run card: `smart_toy` tile (orange), `name ?? 'Unnamed Strategy'`, created time (mono), status badge coloured running info / passed green / failed|error red / tossed orange / else faint; tinted card edge by status → native form: the badge carries the colour (no coloured borders).
- [x] No stages → `Queued…` with `hourglass_empty`.
- [x] Stage rows: status icon (passed check, failed/error cancel, tossed do-not-disturb, duplicate copy, stopped stop-circle, else empty circle; spinning progress while running and the agent runs), connector line tinted running/passed, label, stocks joined `, `, P&L (`fmtPnl`, coloured) + `fmtPct`, details, `In progress…` when running with no details.
- [x] Footer when a final result exists or a running run while the agent is stopped: `✓` / `✗` / `○` + result; stale `Agent stopped — run may be stale` + `Mark Stopped` (`close`).
- [x] Pagination when > 1 page: `N runs · page P of T`, `‹`, numbers (1, last, current ±2, `…` between gaps), `›`.
- [x] Start modal: `Start AI Backtest Agent`, `Optionally provide a special instruction.`, `Special Request` (`e.g. Focus on high-volatility tech stocks…`, 4 lines), `Cancel` / `Start Agent` (trimmed request or none) → native form: a sheet with a form.
- [x] Resume modal: `Resume Agent`, `Resume now or schedule automatic resume.`, `RESUME IN` presets `Now` (green) `5 min` `15 min` `30 min` `1 hr`, `CUSTOM DELAY` minutes field (`0`) + `Schedule` (positive only), `Cancel` → native form: a sheet.
- [x] Pull to refresh.

## Copy changes (title-style capitalisation)

- `Mark Stopped`, `Start Agent`, `Unpause` unchanged; none.

## Rulings

- Ruling: `busy` ends after a successful control action's refresh — Dart's `fetch` copied the previous state (busy = true) and never cleared it, so every control button spun until the screen was rebuilt — cost if wrong: none (the Dart behaviour was a stuck spinner).
- Ruling: the countdown keeps the Dart 1 s tick (a `tick` counter re-renders the ring) and the remaining time is read against an injectable clock, so the ported countdown tests are deterministic — cost if wrong: none.
- Ruling: `Mark Stopped` stays unconfirmed (Dart had no dialog) but is disabled while a control action runs (orchestrator: no double submit) — cost if wrong: none.
- Ruling: `do_not_disturb_on` and `radio_button_unchecked` have no entry in the core `Symbol` map, so the stage icons use `minus.circle` and `circle` directly (requested in the report) — cost if wrong: none.
- Ruling: tinted card borders by status become the status badge's colour only (no coloured borders on content); the stepper keeps its tinted connector lines — cost if wrong: none.
- Ruling: the start and resume dialogs are sheets (medium detent) with a form; the spinning stage glyph is the `progress.indicator` symbol with a variable-colour effect (still under Reduce Motion) — cost if wrong: none.
- Ruling: the Dart test's pagination helper (±1 window, ≤ 7 pages flat) differs from the screen's (±2 window); the screen's algorithm is ported and the test's assertions all hold for it — cost if wrong: none.


# Wheel regrade + manual "Run wheel now" — UI plan (web + iOS)

**Spec:** operator rulings of 2026-11-18 (below) on top of docs/superpowers/specs/2026-09-24-swing-trader-port-design.md.
That spec's deviation 17 ("no manual Run Now button") is REVERSED by the operator.
**Backend:** DONE and tested on this branch (`backend/swing_trader/wheel_manual.py`, 45 tests). The UI tasks below consume it and must not change it.

**Why:** the wheel scores its puts once, on Monday. The operator often approves on Friday, so the card shows Monday's
price, strike and score. (A) Regrade re-scores one pending wheel card at today's prices. (B) "Run wheel now" runs the
wheel scan on demand, to see whether another day finds more contracts.

## Global Constraints

Operator rulings (binding):
- A manual run ONLY creates review cards. It never places an order, even at score 75+. Say so in the UI ("Places no orders").
- A regrade NEVER changes a signal's status. A card that no longer qualifies stays pending with a warning; Approve and Reject stay available.
- A failed regrade keeps the card and its old values, with a warning.

API contract (exact; all routes need the usual bearer token; instance id = the instance's id as used by the existing swing calls):
- `POST /instances/{id}/swing/signals/{sid}/regrade` → 200 `{"signal": <signal row with regrade.status "queued">, "command_id": str}`.
  Errors (FastAPI `{"detail": str}`): 400 not a pending wheel signal; 404 unknown signal; 409 a regrade of it is already under way;
  503 instance not running / crashed / could not be queued. The result is NOT returned: it lands on the signal row, which the
  existing signals list endpoint returns. A regrade takes about a minute.
- Signal row additions: `regrade` (optional object) and `source` (optional; `"manual_scan"` for cards made by a manual run; also `run_id`).
  `regrade` fields: `status` queued|running|done|failed, `requested_at` (ISO), `requested_by`, `finished_at` (ISO|null), `count` (int,
  regrades done), `error` (str|null), `qualifies` (bool, only when done), `rescored` (bool, only when done: false = the name failed the
  screen, so score/strike/reasoning were left as they were), `reason` (str|null, why it no longer qualifies),
  `scored_at` (ISO|absent: when the card's CURRENT score was made; set by a rescored regrade, carried otherwise; absent = `created_at`),
  `previous` {`score`, `recommendation`, `stock_price`, `strike`, `premium_est`, `expiry`, `at`} = what the card said before the latest regrade.
  After a rescored regrade the row's `score`, `recommendation`, `reasoning`, `key_risks`, `proposal.strike/expiry/premium_est/qty`,
  `context.stock_price/otm_pct/rsi/atr_pct/earnings_days` hold the NEW values.
  A queued/running regrade whose `requested_at` is older than 5 minutes is dead: show it as failed/stale and let the operator retry.
- `POST /instances/{id}/wheel/scan` → 200 `{"run": <run row>, "in_progress": true, "command_id": str}`; 409 a run is already under way;
  503 instance not running / could not be queued.
- `GET /instances/{id}/wheel/scan` → `{"run": <newest run row or null>, "in_progress": bool}`. Trust `in_progress` from the server.
  Run row: `id`, `status` running|done|failed, `phase` queued|screening|earnings|scoring|done|failed, `requested_by`, `started_at`,
  `heartbeat_at`, `finished_at`, `session` (YYYY-MM-DD), counts `scanned`, `passed_screen`, `to_score`, `scored`, `new_pending`,
  `rejected`, `skipped_existing`, `errors`, and `error` (str|null).
- Both actions are refused (the regrade/run comes back failed with an explanatory error) until the wheel lane has completed one tick after the
  instance started (up to 20 minutes). Show the server's error text verbatim in that case.

UI behaviour (both platforms):
- Regrade control appears only on cards with `lane == "wheel"` and `status == "pending"`; disabled while that card's regrade is queued/running (not stale).
- While any visible card has a live (queued/running, not stale) regrade, re-fetch the signals list every 4 s; stop when none is live or the view goes away.
- Every wheel card shows when its current score was made: `regrade.scored_at` when present, else `created_at`.
- Approving a wheel card sends `expect_regrade_count` = the `regrade.count` (0 when absent) of the card as the operator saw it, in the existing
  decision body (approve / approve_half only). The server answers 412 Precondition Failed "This card was regraded since you opened it…" when it
  changed (409 keeps meaning "decided elsewhere"): show the message, refresh the cards, and show the new terms.
- After a done + rescored regrade: a compact before → after line for score, stock price (with % move), strike, premium.
- `done` with `qualifies == false`: warning "No longer qualifies: <reason>" (card stays, actions stay).
- `failed`: warning "Regrade failed: <error>" (old values stay).
- Cards with `source == "manual_scan"`: a small "Manual run" tag.
- The approve review screen (web modal / iOS sheet) shows the same "scored …" line and the no-longer-qualifies / failed warning.
- "Run wheel now" lives in the wheel panel/section. It asks for confirmation first (it costs one AI call per candidate) and says it places
  no orders. While `in_progress`: button disabled, phase + counts shown, poll GET every 4 s. On done: summary (scanned / passed screen / scored /
  new cards / rejected / already had a card / errors) and the pending cards are refreshed. On failed: the error. On open, GET once to resume
  showing a run in progress or the last run's summary.
- Error statuses map to plain messages: 409 "already under way", 503 "instance is not running" (append the server detail), others show the detail.

Working rules:
- Another agent works CONCURRENTLY in this same worktree on the other platform. Touch only files under your own directory
  (`frontend/` for Task 1, `ios/` for Task 2). Never edit `backend/`, `AGENTS.md`, `CLAUDE.md`.
- Do NOT commit, stage, stash, reset, checkout or otherwise change git state: the controller commits after review.
- If the backend contract looks wrong or incomplete, report it as a concern; do not work around it silently.

## Task 1: Web UI (Vue 3, frontend/)

Files: `frontend/src/utils/swing.js` (helpers already started at ~lines 617-890: regradeState, scoredAtLabel, regradedLabel,
noLongerQualifies, pctMove, regradeChanges, regradeStatusText, applyLocalRegrades, classifyRegradeFailure, manualRunSummary,
manualRunProgress, classifyRunFailure — written by an earlier agent, UNTESTED; review them against the contract and fix),
`frontend/tests/swing.test.mjs` (or a new `frontend/tests/swingRegrade.test.mjs`), `frontend/src/components/swing/PendingSignalsCard.vue`,
`frontend/src/components/swing/SwingOrderReviewModal.vue`, `frontend/src/components/swing/WheelPanel.vue`,
`frontend/src/views/InstanceDetailView.vue` (only to refresh pending cards when a manual run finishes).

Steps:
1. Read the existing components and the helpers; follow the components' existing fetch/token/error patterns.
2. TDD the helpers: tests first for every exported helper you keep (node --test). Delete helpers you do not use.
3. Wire PendingSignalsCard.vue (Regrade button, 4 s polling, scored-at label, before/after, warnings, Manual run tag).
4. Wire SwingOrderReviewModal.vue (scored line + warning).
5. Wire WheelPanel.vue ("Run wheel now", confirmation, progress, summary, resume on mount) and emit an event that InstanceDetailView.vue
   handles by re-fetching the pending signals.
6. Verify: `cd frontend && npm test` (all suites green) and `npm run build` (clean).

## Task 2: iOS UI (SwiftUI, ios/)

Files: `ios/IntelliStock/Features/Swing/Data/SwingRepository.swift` (+ `protocol SwingSignalsSource`, wherever it is declared),
`ios/IntelliStock/Features/Swing/Model/PendingSignalsModel.swift` (pending cards and the wheel model),
`ios/IntelliStock/Features/Swing/Model/SwingPresentation.swift` (pure helpers),
`ios/IntelliStock/Features/Swing/Views/PendingSignalsSection.swift` (cards and the wheel section),
`ios/IntelliStock/Features/Swing/Views/SwingOrderReviewSheet.swift`, tests under `ios/IntelliStockTests/Features/Instances/`
(fakes in `SwingTestSupport.swift`).

Steps:
1. Read `ios/IntelliStock/DesignSystem/README.md` and the files above. Operator UI rules: no gradients anywhere; Apple Stocks/Wallet/Settings
   look; Swift 6 with MainActor default isolation; no third-party dependencies.
2. Decoding: add optional `regrade` (all fields optional, lenient) and `source` to the swing signal model; a run-status model for the GET/POST
   scan responses. Unknown/missing fields must never fail decoding of the signals list.
3. Repository + `SwingSignalsSource`: `regradeSignal(instance, signal)`, `startWheelRun(instance)`, `wheelRunStatus(instance)`; update every
   conformer and test fake.
4. Pure helpers in SwingPresentation.swift mirroring the UI behaviour (live/stale state, scored-at text, before/after rows, warning text, run
   progress and summary text, error-status mapping), with unit tests written first.
5. Model + views: Regrade action on pending wheel cards with 4 s polling while live (cancel when the view disappears), before/after rows,
   warnings, Manual run tag; the review sheet line + warning; "Run wheel now" in the wheel section with a confirmationDialog, progress,
   summary, polling, and a pending-cards refresh when it finishes.
6. Verify from `ios/`: `xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test`
   (all tests green; if new test files are not picked up, check whether project.yml needs `xcodegen generate`).

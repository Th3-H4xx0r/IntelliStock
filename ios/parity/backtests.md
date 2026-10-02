# Parity checklist — backtests (Wave 2, agent "markets")

Ports `mobile/lib/features/backtests/application/**` and `presentation/**`, and the controller /
presentation parts of `test/features/backtests/backtest_test.dart` (the data agent ported the model
parts).

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## backtests_list_controller.dart → BacktestsListModel

- [x] State: rows, total, totalPages, page, perPage 15, sortBy `completed_at`, sortOrder `desc`, loading, error, statusMap.
- [x] Initial load on appear; `_loadPage(page)` keeps rows while loading, clears the error, stores the response and manages the poll; failure keeps rows and sets `error`.
- [x] Active = running / queued / pending / paused / paused_llm_critical (live status overrides the row's); terminal = completed / finished / stopped / failed / error / cancelled.
- [x] Poll every 3 s while any row is active: fetch `/backtests/:id/status` for each active row (errors ignored), merge into `statusMap`, reload the page when any reached a terminal status; stops when none are active → native form: a cancellable `Task` loop that also pauses in the background.
- [x] `refresh`, `goToPage`, `setPerPage` (back to page 1), `toggleSort` (same field flips asc/desc, new field → desc, page 1).
- [x] `performAction(id, action)`: POST action, refresh that row's status (ignored on failure), reload the page, restart polling on resume; returns the error text.

## backtest_detail_controller.dart → BacktestDetailModel

- [x] Init: summary + graph data (failure → empty) + LLM cost (failure → empty) in parallel; error → message; when the summary is active, fetch status and start polling.
- [x] Poll every 3 s: every 10th tick refreshes the LLM cost silently; status fetch → terminal: stop, refetch summary + graph + cost; paused: stop; otherwise refresh the summary.
- [x] `currentStatus` (status ?? summary ?? 'unknown'), `progress`, `elapsedSeconds`, `nexusLookback`, `isActive`, `isTerminal`.
- [x] `refreshLlmCost` with loading / error; `loadLogs` (guarded while loading; `logs` lines, `source ?? 'db'`).
- [x] `performAction`: delete → DELETE and stop polling; else POST action, fetch status, stop on stop, start on resume; returns the error text.
- [x] `rerun` body: `instance_id` (when set), `stocks`, `start_date`, `end_date`, `granularity ?? '60'`, `initial_cash ?? 100000`, `emulate_fee_venue ?? 'default'` → `POST /backtests`.

## backtest_playback_controller.dart → BacktestPlaybackModel

- [x] Speeds [0.5, 1, 2, 5, 10], default index 1; frame delay `round(1000 / speed)` ms.
- [x] Load playback data; frameIndex −1; error → message.
- [x] Getters: `isFinished`, `isEmpty`, `visibleEvents`, `currentPortfolioEvent`, `currentPortfolioValue` (?? initial cash ?? 100000), `currentHoldings`, `currentDateLabel` ("label — time" / "—"), `portfolioHistory`, `xRange` (first − 1 day / now − 7 days … last / now).
- [x] `togglePlay` (restart from 0 when finished), `reset` (frame 0, paused), `cycleSpeed` (reschedules while playing); `_advance` stops at the end.

## backtests_screen.dart — BacktestsView

- [x] Header: "All Backtests", "N total", refresh (spinner while loading) → native form: inline title "All Backtests" with a refresh toolbar button; "N total" as the list header.
- [x] Toolbar "Backtests (N)" + "Per page" selector (10/15/25/50/100) → native form: `Picker(.menu)` in the header row.
- [x] Initial loading → 5 skeleton cards → native form: redacted placeholder cards.
- [x] Error (no rows) → ErrorBanner + retry; empty → EmptyState `analytics` "No backtests found" / "Run a backtest to see results here."
- [x] Card (tap → `/backtests/:id`): instance id (tap → `/instances/:id`) or "Backtest", `#id`, status pill (upper-cased, pulsing while active), first 4 stock chips + "+N", P&L `fmtPnl` + `fmtPct` (only when set), open-arrow tile, "start → end", timer + `fmtElapsed`, "Completed {fmtDateTime}", progress bar + "N%" (active with progress), Nexus lookback bar ("Lookback", "c/td"), Pause / Resume / Stop icon buttons.
- [x] Action confirm: "{Pause|Resume|Stop} Backtest", "{body}\n\nBacktest ID: {id}", confirm label = verb; errors surface (dialog threw) → native form: `.confirmAlert` with an error toast.
- [x] Pagination (only with rows; hidden when ≤ 1 page): "Page p of t  (N total)", prev / numbered / ellipsis / next, disabled while loading; `_buildPages` ported (unit-tested).
- [x] Pull-to-refresh.

## backtest_detail_screen.dart — BacktestDetailView

- [x] Breadcrumb "Back / Backtest #id" → native form: system back + inline title "Backtest #id".
- [x] Loading skeleton → redacted placeholders; error → ErrorBanner + retry (re-init); no summary → hourglass "This backtest has not completed yet." + status / progress bar.
- [x] Header: analytics tile, "Backtest #id", status pill, "start → end", tickers joined; progress bar "Progress" + "N%" while active.
- [x] Action cluster: Pause (running), Resume (paused / paused_llm_critical), Stop (running / paused / queued), Rerun, Playback (→ `/backtests/:id/playback`), Delete → native form: bordered chip buttons in a wrapping row.
- [x] Confirms: "{Pause|Resume|Stop|Delete} Backtest", body + "\n\nBacktest #id", confirm label = last word; delete pops (or goes to `/backtests`); Rerun confirm "Rerun Backtest" / "A new backtest will be created with the same settings.\n\nBacktest #id" / "Rerun" → go `/backtests/{new id}`.
- [x] LLM pause banner (only `paused_llm_critical`): title, Bar / Reason (attempts) / Provider • Model / Call site / Paused at rows, selectable sample, resume hint.
- [x] Nexus lookback banner: "Nexus Lookback Training", "Day c / t", bar, start / current / end dates, explanation.
- [x] Stat grid (7 tiles, 2 columns): Total P&L, P&L %, Portfolio (From …), Trades (b buy / s sell), Elapsed, Win Rate (≥ 50 green, "W / L"), Portfolio High (Low: …).
- [x] AI CREDITS card: refresh (spinner while loading), error text, skeleton, "No LLM calls were attributed to this backtest.", Total cost / Calls (ok · failed) / Input tokens / Output tokens, By model / By call site / By provider (6 each).
- [x] FEES · CRYPTO card (only with volume > 0): emulated pill, volume, 4 platforms with rate, applied row (actual fees or volume × rate), estimate footnote (emulated / charged copy verbatim).
- [x] STRATEGY collapsible: name, "ID: …", "Sub-strategies" cards (index, name, weight %, phase, conditions + config key/value chips) or "No sub-strategies defined" → native form: tap-to-expand card with a rotating chevron.
- [x] Logs panel: header (status dot, file name, "N lines", "(last 500)" when db), View / Hide Logs (loads on first open), loading "Loading logs…", error, "No logs available.", coloured lines (`_levelColor` ported) in a 400 pt box.
- [x] Portfolio chart: "Portfolio Value Over Time", scrubbed value + "vs start" P&L + scrubbed timestamp, chart (baseline = start value, line colour by last ≥ start).
- [x] P&L per Stock: per ticker P&L, P&L %, price change %.
- [x] Stock Charts & Trades: Expand All / Collapse All (clears decision limits); per-ticker accordion (P&L (pct), "N trades", chevron) → price chart with buy / sell markers, trade table (Time / Action / Shares / Price / Total / Cash After), Decision Trace ("N evaluations", 5 per page, "Show more (N remaining)", decision cards with label / Override / primary strategy / Weighted Score / reason / strategy rows with 360-char truncation).
- [x] Round Trip Statistics: Round Trips, Total RT P&L, Avg Winning, Avg Losing.
- [x] `syncfusion` charts → native form: `ScrubbableAreaChart` (Swift Charts) with `ScrubbableChartMarker`s for trades.
- [x] DataTable → native form: a horizontally scrolling `Grid`.

## backtest_playback_screen.dart — BacktestPlaybackView

- [x] Loading skeleton; error view ("Failed to load playback", message, "Back to Backtest"); empty view ("No playback data", "This backtest has no portfolio snapshots or trades to replay.", "Back to Backtest").
- [x] Header: "Backtest Playback" + LIVE badge, "Backtest #id", current date pill, control bar (play / pause, reset, speed "1x"/"0.5x") → native form: inline title + subtitle, floating Liquid Glass control bar at the bottom.
- [x] Portfolio panel: "PORTFOLIO VALUE", value, holdings mini chips (6: ticker, "N sh", current price coloured vs avg), chart with the leading initial-cash anchor and baseline (no entry animation).
- [x] Execution Log: date markers, strategy nodes ("x → ACTION" split or upper-cased desc, name, reason, "Scanning:" tickers), outcome nodes, decision nodes ("EXECUTION DECISIONS", "No actions taken.", Buy / Sell rows "qty @ $price" + reason), portfolio events hidden, typing node while playing; auto-scrolls to the newest frame.
- [x] Glow shadows on markers and step icons → native form: removed (operator: no glows).

## Copy changes (title-style capitalisation only)

None beyond the ones the Dart already used title-style ("Expand All", "Hide Logs", "Back to Backtest").

## Rulings

- Ruling: the logs header file name is "backtest-{id}.log" — Dart printed `List.hashCode`, a meaningless identity hash that changed every load — cost if wrong: none.
- Ruling: the list and detail polls are `Task` loops that pause while the app is in the background (Dart `Timer.periodic` kept firing) — Review Focus 4 — cost if wrong: none.
- Ruling: a request that ends in cancellation leaves state untouched and shows no error (`marketsIsCancellation`) — orchestrator guidance — cost if wrong: none.
- Ruling: confirmed actions (pause / resume / stop / delete / rerun) disable their trigger while in flight; rerun can never double-post — orchestrator guidance, matching the Dart dialog spinner — cost if wrong: none.
- Ruling: the timer glyph uses SF Symbol `timer` (Dart's `symbol('timer')` fell back to a placeholder) — cost if wrong: none.

## Tests

`IntelliStockTests/Features/Backtests/BacktestsModelTests.swift`: status colours (13 cases + nil), `_buildPages` (4 cases), playback speeds and delays, list load + poll + terminal reload, query keys, keep-rows-on-failure, `performAction`, detail init / poll / terminal refetch, rerun body + id, delete + logs, presentation helpers (level colour, applied fee venue, pause banner formatters, reason truncation), playback getters, play-to-end, speed cycle, empty / error.

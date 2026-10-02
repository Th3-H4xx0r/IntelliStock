# Parity checklist — kalshi + crypto (Wave 2, agent "markets")

Ports `mobile/lib/features/kalshi/presentation/**` (except `kalshi_dashboard_card.dart`, the
dashboard agent's) and `mobile/lib/features/crypto/presentation/**` (`crypto/application` does not
exist in the Dart tree), plus the Riverpod family providers declared in `kalshi_repository.dart` and
`crypto_repository.dart` (handed over by the data agent).

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## Providers (view state from the data layer)

- [x] `kalshiPortfolioProvider(bid)` / `kalshiEdgesProvider` / `kalshiPositionsProvider` / `kalshiInstancesProvider` → `KalshiOverviewModel` per-brokerage `Loadable`s, cached by brokerage id (keepAlive after success) → native form: the tab-root model outlives backgrounding, so the empty-cache trap the keepAlive comments describe cannot occur.
- [x] `kalshiInstanceDetailProvider` / `Decisions` / `Live` / `Orders` → `KalshiInstanceDetailModel`.
- [x] `cryptoInstancesProvider` → `CryptoModel.instances`.

## kalshi_screen.dart — KalshiView (tab root)

- [x] Header icon `sports_soccer` + "Kalshi" → native form: large navigation title "Kalshi" (tab root).
- [x] Gradient crown (`KalshiCrown`) → native form: removed; plain grouped background (operator: no gradients).
- [x] Accounts = `brokeragesProvider.value` filtered to `brokerage_type == 'kalshi'`; loads brokerages on appear (`services.dashboard.loadBrokerages()`).
- [x] Stale selection dropped when the account is no longer linked; default = first account.
- [x] No account → EmptyState `sports_soccer`, "No Kalshi account linked", "Link a Kalshi brokerage (demo or live) to create a trading instance."
- [x] Account selector shown only when > 1 account → native form: `Picker(.menu)` row ("Account").
- [x] Instances loading → `LoadingState` (top padding 40) — only before the first load (see Rulings).
- [x] No instance → EmptyState `smart_toy`, "No trading instance yet", "Create a Kalshi instance to scan soccer markets, flag edge, and (when started) trade.", action "Create instance" → native form: "Create Instance" (title-style).
- [x] One row per instance: status dot (success / faint), name, Running/Stopped pill, Live/Paper pill (danger / accent), chevron; tap → `/kalshi/instances/:id` → native form: `NavigationLink(value: Route.kalshiInstance)` in an inset-grouped section.
- [x] "New instance" text button with `add` icon → native form: "New Instance".
- [x] Portfolio card (`KalshiPortfolioHero`, title "Portfolio value", retry invalidates portfolio).
- [x] Edge Radar card (`bolt`): loading / ErrorBanner + retry / "No +EV contracts right now." / rows "`ticker`  ·  `side`" + "+X.X%" (edge×100, 1 dp, success).
- [x] Open positions card (`receipt_long`): loading / error + retry / "No open positions." / `kalshiPositionTile` per position.
- [x] Live logs card (`terminal`, "Live logs") with `LiveLogsPanel(instanceId: first instance)` in a 300 pt box.
- [x] Pull-to-refresh invalidates instances, portfolio, edges, positions for the selected brokerage (`.refreshable`).
- [x] Create sheet: `KalshiInstanceSheet(accounts, initialBrokerageId: selected)`, `onCreated` refetches that brokerage's instances.
- [x] Card eyebrow headers (icon + uppercase title) → native form: `Card` with `Label` header in `.footnote.weight(.semibold)` caps.

## KalshiInstanceSheet (create / edit)

- [x] `_kLeagues` (18, same order) and `_kRiskPresets` (low/medium/high/max with every value + blurb) verbatim.
- [x] Defaults: edge 4, kelly 0.125, maxContracts 50, exposure 15, leagueCap 25, minPrice 15, maxPrice 90, drawMinEdge 10, orderSizeMin/Max 0/0, dailyLoss 100, poll 60, manualBankroll 1000, noSharpEdge 5, marketShrink 40, sharpWeight 85, leagues {EPL, Serie B, Ligue 2} (insertion-ordered), usage 50, dailyLossPct 0.10, risk medium, liveMonitoring false, paperMode true, oneBetPerFixture true.
- [x] Prefill (edit): every `n(key)` mapping (×100, /100, plain) through `_num`; `odds_api_key`, `oddspapi_api_key`, `sharp_weight`, `bankroll_usage_pct`, `tier`, `model`, `live_monitoring`, `paper_mode`, `one_bet_per_fixture`, non-empty `leagues`; daily-loss prefill marks it touched.
- [x] `_num`: integral → int text, else Dart `toString()`.
- [x] Load models (`GET /models`, errors swallowed) and balance (`portfolio(bid)` → cash > 0 ? cash : value; error → 0) on open; balance reload on account change.
- [x] `_effectiveBankroll`: balance > 0 ? round(balance × usage / 100) : `double.tryParse(manualBankroll) ?? 0`.
- [x] `_scaleDailyLoss`: skipped once touched; else `round(bankroll × pct).clamp(1, 2^30)`.
- [x] `_applyPreset`: sets risk + 9 fields + usage + dailyLossPct, clears touched, rescales.
- [x] Validation copy: "Name is required", "Pick at least one league".
- [x] Body keys + order verbatim incl. `odds_api_key` / `oddspapi_api_key` only when non-blank and `'model': null` when unset; `_d`/`_i` fallbacks (3, 0.25, 50, 60, 25, 15, 90, 10, 0, 0, 100, 60, 5, 40).
- [x] Create → `POST /brokerages/{bid}/kalshi/instances`; edit → `PATCH /instances/{id}/kalshi/config`; success closes and calls `onCreated(bid)`; failure shows `'$e'` inline.
- [x] Title "Create Kalshi instance" / "Edit Kalshi instance" → native form: inline sheet title "Create Kalshi Instance" / "Edit Kalshi Instance"; close X → Cancel toolbar button.
- [x] Account picker + balance line ("Balance: …", "Balance: $X.XX", "Live balance unavailable" in warning) — create only.
- [x] Risk tolerance pills → native form: segmented `Picker`; selecting applies the preset; blurb below.
- [x] Analyst LLM model dropdown with "Default (system model)" (nil) + models by `name ?? model ?? id`.
- [x] Switches: "Live in-match trading" + subtitle; "Paper mode (dry-run)" / "REAL orders" (danger tint, ⚠ subtitle) — paper/real copy verbatim; "One bet per fixture" + subtitle.
- [x] "Odds API key — sharp anchor" + info; secure field "The-Odds-API key (live sharp odds)" hint "the-odds-api.com key (optional)".
- [x] "OddsPapi key" + info "Used for backtest odds only, not live trading."; secure field "OddsPapi key (backtest odds)" hint "oddspapi.io key (optional)".
- [x] "Sharp weight: N%" slider 0–100, 20 divisions.
- [x] "Instance name" hint "e.g. Soccer edge — demo".
- [x] Leagues multi-select chips → native form: a "Leagues" row pushing a checkmark list (`KalshiLeaguePicker`), same toggle semantics and order.
- [x] Bankroll usage: with balance → slider 5–100 (19 divisions) + "N% · $X" (rescales daily loss); without → "Bankroll ($)" field.
- [x] Numeric fields with the verbatim labels (Edge threshold (%), Kelly fraction, Max contracts, Max exposure (%), Per-league cap (%), Scan cadence (s), Min price (¢), Max price (¢), Draw min edge (%), Order size min ($), Order size max ($), No-sharp edge bar (%), Market anchor (%)) → native form: Form rows, decimal keypad.
- [x] "Daily-loss cap ($) — auto-scales with bankroll": a user edit marks it touched.
- [x] CTA "Saving…" / "Save changes" / "Create instance" disabled while saving → native form: full-width large prominent button pinned under the form, "Save Changes" / "Create Instance".
- [x] Tooltip info icons (tap) → native form: `info.circle` buttons opening a compact popover with the same text.

## kalshi_portfolio_hero.dart

- [x] Eyebrow: "Paper P&L · progress" when paper else the title, upper-cased; "MOCK" badge (warning) when paper.
- [x] Loading (110 pt) / ErrorBanner + retry.
- [x] Paper: `paperSeries`/`paperSeriesTs`, headline `paperPnl ?? 0`, change = last − first (≥ 2 points); real: series, value, dayChange.
- [x] Scrub: value = scrubbed point, change = point − baseline (first value).
- [x] Value "−$X.XX"/"$X.XX" (sign before $) with a 500 ms roll → native form: `.contentTransition(.numericText(value:))`.
- [x] Change row: trending_up/down icon + "+$X.XX"/"-$X.XX" in success/danger.
- [x] Chart when > 1 point: `ScrubbableAreaChart(indexed, baseline = first, pulsingEndDot, 168 pt)`, line colour by dayChange sign; else "Equity curve appears once the engine records snapshots."
- [x] Borderless hero on the crown → native form: clean hero `Card` (brief).
- [x] `kalshiPositionTile`: title match ?? ticker; pick (`pickLabel` ?? side) minus " to win"; crest image or initials (2); "Yes · pick"; current value "$X.XX"/"—"; unrealized cents → "+$X.XX" (no sign when negative: Dart prints "$-1.23") / ""; chips "N×", "Buy N%"/"Buy —"; "$N max payout".

## kalshi_instance_detail_screen.dart — KalshiInstanceDetailView

- [x] Title = detail name ?? "Kalshi instance" (inline).
- [x] 15 s timer refetches live + orders → native form: `PollingLoop` (pauses in background).
- [x] 1 s tick for kickoff countdowns → native form: `TimelineView(.periodic(by: 1))` around the pregame section.
- [x] Refresh (pull) re-fetches detail, decisions, live, orders and the brokerage's positions.
- [x] Toolbar (when detail loaded): Start/Stop (warning when running, accent when stopped; disabled while busy), Backtest (`science`, → `/kalshi/instances/:id/backtest`), Edit config (`tune`), Delete → native form: Start/Stop as a toolbar button, the other three in an `ellipsis.circle` Menu.
- [x] Start/stop error → SnackBar `e.toString()` → native form: error Toast.
- [x] Delete dialog "Delete instance?" / "This cannot be undone." / Cancel / Delete (danger) → `DELETE /instances/:id?force=true`, then pop.
- [x] Edit → shared sheet prefilled from `detail.config`, `name`, `brokerage_id`; accounts empty; onCreated → refresh.
- [x] Loading (detail nil) → LoadingState.
- [x] Badges Running/Stopped, Live/Paper from `environment == 'live'`.
- [x] Summary tiles Placed/Skipped/Queued/Blocked (`?? 0` toString) + paper P&L line (realized / unrealized cents → "+$X.XX", " (live)" / " (live · N open)"), hidden when both nil.
- [x] Portfolio hero ("PORTFOLIO VALUE") when brokerage id non-empty.
- [x] LIVE NOW · N card: per match score "h  :  a", clock (score.clock / "N'" / "LIVE"), team badges (logo or ≤ 3 initials), market prob bars per side (clamped 0–1, "N%"), first news line, up to 4 action chips ("OPEN 3", colours open/add success, reduce warning, exit danger).
- [x] OPEN POSITIONS · N card (only when non-empty), `account_balance_wallet` icon.
- [x] ORDERS card: PENDING · N ("No resting orders — everything filled.", first 12), FILLED · N (first 12), MOCK POSITIONS · N ("No mock (paper) positions.", first 12), MOCK FILLED · N (first 12); order tile / mock tile / mock-history tile copy and maths verbatim.
- [x] PREGAME ANALYSIS: loading / error + retry / "No games analyzed yet — picks will appear here once the bot scans the slate."; group by `fixture_id ?? match`; `_dedupeSides` (latest by ts, ever-placed keeps PLACED, entry edge from placed row, home/draw/away order); sort by kickoff (nil last); card title / countdown / best-edge chip / Elo · xG line / side rows (pick, model-only, edge sparkline, decision pill, fair/price/edge metrics, "updated …", "placed @ +X.X%").
- [x] `_kickoffCountdown`, `_priceCents`, `_bestEdge`, `_pair`, `_edgeSeries`, `_fmtTs` ported 1:1 (unit-tested).
- [x] DECISION LOG: loading / error / "No decisions logged yet."; 8 per page, "Page N / M", prev/next clear expansion; card expands to Model/Sharp/LLM/Fair/Size (+ Paper P&L), rationale box, "Blocked: …"/"Skipped — …".
- [x] Edge sparkline `CustomPainter` → native form: `Path` shape, same normalisation and colour rule.
- [x] LIVE LOGS card with `LiveLogsPanel` (300 pt).
- [x] Crown gradient → removed.

## kalshi_backtest_screen.dart — KalshiBacktestView

- [x] `_kPresets` verbatim; defaults (tier max, leagues [World Cup], bankroll 54, edge 2, noSharp 3, kelly 0.2, contracts 100, exposure 40, leagueCap 40, usage 70, daily 15, min 15, max 90, drawMin 10, orderMin 8, orderMax 15, sharpWeight 85, analystMaxCalls 10).
- [x] Load: instance detail + models in parallel; config → every field (×100, /100); oddspapi key; model → useLlm; then the brokerage's backtest list. Failure → "Couldn't load the instance config."
- [x] 3 s poll of the backtest list → native form: `PollingLoop` (pauses in background).
- [x] Risk tolerance buttons (Low/Medium/High/Max) → native form: segmented Picker applying the preset.
- [x] Analyst LLM model picker with "None (statistical model only)"; "Use LLM analyst in this backtest" checkbox disabled without a model → native form: Toggle.
- [x] Start/End date buttons (label until picked; range 2024-01-01 … Jan 1 next year) → native form: rows opening a graphical DatePicker sheet.
- [x] Leagues filter chips → native form: `KalshiLeaguePicker` row.
- [x] 15 numeric fields with verbatim labels; `double.tryParse(t) ?? value`.
- [x] "OddsPapi API key (saved; blank = model-only)" field.
- [x] Validation copy: "Pick a start and end date.", "Start must be on/before end.", "Select at least one league."; failure "Failed to start the backtest."
- [x] Body (`instance_id`, `leagues`, dates, `bankroll_dollars`, `config` with every key, conditional `oddspapi_api_key` / `model`, `use_llm`, `analyst_max_calls`) verbatim → `POST /brokerages/{bid}/kalshi/backtests`; reload list; push `/kalshi/backtests/:id`.
- [x] CTA "Starting…" / "Run backtest" disabled while submitting or brokerage unknown → native form: "Run Backtest".
- [x] Backtests card: "No backtests yet." / rows (id prefix 8 mono, "start → end", "status N%" while running/pending, P&L $, view / stop (running/pending) / delete icon buttons — no confirmation, as in Dart).
- [x] Title "Backtest".

## kalshi_backtest_result_screen.dart — KalshiBacktestResultView

- [x] Load `GET /kalshi/backtests/:id/results`; status block from the payload; result map; default day = last day; errors swallowed.
- [x] 3 s poll only while status pending/running.
- [x] Title "Backtest " + id prefix + status (status colour) → native form: principal toolbar title + status subtitle.
- [x] Summary: Total P&L, ROI, Bets, Win rate, Avg CLV, API/cache tiles; fixtures line verbatim; trust line with colour thresholds 0.9 / 0.75 and " (spans a loss — not proven)".
- [x] Equity card ("Equity — scrub to see that day's trades") when > 1 point: selected-day line "day · N trade(s)", chart (indexed, baseline 0), scrub selects the trade's kickoff day; chips "All · N" + per day "day · N".
- [x] Tabs Trades / Decision log / Logs → native form: segmented Picker.
- [x] Trades: "No bets were placed under these settings." / "Scrub or pick a day to see its trades." / trade cards (flags, "home v away", P&L, league · pick · entry ¢ × size, badges edge / sharp|model-only / outcome).
- [x] Decision log rows (label, decision coloured, reason) / "No decision log recorded."
- [x] Logs monospace block / "No logs recorded."

## crypto_screen.dart — CryptoView

- [x] Title block eyebrow "Trading", title "Crypto", subtitle "24/7 bots — pin fixed coin weights, auto-discover the rest." → native form: inline title "Crypto" + header text.
- [x] Back (system) and refresh toolbar button; "New" button (`add`) opens the sheet.
- [x] Loading / ErrorBanner + retry / EmptyState `currency_bitcoin` "No crypto instances yet" + "Create a 24/7 crypto bot with a fixed + dynamic coin allocation." + "New crypto instance" → native form: "New Crypto Instance".
- [x] Card: icon tile, name ?? id (tap → detail), id mono, "24/7" badge (info), Crashed/Running/Stopped pill (pulsing while running).
- [x] Fixed coins chips or "100% dynamic — fully auto-discovered."
- [x] Actions View / Edit / Backtest / Start|Stop / Delete, all disabled while busy; errors swallowed; list refetched after.
- [x] Delete confirm "Delete instance" / "Delete "{name ?? id}"? This cannot be undone." / "Delete" (danger) → `DELETE /instances/:id?force=true`.
- [x] Pull-to-refresh.

## crypto_instance_detail_screen.dart — CryptoInstanceDetailView

- [x] Load: instance; backfill `crypto_config` (and empty stocks) from the list when the detail omits it; brokerages; backtests; account equity when the brokerage id is set; errors → ErrorBanner + retry.
- [x] 4 s poll of backtests while any is running/queued/pending/paused → native form: `PollingLoop`.
- [x] Header (icon, name ?? id, id, 24/7 badge, status pill); Start/Stop + Edit buttons; toolbar refresh.
- [x] INSTANCE INFO: Band (capitalised / "—"), Cadence (`_cadence` / "~15 min"), Uptime ("Nh Nm Ns" while running), Created by.
- [x] BROKERAGE: Account (`account_name` ?? brokerage id ?? "—"), Mode Paper/Live (info/success), Account value (`$N,NNN` / "—").
- [x] ALLOCATION: donut of fixed slices + Dynamic remainder (> 0.01), "Dynamic strategy" capitalised, chips "SYM N%" + "Dynamic N%" → native form: Swift Charts `SectorMark` donut (`CryptoAllocationChart`).
- [x] BACKTESTS (N) + "New Backtest" → sheet with `onCreated` refreshing in place; empty card "No backtests yet for this instance."; rows (#id, status chip with `_btStatusColor`, P&L "+$N", "+X.XX%", coins or "Dynamic", "start → end") → `/backtests/:id`.

## crypto_instance_sheet.dart — CryptoInstanceSheet

- [x] Coin catalog (12), palette (7) and dynamic colour, `_kStrategies` (7 with blurbs verbatim), recommended bands, `_kBands`, band blurbs verbatim.
- [x] Init: name / brokerage from edit; edit → prefill (band, strategy capitalised, allocations ×100) or the even-split fallback from stocks (`toStringAsFixed(2)`) with `_weightsUnknown`; create → BTC 10 / ETH 20.
- [x] Selectors: brokerages + strategies; default brokerage = first Alpaca/Binance (else first); equity load (`accountEquity`).
- [x] Allocation maths: fixedSum, dynPct clamp, `_over` (> 100.0001), `_fmtNum` (int or 1 dp), `$N` usd, %↔$ editing with clamp, add/remove coin.
- [x] Submit validation "Instance ID is required", "Over-allocated — fixed weights exceed 100%".
- [x] Edit body (`name`, conditional `brokerage_id`, `granularity` from band, `crypto_config`, `stocks`) → `PATCH /instances/:id`; create body (`id`, conditional `name`, `granularity`, `run_command: false`, `kind: 'crypto'`, conditional `brokerage_id`, conditional resolved `strategy_id`, `stocks`, `crypto_config`) → `POST /instances`; allocations `pct` = `double.parse((pct/100).toStringAsFixed(4))`.
- [x] Copy: header "New crypto instance"/"Edit crypto instance" → native form: "New Crypto Instance"/"Edit Crypto Instance"; intro paragraph; "Instance ID *" (hint "e.g. crypto-main"), "Name" (hint "Optional display name"), "Brokerage" + info, "Select a brokerage"; equity line ("Account equity …", "Account equity $N · name", "Account equity unavailable — % still works").
- [x] "Volatility band" + info, pills High/Medium/Low → native form: segmented Picker; blurb; "Recommended X for S — tap to use".
- [x] "Dynamic strategy" + info, dropdown (auto-applies the recommended band), blurb.
- [x] ALLOCATION toolbar with %/$ segmented toggle; donut; legend; weights-unknown warning; table header "COIN" + "WEIGHT · ≈USD"/"USD · ≈WEIGHT"; coin rows (dot, sym, name, editable %/$ field, ≈ counterpart, remove); Dynamic row ("auto-discover & trade"); add-coin bar or "All catalog coins added."; meter bar + "Fixed N%  ·  Dynamic N%" + "Over by N%"/"$N flexible" + the 0 % dynamic warning.
- [x] CTA "Saving…"/"Save changes"/"Create instance" → native form: "Save Changes"/"Create Instance".

## crypto_backtest_sheet.dart — CryptoBacktestSheet

- [x] `_kBandGran`, `_kBandCadence`, `_kFeeVenues` verbatim; dates default now − 90 d … now; granularity from band ("900" fallback); cadence label.
- [x] Intro "Simulate {name ?? id}'s current allocation over a historical window. Crypto fills include the taker fee."; "ALLOCATION UNDER TEST"; tickers or "100% dynamic — the backtest auto-discovers its universe.".
- [x] Start/End date fields (range 2018-01-01 … now) → native form: compact `DatePicker`s.
- [x] "Cadence" row with schedule icon + "from Band"; "Initial cash ($)" field (default 10000); "Emulate fees" picker + caption.
- [x] Validation "End date must be after start date"; body via `createBacktest` (`initial_cash` double, `emulate_fee_venue`).
- [x] Success: close; `onCreated` when given else push `/backtests/:id` (or `/backtests`); failure inline.
- [x] Title "Backtest crypto instance" → native form: "Backtest Crypto Instance"; CTA "Queuing…"/"Run backtest" → "Run Backtest".

## Copy changes (title-style capitalisation only)

"Create instance" → "Create Instance"; "New instance" → "New Instance"; "Save changes" → "Save Changes";
"Run backtest" → "Run Backtest"; "New crypto instance" → "New Crypto Instance"; "Create Kalshi
instance"/"Edit Kalshi instance" → "Create Kalshi Instance"/"Edit Kalshi Instance"; "Edit crypto
instance" → "Edit Crypto Instance"; "Backtest crypto instance" → "Backtest Crypto Instance"; "Edit
config" → "Edit Config"; "Kalshi instance" (title fallback) → "Kalshi Instance".

## Rulings

- Ruling: the overview keeps its instance list on screen during a pull-to-refresh instead of swapping to the loading row — Dart's `isLoading` check flashed the loader because invalidate re-entered loading; the system refresh spinner already shows progress — cost if wrong: none, same data and requests.
- Ruling: per-screen models replace the cross-screen `keepAlive` family cache; the detail screen fetches portfolio and positions itself — SwiftUI does not dispose a tab root on backgrounding, which is the case the keepAlive guarded — cost if wrong: one extra request when opening a detail screen.
- Ruling: start/stop failures show an error Toast with the API message (Dart's SnackBar); delete failures, which Dart left unhandled, also show an error Toast — cost if wrong: an extra visible message.
- Ruling: Kalshi backtest numeric fields show the live value after the config loads or a preset applies — Dart's `TextFormField(initialValue:)` kept the stale default text while the body used the loaded value; the request body is identical — cost if wrong: none.
- Ruling: the backtest list's id label uses `prefix(8)` — Dart's `substring(0, 8)` threw on short ids — cost if wrong: none.
- Ruling: the 3D sector ring (`Sector3DChart`, the dashboard agent's) is replaced by a private Swift Charts donut (`CryptoAllocationChart`) with angle selection and an "Allocation / SYM N%" centre — spec §7's donut form; swap for the dashboard's shared view at the merge if it lands — cost if wrong: two donut implementations.
- Ruling: crypto detail backtest rows push `Route.backtest(id)` in the current tab (go_router `push('/backtests/:id')`) — cost if wrong: none.
- Ruling: league multi-select moves to a pushed checkmark list — 18 chips do not fit a Form row; order and toggle semantics are unchanged — cost if wrong: one extra tap to see the selection.
- Ruling: a request that ends in cancellation (`CancellationError` / `URLError.cancelled`, the screen went away) leaves every state untouched and shows no error (`marketsIsCancellation`) — orchestrator guidance 2026-10-01 — cost if wrong: none.
- Ruling: confirmed and submitting actions keep their trigger disabled while in flight (instance delete via `busy`, crypto card actions via per-card busy, backtest submit via `submitting`/`busy`), and every `ConfirmRequest` passes `onError` — orchestrator guidance; matches the Dart dialog spinner — cost if wrong: none.

## Tests

`IntelliStockTests/Features/Kalshi/KalshiModelTests.swift` and `CryptoModelTests.swift`: 40 tests in 10 suites (form presets / prefill / body order, pregame helpers, overview selection + keep-on-failure, backtest launcher body, result day grouping, crypto allocation maths, create / PATCH bodies, backtest sheet, detail backfill and formatters).

# Parity checklist — dashboard (Wave 2, agent "trading", area 1)

Ports `features/dashboard/application/{account_positions,insights,nexus_strategy}_controller.dart`,
`features/dashboard/presentation/**`, `features/kalshi/presentation/kalshi_dashboard_card.dart`,
`features/stock/**` (application + presentation) and `features/symbol_search/presentation/**`.
The data layers (`dashboard_repository`, `nexus_models`, `dashboard_controller`, `portfolio_analytics`,
`market_hours`, `selected_account_controller`, `symbol_search_models/_repository`) were ported in Wave 1.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## account_positions_controller.dart → `AccountHoldingsModel`

- [x] `AccountHoldingsNotifier(brokerageId)`: first fetch `GET /brokerages/{id}/positions`, then poll every 15 s; lifecycle-aware (pauses in the background); a failed poll keeps the last good data.
- [x] First-fetch failure → `.failed` (the holdings list hides; `valueOrNull == nil`).
- [x] `HoldingsPnlMode { total, daily }`, default `daily` (`holdingsPnlModeProvider`) → native form: held by the dashboard's app-lifetime `DashboardFeedModel`, so the choice survives account switches, as the global StateProvider did.
- [x] `holdingOpensProvider`: `GET /brokerages/{id}/holding-opens`, failure → `{}`.
- [x] `_rangeForHoldingAge`: ≤6 d `1W`, ≤31 `1M`, ≤93 `3M`, ≤366 `1Y`, else `ALL`.
- [x] `holdingsSparklinesProvider(id, range)`: symbols = non-empty position symbols; none → `{}`.
- [x] 1D: one `GET /symbol-historicals?symbols=…&range=1D`; points at/after local midnight (or unparseable ts) form the since-midnight slice; fewer than 2 → full series; keep only series with ≥ 2 points.
- [x] Total: group symbols by age-matched range (no open date → `3M`), one batch per range; with an open date: series = [avg entry if > 0] + since-buy points (t ≥ boughtAt or unparseable) + [last price if > 0], used when ≥ 2 points; otherwise the full series when ≥ 2 points.
- [x] Daily → `1D`, Total → `ALL` key for the holdings list's sparkline fetch; the toggle re-fetches; the previous curves stay drawn meanwhile (`_lastSparks`); skeleton only before any curve exists.

## insights_controller.dart → `DashboardFeedModel` + `DashboardInsightsLoader`

- [x] `NewsArticle.fromJson`: title/source/url via `(x ?? '').toString()`, `published_at` via `parseDateTime`.
- [x] `marketNewsProvider`: `GET /market/news?limit=15` → `articles` maps, empty titles dropped; never throws (`[]`).
- [x] `marketMoversProvider(id)`: `GET /brokerages/{id}/movers?top=6` → `gainers`/`losers` (`symbol`, `pct`, `price` as num), empty symbols dropped; never throws.
- [x] `nexusMomentumProvider(id)`: `GET /brokerages/{id}/nexus-momentum` → `momentum` (`symbol`, `score` ?? 0), empty symbols dropped; never throws.
- [x] `_sectorCache`: session-level symbol → sector cache; `/symbols/{s}/info` hit once per symbol per app run → native form: lives on the app-lifetime `DashboardFeedModel`.
- [x] `MarketQuote(symbol, label, pct, values)`.
- [x] `_indexSymbols` SPY S&P 500 · QQQ Nasdaq · DIA Dow · IWM Russell 2000 (display order).
- [x] `_sectorEtfs` XLK Technology … XLB Materials (11, verbatim labels incl. `Consumer Disc.`).
- [x] `_quotesFor`: one `symbolHistoricals(universe, '1D')`; per symbol in universe order, skip missing / `pctChangeOf == nil`; sector performance sorted by pct desc.
- [x] `marketIndicesProvider` / `sectorPerformanceProvider` never throw (`[]`).
- [x] `DayChangeNotifier(id)`: `portfolioHistory(id,'1D').sinceLocalMidnight()`; empty → nil; `(abs: changeAbs ?? 0, pct: changePct)`; first-fetch failure → nil; polls every 5 s, lifecycle-aware; poll failure keeps the last value; non-autoDispose (cached per id) → native form: only the selected account polls; other accounts keep their last value.
- [x] `concentrationProvider(id)`: `concentration(position market values)`; failure → empty stats.
- [x] `sectorAllocationProvider(id)`: keepAlive; positions with symbol, value > 0, not OCC; misses fetched in parallel `GET /symbols/{sym}/info` with an 8 s timeout, `sector as String?`, failure → nil; `aggregateBySector`; failure → `[]`.
- [x] `todaysMoversProvider(id)`: from the 1D holdings sparklines, `(last/first − 1)×100` where ≥ 2 points and first ≠ 0; `todaysMovers`; failure → `[]`.
- [x] `riskMetricsProvider(id)`: keepAlive; `portfolioHistory(id,'1Y')` → `riskMetrics(values)`; failure → empty metrics.

## nexus_strategy_controller.dart → `NexusStrategyModel`

- [x] Keep-alive (stale-forever) caches per account: trends (active limit 30 + ended limit 6, together; failure → empty view), backfill queue, discovered stocks, trade contexts, nexus outcomes (failure → zero stats), momentum watchlist (failure → zero summary). Each fetched once per account per session.
- [x] → native form: `.refreshable` on the dashboard re-fetches them (Dart needed an app restart), as the brief asks.

## dashboard_screen.dart → `DashboardView`

- [x] `widgetSyncProvider` on entry → `services.widgetDataSyncer.run()` once per dashboard lifetime.
- [x] Order: Portfolio · Insights · Strategy · Kalshi card · Services · Re-run onboarding.
- [x] Every section stays built while scrolling (`cacheExtent: 10000`) → native form: a non-lazy `VStack` in a `ScrollView`, so each card loads once on entry.
- [x] Full-bleed violet gradient backdrop, sheen and bloom → native form: removed (operator rule: no gradients); plain grouped background.
- [x] `DashboardTopActions`: search icon, tooltip `Search symbols`, pushes `/search` → native form: trailing toolbar button, accessibility label `Search symbols`.
- [x] No `Portfolio` heading over the hero (dashboard_top_actions_test) → native form: the tab root carries the large nav title `Dashboard`.
- [x] Pull-to-refresh → native form: added `.refreshable` (brokerages, services, holdings, chart, insights, strategy caches); Dart had none.

### Portfolio section

- [x] Loading → portfolio skeleton (2 card-shaped blocks) → native form: one redacted placeholder hero.
- [x] Error → `ErrorBanner(e.toString(), Retry → invalidate brokerages)` → `ErrorRow` + Retry → `loadBrokerages()`.
- [x] No accounts → EmptyState `account_balance`, `No brokerages linked.`, `Link a brokerage to see your portfolio here.`, action `Link a brokerage` → push `/brokerages` → native form: action title-cased `Link a Brokerage`.
- [x] Selected account: stored id when present in the list, else the first.
- [x] `_accountLabel`: alpaca → `Alpaca · Paper` / `Alpaca`; else account name, else brokerage type.
- [x] Selector: logo + UPPERCASE label + chevron (only with > 1 account); dropdown lists every account with logo and a check on the selected one; selecting persists via `SelectedAccountModel.select` and closes → native form: a `Menu` with a checkmarked `Picker`.
- [x] Chart keyed by account (fresh load per account); range switch reuses cached value.
- [x] Freshness line `Updated ` + live relative time, only once a history fetch succeeded.
- [x] Holdings list below the chart.

### portfolio_chart.dart

- [x] Ranges `1D 1W 1M 3M YTD 1Y ALL`, default `1D` → native form: segmented `Picker`.
- [x] History per (account, range): `GET /brokerages/{id}/portfolio-history?range=`; 1D → `sinceLocalMidnight()`; poll 5 s on 1D, 30 s otherwise; lifecycle-aware; poll failure keeps data; each success stamps `portfolioUpdatedAt`.
- [x] `computeChange(history, scrubIndex)`: baseline `openValue ?? first`; active = scrubbed value, else `currentValue ?? last`; pct nil when baseline is 0; (nil, nil) when empty.
- [x] `nearestIndex(timestamps, fraction)` (ported with its tests).
- [x] Value row: hero value `fmtMoney` with odometer roll → native form: `.contentTransition(.numericText(value:))`; change row trending icon + `+$x.xx (+y.yy%)`, green ≥ 0 else red.
- [x] Value: previous data shown while a new range loads; first load → skeleton; first-load error → error text in red.
- [x] `Markets Open` / `Markets Closed` chip, green dot when open, re-evaluated every 30 s.
- [x] Chart: 224 pt plot; line colour from computeChange sign; monotone spline, 2 pt line; area fill → native form: flat `DS.chartAreaOpacity` fill (no gradient).
- [x] 1D: x = minutes since the point's local midnight on a fixed [0, 1440] axis; labels `12AM 6AM 12PM 6PM 12AM`; scrub snaps to the nearest minute.
- [x] Other ranges: x = index (gapless); labels `formatChartDate` at 4 evenly spaced indices; scrub `fractionToIndex`.
- [x] Scrub: hairline + dot, header follows the scrubbed value, haptic per index change; clears on lift → native form: `chartXSelection` + `RuleMark` + `.sensoryFeedback(.selection)`.
- [x] Not scrubbing → pulsing end dot at the latest value (1D at its minute fraction).
- [x] Animate the curve on first reveal and on each range switch, not on polls or scroll-back.
- [x] Range switch: outgoing curve stays until new data lands (no skeleton); skeleton only on first load.
- [x] Error → `Failed to load`; empty → `No data for this range` with `bar_chart_4_bars`.
- [x] Secondary (non-hero) card variant (`_CardHeader`) → native form: not used — the Dart screen only ever built the hero; the selector replaces it.

### Holdings (`_HoldingsList`)

- [x] Hidden until holdings load, and when empty (no cash and no positions).
- [x] Header `Holdings` + `Total`/`Daily` segmented toggle.
- [x] Total = cash + Σ market value; ring = share of total.
- [x] Cash row: teal ring, `Cash`, `Available to invest`, `fmtMoney(cash)`; only when cash is present.
- [x] Holding row: ring coloured by P&L sign (dim when no P&L); symbol (bold, coloured); `N share(s)` (integer qty without decimals, else 2 dp); mini sparkline (green up / red down, draws in, replays on toggle); value `fmtMoney`; `fmtPnl · fmtPct` or `—`.
- [x] Daily P&L from the 1D spark ratio: abs = value × (1 − 1/ratio), pct = (ratio − 1)×100; Total = unrealized P&L/%.
- [x] `_AllocationRing` label `0%` / `<1%` / rounded %.
- [x] Tap → `/stock/{symbol}` with position, brokerage id and portfolio total.
- [x] Rings → native form: trimmed-circle ring (no gradient), 44 pt.

### Services section

- [x] Title `Services`, subtitle `Status and controls for all IntelliStock background services.`, refresh button (tooltip `Refresh`) → `refreshNow()`.
- [x] Loading → 5 skeleton cards; error → ErrorRow + Retry.
- [x] Services polled every 10 s while the dashboard is visible (`pollServices()` from `.task`; `TODO(merge)`: switch to `pollServices(lifecycle:)` once core lands it, so it pauses in the background).
- [x] `ServiceCard`: icon tile, title, subtitle, status pill (first letter capitalised, empty → `Stopped`, pulsing when running), stats grid (1 full width, else 2 columns), buttons in a row.
- [x] `ServiceStatCell` label + mono value; `NexusProgressCell` `Build progress`, `N%`, bar, last phase.
- [x] Price Engine (`trending_up`, info): `Live market data`; Details or `No extra details`; running → `Terminate` (danger) → `POST /config/terminate-price`; else `Start` (success) → `POST /config/run-price-service`.
- [x] Discover Engine (`search`, accent): `Opportunity discovery`; Stop/Start → `POST /discover/control {running: !isRunning}`.
- [x] AI Backtest Agent (`smart_toy`, warning): `Automated strategy search`; stats `Backtests today` (`count_today` toString or `—`), `Last run` (`last_run_date` or `—`), `Resume at` when present; paused → `Resume` (`{paused:false}`); running → `Pause` (`{paused:true}`); `Stop` (running or paused → `{running:false}`) / `Start` → sheet.
- [x] Start sheet: `Start AI Backtest Agent`, `Optionally provide a special request for this run.`, `Special Request`, hint `e.g. Focus on high-volatility stocks…`, Cancel / `Start Agent` → `POST /agent/control {running:true, special_request?}` (trimmed, empty → omitted) → native form: `.sheet` with detents + `Form`.
- [x] Daily Digest (`newspaper`, success): `Discord market summaries`; status from `digest.running == true`; `Last morning`/`Last evening` via `fmtDateTime`; Stop/Start → `POST /digest/control {running}`; `Send Now` → `POST /digest/send-now`.
- [x] Nexus Graph Engine (`hub`, accent): `Knowledge graph builder`; `graph_build.progress_pct` rounded + last stage message, else `Build` / `No build in progress`; Stop/Start → `POST /nexus/control {running}`.
- [x] Busy per engine: buttons disabled with a spinner while in flight; success refreshes services; errors swallowed (no added confirmations — Dart had none).
- [x] Button labels `Send Now`, `Start Agent` already title case; others single words.

### Re-run onboarding panel

- [x] `replay` icon, `Re-run onboarding`, body copy verbatim, `Open` (`arrow_forward`) → push `/onboarding`.

## insights_section.dart

- [x] Hidden until brokerages load with ≥ 1 account; id = selected (if present) else first.
- [x] Headline `Insights`; movers strip (hidden when none): chip `SYM ▲ +x.xx%`, tap → `/stock/{sym}` with brokerage id.
- [x] TODAY tile: skeleton while first loading; `—` when nil; `fmtPnl(abs)` with odometer → native form: numeric text transition; trending icon + `fmtPct(pct)`.
- [x] DIVERSIFICATION tile: skeleton / `—` when empty / `score` + ` /100` (green ≥ 66, amber ≥ 33, red) + `Top N% · N holdings`.
- [x] SECTOR ALLOCATION card: hidden when loaded empty; skeleton circle while loading; donut.
- [x] RISK card: hidden when loaded empty (`points < 2`); skeleton until loaded; `Volatility` `N%`, `Max drawdown` `N%`, `Sharpe` 2 dp or `—`.
- [x] Headline `Market`; index cards (hidden when loaded empty; skeleton 150 pt): label, sparkline with dashed opening baseline and end dot, level `#,##0.00`, `▲ +x.xx%`; tap → `/stock/{sym}`.
- [x] SECTOR PERFORMANCE: rows label · bar (|pct|/3 clamped) · `fmtPct`.
- [x] MARKET MOVERS: `GAINERS` / `LOSERS` columns, top 5 each, tap → `/stock/{sym}`.
- [x] NEXUS MOMENTUM (`bolt`): hidden unless picks; top 10, bar = |score| / max |score|; tap → stock with brokerage id.
- [x] MARKET NEWS: top 6; title (2 lines), `source  ·  relative time`; tap → in-app browser (http/https only, silently ignores bad URLs) → native form: `SFSafariViewController` sheet.
- [x] Card eyebrows verbatim upper case.

## sector_3d_chart.dart → `DashboardSectorDonut`

- [x] Metallic extruded 3D ring with drill-in → native form: Swift Charts `SectorMark` donut (spec §7), no gradients; the selected sector grows outward.
- [x] Centre readout: the drilled header text `Allocation` + `SECTOR  N%` (Dart's verbatim copy; spec's "Growth" is a paraphrase).
- [x] Tap a sector selects it (`chartAngleSelection`); a horizontal swipe steps sectors (44 pt per step) with a selection haptic.
- [x] Per-slice `N%` labels → native form: legend rows under the donut (tap selects).
- [x] Empty slices → nothing.

## strategy_section.dart

- [x] Fetches deferred 900 ms after the section appears.
- [x] Hidden until brokerages load; whole section (header `Strategy` included) hidden until at least one card has data.
- [x] MARKET TRENDS (`trending_up`): active rows (arrow, name, `N%` strength, bar, up to 5 tickers tappable + `+N`); `RECENTLY ENDED` rows with `ended today` / `ended Nd ago`.
- [x] REVERSAL WATCH (`warning`): name + `N signal(s)`.
- [x] BACKFILL QUEUE (`hourglass_empty`): `N pending`; up to 12 rows: pin when priority, ticker, source, `N paths` when > 0; tap → stock.
- [x] DISCOVERED (`search`): up to 12: ticker, `source · via X` or source; tap → stock.
- [x] BOT RATIONALE (`psychology`): up to 8: symbol, event-type tag, reason (2 lines); tap → stock.
- [x] OUTCOME SCORECARD (`score`): `N%` hit rate (green ≥ 0.5), `hit rate · c/n signals`; recent rows ✓/✗, symbol, event type, `+x.x%`.
- [x] MOMENTUM WATCHLIST (`visibility`): `monitoring N`; chips `SYM @$N` (price 0 dp when > 0); tap → stock.
- [x] `_agoLabel`: empty/unparseable → ''; days ≤ 0 → `ended today`; else `ended Nd ago`.

## kalshi_dashboard_card.dart → `KalshiDashboardCard`

- [x] Hidden unless a `kalshi` brokerage is linked (first one used).
- [x] Header `sports_soccer` + `Kalshi` + `Open →` → `go('/kalshi')` (selects the Kalshi tab).
- [x] Card: account name upper case; loading `Loading…`; error `—`; value `$x.xx` (no grouping), trending icon, `+$x.xx`/`-$x.xx` day change; `N open position(s)`; tap → Kalshi tab.
- [x] `kalshiPortfolioProvider` / `kalshiPositionsProvider` fetched once per card lifetime (keepAlive after success).

## stock (stock_controller.dart + stock_screen.dart) → `StockModel` + `StockView`

- [x] `StockHistoryNotifier(symbol, range)`: `GET /symbol-historicals?symbols=SYM&range=`; unparseable timestamps skipped; 1D → since-local-midnight slice when ≥ 2 points, else the full series; poll 10 s on 1D, 30 s otherwise; lifecycle-aware; poll failure keeps data.
- [x] `stockInfoProvider`: `GET /symbols/{sym}/info`, failure → `{}`.
- [x] `BotContributor` / `BotTradeEvent.fromJson` (ts falls back to `created_at`; side default `buy`; override flag `== true`).
- [x] `stockBotActivityProvider`: `GET /brokerages/{id}/bot-activity?symbol=&per_page=20` → `events`; failure → `[]`.
- [x] `stockOrdersProvider`: `GET /brokerages/{id}/orders?symbol=` → `orders` as `Trade`; failure → `[]`.
- [x] `StockScreenArgs(position, brokerageId, portfolioTotal)` → `StockRoute`.
- [x] Custom round back button + violet crown → native form: system navigation bar back button, inline title = symbol; no gradient.
- [x] Header: name (or symbol), ticker, price odometer → numeric transition; `▲ +$x.xx  +y.yy%` (scrub-aware vs first point); skeletons while loading; `—` / `No price data` otherwise.
- [x] Chart: 280 pt, gapless (`indexed`), colour by last ≥ first, animate on a range's first appearance only; skeleton while loading; `Couldn't load prices` (error) / `No chart data available for SYM`.
- [x] Ranges → segmented `Picker`; switching clears the scrub.
- [x] YOUR POSITION (only with a position): diversity gauge when total > 0, `TOTAL P&L`, `VALUE`, `N shares · avg $x`.
- [x] BOT ACTIVITY: `No linked brokerage` / skeletons / `Couldn't load bot activity` / `No bot trades logged yet for SYM` / rows (side chip, strategy or `Buy decision`/`Sell decision`, relative time, reason 4 lines, `Backed by …` up to 3 distinct others, `@ $x`, `OVERRIDDEN`).
- [x] KEY STATISTICS (3 columns): Prev close, Open, `<range> high/low`, 52W high/low, Volume, Avg volume, Market cap (`$` + compact), P/E, Fwd P/E, Beta (2 dp), Analyst target; zero/absent skipped; hidden when none; skeleton while info loads.
- [x] `_compact`: T/B/M 2 dp, K 1 dp, else 0 dp.
- [x] ABOUT (only with a summary): sector/industry tags, summary (10 lines); skeleton while info loads.
- [x] ORDER HISTORY: `No linked brokerage` / skeletons / `Couldn't load orders` / `No recent orders for SYM` / rows (side chip, `qty @ $price`, `fmtDateTime(ts)`, `fmtMoney(price × qty)`).

## symbol_search_screen.dart → `SymbolSearchModel` + `SymbolSearchView`

- [x] `searchUnavailableMessage`: `Not Found` → online-soon copy; else the connection copy (both verbatim; ported test).
- [x] Field autofocuses; hint `Search stocks, ETFs, crypto`; clear button → native form: `.searchable` in the navigation bar drawer, focused on appear; the system Cancel/clear replace the custom ones; inline title `Search` added.
- [x] Back (tooltip `Back`) → native form: system back button.
- [x] Typing: empty → reset; else results cleared, loading, 250 ms debounce, `GET /symbols/search?q=`; stale replies (query changed) dropped; `ApiError` → its message; other → `Couldn't search symbols right now.`.
- [x] Body: loading with no results → 6 skeleton rows; error → card (`query_stats`, `MARKET SEARCH`, `We’re reconnecting`, message, `Retry search` → re-run the current text); nil → `Find an investment`; empty → `No matching symbols`; results → rows.
- [x] Quotes: one `symbolHistoricals(unique symbols, '1D')` per result set → `searchQuoteFromHistory`; row shows symbol (bold) + `fmtPct` coloured, name, price (`fmtMoney`, skeleton while quotes load).
- [x] Tap → `/stock/{encoded symbol}` with no extras.
- [x] `Retry search` → native form: title case `Retry Search`.

## Tests ported

- [x] dashboard_top_actions_test → `DashboardTopActionsTests` (search label + route; no `Portfolio` title).
- [x] portfolio_chart_helpers_test → `DashboardPortfolioChartHelperTests` (computeChange, nearestIndex, sinceLocalMidnight).
- [x] insights_layout_smoke_test → not ported (a Flutter render-exception smoke test; no native analogue).
- [x] sector_3d_chart_golden_test (behaviour) → `DashboardSectorDonutTests` (selection, stepping, centre text).
- [x] strategy_trends_card_golden_test (behaviour) → `DashboardStrategyTests` (recently-ended rows, visibility rules).
- [x] symbol_search_models_test `searchUnavailableMessage` → `SymbolSearchModelTests`.

## Copy changes (HIG title case)

- `Link a brokerage` → `Link a Brokerage`
- `Retry search` → `Retry Search`
- Native nav titles added: `Dashboard` (tab root), `Search`, the stock symbol.

## Rulings

- Ruling: the dashboard's keep-alive providers (nexus caches, `_sectorCache`, keepAlive sector allocation and risk, the non-autoDispose day change, the P&L mode) live on `@State` models owned by `DashboardView` — the tab root lives as long as the signed-in shell, which is the Riverpod container's effective lifetime; Wave 2 may not edit `AppServices` — cost if wrong: a sign-out/sign-in refetches them (Riverpod would have kept them).
- Ruling: per-account (autoDispose family) state — holdings, chart, movers, momentum, concentration — is rebuilt when the selected account changes, mirroring the keyed providers' dispose — cost if wrong: none.
- Ruling: pollers run only while the dashboard is on screen (tab visible, not covered by a pushed detail) and the app is in the foreground; Flutter's IndexedStack kept them running behind other tabs — cost if wrong: data is up to one cadence stale when returning.
- Ruling: the day-change poller runs for the selected account only; Flutter's non-autoDispose family polled every account ever selected forever — cost if wrong: none (a leak, not a feature).
- Ruling: the sector donut centre shows Dart's drilled header copy `Allocation` / `NAME  N%`, not the spec's paraphrase `Growth` — copy is verbatim from the Dart — cost if wrong: one word.
- Ruling: the 3D drill-in, back affordance and ring rotation are not reproduced; the donut keeps the selection, step and readout behaviour (spec §7 native form) — cost if wrong: none.
- Ruling: allocation rings are trimmed `Circle` strokes rather than `Gauge` — `accessoryCircularCapacity` sizes itself for widgets and cannot be held at 44 pt — cost if wrong: none visually.
- Ruling: icons with no entry in `Symbol` (`query_stats`) use a local SF Symbol fallback (`chart.bar.xaxis.ascending`) at the call site; request to add the mapping to `Symbol.swift` is in the report — cost if wrong: none.
- Ruling: market news opens in `SFSafariViewController` (Dart `LaunchMode.inAppBrowserView`) rather than `openURL`, to stay in-app as Flutter did — cost if wrong: none.
- Ruling: `todaysMovers` ties keep holdings order (Dart kept server map order, which the backend returns in request order) — cost if wrong: equal-move chips swap places.
- Ruling: the widget sync runs once per dashboard lifetime (Dart's autoDispose provider ran once while the branch stayed mounted), not on every tab return — cost if wrong: none.
- Ruling: a cancelled request (the view went away mid-fetch) never changes state — no error shown, and the stale-forever caches (nexus, sector, risk, day change) never store a cancelled load's empties (`tradingIsCancellation`) — orchestrator instruction 2026-10-01 — cost if wrong: none.
- Ruling: skills listed in the agent protocol were applied through the conventions and spec §7 (which distil them) rather than loaded, to protect context on a 17.7k-line scope — cost if wrong: none observed.

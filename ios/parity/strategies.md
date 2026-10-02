# Parity checklist — strategies (Wave 2, agent "markets")

Ports `mobile/lib/features/strategies/application/strategies_controller.dart` and
`presentation/{strategies_screen,strategy_detail_screen}.dart`, plus the presentation part of
`test/features/strategies/strategy_config_test.dart` (rank medal). The data agent ported the models,
repository, `strategy_config.dart` and `strategy_repository_preserve_test.dart`.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

**No config editor exists in the Flutter app.** `StrategyRepository.update` (with
`preserve_history`) and `previewConfigChange` have no caller in `mobile/lib/**/presentation`; the
detail screen is read-only. Nothing here writes a strategy document. The one live-affecting write
is the backtest sheet's `POST /instances/:id/link-strategy`, kept verbatim (only unlinked or new
instances are offered, so it can never re-point an instance that already runs another strategy).

## strategies_controller.dart → StrategiesModel / StrategyDetailModel

- [x] `StrategySortField` name / backtests / bestPnl / bestPct; defaults bestPnl, descending, page 1, 20 per page.
- [x] `fetchAll`: list, agent results, top-5, best-per-strategy in parallel, each failing to empty; top-5 sorted by rank (missing → 99); page reset to 1.
- [x] `_sortRows` (name case-insensitive, run count, best P&L / P&L% with nil = −∞; asc or desc).
- [x] `setSort` (same field flips, new field → desc; page 1), `setPage`, `setPerPage` (page 1).
- [x] `rows` (compute best by strategy → merge → sort), `top5Enriched` (snapshot name or "Strategy #id" / "Strategy", sub-strategy names `strategy ?? type ?? '?'`), `totalPages` (ceil, clamp 1…999), `pagedRows`.
- [x] Detail `_load`: strategy (required), agent results (failure → empty), agent best (nil on error); best id = `id ?? backtest_id`.
- [x] `strategyBacktests`, `sortedBacktests` (created_at / pnl / pct, nil → 0 / ''), `bestPnlBacktest`, `setBtSort`, `isAgentBest` (best id == strategy id).

## strategies_screen.dart — StrategiesView (tab root)

- [x] "Strategies" + "All trading strategies with their best AI backtest results." → native form: large title + subtitle row.
- [x] Refresh (spinner while loading) → native form: toolbar button.
- [x] "Create Strategy" (`add_circle`) → `/instances?createStrategy=1` → native form: switches to the Instances tab (nothing reads the query in Flutter either).
- [x] Per-page selector 10 / 20 / 50 ("N/page") → native form: `Picker(.menu)`.
- [x] Loading: 3 top-5 skeletons + 5 row skeletons → native form: redacted placeholders.
- [x] TOP N BEST STRATEGIES (`emoji_events`, warning) + "ranked by P&L%"; rank cards: medal / "#N" tile, name, "RANK N", "N sub-strategies", BEST P&L / BEST P&L%, first 5 sub-strategy pills + "+N more", "View Strategy" (→ `/strategies/:id`), "Backtest" (→ `/backtests/:id`).
- [x] Rank accent colours (amber / slate / orange / accent) and medals 🥇🥈🥉 / "#N".
- [x] Top accent gradient line → native form: removed (operator: no gradients); the rank tint stays on the tile and pill.
- [x] Sort bar "Sort:" Name / Best P&L / Best P&L% / Backtests with direction arrow → native form: segmented-style capsule buttons in a scroll row.
- [x] Cards: rank tile or schema glyph, name (rank-coloured), "RANK N", "ID n", "N subs", "N runs" (> 0), best P&L / P&L% or "—", backtest button (→ best P&L backtest) ; tap → `/strategies/:id`.
- [x] Empty: EmptyState `schema` "No strategies found." / "Create your first strategy to get started."
- [x] Pagination: "N strategies" (one page) or "N strategies · page p of t", prev / up to 5 numbered / next with the same window rule.
- [x] Pull-to-refresh.

## strategy_detail_screen.dart — StrategyDetailView

- [x] "All Strategies" back → native form: system back (title "Strategy").
- [x] "Backtest this strategy" (when loaded) → native form: toolbar button opening the sheet.
- [x] Loading skeletons; not found: "Strategy not found" + "Back to Strategies" (→ `/strategies`).
- [x] Header card: agent-best styling (`auto_awesome`, "AGENT BEST", amber name) or schema; "Strategy ID n · N sub-strategies · N backtests"; BEST P&L / BEST P&L% + "Best Backtest" (→ `/backtests/:id`) when a best exists.
- [x] Agent-best gradient accent line → native form: removed (no gradients).
- [x] COMPOSITION "Sub-strategies (N)": cards (mono name, "position n", phase pill coloured pre / post / entry / exit, Weight `toString` / "—", Scope / "—", CONFIG rows with `getStrategyConfigFieldMeta` labels and values, or "No config.") / "No sub-strategies defined."
- [x] HISTORY "Backtests (N)": sort bar Date / P&L / P&L% (only with backtests); rows (best marker, `fmtDateTime(created_at)`, first 4 stocks + " +N", "start – end", P&L, P&L%, open glyph) → `/backtests/:id`; empty "No backtests yet." / "Run a backtest to see results here."
- [x] Pull-to-refresh.

## Backtest sheet (`_BacktestModal`) — StrategyBacktestSheet

- [x] Header "Backtest this strategy" + strategy name; close disabled while busy → native form: sheet title "Backtest This Strategy" with the name as the navigation subtitle row; Cancel disabled while busy.
- [x] Loading "Loading instances..."; instances via `GET /instances`; default selection: first linked, else first free, else new.
- [x] SELECT INSTANCE: "Already linked to this strategy" (LINKED tiles), "Available instances (no strategy)", "Create new instance" + NEW INSTANCE NAME (placeholder "e.g. My Strategy Test") → native form: a radio-style selection list.
- [x] BACKTEST PARAMETERS: STOCKS (comma-separated, "AAPL, MSFT, NVDA"), START DATE / END DATE (date pickers; first 2015, last today; start defaults a year ago), GRANULARITY (1 day / 1 hour / 15 min / 5 min / 1 min; default 86400), INITIAL CASH ($) default 10000.
- [x] Validation copy: "At least one stock is required", "Start date is required", "End date is required", "End date must be after start date", "Instance name is required when creating a new one".
- [x] Submit: "Working..."; create instance (`id` = 100000 + now ms % 900000, `name`, `run_command: false`) when new; "Instance created but no ID returned"; link strategy unless already linked; `POST /backtests` (`instance_id`, `stocks`, `start_date`, `end_date`, `granularity`, `initial_cash` = `double.tryParse ?? 10000`); "Backtest #id queued!"; after 900 ms close and push `/backtests/:id`; failure shows the error.
- [x] CTA "Run Backtest" (busy) disabled while busy → native form: full-width prominent button; status message row (success / danger).

## Copy changes (title-style capitalisation only)

"Backtest this strategy" → "Backtest This Strategy" (toolbar button and sheet title).

## Rulings

- Ruling: no strategy editor is ported because the Flutter app has none — adding one would be a new feature (spec §2 out of scope) and would write live configs — cost if wrong: the operator asks for an editor later; the repository already carries `update` / `previewConfigChange` with the preserve-history semantics.
- Ruling: "Create Strategy" selects the Instances tab — Flutter pushed `/instances?createStrategy=1`, a tab-branch path whose query nothing reads — cost if wrong: none.
- Ruling: the backtest sheet disables Run Backtest while busy and refuses a second submit, so instance creation, linking and the backtest POST never double-post — orchestrator guidance — cost if wrong: none.
- Ruling: a cancelled request leaves state untouched and shows no error — orchestrator guidance — cost if wrong: none.
- Ruling: rank-tinted borders become a tinted rank tile and pill only (cards have no borders in the native design system) — cost if wrong: slightly less rank emphasis.
- Ruling: rank text uses the rank accent darkened on its tint in light mode (`DS.Palette.onTint`) instead of the Dart amber-300 / slate-300 / orange-400, which were tuned for the dark-only theme — legibility in both appearances — cost if wrong: slightly different rank text hues.

## Tests

`IntelliStockTests/Features/Strategies/StrategiesModelTests.swift`: rank medal (ported), pager window, fetch / merge / sort / rank, top-5 enrichment, paging, detail load + sort + agent best + best backtest, missing strategy error, backtest sheet default selection, validation copy, new-instance create + link + backtest bodies, already-linked skips the link.

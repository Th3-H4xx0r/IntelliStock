# Parity checklist — live trading (Wave 2, agent "trading", area 3)

Ports `features/live_trading/application/live_state_notifier.dart` and
`features/live_trading/presentation/{live_trading_screen,equity_chart,manual_order_sheet,position_card}.dart`.
The data layer (`live_repository.dart`, `models/live_state.dart`) was ported in Wave 1.
This screen drives REAL money on alpaca-main: every guard is kept.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## live_state_notifier.dart → `LiveTradingModel`

- [x] `CommandToast` (commandId, type, status, error, result; `isTerminal` completed/failed; `isPending` pending/running).
- [x] `LiveTradingState` (liveState, notRunning, equityHistory, positionHistoricals, currentRange `1D`, commandToast, fetchError); Dart's `copyWith(liveState: null)` keeps the previous live state — kept.
- [x] `_fetchState`: `GET /instances/{id}/live-state`; 404 → `notRunning: true` (fetchError cleared); success → live state, `notRunning: false`, fetchError cleared; any error → `fetchError` = `e.toString()` with the previous data kept. Never throws, so the first build always has data.
- [x] Adaptive poll: 3 s while `trading_active`, 10 s otherwise, re-read every cycle; lifecycle-aware.
- [x] Poll cycle / `refreshNow`: state, then (unawaited) equity history and position historicals.
- [x] Equity history: `GET /instances/{id}/portfolio-history?range=`; 1D → `sinceLocalMidnight()`; failures ignored.
- [x] Position historicals: stock positions only (options excluded) → `GET /symbol-historicals?symbols=&range=`; failures ignored.
- [x] `setRange(r)`: set range, refetch equity history, then historicals.
- [x] `runCommand(type, payload)`: cancels pending poll/dismiss; pending toast; `POST /instances/{id}/live-command {type, payload}`; result toast; non-terminal → poll `GET /live-commands/{id}` every 1 s (only while that command's toast is shown); terminal → dismiss after 5 s + `refreshNow`; send failure → `failed` toast with the error, dismiss after 6 s; 30 consecutive poll failures → `Timed out polling command`, dismiss after 6 s.
- [x] `dismissToast`.

## live_trading_screen.dart → `LiveTradingView`

- [x] Loading skeleton; error → banner + Retry.
- [x] Breadcrumb `Back / Live Trading (ID)` → native form: system back button, nav title `Live Trading` with the instance id as the navigation subtitle.
- [x] Header: `monitoring` tile, `Live Trading Terminal`, `Real-time positions & executions` (mono); status pill `NOT RUNNING` / status upper-cased (active green pulsing, halted amber, else dim); chips `Broker offline` (broker_fetch_error), `Container offline` (stale and broker ok), `Feed error` (fetchError with data); `Halt` and `Manual Order` buttons unless not running.
- [x] Lookback banner (when set and running): `HISTORIC LOOKBACK TRAINING`, `spec  ·  start → end`, `current/total`, `N%  ·  date`, bar, `Trades deferred until warmup completes.`
- [x] Not running → EmptyState `power_off`, `No live session`, `This instance has no active broker session. Start the instance to begin live trading.`; live state not loaded yet → body skeleton.
- [x] Hero card: `PORTFOLIO EQUITY`, value (scrubbed history value or live equity), arrow + `+$x.xx` `(+y.yy%)` range label (`today`, `this week`, `this month`, `past 3 months`, `year to date`, `past year`, `all time`); `UPTIME` (`fmtDuration`); chart 240 pt or `No equity history yet — broker is fetching…`; range tabs (active tinted by direction) → native form: segmented `Picker`; style toggle area/line/candle (`area_chart`, `show_chart`, `candlestick_chart`) → native form: segmented `Picker` of symbols; mini stats `RANGE HIGH`, `RANGE LOW`, `DAY P&L` (pct, coloured by day P&L $), `TOTAL P&L` (when the range has a high).
- [x] Secondary stats: `CASH`, `BUYING POWER`, `TOTAL P&L` (coloured).
- [x] `RECENT EXECUTIONS` + count; `No executions recorded yet.`; trade rows (side coloured, symbol, `Option` badge, `FILL PRICE`; `WHEN` fmtDateTime, `SHARES`/`CONTRACTS` quantity, `TOTAL` incl. ×100 for options).
- [x] `ACTIVE POSITIONS` + count; `No open positions.`; position cards with the screen's chart style and range.
- [x] Close position: typed confirm `CLOSE SYM`, `Close Position`, `Submit a market sell for the full quantity of SYM`, `Submit Sell` → `close_position {symbol}` → native form: `.typedConfirmAlert` (confirm disabled until the phrase matches; trigger disabled while running).
- [x] Halt modal: `Halt Live Trading`, warning `This cancels all open orders and stops the running broker. It does NOT liquidate open positions.`, `REASON (OPTIONAL)` (`risk breach` default, hint `e.g. risk breach`), typed `HALT`, Cancel / `Halt Now` (disabled until matched) → `halt {reason}` (`manual halt via UI` when blank); reset on close → native form: `.sheet` with a `Form`.
- [x] Sticky halt pill (bottom-right, unless not running) → native form: floating Liquid Glass button.
- [x] Command toast: icon (pending spinner / completed check / failed error), `TYPE · STATUS`, error (red) or result map, close → native form: floating glass card above the halt button.
- [x] Live logs panel (core `LiveLogsPanel`).
- [x] Pull-to-refresh → native form: added `.refreshable` → `refreshNow` (Dart had none).

## equity_chart.dart → `LiveEquityChart`

- [x] `ChartStyle { area, line, candle }` → `LiveChartStyle`.
- [x] Index x-axis (gapless); 1D area/line on the fixed [0, 1440]-minute axis with `12AM 6AM 12PM 6PM 12AM` labels; otherwise 4 `formatChartDate` labels.
- [x] Candles: points bucketed to ≤ 40 candles (open first, close last, high/low) → native form: Swift Charts `RectangleMark` bodies + `RuleMark` wicks.
- [x] Colour by last ≥ first; flat area fill (no gradient); `No equity data yet` when empty.
- [x] Scrub: 1D nearest minute, else `fractionToIndex`; hairline + dot (no dot on candles); reports the index on change and nil on release → native form: `chartXSelection` + `RuleMark` + selection haptic.
- [x] `RangeStats.from` (high/low/dollars/pct/isUp; nil/1 point → zeros, no high/low; start 0 → pct 0).

## manual_order_sheet.dart → `LiveManualOrderSheet`

- [x] `OrderForm` (symbol, side `buy`, orderType `market`, qty, notional, limitPrice, tif `day`, extendedHours false).
- [x] `validateOrderForm` messages verbatim: `Symbol is required.`, `Enter either qty or notional.`, `Fill qty OR notional, not both.`, `Qty must be a positive number.`, `Notional must be a positive number.`, `Limit order requires a positive limit price.`, `Extended hours requires limit order type + TIF=day.`
- [x] `buildOrderPayload`: `{symbol (trim upper), side, order_type, tif, extended_hours}` + `qty` or `notional` (doubles) + `limit_price` for limit.
- [x] Changing order type away from limit, or TIF away from day, clears extended hours; the toggle is disabled unless limit + day.
- [x] `Manual Order` sheet: `SYMBOL` (`AAPL`), `SIDE` Buy/Sell, `ORDER TYPE` Market/Limit, `QTY (SHARES)` (`0`), `NOTIONAL ($)` (`0.00`), `Fill qty OR notional, not both.`, `LIMIT PRICE` (limit only), `TIF` Day/GTC/IOC/FOK/OPG/CLS, `Extended hours`; error box; Cancel / `Submit Order` (busy) → `submit_order` → native form: `.sheet` with detents and a `Form`.
- [x] No confirmation step (the Dart had none: validation is the guard) — none added, none removed.

## position_card.dart → `LivePositionCard`

- [x] Symbol, `Option` / `Short` badges, `fmtPct` unrealized % (when entry and pct known), option description; range move `+x.xx%  RANGE` with arrow when ≥ 2 points; `MARKET VALUE` (`—` when nil).
- [x] Sparkline 64 pt (90 candle): area / line / ≤ 20 candles; `No price chart for options` / `No price history`.
- [x] Stats: quantity label/text, `LAST`, `ENTRY` (`—` without entry), `P&L $` (`—` without entry; coloured when known).
- [x] `Close` (danger) when closable; else `Managed by the wheel lane, which buys puts back automatically. Close it at the broker if needed.`
- [x] Direction: last ≥ first historical, else unrealized P&L ≥ 0.

## Tests ported

- [x] range_stats_test → `LiveRangeStatsTests`.
- [x] manual_order_validation_test → `LiveOrderFormTests`.
- [x] equity_chart_scrub_test (behaviour) → `LiveEquityGeometryTests` (left edge → first point, right edge → last, 1D minute axis).
- [x] position_card_test (behaviour) → `LivePositionCardLogicTests`.
- [x] trade_row_test (behaviour) → `LivePositionCardLogicTests` (option badge, contracts, ×100 total).
- [x] live_state_options_test → already ported by Wave 1 data.

## Copy changes (HIG title case)

- None on buttons (Dart already title-cased `Manual Order`, `Halt Now`, `Submit Sell`, `Submit Order`).
- Manual order field labels become form row / section labels in title case: `SYMBOL` → `Symbol`, `SIDE` → `Side`, `ORDER TYPE` → `Order Type`, `QTY (SHARES)` → `Qty (Shares)`, `NOTIONAL ($)` → `Notional ($)`, `LIMIT PRICE` → `Limit Price`, `TIF` unchanged; halt `REASON (OPTIONAL)` → `Reason (Optional)`.
- Native nav title `Live Trading` (instance id as the subtitle) added.

## Rulings

- Ruling: the halt sends the reason the operator typed. Dart reset the field to `risk breach` in `_closeHaltModal()` before reading it, so it always sent `risk breach`; the field is labelled `REASON (OPTIONAL)` and the fallback `manual halt via UI` is clearly meant for a blank field — cost if wrong: the backend logs the typed reason instead of `risk breach`.
- Ruling: no confirmation added to Submit Order (Dart had none; byte parity) — flagged in the report for the operator — cost if wrong: a confirm step to add.
- Ruling: command-status polling and toast dismissal run as model-owned tasks with an injectable sleep (Dart `Timer`s) so they are testable — cost if wrong: none.
- Ruling: candles draw as `RectangleMark` bodies with `RuleMark` wicks (Swift Charts has no candlestick mark) — cost if wrong: none.

# Mobile feature-agent contracts & conventions

You are building ONE feature of the IntelliStock Flutter app. The Phase-1
foundation already exists. Follow these rules exactly so parallel agents don't
conflict and everything integrates cleanly.

## Hard rules
1. **Write ONLY inside your assigned `lib/features/<name>/` and `test/features/<name>/`.** Do not edit `pubspec.yaml`, `lib/core/**`, `lib/app.dart`, `lib/main.dart`, or `lib/core/router/**`. Do not edit other features.
2. **NO code generation.** Do NOT use `freezed`, `json_serializable`, or `@riverpod`. Write plain immutable Dart classes with a `fromJson` factory (nullable-tolerant). Use plain Riverpod providers (`Provider`, `AutoDisposeAsyncNotifierProvider`, `NotifierProvider`, `StateProvider`), NOT the generator.
3. **Do NOT run `flutter`/`dart` commands** (no analyze, test, build_runner, pub). The orchestrator verifies centrally. Just write correct code.
4. Match the **dark visual style** exactly (violet `#A78BFA`, glass cards, semantic emerald/red/sky/amber). Reuse the core widgets below — do not reinvent them.
5. All money/percent/dates use the core formatters. Numbers are tabular. Tables that only h-scroll on web become **stacked cards** on mobile.
6. End every file you'd commit as part of a working feature. Write tests in `test/features/<name>/` for any pure logic (formatLike helpers, parsing, controllers).

## Theme tokens — `lib/core/theme/app_colors.dart` (class `AppColors`)
`primary #A78BFA`, `onPrimary #04040C`, `canvas #04040C`, `surface #0F0A1C`, `border #231A3D`, `panel #0F1318`, `panelAlt #0D1117`. Text: `textHi/textMd/textMuted/textDim/textFaint`. Semantic: `success #34D399`, `danger #F87171`, `info #38BDF8`, `warning #FBBF24`, `teal`. Charts: `chartUp #10B981`, `chartDown #EF4444`, `chartLine #38BDF8`, `chartGrid`, `chartAxis`. Helpers: `AppColors.fill(c)` (10% bg), `AppColors.stroke(c)` (20% border).
Text styles: `lib/core/theme/app_text_styles.dart` (class `AppTextStyles`): `h1 h2 h3 cardTitle body bodyHi meta micro nano eyebrow valueLg valueXl value`, and `AppTextStyles.mono(size, color:, weight:)`.

## Core widgets (import from `lib/core/widgets/...`)
- `GlassCard({child, padding, onTap, borderRadius=16, borderColor})` — the standard card.
- `StatusPill({label, color, pulsing})` + static `StatusPill.colorForStatus(String?)`.
- `AppButton.primary/.ghost/.semantic({label, icon, onPressed, busy, color})`.
- `showConfirmDialog(context, {title, body, confirmLabel, confirmColor, icon, onConfirm})` → `Future<bool>`.
- `TypedConfirmField({phrase, onMatchChanged, label})` — the type-to-confirm safety gate.
- `SectionHeader({title, eyebrow, subtitle, trailing})`, `StatTile({label, value, valueColor, sub})`, `AppBadge({label, color})`, `LoadingState({label})`, `EmptyState({icon, title, subtitle, actionLabel, onAction})`, `ErrorBanner({message, onRetry})`, `IconTile({icon, color, size})` — all in `common_widgets.dart`.
- `AppToggle({value, onChanged})` (app_toggle.dart).
- `symbol('icon_name')` → `IconData` (material_symbols.dart). Use web glyph names.
- `AppBackground({child})` is already applied by the shell; standalone (pushed) screens should wrap their body in it via a `Scaffold(backgroundColor: AppColors.canvas, body: AppBackground(child: SafeArea(...)))`.

## Networking — `lib/core/network/...`
- `apiClientProvider` → `ApiClient` with `get/post/put/patch/delete<T>(path, {body, query})`. Returns decoded JSON (`Map`/`List`). Throws `ApiError` (has `.message`, `.statusCode`).
- Base URL is preset; use **bare paths** like `/instances` (NOT `/api/instances`).
- `sessionProvider` → `SessionStore` (ChangeNotifier): `.token`, `.user`, `.isAuthenticated`, `.username`, `.hasCompletedOnboarding`, `setSession(token,user)`, `setUser(map)`, `clear()`.

## Polling — `lib/core/polling/...`
- Extend `PollingNotifier<T>` (an `AutoDisposeAsyncNotifier<T>`): implement `Future<T> fetch()` and `Duration interval()` (may read `state`). It auto-polls, pauses on background, cancels on dispose. Call `refreshNow()` to force. Create the provider with `AutoDisposeAsyncNotifierProvider<MyNotifier, T>(MyNotifier.new)`.
- `LogTailer({client, pathBuilder, runningInterval, idleInterval})` for cursor log tailing: `.stream` of `LogTailerState{lines:List<LogLine>, nextLine, totalLines, truncated, finalStatus, source, buildId, loading, error}`. `LogLine{ts, message, level, color}`. `start/pause/resume/dispose`.

## Formatters — `lib/core/formatters/formatters.dart`
`fmtMoney`, `fmtPnl` (+$/-$), `fmtPct` (+12.34%), `fmtUsdCost`, `fmtTokens` (1.2M/3.4k), `fmtDuration`, `fmtElapsed`, `fmtDateTime`, `fmtDate`, `fmtRelative`, `parseDateTime`, `pnlColor(num?)`.

## Charts (Syncfusion `syncfusion_flutter_charts`)
Import `package:syncfusion_flutter_charts/charts.dart`. Use `SfCartesianChart` with `AreaSeries`/`LineSeries`/`CandleSeries`/stacked `ColumnSeries`. Dark: set `plotAreaBorderWidth:0`, axis `majorGridLines: MajorGridLines(color: AppColors.chartGrid)`, axis labels `axisLabel` color `AppColors.chartAxis`. For equity use a numeric/sequential x-axis to avoid weekend gaps; color up=`chartUp`/down=`chartDown`.

## Wiring manifest (return this as your final message)
After writing your feature, return a concise manifest:
- **Screens**: each route path → the screen widget class + file (so the orchestrator wires the router; the router currently shows placeholders).
- **Providers**: provider variable names you exported.
- **Deps**: any pub package you needed beyond what's in pubspec (avoid if possible).
- **Cross-feature needs**: anything you assumed another feature provides.
- **Notes/risks**: anything the integrator must know.

Keep your final message to the manifest + a 2-3 line summary. The code is the deliverable, not prose.

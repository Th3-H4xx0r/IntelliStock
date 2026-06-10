# Mobile Chart Scrubbing & Cleanup — Design

**Date:** 2026-06-10
**Branch:** `feat/mobile-chart-scrubbing`
**Status:** Approved (design decisions confirmed with user)

## Problem

On the mobile app's equity/value charts (dashboard, live-trading terminal, backtests):

1. The vertical (Y) axis price labels are unwanted — should be removed for a clean
   Robinhood-style look.
2. The scrub line does **not** align with the value it reports, and the reported value
   is shifted right of where the line visually sits.
3. Scrubbing glitches/janks while dragging.
4. Lines should be smoother, and scrubbing should feel smooth, responsive, and give a
   soft haptic tick like the Robinhood app.

## Root cause of the misalignment

The custom-scrubber charts (`portfolio_chart.dart`, `equity_chart.dart`) overlay a
hairline using full-widget-width pixel math:

- `fraction = localDx / widgetWidth`, then `nearestIndex(fraction)` → value.
- Hairline drawn at `fraction * widgetWidth`.

But Syncfusion insets its **plot area** by the Y-axis label gutter on the left (plus
default `rangePadding` and `plotOffset`). The curve therefore lives in
`[gutter → rightEdge]`, while the finger→value mapping assumes `[0 → rightEdge]`. Net:
the reported value is shifted **right** of the curve. The dashboard hairline is worse —
it is positioned against `MediaQuery.size.width` (the whole screen), not the card.

The jank is separate: every drag frame calls `setState`, rebuilding the **entire**
Syncfusion chart (and, on dashboard, the whole card).

## Decisions (confirmed with user)

- **Axis labels:** hide Y (price) labels on all equity/value charts; **keep** clean X
  date/time labels (and fix the dashboard's raw-epoch `1781108200000` label bug).
- **Smoothing:** subtle spline, using `SplineType.monotonic` — smooth but **zero
  overshoot**, so it never invents false highs/lows on a money chart and the curve still
  passes exactly through each data point (keeps scrub accurate).

## Solution

### 1. Plot-area geometry (the alignment fix)
For each scrubbable equity chart:
- `primaryYAxis.isVisible = false` → removes the left gutter entirely (also satisfies the
  "no Y labels" request).
- Explicit Y `minimum`/`maximum` derived from the data with a small symmetric pad, and
  `rangePadding: ChartRangePadding.none` → deterministic vertical mapping (lets us place a
  dot precisely and keeps the curve from being clipped).
- X axis: `plotOffset: 0`, `rangePadding: ChartRangePadding.none`,
  `edgeLabelPlacement: EdgeLabelPlacement.hide`.

Result: **plot area == widget rect**, so `fraction = dx / width` maps exactly onto the
curve, and a hairline at `fraction * width` sits on it.

### 2. X labels
- `portfolio_chart.dart`: switch X from raw-epoch `NumericAxis` to `DateTimeAxis` with a
  per-range date formatter (`HH:mm` for 1D, `EEE`/`MMM d`/`MMM ''yy` for longer ranges).
- `equity_chart.dart` and the backtest charts already format X correctly — kept.

### 3. Smooth, responsive scrubbing (kills the glitch)
- **Cache the `SfCartesianChart`** so it is built only when data/range/style change, never
  on a scrub frame.
- Drive the scrubber via a `ValueNotifier<ScrubSample?>`; a thin `CustomPaint` overlay
  (hairline + dot) and the header value text rebuild via `ValueListenableBuilder` only.
- The `SfCartesianChart` lives under a `RepaintBoundary` so the overlay never repaints it.

### 4. Haptics
- `HapticFeedback.selectionClick()` fired **once per data point crossed** (on index
  change), matching Robinhood's tick-per-point feel.

### 5. Line smoothing
- `AreaSeries → SplineAreaSeries`, `LineSeries → SplineSeries`, `splineType:
  SplineType.monotonic`. Candlesticks unchanged.

### 6. Shared, tested helper (`mobile/lib/core/charts/`)
Extract the fragile bits into one tested module so both scrubbers share one correct
implementation:
- `chart_geometry.dart` — pure functions: `fractionToIndex`, `indexToFraction`,
  `valueToY`, `dataBounds` (min/max with pad). Unit-tested.
- `borderless_axes.dart` — factory helpers returning the hidden-Y / borderless-X
  `NumericAxis`/`DateTimeAxis` configs.
- `scrub_controller.dart` — `ScrubController` (a `ValueNotifier`) that holds the current
  sample and fires `HapticFeedback.selectionClick()` only when the index changes. Haptic
  call is injected so it can be unit-tested with a fake.

## Scope

**Full treatment (Y off / keep X / monotonic spline):**
- `dashboard/.../portfolio_chart.dart` — + scrub-alignment fix, repaint isolation, haptics.
- `live_trading/.../equity_chart.dart` — + scrub-alignment fix, repaint isolation, haptics.
- `live_trading/.../position_card.dart` — sparklines: spline only (axes already hidden, no
  scrubber).
- `backtests/.../backtest_playback_screen.dart` — Y off, keep X, spline.
- `backtests/.../backtest_detail_screen.dart` (×2 charts) — Y off, keep X, spline.
  Syncfusion-native trackball already aligns correctly.

**Deliberately left as-is** (analytical charts where the Y magnitude *is* the information):
- `token_usage/.../token_usage_screen.dart` — "Cost (USD)" stacked chart (axis title +
  legend).
- `chatbot/.../chat_rich_block.dart` — LLM-generated arbitrary charts.

## Testing

- Unit tests for `chart_geometry` pure functions and the `ScrubController` haptic-on-change
  logic (fake haptic sink).
- Existing `nearestIndex` / `computeChange` tests kept green.
- Widget test: scrubbing an equity chart reports the index whose value matches the curve at
  that fraction (alignment regression guard), and no `$`-prefixed Y label text is rendered.
- `flutter analyze` + `flutter test` green; manual run on simulator to confirm feel.

## Risk

LOW — changes are confined to the presentation layer. The two scrubbable widgets are leaf
widgets (`EquityChart` used only by the live-trading screen; `PortfolioChart` only by the
dashboard). GitNexus's index does not cover the Dart layer, so impact was assessed by hand.

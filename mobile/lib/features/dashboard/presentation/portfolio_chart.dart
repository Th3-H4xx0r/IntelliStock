import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/charts/chart_decorations.dart';
import '../../../core/charts/chart_geometry.dart';
import '../../../core/charts/scrub_controller.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/models/portfolio_history.dart';
import '../data/dashboard_repository.dart';

// ── Range constants ───────────────────────────────────────────────────────────

const _ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'];

// ── Per-account portfolio history state ──────────────────────────────────────

class _HistoryArgs {
  const _HistoryArgs(this.id, this.range);
  final String id;
  final String range;

  @override
  bool operator ==(Object other) =>
      other is _HistoryArgs && other.id == id && other.range == range;

  @override
  int get hashCode => Object.hash(id, range);
}

/// AutoDispose family provider for portfolio history (one per account + range).
final _historyProvider = AutoDisposeAsyncNotifierProviderFamily<
    _HistoryNotifier, PortfolioHistory, _HistoryArgs>(
  _HistoryNotifier.new,
);

class _HistoryNotifier
    extends AutoDisposeFamilyAsyncNotifier<PortfolioHistory, _HistoryArgs> {
  @override
  Future<PortfolioHistory> build(_HistoryArgs arg) =>
      ref.read(dashboardRepositoryProvider).portfolioHistory(arg.id, arg.range);
}

// ── Change % helper (pure, also tested) ──────────────────────────────────────

/// Compute absolute and percent change vs baseline (open_value or first point).
/// Returns (changeAbs, changePct) or (null, null) if not enough data.
(double?, double?) computeChange(
  PortfolioHistory history, {
  int? scrubIndex,
}) {
  if (history.isEmpty) return (null, null);

  final baseline = history.openValue ?? history.values.first;
  final double active;
  if (scrubIndex != null &&
      scrubIndex >= 0 &&
      scrubIndex < history.values.length) {
    active = history.values[scrubIndex];
  } else {
    active = history.currentValue ?? history.values.last;
  }

  final abs = active - baseline;
  if (baseline == 0) return (abs, null);
  final pct = (abs / baseline) * 100;
  return (abs, pct);
}

/// Find nearest data-point index by timestamp fraction across [0..1].
int nearestIndex(List<DateTime> timestamps, double fraction) {
  if (timestamps.isEmpty) return 0;
  if (timestamps.length == 1) return 0;
  final target = timestamps.first.millisecondsSinceEpoch +
      fraction *
          (timestamps.last.millisecondsSinceEpoch -
              timestamps.first.millisecondsSinceEpoch);
  var lo = 0;
  var hi = timestamps.length - 1;
  while (lo < hi) {
    final mid = (lo + hi) ~/ 2;
    if (timestamps[mid].millisecondsSinceEpoch < target) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (lo <= 0) return 0;
  final prev = lo - 1;
  return (timestamps[lo].millisecondsSinceEpoch - target).abs() <
          (timestamps[prev].millisecondsSinceEpoch - target).abs()
      ? lo
      : prev;
}

// ── Main widget ───────────────────────────────────────────────────────────────

class PortfolioChart extends ConsumerStatefulWidget {
  const PortfolioChart({super.key, required this.account});

  /// A [BrokerageAccount] from the dashboard brokerages list.
  final dynamic account; // BrokerageAccount from dashboard_repository.dart

  @override
  ConsumerState<PortfolioChart> createState() => _PortfolioChartState();
}

class _PortfolioChartState extends ConsumerState<PortfolioChart>
    with AutomaticKeepAliveClientMixin {
  String _range = '1M';

  // Scrub state lives in a ValueNotifier so dragging repaints only the hairline
  // + header value, never the (expensive) Syncfusion chart — that's what made
  // the old setState-per-frame scrubber jank.
  final ScrubController _scrub = ScrubController();

  // Animate the chart only on first reveal — not every time it scrolls back
  // into view or polls.
  bool _animatedOnce = false;

  // Keep the chart alive while scrolled off-screen so it isn't recreated
  // (which would re-fetch and re-run the entry animation).
  @override
  bool get wantKeepAlive => true;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  _HistoryArgs get _args => _HistoryArgs(widget.account.id as String, _range);

  void _setRange(String r) {
    if (r == _range) return;
    _scrub.clear();
    setState(() => _range = r);
  }

  @override
  Widget build(BuildContext context) {
    super.build(context); // for AutomaticKeepAliveClientMixin
    final histAsync = ref.watch(_historyProvider(_args));
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _CardHeader(account: widget.account),
                const SizedBox(height: 12),
                histAsync.when(
                  loading: () => _ValueSkeleton(),
                  error: (e, _) => Text(
                    e.toString(),
                    style:
                        AppTextStyles.micro.copyWith(color: AppColors.danger),
                  ),
                  data: (h) => ValueListenableBuilder<ScrubSample?>(
                    valueListenable: _scrub,
                    builder: (_, sample, _) => _ValueRow(
                      history: h,
                      scrubIndex: sample?.index,
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                _RangeTabs(active: _range, onSelect: _setRange),
              ],
            ),
          ),
          const SizedBox(height: 8),
          histAsync.when(
            loading: () => const _ChartSkeleton(),
            error: (_, _) => const _ChartEmpty(message: 'Failed to load'),
            data: (history) {
              if (history.isEmpty) {
                return const _ChartEmpty(message: 'No data for this range');
              }
              // Play the entrance animation only the first time the chart
              // renders with data; flip the flag afterwards so scrolling away
              // and back (state kept alive) doesn't re-animate.
              final shouldAnimate = !_animatedOnce;
              if (shouldAnimate) {
                WidgetsBinding.instance.addPostFrameCallback(
                  (_) => _animatedOnce = true,
                );
              }
              return _ChartArea(
                history: history,
                scrub: _scrub,
                range: _range,
                animate: shouldAnimate,
              );
            },
          ),
        ],
      ),
    );
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────────

class _CardHeader extends StatelessWidget {
  const _CardHeader({required this.account});
  final dynamic account;

  @override
  Widget build(BuildContext context) {
    final type = account.brokerageType as String? ?? '';
    final isPaper =
        type == 'alpaca' && (account.alpacaPaper as bool? ?? false);
    final label = type == 'alpaca'
        ? (isPaper ? 'Alpaca · Paper' : 'Alpaca')
        : 'Robinhood';
    final icon = type == 'alpaca' ? symbol('show_chart') : symbol('savings');
    final isActive = (account.status as String? ?? '') == 'active';
    final statusColor = isActive ? AppColors.success : AppColors.danger;

    return Row(
      children: [
        Expanded(
          child: Row(
            children: [
              Icon(icon, color: AppColors.primary, size: 16),
              const SizedBox(width: 6),
              Text(
                label.toUpperCase(),
                style: AppTextStyles.nano.copyWith(
                  color: AppColors.textDim,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.0,
                ),
              ),
            ],
          ),
        ),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              decoration:
                  BoxDecoration(shape: BoxShape.circle, color: statusColor),
            ),
            const SizedBox(width: 4),
            Text(
              account.status as String? ?? '',
              style: AppTextStyles.nano.copyWith(color: statusColor),
            ),
          ],
        ),
      ],
    );
  }
}

class _ValueRow extends StatelessWidget {
  const _ValueRow({required this.history, this.scrubIndex});
  final PortfolioHistory history;
  final int? scrubIndex;

  @override
  Widget build(BuildContext context) {
    final activeValue = (scrubIndex != null &&
            scrubIndex! >= 0 &&
            scrubIndex! < history.values.length)
        ? history.values[scrubIndex!]
        : (history.currentValue ?? (history.values.isNotEmpty ? history.values.last : 0.0));

    final (changeAbs, changePct) =
        computeChange(history, scrubIndex: scrubIndex);
    final positive = (changeAbs ?? 0) >= 0;
    final changeColor = positive ? AppColors.success : AppColors.danger;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          fmtMoney(activeValue),
          style: AppTextStyles.valueLg,
        ),
        const SizedBox(height: 4),
        Row(
          children: [
            Icon(
              positive ? symbol('trending_up') : symbol('trending_down'),
              size: 14,
              color: changeColor,
            ),
            const SizedBox(width: 4),
            Text(
              '${fmtPnl(changeAbs)} (${fmtPct(changePct)})',
              style: AppTextStyles.meta.copyWith(
                color: changeColor,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _ValueSkeleton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Skeleton(width: 120, height: 24, radius: 6),
        const SizedBox(height: 6),
        Skeleton(width: 80, height: 13, radius: 5),
      ],
    );
  }
}

class _RangeTabs extends StatelessWidget {
  const _RangeTabs({required this.active, required this.onSelect});
  final String active;
  final void Function(String) onSelect;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 2,
      children: _ranges.map((r) {
        final isActive = r == active;
        return GestureDetector(
          onTap: () => onSelect(r),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            padding:
                const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: isActive
                  ? AppColors.primary.withValues(alpha: 0.15)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              r,
              style: AppTextStyles.micro.copyWith(
                color: isActive ? AppColors.primary : AppColors.textDim,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        );
      }).toList(),
    );
  }
}

class _ChartSkeleton extends StatelessWidget {
  const _ChartSkeleton();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
      child: Skeleton(height: 180, radius: 8),
    );
  }
}

class _ChartEmpty extends StatelessWidget {
  const _ChartEmpty({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 180,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(symbol('bar_chart_4_bars'),
                size: 32, color: AppColors.textFaint),
            const SizedBox(height: 8),
            Text(message,
                style:
                    AppTextStyles.micro.copyWith(color: AppColors.textFaint)),
          ],
        ),
      ),
    );
  }
}

class _ChartArea extends StatelessWidget {
  const _ChartArea({
    required this.history,
    required this.scrub,
    required this.range,
    required this.animate,
  });

  final PortfolioHistory history;
  final ScrubController scrub;
  final String range;

  /// Whether the area series should play its entrance animation. Only true on
  /// the chart's first build; suppressed afterwards so scrolling away and back
  /// doesn't re-animate.
  final bool animate;

  static const double _plotHeight = 172;

  void _onDrag(double localDx, double width) {
    if (history.values.isEmpty || width <= 0) return;
    final n = history.values.length;
    final frac = (localDx / width).clamp(0.0, 1.0);
    final idx = fractionToIndex(frac, n);
    // Draw the hairline + dot at the data point's own fraction so they sit
    // exactly on the curve and match the value the header reports.
    scrub.update(idx, indexToFraction(idx, n));
  }

  @override
  Widget build(BuildContext context) {
    final (changeAbs, _) = computeChange(history);
    final positive = (changeAbs ?? 0) >= 0;
    final lineColor = positive ? AppColors.success : AppColors.danger;

    final n = history.values.length;
    final bounds = paddedBounds(history.values);

    // Index-based points (gapless across weekends/closed sessions).
    final dataSource = List<_ChartPoint>.generate(
      n,
      (i) => _ChartPoint(x: i.toDouble(), y: history.values[i]),
    );

    final labels = [
      for (final i in evenlySpacedLabelIndices(n, 4))
        formatChartDate(history.timestamps[i], range),
    ];

    final chart = SfCartesianChart(
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis: edgeToEdgeIndexAxis(n),
      primaryYAxis: hiddenValueAxis(minimum: bounds.min, maximum: bounds.max),
      tooltipBehavior: TooltipBehavior(enable: false),
      series: <CartesianSeries<_ChartPoint, double>>[
        SplineAreaSeries<_ChartPoint, double>(
          dataSource: dataSource,
          splineType: SplineType.monotonic,
          animationDuration: animate ? 900 : 0,
          xValueMapper: (pt, _) => pt.x,
          yValueMapper: (pt, _) => pt.y,
          color: lineColor.withValues(alpha: 0.12),
          borderColor: lineColor,
          borderWidth: 2,
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              lineColor.withValues(alpha: 0.35),
              lineColor.withValues(alpha: 0.0),
            ],
          ),
        ),
      ],
    );

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: _plotHeight,
            child: LayoutBuilder(
              builder: (context, constraints) {
                final width = constraints.maxWidth;
                return Stack(
                  children: [
                    // The chart never repaints during a scrub: it isn't wrapped
                    // in the ValueListenableBuilder below, so notifier ticks
                    // don't reach it.
                    Positioned.fill(child: RepaintBoundary(child: chart)),
                    // Hairline + dot — repaints only on scrub.
                    Positioned.fill(
                      child: IgnorePointer(
                        child: ValueListenableBuilder<ScrubSample?>(
                          valueListenable: scrub,
                          builder: (_, sample, _) {
                            if (sample == null ||
                                sample.index < 0 ||
                                sample.index >= n) {
                              return const SizedBox.shrink();
                            }
                            final dotY = valueToY(
                              history.values[sample.index],
                              bounds.min,
                              bounds.max,
                              _plotHeight,
                            );
                            return CustomPaint(
                              painter: ScrubPainter(
                                fraction: sample.fraction,
                                dotY: dotY,
                                color: lineColor,
                              ),
                            );
                          },
                        ),
                      ),
                    ),
                    // Gesture overlay.
                    Positioned.fill(
                      child: GestureDetector(
                        behavior: HitTestBehavior.translucent,
                        onHorizontalDragStart: (d) =>
                            _onDrag(d.localPosition.dx, width),
                        onHorizontalDragUpdate: (d) =>
                            _onDrag(d.localPosition.dx, width),
                        onHorizontalDragEnd: (_) => scrub.clear(),
                        onHorizontalDragCancel: scrub.clear,
                      ),
                    ),
                  ],
                );
              },
            ),
          ),
          ChartDateLabels(labels: labels),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _ChartPoint {
  const _ChartPoint({required this.x, required this.y});
  final double x;
  final double y;
}

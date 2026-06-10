import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
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

// ── Scrubber state ────────────────────────────────────────────────────────────

/// Holds the pan-gesture scrubber state for one chart.
class _ScrubState {
  const _ScrubState({this.index, this.linePercent});

  final int? index;

  /// 0..100, the % x-position of the vertical hairline.
  final double? linePercent;

  _ScrubState copyWith({int? index, double? linePercent, bool clearIndex = false}) =>
      _ScrubState(
        index: clearIndex ? null : (index ?? this.index),
        linePercent: linePercent ?? this.linePercent,
      );

  static const empty = _ScrubState();
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

class _PortfolioChartState extends ConsumerState<PortfolioChart> {
  String _range = '1M';
  _ScrubState _scrub = _ScrubState.empty;

  // chart render key so we can get its RenderBox during scrub
  final _chartKey = GlobalKey();

  _HistoryArgs get _args => _HistoryArgs(widget.account.id as String, _range);

  void _setRange(String r) {
    if (r == _range) return;
    setState(() {
      _range = r;
      _scrub = _ScrubState.empty;
    });
  }

  void _onPanUpdate(DragUpdateDetails d, PortfolioHistory history) {
    final box = _chartKey.currentContext?.findRenderObject() as RenderBox?;
    if (box == null || history.isEmpty) return;
    final local = box.globalToLocal(d.globalPosition);
    final frac = (local.dx / box.size.width).clamp(0.0, 1.0);
    final idx = nearestIndex(history.timestamps, frac);
    setState(() {
      _scrub = _ScrubState(index: idx, linePercent: frac * 100);
    });
  }

  void _onPanEnd(PortfolioHistory history) {
    setState(() => _scrub = _ScrubState.empty);
  }

  @override
  Widget build(BuildContext context) {
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
                  data: (h) => _ValueRow(
                    history: h,
                    scrubIndex: _scrub.index,
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
              return _ChartArea(
                chartKey: _chartKey,
                history: history,
                scrub: _scrub,
                onPanUpdate: (d) => _onPanUpdate(d, history),
                onPanEnd: () => _onPanEnd(history),
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
        Container(
          height: 24,
          width: 120,
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(6),
          ),
        ),
        const SizedBox(height: 6),
        Container(
          height: 14,
          width: 80,
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(4),
          ),
        ),
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
    return Container(
      height: 180,
      margin: const EdgeInsets.fromLTRB(4, 0, 4, 12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
      ),
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
    required this.chartKey,
    required this.history,
    required this.scrub,
    required this.onPanUpdate,
    required this.onPanEnd,
  });

  final GlobalKey chartKey;
  final PortfolioHistory history;
  final _ScrubState scrub;
  final void Function(DragUpdateDetails) onPanUpdate;
  final VoidCallback onPanEnd;

  @override
  Widget build(BuildContext context) {
    final (changeAbs, _) = computeChange(history);
    final positive = (changeAbs ?? 0) >= 0;
    final lineColor = positive ? AppColors.success : AppColors.danger;

    // Build Syncfusion chart data points
    final dataSource = List<_ChartPoint>.generate(history.values.length, (i) {
      return _ChartPoint(
        x: history.timestamps[i].millisecondsSinceEpoch.toDouble(),
        y: history.values[i],
      );
    });

    return SizedBox(
      height: 192,
      child: Stack(
        children: [
          Positioned.fill(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
              child: SfCartesianChart(
                key: chartKey,
                plotAreaBorderWidth: 0,
                margin: EdgeInsets.zero,
                primaryXAxis: NumericAxis(
                  isVisible: true,
                  majorGridLines: const MajorGridLines(width: 0),
                  minorGridLines: const MinorGridLines(width: 0),
                  axisLine: const AxisLine(width: 0),
                  majorTickLines: const MajorTickLines(size: 0),
                  labelStyle: TextStyle(
                    color: AppColors.chartAxis,
                    fontSize: 10,
                  ),
                  numberFormat: null,
                  labelFormat: '',
                ),
                primaryYAxis: NumericAxis(
                  isVisible: true,
                  axisLine: const AxisLine(width: 0),
                  majorTickLines: const MajorTickLines(size: 0),
                  majorGridLines: MajorGridLines(
                    color: AppColors.chartGrid,
                    dashArray: const [4, 4],
                  ),
                  labelStyle: TextStyle(
                    color: AppColors.chartAxis,
                    fontSize: 10,
                  ),
                  desiredIntervals: 4,
                  numberFormat: null,
                  labelFormat: '\${value}',
                ),
                tooltipBehavior: TooltipBehavior(enable: false),
                series: <CartesianSeries<_ChartPoint, double>>[
                  AreaSeries<_ChartPoint, double>(
                    dataSource: dataSource,
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
              ),
            ),
          ),
          // Scrubber hairline
          if (scrub.linePercent != null)
            Positioned(
              top: 0,
              bottom: 12,
              left: (scrub.linePercent! / 100) *
                  (MediaQuery.of(context).size.width - 8),
              width: 1,
              child: IgnorePointer(
                child: Container(
                  color: AppColors.primary.withValues(alpha: 0.5),
                ),
              ),
            ),
          // Gesture overlay
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: onPanUpdate,
              onHorizontalDragEnd: (_) => onPanEnd(),
              onHorizontalDragCancel: onPanEnd,
              child: const SizedBox.expand(),
            ),
          ),
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

import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/charts/chart_decorations.dart';
import '../../../core/charts/chart_geometry.dart';
import '../../../core/charts/scrub_controller.dart';
import '../../../core/models/portfolio_history.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';

enum ChartStyle { area, line, candle }

/// A sequential-index equity chart supporting area / line / candlestick modes.
/// Uses numeric x-axis (index-based) so weekends never create gaps.
/// Includes a scrubber that fires [onScrub] with the nearest data index.
class EquityChart extends StatefulWidget {
  const EquityChart({
    super.key,
    required this.history,
    required this.style,
    required this.range,
    this.height = 280,
    this.onScrub,
    this.onScrubEnd,
  });

  final PortfolioHistory history;
  final ChartStyle style;
  final String range;
  final double height;
  final ValueChanged<int?>? onScrub;
  final VoidCallback? onScrubEnd;

  @override
  State<EquityChart> createState() => _EquityChartState();
}

class _EquityChartState extends State<EquityChart> {
  final ScrubController _scrub = ScrubController();

  // Cached Syncfusion chart so a parent rebuild (e.g. the header showing the
  // scrubbed value) doesn't rebuild the expensive chart — only the thin overlay
  // repaints while scrubbing.
  Widget? _cachedChart;
  PortfolioHistory? _cacheHistory;
  ChartStyle? _cacheStyle;
  String? _cacheRange;

  static const double _labelRowHeight = 20;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  List<_ChartPoint> get _points {
    final ts = widget.history.timestamps;
    final vs = widget.history.values;
    final n = ts.length < vs.length ? ts.length : vs.length;
    return List.generate(n, (i) => _ChartPoint(i, vs[i], ts[i]));
  }

  List<_CandlePoint> _bucketCandles(List<_ChartPoint> pts, {int count = 40}) {
    if (pts.length < 2) return [];
    final samplesPer = ((pts.length / count).ceil()).clamp(1, pts.length);
    final out = <_CandlePoint>[];
    for (int i = 0; i < pts.length; i += samplesPer) {
      final slice = pts.skip(i).take(samplesPer).toList();
      if (slice.isEmpty) continue;
      final open = slice.first.value;
      final close = slice.last.value;
      double high = double.negativeInfinity, low = double.infinity;
      for (final p in slice) {
        if (p.value > high) high = p.value;
        if (p.value < low) low = p.value;
      }
      out.add(_CandlePoint(out.length, open, high, low, close, slice.first.ts));
    }
    return out;
  }

  bool get _isUp {
    final vs = widget.history.values;
    if (vs.length < 2) return true;
    return vs.last >= vs.first;
  }

  Color get _lineColor => _isUp ? AppColors.chartUp : AppColors.chartDown;

  @override
  Widget build(BuildContext context) {
    final pts = _points;
    if (pts.isEmpty) {
      return SizedBox(
        height: widget.height,
        child: Center(
          child: Text(
            'No equity data yet',
            style: AppTextStyles.micro.copyWith(color: AppColors.textFaint),
          ),
        ),
      );
    }

    final color = _lineColor;
    final n = pts.length;
    final plotHeight = (widget.height - _labelRowHeight).clamp(
      40.0,
      widget.height,
    );
    final bounds = paddedBounds([for (final p in pts) p.value]);

    final chart = _chartFor(pts, color, bounds);

    // 1D (area/line) plots against a fixed full-day axis → fixed day labels.
    final useTimeAxis =
        widget.range == '1D' && widget.style != ChartStyle.candle;
    final labels = useTimeAxis
        ? [
            for (final h in [0, 6, 12, 18, 24]) hourAmPm(h),
          ]
        : [
            for (final i in evenlySpacedLabelIndices(n, 4))
              formatChartDate(pts[i].ts, widget.range),
          ];

    return SizedBox(
      height: widget.height,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: plotHeight,
            child: LayoutBuilder(
              builder: (context, constraints) {
                final width = constraints.maxWidth;
                return GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onHorizontalDragStart: (d) =>
                      _handleScrub(d.localPosition.dx, width, pts),
                  onHorizontalDragUpdate: (d) =>
                      _handleScrub(d.localPosition.dx, width, pts),
                  onHorizontalDragEnd: (_) => _endScrub(),
                  onHorizontalDragCancel: _endScrub,
                  child: Stack(
                    children: [
                      // The chart is cached and wrapped in a RepaintBoundary, so
                      // a scrub never repaints it.
                      Positioned.fill(child: RepaintBoundary(child: chart)),
                      Positioned.fill(
                        child: IgnorePointer(
                          child: ValueListenableBuilder<ScrubSample?>(
                            valueListenable: _scrub,
                            builder: (_, sample, _) {
                              if (sample == null ||
                                  sample.index < 0 ||
                                  sample.index >= n) {
                                return const SizedBox.shrink();
                              }
                              final dotY = widget.style == ChartStyle.candle
                                  ? null
                                  : valueToY(
                                      pts[sample.index].value,
                                      bounds.min,
                                      bounds.max,
                                      plotHeight,
                                    );
                              return CustomPaint(
                                painter: ScrubPainter(
                                  fraction: sample.fraction,
                                  dotY: dotY,
                                  color: color,
                                ),
                              );
                            },
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),
          ChartDateLabels(labels: labels),
        ],
      ),
    );
  }

  /// Returns the cached Syncfusion chart, rebuilding only when the data, style,
  /// or range changes.
  Widget _chartFor(
    List<_ChartPoint> pts,
    Color color,
    ({double min, double max}) bounds,
  ) {
    if (_cachedChart != null &&
        identical(_cacheHistory, widget.history) &&
        _cacheStyle == widget.style &&
        _cacheRange == widget.range) {
      return _cachedChart!;
    }
    _cacheHistory = widget.history;
    _cacheStyle = widget.style;
    _cacheRange = widget.range;
    return _cachedChart = _buildSyncfusionChart(pts, color, bounds);
  }

  Widget _buildSyncfusionChart(
    List<_ChartPoint> pts,
    Color color,
    ({double min, double max}) bounds,
  ) {
    if (widget.style == ChartStyle.candle) {
      final candles = _bucketCandles(pts);
      if (candles.isEmpty) return const SizedBox.shrink();
      return SfCartesianChart(
        backgroundColor: Colors.transparent,
        plotAreaBorderWidth: 0,
        margin: EdgeInsets.zero,
        primaryXAxis: edgeToEdgeIndexAxis(candles.length),
        primaryYAxis: hiddenValueAxis(minimum: bounds.min, maximum: bounds.max),
        series: <CartesianSeries>[
          CandleSeries<_CandlePoint, num>(
            dataSource: candles,
            xValueMapper: (d, _) => d.x,
            lowValueMapper: (d, _) => d.low,
            highValueMapper: (d, _) => d.high,
            openValueMapper: (d, _) => d.open,
            closeValueMapper: (d, _) => d.close,
            bullColor: AppColors.chartUp,
            bearColor: AppColors.chartDown,
            enableSolidCandles: true,
          ),
        ],
      );
    }

    final isLine = widget.style == ChartStyle.line;
    // Reached only for area/line (candle returns above). 1D plots against a
    // fixed full-day [0,1440]-minute axis so the line fills only the elapsed
    // part of the day.
    final useTimeAxis = widget.range == '1D';
    num xOf(_ChartPoint d) => useTimeAxis ? _minuteOfDay(d.ts) : d.x;
    return SfCartesianChart(
      backgroundColor: Colors.transparent,
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis: useTimeAxis
          ? edgeToEdgeRangeAxis(0, 24 * 60)
          : edgeToEdgeIndexAxis(pts.length),
      primaryYAxis: hiddenValueAxis(minimum: bounds.min, maximum: bounds.max),
      series: <CartesianSeries>[
        if (!isLine)
          SplineAreaSeries<_ChartPoint, num>(
            dataSource: pts,
            splineType: SplineType.monotonic,
            xValueMapper: (d, _) => xOf(d),
            yValueMapper: (d, _) => d.value,
            color: color.withValues(alpha: 0.28),
            borderColor: color,
            borderWidth: 2,
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                color.withValues(alpha: 0.35),
                color.withValues(alpha: 0.02),
              ],
            ),
          )
        else
          SplineSeries<_ChartPoint, num>(
            dataSource: pts,
            splineType: SplineType.monotonic,
            xValueMapper: (d, _) => xOf(d),
            yValueMapper: (d, _) => d.value,
            color: color,
            width: 1.75,
          ),
      ],
    );
  }

  static double _minuteOfDay(DateTime ts) {
    final m = DateTime(ts.year, ts.month, ts.day);
    return ts.difference(m).inSeconds / 60.0;
  }

  void _handleScrub(double dx, double width, List<_ChartPoint> pts) {
    final n = pts.length;
    if (n == 0 || width <= 0) return;
    final frac = (dx / width).clamp(0.0, 1.0);
    final prev = _scrub.value?.index;
    final useTimeAxis =
        widget.range == '1D' && widget.style != ChartStyle.candle;
    int idx;
    double drawFrac;
    if (useTimeAxis) {
      // Nearest point by minute-of-day so the hairline tracks real time.
      final targetMin = frac * (24 * 60);
      idx = 0;
      var best = double.infinity;
      for (var i = 0; i < n; i++) {
        final d = (_minuteOfDay(pts[i].ts) - targetMin).abs();
        if (d < best) {
          best = d;
          idx = i;
        }
      }
      drawFrac = (_minuteOfDay(pts[idx].ts) / (24 * 60)).clamp(0.0, 1.0);
    } else {
      idx = fractionToIndex(frac, n);
      drawFrac = indexToFraction(idx, n);
    }
    // Draw hairline + dot at the data point's own fraction so they land on the
    // curve and match the value the header reports.
    _scrub.update(idx, drawFrac);
    if (idx != prev) widget.onScrub?.call(idx);
  }

  void _endScrub() {
    if (_scrub.value == null) return;
    _scrub.clear();
    widget.onScrub?.call(null);
    widget.onScrubEnd?.call();
  }
}

// ── Range stats ───────────────────────────────────────────────────────────────

class RangeStats {
  const RangeStats({
    required this.high,
    required this.low,
    required this.dollars,
    required this.pct,
    required this.isUp,
  });

  final double? high;
  final double? low;
  final double dollars;
  final double pct;
  final bool isUp;

  static RangeStats from(PortfolioHistory? h) {
    if (h == null || h.values.length < 2) {
      return const RangeStats(
        high: null,
        low: null,
        dollars: 0,
        pct: 0,
        isUp: true,
      );
    }
    final vs = h.values;
    final start = vs.first;
    final end = vs.last;
    final dollars = end - start;
    final pct = start != 0 ? (dollars / start) * 100 : 0.0;
    double high = double.negativeInfinity, low = double.infinity;
    for (final v in vs) {
      if (v > high) high = v;
      if (v < low) low = v;
    }
    return RangeStats(
      high: high == double.negativeInfinity ? null : high,
      low: low == double.infinity ? null : low,
      dollars: dollars,
      pct: pct,
      isUp: dollars >= 0,
    );
  }
}

// ── Internal data types ───────────────────────────────────────────────────────

class _ChartPoint {
  const _ChartPoint(this.x, this.value, this.ts);
  final int x;
  final double value;
  final DateTime ts;
}

class _CandlePoint {
  const _CandlePoint(
    this.x,
    this.open,
    this.high,
    this.low,
    this.close,
    this.ts,
  );
  final int x;
  final double open, high, low, close;
  final DateTime ts;
}

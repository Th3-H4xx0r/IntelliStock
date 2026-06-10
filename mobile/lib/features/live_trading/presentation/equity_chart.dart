import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
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
  int? _scrubIndex;

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

  String _formatLabel(DateTime ts) {
    switch (widget.range) {
      case '1D':
        return '${ts.hour.toString().padLeft(2, '0')}:${ts.minute.toString().padLeft(2, '0')}';
      case '1W':
        const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        return days[(ts.weekday - 1).clamp(0, 6)];
      case '1M':
      case '3M':
      case 'YTD':
        const m = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return '${m[ts.month - 1]} ${ts.day}';
      default:
        const m = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return "${m[ts.month - 1]} '${ts.year.toString().substring(2)}";
    }
  }

  String _formatMoney(double v) {
    if (v.abs() >= 1000000) return '\$${(v / 1000000).toStringAsFixed(1)}M';
    if (v.abs() >= 1000) return '\$${(v / 1000).toStringAsFixed(1)}k';
    return '\$${v.toStringAsFixed(0)}';
  }

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

    Widget chartWidget;
    if (widget.style == ChartStyle.candle) {
      final candles = _bucketCandles(pts);
      chartWidget = SfCartesianChart(
        backgroundColor: Colors.transparent,
        plotAreaBorderWidth: 0,
        margin: EdgeInsets.zero,
        primaryXAxis: NumericAxis(
          isVisible: true,
          majorGridLines: const MajorGridLines(width: 0),
          axisLine: const AxisLine(width: 0),
          labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
          desiredIntervals: 5,
          axisLabelFormatter: (AxisLabelRenderDetails args) {
            final idx = args.value.toInt().clamp(0, candles.length - 1);
            return ChartAxisLabel(
              _formatLabel(candles[idx].ts),
              TextStyle(color: AppColors.chartAxis, fontSize: 9),
            );
          },
        ),
        primaryYAxis: NumericAxis(
          isVisible: true,
          majorGridLines: MajorGridLines(
            color: AppColors.chartGrid,
            dashArray: const <double>[3, 3],
          ),
          axisLine: const AxisLine(width: 0),
          labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
          axisLabelFormatter: (AxisLabelRenderDetails args) {
            return ChartAxisLabel(
              _formatMoney(args.value.toDouble()),
              TextStyle(color: AppColors.chartAxis, fontSize: 9),
            );
          },
        ),
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
    } else {
      final isLine = widget.style == ChartStyle.line;
      chartWidget = SfCartesianChart(
        backgroundColor: Colors.transparent,
        plotAreaBorderWidth: 0,
        margin: EdgeInsets.zero,
        primaryXAxis: NumericAxis(
          isVisible: true,
          majorGridLines: const MajorGridLines(width: 0),
          axisLine: const AxisLine(width: 0),
          labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
          desiredIntervals: 5,
          axisLabelFormatter: (AxisLabelRenderDetails args) {
            final idx = args.value.toInt().clamp(0, pts.length - 1);
            return ChartAxisLabel(
              _formatLabel(pts[idx].ts),
              TextStyle(color: AppColors.chartAxis, fontSize: 9),
            );
          },
        ),
        primaryYAxis: NumericAxis(
          isVisible: true,
          majorGridLines: MajorGridLines(
            color: AppColors.chartGrid,
            dashArray: const <double>[3, 3],
          ),
          axisLine: const AxisLine(width: 0),
          labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
          axisLabelFormatter: (AxisLabelRenderDetails args) {
            return ChartAxisLabel(
              _formatMoney(args.value.toDouble()),
              TextStyle(color: AppColors.chartAxis, fontSize: 9),
            );
          },
        ),
        series: <CartesianSeries>[
          if (!isLine)
            AreaSeries<_ChartPoint, num>(
              dataSource: pts,
              xValueMapper: (d, _) => d.x,
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
            LineSeries<_ChartPoint, num>(
              dataSource: pts,
              xValueMapper: (d, _) => d.x,
              yValueMapper: (d, _) => d.value,
              color: color,
              width: 1.75,
            ),
        ],
      );
    }

    // Scrubber overlay
    return SizedBox(
      height: widget.height,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onHorizontalDragUpdate: (details) =>
            _handleScrub(details.localPosition.dx, context),
        onHorizontalDragEnd: (_) {
          setState(() => _scrubIndex = null);
          widget.onScrub?.call(null);
          widget.onScrubEnd?.call();
        },
        onHorizontalDragCancel: () {
          setState(() => _scrubIndex = null);
          widget.onScrub?.call(null);
        },
        onTapDown: (d) => _handleScrub(d.localPosition.dx, context),
        child: Stack(
          children: [
            chartWidget,
            if (_scrubIndex != null)
              Positioned.fill(
                child: IgnorePointer(
                  child: CustomPaint(
                    painter: _ScrubLinePainter(
                      fraction: _scrubIndex! / (_points.length - 1).clamp(1, 99999),
                      color: _lineColor,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  void _handleScrub(double dx, BuildContext context) {
    final pts = _points;
    if (pts.isEmpty) return;
    final w = context.size?.width ?? 300;
    final frac = (dx / w).clamp(0.0, 1.0);
    final idx = (frac * (pts.length - 1)).round().clamp(0, pts.length - 1);
    if (idx != _scrubIndex) {
      setState(() => _scrubIndex = idx);
      widget.onScrub?.call(idx);
    }
  }
}

class _ScrubLinePainter extends CustomPainter {
  _ScrubLinePainter({required this.fraction, required this.color});
  final double fraction;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final x = size.width * fraction;
    final paint = Paint()
      ..color = color.withValues(alpha: 0.6)
      ..strokeWidth = 1.2
      ..style = PaintingStyle.stroke;
    canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
  }

  @override
  bool shouldRepaint(_ScrubLinePainter old) => old.fraction != fraction;
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
      return const RangeStats(high: null, low: null, dollars: 0, pct: 0, isUp: true);
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
  const _CandlePoint(this.x, this.open, this.high, this.low, this.close, this.ts);
  final int x;
  final double open, high, low, close;
  final DateTime ts;
}

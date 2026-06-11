import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_charts/charts.dart';

import '../theme/app_colors.dart';
import 'chart_decorations.dart';
import 'chart_geometry.dart';
import 'scrub_controller.dart';

/// A reusable equity/value chart with the app's "dashboard" look & feel:
/// hidden axes, a monotonic spline area fill, clean date labels rendered below
/// the plot, and a scrub interaction (hairline + dot on the line + a soft
/// haptic tick) that reports the touched data index.
///
/// Plotted against real time (a DateTime axis spanning first..last) so
/// unevenly-spaced samples and any [markerSeries] (e.g. buy/sell dots) stay
/// aligned with the curve.
class ScrubbableAreaChart extends StatefulWidget {
  const ScrubbableAreaChart({
    super.key,
    required this.timestamps,
    required this.values,
    required this.lineColor,
    this.height = 200,
    this.baseline,
    this.markerSeries,
    this.onScrub,
    this.animate = true,
  });

  final List<DateTime> timestamps;
  final List<double> values;
  final Color lineColor;
  final double height;

  /// Optional horizontal reference line (e.g. starting equity).
  final double? baseline;

  /// Optional extra series painted over the area (e.g. trade markers). Built
  /// lazily so they're only created when the cached chart (re)builds.
  final List<CartesianSeries> Function()? markerSeries;

  /// Reports the scrubbed data index, or null when the finger lifts. Wire this
  /// to a ValueNotifier-backed header so scrubbing doesn't rebuild the chart.
  final ValueChanged<int?>? onScrub;

  final bool animate;

  @override
  State<ScrubbableAreaChart> createState() => _ScrubbableAreaChartState();
}

class _ScrubbableAreaChartState extends State<ScrubbableAreaChart> {
  final ScrubController _scrub = ScrubController();

  // Cache the Syncfusion chart so an incidental rebuild never re-runs it.
  Widget? _cachedChart;
  List<DateTime>? _cacheTs;
  List<double>? _cacheVals;
  double? _cacheHeight;

  static const double _labelRowHeight = 20;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final n = widget.values.length;
    if (n == 0 || widget.timestamps.length != n) {
      return SizedBox(height: widget.height);
    }

    final plotHeight =
        (widget.height - _labelRowHeight).clamp(40.0, widget.height);
    final boundsValues = widget.baseline != null
        ? <double>[...widget.values, widget.baseline!]
        : widget.values;
    final bounds = paddedBounds(boundsValues);

    final chart = _chartFor(bounds);

    final span = widget.timestamps.last.difference(widget.timestamps.first);
    final labels = [
      for (final i in evenlySpacedLabelIndices(n, 4))
        formatChartDateBySpan(widget.timestamps[i], span),
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
                      _onDrag(d.localPosition.dx, width, n),
                  onHorizontalDragUpdate: (d) =>
                      _onDrag(d.localPosition.dx, width, n),
                  onHorizontalDragEnd: (_) => _end(),
                  onHorizontalDragCancel: _end,
                  child: Stack(
                    children: [
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
                              final dotY = valueToY(
                                widget.values[sample.index],
                                bounds.min,
                                bounds.max,
                                plotHeight,
                              );
                              return CustomPaint(
                                painter: ScrubPainter(
                                  fraction: sample.fraction,
                                  dotY: dotY,
                                  color: widget.lineColor,
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

  Widget _chartFor(({double min, double max}) bounds) {
    if (_cachedChart != null &&
        identical(_cacheTs, widget.timestamps) &&
        identical(_cacheVals, widget.values) &&
        _cacheHeight == widget.height) {
      return _cachedChart!;
    }
    _cacheTs = widget.timestamps;
    _cacheVals = widget.values;
    _cacheHeight = widget.height;

    final pts = [
      for (var i = 0; i < widget.values.length; i++)
        _TVPoint(widget.timestamps[i], widget.values[i]),
    ];
    final c = widget.lineColor;

    return _cachedChart = SfCartesianChart(
      backgroundColor: Colors.transparent,
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis: DateTimeAxis(
        isVisible: false,
        plotOffset: 0,
        rangePadding: ChartRangePadding.none,
        majorGridLines: const MajorGridLines(width: 0),
        axisLine: const AxisLine(width: 0),
        majorTickLines: const MajorTickLines(size: 0),
      ),
      primaryYAxis: hiddenValueAxis(minimum: bounds.min, maximum: bounds.max),
      annotations: widget.baseline != null
          ? <CartesianChartAnnotation>[
              CartesianChartAnnotation(
                widget: Container(
                  width: double.infinity,
                  height: 1,
                  color: AppColors.textFaint.withValues(alpha: 0.4),
                ),
                coordinateUnit: CoordinateUnit.point,
                x: widget.timestamps.first,
                y: widget.baseline,
              ),
            ]
          : null,
      series: <CartesianSeries>[
        SplineAreaSeries<_TVPoint, DateTime>(
          dataSource: pts,
          splineType: SplineType.monotonic,
          animationDuration: widget.animate ? 700 : 0,
          xValueMapper: (p, _) => p.t,
          yValueMapper: (p, _) => p.v,
          color: c.withValues(alpha: 0.18),
          borderColor: c,
          borderWidth: 2,
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              c.withValues(alpha: 0.34),
              c.withValues(alpha: 0.0),
            ],
          ),
        ),
        if (widget.markerSeries != null) ...widget.markerSeries!(),
      ],
    );
  }

  void _onDrag(double dx, double width, int n) {
    if (width <= 0) return;
    final frac = (dx / width).clamp(0.0, 1.0);
    final idx = nearestIndexByTime(widget.timestamps, frac);
    final prev = _scrub.value?.index;
    // Draw the hairline + dot at the data point's own time-fraction so they sit
    // on the curve and match the value reported to the header.
    _scrub.update(idx, timeFractionOf(widget.timestamps, idx));
    if (idx != prev) widget.onScrub?.call(idx);
  }

  void _end() {
    if (_scrub.value == null) return;
    _scrub.clear();
    widget.onScrub?.call(null);
  }
}

class _TVPoint {
  const _TVPoint(this.t, this.v);
  final DateTime t;
  final double v;
}

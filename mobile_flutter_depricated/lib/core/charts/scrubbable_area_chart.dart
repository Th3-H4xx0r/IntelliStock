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
    this.indexed = false,
    this.pulsingEndDot = false,
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

  /// Plot points evenly spaced on a numeric index axis instead of real time —
  /// no weekend/overnight gaps, so the spline can't balloon across them. The
  /// scrub + date labels are already index-positioned, so this also makes them
  /// line up with the curve.
  final bool indexed;

  /// When true, a live "ping" dot pulses at the end of the line (the latest
  /// value) while not scrubbing — the dashboard's live-updating marker.
  final bool pulsingEndDot;

  @override
  State<ScrubbableAreaChart> createState() => _ScrubbableAreaChartState();
}

class _ScrubbableAreaChartState extends State<ScrubbableAreaChart>
    with AutomaticKeepAliveClientMixin {
  final ScrubController _scrub = ScrubController();

  // Cache the Syncfusion chart so an incidental rebuild never re-runs it.
  Widget? _cachedChart;
  List<DateTime>? _cacheTs;
  List<double>? _cacheVals;
  double? _cacheHeight;
  // The Syncfusion entry animation must run AT MOST ONCE. Keep-alive stops the
  // State (and _cachedChart) from being disposed when scrolled out of view, so
  // scrolling back doesn't rebuild+replay; this flag additionally stops a data
  // refresh from re-animating within a live State.
  bool _animatedOnce = false;

  @override
  bool get wantKeepAlive => true;

  static const double _labelRowHeight = 20;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context); // required by AutomaticKeepAliveClientMixin
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
                                // Not scrubbing → a live pulse at the latest point.
                                if (!widget.pulsingEndDot) {
                                  return const SizedBox.shrink();
                                }
                                final endFrac = widget.indexed
                                    ? 1.0
                                    : indexToFraction(n - 1, n);
                                return _PulsingEndDot(
                                  fraction: endFrac.clamp(0.0, 1.0),
                                  dotY: valueToY(widget.values[n - 1],
                                      bounds.min, bounds.max, plotHeight),
                                  color: widget.lineColor,
                                );
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

    // Animate only the first build in this State's lifetime (keep-alive makes
    // that "once per screen visit", not "once per scroll").
    final animate = widget.animate && !_animatedOnce;
    _animatedOnce = true;

    final pts = [
      for (var i = 0; i < widget.values.length; i++)
        _TVPoint(widget.timestamps[i], widget.values[i]),
    ];
    final c = widget.lineColor;
    final fill = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: [c.withValues(alpha: 0.34), c.withValues(alpha: 0.0)],
    );

    // `indexed` → evenly-spaced numeric x (no weekend/overnight gaps, so the
    // monotonic spline can't balloon across them). Else plot against real time.
    final ChartAxis xAxis = widget.indexed
        ? NumericAxis(
            isVisible: false,
            plotOffset: 0,
            rangePadding: ChartRangePadding.none,
            majorGridLines: const MajorGridLines(width: 0),
            axisLine: const AxisLine(width: 0),
            majorTickLines: const MajorTickLines(size: 0),
          )
        : DateTimeAxis(
            isVisible: false,
            plotOffset: 0,
            rangePadding: ChartRangePadding.none,
            majorGridLines: const MajorGridLines(width: 0),
            axisLine: const AxisLine(width: 0),
            majorTickLines: const MajorTickLines(size: 0),
          );

    final CartesianSeries priceSeries = widget.indexed
        ? SplineAreaSeries<_TVPoint, num>(
            dataSource: pts,
            splineType: SplineType.monotonic,
            animationDuration: animate ? 700 : 0,
            xValueMapper: (p, i) => i,
            yValueMapper: (p, _) => p.v,
            color: c.withValues(alpha: 0.18),
            borderColor: c,
            borderWidth: 2,
            gradient: fill,
          )
        : SplineAreaSeries<_TVPoint, DateTime>(
            dataSource: pts,
            splineType: SplineType.monotonic,
            animationDuration: animate ? 700 : 0,
            xValueMapper: (p, _) => p.t,
            yValueMapper: (p, _) => p.v,
            color: c.withValues(alpha: 0.18),
            borderColor: c,
            borderWidth: 2,
            gradient: fill,
          );

    return _cachedChart = SfCartesianChart(
      backgroundColor: Colors.transparent,
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis: xAxis,
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
                x: widget.indexed ? 0 : widget.timestamps.first,
                y: widget.baseline,
              ),
            ]
          : null,
      series: <CartesianSeries>[
        priceSeries,
        if (widget.markerSeries != null) ...widget.markerSeries!(),
      ],
    );
  }

  void _onDrag(double dx, double width, int n) {
    if (width <= 0) return;
    final frac = (dx / width).clamp(0.0, 1.0);
    final int idx;
    final double hairFrac;
    if (widget.indexed) {
      // Evenly-spaced points: index ↔ x-fraction are linear.
      idx = n <= 1 ? 0 : (frac * (n - 1)).round().clamp(0, n - 1);
      hairFrac = n <= 1 ? 0 : idx / (n - 1);
    } else {
      idx = nearestIndexByTime(widget.timestamps, frac);
      // Draw the hairline + dot at the data point's own time-fraction so they
      // sit on the curve and match the value reported to the header.
      hairFrac = timeFractionOf(widget.timestamps, idx);
    }
    final prev = _scrub.value?.index;
    _scrub.update(idx, hairFrac);
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

/// The end-of-line "current value" dot with a soft halo that continuously
/// expands and fades — a live pulse. In its own RepaintBoundary so the 60 fps
/// pulse never repaints the chart underneath.
class _PulsingEndDot extends StatefulWidget {
  const _PulsingEndDot({required this.fraction, required this.dotY, required this.color});
  final double fraction;
  final double dotY;
  final Color color;
  @override
  State<_PulsingEndDot> createState() => _PulsingEndDotState();
}

class _PulsingEndDotState extends State<_PulsingEndDot> with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1600))..repeat();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (_, _) => CustomPaint(
          painter: _PulsingDotPainter(
              fraction: widget.fraction, dotY: widget.dotY, color: widget.color, t: _ctrl.value),
        ),
      ),
    );
  }
}

class _PulsingDotPainter extends CustomPainter {
  _PulsingDotPainter({required this.fraction, required this.dotY, required this.color, required this.t});
  final double fraction;
  final double dotY;
  final Color color;
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    final x = (size.width * fraction).clamp(0.0, size.width);
    final y = dotY.clamp(0.0, size.height);
    final center = Offset(x, y);
    canvas.drawCircle(center, 4 + t * 12, Paint()..color = color.withValues(alpha: (1 - t) * 0.45));
    canvas.drawCircle(center, 7, Paint()..color = color.withValues(alpha: 0.18));
    canvas.drawCircle(center, 4, Paint()..color = color);
    canvas.drawCircle(center, 4,
        Paint()..color = Colors.white.withValues(alpha: 0.9)..style = PaintingStyle.stroke..strokeWidth = 1.5);
  }

  @override
  bool shouldRepaint(_PulsingDotPainter old) =>
      old.t != t || old.fraction != fraction || old.dotY != dotY || old.color != color;
}

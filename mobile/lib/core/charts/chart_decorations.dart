import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_charts/charts.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Paints the scrubber hairline plus a dot sitting exactly on the line.
///
/// Both are drawn at the SAME [fraction] (the snapped data point's fraction),
/// so the hairline always passes through the dot and the dot sits on the curve.
class ScrubPainter extends CustomPainter {
  ScrubPainter({
    required this.fraction,
    required this.dotY,
    required this.color,
  });

  /// Horizontal position 0..1 of the hairline + dot.
  final double fraction;

  /// Pixel y of the data point on the line, or null to draw only the hairline
  /// (e.g. candlestick mode where there is no single line value).
  final double? dotY;

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final x = (size.width * fraction).clamp(0.0, size.width);

    canvas.drawLine(
      Offset(x, 0),
      Offset(x, size.height),
      Paint()
        ..color = color.withValues(alpha: 0.55)
        ..strokeWidth = 1.2,
    );

    if (dotY != null) {
      final y = dotY!.clamp(0.0, size.height);
      // soft glow
      canvas.drawCircle(
        Offset(x, y),
        7,
        Paint()..color = color.withValues(alpha: 0.18),
      );
      // solid core
      canvas.drawCircle(Offset(x, y), 4, Paint()..color = color);
      // white ring for contrast on the dark theme
      canvas.drawCircle(
        Offset(x, y),
        4,
        Paint()
          ..color = Colors.white.withValues(alpha: 0.9)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.5,
      );
    }
  }

  @override
  bool shouldRepaint(ScrubPainter old) =>
      old.fraction != fraction || old.dotY != dotY || old.color != color;
}

/// A thin row of evenly-spaced x-axis date/time labels rendered *below* the
/// plot. The custom-scrubber charts hide Syncfusion's own axes so the plot area
/// fills the widget exactly (the alignment fix); these labels replace the
/// hidden x-axis without affecting plot geometry.
class ChartDateLabels extends StatelessWidget {
  const ChartDateLabels({super.key, required this.labels});

  final List<String> labels;

  @override
  Widget build(BuildContext context) {
    if (labels.isEmpty) return const SizedBox.shrink();
    final style = AppTextStyles.nano.copyWith(color: AppColors.chartAxis);
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          for (var i = 0; i < labels.length; i++)
            Text(
              labels[i],
              style: style,
              textAlign: i == 0
                  ? TextAlign.start
                  : (i == labels.length - 1 ? TextAlign.end : TextAlign.center),
            ),
        ],
      ),
    );
  }
}

/// Formats a timestamp for an x-axis label, scaled to the selected [range]
/// (e.g. `09:31` for 1D, `Mon` for 1W, `Jun 10` for 1M/3M/YTD, `Jun '26`
/// otherwise). Shared by the dashboard and live-trading charts.
String formatChartDate(DateTime ts, String range) {
  String two(int n) => n.toString().padLeft(2, '0');
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  switch (range) {
    case '1D':
      return '${two(ts.hour)}:${two(ts.minute)}';
    case '1W':
      const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      return days[(ts.weekday - 1).clamp(0, 6)];
    case '1M':
    case '3M':
    case 'YTD':
      return '${months[ts.month - 1]} ${ts.day}';
    default:
      return "${months[ts.month - 1]} '${ts.year.toString().substring(2)}";
  }
}

/// Picks up to [slots] evenly-spaced data indices for labelling (always
/// includes first and last). Returns fewer when there are fewer points.
List<int> evenlySpacedLabelIndices(int count, int slots) {
  if (count <= 0) return const [];
  if (count == 1) return const [0];
  final n = slots.clamp(2, count);
  return [
    for (var s = 0; s < n; s++) ((s / (n - 1)) * (count - 1)).round(),
  ];
}

/// A Y value-axis configured to be invisible and inset-free, with explicit
/// [minimum]/[maximum] so the vertical mapping is deterministic (lets the dot
/// land exactly on the line). Used by every equity/value chart.
NumericAxis hiddenValueAxis({double? minimum, double? maximum}) {
  return NumericAxis(
    isVisible: false,
    minimum: minimum,
    maximum: maximum,
    rangePadding: ChartRangePadding.none,
    majorGridLines: const MajorGridLines(width: 0),
    minorGridLines: const MinorGridLines(width: 0),
    axisLine: const AxisLine(width: 0),
    majorTickLines: const MajorTickLines(size: 0),
  );
}

/// An index-based X axis that maps data edge-to-edge (no plot offset, no range
/// padding) and draws no labels/lines — used by the custom-scrubber charts that
/// render their own [ChartDateLabels] below the plot.
NumericAxis edgeToEdgeIndexAxis(int count) {
  return NumericAxis(
    isVisible: false,
    minimum: 0,
    maximum: (count <= 1 ? 1 : count - 1).toDouble(),
    plotOffset: 0,
    rangePadding: ChartRangePadding.none,
    majorGridLines: const MajorGridLines(width: 0),
    minorGridLines: const MinorGridLines(width: 0),
    axisLine: const AxisLine(width: 0),
    majorTickLines: const MajorTickLines(size: 0),
  );
}

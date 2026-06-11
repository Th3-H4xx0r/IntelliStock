/// Pure pixel/value geometry helpers shared by the app's scrubbable charts.
///
/// These exist so the scrubber hairline, the dot on the line, and the value the
/// header reports all use ONE consistent mapping. Getting these wrong is what
/// made the old scrubber report a value shifted right of the curve.
library;

/// Maps a horizontal fraction in `[0, 1]` to the nearest data index for an
/// evenly-spaced series of [count] points. Clamped and rounded.
int fractionToIndex(double fraction, int count) {
  if (count <= 1) return 0;
  final f = fraction.clamp(0.0, 1.0);
  return (f * (count - 1)).round().clamp(0, count - 1);
}

/// The x-fraction in `[0, 1]` of data point [index] in an evenly-spaced series
/// of [count] points. Inverse of [fractionToIndex] at the data points.
double indexToFraction(int index, int count) {
  if (count <= 1) return 0.0;
  final i = index.clamp(0, count - 1);
  return i / (count - 1);
}

/// Min/max of [values] expanded by [padFraction] of the span on each side so
/// the line never touches the top/bottom edge. Degenerate inputs still return a
/// range with positive height so downstream divisions stay finite.
({double min, double max}) paddedBounds(
  Iterable<double> values, {
  double padFraction = 0.06,
}) {
  var lo = double.infinity;
  var hi = double.negativeInfinity;
  for (final v in values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (lo == double.infinity) return (min: 0.0, max: 1.0); // empty
  if (lo == hi) {
    final pad = lo.abs() < 1 ? 1.0 : lo.abs() * 0.01;
    return (min: lo - pad, max: hi + pad);
  }
  final pad = (hi - lo) * padFraction;
  return (min: lo - pad, max: hi + pad);
}

/// Pixel y (0 = top, [height] = bottom) for [value] within `[min, max]` over a
/// plot of [height] logical pixels. Clamps values outside the range.
double valueToY(double value, double min, double max, double height) {
  if (max <= min) return height / 2;
  final t = ((value - min) / (max - min)).clamp(0.0, 1.0);
  return height * (1 - t);
}

// ── Time-based mapping ────────────────────────────────────────────────────────
//
// For charts plotted against real time (a DateTime axis spanning
// first..last), the horizontal position of a point is proportional to its
// timestamp, not its index — so unevenly-spaced samples and trade markers stay
// aligned. These mirror [fractionToIndex] / [indexToFraction] for that case.

/// Nearest data index to a horizontal [fraction] (0..1) on a time-based plot
/// spanning `timestamps.first..timestamps.last`.
int nearestIndexByTime(List<DateTime> timestamps, double fraction) {
  if (timestamps.length <= 1) return 0;
  final first = timestamps.first.millisecondsSinceEpoch;
  final last = timestamps.last.millisecondsSinceEpoch;
  if (last <= first) return 0;
  final f = fraction.clamp(0.0, 1.0);
  final target = first + f * (last - first);
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

/// The horizontal fraction (0..1) of data point [index] on a time-based plot.
/// Inverse of [nearestIndexByTime] at the data points.
double timeFractionOf(List<DateTime> timestamps, int index) {
  if (timestamps.length <= 1) return 0.0;
  final i = index.clamp(0, timestamps.length - 1);
  final first = timestamps.first.millisecondsSinceEpoch;
  final last = timestamps.last.millisecondsSinceEpoch;
  if (last <= first) return 0.0;
  return ((timestamps[i].millisecondsSinceEpoch - first) / (last - first))
      .clamp(0.0, 1.0);
}

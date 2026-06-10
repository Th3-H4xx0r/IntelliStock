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

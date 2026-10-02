import Foundation

// Pure pixel/value geometry shared by the scrubbable charts, ported from
// `core/charts/chart_geometry.dart`. The scrubber hairline, the dot on the
// line and the value the header reports all use ONE mapping.

/// The nearest data index for a horizontal fraction in `[0, 1]` over an
/// evenly spaced series of `count` points. Clamped and rounded.
nonisolated func fractionToIndex(_ fraction: Double, _ count: Int) -> Int {
    if count <= 1 { return 0 }
    let f = min(max(fraction, 0), 1)
    return min(max(Int((f * Double(count - 1)).rounded()), 0), count - 1)
}

/// The x-fraction of data point `index` in an evenly spaced series. Inverse
/// of `fractionToIndex` at the data points.
nonisolated func indexToFraction(_ index: Int, _ count: Int) -> Double {
    if count <= 1 { return 0 }
    let i = min(max(index, 0), count - 1)
    return Double(i) / Double(count - 1)
}

/// A value range with positive height.
nonisolated struct ChartBounds: Equatable, Sendable {
    let min: Double
    let max: Double
}

/// Min/max of `values` widened by `padFraction` of the span on each side, so
/// the line never touches the edges. Degenerate inputs still get a range with
/// positive height.
nonisolated func paddedBounds(_ values: some Sequence<Double>, padFraction: Double = 0.06) -> ChartBounds {
    var lo = Double.infinity
    var hi = -Double.infinity
    for v in values {
        if v < lo { lo = v }
        if v > hi { hi = v }
    }
    if lo == .infinity { return ChartBounds(min: 0, max: 1) }
    if lo == hi {
        let pad = abs(lo) < 1 ? 1.0 : abs(lo) * 0.01
        return ChartBounds(min: lo - pad, max: hi + pad)
    }
    let pad = (hi - lo) * padFraction
    return ChartBounds(min: lo - pad, max: hi + pad)
}

/// Pixel y (0 = top, `height` = bottom) for `value` within `[min, max]`.
/// Clamps values outside the range.
nonisolated func valueToY(_ value: Double, _ min: Double, _ max: Double, _ height: Double) -> Double {
    if max <= min { return height / 2 }
    let t = Swift.min(Swift.max((value - min) / (max - min), 0), 1)
    return height * (1 - t)
}

// MARK: Time-based mapping
//
// For charts plotted against real time, a point's horizontal position follows
// its timestamp, not its index, so unevenly spaced samples and trade markers
// stay aligned.

/// The nearest data index to a horizontal `fraction` on a time-based plot
/// spanning `timestamps.first...timestamps.last`.
nonisolated func nearestIndexByTime(_ timestamps: [Date], _ fraction: Double) -> Int {
    if timestamps.count <= 1 { return 0 }
    let first = timestamps[0].dartMillis
    let last = timestamps[timestamps.count - 1].dartMillis
    if last <= first { return 0 }
    let f = min(max(fraction, 0), 1)
    let target = first + f * (last - first)
    var lo = 0
    var hi = timestamps.count - 1
    while lo < hi {
        let mid = (lo + hi) / 2
        if timestamps[mid].dartMillis < target {
            lo = mid + 1
        } else {
            hi = mid
        }
    }
    if lo <= 0 { return 0 }
    let prev = lo - 1
    return abs(timestamps[lo].dartMillis - target) < abs(timestamps[prev].dartMillis - target) ? lo : prev
}

/// The horizontal fraction of data point `index` on a time-based plot.
/// Inverse of `nearestIndexByTime` at the data points.
nonisolated func timeFractionOf(_ timestamps: [Date], _ index: Int) -> Double {
    if timestamps.count <= 1 { return 0 }
    let i = min(max(index, 0), timestamps.count - 1)
    let first = timestamps[0].dartMillis
    let last = timestamps[timestamps.count - 1].dartMillis
    if last <= first { return 0 }
    return min(max((timestamps[i].dartMillis - first) / (last - first), 0), 1)
}

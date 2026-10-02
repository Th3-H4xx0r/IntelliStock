import Foundation

nonisolated extension Int {
    /// Dart's `double.toInt()`: truncates toward zero and saturates at the
    /// 64-bit limits (the Dart VM clamps rather than overflowing). Nil for
    /// NaN and infinity, where Dart threw. Never traps, unlike `Int(_:)`.
    init?(dartTruncating d: Double) {
        guard d.isFinite else { return nil }
        let t = d.rounded(.towardZero)
        if t >= 9_223_372_036_854_775_807.0 {
            self = .max
        } else if t <= -9_223_372_036_854_775_808.0 {
            self = .min
        } else {
            self = Int(t)
        }
    }
}

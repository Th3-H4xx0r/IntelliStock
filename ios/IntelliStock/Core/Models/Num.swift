import Foundation

/// Dart's `num`: an int or a double, kept apart so `toString()` prints what
/// the Flutter app printed (`5`, not `5.0`; `5.0` stays `5.0`).
///
/// The Dart models typed many backend numbers as `num?` and handed them to
/// formatters or string interpolation unconverted. Those fields are `Num?`
/// here. Use `.double` where Dart called `toDouble()` and `.int` where it
/// called `toInt()`.
///
/// Equality and ordering follow Dart: `5 == 5.0` is true.
nonisolated enum Num: Sendable, CustomStringConvertible {
    case int(Int)
    case double(Double)

    /// Dart `x as num?` on a decoded JSON value; nil for anything that is not
    /// a number.
    init?(json: JSON) {
        switch json {
        case .int(let i): self = .int(i)
        case .double(let d): self = .double(d)
        default: return nil
        }
    }

    /// Dart `num.tryParse(s)`: `int.tryParse(s) ?? double.tryParse(s)`.
    static func tryParse(_ s: String?) -> Num? {
        if let i = JSON.parseInt(s) { return .int(i) }
        if let d = JSON.parseDouble(s) { return .double(d) }
        return nil
    }

    /// Dart `toDouble()`.
    var double: Double {
        switch self {
        case .int(let i): Double(i)
        case .double(let d): d
        }
    }

    /// Dart `toInt()`: truncates toward zero, saturating beyond the 64-bit
    /// range as the Dart VM does. Non-finite doubles read as 0 (Dart threw).
    var int: Int {
        switch self {
        case .int(let i): i
        case .double(let d): Int(dartTruncating: d) ?? 0
        }
    }

    /// Dart `toString()`.
    var description: String {
        switch self {
        case .int(let i): String(i)
        case .double(let d): JSON.dartDoubleString(d)
        }
    }

    /// The JSON form, for request bodies and round trips.
    var json: JSON {
        switch self {
        case .int(let i): .int(i)
        case .double(let d): .double(d)
        }
    }
}

nonisolated extension Num: Hashable, Comparable {
    static func == (lhs: Num, rhs: Num) -> Bool {
        switch (lhs, rhs) {
        case (.int(let a), .int(let b)): a == b
        default: lhs.double == rhs.double
        }
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(double)
    }

    static func < (lhs: Num, rhs: Num) -> Bool {
        switch (lhs, rhs) {
        case (.int(let a), .int(let b)): a < b
        default: lhs.double < rhs.double
        }
    }
}

nonisolated extension Num: ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral {
    init(integerLiteral value: Int) { self = .int(value) }
    init(floatLiteral value: Double) { self = .double(value) }
}

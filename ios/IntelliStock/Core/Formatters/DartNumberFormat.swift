import Foundation

/// Number-to-text conversions that reproduce Dart's output digit for digit.
///
/// `printf`/`NumberFormatter` round exact binary ties to even (`0.125` →
/// `0.12`); Dart's `toStringAsFixed` and intl's `NumberFormat` round them up
/// (`0.13`). The formatters go through these helpers so every figure on screen
/// matches the Flutter app.
nonisolated enum DartNumberFormat {
    /// intl `NumberFormat('#,##0.<digits>', 'en_US').format(v)`: sign prefix,
    /// `floor` integer part with 3-digit grouping, and the fraction rounded
    /// half away from zero on `fraction × 10^digits` (intl's `_formatFixed`).
    static func grouped(_ v: Double, fractionDigits: Int) -> String {
        if v.isNaN { return "NaN" }
        let sign = v.sign == .minus ? "-" : ""
        if v.isInfinite { return sign + "∞" }

        let n = abs(v)
        var integerPart = n.rounded(.down)
        let power = pow(10, Double(fractionDigits))
        var remaining = ((n - integerPart) * power).rounded()
        if remaining >= power {
            integerPart += 1
            remaining -= power
        }

        let digits = integerDigits(integerPart)
        var out = sign + group(digits)
        if fractionDigits > 0 {
            let fraction = String(Int(remaining + power)).dropFirst()
            out += "." + fraction
        }
        return out
    }

    /// Dart `double.toStringAsFixed(digits)`: the exact value rounded half up
    /// (away from zero) at `digits` places; exponential `toString()` at or
    /// above 1e21.
    static func toStringAsFixed(_ v: Double, _ digits: Int) -> String {
        if v.isNaN { return "NaN" }
        if v.isInfinite { return v < 0 ? "-Infinity" : "Infinity" }
        if abs(v) >= 1e21 { return JSON.dartDoubleString(v) }

        let sign = v.sign == .minus ? "-" : ""
        // Apple's printf prints the exact binary expansion; 80 places covers
        // every double in range, so the tie decision below is exact.
        let exact = String(format: "%.80f", abs(v))
        let parts = exact.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false)
        var whole = Array(parts[0].utf8).map { Int($0) - 48 }
        let fractionAll = parts.count > 1 ? Array(parts[1].utf8).map { Int($0) - 48 } : []
        var fraction = Array(fractionAll.prefix(digits))
        while fraction.count < digits { fraction.append(0) }

        let next = fractionAll.count > digits ? fractionAll[digits] : 0
        if next >= 5 {
            // Carry through the fraction, then the whole part.
            var i = fraction.count - 1
            var carry = 1
            while carry == 1, i >= 0 {
                fraction[i] += 1
                if fraction[i] == 10 { fraction[i] = 0; carry = 1 } else { carry = 0 }
                i -= 1
            }
            var j = whole.count - 1
            while carry == 1, j >= 0 {
                whole[j] += 1
                if whole[j] == 10 { whole[j] = 0; carry = 1 } else { carry = 0 }
                j -= 1
            }
            if carry == 1 { whole.insert(1, at: 0) }
        }

        let wholeText = whole.map(String.init).joined()
        if digits == 0 { return sign + wholeText }
        return sign + wholeText + "." + fraction.map(String.init).joined()
    }

    /// Dart `num.toString()` for a double (`5.0`, `1.5e-7`).
    static func numToString(_ v: Double) -> String {
        JSON.dartDoubleString(v)
    }

    // MARK: Helpers

    private static func integerDigits(_ value: Double) -> String {
        if value < 9.2e18 { return String(Int(value)) }
        return String(format: "%.0f", value)
    }

    private static func group(_ digits: String) -> String {
        let chars = Array(digits)
        var out = ""
        for (i, c) in chars.enumerated() {
            out.append(c)
            let remaining = chars.count - i - 1
            if remaining > 0, remaining % 3 == 0 { out.append(",") }
        }
        return out
    }
}

/// Dart's `%` on integers: the Euclidean remainder (never negative for a
/// positive divisor).
nonisolated func dartMod(_ a: Int, _ b: Int) -> Int {
    let r = a % b
    return r < 0 ? r + abs(b) : r
}

/// Dart's `%` on doubles (Euclidean, like the integer form).
nonisolated func dartMod(_ a: Double, _ b: Double) -> Double {
    let r = a.truncatingRemainder(dividingBy: b)
    return r < 0 ? r + abs(b) : r
}

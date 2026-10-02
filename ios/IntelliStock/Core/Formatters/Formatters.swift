import Foundation
import SwiftUI

// Number and date formatters ported from `core/formatters/formatters.dart`,
// with the same names and byte-identical output.
//
// Dart's `num` covers int and double, and `num.toString()` prints them
// differently (`950` vs `950.0`). Each numeric formatter therefore has an
// `Int?` and a `Double?` overload; the `Double?` one is disfavoured so an
// integer literal or `nil` resolves to the `Int?` form, exactly as Dart would
// see an int.

nonisolated private let dash = "—"

// MARK: Money and P&L

/// `$1,234.56`, negatives `-$1,234.56`, nil → `—`.
@_disfavoredOverload
nonisolated func fmtMoney(_ v: Double?) -> String {
    guard let v else { return dash }
    let neg = v < 0
    return "\(neg ? "-" : "")$\(DartNumberFormat.grouped(abs(v), fractionDigits: 2))"
}

nonisolated func fmtMoney(_ v: Int?) -> String { fmtMoney(v.map(Double.init)) }

/// `+$1,234.56` / `-$1,234.56` (sign before `$`), nil → `—`.
@_disfavoredOverload
nonisolated func fmtPnl(_ v: Double?) -> String {
    guard let v else { return dash }
    let sign = v < 0 ? "-" : "+"
    return "\(sign)$\(DartNumberFormat.grouped(abs(v), fractionDigits: 2))"
}

nonisolated func fmtPnl(_ v: Int?) -> String { fmtPnl(v.map(Double.init)) }

/// `+12.34%` / `-5.00%` (2 dp, leading + for ≥ 0), nil or NaN → `—`.
@_disfavoredOverload
nonisolated func fmtPct(_ v: Double?) -> String {
    guard let v, !v.isNaN else { return dash }
    let sign = v >= 0 ? "+" : "-"
    return "\(sign)\(DartNumberFormat.toStringAsFixed(abs(v), 2))%"
}

nonisolated func fmtPct(_ v: Int?) -> String { fmtPct(v.map(Double.init)) }

/// LLM cost: `$0.00`; under $1 (non-zero) uses 4 dp, `$0.0004`.
@_disfavoredOverload
nonisolated func fmtUsdCost(_ v: Double?) -> String {
    guard let v else { return dash }
    if v != 0, abs(v) < 1 { return "$\(DartNumberFormat.grouped(v, fractionDigits: 4))" }
    return "$\(DartNumberFormat.grouped(v, fractionDigits: 2))"
}

nonisolated func fmtUsdCost(_ v: Int?) -> String { fmtUsdCost(v.map(Double.init)) }

// MARK: Counts and durations

/// Token counts: `1.2M` / `3.4k` / raw (`num.toString()`).
@_disfavoredOverload
nonisolated func fmtTokens(_ v: Double?) -> String {
    guard let v else { return dash }
    if v >= 1_000_000 { return "\(DartNumberFormat.toStringAsFixed(v / 1_000_000, 1))M" }
    if v >= 1000 { return "\(DartNumberFormat.toStringAsFixed(v / 1000, 1))k" }
    return DartNumberFormat.numToString(v)
}

nonisolated func fmtTokens(_ v: Int?) -> String {
    guard let v else { return dash }
    if v >= 1000 { return fmtTokens(Double(v)) }
    return String(v)
}

/// Short duration: `1.5s` / `3m 20s` / `2h 5m` / `1d 2h`.
@_disfavoredOverload
nonisolated func fmtDuration(_ seconds: Double?) -> String {
    guard let s = seconds, s.isFinite else { return dash }
    if s < 60 {
        let whole = dartMod(s, 1) == 0 ? Int(dartTruncating: s) : nil
        let text = whole.map(String.init) ?? DartNumberFormat.toStringAsFixed(s, 1)
        return "\(text)s"
    }
    if s < 3600 {
        let m = Int((s / 60).rounded(.down))
        let rem = Int(dartMod(s, 60).rounded())
        return "\(m)m \(rem)s"
    }
    if s < 86400 {
        let h = Int((s / 3600).rounded(.down))
        let m = Int((dartMod(s, 3600) / 60).rounded(.down))
        return "\(h)h \(m)m"
    }
    let d = Int(dartTruncating: (s / 86400).rounded(.down)) ?? 0
    let h = Int((dartMod(s, 86400) / 3600).rounded(.down))
    return "\(d)d \(h)h"
}

nonisolated func fmtDuration(_ seconds: Int?) -> String { fmtDuration(seconds.map(Double.init)) }

/// Elapsed: `Xd Xh Xm` / `Xh Xm Xs` / `Xm Xs` / `Xs`.
@_disfavoredOverload
nonisolated func fmtElapsed(_ seconds: Double?) -> String {
    guard let seconds, let whole = Int(dartTruncating: seconds.rounded(.down)) else { return dash }
    return fmtElapsed(whole)
}

nonisolated func fmtElapsed(_ seconds: Int?) -> String {
    guard let total = seconds else { return dash }
    let d = total / 86400
    let h = dartMod(total, 86400) / 3600
    let m = dartMod(total, 3600) / 60
    let s = dartMod(total, 60)
    if d > 0 { return "\(d)d \(h)h \(m)m" }
    if h > 0 { return "\(h)h \(m)m \(s)s" }
    if m > 0 { return "\(m)m \(s)s" }
    return "\(s)s"
}

// MARK: Dates

/// A value `parseDateTime` accepts — Dart's `dynamic`: a `Date`, epoch
/// seconds or milliseconds (`Int`/`Double`), a numeric or ISO `String`, or a
/// `JSON` holding one of those.
nonisolated protocol DartDateInput {
    var dartDate: Date? { get }
}

extension Date: DartDateInput {
    nonisolated var dartDate: Date? { self }
}

extension Int: DartDateInput {
    nonisolated var dartDate: Date? { epochDate(Double(self), isInt: true) }
}

extension Double: DartDateInput {
    nonisolated var dartDate: Date? { epochDate(self, isInt: false) }
}

extension String: DartDateInput {
    nonisolated var dartDate: Date? {
        // `num.tryParse(v)` first: int.tryParse ?? double.tryParse.
        if let i = JSON.parseInt(self) { return i.dartDate }
        if let d = JSON.parseDouble(self) { return d.dartDate }
        return DartDateTime.tryParse(self)
    }
}

extension JSON: DartDateInput {
    nonisolated var dartDate: Date? {
        switch self {
        case .int(let i): i.dartDate
        case .double(let d): d.dartDate
        case .string(let s): s.dartDate
        default: nil
        }
    }
}

/// Dart: `> 1e12` is milliseconds, else seconds; `toInt()` truncates. Values
/// Dart would throw on (NaN, out of DateTime range) give nil.
nonisolated private func epochDate(_ v: Double, isInt: Bool) -> Date? {
    guard v.isFinite else { return nil }
    let ms = v > 1_000_000_000_000 ? v.rounded(.towardZero) : (v * 1000).rounded(.towardZero)
    guard abs(ms) <= 8_640_000_000_000_000 else { return nil }
    return Date(timeIntervalSince1970: ms / 1000)
}

/// Parses epoch seconds / ms / ISO into a `Date` (local).
nonisolated func parseDateTime(_ v: (any DartDateInput)?) -> Date? {
    v?.dartDate
}

nonisolated private enum DartDateFormats {
    static let medium = make("MMM d, yyyy")
    static let shortTime = make("h:mm a")

    static func make(_ pattern: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .autoupdatingCurrent
        f.dateFormat = pattern
        return f
    }
}

/// Medium date + short time, e.g. `Jun 10, 2026, 2:14 PM`.
nonisolated func fmtDateTime(_ v: (any DartDateInput)?) -> String {
    guard let dt = parseDateTime(v) else { return dash }
    return "\(DartDateFormats.medium.string(from: dt)), \(DartDateFormats.shortTime.string(from: dt))"
}

/// `Jun 10, 2026`.
nonisolated func fmtDate(_ v: (any DartDateInput)?) -> String {
    guard let dt = parseDateTime(v) else { return dash }
    return DartDateFormats.medium.string(from: dt)
}

/// Relative time: `Just now` / `5m ago` / `2h ago` / `3d ago`. Pass `now` to
/// compute against a fixed clock.
nonisolated func fmtRelative(_ v: (any DartDateInput)?, now: Date? = nil) -> String {
    guard let dt = parseDateTime(v) else { return dash }
    let seconds = (now ?? Date()).timeIntervalSince(dt)
    // Dart's Duration.inX truncate toward zero.
    if (seconds).rounded(.towardZero) < 60 { return "Just now" }
    let minutes = Int((seconds / 60).rounded(.towardZero))
    if minutes < 60 { return "\(minutes)m ago" }
    let hours = Int((seconds / 3600).rounded(.towardZero))
    if hours < 24 { return "\(hours)h ago" }
    return "\(Int((seconds / 86400).rounded(.towardZero)))d ago"
}

// MARK: Colours

/// Colour for a P&L value: success when ≥ 0, danger otherwise.
@_disfavoredOverload
nonisolated func pnlColor(_ v: Double?) -> Color {
    (v ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger
}

nonisolated func pnlColor(_ v: Int?) -> Color { pnlColor(v.map(Double.init)) }

// MARK: Dart `num` (`Num?`)
//
// Models typed `num?` in Dart keep the int/double distinction as `Num?`;
// these route an int to the `Int?` form and a double to the `Double?` form,
// so a token count prints `950`, never `950.0`. Disfavoured, so `nil` and
// integer literals still resolve to `Int?`.

@_disfavoredOverload
nonisolated func fmtMoney(_ v: Num?) -> String { fmtMoney(v?.double) }

@_disfavoredOverload
nonisolated func fmtPnl(_ v: Num?) -> String { fmtPnl(v?.double) }

@_disfavoredOverload
nonisolated func fmtPct(_ v: Num?) -> String { fmtPct(v?.double) }

@_disfavoredOverload
nonisolated func fmtUsdCost(_ v: Num?) -> String { fmtUsdCost(v?.double) }

@_disfavoredOverload
nonisolated func fmtTokens(_ v: Num?) -> String {
    switch v {
    case .none: fmtTokens(Int?.none)
    case .int(let i): fmtTokens(i)
    case .double(let d): fmtTokens(d)
    }
}

@_disfavoredOverload
nonisolated func fmtDuration(_ seconds: Num?) -> String {
    switch seconds {
    case .none: fmtDuration(Int?.none)
    case .int(let i): fmtDuration(i)
    case .double(let d): fmtDuration(d)
    }
}

@_disfavoredOverload
nonisolated func fmtElapsed(_ seconds: Num?) -> String {
    switch seconds {
    case .none: fmtElapsed(Int?.none)
    case .int(let i): fmtElapsed(i)
    case .double(let d): fmtElapsed(d)
    }
}

@_disfavoredOverload
nonisolated func pnlColor(_ v: Num?) -> Color { pnlColor(v?.double) }

extension Num: DartDateInput {
    nonisolated var dartDate: Date? {
        switch self {
        case .int(let i): i.dartDate
        case .double(let d): d.dartDate
        }
    }
}

import Foundation

// OCC option-symbol helpers, ported from core/models/option_symbol.dart.
//
// Live-state positions carry `asset_class` (spec 2026-09-24 section 6.1), so
// these are a display fallback for rows that do not: recent trades and the
// dashboard's /brokerages/{id}/positions holdings. The engine identifies
// contracts by Alpaca's contract fields, never by this shape (spec fix 10).

/// Shares per US equity option contract.
nonisolated let kOptionMultiplier = 100

nonisolated struct OccContract: Hashable, Sendable {
    let underlying: String
    /// YYYY-MM-DD.
    let expiry: String
    /// "put" | "call".
    let optionType: String
    let strike: Double
}

/// Root (1-6), YYMMDD, C|P, strike x 1000 in 8 digits.
nonisolated func parseOccSymbol(_ symbol: String?) -> OccContract? {
    // Dart: (symbol ?? '').trim().toUpperCase(), then the anchored regex
    // ^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$
    let text = Array((symbol ?? "").trimmingCharacters(in: .whitespacesAndNewlines).uppercased().utf8)
    // The tail is fixed width: 6 date digits + C|P + 8 strike digits = 15.
    let tail = 15
    guard text.count > tail, text.count - tail <= 6 else { return nil }
    let rootEnd = text.count - tail
    let root = text[0..<rootEnd]
    func isUpper(_ b: UInt8) -> Bool { b >= UInt8(ascii: "A") && b <= UInt8(ascii: "Z") }
    func isDigit(_ b: UInt8) -> Bool { b >= UInt8(ascii: "0") && b <= UInt8(ascii: "9") }
    guard let first = root.first, isUpper(first), root.dropFirst().allSatisfy({ isUpper($0) || isDigit($0) }) else {
        return nil
    }
    let date = text[rootEnd..<(rootEnd + 6)]
    let type = text[rootEnd + 6]
    let strikeDigits = text[(rootEnd + 7)...]
    guard date.allSatisfy(isDigit), type == UInt8(ascii: "C") || type == UInt8(ascii: "P"),
          strikeDigits.allSatisfy(isDigit),
          let strikeMilli = Int(String(decoding: strikeDigits, as: UTF8.self))
    else { return nil }
    let d = String(decoding: date, as: UTF8.self)
    let yy = d.prefix(2)
    let mm = d.dropFirst(2).prefix(2)
    let dd = d.suffix(2)
    return OccContract(
        underlying: String(decoding: root, as: UTF8.self),
        expiry: "20\(yy)-\(mm)-\(dd)",
        optionType: type == UInt8(ascii: "P") ? "put" : "call",
        strike: Double(strikeMilli) / 1000
    )
}

nonisolated func isOccOptionSymbol(_ symbol: String?) -> Bool { parseOccSymbol(symbol) != nil }

/// "APH $130 Put · 2026-10-02". Explicit fields win; the symbol fills gaps.
nonisolated func describeOptionContract(
    symbol: String,
    underlying: String? = nil,
    strike: Double? = nil,
    optionType: String? = nil,
    expiry: String? = nil
) -> String {
    let occ = parseOccSymbol(symbol)
    let u = (underlying?.isEmpty == false) ? underlying! : (occ?.underlying ?? "")
    let k = strike ?? occ?.strike
    let t = (optionType?.isEmpty == false) ? optionType! : (occ?.optionType ?? "")
    let e = (expiry?.isEmpty == false) ? expiry! : (occ?.expiry ?? "")
    var parts: [String] = []
    if !u.isEmpty { parts.append(u) }
    if let k {
        parts.append(k == k.rounded() ? "$" + dartToStringAsFixed(k, 0) : "$" + dartToStringAsFixed(k, 2))
    }
    if !t.isEmpty { parts.append(t.lowercased() == "put" ? "Put" : "Call") }
    return e.isEmpty ? parts.joined(separator: " ") : "\(parts.joined(separator: " ")) · \(e)"
}

/// Dart `double.toStringAsFixed(digits)`.
///
/// Dart rounds the exact binary value half away from zero (`2.5` → `3`,
/// `0.125` → `0.13`, `1.005` → `1.00`); `String(format:)` rounds exact ties
/// to even. This reads the exact expansion and rounds it the Dart way.
nonisolated func dartToStringAsFixed(_ value: Double, _ digits: Int) -> String {
    guard value.isFinite else { return JSON.dartDoubleString(value) }
    let digits = max(0, digits)
    // Every double ≥ 1e-6 has an exact decimal expansion within 80 extra
    // places, so `rest` below decides the rounding exactly.
    let exact = String(format: "%.\(digits + 80)f", abs(value))
    let dot = exact.firstIndex(of: ".")!
    let whole = exact[..<dot]
    let fraction = exact[exact.index(after: dot)...]
    var kept = Array((whole + fraction.prefix(digits)).utf8)
    let roundUp = fraction.dropFirst(digits).first.map { $0 >= "5" } ?? false
    if roundUp {
        var i = kept.count - 1
        while i >= 0 {
            if kept[i] == UInt8(ascii: "9") {
                kept[i] = UInt8(ascii: "0")
                i -= 1
            } else {
                kept[i] += 1
                break
            }
        }
        if i < 0 { kept.insert(UInt8(ascii: "1"), at: 0) }
    }
    var text = String(decoding: kept, as: UTF8.self)
    if digits > 0 {
        text.insert(".", at: text.index(text.endIndex, offsetBy: -digits))
    }
    // Dart keeps the sign of a negative value that rounds to zero ("-0.00").
    return value.sign == .minus ? "-" + text : text
}

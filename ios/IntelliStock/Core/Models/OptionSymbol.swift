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

/// Dart `double.toStringAsFixed(digits)` — forwards to
/// `DartNumberFormat.toStringAsFixed`, the single implementation (which also
/// switches to exponential form at 1e21, as Dart does).
nonisolated func dartToStringAsFixed(_ value: Double, _ digits: Int) -> String {
    DartNumberFormat.toStringAsFixed(value, digits)
}

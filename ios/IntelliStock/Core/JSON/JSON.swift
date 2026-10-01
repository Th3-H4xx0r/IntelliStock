import Foundation

/// A decoded JSON value, read the way the Flutter app read `Map<String, dynamic>`.
///
/// The Dart models never used strict decoding: they wrote `x?.toString()`,
/// `(x as num?)?.toDouble()`, `x == true` and `?? fallback`. Every model in
/// the native app has an `init(json:)` that mirrors its Dart `fromJson` line for
/// line through these accessors, so a backend quirk the Flutter app tolerated
/// is tolerated here too.
///
/// Integers and doubles stay distinct, as they do in Dart's `jsonDecode`, so
/// `5` prints as `"5"` and `5.0` prints as `"5.0"`.
nonisolated enum JSON: Hashable, Sendable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSON])
    case object([String: JSON])
}

// MARK: - Decoding and encoding

nonisolated extension JSON {
    init(data: Data) throws {
        let any = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        self.init(any: any)
    }

    /// Bridges a Foundation / Swift value (as JSONSerialization produces it).
    init(any value: Any?) {
        guard let value, !(value is NSNull) else {
            self = .null
            return
        }
        switch value {
        case let json as JSON:
            self = json
        case let string as String:
            self = .string(string)
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                self = .bool(number.boolValue)
            } else {
                let type = String(cString: number.objCType)
                self = (type == "d" || type == "f") ? .double(number.doubleValue) : .int(number.intValue)
            }
        case let array as [Any]:
            self = .array(array.map { JSON(any: $0) })
        case let object as [String: Any]:
            self = .object(object.mapValues { JSON(any: $0) })
        default:
            self = .string(String(describing: value))
        }
    }

    func data() throws -> Data {
        try JSONSerialization.data(withJSONObject: anyValue, options: [.fragmentsAllowed])
    }

    /// The Foundation form JSONSerialization accepts.
    var anyValue: Any {
        switch self {
        case .null: NSNull()
        case .bool(let b): b
        case .int(let i): i
        case .double(let d): d
        case .string(let s): s
        case .array(let a): a.map(\.anyValue)
        case .object(let o): o.mapValues(\.anyValue)
        }
    }
}

// MARK: - Dart-style accessors

nonisolated extension JSON {
    /// `map[key]`; `.null` when missing or when this is not an object.
    subscript(key: String) -> JSON {
        if case .object(let o) = self { return o[key] ?? .null }
        return .null
    }

    /// `list[index]`; `.null` when out of range or when this is not an array.
    subscript(index: Int) -> JSON {
        if case .array(let a) = self, a.indices.contains(index) { return a[index] }
        return .null
    }

    var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    /// Dart `x?.toString()`.
    var string: String? {
        switch self {
        case .null: nil
        default: dartDescription
        }
    }

    /// Dart `(x as num?)?.toDouble()`.
    var double: Double? {
        switch self {
        case .int(let i): Swift.Double(i)
        case .double(let d): d
        default: nil
        }
    }

    /// Dart `(x as num?)?.toInt()` — truncates a double toward zero.
    var int: Int? {
        switch self {
        case .int(let i): i
        case .double(let d) where d.isFinite: Int(d)
        default: nil
        }
    }

    /// Dart `x == true`.
    var bool: Bool { self == .bool(true) }

    /// Dart `x as bool?` — nil unless the value is a JSON boolean.
    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    var array: [JSON]? {
        if case .array(let a) = self { return a }
        return nil
    }

    var object: [String: JSON]? {
        if case .object(let o) = self { return o }
        return nil
    }

    /// Dart `(x as List?) ?? const []`.
    var arrayValue: [JSON] { array ?? [] }

    /// Dart `(x as Map?) ?? const {}`.
    var objectValue: [String: JSON] { object ?? [:] }

    /// Dart `(x ?? fallback).toString()`.
    func stringOr(_ fallback: String) -> String { string ?? fallback }

    /// Dart `(x as num?)?.toDouble() ?? fallback`.
    func doubleOr(_ fallback: Swift.Double) -> Swift.Double { double ?? fallback }

    /// Dart `(x as num?)?.toInt() ?? fallback`.
    func intOr(_ fallback: Int) -> Int { int ?? fallback }

    /// Dart `int.tryParse(s)`: trims whitespace, accepts a sign and a `0x` prefix.
    static func parseInt(_ s: String?) -> Int? {
        guard var t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        var negative = false
        if t.hasPrefix("-") || t.hasPrefix("+") {
            negative = t.hasPrefix("-")
            t.removeFirst()
        }
        let value: Int?
        if t.lowercased().hasPrefix("0x") {
            value = Int(t.dropFirst(2), radix: 16)
        } else {
            value = t.allSatisfy(\.isASCIIDigit) ? Int(t) : nil
        }
        guard let value else { return nil }
        return negative ? -value : value
    }

    /// Dart `double.tryParse(s)`: trims whitespace.
    static func parseDouble(_ s: String?) -> Swift.Double? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        switch t {
        case "NaN": return .nan
        case "Infinity": return .infinity
        case "-Infinity": return -.infinity
        default: break
        }
        guard t.first.map({ $0.isASCIIDigit || "+-.".contains($0) }) == true else { return nil }
        return Swift.Double(t)
    }

    /// What Dart's `toString()` prints for this value.
    var dartDescription: String {
        switch self {
        case .null: "null"
        case .bool(let b): b ? "true" : "false"
        case .int(let i): String(i)
        case .double(let d): JSON.dartDoubleString(d)
        case .string(let s): s
        case .array(let a): "[" + a.map(\.dartDescription).joined(separator: ", ") + "]"
        case .object(let o):
            "{" + o.keys.sorted().map { "\($0): \(o[$0]!.dartDescription)" }.joined(separator: ", ") + "}"
        }
    }

    /// Dart's `double.toString()`: shortest round-trip digits, decimal notation
    /// for 1e-6 ≤ |d| < 1e21 (always with a fraction, so 5.0 → "5.0"),
    /// exponential (`1.5e-7`, `1e+21`) outside that range.
    static func dartDoubleString(_ d: Swift.Double) -> String {
        if d.isNaN { return "NaN" }
        if d.isInfinite { return d < 0 ? "-Infinity" : "Infinity" }
        if d == 0 { return d.sign == .minus ? "-0.0" : "0.0" }

        var text = "\(d)"
        let negative = text.hasPrefix("-")
        if negative { text.removeFirst() }

        var mantissa = Substring(text)
        var exponent = 0
        if let e = text.firstIndex(where: { $0 == "e" || $0 == "E" }) {
            mantissa = text[..<e]
            exponent = Int(text[text.index(after: e)...]) ?? 0
        }
        let parts = mantissa.split(separator: ".", omittingEmptySubsequences: false)
        let whole = String(parts[0])
        let fraction = parts.count > 1 ? String(parts[1]) : ""

        // value = 0.<digits> × 10^point
        var digits = whole + fraction
        var point = whole.count + exponent
        while digits.count > 1, digits.hasPrefix("0") {
            digits.removeFirst()
            point -= 1
        }
        while digits.count > 1, digits.hasSuffix("0") {
            digits.removeLast()
        }

        let sign = negative ? "-" : ""
        let magnitude = abs(d)
        if magnitude >= 1e-6, magnitude < 1e21 {
            if point <= 0 {
                return sign + "0." + String(repeating: "0", count: -point) + digits
            }
            if point >= digits.count {
                return sign + digits + String(repeating: "0", count: point - digits.count) + ".0"
            }
            let split = digits.index(digits.startIndex, offsetBy: point)
            return sign + digits[..<split] + "." + digits[split...]
        }
        let power = point - 1
        let lead = digits.count == 1 ? digits : String(digits.first!) + "." + digits.dropFirst()
        return sign + lead + "e" + (power >= 0 ? "+" : "-") + String(abs(power))
    }
}

private extension Character {
    nonisolated var isASCIIDigit: Bool { isASCII && isNumber }
}

// MARK: - Building values (request bodies)

nonisolated extension JSON {
    init(_ value: String?) { self = value.map(JSON.string) ?? .null }
    init(_ value: Int?) { self = value.map(JSON.int) ?? .null }
    init(_ value: Swift.Double?) { self = value.map(JSON.double) ?? .null }
    init(_ value: Bool?) { self = value.map(JSON.bool) ?? .null }
    init(_ value: [JSON]) { self = .array(value) }
    init(_ value: [String: JSON]) { self = .object(value) }
}

nonisolated extension JSON: ExpressibleByNilLiteral, ExpressibleByBooleanLiteral,
    ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral, ExpressibleByStringLiteral,
    ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral
{
    init(nilLiteral: ()) { self = .null }
    init(booleanLiteral value: Bool) { self = .bool(value) }
    init(integerLiteral value: Int) { self = .int(value) }
    init(floatLiteral value: Swift.Double) { self = .double(value) }
    init(stringLiteral value: String) { self = .string(value) }
    init(arrayLiteral elements: JSON...) { self = .array(elements) }
    init(dictionaryLiteral elements: (String, JSON)...) {
        self = .object(Dictionary(elements, uniquingKeysWith: { _, last in last }))
    }
}

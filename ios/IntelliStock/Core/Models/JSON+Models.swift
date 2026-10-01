import Foundation

/// Accessors the model `init(json:)`s use to mirror recurring Dart idioms.
/// The base accessors (`string`, `double`, `int`, `bool`, `stringOr`, …) live
/// in `Core/JSON/JSON.swift`.
nonisolated extension JSON {
    /// Dart `a ?? b` where both sides are map lookups: `self` unless it is
    /// null, then `fallback`.
    func or(_ fallback: @autoclosure () -> JSON) -> JSON {
        isNull ? fallback() : self
    }

    var isObject: Bool { object != nil }
    var isArray: Bool { array != nil }

    /// Dart `x is num`.
    var isNum: Bool {
        switch self {
        case .int, .double: true
        default: false
        }
    }

    /// Dart `x is String`.
    var isString: Bool {
        if case .string = self { return true }
        return false
    }

    /// Dart `(x as List? ?? const []).whereType<Map>()`: the array's object
    /// elements, in order. Empty when this is not an array.
    var objectElements: [JSON] { arrayValue.filter(\.isObject) }

    /// Dart `(x as List? ?? const []).map((e) => e.toString())`.
    var stringElements: [String] { arrayValue.map(\.dartDescription) }

    /// Dart `x as num?`.
    var num: Num? { Num(json: self) }

    /// The backtest models' `_num`: a number as-is, anything else through
    /// `num.tryParse(v.toString())`.
    var lenientNum: Num? {
        if isNull { return nil }
        if let n = Num(json: self) { return n }
        return Num.tryParse(dartDescription)
    }

    /// Dart `double.tryParse` fallback helpers used by several models:
    /// a number's `toDouble()`, a string through `double.tryParse`, else nil.
    var numOrParsedDouble: Double? {
        if let d = double { return d }
        if case .string(let s) = self { return JSON.parseDouble(s) }
        return nil
    }

    /// Dart `jsonEncode(x)`, or `JsonEncoder.withIndent(indent).convert(x)`
    /// when `indent` is given. Object keys are sorted: `JSON` does not keep
    /// the server's key order (Dart maps did).
    func dartEncoded(indent: String? = nil) -> String {
        var out = ""
        JSON.encode(self, indent: indent, depth: 0, into: &out)
        return out
    }

    private static func encode(_ value: JSON, indent: String?, depth: Int, into out: inout String) {
        switch value {
        case .null: out += "null"
        case .bool(let b): out += b ? "true" : "false"
        case .int(let i): out += String(i)
        case .double(let d): out += JSON.dartDoubleString(d)
        case .string(let s): encodeString(s, into: &out)
        case .array(let items):
            if items.isEmpty { out += "[]"; return }
            out += "["
            for (i, item) in items.enumerated() {
                if i > 0 { out += "," }
                newline(indent, depth + 1, &out)
                encode(item, indent: indent, depth: depth + 1, into: &out)
            }
            newline(indent, depth, &out)
            out += "]"
        case .object(let map):
            if map.isEmpty { out += "{}"; return }
            out += "{"
            for (i, key) in map.keys.sorted().enumerated() {
                if i > 0 { out += "," }
                newline(indent, depth + 1, &out)
                encodeString(key, into: &out)
                out += indent == nil ? ":" : ": "
                encode(map[key]!, indent: indent, depth: depth + 1, into: &out)
            }
            newline(indent, depth, &out)
            out += "}"
        }
    }

    private static func newline(_ indent: String?, _ depth: Int, _ out: inout String) {
        guard let indent else { return }
        out += "\n" + String(repeating: indent, count: depth)
    }

    /// Dart's JSON string escaping: `"` and `\`, the short escapes for
    /// \b \t \n \f \r, `\u00xx` (lowercase hex) for other control characters.
    private static func encodeString(_ s: String, into out: inout String) {
        out += "\""
        for scalar in s.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\u{08}": out += "\\b"
            case "\t": out += "\\t"
            case "\n": out += "\\n"
            case "\u{0C}": out += "\\f"
            case "\r": out += "\\r"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
    }
}

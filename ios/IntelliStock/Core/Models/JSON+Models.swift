import Foundation

/// Accessors the model `init(json:)`s use to mirror recurring Dart idioms.
/// The base accessors (`string`, `double`, `int`, `bool`, `stringOr`,
/// `entries`, `orderedObject`, …) live in `Core/JSON/JSON.swift`.
nonisolated extension JSON {
    /// Dart `a ?? b` where both sides are map lookups: `self` unless it is
    /// null, then `fallback`.
    func or(_ fallback: @autoclosure () -> JSON) -> JSON {
        isNull ? fallback() : self
    }

    var isObject: Bool {
        if case .object = self { return true }
        return false
    }

    var isArray: Bool {
        if case .array = self { return true }
        return false
    }

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

    /// Dart `(x as Map?) ?? const {}`, keeping the server's key order — the
    /// form every model uses for a `Map<String, dynamic>` field.
    var orderedObjectValue: JSONObject { orderedObject ?? JSONObject() }

    /// Dart `(x as List? ?? const []).whereType<Map>()`: the array's object
    /// elements, in order. Empty when this is not an array.
    var objectElements: [JSON] { arrayValue.filter(\.isObject) }

    /// `objectElements` as ordered maps (Dart `List<Map<String, dynamic>>`).
    var objectList: [JSONObject] { arrayValue.compactMap(\.orderedObject) }

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
    /// when `indent` is given. The bytes come from `JSON.data()` (the one
    /// encoder: key order kept, doubles as Dart prints them); `indent` only
    /// adds Dart's line breaks and indentation. Throws on NaN/Infinity, as
    /// Dart does.
    func dartEncoded(indent: String? = nil) throws -> String {
        let compact = try data()
        guard let indent else { return String(decoding: compact, as: UTF8.self) }
        return JSON.reindent(compact, indent: indent)
    }

    /// Lays compact JSON out the way Dart's `JsonEncoder.withIndent` does:
    /// one member per line, `": "` after keys, and `[]` / `{}` kept on one
    /// line. Compact JSON has no whitespace outside strings, so only string
    /// contents need protecting.
    private static func reindent(_ compact: Data, indent: String) -> String {
        let bytes = Array(compact)
        let unit = Array(indent.utf8)
        var out: [UInt8] = []
        out.reserveCapacity(bytes.count * 2)
        var depth = 0
        var inString = false
        var escaped = false

        func newline() {
            out.append(UInt8(ascii: "\n"))
            for _ in 0..<depth { out.append(contentsOf: unit) }
        }

        var i = 0
        while i < bytes.count {
            let byte = bytes[i]
            if inString {
                out.append(byte)
                if escaped {
                    escaped = false
                } else if byte == UInt8(ascii: "\\") {
                    escaped = true
                } else if byte == UInt8(ascii: "\"") {
                    inString = false
                }
                i += 1
                continue
            }
            switch byte {
            case UInt8(ascii: "\""):
                inString = true
                out.append(byte)
            case UInt8(ascii: "{"), UInt8(ascii: "["):
                let close = byte == UInt8(ascii: "{") ? UInt8(ascii: "}") : UInt8(ascii: "]")
                out.append(byte)
                if i + 1 < bytes.count, bytes[i + 1] == close {
                    out.append(close)
                    i += 1
                } else {
                    depth += 1
                    newline()
                }
            case UInt8(ascii: "}"), UInt8(ascii: "]"):
                depth -= 1
                newline()
                out.append(byte)
            case UInt8(ascii: ","):
                out.append(byte)
                newline()
            case UInt8(ascii: ":"):
                out.append(contentsOf: ": ".utf8)
            default:
                out.append(byte)
            }
            i += 1
        }
        return String(decoding: out, as: UTF8.self)
    }
}

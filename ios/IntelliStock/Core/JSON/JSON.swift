import Foundation

/// A decoded JSON value, read the way the Flutter app read `Map<String, dynamic>`.
///
/// The Dart models never used strict decoding: they wrote `x?.toString()`,
/// `(x as num?)?.toDouble()`, `x == true` and `?? fallback`. Every model in
/// the native app has an `init(json:)` that mirrors its Dart `fromJson` line for
/// line through these accessors, so a backend quirk the Flutter app tolerated
/// is tolerated here too.
///
/// Like Dart's `jsonDecode` / `jsonEncode`:
/// - integers and doubles stay distinct, so `5` prints `"5"` and `5.0` prints `"5.0"`;
/// - objects keep the server's key order (Dart maps are insertion-ordered), so a
///   screen that lists a map unsorted shows it in the same order;
/// - encoding writes doubles with Dart's `toString` (`100000.0`, not `100000`).
nonisolated enum JSON: Hashable, Sendable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSON])
    case object(JSONObject)

    /// An object from an unordered dictionary (keys sorted, so the order is stable).
    static func object(_ dictionary: [String: JSON]) -> JSON {
        .object(JSONObject(dictionary))
    }
}

/// An insertion-ordered JSON object — Dart's `LinkedHashMap`. Equality ignores order.
nonisolated struct JSONObject: Hashable, Sendable, Sequence, ExpressibleByDictionaryLiteral {
    typealias Element = (key: String, value: JSON)

    private(set) var keys: [String] = []
    private var storage: [String: JSON] = [:]

    init() {}

    init(_ pairs: [(String, JSON)]) {
        for (key, value) in pairs { self[key] = value }
    }

    /// From an unordered dictionary: keys sorted, so the order is at least stable.
    init(_ dictionary: [String: JSON]) {
        for key in dictionary.keys.sorted() { self[key] = dictionary[key] }
    }

    init(dictionaryLiteral elements: (String, JSON)...) {
        self.init(elements)
    }

    /// Assigning an existing key keeps its position, as a Dart map does.
    subscript(key: String) -> JSON? {
        get { storage[key] }
        set {
            if let newValue {
                if storage.updateValue(newValue, forKey: key) == nil { keys.append(key) }
            } else if storage.removeValue(forKey: key) != nil {
                keys.removeAll { $0 == key }
            }
        }
    }

    var dictionary: [String: JSON] { storage }
    var values: [JSON] { keys.map { storage[$0]! } }
    var entries: [Element] { keys.map { (key: $0, value: storage[$0]!) } }
    var count: Int { keys.count }
    var isEmpty: Bool { keys.isEmpty }

    func makeIterator() -> IndexingIterator<[Element]> { entries.makeIterator() }

    func mapValues(_ transform: (JSON) throws -> JSON) rethrows -> JSONObject {
        var out = JSONObject()
        for key in keys { out[key] = try transform(storage[key]!) }
        return out
    }

    static func == (lhs: JSONObject, rhs: JSONObject) -> Bool { lhs.storage == rhs.storage }
    func hash(into hasher: inout Hasher) { hasher.combine(storage) }
}

// MARK: - Decoding and encoding

nonisolated struct JSONParseError: Error, Equatable {
    let offset: Int
    let reason: String
}

nonisolated struct JSONEncodeError: Error, Equatable {
    let reason: String
}

nonisolated extension JSON {
    /// Parses JSON text (top-level fragments allowed), keeping object key order.
    init(data: Data) throws {
        self = try data.withUnsafeBytes { raw in
            var parser = JSONParser(bytes: raw.bindMemory(to: UInt8.self))
            return try parser.parseDocument()
        }
    }

    /// Bridges a Foundation / Swift value (as JSONSerialization produces it).
    /// Foundation dictionaries carry no order, so their keys are sorted.
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

    /// Dart's `jsonEncode`: keys in order, doubles via `toString` (`100000.0`),
    /// `/` and non-ASCII unescaped. Throws on NaN/Infinity, as Dart does.
    func data() throws -> Data {
        var out: [UInt8] = []
        out.reserveCapacity(256)
        try encode(into: &out)
        return Data(out)
    }

    /// The Foundation form of this value (dictionaries lose key order).
    var anyValue: Any {
        switch self {
        case .null: NSNull()
        case .bool(let b): b
        case .int(let i): i
        case .double(let d): d
        case .string(let s): s
        case .array(let a): a.map(\.anyValue)
        case .object(let o): o.dictionary.mapValues(\.anyValue)
        }
    }

    private func encode(into out: inout [UInt8]) throws {
        switch self {
        case .null:
            out.append(contentsOf: "null".utf8)
        case .bool(let b):
            out.append(contentsOf: (b ? "true" : "false").utf8)
        case .int(let i):
            out.append(contentsOf: String(i).utf8)
        case .double(let d):
            guard d.isFinite else { throw JSONEncodeError(reason: "Converting object did not encode: \(d)") }
            out.append(contentsOf: JSON.dartDoubleString(d).utf8)
        case .string(let s):
            JSON.encodeString(s, into: &out)
        case .array(let items):
            out.append(UInt8(ascii: "["))
            for (n, item) in items.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                try item.encode(into: &out)
            }
            out.append(UInt8(ascii: "]"))
        case .object(let object):
            out.append(UInt8(ascii: "{"))
            for (n, entry) in object.entries.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                JSON.encodeString(entry.key, into: &out)
                out.append(UInt8(ascii: ":"))
                try entry.value.encode(into: &out)
            }
            out.append(UInt8(ascii: "}"))
        }
    }

    /// Dart's `_JsonStringifier.writeStringContent`: escapes `"`, `\` and
    /// control characters (lowercase `\u00xx`), nothing else.
    private static func encodeString(_ s: String, into out: inout [UInt8]) {
        out.append(UInt8(ascii: "\""))
        for byte in s.utf8 {
            switch byte {
            case UInt8(ascii: "\""): out.append(contentsOf: #"\""#.utf8)
            case UInt8(ascii: "\\"): out.append(contentsOf: #"\\"#.utf8)
            case 0x08: out.append(contentsOf: #"\b"#.utf8)
            case 0x09: out.append(contentsOf: #"\t"#.utf8)
            case 0x0A: out.append(contentsOf: #"\n"#.utf8)
            case 0x0C: out.append(contentsOf: #"\f"#.utf8)
            case 0x0D: out.append(contentsOf: #"\r"#.utf8)
            case 0x00..<0x20:
                let hex = Array("0123456789abcdef".utf8)
                out.append(contentsOf: #"\u00"#.utf8)
                out.append(hex[Int(byte >> 4)])
                out.append(hex[Int(byte & 0xF)])
            default:
                out.append(byte)
            }
        }
        out.append(UInt8(ascii: "\""))
    }
}

/// Recursive-descent JSON parser over UTF-8 bytes (RFC 8259), keeping object
/// key order. Numbers without a fraction or exponent are ints when they fit,
/// as in Dart's `jsonDecode`.
nonisolated private struct JSONParser {
    let bytes: UnsafeBufferPointer<UInt8>
    var i = 0

    init(bytes: UnsafeBufferPointer<UInt8>) {
        self.bytes = bytes
    }

    mutating func parseDocument() throws -> JSON {
        skipWhitespace()
        let value = try parseValue(depth: 0)
        skipWhitespace()
        guard i == bytes.count else { throw fail("Unexpected trailing characters") }
        return value
    }

    private func fail(_ reason: String) -> JSONParseError {
        JSONParseError(offset: i, reason: reason)
    }

    private mutating func skipWhitespace() {
        while i < bytes.count {
            switch bytes[i] {
            case 0x20, 0x09, 0x0A, 0x0D: i += 1
            default: return
            }
        }
    }

    private mutating func parseValue(depth: Int) throws -> JSON {
        guard depth < 512 else { throw fail("Nesting too deep") }
        guard i < bytes.count else { throw fail("Unexpected end of input") }
        switch bytes[i] {
        case UInt8(ascii: "{"): return try parseObject(depth: depth)
        case UInt8(ascii: "["): return try parseArray(depth: depth)
        case UInt8(ascii: "\""): return .string(try parseString())
        case UInt8(ascii: "t"): try expectLiteral("true"); return .bool(true)
        case UInt8(ascii: "f"): try expectLiteral("false"); return .bool(false)
        case UInt8(ascii: "n"): try expectLiteral("null"); return .null
        case UInt8(ascii: "-"), UInt8(ascii: "0")...UInt8(ascii: "9"): return try parseNumber()
        default: throw fail("Unexpected character")
        }
    }

    private mutating func expectLiteral(_ word: StaticString) throws {
        let count = word.utf8CodeUnitCount
        guard i + count <= bytes.count else { throw fail("Unexpected end of input") }
        for k in 0..<count where bytes[i + k] != word.utf8Start[k] {
            throw fail("Invalid literal")
        }
        i += count
    }

    private mutating func parseObject(depth: Int) throws -> JSON {
        i += 1
        var object = JSONObject()
        skipWhitespace()
        if i < bytes.count, bytes[i] == UInt8(ascii: "}") {
            i += 1
            return .object(object)
        }
        while true {
            skipWhitespace()
            guard i < bytes.count, bytes[i] == UInt8(ascii: "\"") else { throw fail("Expected a key") }
            let key = try parseString()
            skipWhitespace()
            guard i < bytes.count, bytes[i] == UInt8(ascii: ":") else { throw fail("Expected ':'") }
            i += 1
            skipWhitespace()
            object[key] = try parseValue(depth: depth + 1)
            skipWhitespace()
            guard i < bytes.count else { throw fail("Unterminated object") }
            if bytes[i] == UInt8(ascii: ",") { i += 1; continue }
            if bytes[i] == UInt8(ascii: "}") { i += 1; return .object(object) }
            throw fail("Expected ',' or '}'")
        }
    }

    private mutating func parseArray(depth: Int) throws -> JSON {
        i += 1
        var items: [JSON] = []
        skipWhitespace()
        if i < bytes.count, bytes[i] == UInt8(ascii: "]") {
            i += 1
            return .array(items)
        }
        while true {
            skipWhitespace()
            items.append(try parseValue(depth: depth + 1))
            skipWhitespace()
            guard i < bytes.count else { throw fail("Unterminated array") }
            if bytes[i] == UInt8(ascii: ",") { i += 1; continue }
            if bytes[i] == UInt8(ascii: "]") { i += 1; return .array(items) }
            throw fail("Expected ',' or ']'")
        }
    }

    private mutating func parseString() throws -> String {
        i += 1
        let start = i
        // Fast path: no escapes.
        while i < bytes.count {
            let byte = bytes[i]
            if byte == UInt8(ascii: "\"") {
                let s = String(decoding: UnsafeBufferPointer(rebasing: bytes[start..<i]), as: UTF8.self)
                i += 1
                return s
            }
            if byte == UInt8(ascii: "\\") { break }
            if byte < 0x20 { throw fail("Control character in string") }
            i += 1
        }
        var buffer = Array(UnsafeBufferPointer(rebasing: bytes[start..<i]))
        while i < bytes.count {
            let byte = bytes[i]
            switch byte {
            case UInt8(ascii: "\""):
                i += 1
                return String(decoding: buffer, as: UTF8.self)
            case UInt8(ascii: "\\"):
                i += 1
                guard i < bytes.count else { throw fail("Unterminated escape") }
                let escape = bytes[i]
                i += 1
                switch escape {
                case UInt8(ascii: "\""): buffer.append(UInt8(ascii: "\""))
                case UInt8(ascii: "\\"): buffer.append(UInt8(ascii: "\\"))
                case UInt8(ascii: "/"): buffer.append(UInt8(ascii: "/"))
                case UInt8(ascii: "b"): buffer.append(0x08)
                case UInt8(ascii: "f"): buffer.append(0x0C)
                case UInt8(ascii: "n"): buffer.append(0x0A)
                case UInt8(ascii: "r"): buffer.append(0x0D)
                case UInt8(ascii: "t"): buffer.append(0x09)
                case UInt8(ascii: "u"):
                    var scalar = try parseHex4()
                    if (0xD800...0xDBFF).contains(scalar), i + 1 < bytes.count,
                       bytes[i] == UInt8(ascii: "\\"), bytes[i + 1] == UInt8(ascii: "u") {
                        let save = i
                        i += 2
                        let low = try parseHex4()
                        if (0xDC00...0xDFFF).contains(low) {
                            scalar = 0x10000 + ((scalar - 0xD800) << 10) + (low - 0xDC00)
                        } else {
                            i = save
                        }
                    }
                    let char = Unicode.Scalar(scalar) ?? "\u{FFFD}"
                    buffer.append(contentsOf: String(char).utf8)
                default:
                    throw fail("Invalid escape")
                }
            default:
                if byte < 0x20 { throw fail("Control character in string") }
                buffer.append(byte)
                i += 1
            }
        }
        throw fail("Unterminated string")
    }

    private mutating func parseHex4() throws -> UInt32 {
        guard i + 4 <= bytes.count else { throw fail("Bad \\u escape") }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let byte = bytes[i]
            let digit: UInt32
            switch byte {
            case UInt8(ascii: "0")...UInt8(ascii: "9"): digit = UInt32(byte - UInt8(ascii: "0"))
            case UInt8(ascii: "a")...UInt8(ascii: "f"): digit = UInt32(byte - UInt8(ascii: "a") + 10)
            case UInt8(ascii: "A")...UInt8(ascii: "F"): digit = UInt32(byte - UInt8(ascii: "A") + 10)
            default: throw fail("Bad \\u escape")
            }
            value = value << 4 | digit
            i += 1
        }
        return value
    }

    private mutating func parseNumber() throws -> JSON {
        let start = i
        var isDouble = false
        if bytes[i] == UInt8(ascii: "-") { i += 1 }
        guard i < bytes.count, isDigit(bytes[i]) else { throw fail("Invalid number") }
        if bytes[i] == UInt8(ascii: "0") {
            i += 1
            if i < bytes.count, isDigit(bytes[i]) { throw fail("Leading zero") }
        } else {
            while i < bytes.count, isDigit(bytes[i]) { i += 1 }
        }
        if i < bytes.count, bytes[i] == UInt8(ascii: ".") {
            isDouble = true
            i += 1
            guard i < bytes.count, isDigit(bytes[i]) else { throw fail("Invalid fraction") }
            while i < bytes.count, isDigit(bytes[i]) { i += 1 }
        }
        if i < bytes.count, bytes[i] == UInt8(ascii: "e") || bytes[i] == UInt8(ascii: "E") {
            isDouble = true
            i += 1
            if i < bytes.count, bytes[i] == UInt8(ascii: "+") || bytes[i] == UInt8(ascii: "-") { i += 1 }
            guard i < bytes.count, isDigit(bytes[i]) else { throw fail("Invalid exponent") }
            while i < bytes.count, isDigit(bytes[i]) { i += 1 }
        }
        let text = String(decoding: UnsafeBufferPointer(rebasing: bytes[start..<i]), as: UTF8.self)
        if !isDouble, let int = Int(text) { return .int(int) }
        guard let double = Double(text) else { throw fail("Invalid number") }
        return .double(double)
    }

    private func isDigit(_ byte: UInt8) -> Bool {
        byte >= UInt8(ascii: "0") && byte <= UInt8(ascii: "9")
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

    /// The object as a dictionary (order dropped). Use `entries` or
    /// `orderedObject` where Dart iterated the map in order.
    var object: [String: JSON]? {
        orderedObject?.dictionary
    }

    /// The object with its key order.
    var orderedObject: JSONObject? {
        if case .object(let o) = self { return o }
        return nil
    }

    /// Dart `map.entries`, in the server's order; empty when not an object.
    var entries: [JSONObject.Element] { orderedObject?.entries ?? [] }

    /// Dart `(x as List?) ?? const []`.
    var arrayValue: [JSON] { array ?? [] }

    /// Dart `(x as Map?) ?? const {}` (order dropped — see `entries`).
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

    /// What Dart's `toString()` prints for this value (maps in key order).
    var dartDescription: String {
        switch self {
        case .null: "null"
        case .bool(let b): b ? "true" : "false"
        case .int(let i): String(i)
        case .double(let d): JSON.dartDoubleString(d)
        case .string(let s): s
        case .array(let a): "[" + a.map(\.dartDescription).joined(separator: ", ") + "]"
        case .object(let o): "{" + o.entries.map { "\($0.key): \($0.value.dartDescription)" }.joined(separator: ", ") + "}"
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
    /// Literal order is kept, as in a Dart map literal.
    init(dictionaryLiteral elements: (String, JSON)...) {
        self = .object(JSONObject(elements))
    }
}

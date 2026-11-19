import CryptoKit
import Foundation

/// `body_sha256` as the API computes it (contract §5): the SHA-256 hex of
///
///     json.dumps({"method", "path", "query", "body"}, sort_keys=True,
///                separators=(",", ":"), ensure_ascii=False)
///
/// The app recomputes it from the approval it shows and signs only when the
/// two agree, so what the operator reads is what the server executes.
///
/// This is its own encoder, not `JSON.data()`: that one writes doubles the way
/// Dart prints them and keeps the server's key order.
nonisolated enum ApprovalFingerprint {
    /// The canonical bytes; nil when a value has no Python JSON form (NaN, ±inf).
    static func canonicalJSON(method: String, path: String, query: JSON, body: JSON) -> Data? {
        let envelope: JSON = [
            "body": body, "method": .string(method), "path": .string(path), "query": query,
        ]
        var out: [UInt8] = []
        out.reserveCapacity(512)
        guard write(envelope, into: &out) else { return nil }
        return Data(out)
    }

    /// Lowercase hex SHA-256 of `canonicalJSON`.
    static func sha256Hex(method: String, path: String, query: JSON, body: JSON) -> String? {
        guard let data = canonicalJSON(method: method, path: path, query: query, body: body) else { return nil }
        var hex: [UInt8] = []
        hex.reserveCapacity(64)
        for byte in SHA256.hash(data: data) {
            hex.append(hexDigits[Int(byte >> 4)])
            hex.append(hexDigits[Int(byte & 0xF)])
        }
        return String(decoding: hex, as: UTF8.self)
    }

    /// The approval's own fields hash to the fingerprint the server sent.
    static func matches(_ approval: McpApproval) -> Bool {
        sha256Hex(method: approval.method, path: approval.path, query: approval.query, body: approval.body)
            == approval.bodySha256.lowercased()
    }

    private static let hexDigits = Array("0123456789abcdef".utf8)

    private static func write(_ value: JSON, into out: inout [UInt8]) -> Bool {
        switch value {
        case .null:
            out.append(contentsOf: "null".utf8)
        case .bool(let b):
            out.append(contentsOf: (b ? "true" : "false").utf8)
        case .int(let i):
            out.append(contentsOf: String(i).utf8)
        case .double(let d):
            guard let repr = pythonFloatRepr(d) else { return false }
            out.append(contentsOf: repr.utf8)
        case .string(let s):
            writeString(s, into: &out)
        case .array(let items):
            out.append(UInt8(ascii: "["))
            for (n, item) in items.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                guard write(item, into: &out) else { return false }
            }
            out.append(UInt8(ascii: "]"))
        case .object(let object):
            // Python sorts str keys by code point. Swift's String < compares
            // canonically equivalent forms, which orders "e\u{0301}" differently.
            let entries = object.entries.sorted { a, b in
                a.key.unicodeScalars.lexicographicallyPrecedes(b.key.unicodeScalars) { $0.value < $1.value }
            }
            out.append(UInt8(ascii: "{"))
            for (n, entry) in entries.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                writeString(entry.key, into: &out)
                out.append(UInt8(ascii: ":"))
                guard write(entry.value, into: &out) else { return false }
            }
            out.append(UInt8(ascii: "}"))
        }
        return true
    }

    /// Python's `ensure_ascii=False` string form: `"` and `\` escaped,
    /// `\b \f \n \r \t` short, other U+0000–U+001F as lowercase `\u00xx`,
    /// everything else as raw UTF-8. Every escaped character is ASCII, and no
    /// byte of a multi-byte UTF-8 sequence is, so a byte walk is exact.
    private static func writeString(_ s: String, into out: inout [UInt8]) {
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
                out.append(contentsOf: #"\u00"#.utf8)
                out.append(hexDigits[Int(byte >> 4)])
                out.append(hexDigits[Int(byte & 0xF)])
            default:
                out.append(byte)
            }
        }
        out.append(UInt8(ascii: "\""))
    }
}

/// CPython's `repr(float)`, which is what `json.dumps` writes for a float:
/// the shortest round-trip digits, in fixed notation for 1e-4 <= |d| < 1e16
/// (always with a fraction, so 5.0 is "5.0"), otherwise as `1e+16` / `1e-05`
/// (the exponent signed and at least two digits). nil for NaN and ±inf,
/// which have no JSON form.
nonisolated func pythonFloatRepr(_ d: Double) -> String? {
    guard d.isFinite else { return nil }
    if d == 0 { return d.sign == .minus ? "-0.0" : "0.0" }

    // Swift's description is also shortest round-trip; only the layout differs.
    let text = abs(d).description
    var mantissa = Substring(text)
    var exponent = 0
    if let e = text.firstIndex(where: { $0 == "e" || $0 == "E" }) {
        mantissa = text[..<e]
        guard let parsed = Int(text[text.index(after: e)...]) else { return nil }
        exponent = parsed
    }
    let parts = mantissa.split(separator: ".", omittingEmptySubsequences: false)
    let whole = parts[0]
    let fraction = parts.count > 1 ? parts[1] : ""

    // value = 0.<digits> × 10^decpt
    var digits = Array((whole + fraction).utf8)
    var decpt = whole.count + exponent
    let leadingZeros = digits.prefix { $0 == UInt8(ascii: "0") }.count
    digits.removeFirst(leadingZeros)
    decpt -= leadingZeros
    while digits.last == UInt8(ascii: "0") { digits.removeLast() }
    guard let first = digits.first else { return nil }
    let n = digits.count
    let all = String(decoding: digits, as: UTF8.self)

    let body: String
    if decpt > 16 || decpt < -3 {
        let power = decpt - 1
        let magnitude = String(abs(power))
        let lead = n > 1 ? String(UnicodeScalar(first)) + "." + all.dropFirst() : all
        body = lead + "e" + (power < 0 ? "-" : "+") + (magnitude.count < 2 ? "0" + magnitude : magnitude)
    } else if decpt <= 0 {
        body = "0." + String(repeating: "0", count: -decpt) + all
    } else if decpt >= n {
        body = all + String(repeating: "0", count: decpt - n) + ".0"
    } else {
        let split = all.index(all.startIndex, offsetBy: decpt)
        body = String(all[..<split]) + "." + all[split...]
    }
    return d < 0 ? "-" + body : body
}

/// The bytes a decision signs (contract §5): UTF-8 of
/// `f"{id}|{body_sha256}|{decision}|{ts}"`.
nonisolated enum ApprovalMessage {
    static func bytes(id: String, bodySha256: String, decision: ApprovalDecision, ts: Int) -> Data {
        Data("\(id)|\(bodySha256)|\(decision.rawValue)|\(ts)".utf8)
    }
}

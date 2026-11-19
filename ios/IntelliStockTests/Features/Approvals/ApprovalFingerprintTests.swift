import Foundation
import Testing
@testable import IntelliStock

// The cross-language pin: these vectors were produced with CPython's
// json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)
// and hashlib.sha256, the way the API computes body_sha256 (contract §5).

nonisolated let kVectorAHash = "15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d"

struct ApprovalFingerprintTests {
    private func parse(_ text: String) throws -> JSON { try JSON(data: Data(text.utf8)) }

    @Test func matchesPythonForAnOrder() throws {
        let body = try parse(#"{"symbol":"TQQQ","qty":10,"side":"buy","type":"market","limit_price":null}"#)
        let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "POST", path: "/brokerages/b1/orders", query: .object(JSONObject()), body: body))
        #expect(String(decoding: bytes, as: UTF8.self) == #"{"body":{"limit_price":null,"qty":10,"side":"buy","symbol":"TQQQ","type":"market"},"method":"POST","path":"/brokerages/b1/orders","query":{}}"#)
        #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/brokerages/b1/orders", query: .object(JSONObject()), body: body) == kVectorAHash)
    }

    @Test func matchesPythonForFloatsUnicodeAndEscapes() throws {
        // Keys deliberately out of order; numbers as FastAPI's JSON encoder writes them.
        let body = try parse(#"{"z":1.0,"a":[0.1,1e+16,9500000000000000.0,1e-05,0.0001,-0.0,123.45],"name":"Café ☕ \"q\"\n\t\u0001","nested":{"b":true,"a":null},"B":2,"_k":"x"}"#)
        let query = try parse(#"{"dry":"false"}"#)
        let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "POST", path: "/instances/alpaca-main/live-command", query: query, body: body))
        #expect(String(decoding: bytes, as: UTF8.self) == #"{"body":{"B":2,"_k":"x","a":[0.1,1e+16,9500000000000000.0,1e-05,0.0001,-0.0,123.45],"name":"Café ☕ \"q\"\n\t\u0001","nested":{"a":null,"b":true},"z":1.0},"method":"POST","path":"/instances/alpaca-main/live-command","query":{"dry":"false"}}"#)
        #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/instances/alpaca-main/live-command", query: query, body: body)
                == "75f4624cffd3532685712841e50e14fcd1326bd508569e0d137f76f7f19ea5f8")
    }

    @Test func matchesPythonForANullBodyAndAListQuery() throws {
        let query = try parse(#"{"status":["a","b"]}"#)
        #expect(ApprovalFingerprint.sha256Hex(method: "DELETE", path: "/strategies/doc 7", query: query, body: .null)
                == "3cabaf62f5af592cd98c01381cf5562cd18fb067d5eee33830ceea18d8c1b016")
    }

    @Test(arguments: [
        (5.0, "5.0"), (0.1, "0.1"), (1e16, "1e+16"), (9.5e15, "9500000000000000.0"), (1e-05, "1e-05"),
        (0.0001, "0.0001"), (-0.0, "-0.0"), (0.0, "0.0"), (123.45, "123.45"), (1e22, "1e+22"),
        (1.5e-07, "1.5e-07"), (123456789.125, "123456789.125"), (-2.5, "-2.5"),
    ])
    func pythonFloatReprMatchesPythonRepr(value: Double, repr: String) {
        #expect(pythonFloatRepr(value) == repr)
    }

    @Test func sortsKeysByCodePointNotSwiftCollation() throws {
        // "e\u{0301}" (a decomposed é) sorts before "f" by code point, as Python does,
        // but after it under Swift's String <, which compares canonically.
        let body: JSON = .object(JSONObject([("f", .int(2)), ("e\u{0301}", .int(1)), ("Z", .int(3)), ("_", .int(4))]))
        let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "GET", path: "/p", query: .object(JSONObject()), body: body))
        #expect(bytes.starts(with: Data("{\"body\":{\"Z\":3,\"_\":4,\"e\u{0301}\":1,\"f\":2}".utf8)))   // bytes, not String ==
    }

    @Test func nonFiniteNumbersHaveNoFingerprint() {
        #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/p", query: .null, body: ["x": .double(.nan)]) == nil)
    }

    @Test func messageBytesAreTheContractString() {
        #expect(ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .approve, ts: 1_763_538_000)
                == Data("ap_7f3c|\(kVectorAHash)|approve|1763538000".utf8))
        #expect(ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .deny, ts: 7)
                == Data("ap_7f3c|\(kVectorAHash)|deny|7".utf8))
    }

    @Test func approvalIdsAreValidated() {
        for ok in ["ap_7f3c", "3f2b1c4e-9d1a-4c8e-b2f0-7a6d5e4c3b2a", String(repeating: "a", count: 128)] { #expect(ApprovalIds.isValid(ok)) }
        for bad in ["", "../brokerages", "a/b", "a%2Fb", "ap 1", "ap.1", String(repeating: "a", count: 129)] { #expect(!ApprovalIds.isValid(bad)) }
    }
}

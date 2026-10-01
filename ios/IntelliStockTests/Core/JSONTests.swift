import Foundation
import Testing
@testable import IntelliStock

/// The JSON value mirrors how the Dart models read `Map<String, dynamic>`:
/// `x?.toString()`, `(x as num?)?.toDouble()`, `x == true`, `?? default`.
struct JSONTests {
    private func parse(_ text: String) throws -> JSON {
        try JSON(data: Data(text.utf8))
    }

    @Test func integerLiteralPrintsWithoutFraction() throws {
        let j = try parse(#"{"a": 5}"#)
        #expect(j["a"].string == "5")
    }

    @Test func doubleLiteralKeepsDartFraction() throws {
        // Dart's jsonDecode keeps 5.0 a double, and double.toString() is "5.0".
        let j = try parse(#"{"a": 5.0, "b": 5.5}"#)
        #expect(j["a"].string == "5.0")
        #expect(j["b"].string == "5.5")
    }

    @Test func numericAccessorsCoerceLikeNum() throws {
        let j = try parse(#"{"i": 5, "d": 5.7, "s": "5", "b": true}"#)
        #expect(j["i"].double == 5)
        #expect(j["i"].int == 5)
        #expect(j["d"].double == 5.7)
        #expect(j["d"].int == 5)          // num.toInt() truncates
        #expect(j["s"].int == nil)        // a String is not a num
        #expect(j["s"].double == nil)
        #expect(j["b"].int == nil)        // a bool is not a num
    }

    @Test func tryParseHelpersMatchDart() {
        #expect(JSON.parseInt("42") == 42)
        #expect(JSON.parseInt("4.2") == nil)
        #expect(JSON.parseInt(nil) == nil)
        #expect(JSON.parseDouble("4.25") == 4.25)
        #expect(JSON.parseDouble("x") == nil)
    }

    @Test func boolIsTrueOnlyForTrue() throws {
        let j = try parse(#"{"t": true, "f": false, "s": "true", "n": 1}"#)
        #expect(j["t"].bool)
        #expect(!j["f"].bool)
        #expect(!j["s"].bool)
        #expect(!j["n"].bool)
        #expect(!j["missing"].bool)
        #expect(j["f"].boolValue == false)
        #expect(j["s"].boolValue == nil)
    }

    @Test func missingKeysAndNullsFallBack() throws {
        let j = try parse(#"{"n": null, "o": {"x": 1}}"#)
        #expect(j["n"].isNull)
        #expect(j["missing"].isNull)
        #expect(j["o"]["missing"]["deeper"].isNull)
        #expect(j["n"].stringOr("x") == "x")
        #expect(j["missing"].doubleOr(1.5) == 1.5)
        #expect(j["o"]["x"].intOr(9) == 1)
        #expect(j["n"].string == nil)
        #expect(j["n"].arrayValue.isEmpty)
        #expect(j["n"].objectValue.isEmpty)
    }

    @Test func boolAndStringToStringLikeDart() throws {
        let j = try parse(#"{"b": true, "s": "hi"}"#)
        #expect(j["b"].string == "true")
        #expect(j["s"].string == "hi")
    }

    @Test func arraysIndexSafely() throws {
        let j = try parse(#"[1, "two", null]"#)
        #expect(j.array?.count == 3)
        #expect(j[1].string == "two")
        #expect(j[7].isNull)
        #expect(j["k"].isNull)
    }

    @Test func roundTripsThroughData() throws {
        let original: JSON = ["a": 1, "b": 2.5, "c": "x", "d": true, "e": nil, "f": [1, 2]]
        let back = try JSON(data: original.data())
        #expect(back == original)
        #expect(back["a"].string == "1")
        #expect(back["b"].string == "2.5")
    }

    @Test func initFromAnyBridgesFoundationValues() {
        let any: Any = ["n": NSNumber(value: 3), "d": NSNumber(value: 1.25), "b": NSNumber(value: true), "x": NSNull()]
        let j = JSON(any: any)
        #expect(j["n"] == .int(3))
        #expect(j["d"] == .double(1.25))
        #expect(j["b"] == .bool(true))
        #expect(j["x"].isNull)
    }

    @Test(arguments: [
        (5.0, "5.0"), (100.0, "100.0"), (0.001, "0.001"), (-2.25, "-2.25"),
        (1e16, "10000000000000000.0"), (1.5e-7, "1.5e-7"), (1e21, "1e+21"), (0.0, "0.0"),
    ])
    func doubleToStringMatchesDart(value: Double, expected: String) {
        #expect(JSON.dartDoubleString(value) == expected)
    }

    // MARK: Order and encoding (Dart's LinkedHashMap + jsonEncode)

    @Test func objectKeysKeepServerOrder() throws {
        let j = try parse(#"{"zeta": 1, "alpha": 2, "mid": {"b": 1, "a": 2}}"#)
        #expect(j.entries.map(\.key) == ["zeta", "alpha", "mid"])
        #expect(j["mid"].entries.map(\.key) == ["b", "a"])
        #expect(j.dartDescription == "{zeta: 1, alpha: 2, mid: {b: 1, a: 2}}")
    }

    @Test func duplicateKeysKeepFirstPositionAndLastValue() throws {
        let j = try parse(#"{"a": 1, "b": 2, "a": 3}"#)
        #expect(j.entries.map(\.key) == ["a", "b"])
        #expect(j["a"].int == 3)
    }

    @Test func equalityIgnoresKeyOrder() throws {
        #expect(try parse(#"{"a": 1, "b": 2}"#) == parse(#"{"b": 2, "a": 1}"#))
        #expect(try parse(#"{"a": 1, "b": 2}"#) == ["b": 2, "a": 1])
    }

    @Test func dictionaryAccessorsStillWork() throws {
        let j = try parse(#"{"a": 1}"#)
        #expect(j.object == ["a": .int(1)])
        #expect(j.objectValue["a"] == .int(1))
        let built = JSON.object(["k": "v"])
        #expect(built["k"].string == "v")
    }

    @Test func encodesLikeDartJsonEncode() throws {
        let body: JSON = .object(JSONObject([
            ("initial_cash", .double(100000)), ("ratio", .double(0.25)), ("n", .int(3)),
            ("s", .string("a/b \"q\" \\ é\n")), ("flag", .bool(false)), ("none", .null),
            ("list", [1, 2.5]),
        ]))
        let text = String(decoding: try body.data(), as: UTF8.self)
        #expect(text == #"{"initial_cash":100000.0,"ratio":0.25,"n":3,"s":"a/b \"q\" \\ é\n","flag":false,"none":null,"list":[1,2.5]}"#)
    }

    @Test func encodingNonFiniteThrows() {
        #expect(throws: (any Error).self) { _ = try JSON.double(.nan).data() }
    }

    @Test func parsesEscapesAndSurrogatePairs() throws {
        let j = try parse(#"{"s": "tab\tq\"é😀\/"}"#)
        #expect(j["s"].string == "tab\tq\"é😀/")
    }

    @Test func numbersParseLikeDart() throws {
        let j = try parse(#"[0, -12, 3.0, 1e3, 2E-2, 12345678901234567890]"#)
        #expect(j[0] == .int(0))
        #expect(j[1] == .int(-12))
        #expect(j[2] == .double(3))
        #expect(j[3] == .double(1000))
        #expect(j[4] == .double(0.02))
        #expect(j[5].double == 12345678901234567890)
    }

    @Test(arguments: ["", "{", #"{"a" 1}"#, "[1,]", "tru", #""unterminated"#, "{} x", "01"])
    func malformedInputThrows(text: String) {
        #expect(throws: (any Error).self) { _ = try JSON(data: Data(text.utf8)) }
    }

    @Test func topLevelFragmentsParse() throws {
        #expect(try parse("\"ok\"").string == "ok")
        #expect(try parse("7").int == 7)
        #expect(try parse("null").isNull)
    }
}

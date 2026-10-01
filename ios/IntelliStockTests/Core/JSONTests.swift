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

    @Test func topLevelFragmentsParse() throws {
        #expect(try parse("\"ok\"").string == "ok")
        #expect(try parse("7").int == 7)
        #expect(try parse("null").isNull)
    }
}

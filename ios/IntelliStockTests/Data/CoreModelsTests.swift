import Foundation
import Testing
@testable import IntelliStock

/// The Dart-semantics helpers every model leans on: `num`, `DateTime.tryParse`,
/// `toStringAsFixed`, `jsonEncode`, and `PortfolioHistory`.
struct NumTests {
    @Test func keepsIntAndDoubleApartForPrinting() {
        #expect(Num(json: 5)?.description == "5")
        #expect(Num(json: 5.0)?.description == "5.0")
        #expect(Num(json: 0.1)?.description == "0.1")
        #expect(Num(json: "5") == nil)
        #expect(Num(json: .null) == nil)
    }

    @Test func equalityAndOrderingFollowDart() {
        #expect(Num.int(5) == Num.double(5.0))
        #expect(Set([Num.int(5), Num.double(5.0)]).count == 1)
        #expect(Num.int(2) < Num.double(2.5))
        #expect(Num.double(-1).int == -1)
        #expect(Num.double(2.9).int == 2)
    }

    @Test func tryParseIsIntThenDouble() {
        #expect(Num.tryParse("12") == .int(12))
        #expect(Num.tryParse("12")?.description == "12")
        #expect(Num.tryParse("1.5") == .double(1.5))
        #expect(Num.tryParse("0x1F") == .int(31))
        #expect(Num.tryParse("abc") == nil)
        #expect(Num.tryParse(nil) == nil)
    }

    @Test func lenientNumParsesStringsButNotBools() {
        #expect(JSON.string("7").lenientNum == .int(7))
        #expect(JSON.bool(true).lenientNum == nil)
        #expect(JSON.null.lenientNum == nil)
        #expect(JSON.double(2.5).lenientNum == .double(2.5))
    }
}

struct DartDateTimeDataTests {
    private func ms(_ s: String) -> Int? { DartDateTime.tryParse(s).map(DartDateTime.millisecondsSinceEpoch) }

    @Test func utcForms() {
        #expect(ms("2026-06-10T12:00:00Z") == 1_781_092_800_000)
        #expect(ms("2026-06-10T12:00:00+00:00") == 1_781_092_800_000)
        #expect(ms("2026-06-10T14:00:00+02:00") == 1_781_092_800_000)
        #expect(ms("2026-06-10T07:30:00-0430") == 1_781_092_800_000)
        #expect(ms("2026-06-10 12:00:00z") == 1_781_092_800_000)
        #expect(ms("20260610T120000Z") == 1_781_092_800_000)
    }

    @Test func fractionsKeepSixDigits() {
        #expect(ms("2026-06-10T12:00:00.123Z") == 1_781_092_800_123)
        #expect(ms("2026-06-10T12:00:00.1239999Z") == 1_781_092_800_123)
        #expect(ms("2026-06-10T12:00:00,5Z") == 1_781_092_800_500)
    }

    @Test func noSuffixIsLocalTime() throws {
        let date = try #require(DartDateTime.tryParse("2026-06-10T12:34:56"))
        let c = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        #expect(c.year == 2026 && c.month == 6 && c.day == 10)
        #expect(c.hour == 12 && c.minute == 34 && c.second == 56)
        let dateOnly = try #require(DartDateTime.tryParse("2026-06-10"))
        #expect(Calendar.current.dateComponents([.hour], from: dateOnly).hour == 0)
    }

    @Test func outOfRangeFieldsRollOver() {
        #expect(DartDateTime.tryParse("2026-02-30T00:00:00Z") == DartDateTime.tryParse("2026-03-02T00:00:00Z"))
        #expect(DartDateTime.tryParse("2026-13-01T00:00:00Z") == DartDateTime.tryParse("2027-01-01T00:00:00Z"))
    }

    @Test func rejectsWhatDartRejects() {
        for bad in ["", "now", "2026-6-10", "2026-06-10T", "2026-06-10T1", "2026-06-10Z", " 2026-06-10", "١٢٣٤-01-01"] {
            #expect(DartDateTime.tryParse(bad) == nil, "\(bad)")
        }
        #expect(DartDateTime.tryParse(nil) == nil)
    }
}

struct DartFormattingTests {
    @Test func toStringAsFixedRoundsTiesAwayFromZero() {
        #expect(dartToStringAsFixed(2.5, 0) == "3")
        #expect(dartToStringAsFixed(-2.5, 0) == "-3")
        #expect(dartToStringAsFixed(0.125, 2) == "0.13")
        #expect(dartToStringAsFixed(1.005, 2) == "1.00") // binary value is below the tie
        #expect(dartToStringAsFixed(12.5, 4) == "12.5000")
        #expect(dartToStringAsFixed(130, 0) == "130")
        #expect(dartToStringAsFixed(127.5, 2) == "127.50")
        #expect(dartToStringAsFixed(9.999, 2) == "10.00")
        #expect(dartToStringAsFixed(-0.001, 2) == "-0.00")
        #expect(dartToStringAsFixed(0, 2) == "0.00")
    }

    @Test func dartEncodedMatchesJsonEncode() throws {
        // Key order is kept (a Dart map literal is insertion-ordered).
        let value: JSON = ["b": [1, 2.0, "x\n\"y\""], "a": nil, "c": true]
        #expect(try value.dartEncoded() == #"{"b":[1,2.0,"x\n\"y\""],"a":null,"c":true}"#)
        // One encoder: the compact form is exactly JSON.data().
        #expect(try value.dartEncoded() == String(decoding: try value.data(), as: UTF8.self))
        #expect(try JSON.string("\u{01}").dartEncoded() == #""\u0001""#)
        #expect(throws: JSONEncodeError.self) { try JSON.double(.nan).dartEncoded() }
    }

    @Test func dartEncodedWithIndentMatchesJsonEncoderWithIndent() throws {
        #expect(try JSON.array([]).dartEncoded(indent: "  ") == "[]")
        let value: JSON = ["k": [1], "e": [:], "l": [], "s": "a,b:{c}[d] \"q\""]
        #expect(try value.dartEncoded(indent: "  ") == """
        {
          "k": [
            1
          ],
          "e": {},
          "l": [],
          "s": "a,b:{c}[d] \\"q\\""
        }
        """)
    }

    @Test func llmAsStringStringifiesStructuredValues() {
        #expect(llmAsString(.null) == nil)
        #expect(llmAsString("s") == "s")
        #expect(llmAsString(5) == "5")
        #expect(llmAsString(["text": "hi"]) == #"{"text":"hi"}"#)
    }

    @Test func dartCompareIsCodeUnitOrder() {
        #expect(dartCompare("2025-03-01", "2025-02-01") > 0)
        #expect(dartCompare("a", "a") == 0)
        #expect(dartCompare("a", "ab") < 0)
        #expect(dartCompare("Z", "a") < 0)
    }
}

struct PortfolioHistoryTests {
    @Test func parsesEpochSecondsMillisAndIsoTimestamps() {
        let h = PortfolioHistory(json: [
            "timestamps": [1_700_000_000, 1_700_000_000_500, "2026-01-01T00:00:00Z", nil, true],
            "values": [1, 2.5, nil],
            "current_value": 3,
            "change_pct": "1.0",
        ])
        #expect(h.timestamps.map(DartDateTime.millisecondsSinceEpoch) == [1_700_000_000_000, 1_700_000_000_500, 1_767_225_600_000])
        #expect(h.values == [1, 2.5, 0])
        #expect(h.currentValue == 3)
        #expect(h.changePct == nil)
        #expect(!h.isEmpty)
        #expect(PortfolioHistory(json: [:]).isEmpty)
    }

    @Test func sinceLocalMidnightRebaselinesToMidnight() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 18, hour: 10)))
        let midnight = calendar.startOfDay(for: now)
        let h = PortfolioHistory(
            timestamps: [
                midnight.addingTimeInterval(-7_200),
                midnight.addingTimeInterval(-60),
                midnight,
                midnight.addingTimeInterval(3_600),
                midnight.addingTimeInterval(7_200),
            ],
            values: [90, 100, 101, 105, 110]
        )
        let day = h.sinceLocalMidnight(now: now, calendar: calendar)
        #expect(day.timestamps.first == midnight)
        // The at-midnight sample is dropped; the baseline is the last one before it.
        #expect(day.values == [100, 105, 110])
        #expect(day.openValue == 100)
        #expect(day.currentValue == 110)
        #expect(day.changeAbs == 10)
        #expect(close(day.changePct, 10))
    }

    @Test func sinceLocalMidnightFallsBackToTheEarliestValueAndKeepsCurrent() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 6, day: 18, hour: 10)))
        let midnight = calendar.startOfDay(for: now)
        let h = PortfolioHistory(
            timestamps: [midnight.addingTimeInterval(60), midnight.addingTimeInterval(120)],
            values: [0, 50],
            currentValue: 55
        )
        let day = h.sinceLocalMidnight(now: now, calendar: calendar)
        #expect(day.values == [0, 0, 50])
        #expect(day.currentValue == 55)
        #expect(day.changePct == nil) // baseline 0
        #expect(PortfolioHistory(timestamps: [], values: []).sinceLocalMidnight() == PortfolioHistory(timestamps: [], values: []))
    }
}

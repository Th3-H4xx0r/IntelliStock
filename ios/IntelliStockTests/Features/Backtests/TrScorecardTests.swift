import Foundation
import Testing
@testable import IntelliStock

/// The TR scorecard on GET /backtests/:id/summary (backend
/// trend_research/scorecard.py): absent, `{error}`, or the three periods.
struct TrScorecardDecodingTests {
    private func summary(_ text: String) throws -> BacktestSummary {
        BacktestSummary(json: try JSON(data: Data(text.utf8)))
    }

    static let withoutCard = #"{"id": "7", "status": "finished", "pnl": 12.5}"#

    static let errorCard = #"""
    {"id": "7", "status": "finished", "tr_scorecard": {"error": "ValueError: no SPY bars"}}
    """#

    static let fullCard = #"""
    {"id": "7", "status": "finished", "tr_scorecard": {
      "overall": {"untested": false, "start": "2023-01-03", "end": "2024-12-31", "sessions": 502,
        "ret": 41.2, "spy_ret": 30.1, "qqq_ret": 35.0, "cagr": 18.84, "spy_cagr": 14.04,
        "qqq_cagr": 16.0, "excess_pp": 4.8, "max_dd": -12.34, "spy_max_dd": -9.9,
        "roll12_beat_pct": 62.5,
        "windows": [
          {"tag": "ru1", "kind": "bull", "start": "2023-01-01", "end": "2023-07-31",
           "source": "battery", "ret": 22.24, "spy_ret": 18.0, "beat": true},
          {"tag": "rule-1", "kind": "bear", "start": "2024-04-01", "end": "2024-05-01",
           "source": "rule", "ret": null, "spy_ret": -3.0, "beat": null}
        ],
        "regimes": {"bull": true, "chop": "untested", "bear": "untested"},
        "random_pctile_candidates": 71.26, "random_pctile_universe": null,
        "pass_all_regimes": "untested", "pass_plus5": false, "pass": false},
      "optimistic": {"untested": false, "start": "2023-01-03", "end": "2024-12-31",
        "cagr": 18.84, "spy_cagr": 14.04, "excess_pp": 4.8, "max_dd": -12.34, "spy_max_dd": -9.9,
        "windows": [], "random_pctile_candidates": null, "random_pctile_universe": null,
        "pass_all_regimes": true, "pass_plus5": true, "pass": true},
      "honest": {"untested": true, "start": null, "end": null, "sessions": 0,
        "ret": null, "spy_ret": null, "cagr": null, "spy_cagr": null, "excess_pp": null,
        "max_dd": null, "spy_max_dd": null, "roll12_beat_pct": null, "windows": [],
        "random_pctile_candidates": null, "random_pctile_universe": null,
        "pass_all_regimes": "untested", "pass_plus5": "untested", "pass": "untested"},
      "meta": {"cutoff": "2025-01-01", "notes": ["Engine NAV is price-only.", "Survivorship."]}
    }}
    """#

    @Test func aSummaryWithoutTheFieldHasNoScorecard() throws {
        let s = try summary(Self.withoutCard)
        #expect(s.trScorecard == nil)
        #expect(s.pnl?.double == 12.5)
    }

    @Test func aNonObjectScorecardReadsAsAbsent() throws {
        #expect(try summary(#"{"tr_scorecard": null}"#).trScorecard == nil)
        #expect(try summary(#"{"tr_scorecard": "x"}"#).trScorecard == nil)
    }

    @Test func theErrorShapeDecodesToTheErrorOnly() throws {
        let card = try #require(try summary(Self.errorCard).trScorecard)
        #expect(card.error == "ValueError: no SPY bars")
        #expect(TrScorecardFormat.errorNote(card.error!) == "Scorecard unavailable: ValueError: no SPY bars")
    }

    @Test func theFullShapeDecodesEveryPeriod() throws {
        let card = try #require(try summary(Self.fullCard).trScorecard)
        #expect(card.error == nil)
        #expect(card.cutoff == "2025-01-01")
        #expect(card.notes == ["Engine NAV is price-only.", "Survivorship."])

        let overall = card.overall
        #expect(!overall.untested)
        #expect(overall.start == "2023-01-03")
        #expect(overall.cagr == 18.84)
        #expect(overall.spyCagr == 14.04)
        #expect(overall.excessPp == 4.8)
        #expect(overall.maxDd == -12.34)
        #expect(overall.spyMaxDd == -9.9)
        #expect(overall.randomPctileCandidates == 71.26)
        #expect(overall.randomPctileUniverse == nil)
        #expect(overall.passAllRegimes == .untested)
        #expect(overall.passPlus5 == .fail)
        #expect(overall.windows.count == 2)
        #expect(overall.windows[0] == TrScorecardWindow(tag: "ru1", kind: "bull", start: "2023-01-01",
                                                         end: "2023-07-31", ret: 22.24, spyRet: 18.0, beat: true))
        #expect(overall.windows[1].ret == nil)
        #expect(overall.windows[1].beat == nil)

        #expect(card.optimistic.passAllRegimes == .pass)
        #expect(card.optimistic.passPlus5 == .pass)

        #expect(card.honest.untested)
        #expect(card.honest.cagr == nil)
        #expect(card.honest.passPlus5 == .untested)
        #expect(card.honest.windows.isEmpty)
    }

    @Test func aMissingPeriodReadsAsUntested() throws {
        let card = try #require(try summary(#"{"tr_scorecard": {"overall": {"cagr": 1.0}}}"#).trScorecard)
        #expect(!card.overall.untested)
        #expect(card.honest.untested)
        #expect(card.optimistic.passAllRegimes == .untested)
        #expect(card.notes.isEmpty)
        #expect(card.cutoff == nil)
    }
}

struct TrScorecardFormatTests {
    @Test func periodsInPageOrderWithPlainLabels() throws {
        let card = try #require(BacktestSummary(json: try JSON(data: Data(TrScorecardDecodingTests.fullCard.utf8))).trScorecard)
        #expect(TrScorecardFormat.periods(card).map(\.label) == [
            "Whole test",
            "Honest period (after AI training cutoff)",
            "Optimistic period (AI may remember)",
        ])
        #expect(TrScorecardFormat.periods(card)[1].period.untested)
    }

    @Test func verdictLabels() {
        #expect(TrScorecardFormat.verdictLabel(.pass) == "Pass")
        #expect(TrScorecardFormat.verdictLabel(.fail) == "Fail")
        #expect(TrScorecardFormat.verdictLabel(.untested) == "Not tested yet")
    }

    @Test func percentagesHaveOneDecimal() {
        #expect(TrScorecardFormat.signedPct(18.84) == "+18.8%")
        #expect(TrScorecardFormat.signedPct(-1.06) == "-1.1%")
        #expect(TrScorecardFormat.signedPct(0) == "0.0%")
        #expect(TrScorecardFormat.signedPct(nil) == "—")
        #expect(TrScorecardFormat.pct(-12.34) == "-12.3%")
        #expect(TrScorecardFormat.pct(nil) == "—")
        #expect(TrScorecardFormat.points(4.8) == "+4.8 pts")
        #expect(TrScorecardFormat.points(nil) == "—")
    }

    @Test func marketTypesAndBeats() {
        #expect(TrScorecardFormat.marketType("bull") == "Bull")
        #expect(TrScorecardFormat.marketType("chop") == "Choppy")
        #expect(TrScorecardFormat.marketType("bear") == "Bear")
        #expect(TrScorecardFormat.beatLabel(true) == "Yes")
        #expect(TrScorecardFormat.beatLabel(false) == "No")
        #expect(TrScorecardFormat.beatLabel(nil) == "—")
    }

    @Test func randomBaselineLines() {
        #expect(TrScorecardFormat.randomLines(candidates: 71.26, universe: 88) == [
            "Better than 71.3% of random picks from the AI's own shortlist",
            "Better than 88.0% of random picks from all names it considered",
        ])
        #expect(TrScorecardFormat.randomLines(candidates: nil, universe: nil).isEmpty)
    }
}

import Foundation
import Testing
@testable import IntelliStock

/// The three "why it moved" payloads, from the contract's own examples
/// (docs/superpowers/specs/2026-11-14-why-it-moved-design.md, section API),
/// plus the null shapes the contract allows. Nonisolated: `DataStub`
/// handlers build responses from it off the main actor.
nonisolated enum AnalysisFixtures {
    static let settings = #"""
    {"model_id": "abc", "model_name": "Opus", "sigma": 2.5, "min_move_pct": 3.0,
     "models": [{"id": "abc", "name": "Opus", "provider": "claude-cli", "model": "claude-opus-5-5"}]}
    """#

    /// The contract's example, with a second bar (the first of a series,
    /// null parts) and an unexplained event (every explained field null).
    static let moves = #"""
    {"symbol": "NVDA", "range": "3M", "sector": "technology", "sector_etf": "XLK",
     "beta": 1.62, "model_configured": true, "pending": 0,
     "bars": [{"date": "2026-11-12", "close": 210.0, "volume": 150000000,
               "ret_pct": null, "market_pct": null, "sector_pct": null,
               "specific_pct": null, "flagged": false},
              {"date": "2026-11-13", "close": 201.4, "volume": 190000000,
               "ret_pct": -4.1, "market_pct": -1.2, "sector_pct": -0.9,
               "specific_pct": -2.0, "flagged": false}],
     "events": [{"id": "NVDA|2026-10-02", "symbol": "NVDA", "date": "2026-10-02",
                 "close": 188.1, "ret_pct": -7.9, "market_pct": -0.8,
                 "sector_pct": -1.1, "specific_pct": -6.0,
                 "status": "explained", "category": "analyst",
                 "category_label": "Analysts", "headline": "Downgrade on export limits",
                 "explanation": "…", "confidence": "medium",
                 "sources": [{"title": "…", "url": "https://example.com/a", "source": "Benzinga",
                              "published_at": "2026-10-02T12:31:00Z"}],
                 "model_name": "Opus", "explained_at": "2026-11-14T20:00:00Z",
                 "error": null},
                {"id": "NVDA|2026-09-15", "symbol": "NVDA", "date": "2026-09-15",
                 "close": 170.2, "ret_pct": 5.1, "market_pct": 0.4,
                 "sector_pct": 0.6, "specific_pct": 4.1,
                 "status": "unexplained", "category": null,
                 "category_label": null, "headline": null,
                 "explanation": null, "confidence": null, "sources": [],
                 "model_name": null, "explained_at": null, "error": null}],
     "categories": [{"key": "earnings", "label": "Earnings"}]}
    """#

    static let portfolio = #"""
    {"as_of": "2026-11-13", "spy_pct": -0.7,
     "totals": {"contribution_pct": -1.31, "market_pct": -0.82,
                "sector_pct": -0.2, "specific_pct": -0.29},
     "holdings": [{"symbol": "TQQQ", "weight_pct": 41.2, "ret_pct": -2.1,
                   "contribution_pct": -0.87, "market_pct": -0.6,
                   "sector_pct": 0.0, "specific_pct": -0.27, "flagged": false,
                   "event": null}]}
    """#

    static func json(_ text: String) throws -> JSON {
        try JSON(data: Data(text.utf8))
    }

    /// An event row for model tests.
    static func event(
        _ date: String,
        status: String,
        category: String? = nil,
        label: String? = nil,
        ret: Double = -5,
        symbol: String = "NVDA"
    ) -> String {
        let explained = status == "explained"
        func q(_ s: String?) -> String { s.map { "\"\($0)\"" } ?? "null" }
        return """
        {"id": "\(symbol)|\(date)", "symbol": "\(symbol)", "date": "\(date)", "close": 100.0,
         "ret_pct": \(ret), "market_pct": -1.0, "sector_pct": -0.5, "specific_pct": \(ret + 1.5),
         "status": "\(status)", "category": \(q(explained ? category : nil)),
         "category_label": \(q(explained ? label : nil)),
         "headline": \(q(explained ? "Headline \(date)" : nil)), "explanation": \(q(explained ? "Because." : nil)),
         "confidence": \(q(explained ? "high" : nil)), "sources": [],
         "model_name": \(q(explained ? "Opus" : nil)), "explained_at": null,
         "error": \(q(status == "failed" ? "timeout" : nil))}
        """
    }

    /// A moves payload over consecutive bars `2026-10-01` … with `events`.
    static func moves(symbol: String = "NVDA", pending: Int = 0, configured: Bool = true, bars: Int = 10, events: [String]) -> String {
        let barRows = (0..<bars).map { i in
            let day = String(format: "2026-10-%02d", i + 1)
            return #"{"date": "\#(day)", "close": \#(100 + i), "volume": 1000, "ret_pct": \#(i == 0 ? "null" : "1.0"), "market_pct": 0.2, "sector_pct": 0.1, "specific_pct": 0.7, "flagged": false}"#
        }
        return """
        {"symbol": "\(symbol)", "range": "3M", "sector": "technology", "sector_etf": "XLK", "beta": 1.2,
         "model_configured": \(configured), "pending": \(pending),
         "bars": [\(barRows.joined(separator: ","))],
         "events": [\(events.joined(separator: ","))],
         "categories": [{"key": "earnings", "label": "Earnings"}, {"key": "analyst", "label": "Analysts"},
                        {"key": "macro", "label": "Macro"}]}
        """
    }
}

struct AnalysisDecodingTests {
    @Test func settingsDecodeTheContractExample() throws {
        let s = AnalysisSettings(json: try AnalysisFixtures.json(AnalysisFixtures.settings))
        #expect(s.modelId == "abc")
        #expect(s.modelName == "Opus")
        #expect(s.sigma == 2.5)
        #expect(s.minMovePct == 3.0)
        #expect(s.models == [AnalysisModelOption(id: "abc", name: "Opus", provider: "claude-cli", model: "claude-opus-5-5")])
        #expect(s.modelLabel == "Opus")
    }

    @Test func settingsWithNoModelReadAsNone() throws {
        let s = AnalysisSettings(json: try AnalysisFixtures.json(#"{"model_id": null, "model_name": null, "sigma": 4, "min_move_pct": 1.5, "models": []}"#))
        #expect(s.modelId == nil)
        #expect(s.modelLabel == "None")
        #expect(s.sigma == 4)
        #expect(s.minMovePct == 1.5)
        // Missing thresholds fall back to the documented defaults.
        let bare = AnalysisSettings(json: try AnalysisFixtures.json("{}"))
        #expect(bare.sigma == 2.5 && bare.minMovePct == 3.0 && bare.models.isEmpty)
    }

    @Test func movesDecodeTheContractExample() throws {
        let m = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves))
        #expect(m.symbol == "NVDA")
        #expect(m.range == "3M")
        #expect(m.sector == "technology")
        #expect(m.sectorEtf == "XLK")
        #expect(m.beta == 1.62)
        #expect(m.modelConfigured)
        #expect(m.pending == 0)
        #expect(m.categories == [MoveCategory(key: "earnings", label: "Earnings")])

        #expect(m.bars.count == 2)
        let first = m.bars[0]
        #expect(first.date == "2026-11-12" && first.close == 210.0)
        #expect(first.retPct == nil && first.marketPct == nil && first.sectorPct == nil && first.specificPct == nil)
        let bar = m.bars[1]
        #expect(bar.date == "2026-11-13")
        #expect(bar.close == 201.4)
        #expect(bar.volume == 190_000_000)
        #expect(bar.retPct == -4.1 && bar.marketPct == -1.2 && bar.sectorPct == -0.9 && bar.specificPct == -2.0)
        #expect(!bar.flagged)

        let e = m.events[0]
        #expect(e.id == "NVDA|2026-10-02")
        #expect(e.symbol == "NVDA" && e.date == "2026-10-02" && e.close == 188.1)
        #expect(e.retPct == -7.9 && e.marketPct == -0.8 && e.sectorPct == -1.1 && e.specificPct == -6.0)
        #expect(e.status == .explained)
        #expect(e.category == "analyst" && e.categoryLabel == "Analysts")
        #expect(e.headline == "Downgrade on export limits")
        #expect(e.explanation == "…")
        #expect(e.confidence == "medium")
        #expect(e.sources.count == 1)
        #expect(e.sources[0].title == "…")
        #expect(e.sources[0].url == "https://example.com/a")
        #expect(e.sources[0].source == "Benzinga")
        #expect(e.sources[0].publishedAt == "2026-10-02T12:31:00Z")
        #expect(e.modelName == "Opus")
        #expect(e.explainedAt == "2026-11-14T20:00:00Z")
        #expect(e.error == nil)
    }

    @Test func anUnexplainedEventHasEveryExplainedFieldNull() throws {
        let m = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves))
        let e = m.events[1]
        #expect(e.status == .unexplained)
        #expect(e.needsExplaining && !e.isExplained)
        #expect(e.category == nil && e.categoryLabel == nil)
        #expect(e.headline == nil && e.explanation == nil && e.confidence == nil)
        #expect(e.modelName == nil && e.explainedAt == nil && e.error == nil)
        #expect(e.sources.isEmpty)
        // The numbers are still there.
        #expect(e.retPct == 5.1 && e.specificPct == 4.1)
        #expect(m.unexplainedCount == 1)
    }

    @Test func statusesAndNullableProfile() throws {
        #expect(MoveEventStatus(raw: "pending") == .pending)
        #expect(MoveEventStatus(raw: "FAILED") == .failed)
        #expect(MoveEventStatus(raw: "explained") == .explained)
        // Unknown or missing never hides an event.
        #expect(MoveEventStatus(raw: "queued") == .unexplained)
        #expect(MoveEventStatus(raw: nil) == .unexplained)

        let m = MoveAnalysis(json: try AnalysisFixtures.json(#"{"symbol": "ZZ", "range": "1M", "sector": null, "sector_etf": null, "beta": null, "model_configured": false, "pending": 2, "bars": [], "events": [], "categories": []}"#))
        #expect(m.sector == nil && m.sectorEtf == nil && m.beta == nil)
        #expect(!m.modelConfigured && m.pending == 2)
        #expect(AnalysisFormat.profileLine(m) == "")
    }

    @Test func eventWithoutAnIdFallsBackToSymbolAndDate() throws {
        let e = MoveEvent(json: try AnalysisFixtures.json(#"{"symbol": "AAPL", "date": "2026-01-05", "status": "pending"}"#))
        #expect(e.id == "AAPL|2026-01-05")
        #expect(e.status == .pending && !e.needsExplaining)
    }

    @Test func portfolioDecodesTheContractExample() throws {
        let p = PortfolioMoves(json: try AnalysisFixtures.json(AnalysisFixtures.portfolio))
        #expect(p.asOf == "2026-11-13")
        #expect(p.spyPct == -0.7)
        let t = try #require(p.totals)
        #expect(t.contributionPct == -1.31 && t.marketPct == -0.82 && t.sectorPct == -0.2 && t.specificPct == -0.29)
        #expect(p.holdings.count == 1)
        let h = p.holdings[0]
        #expect(h.symbol == "TQQQ" && h.id == "TQQQ")
        #expect(h.weightPct == 41.2 && h.retPct == -2.1 && h.contributionPct == -0.87)
        #expect(h.marketPct == -0.6 && h.sectorPct == 0.0 && h.specificPct == -0.27)
        #expect(!h.flagged)
        #expect(h.event == nil)
        // The three parts are contributions and add up to it.
        #expect(close((h.marketPct ?? 0) + (h.sectorPct ?? 0) + (h.specificPct ?? 0), h.contributionPct ?? 0, 1e-9))
    }

    @Test func portfolioWithNoPositionsAndAFlaggedHolding() throws {
        let empty = PortfolioMoves(json: try AnalysisFixtures.json(#"{"as_of": null, "spy_pct": null, "totals": null, "holdings": []}"#))
        #expect(empty.asOf == nil && empty.spyPct == nil && empty.totals == nil && empty.holdings.isEmpty)

        let flagged = PortfolioMoves(json: try AnalysisFixtures.json("""
        {"as_of": "2026-11-13", "spy_pct": 0.1, "totals": {"contribution_pct": 1.0, "market_pct": 0.1, "sector_pct": 0.1, "specific_pct": 0.8},
         "holdings": [{"symbol": "NVDA", "weight_pct": 20, "ret_pct": 5.0, "contribution_pct": 1.0, "market_pct": 0.1,
                       "sector_pct": 0.1, "specific_pct": 0.8, "flagged": true,
                       "event": \(AnalysisFixtures.event("2026-11-13", status: "explained", category: "earnings", label: "Earnings"))}]}
        """))
        let h = try #require(flagged.holdings.first)
        #expect(h.flagged)
        #expect(h.event?.category == "earnings")
        #expect(h.event?.status == .explained)
    }
}

struct AnalysisSettingsChangeTests {
    @Test func bodySendsOnlyWhatChanged() {
        #expect(AnalysisSettingsChange(sigma: 3.0).body == ["sigma": 3.0])
        #expect(AnalysisSettingsChange(modelId: .some(nil)).body == ["model_id": nil])
        #expect(AnalysisSettingsChange(modelId: .some("abc")).body == ["model_id": "abc"])
        #expect(AnalysisSettingsChange(minMovePct: 4.5).body == ["min_move_pct": 4.5])
        #expect(AnalysisSettingsChange().isEmpty)
        let merged = AnalysisSettingsChange(sigma: 3.0).merging(AnalysisSettingsChange(sigma: 3.5, minMovePct: 2))
        #expect(merged == AnalysisSettingsChange(sigma: 3.5, minMovePct: 2))
    }

    @Test func noModelEncodesAsJSONNull() throws {
        let data = try AnalysisSettingsChange(modelId: .some(nil)).body.data()
        #expect(String(decoding: data, as: UTF8.self) == #"{"model_id":null}"#)
    }
}

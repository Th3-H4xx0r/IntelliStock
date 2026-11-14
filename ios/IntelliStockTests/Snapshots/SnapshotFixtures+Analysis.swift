import Foundation
@testable import IntelliStock

/// Snapshot stub routes for AI analysis (`/analysis/*`), chained from
/// `SnapshotFixtures.respond`. Return nil for paths this area doesn't serve.
///
/// Moves are deterministic per symbol: weekday sessions counted back from
/// today, a gentle wiggle, and seven flagged days covering every marker
/// style (company, street and macro causes, a market day, no catalyst, one
/// unexplained and one failed). `ZZZZ` answers 404. The portfolio is Alpaca
/// Live's five holdings, weighted by their dashboard values.
nonisolated extension SnapshotFixtures {
    static func analysisResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        guard parts.first == "analysis", parts.count >= 2 else { return nil }
        switch parts[1] {
        case "settings":
            return (200, json(analysisSettings(modelId: "m-opus")))
        case "moves":
            let symbol = (query["symbol"] ?? "NVDA").uppercased()
            if symbol == "ZZZZ" { return (404, #"{"detail":"No price history for ZZZZ"}"#) }
            return (200, json(analysisMoves(symbol: symbol, range: query["range"] ?? "3M", now: now)))
        case "portfolio":
            return (200, json(analysisPortfolio(id: query["brokerage_id"] ?? "", now: now)))
        default:
            return nil
        }
    }

    /// Routes a screen's own stub answers first: no model chosen
    /// (`modelConfigured: false`), explanations still running (`pending`),
    /// or a server that predates the feature (`oldServer`: FastAPI's bare
    /// 404 on every `/analysis` route).
    static func analysisOverride(_ request: URLRequest, modelConfigured: Bool = true, pending: Bool = false, oldServer: Bool = false) -> (status: Int, body: String)? {
        let parts = request.path.split(separator: "/").map(String.init)
        let query = request.queryItems
        if oldServer, parts.first == "analysis" {
            return (404, #"{"detail":"Not Found"}"#)
        }
        if parts == ["analysis", "moves"] {
            let symbol = (query["symbol"] ?? "NVDA").uppercased()
            // The shared route answers the unknown ticker's 404.
            if symbol == "ZZZZ" { return nil }
            return (200, json(analysisMoves(symbol: symbol, range: query["range"] ?? "3M", now: Date(), modelConfigured: modelConfigured, pending: pending)))
        }
        if parts == ["analysis", "settings"], !modelConfigured {
            return (200, json(analysisSettings(modelId: nil)))
        }
        return nil
    }

    // MARK: Settings

    private static func analysisSettings(modelId: String?) -> [String: Any] {
        [
            "model_id": modelId ?? NSNull(),
            "model_name": modelId == nil ? NSNull() : "Opus",
            "sigma": 2.5,
            "min_move_pct": 3.0,
            "models": [
                ["id": "m-opus", "name": "Opus", "provider": "claude-cli", "model": "claude-opus-5-5"],
                ["id": "m-openrouter", "name": "OpenRouter Gemini", "provider": "openrouter", "model": "google/gemini-3-pro"],
            ],
        ]
    }

    // MARK: Moves

    private static let analysisCategories: [(String, String)] = [
        ("earnings", "Earnings"), ("guidance", "Guidance"), ("analyst", "Analysts"),
        ("deal_product", "Deals and products"), ("macro", "Macro"), ("sector_peer", "Sector peers"),
        ("regulatory_legal", "Regulatory and legal"), ("management", "Management"),
        ("capital_insider", "Capital and insiders"), ("market", "Market"), ("no_catalyst", "No catalyst found"),
    ]

    /// A flagged day `back` sessions before the last, with its move and cause.
    private struct AnalysisFixtureEvent {
        let back: Int
        let ret: Double
        let status: String
        var category: String?
        var headline: String?
        var explanation: String?
        var confidence: String?
        var sources: Int = 0
        var error: String?
    }

    private static let analysisFixtureEvents: [AnalysisFixtureEvent] = [
        AnalysisFixtureEvent(back: 3, ret: 8.4, status: "explained", category: "earnings",
                             headline: "Revenue beat and a raised outlook",
                             explanation: "Quarterly revenue came in 6% above estimates and the company raised next quarter's guidance. Coverage the morning after attributed the gap up to the outlook rather than the beat.",
                             confidence: "high", sources: 2),
        AnalysisFixtureEvent(back: 14, ret: -6.1, status: "explained", category: "analyst",
                             headline: "Downgrade on export limits",
                             explanation: "A large broker cut the stock to neutral, citing new export limits on its top products. Two other notes repeated the concern the same day.",
                             confidence: "medium", sources: 3),
        AnalysisFixtureEvent(back: 23, ret: 5.2, status: "unexplained"),
        AnalysisFixtureEvent(back: 27, ret: -4.8, status: "explained", category: "macro",
                             headline: "Hot inflation print hit growth stocks",
                             explanation: "Consumer prices rose faster than expected and rate-cut odds fell. Rate-sensitive growth names led the decline.",
                             confidence: "medium", sources: 1),
        AnalysisFixtureEvent(back: 36, ret: -3.9, status: "explained", category: "market",
                             headline: "A market-wide sell-off",
                             explanation: "Most of the move came from the market; the stock's own part was under half of it.",
                             confidence: nil),
        AnalysisFixtureEvent(back: 47, ret: -4.4, status: "failed", error: "The model did not answer within 60 seconds"),
        AnalysisFixtureEvent(back: 55, ret: 4.6, status: "explained", category: "no_catalyst",
                             headline: "No catalyst found",
                             explanation: "None of the articles published before the close explained the move.",
                             confidence: "low"),
    ]

    private static func analysisSessionCount(_ range: String) -> Int {
        switch range {
        case "1M": 21
        case "6M": 126
        case "1Y": 252
        default: 63
        }
    }

    /// The last `count` weekday dates up to `now`, oldest first.
    private static func analysisSessions(_ count: Int, now: Date) -> [String] {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = .current
        var day = cal.startOfDay(for: now)
        var out: [String] = []
        while out.count < count {
            let weekday = cal.component(.weekday, from: day)
            if weekday != 1, weekday != 7 {
                let c = cal.dateComponents([.year, .month, .day], from: day)
                out.append(String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!))
            }
            day = cal.date(byAdding: .day, value: -1, to: day)!
        }
        return out.reversed()
    }

    private static func analysisLastPrice(_ symbol: String) -> Double {
        switch symbol {
        case "GLD": 365.02
        case "BIL": 91.72
        case "GDX": 85.26
        case "XLE": 63.34
        case "AAPL": 188.15
        case "TQQQ": 92.40
        default: 201.40
        }
    }

    static func analysisMoves(symbol: String, range: String, now: Date, modelConfigured: Bool = true, pending: Bool = false) -> [String: Any] {
        let dates = analysisSessions(analysisSessionCount(range), now: now)
        let n = dates.count
        var byIndex: [Int: AnalysisFixtureEvent] = [:]
        for e in analysisFixtureEvents where e.back < n - 1 { byIndex[n - 1 - e.back] = e }

        // Daily returns: a small wave, the flagged days' moves on top.
        let scalarSum = symbol.unicodeScalars.reduce(0) { $0 + Int($1.value) }
        let seed = Double(scalarSum % 13)
        var rets: [Double] = []
        for i in 0..<n {
            let t = Double(i)
            let wave: Double = 0.9 * sin(t * 0.7 + seed)
            let ripple: Double = 0.5 * cos(t * 1.9 + seed / 2)
            rets.append(wave + ripple)
        }
        for (i, e) in byIndex { rets[i] = e.ret }
        // Prices end at the symbol's last price.
        var closes = [Double](repeating: 0, count: n)
        closes[n - 1] = analysisLastPrice(symbol)
        if n > 1 {
            for i in stride(from: n - 1, to: 0, by: -1) {
                closes[i - 1] = closes[i] / (1 + rets[i] / 100)
            }
        }

        func parts(_ i: Int) -> (market: Double, sector: Double, specific: Double) {
            let r = rets[i]
            if let e = byIndex[i] {
                if e.category == "market" { return (aRound2(r * 0.7), aRound2(r * 0.12), aRound2(r - aRound2(r * 0.7) - aRound2(r * 0.12))) }
                let m = aRound2(r * 0.12)
                let s = aRound2(r * 0.1)
                return (m, s, aRound2(r - m - s))
            }
            let m = aRound2(r * 0.55)
            let s = aRound2(r * 0.2)
            return (m, s, aRound2(r - m - s))
        }

        let bars: [[String: Any]] = (0..<n).map { i in
            if i == 0 {
                return ["date": dates[i], "close": aRound2(closes[i]), "volume": 41_000_000,
                        "ret_pct": NSNull(), "market_pct": NSNull(), "sector_pct": NSNull(),
                        "specific_pct": NSNull(), "flagged": false]
            }
            let p = parts(i)
            return ["date": dates[i], "close": aRound2(closes[i]), "volume": 38_000_000 + (i % 7) * 2_500_000,
                    "ret_pct": aRound2(rets[i]), "market_pct": p.market, "sector_pct": p.sector,
                    "specific_pct": p.specific, "flagged": byIndex[i] != nil]
        }

        let labels = Dictionary(uniqueKeysWithValues: analysisCategories)
        var pendingCount = 0
        let events: [[String: Any]] = byIndex.keys.sorted(by: >).map { i in
            let e = byIndex[i]!
            let p = parts(i)
            // While explaining, the unexplained day is in flight.
            var status = e.status
            if pending, status == "unexplained" {
                status = "pending"
                pendingCount += 1
            }
            let explained = status == "explained"
            let sources: [[String: Any]] = (0..<(explained ? e.sources : 0)).map { k in
                let outlets = ["Reuters", "Benzinga", "MarketWatch"]
                return ["title": [
                    "\(symbol) jumps as outlook tops forecasts",
                    "Broker turns cautious on \(symbol) after export rules",
                    "What the inflation report means for tech",
                ][min(k, 2)],
                "url": "https://example.com/news/\(symbol.lowercased())-\(i)-\(k)",
                "source": outlets[k % outlets.count],
                "published_at": "\(dates[i])T13:\(String(format: "%02d", 10 + k * 7)):00Z"]
            }
            func orNull(_ v: String?) -> Any { v.map { $0 as Any } ?? NSNull() }
            return [
                "id": "\(symbol)|\(dates[i])", "symbol": symbol, "date": dates[i],
                "close": aRound2(closes[i]), "ret_pct": aRound2(rets[i]),
                "market_pct": p.market, "sector_pct": p.sector, "specific_pct": p.specific,
                "status": status,
                "category": explained ? orNull(e.category) : NSNull(),
                "category_label": explained ? orNull(e.category.flatMap { labels[$0] }) : NSNull(),
                "headline": explained ? orNull(e.headline) : NSNull(),
                "explanation": explained ? orNull(e.explanation) : NSNull(),
                "confidence": explained ? orNull(e.confidence) : NSNull(),
                "sources": sources,
                "model_name": explained && e.category != "market" ? "Opus" : NSNull(),
                "explained_at": explained ? aIso(now.addingTimeInterval(-Double(e.back + 1) * 3_600)) : NSNull(),
                "error": status == "failed" ? orNull(e.error) : NSNull(),
            ]
        }

        return [
            "symbol": symbol, "range": range,
            // A real underscore key, so the label shows as words.
            "sector": symbol == "GLD" ? "precious_metals" : "technology",
            "sector_etf": symbol == "GLD" ? NSNull() : "XLK",
            "beta": symbol == "GLD" ? 0.12 : 1.62,
            "model_configured": modelConfigured,
            "pending": pendingCount,
            "bars": bars,
            "events": events,
            "categories": analysisCategories.map { ["key": $0.0, "label": $0.1] },
        ]
    }

    // MARK: Portfolio

    private static func analysisPortfolio(id: String, now: Date) -> [String: Any] {
        guard id == alpacaLiveId || id == paperTestId else {
            return ["as_of": NSNull(), "spy_pct": NSNull(), "totals": NSNull(), "holdings": [Any]()]
        }
        // (symbol, weight %, day %, market share, sector share)
        let rows: [(String, Double, Double, Double, Double)] = id == paperTestId
            ? [("AAPL", 22.8, -0.77, 0.8, 0.1)]
            : [
                ("GLD", 37.4, -0.89, 0.1, 0.6),
                ("GDX", 17.7, -0.85, 0.3, 0.6),
                ("XLE", 17.6, 1.34, 0.2, 0.7),
                ("AAPL", 14.8, -0.77, 0.8, 0.1),
                ("BIL", 10.3, 0.0, 0.0, 0.0),
            ]
        var holdings: [[String: Any]] = rows.map { s, w, r, ms, ss in
            let c = aRound2(w * r / 100)
            let m = aRound2(c * ms)
            let sec = aRound2(c * ss)
            return ["symbol": s, "weight_pct": w, "ret_pct": r, "contribution_pct": c,
                    "market_pct": m, "sector_pct": sec, "specific_pct": aRound2(c - m - sec),
                    "flagged": false, "event": NSNull()]
        }
        // XLE stood out today.
        if let i = holdings.firstIndex(where: { ($0["symbol"] as? String) == "XLE" }) {
            holdings[i]["flagged"] = true
            holdings[i]["event"] = [
                "id": "XLE|today", "symbol": "XLE", "date": analysisSessions(1, now: now)[0],
                "close": 63.34, "ret_pct": 1.34, "market_pct": 0.27, "sector_pct": 0.94, "specific_pct": 0.13,
                "status": "explained", "category": "sector_peer", "category_label": "Sector peers",
                "headline": "Oil majors rallied on a supply cut", "explanation": "Crude rose 3% after an output cut.",
                "confidence": "medium", "sources": [Any](), "model_name": "Opus",
                "explained_at": aIso(now.addingTimeInterval(-1_800)), "error": NSNull(),
            ] as [String: Any]
        }
        holdings.sort { abs(($0["contribution_pct"] as? Double) ?? 0) > abs(($1["contribution_pct"] as? Double) ?? 0) }
        func sum(_ key: String) -> Double { aRound2(holdings.reduce(0) { $0 + (($1[key] as? Double) ?? 0) }) }
        return [
            "as_of": analysisSessions(1, now: now)[0],
            "spy_pct": -0.7,
            "totals": ["contribution_pct": sum("contribution_pct"), "market_pct": sum("market_pct"),
                       "sector_pct": sum("sector_pct"), "specific_pct": sum("specific_pct")],
            "holdings": holdings,
        ]
    }
}

nonisolated private func aRound2(_ v: Double) -> Double { (v * 100).rounded() / 100 }

nonisolated private func aIso(_ date: Date) -> String { ISO8601DateFormatter().string(from: date) }

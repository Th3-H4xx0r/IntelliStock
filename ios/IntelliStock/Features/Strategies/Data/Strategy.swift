import Foundation

// Plain immutable models for the Strategies feature, ported from
// features/strategies/data/models/strategy.dart.

// MARK: - SubStrategy

/// A single sub-strategy entry inside a `Strategy`.
nonisolated struct SubStrategy: Hashable, Sendable {
    /// Strategy type name (e.g. "graph_nexus_analysis").
    let strategy: String
    let executionPosition: Int
    /// "pre" | "entry" | "exit" | "post"
    let decisionPhase: String
    let weight: Double?
    let executionScope: String?
    /// Merged conditions + config map (all non-null/non-empty entries;
    /// config wins over a legacy condition with the same key).
    let config: [String: JSON]

    init(json j: JSON, fallbackPosition: Int = 0) {
        var merged: [String: JSON] = [:]
        for (k, v) in j["conditions"].objectValue where !v.isNull && v != "" {
            merged[k] = v
        }
        for (k, v) in j["config"].objectValue where !v.isNull && v != "" {
            merged[k] = v
        }
        strategy = j["strategy"].or(j["type"]).stringOr("")
        executionPosition = j["execution_position"].int ?? fallbackPosition
        decisionPhase = j["decision_phase"].stringOr("pre")
        weight = j["weight"].double
        executionScope = j["execution_scope"].string
        config = merged
    }
}

// MARK: - Strategy

/// Full strategy object returned by `GET /strategies/:id`.
nonisolated struct Strategy: Hashable, Sendable, Identifiable {
    let id: Int
    let name: String
    let description: String?
    let strategies: [SubStrategy]

    init(json j: JSON) {
        var subs: [SubStrategy] = []
        for (i, raw) in j["strategies"].arrayValue.enumerated() where raw.isObject {
            subs.append(SubStrategy(json: raw, fallbackPosition: i))
        }
        id = j["id"].intOr(0)
        name = j["name"].stringOr("")
        description = j["description"].string
        strategies = subs
    }
}

// MARK: - StrategyListRow

/// A merged row for the strategies list, combining API fields with
/// client-side best-backtest stats (mirrors the Vue `enrichedStrategies`).
nonisolated struct StrategyListRow: Hashable, Sendable, Identifiable {
    let id: Int
    let name: String
    let subCount: Int
    /// Instance IDs that reference this strategy.
    let instancesUsing: [String]
    let runCount: Int
    let bestPnl: Double?
    /// Backtest ID with the best absolute P&L.
    let bestPnlBid: String?
    let bestPct: Double?
    /// Backtest ID with the best P&L%.
    let bestPctBid: String?
    /// Top-5 rank (1–5), or nil if not in the top 5.
    let rank: Int?
    /// Sub-strategy type names (for pills).
    let subStrategyNames: [String]

    var isTop5: Bool { rank != nil }

    /// Build a row from a raw strategy JSON map + pre-computed best-backtest
    /// stats from agent results and the top-5 lists.
    init(
        strategyJson j: JSON,
        bestPnl: Double? = nil,
        bestPnlBid: String? = nil,
        bestPct: Double? = nil,
        bestPctBid: String? = nil,
        runCount: Int = 0,
        rank: Int? = nil
    ) {
        let rawSubs = j["strategies"].arrayValue
        var subNames: [String] = []
        for s in rawSubs where s.isObject {
            let name = s["strategy"].or(s["type"]).stringOr("")
            if !name.isEmpty { subNames.append(name) }
        }
        id = j["id"].intOr(0)
        name = j["name"].stringOr("")
        subCount = rawSubs.count
        instancesUsing = j["instances_using"].stringElements
        self.runCount = runCount
        self.bestPnl = bestPnl
        self.bestPnlBid = bestPnlBid
        self.bestPct = bestPct
        self.bestPctBid = bestPctBid
        self.rank = rank
        subStrategyNames = subNames
    }
}

// MARK: - AgentResult

/// Lightweight model for a single entry from `GET /agent/results`.
nonisolated struct AgentResult: Hashable, Sendable {
    let backtestId: String
    let strategyId: Int?
    let overallProfit: Double?
    let pnlPercent: Double?
    let stocksUsed: [String]
    let startDate: String?
    let endDate: String?
    let createdAt: String?

    init(json j: JSON) {
        var stocks: [String] = []
        for s in j["stocks_used"].arrayValue {
            if case .string(let str) = s, !str.isEmpty { stocks.append(str) }
        }
        backtestId = j["backtest_id"].or(j["id"]).stringOr("")
        strategyId = j["strategy_id"].int
        overallProfit = strategyToDouble(j["overall_profit"])
        pnlPercent = strategyToDouble(j["pnl_percent"])
        stocksUsed = stocks
        startDate = j["start_date"].string
        endDate = j["end_date"].string
        createdAt = j["created_at"].string
    }
}

// MARK: - BestPerStrategy

/// The best backtest stats for a single strategy (client-computed).
nonisolated struct BestPerStrategy: Hashable, Sendable {
    let strategyId: Int
    var bestPnl: Double
    var bestPnlBid: String
    var bestPct: Double
    var bestPctBid: String
    var count: Int
    var latest: String?

    /// Fold an additional `AgentResult` into this accumulator.
    mutating func fold(_ r: AgentResult) {
        let profit = r.overallProfit ?? 0
        let pct = r.pnlPercent ?? 0
        if profit > bestPnl {
            bestPnl = profit
            bestPnlBid = r.backtestId
        }
        if pct > bestPct {
            bestPct = pct
            bestPctBid = r.backtestId
        }
        count += 1
        let ts = r.createdAt ?? ""
        if latest == nil || dartCompare(ts, latest!) > 0 { latest = ts }
    }
}

/// `_toDouble`: a number's `toDouble()`, else `double.tryParse(v.toString())`.
nonisolated func strategyToDouble(_ v: JSON) -> Double? {
    if v.isNull { return nil }
    if let d = v.double { return d }
    return JSON.parseDouble(v.dartDescription)
}

/// Dart `String.compareTo`: UTF-16 code-unit order (negative, 0, positive).
nonisolated func dartCompare(_ a: String, _ b: String) -> Int {
    let lhs = Array(a.utf16)
    let rhs = Array(b.utf16)
    for (x, y) in zip(lhs, rhs) where x != y {
        return x < y ? -1 : 1
    }
    return lhs.count == rhs.count ? 0 : (lhs.count < rhs.count ? -1 : 1)
}

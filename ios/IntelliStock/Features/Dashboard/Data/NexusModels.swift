import Foundation

// Ported from features/dashboard/data/nexus_models.dart: the read-only nexus
// strategy telemetry the dashboard's Strategy section shows.

/// A market trend tracked by the nexus strategy (GraphNexusMarketTrends).
nonisolated struct MarketTrend: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    /// active | weakening | ended
    let status: String
    /// bullish | bearish
    let direction: String
    /// 0..1
    let strength: Double
    let tickers: [String]
    let sectors: [String]
    let reversalCount: Int
    let endedAt: String?

    init(json: JSON) {
        id = json["id"].stringOr("")
        name = json["name"].stringOr("")
        status = json["status"].stringOr("active")
        direction = json["direction"].stringOr("bullish")
        strength = json["strength"].doubleOr(0)
        tickers = json["affected_tickers"].stringElements
        sectors = json["affected_sectors"].stringElements
        reversalCount = json["reversal_articles"].arrayValue.count
        endedAt = json["ended_at"].string ?? json["end_date"].string ?? json["last_confirmed_date"].string
    }

    var bullish: Bool { direction.lowercased() == "bullish" }
    var hasReversal: Bool { reversalCount > 0 || status.lowercased() == "weakening" }
}

/// Active + recently-ended trends for one account (two endpoint calls).
nonisolated struct NexusTrendsView: Hashable, Sendable {
    let active: [MarketTrend]
    let recentlyEnded: [MarketTrend]

    var reversalWatch: [MarketTrend] { active.filter(\.hasReversal) }
    var isEmpty: Bool { active.isEmpty && recentlyEnded.isEmpty }
}

/// A pending buy candidate in the strategy's backfill queue.
nonisolated struct BackfillItem: Hashable, Sendable {
    let ticker: String
    let score: Double
    let nPaths: Int
    let source: String
    let priority: Bool

    init(json: JSON) {
        ticker = json["ticker"].stringOr("").uppercased()
        score = json["score"].doubleOr(0)
        nPaths = json["n_paths"].intOr(0)
        source = json["source"].stringOr("")
        priority = json["priority"].boolValue ?? false
    }
}

/// A stock the discover engine surfaced (GraphNexusDiscoveredStocks).
nonisolated struct DiscoveredStock: Hashable, Sendable {
    let ticker: String
    let source: String
    let sourceTicker: String?
    let discoveredAt: String?

    init(json: JSON) {
        ticker = json["ticker"].stringOr("").uppercased()
        source = json["source"].stringOr("")
        sourceTicker = json["source_ticker"].string
        discoveredAt = json["discovered_at"].string ?? json["discovered_date"].string
    }
}

/// The bot's persisted rationale for a symbol (GraphNexusTradeContexts).
nonisolated struct TradeRationale: Hashable, Sendable {
    let symbol: String
    let reason: String
    let eventType: String
    let actionIntent: String
    let score: Double

    init(json: JSON) {
        symbol = json["symbol"].stringOr("").uppercased()
        reason = json["reason"].stringOr("")
        eventType = json["dominant_event_type"].stringOr("")
        actionIntent = json["action_intent"].stringOr("")
        score = json["score"].doubleOr(0)
    }
}

/// One realized signal outcome (for the scorecard's recent list).
nonisolated struct OutcomeRow: Hashable, Sendable {
    let symbol: String
    let actionIntent: String
    let latestReturn: Double
    let eventType: String
    let entryDate: String

    init(json: JSON) {
        symbol = json["symbol"].stringOr("").uppercased()
        actionIntent = json["action_intent"].stringOr("")
        latestReturn = json["latest_return"].doubleOr(0)
        eventType = json["dominant_event_type"].stringOr("")
        entryDate = json["entry_date"].stringOr("")
    }

    var isLong: Bool {
        actionIntent.lowercased().contains("buy") || actionIntent.lowercased().contains("long")
    }

    var correct: Bool { (isLong && latestReturn > 0) || (!isLong && latestReturn < 0) }
}

/// Aggregate signal->outcome scorecard (`GET /brokerages/{id}/nexus-outcomes`).
nonisolated struct OutcomeStats: Hashable, Sendable {
    /// 0..1
    let hitRate: Double
    let n: Int
    let nCorrect: Int
    let avgReturn: Double
    let recent: [OutcomeRow]

    init(json: JSON) {
        hitRate = json["hit_rate"].doubleOr(0)
        n = json["n"].intOr(0)
        nCorrect = json["n_correct"].intOr(0)
        avgReturn = json["avg_return"].doubleOr(0)
        recent = json["recent"].objectElements.map(OutcomeRow.init(json:))
    }

    var isEmpty: Bool { n == 0 }
}

/// One newest watchlist entry. The strategy persists the bar it was first
/// seen and the price at that time (no return field is stored).
nonisolated struct WatchlistEntry: Hashable, Sendable {
    let symbol: String
    let firstSeenBar: Int
    let firstSeenPrice: Double

    init(json: JSON) {
        symbol = json["symbol"].stringOr("").uppercased()
        firstSeenBar = json["first_seen_bar"].intOr(0)
        firstSeenPrice = json["first_seen_price"].doubleOr(0)
    }
}

/// Momentum watchlist summary (`GET /brokerages/{id}/momentum-watchlist`).
nonisolated struct WatchlistSummary: Hashable, Sendable {
    let count: Int
    let newest: [WatchlistEntry]

    init(json: JSON) {
        count = json["count"].intOr(0)
        newest = json["newest"].objectElements.map(WatchlistEntry.init(json:))
    }

    var isEmpty: Bool { count == 0 && newest.isEmpty }
}

import Foundation

// Plain immutable models for the backtests feature, ported from
// features/backtests/data/models/backtest.dart. Every init(json:) tolerates
// missing fields (they read as nil), as the Dart factories did.
//
// Dart `num?` fields are `Num?` so `toString()` keeps "5" vs "5.0"; Dart
// `dynamic` fields are `JSON`; `SubStrategy` is `BacktestSubStrategy` here
// (the strategies feature owns `SubStrategy`).

nonisolated struct BacktestRow: Hashable, Sendable, Identifiable {
    let id: String
    let instanceId: String?
    let status: String?
    let stocks: [String]
    let startDate: String?
    let endDate: String?
    /// Epoch or ISO.
    let completedAt: JSON
    let pnl: Num?
    let pnlPercent: Num?
    let timeElapsedSeconds: Num?

    init(json j: JSON) {
        id = j["id"].string ?? ""
        instanceId = j["instance_id"].or(j["instance"]).string
        status = j["status"].string
        stocks = backtestStringList(j["stocks"].or(j["tickers"]))
        startDate = j["start_date"].string
        endDate = j["end_date"].string
        completedAt = j["completed_at"]
        pnl = j["pnl"].lenientNum
        pnlPercent = j["pnl_percent"].lenientNum
        timeElapsedSeconds = j["time_elapsed_seconds"].lenientNum
    }
}

nonisolated struct BacktestStatus: Hashable, Sendable {
    let status: String?
    let progress: Num?
    let nexusLookback: NexusLookback?
    let timeElapsedSeconds: Num?

    init(json j: JSON) {
        status = j["status"].string
        progress = j["progress"].lenientNum
        nexusLookback = j["nexus_lookback"].isObject ? NexusLookback(json: j["nexus_lookback"]) : nil
        timeElapsedSeconds = j["time_elapsed_seconds"].lenientNum
    }
}

nonisolated struct NexusLookback: Hashable, Sendable {
    let current: Int
    let total: Int
    let currentDate: String?
    let startDate: String?
    let endDate: String?

    init(json j: JSON) {
        current = j["current"].intOr(0)
        total = j["total"].intOr(0)
        currentDate = j["current_date"].string
        startDate = j["start_date"].string
        endDate = j["end_date"].string
    }

    var fraction: Double { total > 0 ? Double(current) / Double(total) : 0 }
}

nonisolated struct BacktestSummary: Hashable, Sendable {
    let id: String?
    let status: String?
    let pnl: Num?
    let pnlPercent: Num?
    /// Crypto fee accounting {total_fees, total_volume, taker_rate}; nil for
    /// equity (commission-free) runs.
    let fees: [String: Num]?
    /// True when the fee was EMULATED (a venue other than the instance's
    /// brokerage).
    let feeEmulated: Bool?
    /// The venue whose taker fee was applied (id or label), when emulated.
    let feeVenue: String?
    /// The emulated-fee venue choice, so "rerun" can preserve it.
    let emulateFeeVenue: String?
    let portfolioStartValue: Num?
    let portfolioEndValue: Num?
    let totalTrades: Num?
    let totalBuys: Num?
    let totalSells: Num?
    let timeElapsedSeconds: Num?
    let winRatePercent: Num?
    let winningRoundTrips: Num?
    let losingRoundTrips: Num?
    let portfolioValueHigh: Num?
    let portfolioValueLow: Num?
    let roundTrips: Num?
    let pnlPerStock: [String: Num]?
    let pnlPercentPerStock: [String: Num]?
    let stockPriceChange: [String: Num]?
    let tickers: [String]
    let startDate: String?
    let endDate: String?
    let strategySchema: StrategySchema?
    let strategyId: String?
    let instanceId: String?
    let granularity: String?
    let initialCash: Num?
    // LLM pause
    let pauseReasonTag: String?
    let pauseProvider: String?
    let pauseModel: String?
    let pauseCallSite: String?
    let pauseAttempts: Num?
    let pauseBarTime: String?
    let pausedAt: JSON
    let pauseSample: String?
    // Round trips
    let totalRoundTripPnl: Num?
    let avgWinningRoundTrip: Num?
    let avgLosingRoundTrip: Num?

    init(json j: JSON) {
        id = j["id"].string
        status = j["status"].string
        pnl = j["pnl"].lenientNum
        pnlPercent = j["pnl_percent"].lenientNum
        fees = backtestNumMap(j["fees"])
        feeEmulated = j["fees"].isObject ? j["fees"]["emulated"].bool : nil
        feeVenue = j["fees"].isObject ? j["fees"]["venue"].string : nil
        emulateFeeVenue = j["emulate_fee_venue"].string
        portfolioStartValue = j["portfolio_start_value"].lenientNum
        portfolioEndValue = j["portfolio_end_value"].lenientNum
        totalTrades = j["total_trades"].lenientNum
        totalBuys = j["total_buys"].lenientNum
        totalSells = j["total_sells"].lenientNum
        timeElapsedSeconds = j["time_elapsed_seconds"].lenientNum
        winRatePercent = j["win_rate_percent"].lenientNum
        winningRoundTrips = j["winning_round_trips"].lenientNum
        losingRoundTrips = j["losing_round_trips"].lenientNum
        portfolioValueHigh = j["portfolio_value_high"].lenientNum
        portfolioValueLow = j["portfolio_value_low"].lenientNum
        roundTrips = j["round_trips"].lenientNum
        pnlPerStock = backtestNumMap(j["pnl_per_stock"])
        pnlPercentPerStock = backtestNumMap(j["pnl_percent_per_stock"])
        stockPriceChange = backtestChangePctMap(j["stock_price_change"])
        tickers = backtestStringList(j["tickers"])
        startDate = j["start_date"].string
        endDate = j["end_date"].string
        strategySchema = j["strategy_schema"].isObject ? StrategySchema(json: j["strategy_schema"]) : nil
        strategyId = j["strategy_id"].string
        instanceId = j["instance_id"].or(j["instance"]).string
        granularity = j["granularity"].string
        initialCash = j["initial_cash"].lenientNum
        pauseReasonTag = j["pause_reason_tag"].string
        pauseProvider = j["pause_provider"].string
        pauseModel = j["pause_model"].string
        pauseCallSite = j["pause_call_site"].string
        pauseAttempts = j["pause_attempts"].lenientNum
        pauseBarTime = j["pause_bar_time"].string
        pausedAt = j["paused_at"]
        pauseSample = j["pause_sample"].string
        totalRoundTripPnl = j["total_round_trip_pnl"].lenientNum
        avgWinningRoundTrip = j["avg_winning_round_trip"].lenientNum
        avgLosingRoundTrip = j["avg_losing_round_trip"].lenientNum
    }
}

nonisolated struct StrategySchema: Hashable, Sendable {
    let name: String?
    let strategies: [BacktestSubStrategy]

    init(json j: JSON) {
        name = j["name"].string
        strategies = j["strategies"].objectElements.map(BacktestSubStrategy.init(json:))
    }
}

/// Dart `SubStrategy` in backtest.dart.
nonisolated struct BacktestSubStrategy: Hashable, Sendable {
    let strategy: String?
    let weight: Num?
    let executionPosition: Num?
    let decisionPhase: String?
    let executionScope: String?
    let conditions: [String: JSON]
    let config: [String: JSON]

    init(json j: JSON) {
        strategy = j["strategy"].string
        weight = j["weight"].lenientNum
        executionPosition = j["execution_position"].lenientNum
        decisionPhase = j["decision_phase"].string
        executionScope = j["execution_scope"].string
        conditions = j["conditions"].objectValue
        config = j["config"].objectValue
    }
}

nonisolated struct LlmCost: Hashable, Sendable {
    let totalCostUsd: Num?
    let totalCalls: Num?
    let okCalls: Num?
    let failedCalls: Num?
    let totalInputTokens: Num?
    let totalOutputTokens: Num?
    let totalReasoningTokens: Num?
    let byModel: [LlmCostRow]
    let byCallSite: [LlmCostRow]
    let byProvider: [LlmCostRow]

    init(json j: JSON) {
        totalCostUsd = j["total_cost_usd"].lenientNum
        totalCalls = j["total_calls"].lenientNum
        okCalls = j["ok_calls"].lenientNum
        failedCalls = j["failed_calls"].lenientNum
        totalInputTokens = j["total_input_tokens"].lenientNum
        totalOutputTokens = j["total_output_tokens"].lenientNum
        totalReasoningTokens = j["total_reasoning_tokens"].lenientNum
        byModel = j["by_model"].objectElements.map(LlmCostRow.init(json:))
        byCallSite = j["by_call_site"].objectElements.map(LlmCostRow.init(json:))
        byProvider = j["by_provider"].objectElements.map(LlmCostRow.init(json:))
    }
}

nonisolated struct LlmCostRow: Hashable, Sendable {
    let key: String
    let costUsd: Num?

    init(json j: JSON) {
        key = j["key"].string ?? "?"
        costUsd = j["cost_usd"].lenientNum
    }
}

nonisolated struct PortfolioValuePoint: Hashable, Sendable {
    let timestamp: Date
    let value: Double

    init(timestamp: Date, value: Double) {
        self.timestamp = timestamp
        self.value = value
    }

    init(json j: JSON) {
        let raw = j["timestamp"].or(j["date"]).or(j["time"])
        let dt: Date
        if let n = raw.num {
            let v = n.double
            let ms = v > 1e12 ? n.int : Num.double(v * 1000).int
            dt = DartDateTime.fromMillisecondsSinceEpoch(ms)
        } else {
            dt = DartDateTime.tryParse(raw.string ?? "") ?? Date()
        }
        self.init(timestamp: dt, value: j["value"].doubleOr(0))
    }
}

nonisolated struct BacktestTrade: Hashable, Sendable {
    let ticker: String
    let action: String?
    let timestamp: Date?
    let price: Num?
    let shares: Num?
    let total: Num?
    let cashAfter: Num?

    init(json j: JSON) {
        ticker = j["ticker"].or(j["symbol"]).stringOr("")
        action = j["action"].string
        timestamp = DartDateTime.tryParse(j["timestamp"].string ?? "")
        price = j["price"].lenientNum
        shares = j["shares"].lenientNum
        total = j["total"].lenientNum
        cashAfter = j["cash_after"].lenientNum
    }
}

nonisolated struct BacktestDecision: Hashable, Sendable {
    let symbol: String?
    let timestamp: Date?
    /// int or null.
    let decision: JSON
    let action: String?
    let normalizedScore: Num?
    let finalReason: String?
    let overrideApplied: Bool?
    let preOverrideAction: String?
    let preOverrideDecision: JSON
    let primaryStrategy: String?
    let primaryActionIntent: String?
    let strategies: [DecisionStrategy]
    let postDecision: [PostDecision]
    let rawJson: [String: JSON]

    init(json j: JSON) {
        symbol = j["symbol"].string
        timestamp = DartDateTime.tryParse(j["timestamp"].string ?? "")
        decision = j["decision"]
        action = j["action"].string
        normalizedScore = j["normalized_score"].lenientNum
        finalReason = j["final_reason"].string
        overrideApplied = j["override_applied"].boolValue
        preOverrideAction = j["pre_override_action"].string
        preOverrideDecision = j["pre_override_decision"]
        primaryStrategy = j["primary_strategy"].string
        primaryActionIntent = j["primary_action_intent"].string
        strategies = j["strategies"].objectElements.map(DecisionStrategy.init(json:))
        postDecision = j["post_decision"].objectElements.map(PostDecision.init(json:))
        rawJson = j.objectValue
    }

    func decisionLabel() -> String {
        if let action, !action.isEmpty { return action.uppercased() }
        return backtestDecisionLabel(decision)
    }
}

nonisolated struct DecisionStrategy: Hashable, Sendable {
    let strategy: String?
    let decision: JSON
    let weight: Num?
    let actionIntent: String?
    let reason: String?

    init(json j: JSON) {
        strategy = j["strategy"].string
        decision = j["decision"]
        weight = j["weight"].lenientNum
        actionIntent = j["action_intent"].string
        reason = j["reason"].string
    }

    func decisionLabel() -> String { backtestDecisionLabel(decision) }
}

nonisolated struct PostDecision: Hashable, Sendable {
    let strategy: String?
    let decision: JSON
    let reason: String?

    init(json j: JSON) {
        strategy = j["strategy"].string
        decision = j["decision"]
        reason = j["reason"].string
    }
}

nonisolated struct BacktestPrice: Hashable, Sendable {
    let symbol: String
    let timestamp: Date
    let close: Double

    init(json j: JSON) {
        symbol = j["symbol"].stringOr("")
        timestamp = DartDateTime.tryParse(j["timestamp"].string ?? "") ?? Date()
        close = j["close"].doubleOr(0)
    }
}

nonisolated struct BacktestGraphData: Hashable, Sendable {
    let portfolioValueHistory: [PortfolioValuePoint]
    let backtestPrices: [BacktestPrice]
    let backtestTrades: [BacktestTrade]
    let backtestDecisions: [BacktestDecision]

    init(json j: JSON) {
        portfolioValueHistory = j["portfolio_value_history"].objectElements.map(PortfolioValuePoint.init(json:))
        backtestPrices = j["backtest_prices"].objectElements.map(BacktestPrice.init(json:))
        backtestTrades = j["backtest_trades"].objectElements.map(BacktestTrade.init(json:))
        backtestDecisions = j["backtest_decisions"].objectElements.map(BacktestDecision.init(json:))
    }
}

// MARK: - Playback

nonisolated struct PlaybackMetadata: Hashable, Sendable {
    let initialCash: Num?
    let extra: [String: JSON]

    init(initialCash: Num? = nil, extra: [String: JSON] = [:]) {
        self.initialCash = initialCash
        self.extra = extra
    }

    init(json j: JSON) {
        self.init(initialCash: j["initial_cash"].lenientNum, extra: j.objectValue)
    }
}

nonisolated struct PlaybackEvent: Hashable, Sendable, Identifiable {
    let id: String
    /// 'date' | 'strategy' | 'outcome' | 'decision' | 'portfolio'
    let type: String
    let label: String?
    let time: String?
    let name: String?
    let desc: String?
    let reason: String?
    let decision: String?
    let tickers: [String]
    let details: String?
    let buys: [PlaybackTrade]
    let sells: [PlaybackTrade]
    let portfolioValue: Num?
    let holdings: [PlaybackHolding]
    let date: String?
    let raw: [String: JSON]

    init(json j: JSON, index: Int) {
        id = "\(j["type"].dartDescription)_\(index)"
        type = j["type"].string ?? "unknown"
        label = j["label"].string
        time = j["time"].string
        name = j["name"].string
        desc = j["desc"].string
        reason = j["reason"].string
        decision = j["decision"].string
        tickers = backtestStringList(j["tickers"])
        details = j["details"].string
        buys = j["buys"].objectElements.map(PlaybackTrade.init(json:))
        sells = j["sells"].objectElements.map(PlaybackTrade.init(json:))
        portfolioValue = j["value"].lenientNum
        holdings = j["holdings"].objectElements.map(PlaybackHolding.init(json:))
        date = j["date"].string
        raw = j.objectValue
    }
}

nonisolated struct PlaybackTrade: Hashable, Sendable {
    let ticker: String?
    let qty: Num?
    let price: Num?
    let reason: String?

    init(json j: JSON) {
        ticker = j["ticker"].string
        qty = j["qty"].lenientNum
        price = j["price"].lenientNum
        reason = j["reason"].string
    }
}

nonisolated struct PlaybackHolding: Hashable, Sendable {
    let ticker: String
    let qty: Num?
    let avg: Num?
    let curr: Num?

    init(json j: JSON) {
        ticker = j["ticker"].or(j["symbol"]).stringOr("")
        qty = j["qty"].lenientNum
        avg = j["avg"].lenientNum
        curr = j["curr"].lenientNum
    }
}

nonisolated struct PlaybackData: Hashable, Sendable {
    let events: [PlaybackEvent]
    let metadata: PlaybackMetadata

    init(json j: JSON) {
        events = j["events"].objectElements.enumerated().map { PlaybackEvent(json: $0.element, index: $0.offset) }
        metadata = j["metadata"].isObject ? PlaybackMetadata(json: j["metadata"]) : PlaybackMetadata()
    }
}

nonisolated struct BacktestListResponse: Hashable, Sendable {
    let backtests: [BacktestRow]
    let total: Int
    let totalPages: Int
    let page: Int

    init(json j: JSON) {
        backtests = j["backtests"].objectElements.map(BacktestRow.init(json:))
        total = j["total"].intOr(0)
        totalPages = j["total_pages"].intOr(1)
        page = j["page"].intOr(1)
    }
}

// MARK: - Shared helpers (backtest.dart's private `_strList`, `_numMap`, …)

/// `_strList`: `v is List ? v.map(toString) : []`.
nonisolated func backtestStringList(_ v: JSON) -> [String] { v.stringElements }

/// `_numMap`: every value that reads as a number; nil when none do.
nonisolated func backtestNumMap(_ v: JSON) -> [String: Num]? {
    guard let map = v.object else { return nil }
    var result: [String: Num] = [:]
    for (k, val) in map {
        if let n = val.lenientNum { result[k] = n }
    }
    return result.isEmpty ? nil : result
}

/// `_changePctMap`: stock_price_change values are dicts {start_price,
/// end_price, change_percent}; pull the change_percent so the UI renders the
/// real %, not '—' or NaN%. A flat number is tolerated too.
nonisolated func backtestChangePctMap(_ v: JSON) -> [String: Num]? {
    guard let map = v.object else { return nil }
    var result: [String: Num] = [:]
    for (k, val) in map {
        let n = val.isObject ? val["change_percent"].lenientNum : val.lenientNum
        if let n { result[k] = n }
    }
    return result.isEmpty ? nil : result
}

/// `decisionLabel()`'s switch on `decision?.toString()`.
nonisolated func backtestDecisionLabel(_ decision: JSON) -> String {
    switch decision.string {
    case "1": "BUY"
    case "-1": "SELL"
    default: "HOLD"
    }
}

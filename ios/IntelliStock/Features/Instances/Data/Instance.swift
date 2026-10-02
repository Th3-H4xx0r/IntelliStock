import Foundation

// Ported from features/instances/data/models/instance.dart.

/// A trading instance.
///
/// The properties are `var`: Dart's `copyWith` (with a sentinel so nullable
/// fields could be cleared) becomes "copy the value and assign".
nonisolated struct Instance: Hashable, Sendable, Identifiable {
    var id: String
    var name: String
    /// 'user' | 'ai'
    var createdBy: String
    var runCommand: Bool
    /// True when the instance process died (not an operator Stop) and is
    /// held open for log capture — surfaced as a 'Crashed' badge.
    var crashed: Bool
    var strategyId: String?
    var brokerageId: String?
    var alpacaDataBrokerageId: String?
    /// Granularity in seconds (e.g. 60, 300, 900, 3600, 86400).
    var granularityTimeIncrement: Int?
    var maxUsage: Double?
    var uptimeSeconds: Int?
    var stocks: [String]
    /// Nested brokerage map (if the API returns it).
    var brokerage: JSONObject?
    /// Nested strategy map (if the API returns it).
    var strategy: JSONObject?
    /// Instance kind: 'kalshi' | 'crypto' | nil (equity). Drives which screen
    /// the instance is surfaced on (crypto/Kalshi bots have their own tabs).
    var kind: String?
    /// Crypto allocation blob (`{band, allocations:[{symbol,pct}]}`) for
    /// kind='crypto' instances. Present only when the API surfaces it; used
    /// to prefill the crypto edit sheet with exact per-coin weights.
    var cryptoConfig: JSONObject?

    init(
        id: String,
        name: String,
        createdBy: String,
        runCommand: Bool,
        crashed: Bool = false,
        strategyId: String? = nil,
        brokerageId: String? = nil,
        alpacaDataBrokerageId: String? = nil,
        granularityTimeIncrement: Int? = nil,
        maxUsage: Double? = nil,
        uptimeSeconds: Int? = nil,
        stocks: [String] = [],
        brokerage: JSONObject? = nil,
        strategy: JSONObject? = nil,
        kind: String? = nil,
        cryptoConfig: JSONObject? = nil
    ) {
        self.id = id
        self.name = name
        self.createdBy = createdBy
        self.runCommand = runCommand
        self.crashed = crashed
        self.strategyId = strategyId
        self.brokerageId = brokerageId
        self.alpacaDataBrokerageId = alpacaDataBrokerageId
        self.granularityTimeIncrement = granularityTimeIncrement
        self.maxUsage = maxUsage
        self.uptimeSeconds = uptimeSeconds
        self.stocks = stocks
        self.brokerage = brokerage
        self.strategy = strategy
        self.kind = kind
        self.cryptoConfig = cryptoConfig
    }

    init(json j: JSON) {
        var stocks: [String] = []
        for s in j["stocks"].arrayValue {
            if s.isObject {
                let sym = s["symbol"].or(s["ticker"]).stringOr("")
                if !sym.isEmpty { stocks.append(sym) }
            } else if case .string(let str) = s, !str.isEmpty {
                stocks.append(str)
            }
        }
        let granularity = j["granularity"]
        self.init(
            id: j["id"].stringOr(""),
            name: j["name"].or(j["id"]).stringOr(""),
            createdBy: j["created_by"].stringOr("user"),
            runCommand: j["run_command"].bool || j["runCommand"].bool,
            crashed: j["crashed"].bool,
            strategyId: j["strategy_id"].string,
            brokerageId: j["brokerage_id"].string,
            alpacaDataBrokerageId: j["alpaca_data_brokerage_id"].string,
            granularityTimeIncrement: j["granularity_time_increment"].int
                ?? (granularity.isNull ? nil : JSON.parseInt(granularity.dartDescription)),
            maxUsage: j["max_usage"].double,
            uptimeSeconds: j["uptime_seconds"].int,
            stocks: stocks,
            brokerage: j["brokerage"].orderedObject,
            strategy: j["strategy"].orderedObject,
            kind: j["kind"].string,
            cryptoConfig: j["crypto_config"].orderedObject
        )
    }
}

/// Lightweight model for an instance's backtests list. Dart `BacktestRow` in
/// instance.dart; renamed because the backtests feature owns `BacktestRow`.
nonisolated struct InstanceBacktestRow: Hashable, Sendable, Identifiable {
    var id: String
    var stocks: [String]
    var startDate: String?
    var endDate: String?
    var completedAt: String?
    var status: String
    var timeElapsedSeconds: Double?
    var pnl: Double?
    var pnlPercent: Double?
    var instanceId: String?
    var granularity: String?
    var initialCash: Double?
    /// Progress 0–100 from the polling endpoint.
    var progress: Int?

    init(json j: JSON) {
        var stocks: [String] = []
        for s in j["stocks"].arrayValue {
            if case .string(let str) = s, !str.isEmpty { stocks.append(str) }
        }
        self.id = j["id"].stringOr("")
        self.stocks = stocks
        startDate = j["start_date"].string
        endDate = j["end_date"].string
        completedAt = j["completed_at"].string
        status = j["status"].stringOr("unknown")
        timeElapsedSeconds = j["time_elapsed_seconds"].double
        pnl = j["pnl"].double
        pnlPercent = j["pnl_percent"].double
        instanceId = j["instance_id"].string
        granularity = j["granularity"].string
        initialCash = j["initial_cash"].double
        progress = j["progress"].int
    }

    /// Dart `copyWith({int? progress, String? status})`: nil keeps the value.
    func copyWith(progress: Int? = nil, status: String? = nil) -> InstanceBacktestRow {
        var copy = self
        copy.progress = progress ?? self.progress
        copy.status = status ?? self.status
        return copy
    }
}

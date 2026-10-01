import Foundation

// Plain immutable models for the live-trading endpoint, ported from
// features/live_trading/data/models/live_state.dart. Nullable-tolerant.

nonisolated struct LiveState: Hashable, Sendable {
    let status: String
    let equity: Double
    let cash: Double
    let buyingPower: Double
    let totalPnl: Double
    let totalPnlPct: Double
    let dayPnl: Double
    let dayPnlPct: Double
    let uptimeSec: Double
    let tradingActive: Bool
    let brokerFetchError: String?
    let containerStale: Bool
    let lookback: Lookback?
    let positions: [Position]
    let recentTrades: [Trade]

    init(json j: JSON) {
        status = j["status"].string ?? "unknown"
        equity = j["equity"].doubleOr(0)
        cash = j["cash"].doubleOr(0)
        buyingPower = j["buying_power"].double ?? j["cash"].double ?? 0
        totalPnl = j["total_pnl"].doubleOr(0)
        totalPnlPct = j["total_pnl_pct"].doubleOr(0)
        dayPnl = j["day_pnl"].doubleOr(0)
        dayPnlPct = j["day_pnl_pct"].doubleOr(0)
        uptimeSec = j["uptime_sec"].doubleOr(0)
        tradingActive = j["trading_active"].bool
        brokerFetchError = j["broker_fetch_error"].string
        containerStale = j["container_stale"].bool
        lookback = j["lookback"].isObject ? Lookback(json: j["lookback"]) : nil
        positions = j["positions"].objectElements.map(Position.init(json:))
        recentTrades = j["recent_trades"].objectElements.map(Trade.init(json:))
    }
}

nonisolated struct Position: Hashable, Sendable {
    let symbol: String
    /// Signed: negative for a short option.
    let qty: Double
    /// Broker-reported dollars, contract multiplier already included. nil
    /// when the broker has no quote (an illiquid option): render a dash,
    /// never 0.
    let marketValue: Double?
    /// Per share. For an option, the per-share premium.
    let lastPrice: Double?
    let avgEntryPrice: Double?
    let unrealizedPnl: Double?
    let unrealizedPnlPct: Double?
    /// "us_equity" | "us_option" (spec 2026-09-24 section 6.1). nil from an
    /// API build that predates the field.
    let assetClass: String?
    /// "long" | "short".
    let side: String?
    let multiplier: Int?
    let underlying: String?
    let strike: Double?
    /// YYYY-MM-DD.
    let expiry: String?

    init(json j: JSON) {
        symbol = j["symbol"].string ?? ""
        qty = j["qty"].doubleOr(0)
        marketValue = j["market_value"].double
        lastPrice = j["last_price"].double
        avgEntryPrice = j["avg_entry_price"].double
        unrealizedPnl = j["unrealized_pnl"].double
        unrealizedPnlPct = j["unrealized_pnl_pct"].double
        assetClass = j["asset_class"].string
        side = j["side"].string
        multiplier = j["multiplier"].int
        underlying = j["underlying"].string
        strike = j["strike"].double
        expiry = j["expiry"].string
    }

    var isOption: Bool {
        if let assetClass, !assetClass.isEmpty { return assetClass.lowercased() == "us_option" }
        return isOccOptionSymbol(symbol)
    }

    var isShort: Bool {
        if let side, !side.isEmpty { return side.lowercased() == "short" }
        return qty < 0
    }

    var contractMultiplier: Int {
        if !isOption { return 1 }
        if let m = multiplier, m > 0 { return m }
        return kOptionMultiplier
    }

    var quantityLabel: String { isOption ? "CONTRACTS" : "SHARES" }

    /// Options: unsigned whole contracts (the SHORT badge carries the sign).
    var quantityText: String { liveQuantityText(qty, isOption: isOption) }

    /// The close_position command reads the adapter's equity book only, so a
    /// Close on an option would fail. The wheel lane manages its own
    /// buy-backs.
    var canClose: Bool { !isOption }

    var optionDescription: String {
        isOption
            ? describeOptionContract(symbol: symbol, underlying: underlying, strike: strike, expiry: expiry)
            : ""
    }
}

nonisolated struct Trade: Hashable, Sendable {
    let side: String
    let symbol: String
    /// Per share. For an option fill, the per-share premium.
    let price: Double
    let qty: Double
    /// ISO string or epoch.
    let ts: JSON
    let orderId: String?
    /// Not sent by today's recent_trades; read if a later build adds it.
    let assetClass: String?

    init(json j: JSON) {
        side = j["side"].string ?? ""
        symbol = j["symbol"].string ?? ""
        price = j["price"].doubleOr(0)
        qty = j["qty"].doubleOr(0)
        ts = j["ts"]
        orderId = j["order_id"].string
        assetClass = j["asset_class"].string
    }

    var isOption: Bool {
        if let assetClass, !assetClass.isEmpty { return assetClass.lowercased() == "us_option" }
        return isOccOptionSymbol(symbol)
    }

    var quantityLabel: String { isOption ? "CONTRACTS" : "SHARES" }

    var quantityText: String { liveQuantityText(qty, isOption: isOption) }

    /// Fill value in dollars: price x qty, x100 for an option contract.
    var total: Double { price * qty * Double(isOption ? kOptionMultiplier : 1) }
}

nonisolated struct Lookback: Hashable, Sendable {
    let specName: String
    let startDate: String
    let endDate: String
    let current: Int
    let total: Int
    let currentDate: String

    init(json j: JSON) {
        specName = j["spec_name"].string ?? ""
        startDate = j["start_date"].string ?? ""
        endDate = j["end_date"].string ?? ""
        current = j["current"].intOr(0)
        total = j["total"].intOr(0)
        currentDate = j["current_date"].string ?? ""
    }

    var pct: Double {
        if total == 0 { return 0 }
        let v = (Double(current) / Double(total)) * 100
        return min(max(v, 0), 100)
    }
}

/// `qty.abs().truncate().toString()` for options, `qty.toStringAsFixed(4)`
/// otherwise.
nonisolated func liveQuantityText(_ qty: Double, isOption: Bool) -> String {
    if isOption {
        let whole = abs(qty).rounded(.towardZero)
        return whole.isFinite ? String(Int(whole)) : JSON.dartDoubleString(whole)
    }
    return dartToStringAsFixed(qty, 4)
}

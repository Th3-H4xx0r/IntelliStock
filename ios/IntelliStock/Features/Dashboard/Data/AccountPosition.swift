import Foundation

/// A single open holding from `GET /brokerages/{id}/positions`
/// (dashboard_repository.dart). `StockRoute` carries it, so it stays
/// `nonisolated`, `Hashable` and `Sendable`.
nonisolated struct AccountPosition: Hashable, Sendable {
    let symbol: String
    let qty: Double
    let marketValue: Double
    let unrealizedPnl: Double
    let unrealizedPnlPct: Double
    let lastPrice: Double?
    let avgEntryPrice: Double?

    init(
        symbol: String,
        qty: Double,
        marketValue: Double,
        unrealizedPnl: Double,
        unrealizedPnlPct: Double,
        lastPrice: Double? = nil,
        avgEntryPrice: Double? = nil
    ) {
        self.symbol = symbol
        self.qty = qty
        self.marketValue = marketValue
        self.unrealizedPnl = unrealizedPnl
        self.unrealizedPnlPct = unrealizedPnlPct
        self.lastPrice = lastPrice
        self.avgEntryPrice = avgEntryPrice
    }

    init(json: JSON) {
        self.init(
            symbol: json["symbol"].stringOr(""),
            qty: json["qty"].doubleOr(0),
            marketValue: json["marketValue"].doubleOr(0),
            unrealizedPnl: json["unrealizedPnl"].doubleOr(0),
            unrealizedPnlPct: json["unrealizedPnlPct"].doubleOr(0),
            lastPrice: json["lastPrice"].double,
            avgEntryPrice: json["avgEntryPrice"].double
        )
    }
}

/// The account's uninvested cash + open positions
/// (`GET /brokerages/{id}/positions`).
nonisolated struct AccountHoldings: Hashable, Sendable {
    let cash: Double?
    let positions: [AccountPosition]

    var isEmpty: Bool { cash == nil && positions.isEmpty }
}

import Foundation

/// One order from `GET /brokerages/{id}/order-book`, read-only.
///
/// Decoded leniently, like every model here: only `id`, `symbol`, `side` and
/// `status` are promised by the server, and even those fall back to `""`
/// rather than failing the whole book. Numbers are read as JSON numbers or
/// numeric strings (Alpaca's own API sends strings), and the enums stay as
/// Alpaca's raw lowercase words so a status the app does not know still
/// shows.
nonisolated struct BrokerOrder: Hashable, Sendable, Identifiable {
    let id: String
    let symbol: String
    /// `buy` or `sell`.
    let side: String
    /// `market`, `limit`, `stop`, `stop_limit` or `trailing_stop`; nil when absent.
    let type: String?
    /// Alpaca's raw status: `new`, `partially_filled`, `filled`, `canceled`, …
    let status: String
    /// nil for a notional (dollar) order.
    let qty: Double?
    let notional: Double?
    let filledQty: Double?
    /// Per share; for an option, the per-share premium.
    let filledAvgPrice: Double?
    let limitPrice: Double?
    let stopPrice: Double?
    let timeInForce: String?
    let submittedAt: Date?
    let filledAt: Date?
    let canceledAt: Date?
    /// `us_equity` or `us_option`.
    let assetClass: String?

    init(
        id: String,
        symbol: String,
        side: String,
        type: String? = nil,
        status: String,
        qty: Double? = nil,
        notional: Double? = nil,
        filledQty: Double? = nil,
        filledAvgPrice: Double? = nil,
        limitPrice: Double? = nil,
        stopPrice: Double? = nil,
        timeInForce: String? = nil,
        submittedAt: Date? = nil,
        filledAt: Date? = nil,
        canceledAt: Date? = nil,
        assetClass: String? = nil
    ) {
        self.id = id
        self.symbol = symbol
        self.side = side
        self.type = type
        self.status = status
        self.qty = qty
        self.notional = notional
        self.filledQty = filledQty
        self.filledAvgPrice = filledAvgPrice
        self.limitPrice = limitPrice
        self.stopPrice = stopPrice
        self.timeInForce = timeInForce
        self.submittedAt = submittedAt
        self.filledAt = filledAt
        self.canceledAt = canceledAt
        self.assetClass = assetClass
    }

    init(json: JSON) {
        self.init(
            id: json["id"].stringOr(""),
            symbol: json["symbol"].stringOr("").trimmingCharacters(in: .whitespacesAndNewlines),
            side: Self.word(json["side"]) ?? "",
            type: Self.word(json["type"]),
            status: Self.word(json["status"]) ?? "",
            qty: json["qty"].numOrParsedDouble,
            notional: json["notional"].numOrParsedDouble,
            filledQty: json["filled_qty"].numOrParsedDouble,
            filledAvgPrice: json["filled_avg_price"].numOrParsedDouble,
            limitPrice: json["limit_price"].numOrParsedDouble,
            stopPrice: json["stop_price"].numOrParsedDouble,
            timeInForce: Self.word(json["time_in_force"]),
            submittedAt: parseDateTime(json["submitted_at"]),
            filledAt: parseDateTime(json["filled_at"]),
            canceledAt: parseDateTime(json["canceled_at"]),
            assetClass: Self.word(json["asset_class"])
        )
    }

    /// A trimmed, lowercased enum word; nil when absent or blank.
    private static func word(_ v: JSON) -> String? {
        guard let s = v.string?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(), !s.isEmpty else {
            return nil
        }
        return s
    }

    /// An options order: `asset_class` when the server sends it, else the
    /// OCC shape of the symbol.
    var isOption: Bool {
        if let assetClass { return assetClass == "us_option" }
        return isOccOptionSymbol(symbol)
    }

    /// Shares per unit of `qty`: 100 for an option contract, else 1.
    var multiplier: Double { isOption ? Double(kOptionMultiplier) : 1 }

    /// A notional (dollar-amount) order: no share quantity, a dollar figure.
    var isNotional: Bool { qty == nil && notional != nil }

    /// Some quantity has filled.
    var hasFill: Bool { (filledQty ?? 0) > 0 }
}

/// The selected account's orders (`GET /brokerages/{id}/order-book`).
nonisolated struct OrderBook: Hashable, Sendable {
    /// False for a brokerage without an order book (Kalshi), and for a
    /// server that predates the endpoint; the dashboard then hides the
    /// section.
    let supported: Bool
    /// Working orders, newest submitted first.
    let open: [BrokerOrder]
    /// Closed orders (filled, canceled, expired, rejected), newest first.
    let recent: [BrokerOrder]

    init(supported: Bool = true, open: [BrokerOrder] = [], recent: [BrokerOrder] = []) {
        self.supported = supported
        self.open = open
        self.recent = recent
    }

    /// A missing `supported` reads as true: the server answered with lists.
    init(json: JSON) {
        self.init(
            supported: json["supported"].boolValue ?? true,
            open: json["open"].objectElements.map(BrokerOrder.init(json:)),
            recent: json["recent"].objectElements.map(BrokerOrder.init(json:))
        )
    }

    static let unsupported = OrderBook(supported: false)
}

import Foundation

// The data contract between the app and the WidgetKit extension, ported from
// `widget_payload.dart`. `WidgetSync` serialises these to JSON strings in the
// App Group container; `PortfolioWidget.swift` reads the same keys and shapes.

/// One intraday sample: `t` epoch seconds, `v` portfolio value.
nonisolated struct IntradayPoint: Hashable, Sendable {
    let t: Int
    let v: Double

    init(t: Int, v: Double) {
        self.t = t
        self.v = v
    }

    init(json j: JSON) {
        t = j["t"].int ?? 0
        v = j["v"].double ?? 0
    }

    func toJSON() -> JSON {
        ["t": .int(t), "v": .double(v)]
    }
}

nonisolated struct WidgetPortfolio: Hashable, Sendable {
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double
    let intradayPoints: [IntradayPoint]
    /// ISO-8601 timestamp string.
    let asOf: String

    init(accountValue: Double, dayPnlAbs: Double, dayPnlPct: Double, intradayPoints: [IntradayPoint], asOf: String) {
        self.accountValue = accountValue
        self.dayPnlAbs = dayPnlAbs
        self.dayPnlPct = dayPnlPct
        self.intradayPoints = intradayPoints
        self.asOf = asOf
    }

    init(json j: JSON) {
        accountValue = j["accountValue"].double ?? 0
        dayPnlAbs = j["dayPnlAbs"].double ?? 0
        dayPnlPct = j["dayPnlPct"].double ?? 0
        intradayPoints = j["intradayPoints"].arrayValue.filter { $0.object != nil }.map(IntradayPoint.init(json:))
        if case .string(let s) = j["asOf"] { asOf = s } else { asOf = "" }
    }

    func toJSON() -> JSON {
        [
            "accountValue": .double(accountValue),
            "dayPnlAbs": .double(dayPnlAbs),
            "dayPnlPct": .double(dayPnlPct),
            "intradayPoints": .array(intradayPoints.map { $0.toJSON() }),
            "asOf": .string(asOf),
        ]
    }
}

/// One selectable portfolio (an instance) for the configurable widget.
nonisolated struct WidgetAccount: Hashable, Sendable {
    let id: String
    let label: String
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double
    let intradayPoints: [IntradayPoint]
    let positions: [WidgetPosition]

    init(
        id: String, label: String, accountValue: Double, dayPnlAbs: Double, dayPnlPct: Double,
        intradayPoints: [IntradayPoint] = [], positions: [WidgetPosition] = []
    ) {
        self.id = id
        self.label = label
        self.accountValue = accountValue
        self.dayPnlAbs = dayPnlAbs
        self.dayPnlPct = dayPnlPct
        self.intradayPoints = intradayPoints
        self.positions = positions
    }

    func toJSON() -> JSON {
        [
            "id": .string(id),
            "label": .string(label),
            "accountValue": .double(accountValue),
            "dayPnlAbs": .double(dayPnlAbs),
            "dayPnlPct": .double(dayPnlPct),
            "intradayPoints": .array(intradayPoints.map { $0.toJSON() }),
            "positions": .array(positions.map { $0.toJSON() }),
        ]
    }

    /// The primary-portfolio shape, for the non-configurable fallback.
    func toPortfolioJSON() -> JSON {
        [
            "accountValue": .double(accountValue),
            "dayPnlAbs": .double(dayPnlAbs),
            "dayPnlPct": .double(dayPnlPct),
            "intradayPoints": .array(intradayPoints.map { $0.toJSON() }),
            "asOf": "",
        ]
    }
}

nonisolated struct WidgetPosition: Hashable, Sendable {
    let symbol: String
    let qty: Double
    let marketValue: Double
    let unrealizedPnlAbs: Double
    let unrealizedPnlPct: Double

    init(symbol: String, qty: Double, marketValue: Double, unrealizedPnlAbs: Double, unrealizedPnlPct: Double) {
        self.symbol = symbol
        self.qty = qty
        self.marketValue = marketValue
        self.unrealizedPnlAbs = unrealizedPnlAbs
        self.unrealizedPnlPct = unrealizedPnlPct
    }

    init(json j: JSON) {
        if case .string(let s) = j["symbol"] { symbol = s } else { symbol = "" }
        qty = j["qty"].double ?? 0
        marketValue = j["marketValue"].double ?? 0
        unrealizedPnlAbs = j["unrealizedPnlAbs"].double ?? 0
        unrealizedPnlPct = j["unrealizedPnlPct"].double ?? 0
    }

    func toJSON() -> JSON {
        [
            "symbol": .string(symbol),
            "qty": .double(qty),
            "marketValue": .double(marketValue),
            "unrealizedPnlAbs": .double(unrealizedPnlAbs),
            "unrealizedPnlPct": .double(unrealizedPnlPct),
        ]
    }
}

nonisolated struct WidgetInstance: Hashable, Sendable {
    let id: String
    let name: String
    let running: Bool
    let pnlAbs: Double
    let pnlPct: Double

    init(id: String, name: String, running: Bool, pnlAbs: Double, pnlPct: Double) {
        self.id = id
        self.name = name
        self.running = running
        self.pnlAbs = pnlAbs
        self.pnlPct = pnlPct
    }

    init(json j: JSON) {
        if case .string(let s) = j["id"] { id = s } else { id = "" }
        if case .string(let s) = j["name"] { name = s } else { name = "" }
        running = j["running"].boolValue ?? false
        pnlAbs = j["pnlAbs"].double ?? 0
        pnlPct = j["pnlPct"].double ?? 0
    }

    func toJSON() -> JSON {
        [
            "id": .string(id),
            "name": .string(name),
            "running": .bool(running),
            "pnlAbs": .double(pnlAbs),
            "pnlPct": .double(pnlPct),
        ]
    }
}

/// The complete snapshot written to the App Group on every sync.
nonisolated struct WidgetPayload: Hashable, Sendable {
    let portfolio: WidgetPortfolio
    let positions: [WidgetPosition]
    let instances: [WidgetInstance]

    init(portfolio: WidgetPortfolio, positions: [WidgetPosition], instances: [WidgetInstance]) {
        self.portfolio = portfolio
        self.positions = positions
        self.instances = instances
    }

    init(json j: JSON) {
        portfolio = WidgetPortfolio(json: j["portfolio"].object.map(JSON.object) ?? [:])
        positions = j["positions"].arrayValue.filter { $0.object != nil }.map(WidgetPosition.init(json:))
        instances = j["instances"].arrayValue.filter { $0.object != nil }.map(WidgetInstance.init(json:))
    }

    func toJSON() -> JSON {
        [
            "portfolio": portfolio.toJSON(),
            "positions": .array(positions.map { $0.toJSON() }),
            "instances": .array(instances.map { $0.toJSON() }),
        ]
    }
}

import Foundation

/// What the trade ticket and the stock screen's open orders read and write.
/// `TradeRepository` is the real one; tests stand in a fake.
nonisolated protocol TradeBackend: Sendable {
    /// `POST /brokerages/{id}/orders`: places a REAL order.
    func placeOrder(_ brokerageId: String, _ request: TradeOrderRequest) async throws -> BrokerOrder
    /// `DELETE /brokerages/{id}/orders/{order_id}`: cancels a REAL order.
    func cancelOrder(_ brokerageId: String, orderId: String) async throws
    /// The account's cash and positions.
    func holdings(_ brokerageId: String) async throws -> AccountHoldings
    /// The account's working orders.
    func openOrders(_ brokerageId: String) async throws -> [BrokerOrder]
    /// The equity instances, for "… trades this account".
    func instances() async throws -> [Instance]
}

/// Manual orders on a linked Alpaca account (the contract in
/// `trade-contract.md`), plus the reads the ticket needs from other
/// features' endpoints.
nonisolated struct TradeRepository: TradeBackend {
    let client: ApiClient

    /// Sends the order once; never retried here. A 200 without an order in
    /// it reads as unconfirmed (a 502), like the server's own fallback.
    func placeOrder(_ brokerageId: String, _ request: TradeOrderRequest) async throws -> BrokerOrder {
        let data = try await client.post("/brokerages/\(Self.segment(brokerageId))/orders", body: request.body)
        let order = BrokerOrder(json: data["order"])
        guard !order.id.isEmpty else {
            throw ApiError(message: "The server answered without an order. \(TradeTicket.unconfirmedTail)", statusCode: 502)
        }
        return order
    }

    func cancelOrder(_ brokerageId: String, orderId: String) async throws {
        _ = try await client.delete("/brokerages/\(Self.segment(brokerageId))/orders/\(Self.segment(orderId))")
    }

    func holdings(_ brokerageId: String) async throws -> AccountHoldings {
        try await DashboardRepository(client: client).accountHoldings(brokerageId)
    }

    /// The order book's open list; one closed order is the least it asks for.
    func openOrders(_ brokerageId: String) async throws -> [BrokerOrder] {
        try await DashboardRepository(client: client).orderBook(brokerageId, recentLimit: 1).open
    }

    func instances() async throws -> [Instance] {
        try await InstanceRepository(client: client).listInstances()
    }

    /// One path segment, percent-encoded.
    static func segment(_ s: String) -> String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }
}

/// Tells screens that hold an account's positions or orders (the dashboard)
/// that an order was placed or canceled there. The object is the brokerage id.
nonisolated enum TradeEvents {
    static let ordersChanged = Notification.Name("IntelliStock.tradeOrdersChanged")

    static func postOrdersChanged(_ brokerageId: String, center: NotificationCenter = .default) {
        center.post(name: ordersChanged, object: brokerageId)
    }
}

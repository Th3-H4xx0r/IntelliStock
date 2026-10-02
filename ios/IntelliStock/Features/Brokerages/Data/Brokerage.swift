import Foundation

/// A linked brokerage account, ported from
/// features/brokerages/data/models/brokerage.dart. Missing JSON keys read as
/// nil rather than failing the parse.
nonisolated struct Brokerage: Hashable, Sendable, Identifiable {
    let id: String
    /// Supported brokerage discriminator.
    let brokerageType: String
    let accountName: String
    /// 'active', 'expired', etc.
    let status: String?
    let paper: Bool
    let accountNumber: String?
    /// 'iex' or 'sip' — Alpaca only.
    let alpacaDataFeed: String?
    let lastRefreshAt: String?
    let lastError: String?
    let equity: Double?
    let buyingPower: Double?
    let managementType: String?

    init(
        id: String,
        brokerageType: String,
        accountName: String,
        status: String? = nil,
        paper: Bool,
        accountNumber: String? = nil,
        alpacaDataFeed: String? = nil,
        lastRefreshAt: String? = nil,
        lastError: String? = nil,
        equity: Double? = nil,
        buyingPower: Double? = nil,
        managementType: String? = nil
    ) {
        self.id = id
        self.brokerageType = brokerageType
        self.accountName = accountName
        self.status = status
        self.paper = paper
        self.accountNumber = accountNumber
        self.alpacaDataFeed = alpacaDataFeed
        self.lastRefreshAt = lastRefreshAt
        self.lastError = lastError
        self.equity = equity
        self.buyingPower = buyingPower
        self.managementType = managementType
    }

    init(json: JSON) {
        // Account number can live under different keys depending on
        // brokerage type.
        let acctNum = json["account_number"].string ?? json["alpaca_account_number"].string
        self.init(
            id: json["id"].stringOr(""),
            brokerageType: json["brokerage_type"].stringOr("alpaca"),
            accountName: json["account_name"].stringOr(""),
            status: json["status"].string,
            paper: json["alpaca_paper"].boolValue ?? json["paper"].boolValue ?? true,
            accountNumber: acctNum,
            alpacaDataFeed: json["alpaca_data_feed"].string,
            lastRefreshAt: json["last_refresh_at"].string,
            lastError: json["last_error"].string,
            equity: json["equity"].numOrParsedDouble,
            buyingPower: json["buying_power"].numOrParsedDouble,
            managementType: json["management_type"].string
        )
    }

    func toJSON() -> JSON {
        // Keys in the Dart map literal's order.
        var m: JSONObject = [
            "id": .string(id),
            "brokerage_type": .string(brokerageType),
            "account_name": .string(accountName),
        ]
        if let status { m["status"] = .string(status) }
        m["paper"] = .bool(paper)
        if let accountNumber { m["account_number"] = .string(accountNumber) }
        if let alpacaDataFeed { m["alpaca_data_feed"] = .string(alpacaDataFeed) }
        if let lastRefreshAt { m["last_refresh_at"] = .string(lastRefreshAt) }
        if let lastError { m["last_error"] = .string(lastError) }
        if let equity { m["equity"] = .double(equity) }
        if let buyingPower { m["buying_power"] = .double(buyingPower) }
        if let managementType { m["management_type"] = .string(managementType) }
        return .object(m)
    }
}

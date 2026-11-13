import Foundation

// The pure half of manual trading: what the ticket's fields mean, the checks
// the server makes (mirrored so the Review button only lights for an order
// the server will take), the request body, the estimate and the words the
// ticket and review show. No I/O; `TradeTicketModel` drives it.
//
// The server's rules live in `build_manual_order` (backend/api/main.py); the
// messages here repeat its sentences so the app and the server say the same
// thing.

nonisolated enum TradeSide: String, Hashable, Sendable, CaseIterable {
    case buy, sell

    var label: String { self == .buy ? "Buy" : "Sell" }
}

/// What the amount field holds.
nonisolated enum TradeAmountKind: String, Hashable, Sendable, CaseIterable {
    case shares, dollars

    var label: String { self == .shares ? "Shares" : "Dollars" }
}

nonisolated enum TradeOrderType: String, Hashable, Sendable, CaseIterable {
    case market, limit

    var label: String { self == .market ? "Market" : "Limit" }
}

nonisolated enum TradeTimeInForce: String, Hashable, Sendable, CaseIterable {
    case day, gtc

    var label: String { self == .day ? "Day" : "Good till canceled" }
}

/// A decimal the person typed, kept as text so nothing is lost on the way
/// to the server: `text` is normalised ("03.50" → "3.5"), `places` counts
/// its decimals the way Python's `Decimal.normalize()` does.
nonisolated struct TradeNumber: Hashable, Sendable {
    let text: String
    let value: Double
    let places: Int

    var isWhole: Bool { places == 0 }

    /// Parses what a decimal pad produces: digits and one separator, with an
    /// optional leading `$`. Thousands separators are dropped. nil for
    /// anything else, or when there is no digit.
    static func parse(_ raw: String, decimalSeparator: String = Locale.current.decimalSeparator ?? ".") -> TradeNumber? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("$") { s.removeFirst() }
        s = s.trimmingCharacters(in: .whitespaces)
        if decimalSeparator == "," {
            s = s.replacingOccurrences(of: ".", with: "").replacingOccurrences(of: ",", with: ".")
        } else {
            s = s.replacingOccurrences(of: ",", with: "")
        }
        guard !s.isEmpty, s.allSatisfy({ Self.isDigit($0) || $0 == "." }),
              s.filter({ $0 == "." }).count <= 1, s.contains(where: Self.isDigit)
        else { return nil }
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        var whole = String(parts[0])
        var fraction = parts.count > 1 ? String(parts[1]) : ""
        while whole.count > 1, whole.hasPrefix("0") { whole.removeFirst() }
        if whole.isEmpty { whole = "0" }
        while fraction.hasSuffix("0") { fraction.removeLast() }
        let text = fraction.isEmpty ? whole : "\(whole).\(fraction)"
        guard let value = Double(text), value.isFinite else { return nil }
        return TradeNumber(text: text, value: value, places: fraction.count)
    }

    private static func isDigit(_ c: Character) -> Bool { ("0"..."9").contains(c) }

    /// The JSON number: an int when whole (and small enough), else a double,
    /// which the encoder writes in its shortest round-trip form.
    var json: JSON {
        isWhole && value < 1e15 ? .int(Int(value)) : .double(value)
    }
}

/// Everything the ticket's fields hold, as typed.
nonisolated struct TradeDraft: Hashable, Sendable {
    var symbol: String
    var side: TradeSide
    var amountKind: TradeAmountKind = .shares
    var amountText = ""
    var orderType: TradeOrderType = .market
    var limitText = ""
    var timeInForce: TradeTimeInForce = .day

    init(symbol: String, side: TradeSide) {
        self.symbol = symbol
        self.side = side
    }

    var amount: TradeNumber? { TradeNumber.parse(amountText) }
    var limit: TradeNumber? { TradeNumber.parse(limitText) }

    /// A dollar amount is a market order: Alpaca takes notional orders only
    /// at market, so the ticket shows no order-type choice for one.
    var effectiveOrderType: TradeOrderType { amountKind == .dollars ? .market : orderType }

    /// Fractional shares and dollar amounts are day orders only.
    var requiresDay: Bool {
        if amountKind == .dollars { return true }
        if let amount { return !amount.isWhole }
        return false
    }

    var effectiveTimeInForce: TradeTimeInForce { requiresDay ? .day : timeInForce }
}

/// The order the server is sent: `POST /brokerages/{id}/orders`.
nonisolated struct TradeOrderRequest: Hashable, Sendable {
    let symbol: String
    let side: TradeSide
    /// Exactly one of `qty` and `notional`.
    let qty: TradeNumber?
    let notional: TradeNumber?
    let type: TradeOrderType
    let limitPrice: TradeNumber?
    let timeInForce: TradeTimeInForce
    let clientOrderId: String

    /// The request body, in the contract's field names.
    var body: JSON {
        var o = JSONObject()
        o["symbol"] = .string(symbol)
        o["side"] = .string(side.rawValue)
        if let qty { o["qty"] = qty.json }
        if let notional { o["notional"] = notional.json }
        o["type"] = .string(type.rawValue)
        if let limitPrice { o["limit_price"] = limitPrice.json }
        o["time_in_force"] = .string(timeInForce.rawValue)
        o["client_order_id"] = .string(clientOrderId)
        return .object(o)
    }

    /// The confirm control's words: "Buy 3 AAPL", "Sell $250 of NVDA".
    var actionLabel: String {
        TradeTicket.actionLabel(side: side, symbol: symbol, qty: qty?.text, notional: notional?.value)
    }

    /// "3 sh", "$250.00".
    var sizeText: String {
        if let qty { return "\(qty.text) sh" }
        return fmtMoney(notional?.value)
    }
}

/// Whether the ticket can go to review.
nonisolated enum TradeCheck: Hashable, Sendable {
    /// Ready: the request to send.
    case ready(TradeOrderRequest)
    /// Something is still empty or loading. Review stays off, nothing is said.
    case incomplete
    /// The server would refuse it, for this reason.
    case invalid(String)

    var request: TradeOrderRequest? {
        if case .ready(let r) = self { return r }
        return nil
    }

    var message: String? {
        if case .invalid(let m) = self { return m }
        return nil
    }
}

/// How a submit ended.
nonisolated enum TradeSubmitResult: Hashable, Sendable {
    /// Alpaca took it; the order as the server returned it.
    case placed(BrokerOrder)
    /// Refused before or by Alpaca: nothing was placed.
    case rejected(String)
    /// The server could not confirm it (Alpaca unreachable, a timeout): it
    /// may or may not be placed.
    case unconfirmed(String)

    var message: String? {
        switch self {
        case .placed: nil
        case .rejected(let m), .unconfirmed(let m): m
        }
    }

    var order: BrokerOrder? {
        if case .placed(let o) = self { return o }
        return nil
    }
}

nonisolated enum TradeTicket {
    /// The server's symbol rule, `^[A-Z][A-Z0-9.\-]{0,9}$`: equities only.
    static func isTradableSymbol(_ s: String) -> Bool {
        let chars = Array(s)
        guard (1...10).contains(chars.count), let first = chars.first, ("A"..."Z").contains(first) else { return false }
        return chars.dropFirst().allSatisfy { ("A"..."Z").contains($0) || ("0"..."9").contains($0) || $0 == "." || $0 == "-" }
    }
    /// Alpaca books fractional shares to 9 decimals; so does the server.
    static let maxShareDecimals = 9
    static let unconfirmedTail = "The order was not confirmed. Check Orders before trying again."
    static let unsupportedServer = "This server can't place orders from the app yet. Update IntelliStock on the server, then try again."

    // MARK: Accounts

    /// The accounts the app can trade: Alpaca.
    static func tradableAccounts(_ accounts: [BrokerageAccount]) -> [BrokerageAccount] {
        accounts.filter(\.hasOrderBook)
    }

    /// The ticket's starting account: the dashboard's selection when it is
    /// tradable, else the stock screen's account, else the first tradable.
    static func defaultAccountId(_ tradable: [BrokerageAccount], dashboardSelected: String?, route: String?) -> String? {
        let ids = Set(tradable.map(\.id))
        if let dashboardSelected, ids.contains(dashboardSelected) { return dashboardSelected }
        if let route, ids.contains(route) { return route }
        return tradable.first?.id
    }

    /// "Live money" or "Paper", the way the review states it.
    static func modeText(_ account: BrokerageAccount) -> String {
        account.alpacaPaper ? "Paper" : "Live money"
    }

    /// The section footer under the account picker.
    static func modeFootnote(_ account: BrokerageAccount) -> String {
        account.alpacaPaper
            ? "Paper. Orders here use simulated money."
            : "Live money. Orders here use real money."
    }

    /// One quiet line when a running instance trades the account: "f14-paper
    /// trades this account and may act on this position".
    static func instanceWarning(_ instances: [Instance], accountId: String) -> String? {
        var seen = Set<String>()
        let names = instances
            .filter { $0.brokerageId == accountId && $0.runCommand && !$0.crashed }
            .map { $0.name.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
        switch names.count {
        case 0: return nil
        case 1: return "\(names[0]) trades this account and may act on this position"
        default:
            let list = names.dropLast().joined(separator: ", ") + " and " + names[names.count - 1]
            return "\(list) trade this account and may act on this position"
        }
    }

    // MARK: Held quantity

    /// The held quantity as the exact text "Sell all" fills: up to 9
    /// decimals (Alpaca's precision), trailing zeros dropped. A position
    /// read back as 0.30000000000000004 fills "0.3".
    static func exactQtyText(_ qty: Double) -> String {
        var s = DartNumberFormat.toStringAsFixed(qty, maxShareDecimals)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s == "-0" ? "0" : s
    }

    /// "You hold 12 sh", or that none is held.
    static func heldText(_ qty: Double, symbol: String) -> String {
        qty > 0 ? "You hold \(exactQtyText(qty)) sh" : "You don't hold \(symbol) in this account"
    }

    // MARK: Check

    /// The server's checks, on what the ticket will send. `held` is the
    /// account's quantity for a sell (nil while it loads).
    ///
    /// One rule is the app's own: a sell never goes past what the account
    /// holds, so the ticket cannot open a short position by mistake.
    static func check(_ d: TradeDraft, held: Double?, clientOrderId: String) -> TradeCheck {
        let symbol = d.symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if symbol.isEmpty { return .invalid("Enter a stock symbol") }
        if !isTradableSymbol(symbol) {
            return .invalid("Enter a stock symbol like AAPL. Options and crypto aren't supported here")
        }

        let rawAmount = d.amountText.trimmingCharacters(in: .whitespacesAndNewlines)
        if rawAmount.isEmpty || rawAmount == "$" { return .incomplete }
        let shares = d.amountKind == .shares
        guard let amount = d.amount else {
            return .invalid(shares ? "Shares must be a number" : "Dollar amount must be a number")
        }
        if shares {
            if amount.value <= 0 { return .invalid("Shares must be more than zero") }
            if amount.places > maxShareDecimals {
                return .invalid("Shares can have at most \(maxShareDecimals) decimal places")
            }
        } else {
            if amount.value <= 0 { return .invalid("The dollar amount must be more than zero") }
            if amount.places > 2 { return .invalid("The dollar amount can't go past cents") }
        }

        let type = d.effectiveOrderType
        var limit: TradeNumber?
        if type == .limit {
            if d.limitText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return .incomplete }
            guard let l = d.limit else { return .invalid("Limit price must be a number") }
            if l.value <= 0 { return .invalid("Enter a limit price above zero") }
            if l.value >= 1, l.places > 2 { return .invalid("Limit prices of $1 or more go to the cent") }
            if l.value < 1, l.places > 4 { return .invalid("Limit prices under $1 go to 4 decimal places at most") }
            limit = l
        }

        if d.side == .sell {
            guard let held else { return .incomplete }
            if held <= 0 { return .invalid("You don't hold \(symbol) in this account") }
            if shares, let cap = Double(exactQtyText(held)), amount.value > cap {
                return .invalid("That's more than the \(exactQtyText(held)) sh you hold")
            }
        }

        return .ready(TradeOrderRequest(
            symbol: symbol,
            side: d.side,
            qty: shares ? amount : nil,
            notional: shares ? nil : amount,
            type: type,
            limitPrice: limit,
            timeInForce: d.effectiveTimeInForce,
            clientOrderId: clientOrderId
        ))
    }

    // MARK: Estimate

    /// The dollar amount, or shares × the limit (for a limit order) or the
    /// last price. nil when there is no amount or no price.
    static func estimate(_ d: TradeDraft, lastPrice: Double?) -> Double? {
        guard let amount = d.amount, amount.value > 0 else { return nil }
        if d.amountKind == .dollars { return amount.value }
        let price = d.effectiveOrderType == .limit ? d.limit?.value : lastPrice
        guard let price, price > 0 else { return nil }
        return amount.value * price
    }

    /// "Estimated cost" for a buy, "Estimated proceeds" for a sell.
    static func estimateLabel(_ side: TradeSide) -> String {
        side == .buy ? "Estimated cost" : "Estimated proceeds"
    }

    // MARK: Words

    /// "Buy 3 AAPL", "Sell $250 of NVDA".
    static func actionLabel(side: TradeSide, symbol: String, qty: String?, notional: Double?) -> String {
        if let qty { return "\(side.label) \(qty) \(symbol)" }
        if let notional { return "\(side.label) \(dollarText(notional)) of \(symbol)" }
        return "\(side.label) \(symbol)"
    }

    /// A limit price as money: "$225.50", "$1,250.00"; under $1, the sub-cent
    /// digits the server allows: "$0.50", "$0.1234".
    static func priceText(_ n: TradeNumber) -> String {
        if n.value >= 1 { return fmtMoney(n.value) }
        let places = max(2, n.places)
        return "$\(DartNumberFormat.toStringAsFixed(n.value, places))"
    }

    /// "$250", "$1,250.50": cents only when there are some.
    static func dollarText(_ v: Double) -> String {
        let whole = v == v.rounded()
        return "$\(DartNumberFormat.grouped(v, fractionDigits: whole ? 0 : 2))"
    }

    /// A new idempotency key for one ticket: Alpaca refuses a second order
    /// with the same `client_order_id`, so a retried swipe cannot double up.
    static func newClientOrderId() -> String {
        "ios-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }

    /// How a failed submit reads: a 5xx or a transport failure may have
    /// reached Alpaca, so it is "not confirmed"; anything else was refused.
    static func submitResult(for error: any Error) -> TradeSubmitResult {
        let api = error as? ApiError
        let message = api?.message ?? error.localizedDescription
        guard let status = api?.statusCode else {
            return .unconfirmed("\(message) \(unconfirmedTail)")
        }
        if isUnsupportedServer(api) { return .rejected(unsupportedServer) }
        if status >= 500 { return .unconfirmed(message) }
        return .rejected(message)
    }

    /// A server without the trading routes: POST on the read-only
    /// `/orders` path is a 405, an unknown path FastAPI's 404 "Not Found"
    /// (a missing brokerage is "Brokerage not found").
    static func isUnsupportedServer(_ error: ApiError?) -> Bool {
        guard let error, let status = error.statusCode else { return false }
        return status == 405 || (status == 404 && error.message == "Not Found")
    }

    /// A cancel's failure in words, with the same old-server case.
    static func cancelErrorText(_ error: any Error) -> String {
        let api = error as? ApiError
        if isUnsupportedServer(api) {
            return "This server can't cancel orders from the app yet. Update IntelliStock on the server, then try again."
        }
        return api?.message ?? error.localizedDescription
    }

    // MARK: Open orders

    /// An open order the app can cancel: still working, not already being
    /// canceled.
    static func isCancelable(_ o: BrokerOrder) -> Bool {
        !o.id.isEmpty && !OrderRowText.isClosed(o.status) && o.status != "pending_cancel"
    }

    /// "Buy 3 AAPL, limit $190.00", "Sell $250 of NVDA, market".
    static func orderSummary(_ o: BrokerOrder) -> String {
        let side: TradeSide = o.side == "sell" ? .sell : .buy
        let symbol = o.symbol.isEmpty ? "—" : stockDisplayTitle(o.symbol)
        let qty = o.qty.map { exactQtyText($0) }
        let action = actionLabel(side: side, symbol: symbol, qty: qty, notional: qty == nil ? o.notional : nil)
        if let limit = o.limitPrice { return "\(action), limit \(fmtMoney(limit))" }
        if let stop = o.stopPrice { return "\(action), stop \(fmtMoney(stop))" }
        return "\(action), \((o.type ?? "market").replacingOccurrences(of: "_", with: " "))"
    }

    /// The cancel confirmation's message.
    static func cancelPromptBody(_ o: BrokerOrder, account: BrokerageAccount?) -> String {
        var text = orderSummary(o)
        if let account {
            text += ", in \(DashboardFormat.accountName(account)) (\(modeText(account).lowercased()))"
        }
        return text + ". Anything that hasn't filled is canceled."
    }
}

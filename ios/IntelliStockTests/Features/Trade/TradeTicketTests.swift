import Foundation
import Testing
@testable import IntelliStock

// Manual trading: the ticket's checks (mirroring the server's
// build_manual_order), the request body, estimates and words, the
// repository's requests, and the models over a fake backend. No network:
// the repository runs against `DataStub`, the models against
// `FakeTradeBackend`.

// MARK: - Fixtures

private func alpaca(_ id: String, _ name: String = "", paper: Bool = true) -> BrokerageAccount {
    BrokerageAccount(id: id, accountName: name.isEmpty ? id : name, brokerageType: "alpaca", status: "active", alpacaPaper: paper)
}

private func kalshi(_ id: String) -> BrokerageAccount {
    BrokerageAccount(id: id, accountName: id, brokerageType: "kalshi", status: "active")
}

private func position(_ symbol: String, _ qty: Double, value: Double = 100) -> AccountPosition {
    AccountPosition(symbol: symbol, qty: qty, marketValue: value, unrealizedPnl: 0, unrealizedPnlPct: 0)
}

private func order(
    _ id: String = "o1",
    _ symbol: String = "AAPL",
    side: String = "buy",
    type: String? = "limit",
    status: String = "new",
    qty: Double? = 3,
    notional: Double? = nil,
    limitPrice: Double? = 190,
    submittedAt: Date? = nil
) -> BrokerOrder {
    BrokerOrder(id: id, symbol: symbol, side: side, type: type, status: status, qty: qty,
                notional: notional, limitPrice: limitPrice, submittedAt: submittedAt)
}

private func draft(
    _ side: TradeSide = .buy,
    symbol: String = "AAPL",
    kind: TradeAmountKind = .shares,
    amount: String = "3",
    type: TradeOrderType = .market,
    limit: String = "",
    tif: TradeTimeInForce = .day
) -> TradeDraft {
    var d = TradeDraft(symbol: symbol, side: side)
    d.amountKind = kind
    d.amountText = amount
    d.orderType = type
    d.limitText = limit
    d.timeInForce = tif
    return d
}

private func bodyText(_ request: TradeOrderRequest) throws -> String {
    String(decoding: try request.body.data(), as: UTF8.self)
}

/// Test double for `TradeRepository`. Records every write.
nonisolated final class FakeTradeBackend: TradeBackend, @unchecked Sendable {
    var holdingsByAccount: [String: AccountHoldings] = [:]
    var holdingsError: (any Error)?
    var holdingsCalls: [String] = []
    var openByAccount: [String: [BrokerOrder]] = [:]
    var failingOpenAccounts: Set<String> = []
    var instanceList: [Instance] = []
    var placeError: (any Error)?
    var placedOrder = BrokerOrder(id: "alp-1", symbol: "AAPL", side: "buy", type: "market", status: "accepted", qty: 3)
    var placeCalls: [(account: String, request: TradeOrderRequest)] = []
    /// When set, placeOrder waits on it.
    var placeGate: SwingTestGate?
    var cancelCalls: [(account: String, order: String)] = []
    var cancelError: (any Error)?

    func placeOrder(_ brokerageId: String, _ request: TradeOrderRequest) async throws -> BrokerOrder {
        placeCalls.append((brokerageId, request))
        if let placeGate { await placeGate.wait() }
        if let placeError { throw placeError }
        return placedOrder
    }

    func cancelOrder(_ brokerageId: String, orderId: String) async throws {
        cancelCalls.append((brokerageId, orderId))
        if let cancelError { throw cancelError }
        openByAccount[brokerageId]?.removeAll { $0.id == orderId }
    }

    func holdings(_ brokerageId: String) async throws -> AccountHoldings {
        holdingsCalls.append(brokerageId)
        if let holdingsError { throw holdingsError }
        return holdingsByAccount[brokerageId] ?? AccountHoldings(cash: nil, positions: [])
    }

    func openOrders(_ brokerageId: String) async throws -> [BrokerOrder] {
        if failingOpenAccounts.contains(brokerageId) { throw ApiError(message: "boom", statusCode: 502) }
        return openByAccount[brokerageId] ?? []
    }

    func instances() async throws -> [Instance] { instanceList }
}

// MARK: - Numbers

struct TradeNumberTests {
    @Test func parsesAndNormalisesWhatADecimalPadTypes() throws {
        let three = try #require(TradeNumber.parse("3"))
        #expect(three.text == "3" && three.places == 0 && three.isWhole && three.value == 3)
        let padded = try #require(TradeNumber.parse(" 03.50 "))
        #expect(padded.text == "3.5" && padded.places == 1 && !padded.isWhole)
        #expect(TradeNumber.parse(".5")?.text == "0.5")
        #expect(TradeNumber.parse("5.")?.text == "5")
        #expect(TradeNumber.parse("0.000")?.text == "0")
        #expect(TradeNumber.parse("$1,250.75")?.text == "1250.75")
        #expect(TradeNumber.parse("12.123456789")?.places == 9)
    }

    @Test func rejectsAnythingElse() {
        for bad in ["", " ", ".", "$", "1.2.3", "abc", "-3", "3e2", "1/2"] {
            #expect(TradeNumber.parse(bad) == nil, "\(bad)")
        }
    }

    @Test func aCommaLocaleReadsTheCommaAsTheDecimal() {
        #expect(TradeNumber.parse("2,5", decimalSeparator: ",")?.text == "2.5")
        #expect(TradeNumber.parse("1.250,75", decimalSeparator: ",")?.text == "1250.75")
    }

    @Test func wholeNumbersEncodeAsIntsAndFractionsAsShortestDoubles() throws {
        #expect(TradeNumber.parse("3")?.json == .int(3))
        #expect(TradeNumber.parse("0.5")?.json == .double(0.5))
        let exact = try #require(TradeNumber.parse("12.123456789"))
        #expect(String(decoding: try exact.json.data(), as: UTF8.self) == "12.123456789")
    }
}

// MARK: - Checks (the server's rules)

struct TradeCheckTests {
    private func check(_ d: TradeDraft, held: Double? = nil) -> TradeCheck {
        TradeTicket.check(d, held: held, clientOrderId: "ios-1")
    }

    @Test func nothingTypedIsIncompleteAndSaysNothing() {
        #expect(check(draft(amount: "")) == .incomplete)
        #expect(check(draft(amount: "  ")) == .incomplete)
        #expect(check(draft(kind: .dollars, amount: "$")) == .incomplete)
        #expect(check(draft(type: .limit, limit: "")) == .incomplete)
        #expect(check(draft(amount: "")).message == nil)
    }

    @Test func sharesFollowTheServersRules() {
        #expect(check(draft(amount: "abc")).message == "Shares must be a number")
        #expect(check(draft(amount: "0")).message == "Shares must be more than zero")
        #expect(check(draft(amount: "0.1234567891")).message == "Shares can have at most 9 decimal places")
        #expect(check(draft(amount: "0.123456789")).request?.qty?.text == "0.123456789")
    }

    @Test func dollarsFollowTheServersRules() {
        #expect(check(draft(kind: .dollars, amount: "x")).message == "Dollar amount must be a number")
        #expect(check(draft(kind: .dollars, amount: "0")).message == "The dollar amount must be more than zero")
        #expect(check(draft(kind: .dollars, amount: "10.555")).message == "The dollar amount can't go past cents")
        #expect(check(draft(kind: .dollars, amount: "250.50")).request?.notional?.text == "250.5")
    }

    @Test func limitPricesFollowTheServersTicks() {
        #expect(check(draft(type: .limit, limit: "x")).message == "Limit price must be a number")
        #expect(check(draft(type: .limit, limit: "0")).message == "Enter a limit price above zero")
        #expect(check(draft(type: .limit, limit: "190.123")).message == "Limit prices of $1 or more go to the cent")
        #expect(check(draft(type: .limit, limit: "0.12345")).message == "Limit prices under $1 go to 4 decimal places at most")
        #expect(check(draft(type: .limit, limit: "0.1234")).request?.limitPrice?.text == "0.1234")
        #expect(check(draft(type: .limit, limit: "190.10")).request?.limitPrice?.text == "190.1")
    }

    @Test func onlyEquitySymbolsPass() {
        #expect(check(draft(symbol: "QQQ261120C00510000")).message == "Enter a stock symbol like AAPL. Options and crypto aren't supported here")
        #expect(check(draft(symbol: "BTC/USD")).message == "Enter a stock symbol like AAPL. Options and crypto aren't supported here")
        #expect(check(draft(symbol: " ")).message == "Enter a stock symbol")
        #expect(check(draft(symbol: "brk.b")).request?.symbol == "BRK.B")
        #expect(TradeTicket.isTradableSymbol("AAPL"))
        #expect(TradeTicket.isTradableSymbol("BRK-B"))
        #expect(!TradeTicket.isTradableSymbol("1AAPL"))
        #expect(!TradeTicket.isTradableSymbol("ABCDEFGHIJK"))
    }

    @Test func aDollarAmountIsAlwaysAMarketDayOrder() throws {
        let r = try #require(check(draft(kind: .dollars, amount: "250", type: .limit, limit: "190", tif: .gtc)).request)
        #expect(r.type == .market)
        #expect(r.limitPrice == nil)
        #expect(r.timeInForce == .day)
        #expect(r.qty == nil)
    }

    @Test func fractionalSharesAreDayOrdersAndWholeOnesKeepGtc() {
        #expect(check(draft(amount: "1.5", tif: .gtc)).request?.timeInForce == .day)
        #expect(check(draft(amount: "2", tif: .gtc)).request?.timeInForce == .gtc)
        #expect(draft(amount: "1.5").requiresDay)
        #expect(!draft(amount: "2").requiresDay)
        #expect(draft(kind: .dollars, amount: "2").requiresDay)
    }

    @Test func aSellNeverGoesPastWhatIsHeld() {
        // Unknown yet: wait.
        #expect(check(draft(.sell), held: nil) == .incomplete)
        // Nothing held: refused, so the ticket can't open a short.
        #expect(check(draft(.sell), held: 0).message == "You don't hold AAPL in this account")
        #expect(check(draft(.sell), held: -2).message == "You don't hold AAPL in this account")
        #expect(check(draft(.sell, amount: "13"), held: 12).message == "That's more than the 12 sh you hold")
        #expect(check(draft(.sell, amount: "12"), held: 12).request != nil)
        // The exact fractional holding, as Sell all fills it.
        #expect(check(draft(.sell, amount: "0.3"), held: 0.30000000000000004).request?.qty?.text == "0.3")
        // A buy ignores the holding.
        #expect(check(draft(.buy), held: 0).request != nil)
        // A dollar sell is Alpaca's to check.
        #expect(check(draft(.sell, kind: .dollars, amount: "5000"), held: 1).request != nil)
    }

    @Test func theBodyIsTheContractsFieldsInOrder() throws {
        let market = try #require(check(draft()).request)
        #expect(try bodyText(market) == #"{"symbol":"AAPL","side":"buy","qty":3,"type":"market","time_in_force":"day","client_order_id":"ios-1"}"#)

        let limit = try #require(check(draft(.sell, amount: "1.25", type: .limit, limit: "190.5", tif: .gtc), held: 2).request)
        #expect(try bodyText(limit) == #"{"symbol":"AAPL","side":"sell","qty":1.25,"type":"limit","limit_price":190.5,"time_in_force":"day","client_order_id":"ios-1"}"#)

        let dollars = try #require(check(draft(kind: .dollars, amount: "250")).request)
        #expect(try bodyText(dollars) == #"{"symbol":"AAPL","side":"buy","notional":250,"type":"market","time_in_force":"day","client_order_id":"ios-1"}"#)
    }
}

// MARK: - Estimates and words

struct TradeWordsTests {
    @Test func estimates() {
        #expect(TradeTicket.estimate(draft(amount: "3"), lastPrice: 190) == 570)
        #expect(TradeTicket.estimate(draft(amount: "3", type: .limit, limit: "180"), lastPrice: 190) == 540)
        #expect(TradeTicket.estimate(draft(kind: .dollars, amount: "250"), lastPrice: nil) == 250)
        #expect(TradeTicket.estimate(draft(amount: "3"), lastPrice: nil) == nil)
        #expect(TradeTicket.estimate(draft(amount: ""), lastPrice: 190) == nil)
        #expect(TradeTicket.estimateLabel(.buy) == "Estimated cost")
        #expect(TradeTicket.estimateLabel(.sell) == "Estimated proceeds")
    }

    @Test func theConfirmControlNamesTheAction() throws {
        #expect(TradeTicket.actionLabel(side: .buy, symbol: "AAPL", qty: "3", notional: nil) == "Buy 3 AAPL")
        #expect(TradeTicket.actionLabel(side: .sell, symbol: "NVDA", qty: nil, notional: 250) == "Sell $250 of NVDA")
        #expect(TradeTicket.actionLabel(side: .sell, symbol: "NVDA", qty: nil, notional: 1250.5) == "Sell $1,250.50 of NVDA")
        let r = try #require(TradeTicket.check(draft(amount: "0.5"), held: nil, clientOrderId: "x").request)
        #expect(r.actionLabel == "Buy 0.5 AAPL")
        #expect(r.sizeText == "0.5 sh")
        let d = try #require(TradeTicket.check(draft(kind: .dollars, amount: "250"), held: nil, clientOrderId: "x").request)
        #expect(d.sizeText == "$250.00")
    }

    @Test func limitPricesReadAsMoney() throws {
        #expect(TradeTicket.priceText(try #require(TradeNumber.parse("225.5"))) == "$225.50")
        #expect(TradeTicket.priceText(try #require(TradeNumber.parse("1250"))) == "$1,250.00")
        #expect(TradeTicket.priceText(try #require(TradeNumber.parse("0.5"))) == "$0.50")
        #expect(TradeTicket.priceText(try #require(TradeNumber.parse("0.1234"))) == "$0.1234")
    }

    @Test func heldQuantityIsExact() {
        #expect(TradeTicket.exactQtyText(12) == "12")
        #expect(TradeTicket.exactQtyText(0.30000000000000004) == "0.3")
        #expect(TradeTicket.exactQtyText(1.123456789) == "1.123456789")
        #expect(TradeTicket.heldText(12.5, symbol: "AAPL") == "You hold 12.5 sh")
        #expect(TradeTicket.heldText(0, symbol: "AAPL") == "You don't hold AAPL in this account")
    }

    @Test func liveMoneyOrPaperIsSaidPlainly() {
        #expect(TradeTicket.modeText(alpaca("a", paper: false)) == "Live money")
        #expect(TradeTicket.modeText(alpaca("a", paper: true)) == "Paper")
        #expect(TradeTicket.modeFootnote(alpaca("a", paper: false)).hasPrefix("Live money."))
    }

    @Test func theInstanceLineNamesRunningInstancesOnTheAccount() {
        let instances = [
            Instance(id: "1", name: "f14-paper", createdBy: "user", runCommand: true, brokerageId: "acct"),
            Instance(id: "2", name: "stopped", createdBy: "user", runCommand: false, brokerageId: "acct"),
            Instance(id: "3", name: "crashed", createdBy: "user", runCommand: true, crashed: true, brokerageId: "acct"),
            Instance(id: "4", name: "elsewhere", createdBy: "user", runCommand: true, brokerageId: "other"),
        ]
        #expect(TradeTicket.instanceWarning(instances, accountId: "acct") == "f14-paper trades this account and may act on this position")
        #expect(TradeTicket.instanceWarning(instances, accountId: "nobody") == nil)
        let two = instances + [Instance(id: "5", name: "strategy-x", createdBy: "ai", runCommand: true, brokerageId: "acct")]
        #expect(TradeTicket.instanceWarning(two, accountId: "acct") == "f14-paper and strategy-x trade this account and may act on this position")
        let three = two + [Instance(id: "6", name: "eb", createdBy: "ai", runCommand: true, brokerageId: "acct")]
        #expect(TradeTicket.instanceWarning(three, accountId: "acct") == "f14-paper, strategy-x and eb trade this account and may act on this position")
    }

    @Test func theDefaultAccountIsTheDashboardsWhenTradable() {
        let accounts = [kalshi("k"), alpaca("live", paper: false), alpaca("paper")]
        let tradable = TradeTicket.tradableAccounts(accounts)
        #expect(tradable.map(\.id) == ["live", "paper"])
        #expect(TradeTicket.defaultAccountId(tradable, dashboardSelected: "paper", route: "live") == "paper")
        #expect(TradeTicket.defaultAccountId(tradable, dashboardSelected: "k", route: "paper") == "paper")
        #expect(TradeTicket.defaultAccountId(tradable, dashboardSelected: "k", route: nil) == "live")
        #expect(TradeTicket.defaultAccountId([], dashboardSelected: "k", route: nil) == nil)
    }

    @Test func clientOrderIdsAreUniqueAndFitTheServersRule() {
        let a = TradeTicket.newClientOrderId()
        let b = TradeTicket.newClientOrderId()
        #expect(a != b)
        #expect(a.hasPrefix("ios-"))
        #expect(a.count <= 48)
        #expect(a.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" })
    }

    @Test func failuresSplitIntoRefusedAndNotConfirmed() {
        #expect(TradeTicket.submitResult(for: ApiError(message: "Alpaca rejected the order: insufficient buying power", statusCode: 400))
            == .rejected("Alpaca rejected the order: insufficient buying power"))
        let unreachable = "Couldn't reach Alpaca. The order was not confirmed — check Orders before trying again"
        #expect(TradeTicket.submitResult(for: ApiError(message: unreachable, statusCode: 502)) == .unconfirmed(unreachable))
        #expect(TradeTicket.submitResult(for: ApiError(message: "Request timed out. Check your connection and try again."))
            == .unconfirmed("Request timed out. Check your connection and try again. \(TradeTicket.unconfirmedTail)"))
        // A server without the routes.
        #expect(TradeTicket.submitResult(for: ApiError(message: "Method Not Allowed", statusCode: 405)) == .rejected(TradeTicket.unsupportedServer))
        #expect(TradeTicket.submitResult(for: ApiError(message: "Not Found", statusCode: 404)) == .rejected(TradeTicket.unsupportedServer))
        #expect(TradeTicket.submitResult(for: ApiError(message: "Brokerage not found", statusCode: 404)) == .rejected("Brokerage not found"))
        #expect(TradeTicket.cancelErrorText(ApiError(message: "Alpaca couldn't cancel this order: order is already filled", statusCode: 400))
            == "Alpaca couldn't cancel this order: order is already filled")
    }

    @Test func onlyWorkingOrdersCancel() {
        #expect(TradeTicket.isCancelable(order(status: "new")))
        #expect(TradeTicket.isCancelable(order(status: "partially_filled")))
        #expect(TradeTicket.isCancelable(order(status: "accepted")))
        #expect(!TradeTicket.isCancelable(order(status: "pending_cancel")))
        #expect(!TradeTicket.isCancelable(order(status: "filled")))
        #expect(!TradeTicket.isCancelable(order(status: "canceled")))
        #expect(!TradeTicket.isCancelable(order("", status: "new")))
    }

    @Test func theCancelConfirmSaysWhichOrder() {
        #expect(TradeTicket.orderSummary(order()) == "Buy 3 AAPL, limit $190.00")
        #expect(TradeTicket.orderSummary(order(side: "sell", type: "market", qty: nil, notional: 250, limitPrice: nil)) == "Sell $250 of AAPL, market")
        #expect(TradeTicket.cancelPromptBody(order(), account: alpaca("p", "Paper Test")) == "Buy 3 AAPL, limit $190.00, in Paper Test (paper). Anything that hasn't filled is canceled.")
        #expect(TradeTicket.cancelPromptBody(order(), account: alpaca("l", "Main", paper: false)) == "Buy 3 AAPL, limit $190.00, in Main (live money). Anything that hasn't filled is canceled.")
        #expect(TradeTicket.cancelPromptBody(order(), account: nil) == "Buy 3 AAPL, limit $190.00. Anything that hasn't filled is canceled.")
    }
}

// MARK: - Repository

struct TradeRepositoryTests {
    private let orderJSON = #"{"brokerage_id": "b1", "order": {"id": "alp-9", "symbol": "AAPL", "side": "buy", "type": "market", "status": "accepted", "qty": 3.0, "filled_qty": 0.0}}"#

    @Test func placesTheOrderWithTheContractsBody() async throws {
        let stub = DataStub(json: orderJSON)
        let request = try #require(TradeTicket.check(draft(), held: nil, clientOrderId: "ios-abc").request)
        let placed = try await TradeRepository(client: stub.client).placeOrder("b1", request)
        #expect(placed.id == "alp-9")
        #expect(placed.status == "accepted")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/brokerages/b1/orders")
        #expect(stub.last?.jsonBody["client_order_id"] == "ios-abc")
        #expect(stub.last?.jsonBody["qty"] == .int(3))
        #expect(stub.last?.jsonBody["symbol"] == "AAPL")
    }

    @Test func anAnswerWithoutAnOrderIsNotConfirmed() async throws {
        let stub = DataStub(json: #"{"brokerage_id": "b1"}"#)
        let request = try #require(TradeTicket.check(draft(), held: nil, clientOrderId: "x").request)
        do {
            _ = try await TradeRepository(client: stub.client).placeOrder("b1", request)
            Issue.record("expected a throw")
        } catch {
            #expect(TradeTicket.submitResult(for: error).message?.contains("not confirmed") == true)
            guard case .unconfirmed = TradeTicket.submitResult(for: error) else {
                Issue.record("expected unconfirmed")
                return
            }
        }
    }

    @Test func theServersRefusalComesBackAsItsDetail() async throws {
        let stub = DataStub(status: 400, json: #"{"detail": "Alpaca rejected the order: market is closed"}"#)
        let request = try #require(TradeTicket.check(draft(), held: nil, clientOrderId: "x").request)
        await #expect(throws: ApiError(message: "Alpaca rejected the order: market is closed", statusCode: 400)) {
            _ = try await TradeRepository(client: stub.client).placeOrder("b1", request)
        }
    }

    @Test func cancelsWithADelete() async throws {
        let stub = DataStub(json: #"{"brokerage_id": "b1", "order_id": "o-1", "canceled": true}"#)
        try await TradeRepository(client: stub.client).cancelOrder("b1", orderId: "o-1")
        #expect(stub.last?.method == "DELETE")
        #expect(stub.last?.path == "/brokerages/b1/orders/o-1")
    }

    @Test func openOrdersComeFromTheOrderBook() async throws {
        let stub = DataStub(json: #"{"supported": true, "open": [{"id": "1", "symbol": "AAPL", "side": "buy", "status": "new"}], "recent": []}"#)
        let open = try await TradeRepository(client: stub.client).openOrders("b1")
        #expect(open.map(\.id) == ["1"])
        #expect(stub.last?.path == "/brokerages/b1/order-book")
        #expect(stub.last?.queryItems == ["recent_limit": "1"])
    }
}

// MARK: - Ticket model

struct TradeTicketModelTests {
    private let accounts = [alpaca("live", "Main", paper: false), alpaca("paper", "Paper Test")]

    private func model(
        _ fake: FakeTradeBackend,
        side: TradeSide = .buy,
        accountId: String = "paper",
        changed: @escaping (String) -> Void = { _ in }
    ) -> TradeTicketModel {
        TradeTicketModel(
            symbol: "aapl", side: side, accounts: accounts, accountId: accountId,
            lastPrice: { 190 }, backend: { fake }, clientOrderId: "ios-fixed", onOrdersChanged: changed
        )
    }

    @Test func readsTheHeldQuantityPerAccount() async {
        let fake = FakeTradeBackend()
        fake.holdingsByAccount = [
            "paper": AccountHoldings(cash: 100, positions: [position("MSFT", 1), position("AAPL", 12.5)]),
            "live": AccountHoldings(cash: 100, positions: []),
        ]
        let m = model(fake, side: .sell)
        #expect(m.symbol == "AAPL")
        await m.loadHeld()
        #expect(m.heldQty == 12.5)
        #expect(m.canSellAll)
        m.selectAccount("live")
        await m.loadHeld()
        #expect(m.heldQty == 0)
        #expect(!m.canSellAll)
        // Known accounts are not asked again unless forced.
        m.selectAccount("paper")
        await m.loadHeld()
        #expect(fake.holdingsCalls == ["paper", "live"])
        // An unknown account is ignored.
        m.selectAccount("nope")
        #expect(m.accountId == "paper")
    }

    @Test func aFailedHoldingsReadShowsAndRetries() async {
        let fake = FakeTradeBackend()
        fake.holdingsError = ApiError(message: "Alpaca unreachable", statusCode: 502)
        let m = model(fake, side: .sell)
        await m.loadHeld()
        #expect(m.heldState?.error != nil)
        #expect(m.check == .incomplete)
        fake.holdingsError = nil
        fake.holdingsByAccount["paper"] = AccountHoldings(cash: nil, positions: [position("AAPL", 2)])
        await m.loadHeld(force: true)
        #expect(m.heldQty == 2)
    }

    @Test func sellAllFillsTheExactHolding() async {
        let fake = FakeTradeBackend()
        fake.holdingsByAccount["paper"] = AccountHoldings(cash: nil, positions: [position("AAPL", 0.123456789)])
        let m = model(fake, side: .sell)
        m.setAmountKind(.dollars)
        await m.loadHeld()
        m.sellAll()
        #expect(m.draft.amountKind == .shares)
        #expect(m.draft.amountText == "0.123456789")
        #expect(m.check.request?.qty?.text == "0.123456789")
        #expect(m.check.request?.timeInForce == .day)
    }

    @Test func aNineDecimalHoldingGoesOutExactly() async throws {
        // Alpaca fills dollar orders to 9 decimals; the positions route sends
        // the float, and Sell all must send those same digits.
        let decoded = AccountPosition(json: try JSON(data: Data(#"{"symbol": "AAPL", "qty": 1.358695652}"#.utf8)))
        let fake = FakeTradeBackend()
        fake.holdingsByAccount["paper"] = AccountHoldings(cash: nil, positions: [decoded])
        let m = model(fake, side: .sell)
        await m.loadHeld()
        m.sellAll()
        #expect(m.draft.amountText == "1.358695652")
        let request = try #require(m.reviewItem?.request)
        #expect(try bodyText(request).contains(#""qty":1.358695652,"#))
        #expect(request.actionLabel == "Sell 1.358695652 AAPL")
    }

    @Test func switchingUnitsClearsTheAmount() {
        let m = model(FakeTradeBackend())
        m.draft.amountText = "3"
        m.setAmountKind(.dollars)
        #expect(m.draft.amountText == "")
        m.draft.amountText = "250"
        m.setAmountKind(.dollars)
        #expect(m.draft.amountText == "250")
    }

    @Test func theReviewItemCarriesTheAccountAndAnEstimate() {
        let m = model(FakeTradeBackend())
        #expect(m.reviewItem == nil)
        m.draft.amountText = "3"
        #expect(m.reviewItem?.account.id == "paper")
        #expect(m.reviewItem?.request.clientOrderId == "ios-fixed")
        #expect(m.estimate == 570)
    }

    @Test func aPlacedOrderSendsOnceAndTellsTheDashboard() async throws {
        let fake = FakeTradeBackend()
        var changed: [String] = []
        let m = model(fake) { changed.append($0) }
        m.draft.amountText = "3"
        let item = try #require(m.reviewItem)
        let result = await m.submit(item)
        #expect(result?.order?.id == "alp-1")
        #expect(m.isPlaced)
        #expect(fake.placeCalls.count == 1)
        #expect(fake.placeCalls.first?.account == "paper")
        #expect(fake.placeCalls.first?.request.clientOrderId == "ios-fixed")
        #expect(changed == ["paper"])
        // Never twice.
        _ = await m.submit(item)
        #expect(fake.placeCalls.count == 1)
    }

    @Test func aSecondSwipeWhileSendingSendsNothing() async throws {
        let fake = FakeTradeBackend()
        let gate = SwingTestGate()
        fake.placeGate = gate
        let m = model(fake)
        m.draft.amountText = "3"
        let item = try #require(m.reviewItem)
        let first = Task { await m.submit(item) }
        let second = Task { await m.submit(item) }
        gate.open()
        _ = await first.value
        _ = await second.value
        #expect(fake.placeCalls.count == 1)
    }

    @Test func aRefusalStaysOnTheTicketAndAnEditClearsIt() async throws {
        let fake = FakeTradeBackend()
        fake.placeError = ApiError(message: "Alpaca rejected the order: insufficient buying power", statusCode: 400)
        var changed: [String] = []
        let m = model(fake) { changed.append($0) }
        m.draft.amountText = "3"
        let item = try #require(m.reviewItem)
        let result = await m.submit(item)
        #expect(result == .rejected("Alpaca rejected the order: insufficient buying power"))
        #expect(m.result?.message == "Alpaca rejected the order: insufficient buying power")
        #expect(changed.isEmpty)
        // A swipe again resends with the same id; nothing retries on its own.
        fake.placeError = nil
        _ = await m.submit(item)
        #expect(fake.placeCalls.map { $0.request.clientOrderId } == ["ios-fixed", "ios-fixed"])
        #expect(m.isPlaced)
    }

    @Test func anEditClearsAFailedResult() async throws {
        let fake = FakeTradeBackend()
        fake.placeError = ApiError(message: "nope", statusCode: 400)
        let m = model(fake)
        m.draft.amountText = "3"
        _ = await m.submit(try #require(m.reviewItem))
        #expect(m.result != nil)
        m.draft.amountText = "2"
        #expect(m.result == nil)
    }

    @Test func anUnconfirmedSendTellsTheDashboardToLook() async throws {
        let fake = FakeTradeBackend()
        fake.placeError = ApiError(message: "Couldn't reach Alpaca. The order was not confirmed — check Orders before trying again", statusCode: 502)
        var changed: [String] = []
        let m = model(fake) { changed.append($0) }
        m.draft.amountText = "3"
        let result = await m.submit(try #require(m.reviewItem))
        guard case .unconfirmed = result else {
            Issue.record("expected unconfirmed, got \(String(describing: result))")
            return
        }
        #expect(changed == ["paper"])
        #expect(!m.isPlaced)
    }

    @Test func anItemFromAnotherTicketIsNotSent() async throws {
        let fake = FakeTradeBackend()
        let m = model(fake)
        m.draft.amountText = "3"
        let item = try #require(m.reviewItem)
        let foreign = TradeReviewItem(
            request: try #require(TradeTicket.check(m.draft, held: nil, clientOrderId: "ios-other").request),
            account: item.account
        )
        #expect(await m.submit(foreign) == nil)
        #expect(fake.placeCalls.isEmpty)
    }

    @Test func theInstanceLineFollowsTheAccount() async {
        let fake = FakeTradeBackend()
        fake.instanceList = [Instance(id: "1", name: "f14-paper", createdBy: "user", runCommand: true, brokerageId: "paper")]
        let m = model(fake)
        #expect(m.instanceWarning == nil)
        await m.loadInstances()
        #expect(m.instanceWarning == "f14-paper trades this account and may act on this position")
        m.selectAccount("live")
        #expect(m.instanceWarning == nil)
    }
}

// MARK: - Stock screen model

struct StockTradeModelTests {
    private let accounts = [alpaca("live", "Main", paper: false), alpaca("paper", "Paper Test")]

    private func model(_ fake: FakeTradeBackend, changed: @escaping (String) -> Void = { _ in }) -> StockTradeModel {
        StockTradeModel(symbol: "aapl", backend: { fake }, onOrdersChanged: changed, sleep: { _ in })
    }

    @Test func openOrdersAreTheSymbolsOnEveryAccountNewestFirst() async {
        let fake = FakeTradeBackend()
        let t0 = Date(timeIntervalSince1970: 1_000)
        fake.openByAccount = [
            "live": [order("l1", submittedAt: t0), order("x", "MSFT", submittedAt: t0)],
            "paper": [order("p1", submittedAt: t0.addingTimeInterval(60))],
        ]
        let m = model(fake)
        #expect(m.openOrders == nil)
        await m.loadOpenOrders(accounts)
        #expect(m.openOrders?.map(\.order.id) == ["p1", "l1"])
        #expect(m.openOrders?.first?.account.id == "paper")
        #expect(m.openOrders?.first?.id == "paper/p1")
    }

    @Test func oneFailingAccountIsSkippedAndAllFailingShowsAnError() async {
        let fake = FakeTradeBackend()
        fake.openByAccount = ["paper": [order("p1")]]
        fake.failingOpenAccounts = ["live"]
        let m = model(fake)
        await m.loadOpenOrders(accounts)
        #expect(m.openOrders?.map(\.order.id) == ["p1"])
        #expect(!m.openOrdersFailed)

        let failing = FakeTradeBackend()
        failing.failingOpenAccounts = ["live", "paper"]
        let n = model(failing)
        await n.loadOpenOrders(accounts)
        #expect(n.openOrders == nil)
        #expect(n.openOrdersFailed)
    }

    @Test func aCancelGoesThroughItsConfirmThenReReads() async throws {
        let fake = FakeTradeBackend()
        fake.openByAccount = ["paper": [order("p1")]]
        var changed: [String] = []
        let m = model(fake) { changed.append($0) }
        await m.loadOpenOrders(accounts)
        let item = try #require(m.openOrders?.first)
        m.requestCancel(item)
        let prompt = try #require(m.cancelPrompt)
        #expect(prompt.brokerageId == "paper")
        #expect(prompt.message == "Buy 3 AAPL, limit $190.00, in Paper Test (paper). Anything that hasn't filled is canceled.")
        await m.cancel(brokerageId: prompt.brokerageId, orderId: prompt.order.id)
        #expect(fake.cancelCalls.map { $0.order } == ["p1"])
        #expect(changed == ["paper"])
        #expect(m.toast != nil)
        #expect(m.openOrders?.isEmpty == true)
        #expect(m.cancelError == nil)
    }

    @Test func aFailedCancelShowsTheServersDetail() async throws {
        let fake = FakeTradeBackend()
        fake.openByAccount = ["paper": [order("p1")]]
        fake.cancelError = ApiError(message: "Alpaca couldn't cancel this order: order is already filled", statusCode: 400)
        let m = model(fake)
        await m.loadOpenOrders(accounts)
        await m.cancel(brokerageId: "paper", orderId: "p1")
        #expect(m.cancelError == "Alpaca couldn't cancel this order: order is already filled")
        #expect(m.toast == nil)
    }

    @Test func aClosedOrderGetsNoPrompt() {
        let m = model(FakeTradeBackend())
        m.requestCancel(AccountOrder(account: accounts[1], order: order(status: "filled")))
        #expect(m.cancelPrompt == nil)
    }

    @Test func afterATradeThePositionIsReReadForTheScreensAccount() async {
        let fake = FakeTradeBackend()
        fake.holdingsByAccount["paper"] = AccountHoldings(cash: 900, positions: [position("AAPL", 3, value: 100)])
        let m = model(fake)
        await m.loadOpenOrders(accounts)
        await m.refreshAfterTrade(accountId: "paper", routeAccountId: "paper")
        #expect(m.livePosition?.position?.qty == 3)
        #expect(m.livePosition?.total == 1000)
        // Two passes: now and once more after the pause.
        #expect(fake.holdingsCalls == ["paper", "paper"])

        // A trade on another account leaves this screen's position alone.
        let other = FakeTradeBackend()
        let n = model(other)
        await n.refreshAfterTrade(accountId: "live", routeAccountId: "paper")
        #expect(other.holdingsCalls.isEmpty)
        #expect(n.livePosition == nil)
    }

    @Test func aSoldOutPositionReadsAsNone() async {
        let fake = FakeTradeBackend()
        fake.holdingsByAccount["paper"] = AccountHoldings(cash: 1000, positions: [])
        let m = model(fake)
        await m.loadLivePosition("paper")
        #expect(m.livePosition != nil)
        #expect(m.livePosition?.position == nil)
    }
}

// MARK: - Dashboard Orders cancel

struct DashboardOrdersCancelTests {
    private let book = #"{"supported": true, "open": [{"id": "o1", "symbol": "QQQ", "side": "buy", "type": "limit", "status": "new", "qty": 3, "limit_price": 510}], "recent": []}"#

    @Test func withoutATradeBackendTheSectionStaysReadOnly() async {
        let stub = DataStub(json: book)
        let m = AccountOrdersModel(brokerageId: "b1", repository: { DashboardRepository(client: stub.client) })
        await m.reload()
        guard let o = m.orders.first else {
            Issue.record("expected an open order")
            return
        }
        #expect(!m.canCancel(o))
        m.requestCancel(o)
        #expect(m.cancelPrompt == nil)
    }

    @Test func anOpenOrderCancelsThenTheBookReloads() async throws {
        let stub = DataStub(json: book)
        let fake = FakeTradeBackend()
        let m = AccountOrdersModel(brokerageId: "b1", repository: { DashboardRepository(client: stub.client) }, trade: { fake })
        await m.reload()
        let o = try #require(m.orders.first)
        #expect(m.canCancel(o))
        m.requestCancel(o)
        #expect(m.cancelPrompt?.message == "Buy 3 QQQ, limit $510.00. Anything that hasn't filled is canceled.")
        let before = stub.requests.count
        await m.cancel(o.id)
        #expect(fake.cancelCalls.map { $0.account } == ["b1"])
        #expect(fake.cancelCalls.map { $0.order } == ["o1"])
        #expect(stub.requests.count == before + 1)
        #expect(m.cancelError == nil)
    }

    @Test func aFailedCancelShowsInTheSection() async throws {
        let stub = DataStub(json: book)
        let fake = FakeTradeBackend()
        fake.cancelError = ApiError(message: "Alpaca couldn't cancel this order: order is already filled", statusCode: 400)
        let m = AccountOrdersModel(brokerageId: "b1", repository: { DashboardRepository(client: stub.client) }, trade: { fake })
        await m.reload()
        await m.cancel("o1")
        #expect(m.cancelError == "Alpaca couldn't cancel this order: order is already filled")
    }
}

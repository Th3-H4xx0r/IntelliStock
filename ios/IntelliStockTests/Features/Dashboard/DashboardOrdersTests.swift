import Foundation
import Testing
@testable import IntelliStock

// The dashboard's read-only Orders section: decoding the order book, the
// request, the poller's mode and failure rules, and the row text.

private func utcCalendar() -> Calendar {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    return cal
}

private func utc(_ y: Int, _ mo: Int, _ d: Int, _ h: Int = 0, _ mi: Int = 0, _ s: Int = 0) -> Date {
    utcCalendar().date(from: DateComponents(year: y, month: mo, day: d, hour: h, minute: mi, second: s))!
}

private func order(
    _ symbol: String = "QQQ",
    side: String = "buy",
    type: String? = "limit",
    status: String = "new",
    qty: Double? = 3,
    notional: Double? = nil,
    filledQty: Double? = nil,
    filledAvgPrice: Double? = nil,
    limitPrice: Double? = nil,
    stopPrice: Double? = nil,
    submittedAt: Date? = nil,
    filledAt: Date? = nil,
    canceledAt: Date? = nil,
    assetClass: String? = nil
) -> BrokerOrder {
    BrokerOrder(
        id: UUID().uuidString, symbol: symbol, side: side, type: type, status: status,
        qty: qty, notional: notional, filledQty: filledQty, filledAvgPrice: filledAvgPrice,
        limitPrice: limitPrice, stopPrice: stopPrice, submittedAt: submittedAt,
        filledAt: filledAt, canceledAt: canceledAt, assetClass: assetClass
    )
}

/// An order-book body with `open` and `recent` orders named by symbol.
private func bookJSON(open: [String] = [], recent: [String] = [], supported: Bool = true) -> String {
    func list(_ symbols: [String], _ status: String) -> String {
        symbols.enumerated()
            .map { #"{"id": "\#($0.element)-\#($0.offset)", "symbol": "\#($0.element)", "side": "buy", "status": "\#(status)"}"# }
            .joined(separator: ", ")
    }
    return #"{"brokerage_id": "b1", "supported": \#(supported), "open": [\#(list(open, "new"))], "recent": [\#(list(recent, "filled"))]}"#
}

// MARK: - Decoding

struct BrokerOrderDecodingTests {
    @Test func decodesTheContractExample() throws {
        let json = try JSON(data: Data(#"""
        {"id": "0b2c", "symbol": "QQQ", "side": "buy", "type": "limit", "status": "partially_filled",
         "qty": 3.0, "notional": null, "filled_qty": 1.0, "filled_avg_price": 512.4,
         "limit_price": 510.0, "stop_price": null, "time_in_force": "day",
         "submitted_at": "2026-11-12T14:31:05.123456Z", "filled_at": null, "canceled_at": null,
         "asset_class": "us_equity"}
        """#.utf8))
        let o = BrokerOrder(json: json)
        #expect(o.id == "0b2c")
        #expect(o.symbol == "QQQ")
        #expect(o.side == "buy")
        #expect(o.type == "limit")
        #expect(o.status == "partially_filled")
        #expect(o.qty == 3)
        #expect(o.notional == nil)
        #expect(o.filledQty == 1)
        #expect(o.filledAvgPrice == 512.4)
        #expect(o.limitPrice == 510)
        #expect(o.stopPrice == nil)
        #expect(o.timeInForce == "day")
        #expect(close(o.submittedAt?.timeIntervalSince1970, utc(2026, 11, 12, 14, 31, 5).timeIntervalSince1970 + 0.123456, 1e-5))
        #expect(o.filledAt == nil)
        #expect(o.canceledAt == nil)
        #expect(o.assetClass == "us_equity")
        #expect(!o.isOption)
        #expect(!o.isNotional)
        #expect(o.hasFill)
    }

    @Test func missingFieldsAndNullsFallBackInsteadOfFailing() throws {
        let o = BrokerOrder(json: try JSON(data: Data(#"{"id": "x", "type": null, "qty": null, "submitted_at": "garbage"}"#.utf8)))
        #expect(o.id == "x")
        #expect(o.symbol == "")
        #expect(o.side == "")
        #expect(o.status == "")
        #expect(o.type == nil)
        #expect(o.qty == nil)
        #expect(o.filledQty == nil)
        #expect(o.limitPrice == nil)
        #expect(o.submittedAt == nil)
        #expect(o.assetClass == nil)
        #expect(!o.hasFill)
        // An empty object decodes too.
        #expect(BrokerOrder(json: .object([:])).id == "")
    }

    @Test func alpacasNanosecondTimestampsParse() throws {
        let o = BrokerOrder(json: try JSON(data: Data(#"{"id": "t", "filled_at": "2026-11-12T14:31:05.123456789Z", "canceled_at": "2026-11-12T14:31:05Z"}"#.utf8)))
        #expect(close(o.filledAt?.timeIntervalSince1970, utc(2026, 11, 12, 14, 31, 5).timeIntervalSince1970 + 0.123456, 1e-5))
        #expect(o.canceledAt == utc(2026, 11, 12, 14, 31, 5))
    }

    @Test func notionalOrderHasNoQuantity() throws {
        let o = BrokerOrder(json: try JSON(data: Data(#"{"id": "n", "symbol": "SPY", "side": "buy", "type": "market", "status": "new", "qty": null, "notional": 250}"#.utf8)))
        #expect(o.qty == nil)
        #expect(o.notional == 250)
        #expect(o.isNotional)
    }

    @Test func numericStringsAndCaseAreTolerated() throws {
        let o = BrokerOrder(json: try JSON(data: Data(#"{"id": "s", "symbol": " AAPL ", "side": "SELL", "type": "Stop_Limit", "status": "FILLED", "qty": "5", "filled_qty": "5", "filled_avg_price": "190.25", "stop_price": "189"}"#.utf8)))
        #expect(o.symbol == "AAPL")
        #expect(o.side == "sell")
        #expect(o.type == "stop_limit")
        #expect(o.status == "filled")
        #expect(o.qty == 5)
        #expect(o.filledAvgPrice == 190.25)
        #expect(o.stopPrice == 189)
    }

    @Test func optionsComeFromAssetClassOrTheOccSymbol() {
        #expect(order("QQQ261120C00510000", assetClass: "us_option").isOption)
        #expect(order("QQQ261120C00510000").isOption)
        #expect(order("QQQ261120C00510000").multiplier == 100)
        #expect(!order("QQQ", assetClass: "us_equity").isOption)
        #expect(order("QQQ").multiplier == 1)
    }

    @Test func bookReadsBothListsAndSupported() throws {
        let book = OrderBook(json: try JSON(data: Data(bookJSON(open: ["A", "B"], recent: ["C"]).utf8)))
        #expect(book.supported)
        #expect(book.open.map(\.symbol) == ["A", "B"])
        #expect(book.recent.map(\.symbol) == ["C"])
        #expect(book.recent.first?.status == "filled")

        let kalshi = OrderBook(json: try JSON(data: Data(#"{"brokerage_id": "k", "supported": false, "open": [], "recent": []}"#.utf8)))
        #expect(!kalshi.supported)
        #expect(kalshi.open.isEmpty && kalshi.recent.isEmpty)

        // No `supported`, missing lists, and non-object entries.
        let sparse = OrderBook(json: try JSON(data: Data(#"{"open": [1, null, {"id": "o", "symbol": "Z", "side": "buy", "status": "new"}]}"#.utf8)))
        #expect(sparse.supported)
        #expect(sparse.open.map(\.symbol) == ["Z"])
        #expect(sparse.recent.isEmpty)
    }

    @Test func onlyAlpacaAccountsHaveAnOrderBook() {
        #expect(BrokerageAccount(id: "a", accountName: "", brokerageType: "alpaca", status: "active").hasOrderBook)
        #expect(BrokerageAccount(id: "a", accountName: "", brokerageType: "Alpaca", status: "active").hasOrderBook)
        #expect(!BrokerageAccount(id: "k", accountName: "", brokerageType: "kalshi", status: "active").hasOrderBook)
        #expect(!BrokerageAccount(id: "c", accountName: "", brokerageType: "binanceus", status: "active").hasOrderBook)
    }
}

// MARK: - Repository

struct OrderBookRepositoryTests {
    @Test func requestsTheOrderBookWithTheRecentLimit() async throws {
        let stub = DataStub(json: bookJSON(open: ["QQQ"]))
        let book = try await DashboardRepository(client: stub.client).orderBook("b1")
        #expect(book.open.map(\.symbol) == ["QQQ"])
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/brokerages/b1/order-book")
        #expect(stub.last?.queryItems == ["recent_limit": "25"])
        _ = try await DashboardRepository(client: stub.client).orderBook("b2", recentLimit: 5)
        #expect(stub.last?.path == "/brokerages/b2/order-book")
        #expect(stub.last?.queryItems == ["recent_limit": "5"])
    }

    @Test func aServerWithoutTheEndpointReadsAsUnsupported() async throws {
        let stub = DataStub(status: 404, json: #"{"detail": "Not Found"}"#)
        let book = try await DashboardRepository(client: stub.client).orderBook("b1")
        #expect(!book.supported)
    }

    @Test func aBrokerFailureThrowsTheServersMessage() async {
        let stub = DataStub(status: 502, json: #"{"detail": "Alpaca unreachable"}"#)
        await #expect(throws: ApiError(message: "Alpaca unreachable", statusCode: 502)) {
            _ = try await DashboardRepository(client: stub.client).orderBook("b1")
        }
    }
}

// MARK: - Model

struct AccountOrdersModelTests {
    private func model(_ stub: DataStub) -> AccountOrdersModel {
        AccountOrdersModel(brokerageId: "b1", repository: { DashboardRepository(client: stub.client) })
    }

    @Test func initialModeIsOpenWhenAnythingIsWorkingElseRecent() {
        #expect(OrdersMode.initial(for: OrderBook(open: [order()], recent: [])) == .open)
        #expect(OrdersMode.initial(for: OrderBook(open: [], recent: [order(status: "filled")])) == .recent)
        #expect(OrdersMode.initial(for: OrderBook()) == .recent)
    }

    @Test func modeLabelsAndEmptyText() {
        #expect(OrdersMode.open.label(openCount: 2) == "Open (2)")
        #expect(OrdersMode.open.label(openCount: 0) == "Open")
        #expect(OrdersMode.recent.label(openCount: 2) == "Recent")
        #expect(OrdersMode.open.emptyText == "No open orders")
        #expect(OrdersMode.recent.emptyText == "No recent orders")
    }

    @Test func firstLoadPicksTheModeAndLaterPollsNeverFlipIt() async {
        let stub = DataStub(json: bookJSON(recent: ["GLD"]))
        let m = model(stub)
        #expect(m.isHidden)
        await m.reload()
        #expect(m.mode == .recent)
        #expect(m.orders.map(\.symbol) == ["GLD"])
        // An order starts working: the list updates, the mode stays.
        stub.respond(json: bookJSON(open: ["QQQ"], recent: ["GLD"]))
        await m.refresh()
        #expect(m.mode == .recent)
        #expect(m.openCount == 1)
        // The user's pick sticks through a poll with nothing open.
        m.select(.open)
        stub.respond(json: bookJSON(recent: ["GLD"]))
        await m.refresh()
        #expect(m.mode == .open)
        #expect(m.orders.isEmpty)
    }

    @Test func aPickBeforeTheFirstLoadIsKept() async {
        let stub = DataStub(json: bookJSON(open: ["QQQ"]))
        let m = model(stub)
        m.select(.recent)
        await m.reload()
        #expect(m.mode == .recent)
    }

    @Test func aFailedFirstLoadShowsAnErrorThenAPollRecovers() async {
        let stub = DataStub(status: 502, json: #"{"detail": "Alpaca unreachable"}"#)
        let m = model(stub)
        await m.reload()
        #expect(m.book.errorMessage == "Alpaca unreachable")
        #expect(!m.isHidden)
        stub.respond(json: bookJSON(open: ["QQQ"]))
        await m.refresh()
        #expect(m.book.value?.open.map(\.symbol) == ["QQQ"])
        #expect(m.mode == .open)
    }

    @Test func aFailedPollKeepsTheLastGoodData() async {
        let stub = DataStub(json: bookJSON(open: ["QQQ"]))
        let m = model(stub)
        await m.reload()
        stub.respond(status: 502, json: #"{"detail": "boom"}"#)
        await m.refresh()
        #expect(m.book.value?.open.map(\.symbol) == ["QQQ"])
        stub.fail(.notConnectedToInternet)
        await m.refresh()
        #expect(m.book.value?.open.map(\.symbol) == ["QQQ"])
    }

    @Test func anUnsupportedAccountHidesAndIsNotPolledAgain() async {
        let stub = DataStub(json: bookJSON(supported: false))
        let m = model(stub)
        await m.reload()
        #expect(m.isHidden)
        let asked = stub.requests.count
        await m.refresh()
        #expect(stub.requests.count == asked)
        // Pull to refresh still asks.
        await m.reload()
        #expect(stub.requests.count == asked + 1)
    }

    @Test func showsTenRowsUntilShowAllPerMode() async {
        let symbols = (1...14).map { "S\($0)" }
        let stub = DataStub(json: bookJSON(open: ["QQQ"], recent: symbols))
        let m = model(stub)
        await m.reload()
        #expect(m.visibleOrders.count == 1)
        #expect(m.hiddenCount == 0)
        m.select(.recent)
        #expect(m.visibleOrders.map(\.symbol) == Array(symbols.prefix(10)))
        #expect(m.hiddenCount == 4)
        m.showAll()
        #expect(m.visibleOrders.count == 14)
        #expect(m.hiddenCount == 0)
        // Expanding Recent leaves Open as it was.
        #expect(!m.expanded.contains(.open))
    }

    @Test func pollRunsEveryThirtySeconds() async {
        let stub = DataStub(json: bookJSON())
        let clock = ManualClock()
        let m = model(stub)
        let task = Task { await m.poll(lifecycle: nil, sleep: clock.sleep) }
        #expect(await eventually { m.book.value != nil })
        await clock.advance(by: .seconds(1))
        #expect(clock.requested.first == .seconds(30))
        task.cancel()
    }
}

// MARK: - Row text

struct OrderRowTextTests {
    private let now = utc(2026, 11, 12, 16, 0)
    private let cal = utcCalendar()

    private func text(_ o: BrokerOrder) -> OrderRowText {
        OrderRowText(o, now: now, calendar: cal)
    }

    @Test func sentenceCasesSideAndType() {
        #expect(OrderRowText.sentenceCase("stop_limit") == "Stop limit")
        #expect(OrderRowText.sentenceCase("TRAILING_STOP") == "Trailing stop")
        #expect(OrderRowText.sentenceCase("") == "")
        #expect(OrderRowText.sideAndType(order(side: "buy", type: "limit")) == "Buy · Limit")
        #expect(OrderRowText.sideAndType(order(side: "sell", type: "trailing_stop")) == "Sell · Trailing stop")
        #expect(OrderRowText.sideAndType(order(side: "sell", type: nil)) == "Sell")
        #expect(OrderRowText.sideAndType(order(side: "", type: nil)) == nil)
    }

    @Test func subtitleIsSideTypeAndSize() {
        #expect(text(order(side: "buy", type: "market", status: "filled", qty: 2, filledQty: 2, filledAvgPrice: 241.1)).subtitle == "Buy · Market · 2 sh")
        #expect(text(order(side: "sell", type: "trailing_stop", qty: 10)).subtitle == "Sell · Trailing stop · 10 sh")
        #expect(text(order(side: "buy", type: "limit", status: "partially_filled", qty: 3, filledQty: 1)).subtitle == "Buy · Limit · 1 of 3 sh")
        #expect(text(order("QQQ261120C00510000", side: "buy", type: "limit", qty: 2, assetClass: "us_option")).subtitle == "Buy · Limit · 2 ct")
        #expect(text(order("SPY", side: "buy", type: "market", qty: nil, notional: 250)).subtitle == "Buy · Market · $250.00")
        // Nothing known about the size: side · type alone.
        #expect(text(order(side: "sell", type: nil, qty: nil)).subtitle == "Sell")
        #expect(text(order(side: "", type: nil, qty: nil)).subtitle == nil)
    }

    @Test func qtyNumbersFollowTheWebRounding() {
        #expect(OrderRowText.qtyNumber(3) == "3")
        #expect(OrderRowText.qtyNumber(1.5) == "1.5")
        #expect(OrderRowText.qtyNumber(0.4925) == "0.4925")
        #expect(OrderRowText.qtyNumber(0.123456) == "0.1235")
        #expect(OrderRowText.qtyNumber(22.4) == "22.4")
        #expect(OrderRowText.qtyNumber(12.3456) == "12.35")
        #expect(OrderRowText.qtyNumber(10) == "10")
        #expect(OrderRowText.qtyNumber(150.6) == "151")
        #expect(OrderRowText.qtyNumber(100) == "100")
    }

    @Test func sizeMatchesTheWebsOrderQtyLabel() {
        #expect(OrderRowText.sizeText(order(qty: 3)) == "3 sh")
        #expect(OrderRowText.sizeText(order(qty: 0.4925)) == "0.4925 sh")
        #expect(OrderRowText.sizeText(order(status: "filled", qty: 3, filledQty: 3)) == "3 sh")
        // Options count contracts, by asset class or by the OCC symbol.
        #expect(OrderRowText.sizeText(order("QQQ261120C00510000", qty: 2, assetClass: "us_option")) == "2 ct")
        #expect(OrderRowText.sizeText(order("QQQ261120C00510000", qty: 1)) == "1 ct")
        // A partial fill still working.
        #expect(OrderRowText.sizeText(order(status: "partially_filled", qty: 3, filledQty: 1)) == "1 of 3 sh")
        #expect(OrderRowText.sizeText(order(status: "done_for_day", qty: 3, filledQty: 2)) == "2 of 3 sh")
        // A closed partial shows what was ordered; its status says what filled.
        #expect(OrderRowText.sizeText(order(status: "canceled", qty: 10, filledQty: 4)) == "10 sh")
        #expect(OrderRowText.sizeText(order(status: "expired", qty: 8, filledQty: 2)) == "8 sh")
        // A dollar order: the amount, then the shares once any filled.
        #expect(OrderRowText.sizeText(order(qty: nil, notional: 250)) == "$250.00")
        #expect(OrderRowText.sizeText(order(status: "filled", qty: nil, notional: 250, filledQty: 0.3998)) == "0.3998 sh")
        #expect(OrderRowText.sizeText(order(qty: nil)) == nil)
    }

    @Test func figureIsTheFillThenPriceThenMarket() {
        // Filled: avg × filled qty.
        #expect(text(order(status: "filled", qty: 3, filledQty: 3, filledAvgPrice: 512.4, limitPrice: 510)).value == "$1,537.20")
        // Partially filled: what has filled so far.
        #expect(text(order(status: "partially_filled", qty: 3, filledQty: 1, filledAvgPrice: 512.4, limitPrice: 510)).value == "$512.40")
        // An option fill is × 100.
        #expect(text(order("QQQ261120C00510000", status: "filled", qty: 2, filledQty: 2, filledAvgPrice: 1.25, assetClass: "us_option")).value == "$250.00")
        // A dollar order's amount is its size on the left; a filled one shows its fill.
        #expect(text(order("SPY", type: "market", qty: nil, notional: 250)).value == "Market")
        #expect(text(order("SPY", type: "market", status: "filled", qty: nil, notional: 250, filledQty: 0.4, filledAvgPrice: 625)).value == "$250.00")
        // Limit, then stop.
        #expect(text(order(type: "limit", limitPrice: 510)).value == "$510.00")
        #expect(text(order(type: "stop", stopPrice: 61.25)).value == "$61.25")
        #expect(text(order(type: "stop_limit", limitPrice: 60, stopPrice: 61)).value == "$60.00")
        // No price.
        #expect(text(order(type: "market")).value == "Market")
        #expect(text(order(type: "trailing_stop")).value == "Trailing")
    }

    @Test func statusWords() {
        #expect(text(order(status: "filled", filledQty: 3, filledAvgPrice: 1)).detail.hasPrefix("Filled"))
        #expect(OrderRowText.statusText(order(status: "filled")) == "Filled")
        // How much filled is the size on the left, not the status.
        #expect(OrderRowText.statusText(order(status: "partially_filled", qty: 3, filledQty: 1)) == "Partially filled")
        #expect(OrderRowText.statusText(order(status: "partially_filled", qty: nil, notional: 100, filledQty: 0.2)) == "Partially filled")
        #expect(OrderRowText.statusText(order(status: "canceled")) == "Canceled")
        #expect(OrderRowText.statusText(order(status: "canceled", qty: 3, filledQty: 1)) == "Canceled, 1 of 3 filled")
        #expect(OrderRowText.statusText(order(status: "canceled", qty: 10, filledQty: 4)) == "Canceled, 4 of 10 filled")
        #expect(OrderRowText.statusText(order(status: "expired", qty: 8, filledQty: 2.5)) == "Expired, 2.5 of 8 filled")
        #expect(OrderRowText.statusText(order(status: "expired")) == "Expired")
        #expect(OrderRowText.statusText(order(status: "rejected")) == "Rejected")
        for open in ["new", "accepted", "pending_new", ""] {
            #expect(OrderRowText.statusText(order(status: open)) == "Open")
        }
        #expect(OrderRowText.statusText(order(status: "pending_cancel")) == "Canceling")
        #expect(OrderRowText.statusText(order(status: "done_for_day")) == "Done for day")
        #expect(OrderRowText.statusText(order(status: "replaced")) == "Replaced")
    }

    @Test func relativeTimeThenADate() {
        #expect(OrderRowText.whenText(nil, now: now, calendar: cal) == nil)
        #expect(OrderRowText.whenText(now.addingTimeInterval(-30), now: now, calendar: cal) == "Just now")
        #expect(OrderRowText.whenText(now.addingTimeInterval(30), now: now, calendar: cal) == "Just now")
        #expect(OrderRowText.whenText(now.addingTimeInterval(-5 * 60), now: now, calendar: cal) == "5m ago")
        #expect(OrderRowText.whenText(now.addingTimeInterval(-2 * 3600 - 59), now: now, calendar: cal) == "2h ago")
        #expect(OrderRowText.whenText(utc(2026, 11, 3, 15), now: now, calendar: cal) == "Nov 3")
        #expect(OrderRowText.whenText(utc(2025, 12, 30, 15), now: now, calendar: cal) == "Dec 30, 2025")
    }

    @Test func detailUsesTheFillOrCancelTime() {
        let filled = order(
            status: "filled", filledQty: 3, filledAvgPrice: 10,
            submittedAt: utc(2026, 11, 10), filledAt: now.addingTimeInterval(-2 * 3600)
        )
        #expect(text(filled).detail == "Filled · 2h ago")
        let canceled = order(status: "canceled", submittedAt: utc(2026, 11, 1), canceledAt: utc(2026, 11, 3, 15))
        #expect(text(canceled).detail == "Canceled · Nov 3")
        let open = order(status: "new", submittedAt: now.addingTimeInterval(-300))
        #expect(text(open).detail == "Open · 5m ago")
        let partial = order(status: "partially_filled", qty: 3, filledQty: 1, filledAvgPrice: 512.4, submittedAt: now.addingTimeInterval(-600))
        #expect(text(partial).detail == "Partially filled · 10m ago")
        #expect(text(partial).subtitle == "Buy · Limit · 1 of 3 sh")
        // No timestamps at all: the status alone.
        #expect(text(order(status: "rejected")).detail == "Rejected")
    }

    @Test func closedOrdersThatNeverFilledAreQuiet() {
        #expect(text(order(status: "canceled", limitPrice: 92)).isQuiet)
        #expect(text(order(status: "expired")).isQuiet)
        #expect(text(order(status: "rejected")).isQuiet)
        #expect(!text(order(status: "canceled", filledQty: 1, filledAvgPrice: 90)).isQuiet)
        #expect(!text(order(status: "filled", filledQty: 1, filledAvgPrice: 90)).isQuiet)
        #expect(!text(order(status: "new")).isQuiet)
    }

    @Test func optionTitlesReadAsContracts() {
        #expect(text(order("QQQ261120C00510000", assetClass: "us_option")).title == "QQQ $510 Call")
        #expect(text(order("QQQ")).title == "QQQ")
        #expect(text(order("")).title == "—")
    }
}

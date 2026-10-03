import Foundation
import Testing
@testable import IntelliStock

/// stock_controller.dart + the stock screen's pure helpers.
struct StockModelTests {
    @Test func oneDaySeriesTrimsToMidnightOrFallsBackToTheFullSeries() {
        let now = Date()
        let midnight = DartDateTime.localCalendar.startOfDay(for: now)
        func pt(_ offset: Double, _ v: Double) -> HistPoint {
            HistPoint(ts: .int(Int(midnight.addingTimeInterval(offset).timeIntervalSince1970)), value: v)
        }
        let trimmed = StockModel.series(from: [pt(-60, 1), pt(60, 2), pt(120, 3), HistPoint(ts: "junk", value: 9)], range: "1D", now: now)
        #expect(trimmed.vals == [2, 3])
        let fallback = StockModel.series(from: [pt(-120, 1), pt(-60, 2), pt(60, 3)], range: "1D", now: now)
        #expect(fallback.vals == [1, 2, 3])
        let week = StockModel.series(from: [pt(-60, 1), pt(60, 2), pt(120, 3)], range: "1W", now: now)
        #expect(week.vals == [1, 2, 3])
    }

    @Test func pollsEveryTenSecondsOnOneDayAndThirtyOtherwise() {
        let model = StockModel(symbol: "AAPL", brokerageId: nil, client: { DataStub().client })
        #expect(model.interval == .seconds(10))
        model.scrubIndex = 3
        model.setRange("1Y")
        #expect(model.interval == .seconds(30))
        #expect(model.scrubIndex == nil)
        #expect(model.historyLoading)
    }

    @Test func historyFetchesOneSymbolAndKeepsDataOnAFailedPoll() async {
        let stub = DataStub(json: #"{"results": {"AAPL": [{"ts": 1, "value": 10}, {"ts": 2, "value": 11}]}}"#)
        let model = StockModel(symbol: "AAPL", brokerageId: nil, client: { stub.client })
        model.setRange("1M")
        await model.loadHistory()
        #expect(stub.last?.queryItems == ["symbols": "AAPL", "range": "1M"])
        #expect(model.series?.vals == [10, 11])
        #expect(!model.historyLoading)
        stub.respond(status: 500, json: "{}")
        await model.refreshHistory()
        #expect(model.series?.vals == [10, 11])
    }

    @Test func detailsUseTheDartEndpointsAndNeverThrow() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/symbols/AAPL/info": return (200, #"{"name": "Apple Inc.", "sector": "Technology"}"#)
            case "/brokerages/b1/bot-activity": return (200, #"{"events": [{"symbol": "AAPL", "side": "sell", "created_at": "2026-06-01T00:00:00Z", "override_applied": true}]}"#)
            default: return (500, "{}")
            }
        }
        let model = StockModel(symbol: "AAPL", brokerageId: "b1", client: { stub.client })
        await model.loadDetails()
        #expect(stockInfoText(model.info ?? [:], "name") == "Apple Inc.")
        let bot = stub.requests.first { $0.path == "/brokerages/b1/bot-activity" }
        #expect(bot?.queryItems == ["symbol": "AAPL", "per_page": "20"])
        let orders = stub.requests.first { $0.path == "/brokerages/b1/orders" }
        #expect(orders?.queryItems == ["symbol": "AAPL"])
        #expect(model.botEvents?.first?.overrideApplied == true)
        #expect(model.botEvents?.first?.ts != nil)
        #expect(model.orders == [])
    }

    @Test func noBrokerageSkipsBotAndOrders() async {
        let stub = DataStub(json: "{}")
        let model = StockModel(symbol: "AAPL", brokerageId: nil, client: { stub.client })
        await model.loadDetails()
        #expect(stub.requests.map(\.path) == ["/symbols/AAPL/info"])
        #expect(model.botEvents == nil)
    }

    @Test func botTradeEventDefaultsAndBackers() {
        let e = BotTradeEvent(json: [
            "symbol": "NVDA",
            "ts": "",
            "created_at": "2026-06-01T10:00:00Z",
            "price": 12,
            "contributors": [
                ["strategy": "momentum"], ["strategy": "  "], ["strategy": "graph"],
                ["strategy": "momentum"], ["strategy": "pair"], ["strategy": "news"],
            ],
        ])
        #expect(e.side == "buy")
        #expect(e.isBuy)
        #expect(e.ts == DartDateTime.tryParse("2026-06-01T10:00:00Z"))
        #expect(e.title == "Buy decision")
        #expect(e.backers == ["momentum", "graph", "pair"])
        #expect(!e.overrideApplied)
        let titled = BotTradeEvent(json: ["side": "SELL", "strategy": " graph ", "contributors": [["strategy": "graph"], ["strategy": "x"]]])
        #expect(titled.title == "graph")
        #expect(titled.backers == ["x"])
        #expect(!titled.isBuy)
    }

    @Test func compactMatchesDart() {
        #expect(stockCompact(1_234_000_000_000) == "1.23T")
        #expect(stockCompact(4_560_000_000) == "4.56B")
        #expect(stockCompact(7_890_000) == "7.89M")
        #expect(stockCompact(1_250) == "1.3K")
        #expect(stockCompact(950) == "950")
        #expect(stockCompact(-2_000_000) == "-2.00M")
    }

    @Test func statCellsSkipZerosAndAddTheRangeOpenHighLow() {
        let info: JSONObject = [
            "previousClose": 100, "fiftyTwoWeekHigh": 0, "volume": 1_500_000,
            "marketCap": 2_500_000_000_000.0, "trailingPE": 31.456, "beta": nil, "targetMeanPrice": "x",
        ]
        let cells = stockStatCells(info: info, series: StockSeries(ts: [Date(), Date()], vals: [10, 12]), range: "1D")
        #expect(cells.map(\.label) == ["Prev close", "Open", "1D high", "1D low", "Volume", "Market cap", "P/E"])
        #expect(cells.map(\.value) == ["$100.00", "$10.00", "$12.00", "$10.00", "1.50M", "$2.50T", "31.46"])
        #expect(stockStatCells(info: [:], series: nil, range: "1D").isEmpty)
    }

    // An option contract reads as a contract, not as its OCC code.

    @Test func anOptionIsTitledByItsContract() {
        #expect(stockDisplayTitle("QCOM261009P00177500") == "QCOM $177.50 Put")
        #expect(stockDisplayTitle("SPY") == "SPY")
    }

    @Test func anOptionStatusLineIsItsExpiry() {
        #expect(stockStatusLine(symbol: "QCOM261009P00177500", name: "QCOM Oct 2026 177.500 put") == "Expires Oct 9, 2026")
        #expect(stockStatusLine(symbol: "AAPL", name: "Apple Inc.") == "Apple Inc.")
    }

    @Test func positionLinesCountContractsAndSayShort() {
        #expect(stockPositionLine(symbol: "QCOM261009P00177500", qty: -1, avg: 1.31) == "1 contract short · avg $1.31")
        #expect(stockPositionLine(symbol: "QCOM261009P00177500", qty: 2, avg: 1.31) == "2 contracts · avg $1.31")
        #expect(stockPositionLine(symbol: "ABNB", qty: 82, avg: 160) == "82 shares · avg $160.00")
        #expect(stockPositionLine(symbol: "ABNB", qty: -5, avg: 160) == "5 shares short · avg $160.00")
    }

    @Test func anOptionPositionExplainsItself() {
        let put = "QCOM261009P00177500"
        #expect(stockOptionPlanText(put, qty: -1, premium: 1.31)
                == "Keep the $131.00 premium if QCOM stays above $177.50 by Oct 9, 2026. Below $177.50 you buy 100 shares at $177.50.")
        #expect(stockOptionPlanText(put, qty: 1, premium: 1.31) == "Gains if QCOM falls below $176.19 by Oct 9, 2026.")
        #expect(stockOptionPlanText("AAPL261016C00250000", qty: -1, premium: 2)
                == "Keep the $200.00 premium if AAPL stays below $250.00 by Oct 16, 2026. Above $250.00 your 100 shares are sold at $250.00.")
        #expect(stockOptionPlanText(put, qty: nil, premium: nil) == "QCOM $177.50 put, expiring Oct 9, 2026. The dashed line is the strike.")
        #expect(stockOptionPlanText("SPY", qty: 1, premium: 1) == nil)
    }

    @Test func optionStatsAreTheContractNotTheFiftyTwoWeekRange() {
        let info: JSONObject = ["previousClose": 2.64, "fiftyTwoWeekHigh": 1.78, "fiftyTwoWeekLow": 1.05, "volume": 400]
        let cells = stockStatCells(info: info, series: StockSeries(ts: [Date(), Date()], vals: [1.12, 1.43]), range: "1D",
                                   symbol: "QCOM261009P00177500")
        #expect(cells.map(\.label) == ["Strike", "Expiry", "Type", "Prev close", "Open", "1D high", "1D low", "Volume"])
        #expect(cells.prefix(3).map(\.value) == ["$177.50", "Oct 9, 2026", "Put"])
    }
}

/// symbol_search_screen.dart: the debounced query, stale replies, errors
/// and the quote batch.
struct SymbolSearchModelTests {
    @Test func explainsAnUnavailableSearchServiceWithoutExposingARaw404() {
        #expect(searchUnavailableMessage("Not Found") == "Search is taking a moment to come online. Your dashboard is still up to date.")
        #expect(searchUnavailableMessage("boom") == "We could not reach market search. Check your connection and try again.")
    }

    @Test func emptyQueryResetsWithoutSearching() async {
        var searches = 0
        let model = SymbolSearchModel(search: { _ in searches += 1; return [] }, historicals: { _, _ in [:] }, sleep: { _ in })
        model.onQueryChanged("   ")
        await model.settle()
        #expect(searches == 0)
        #expect(model.results == nil)
        #expect(!model.loading)
    }

    @Test func searchesTheTrimmedQueryAfterTheDebounceAndPricesResults() async {
        var queries: [String] = []
        var slept: [Duration] = []
        var historicalArgs: ([String], String)?
        let model = SymbolSearchModel(
            search: { q in
                queries.append(q)
                return [SearchInstrument(symbol: "AAPL", name: "Apple", type: "stock"), SearchInstrument(symbol: "AAPL", name: "Dup", type: "stock")]
            },
            historicals: { symbols, range in
                historicalArgs = (symbols, range)
                return ["AAPL": [HistPoint(ts: 1, value: 100), HistPoint(ts: 2, value: 110)]]
            },
            sleep: { slept.append($0) }
        )
        model.onQueryChanged(" aapl ")
        #expect(model.loading)
        #expect(model.results == nil)
        await model.settle()
        #expect(slept == [.milliseconds(250)])
        #expect(queries == ["aapl"])
        #expect(model.results?.count == 2)
        #expect(!model.loading)
        #expect(historicalArgs?.0 == ["AAPL"])
        #expect(historicalArgs?.1 == "1D")
        #expect(model.quotes?["AAPL"]?.price == 110)
        #expect(close(model.quotes?["AAPL"]?.changePct, 10))
        #expect(!model.quotesLoading)
    }

    @Test func apiErrorsShowTheirMessageOthersAGenericOne() async {
        let model = SymbolSearchModel(search: { _ in throw ApiError(message: "Not Found", statusCode: 404) }, historicals: { _, _ in [:] }, sleep: { _ in })
        model.onQueryChanged("x")
        await model.settle()
        #expect(model.error == "Not Found")
        #expect(!model.loading)

        struct Boom: Error {}
        let other = SymbolSearchModel(search: { _ in throw Boom() }, historicals: { _, _ in [:] }, sleep: { _ in })
        other.onQueryChanged("x")
        await other.settle()
        #expect(other.error == "Couldn't search symbols right now.")
    }

    @Test func aReplyForAnOldQueryIsDropped() async {
        var release: CheckedContinuation<Void, Never>?
        let model = SymbolSearchModel(
            search: { q in
                if q == "old" { await withCheckedContinuation { release = $0 } }
                return [SearchInstrument(symbol: q.uppercased(), name: q, type: "")]
            },
            historicals: { _, _ in [:] },
            sleep: { _ in }
        )
        model.onQueryChanged("old")
        #expect(await eventually { release != nil })
        // The field changes while "old" is in flight; then "old" answers late.
        model.onQueryChanged("new")
        await model.settle()
        release?.resume()
        await Task.yield()
        #expect(model.results?.map(\.symbol) == ["NEW"])
    }

    @Test func retryReRunsTheCurrentText() async {
        var calls = 0
        let model = SymbolSearchModel(search: { _ in calls += 1; return [] }, historicals: { _, _ in [:] }, sleep: { _ in })
        model.onQueryChanged("abc")
        await model.settle()
        model.retry()
        await model.settle()
        #expect(calls == 2)
        #expect(model.results == [])
    }
}

import Foundation
import Testing
@testable import IntelliStock

/// insights_controller.dart: every provider's wire shape and fallback.
struct DashboardInsightsLoaderTests {
    @Test func marketNewsAsksForFifteenAndDropsUntitled() async {
        let stub = DataStub(json: #"{"articles": [{"title": "A", "source": "WSJ", "url": "https://x", "published_at": "2026-06-01T00:00:00Z"}, {"title": ""}, 5]}"#)
        let news = await DashboardInsightsLoader(client: stub.client).marketNews()
        #expect(stub.last?.path == "/market/news")
        #expect(stub.last?.queryItems == ["limit": "15"])
        #expect(news.map(\.title) == ["A"])
        #expect(news.first?.source == "WSJ")
        #expect(news.first?.publishedAt != nil)
    }

    @Test func marketNewsNeverThrows() async {
        let stub = DataStub(status: 500, json: "{}")
        #expect(await DashboardInsightsLoader(client: stub.client).marketNews().isEmpty)
    }

    @Test func marketMoversAsksForTopSix() async {
        let stub = DataStub(json: #"{"gainers": [{"symbol": "UP", "pct": 5, "price": 10.5}, {"symbol": ""}], "losers": [{"symbol": "DN", "pct": -3.5}]}"#)
        let data = await DashboardInsightsLoader(client: stub.client).marketMovers("b1")
        #expect(stub.last?.path == "/brokerages/b1/movers")
        #expect(stub.last?.queryItems == ["top": "6"])
        #expect(data.gainers == [MarketMover(symbol: "UP", pct: 5, price: 10.5)])
        #expect(data.losers == [MarketMover(symbol: "DN", pct: -3.5, price: nil)])
    }

    @Test func nexusMomentumDefaultsScoreToZero() async {
        let stub = DataStub(json: #"{"momentum": [{"symbol": "A", "score": 1.5}, {"symbol": "B"}, {"symbol": ""}]}"#)
        let picks = await DashboardInsightsLoader(client: stub.client).nexusMomentum("b1")
        #expect(stub.last?.path == "/brokerages/b1/nexus-momentum")
        #expect(picks == [MomentumPick(symbol: "A", score: 1.5), MomentumPick(symbol: "B", score: 0)])
    }

    @Test func indicesKeepDisplayOrderAndSkipUnusableSeries() async {
        let stub = DataStub(json: #"""
        {"results": {
          "QQQ": [{"ts": 1, "value": 100}, {"ts": 2, "value": 101}],
          "SPY": [{"ts": 1, "value": 200}, {"ts": 2, "value": 198}],
          "DIA": [{"ts": 1, "value": 0}, {"ts": 2, "value": 5}],
          "IWM": [{"ts": 1, "value": 50}]
        }}
        """#)
        let quotes = await DashboardInsightsLoader(client: stub.client).marketIndices()
        #expect(stub.last?.path == "/symbol-historicals")
        #expect(stub.last?.queryItems == ["symbols": "SPY,QQQ,DIA,IWM", "range": "1D"])
        #expect(quotes.map(\.symbol) == ["SPY", "QQQ"])
        #expect(quotes.map(\.label) == ["S&P 500", "Nasdaq"])
        #expect(close(quotes[0].pct, -1))
    }

    @Test func sectorPerformanceIsRankedBestFirst() async {
        let stub = DataStub(json: #"""
        {"results": {
          "XLK": [{"ts": 1, "value": 100}, {"ts": 2, "value": 99}],
          "XLE": [{"ts": 1, "value": 100}, {"ts": 2, "value": 103}],
          "XLF": [{"ts": 1, "value": 100}, {"ts": 2, "value": 101}]
        }}
        """#)
        let quotes = await DashboardInsightsLoader(client: stub.client).sectorPerformance()
        #expect(quotes.map(\.label) == ["Energy", "Financials", "Technology"])
        #expect(dashboardSectorEtfs.count == 11)
        #expect(dashboardSectorEtfs.map(\.label).contains("Consumer Disc."))
    }

    @Test func dayChangeIsRebasedToMidnight() async throws {
        let now = Date()
        let midnight = Calendar.current.startOfDay(for: now)
        let before = Int(midnight.timeIntervalSince1970) - 600
        let after = Int(midnight.timeIntervalSince1970) + 600
        let stub = DataStub(json: #"{"timestamps": [\#(before), \#(after)], "values": [100, 105], "current_value": 105}"#)
        let d = try await DashboardInsightsLoader(client: stub.client).dayChange("b1", now: now)
        #expect(stub.last?.path == "/brokerages/b1/portfolio-history")
        #expect(stub.last?.queryItems == ["range": "1D"])
        #expect(close(d?.abs, 5))
        #expect(close(d?.pct, 5))
    }

    @Test func dayChangeIsNilForAnEmptyHistory() async throws {
        let stub = DataStub(json: #"{"timestamps": [], "values": []}"#)
        #expect(try await DashboardInsightsLoader(client: stub.client).dayChange("b1") == nil)
    }

    @Test func sectorReadsOnlyAStringSector() async {
        let stub = DataStub()
        stub.handler = { req in
            req.path == "/symbols/AAPL/info" ? (200, #"{"sector": "Technology"}"#) : (200, #"{"sector": 5}"#)
        }
        let loader = DashboardInsightsLoader(client: stub.client)
        #expect(await loader.sector("AAPL") == "Technology")
        #expect(await loader.sector("ODD") == nil)
    }

    @Test func riskMetricsUseTheOneYearCurveAndNeverThrow() async {
        let stub = DataStub(json: #"{"timestamps": [1, 2, 3], "values": [100, 90, 99]}"#)
        let r = await DashboardInsightsLoader(client: stub.client).riskMetrics("b1")
        #expect(stub.last?.queryItems == ["range": "1Y"])
        #expect(r.points == 3)
        #expect(close(r.maxDrawdown, 10, 1e-6))
        stub.respond(status: 500, json: "{}")
        #expect(await DashboardInsightsLoader(client: stub.client).riskMetrics("b1").isEmpty)
    }

    @Test func todaysMoversRankBiggestGainerFirstInHoldingsOrderForTies() {
        let movers = todaysMoversFromSparks(
            ["A": [100, 101], "B": [100, 110], "C": [0, 5], "D": [100, 101], "E": [7]],
            order: ["D", "A", "B", "C", "E"]
        )
        #expect(movers.map(\.symbol) == ["B", "D", "A"])
    }

    @Test func sectorAllocationSkipsOptionsCashlessAndBlankSymbols() {
        let positions = [
            AccountPosition(symbol: "AAPL", qty: 1, marketValue: 100, unrealizedPnl: 0, unrealizedPnlPct: 0),
            AccountPosition(symbol: "AAPL260116P00150000", qty: -1, marketValue: -50, unrealizedPnl: 0, unrealizedPnlPct: 0),
            AccountPosition(symbol: "ZERO", qty: 0, marketValue: 0, unrealizedPnl: 0, unrealizedPnlPct: 0),
            AccountPosition(symbol: "", qty: 1, marketValue: 5, unrealizedPnl: 0, unrealizedPnlPct: 0),
        ]
        #expect(sectorAllocationPositions(positions).map(\.symbol) == ["AAPL"])
    }
}

/// `DashboardFeedModel`: the session sector cache and the day-change fallback.
struct DashboardFeedModelTests {
    @Test func sectorLookupsHitEachSymbolOncePerSession() async {
        let stub = DataStub(json: #"{"sector": "Technology"}"#)
        let feed = DashboardFeedModel(loader: { DashboardInsightsLoader(client: stub.client) })
        let holdings = AccountHoldings(cash: 0, positions: [
            AccountPosition(symbol: "AAPL", qty: 1, marketValue: 100, unrealizedPnl: 0, unrealizedPnlPct: 0),
            AccountPosition(symbol: "MSFT", qty: 1, marketValue: 300, unrealizedPnl: 0, unrealizedPnlPct: 0),
        ])
        await feed.loadSectorAllocation("b1", holdings: { holdings })
        #expect(stub.requests.count == 2)
        #expect(feed.sectorAllocation["b1"] == [SectorSlice(sector: "Technology", value: 400, pct: 100)])
        // A second account holding the same names reuses the cache.
        await feed.loadSectorAllocation("b2", holdings: { holdings })
        #expect(stub.requests.count == 2)
    }

    @Test func aFailedFirstDayChangeReadsAsNilNotLoading() async {
        let stub = DataStub(status: 500, json: "{}")
        let feed = DashboardFeedModel(loader: { DashboardInsightsLoader(client: stub.client) })
        #expect(!feed.dayChangeLoaded("b1"))
        let task = Task { await feed.pollDayChange("b1", lifecycle: nil, sleep: { _ in try await Task.sleep(for: .seconds(3600)) }) }
        #expect(await eventually { feed.dayChangeLoaded("b1") })
        #expect((feed.dayChange["b1"] ?? nil) == nil)
        task.cancel()
    }

    @Test func marketDataLoadsOnceUnlessForced() async {
        let stub = DataStub(json: #"{"articles": [{"title": "T"}], "results": {}}"#)
        let feed = DashboardFeedModel(loader: { DashboardInsightsLoader(client: stub.client) })
        await feed.loadMarket()
        let first = stub.requests.count
        #expect(first == 3)
        await feed.loadMarket()
        #expect(stub.requests.count == first)
        await feed.loadMarket(force: true)
        #expect(stub.requests.count == first * 2)
    }
}

/// nexus_strategy_controller.dart: stale-forever caches per account.
struct DashboardNexusStrategyModelTests {
    private func strategyStub() -> DataStub {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/brokerages/b1/trends":
                return (200, req.queryItems["status"] == "active"
                    ? #"{"trends": [{"name": "AI Semiconductor Rally", "status": "active", "direction": "bullish", "strength": 0.78}]}"#
                    : #"{"trends": [{"name": "Energy Squeeze", "status": "ended", "direction": "bullish", "ended_at": "2026-06-16T00:00:00"}]}"#)
            case "/brokerages/b1/backfill-queue":
                return (500, "{}")
            default:
                return (200, "{}")
            }
        }
        return stub
    }

    @Test func loadsEachEndpointOnceWithTheDartLimits() async {
        let stub = strategyStub()
        let model = NexusStrategyModel(repository: { DashboardRepository(client: stub.client) })
        await model.load("b1")
        let trendQueries = stub.requests.filter { $0.path == "/brokerages/b1/trends" }.map(\.queryItems)
        #expect(trendQueries.contains(["status": "active", "limit": "30"]))
        #expect(trendQueries.contains(["status": "ended", "limit": "6"]))
        #expect(model.trends["b1"]?.active.map(\.name) == ["AI Semiconductor Rally"])
        #expect(model.trends["b1"]?.recentlyEnded.map(\.name) == ["Energy Squeeze"])
        // A failure falls back to the empty value.
        #expect(model.backfill["b1"] == [])
        #expect(model.anyData("b1"))
        let count = stub.requests.count
        await model.load("b1")
        #expect(stub.requests.count == count)
        await model.reload("b1")
        #expect(stub.requests.count == count * 2)
    }

    @Test func anyDataIsFalseUntilACardHasData() async {
        let stub = DataStub(json: "{}")
        let model = NexusStrategyModel(repository: { DashboardRepository(client: stub.client) })
        #expect(!model.anyData("b1"))
        await model.load("b1")
        #expect(!model.anyData("b1"))
        #expect(model.outcomes["b1"]?.isEmpty == true)
    }

    @Test func armWaitsOutTheDeferralOnce() async {
        let stub = DataStub(json: "{}")
        let model = NexusStrategyModel(repository: { DashboardRepository(client: stub.client) })
        var slept: [Duration] = []
        await model.arm("b1", sleep: { slept.append($0) })
        #expect(model.armed)
        await model.arm("b1", sleep: { slept.append($0) })
        #expect(slept == [.milliseconds(900)])
    }
}

/// sector_3d_chart_golden_test (behaviour): selection, swipe stepping and
/// the centre readout.
struct DashboardSectorDonutTests {
    private let slices = [
        SectorSlice(sector: "Technology", value: 4000, pct: 40),
        SectorSlice(sector: "Healthcare", value: 2500, pct: 25),
        SectorSlice(sector: "Financials", value: 1500, pct: 15),
        SectorSlice(sector: "Energy", value: 1200, pct: 12),
        SectorSlice(sector: "Consumer", value: 800, pct: 8),
    ]

    @Test func startsOnTheLargestSectorWithTheAllocationReadout() {
        let s = DashboardSectorSelection(slices: slices)
        #expect(s.selected == 0)
        #expect(s.caption == "Allocation")
        #expect(s.sectorName == "Technology")
        #expect(s.percentText == "40%")
    }

    @Test func advanceWrapsBothWays() {
        var s = DashboardSectorSelection(slices: slices)
        let back = s.advance(-1)
        #expect(back)
        #expect(s.selected == 4)
        let forward = s.advance(2)
        #expect(forward)
        #expect(s.selected == 1)
        let fullTurn = s.advance(5)
        #expect(!fullTurn)
    }

    @Test func swipeStepsEvery44Points() {
        #expect(DashboardSectorSelection.steps(forDrag: 43) == 0)
        #expect(DashboardSectorSelection.steps(forDrag: 90) == 2)
        #expect(DashboardSectorSelection.steps(forDrag: -45) == -1)
    }

    @Test func angleValuesMapToTheirSlice() {
        let s = DashboardSectorSelection(slices: slices)
        #expect(s.index(forAngleValue: 10) == 0)
        #expect(s.index(forAngleValue: 41) == 1)
        #expect(s.index(forAngleValue: 99) == 4)
        #expect(DashboardSectorSelection(slices: []).index(forAngleValue: 1) == nil)
    }

    @Test func selectIgnoresOutOfRangeAndCurrent() {
        var s = DashboardSectorSelection(slices: slices)
        let same = s.select(0)
        let outOfRange = s.select(9)
        let energy = s.select(3)
        #expect(!same)
        #expect(!outOfRange)
        #expect(energy)
        #expect(s.sectorName == "Energy")
    }
}

/// kalshi_dashboard_card.dart's copy, and the services card parsing.
struct DashboardCardCopyTests {
    @Test func kalshiCardFormatsWithoutGrouping() {
        #expect(KalshiDashboardCard.valueText(1234.5) == "$1234.50")
        #expect(KalshiDashboardCard.dayChangeText(-1.234) == "-$1.23")
        #expect(KalshiDashboardCard.dayChangeText(0) == "+$0.00")
        #expect(KalshiDashboardCard.positionsText(1) == "1 open position")
        #expect(KalshiDashboardCard.positionsText(0) == "0 open positions")
    }

    @Test func nexusProgressRoundsAndReadsTheLastStage() {
        let p = DashboardServicesSection.nexusProgress([
            "graph_build": ["progress_pct": 42.6, "stages": [["message": "fetch"], ["message": "link"]]],
        ])
        #expect(p?.pct == 43)
        #expect(p?.phase == "link")
        #expect(DashboardServicesSection.nexusProgress(["graph_build": nil]) == nil)
        #expect(DashboardServicesSection.nexusProgress(nil) == nil)
    }
}

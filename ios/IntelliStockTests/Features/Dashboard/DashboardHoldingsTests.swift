import Foundation
import Testing
@testable import IntelliStock

/// account_positions_controller.dart: the age → range map, the sparkline
/// builder, the holdings poller, and the row P&L rules.
struct DashboardHoldingsSparklineTests {
    private func position(_ symbol: String, value: Double = 100, entry: Double? = nil, last: Double? = nil) -> AccountPosition {
        AccountPosition(
            symbol: symbol, qty: 1, marketValue: value, unrealizedPnl: 0, unrealizedPnlPct: 0,
            lastPrice: last, avgEntryPrice: entry
        )
    }

    private func point(_ date: Date, _ value: Double) -> HistPoint {
        HistPoint(ts: .string(ISO8601DateFormatter().string(from: date)), value: value)
    }

    @Test func rangeForHoldingAgeMatchesTheBackendMap() {
        #expect(rangeForHoldingAge(0) == "1W")
        #expect(rangeForHoldingAge(6) == "1W")
        #expect(rangeForHoldingAge(7) == "1M")
        #expect(rangeForHoldingAge(31) == "1M")
        #expect(rangeForHoldingAge(93) == "3M")
        #expect(rangeForHoldingAge(366) == "1Y")
        #expect(rangeForHoldingAge(367) == "ALL")
    }

    @Test func noSymbolsMeansNoFetch() async throws {
        var calls = 0
        let out = try await HoldingsSparklines.load(
            positions: [position("")], range: "1D", opens: { [:] },
            historicals: { _, _ in calls += 1; return [:] }
        )
        #expect(out.isEmpty)
        #expect(calls == 0)
    }

    @Test func oneDayTrimsToLocalMidnightWithFallback() async throws {
        let now = Date()
        let midnight = DartDateTime.localCalendar.startOfDay(for: now)
        var requested: [([String], String)] = []
        let out = try await HoldingsSparklines.load(
            positions: [position("AAA"), position("BBB"), position("CCC")],
            range: "1D",
            opens: { [:] },
            historicals: { symbols, range in
                requested.append((symbols, range))
                return [
                    // Two post-midnight bars → the since-midnight slice.
                    "AAA": [point(midnight.addingTimeInterval(-60), 1), point(midnight.addingTimeInterval(60), 2), point(midnight.addingTimeInterval(120), 3)],
                    // One post-midnight bar → the full series.
                    "BBB": [point(midnight.addingTimeInterval(-120), 5), point(midnight.addingTimeInterval(-60), 6), point(midnight.addingTimeInterval(60), 7)],
                    // A single point → dropped.
                    "CCC": [point(midnight.addingTimeInterval(60), 9)],
                ]
            },
            now: now
        )
        #expect(requested.count == 1)
        #expect(requested[0].0 == ["AAA", "BBB", "CCC"])
        #expect(requested[0].1 == "1D")
        #expect(out["AAA"] == [2, 3])
        #expect(out["BBB"] == [5, 6, 7])
        #expect(out["CCC"] == nil)
    }

    @Test func totalAnchorsAtEntryAndLastAndGroupsByAge() async throws {
        let now = Date()
        let bought = now.addingTimeInterval(-3 * 86_400)
        var requested: [([String], String)] = []
        let out = try await HoldingsSparklines.load(
            positions: [position("NEW", entry: 10, last: 14), position("OLD")],
            range: "ALL",
            opens: { ["NEW": bought] },
            historicals: { symbols, range in
                requested.append((symbols, range))
                if range == "1W" {
                    return ["NEW": [point(bought.addingTimeInterval(-3600), 9), point(bought.addingTimeInterval(3600), 11), point(bought.addingTimeInterval(7200), 12)]]
                }
                return ["OLD": [point(now.addingTimeInterval(-86_400), 20), point(now, 21)]]
            },
            now: now
        )
        // A 3-day-old holding → 1W; no open date → 3M; in first-seen order.
        #expect(requested.map(\.1) == ["1W", "3M"])
        #expect(requested.map(\.0) == [["NEW"], ["OLD"]])
        // Entry 10, then the since-buy bars, then the last price 14.
        #expect(out["NEW"] == [10, 11, 12, 14])
        #expect(out["OLD"] == [20, 21])
    }

    @Test func dailyPnlComesFromTheSparkRatio() {
        let p = AccountPosition(symbol: "X", qty: 2, marketValue: 120, unrealizedPnl: -5, unrealizedPnlPct: -4)
        let daily = HoldingRowPnl(position: p, spark: [100, 120], mode: .daily)
        #expect(daily.hasPnl)
        #expect(close(daily.abs, 20))
        #expect(close(daily.pct, 20))
        #expect(daily.label == "+$20.00 · +20.00%")
        let noSpark = HoldingRowPnl(position: p, spark: nil, mode: .daily)
        #expect(!noSpark.hasPnl)
        #expect(noSpark.label == "—")
        let total = HoldingRowPnl(position: p, spark: nil, mode: .total)
        #expect(total.hasPnl)
        #expect(total.abs == -5)
        #expect(total.label == "-$5.00 · -4.00%")
    }

    @Test func pnlModeDefaultsToDailyAndMapsToItsSparkRange() {
        #expect(DashboardFeedModel(loader: { DashboardInsightsLoader(client: DataStub().client) }).pnlMode == .daily)
        #expect(HoldingsPnlMode.daily.sparkRange == "1D")
        #expect(HoldingsPnlMode.total.sparkRange == "ALL")
        #expect(HoldingsPnlMode.total.label == "Total")
    }
}

/// `AccountHoldingsNotifier` against a stubbed backend.
struct DashboardAccountHoldingsModelTests {
    @Test func firstLoadThenAFailedPollKeepsTheLastGoodData() async throws {
        let stub = DataStub(json: #"{"cash": 50, "positions": [{"symbol": "AAPL", "qty": 1, "marketValue": 150}]}"#)
        let model = AccountHoldingsModel(
            brokerageId: "b1",
            repository: { DashboardRepository(client: stub.client) },
            live: { LiveRepository(client: stub.client) }
        )
        let h = try await model.currentHoldings()
        #expect(h.cash == 50)
        #expect(stub.last?.path == "/brokerages/b1/positions")
        stub.respond(status: 500, json: #"{"detail": "boom"}"#)
        await model.refresh()
        #expect(model.holdings.value?.positions.map(\.symbol) == ["AAPL"])
    }

    @Test func pollRunsEveryFifteenSeconds() async {
        let stub = DataStub(json: #"{"positions": []}"#)
        let clock = ManualClock()
        let model = AccountHoldingsModel(
            brokerageId: "b1",
            repository: { DashboardRepository(client: stub.client) },
            live: { LiveRepository(client: stub.client) }
        )
        let task = Task { await model.poll(lifecycle: nil, sleep: clock.sleep) }
        #expect(await eventually { model.holdings.value != nil })
        await clock.advance(by: .seconds(1))
        #expect(clock.requested.first == .seconds(15))
        task.cancel()
    }

    @Test func showSparksKeepsThePreviousCurvesWhileTheToggleRefetches() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/brokerages/b1/positions":
                return (200, #"{"positions": [{"symbol": "AAPL", "qty": 1, "marketValue": 10}]}"#)
            case "/brokerages/b1/holding-opens":
                return (200, #"{"opens": {}}"#)
            default:
                return (200, #"{"results": {"AAPL": [{"ts": "2020-01-01T00:00:00Z", "value": 1}, {"ts": "2020-01-02T00:00:00Z", "value": 2}]}}"#)
            }
        }
        let model = AccountHoldingsModel(
            brokerageId: "b1",
            repository: { DashboardRepository(client: stub.client) },
            live: { LiveRepository(client: stub.client) }
        )
        await model.showSparks("1D")
        #expect(model.freshSparks?["AAPL"] == [1, 2])
        // Toggle: fresh clears, the last curves stay displayed until it lands.
        let toggle = Task { await model.showSparks("ALL") }
        await Task.yield()
        #expect(model.displayedSparks?["AAPL"] == [1, 2])
        await toggle.value
        #expect(model.sparksRange == "ALL")
        #expect(stub.requests.contains { $0.queryItems["range"] == "3M" })
    }
}

/// The private Dart text helpers behind the dashboard rows and cards.
struct DashboardFormatTests {
    @Test func qtyLabelsDropWholeDecimals() {
        #expect(DashboardFormat.qtyLabel(1) == "1 share")
        #expect(DashboardFormat.qtyLabel(5) == "5 shares")
        #expect(DashboardFormat.qtyLabel(2.5) == "2.50 shares")
        #expect(DashboardFormat.qtyNumber(0.125) == "0.13")
    }

    @Test func allocationLabels() {
        #expect(DashboardFormat.allocationLabel(0) == "0%")
        #expect(DashboardFormat.allocationLabel(0.004) == "<1%")
        #expect(DashboardFormat.allocationLabel(0.125) == "13%")
        #expect(DashboardFormat.allocationLabel(1) == "100%")
    }

    @Test func accountLabels() {
        #expect(DashboardFormat.accountLabel(BrokerageAccount(id: "a", accountName: "Main", brokerageType: "alpaca", status: "active", alpacaPaper: true)) == "Alpaca · Paper")
        #expect(DashboardFormat.accountLabel(BrokerageAccount(id: "a", accountName: "Main", brokerageType: "alpaca", status: "active")) == "Alpaca")
        #expect(DashboardFormat.accountLabel(BrokerageAccount(id: "b", accountName: "Bets", brokerageType: "kalshi", status: "active")) == "Bets")
        #expect(DashboardFormat.accountLabel(BrokerageAccount(id: "c", accountName: "", brokerageType: "binanceus", status: "active")) == "binanceus")
    }

    @Test func resolveSelectedPrefersTheStoredIdElseTheFirst() {
        let a = BrokerageAccount(id: "a", accountName: "", brokerageType: "alpaca", status: "")
        let b = BrokerageAccount(id: "b", accountName: "", brokerageType: "kalshi", status: "")
        #expect(DashboardFormat.resolveSelected([a, b], "b")?.id == "b")
        #expect(DashboardFormat.resolveSelected([a, b], "zz")?.id == "a")
        #expect(DashboardFormat.resolveSelected([a, b], nil)?.id == "a")
        #expect(DashboardFormat.resolveSelected([], "a") == nil)
    }

    @Test func indexLevelGroupsThousands() {
        #expect(DashboardFormat.indexLevel(7489.776) == "7,489.78")
        #expect(DashboardFormat.indexLevel(42) == "42.00")
        #expect(DashboardFormat.indexLevel(1_234_567.1) == "1,234,567.10")
    }

    @Test func pillLabelCapitalisesTheFirstLetter() {
        #expect(DashboardFormat.pillLabel("running") == "Running")
        #expect(DashboardFormat.pillLabel("") == "Stopped")
        #expect(DashboardFormat.pillLabel("paused_llm_critical") == "Paused_llm_critical")
    }

    @Test func endedAgoLabels() {
        let now = DartDateTime.tryParse("2026-06-20T12:00:00Z")!
        #expect(DashboardFormat.endedAgo(nil, now: now) == "")
        #expect(DashboardFormat.endedAgo("not a date", now: now) == "")
        #expect(DashboardFormat.endedAgo("2026-06-20T08:00:00Z", now: now) == "ended today")
        #expect(DashboardFormat.endedAgo("2026-06-16T00:00:00Z", now: now) == "ended 4d ago")
    }
}

/// dashboard_top_actions_test.dart: search sits top-right, labelled, and the
/// hero carries no `Portfolio` heading.
struct DashboardTopActionsTests {
    @Test func searchIsLabelledAndPushesSearch() {
        #expect(DashboardTopActions.searchLabel == "Search symbols")
        #expect(DashboardTopActions.searchRoute == .search)
        #expect(DashboardTopActions.title != "Portfolio")
    }
}

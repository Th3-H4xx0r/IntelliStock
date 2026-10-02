import Foundation
import Testing
@testable import IntelliStock

/// The Portfolios sheet: every account's own read-only source, fetched in
/// parallel, filled as each answers, cached for the session — and the row
/// formatting.
struct DashboardPortfoliosTests {
    private static let alpaca = BrokerageAccount(id: "A1", accountName: "Alpaca Live", brokerageType: "alpaca", status: "active")
    private static let paper = BrokerageAccount(id: "A2", accountName: "Alpaca Paper", brokerageType: "alpaca", status: "active", alpacaPaper: true)
    private static let kalshi = BrokerageAccount(id: "K1", accountName: "Kalshi Live", brokerageType: "kalshi", status: "", kalshiEnvironment: "live")

    /// Mid-afternoon local time, so the 1D series straddles local midnight.
    private nonisolated static let now = DartDateTime.localCalendar.startOfDay(for: Date()).addingTimeInterval(15 * 3600)

    private nonisolated static func ms(_ hoursFromMidnight: Double) -> Int {
        let midnight = DartDateTime.localCalendar.startOfDay(for: now)
        return Int(midnight.addingTimeInterval(hoursFromMidnight * 3600).timeIntervalSince1970 * 1000)
    }

    /// A 1D history: 100 before midnight, then 105 and 110; the server's own
    /// open/change are deliberately different, as the hero re-bases them.
    private nonisolated static func historyJSON(base: Double = 100) -> String {
        #"{"timestamps": [\#(ms(-1)), \#(ms(1)), \#(ms(14))], "values": [\#(base), \#(base + 5), \#(base + 10)], "current_value": \#(base + 10), "open_value": 1, "change_abs": 999, "change_pct": 999}"#
    }

    private nonisolated static let kalshiJSON = #"{"value": 35.23, "cash": 10, "day_change": -0.2, "series": []}"#

    /// Answers each account's own endpoint; `failing` paths answer 400.
    private func sheetStub(failing: Set<String> = []) -> DataStub {
        let stub = DataStub()
        stub.handler = { req in
            if failing.contains(req.path) { return (400, #"{"detail": "Unsupported brokerage type: x"}"#) }
            switch req.path {
            case "/brokerages/A1/portfolio-history": return (200, Self.historyJSON())
            case "/brokerages/A2/portfolio-history": return (200, Self.historyJSON(base: 10000))
            case "/brokerages/K1/kalshi/portfolio": return (200, Self.kalshiJSON)
            default: return (404, #"{"detail": "Not Found"}"#)
            }
        }
        return stub
    }

    private func fetcher(_ stub: DataStub) -> DashboardPortfoliosModel.Fetch {
        DashboardPortfolios.fetcher(
            dashboard: DashboardRepository(client: stub.client),
            kalshi: KalshiRepository(client: stub.client),
            now: { Self.now }
        )
    }

    @Test func eachAccountReadsItsOwnScreensSource() async throws {
        let stub = sheetStub()
        let model = DashboardPortfoliosModel(fetcher: { self.fetcher(stub) })
        await model.refresh([Self.alpaca, Self.kalshi])
        let paths = Set(stub.requests.map { "\($0.method) \($0.path)" })
        #expect(paths == ["GET /brokerages/A1/portfolio-history", "GET /brokerages/K1/kalshi/portfolio"])
        #expect(stub.requests.first { $0.path.hasSuffix("/portfolio-history") }?.queryItems == ["range": "1D"])
        // Never the slow widget endpoint, nor the instance list it needed.
        #expect(!stub.requests.contains { $0.path == "/widget/accounts" || $0.path == "/instances" })
    }

    @Test func alpacaFiguresAreTheHerosOwn() async throws {
        let stub = sheetStub()
        let model = DashboardPortfoliosModel(fetcher: { self.fetcher(stub) })
        await model.refresh([Self.alpaca])
        let summary = try #require(model.summary("A1"))
        // Re-based to local midnight, exactly as the hero does it.
        let hero = PortfolioHistory(json: try JSON(data: Data(Self.historyJSON().utf8))).sinceLocalMidnight(now: Self.now)
        #expect(summary.equity == dashboardHeroValue(hero))
        #expect(summary.dayChange == computeChange(hero).abs)
        #expect(summary.dayChangePct == computeChange(hero).pct)
        #expect(summary.equity == 110 && close(summary.dayChange, 10) && close(summary.dayChangePct, 10))
        #expect(summary.changeText == "+$10.00 (+10.00%)")
    }

    @Test func kalshiReadsThePortfolioValueAndDayChange() async throws {
        let stub = sheetStub()
        let model = DashboardPortfoliosModel(fetcher: { self.fetcher(stub) })
        await model.refresh([Self.kalshi])
        let summary = try #require(model.summary("K1"))
        #expect(summary.equity == 35.23)
        #expect(summary.dayChange == -0.2)
        #expect(close(summary.dayChangePct, -0.2 / 35.43 * 100))
        #expect(summary.changeText == "-$0.20 (-0.56%)")
        #expect(summary.direction == .down)
    }

    @Test func accountsAreFetchedInParallel() async {
        let stub = sheetStub()
        let gate = AsyncGate()
        let started = DashboardSheetCounter()
        let real = fetcher(stub)
        let model = DashboardPortfoliosModel(fetcher: {
            { account in
                await started.add(account.id)
                await gate.wait()
                return try await real(account)
            }
        })
        let refresh = Task { await model.refresh([Self.alpaca, Self.paper, Self.kalshi]) }
        // All three are in flight together before any is let through.
        #expect(await eventually { started.count == 3 })
        #expect(model.inFlight == ["A1", "A2", "K1"])
        #expect(model.isPending("A1") && model.isPending("A2") && model.isPending("K1"))
        await gate.open()
        await refresh.value
        #expect(model.summary("A1") != nil && model.summary("A2") != nil && model.summary("K1") != nil)
        #expect(!model.isLoading)
    }

    @Test func rowsFillAsTheyArrive() async {
        let stub = sheetStub()
        let gates = DashboardSheetGates()
        let real = fetcher(stub)
        let model = DashboardPortfoliosModel(fetcher: {
            { account in
                await gates.wait(account.id)
                return try await real(account)
            }
        })
        let refresh = Task { await model.refresh([Self.alpaca, Self.kalshi]) }
        await gates.open("K1")
        #expect(await eventually { model.summary("K1") != nil })
        // Kalshi landed; Alpaca is still redacted, not "—".
        #expect(model.summary("A1") == nil)
        #expect(model.isPending("A1"))
        await gates.open("A1")
        await refresh.value
        #expect(model.summary("A1")?.equity == 110)
    }

    @Test func oneFailureBlanksNothingElse() async {
        let stub = sheetStub(failing: ["/brokerages/A2/portfolio-history"])
        let model = DashboardPortfoliosModel(fetcher: { self.fetcher(stub) })
        await model.refresh([Self.alpaca, Self.paper, Self.kalshi])
        #expect(model.summary("A1")?.equity == 110)
        #expect(model.summary("K1")?.equity == 35.23)
        // The failed account settles with no figures: its row reads "—".
        #expect(model.summary("A2") == nil)
        #expect(!model.isPending("A2"))
    }

    @Test func aLaterFailureKeepsTheCachedFigures() async {
        let stub = sheetStub()
        let model = DashboardPortfoliosModel(fetcher: { self.fetcher(stub) })
        await model.refresh([Self.alpaca, Self.kalshi])
        stub.handler = { req in
            req.path == "/brokerages/K1/kalshi/portfolio" ? (200, #"{"value": 40, "day_change": 1}"#) : (503, #"{"detail": "down"}"#)
        }
        await model.refresh([Self.alpaca, Self.kalshi])
        #expect(model.summary("A1")?.equity == 110)
        #expect(model.summary("K1")?.equity == 40)
    }

    @Test func anAccountAlreadyInFlightIsNotFetchedAgain() async {
        let stub = sheetStub()
        let gate = AsyncGate()
        let started = DashboardSheetCounter()
        let real = fetcher(stub)
        let model = DashboardPortfoliosModel(fetcher: {
            { account in
                await started.add(account.id)
                await gate.wait()
                return try await real(account)
            }
        })
        let first = Task { await model.refresh([Self.alpaca]) }
        #expect(await eventually { started.count == 1 })
        // The sheet reopened mid-fetch: only the new account is fetched.
        let second = Task { await model.refresh([Self.alpaca, Self.kalshi]) }
        #expect(await eventually { started.count == 2 })
        await gate.open()
        await first.value
        await second.value
        #expect(started.ids == ["A1", "K1"])
    }

    @Test func theSelectedRowUsesTheHerosLiveDayFigures() async {
        let h = PortfolioHistory(timestamps: [Date(timeIntervalSince1970: 1), Date(timeIntervalSince1970: 2)], values: [100, 101], currentValue: 102, openValue: 100)
        let chart = DashboardPortfolioChartModel(accountId: "A1", fetch: { _, _ in h }, now: { Self.now })
        await chart.load()
        let rebased = h.sinceLocalMidnight(now: Self.now)
        #expect(chart.daySummary == DashboardAccountSummary(history: rebased))
        chart.setRange("1W")
        await chart.load()
        #expect(chart.daySummary == nil)
    }

    @Test func changeTextMatchesTheHero() {
        #expect(DashboardAccountSummary(equity: 1, dayChange: 65.37, dayChangePct: 1.12).changeText == "+$65.37 (+1.12%)")
        #expect(DashboardAccountSummary(equity: 1, dayChange: -1.01, dayChangePct: -0.01).changeText == "-$1.01 (-0.01%)")
        // No baseline: the hero prints a dash for the percent.
        #expect(DashboardAccountSummary(equity: 1, dayChange: 5, dayChangePct: nil).changeText == "+$5.00 (—)")
        #expect(DashboardAccountSummary(history: PortfolioHistory(timestamps: [], values: [])).changeText == "— (—)")
    }

    @Test func paperIsAlpacaPaperOrKalshiDemo() {
        #expect(BrokerageAccount(json: ["id": "a", "brokerage_type": "alpaca", "alpaca_paper": true]).isPaper)
        #expect(!BrokerageAccount(json: ["id": "a", "brokerage_type": "alpaca", "alpaca_paper": false]).isPaper)
        #expect(BrokerageAccount(json: ["id": "k", "brokerage_type": "kalshi", "kalshi_environment": "demo"]).isPaper)
        #expect(!BrokerageAccount(json: ["id": "k", "brokerage_type": "kalshi", "kalshi_environment": "live"]).isPaper)
    }

    @Test func accountNameFallsBackToTheLabel() {
        #expect(DashboardFormat.accountName(BrokerageAccount(id: "a", accountName: "Swing Trade Paper", brokerageType: "alpaca", status: "")) == "Swing Trade Paper")
        #expect(DashboardFormat.accountName(BrokerageAccount(id: "a", accountName: " ", brokerageType: "alpaca", status: "", alpacaPaper: true)) == "Alpaca · Paper")
    }

    @Test func shortQuantityKeepsTheWholeCount() {
        #expect(DashboardFormat.qtyShort(22.4) == "22.4 sh")
        #expect(DashboardFormat.qtyShort(5.29) == "5.29 sh")
        #expect(DashboardFormat.qtyShort(5) == "5 sh")
        #expect(DashboardFormat.qtyShort(0.5) == "0.5 sh")
    }
}

/// The Kalshi glance's fetch-once model.
struct KalshiDashboardCardModelTests {
    @Test func loadsOncePerAccountAndStartsOverForANewOne() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.url?.path.hasSuffix("/kalshi/portfolio") == true {
                return (200, #"{"value": 35.23, "cash": 10, "day_change": -0.2}"#)
            }
            return (200, #"{"positions": [{"ticker": "A"}, {"ticker": "B"}]}"#)
        }
        let model = KalshiDashboardCardModel()
        let repo = KalshiRepository(client: stub.client)
        await model.load("k1", repository: repo)
        #expect(model.portfolio.value != nil)
        #expect(model.positions == 2)
        let after = stub.requests.count
        await model.load("k1", repository: repo)
        #expect(stub.requests.count == after)
        await model.load("k2", repository: repo)
        #expect(stub.requests.count == after + 2)
        #expect(model.accountId == "k2")
    }
}

/// The instance row's subtitle and short ids.
struct InstanceRowFormattingTests {
    @Test func subtitleNamesOriginStrategyAndBrokerage() {
        let ai = Instance(id: "x", name: "X", createdBy: "ai", runCommand: false, strategyId: "197", brokerageId: "B1")
        let accounts = [BrokerageAccount(id: "B1", accountName: "Alpaca Paper", brokerageType: "alpaca", status: "")]
        #expect(instanceRowSubtitle(ai, brokerages: accounts) == "AI · Strategy 197 · Alpaca Paper")
        let user = Instance(id: "y", name: "Y", createdBy: "user", runCommand: false)
        #expect(instanceRowSubtitle(user, brokerages: nil) == "No strategy linked")
        let named = Instance(id: "z", name: "Z", createdBy: "user", runCommand: false, strategyId: "5", strategy: ["name": "Swing"])
        #expect(instanceRowSubtitle(named, brokerages: nil) == "Swing")
    }

    @Test func anUnknownBrokerageIsShortenedInTheMiddle() {
        #expect(instanceBrokerageName("bf78ad0c-3073-4aac-97a5-a29c7b043404", nested: nil, brokerages: []) == "bf78ad0c…3404")
        #expect(instanceBrokerageName("short-id", nested: nil, brokerages: nil) == "short-id")
        #expect(instanceBrokerageName("b", nested: ["account_name": "Named"], brokerages: nil) == "Named")
    }
}

/// Live Trading's new row copy.
struct LiveRedesignFormattingTests {
    @Test func fillTextShowsSharesOrContracts() {
        let stock = Trade(json: ["symbol": "EL", "side": "sell", "qty": 131, "price": 89.8])
        #expect(liveFillText(stock) == "131 sh @ $89.80")
        let option = Trade(json: ["symbol": "AAPL260116P00150000", "side": "buy", "qty": 1, "price": 1.2, "asset_class": "us_option"])
        if option.isOption {
            #expect(liveFillText(option) == "1 contract @ $1.20")
        }
    }

    @Test func commandWordsAreSentenceCase() {
        #expect(liveCommandWords("close_position") == "Close position")
        #expect(liveCommandWords("PENDING") == "Pending")
        #expect(liveCommandWords("halt") == "Halt")
    }
}

/// Records which accounts' fetches have started, in order.
@MainActor
private final class DashboardSheetCounter {
    private(set) var ids: [String] = []
    var count: Int { ids.count }

    func add(_ id: String) { ids.append(id) }
}

/// One gate per account, so a test lets each fetch finish on its own.
private actor DashboardSheetGates {
    private var opened: Set<String> = []
    private var waiters: [String: [CheckedContinuation<Void, Never>]] = [:]

    func wait(_ id: String) async {
        if opened.contains(id) { return }
        await withCheckedContinuation { waiters[id, default: []].append($0) }
    }

    func open(_ id: String) {
        opened.insert(id)
        waiters.removeValue(forKey: id)?.forEach { $0.resume() }
    }
}

/// A one-shot gate a test opens to let a suspended fetch finish.
private actor AsyncGate {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if opened { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        opened = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

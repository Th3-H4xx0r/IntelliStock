import Foundation
import Testing
@testable import IntelliStock

/// The redesign's one new data path — the Portfolios sheet over the
/// read-only `GET /widget/accounts`, keyed by brokerage through
/// `GET /instances` — and the new row formatting.
struct DashboardPortfoliosTests {
    private func instance(_ id: String, brokerage: String? = nil, nested: JSONObject? = nil) -> Instance {
        Instance(id: id, name: id, createdBy: "user", runCommand: false, brokerageId: brokerage, brokerage: nested)
    }

    private func widget(_ id: String, _ value: Double, _ abs: Double = 1, _ pct: Double = 0.5) -> DashboardWidgetAccount {
        DashboardWidgetAccount(id: id, accountValue: value, dayPnlAbs: abs, dayPnlPct: pct)
    }

    @Test func widgetAccountsReadsTheAccountsArray() async throws {
        let stub = DataStub(json: #"{"accounts": [{"id": "i1", "label": "One", "accountValue": 10157.05, "dayPnlAbs": 90.25, "dayPnlPct": 0.8965, "intradayPoints": []}, {"id": "i2"}, 7], "synced_at": 1}"#)
        let accounts = try await DashboardRepository(client: stub.client).widgetAccounts()
        #expect(stub.last?.httpMethod == "GET")
        #expect(stub.last?.url?.path == "/widget/accounts")
        #expect(accounts == [
            DashboardWidgetAccount(id: "i1", accountValue: 10157.05, dayPnlAbs: 90.25, dayPnlPct: 0.8965),
            DashboardWidgetAccount(id: "i2", accountValue: 0, dayPnlAbs: 0, dayPnlPct: 0),
        ])
    }

    @Test func summariesKeyEachInstanceByItsBrokerageFirstWins() {
        let out = DashboardPortfolios.summaries(
            widget: [widget("a", 100, 2, 1), widget("b", 101), widget("c", 5000, -3, -0.06)],
            instances: [instance("a", brokerage: "B1"), instance("b", brokerage: "B1"), instance("c", brokerage: "B2")]
        )
        #expect(out["B1"] == DashboardAccountSummary(equity: 100, dayChange: 2, dayChangePct: 1))
        #expect(out["B2"] == DashboardAccountSummary(equity: 5000, dayChange: -3, dayChangePct: -0.06))
        #expect(out.count == 2)
    }

    @Test func anUnlinkedOrUnknownInstanceAddsNothing() {
        let out = DashboardPortfolios.summaries(
            widget: [widget("orphan", 1), widget("unlinked", 2)],
            instances: [instance("unlinked")]
        )
        #expect(out.isEmpty)
    }

    @Test func brokerageResolvesLikeTheServer() {
        #expect(DashboardPortfolios.brokerageId(of: instance("x", brokerage: "top", nested: ["brokerage_id": "nested"])) == "nested")
        #expect(DashboardPortfolios.brokerageId(of: instance("x", brokerage: "top")) == "top")
        #expect(DashboardPortfolios.brokerageId(of: instance("x", nested: ["id": "nestedId"])) == "nestedId")
        #expect(DashboardPortfolios.brokerageId(of: instance("x", brokerage: "")) == nil)
    }

    @Test func changeTextMatchesTheHero() {
        #expect(DashboardAccountSummary(equity: 1, dayChange: 65.37, dayChangePct: 1.12).changeText == "+$65.37 (+1.12%)")
        #expect(DashboardAccountSummary(equity: 1, dayChange: -1.01, dayChangePct: -0.01).changeText == "-$1.01 (-0.01%)")
    }

    @Test func refreshMapsKeepsFiguresOnFailureAndSettles() async {
        var fail = false
        let model = DashboardPortfoliosModel(fetch: {
            if fail { throw URLError(.timedOut) }
            return ([DashboardWidgetAccount(id: "a", accountValue: 9, dayPnlAbs: 1, dayPnlPct: 2)], [Instance(id: "a", name: "a", createdBy: "user", runCommand: false, brokerageId: "B")])
        })
        #expect(!model.hasLoaded)
        await model.refresh()
        #expect(model.hasLoaded)
        #expect(model.summary("B")?.equity == 9)
        fail = true
        await model.refresh()
        #expect(model.summary("B")?.equity == 9)
        #expect(!model.isLoading)
    }

    @Test func aFailedFirstFetchStillSettlesSoRowsShowADash() async {
        let model = DashboardPortfoliosModel(fetch: { throw URLError(.notConnectedToInternet) })
        await model.refresh()
        #expect(model.hasLoaded)
        #expect(model.summary("B") == nil)
    }

    @Test func aSecondRefreshWhileOneRunsIsDropped() async {
        var calls = 0
        let gate = AsyncGate()
        let model = DashboardPortfoliosModel(fetch: {
            calls += 1
            await gate.wait()
            return ([], [])
        })
        async let first: Void = model.refresh()
        #expect(await eventually { model.isLoading })
        await model.refresh()
        #expect(calls == 1)
        await gate.open()
        await first
        #expect(calls == 1)
        #expect(model.hasLoaded)
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

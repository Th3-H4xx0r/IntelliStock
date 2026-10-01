import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/dashboard/nexus_models_test.dart, plus the
/// dashboard repository's wire shape and the two dashboard view models.
struct DashboardNexusModelsTests {
    @Test func marketTrendParsesDirectionStrengthTickers() {
        let t = MarketTrend(json: [
            "id": "inst_ai",
            "name": "AI Rally",
            "status": "active",
            "direction": "bullish",
            "strength": 0.78,
            "affected_tickers": ["NVDA", "AMD"],
            "reversal_articles": [],
            "end_date": nil,
        ])
        #expect(t.name == "AI Rally")
        #expect(t.bullish)
        #expect(t.strength == 0.78)
        #expect(t.tickers == ["NVDA", "AMD"])
        #expect(!t.hasReversal)
    }

    @Test func marketTrendPrefersEndedAtThenEndDateWeakeningHasReversal() {
        let t = MarketTrend(json: [
            "name": "X",
            "status": "weakening",
            "direction": "bearish",
            "ended_at": "2026-06-16T00:00:00",
            "end_date": "2026-06-10",
        ])
        #expect(t.endedAt == "2026-06-16T00:00:00")
        #expect(!t.bullish)
        #expect(t.hasReversal)
    }

    @Test func nexusTrendsViewReversalWatchFiltersActiveWeakeningOrReversal() {
        let view = NexusTrendsView(
            active: [
                MarketTrend(json: ["name": "A", "status": "active", "direction": "bullish"]),
                MarketTrend(json: ["name": "B", "status": "active", "direction": "bullish", "reversal_articles": [["headline": "x"]]]),
            ],
            recentlyEnded: []
        )
        #expect(view.reversalWatch.map(\.name) == ["B"])
        #expect(!view.isEmpty)
    }

    @Test func backfillItemReadsNormalizedServerShape() {
        let b = BackfillItem(json: ["ticker": "NVDA", "score": 1.4, "n_paths": 3, "source": "propagation", "priority": true])
        #expect(b.ticker == "NVDA")
        #expect(b.priority)
        #expect(b.nPaths == 3)
    }

    @Test func outcomeStatsParsesHitRateAndRecentCorrectnessFlag() {
        let s = OutcomeStats(json: [
            "hit_rate": 0.6,
            "n": 10,
            "n_correct": 6,
            "avg_return": 1.2,
            "recent": [
                ["symbol": "A", "action_intent": "buy", "latest_return": 2.0, "dominant_event_type": "m_and_a", "entry_date": "2026-06-01"],
            ],
        ])
        #expect(s.hitRate == 0.6)
        #expect(s.n == 10)
        #expect(s.recent.count == 1)
        #expect(s.recent.first?.symbol == "A")
        #expect(s.recent.first?.correct == true)
    }

    @Test func watchlistSummaryParsesCountAndNewest() {
        let w = WatchlistSummary(json: [
            "count": 42,
            "newest": [["symbol": "NVDA", "first_seen_bar": 30, "first_seen_price": 450.5]],
        ])
        #expect(w.count == 42)
        #expect(w.newest.first?.symbol == "NVDA")
        #expect(w.newest.first?.firstSeenPrice == 450.5)
        #expect(!w.isEmpty)
    }

    @Test func discoveredStockAndTradeRationale() {
        let d = DiscoveredStock(json: ["ticker": "AVGO", "source": "sector_peer", "source_ticker": "NVDA", "discovered_at": "2026-06-15"])
        #expect(d.ticker == "AVGO")
        #expect(d.sourceTicker == "NVDA")
        let r = TradeRationale(json: ["symbol": "NVDA", "reason": "capex", "dominant_event_type": "supply_disruption", "score": 3.0])
        #expect(r.symbol == "NVDA")
        #expect(r.reason == "capex")
    }
}

struct DashboardRepositoryTests {
    @Test func servicesFetchesFourEndpointsAndTreatsFailuresAsAbsent() async {
        let stub = DataStub()
        stub.handler = { request in
            switch request.path {
            case "/status": (200, #"{"engines": [{"id": "price_engine", "status": "Running"}, "junk"]}"#)
            case "/agent/control": (200, #"{"running": true}"#)
            case "/digest/control": (500, #"{"detail": "down"}"#)
            default: (200, "{}")
            }
        }
        let snap = await DashboardRepository(client: stub.client).services()
        #expect(Set(stub.requests.map(\.path)) == ["/status", "/agent/control", "/digest/control", "/nexus/status"])
        #expect(snap.engines.map(\.id) == ["price_engine"])
        #expect(snap.isRunning("price_engine"))
        #expect(snap.statusFor("missing") == "stopped")
        #expect(snap.agentControl == ["running": true])
        #expect(snap.digestControl == nil)
        #expect(snap.nexusStatus == nil)
    }

    @Test func accountHoldingsParsesCashAndPositions() async throws {
        let stub = DataStub(json: #"{"cash": 12.5, "positions": [{"symbol": "AAPL", "qty": 2, "marketValue": 400, "lastPrice": 200}]}"#)
        let h = try await DashboardRepository(client: stub.client).accountHoldings("b1")
        #expect(stub.last?.path == "/brokerages/b1/positions")
        #expect(h.cash == 12.5)
        #expect(h.positions == [AccountPosition(symbol: "AAPL", qty: 2, marketValue: 400, unrealizedPnl: 0, unrealizedPnlPct: 0, lastPrice: 200)])
        #expect(!h.isEmpty)
    }

    @Test func nexusTrendsAndControlBodies() async throws {
        let stub = DataStub(json: #"{"trends": []}"#)
        let repo = DashboardRepository(client: stub.client)
        _ = try await repo.nexusTrends("b1", status: "ended", limit: 5)
        #expect(stub.last?.path == "/brokerages/b1/trends")
        #expect(stub.last?.queryItems == ["status": "ended", "limit": "5"])
        _ = try await repo.portfolioHistory("b1", "1D")
        #expect(stub.last?.queryItems == ["range": "1D"])
        try await repo.controlAgent(running: false, specialRequest: "")
        #expect(stub.last?.jsonBody == ["running": false, "special_request": ""])
        try await repo.controlDiscover(running: true)
        #expect(stub.last?.path == "/discover/control")
        #expect(stub.last?.jsonBody == ["running": true])
        try await repo.digestSendNow()
        #expect(stub.last?.path == "/digest/send-now")
        #expect(stub.last?.jsonBody == .null)
    }
}

@MainActor
struct DashboardModelTests {
    @Test func runMarksBusyRefreshesAndIgnoresASecondCall() async {
        let stub = DataStub(json: #"{"engines": [{"id": "price_engine", "status": "running"}]}"#)
        let model = DashboardModel { DashboardRepository(client: stub.client) }
        var sawBusy = false
        var nestedCallRan = false
        await model.run("price_engine") {
            sawBusy = model.isBusy("price_engine")
            await model.run("price_engine") { nestedCallRan = true }
        }
        #expect(sawBusy)
        #expect(!nestedCallRan)
        #expect(!model.isBusy("price_engine"))
        guard case .loaded(let snap) = model.services else {
            Issue.record("services did not refresh after the action")
            return
        }
        #expect(snap.isRunning("price_engine"))
    }

    @Test func runSwallowsErrorsAndClearsBusy() async {
        struct Boom: Error {}
        let model = DashboardModel { DashboardRepository(client: DataStub().client) }
        await model.run("x") { throw Boom() }
        #expect(!model.isBusy("x"))
        if case .loading = model.services {} else { Issue.record("a failed action must not refresh") }
    }

    @Test func loadBrokeragesKeepsTheLastGoodValueOnFailure() async {
        let stub = DataStub(json: #"{"accounts": [{"id": "b1", "account_name": "Main", "brokerage_type": "alpaca", "status": "active", "alpaca_paper": true}]}"#)
        let model = DashboardModel { DashboardRepository(client: stub.client) }
        await model.loadBrokerages()
        #expect(model.brokeragesValue?.map(\.id) == ["b1"])
        #expect(model.brokeragesValue?.first?.isActive == true)
        stub.respond(status: 500, json: #"{"detail": "down"}"#)
        await model.loadBrokerages()
        guard case .failed(let error) = model.brokerages else {
            Issue.record("expected a failure")
            return
        }
        #expect((error as? ApiError)?.message == "down")
        #expect(model.brokeragesValue?.map(\.id) == ["b1"])
    }
}

@MainActor
@Suite(.serialized)
struct SelectedAccountModelTests {
    private let store = KeychainStore(service: "dev.pkrishna.intellistock.tests.selected-account")

    init() { store.delete(SelectedAccountModel.storageKey) }

    @Test func startsNilAndPersistsASelection() {
        let model = SelectedAccountModel(store: store)
        #expect(model.selectedId == nil)
        model.select("acct-2")
        #expect(model.selectedId == "acct-2")
        #expect(store.read("dashboard_selected_account") == "acct-2")
        // A fresh model hydrates from storage.
        #expect(SelectedAccountModel(store: store).selectedId == "acct-2")
        store.delete(SelectedAccountModel.storageKey)
    }

    @Test func anEmptyStoredValueReadsAsNoSelection() throws {
        try store.write(SelectedAccountModel.storageKey, "")
        #expect(SelectedAccountModel(store: store).selectedId == nil)
        store.delete(SelectedAccountModel.storageKey)
    }
}

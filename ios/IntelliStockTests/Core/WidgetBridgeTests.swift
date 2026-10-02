import Foundation
import Testing
@testable import IntelliStock

/// The App Group contract the widget extension reads (widget_payload.dart and
/// widget_sync_service.dart).
@MainActor
struct WidgetSyncTests {
    private let position = WidgetPosition(symbol: "AAPL", qty: 2, marketValue: 400, unrealizedPnlAbs: 10, unrealizedPnlPct: 2.5)

    @Test func syncWritesEverySectionAndReloadsBothKinds() {
        let probe = WidgetSyncProbe()
        let payload = WidgetPayload(
            portfolio: WidgetPortfolio(accountValue: 1000, dayPnlAbs: 5, dayPnlPct: 0.5,
                                       intradayPoints: [IntradayPoint(t: 1_700_000_000, v: 995)], asOf: "2026-06-10T00:00:00Z"),
            positions: [position],
            instances: [WidgetInstance(id: "i1", name: "Main", running: true, pnlAbs: 3, pnlPct: 0.3)]
        )
        probe.sync.sync(payload)

        let portfolio = probe.json(WidgetSync.Key.portfolio)
        #expect(portfolio?["accountValue"].double == 1000)
        #expect(portfolio?["intradayPoints"][0]["t"].int == 1_700_000_000)
        #expect(portfolio?["asOf"].string == "2026-06-10T00:00:00Z")
        #expect(probe.json(WidgetSync.Key.positions)?[0]["symbol"].string == "AAPL")
        #expect(probe.json(WidgetSync.Key.positions)?[0]["unrealizedPnlPct"].double == 2.5)
        #expect(probe.json(WidgetSync.Key.instances)?[0]["running"].bool == true)
        #expect(probe.reloads == ["PortfolioWidget", "InstanceWidget"])
    }

    @Test func syncAccountsWritesTheListThePrimaryAndTheTimestamp() {
        let probe = WidgetSyncProbe(now: Date(timeIntervalSince1970: 1_750_000_123.9))
        let accounts = [
            WidgetAccount(id: "a", label: "Alpha", accountValue: 100, dayPnlAbs: 1, dayPnlPct: 1, positions: [position]),
            WidgetAccount(id: "b", label: "Beta", accountValue: 200, dayPnlAbs: -2, dayPnlPct: -1),
        ]
        probe.sync.syncAccounts(accounts)

        let list = probe.json(WidgetSync.Key.accounts)
        #expect(list?.array?.count == 2)
        #expect(list?[0]["label"].string == "Alpha")
        #expect(list?[0]["positions"][0]["marketValue"].double == 400)
        let primary = probe.json(WidgetSync.Key.portfolio)
        #expect(primary?["accountValue"].double == 100)
        #expect(primary?["asOf"].string == "")
        #expect(primary?["id"].isNull == true)
        #expect(probe.defaults.integer(forKey: WidgetSync.Key.syncedAt) == 1_750_000_123)
        #expect(probe.reloads == ["PortfolioWidget"])
    }

    @Test func syncAccountsWithNoneLeavesThePrimaryAlone() {
        let probe = WidgetSyncProbe()
        probe.sync.syncAccounts([])
        #expect(probe.json(WidgetSync.Key.accounts) == [])
        #expect(probe.defaults.string(forKey: WidgetSync.Key.portfolio) == nil)
    }

    @Test func credentialsMirrorAndReload() {
        let probe = WidgetSyncProbe()
        probe.sync.syncCredentials(apiBase: "https://api.example.test", token: "jwt")
        #expect(probe.defaults.string(forKey: "widget_api_base") == "https://api.example.test")
        #expect(probe.defaults.string(forKey: "widget_token") == "jwt")
        #expect(probe.reloads == ["PortfolioWidget"])
    }

    @Test func payloadRoundTripsThroughJson() {
        let payload = WidgetPayload(
            portfolio: WidgetPortfolio(accountValue: 1, dayPnlAbs: 2, dayPnlPct: 3, intradayPoints: [IntradayPoint(t: 4, v: 5)], asOf: "x"),
            positions: [position],
            instances: [WidgetInstance(id: "i", name: "n", running: false, pnlAbs: 0, pnlPct: 0)]
        )
        #expect(WidgetPayload(json: payload.toJSON()) == payload)
    }

    @Test func fromJsonDefaultsMatchDart() {
        let empty = WidgetPayload(json: [:])
        #expect(empty.portfolio.accountValue == 0)
        #expect(empty.portfolio.asOf == "")
        #expect(empty.positions.isEmpty)
        let instance = WidgetInstance(json: ["id": 5, "running": "yes"])
        #expect(instance.id == "")
        #expect(instance.running == false)
    }
}

/// `WidgetDataSyncer` against stubbed instance endpoints.
@MainActor
struct WidgetDataSyncerTests {
    private let stub = DataStub()

    @Test func buildsOnePortfolioPerInstanceWithHistory() async throws {
        stub.handler = { request in
            let body: String
            switch request.url?.path ?? "" {
            case "/instances":
                body = #"{"instances": [{"id": "i1", "name": "Main"}, {"id": "i2", "name": ""}, {"id": "", "name": "skip"}]}"#
            case "/instances/i1/portfolio-history":
                body = #"{"timestamps": [1700000000, 1700000060], "values": [100, 101.5], "current_value": 102, "change_abs": 2, "change_pct": 2.0}"#
            case "/instances/i1/live-state":
                body = #"{"positions": [{"symbol": "SPY", "qty": 1, "market_value": 500, "unrealized_pnl": 5, "unrealized_pnl_pct": 1}]}"#
            case "/instances/i2/portfolio-history":
                body = #"{"timestamps": [], "values": []}"#
            default:
                return (404, "{}")
            }
            return (200, body)
        }
        let probe = WidgetSyncProbe()
        let client = stub.client
        await WidgetDataSyncer(client: { client }, widgetSync: probe.sync).run()

        let accounts = try #require(probe.json(WidgetSync.Key.accounts)?.array)
        #expect(accounts.count == 1)
        #expect(accounts[0]["id"].string == "i1")
        #expect(accounts[0]["label"].string == "Main")
        #expect(accounts[0]["accountValue"].double == 102)
        #expect(accounts[0]["dayPnlAbs"].double == 2)
        #expect(accounts[0]["intradayPoints"].array?.count == 2)
        #expect(accounts[0]["intradayPoints"][1]["t"].int == 1_700_000_060)
        #expect(accounts[0]["positions"][0]["symbol"].string == "SPY")
        #expect(accounts[0]["positions"][0]["marketValue"].double == 500)

        let history = stub.requests.first { $0.url?.path == "/instances/i1/portfolio-history" }
        #expect(history?.queryItems == ["range": "1D"])
    }

    @Test func nothingIsWrittenWhenNoInstanceHasHistory() async {
        stub.respond(json: #"{"instances": []}"#)
        let probe = WidgetSyncProbe()
        let client = stub.client
        await WidgetDataSyncer(client: { client }, widgetSync: probe.sync).run()
        #expect(probe.defaults.string(forKey: WidgetSync.Key.accounts) == nil)
        #expect(probe.reloads.isEmpty)
    }
}

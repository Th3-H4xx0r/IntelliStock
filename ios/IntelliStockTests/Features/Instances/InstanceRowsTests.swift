import Foundation
import Testing
@testable import IntelliStock

// The Instances list's richer rows (2026-10-02 redesign) and the wheel put
// rows on the instance screen.

nonisolated private func inst(
    _ id: String,
    running: Bool = false,
    strategyId: String? = nil,
    brokerageId: String? = nil,
    uptime: Int? = nil,
    strategy: JSONObject? = nil,
    brokerage: JSONObject? = nil,
    kind: String? = nil
) -> Instance {
    Instance(
        id: id, name: id, createdBy: "user", runCommand: running,
        strategyId: strategyId, brokerageId: brokerageId, uptimeSeconds: uptime,
        brokerage: brokerage, strategy: strategy, kind: kind
    )
}

nonisolated private let rowsSwingDoc: JSONObject = ["name": "Swing trader paper", "strategies": [["strategy": "strategy_swing"]]]

private actor CallLog {
    var ids: [String] = []
    func add(_ id: String) { ids.append(id) }
}

struct InstanceRowTextTests {
    @Test func uptimeToTheMinute() {
        #expect(instanceUptimeShort(30) == "Under a minute")
        #expect(instanceUptimeShort(45 * 60 + 9) == "45m")
        #expect(instanceUptimeShort(6 * 3600 + 12 * 60 + 58) == "6h 12m")
        #expect(instanceUptimeShort(3 * 86_400 + 4 * 3600 + 59 * 60) == "3d 4h")
    }

    @Test func liveUptimeCountsOnFromTheDetailOnlyWhileRunning() {
        let at = Date(timeIntervalSince1970: 1_000)
        let detail = inst("a", running: true, uptime: 600)
        #expect(instanceLiveUptime(running: true, detail: detail, fetchedAt: at, now: at.addingTimeInterval(90)) == 690)
        // The list says it stopped: no uptime, whatever the detail said.
        #expect(instanceLiveUptime(running: false, detail: detail, fetchedAt: at, now: at) == nil)
        // The detail predates the start.
        #expect(instanceLiveUptime(running: true, detail: inst("a"), fetchedAt: at, now: at) == nil)
        #expect(instanceLiveUptime(running: true, detail: nil, fetchedAt: nil, now: at) == nil)
    }

    @Test func kindFromTheKindThenTheStrategysLanes() {
        #expect(instanceKindLabel(inst("c", kind: "crypto")) == "Crypto")
        #expect(instanceKindLabel(inst("k", kind: "kalshi")) == "Kalshi")
        #expect(instanceKindLabel(inst("s", strategyId: "204", strategy: ["strategies": [["strategy": "strategy_wheel"]]])) == "Swing")
        #expect(instanceKindLabel(inst("e", strategyId: "195", strategy: ["strategies": [["strategy": "strategy_eb"]]])) == "Equity")
        #expect(instanceKindLabel(inst("n")) == "Equity")
        // A linked strategy not loaded yet: unknown, never a guess.
        #expect(instanceKindLabel(inst("u", strategyId: "204")) == nil)
        // The detail came back without it (a deleted strategy).
        #expect(instanceKindLabel(inst("u", strategyId: "204"), detailLoaded: true) == "Equity")
    }

    @Test func paperFromTheNestedBrokerageThenTheLoadedAccount() {
        #expect(instanceIsPaper(inst("a", brokerageId: "b1", brokerage: ["alpaca_paper": true]), brokerages: nil) == true)
        #expect(instanceIsPaper(inst("a", brokerageId: "b1", brokerage: ["alpaca_paper": false]), brokerages: nil) == false)
        let accounts = [
            BrokerageAccount(id: "b1", accountName: "Main", brokerageType: "alpaca", status: "active", alpacaPaper: false),
            BrokerageAccount(id: "b2", accountName: "Paper", brokerageType: "Alpaca", status: "active", alpacaPaper: true),
            BrokerageAccount(id: "b3", accountName: "RH", brokerageType: "robinhood", status: "active"),
        ]
        #expect(instanceIsPaper(inst("a", brokerageId: "b1"), brokerages: accounts) == false)
        #expect(instanceIsPaper(inst("a", brokerageId: "b2"), brokerages: accounts) == true)
        #expect(instanceIsPaper(inst("a", brokerageId: "b3"), brokerages: accounts) == nil)
        #expect(instanceIsPaper(inst("a", brokerageId: "zz"), brokerages: accounts) == nil)
        #expect(instanceIsPaper(inst("a"), brokerages: accounts) == nil)
    }

    @Test func metaAndChangeText() {
        #expect(instanceRowMeta(uptime: 6 * 3600 + 12 * 60, kind: "Swing") == "6h 12m · Swing")
        #expect(instanceRowMeta(uptime: nil, kind: "Equity") == "Equity")
        #expect(instanceRowMeta(uptime: nil, kind: nil) == "")
        #expect(instanceRowChangeText(DashboardAccountSummary(equity: 1, dayChange: 12.34, dayChangePct: 0.07)) == "+$12.34 · +0.07%")
        #expect(instanceRowChangeText(DashboardAccountSummary(equity: 1, dayChange: -5, dayChangePct: nil)) == "-$5.00 · —")
        #expect(instanceRowChangeText(DashboardAccountSummary(equity: 1, dayChange: nil, dayChangePct: nil)) == nil)
    }

    @Test func accountsOncePerBrokeragePreferringTheLoadedOne() {
        let loaded = BrokerageAccount(id: "b1", accountName: "Main", brokerageType: "alpaca", status: "active")
        let out = instanceRowAccounts(
            [inst("a", brokerageId: "b1"), inst("b", brokerageId: "b1"), inst("c", brokerageId: "b2"), inst("d"), inst("e", brokerageId: "")],
            brokerages: [loaded]
        )
        #expect(out.map(\.id) == ["b1", "b2"])
        #expect(out[0] == loaded)
        #expect(out[1].brokerageType == "")
    }

    @Test func summaryKeepsTheDaysCurve() {
        let history = PortfolioHistory(
            timestamps: [Date(timeIntervalSince1970: 0), Date(timeIntervalSince1970: 60)],
            values: [100, 110]
        )
        #expect(DashboardAccountSummary(history: history).spark == [100, 110])
        #expect(DashboardAccountSummary(equity: 1, dayChange: nil, dayChangePct: nil).spark.isEmpty)
    }
}

struct InstanceRowsModelTests {

    private func model(_ log: CallLog, accounts: CallLog, clock: @escaping () -> Date = { Date() }) -> InstanceRowsModel {
        InstanceRowsModel(
            detailFetcher: {
                { id in
                    await log.add(id)
                    if id == "bad" { throw ApiError(message: "boom", statusCode: 500) }
                    return inst(id, running: true, strategyId: "204", brokerageId: "b1", uptime: 120, strategy: rowsSwingDoc)
                }
            },
            accountFetcher: {
                { account in
                    await accounts.add(account.id)
                    return DashboardAccountSummary(equity: 17_966.98, dayChange: 12.34, dayChangePct: 0.07, spark: [1, 2])
                }
            },
            now: clock
        )
    }

    @Test func fillsEachRowAndMergesTheDetailUnderTheListsRunState() async {
        let log = CallLog(), accounts = CallLog()
        let rows = model(log, accounts: accounts)
        let listed = inst("a", running: false, strategyId: "204", brokerageId: "b1")
        await rows.refresh([listed, inst("bad")], brokerages: nil)
        #expect(Set(await log.ids) == ["a", "bad"])
        #expect(await accounts.ids == ["b1"])
        let shown = rows.merged(listed)
        #expect(shown.runCommand == false)
        #expect(instanceRowSubtitle(shown, brokerages: nil).hasPrefix("Swing trader paper"))
        #expect(instanceKindLabel(shown) == "Swing")
        // The list says stopped, so no uptime.
        #expect(rows.uptime(listed) == nil)
        #expect(rows.accounts.summary("b1")?.equity == 17_966.98)
        // A failed detail leaves the row as the list has it.
        #expect(rows.merged(inst("bad")) == inst("bad"))
    }

    @Test func aRelinkedStrategyIgnoresTheOldDetail() async {
        let rows = model(CallLog(), accounts: CallLog())
        await rows.refresh([inst("a", strategyId: "204")], brokerages: nil)
        let relinked = inst("a", strategyId: "999")
        #expect(rows.merged(relinked).strategy == nil)
    }

    @Test func aSecondRefreshInsideMaxAgeIsSkippedUnlessForced() async {
        let log = CallLog(), accounts = CallLog()
        var now = Date(timeIntervalSince1970: 10_000)
        let rows = model(log, accounts: accounts, clock: { now })
        let list = [inst("a", running: true, brokerageId: "b1")]
        await rows.refresh(list, brokerages: nil)
        await rows.refresh(list, brokerages: nil)
        #expect(await log.ids == ["a"])
        #expect(await accounts.ids == ["b1"])
        await rows.refresh(list, brokerages: nil, force: true)
        #expect(await log.ids == ["a", "a"])
        #expect(await accounts.ids == ["b1", "b1"])
        now = now.addingTimeInterval(InstanceRowsModel.maxAge + 1)
        await rows.refresh(list, brokerages: nil)
        #expect(await log.ids.count == 3)
        // Running by the list and the detail: uptime counts on from the fetch.
        now = now.addingTimeInterval(30)
        #expect(rows.uptime(list[0]) == 150)
    }

    @Test func resetForgetsEverything() async {
        let rows = model(CallLog(), accounts: CallLog())
        await rows.refresh([inst("a", brokerageId: "b1")], brokerages: nil)
        rows.reset()
        #expect(rows.details.isEmpty)
        #expect(rows.accounts.summary("b1") == nil)
    }
}

struct WheelPutRowTests {
    private let put = WheelPut(
        contract: "QCOM261009P00177500", underlying: "QCOM", strike: 177.5, expiry: "2026-10-09",
        qty: 1, avgEntryPrice: 1.31, currentPrice: 0.86, itmPct: -4.1, dte: 7, unrealizedPl: 45
    )

    @Test func titleAndSubtitle() {
        #expect(wheelPutTitle(put) == "QCOM $177.50 Put")
        #expect(wheelPutTitle(WheelPut(contract: "QCOM261009P00177500", underlying: "", expiry: "")) == "QCOM $177.50 Put")
        #expect(wheelPutSubtitle(put) == "7 days left · 4.1% above strike")
        #expect(wheelPutSubtitle(WheelPut(contract: "x", underlying: "X", expiry: "", itmPct: 3, dte: 1)) == "1 day left · 3.0% below strike")
        #expect(wheelPutSubtitle(WheelPut(contract: "x", underlying: "X", expiry: "", itmPct: 0.01, dte: 0)) == "Expires today · At the strike")
        #expect(wheelPutSubtitle(WheelPut(contract: "x", underlying: "X", expiry: "2026-10-09")) == "Expires 2026-10-09")
    }

    @Test func positionIsShortAndValuedAtTheMark() throws {
        let p = try #require(wheelPutPosition(put))
        #expect(p.symbol == "QCOM261009P00177500")
        #expect(p.qty == -1)
        #expect(abs(p.marketValue - -86) < 1e-9)
        #expect(p.unrealizedPnl == 45)
        #expect(abs(p.unrealizedPnlPct - 45 / 131 * 100) < 1e-9)
        #expect(p.avgEntryPrice == 1.31 && p.lastPrice == 0.86)
        #expect(wheelPutPosition(WheelPut(contract: "x", underlying: "X", expiry: "")) == nil)
    }

    @Test func bookFooter() {
        #expect(wheelBookFooter(puts: 1, collateral: 17_750) == "1 open put · $17,750.00 held as collateral.")
        #expect(wheelBookFooter(puts: 2, collateral: nil) == "2 open puts.")
    }
}

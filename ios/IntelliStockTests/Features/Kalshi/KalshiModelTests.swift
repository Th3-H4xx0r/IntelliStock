import Foundation
import Testing
@testable import IntelliStock

/// Kalshi view-model logic (no Dart tests existed): the instance form's
/// presets, prefill and body, the detail screen's pregame helpers, the
/// backtest launcher's body and the result screen's day grouping.
struct KalshiFormatTests {
    @Test func numPrintsIntegralValuesAsInts() {
        #expect(KalshiFormat.num(Num.double(5.0)) == "5")
        #expect(KalshiFormat.num(Num.int(25)) == "25")
        #expect(KalshiFormat.num(Num.double(0.125)) == "0.125")
        #expect(KalshiFormat.num(Num.double(0.07 * 100)) == "7.000000000000001")
    }

    @Test func initialsAndBadgeInitials() {
        #expect(KalshiFormat.initials("Real Madrid") == "RM")
        #expect(KalshiFormat.initials("Paris Saint-Germain FC") == "PS")
        #expect(KalshiFormat.badgeInitials("Borussia Mönchengladbach Eins") == "BME")
        #expect(KalshiFormat.badgeInitials("A B C D") == "ABC")
    }

    @Test func moneyPctAndSignedHelpers() {
        #expect(KalshiFormat.money(cents: 1234) == "$12.34")
        #expect(KalshiFormat.money(cents: nil) == "—")
        #expect(KalshiFormat.pct(0.1234) == "12.3%")
        #expect(KalshiFormat.signedEdge(0.06) == "+6.0%")
        #expect(KalshiFormat.signedEdge(-0.015) == "-1.5%")
        #expect(KalshiFormat.signedDollars(cents: -250) == "-$2.50")
    }
}

@MainActor
struct KalshiInstanceFormModelTests {
    private func model(_ stub: DataStub = DataStub(), edit: JSONObject? = nil, name: String? = nil) -> KalshiInstanceFormModel {
        KalshiInstanceFormModel(
            initialBrokerageId: "b1",
            editInstanceId: edit == nil ? nil : "i1",
            editName: name,
            editConfig: edit,
            repository: { KalshiRepository(client: stub.client) }
        )
    }

    @Test func defaultsMatchTheDartControllers() {
        let m = model()
        #expect(m.edge == "4" && m.kelly == "0.125" && m.maxContracts == "50")
        #expect(m.leagues == ["EPL", "Serie B", "Ligue 2"])
        #expect(m.paperMode && !m.liveMonitoring && m.oneBetPerFixture)
        #expect(m.risk == "medium")
    }

    @Test func presetTunesEveryFieldAndRescalesTheDailyLossCap() {
        let m = model()
        m.manualBankroll = "1000"
        m.applyPreset("max")
        #expect(m.edge == "2")
        #expect(m.kelly == "0.2")
        #expect(m.maxContracts == "100")
        #expect(m.exposure == "40")
        #expect(m.leagueCap == "40")
        #expect(m.poll == "30")
        #expect(m.orderSizeMin == "8" && m.orderSizeMax == "15")
        #expect(m.usagePct == 70)
        // No balance → manual bankroll 1000 × 0.15.
        #expect(m.dailyLoss == "150")
        #expect(m.riskBlurb.hasPrefix("Aggressive"))
    }

    @Test func touchedDailyLossIsNotRescaled() {
        let m = model()
        m.editDailyLoss("42")
        m.scaleDailyLoss()
        #expect(m.dailyLoss == "42")
        m.applyPreset("low") // a preset clears the touched flag
        #expect(m.dailyLoss == "50")
    }

    @Test func dailyLossClampsToAtLeastOne() {
        let m = model()
        m.manualBankroll = "1"
        m.applyPreset("low")
        #expect(m.dailyLoss == "1")
    }

    @Test func prefillMapsTheStoredConfig() {
        let cfg: JSONObject = [
            "edge_threshold": 0.04, "kelly_fraction": 0.125, "max_contracts_per_market": 50,
            "max_open_exposure_frac": 0.15, "per_league_cap_frac": 0.25,
            "min_price_cents": 15, "max_price_cents": 90, "draw_min_edge": 0.1,
            "order_size_min_cents": 200, "order_size_max_cents": 550,
            "daily_loss_cap_cents": 4000, "poll_seconds": 45, "bankroll_cents": 123456,
            "odds_api_key": "abc", "no_sharp_edge_threshold": 0.05, "market_shrink": 0.4,
            "sharp_weight": 0.85, "bankroll_usage_pct": 60, "tier": "high", "model": "m1",
            "live_monitoring": true, "paper_mode": false, "one_bet_per_fixture": false,
            "leagues": ["MLS", "EPL", "MLS"],
        ]
        let m = model(edit: cfg, name: "Bot")
        #expect(m.isEdit && m.name == "Bot")
        #expect(m.edge == "4" && m.exposure == "15" && m.leagueCap == "25" && m.drawMinEdge == "10")
        #expect(m.orderSizeMin == "2" && m.orderSizeMax == "5.5")
        #expect(m.dailyLoss == "40" && m.dailyLossTouched)
        #expect(m.manualBankroll == "1234.56")
        #expect(m.oddsKey == "abc")
        #expect(close(m.sharpWeight, 85))
        #expect(m.usagePct == 60)
        #expect(m.risk == "high" && m.selectedModel == "m1")
        #expect(m.liveMonitoring && !m.paperMode && !m.oneBetPerFixture)
        #expect(m.leagues == ["MLS", "EPL"])
    }

    @Test func bodyKeysAndOrderMatchTheDartMap() throws {
        let m = model()
        m.name = "  Soccer  "
        m.oddsKey = " k1 "
        let body = m.body()
        #expect(body.entries.map(\.key) == [
            "name", "leagues", "edge_threshold", "kelly_fraction", "max_contracts_per_market",
            "max_open_exposure_frac", "per_league_cap_frac", "min_price_cents", "max_price_cents",
            "draw_min_edge", "order_size_min_dollars", "order_size_max_dollars", "daily_loss_cap_dollars",
            "bankroll_dollars", "poll_seconds", "bankroll_usage_pct", "live_monitoring", "odds_api_key",
            "sharp_weight", "tier", "model", "paper_mode", "no_sharp_edge_threshold", "market_shrink",
            "one_bet_per_fixture",
        ])
        #expect(body["name"] == "Soccer")
        #expect(body["odds_api_key"] == "k1")
        #expect(body["model"] == .null)
        #expect(body["max_contracts_per_market"] == 50)
        #expect(body["bankroll_usage_pct"] == 50)
        #expect(close(body["edge_threshold"]?.double, 0.04))
        #expect(close(body["sharp_weight"]?.double, 0.85))
        #expect(body["paper_mode"] == true)
    }

    @Test func badNumbersFallBackToTheDartDefaults() {
        let m = model()
        m.edge = "x"; m.kelly = ""; m.maxContracts = "1.5"; m.poll = "abc"
        let body = m.body()
        #expect(close(body["edge_threshold"]?.double, 0.03))
        #expect(close(body["kelly_fraction"]?.double, 0.25))
        #expect(body["max_contracts_per_market"] == 50)
        #expect(body["poll_seconds"] == 60)
    }

    @Test func submitValidatesThenPostsOrPatches() async throws {
        let stub = DataStub()
        let create = model(stub)
        #expect(await create.submit() == nil)
        #expect(create.err == "Name is required")
        create.name = "N"
        create.leagues = []
        #expect(await create.submit() == nil)
        #expect(create.err == "Pick at least one league")
        create.leagues = ["EPL"]
        #expect(await create.submit() == "b1")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/brokerages/b1/kalshi/instances")

        let edit = model(stub, edit: [:], name: "E")
        #expect(await edit.submit() == "b1")
        #expect(stub.last?.method == "PATCH")
        #expect(stub.last?.path == "/instances/i1/kalshi/config")
    }

    @Test func balanceUsesCashThenValueAndScalesTheCap() async {
        let stub = DataStub(json: #"{"value": 500, "cash": 0, "day_change": 0, "series": []}"#)
        let m = model(stub)
        await m.loadBalance()
        #expect(m.hasBalance && m.balance == 500)
        // 500 × 50 % = 250; × 0.10 = 25.
        #expect(m.effectiveBankroll == 250)
        #expect(m.dailyLoss == "25")
    }
}

struct KalshiPregameTests {
    @Test func countdownUsesTwoUnitsAndTodayAndHidesPastDays() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        #expect(KalshiPregame.kickoffCountdown(nil, now: now) == "")
        #expect(KalshiPregame.kickoffCountdown(1_000_000 + 5 * 86400 + 4 * 3600 + 30, now: now) == "5d 4h")
        #expect(KalshiPregame.kickoffCountdown(1_000_000 + 3 * 60 + 2, now: now) == "3m 2s")
        #expect(KalshiPregame.kickoffCountdown(1_000_000 + 3600 + 5, now: now) == "1h")
        #expect(KalshiPregame.kickoffCountdown(1_000_000 - 10, now: now) == "today")
        #expect(KalshiPregame.kickoffCountdown(1_000_000 - 86400, now: now) == "")
    }

    @Test func priceCentsPrefersTheFillAverage() {
        #expect(KalshiPregame.priceCents(["entry_avg_cents": 41.6]) == 42)
        #expect(KalshiPregame.priceCents(["fused_fair": 0.55, "edge": 0.05]) == 50)
        #expect(KalshiPregame.priceCents(["fused_fair": 0.55]) == nil)
    }

    @Test func bestEdgeAndPair() {
        #expect(KalshiPregame.bestEdge([["edge": 0.02], ["edge": 0.07], [:]]) == 0.07)
        #expect(KalshiPregame.bestEdge([[:]]) == 0)
        #expect(KalshiPregame.pair(.double(1520.4), .null, decimals: 0) == "1520/—")
        #expect(KalshiPregame.pair(nil, .string("x"), decimals: 2) == nil)
        #expect(KalshiPregame.edgeSeries([["edge": 0.01], "x", ["edge": 0.03]]) == [0.01, 0.03])
    }

    @Test func dedupeKeepsLatestPerSideRemembersPlacedAndOrdersSides() {
        let rows: [JSONObject] = [
            ["side": "away", "ts": "2026-01-01T10:00:00Z", "decision": "skipped", "edge": 0.01],
            ["side": "home", "ts": "2026-01-01T10:00:00Z", "decision": "placed", "entry_edge": 0.06],
            ["side": "home", "ts": "2026-01-01T11:00:00Z", "decision": "skipped", "edge": 0.02],
            ["side": "draw", "ts": "2026-01-01T09:00:00Z", "decision": "blocked"],
        ]
        let out = KalshiPregame.dedupeSides(rows)
        #expect(out.map { $0["side"] } == ["home", "draw", "away"])
        #expect(out[0]["decision"] == "placed")
        #expect(out[0]["edge"] == 0.02)
        #expect(out[0]["entry_edge"] == 0.06)
        #expect(out[2]["decision"] == "skipped")
    }

    @Test func gamesGroupByFixtureAndSortByKickoff() {
        let rows: [JSONObject] = [
            ["fixture_id": "f2", "side": "home", "kickoff_ts": 200],
            ["fixture_id": "f1", "side": "home", "kickoff_ts": 100],
            ["match": "No kickoff", "side": "home"],
        ]
        let games = KalshiPregame.games(rows)
        #expect(games.map { $0[0]["fixture_id"] ?? $0[0]["match"] } == ["f1", "f2", "No kickoff"])
    }

    @Test func fmtTsRelativeThenDate() {
        let now = DartDateTime.tryParse("2026-06-10T12:00:00Z")!
        #expect(KalshiPregame.fmtTs("2026-06-10T11:59:30Z", now: now) == "just now")
        #expect(KalshiPregame.fmtTs("2026-06-10T11:30:00Z", now: now) == "30m ago")
        #expect(KalshiPregame.fmtTs("2026-06-10T09:00:00Z", now: now) == "3h ago")
        #expect(KalshiPregame.fmtTs("", now: now) == "")
        #expect(KalshiPregame.fmtTs("2026-06-08T09:05:00Z", now: now).count == 11)
    }

    @Test func decisionPagesClampAndSlice() {
        let rows: [JSON] = (0..<19).map { .int($0) }
        let p = KalshiPregame.page(rows, requested: 5)
        #expect(p.pages == 3 && p.page == 2 && p.start == 16 && p.slice.count == 3)
        #expect(KalshiPregame.page([], requested: 0).slice.isEmpty)
    }
}

@MainActor
struct KalshiOverviewModelTests {
    @Test func reconcileDropsStaleSelectionAndDefaultsToFirst() {
        let stub = DataStub()
        let m = KalshiOverviewModel(repository: { KalshiRepository(client: stub.client) })
        let a = BrokerageAccount(id: "a", accountName: "A", brokerageType: "kalshi", status: "")
        let b = BrokerageAccount(id: "b", accountName: "B", brokerageType: "kalshi", status: "")
        let alpaca = BrokerageAccount(id: "c", accountName: "C", brokerageType: "alpaca", status: "")
        let kalshi = KalshiOverviewModel.kalshiAccounts([alpaca, a, b])
        #expect(kalshi.map(\.id) == ["a", "b"])
        #expect(m.reconcile(accounts: kalshi) == "a")
        m.select("b")
        m.reconcile(accounts: [a])
        #expect(m.selectedId == "a")
        m.reconcile(accounts: [])
        #expect(m.selectedId == nil)
    }

    @Test func refreshKeepsTheLastInstanceListOnFailure() async {
        let stub = DataStub(json: #"{"instances": [{"id": "i1", "name": "One", "running": true}]}"#)
        let m = KalshiOverviewModel(repository: { KalshiRepository(client: stub.client) })
        m.reconcile(accounts: [BrokerageAccount(id: "b", accountName: "B", brokerageType: "kalshi", status: "")])
        await m.loadInstances("b")
        #expect(m.instanceList("b").map(\.id) == ["i1"])
        stub.respond(status: 500, json: #"{"detail": "boom"}"#)
        await m.refresh()
        #expect(m.instanceList("b").map(\.id) == ["i1"])
        #expect(m.edges["b"]?.errorMessage == "boom")
    }
}

@MainActor
struct KalshiBacktestModelTests {
    @Test func loadAppliesTheInstanceConfig() async {
        let stub = DataStub()
        stub.handler = { req in
            if req.url?.path == "/models" { return (200, #"{"models": [{"id": "m1", "name": "Claude"}]}"#) }
            if req.url?.path.hasSuffix("/backtests") == true { return (200, #"{"backtests": [{"id": "bt1"}]}"#) }
            return (200, #"{"brokerage_id": "b9", "config": {"tier": "low", "edge_threshold": 0.05, "order_size_min_cents": 300, "leagues": ["MLS"], "model": "m1", "oddspapi_api_key": "pk"}}"#)
        }
        let m = KalshiBacktestModel(instanceId: "i1", repository: { KalshiRepository(client: stub.client) })
        await m.load()
        #expect(m.bid == "b9")
        #expect(m.tier == "low")
        #expect(m.value(.edge) == 5 && m.text(.edge) == "5")
        #expect(m.value(.orderMin) == 3)
        #expect(m.leagues == ["MLS"])
        #expect(m.modelId == "m1" && m.useLlm)
        #expect(m.oddsKey == "pk")
        #expect(m.backtests.count == 1)
    }

    @Test func submitValidatesAndBuildsTheDartBody() async throws {
        let stub = DataStub(json: #"{"id": "new1", "backtests": []}"#)
        let m = KalshiBacktestModel(instanceId: "i1", repository: { KalshiRepository(client: stub.client) })
        #expect(await m.submit() == nil)
        #expect(m.err == "Pick a start and end date.")
        let cal = Calendar.current
        m.start = cal.date(from: DateComponents(year: 2026, month: 3, day: 2))
        m.end = cal.date(from: DateComponents(year: 2026, month: 3, day: 1))
        #expect(await m.submit() == nil)
        #expect(m.err == "Start must be on/before end.")
        m.end = cal.date(from: DateComponents(year: 2026, month: 3, day: 9))
        m.applyPreset("high")
        let body = m.body()
        #expect(body.entries.map(\.key) == ["instance_id", "leagues", "start_date", "end_date", "bankroll_dollars", "config"])
        #expect(body["start_date"] == "2026-03-02" && body["end_date"] == "2026-03-09")
        let config = try #require(body["config"]?.orderedObject)
        #expect(config.entries.map(\.key).last == "analyst_max_calls")
        #expect(config["use_llm"] == false)
        #expect(config["model"] == nil)
        #expect(config["max_contracts_per_market"] == .double(75))
        #expect(close(config["edge_threshold"]?.double, 0.03))
    }

    @Test func modelPickTurnsTheAnalystOnAndFieldEditsParse() {
        let stub = DataStub()
        let m = KalshiBacktestModel(instanceId: "i1", repository: { KalshiRepository(client: stub.client) })
        m.selectModel("m2")
        #expect(m.useLlm)
        m.edit(.kelly, "0.3")
        #expect(m.value(.kelly) == 0.3)
        m.edit(.kelly, "abc")
        #expect(m.value(.kelly) == 0.3)
        m.selectModel(nil)
        #expect(!m.useLlm)
        #expect(m.body()["config"]?["use_llm"] == false)
    }
}

@MainActor
struct KalshiBacktestResultModelTests {
    @Test func dayGroupingEquityAndPickLabels() {
        let stub = DataStub()
        let m = KalshiBacktestResultModel(backtestId: "abcdef1234", repository: { KalshiRepository(client: stub.client) })
        m.apply([
            "status": "finished",
            "summary": ["pnl_cents": 250],
            "result": [
                "trades": [
                    ["kickoff": 1_767_225_600, "side": "home", "home": "A"],
                    ["kickoff": 1_767_312_000, "side": "draw"],
                    ["kickoff": nil, "side": "away"],
                ],
                "equity_curve": [100, 150, 250, 300],
            ],
        ])
        #expect(m.statusText == "finished")
        #expect(m.daysList == ["2026-01-01", "2026-01-02", "unknown"])
        #expect(m.selectedDay == "unknown")
        #expect(m.equity.values == [1, 1.5, 2.5, 3])
        #expect(m.equity.timestamps[3] == Date(timeIntervalSince1970: 3 * 3600))
        m.scrubbed(1)
        #expect(m.selectedDay == "2026-01-02")
        #expect(m.dayTrades.count == 1)
        m.selectedDay = "all"
        #expect(m.dayTrades.count == 3)
        #expect(KalshiBacktestResultModel.pickLabel(["side": "home", "home": "A"]) == "A to win")
        #expect(KalshiBacktestResultModel.pickLabel(["side": "away"]) == "Away to win")
        #expect(KalshiBacktestResultModel.pickLabel(["side": "draw"]) == "Draw")
        #expect(KalshiBacktestResultModel.pickLabel([:]) == "null")
    }
}
